"""Migration 0093 (F-DB-01): playbook_scores gets an owner.

Offline (SQLite) checks; the PostgreSQL behaviour -- the raw upsert, the
cascade, the downgrade with data -- is in
tests/integration/test_migration_0093_playbook_owner_pg.py.

What must hold:
  * the upgraded table, with the later revisions that touch it, matches the
    model (columns, indexes, FK, unique key);
  * rows that existed before the upgrade survive it with user_id NULL;
  * the downgrade restores the pre-0093 shape;
  * it chains after 0092, and the activation script targets the single head;
  * the table is account-owned for GDPR export and erasure.
"""

from __future__ import annotations

import importlib.util
import os
import re
import uuid

import sqlalchemy as sa
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory

from app.db import models as m  # noqa: F401
from app.db.base import Base

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATION = "0093_playbook_scores_owner.py"
TABLE = "playbook_scores"


def _load():
    spec = importlib.util.spec_from_file_location(
        MIGRATION[:-3], os.path.join(ROOT, "alembic", "versions", MIGRATION))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_revision(filename):
    spec = importlib.util.spec_from_file_location(
        filename[:-3], os.path.join(ROOT, "alembic", "versions", filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(engine, step):
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            step()


def _pre_0093_table(metadata: sa.MetaData) -> sa.Table:
    """playbook_scores as migrations 0001, 0008 and 0010 left it."""
    return sa.Table(
        TABLE, metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("pattern_key", sa.String(200), nullable=False, index=True),
        sa.Column("metric", sa.String(50), nullable=True),
        sa.Column("variant", sa.String(20), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("reply_rate", sa.Float(), nullable=True),
        sa.Column("booking_rate", sa.Float(), nullable=True),
        sa.Column("sample_size", sa.Integer(), nullable=False),
        sa.Column("is_reliable", sa.Boolean(), nullable=False),
        sa.Column("last_aggregated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("effective_sample_size", sa.Float(), nullable=True),
        sa.Column("decay_half_life_days", sa.Integer(), nullable=True),
        sa.Column("oldest_outcome_ts", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trend", sa.String(16), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("pattern_key", "variant", name="pattern_variant"),
    )


def _engine_before(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'playbook.db'}")
    Base.metadata.create_all(engine, tables=[t for t in Base.metadata.sorted_tables
                                             if t.name != TABLE])
    old = sa.MetaData()
    _pre_0093_table(old).create(engine)
    return engine


def _describe(inspector, table):
    return {
        "columns": {c["name"]: {"type": str(c["type"]), "nullable": bool(c["nullable"])}
                    for c in inspector.get_columns(table)},
        "indexes": {i["name"]: (list(i["column_names"]), bool(i["unique"]))
                    for i in inspector.get_indexes(table)},
        "uniques": {u["name"]: list(u["column_names"])
                    for u in inspector.get_unique_constraints(table)},
        "fks": {(tuple(f["constrained_columns"]), f["referred_table"],
                 f["options"].get("ondelete")) for f in inspector.get_foreign_keys(table)},
    }


def _config():
    config = Config(os.path.join(ROOT, "alembic.ini"))
    config.set_main_option("script_location", os.path.join(ROOT, "alembic"))
    return config


def _insert_pooled_row(engine, pattern_key):
    with engine.begin() as c:
        c.execute(sa.text(
            "INSERT INTO playbook_scores (id, pattern_key, variant, score, sample_size, "
            "is_reliable, created_at, updated_at) VALUES (:id, :pk, 'A', 0.1, 40, 1, "
            "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"), {"id": uuid.uuid4().hex, "pk": pattern_key})


def test_the_revision_follows_0092_and_the_activation_script_targets_the_head():
    from scripts import activate_with_keys as act

    module = _load()
    assert (module.revision, module.down_revision) == ("0093_playbook_scores_owner",
                                                       "0092_agent_runs")
    assert ScriptDirectory.from_config(_config()).get_heads() == [act.TARGET_REVISION]


def test_upgrade_matches_the_model_and_keeps_existing_rows(tmp_path):
    engine = _engine_before(tmp_path)
    _insert_pooled_row(engine, "pk-pooled")
    _run(engine, _load().upgrade)
    # The model is at the head: 0095 (F-DB-04) later made `score` nullable.
    _run(engine, _load_revision("0095_playbook_scores_score_nullable.py").upgrade)

    reference = sa.create_engine(f"sqlite:///{tmp_path / 'model.db'}")
    Base.metadata.create_all(reference)
    upgraded = _describe(sa.inspect(engine), TABLE)
    assert upgraded == _describe(sa.inspect(reference), TABLE)
    assert upgraded["uniques"] == {
        "uq_playbook_scores_owner_pattern_variant": ["user_id", "pattern_key", "variant"]}

    with engine.connect() as c:
        rows = c.execute(sa.text("SELECT pattern_key, user_id FROM playbook_scores")).all()
    assert [(r.pattern_key, r.user_id) for r in rows] == [("pk-pooled", None)]
    engine.dispose()
    reference.dispose()


def test_downgrade_restores_the_pre_0093_table(tmp_path):
    engine = _engine_before(tmp_path)
    before = _describe(sa.inspect(engine), TABLE)
    module = _load()
    _run(engine, module.upgrade)
    _run(engine, module.downgrade)
    assert _describe(sa.inspect(engine), TABLE) == before
    engine.dispose()


def test_names_fit_postgres_and_no_jsonb():
    source = open(os.path.join(ROOT, "alembic", "versions", MIGRATION), encoding="utf-8").read()
    names = re.findall(r'"((?:ix|uq|pk|fk)_[a-z0-9_]+)"', source)
    assert names and all(len(n) <= 63 for n in names)
    assert "postgresql." not in source and "JSONB" not in source


def test_the_table_belongs_to_the_account():
    from app.services import account_erasure, account_export

    assert account_export.OWNERSHIP[TABLE] == ["user_id"]
    assert TABLE not in account_export.EXCLUDED
    assert TABLE in account_erasure.DELETED
    fks = Base.metadata.tables[TABLE].foreign_keys
    assert {(fk.column.table.name, fk.ondelete) for fk in fks} == {("users", "CASCADE")}
