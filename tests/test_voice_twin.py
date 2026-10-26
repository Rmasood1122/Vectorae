"""Voice twin -- feature behaviour (docs/features/voice-twin.md).

The legal/safety guards are in tests/test_voice_twin_guards.py. This file
covers the rest: the clone lifecycle against a fake ElevenLabs, the request
shapes sent to the providers, live notification and takeover, inbound tools
(booking through the host lock, taking a message), export/erasure of
lead-less inbound calls, the flag and the housekeeping task. Offline: every
provider is a fake; httpx is never reached.
"""

from __future__ import annotations

import json
from datetime import time, timedelta, timezone

import pytest

from app.db import models as m
from app.integrations import voice_providers as vp
from app.services import credentials, system_settings

from .conftest import NOW
from .test_voice_twin_guards import (
    MP3,
    SECRET,
    FakeEleven,
    FakeVapi,
    _assistant_request,
    _consent,
    _inbound_setup,
    _ready_clone,
    _upload,
    _vapi,
)

UTC = timezone.utc


@pytest.fixture()
def providers(monkeypatch, tmp_path):
    from app.config import settings

    monkeypatch.setattr(settings, "media_dir", str(tmp_path))
    vapi, eleven = FakeVapi(), FakeEleven()
    monkeypatch.setattr("app.integrations.voice_providers.get_clients",
                        lambda db, user_id=None: (vapi, eleven))
    return {"vapi": vapi, "eleven": eleven}


@pytest.fixture()
def owner(db_session, test_user):
    test_user.full_name = "Rehan Masood"
    test_user.plan = m.PlanTier.SCALE
    db_session.commit()
    return test_user


@pytest.fixture()
def twin_on(db_session):
    system_settings.set(db_session, "phone_calling_enabled", True)
    system_settings.set(db_session, "voice_clone_calls_enabled", True)


# --------------------------------------------------------------------------
# Flag, events, audit names
# --------------------------------------------------------------------------


class TestFlag:
    def test_default_off_with_a_description(self):
        default, description = system_settings.DEFAULTS["voice_clone_calls_enabled"]
        assert default is False and "AI assistant" in description

    def test_needs_ai_calling_too(self, db_session):
        from app.services import voice_twin

        system_settings.set(db_session, "voice_clone_calls_enabled", True)
        assert voice_twin.enabled(db_session) is False
        system_settings.set(db_session, "phone_calling_enabled", True)
        assert voice_twin.enabled(db_session) is True

    def test_flag_off_refuses_consent_and_upload(self, client, db_session, owner, providers):
        state = client.get("/me/voice-clone").json()
        assert state["feature_enabled"] is False and state["clone"] is None
        assert _consent(client).status_code == 409
        assert _upload(client).status_code == 409

    def test_events_are_registered(self):
        from app.services import event_bus

        assert {"ai_call_live", "voice_twin_incident", "ai_call_message"} <= set(event_bus.EVENTS)


# --------------------------------------------------------------------------
# Clone lifecycle
# --------------------------------------------------------------------------


