"""L0 assisted LinkedIn tasks: Vectorae drafts, the USER acts on LinkedIn.

Pins Phase 5B (evals/vectorae) rules for L0, offline:
  * per-task approval, expiring after 24h; no bulk approve endpoint;
  * suppression checked at creation AND at open; an unsubscribe cancels open
    tasks for the person in every account;
  * daily caps (20 notes / 50 messages per user per local day), lowerable
    only; business hours only;
  * claim-check is required before a task is queued;
  * every action writes an audit event; tasks are owner-only (404 otherwise).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.db import models as m
from app.services import linkedin_tasks as lt
from app.services import system_settings

from .conftest import NOW, auth_headers

URL = "https://www.linkedin.com/in/ana-lopez/"


def _task(db, user, *, kind="message", url=URL, text="Hi Ana, quick idea for Acme?"):
    t = lt.create(db, user, source="network", kind=kind, text=text, profile_url=url,
                  claim_checked=True)
    db.commit()
    return t


def _approved(db, user, **kw):
    t = _task(db, user, **kw)
    lt.approve(db, user, t.id, now=NOW)
    db.commit()
    return t


def _suppress(db, url=URL):
    db.add(m.LinkedInSuppression(profile=url.rstrip("/").rsplit("/", 1)[-1].lower(),
                                 reason="unsubscribe"))
    db.commit()


def _actions(db, user):
    return [e.action for e in db.execute(
        select(m.SecurityAuditEvent).where(m.SecurityAuditEvent.user_id == user.id)
        .order_by(m.SecurityAuditEvent.created_at)).scalars()]


class TestCreate:
    def test_requires_the_claim_check(self, db_session, test_user):
        with pytest.raises(ValueError, match="claim check"):
            lt.create(db_session, test_user, source="network", kind="message", text="x",
                      profile_url=URL, claim_checked=False)

    def test_only_member_profile_links(self, db_session, test_user):
        for bad in ("https://www.linkedin.com/company/acme/", "https://example.com/in/ana",
                    "https://www.linkedin.com/feed/update/123", ""):
            with pytest.raises(lt.TaskError) as exc:
                lt.create(db_session, test_user, source="network", kind="message", text="x",
                          profile_url=bad, claim_checked=True)
            assert exc.value.status == 422

    def test_stores_a_plain_public_profile_link(self, db_session, test_user):
        t = _task(db_session, test_user,
                  url="https://linkedin.com/in/Ana-Lopez?trk=abc&utm=1")
        assert t.profile_url == "https://www.linkedin.com/in/Ana-Lopez/"
        assert t.profile_key == "ana-lopez" and t.status == "pending"

    def test_suppressed_person_gets_no_task(self, db_session, test_user):
        _suppress(db_session)
        with pytest.raises(lt.TaskError) as exc:
            _task(db_session, test_user)
        assert exc.value.status == 409
        assert db_session.query(m.LinkedInTask).count() == 0

    def test_connection_note_length(self, db_session, test_user):
        with pytest.raises(lt.TaskError):
            _task(db_session, test_user, kind="connection_note", text="x" * 301)


class TestApproval:
    def test_approval_expires_after_24_hours(self, db_session, test_user):
        t = _approved(db_session, test_user)
        assert t.approval_expires_at - t.approved_at == timedelta(hours=24)
        with pytest.raises(lt.TaskError) as exc:
            lt.open_task(db_session, test_user, t.id, now=NOW + timedelta(hours=24, minutes=1))
        assert exc.value.status == 409 and "expired" in exc.value.detail
        assert t.status == "expired"

    def test_open_needs_an_approval(self, db_session, test_user):
        t = _task(db_session, test_user)
        with pytest.raises(lt.TaskError, match="Approve"):
            lt.open_task(db_session, test_user, t.id, now=NOW)

    def test_editing_withdraws_the_approval(self, db_session, test_user):
        t = _approved(db_session, test_user)
        lt.edit(db_session, test_user, t.id, "Different text")
        assert t.status == "pending" and t.approval_expires_at is None

    def test_there_is_no_bulk_approve_route(self, client):
        from app.main import app  # noqa: PLC0415

        paths = {getattr(r, "path", "") for r in app.routes}
        li = {p for p in paths if p.startswith("/linkedin-tasks")}
        assert li and not {p for p in li if "bulk" in p or "approve-all" in p}
        assert "/linkedin-tasks/{task_id}/approve" in li


class TestSuppression:
    def test_rechecked_at_open(self, db_session, test_user):
        t = _approved(db_session, test_user)
        _suppress(db_session)
        with pytest.raises(lt.TaskError) as exc:
            lt.open_task(db_session, test_user, t.id, now=NOW)
        assert exc.value.status == 409
        assert t.status == "cancelled" and t.cancel_reason == "suppressed"

    def test_unsubscribe_cancels_open_tasks_in_every_account(self, db_session, test_user,
                                                             verified_strategy):
        from app.services import consent  # noqa: PLC0415

        other = m.User(email="other-li@x.test", email_verified=True)
        db_session.add(other)
        db_session.commit()
        mine, theirs = _task(db_session, test_user), _task(db_session, other)
        lead = m.Lead(strategy_id=verified_strategy.id, source="apollo", external_id="LI-T1",
                      full_name="Ana Lopez", company="Acme", status=m.LeadStatus.VERIFIED,
                      email="ana@acme.test", linkedin_url=URL)
        db_session.add(lead)
        db_session.commit()
        consent.suppress_everywhere(db_session, lead, source="test", reason="unsubscribe")
        db_session.commit()
        assert {mine.status, theirs.status} == {"cancelled"}


class TestCapsAndHours:
    def test_message_cap_is_50_and_counts_on_open(self, db_session, test_user):
        ids = [_approved(db_session, test_user, url=f"https://www.linkedin.com/in/p{i}").id
               for i in range(51)]
        for i in ids[:50]:
            lt.open_task(db_session, test_user, i, now=NOW)
        with pytest.raises(lt.TaskError) as exc:
            lt.open_task(db_session, test_user, ids[50], now=NOW)
        assert exc.value.status == 429
        # Next local day (09:00, inside the approval's 24h): allowed again.
        lt.open_task(db_session, test_user, ids[50], now=NOW + timedelta(hours=23))

    def test_connection_note_cap_is_20(self, db_session, test_user):
        ids = [_approved(db_session, test_user, kind="connection_note",
                         url=f"https://www.linkedin.com/in/n{i}").id for i in range(21)]
        for i in ids[:20]:
            lt.open_task(db_session, test_user, i, now=NOW)
        with pytest.raises(lt.TaskError) as exc:
            lt.open_task(db_session, test_user, ids[20], now=NOW)
        assert exc.value.status == 429

    def test_settings_can_lower_but_not_raise_the_cap(self, db_session, test_user, monkeypatch):
        real = system_settings.get

        def fake(db, key):
            if key == "linkedin_daily_connection_limit":
                return 500
            if key == "linkedin_daily_message_limit":
                return 2
            return real(db, key)
        monkeypatch.setattr(system_settings, "get", fake)
        assert lt._cap(db_session, "connection_note") == 20
        assert lt._cap(db_session, "message") == 2

    def test_business_hours_only(self, db_session, test_user):
        t = _approved(db_session, test_user)
        night = datetime(2026, 8, 11, 23, 30, tzinfo=timezone.utc)
        with pytest.raises(lt.TaskError, match="business hours"):
            lt.open_task(db_session, test_user, t.id, now=night)


class TestCompleteAndAudit:
    def test_full_lifecycle_is_audited(self, db_session, test_user):
        t = _approved(db_session, test_user)
        payload = lt.open_task(db_session, test_user, t.id, now=NOW)
        assert payload["profile_url"] == URL and payload["text"].startswith("Hi Ana")
        lt.complete(db_session, test_user, t.id, "replied", now=NOW)
        db_session.commit()
        assert t.status == "replied"
        assert _actions(db_session, test_user) == [
            lt.TASK_CREATED, lt.TASK_APPROVED, lt.TASK_OPENED, lt.TASK_COMPLETED]

    def test_audit_never_holds_the_message_text(self, db_session, test_user):
        _approved(db_session, test_user, text="SECRET-PITCH-TEXT")
        for e in db_session.execute(select(m.SecurityAuditEvent)).scalars():
            assert "SECRET-PITCH-TEXT" not in str(e.details_json)

    def test_done_requires_open(self, db_session, test_user):
        t = _approved(db_session, test_user)
        with pytest.raises(lt.TaskError):
            lt.complete(db_session, test_user, t.id, "done", now=NOW)
        lt.complete(db_session, test_user, t.id, "skipped", now=NOW)
        assert t.status == "skipped"


class TestApi:
    def test_owner_only(self, client, db_session, test_user):
        t = _task(db_session, test_user)
        other = m.User(email="o2@x.test", email_verified=True)
        db_session.add(other)
        db_session.commit()
        h = auth_headers(other)
        assert client.post(f"/linkedin-tasks/{t.id}/approve", headers=h).status_code == 404
        assert client.post(f"/linkedin-tasks/{t.id}/open", headers=h).status_code == 404
        assert client.get("/linkedin-tasks", headers=h).json()["tasks"] == []

    def test_approve_then_open_over_http(self, client, db_session, test_user, monkeypatch):
        monkeypatch.setattr(lt, "_now", lambda: NOW)
        t = _task(db_session, test_user)
        r = client.post(f"/linkedin-tasks/{t.id}/approve")
        assert r.status_code == 200 and r.json()["status"] == "approved"
        r = client.post(f"/linkedin-tasks/{t.id}/open")
        assert r.status_code == 200 and r.json()["profile_url"] == URL
        r = client.post(f"/linkedin-tasks/{t.id}/complete", json={"outcome": "done"})
        assert r.json()["status"] == "done"


class TestNetworkBridge:
    def test_saved_network_message_becomes_a_task_and_done_marks_it_sent(
            self, client, db_session, test_user, monkeypatch):
        monkeypatch.setattr(lt, "_now", lambda: NOW)
        test_user.plan = m.PlanTier.GROWTH
        product = m.Product(user=test_user, name="Fire SEO", type=m.ProductType.SKILL,
                            description="Local SEO for fire protection companies")
        conn = m.LinkedInConnection(user_id=test_user.id, dedupe_key="k1",
                                    full_name="Ana Lopez", company="Acme", position="Owner",
                                    profile_url=URL)
        db_session.add_all([product, conn])
        db_session.commit()
        msg = m.LinkedInNetworkMessage(user_id=test_user.id, connection_id=conn.id,
                                       product_id=product.id, text="Hi Ana, idea for Acme?",
                                       status="approved")
        db_session.add(msg)
        db_session.commit()
        r = client.post("/linkedin-tasks/from-network-message", json={"message_id": str(msg.id)})
        assert r.status_code == 201 and r.json()["source"] == "network"
        tid = r.json()["id"]
        assert client.post(f"/linkedin-tasks/{tid}/approve").status_code == 200
        assert client.post(f"/linkedin-tasks/{tid}/open").status_code == 200
        assert client.post(f"/linkedin-tasks/{tid}/complete",
                           json={"outcome": "done"}).status_code == 200
        db_session.refresh(msg)
        assert (msg.status, msg.channel) == ("sent", "copy")

    def test_a_draft_that_is_not_saved_cannot_become_a_task(self, client, db_session, test_user):
        product = m.Product(user=test_user, name="P", type=m.ProductType.SKILL, description="d")
        conn = m.LinkedInConnection(user_id=test_user.id, dedupe_key="k2", full_name="B",
                                    profile_url=URL)
        db_session.add_all([product, conn])
        db_session.commit()
        msg = m.LinkedInNetworkMessage(user_id=test_user.id, connection_id=conn.id,
                                       product_id=product.id, text="x", status="draft")
        db_session.add(msg)
        db_session.commit()
        r = client.post("/linkedin-tasks/from-network-message", json={"message_id": str(msg.id)})
        assert r.status_code == 409
