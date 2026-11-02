"""GDPR -- an account holder can erase their own account (Art. 17).

BEFORE: no route, service or task deleted or anonymised a `users` row
(BASELINE.md, "GDPR export / deletion for user accounts", HIGH). An erasure
request could only be met by hand in the database.

NOW: DELETE /me (password + typed "DELETE") cancels any paid plan, then
app/services/account_erasure.py erases every row the account owns, using
account_export.OWNERSHIP as the source of truth. Pinned here:

  * COMPLETENESS -- every OWNERSHIP table has exactly one erasure decision; a
    table added without one fails. A row is generated in EVERY owned table for
    two accounts, one is erased, and every table is checked: deleted for the
    erased account, untouched for the other, kept rows kept.
  * DECISIONS -- E1 billing kept without personal data, E2 consent proof kept,
    E3 workspaces, the users row a non-personal tombstone.
  * ACCESS -- password, typed confirmation, no API key, no admin, rate limit,
    the workspace header never redirects the deletion to the owner.
  * AFTERMATH -- old tokens stop working, sign-in fails, the email can sign up
    again, nothing scheduled can send, credentials are gone.

Offline: SQLite, the real app, real auth, Stripe monkeypatched.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import Boolean, Enum, Float, Integer, Numeric, String, Text, func, insert, select
from sqlalchemy import JSON, BigInteger, Date, DateTime, Time, Uuid

from app.config import settings
from app.db import models as m
from app.db.base import Base
from app.integrations import stripe_billing
from app.services import account_erasure as ae
from app.services import account_export as ax
from app.services import api_keys, auth_sessions, billing, security_audit
from app.services import auth as auth_svc
from app.services import crypto
from tests.conftest import auth_headers

PASSWORD = "Erase-me-1!"
NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------
# The decision map
# --------------------------------------------------------------------------

class TestDecisionMap:
    def test_every_owned_table_has_exactly_one_decision(self):
        missing = sorted(set(ax.OWNERSHIP) - set(ae.ERASURE))
        assert missing == [], (
            f"OWNERSHIP tables with no erasure decision: {missing}. Add each to "
            "account_erasure.DELETED or account_erasure.KEPT (with the reason).")
        assert set(ae.ERASURE) == set(ax.OWNERSHIP)

    def test_no_table_is_both_deleted_and_kept(self):
        assert ae.DELETED & set(ae.KEPT) == set()

    def test_the_kept_set_is_exactly_the_documented_decisions(self):
        assert ae.KEPT == {
            "users": ae.TOMBSTONE,
            "billing_subscriptions": ae.KEEP_BILLING,
            "billable_meetings": ae.KEEP_BILLING,
            "billing_renewal_notices": ae.KEEP_BILLING,
            "consent_events": ae.KEEP_PROOF,
            "compliance_audit_log": ae.KEEP_PROOF,
            "activity_audit_events": ae.KEEP_SEALED,
            "security_audit_events": ae.KEEP_SCRUBBED,
            "account_security_events": ae.KEEP_SCRUBBED,
        }

    def test_excluded_tables_that_hang_off_users_are_handled(self):
        # A table the export leaves out but that holds a user's rows must be
        # deleted too, or be an actor column (SET NULL) cleared by erasure.
        for name in ax.EXCLUDED:
            table = Base.metadata.tables.get(name)
            if table is None:
                continue
            for fk in table.foreign_keys:
                if fk.column.table.name != "users":
                    continue
                handled = (ae.EXCLUDED_USER_TABLES.get(name) == fk.parent.name
                           or (name, fk.parent.name) in ae.actor_columns())
                assert handled, f"{name}.{fk.parent.name} -> users is not erased"

    def test_links_from_kept_rows_to_deleted_rows_can_be_cleared(self):
        links = ae.kept_links_to_deleted()
        assert ("consent_events", "lead_id", "leads") in links
        assert ("billable_meetings", "lead_id", "leads") in links
        for table, column, _target in links:
            assert Base.metadata.tables[table].c[column].nullable, f"{table}.{column}"

    def test_deletion_order_puts_children_before_parents(self):
        order = ae.deletion_order()
        assert set(order) == ae.DELETED
        position = {name: i for i, name in enumerate(order)}
        for name in order:
            for fk in Base.metadata.tables[name].foreign_keys:
                parent = fk.column.table.name
                if parent in position and parent != name:
                    assert position[name] < position[parent], f"{name} after {parent}"

    def test_billing_rows_cannot_be_cascaded_away_with_their_user(self):
        for name in ("billing_subscriptions", "billable_meetings"):
            (fk,) = Base.metadata.tables[name].c.user_id.foreign_keys
            assert fk.ondelete == "RESTRICT"

    def test_retention_constant(self):
        assert ae.BILLING_RETENTION_YEARS == 7


# --------------------------------------------------------------------------
# One row in EVERY owned table, for two accounts
# --------------------------------------------------------------------------

_OVERRIDES = {
    ("calendar_availability", "day_of_week"): 1,
    ("calendar_booking_pages", "duration_minutes"): 30,
}


def _value(table, column):
    if (table.name, column.name) in _OVERRIDES:
        return _OVERRIDES[(table.name, column.name)]
    kind = column.type
    if isinstance(kind, Enum):
        return list(kind.enum_class)[0] if kind.enum_class else kind.enums[0]
    if isinstance(kind, Uuid):
        return uuid.uuid4()
    if isinstance(kind, Boolean):
        return False
    if isinstance(kind, (Integer, BigInteger)):
        return 1
    if isinstance(kind, Float):
        return 1.0
    if isinstance(kind, Numeric):
        return Decimal("1")
    if isinstance(kind, DateTime):
        return NOW
    if isinstance(kind, Date):
        return date(2026, 10, 11)
    if isinstance(kind, Time):
        return time(9, 0)
    if isinstance(kind, JSON):
        return {}
    if isinstance(kind, Text):
        return "personal text " + uuid.uuid4().hex
    if isinstance(kind, String):
        return uuid.uuid4().hex[: kind.length or 32]
    raise AssertionError(f"no generator for {table.name}.{column.name} {kind!r}")


def _populate(db, user_id) -> dict[str, uuid.UUID]:
    """Insert one row into every OWNERSHIP table (and the excluded tables that
    hang off users), every foreign key pointing at this account's own rows."""
    ids: dict[str, uuid.UUID] = {"users": user_id}
    wanted = (set(ax.OWNERSHIP) | set(ae.EXCLUDED_USER_TABLES)) - {"users"}
    for table in Base.metadata.sorted_tables:
        if table.name not in wanted:
            continue
        row = {}
        for column in table.columns:
            fks = list(column.foreign_keys)
            if (table.name, column.name) in ax._PLAIN_USER_COLUMNS:
                row[column.name] = user_id      # users.id with no FK, by design
                continue
            if fks:
                target = fks[0].column.table.name
                if target in ids:
                    row[column.name] = ids[target]
                elif not column.nullable:
                    raise AssertionError(f"{table.name}.{column.name}: no parent {target}")
                continue
            if column.primary_key:
                row[column.name] = uuid.uuid4()
            elif column.nullable or column.default is not None or column.server_default is not None:
                if column.nullable and isinstance(column.type, (String, Text)):
                    row[column.name] = _value(table, column)   # personal-looking content
                elif column.nullable and isinstance(column.type, JSON):
                    # Filled, so a scrub of a JSON column (details_json...) is
                    # really tested, not trivially true on a NULL.
                    row[column.name] = {"personal": "data " + uuid.uuid4().hex}
                continue
            else:
                row[column.name] = _value(table, column)
        db.execute(insert(table).values(row))
        ids[table.name] = row["id"]
    db.commit()
    return ids