class TestCloneLifecycle:
    def test_consent_upload_verify_train_ready(self, client, db_session, owner, providers,
                                               twin_on):
        eleven = providers["eleven"]
        state = client.get("/me/voice-clone").json()
        assert state["consent"]["version"] == "1"
        assert "this is my own voice" in state["consent"]["text"]
        assert _consent(client).status_code == 201
        assert _consent(client).status_code == 409            # one live clone at a time
        for _ in range(2):
            assert _upload(client).status_code == 201
        assert eleven.created == ["Vectorae - Rehan"]
        state = client.post("/me/voice-clone/submit").json()
        assert state["clone"]["status"] == "verifying"

        # verification text: passed on when ElevenLabs gives an image
        eleven.pvc_captcha = lambda vid: {"kind": "image", "image": "data:image/png;base64,AA"}
        challenge = client.get("/me/voice-clone/verification").json()
        assert challenge["kind"] == "image" and "elevenlabs.io" in challenge["fallback"]
        received = []
        eleven.pvc_verify_captcha = lambda vid, **kw: received.append((vid, kw["content_type"]))
        eleven.voice_state = {"voice_verification": {"requires_verification": True,
                                                     "is_verified": True},
                              "fine_tuning": {"state": {"eleven_flash_v2_5": "not_started"}}}
        state = client.post("/me/voice-clone/verification", content=MP3,
                            headers={"Content-Type": "audio/mpeg"}).json()
        assert received == [("pvc-1", "audio/mpeg")]
        assert state["clone"]["status"] == "training"
        assert eleven.trained == ("pvc-1", "eleven_flash_v2_5")

        eleven.voice_state = {"fine_tuning": {"state": {"eleven_flash_v2_5": "fine_tuned"}}}
        state = client.post("/me/voice-clone/refresh").json()
        assert state["clone"]["status"] == "ready" and state["clone"]["ready_at"]

        # preview: audio straight back, nothing stored
        resp = client.get("/me/voice-clone/preview")
        assert resp.status_code == 200 and resp.headers["content-type"] == "audio/mpeg"
        assert resp.headers["cache-control"] == "no-store"

        # use my voice
        assert client.put("/me/voice-clone/use", json={"enabled": True}).json()[
            "settings"]["use_clone_for_calls"] is True

    def test_failed_training_is_shown_honestly(self, client, db_session, owner, providers,
                                               twin_on):
        _consent(client)
        _upload(client)
        client.post("/me/voice-clone/submit")
        providers["eleven"].voice_state = {
            "fine_tuning": {"state": {"eleven_flash_v2_5": "failed"}}}
        state = client.post("/me/voice-clone/refresh").json()
        assert state["clone"]["status"] == "failed" and state["clone"]["error"]

    def test_use_needs_a_ready_clone(self, client, db_session, owner, providers, twin_on):
        assert client.put("/me/voice-clone/use", json={"enabled": True}).status_code == 409
        assert client.put("/me/voice-clone/use", json={"enabled": False}).status_code == 200

    def test_sample_caps(self, client, db_session, owner, providers, twin_on, monkeypatch):
        from app.services import voice_twin

        _consent(client)
        monkeypatch.setattr(voice_twin, "MAX_SAMPLES", 1)
        assert _upload(client).status_code == 201
        assert _upload(client).status_code == 413
        monkeypatch.setattr(voice_twin, "MAX_SAMPLES", 25)
        monkeypatch.setattr(voice_twin, "MAX_TOTAL_BYTES", len(MP3) + 10)
        assert _upload(client).status_code == 413

    def test_provider_failure_keeps_nothing(self, client, db_session, owner, providers,
                                            twin_on):
        def boom(*a, **k):
            raise vp.VoiceProviderError("nope", status=500)

        _consent(client)
        providers["eleven"].pvc_add_sample = boom
        resp = _upload(client)
        assert resp.status_code == 502 and "Nothing was kept" in resp.json()["detail"]
        clone = db_session.query(m.VoiceClone).one()
        assert clone.sample_count == 0
        # the provider id survives so a revoke can still delete the voice
        assert clone.provider_voice_id == "pvc-1"

    def test_retention_expiry_revokes_an_unused_clone(self, db_session, owner, providers):
        from app.services import voice_twin

        clone = _ready_clone(db_session, owner)
        clone.last_used_at = NOW - timedelta(days=365 * 3 + 2)
        db_session.commit()
        result = voice_twin.retry_pending(db_session, now=NOW)
        assert result["expired"] == 1
        db_session.refresh(clone)
        assert clone.status == "revoked" and providers["eleven"].deleted == ["pvc-1"]

    def test_housekeeping_task_is_scheduled(self):
        from app.workers.celery_app import celery_app

        entry = celery_app.conf.beat_schedule["voice-clone-housekeeping"]
        assert entry["task"] == "app.workers.call_tasks.voice_clone_housekeeping"


# --------------------------------------------------------------------------
# Provider request shapes (fake httpx)
# --------------------------------------------------------------------------


class _Resp:
    def __init__(self, status=200, data=None, content=b"", ctype="application/json"):
        self.status_code, self._data, self.content = status, data, content
        self.headers = {"content-type": ctype}
        self.text = json.dumps(data) if data is not None else ""

    def json(self):
        if self._data is None:
            raise ValueError("no json")
        return self._data


class _Http:
    def __init__(self, responses):
        self.responses, self.requests = list(responses), []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def _go(self, method, url, **kw):
        self.requests.append((method, url, kw))
        return self.responses.pop(0)

    def post(self, url, **kw):
        return self._go("POST", url, **kw)

    def get(self, url, **kw):
        return self._go("GET", url, **kw)

    def delete(self, url, **kw):
        return self._go("DELETE", url, **kw)


def _fake_http(monkeypatch, *responses):
    http = _Http(responses)
    monkeypatch.setattr(vp, "_http", lambda: http)
    return http


