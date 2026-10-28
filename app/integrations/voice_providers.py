"""Vapi.ai (primary) and ElevenLabs (fallback + TTS) — Feature Group 6.

VAPI
  POST https://api.vapi.ai/call   an outbound call with a TRANSIENT assistant:
                                  the per-lead system prompt and first line,
                                  voicemail detection, the voicemail message,
                                  recording on, and our webhook as serverUrl
                                  (Vapi sends `x-vapi-secret` with it).
ELEVENLABS
  POST /v1/text-to-speech/{voice}  the personalised voicemail as audio
  POST /v1/convai/twilio/outbound-call   the calling FALLBACK, via a
                                  pre-configured Conversational AI agent whose
                                  prompt and first message are overridden per
                                  call

VOICE TWIN (docs/features/voice-twin.md)
  vapi_assistant()                 the ONE assistant builder: outbound calls
                                  and inbound answering both use it, so the
                                  natural-conversation settings, recording,
                                  live control and our webhook auth are the
                                  same on every call
  POST <monitor.controlUrl>       live call control: transfer to the owner
  ElevenLabs Professional Voice Clone (PVC):
    POST   /v1/voices/pvc                       create (name, language)
    POST   /v1/voices/pvc/{id}/samples          multipart `files`
    GET    /v1/voices/pvc/{id}/captcha          verification text (shape UNVERIFIED)
    POST   /v1/voices/pvc/{id}/captcha          multipart `recording`
    POST   /v1/voices/pvc/{id}/train            {"model_id"}
    GET    /v1/voices/{id}                      fine_tuning.state, voice_verification
    DELETE /v1/voices/{id}                      revoke / erasure

Credentials are system scope (Admin > Integrations): `vapi.api_key`,
`vapi.phone_number_id`, optional `vapi.webhook_secret`; `elevenlabs.api_key`,
`elevenlabs.voice_id`, optional `agent_id`, `agent_phone_number_id`,
`webhook_secret`. Payload fields not verified against current provider docs
carry `# TODO: verify` / `# UNVERIFIED against docs` per the codebase's
honesty rule; the voice-twin spec lists every one.
"""

from __future__ import annotations

import contextlib
import logging
import re

import httpx

logger = logging.getLogger(__name__)

VAPI_BASE = "https://api.vapi.ai"
ELEVEN_BASE = "https://api.elevenlabs.io"


class VoiceProviderError(Exception):
    def __init__(self, message: str, status: int | None = None, *,
                 permanent: bool | None = None):
        super().__init__(message)
        self.status = status
        self._permanent = permanent

    @property
    def permanent(self) -> bool:
        if self._permanent is not None:
            return self._permanent
        return self.status is not None and 400 <= self.status < 500 and self.status != 429


def _http() -> httpx.Client:
    """Factory tests monkeypatch."""
    return httpx.Client(timeout=30.0)


@contextlib.contextmanager
def _transport():
    """Review fix M2: a DNS failure, refused connection or timeout is a
    provider error to handle (retry, 502), never an unhandled 500."""
    try:
        yield
    except httpx.HTTPError as exc:
        # The type only: exception text can carry hosts or URLs and must never
        # reach a response (tests/test_gaps_sweep_2.py). The cause is chained.
        raise VoiceProviderError(f"network error ({type(exc).__name__})") from exc


def _check(resp, what: str) -> None:
    if resp.status_code >= 400:
        raise VoiceProviderError(f"{what}: HTTP {resp.status_code} {resp.text[:200]}",
                                 status=resp.status_code)


# ---------------------------------------------------------------------------
# Vapi assistant -- natural conversation, verified field names
# ---------------------------------------------------------------------------
# Checked against docs.vapi.ai on 2026-10-12 (API reference "Create assistant",
# "Voice pipeline configuration", "Call features", "Server authentication",
# the ElevenLabs voice provider page): firstMessage, firstMessageMode,
# startSpeakingPlan.waitSeconds (0-5 s, default 0.4),
# startSpeakingPlan.smartEndpointingPlan.provider, stopSpeakingPlan.numWords /
# voiceSeconds / backoffSeconds, monitorPlan.listenEnabled / controlEnabled,
# server.url / server.headers, artifactPlan.recordingEnabled,
# voicemailDetection, voicemailMessage, maxDurationSeconds, metadata,
# model.tools (types transferCall, endCall, function), voice.provider "11labs"
# with voiceId, model, stability, similarityBoost, optimizeStreamingLatency
# (0-4). NOT in the current create-assistant schema, so no longer sent:
# serverUrl, serverUrlSecret, top-level recordingEnabled,
# endCallFunctionEnabled (replaced by server, artifactPlan and an endCall tool).