def _user(db, email, **extra):
    user = m.User(email=email, email_verified=True, full_name="Name " + email,
                  password_hash=auth_svc.hash_password(PASSWORD), **extra)
    db.add(user)
    db.commit()
    return user


def _count(db, name, user_id) -> int:
    table = Base.metadata.tables[name]
    return db.execute(select(func.count()).select_from(table)
                      .where(ax.owner_clause(name, user_id))).scalar_one()


class TestEveryTable:
    @pytest.fixture()
    def two(self, db_session):
        alice = _user(db_session, "alice@a.example", personal_country="DE",
                      phone_number="+4915112345678", signup_ip="203.0.113.7",
                      company_name="Alice GmbH")
        bob = _user(db_session, "bob@b.example")
        _populate(db_session, alice.id)
        _populate(db_session, bob.id)
        return alice, bob

    def test_the_generator_gave_each_account_one_row_everywhere(self, db_session, two):
        alice, bob = two
        for name in ax.OWNERSHIP:
            assert _count(db_session, name, alice.id) == 1, name
            assert _count(db_session, name, bob.id) == 1, name

    def test_erasure_deletes_every_owned_row_and_nothing_of_anyone_else(self, db_session, two):
        alice, bob = two
        ae.erase_account(db_session, alice, now=NOW)
        db_session.commit()
        for name, decision in ae.ERASURE.items():
            assert _count(db_session, name, bob.id) == 1, f"{name}: bob's row touched"
            if decision == ae.DELETE:
                assert _count(db_session, name, alice.id) == 0, f"{name}: not erased"
            else:
                assert _count(db_session, name, alice.id) >= 1, f"{name}: kept row gone"
        for name, column in ae.EXCLUDED_USER_TABLES.items():
            table = Base.metadata.tables[name]
            assert db_session.execute(select(func.count()).select_from(table).where(
                table.c[column] == alice.id)).scalar_one() == 0
            assert db_session.execute(select(func.count()).select_from(table).where(
                table.c[column] == bob.id)).scalar_one() == 1

    def test_kept_rows_lose_their_links_and_personal_columns(self, db_session, two):
        alice, bob = two
        ae.erase_account(db_session, alice, now=NOW)
        db_session.commit()
        for name, column, _target in ae.kept_links_to_deleted():
            table = Base.metadata.tables[name]
            values = db_session.execute(select(table.c[column]).where(
                ax.owner_clause(name, alice.id))).scalars().all()
            assert values and all(v is None for v in values), f"{name}.{column}"
        for name, columns in ae.SCRUBBED_COLUMNS.items():
            table = Base.metadata.tables[name]
            rows = db_session.execute(select(table).where(
                ax.owner_clause(name, alice.id))).mappings().all()
            for row in rows:
                if row.get("action") == security_audit.ACCOUNT_DELETED:
                    continue
                assert all(row[c] is None for c in columns), f"{name}: {dict(row)}"
        # bob's kept rows are untouched
        consent = Base.metadata.tables["consent_events"]
        assert db_session.execute(select(consent.c.lead_id).where(
            consent.c.user_id == bob.id)).scalar_one() is not None

    def test_the_users_row_becomes_a_non_personal_tombstone(self, db_session, two):
        alice, _ = two
        alice_id = alice.id
        ae.erase_account(db_session, alice, now=NOW)
        db_session.commit()
        users = Base.metadata.tables["users"]
        row = db_session.execute(select(users).where(users.c.id == alice_id)).mappings().one()
        assert row["email"] == f"deleted-{alice_id.hex}@deleted.invalid"
        assert row["password_hash"] is None
        assert row["is_suspended"] is True
        assert row["suspended_reason"] == ae.TOMBSTONE_REASON
        for column, value in row.items():
            if column in ae.TOMBSTONE_KEEPS:
                continue
            assert value in (None, False, {}), f"users.{column} kept {value!r}"
        assert "alice" not in str(dict(row)).lower()

    def test_the_erasure_runs_with_foreign_keys_enforced(self):
        # The unit-test harness leaves SQLite's foreign keys OFF. PostgreSQL
        # enforces them, so the same erasure runs here with them ON: a wrong
        # deletion order, or a kept row left pointing at a deleted one, raises.
        from sqlalchemy import create_engine, event  # noqa: PLC0415
        from sqlalchemy.orm import sessionmaker  # noqa: PLC0415
        from sqlalchemy.pool import StaticPool  # noqa: PLC0415

        engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                               poolclass=StaticPool)

        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_connection, _record):
            dbapi_connection.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(engine)
        db = sessionmaker(bind=engine, expire_on_commit=False)()
        try:
            alice = _user(db, "alice@a.example")
            bob = _user(db, "bob@b.example")
            _populate(db, alice.id)
            _populate(db, bob.id)
            assert db.execute(select(func.count()).select_from(
                Base.metadata.tables["leads"])).scalar_one() == 2
            ae.erase_account(db, alice, now=NOW)
            db.commit()
            for name, decision in ae.ERASURE.items():
                assert _count(db, name, bob.id) == 1, name
                if decision == ae.DELETE:
                    assert _count(db, name, alice.id) == 0, name
            # and the database itself refuses to cascade billing away (E1)
            from sqlalchemy.exc import IntegrityError  # noqa: PLC0415

            users = Base.metadata.tables["users"]
            with pytest.raises(IntegrityError):
                db.execute(users.delete().where(users.c.id == alice.id))
            db.rollback()
        finally:
            db.close()
            engine.dispose()

    def test_the_erasure_record_has_no_personal_data(self, db_session, two):
        alice, _ = two
        alice_id = alice.id
        summary = ae.erase_account(db_session, alice, now=NOW,
                                   billing={"subscription": "canceled_locally"})
        db_session.commit()
        (event,) = db_session.execute(select(m.SecurityAuditEvent).where(
            m.SecurityAuditEvent.action == security_audit.ACCOUNT_DELETED)).scalars().all()
        assert event.user_id == alice_id
        assert event.ip is None and event.user_agent is None
        assert "alice" not in str(event.details_json).lower()
        assert event.details_json["billing_retention_years"] == 7
        assert event.details_json["kept"]["consent_events"] == ae.KEEP_PROOF
        assert summary["rows_deleted"]["leads"] == 1