class TestProviderShapes:
    def test_assistant_uses_documented_fields_only(self):
        a = vp.vapi_assistant(first_message="Hi", system_prompt="p", server_url="https://x/w",
                              server_secret="s", voicemail_message="vm", voice_id="pvc-1",
                              voice_is_clone=True, transfer_number="+15550001111")
        for legacy in ("serverUrl", "serverUrlSecret", "recordingEnabled",
                       "endCallFunctionEnabled", "backchannelingEnabled"):
            assert legacy not in a
        assert a["server"] == {"url": "https://x/w", "headers": {"x-vapi-secret": "s"}}
        assert a["artifactPlan"] == {"recordingEnabled": True}
        assert a["monitorPlan"]["controlEnabled"] is True
        assert a["stopSpeakingPlan"]["numWords"] == 0
        assert a["startSpeakingPlan"]["waitSeconds"] == 0.4
        assert a["voice"]["provider"] == "11labs" and a["voice"]["voiceId"] == "pvc-1"
        assert a["voice"]["model"] == "eleven_flash_v2_5"
        assert "fillerInjectionEnabled" not in a["voice"]
        tools = {t["type"] for t in a["model"]["tools"]}
        assert tools == {"endCall", "transferCall"}
        transfer = [t for t in a["model"]["tools"] if t["type"] == "transferCall"][0]
        assert transfer["destinations"][0]["number"] == "+15550001111"

    def test_unverified_fields_only_behind_the_constant(self, monkeypatch):
        monkeypatch.setattr(vp, "SEND_UNVERIFIED_VAPI_FIELDS", True)
        a = vp.vapi_assistant(first_message="Hi", system_prompt="p", server_url="u",
                              server_secret=None, voice_id="v")
        assert a["backchannelingEnabled"] is True and a["voice"]["fillerInjectionEnabled"]

    def test_create_call_passes_the_clone_and_returns_the_control_url(self, monkeypatch):
        http = _fake_http(monkeypatch, _Resp(data={
            "id": "c1", "status": "queued",
            "monitor": {"controlUrl": "https://aws.vapi.ai/c1/control"}}))
        out = vp.VapiClient("key", "pn").create_call(
            to_number="+1555", customer_name="S", system_prompt="p", first_message="Hi",
            voicemail_message=None, server_url="u", server_secret=None, metadata={},
            voice_id="pvc-1", voice_is_clone=True)
        assert out["control_url"] == "https://aws.vapi.ai/c1/control"
        body = http.requests[0][2]["json"]
        assert body["assistant"]["voice"]["voiceId"] == "pvc-1"
        assert body["phoneNumberId"] == "pn"

    def test_pvc_requests(self, monkeypatch):
        http = _fake_http(monkeypatch, _Resp(data={"voice_id": "v9"}),
                          _Resp(data={"sample_id": "s"}), _Resp(data={"status": "ok"}),
                          _Resp(content=b"\x89PNG", ctype="image/png"),
                          _Resp(data={"status": "ok"}), _Resp(status=404, data={}))
        el = vp.ElevenLabsClient("k")
        assert el.pvc_create(name="Vectorae - Rehan", language="en") == "v9"
        el.pvc_add_sample("v9", filename="a.mp3", content=b"ID3", content_type="audio/mpeg")
        el.pvc_train("v9", "eleven_flash_v2_5")
        assert el.pvc_captcha("v9")["kind"] == "image"
        el.pvc_verify_captcha("v9", filename="r.mp3", content=b"ID3", content_type="audio/mpeg")
        el.delete_voice("v9")                  # 404 = already gone
        methods = [(r[0], r[1].replace(vp.ELEVEN_BASE, "")) for r in http.requests]
        assert methods == [("POST", "/v1/voices/pvc"), ("POST", "/v1/voices/pvc/v9/samples"),
                           ("POST", "/v1/voices/pvc/v9/train"),
                           ("GET", "/v1/voices/pvc/v9/captcha"),
                           ("POST", "/v1/voices/pvc/v9/captcha"), ("DELETE", "/v1/voices/v9")]
        assert http.requests[1][2]["files"][0][0] == "files"
        assert http.requests[4][2]["files"][0][0] == "recording"
        assert http.requests[2][2]["json"] == {"model_id": "eleven_flash_v2_5"}


# --------------------------------------------------------------------------
# Live: notification, banner, takeover
# --------------------------------------------------------------------------


