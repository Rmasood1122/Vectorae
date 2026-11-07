"""Production sign-in outage, 2026-11-07: reproduce the exact database state.

Production ran the ORIGINAL 0099 (one column, ppm_credits_limit_cents). The
file was later edited to add three more columns; Alembic never re-runs an
applied revision, so production's billing_subscriptions lacked them and every
BillingSubscription load -- POST /auth/login included -- raised UndefinedColumn.
Separately, 0103 dropped a constraint on credit_transaction_log, a table no
migration creates, so every deploy after it crash-looped.

These tests build billing_subscriptions the way production has it, run 0103
and 0105 against it, and require the result to match the model.
"""

from __future__ import annotations

import importlib.util
import os

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.db import models as m  # noqa: F401
from app.db.base import Base

LATER = ("ppm_credits_expires_at", "ppm_credits_carryover_cents", "ppm_credits_reset_day")


def _load(name):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location(
        name[:-3], os.path.join(root, "alembic", "versions", name))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(engine, migration):
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()


def _billing_table(engine, drop=()):
    meta = sa.MetaData(naming_convention=Base.metadata.naming_convention)
    for table in Base.metadata.sorted_tables:
        table.to_metadata(meta)
    tbl = meta.tables["billing_subscriptions"]
    for name in drop:
        tbl._columns.remove(tbl.c[name])
    meta.create_all(engine, tables=[meta.tables["users"], tbl])


def _cols(engine):
    return {c["name"] for c in sa.inspect(engine).get_columns("billing_subscriptions")}


@pytest.fixture()
def prod_shaped(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'prod.db'}")
    _billing_table(engine, drop=LATER)
    assert not set(LATER) & _cols(engine)
    return engine


def test_0103_is_a_noop_when_credit_transaction_log_does_not_exist(prod_shaped):
    assert not sa.inspect(prod_shaped).has_table("credit_transaction_log")
    _run(prod_shaped, _load("0103_credit_transaction_log_fk.py"))  # must not raise


def test_0105_adds_the_columns_production_is_missing(prod_shaped):
    _run(prod_shaped, _load("0105_ppm_credits_columns_repair.py"))
    model = {c.name for c in Base.metadata.tables["billing_subscriptions"].columns}
    assert _cols(prod_shaped) == model


def test_0105_is_idempotent_on_a_database_that_already_has_them(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    _billing_table(engine)
    before = _cols(engine)
    _run(engine, _load("0105_ppm_credits_columns_repair.py"))
    assert _cols(engine) == before


def test_login_query_works_after_repair(prod_shaped):
    _run(prod_shaped, _load("0105_ppm_credits_columns_repair.py"))
    from sqlalchemy.orm import Session
    with Session(prod_shaped) as db:
        db.execute(sa.select(m.BillingSubscription)).all()  # the query that 500'd