# --------------------------------------------------------------------------
# Focused scenarios
# --------------------------------------------------------------------------

def _account(db, email, **extra):
    user = _user(db, email, **extra)
    product = m.Product(user_id=user.id, name=f"Product of {email}", description="d",
                        type=m.ProductType.SKILL)
    db.add(product)
    db.flush()
    strategy = m.Strategy(product_id=product.id, flow_type=m.FlowType.WITH_CLIENTS)
    db.add(strategy)
    db.flush()
    lead = m.Lead(strategy_id=strategy.id, source="apollo", external_id=uuid.uuid4().hex,
                  full_name=f"Prospect of {email}", email=f"prospect.{email}",
                  status=m.LeadStatus.VERIFIED)
    db.add(lead)
    db.flush()
    seq = m.Sequence(strategy_id=strategy.id, channel=m.ChannelType.EMAIL,
                     name=f"Seq of {email}", status=m.SequenceStatus.ACTIVE)
    db.add(seq)
    db.flush()
    db.add(m.SequenceEnrollment(sequence_id=seq.id, lead_id=lead.id,
                                status=m.EnrollmentStatus.ACTIVE))
    db.add(m.Message(sequence_id=seq.id, lead_id=lead.id, step_no=1,
                     channel=m.ChannelType.EMAIL, template="t", subject=f"Hello {email}",
                     body="body", status=m.MessageStatus.SCHEDULED, scheduled_at=NOW))
    db.add(m.GmailAccount(user_id=user.id, email_address=f"sender.{email}",
                          token_ciphertext=crypto.encrypt_json({"refresh_token": "rt-secret"}),
                          scopes="send"))
    db.add(m.IntegrationToken(user_id=user.id, provider="hubspot", value="plain-secret"))
    db.add(m.ConsentEvent(user_id=user.id, lead_id=lead.id, kind="withdrawn", channel="all",
                          identifier=f"optout.{email}", source="unsubscribe_link"))
    db.add(m.SuppressionEntry(email=f"optout.{email}", reason="unsubscribed"))
    db.commit()
    return user


@pytest.fixture()
def alice(db_session):
    return _account(db_session, "alice@a.example")


@pytest.fixture()
def bob(db_session):
    return _account(db_session, "bob@b.example")


def _delete(client, user, password=PASSWORD, confirm="DELETE", headers=None):
    return client.request("DELETE", "/me", json={"password": password, "confirm": confirm},
                          headers=headers or auth_headers(user))