# Barge-in: the caller interrupts as soon as voice activity is heard
# (numWords 0 = VAD), the assistant backs off for a second; short
# acknowledgements ("uh-huh", "right") do not count as an interruption.
STOP_SPEAKING_PLAN = {
    "numWords": 0,
    "voiceSeconds": 0.2,
    "backoffSeconds": 1.0,
    # Shown only in the docs' Deepgram Flux examples. # UNVERIFIED against docs
    # for other transcribers.
    "acknowledgementPhrases": ["okay", "right", "uh-huh", "yeah", "mm-hmm", "got it"],
}
# Endpointing: answer quickly, but not over someone who paused mid-sentence.
# LiveKit's model is English-only (per the docs); calls are written in English.
START_SPEAKING_PLAN = {
    "waitSeconds": 0.4,
    "smartEndpointingPlan": {"provider": "livekit"},
}
# Low latency: ElevenLabs Flash v2.5 (listed by Vapi) at streaming latency 3
# (Vapi's default; range 0-4).
ELEVEN_VOICE_MODEL = "eleven_flash_v2_5"
ELEVEN_STREAMING_LATENCY = 3
# A cloned voice should sound like the person: high similarity, mid stability.
CLONE_VOICE_SETTINGS = {"stability": 0.5, "similarityBoost": 0.85}
MAX_CALL_SECONDS = 900

# Backchanneling ("mm-hmm" while the caller talks), filler injection and
# background-speech denoising are NOT in the current create-assistant schema
# we could read. Sending an unknown property may make Vapi refuse the whole
# call, so these stay OFF until someone verifies them against a live account
# and flips this constant. Natural fillers are asked for in the prompt instead.
SEND_UNVERIFIED_VAPI_FIELDS = False
BACKCHANNELING_FIELD = {"backchannelingEnabled": True}          # UNVERIFIED against docs
FILLER_INJECTION_FIELD = {"fillerInjectionEnabled": True}       # UNVERIFIED against docs (voice.*)
BACKGROUND_DENOISING_FIELD = {                                   # UNVERIFIED against docs (inner shape)
    "backgroundSpeechDenoisingPlan": {"smartDenoisingPlan": {"enabled": True}}}
# Live-control endpoint auth: monitorPlan.controlAuthenticationEnabled makes
# the controlUrl require "Authorization: Bearer <api key>" (Vapi MonitorPlan
# schema). It is switched ON: without it anyone holding a call's control URL
# could transfer that live call to any number. Founder choice 2026-10-28
# ("Require auth"). Not yet exercised on a live account.
CONTROL_SENDS_API_KEY = True
# Only Vapi's own hosts may be driven through a control URL (it arrives on a
# webhook; never let it point anywhere else).
CONTROL_HOST_SUFFIX = ".vapi.ai"


def vapi_assistant(*, first_message: str, system_prompt: str, server_url: str,
                   server_secret: str | None, voicemail_message: str | None = None,
                   voice_id: str | None = None, voice_is_clone: bool = False,
                   transfer_number: str | None = None, tools: list | None = None,
                   metadata: dict | None = None, model: str | None = None) -> dict:
    """The transient assistant every Vapi call (outbound or inbound) uses."""
    model_tools: list = [{"type": "endCall"}]
    if transfer_number:
        # Partially verified: `destinations` is documented; the item fields
        # (type "number", E.164 number, message <= 1000 chars, description)
        # come from the call-forwarding and live-control pages.
        model_tools.append({
            "type": "transferCall",
            "destinations": [{
                "type": "number", "number": transfer_number,
                "message": "Of course -- I'm putting you through now. One moment.",
                "description": "The account owner, the real person this assistant "
                               "works for. Use when the caller asks for them, asks "
                               "for a human, or the owner takes over.",
            }],
        })
    model_tools.extend(tools or [])
    assistant: dict = {
        "firstMessage": first_message,
        "firstMessageMode": "assistant-speaks-first",
        "model": {"provider": "anthropic", "model": model or "claude-sonnet-4-6",
                  "messages": [{"role": "system", "content": system_prompt}],
                  "tools": model_tools},
        "startSpeakingPlan": dict(START_SPEAKING_PLAN),
        "stopSpeakingPlan": dict(STOP_SPEAKING_PLAN),
        # Vapi's docs list vapi (default), google (recommended), openai and
        # twilio; twilio is the legacy beep detector with more false
        # positives. Founder choice 2026-10-28 ("Google").
        "voicemailDetection": {"provider": "google"},
        "artifactPlan": {"recordingEnabled": True},
        "monitorPlan": {"listenEnabled": False, "controlEnabled": True,
                        "controlAuthenticationEnabled": True},
        "maxDurationSeconds": MAX_CALL_SECONDS,
        "server": {"url": server_url},
        "metadata": metadata or {},
    }
    if server_secret:
        # Transient assistants get NO org credential (docs: "Server
        # authentication"); the request's own server.headers carry it, and
        # app/api/calls.py::verify_vapi checks exactly this header.
        assistant["server"]["headers"] = {"x-vapi-secret": server_secret}
    if voicemail_message:
        assistant["voicemailMessage"] = voicemail_message
    if voice_id:
        voice = {"provider": "11labs", "voiceId": voice_id, "model": ELEVEN_VOICE_MODEL,
                 "optimizeStreamingLatency": ELEVEN_STREAMING_LATENCY}
        if voice_is_clone:
            voice.update(CLONE_VOICE_SETTINGS)
        if SEND_UNVERIFIED_VAPI_FIELDS:
            voice.update(FILLER_INJECTION_FIELD)
        assistant["voice"] = voice
    if SEND_UNVERIFIED_VAPI_FIELDS:
        assistant.update(BACKCHANNELING_FIELD)
        assistant.update(BACKGROUND_DENOISING_FIELD)
    return assistant