@pytest.fixture()
def live_call(db_session, owner, verified_strategy):
    lead = m.Lead(strategy_id=verified_strategy.id, source="x", external_id="L1",
                  full_name="Sara Khan", phone="+15550102000", status=m.LeadStatus.VERIFIED)
    db_session.add(lead)
    db_session.flush()
    call = m.Call(lead_id=lead.id, strategy_id=verified_strategy.id, user_id=owner.id,
                  provider="vapi", provider_call_id="live-1", to_number="+15550102000",
                  status="ringing", voice_clone_used=True)
    db_session.add(call)
    db_session.commit()
    return call


class TestLive:
    def test_going_live_notifies_once_and_stores_the_control_url(self, client, db_session,
                                                                live_call, queued_jobs):
        credentials.set_system_secret(db_session, "vapi", "webhook_secret", SECRET)
        update = {"message": {"type": "status-update", "status": "in-progress",
                              "call": {"id": "live-1", "monitor": {
                                  "controlUrl": "https://aws.vapi.ai/live-1/control"}}}}
        _vapi(client, update)
        _vapi(client, update)
        events = [e for e in queued_jobs["events"] if e[1] == "ai_call_live"]
        assert len(events) == 1
        assert "in your voice" in events[0][2]["body"]
        assert events[0][2]["data"]["action"] == "take_over"
        db_session.refresh(live_call)
        assert live_call.control_url == "https://aws.vapi.ai/live-1/control"
        # the control URL is never returned
        assert "control_url" not in client.get(f"/calls/{live_call.id}").json()

    def test_a_foreign_control_url_is_ignored(self, client, db_session, live_call):
        credentials.set_system_secret(db_session, "vapi", "webhook_secret", SECRET)
        _vapi(client, {"message": {"type": "status-update", "status": "in-progress",
                                   "call": {"id": "live-1",
                                            "monitor": {"controlUrl": "https://evil.test/c"}}}})
        db_session.refresh(live_call)
        assert live_call.control_url is None

    def test_banner_and_takeover(self, client, db_session, owner, live_call, providers):
        live_call.control_url = "https://aws.vapi.ai/live-1/control"
        live_call.status = "in_progress"
        db_session.add(m.VoiceTwinSettings(user_id=owner.id))
        db_session.commit()
        # L7: with AI calling off there is nothing to poll for
        assert client.get("/voice-twin/live-calls").json() == {"calling_enabled": False,
                                                                "calls": []}
        system_settings.set(db_session, "phone_calling_enabled", True)
        banner = client.get("/voice-twin/live-calls").json()["calls"]
        assert banner[0]["who"] == "Sara Khan" and banner[0]["can_take_over"] is False
        assert client.post(f"/calls/{live_call.id}/takeover").status_code == 409  # no number
        client.put("/voice-twin/settings", json={"transfer_number": "+1 555 000 1111"})
        assert client.get("/voice-twin/live-calls").json()["calls"][0]["can_take_over"] is True
        resp = client.post(f"/calls/{live_call.id}/takeover")
        assert resp.status_code == 200 and resp.json()["to"].endswith("1111")
        url, number, content = providers["vapi"].controls[0]
        assert number == "+15550001111" and "Rehan" in content
        assert db_session.query(m.SecurityAuditEvent).filter_by(
            action="voice_twin.takeover").count() == 1

    def test_takeover_of_an_ended_call(self, client, db_session, live_call, providers):
        live_call.status = "ended"
        db_session.commit()
        assert client.post(f"/calls/{live_call.id}/takeover").status_code == 409


# --------------------------------------------------------------------------
# Inbound: assistant, tools, unknown callers
# --------------------------------------------------------------------------


@pytest.fixture()
def booking_page(db_session, owner):
    page = m.CalendarBookingPage(user_id=owner.id, slug="rehan-intro", title="Intro",
                                 duration_minutes=30, is_active=True)
    db_session.add(page)
    for dow in range(7):
        db_session.add(m.CalendarAvailability(user_id=owner.id, day_of_week=dow,
                                              start_time=time(0, 0), end_time=time(23, 30),
                                              timezone="UTC", is_active=True))
    db_session.commit()
    return page


def _tool_call(name, args, call_id="in-1"):
    return {"message": {"type": "tool-calls", "call": {"id": call_id},
                        "toolCallList": [{"id": "t1", "name": name, "parameters": args}]}}


