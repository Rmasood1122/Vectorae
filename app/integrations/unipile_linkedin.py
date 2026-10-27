"""Unipile — the LinkedIn API behind Feature Group 5's outreach channel.

One small client for every LinkedIn call the channel makes:
  profile(identifier)          provider id, premium flag, network distance
  invite(provider_id, note)    a connection request (note <= 300 chars)
  start_chat(ids, text, ...)   a first message / InMail (creates a chat)
  send_in_chat(chat_id, text)  a message in an existing conversation
  hosted_link(...)             Unipile's hosted-auth page for connecting an
                               account (the user logs in on Unipile, never here)
  account(account_id)          display name, premium / Sales Navigator
  inmail_balance(account_id)   remaining InMail credits

Auth is the X-API-KEY header against https://<dsn>/api/v1, both SYSTEM
credentials (Admin > Integrations > Unipile). Paths, fields and error types
were checked against Unipile's public docs on 2026-10-27
(developer.unipile.com: api-usage, provider-limits-and-restrictions, the
users/chats/hosted/accounts/linkedin references); tests/test_unipile_contract.py
pins what was checked.

Errors are raised as UnipileError with the HTTP status and Unipile's error
`type` (e.g. "errors/cannot_resend_yet"). A provider limit or a 429 is
retried later; a disconnected or restricted account is the account's fault,
not the lead's; any other 4xx is permanent (bad profile, no InMail credits).
"""

from __future__ import annotations

import json as _json
import logging

import httpx

logger = logging.getLogger(__name__)

CONNECTION_NOTE_MAX = 300


# provider-limits-and-restrictions / the invite reference: LinkedIn limits
# ("You have reached a temporary provider limit. Please try again later.").
RETRY_LATER_TYPES = frozenset({
    "errors/cannot_resend_yet", "errors/cannot_resend_within_24hrs",
    "errors/already_invited_recently", "errors/limit_exceeded",
    "errors/connection_limit_reached",
})
# The account, not the lead: reconnect, or wait out a restriction. Credential
# errors are matched by their "...credentials" suffix.
ACCOUNT_FAULT_TYPES = frozenset({"errors/disconnected_account", "errors/account_restricted"})


class UnipileError(Exception):
    def __init__(self, message: str, status: int | None = None,
                 error_type: str | None = None):
        super().__init__(message)
        self.status = status
        self.error_type = error_type

    @property
    def retry_later(self) -> bool:
        return self.status == 429 or self.error_type in RETRY_LATER_TYPES

    @property
    def account_fault(self) -> bool:
        return (self.error_type in ACCOUNT_FAULT_TYPES
                or (self.error_type or "").endswith("credentials"))

    @property
    def permanent(self) -> bool:
        return (self.status is not None and 400 <= self.status < 500
                and not self.retry_later and not self.account_fault)


def _http() -> httpx.Client:
    """Factory tests monkeypatch."""
    return httpx.Client(timeout=30.0)


