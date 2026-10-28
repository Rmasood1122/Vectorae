"""Migration 0092 (Vectorae agents): agent_runs matches the model, chains after
0091, names fit PostgreSQL, no JSONB, the downgrade removes exactly the table,
one head (which the activation script targets), and the table belongs to the
account for export and erasure."""

from __future__ import annotations

import importlib.util
import os
import re

import sqlalchemy as sa
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory

from app.db import models as m  # noqa: F401
from app.db.base import Base

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATION = "0092_agent_runs.py"
TABLE = "agent_runs"


def _load():
    spec = importlib.util.spec_from_file_location(
        MIGRATION[:-3], os.path.join(ROOT, "alembic", "versions", MIGRATION))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(engine, step):
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            step()


def _engine_before(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'agents.db'}")
    Base.metadata.create_all(engine, tables=[t for t in Base.metadata.sorted_tables
                                             if t.name != TABLE])
    return engine


def _describe(inspector, table):
    return {
        "columns": {c["name"]: {"type": str(c["type"]), "nullable": bool(c["nullable"])}
                    for c in inspector.get_columns(table)},
        "indexes": {i["name"]: (list(i["column_names"]), bool(i["unique"]))
                    for i in inspector.get_indexes(table)},
        "fks": {(tuple(f["constrained_columns"]), f["referred_table"],
                 f["options"].get("ondelete")) for f in inspector.get_foreign_keys(table)},
    }


def _config():
    config = Config(os.path.join(ROOT, "alembic.ini"))
    config.set_main_option("script_location", os.path.join(ROOT, "alembic"))
    return config


def test_the_revision_follows_0091_and_the_activation_script_targets_the_head():
    from scripts import activate_with_keys as act

    module = _load()
    assert (module.revision, module.down_revision) == ("0092_agent_runs", "0091_oauth_connector")
    assert ScriptDirectory.from_config(_config()).get_heads() == [act.TARGET_REVISION]


def test_upgrade_matches_the_model(tmp_path):
    engine = _engine_before(tmp_path)
    _run(engine, _load().upgrade)
    reference = sa.create_engine(f"sqlite:///{tmp_path / 'model.db'}")
    Base.metadata.create_all(reference)
    assert _describe(sa.inspect(engine), TABLE) == _describe(sa.inspect(reference), TABLE)
    engine.dispose()
    reference.dispose()


def test_names_fit_postgres_and_no_jsonb():
    source = open(os.path.join(ROOT, "alembic", "versions", MIGRATION), encoding="utf-8").read()
    names = re.findall(r'"((?:ix|uq|pk|fk)_[a-z0-9_]+)"', source)
    assert names and all(len(n) <= 63 for n in names)
    assert "postgresql." not in source and "JSONB" not in source


def test_downgrade_removes_exactly_the_table(tmp_path):
    engine = _engine_before(tmp_path)
    before = set(sa.inspect(engine).get_table_names())
    module = _load()
    _run(engine, module.upgrade)
    assert TABLE in sa.inspect(engine).get_table_names()
    _run(engine, module.downgrade)
    assert set(sa.inspect(engine).get_table_names()) == before
    engine.dispose()


def test_the_table_belongs_to_the_account():
    from app.services import account_erasure, account_export

    assert account_export.OWNERSHIP[TABLE] == ["user_id"]
    assert TABLE in account_erasure.DELETED
    fks = Base.metadata.tables[TABLE].foreign_keys
    assert {(fk.column.table.name, fk.ondelete) for fk in fks} == {("users", "CASCADE")}