class TestEndpoint:
    def test_deletes_with_password_and_typed_confirmation(self, client, db_session, alice, bob):
        alice_id = alice.id
        resp = _delete(client, alice)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["deleted"] is True
        assert body["billing_retention_years"] == 7
        assert body["kept"]["billing_subscriptions"] == ae.KEEP_BILLING
        assert "alice" not in resp.text
        db_session.expire_all()
        user = db_session.get(m.User, alice_id)
        assert ae.is_tombstone(user)
        assert _count(db_session, "leads", alice_id) == 0
        assert _count(db_session, "leads", bob.id) == 1

    def test_wrong_password_is_refused_audited_and_nothing_changes(self, client, db_session, alice):
        resp = _delete(client, alice, password="not-it")
        assert resp.status_code == 403
        assert resp.json()["detail"] == "INVALID_PASSWORD"
        events = db_session.execute(select(m.SecurityAuditEvent).where(
            m.SecurityAuditEvent.action == security_audit.ACCOUNT_DELETE_DENIED)).scalars().all()
        assert [e.user_id for e in events] == [alice.id]
        assert _count(db_session, "leads", alice.id) == 1
        assert not ae.is_tombstone(db_session.get(m.User, alice.id))

    @pytest.mark.parametrize("confirm", ["", "delete", "DELETE ", "yes"])
    def test_confirmation_must_be_typed_exactly(self, client, db_session, alice, confirm):
        resp = _delete(client, alice, confirm=confirm)
        assert resp.status_code == 422
        assert resp.json()["detail"] == "CONFIRMATION_REQUIRED"
        assert _count(db_session, "leads", alice.id) == 1

    def test_password_is_required(self, client, alice):
        resp = client.request("DELETE", "/me", json={"confirm": "DELETE"},
                              headers=auth_headers(alice))
        assert resp.status_code == 422

    def test_personal_api_key_is_refused(self, client, db_session, alice):
        _, raw = api_keys.create(db_session, alice.id, "zapier")
        db_session.commit()
        resp = _delete(client, alice, headers={"Authorization": f"Bearer {raw}"})
        assert resp.status_code == 403
        assert "API key" in resp.json()["detail"]
        assert _count(db_session, "leads", alice.id) == 1

    def test_unauthenticated_is_refused(self, anon_client):
        resp = anon_client.request("DELETE", "/me",
                                   json={"password": PASSWORD, "confirm": "DELETE"})
        assert resp.status_code == 401

    def test_an_administrator_cannot_delete_from_the_app(self, client, db_session, alice):
        alice.is_admin = True
        db_session.commit()
        resp = _delete(client, alice)
        assert resp.status_code == 409
        assert _count(db_session, "leads", alice.id) == 1

    def test_rate_limited_in_the_password_confirmation_bucket(self, client, db_session, alice,
                                                              monkeypatch):
        calls = []

        def limiter(key, bucket, setting, window):
            calls.append((key, bucket, setting))

        monkeypatch.setattr("app.api.account_data.enforce_rate_limit", limiter)
        _delete(client, alice, password="wrong")
        assert calls == [(f"acct:{alice.id}", "password_confirm", "RATE_LIMIT_AUTH")]

    def test_the_limit_refuses_before_the_password_is_tested(self, client, db_session, alice,
                                                             monkeypatch):
        from fastapi import HTTPException  # noqa: PLC0415

        def limiter(*_a):
            raise HTTPException(status_code=429, detail="Too many requests")

        monkeypatch.setattr("app.api.account_data.enforce_rate_limit", limiter)
        assert _delete(client, alice).status_code == 429
        assert _count(db_session, "leads", alice.id) == 1

    def test_old_tokens_stop_working_and_sign_in_fails(self, client, anon_client, db_session,
                                                       alice):
        headers = auth_headers(alice)
        tokens = auth_sessions.issue_session_tokens(db_session, alice, persistent=True)
        db_session.commit()
        assert _delete(client, alice, headers=headers).status_code == 200
        assert anon_client.get("/auth/me", headers=headers).status_code == 401
        refresh = anon_client.post("/auth/refresh", json={"refresh_token": tokens.refresh_token})
        assert refresh.status_code == 401
        login = anon_client.post("/auth/login", json={"email": "alice@a.example",
                                                      "password": PASSWORD})
        assert login.status_code == 401

    def test_the_same_email_can_sign_up_again(self, client, anon_client, db_session, alice):
        alice_id = alice.id
        assert _delete(client, alice).status_code == 200
        assert db_session.execute(select(m.User).where(
            m.User.email == "alice@a.example")).scalar_one_or_none() is None
        resp = anon_client.post("/auth/signup", json={"email": "alice@a.example",
                                                      "password": "A-new-start-1!"})
        assert resp.status_code == 201, resp.text
        fresh = db_session.execute(select(m.User).where(
            m.User.email == "alice@a.example")).scalar_one()
        assert fresh.id != alice_id                     # a new, empty account
        assert _count(db_session, "leads", fresh.id) == 0

    def test_credentials_and_sessions_are_gone(self, client, db_session, alice):
        alice_id = alice.id
        api_keys.create(db_session, alice_id, "zapier")
        auth_sessions.issue_session_tokens(db_session, alice, persistent=True)
        db_session.commit()
        assert _delete(client, alice).status_code == 200
        for name in ("gmail_accounts", "integration_tokens", "api_keys", "auth_sessions",
                     "linkedin_accounts", "external_crm_connections", "device_tokens",
                     "webhook_targets", "tool_integration_settings"):
            assert _count(db_session, name, alice_id) == 0, name

    def test_nothing_scheduled_can_send_afterwards(self, client, db_session, alice):
        alice_id = alice.id
        assert _delete(client, alice).status_code == 200
        pending = db_session.execute(select(func.count()).select_from(m.Message).where(
            ax.owner_clause("messages", alice_id))).scalar_one()
        assert pending == 0
        record = db_session.execute(select(m.SecurityAuditEvent).where(
            m.SecurityAuditEvent.action == security_audit.ACCOUNT_DELETED)).scalar_one()
        assert record.details_json["outreach"] == {"messages_cancelled": 1,
                                                   "enrollments_stopped": 1}

    def test_e2_consent_proof_and_suppression_survive(self, client, db_session, alice):
        alice_id = alice.id
        assert _delete(client, alice).status_code == 200
        event = db_session.execute(select(m.ConsentEvent).where(
            m.ConsentEvent.user_id == alice_id)).scalar_one()
        assert event.kind == "withdrawn"
        assert event.identifier == "optout.alice@a.example"
        assert event.lead_id is None
        assert db_session.execute(select(m.SuppressionEntry).where(
            m.SuppressionEntry.email == "optout.alice@a.example")).scalar_one()


# --------------------------------------------------------------------------
# E1 -- billing
# --------------------------------------------------------------------------

