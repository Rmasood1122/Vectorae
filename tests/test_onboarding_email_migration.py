"""Migration 0098 must add exactly the column the model declares.

Same copy-based technique as tests/test_identity_verification_migration.py:
copy the model metadata, drop the column 0098 adds, create users from that
copy, run 0098, and compare against the models.
"""

from __future__ import annotations

import importlib.util
import os
import uuid

import sqlalchemy as sa
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.db import models as m  # noqa: F401
from app.db.base import Base

MIGRATION = "0098_onboarding_email.py"
COLUMN = "onboarding_email_sent_at"


def _load():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location(
        MIGRATION[:-3], os.path.join(root, "alembic", "versions", MIGRATION))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _users_without_column(engine):
    meta = sa.MetaData(naming_convention=Base.metadata.naming_convention)
    for table in Base.metadata.sorted_tables:
        table.to_metadata(meta)
    users = meta.tables["users"]
    users._columns.remove(users.c[COLUMN])
    meta.create_all(engine, tables=[users])


def _columns(inspector):
    return {c["name"]: (str(c["type"]), bool(c["nullable"]))
            for c in inspector.get_columns("users")}


@pytest.fixture()
def migrated(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'mig.db'}")
    _users_without_column(engine)
    with engine.begin() as connection:
        connection.execute(sa.text(
            "INSERT INTO users (id, email, plan, theme_json, is_admin, is_suspended, "
            "email_verified, identity_required, phone_verified, created_at, updated_at) "
            "VALUES (:id, 'old@x.com', 'free', '{}', 0, 0, 1, 0, 0, "
            "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"), {"id": uuid.uuid4().hex})
        with Operations.context(MigrationContext.configure(connection)):
            _load().upgrade()
    yield engine
    engine.dispose()


def test_the_revision_follows_0097():
    module = _load()
    assert module.revision == "0098_onboarding_email"
    assert module.down_revision == "0097_linkedin_consent_tasks"


def test_users_columns_match_the_models(tmp_path, migrated):
    modelled = sa.create_engine(f"sqlite:///{tmp_path / 'models.db'}")
    Base.metadata.create_all(modelled, tables=[Base.metadata.tables["users"]])
    assert _columns(sa.inspect(migrated)) == _columns(sa.inspect(modelled))
    assert _columns(sa.inspect(migrated))[COLUMN][1] is True  # nullable
    modelled.dispose()


def test_existing_accounts_read_never_sent(migrated):
    """No backfill: every existing account reads NULL, i.e. 'never got it'."""
    with migrated.begin() as connection:
        value = connection.execute(sa.text(f"SELECT {COLUMN} FROM users")).scalar_one()
    assert value is None


def test_downgrade_round_trips(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'rt.db'}")
    _users_without_column(engine)
    module = _load()
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
            module.downgrade()
    assert COLUMN not in _columns(sa.inspect(engine))
    engine.dispose()
