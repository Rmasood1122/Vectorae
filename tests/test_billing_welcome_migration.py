"""Migration 0098: billing_subscriptions.welcome_email_sent_at matches the
model, leaves existing rows NULL (no one is welcomed retroactively), and
downgrade round-trips."""

from __future__ import annotations

import importlib.util
import os
import uuid

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.db import models as m  # noqa: F401
from app.db.base import Base

MIGRATION = "0098_billing_welcome_email.py"
COLUMN = "welcome_email_sent_at"


def _load(name):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location(
        name[:-3], os.path.join(root, "alembic", "versions", name))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def before(tmp_path):
    """users + billing_subscriptions as they were before 0098."""
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    meta = sa.MetaData(naming_convention=Base.metadata.naming_convention)
    for table in Base.metadata.sorted_tables:
        table.to_metadata(meta)
    subs = meta.tables["billing_subscriptions"]
    subs._columns.remove(subs.c[COLUMN])
    meta.create_all(engine, tables=[meta.tables["users"], subs])
    yield engine
    engine.dispose()


def _run(engine, fn_name):
    module = _load(MIGRATION)
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            getattr(module, fn_name)()


def _cols(engine):
    return {c["name"]: c for c in sa.inspect(engine).get_columns("billing_subscriptions")}


def test_revision_chain():
    module = _load(MIGRATION)
    assert module.revision == "0098_billing_welcome_email"
    assert module.down_revision == "0097_linkedin_consent_tasks"


def test_upgrade_matches_the_model(before, tmp_path):
    assert COLUMN not in _cols(before)
    _run(before, "upgrade")
    migrated = _cols(before)[COLUMN]
    model_engine = sa.create_engine(f"sqlite:///{tmp_path / 'model.db'}")
    Base.metadata.create_all(model_engine, tables=[Base.metadata.tables["users"],
                                                   Base.metadata.tables["billing_subscriptions"]])
    modelled = _cols(model_engine)[COLUMN]
    model_engine.dispose()
    assert str(migrated["type"]) == str(modelled["type"])
    assert migrated["nullable"] is True and modelled["nullable"] is True


def test_existing_rows_stay_null_and_downgrade_round_trips(before):
    uid, sid = uuid.uuid4().hex, uuid.uuid4().hex
    with before.begin() as conn:
        # SQLite does not enforce the FK here: no users row needed.
        conn.execute(sa.text(
            "INSERT INTO billing_subscriptions (id, user_id, billing_model, tier, status, "
            "cancel_at_period_end, is_stub) VALUES (:s, :u, 'monthly', 'growth', 'active', 0, 0)"),
            {"s": sid, "u": uid})
    _run(before, "upgrade")
    with before.connect() as conn:
        assert conn.execute(sa.text(
            f"SELECT {COLUMN} FROM billing_subscriptions")).scalar_one() is None
    _run(before, "downgrade")
    assert COLUMN not in _cols(before)
    with before.connect() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM billing_subscriptions")).scalar_one() == 1
