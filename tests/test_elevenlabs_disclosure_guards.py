"""An ElevenLabs call is never placed without our AI and recording disclosure.

The disclosure rides on per-call overrides (the opening line and the script,
plus the voice for a cloned voice), which the agent must allow: ElevenLabs
"Overrides" doc (read 2026-10-30) -- off by default, switched on per field in
the agent's Security tab; an override that is not switched on is rejected for
most fields and ignored for some. So the phone channel reads the agent's
settings first (GET agent: platform_settings.overrides
.conversation_config_override) and refuses the call, before anything is
dialled, unless every override it sends is allowed.

Founder choice in session 2026-10-30: "AI-call disclosure check".
"""

from __future__ import annotations

import httpx
import pytest

from app.integrations import voice_providers as vp
from app.integrations.outreach_base import OutboundMessage
from app.integrations.phone_channel import PhoneChannel

CALL_OK = {"success": True, "message": "ok", "conversation_id": "conv-1", "callSid": "CA1"}


def _agent(first_message=True, prompt=True, voice_id=True):
    return {"agent_id": "ag", "platform_settings": {"overrides": {
        "conversation_config_override": {
            "agent": {"first_message": first_message, "language": False,
                      "prompt": {"prompt": prompt}},
            "tts": {"voice_id": voice_id}}}}}


class _ElevenLabs:
    """ElevenLabs over a mock transport: GET agent, POST outbound call."""

    def __init__(self, agent=None, agent_status=200):
        self.agent, self.agent_status = agent if agent is not None else _agent(), agent_status
        self.requests: list[httpx.Request] = []

    def __call__(self):
        def handler(request):
            self.requests.append(request)
            if request.method == "GET" and request.url.path == "/v1/convai/agents/ag":
                return httpx.Response(self.agent_status, json=self.agent)
            if request.method == "POST" and request.url.path.endswith("/outbound-call"):
                return httpx.Response(200, json=CALL_OK)
            return httpx.Response(404, json={})
        return httpx.Client(transport=httpx.MockTransport(handler))

    @property
    def posts(self):
        return [r for r in self.requests if r.method == "POST"]

    @property
    def gets(self):
        return [r for r in self.requests if r.method == "GET"]


def _send(db_session, monkeypatch, api, **meta):
    monkeypatch.setattr(vp, "_http", api)
    eleven = vp.ElevenLabsClient(api_key="k", agent_id="ag", agent_phone_number_id="ph")
    return PhoneChannel(db_session, vapi=None, eleven=eleven).send(OutboundMessage(
        message_id="m1", lead_id=None, to_address="+15550100000", body="hi",
        metadata={"first_message": "hi", **meta}))


class TestAgentAllowsOverrides:
    def test_the_call_is_placed(self, db_session, monkeypatch):
        api = _ElevenLabs()
        result = _send(db_session, monkeypatch, api)
        assert result.ok and result.provider_message_id == "conv-1"
        assert [r.method for r in api.requests] == ["GET", "POST"]
        assert api.gets[0].headers["xi-api-key"] == "k"

    def test_the_settings_are_read_before_every_call(self, db_session, monkeypatch):
        api = _ElevenLabs()
        _send(db_session, monkeypatch, api)
        _send(db_session, monkeypatch, api)
        assert [r.method for r in api.requests] == ["GET", "POST", "GET", "POST"]

    def test_an_override_switched_off_stops_the_next_call(self, db_session, monkeypatch):
        api = _ElevenLabs()
        assert _send(db_session, monkeypatch, api).ok
        api.agent = _agent(first_message=False)
        assert not _send(db_session, monkeypatch, api).ok
        assert len(api.posts) == 1

    def test_an_ordinary_voice_call_does_not_need_the_voice_override(self, db_session,
                                                                      monkeypatch):
        api = _ElevenLabs(_agent(voice_id=False))
        assert _send(db_session, monkeypatch, api).ok


class TestAgentRefusesOverrides:
    @pytest.mark.parametrize("missing", ["first_message", "prompt"])
    def test_no_call_is_placed_without_the_disclosure(self, db_session, monkeypatch, missing):
        api = _ElevenLabs(_agent(**{missing: False}))
        result = _send(db_session, monkeypatch, api)
        assert not result.ok and result.permanent_failure
        assert api.posts == []
        assert "No call placed" in result.error and "Security tab" in result.error

    def test_a_cloned_voice_needs_the_voice_override(self, db_session, monkeypatch):
        api = _ElevenLabs(_agent(voice_id=False))
        result = _send(db_session, monkeypatch, api, voice_clone=True, voice_id="v1")
        assert not result.ok and api.posts == []
        assert "cloned voice" in result.error

    @pytest.mark.parametrize("agent", [
        {"agent_id": "ag"},                                     # no platform_settings
        {"platform_settings": {"overrides": None}},
        _agent(first_message="true"),                           # not a real boolean
    ])
    def test_settings_that_do_not_say_allowed_count_as_refused(self, db_session, monkeypatch,
                                                               agent):
        api = _ElevenLabs(agent)
        result = _send(db_session, monkeypatch, api)
        assert not result.ok and api.posts == []

    def test_a_refusal_is_not_remembered(self, db_session, monkeypatch):
        api = _ElevenLabs(_agent(prompt=False))
        assert not _send(db_session, monkeypatch, api).ok
        api.agent = _agent()
        assert _send(db_session, monkeypatch, api).ok   # fixed in ElevenLabs: works at once
        assert len(api.gets) == 2


class TestLookupFails:
    def test_a_server_error_is_retried_later_never_dialled(self, db_session, monkeypatch):
        api = _ElevenLabs(agent_status=503)
        result = _send(db_session, monkeypatch, api)
        assert not result.ok and not result.permanent_failure
        assert api.posts == []

    def test_a_network_failure_is_retried_later_never_dialled(self, db_session, monkeypatch):
        def broken():
            def handler(request):
                raise httpx.ConnectError("down")
            return httpx.Client(transport=httpx.MockTransport(handler))

        monkeypatch.setattr(vp, "_http", broken)
        eleven = vp.ElevenLabsClient(api_key="k", agent_id="ag", agent_phone_number_id="ph")
        result = PhoneChannel(db_session, eleven=eleven).send(OutboundMessage(
            message_id="m1", lead_id=None, to_address="+15550100000", body="hi",
            metadata={"first_message": "hi"}))
        assert not result.ok and not result.permanent_failure

    def test_an_unknown_agent_is_a_permanent_failure(self, db_session, monkeypatch):
        api = _ElevenLabs(agent_status=404)
        result = _send(db_session, monkeypatch, api)
        assert not result.ok and result.permanent_failure and api.posts == []


def test_vapi_calls_are_not_checked(db_session, monkeypatch):
    """The check is for ElevenLabs only: Vapi takes the whole assistant per call."""
    calls = []

    class Vapi:
        provider = "vapi"

        def create_call(self, **kw):
            calls.append(kw)
            return {"id": "v-1", "status": "queued"}

    monkeypatch.setattr(vp, "_http", lambda: pytest.fail("no ElevenLabs lookup"))
    result = PhoneChannel(db_session, vapi=Vapi()).send(OutboundMessage(
        message_id="m1", lead_id=None, to_address="+15550100000", body="hi",
        metadata={"first_message": "hi"}))
    assert result.ok and len(calls) == 1
