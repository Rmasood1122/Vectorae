"""Migration 0095 (F-DB-04, step 1 of 3): playbook_scores.score becomes nullable.

`score` only ever copied `reply_rate`. It is removed in three deploys (see the
migration's docstring), and this is the first: the column becomes nullable,
the code still writes it, and the nightly trend compares reply_rate.

What must hold:
  * it chains after 0094 and is the head the activation script targets;
  * the upgrade keeps every row and value, and only `score` changes;
  * the downgrade fills a NULL `score` from reply_rate and restores NOT NULL;
  * the nightly upsert's trend no longer reads `score`.
"""

from __future__ import annotations

import importlib.util
import inspect
import os
import uuid

import sqlalchemy as sa
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATION = "0095_playbook_score_nullable.py"
TABLE = "playbook_scores"


def _load():
    spec = importlib.util.spec_from_file_location(
        MIGRATION[:-3], os.path.join(ROOT, "alembic", "versions", MIGRATION))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _config():
    config = Config(os.path.join(ROOT, "alembic.ini"))
    config.set_main_option("script_location", os.path.join(ROOT, "alembic"))
    return config


def _run(engine, step):
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            step()


def _engine_at_0094(tmp_path):
    """playbook_scores as 0093 left it (the columns this migration touches)."""
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'scores.db'}")
    sa.Table(
        TABLE, sa.MetaData(),
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("pattern_key", sa.String(200), nullable=False),
        sa.Column("variant", sa.String(20), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("reply_rate", sa.Float(), nullable=True),
    ).create(engine)
    return engine


def _columns(engine):
    return {c["name"]: bool(c["nullable"]) for c in sa.inspect(engine).get_columns(TABLE)}


def _insert(engine, score, reply_rate):
    with engine.begin() as c:
        c.execute(sa.text(f"INSERT INTO {TABLE} (id, pattern_key, variant, score, reply_rate) "
                          "VALUES (:id, 'pk', :v, :s, :r)"),
                  {"id": uuid.uuid4().hex, "v": uuid.uuid4().hex[:8], "s": score,
                   "r": reply_rate})


def test_it_chains_after_0094_and_is_the_activation_target():
    from scripts import activate_with_keys as act

    module = _load()
    assert (module.revision, module.down_revision) == (
        "0095_playbook_score_nullable", "0094_help_chunks")
    assert ScriptDirectory.from_config(_config()).get_heads() == [act.TARGET_REVISION]
    # 0096 (outbound threading) later became the head: the target must still
    # carry this revision, i.e. be it or descend from it.
    chain = {r.revision for r in ScriptDirectory.from_config(_config())
             .walk_revisions("base", act.TARGET_REVISION)}
    assert module.revision in chain


def test_upgrade_makes_only_score_nullable_and_keeps_the_rows(tmp_path):
    engine = _engine_at_0094(tmp_path)
    before = _columns(engine)
    _insert(engine, 0.25, 0.25)

    _run(engine, _load().upgrade)

    after = _columns(engine)
    assert after["score"] is True
    assert {k: v for k, v in after.items() if k != "score"} == \
        {k: v for k, v in before.items() if k != "score"}
    with engine.connect() as c:
        assert c.execute(sa.text(f"SELECT score, reply_rate FROM {TABLE}")).one() == (0.25, 0.25)
    _insert(engine, None, 0.4)     # the code may now leave it out
    engine.dispose()


def test_downgrade_fills_score_and_restores_not_null(tmp_path):
    engine = _engine_at_0094(tmp_path)
    module = _load()
    _run(engine, module.upgrade)
    _insert(engine, None, 0.4)
    _insert(engine, None, None)

    _run(engine, module.downgrade)

    assert _columns(engine)["score"] is False
    with engine.connect() as c:
        scores = sorted(r.score for r in c.execute(sa.text(f"SELECT score FROM {TABLE}")))
    assert scores == [0.0, 0.4]
    engine.dispose()


def test_the_nightly_trend_compares_reply_rate_not_score():
    from app.workers.learning_tasks import _upsert_playbook_score

    sql = inspect.getsource(_upsert_playbook_score)
    trend = sql[sql.index("trend = CASE"):sql.index("END,")]
    assert "playbook_scores.score" not in trend and "EXCLUDED.score" not in trend
    assert "playbook_scores.reply_rate IS NULL THEN 'new'" in trend
    assert "EXCLUDED.reply_rate > playbook_scores.reply_rate + 0.005" in trend


def test_every_revision_id_fits_the_version_column():
    """alembic_version.version_num is VARCHAR(32) on PostgreSQL; a longer id
    makes the upgrade fail while stamping the new head. SQLite does not
    enforce the length, so only this test catches it offline."""
    script = ScriptDirectory.from_config(_config())
    too_long = sorted(rev.revision for rev in script.walk_revisions()
                      if len(rev.revision) > 32)
    assert too_long == []