_VAPI_HOST = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+vapi\.ai$")


def control_url_ok(url) -> bool:
    """True only for a plain https URL whose host is a DNS name under vapi.ai
    (no user info, no odd port, no escapes). Parsed with httpx -- the client
    that will make the request -- so the check and the request agree on the
    host. Never raises (L6)."""
    if not isinstance(url, str) or not url or len(url) > 500:
        return False
    if any(ch.isspace() or ord(ch) < 32 for ch in url) or "\\" in url:
        return False
    try:
        parsed = httpx.URL(url)
        host = (parsed.host or "").lower()
        if parsed.scheme != "https" or parsed.userinfo or parsed.port not in (None, 443):
            return False
    except Exception:  # noqa: BLE001 -- anything unparseable is refused
        return False
    return bool(_VAPI_HOST.match(host))


class VapiClient:
    provider = "vapi"

    def __init__(self, api_key: str, phone_number_id: str,
                 voice_id: str | None = None, model: str | None = None):
        self.api_key = api_key
        self.phone_number_id = phone_number_id
        self.voice_id = voice_id
        self.model = model

    def create_call(self, *, to_number: str, customer_name: str | None, system_prompt: str,
                    first_message: str, voicemail_message: str | None, server_url: str,
                    server_secret: str | None, metadata: dict, voice_id: str | None = None,
                    voice_is_clone: bool = False, transfer_number: str | None = None) -> dict:
        """POST /call with a transient assistant. `voice_id` overrides the
        configured stock voice (the owner's ready clone, voice twin)."""
        assistant = vapi_assistant(
            first_message=first_message, system_prompt=system_prompt, server_url=server_url,
            server_secret=server_secret, voicemail_message=voicemail_message,
            voice_id=voice_id or self.voice_id, voice_is_clone=bool(voice_id and voice_is_clone),
            transfer_number=transfer_number, metadata=metadata, model=self.model)
        body = {"phoneNumberId": self.phone_number_id,
                "customer": {"number": to_number, "name": customer_name or ""},
                "assistant": assistant, "metadata": metadata}   # TODO: verify top-level metadata
        with _http() as client:
            resp = client.post(f"{VAPI_BASE}/call", json=body,
                               headers={"Authorization": f"Bearer {self.api_key}"})
        _check(resp, "vapi create call")
        data = resp.json() or {}
        monitor = data.get("monitor") if isinstance(data.get("monitor"), dict) else {}
        return {"id": data.get("id"), "status": data.get("status") or "queued",
                "control_url": monitor.get("controlUrl")}

    def control(self, control_url: str, command: dict) -> dict:
        """POST one live-control command (verified shape for transfer:
        {"type": "transfer", "destination": {"type": "number", "number"},
        "content"}). Refuses any URL that is not Vapi's."""
        if not control_url_ok(control_url):
            raise VoiceProviderError("refusing a control URL that is not on Vapi's domain")
        headers = {"Content-Type": "application/json"}
        if CONTROL_SENDS_API_KEY:
            headers["Authorization"] = f"Bearer {self.api_key}"
        with _transport(), _http() as client:
            resp = client.post(control_url, json=command, headers=headers)
        _check(resp, "vapi live control")
        try:
            return resp.json() or {}
        except ValueError:
            return {}

    def transfer(self, control_url: str, number: str, content: str) -> dict:
        return self.control(control_url, {"type": "transfer",
                                          "destination": {"type": "number", "number": number},
                                          "content": content})