class TestInbound:
    def test_unknown_caller_gets_a_receptionist_and_a_lead_less_call(
            self, client, db_session, owner, providers, twin_on):
        _inbound_setup(client, db_session, owner)
        resp = _vapi(client, _assistant_request(caller="+15550107777"),
                     path="/webhooks/vapi/inbound")
        assistant = resp.json()["assistant"]
        prompt = assistant["model"]["messages"][0]["content"]
        assert "not a known contact" in prompt
        names = {t.get("function", {}).get("name") for t in assistant["model"]["tools"]}
        assert "take_message" in names and "book_meeting" not in names   # no booking page
        call = db_session.query(m.Call).one()
        assert call.lead_id is None and call.direction == "inbound"
        assert call.from_number == "+15550107777"
        # the call shows on the API, with its direction
        out = client.get(f"/calls/{call.id}").json()
        assert out["direction"] == "inbound" and out["lead_id"] is None
        listed = client.get("/voice-twin/inbound-calls").json()
        assert [c["id"] for c in listed] == [str(call.id)]
        assert "control_url" not in listed[0]
        # a retried assistant-request reuses the row
        _vapi(client, _assistant_request(caller="+15550107777"), path="/webhooks/vapi/inbound")
        assert db_session.query(m.Call).count() == 1

    def test_tools_book_through_the_host_lock(self, client, db_session, owner, providers,
                                              twin_on, booking_page, monkeypatch):
        from app.services import calendar_service as cal
        from app.workers import calendar_tasks

        monkeypatch.setattr(calendar_tasks.on_booking_created, "delay", lambda *a: None)
        locked = []
        real_lock = cal.lock_host_calendar
        monkeypatch.setattr(cal, "lock_host_calendar",
                            lambda db, uid: (locked.append(uid), real_lock(db, uid)))
        _inbound_setup(client, db_session, owner)
        assistant = _vapi(client, _assistant_request(),
                          path="/webhooks/vapi/inbound").json()["assistant"]
        names = {t.get("function", {}).get("name") for t in assistant["model"]["tools"]}
        assert {"find_times", "book_meeting", "take_message"} <= names
        found = _vapi(client, _tool_call("find_times", {}),
                      path="/webhooks/vapi/inbound").json()["results"][0]
        assert found["toolCallId"] == "t1" and "start_at" in found["result"]
        slot = found["result"].split("(start_at ")[1].split(")")[0]
        booked = _vapi(client, _tool_call("book_meeting", {
            "start_at": slot, "name": "Sara Khan", "email": "sara@acme.test"}),
            path="/webhooks/vapi/inbound").json()["results"][0]["result"]
        assert booked.startswith("Booked for"), booked
        assert locked == [owner.id]
        booking = db_session.query(m.CalendarBooking).one()
        assert booking.invitee_email == "sara@acme.test"
        again = _vapi(client, _tool_call("book_meeting", {
            "start_at": slot, "name": "X", "email": "x@y.test"}),
            path="/webhooks/vapi/inbound").json()["results"][0]["result"]
        assert "no longer free" in again or "just taken" in again

    def test_take_message_notifies_the_owner(self, client, db_session, owner, providers,
                                             twin_on, queued_jobs):
        _inbound_setup(client, db_session, owner)
        _vapi(client, _assistant_request(caller="+15550107777"), path="/webhooks/vapi/inbound")
        out = _vapi(client, _tool_call("take_message", {
            "caller_name": "Bo", "message": "Call me about pricing",
            "callback_number": "+15550107777"}), path="/webhooks/vapi/inbound").json()
        assert out["results"][0]["result"].startswith("Message taken")
        call = db_session.query(m.Call).one()
        assert call.script_json["messages_taken"][0]["message"] == "Call me about pricing"
        assert "ai_call_message" in [e[1] for e in queued_jobs["events"]]

    def test_tools_never_run_for_an_outbound_call(self, client, db_session, live_call):
        credentials.set_system_secret(db_session, "vapi", "webhook_secret", SECRET)
        out = _vapi(client, _tool_call("take_message", {"message": "x"}, call_id="live-1"))
        assert "results" not in out.json()

    def test_inbound_number_cannot_be_claimed_twice(self, client, db_session, owner,
                                                    providers, twin_on):
        from .conftest import auth_headers

        _inbound_setup(client, db_session, owner)
        other = m.User(email="o9@x.test", email_verified=True)
        db_session.add(other)
        db_session.commit()
        from .test_voice_twin_guards import _own_number

        _own_number(db_session, other)            # the same number id, assigned twice
        resp = client.put("/voice-twin/settings", json={"answer_inbound": True},
                          headers=auth_headers(other))
        assert resp.status_code == 409
        assert "already answers for another account" in resp.json()["detail"]
