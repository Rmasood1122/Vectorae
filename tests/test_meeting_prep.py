"""Feature Group 7 — meeting prep brief, reminders, outcomes, follow-up, deals.

Also covers the feature-expansion foundation it is the first user of:
TokenStore (repaired), system credentials, system settings, and the admin
endpoints over both.

What is load-bearing, and therefore what these tests insist on:
  * a booking on EITHER path produces exactly one brief, and a cancellation
    stops its reminders;
  * the lead profile in the brief is copied from the records, not written by
    the model;
  * a model failure leaves a FAILED brief, never an exception into the
    webhook or the booking task;
  * reminders are at-most-once and never fire for a started or cancelled
    meeting;
  * logging an outcome is durable even when the follow-up cannot be drafted,
    stops cold outreach, and never drafts or sends to a suppressed contact;
  * ownership, as everywhere: 404, never 403.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.db import models as m
from app.integrations.token_store import TokenStore
from app.services import credentials, meeting_prep, system_settings
from app.workers import meeting_prep_tasks

from .conftest import auth_headers

UTC = timezone.utc


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture()
def lead(db_session, verified_strategy):
    row = m.Lead(
        strategy_id=verified_strategy.id, source="apollo", external_id="MP1",
        full_name="Sara Khan", title="Owner", company="Acme Fire",
        email="sara@acme.test", status=m.LeadStatus.REPLIED,
        enrichment_json={"person": {
            "linkedin_url": "https://linkedin.com/in/sarakhan",
            "city": "Austin", "state": "Texas", "country": "United States",
            "organization": {"name": "Acme Fire", "industry": "fire protection",
                             "estimated_num_employees": 30,
                             "latest_funding_stage": "Seed"},
        }},
    )
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture()
def emitted(monkeypatch):
    """Capture event_bus.emit instead of fanning out."""
    from app.services import event_bus

    calls: list[dict] = []

    def _emit(db, user_id, event, **kwargs):
        calls.append({"user_id": user_id, "event": event, **kwargs})
        return {"event": event, "push": 0, "slack": False, "webhooks": 0}

    monkeypatch.setattr(event_bus, "emit", _emit)
    return calls


def _brief(db_session, lead, user, *, start=None, source="manual", ref=None):
    brief = meeting_prep.upsert_brief(
        db_session, lead, user_id=user.id, source=source,
        external_ref=ref or f"manual:{uuid.uuid4()}", meeting_start_at=start,
    )
    db_session.commit()
    return brief


# --------------------------------------------------------------------------
# Foundation: TokenStore, credentials, system settings
# --------------------------------------------------------------------------


class TestTokenStore:
    def test_user_scope_round_trip_and_encryption(self, db_session, test_user):
        TokenStore.set(db_session, user_id=test_user.id, provider="slack",
                       key="bot_token", value="xoxb-secret")
        assert TokenStore.get(db_session, test_user.id, "slack", "bot_token") == "xoxb-secret"
        row = db_session.query(m.IntegrationToken).one()
        assert row.token_kind == "bot_token"
        assert "xoxb-secret" not in (row.encrypted_value or "")

    def test_string_user_id_is_accepted(self, db_session, test_user):
        TokenStore.set(db_session, user_id=str(test_user.id), provider="p",
                       key="k", value="v")
        assert TokenStore.get(db_session, str(test_user.id), "p", "k") == "v"

    def test_update_in_place_including_system_scope(self, db_session):
        TokenStore.set(db_session, user_id=None, provider="openai", key="api_key", value="a")
        TokenStore.set(db_session, user_id=None, provider="openai", key="api_key", value="b")
        assert db_session.query(m.IntegrationToken).count() == 1
        assert TokenStore.get(db_session, None, "openai", "api_key") == "b"

    def test_scopes_are_isolated(self, db_session, test_user):
        TokenStore.set(db_session, user_id=None, provider="newsapi", key="api_key", value="sys")
        assert TokenStore.get(db_session, test_user.id, "newsapi", "api_key") is None

    def test_expired_token_reads_as_none(self, db_session, test_user):
        TokenStore.set(db_session, user_id=test_user.id, provider="p", key="k",
                       value="v", expires_at=datetime.now(UTC) - timedelta(seconds=1))
        assert TokenStore.get(db_session, test_user.id, "p", "k") is None

    def test_keys_and_delete(self, db_session, test_user):
        for key in ("a", "b"):
            TokenStore.set(db_session, user_id=test_user.id, provider="p", key=key, value="v")
        assert sorted(TokenStore.keys(db_session, test_user.id, "p")) == ["a", "b"]
        assert TokenStore.delete(db_session, test_user.id, "p", "a") == 1
        assert TokenStore.keys(db_session, test_user.id, "p") == ["b"]


class TestCredentials:
    def test_user_key_wins_then_system_fallback(self, db_session, test_user):
        credentials.set_system_secret(db_session, "newsapi", "api_key", "system-key")
        assert credentials.get_secret(db_session, "newsapi", "api_key", test_user.id) == "system-key"
        TokenStore.set(db_session, user_id=test_user.id, provider="newsapi",
                       key="api_key", value="user-key")
        assert credentials.get_secret(db_session, "newsapi", "api_key", test_user.id) == "user-key"

    def test_require_raises_not_configured(self, db_session):
        with pytest.raises(credentials.IntegrationNotConfigured):
            credentials.require_secret(db_session, "openai", "api_key")

    def test_status_never_contains_values(self, db_session):
        credentials.set_system_secret(db_session, "openai", "api_key", "sk-very-secret")
        status = credentials.system_status(db_session)
        assert "sk-very-secret" not in json.dumps(status)
        openai = next(p for p in status if p["provider"] == "openai")
        assert openai["configured"] is True


class TestSystemSettings:
    def test_defaults_and_typed_set(self, db_session):
        assert system_settings.get(db_session, "linkedin_daily_connection_limit") == 20
        system_settings.set(db_session, "linkedin_daily_connection_limit", 15)
        assert system_settings.get(db_session, "linkedin_daily_connection_limit") == 15

    @pytest.mark.parametrize("key,value", [
        ("meeting_prep_enabled", "yes"),
        ("linkedin_daily_connection_limit", "20"),
        ("linkedin_daily_connection_limit", True),
        ("linkedin_daily_connection_limit", -1),
        ("objection_spike_threshold", "0.2"),
    ])
    def test_wrong_types_are_refused(self, db_session, key, value):
        with pytest.raises(system_settings.SettingTypeError):
            system_settings.set(db_session, key, value)

    def test_unknown_key(self, db_session):
        with pytest.raises(KeyError):
            system_settings.get(db_session, "no_such_setting")


class TestAdminEndpoints:
    def test_non_admin_is_refused(self, client):
        assert client.get("/admin/integrations").status_code == 403
        assert client.get("/admin/system-settings").status_code == 403

    def test_admin_sets_credential_and_setting(self, client, db_session, test_user):
        test_user.is_admin = True
        db_session.commit()
        resp = client.put("/admin/integrations/openai",
                          json={"values": {"api_key": "sk-abc"}})
        assert resp.status_code == 200, resp.text
        assert "sk-abc" not in resp.text
        assert resp.json()["configured"] is True
        assert credentials.get_secret(db_session, "openai", "api_key") == "sk-abc"

        # An empty value clears the key.
        client.put("/admin/integrations/openai", json={"values": {"api_key": ""}})
        assert credentials.get_secret(db_session, "openai", "api_key") is None

        assert client.put("/admin/integrations/openai",
                          json={"values": {"nope": "x"}}).status_code == 422
        assert client.put("/admin/integrations/unknown",
                          json={"values": {}}).status_code == 404

        resp = client.put("/admin/system-settings/consensus_enabled", json={"value": True})
        assert resp.status_code == 200
        assert client.put("/admin/system-settings/consensus_enabled",
                          json={"value": "on"}).status_code == 422
        listing = client.get("/admin/system-settings").json()
        assert next(s for s in listing if s["key"] == "consensus_enabled")["value"] is True


# --------------------------------------------------------------------------
# Booking hooks -> brief requested
# --------------------------------------------------------------------------


def _calendly_post(client, payload: dict):
    raw = json.dumps(payload)
    ts = str(int(time.time()))
    sig = hmac.new(settings.calendly_webhook_signing_key.encode(),
                   f"{ts}.{raw}".encode(), hashlib.sha256).hexdigest()
    return client.post("/webhooks/calendly", content=raw,
                       headers={"Calendly-Webhook-Signature": f"t={ts},v1={sig}",
                                "Content-Type": "application/json"})


def _calendly_payload(event, lead, tenant_id, *, start, uri="https://api.calendly.com/scheduled_events/EV1"):
    return {
        "event": event,
        "created_at": f"{event}-{uuid.uuid4()}",
        "payload": {
            "email": lead.email,
            "name": lead.full_name,
            "uri": f"{uri}/invitees/INV1",
            "tracking": {"utm_content": str(tenant_id)},
            "scheduled_event": {"uri": uri, "start_time": start.isoformat(),
                                "location": {"join_url": "https://zoom.us/j/1"}},
        },
    }


@pytest.fixture(autouse=True)
def _calendly_signing_key(monkeypatch):
    monkeypatch.setattr(settings, "calendly_webhook_signing_key", "fg7-signing-key")


class TestBookingHooks:
    def test_calendly_booking_requests_one_brief(self, client, db_session, test_user,
                                                 lead, queued_jobs):
        start = datetime.now(UTC) + timedelta(days=2)
        resp = _calendly_post(client, _calendly_payload("invitee.created", lead,
                                                        test_user.id, start=start))
        assert resp.json()["action"] == "booked"
        briefs = db_session.query(m.MeetingPrepBrief).all()
        assert len(briefs) == 1
        brief = briefs[0]
        assert brief.source == "calendly"
        assert brief.meeting_url == "https://zoom.us/j/1"
        assert abs((meeting_prep._aware(brief.meeting_start_at) - start).total_seconds()) < 1
        assert queued_jobs["prep"] == [str(brief.id)]

        # A second delivery about the same scheduled event (a retry with a new
        # delivery id) must not produce a second brief.
        _calendly_post(client, _calendly_payload("invitee.created", lead,
                                                 test_user.id, start=start))
        assert db_session.query(m.MeetingPrepBrief).count() == 1

    def test_calendly_cancel_stops_reminders(self, client, db_session, test_user, lead):
        start = datetime.now(UTC) + timedelta(days=2)
        _calendly_post(client, _calendly_payload("invitee.created", lead,
                                                 test_user.id, start=start))
        _calendly_post(client, _calendly_payload("invitee.canceled", lead,
                                                 test_user.id, start=start))
        brief = db_session.query(m.MeetingPrepBrief).one()
        assert brief.cancelled_at is not None

    def test_prep_disabled_by_admin_setting(self, client, db_session, test_user, lead):
        system_settings.set(db_session, "meeting_prep_enabled", False)
        _calendly_post(client, _calendly_payload("invitee.created", lead, test_user.id,
                                                 start=datetime.now(UTC) + timedelta(days=1)))
        assert db_session.query(m.MeetingPrepBrief).count() == 0

    def test_own_calendar_booking_requests_brief_and_cancel(self, db_session, test_user,
                                                            lead, queued_jobs):
        from app.services import calendar_service as cal
        from app.workers import calendar_tasks

        page = m.CalendarBookingPage(user_id=test_user.id, slug="intro",
                                     title="Intro call", duration_minutes=30)
        db_session.add(page)
        db_session.flush()
        start = datetime.now(UTC) + timedelta(days=1)
        booking = m.CalendarBooking(
            booking_page_id=page.id, invitee_name="Sara Khan",
            invitee_email=lead.email, start_at=start,
            end_at=start + timedelta(minutes=30), lead_id=lead.id,
            status=m.BookingStatus.CONFIRMED, slot_key=cal.slot_key_for(start),
            meeting_link="https://meet.google.com/abc",
        )
        db_session.add(booking)
        db_session.commit()

        assert calendar_tasks.on_booking_created_impl(db_session, booking.id) == "ok"
        brief = db_session.query(m.MeetingPrepBrief).one()
        assert brief.source == "leadpilot_calendar"
        assert brief.booking_id == booking.id
        assert brief.external_ref == str(booking.id)
        assert queued_jobs["prep"] == [str(brief.id)]

        calendar_tasks.on_booking_cancelled_impl(db_session, booking.id)
        db_session.refresh(brief)
        assert brief.cancelled_at is not None


# --------------------------------------------------------------------------
# Generation
# --------------------------------------------------------------------------


class TestGeneration:
    def test_ready_brief_with_verbatim_profile(self, db_session, test_user, lead,
                                               fake_claude, emitted):
        db_session.add(m.InboundReply(lead_id=lead.id, from_address=lead.email,
                                      body="Honestly we are drowning in inspections.",
                                      classification="interested"))
        db_session.commit()
        brief = _brief(db_session, lead, test_user,
                       start=datetime.now(UTC) + timedelta(days=1))

        assert meeting_prep.generate_brief(db_session, brief.id) == "ready"
        db_session.refresh(brief)
        assert brief.status is m.MeetingPrepStatus.READY
        # The profile is copied from the records.
        assert brief.profile_json["linkedin_url"] == "https://linkedin.com/in/sarakhan"
        assert brief.profile_json["company_size"] == 30
        assert brief.profile_json["funding"] == "Seed"
        md = brief.content_md
        for heading in ("Lead profile", "Company overview", "Recent LinkedIn activity",
                        "Why they booked", "Likely pain points", "Likely objections",
                        "Talking points", "Discovery questions",
                        "Competitive landscape", "Recommended next steps",
                        "Deal structure", "Your opening 60 seconds"):
            assert f"## {heading}" in md, heading
        assert "sara@acme.test" in md
        assert brief.opening_script.startswith("Thanks for booking")
        # The reply that converted them reached the prompt.
        assert "drowning in inspections" in fake_claude.meeting_prep_prompts[0]

        # One notification, deep-linked to the Meeting Prep tab.
        assert [c["event"] for c in emitted] == ["meeting_prep_ready"]
        assert emitted[0]["deep_link"] == f"/leads/detail?id={lead.id}&tab=prep"
        assert brief.notified_at is not None

        activity = db_session.query(m.CrmActivity).filter_by(
            kind=m.CrmActivityKind.MEETING_PREP_READY).one()
        assert activity.lead_id == lead.id

        # Regenerating does not notify a second time.
        meeting_prep.generate_brief(db_session, brief.id)
        assert len(emitted) == 1

    def test_model_failure_marks_failed_never_raises(self, db_session, test_user, lead,
                                                     fake_claude, emitted):
        fake_claude.meeting_prep_response = RuntimeError("API down")
        brief = _brief(db_session, lead, test_user)
        assert meeting_prep.generate_brief(db_session, brief.id) == "failed"
        db_session.refresh(brief)
        assert brief.status is m.MeetingPrepStatus.FAILED
        assert "API down" in brief.error
        assert emitted == []

    def test_empty_model_answer_is_a_failure(self, db_session, test_user, lead,
                                             fake_claude, emitted):
        fake_claude.meeting_prep_response = {"company_overview": 42}
        brief = _brief(db_session, lead, test_user)
        assert meeting_prep.generate_brief(db_session, brief.id) == "failed"

    def test_clean_sections_drops_malformed_items(self):
        sections = meeting_prep.clean_sections({
            "talking_points": ["ok", {"not": "a string"}, "", None],
            "likely_objections": ["too pricey", {"response": "no objection text"}],
            "discovery_questions": "single string",
        })
        assert sections["talking_points"] == ["ok"]
        assert sections["likely_objections"] == [{"objection": "too pricey", "response": ""}]
        assert sections["discovery_questions"] == ["single string"]
        assert sections["why_they_booked"] == ""


    def test_reschedule_rearms_reminders(self, db_session, test_user, lead):
        start = datetime.now(UTC) + timedelta(hours=10)
        brief = _brief(db_session, lead, test_user, start=start, ref="ev-1")
        brief.reminder_24h_sent_at = datetime.now(UTC)
        db_session.commit()
        meeting_prep.upsert_brief(db_session, lead, user_id=test_user.id,
                                  source="manual", external_ref="ev-1",
                                  meeting_start_at=start + timedelta(days=3))
        db_session.commit()
        db_session.refresh(brief)
        assert brief.reminder_24h_sent_at is None


# --------------------------------------------------------------------------
# Reminders
# --------------------------------------------------------------------------


class TestReminders:
    def test_24h_then_1h_each_exactly_once(self, db_session, test_user, lead, emitted):
        now = datetime.now(UTC)
        brief = _brief(db_session, lead, test_user, start=now + timedelta(hours=20))
        brief.opening_script = "Thanks for booking, Sara."
        db_session.commit()

        assert meeting_prep_tasks.send_meeting_reminders_impl(db_session, now=now) == {"24h": 1, "1h": 0}
        assert meeting_prep_tasks.send_meeting_reminders_impl(db_session, now=now) == {"24h": 0, "1h": 0}
        assert emitted[0]["event"] == "meeting_reminder_24h"
        assert "tomorrow" in emitted[0]["title"]

        later = now + timedelta(hours=19, minutes=30)
        assert meeting_prep_tasks.send_meeting_reminders_impl(db_session, now=later) == {"24h": 0, "1h": 1}
        assert meeting_prep_tasks.send_meeting_reminders_impl(db_session, now=later) == {"24h": 0, "1h": 0}
        one_hour = emitted[1]
        assert one_hour["event"] == "meeting_reminder_1h"
        # The 1h reminder carries the opening script as its body.
        assert one_hour["body"] == "Thanks for booking, Sara."
        assert one_hour["deep_link"].endswith("tab=prep")

    def test_short_notice_booking_gets_only_the_1h(self, db_session, test_user, lead, emitted):
        now = datetime.now(UTC)
        brief = _brief(db_session, lead, test_user, start=now + timedelta(minutes=40))
        meeting_prep_tasks.send_meeting_reminders_impl(db_session, now=now)
        assert [c["event"] for c in emitted] == ["meeting_reminder_1h"]
        db_session.refresh(brief)
        assert brief.reminder_24h_sent_at is not None
        # A later sweep sends nothing stale.
        meeting_prep_tasks.send_meeting_reminders_impl(db_session, now=now + timedelta(minutes=5))
        assert len(emitted) == 1

    def test_cancelled_started_and_far_meetings_are_silent(self, db_session, test_user,
                                                           lead, emitted):
        now = datetime.now(UTC)
        cancelled = _brief(db_session, lead, test_user, start=now + timedelta(hours=5))
        cancelled.cancelled_at = now
        _brief(db_session, lead, test_user, start=now - timedelta(minutes=5))
        _brief(db_session, lead, test_user, start=now + timedelta(days=3))
        db_session.commit()
        assert meeting_prep_tasks.send_meeting_reminders_impl(db_session, now=now) == {"24h": 0, "1h": 0}
        assert emitted == []

    def test_admin_can_turn_off_the_1h_reminder(self, db_session, test_user, lead, emitted):
        system_settings.set(db_session, "meeting_reminder_1h_enabled", False)
        now = datetime.now(UTC)
        _brief(db_session, lead, test_user, start=now + timedelta(minutes=30))
        meeting_prep_tasks.send_meeting_reminders_impl(db_session, now=now)
        assert emitted == []


# --------------------------------------------------------------------------
# Meeting prep API
# --------------------------------------------------------------------------


class TestPrepApi:
    def test_get_and_manual_request(self, client, db_session, lead, queued_jobs):
        assert client.get(f"/leads/{lead.id}/meeting-prep").json() == {"brief": None, "history": []}
        resp = client.post(f"/leads/{lead.id}/meeting-prep", json={})
        assert resp.status_code == 202, resp.text
        brief_id = resp.json()["id"]
        assert queued_jobs["prep"] == [brief_id]
        body = client.get(f"/leads/{lead.id}/meeting-prep").json()
        assert body["brief"]["id"] == brief_id
        assert len(body["history"]) == 1

        # Asking again reuses the same manual brief.
        client.post(f"/leads/{lead.id}/meeting-prep", json={})
        assert db_session.query(m.MeetingPrepBrief).count() == 1

        assert client.post(f"/meeting-prep/{brief_id}/regenerate").status_code == 202
        assert client.get(f"/meeting-prep/{brief_id}").json()["status"] == "pending"

    def test_regenerate_refuses_while_generating(self, client, db_session, test_user, lead):
        brief = _brief(db_session, lead, test_user)
        brief.status = m.MeetingPrepStatus.GENERATING
        db_session.commit()
        assert client.post(f"/meeting-prep/{brief.id}/regenerate").status_code == 409

    def test_other_tenant_gets_404(self, client, db_session, test_user, lead):
        brief = _brief(db_session, lead, test_user)
        other = m.User(email="other@tenant.test", email_verified=True)
        db_session.add(other)
        db_session.commit()
        headers = auth_headers(other)
        assert client.get(f"/leads/{lead.id}/meeting-prep", headers=headers).status_code == 404
        assert client.get(f"/meeting-prep/{brief.id}", headers=headers).status_code == 404
        assert client.post(f"/meeting-prep/{brief.id}/regenerate",
                           headers=headers).status_code == 404
        assert client.post(f"/leads/{lead.id}/meeting-outcome", headers=headers,
                           json={"outcome": "interested"}).status_code == 404


# --------------------------------------------------------------------------
# Meeting outcomes + Gmail draft
# --------------------------------------------------------------------------


class _Resp:
    def __init__(self, status: int, data: dict):
        self.status_code = status
        self._data = data

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeGmailHTTP:
    def __init__(self):
        self.calls: list[tuple] = []
        self.status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def request(self, method, path, json=None, headers=None):
        self.calls.append((method, path, json))
        if path.endswith("/send"):
            return _Resp(self.status, {"id": "sent-1", "threadId": "t1"})
        return _Resp(self.status, {"id": "draft-1", "message": {"id": "msg-1"}})


@pytest.fixture()
def fake_gmail(monkeypatch):
    from app.integrations import gmail

    http = FakeGmailHTTP()
    monkeypatch.setattr(gmail, "_gmail_http", lambda: http)
    monkeypatch.setattr(gmail, "get_valid_access_token", lambda *a, **k: "at")
    return http


class TestMeetingOutcome:
    def test_closed_won_end_to_end(self, client, db_session, lead, gmail_account,
                                   fake_gmail, fake_claude, queued_jobs,
                                   email_sequence):
        enrollment = m.SequenceEnrollment(sequence_id=email_sequence.id, lead_id=lead.id,
                                          status=m.EnrollmentStatus.PAUSED,
                                          stop_reason="meeting_pending")
        db_session.add(enrollment)
        db_session.commit()

        resp = client.post(f"/leads/{lead.id}/meeting-outcome", json={
            "outcome": "closed_won", "notes": "Signed. Kickoff Monday.",
            "deal_value": 4500.5, "currency": "usd",
        })
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["previous_status"] == "replied"
        assert body["new_status"] == "closed_won"
        assert body["draft_status"] == "draft_saved"
        assert body["followup_subject"] == "Great speaking today"
        assert body["gmail_draft_id"] == "draft-1"

        db_session.refresh(lead)
        assert lead.status is m.LeadStatus.CLOSED_WON
        deal = db_session.query(m.Deal).one()
        assert deal.value_cents == 450050
        assert deal.currency == "USD"
        assert deal.stage is m.DealStage.WON
        assert deal.strategy_id == lead.strategy_id
        assert db_session.query(m.Outcome).filter_by(event=m.OutcomeEvent.WON).count() == 1
        db_session.refresh(enrollment)
        assert enrollment.status is m.EnrollmentStatus.STOPPED
        # The notes are in the follow-up prompt and in the CRM.
        assert "Kickoff Monday" in fake_claude.followup_email_prompts[0]
        assert db_session.query(m.CrmNote).filter(
            m.CrmNote.body.contains("Kickoff Monday")).count() == 1
        assert any(e[1] == "deal_won" for e in queued_jobs["events"])

        method, path, payload = fake_gmail.calls[0]
        assert (method, path) == ("POST", "/users/me/drafts")
        assert "raw" in payload["message"]

        send = client.post(f"/meeting-outcomes/{body['id']}/send")
        assert send.status_code == 200, send.text
        assert send.json()["draft_status"] == "sent"
        assert client.post(f"/meeting-outcomes/{body['id']}/send").status_code == 409


    def test_interested_sets_next_action_no_deal(self, client, db_session, lead,
                                                 fake_claude):
        resp = client.post(f"/leads/{lead.id}/meeting-outcome",
                           json={"outcome": "interested"})
        assert resp.status_code == 201
        # No Gmail connected: the outcome stands, the draft text is kept.
        assert resp.json()["draft_status"] == "not_connected"
        assert resp.json()["followup_body"]
        db_session.refresh(lead)
        assert lead.status is m.LeadStatus.OPPORTUNITY
        assert db_session.query(m.Deal).count() == 0
        meta = db_session.query(m.CrmLeadMeta).filter_by(lead_id=lead.id).one()
        assert meta.next_action_at is not None

    def test_generation_failure_keeps_the_outcome(self, client, db_session, lead,
                                                  fake_claude):
        fake_claude.followup_email_response = RuntimeError("model down")
        resp = client.post(f"/leads/{lead.id}/meeting-outcome",
                           json={"outcome": "not_a_fit"})
        assert resp.status_code == 201
        assert resp.json()["draft_status"] == "generation_failed"
        db_session.refresh(lead)
        assert lead.status is m.LeadStatus.DISQUALIFIED

    def test_suppressed_contact_gets_no_draft_and_no_send(self, client, db_session, lead,
                                                          gmail_account, fake_gmail,
                                                          fake_claude):
        db_session.add(m.SuppressionEntry(email=lead.email, reason="unsubscribed_test"))
        db_session.commit()
        resp = client.post(f"/leads/{lead.id}/meeting-outcome",
                           json={"outcome": "closed_lost"})
        assert resp.json()["draft_status"] == "suppressed"
        assert fake_gmail.calls == []
        assert client.post(f"/meeting-outcomes/{resp.json()['id']}/send").status_code == 409

    def test_missing_compose_scope_is_reauth(self, client, db_session, lead,
                                             gmail_account, fake_gmail, fake_claude):
        fake_gmail.status = 403
        resp = client.post(f"/leads/{lead.id}/meeting-outcome",
                           json={"outcome": "needs_follow_up"})
        assert resp.json()["draft_status"] == "reauth_required"

    def test_edit_draft_updates_gmail(self, client, db_session, lead, gmail_account,
                                      fake_gmail, fake_claude):
        outcome_id = client.post(f"/leads/{lead.id}/meeting-outcome",
                                 json={"outcome": "interested"}).json()["id"]
        resp = client.put(f"/meeting-outcomes/{outcome_id}/draft",
                          json={"subject": "Edited", "body": "New body"})
        assert resp.status_code == 200
        assert resp.json()["followup_subject"] == "Edited"
        assert fake_gmail.calls[-1][0:2] == ("PUT", "/users/me/drafts/draft-1")

    def test_skip_followup_generation(self, client, lead, fake_claude):
        resp = client.post(f"/leads/{lead.id}/meeting-outcome",
                           json={"outcome": "interested", "generate_followup": False})
        assert resp.json()["draft_status"] == "pending"
        assert fake_claude.followup_email_prompts == []

    def test_validation(self, client, lead):
        assert client.post(f"/leads/{lead.id}/meeting-outcome",
                           json={"outcome": "maybe"}).status_code == 422
        assert client.post(f"/leads/{lead.id}/meeting-outcome",
                           json={"outcome": "closed_won", "currency": "US1"}).status_code == 422


# --------------------------------------------------------------------------
# Deals API
# --------------------------------------------------------------------------


class TestDeals:
    def test_crud(self, client, lead):
        resp = client.post("/deals", json={"value": 1200, "lead_id": str(lead.id)})
        assert resp.status_code == 201, resp.text
        deal = resp.json()
        assert deal["value"] == 1200.0
        assert deal["value_cents"] == 120000
        assert deal["strategy_id"] == str(lead.strategy_id)
        assert deal["name"] == "Acme Fire"
        assert deal["close_date"] is not None  # won deals get a close date

        assert client.get("/deals").json()["total"] == 1
        patched = client.patch(f"/deals/{deal['id']}", json={"value": 99.99, "stage": "lost"})
        assert patched.json()["value_cents"] == 9999
        assert patched.json()["stage"] == "lost"
        assert client.delete(f"/deals/{deal['id']}").status_code == 204
        assert client.get(f"/deals/{deal['id']}").status_code == 404

    def test_mismatched_strategy_is_rejected(self, client, db_session, lead,
                                             product_with_strategy):
        _, other_strategy = product_with_strategy(m.FlowType.WITH_CLIENTS)
        resp = client.post("/deals", json={"value": 1, "lead_id": str(lead.id),
                                           "strategy_id": str(other_strategy.id)})
        assert resp.status_code == 422

    def test_other_tenant_404(self, client, db_session, lead):
        deal_id = client.post("/deals", json={"value": 5}).json()["id"]
        other = m.User(email="other2@tenant.test", email_verified=True)
        db_session.add(other)
        db_session.commit()
        headers = auth_headers(other)
        assert client.get(f"/deals/{deal_id}", headers=headers).status_code == 404
        assert client.post("/deals", headers=headers,
                           json={"value": 5, "lead_id": str(lead.id)}).status_code == 404