class ElevenLabsClient:
    provider = "elevenlabs"

    def __init__(self, api_key: str, voice_id: str | None = None,
                 agent_id: str | None = None, agent_phone_number_id: str | None = None):
        self.api_key = api_key
        self.voice_id = voice_id
        self.agent_id = agent_id
        self.agent_phone_number_id = agent_phone_number_id

    @property
    def can_call(self) -> bool:
        return bool(self.agent_id and self.agent_phone_number_id)

    def tts(self, text: str, voice_id: str | None = None,
            model_id: str = "eleven_multilingual_v2") -> bytes:
        """POST /v1/text-to-speech/{voice_id} -- path, `text` and `model_id`
        verified 2026-10-12; the response body is the audio (mp3 by default)."""
        voice = voice_id or self.voice_id
        if not voice:
            raise VoiceProviderError("no ElevenLabs voice_id configured")
        with _transport(), _http() as client:
            resp = client.post(f"{ELEVEN_BASE}/v1/text-to-speech/{voice}",
                               json={"text": text, "model_id": model_id},
                               headers={"xi-api-key": self.api_key, "accept": "audio/mpeg"})
        _check(resp, "elevenlabs tts")
        return resp.content

    # --- Professional Voice Clone (voice twin) ---------------------------
    # Paths and field names verified against elevenlabs.io/docs on 2026-10-12
    # unless marked otherwise.

    def _headers(self) -> dict:
        return {"xi-api-key": self.api_key}

    def pvc_create(self, *, name: str, language: str, description: str | None = None) -> str:
        """POST /v1/voices/pvc {name, language, description} -> voice_id."""
        body = {"name": name[:100], "language": language}
        if description:
            body["description"] = description[:500]
        with _transport(), _http() as client:
            resp = client.post(f"{ELEVEN_BASE}/v1/voices/pvc", json=body,
                               headers=self._headers())
        _check(resp, "elevenlabs create voice clone")
        voice_id = (resp.json() or {}).get("voice_id")
        if not voice_id:
            raise VoiceProviderError("elevenlabs create voice clone: no voice_id returned")
        return voice_id

    def pvc_add_sample(self, voice_id: str, *, filename: str, content: bytes,
                       content_type: str) -> dict:
        """POST /v1/voices/pvc/{id}/samples, multipart `files`. The bytes are
        sent from memory; nothing is written to disk."""
        with _transport(), _http() as client:
            resp = client.post(f"{ELEVEN_BASE}/v1/voices/pvc/{voice_id}/samples",
                               files=[("files", (filename, content, content_type))],
                               data={"remove_background_noise": "false"},
                               headers=self._headers())
        _check(resp, "elevenlabs add voice sample")
        data = resp.json()
        first = data[0] if isinstance(data, list) and data else data
        return first if isinstance(first, dict) else {}

    def pvc_captcha(self, voice_id: str) -> dict:
        """GET /v1/voices/pvc/{id}/captcha. The docs show only `{}` as the
        200 body and do not say whether the text to read comes back as an
        image or as text. # UNVERIFIED against docs: we pass on an image as a
        data URL and any JSON text field we recognise; anything else means
        the person verifies on elevenlabs.io instead."""
        import base64  # noqa: PLC0415

        with _transport(), _http() as client:
            resp = client.get(f"{ELEVEN_BASE}/v1/voices/pvc/{voice_id}/captcha",
                              headers=self._headers())
        _check(resp, "elevenlabs voice captcha")
        kind = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
        if kind.startswith("image/"):
            return {"kind": "image",
                    "image": f"data:{kind};base64,{base64.b64encode(resp.content).decode()}"}
        try:
            data = resp.json() or {}
        except ValueError:
            data = {}
        for key in ("text", "captcha", "phrase", "sentence"):        # UNVERIFIED key names
            if isinstance(data.get(key), str) and data[key].strip():
                return {"kind": "text", "text": data[key].strip()[:500]}
        return {"kind": "unavailable"}

    def pvc_verify_captcha(self, voice_id: str, *, filename: str, content: bytes,
                           content_type: str) -> None:
        """POST /v1/voices/pvc/{id}/captcha, multipart `recording`."""
        with _transport(), _http() as client:
            resp = client.post(f"{ELEVEN_BASE}/v1/voices/pvc/{voice_id}/captcha",
                               files=[("recording", (filename, content, content_type))],
                               headers=self._headers())
        _check(resp, "elevenlabs verify voice captcha")

    def pvc_train(self, voice_id: str, model_id: str) -> None:
        """POST /v1/voices/pvc/{id}/train {"model_id"}."""
        with _transport(), _http() as client:
            resp = client.post(f"{ELEVEN_BASE}/v1/voices/pvc/{voice_id}/train",
                               json={"model_id": model_id}, headers=self._headers())
        _check(resp, "elevenlabs train voice clone")

    def get_voice(self, voice_id: str) -> dict:
        """GET /v1/voices/{id}: fine_tuning.state {model: not_started |
        queued | fine_tuning | fine_tuned | failed | delayed} and
        voice_verification {requires_verification, is_verified, ...}."""
        with _transport(), _http() as client:
            resp = client.get(f"{ELEVEN_BASE}/v1/voices/{voice_id}", headers=self._headers())
        _check(resp, "elevenlabs get voice")
        data = resp.json()
        return data if isinstance(data, dict) else {}

    def delete_voice(self, voice_id: str) -> None:
        """DELETE /v1/voices/{id}. A 404 means it is already gone -- success."""
        with _transport(), _http() as client:
            resp = client.delete(f"{ELEVEN_BASE}/v1/voices/{voice_id}", headers=self._headers())
        if resp.status_code == 404:
            return
        _check(resp, "elevenlabs delete voice")

    def create_call(self, *, to_number: str, customer_name: str | None, system_prompt: str,
                    first_message: str, voicemail_message: str | None, server_url: str,
                    server_secret: str | None, metadata: dict, voice_id: str | None = None,
                    voice_is_clone: bool = False, transfer_number: str | None = None) -> dict:
        """POST /v1/convai/twilio/outbound-call (ElevenLabs API reference,
        checked 2026-10-28): agent_id, agent_phone_number_id, to_number
        required; the answer is {success, message, conversation_id, callSid}
        and either id may be null.

        A 200 with success false, or without a conversation_id, is a call
        that did not start: the post-call webhook is matched by
        conversation_id, so a callSid stand-in would never be matched and the
        transcript (and any opt-out spoken on it) would be dropped.

        Voice twin: `voice_id` (the owner's clone) is sent as a TTS override.
        # UNVERIFIED against docs: conversation_config_override.tts.voice_id
        (the agent must also allow the override in its security settings).
        `transfer_number` is not used: live takeover is Vapi-only."""
        if not self.can_call:
            raise VoiceProviderError("ElevenLabs agent_id / agent_phone_number_id not set")
        body = {
            "agent_id": self.agent_id,
            "agent_phone_number_id": self.agent_phone_number_id,
            "to_number": to_number,
            "conversation_initiation_client_data": {
                "conversation_config_override": {
                    "agent": {"first_message": first_message,
                              "prompt": {"prompt": system_prompt}},
                    **({"tts": {"voice_id": voice_id}} if voice_id else {})},
                "dynamic_variables": {k: str(v) for k, v in metadata.items()},
            },
        }
        with _http() as client:
            resp = client.post(f"{ELEVEN_BASE}/v1/convai/twilio/outbound-call", json=body,
                               headers={"xi-api-key": self.api_key})
        _check(resp, "elevenlabs outbound call")
        data = resp.json() or {}
        if not data.get("success") or not data.get("conversation_id"):
            logger.warning("elevenlabs outbound call not started (success=%s, "
                           "conversation_id present=%s)", data.get("success"),
                           bool(data.get("conversation_id")))
            # A callSid means Twilio may already be ringing the person: never
            # dial again automatically. Without one nothing was placed, and
            # the normal retry applies.
            raise VoiceProviderError("ElevenLabs did not start the call",
                                     permanent=bool(data.get("callSid")) or None)
        return {"id": data["conversation_id"], "status": "queued"}


def get_clients(db, user_id=None) -> tuple[VapiClient | None, ElevenLabsClient | None]:
    """(vapi, elevenlabs), each None when not configured. Tests monkeypatch."""
    from app.config import settings  # noqa: PLC0415
    from app.services import credentials  # noqa: PLC0415

    def secret(provider, key):
        return credentials.get_secret(db, provider, key, user_id=user_id)

    eleven = None
    if secret("elevenlabs", "api_key"):
        eleven = ElevenLabsClient(secret("elevenlabs", "api_key"), secret("elevenlabs", "voice_id"),
                                  secret("elevenlabs", "agent_id"),
                                  secret("elevenlabs", "agent_phone_number_id"))
    vapi = None
    if secret("vapi", "api_key") and secret("vapi", "phone_number_id"):
        vapi = VapiClient(secret("vapi", "api_key"), secret("vapi", "phone_number_id"),
                          voice_id=eleven.voice_id if eleven else None,
                          model=settings.anthropic_model)
    return vapi, eleven
