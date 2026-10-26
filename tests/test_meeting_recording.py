"""Feature 3 — Recall.ai transcripts feeding the AI meeting summary.

What must hold:
  * the webhook is CLOSED without a secret, and only a correctly signed,
    in-tolerance delivery (Svix scheme) is accepted;
  * a redelivered webhook is processed once;
  * a delivery is matched to a meeting ONLY by the bot id we stored -- an
    unknown bot is quarantined, and a payload whose metadata names a
    different meeting is refused rather than guessed at;
  * one bot per meeting, even on a double click;
  * the summary never waits for the transcript: /end still queues it at once,
    from the notes; the transcript, when it arrives (even late), upgrades it,
    and a wait past the deadline is marked timed_out.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.db import models as m
from app.integrations import recall
from app.services import calendar_service as cal
from app.services import credentials, meeting_recording

UTC = timezone.utc
KEY = b"k" * 32
SECRET = "whsec_" + base64.b64encode(KEY).decode()


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture()
def lead(db_session, verified_strategy):
    row = m.Lead(strategy_id=verified_strategy.id, source="manual", full_name="Sara Khan",
                 company="Acme Fire", email="sara@acme.test", status=m.LeadStatus.REPLIED)
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture()
def meeting(client, db_session, test_user, lead):
    page = m.CalendarBookingPage(user_id=test_user.id, slug="rec-intro",
                                 title="Intro call", duration_minutes=30)
    db_session.add(page)
    db_session.flush()
    start = datetime.now(UTC) + timedelta(hours=1)
    booking = m.CalendarBooking(
        booking_page_id=page.id, invitee_name="Sara Khan", invitee_email="sara@acme.test",
        start_at=start, end_at=start + timedelta(minutes=30),
        status=m.BookingStatus.CONFIRMED, lead_id=lead.id, slot_key=cal.slot_key_for(start))
    db_session.add(booking)
    db_session.commit()
    resp = client.post("/meetings", json={"booking_id": str(booking.id), "platform": "custom",
                                          "meeting_url": "https://meet.example.test/abc"})
    assert resp.status_code == 201, resp.text
    return db_session.get(m.Meeting, uuid.UUID(resp.json()["id"]))


@pytest.fixture(autouse=True)
def queued(monkeypatch):
    """No broker in the root suite: record what would have been queued."""
    from app.workers import calendar_tasks

    jobs = {"summary": [], "transcript": []}
    monkeypatch.setattr(calendar_tasks.generate_meeting_summary, "delay",
                        lambda mid: jobs["summary"].append(mid), raising=False)
    monkeypatch.setattr(calendar_tasks.fetch_meeting_transcript, "delay",
                        lambda mid, tid=None: jobs["transcript"].append((mid, tid)),
                        raising=False)
    return jobs


class FakeRecall:
    def __init__(self, text="Sara: we are stuck on onboarding\nRehan: since when?",
                 fail=False):
        self.text, self.fail = text, fail
        self.bots: list[dict] = []
        self.fetches: list[tuple] = []

    def create_bot(self, meeting_url, *, join_at, metadata, bot_name="Vectorae Notetaker"):
        if self.fail:
            from app.integrations.plumbing import ExternalAPIError

            raise ExternalAPIError("recall", "/api/v1/bot/", "HTTP 400: nope", status=400)
        self.bots.append({"url": meeting_url, "metadata": metadata})
        return {"id": f"bot-{len(self.bots)}"}

    def fetch_transcript_text(self, bot_id, transcript_id=None):
        self.fetches.append((bot_id, transcript_id))
        return self.text


def _signed(body: dict, *, secret_key: bytes = KEY, msg_id: str = "msg_1",
            ts: int | None = None, prefix: str = "webhook") -> tuple[bytes, dict]:
    raw = json.dumps(body).encode()
    ts = int(time.time()) if ts is None else ts
    sig = base64.b64encode(hmac.new(secret_key, f"{msg_id}.{ts}.".encode() + raw,
                                    hashlib.sha256).digest()).decode()
    return raw, {f"{prefix}-id": msg_id, f"{prefix}-timestamp": str(ts),
                 f"{prefix}-signature": f"v1,{sig}", "Content-Type": "application/json"}


def _event(bot_id: str, event="transcript.done", meeting_id: str | None = None,
           transcript_id="tr_1") -> dict:
    bot: dict = {"id": bot_id, "metadata": {}}
    if meeting_id is not None:
        bot["metadata"] = {"meeting_id": meeting_id}
    return {"event": event, "data": {"bot": bot, "transcript": {"id": transcript_id},
                                     "data": {"code": "done"}}}


def _with_bot(db, meeting, bot_id="bot-1"):
    meeting.recording_bot_id = bot_id
    meeting.transcript_status = meeting_recording.PENDING
    db.commit()
    return meeting


# --------------------------------------------------------------------------
# Signature verification and formatting
# --------------------------------------------------------------------------


class TestVerify:
    def test_a_correct_signature_is_accepted(self):
        raw, headers = _signed({"event": "x"})
        assert recall.verify_webhook(raw, headers, SECRET)

    def test_legacy_svix_headers_are_accepted(self):
        raw, headers = _signed({"event": "x"}, prefix="svix")
        assert recall.verify_webhook(raw, headers, SECRET)

    def test_one_matching_signature_among_several_is_enough(self):
        raw, headers = _signed({"event": "x"})
        headers["webhook-signature"] = f"v1,bm9wZQ== {headers['webhook-signature']}"
        assert recall.verify_webhook(raw, headers, SECRET)

    def test_a_tampered_body_is_rejected(self):
        raw, headers = _signed({"event": "transcript.done"})
        assert not recall.verify_webhook(raw.replace(b"done", b"fail"), headers, SECRET)

    def test_the_wrong_secret_is_rejected(self):
        raw, headers = _signed({"event": "x"}, secret_key=b"z" * 32)
        assert not recall.verify_webhook(raw, headers, SECRET)

    def test_a_replayed_old_delivery_is_rejected(self):
        raw, headers = _signed({"event": "x"}, ts=int(time.time()) - 3600)
        assert not recall.verify_webhook(raw, headers, SECRET)

    def test_missing_headers_or_secret_are_rejected(self):
        raw, headers = _signed({"event": "x"})
        assert not recall.verify_webhook(raw, {}, SECRET)
        assert not recall.verify_webhook(raw, headers, None)

    def test_a_malformed_region_is_refused_before_any_call(self):
        with pytest.raises(recall.RecallNotConfigured):
            recall.RecallAdapter(api_key="k", region="evil.example.com/x")


def test_transcript_turns_are_merged_per_speaker():
    segments = [
        {"participant": {"name": "Sara"}, "words": [{"text": "we are"}, {"text": "stuck"}]},
        {"participant": {"name": "Sara"}, "words": [{"text": "on onboarding"}]},
        {"participant": {"name": None}, "words": [{"text": "since when?"}]},
        {"participant": {"name": "Rehan"}, "words": []},
    ]
    assert recall.format_transcript(segments) == (
        "Sara: we are stuck on onboarding\nUnknown speaker: since when?")
    assert recall.format_transcript("not a list") == ""


# --------------------------------------------------------------------------
# Starting a bot
# --------------------------------------------------------------------------


class TestStartBot:
    def test_it_starts_one_bot_and_tags_it_with_the_meeting(self, db_session, meeting):
        fake = FakeRecall()
        meeting_recording.start_bot(db_session, meeting, client=fake)
        meeting_recording.start_bot(db_session, meeting, client=fake)   # double click
        assert len(fake.bots) == 1
        assert fake.bots[0]["metadata"] == {"meeting_id": str(meeting.id)}
        assert meeting.recording_bot_id == "bot-1"
        assert meeting.transcript_status == meeting_recording.PENDING

    def test_a_provider_failure_releases_the_claim(self, db_session, meeting):
        from app.integrations.plumbing import ExternalAPIError

        with pytest.raises(ExternalAPIError):
            meeting_recording.start_bot(db_session, meeting, client=FakeRecall(fail=True))
        db_session.refresh(meeting)
        assert meeting.transcript_status is None and meeting.recording_bot_id is None
        meeting_recording.start_bot(db_session, meeting, client=FakeRecall())
        assert meeting.recording_bot_id == "bot-1"

    def test_the_api_is_503_until_an_admin_configures_recall(self, client, meeting):
        resp = client.post(f"/meetings/{meeting.id}/recording-bot")
        assert resp.status_code == 503
        assert "Admin > Integrations" in resp.json()["detail"]

    def test_the_api_starts_a_bot(self, client, meeting, monkeypatch):
        monkeypatch.setattr(recall, "client_for", lambda db: FakeRecall())
        resp = client.post(f"/meetings/{meeting.id}/recording-bot")
        assert resp.status_code == 200, resp.text
        assert resp.json()["recording"] is True
        assert resp.json()["transcript_status"] == "pending"

    def test_a_finished_meeting_cannot_be_recorded(self, client, db_session, meeting,
                                                   monkeypatch):
        monkeypatch.setattr(recall, "client_for", lambda db: FakeRecall())
        meeting.status = m.MeetingStatus.COMPLETED
        db_session.commit()
        assert client.post(f"/meetings/{meeting.id}/recording-bot").status_code == 409

    def test_another_users_meeting_is_404(self, client, db_session, meeting):
        from .conftest import auth_headers

        other = m.User(email="someone@else.test", email_verified=True)
        db_session.add(other)
        db_session.commit()
        resp = client.post(f"/meetings/{meeting.id}/recording-bot", headers=auth_headers(other))
        assert resp.status_code == 404


# --------------------------------------------------------------------------
# Webhook
# --------------------------------------------------------------------------


class TestWebhook:
    @pytest.fixture()
    def secret(self, db_session):
        credentials.set_system_secret(db_session, "recall", "webhook_secret", SECRET)
        db_session.commit()

    def test_closed_without_a_secret(self, client):
        raw, headers = _signed(_event("bot-1"))
        assert client.post("/webhooks/recall", content=raw, headers=headers).status_code == 503

    def test_a_bad_signature_is_401(self, client, secret):
        raw, headers = _signed(_event("bot-1"), secret_key=b"z" * 32)
        assert client.post("/webhooks/recall", content=raw, headers=headers).status_code == 401

    def test_transcript_done_queues_the_fetch_once(self, client, db_session, meeting, secret,
                                                   queued):
        _with_bot(db_session, meeting)
        raw, headers = _signed(_event("bot-1", meeting_id=str(meeting.id)))
        first = client.post("/webhooks/recall", content=raw, headers=headers)
        again = client.post("/webhooks/recall", content=raw, headers=headers)
        assert first.json()["result"] == "queued"
        assert again.json() == {"ok": True, "duplicate": True}
        assert queued["transcript"] == [(str(meeting.id), "tr_1")]

    def test_an_unknown_bot_is_quarantined(self, client, secret, queued):
        raw, headers = _signed(_event("bot-nobody"))
        assert client.post("/webhooks/recall", content=raw,
                           headers=headers).json()["result"] == "unknown_bot"
        assert queued["transcript"] == []

    def test_metadata_naming_another_meeting_is_refused(self, client, db_session, meeting,
                                                       secret, queued):
        _with_bot(db_session, meeting)
        raw, headers = _signed(_event("bot-1", meeting_id=str(uuid.uuid4())))
        assert client.post("/webhooks/recall", content=raw,
                           headers=headers).json()["result"] == "metadata_mismatch"
        assert queued["transcript"] == []

    def test_transcript_failed_is_recorded(self, client, db_session, meeting, secret):
        _with_bot(db_session, meeting)
        raw, headers = _signed(_event("bot-1", event="transcript.failed"), msg_id="msg_f")
        assert client.post("/webhooks/recall", content=raw,
                           headers=headers).json()["result"] == "failed"
        db_session.refresh(meeting)
        assert meeting.transcript_status == "failed"

    def test_other_events_are_acknowledged_and_ignored(self, client, secret):
        raw, headers = _signed({"event": "bot.joining_call", "data": {}}, msg_id="msg_o")
        assert client.post("/webhooks/recall", content=raw,
                           headers=headers).json()["result"] == "ignored"


# --------------------------------------------------------------------------
# The transcript and the summary
# --------------------------------------------------------------------------


class TestTranscriptAndSummary:
    def test_a_transcript_for_a_finished_meeting_upgrades_the_summary(self, db_session,
                                                                      meeting):
        _with_bot(db_session, meeting)
        meeting.transcript = "chunk text that the provider transcript supersedes"
        meeting.status = m.MeetingStatus.COMPLETED
        db_session.commit()
        calls: list = []

        result = meeting_recording.fetch_transcript(
            db_session, meeting.id, "tr_1", client=FakeRecall(),
            summarise=lambda db, mid: calls.append(mid) or "ok")
        assert result == "received_summarised:ok"
        assert calls == [meeting.id]
        db_session.refresh(meeting)
        assert meeting.transcript.startswith("Sara: we are stuck")
        assert meeting.transcript_status == "received"

    def test_a_transcript_before_end_waits_for_end_to_summarise(self, db_session, meeting):
        _with_bot(db_session, meeting)
        calls: list = []
        assert meeting_recording.fetch_transcript(
            db_session, meeting.id, None, client=FakeRecall(),
            summarise=lambda db, mid: calls.append(mid)) == "received"
        assert calls == []

    def test_a_repeat_fetch_does_nothing(self, db_session, meeting):
        _with_bot(db_session, meeting)
        fake = FakeRecall()
        meeting_recording.fetch_transcript(db_session, meeting.id, client=fake,
                                           summarise=lambda db, mid: "ok")
        assert meeting_recording.fetch_transcript(db_session, meeting.id, client=fake) == \
            "already_received"
        assert len(fake.fetches) == 1

    def test_an_empty_transcript_is_a_failure_not_a_blank_summary(self, db_session, meeting):
        _with_bot(db_session, meeting)
        assert meeting_recording.fetch_transcript(
            db_session, meeting.id, client=FakeRecall(text="  ")) == "empty"
        db_session.refresh(meeting)
        assert meeting.transcript_status == "failed"

    def test_ending_a_recorded_meeting_does_not_wait_for_the_transcript(
            self, client, db_session, meeting, queued):
        _with_bot(db_session, meeting)
        before = datetime.now(UTC)
        resp = client.post(f"/meetings/{meeting.id}/end")
        assert resp.status_code == 200, resp.text
        # The summary is queued NOW, from the notes...
        assert queued["summary"] == [str(meeting.id)]
        # ...and the transcript wait starts, bounded by the admin setting.
        db_session.refresh(meeting)
        deadline = meeting.transcript_deadline_at
        deadline = deadline if deadline.tzinfo else deadline.replace(tzinfo=UTC)
        assert before + timedelta(minutes=59) < deadline < before + timedelta(minutes=61)
        assert resp.json()["transcript_status"] == "pending"

    def test_ending_an_unrecorded_meeting_starts_no_wait(self, client, db_session, meeting):
        client.post(f"/meetings/{meeting.id}/end")
        db_session.refresh(meeting)
        assert meeting.transcript_deadline_at is None and meeting.transcript_status is None

    def test_the_summary_says_what_it_was_built_from(self, db_session, meeting, fake_claude):
        from app.workers.calendar_tasks import generate_summary_impl

        meeting.raw_notes = "They are stuck on onboarding. Send pricing Friday."
        db_session.commit()
        assert generate_summary_impl(db_session, meeting.id) == "ok"
        db_session.refresh(meeting)
        assert meeting.summary_source == "notes"

        meeting.transcript = "Sara: we are stuck on onboarding"
        db_session.commit()
        assert generate_summary_impl(db_session, meeting.id) == "ok"
        db_session.refresh(meeting)
        assert meeting.summary_source == "transcript"

    def test_an_expired_wait_is_marked_and_a_late_transcript_still_lands(self, db_session,
                                                                         meeting):
        _with_bot(db_session, meeting)
        now = datetime.now(UTC)
        meeting.transcript_deadline_at = now - timedelta(minutes=1)
        meeting.status = m.MeetingStatus.COMPLETED
        db_session.commit()
        assert meeting_recording.expire_waits(db_session, now) == 1
        db_session.refresh(meeting)
        assert meeting.transcript_status == "timed_out"

        assert meeting_recording.fetch_transcript(
            db_session, meeting.id, client=FakeRecall(),
            summarise=lambda db, mid: "ok").startswith("received_summarised")

    def test_a_wait_inside_its_deadline_is_left_alone(self, db_session, meeting):
        _with_bot(db_session, meeting)
        meeting.transcript_deadline_at = datetime.now(UTC) + timedelta(minutes=30)
        db_session.commit()
        assert meeting_recording.expire_waits(db_session) == 0


def test_the_expiry_sweep_is_scheduled_on_the_default_queue():
    from app.workers.celery_app import celery_app

    entry = celery_app.conf.beat_schedule["expire-transcript-waits"]
    assert entry["task"] == "app.workers.calendar_tasks.expire_transcript_waits"
    assert celery_app.amqp.router.route({}, entry["task"])["queue"].name == "default"
