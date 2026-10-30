"""SEALED safety guard (evals/compliance_testset.yaml): AI phone calls.

* ElevenLabs: a call that may already be ringing (a callSid came back) is
  never dialled again; one that did not start is not recorded as queued
  (its post-call webhook, with any opt-out spoken on it, would be lost).
* ElevenLabs post-call webhooks older than 30 minutes are refused (replay).
* Vapi: the live-call control URL requires our key, so the URL alone cannot
  transfer a live call to another number; voicemail detection uses the
  recommended detector (a person misread as voicemail hears the wrong script).

Moved byte-identically from tests/test_hubspot_recall_voice_contract.py
(#151) by the 2026-10-29 gate PR (docs/program/plans/phase5-seal-vendor-guards.md).
"""

import hashlib
import hmac
import time

import httpx
import pytest

from app.api import calls as calls_api
from app.integrations import voice_providers as vp


class _Recorder:
    def __init__(self, responder):
        self.requests = []
        self.responder = responder

    def __call__(self):
        def handler(request):
            self.requests.append(request)
            return self.responder(request)
        return httpx.Client(transport=httpx.MockTransport(handler))


class TestVapiAssistant:
    def _assistant(self):
        return vp.vapi_assistant(first_message="hi", system_prompt="p",
                                 server_url="https://x.test/hook", server_secret="s")

    def test_voicemail_uses_googles_detector(self):
        assert self._assistant()["voicemailDetection"] == {"provider": "google"}

    def test_live_control_requires_authentication(self):
        plan = self._assistant()["monitorPlan"]
        assert plan["controlEnabled"] is True
        assert plan["controlAuthenticationEnabled"] is True


def _eleven(monkeypatch, body, status=200):
    monkeypatch.setattr(vp, "_http", _Recorder(lambda r: httpx.Response(status, json=body)))
    client = vp.ElevenLabsClient(api_key="k", agent_id="ag", agent_phone_number_id="ph")
    return lambda: client.create_call(
        to_number="+15550100000", customer_name="A", system_prompt="p", first_message="hi",
        voicemail_message=None, server_url="https://x.test", server_secret=None,
        metadata={"call_id": "c1"})


class TestElevenLabsOutboundCall:
    def test_a_started_call_is_identified_by_its_conversation(self, monkeypatch):
        call = _eleven(monkeypatch, {"success": True, "message": "ok",
                                     "conversation_id": "conv-1", "callSid": "CA1"})
        assert call() == {"id": "conv-1", "status": "queued"}

    def test_success_false_is_not_a_queued_call(self, monkeypatch):
        call = _eleven(monkeypatch, {"success": False, "message": "no",
                                     "conversation_id": None, "callSid": None})
        with pytest.raises(vp.VoiceProviderError) as exc:
            call()
        assert not exc.value.permanent  # nothing was dialled: the normal retry applies

    def test_a_call_sid_without_a_conversation_is_never_redialled(self, monkeypatch):
        call = _eleven(monkeypatch, {"success": True, "message": "ok",
                                     "conversation_id": None, "callSid": "CA1"})
        with pytest.raises(vp.VoiceProviderError) as exc:
            call()
        assert exc.value.permanent


class TestElevenLabsWebhookReplay:
    def _header(self, ts, body: bytes, secret=b"esec"):
        sig = hmac.new(secret, str(ts).encode() + b"." + body, hashlib.sha256).hexdigest()
        return f"t={ts},v0={sig}"

    def test_a_fresh_delivery_verifies(self):
        now = int(time.time())
        assert calls_api.verify_elevenlabs(b"{}", self._header(now, b"{}"), "esec")

    def test_an_old_delivery_is_refused(self):
        old = int(time.time()) - 31 * 60
        assert not calls_api.verify_elevenlabs(b"{}", self._header(old, b"{}"), "esec")

    def test_a_non_numeric_timestamp_is_refused(self):
        assert not calls_api.verify_elevenlabs(b"{}", "t=abc,v0=00", "esec")

    def test_a_non_utf8_body_is_refused_not_a_crash(self):
        now = int(time.time())
        assert not calls_api.verify_elevenlabs(b"\xff", self._header(now, b"other"), "esec")