class TestBilling:
    def _subscribe(self, db, user, **kw):
        sub = m.BillingSubscription(user_id=user.id, billing_model="monthly", tier="growth",
                                    status="active", stripe_customer_id="cus_1",
                                    stripe_subscription_id="sub_1", **kw)
        db.add(sub)
        db.commit()
        return sub

    def test_live_subscription_is_cancelled_now_in_stripe(self, client, db_session, alice,
                                                          monkeypatch):
        monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_123")
        cancelled = []
        monkeypatch.setattr(stripe_billing, "cancel_subscription_now",
                            lambda sid: cancelled.append(sid) or {"status": "canceled"})
        monkeypatch.setattr(stripe_billing, "cancel_subscription_at_period_end",
                            lambda sid: pytest.fail("must cancel now, not at period end"))
        sub = self._subscribe(db_session, alice)
        sub_id = sub.id
        assert _delete(client, alice).status_code == 200
        assert cancelled == ["sub_1"]
        db_session.expire_all()
        kept = db_session.get(m.BillingSubscription, sub_id)
        assert kept is not None and kept.status == "canceled"
        assert kept.stripe_customer_id == "cus_1" and kept.stripe_subscription_id == "sub_1"

    def _refused_and_untouched(self, resp, db_session, alice):
        assert resp.status_code == 502, resp.text
        assert "nothing was deleted" in resp.json()["detail"]
        db_session.expire_all()
        assert _count(db_session, "leads", alice.id) == 1
        assert not ae.is_tombstone(db_session.get(m.User, alice.id))
        assert billing.get_subscription(db_session, alice.id).status == "active"

    def test_stripe_unreachable_means_nothing_is_erased(self, client, db_session, alice,
                                                        monkeypatch):
        monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_123")

        def down(_sid):
            raise stripe_billing.StripeError("stripe transport error: connection reset")

        monkeypatch.setattr(stripe_billing, "cancel_subscription_now", down)
        monkeypatch.setattr(stripe_billing, "retrieve_subscription", down)
        self._subscribe(db_session, alice)
        resp = _delete(client, alice)
        self._refused_and_untouched(resp, db_session, alice)
        assert "did not confirm" in resp.json()["detail"]   # not "could not reach"

    def test_a_real_subscription_without_a_stripe_key_fails_closed(self, client, db_session,
                                                                    alice, monkeypatch):
        # Review M-H: a live (is_stub=False) subscription on a server with no
        # STRIPE_SECRET_KEY used to be cancelled LOCALLY while Stripe kept
        # charging the deleted customer.
        monkeypatch.setattr(settings, "stripe_secret_key", "")
        called = []
        monkeypatch.setattr(stripe_billing, "cancel_subscription_now",
                            lambda sid: called.append(sid))
        self._subscribe(db_session, alice, is_stub=False)
        resp = _delete(client, alice)
        self._refused_and_untouched(resp, db_session, alice)
        assert "not configured" in resp.json()["detail"]
        assert called == []

    @pytest.mark.parametrize("gone", ["404", "canceled", "incomplete_expired"])
    def test_a_subscription_already_gone_in_stripe_does_not_block_deletion(
            self, client, db_session, alice, monkeypatch, gone):
        monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_123")

        def refuse(_sid):
            raise stripe_billing.StripeError("stripe HTTP 400: refused")

        def retrieve(_sid):
            if gone == "404":
                raise stripe_billing.StripeError("stripe HTTP 404: No such subscription")
            return {"id": "sub_1", "status": gone}

        monkeypatch.setattr(stripe_billing, "cancel_subscription_now", refuse)
        monkeypatch.setattr(stripe_billing, "retrieve_subscription", retrieve)
        self._subscribe(db_session, alice)
        assert _delete(client, alice).status_code == 200
        db_session.expire_all()
        assert billing.get_subscription(db_session, alice.id).status == "canceled"
        record = db_session.execute(select(m.SecurityAuditEvent).where(
            m.SecurityAuditEvent.action == security_audit.ACCOUNT_DELETED)).scalar_one()
        assert record.details_json["billing"]["subscription"] == "already_canceled_in_stripe"

    def test_a_still_active_subscription_after_a_refusal_fails_closed(
            self, client, db_session, alice, monkeypatch):
        monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_123")

        def refuse(_sid):
            raise stripe_billing.StripeError("stripe HTTP 400: refused")

        monkeypatch.setattr(stripe_billing, "cancel_subscription_now", refuse)
        monkeypatch.setattr(stripe_billing, "retrieve_subscription",
                            lambda sid: {"id": sid, "status": "active"})
        self._subscribe(db_session, alice)
        self._refused_and_untouched(_delete(client, alice), db_session, alice)

    def test_stub_mode_cancels_locally(self, client, db_session, alice, monkeypatch):
        monkeypatch.setattr(settings, "stripe_secret_key", "")
        monkeypatch.setattr(stripe_billing, "cancel_subscription_now",
                            lambda sid: pytest.fail("stub mode must not call Stripe"))
        self._subscribe(db_session, alice, is_stub=True)
        assert _delete(client, alice).status_code == 200
        db_session.expire_all()
        assert billing.get_subscription(db_session, alice.id).status == "canceled"

    def test_meetings_are_kept_anonymised_and_uncharged_ones_waived(self, client, db_session,
                                                                     alice):
        lead = db_session.execute(select(m.Lead).where(
            ax.owner_clause("leads", alice.id))).scalar_one()
        common = dict(user_id=alice.id, lead_id=lead.id, strategy_id=lead.strategy_id,
                      source="calendly", amount_cents=5000, occurred_at=NOW,
                      charge_after=NOW + timedelta(days=2))
        charged = m.BillableMeeting(dedupe_key="a", status="charged", stripe_invoice_id="in_1",
                                    resolution_note="Prospect Jane said yes", **common)
        pending = m.BillableMeeting(dedupe_key="b", status="pending", **common)
        disputed = m.BillableMeeting(dedupe_key="c", status="disputed",
                                     resolution_note="Jane Doe never showed", **common)
        db_session.add_all([charged, pending, disputed])
        db_session.commit()
        ids = (charged.id, pending.id, disputed.id)
        assert _delete(client, alice).status_code == 200
        db_session.expire_all()
        charged, pending, disputed = (db_session.get(m.BillableMeeting, i) for i in ids)
        assert charged.status == "charged" and charged.amount_cents == 5000
        assert charged.stripe_invoice_id == "in_1"
        assert charged.lead_id is None and charged.strategy_id is None
        assert charged.resolution_note is None
        for meeting in (pending, disputed):
            assert meeting.status == "waived"
            assert meeting.resolution_note == ae.WAIVED_ON_ACCOUNT_DELETION


