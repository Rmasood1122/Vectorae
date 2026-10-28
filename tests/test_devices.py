"""
test_devices.py — Vectorae (M7 Chunk 4)

Tests for app/api/devices.py (POST /devices/register, DELETE /devices/{token}).
Uses the existing test client and db_session fixtures from conftest.py.
"""

from __future__ import annotations

import pytest
from datetime import datetime, timezone, timedelta
from sqlalchemy import select

from app.db.models import DeviceToken


# ── Helpers ───────────────────────────────────────────────────────────────────

def _auth_headers(access_token: str) -> dict:
    return {"Authorization": f"Bearer {access_token}"}


def _get_token_from_db(db_session, token: str) -> DeviceToken | None:
    # db_session is a SYNC Session - app/db/base.py yields Session and
    # app/api/devices.py is sync to match. No await anywhere in this module.
    result = db_session.execute(
        select(DeviceToken).where(DeviceToken.token == token)
    )
    return result.scalar_one_or_none()


@pytest.fixture()
def pg_upsert(db_session):
    """Skip unless the session is on PostgreSQL.

    app/api/devices.py registers tokens with
    sqlalchemy.dialects.postgresql.insert(...).on_conflict_do_update(), which
    SQLite cannot execute - it aborts the transaction with "cannot commit
    transaction - SQL statements in progress". That is correct for the app:
    PostgreSQL is the deployment target (docker-compose, alembic). It just
    means the registration path is a PostgreSQL test.

    Device-token ownership IS covered against real PostgreSQL by
    tests/integration/test_security.py::TestAdminOnlyEndpoints
    ::test_device_token_only_deletable_by_owner. Only the tests that actually
    perform the upsert request this fixture; the auth, validation and
    deregistration tests around them stay dialect-neutral and keep running.
    """
    if db_session.bind.dialect.name != "postgresql":
        pytest.skip("device-token upsert requires PostgreSQL ON CONFLICT")


# ── Registration ──────────────────────────────────────────────────────────────

class TestDeviceRegister:
    @pytest.mark.asyncio
    async def test_register_new_token_inserts_row(self, client, db_session, user_tokens, pg_upsert):
        """POST /devices/register with a new token → inserts a DeviceToken row."""
        resp = client.post(
            "/devices/register",
            json={"token": "fcm_token_abc123", "platform": "android"},
            headers=_auth_headers(user_tokens["access_token"]),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["token"] == "fcm_token_abc123"
        assert body["platform"] == "android"
        assert body["registered"] is True  # new row

        # Verify DB
        row = _get_token_from_db(db_session, "fcm_token_abc123")
        assert row is not None
        assert row.is_valid is True
        assert row.platform == "android"

    @pytest.mark.asyncio
    async def test_register_existing_token_updates_last_seen_at(
        self, client, db_session, user_tokens, pg_upsert
    ):
        """POST with an existing token → updates last_seen_at, registered=False."""
        # First registration
        client.post(
            "/devices/register",
            json={"token": "fcm_token_existing", "platform": "android"},
            headers=_auth_headers(user_tokens["access_token"]),
        )
        row_before = _get_token_from_db(db_session, "fcm_token_existing")
        first_seen = row_before.last_seen_at

        # Second registration (same token — simulating app restart)
        resp = client.post(
            "/devices/register",
            json={"token": "fcm_token_existing", "platform": "android"},
            headers=_auth_headers(user_tokens["access_token"]),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["registered"] is False  # already existed

        # last_seen_at should be updated (or same if too fast — allow equal)
        row_after = _get_token_from_db(db_session, "fcm_token_existing")
        assert row_after.last_seen_at >= first_seen

    @pytest.mark.asyncio
    async def test_register_revalidates_previously_invalid_token(
        self, client, db_session, user_tokens, pg_upsert
    ):
        """A token marked is_valid=False gets re-validated on re-registration."""
        # Insert an invalid token directly
        row = DeviceToken(
            user_id=user_tokens["user_id"],
            token="stale_token_reactivate",
            platform="android",
            is_valid=False,
        )
        db_session.add(row)
        db_session.commit()

        # Re-register → should set is_valid=True
        resp = client.post(
            "/devices/register",
            json={"token": "stale_token_reactivate", "platform": "android"},
            headers=_auth_headers(user_tokens["access_token"]),
        )
        assert resp.status_code == 200

        refreshed = _get_token_from_db(db_session, "stale_token_reactivate")
        assert refreshed.is_valid is True

    @pytest.mark.asyncio
    async def test_register_requires_auth(self, anon_client):
        """POST /devices/register without JWT → 401."""
        resp = anon_client.post(
            "/devices/register",
            json={"token": "any_token", "platform": "android"},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_register_requires_valid_platform(self, client, user_tokens):
        """Platform must be 'android' or 'ios'."""
        resp = client.post(
            "/devices/register",
            json={"token": "tok", "platform": "windows"},
            headers=_auth_headers(user_tokens["access_token"]),
        )
        assert resp.status_code == 422  # Pydantic validation error

    @pytest.mark.asyncio
    async def test_duplicate_webhook_delivery_idempotent(self, client, db_session, user_tokens, pg_upsert):
        """Sending the same token 5 times in quick succession is safe (idempotent)."""
        for _ in range(5):
            resp = client.post(
                "/devices/register",
                json={"token": "idempotent_token", "platform": "android"},
                headers=_auth_headers(user_tokens["access_token"]),
            )
            assert resp.status_code == 200

        # Should be exactly ONE row in the DB
        result = db_session.execute(
            select(DeviceToken).where(DeviceToken.token == "idempotent_token")
        )
        rows = result.scalars().all()
        assert len(rows) == 1


# ── Deregistration ────────────────────────────────────────────────────────────

class TestDeviceDeregister:
    @pytest.mark.asyncio
    async def test_delete_soft_deletes_token(self, client, db_session, user_tokens, pg_upsert):
        """DELETE /devices/{token} marks is_valid=False (soft delete)."""
        # Register first
        client.post(
            "/devices/register",
            json={"token": "token_to_delete", "platform": "android"},
            headers=_auth_headers(user_tokens["access_token"]),
        )

        # Delete
        resp = client.delete(
            "/devices/token_to_delete",
            headers=_auth_headers(user_tokens["access_token"]),
        )
        assert resp.status_code == 204

        # Row still exists but is_valid=False
        row = _get_token_from_db(db_session, "token_to_delete")
        assert row is not None
        assert row.is_valid is False

    @pytest.mark.asyncio
    async def test_delete_requires_auth(self, anon_client):
        """DELETE without JWT → 401."""
        resp = anon_client.delete("/devices/sometoken")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_delete_nonexistent_token_returns_404(self, client, user_tokens):
        """DELETE a token that doesn't exist → 404."""
        resp = client.delete(
            "/devices/nonexistent_token_xyz",
            headers=_auth_headers(user_tokens["access_token"]),
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_cannot_delete_other_users_token(self, client, db_session, user_tokens):
        """A user cannot deregister a token belonging to a different user."""
        # Create another user and their token
        from app.db.models import User

        other_user = User(email="someone-else@leadpilot.dev")
        db_session.add(other_user)
        db_session.flush()
        other_user_row = DeviceToken(
            user_id=other_user.id,  # a genuinely different user
            token="other_users_token",
            platform="android",
            is_valid=True,
        )
        db_session.add(other_user_row)
        db_session.commit()

        resp = client.delete(
            "/devices/other_users_token",
            headers=_auth_headers(user_tokens["access_token"]),
        )
        assert resp.status_code == 404  # 404 not 403 — don't reveal other users' tokens
