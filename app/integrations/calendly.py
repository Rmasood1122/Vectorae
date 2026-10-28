"""Calendly adapter — booking links + webhook handling (M3 Chunk 5).

Auth: personal access token from CALENDLY_API_TOKEN, sent as a Bearer token
(the API takes OAuth and personal access tokens the same way).

Webhook signature: Calendly sends a `Calendly-Webhook-Signature` header of
the form "t=<timestamp>,v1=<hex hmac-sha256 of '<t>.<raw body>' with the
signing key>" (developer.calendly.com, webhook-signatures; their examples
use a 3-minute tolerance). A webhook subscription created without a signing
key cannot be verified, and its deliveries are refused.

Checked against developer.calendly.com on 2026-10-27;
tests/test_calendly_zoom_contract.py pins what was checked.
"""

import hashlib
import hmac
import logging
import time

from app.config import settings
from app.integrations.plumbing import BaseHttpAdapter

logger = logging.getLogger(__name__)

CALENDLY_API_BASE = "https://api.calendly.com"
SIGNATURE_HEADER = "Calendly-Webhook-Signature"
#: event_types pages (count up to 100; pagination.next_page_token).
_EVENT_TYPE_PAGE = 100
_EVENT_TYPE_PAGES_MAX = 10
#: How far AHEAD of our clock a delivery's timestamp may be (clock drift).
FUTURE_SKEW_SECONDS = 300


def _now() -> float:
    """Current Unix time. A function of its own so tests can pin the clock."""
    return time.time()


class CalendlyAdapter(BaseHttpAdapter):
    provider = "calendly"
    base_url = CALENDLY_API_BASE

    def __init__(self, api_token: str | None = None, **kwargs):
        super().__init__(**kwargs)
        self.api_token = api_token if api_token is not None else settings.calendly_api_token

    def _auth(self) -> dict:
        return {"headers": {"Authorization": f"Bearer {self.api_token}"}}

    def current_user(self) -> dict:
        # GET /users/me -> {"resource": {"uri", ...}}
        return self.call("GET", "/users/me").get("resource", {})

    def event_types(self) -> list[dict]:
        """The user's bookable event types (name + scheduling_url)."""
        user_uri = self.current_user().get("uri")
        if not user_uri:
            return []
        # {"collection": [...], "pagination": {"next_page_token"}}; the default
        # page is 20, so a user with more event types lost the rest.
        items: list[dict] = []
        params: dict = {"user": user_uri, "count": _EVENT_TYPE_PAGE}
        for _ in range(_EVENT_TYPE_PAGES_MAX):
            data = self.call("GET", "/event_types", params=params)
            items.extend(data.get("collection", []))
            token = (data.get("pagination") or {}).get("next_page_token")
            if not token:
                break
            params = {**params, "page_token": token}
        return [
            {
                "name": item.get("name"),
                "scheduling_url": item.get("scheduling_url"),
                "uri": item.get("uri"),
                "active": item.get("active"),
            }
            for item in items
        ]

    def health_check(self) -> bool:
        if self.breaker.is_open():
            return False
        try:
            self.call("GET", "/users/me")
            return True
        except Exception:
            return False


def verify_webhook_signature(raw_body: bytes, signature_header: str | None,
                             signing_key: str | None = None, *,
                             now: float | None = None,
                             tolerance_seconds: int | None = None) -> bool:
    """Constant-time verification of Calendly's t=...,v1=... signature, AND
    of the freshness of `t`.

    F-OPS-02: the signature covers "<t>.<body>" but `t` itself was never
    checked, so a captured delivery stayed valid for ever. A timestamp older
    than the tolerance (settings.calendly_webhook_tolerance_seconds) or more
    than FUTURE_SKEW_SECONDS ahead of our clock is refused. `now` and
    `tolerance_seconds` are injectable for tests.
    """
    signing_key = signing_key if signing_key is not None else settings.calendly_webhook_signing_key
    if not signing_key or not signature_header:
        return False
    try:
        parts = dict(p.split("=", 1) for p in signature_header.split(","))
        timestamp, given = parts["t"], parts["v1"]
    except (ValueError, KeyError):
        return False
    # Signed over bytes: a body that is not UTF-8 is refused, not a 500.
    expected = hmac.new(
        signing_key.encode(),
        f"{timestamp}.".encode() + raw_body,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, given):
        return False

    try:
        sent_at = int(timestamp)
    except (TypeError, ValueError):
        return False
    current = _now() if now is None else now
    tolerance = (settings.calendly_webhook_tolerance_seconds
                 if tolerance_seconds is None else tolerance_seconds)
    if current - sent_at > tolerance:
        logger.warning("calendly webhook refused: timestamp %ss old (tolerance %ss)",
                       int(current - sent_at), tolerance)
        return False
    if sent_at - current > FUTURE_SKEW_SECONDS:
        logger.warning("calendly webhook refused: timestamp %ss in the future",
                       int(sent_at - current))
        return False
    return True