# --------------------------------------------------------------------------
# E3 -- workspaces
# --------------------------------------------------------------------------

def _workspace(db, owner, member, role="manager"):
    ws = m.Workspace(owner_user_id=owner.id, name="Co", slug=f"co-{uuid.uuid4().hex[:8]}")
    db.add(ws)
    db.flush()
    db.add(m.WorkspaceMember(workspace_id=ws.id, user_id=member.id, role=role))
    db.add(m.WorkspaceInvitation(workspace_id=ws.id, email=member.email, role=role,
                                 token_hash=uuid.uuid4().hex, expires_at=NOW,
                                 accepted_at=NOW))
    db.commit()
    return ws


class TestWorkspaces:
    def test_owner_deletion_removes_the_workspace_and_its_memberships_not_members(
            self, client, db_session, alice, bob):
        ws = _workspace(db_session, alice, bob)
        ws_id, bob_id = ws.id, bob.id
        assert _delete(client, alice).status_code == 200
        db_session.expire_all()
        assert db_session.get(m.Workspace, ws_id) is None
        assert db_session.execute(select(m.WorkspaceMember).where(
            m.WorkspaceMember.workspace_id == ws_id)).first() is None
        assert db_session.execute(select(m.WorkspaceInvitation).where(
            m.WorkspaceInvitation.workspace_id == ws_id)).first() is None
        survivor = db_session.get(m.User, bob_id)
        assert survivor.email == "bob@b.example" and not ae.is_tombstone(survivor)
        assert _count(db_session, "leads", bob_id) == 1

    def test_member_deletion_removes_only_the_member(self, client, db_session, alice, bob):
        ws = _workspace(db_session, alice, bob)
        alice_lead = db_session.execute(select(m.Lead).where(
            ax.owner_clause("leads", alice.id))).scalar_one()
        db_session.add(m.CrmNote(lead_id=alice_lead.id, author_user_id=bob.id, body="note"))
        db_session.commit()
        ws_id, bob_id, alice_id = ws.id, bob.id, alice.id
        assert _delete(client, bob).status_code == 200
        db_session.expire_all()
        assert db_session.get(m.Workspace, ws_id) is not None
        assert _count(db_session, "leads", alice_id) == 1
        assert db_session.execute(select(m.WorkspaceMember).where(
            m.WorkspaceMember.user_id == bob_id)).first() is None
        # the invitation addressed to bob's email is personal data: gone
        assert db_session.execute(select(m.WorkspaceInvitation).where(
            m.WorkspaceInvitation.workspace_id == ws_id)).first() is None
        # alice's note survives; it just no longer points at the deleted person
        note = db_session.execute(select(m.CrmNote).where(
            m.CrmNote.lead_id == alice_lead.id)).scalar_one()
        assert note.author_user_id is None

    def test_the_workspace_header_never_deletes_the_owner(self, client, db_session, alice, bob):
        ws = _workspace(db_session, alice, bob)
        alice_id, bob_id = alice.id, bob.id
        resp = _delete(client, bob, headers={**auth_headers(bob), "X-Workspace-Id": str(ws.id)})
        assert resp.status_code == 200, resp.text
        db_session.expire_all()
        assert ae.is_tombstone(db_session.get(m.User, bob_id))
        assert not ae.is_tombstone(db_session.get(m.User, alice_id))
        assert _count(db_session, "leads", alice_id) == 1


# --------------------------------------------------------------------------
# Small pieces
# --------------------------------------------------------------------------

def test_the_accounts_files_are_removed_and_nobody_elses(client, db_session, alice, bob,
                                                         tmp_path, monkeypatch):
    # Review 3: voicemail audio and workspace logos were left on disk.
    monkeypatch.setattr(settings, "media_dir", str(tmp_path))
    (tmp_path / "voicemails").mkdir()
    (tmp_path / "branding").mkdir()

    def files_for(user, tag):
        lead = db_session.execute(select(m.Lead).where(
            ax.owner_clause("leads", user.id))).scalar_one()
        voice = uuid.uuid4().hex + ".mp3"
        (tmp_path / "voicemails" / voice).write_bytes(b"audio")
        db_session.add(m.Call(lead_id=lead.id, provider="vapi", to_number="+15550100",
                              status="completed",
                              voicemail_audio_url=f"https://api.example/media/voicemails/{voice}"))
        ws = m.Workspace(owner_user_id=user.id, name=tag, slug=f"{tag}-{uuid.uuid4().hex[:6]}")
        db_session.add(ws)
        db_session.flush()
        logo = tmp_path / "branding" / f"{ws.id.hex}-abc12345.png"
        logo.write_bytes(b"png")
        bg = tmp_path / f"bg_{user.id.hex}_abc.png"
        bg.write_bytes(b"png")
        db_session.commit()
        return [tmp_path / "voicemails" / voice, logo, bg]

    mine, theirs = files_for(alice, "alice"), files_for(bob, "bob")
    assert _delete(client, alice).status_code == 200
    assert [p.exists() for p in mine] == [False, False, False]
    assert [p.exists() for p in theirs] == [True, True, True]


def test_a_voicemail_url_cannot_steer_a_deletion_outside_the_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "media_dir", str(tmp_path))
    paths = ae._voicemail_paths(["https://x/media/voicemails/../../etc/passwd",
                                 "https://x/media/voicemails/" + "a" * 32 + ".mp3"])
    assert paths == [tmp_path / "voicemails" / ("a" * 32 + ".mp3")]