class UnipileClient:
    def __init__(self, api_key: str, dsn: str):
        self.api_key = api_key
        self.base = f"https://{dsn.strip().removeprefix('https://').rstrip('/')}/api/v1"
        self.dsn_url = f"https://{dsn.strip().removeprefix('https://').rstrip('/')}"

    def _request(self, method: str, path: str, *, params: dict | None = None,
                 json: dict | None = None) -> dict:
        with _http() as client:
            resp = client.request(method, f"{self.base}{path}", params=params, json=json,
                                  headers={"X-API-KEY": self.api_key,
                                           "accept": "application/json"})
        if resp.status_code >= 400:
            # {title, detail, type: "errors/...", status} (api-usage).
            try:
                error_type = (_json.loads(resp.text) or {}).get("type")
            except (ValueError, AttributeError):
                error_type = None
            raise UnipileError(f"{method} {path}: HTTP {resp.status_code} {resp.text[:200]}",
                               status=resp.status_code, error_type=error_type)
        try:
            return resp.json() or {}
        except ValueError:
            return {}

    # ---- profiles --------------------------------------------------------
    def profile(self, identifier: str, account_id: str) -> dict:
        """GET /users/{identifier}?account_id=. network_distance is one of
        FIRST_DEGREE, SECOND_DEGREE, THIRD_DEGREE, OUT_OF_NETWORK; invitation
        is {type: SENT|RECEIVED, status}. Only an invitation WE sent that is
        still pending blocks another one."""
        data = self._request("GET", f"/users/{identifier}", params={"account_id": account_id})
        invitation = data.get("invitation") or {}
        return {
            "provider_id": data.get("provider_id") or identifier,
            "is_premium": bool(data.get("is_premium")),
            "network_distance": data.get("network_distance"),
            "is_connected": data.get("network_distance") == "FIRST_DEGREE",
            "invitation_pending": (invitation.get("type") == "SENT"
                                   and invitation.get("status") == "PENDING"),
            "name": " ".join(filter(None, [data.get("first_name"), data.get("last_name")])),
        }

    # ---- sending ---------------------------------------------------------
    def invite(self, provider_id: str, account_id: str, note: str | None) -> dict:
        """POST /users/invite {provider_id, account_id, message <= 300 chars};
        returns {invitation_id}. LinkedIn itself allows a free account about 5
        notes a month of up to 200 characters."""
        body = {"provider_id": provider_id, "account_id": account_id}
        if note:
            body["message"] = note[:CONNECTION_NOTE_MAX]
        return self._request("POST", "/users/invite", json=body)

    def start_chat(self, provider_id: str, account_id: str, text: str, *,
                   inmail: bool = False, subject: str | None = None) -> dict:
        """First message or InMail: POST /chats, returns {chat_id,
        message_id}. Unipile's reference shows multipart form fields
        (linkedin[api]=classic, linkedin[inmail]=true) and states the API
        accepts JSON bodies too; this sends JSON."""
        body: dict = {"account_id": account_id, "attendees_ids": [provider_id], "text": text}
        if inmail:
            body["linkedin"] = {"api": "classic", "inmail": True}
            if subject:
                body["subject"] = subject[:200]
        return self._request("POST", "/chats", json=body)

    def send_in_chat(self, chat_id: str, text: str) -> dict:
        """POST /chats/{id}/messages {text}; returns {message_id}."""
        return self._request("POST", f"/chats/{chat_id}/messages", json={"text": text})

    def chat_messages(self, chat_id: str, limit: int = 20) -> list[dict]:
        data = self._request("GET", f"/chats/{chat_id}/messages", params={"limit": limit})
        return data.get("items") or []

    # ---- accounts --------------------------------------------------------
    def hosted_link(self, *, notify_url: str, name: str, expires_on: str,
                    success_url: str | None = None) -> str:
        """Unipile's hosted-auth page. The user signs in to LinkedIn THERE;
        Victorae never sees their LinkedIn password. POST
        /hosted/accounts/link; expiresOn is YYYY-MM-DDTHH:MM:SS.sssZ."""
        body = {"type": "create", "providers": ["LINKEDIN"], "api_url": self.dsn_url,
                "expiresOn": expires_on, "notify_url": notify_url, "name": name}
        if success_url:
            body["success_redirect_url"] = success_url
        data = self._request("POST", "/hosted/accounts/link", json=body)
        if not data.get("url"):
            raise UnipileError("hosted auth returned no url")
        return data["url"]

    def delete_account(self, account_id: str) -> None:
        """Disconnect a LinkedIn account: Unipile drops the stored session.
        Used when the Victorae account that connected it is erased
        (app/services/account_erasure.py). DELETE /accounts/{id} also revokes
        the LinkedIn session (provider_session_revoke defaults to true)."""
        self._request("DELETE", f"/accounts/{account_id}")

    def account(self, account_id: str) -> dict:
        """GET /accounts/{id}; premium is in connection_params.im
        (premiumId, premiumFeatures)."""
        data = self._request("GET", f"/accounts/{account_id}")
        params = ((data.get("connection_params") or {}).get("im") or {})
        premium = bool(params.get("premiumId") or params.get("premiumFeatures"))
        return {"id": data.get("id") or account_id, "name": data.get("name"),
                "type": data.get("type"), "has_premium": premium,
                "profile_url": params.get("publicIdentifier")
                and f"https://www.linkedin.com/in/{params['publicIdentifier']}"}

    def inmail_balance(self, account_id: str) -> int | None:
        """GET /linkedin/inmail_balance -> {premium, recruiter,
        sales_navigator}, each a number or null. A real 0 is a balance of 0,
        not "unknown"."""
        try:
            data = self._request("GET", "/linkedin/inmail_balance",
                                 params={"account_id": account_id})
        except UnipileError:
            return None
        value = next((data[k] for k in ("premium", "sales_navigator", "recruiter")
                      if data.get(k) is not None), None)
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def health(self) -> bool:
        try:
            self._request("GET", "/accounts", params={"limit": 1})
            return True
        except Exception:  # noqa: BLE001
            return False


def get_client(db, user_id=None) -> UnipileClient | None:
    """A configured client, or None. Tests monkeypatch this."""
    from app.services import credentials  # noqa: PLC0415

    key = credentials.get_secret(db, "unipile", "api_key", user_id=user_id)
    dsn = credentials.get_secret(db, "unipile", "dsn", user_id=user_id)
    return UnipileClient(key, dsn) if key and dsn else None
