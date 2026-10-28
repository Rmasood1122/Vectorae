"""Zoom adapter — creates a meeting and returns its join_url.

AUTH: SERVER-TO-SERVER OAUTH, NOT THE USER-CONSENT FLOW
Zoom offers both. This uses server-to-server (grant_type=account_credentials,
Basic-authenticated with the app's client id/secret plus the account id),
because the alternative would put a second per-user OAuth dance in front of
booking a call. The Victorae operator authorises once, in the Zoom
marketplace app, and every meeting is created under that account.

The trade-off is stated rather than hidden: meetings are hosted by the
configured Zoom account (or the configured `host_user` on it), not by each
individual Victorae user. Right for solo founders; wrong for a team plan.

CREDENTIALS (Feature 7)
Read from Admin > Integrations > Zoom (TokenStore, encrypted) first, falling
back to the ZOOM_OAUTH_CLIENT_ID / ZOOM_OAUTH_CLIENT_SECRET / ZOOM_ACCOUNT_ID
environment variables this adapter originally shipped with, so an existing
deployment keeps working and a new one follows the codebase rule that API
keys live in the encrypted store, not the environment.

VERIFIED AGAINST CURRENT DOCS (2026-09-13, re-read 2026-10-27), NEVER CALLED LIVE
developers.zoom.us/docs/internal-apps/s2s-oauth: POST https://zoom.us/oauth/token
with grant_type=account_credentials and account_id in the form body, Basic
auth of client_id:client_secret, one-hour tokens. developers.zoom.us/docs/api/
meetings: POST /users/{userId}/meetings; type 2 = scheduled; duration 1-1440;
agenda up to 2,000 characters; settings.join_before_host / waiting_room
(a waiting room disables join-before-host). userId is "the user's user ID or
email address. For user-level apps, pass the `me` value" -- a Server-to-Server
app is account-level, so there is NO default host: `host_user` (an email or
user id) must be configured, and an unset one refuses with ZoomNotConfigured
(founder choice, 2026-10-27).
Live verification needs credentials: scripts/verify_meeting_platforms.py.

THE TOKEN IS CACHED IN REDIS, keyed per client id, so rotating credentials
invalidates it rather than serving a token minted with the old secret.
"""

from __future__ import annotations

import base64
import logging
from datetime import timezone
from urllib.parse import quote

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.core.exceptions import ClientHunterError
from app.integrations.plumbing import BaseHttpAdapter

logger = logging.getLogger(__name__)

ZOOM_API_BASE = "https://api.zoom.us/v2"
ZOOM_TOKEN_ENDPOINT = "https://zoom.us/oauth/token"
PROVIDER = "zoom"
CONFIGURE_ACTION = "configure_zoom"

_SCHEDULED = 2
_DURATION_MAX = 1440
_TOKEN_CACHE_SECONDS = 3000  # one-hour tokens, expired early


class ZoomNotConfigured(ClientHunterError):
    """The deployment has no usable Zoom credentials. `action` tells the
    client to point an admin at Admin > Integrations."""

    def __init__(self, message: str):
        super().__init__(message)
        self.action = CONFIGURE_ACTION


def zoom_credentials(db: Session | None) -> dict:
    """client_id / client_secret / account_id / host_user, admin store first."""
    stored: dict = {}
    if db is not None:
        from app.services import credentials  # noqa: PLC0415

        for key in ("client_id", "client_secret", "account_id", "host_user"):
            value = credentials.get_secret(db, PROVIDER, key)
            if value:
                stored[key] = value
    return {
        "client_id": stored.get("client_id") or settings.zoom_oauth_client_id,
        "client_secret": stored.get("client_secret") or settings.zoom_oauth_client_secret,
        "account_id": stored.get("account_id") or settings.zoom_account_id,
        "host_user": stored.get("host_user") or "",
    }