def test_is_tombstone():
    assert not ae.is_tombstone(None)
    assert not ae.is_tombstone(m.User(email="a@b.example"))
    assert ae.is_tombstone(m.User(email=ae.tombstone_email(uuid.uuid4())))
    assert ae.is_tombstone(m.User(email="x@y.example", suspended_reason=ae.TOMBSTONE_REASON))


# --------------------------------------------------------------------------
# Review fixes (2026-10-11): what survived the first version
# --------------------------------------------------------------------------

class TestWhatSurvivedBefore:
    def test_refused_signins_recorded_by_email_only_are_scrubbed(self, client, db_session,
                                                                  alice, bob):
        # A VPN-blocked sign-in is recorded with NO user id, only the address.
        from app.services import identity  # noqa: PLC0415

        identity.record_event(db_session, event="login_blocked_vpn", email="ALICE@a.example",
                              ip="198.51.100.9", details={"reasons": ["vpn"]})
        identity.record_event(db_session, event="login_blocked_vpn", email=bob.email,
                              ip="198.51.100.10", details={"reasons": ["vpn"]})
        db_session.commit()
        assert _delete(client, alice).status_code == 200
        db_session.expire_all()
        rows = db_session.execute(select(m.AccountSecurityEvent).where(
            m.AccountSecurityEvent.event == "login_blocked_vpn")).scalars().all()
        mine = [r for r in rows if r.ip is None]
        assert len(mine) == 1 and mine[0].email is None and mine[0].details_json is None
        theirs = [r for r in rows if r.email == bob.email]
        assert len(theirs) == 1 and theirs[0].ip == "198.51.100.10"

    # Gate change 2026-11-02 (docs/program/plans/gate-linkedin-unipile-removal.md):
    # Unipile was removed; legacy LinkedIn sessions are ended once by
    # scripts/linkedin_unipile_offboard.py (tests/test_linkedin_unipile_offboard.py).
    # What erasure must still guarantee: the legacy rows go with the account,
    # the record says they existed, and no outside call is attempted.
    def test_legacy_linkedin_rows_are_erased_and_recorded(self, client, db_session, alice):
        for ref in ("acc-1", "acc-2"):
            db_session.add(m.LinkedInAccount(user_id=alice.id, unipile_account_id=ref))
        db_session.commit()
        assert _delete(client, alice).status_code == 200
        record = db_session.execute(select(m.SecurityAuditEvent).where(
            m.SecurityAuditEvent.action == security_audit.ACCOUNT_DELETED)).scalar_one()
        assert record.details_json["providers"]["unipile"] == "offboarded_by_script"
        assert record.details_json["providers"]["accounts"] == 2
        assert _count(db_session, "linkedin_accounts", alice.id) == 0

    def test_linkedin_tasks_and_consents_go_with_the_account(self, client, db_session, alice):
        from app.services import linkedin_consent, linkedin_tasks  # noqa: PLC0415

        linkedin_consent.record(db_session, user_id=alice.id,
                                scope=linkedin_consent.CONNECTIONS_IMPORT,
                                text_version=linkedin_consent.current_text(
                                    linkedin_consent.CONNECTIONS_IMPORT).version,
                                lawful_basis="legitimate_interests")
        linkedin_tasks.create(db_session, alice, source="network", kind="message", text="hi",
                              profile_url="https://www.linkedin.com/in/someone/",
                              claim_checked=True)
        db_session.commit()
        assert _delete(client, alice).status_code == 200
        assert _count(db_session, "linkedin_tasks", alice.id) == 0
        assert _count(db_session, "linkedin_consents", alice.id) == 0

    def test_without_legacy_rows_it_says_none(self, client, db_session, alice):
        assert _delete(client, alice).status_code == 200
        record = db_session.execute(select(m.SecurityAuditEvent).where(
            m.SecurityAuditEvent.action == security_audit.ACCOUNT_DELETED)).scalar_one()
        assert record.details_json["providers"]["unipile"] == "none"

    def test_the_security_audit_log_line_carries_no_ip(self, db_session, caplog):
        import logging  # noqa: PLC0415

        from starlette.requests import Request  # noqa: PLC0415

        request = Request({"type": "http", "headers": [(b"user-agent", b"UA")],
                           "client": ("203.0.113.77", 1234), "method": "GET", "path": "/"})
        with caplog.at_level(logging.INFO, logger="app.services.security_audit"):
            event = security_audit.record(db_session, action="probe", request=request,
                                          user_id=uuid.uuid4())
        assert "203.0.113.77" not in caplog.text
        assert "SECURITY AUDIT action=probe" in caplog.text
        assert event.ip is not None          # the row still has it (and erasure scrubs it)


class TestRefreshAfterErasure:
    def test_a_legacy_refresh_token_cannot_recreate_a_session(self, client, anon_client,
                                                              db_session, alice, monkeypatch):
        monkeypatch.setattr(settings, "auth_accept_legacy_refresh_tokens", True)
        alice_id = alice.id
        legacy = auth_svc._issue(alice_id, "refresh", 3600)     # pre-session: no jti
        assert _delete(client, alice).status_code == 200
        resp = anon_client.post("/auth/refresh", json={"refresh_token": legacy},
                                headers={"User-Agent": "probe-UA/1.0"})
        assert resp.status_code == 401
        sessions = db_session.execute(select(func.count()).select_from(m.AuthSession).where(
            m.AuthSession.user_id == alice_id)).scalar_one()
        assert sessions == 0

    def test_a_grace_window_refresh_cannot_mint_an_access_token(self, db_session, alice):
        from fastapi import HTTPException  # noqa: PLC0415

        tokens = auth_sessions.issue_session_tokens(db_session, alice, persistent=True)
        db_session.commit()
        rotated = auth_sessions.rotate(db_session, tokens.refresh_token)   # now "rotated"
        assert rotated.refresh_token
        ae.erase_account(db_session, alice, now=NOW)
        db_session.commit()
        # the old token again, inside the grace window: the row was deleted,
        # but even a surviving row must not yield tokens for a tombstone
        with pytest.raises(HTTPException) as exc:
            auth_sessions.rotate(db_session, tokens.refresh_token)
        assert exc.value.status_code == 401