def extract_event_id(payload: dict, raw_body: bytes) -> str:
    """Stable id for webhook dedupe: Calendly retries deliveries.

    PREFIXED WITH THE EVENT TYPE. The key used to be the invitee URI alone,
    and Calendly sends the SAME invitee URI on `invitee.created` and on the
    later `invitee.canceled` for that booking -- so every real cancellation
    hit the (provider, event_id) unique constraint, was acknowledged as a
    duplicate, and never reached the handler: the lead stayed meeting_booked
    and (since Feature Group 7) its reminders kept firing for a call that was
    not happening. The existing tests missed it by giving the cancellation a
    different invitee URI than the booking. A retry of the same delivery still
    maps to the same key, which is all dedupe needs.

    Deploy note: a booking delivered before this change and REDELIVERED after
    it would be processed once more. Calendly retries within hours, the
    booked-path writes are idempotent for the brief (unique per scheduled
    event), and the cost is at most one duplicate BOOKED outcome row.
    """
    event_type = str(payload.get("event") or "").strip()
    for candidate in (
        payload.get("event_id"),
        (payload.get("payload") or {}).get("uri"),
        payload.get("created_at"),
    ):
        if candidate:
            key = f"{event_type}:{candidate}" if event_type else str(candidate)
            return key[:200]
    return hashlib.sha256(raw_body).hexdigest()


# --------------------------------------------------------------------------
# Tenant tagging — which account does an inbound booking belong to?
# --------------------------------------------------------------------------
#
# A Calendly delivery carries no account identity of its own: one deployment
# has ONE webhook subscription and ONE signing key, and the payload describes
# the invitee, not which of our users owns the campaign. The handler used to
# resolve the lead with `select(Lead).where(Lead.email == email)` over the
# whole table and take the first row - so when two customers prospected the
# same person (routine in B2B) a booking could land on the wrong tenant's
# lead, flip its status and stop their sequences. Same bug class as the
# inbound-reply matching fixed in session update 4.
#
# The fix is to put the identity INTO the link we hand the lead, and read it
# back off the webhook. Calendly forwards the standard UTM query parameters on
# a booking link into `payload.tracking` on invitee.created / invitee.canceled,
# so the tenant id makes the round trip with no Calendly plan requirement (a
# per-tenant webhook subscription needs Enterprise) and no reliance on email.
#
# FIELD CHOICE: utm_content. `booking_url` is a URL the USER pastes in when
# they create a sequence and may already carry their own utm_source /
# utm_campaign for their own attribution; utm_content is the least likely of
# the set to be already in use, and overwriting a customer's utm_source to
# smuggle an internal id would silently corrupt their reporting. Anything
# already on their URL is preserved.

TENANT_TRACKING_PARAM = "utm_content"


def tag_booking_url(booking_url: str | None, user_id) -> str | None:
    """Return `booking_url` with the owning tenant's id attached.

    Idempotent, and never raises: a malformed URL is returned untouched
    rather than costing the lead their scheduling link.
    """
    if not booking_url or user_id is None:
        return booking_url
    try:
        from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

        parts = urlparse(booking_url)
        params = dict(parse_qsl(parts.query, keep_blank_values=True))
        params[TENANT_TRACKING_PARAM] = str(user_id)
        return urlunparse(parts._replace(query=urlencode(params)))
    except Exception:  # noqa: BLE001 - a link is better than no link
        logger.warning("could not tag booking url %r - sending it untagged",
                       booking_url)
        return booking_url


def tenant_id_from_payload(payload: dict):
    """The tenant id a booking came back with, or None.

    Returns None for every "we cannot tell" case - missing tracking block,
    absent parameter, or a value that is not a UUID (an organic visitor who
    found the link elsewhere, or a link generated before tagging existed).
    The caller must treat None as "do not match", never as "match anything".
    """
    import uuid as _uuid

    tracking = ((payload or {}).get("payload") or {}).get("tracking") or {}
    raw = (tracking.get(TENANT_TRACKING_PARAM) or "").strip()
    if not raw:
        return None
    try:
        return _uuid.UUID(raw)
    except (ValueError, AttributeError, TypeError):
        logger.info("calendly booking carried an unparseable tenant id %r", raw[:80])
        return None
