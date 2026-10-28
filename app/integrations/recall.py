"""Recall.ai — the recording provider behind meeting transcripts (Feature 3).

WHY RECALL AND NOT A BROWSER EXTENSION
One integration covers Google Meet, Zoom and Teams: a bot joins the call and
Recall produces the transcript. An extension would have to be built, published
to a store, installed by every user and kept working against three meeting UIs
that change without notice. Here the integration surface is one API call to
start a bot and one webhook when the transcript is ready.

WHAT IS VERIFIED AND WHAT IS NOT
Checked against docs.recall.ai on 2026-09-13, never called live (there are no
Recall credentials in any environment yet):
  * webhook signing -- headers webhook-id / webhook-timestamp / webhook-signature
    (svix-* on legacy endpoints), a `whsec_`-prefixed base64 secret, HMAC-SHA256
    over "<id>.<timestamp>.<raw body>", base64 digest, header "v1,<sig>" with
    space-separated multiples ("authenticating-requests-from-recallai");
  * transcript.done / transcript.failed carry data.bot.id, data.transcript.id and
    data.bot.metadata ("async-transcription");
  * the downloaded transcript is a list of {participant: {name, ...}, words:
    [{text, ...}]}.
Everything else carries `# TODO: verify` per the convention in apollo.py.

Recall's docs name no replay tolerance for webhook-timestamp. Five minutes is
the Svix default the signing scheme comes from, and is what this module uses.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
import time
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from app.integrations.plumbing import BaseHttpAdapter

logger = logging.getLogger(__name__)

PROVIDER = "recall"
WEBHOOK_TOLERANCE_SECONDS = 300
# A downloaded transcript larger than this is not a sales call. Refusing it
# keeps one bad file from being read into a worker's memory whole.
MAX_DOWNLOAD_BYTES = 5_000_000
# A region is interpolated into a hostname. Anything but a plain region slug is
# refused, so a mistyped (or malicious) admin value cannot point API calls --
# carrying the API key -- at some other host.
_REGION = re.compile(r"^[a-z0-9-]{2,40}$")


class RecallNotConfigured(ValueError):
    pass


# --------------------------------------------------------------------------
# Webhook verification (Svix scheme)
# --------------------------------------------------------------------------


def _header(headers, *names: str) -> str | None:
    for name in names:
        value = headers.get(name)
        if value:
            return value
    return None


def verify_webhook(raw: bytes, headers, secret: str | None,
                   now: float | None = None) -> bool:
    """True only for a delivery signed with `secret`, inside the tolerance."""
    if not secret:
        return False
    msg_id = _header(headers, "webhook-id", "svix-id")
    timestamp = _header(headers, "webhook-timestamp", "svix-timestamp")
    signatures = _header(headers, "webhook-signature", "svix-signature")
    if not (msg_id and timestamp and signatures):
        return False
    try:
        sent_at = int(timestamp)
    except ValueError:
        return False
    if abs((now if now is not None else time.time()) - sent_at) > WEBHOOK_TOLERANCE_SECONDS:
        return False
    try:
        key = base64.b64decode(secret.strip().removeprefix("whsec_"))
    except (ValueError, TypeError):
        return False
    signed = f"{msg_id}.{timestamp}.".encode() + raw
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    for candidate in signatures.split():
        version, _, value = candidate.partition(",")
        if version == "v1" and hmac.compare_digest(value, expected):
            return True
    return False


# --------------------------------------------------------------------------
# Transcript formatting
# --------------------------------------------------------------------------


def format_transcript(segments) -> str:
    """Recall's transcript JSON as "Speaker: words" lines.

    Consecutive segments from the same participant are joined, so a speaker
    who paused mid-thought reads as one turn -- the shape the summary prompt
    and the transcript viewer both expect from the chunk endpoint.
    """
    lines: list[tuple[str, str]] = []
    for segment in segments if isinstance(segments, list) else []:
        if not isinstance(segment, dict):
            continue
        participant = segment.get("participant") or {}
        name = str(participant.get("name") or "Unknown speaker").strip()[:120]
        words = " ".join(str(w.get("text") or "").strip()
                         for w in segment.get("words") or [] if isinstance(w, dict))
        words = " ".join(words.split())
        if not words:
            continue
        if lines and lines[-1][0] == name:
            lines[-1] = (name, f"{lines[-1][1]} {words}")
        else:
            lines.append((name, words))
    return "\n".join(f"{name}: {text}" for name, text in lines)


# --------------------------------------------------------------------------
# API client
# --------------------------------------------------------------------------


class RecallAdapter(BaseHttpAdapter):
    provider = PROVIDER

    def __init__(self, api_key: str, region: str, **kwargs):
        region = (region or "").strip().lower()
        if not api_key or not _REGION.match(region):
            raise RecallNotConfigured("recall api_key and a valid region are required")
        self.api_key = api_key
        # Set before super().__init__, which builds the httpx client from it.
        self.base_url = f"https://{region}.recall.ai"
        super().__init__(**kwargs)

    def _auth(self) -> dict:
        # "All requests must be authenticated via token in the HTTP
        # Authorization header ... Authorization: Token {YOUR_API_KEY}"
        # (Recall API reference, checked 2026-10-28).
        return {"headers": {"Authorization": f"Token {self.api_key}"}}

    def create_bot(self, meeting_url: str, *, join_at: datetime | None,
                   metadata: dict[str, str], bot_name: str = "Vectorae Notetaker") -> dict:
        body: dict = {
            "meeting_url": meeting_url,
            "bot_name": bot_name,
            "metadata": metadata,
            # Only the end-of-call transcript is used, so ask for Recall's
            # accuracy mode rather than leave the mode to its default
            # ("prioritize_accuracy" / "prioritize_low_latency", Recall
            # transcription docs, checked 2026-10-28).
            "recording_config": {"transcript": {"provider": {
                "recallai_streaming": {"mode": "prioritize_accuracy"}}}},
        }
        if join_at is not None and join_at > datetime.now(timezone.utc):
            body["join_at"] = join_at.astimezone(timezone.utc).isoformat()
        return self.call("POST", "/api/v1/bot/", json_body=body)  # TODO: verify

    def retrieve_bot(self, bot_id: str) -> dict:
        return self.call("GET", f"/api/v1/bot/{bot_id}/")

    def retrieve_transcript(self, transcript_id: str) -> dict:
        return self.call("GET", f"/api/v1/transcript/{transcript_id}/")

    def _download(self, url: str):
        """The transcript file. A pre-signed URL: no API key is sent with it."""
        if not url.startswith("https://"):
            raise ValueError("refusing a non-HTTPS transcript download URL")
        with httpx.Client(timeout=30.0, follow_redirects=False) as client:
            resp = client.get(url)
            resp.raise_for_status()
            if len(resp.content) > MAX_DOWNLOAD_BYTES:
                raise ValueError("transcript download exceeds the size limit")
            return resp.json()

    def transcript_download_url(self, bot_id: str, transcript_id: str | None) -> str | None:
        if transcript_id:
            url = (self.retrieve_transcript(transcript_id).get("data") or {}).get("download_url")
            if url:
                return url
        # TODO: verify -- documented in the quickstart as the bot-level shortcut.
        for recording in self.retrieve_bot(bot_id).get("recordings") or []:
            shortcut = ((recording.get("media_shortcuts") or {}).get("transcript") or {})
            url = (shortcut.get("data") or {}).get("download_url")
            if url:
                return url
        return None

    def fetch_transcript_text(self, bot_id: str, transcript_id: str | None = None) -> str:
        url = self.transcript_download_url(bot_id, transcript_id)
        return format_transcript(self._download(url)) if url else ""


def client_for(db: Session) -> RecallAdapter:
    """Raises credentials.IntegrationNotConfigured when an admin has not set
    Recall up; RecallNotConfigured when the region is malformed."""
    from app.services import credentials  # noqa: PLC0415

    return RecallAdapter(
        api_key=credentials.require_secret(db, PROVIDER, "api_key"),
        region=credentials.require_secret(db, PROVIDER, "region"),
    )