class TestCallbacksRefuseErasedAccounts:
    """A signed OAuth/notify state outlives the account that asked for it."""

    @pytest.fixture()
    def erased(self, client, db_session, alice):
        alice_id = alice.id
        assert _delete(client, alice).status_code == 200
        return alice_id

    @pytest.mark.parametrize("provider", ["hubspot", "salesforce"])
    def test_crm_callback(self, anon_client, db_session, erased, provider, monkeypatch):
        from app.api import crm_integrations  # noqa: PLC0415
        from app.services import oauth_state  # noqa: PLC0415

        monkeypatch.setattr(crm_integrations, "_app_credentials",
                            lambda db, p: pytest.fail("must refuse before any provider call"))
        state = oauth_state.make(erased, provider)
        resp = anon_client.get(f"/integrations/{provider}/callback",
                               params={"code": "c", "state": state}, follow_redirects=False)
        assert resp.status_code in (302, 303, 307)
        assert "unknown" in resp.headers["location"]
        assert _count(db_session, "external_crm_connections", erased) == 0

    def test_slack_callback(self, anon_client, db_session, erased, monkeypatch):
        from app.api import slack_integration  # noqa: PLC0415
        from app.services import oauth_state  # noqa: PLC0415

        monkeypatch.setattr(slack_integration, "_app",
                            lambda db: pytest.fail("must refuse before any provider call"))
        state = oauth_state.make(erased, "slack")
        resp = anon_client.get("/integrations/slack/callback",
                               params={"code": "c", "state": state}, follow_redirects=False)
        assert "unknown" in resp.headers["location"]

    def test_the_unipile_callback_no_longer_exists(self, anon_client, db_session, erased):
        # Gate change 2026-11-02: the hosted-auth callback was removed with
        # Unipile, so an erased account cannot be re-linked through it.
        resp = anon_client.post("/integrations/linkedin/unipile/notify",
                                params={"state": "x" * 40},
                                json={"status": "CREATION_SUCCESS", "account_id": "acc-9"})
        assert resp.status_code in (404, 405)
        assert _count(db_session, "linkedin_accounts", erased) == 0

    def test_gmail_callback(self, anon_client, db_session, erased, monkeypatch):
        from app.api import integrations as gmail_api  # noqa: PLC0415

        monkeypatch.setattr(gmail_api, "get_oauth",
                            lambda: pytest.fail("must refuse before any provider call"))
        state = gmail_api.make_state(erased)
        resp = anon_client.get("/integrations/gmail/callback",
                               params={"code": "c", "state": state})
        assert resp.status_code == 404
        assert _count(db_session, "gmail_accounts", erased) == 0


class TestUnsubscribeAfterErasure:
    """CAN-SPAM 15 U.S.C. 7704(a)(4): an opt-out must be honoured even when
    the sender's records are gone."""

    def _token(self, db, user):
        from app.services import sequence_engine  # noqa: PLC0415

        lead = db.execute(select(m.Lead).where(ax.owner_clause("leads", user.id))).scalar_one()
        return sequence_engine.make_unsubscribe_token(lead.id, lead.email), lead.email

    def test_after_the_account_is_deleted_the_address_is_suppressed(self, client, anon_client,
                                                                   db_session, alice):
        token, address = self._token(db_session, alice)
        assert _delete(client, alice).status_code == 200
        resp = anon_client.get(f"/unsubscribe/{token}")
        assert resp.status_code == 200
        assert db_session.execute(select(m.SuppressionEntry).where(
            m.SuppressionEntry.email == address)).scalar_one()
        event = db_session.execute(select(m.ConsentEvent).where(
            m.ConsentEvent.identifier == address,
            m.ConsentEvent.source == "unsubscribe_link")).scalar_one()
        assert event.kind == "suppressed" and event.lead_id is None

    def test_after_the_lead_is_erased_it_is_recorded_too(self, client, anon_client,
                                                        db_session, alice):
        token, address = self._token(db_session, alice)
        lead_id = db_session.execute(select(m.Lead.id).where(
            ax.owner_clause("leads", alice.id))).scalar_one()
        assert client.delete(f"/leads/{lead_id}", headers=auth_headers(alice)).status_code == 204
        assert anon_client.post(f"/unsubscribe/{token}").status_code == 200
        assert db_session.execute(select(m.ConsentEvent).where(
            m.ConsentEvent.identifier == address,
            m.ConsentEvent.source == "unsubscribe_link")).scalar_one()

    def test_an_old_token_without_the_address_still_answers(self, client, anon_client,
                                                           db_session, alice):
        from app.services import sequence_engine  # noqa: PLC0415

        lead_id = db_session.execute(select(m.Lead.id).where(
            ax.owner_clause("leads", alice.id))).scalar_one()
        token = sequence_engine.make_unsubscribe_token(lead_id)
        assert _delete(client, alice).status_code == 200
        assert anon_client.get(f"/unsubscribe/{token}").status_code == 200

    def test_the_sent_email_carries_the_address_in_its_token(self, db_session, alice):
        from app.services import sequence_engine  # noqa: PLC0415

        lead = db_session.execute(select(m.Lead).where(
            ax.owner_clause("leads", alice.id))).scalar_one()
        _subject, _body, _headers, token = sequence_engine.build_compliant_email(lead, "s", "b")
        assert sequence_engine.parse_unsubscribe_payload(token) == (lead.id, lead.email)