class ZoomAdapter(BaseHttpAdapter):
    provider = PROVIDER
    base_url = ZOOM_API_BASE

    def __init__(self, credentials: dict | None = None,
                 token_http: httpx.Client | None = None, **kwargs):
        super().__init__(**kwargs)
        self.creds = credentials if credentials is not None else zoom_credentials(None)
        # Separate client: the token endpoint is on a different host than the
        # API base, and BaseHttpAdapter's client is pinned to base_url.
        self._token_http = token_http or httpx.Client(timeout=30.0)

    def configured(self) -> bool:
        return bool(self.creds.get("client_id") and self.creds.get("client_secret")
                    and self.creds.get("account_id"))

    def _cache_key(self) -> str:
        return f"zoom:token:{self.creds.get('client_id')}"

    def _access_token(self) -> str:
        if not self.configured():
            raise ZoomNotConfigured(
                "Zoom is not configured. An admin can add the Server-to-Server "
                "OAuth app's client_id, client_secret and account_id under "
                "Admin > Integrations > Zoom, or create the meeting with a "
                "custom link instead."
            )
        try:
            cached = self.redis.get(self._cache_key())
            if cached:
                return cached
        except Exception as exc:  # noqa: BLE001 — Redis down must not block a call
            logger.warning("zoom token cache read failed: %s", exc)

        basic = base64.b64encode(
            f"{self.creds['client_id']}:{self.creds['client_secret']}".encode()
        ).decode()
        resp = self._token_http.post(
            ZOOM_TOKEN_ENDPOINT,
            headers={"Authorization": f"Basic {basic}"},
            data={"grant_type": "account_credentials",
                  "account_id": self.creds["account_id"]},
        )
        if resp.status_code >= 400:
            # Zoom's response body goes to the log only: ZoomNotConfigured's
            # message reaches the user as the meeting's platform_error.
            logger.warning("zoom token request refused: HTTP %s %s",
                           resp.status_code, resp.text[:500])
            raise ZoomNotConfigured(
                f"Zoom rejected the credentials (HTTP {resp.status_code}). Check the "
                "Zoom account ID, client ID and client secret under Admin > Integrations."
            )
        token = resp.json().get("access_token") or ""
        if not token:
            raise ZoomNotConfigured("Zoom returned no access_token")
        try:
            self.redis.setex(self._cache_key(), _TOKEN_CACHE_SECONDS, token)
        except Exception as exc:  # noqa: BLE001
            logger.warning("zoom token cache write failed: %s", exc)
        return token

    def _auth(self) -> dict:
        return {"headers": {"Authorization": f"Bearer {self._access_token()}"}}

    def _host(self) -> str:
        host = str(self.creds.get("host_user") or "").strip()
        if not host:
            raise ZoomNotConfigured(
                "Zoom has no host user. An admin sets `host_user` (the email or "
                "user id meetings are created under) under Admin > Integrations > "
                "Zoom; a Server-to-Server app has no \"me\".")
        return quote(host, safe="@.")

    def create_meeting(self, *, topic: str, start_at, duration_minutes: int,
                       agenda: str | None = None) -> dict:
        """Schedule a meeting on the configured host. Returns the resource.

        `start_time` is UTC with an explicit Z; a local time without a
        timezone field is how a meeting lands in the wrong hour.
        """
        body = {
            "topic": topic[:200],
            "type": _SCHEDULED,
            "start_time": start_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "duration": min(_DURATION_MAX, max(1, int(duration_minutes))),
            "timezone": "UTC",
            "agenda": (agenda or "")[:2000] or None,
            "settings": {
                # A prospect on a "waiting for host" screen is a lost call.
                "join_before_host": True,
                "waiting_room": False,  # a waiting room disables join-before-host
            },
        }
        return self.call("POST", f"/users/{self._host()}/meetings", json_body=body)

    def delete_meeting(self, meeting_id: str) -> None:
        self.call("DELETE", f"/meetings/{quote(str(meeting_id), safe='')}")

    def health_check(self) -> bool:
        if self.breaker.is_open() or not self.configured():
            return False
        try:
            self.call("GET", f"/users/{self._host()}")
            return True
        except Exception:  # noqa: BLE001
            return False


def create_zoom_meeting(db: Session | None = None, *, topic: str, start_at,
                        duration_minutes: int, agenda: str | None = None) -> dict:
    """{"join_url", "external_event_id"} for a new Zoom meeting."""
    data = ZoomAdapter(credentials=zoom_credentials(db)).create_meeting(
        topic=topic, start_at=start_at, duration_minutes=duration_minutes, agenda=agenda,
    )
    return {
        "join_url": data.get("join_url") or "",
        "external_event_id": str(data.get("id") or "")[:200],
    }
