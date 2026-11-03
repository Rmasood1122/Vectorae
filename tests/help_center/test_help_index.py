"""Help AI v2, Phase 2: the help_chunks index, its migration and reindex.

Offline (SQLite). What PostgreSQL alone can show -- halfvec storage, the HNSW
search, a second reindex embedding nothing -- is in
tests/integration/test_help_index_pg.py.

Pinned here:
  * index rows ARE the BM25 chunks (same chunker, same stable ids) with
    links to real heading anchors;
  * the content hash: editing a chunk re-embeds its whole article (the
    vectors are contextual) and nothing else;
  * the CI staleness check: scripts/help_center/index_manifest.json must
    match the corpus, and a corpus edit without regenerating it fails;
  * reindex is idempotent on the text index, and on SQLite skips the vector
    column cleanly;
  * migration 0094 chains after 0093, is the head the activation script
    targets, creates exactly the model's table on SQLite and drops it;
  * the deploy hook runs reindex after migrations and cannot block the boot;
  * the BM25 path works with no index and with the vendor down.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import select

from app.config import settings
from app.db import models as m
from app.db.base import Base
from app.services import embeddings_client, help_center, help_index
from scripts.help_center import reindex

REPO = Path(__file__).resolve().parents[2]
HELP = REPO / "docs" / "help-center"
MIGRATION = REPO / "alembic" / "versions" / "0094_help_chunks.py"


@pytest.fixture(scope="module")
def rows():
    return help_index.build_rows(help_center.load_corpus(HELP))


@pytest.fixture()
def corpus_copy(tmp_path):
    target = tmp_path / "help-center"
    shutil.copytree(HELP, target)
    return target


def _append(directory: Path, prefix: str) -> str:
    """Add a sentence to the end of article `prefix`'s intro; return its id.
    (Not the end of the file: that is `## Related`, which is not indexed.)"""
    path = next(directory.glob(f"{prefix}-*.md"))
    text = path.read_text(encoding="utf-8")
    cut = text.index("\n## ")
    path.write_text(text[:cut] + "\n\nOne more sentence." + text[cut:], encoding="utf-8")
    return help_center._parse_article(path).id


# --------------------------------------------------------------------------
# Rows
# --------------------------------------------------------------------------

class TestRows:
    def test_rows_are_the_bm25_chunks(self, rows):
        corpus = help_center.load_corpus(HELP)
        assert [r.id for r in rows] == [c.id for c in corpus.chunks]
        assert len({r.id for r in rows}) == len(rows)

    def test_every_url_is_a_page_and_a_real_heading_anchor(self, rows):
        corpus = help_center.load_corpus(HELP)
        for row in rows:
            article = corpus.articles_by_id[row.article_id]
            page, _, anchor = row.url.partition("#")
            assert page == f"/help/{row.article_id}/", row.url
            if anchor:
                assert anchor in {s.slug for s in article.sections}, row.url

    def test_intro_chunks_link_to_the_page(self, rows):
        intros = [r for r in rows if " > " not in r.heading_path]
        assert intros
        assert all(r.url == f"/help/{r.article_id}/" for r in intros)

    def test_continuations_link_to_the_heading_they_continue(self):
        section = help_center.Section("How long?", "how-long-2", "body")
        chunk = help_center.Chunk("a#how-long-2", "a", "Title", "How long? (continued)",
                                  (section,))
        assert help_index.chunk_url(chunk) == "/help/a/#how-long"

    def test_heading_path_and_embedding_text(self, rows):
        row = next(r for r in rows if r.id == "plans-limits-billing#how-do-i-cancel")
        assert row.heading_path.endswith("> How do I cancel?")
        assert row.embed_text.startswith(row.heading_path + "\n\n")
        assert row.body in row.embed_text

    def test_rows_hold_nothing_but_corpus_text(self, rows):
        corpus_text = "".join(p.read_text(encoding="utf-8") for p in HELP.glob("[0-9][0-9]-*.md"))
        for row in rows:
            for line in row.body.splitlines():
                assert line in corpus_text


# --------------------------------------------------------------------------
# Content hash
# --------------------------------------------------------------------------

class TestContentHash:
    def test_stable_across_builds(self, rows):
        again = help_index.build_rows(help_center.load_corpus(HELP))
        assert [r.content_hash for r in again] == [r.content_hash for r in rows]

    def test_an_edit_changes_its_whole_article_and_nothing_else(self, rows, corpus_copy):
        article = _append(corpus_copy, "09")
        edited = {r.id: r for r in help_index.build_rows(help_center.load_corpus(corpus_copy))}
        assert set(edited) == {r.id for r in rows}
        for row in rows:
            same = edited[row.id].content_hash == row.content_hash
            assert same is (row.article_id != article), row.id


# --------------------------------------------------------------------------
# CI staleness check (the manifest)
# --------------------------------------------------------------------------

class TestManifest:
    def test_committed_manifest_matches_the_corpus(self):
        diff = reindex.check_manifest()
        assert diff == [], ("scripts/help_center/index_manifest.json is stale; run "
                            "python -m scripts.help_center.reindex --write-manifest\n"
                            + "\n".join(diff))

    def test_a_corpus_edit_makes_it_stale(self, corpus_copy, tmp_path):
        manifest = tmp_path / "manifest.json"
        reindex.write_manifest(corpus_copy, manifest)
        assert reindex.check_manifest(corpus_copy, manifest) == []
        _append(corpus_copy, "36")
        diff = reindex.check_manifest(corpus_copy, manifest)
        assert diff and all(line.startswith("~ ") for line in diff)

    def test_added_and_removed_chunks_are_named(self, rows):
        expected = help_index.manifest(rows)
        committed = json.loads(json.dumps(expected))
        gone = next(iter(committed["chunks"]))
        del committed["chunks"][gone]
        committed["chunks"]["old-article#old"] = "0" * 64
        diff = help_index.manifest_diff(expected, committed)
        assert f"+ {gone}" in diff and "- old-article#old" in diff

    def test_cli_exit_code(self):
        assert reindex.main(["--check-manifest"]) == reindex.EXIT_OK


# --------------------------------------------------------------------------
# Reindex on SQLite: text index only, idempotent
# --------------------------------------------------------------------------

class TestReindexSqlite:
    @pytest.fixture(autouse=True)
    def _mock(self, monkeypatch):
        monkeypatch.setattr(settings, "embeddings_mode", "mock")

    def test_first_run_adds_second_run_changes_nothing(self, db_session):
        first = reindex.run(db_session, allow_mock=True, directory=HELP)
        assert len(first.added) == db_session.query(m.HelpChunk).count() > 250
        second = reindex.run(db_session, allow_mock=True, directory=HELP)
        assert not second.changed
        assert second.unchanged == len(first.added)

    def test_sqlite_skips_the_vector_column_cleanly(self, db_session):
        result = reindex.run(db_session, allow_mock=True, directory=HELP)
        assert result.embedded == [] and len(result.pending) == len(result.added)
        assert "no embedding column" in result.embedding_note
        assert not help_index.has_vector_column(db_session)
        assert all(c.embedded_at is None and c.model == "" and c.context_sentence == ""
                   for c in db_session.execute(select(m.HelpChunk)).scalars())

    def test_edits_and_deletions_are_applied(self, db_session, corpus_copy):
        reindex.run(db_session, directory=corpus_copy)
        edited = _append(corpus_copy, "36")
        next(corpus_copy.glob("44-*.md")).unlink()
        result = reindex.run(db_session, directory=corpus_copy)
        assert result.updated and {i.split("#")[0] for i in result.updated} == {edited}
        assert result.removed and all(i.startswith("learn-tab-and-tutorials#")
                                      for i in result.removed)
        assert db_session.get(m.HelpChunk, result.removed[0]) is None

    def test_check_reports_stale_then_current(self, db_session, rows):
        assert help_index.stale(db_session, rows, embeddings_client.embedder_id())
        reindex.run(db_session, directory=HELP)
        assert help_index.stale(db_session, rows, embeddings_client.embedder_id()) == []

    def test_summary_is_a_diff(self, db_session):
        text = reindex.run(db_session, directory=HELP).summary()
        assert text.splitlines()[0].startswith("help_chunks: ")
        assert "+ what-is-vectorae#" in text

    def test_needs_embedding(self, rows):
        row = rows[0]
        done = m.HelpChunk(id=row.id, content_hash=row.content_hash, model="e@1",
                           embedded_at=help_index.datetime.now(help_index.timezone.utc))
        assert not help_index.needs_embedding(row, done, "e@1")
        assert help_index.needs_embedding(row, done, "other@1024")
        assert help_index.needs_embedding(row, None, "e@1")


# --------------------------------------------------------------------------
# Migration 0094
# --------------------------------------------------------------------------

def _load_migration():
    spec = importlib.util.spec_from_file_location("m0094", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(engine, step):
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            step()


def _describe(inspector, table):
    return {
        "columns": {c["name"]: (str(c["type"]), bool(c["nullable"]))
                    for c in inspector.get_columns(table)},
        "indexes": {i["name"]: (list(i["column_names"]), bool(i["unique"]))
                    for i in inspector.get_indexes(table)},
        "pk": inspector.get_pk_constraint(table)["constrained_columns"],
    }


class TestMigration:
    def test_chain_and_head(self):
        from scripts import activate_with_keys as act

        module = _load_migration()
        assert (module.revision, module.down_revision) == ("0094_help_chunks",
                                                           "0093_playbook_scores_owner")
        config = Config(str(REPO / "alembic.ini"))
        config.set_main_option("script_location", str(REPO / "alembic"))
        assert ScriptDirectory.from_config(config).get_heads() == [act.TARGET_REVISION]
        assert module.DIMENSION == settings.voyage_embed_dimension

    def test_sqlite_upgrade_is_the_model_and_downgrade_drops_it(self, tmp_path):
        engine = sa.create_engine(f"sqlite:///{tmp_path / 'm.db'}")
        module = _load_migration()
        _run(engine, module.upgrade)
        reference = sa.create_engine(f"sqlite:///{tmp_path / 'ref.db'}")
        Base.metadata.create_all(reference, tables=[m.HelpChunk.__table__])
        assert (_describe(sa.inspect(engine), "help_chunks")
                == _describe(sa.inspect(reference), "help_chunks"))
        assert "embedding" not in {c["name"] for c in sa.inspect(engine).get_columns("help_chunks")}
        _run(engine, module.downgrade)
        assert "help_chunks" not in sa.inspect(engine).get_table_names()
        engine.dispose()
        reference.dispose()

    @pytest.mark.parametrize("installed,available,usable", [
        (None, "0.8.1", True), (None, "0.7.0", True), (None, "0.6.0", False),
        (None, None, False), ("0.7.4", None, True), ("0.5.1", "0.8.0", False),
    ])
    def test_pgvector_gate(self, installed, available, usable):
        class Bind:
            def execute(self, statement):
                value = installed if "pg_extension" in str(statement) else available

                class Result:
                    def scalar(self_inner):
                        return value
                return Result()

        assert _load_migration()._pgvector_usable(Bind()) is usable

    def test_index_parameters_are_the_specified_ones(self):
        source = MIGRATION.read_text(encoding="utf-8")
        assert "CREATE EXTENSION IF NOT EXISTS vector" in source
        assert "halfvec({DIMENSION})" in source
        assert "USING hnsw (embedding halfvec_cosine_ops) WITH (m = 16, ef_construction = 64)" in source
        assert "USING gin (body_tsv)" in source

    def test_not_in_the_account_export(self):
        from app.services import account_export

        assert "help_chunks" in account_export.EXCLUDED
        assert "help_chunks" not in account_export.OWNERSHIP


# --------------------------------------------------------------------------
# Deploy hook
# --------------------------------------------------------------------------

def test_the_api_entrypoint_reindexes_after_migrating_without_blocking_boot():
    script = (REPO / "scripts" / "render_api_entrypoint.sh").read_text(encoding="utf-8")
    migrate = script.index("python -m app.db.migrate")
    hook = script.index("python -m scripts.help_center.reindex")
    serve = script.index("exec uvicorn")
    assert migrate < hook < serve
    command = script[hook:script.index("\n\n", hook)].replace("\\\n", " ")
    assert "|| " in command, "a failed reindex must not stop the API from starting (set -e)"
    assert "--allow-mock" not in command


# --------------------------------------------------------------------------
# BM25 never depends on the index or the vendor
# --------------------------------------------------------------------------

class TestBm25Fallback:
    def test_bm25_does_not_import_the_vector_path(self):
        source = (REPO / "app" / "services" / "help_center.py").read_text(encoding="utf-8")
        assert "embeddings_client" not in source and "help_index" not in source

    def test_retrieval_works_with_the_vendor_down(self, monkeypatch):
        def down(*args, **kwargs):
            raise embeddings_client.EmbeddingsUnavailable("down")

        monkeypatch.setattr(settings, "embeddings_mode", "live")
        monkeypatch.setattr(embeddings_client, "_post", down)
        hits = help_center.retrieve("How do I cancel my subscription?")
        assert hits and hits[0].chunk.id == "plans-limits-billing#how-do-i-cancel"

    def test_vendor_down_leaves_the_text_index_current(self, db_session, monkeypatch):
        def down(*args, **kwargs):
            raise embeddings_client.EmbeddingsUnavailable("down")

        monkeypatch.setattr(help_index, "has_vector_column", lambda session: True)
        monkeypatch.setattr(embeddings_client, "embed_documents", down)
        monkeypatch.setattr(settings, "embeddings_mode", "live")
        monkeypatch.setattr(settings, "voyage_api_key", "k")
        result = reindex.run(db_session, directory=HELP)
        assert result.added and result.embedded == []
        assert result.embedding_note.startswith("embeddings unavailable")
        assert db_session.query(m.HelpChunk).count() == len(result.added)

