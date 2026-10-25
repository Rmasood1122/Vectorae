"""Feature Group 6 — AI phone calls.

What must hold:
  * nothing dials unless the admin enabled calling, the number is dialable,
    the lead has recorded consent (while consent is required), the contact
    is not suppressed, a provider is configured and the daily limit allows;
  * every call discloses it is an AI, even if the model forgot;
  * the script and voicemail exist before the provider is asked to dial,
    a failed dial marks the call failed and gives the slot back;
  * webhooks are authenticated (fail closed); a voicemail settles without a
    model call, a conversation is analysed off the request path, and an
    interested / not-interested call stops the sequence like a reply.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from app.db import models as m
from app.integrations.phone_channel import PhoneChannel
from app.integrations.voice_providers import VoiceProviderError
from app.services import credentials, phone_calls, system_settings
from app.workers import outreach_tasks

from .conftest import NOW, auth_headers, entitle_owner

UTC = timezone.utc


class FakeVoice:
    def __init__(self, provider="vapi", fail=None, can_call=True, voice_id="v1"):
        self.provider, self.fail, self.can_call, self.voice_id = provider, fail, can_call, voice_id
        self.calls = []

    def create_call(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise self.fail
        return {"id": f"{self.provider}-call-{len(self.calls)}", "status": "queued"}

    def tts(self, text):
        return b"ID3fake-mp3"


@pytest.fixture()
def voice(monkeypatch, tmp_path):
    from app.config import settings

    monkeypatch.setattr(settings, "media_dir", str(tmp_path))
    vapi, eleven = FakeVoice("vapi"), FakeVoice("elevenlabs", can_call=False)
    monkeypatch.setattr("app.integrations.voice_providers.get_clients",
                        lambda db, user_id=None: (vapi, eleven))
    return {"vapi": vapi, "eleven": eleven}


@pytest.fixture()
def calling_on(db_session):
    system_settings.set(db_session, "phone_calling_enabled", True)


@pytest.fixture()
def phone_lead(db_session, verified_strategy):
    lead = m.Lead(strategy_id=verified_strategy.id, source="apollo", external_id="PH1",
                  full_name="Sara Khan", company="Acme Fire", phone="+1 (555) 010-2000",
                  status=m.LeadStatus.VERIFIED, phone_consent_at=datetime.now(UTC),
                  phone_consent_source="web form")
    db_session.add(lead)
    db_session.commit()
    return lead


@pytest.fixture()
def phone_sequence(db_session, verified_strategy):
    # Scale is the cheapest plan with the phone channel; the send path re-checks
    # the plan (app/services/plan_holds.py). This file is about calls, not plans.
    entitle_owner(db_session, verified_strategy, m.PlanTier.SCALE)
    seq = m.Sequence(strategy_id=verified_strategy.id, channel=m.ChannelType.PHONE,
                     name="Calls", status=m.SequenceStatus.ACTIVE)
    db_session.add(seq)
    db_session.flush()
    db_session.add(m.SequenceStep(sequence_id=seq.id, step_no=1, template="intro call",
                                  delay_days=0))
    db_session.add(m.SequenceStep(sequence_id=seq.id, step_no=2, template="second call",
                                  delay_days=2))
    db_session.commit()
    return seq


def _message(db_session, seq, lead):
    from app.services import sequence_engine as engine

    enrollment = m.SequenceEnrollment(sequence_id=seq.id, lead_id=lead.id)
    db_session.add(enrollment)
    db_session.flush()
    return engine._schedule_step_message(db_session, seq, enrollment, lead, seq.steps[0],
                                         base_time=NOW)


def _send(db_session, msg, channel):
    return outreach_tasks.send_message_impl(db_session, msg.id, channel=channel, now=NOW)


def test_e164():
    assert phone_calls.e164("+1 (555) 010-2000") == "+15550102000"
    assert phone_calls.e164("12") is None and phone_calls.e164(None) is None


class TestGates:
    def test_disabled_by_default(self, db_session, phone_sequence, phone_lead, voice, fake_claude):
        msg = _message(db_session, phone_sequence, phone_lead)
        assert _send(db_session, msg, PhoneChannel(db_session, **voice)) == "failed_calling_disabled"
        assert voice["vapi"].calls == []

    def test_no_consent_skips_and_the_sequence_continues(self, db_session, phone_sequence,
                                                         phone_lead, voice, calling_on,
                                                         fake_claude):
        phone_lead.phone_consent_at = None
        db_session.commit()
        msg = _message(db_session, phone_sequence, phone_lead)
        assert _send(db_session, msg, PhoneChannel(db_session, **voice)) == "skipped_no_call_consent"
        assert voice["vapi"].calls == []
        assert db_session.query(m.Message).filter_by(lead_id=phone_lead.id, step_no=2).count() == 1

    def test_admin_can_waive_consent(self, db_session, phone_sequence, phone_lead, voice,
                                     calling_on, fake_claude):
        system_settings.set(db_session, "phone_require_consent", False)
        phone_lead.phone_consent_at = None
        db_session.commit()
        assert _send(db_session, _message(db_session, phone_sequence, phone_lead),
                     PhoneChannel(db_session, **voice)) == "sent"

    def test_bad_number_not_configured_suppressed_and_cap(self, db_session, phone_sequence,
                                                          verified_strategy, phone_lead,
                                                          voice, calling_on, fake_claude,
                                                          monkeypatch):
        bad = m.Lead(strategy_id=verified_strategy.id, source="x", external_id="B",
                     phone="12", status=m.LeadStatus.VERIFIED, phone_consent_at=NOW)
        db_session.add(bad)
        db_session.commit()
        assert _send(db_session, _message(db_session, phone_sequence, bad),
                     PhoneChannel(db_session, **voice)) == "failed_bad_number"

        db_session.add(m.SuppressionEntry(phone="+1 (555) 010-2000", reason="dnc"))
        db_session.commit()
        assert _send(db_session, _message(db_session, phone_sequence, phone_lead),
                     PhoneChannel(db_session, **voice)) == "cancelled_suppressed"

    def test_cap_defers(self, db_session, phone_sequence, phone_lead, voice, calling_on,
                        fake_claude):
        system_settings.set(db_session, "phone_daily_call_limit", 0)
        msg = _message(db_session, phone_sequence, phone_lead)
        assert _send(db_session, msg, PhoneChannel(db_session, **voice)) == "deferred_cap"

    def test_no_provider(self, db_session, phone_sequence, phone_lead, calling_on,
                         fake_claude, monkeypatch):
        monkeypatch.setattr("app.integrations.voice_providers.get_clients",
                            lambda db, user_id=None: (None, None))
        assert _send(db_session, _message(db_session, phone_sequence, phone_lead),
                     None) == "failed_calling_not_configured"


class TestPlacingACall:
    def test_call_is_prepared_then_placed(self, db_session, phone_sequence, phone_lead, voice,
                                          calling_on, fake_claude, tmp_path):
        msg = _message(db_session, phone_sequence, phone_lead)
        assert _send(db_session, msg, PhoneChannel(db_session, **voice)) == "sent"
        call = db_session.query(m.Call).one()
        assert call.provider_call_id == "vapi-call-1" and call.status == "ringing"
        assert call.to_number == "+15550102000"
        assert "AI assistant" in call.script_json["first_message"]
        # Voice twin guard 1 (contract change): the voicemail now always OPENS
        # with the AI disclosure, then the model's personalised words.
        assert call.voicemail_text.startswith("This is an AI assistant calling for ")
        assert "Hi Sara" in call.voicemail_text
        assert call.script_json["first_message"].startswith("Hi Sara, this is an AI assistant")
        assert "This call is recorded." in call.script_json["first_message"]
        # Voicemail rendered to audio and hosted.
        assert call.voicemail_audio_url.endswith(".mp3")
        assert list((tmp_path / "voicemails").iterdir())
        placed = voice["vapi"].calls[0]
        assert placed["to_number"] == "+15550102000"
        assert placed["voicemail_message"] == call.voicemail_text
        assert placed["server_url"].endswith("/webhooks/vapi")
        assert "book a short meeting" in placed["system_prompt"]
        db_session.refresh(msg)
        assert msg.status is m.MessageStatus.SENT and msg.channel is m.ChannelType.PHONE

    def test_disclosure_is_forced_when_the_model_omits_it(self, db_session, verified_strategy,
                                                          phone_lead, fake_claude):
        fake_claude.call_script_response = {"first_message": "Hey Sara, quick question for you.",
                                            "voicemail": "Call me back."}
        script = phone_calls.write_script(db_session, verified_strategy, phone_lead, "brief")
        assert script["first_message"].startswith("Hi, this is an AI assistant calling on behalf of")

    def test_failed_dial_marks_call_and_releases_slot(self, db_session, phone_sequence,
                                                      phone_lead, voice, calling_on, fake_claude,
                                                      test_user):
        voice["vapi"].fail = VoiceProviderError("invalid number", status=400)
        msg = _message(db_session, phone_sequence, phone_lead)
        assert _send(db_session, msg, PhoneChannel(db_session, **voice)) == "failed_permanent"
        call = db_session.query(m.Call).one()
        assert call.status == "failed" and call.outcome is m.CallOutcome.FAILED
        assert phone_calls.reserve_call_slot(db_session, test_user.id)   # slot came back

    def test_channel_falls_back_to_elevenlabs(self, db_session, phone_lead):
        from app.integrations.outreach_base import OutboundMessage

        vapi = FakeVoice("vapi", fail=VoiceProviderError("down", status=503))
        eleven = FakeVoice("elevenlabs")
        result = PhoneChannel(db_session, vapi=vapi, eleven=eleven).send(OutboundMessage(
            message_id="x", lead_id=str(phone_lead.id), to_address="+15550102000", body="hi",
            metadata={"first_message": "hi"}))
        assert result.ok and result.raw["provider"] == "elevenlabs"


# --------------------------------------------------------------------------
# Webhooks + analysis
# --------------------------------------------------------------------------


@pytest.fixture()
def placed_call(db_session, phone_lead, phone_sequence, test_user):
    enrollment = m.SequenceEnrollment(sequence_id=phone_sequence.id, lead_id=phone_lead.id)
    db_session.add(enrollment)
    call = m.Call(lead_id=phone_lead.id, strategy_id=phone_lead.strategy_id, user_id=test_user.id,
                  provider="vapi", provider_call_id="vc-1", to_number="+15550102000",
                  status="ringing", voicemail_text="Hi Sara, AI assistant here.")
    db_session.add(call)
    db_session.commit()
    return call


def _vapi(client, payload, secret="vsec"):
    return client.post("/webhooks/vapi", content=json.dumps(payload),
                       headers={"x-vapi-secret": secret, "Content-Type": "application/json"})


class TestWebhooks:
    def test_fail_closed_and_bad_secret(self, client, db_session, placed_call):
        assert _vapi(client, {"message": {"type": "status-update"}}).status_code == 401
        credentials.set_system_secret(db_session, "vapi", "webhook_secret", "vsec")
        assert _vapi(client, {"message": {}}, secret="nope").status_code == 401

    def test_voicemail_settles_without_analysis(self, client, db_session, placed_call,
                                                queued_jobs):
        credentials.set_system_secret(db_session, "vapi", "webhook_secret", "vsec")
        resp = _vapi(client, {"message": {"type": "end-of-call-report", "call": {"id": "vc-1"},
                                          "endedReason": "voicemail", "durationSeconds": 31,
                                          "recordingUrl": "https://rec/1"}})
        assert resp.json()["outcome"] == "voicemail_dropped"
        assert queued_jobs["calls"] == []
        db_session.refresh(placed_call)
        assert placed_call.recording_url == "https://rec/1"
        assert db_session.get(m.Lead, placed_call.lead_id).last_call_outcome == "voicemail_dropped"
        # Retried delivery is a no-op.
        again = _vapi(client, {"message": {"type": "end-of-call-report", "call": {"id": "vc-1"},
                                           "endedReason": "voicemail"}})
        assert again.json().get("duplicate") is True

    def test_conversation_is_queued_for_analysis(self, client, db_session, placed_call,
                                                 queued_jobs):
        credentials.set_system_secret(db_session, "vapi", "webhook_secret", "vsec")
        _vapi(client, {"message": {"type": "status-update", "call": {"id": "vc-1"},
                                   "status": "in-progress"}})
        db_session.refresh(placed_call)
        assert placed_call.status == "in_progress"
        resp = _vapi(client, {"message": {"type": "end-of-call-report", "call": {"id": "vc-1"},
                                          "endedReason": "customer-ended-call",
                                          "transcript": "AI: hi\nUser: sure, Thursday works",
                                          "startedAt": "2026-08-11T10:00:00Z",
                                          "endedAt": "2026-08-11T10:03:00Z"}})
        assert resp.json()["analysis_queued"] is True
        assert queued_jobs["calls"] == [str(placed_call.id)]
        db_session.refresh(placed_call)
        assert placed_call.duration_seconds == 180

    def test_elevenlabs_signature(self, client, db_session, phone_lead, test_user, queued_jobs):
        credentials.set_system_secret(db_session, "elevenlabs", "webhook_secret", "esec")
        db_session.add(m.Call(lead_id=phone_lead.id, user_id=test_user.id, provider="elevenlabs",
                              provider_call_id="conv-1", to_number="+1555"))
        db_session.commit()
        body = json.dumps({"type": "post_call_transcription", "data": {
            "conversation_id": "conv-1", "transcript": [{"role": "user", "message": "ok"}],
            "metadata": {"call_duration_secs": 42}}})
        ts = str(int(time.time()))
        sig = hmac.new(b"esec", f"{ts}.{body}".encode(), hashlib.sha256).hexdigest()
        ok = client.post("/webhooks/elevenlabs", content=body,
                         headers={"ElevenLabs-Signature": f"t={ts},v0={sig}",
                                  "Content-Type": "application/json"})
        assert ok.json()["analysis_queued"] is True
        bad = client.post("/webhooks/elevenlabs", content=body,
                          headers={"ElevenLabs-Signature": f"t={ts},v0=deadbeef"})
        assert bad.status_code == 401


class TestAnalysis:
    def test_interested_stops_the_sequence_and_notifies(self, db_session, placed_call,
                                                        fake_claude, monkeypatch):
        emitted = []
        from app.services import event_bus
        monkeypatch.setattr(event_bus, "emit",
                            lambda db, uid, event, **kw: emitted.append(event))
        placed_call.transcript = "User: yes please, Thursday"
        db_session.commit()
        analysis = phone_calls.analyze(db_session, placed_call)
        assert analysis["outcome"] == "interested"
        lead = db_session.get(m.Lead, placed_call.lead_id)
        assert lead.status is m.LeadStatus.REPLIED and lead.last_call_outcome == "interested"
        enrollment = db_session.query(m.SequenceEnrollment).filter_by(lead_id=lead.id).one()
        assert enrollment.status is m.EnrollmentStatus.STOPPED
        assert db_session.query(m.Outcome).filter_by(event=m.OutcomeEvent.REPLIED,
                                                     channel="phone").count() == 1
        assert emitted == ["reply_interested", "call_completed"]

    def test_stop_request_suppresses_the_number(self, db_session, placed_call, fake_claude):
        from app.workers.lead_tasks import is_suppressed

        fake_claude.call_analysis_response = {"outcome": "not_interested", "stop_request": True}
        placed_call.transcript = "User: stop calling me"
        db_session.commit()
        phone_calls.analyze(db_session, placed_call)
        assert is_suppressed(db_session, phone="+1 (555) 010-2000")

    def test_outbound_spoken_stop_is_recorded_once_without_the_model(self, db_session,
                                                                     placed_call, fake_claude):
        """I1 / T15: on an OUTBOUND call a spoken "stop calling me" is recorded
        by the deterministic check, from the callee's turns only, when the
        model is down -- through the webhook's post-call check and every
        Celery retry -- and the unsubscribe is written exactly once."""
        from app.services import voice_twin
        from app.workers.lead_tasks import is_suppressed

        fake_claude.call_analysis_response = RuntimeError("simulated gateway outage")
        placed_call.transcript = ("AI: Hi Sara, this is an AI assistant.\n"
                                  "User: Don't call me again.")
        db_session.commit()
        assert voice_twin.post_call_check(db_session, placed_call)["stop"] is True
        for _attempt in range(3):  # analyze_call: max_retries=2, then it gives up
            with pytest.raises(RuntimeError):
                phone_calls.analyze(db_session, placed_call)
            db_session.rollback()
        assert is_suppressed(db_session, phone="+1 (555) 010-2000")
        lead = db_session.get(m.Lead, placed_call.lead_id)
        assert db_session.query(m.Outcome).filter_by(
            lead_id=lead.id, event=m.OutcomeEvent.UNSUBSCRIBED).count() == 1
        enrollment = db_session.query(m.SequenceEnrollment).filter_by(lead_id=lead.id).one()
        assert enrollment.status is m.EnrollmentStatus.STOPPED

    def test_stop_seen_by_check_and_model_is_unsubscribed_once(self, db_session, placed_call,
                                                               fake_claude):
        from app.services import voice_twin

        fake_claude.call_analysis_response = {"outcome": "not_interested", "stop_request": True}
        placed_call.transcript = "AI: Hi Sara.\nUser: Please stop calling me."
        db_session.commit()
        voice_twin.post_call_check(db_session, placed_call)
        phone_calls.analyze(db_session, placed_call)
        phone_calls.analyze(db_session, placed_call)  # a redelivered task
        assert db_session.query(m.Outcome).filter_by(
            lead_id=placed_call.lead_id, event=m.OutcomeEvent.UNSUBSCRIBED).count() == 1
        assert db_session.query(m.ConsentEvent).filter_by(
            lead_id=placed_call.lead_id, kind="withdrawn").count() == 1

    @pytest.mark.parametrize("said", [
        "Do not contact me again.", "Stop contacting me.", "Never call here again.",
        "I want to opt out.", "Not interested, don't call back.", "Delete my data.",
        "Leave me alone.", "Please don't phone me.", "Don’t ring me again."])
    def test_common_stop_wordings_are_recorded_when_the_model_is_down(
            self, db_session, placed_call, fake_claude, said):
        """The pre-model check is the only stop handling when analysis fails.
        It used to know only "stop calling" / "don't call me" style phrases,
        so "Do not contact me again" left the number callable."""
        from app.services import voice_twin
        from app.workers.lead_tasks import is_suppressed

        fake_claude.call_analysis_response = RuntimeError("simulated gateway outage")
        placed_call.transcript = f"AI: Hi Sara, this is an AI assistant.\nUser: {said}"
        db_session.commit()
        assert voice_twin.post_call_check(db_session, placed_call)["stop"] is True
        assert is_suppressed(db_session, phone="+1 (555) 010-2000")

    @pytest.mark.parametrize("said", [
        "Sounds good, call me Tuesday.", "I can't stop thinking about month-end.",
        "Don't worry, call me later this week."])
    def test_ordinary_replies_are_not_a_stop(self, said):
        from app.services import voice_twin

        assert voice_twin.caller_asked_to_stop([said]) is False

    def test_outbound_stop_the_model_missed_is_still_recorded(self, db_session, placed_call,
                                                              fake_claude):
        from app.workers.lead_tasks import is_suppressed

        fake_claude.call_analysis_response = {"outcome": "not_interested",
                                              "stop_request": False}
        placed_call.transcript = "AI: Hi Sara.\nUser: Stop calling me. Remove my number."
        db_session.commit()
        assert phone_calls.analyze(db_session, placed_call)["stop_request"] is True
        assert is_suppressed(db_session, phone="+1 (555) 010-2000")

    def test_assistant_saying_it_wont_call_again_is_not_a_stop(self, db_session, placed_call,
                                                               fake_claude):
        from app.services import voice_twin
        from app.workers.lead_tasks import is_suppressed

        fake_claude.call_analysis_response = {"outcome": "answered", "stop_request": False}
        placed_call.transcript = ("AI: No problem, I won't call again unless you ask. "
                                  "Don't call me back is fine too.\n"
                                  "User: Actually, call me next week.")
        db_session.commit()
        assert voice_twin.post_call_check(db_session, placed_call)["stop"] is False
        phone_calls.analyze(db_session, placed_call)
        assert not is_suppressed(db_session, phone="+1 (555) 010-2000")

    def test_answered_keeps_the_sequence(self, db_session, placed_call, fake_claude):
        fake_claude.call_analysis_response = {"outcome": "answered", "summary": "Call back later"}
        placed_call.transcript = "User: busy now"
        db_session.commit()
        phone_calls.analyze(db_session, placed_call)
        enrollment = db_session.query(m.SequenceEnrollment).one()
        assert enrollment.status is m.EnrollmentStatus.ACTIVE

    def test_call_completed_reaches_a_webhook_subscriber(self, db_session, placed_call,
                                                         fake_claude, test_user, monkeypatch):
        """gaps-sweep-2 #1: call_completed is subscribable and documented, but
        was emitted without a webhook payload, so emit() never delivered it."""
        from app.core.crypto import encrypt
        from app.services import event_bus
        from app.services import webhook_delivery as wd

        monkeypatch.setattr(event_bus, "_push", lambda *a, **k: 0)
        monkeypatch.setattr(event_bus, "_slack", lambda *a, **k: False)
        monkeypatch.setattr(wd, "public_destination", lambda url: True)
        db_session.add(m.WebhookTarget(user_id=test_user.id, url="https://hooks.example.com/c",
                                       event_types=["call_completed"], active=True,
                                       secret_encrypted=encrypt("s")))
        placed_call.duration_seconds = 184
        placed_call.transcript = "User: busy now"
        db_session.commit()
        fake_claude.call_analysis_response = {"outcome": "answered", "summary": "Call back"}
        results = []
        real_emit = event_bus.emit

        def spy(db, uid, event, **kw):
            out = real_emit(db, uid, event, **kw)
            results.append((event, kw.get("webhook_payload"), out))
            return out

        monkeypatch.setattr(event_bus, "emit", spy)
        phone_calls.analyze(db_session, placed_call)
        (event, payload, out), = [r for r in results if r[0] == "call_completed"]
        assert out["webhooks"] == 1
        assert set(payload) == set(wd.SAMPLES["call_completed"])
        assert payload == {"call_id": str(placed_call.id), "lead_id": str(placed_call.lead_id),
                           "outcome": "answered", "duration_seconds": 184}
        delivery = db_session.query(m.WebhookDelivery).filter_by(event_type="call_completed").one()
        assert delivery.payload_json["data"]["call_id"] == str(placed_call.id)


class TestApi:
    def test_consent_and_listing(self, client, db_session, phone_lead, placed_call):
        phone_lead.phone_consent_at = None
        db_session.commit()
        assert client.put(f"/leads/{phone_lead.id}/phone-consent",
                          json={"source": "signed form 2026-09-01"}).status_code == 200
        db_session.refresh(phone_lead)
        assert phone_lead.phone_consent_source == "signed form 2026-09-01"
        assert client.delete(f"/leads/{phone_lead.id}/phone-consent").status_code == 204
        calls = client.get(f"/strategies/{phone_lead.strategy_id}/calls").json()
        assert calls[0]["lead_name"] == "Sara Khan"
        detail = client.get(f"/calls/{placed_call.id}")
        assert detail.status_code == 200
        assert detail.json()["id"] == str(placed_call.id)
        assert detail.json()["voicemail_text"] == "Hi Sara, AI assistant here."

    def test_call_now(self, client, db_session, phone_lead, voice, calling_on, fake_claude,
                      test_user):
        # Plan enforcement: AI calls need a plan with the phone channel (Scale
        # and up); this test is about the call path, not plans.
        test_user.plan = m.PlanTier.SCALE
        db_session.commit()
        resp = client.post(f"/leads/{phone_lead.id}/call", json={})
        assert resp.status_code == 201, resp.text
        assert resp.json()["status"] == "ringing"
        phone_lead.phone_consent_at = None
        db_session.commit()
        assert client.post(f"/leads/{phone_lead.id}/call", json={}).status_code == 409

    def test_call_now_refuses_when_disabled(self, client, phone_lead, voice, fake_claude):
        assert client.post(f"/leads/{phone_lead.id}/call", json={}).status_code == 409

    def test_other_tenant_404(self, client, db_session, placed_call):
        other = m.User(email="calls-o@x.test", email_verified=True)
        db_session.add(other)
        db_session.commit()
        assert client.get(f"/calls/{placed_call.id}", headers=auth_headers(other)).status_code == 404
