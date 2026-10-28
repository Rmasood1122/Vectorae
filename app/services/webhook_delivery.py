"""Outbound webhooks — Zapier, Make, or any HTTPS endpoint (Feature Group 4).

REBUILT, BECAUSE THE M8-C5 VERSION NEVER DELIVERED ANYTHING
It was raw SQL written for a schema that does not exist: `:event =
ANY(event_types)` on a JSON column (an array operator -- a syntax error on
PostgreSQL, so every event matched zero targets), an INSERT naming a
`max_retries` column the table never had, UPDATEs writing `last_response_code`
and `last_attempt_at` (the model has `last_status_code`; the latter did not
exist until migration 0029), and the targets API bound a Python list into the
JSON column through `text()`. The event hub (event_bus._webhooks) swallowed
the resulting errors by design, so nothing ever surfaced. This module is now
ORM throughout and runs the same on SQLite and PostgreSQL.

WHAT A RECEIVER GETS
POST, JSON body:
    {"id": "<delivery id>", "event": "reply_received",
     "occurred_at": "2026-09-11T10:00:00+00:00", "data": {...}}
Headers:
    X-LeadPilot-Event:      the event name
    X-LeadPilot-Delivery:   the delivery id (use it to de-duplicate retries)
    X-LeadPilot-Signature:  t=<unix ts>,v1=<hex HMAC-SHA256(secret, "<ts>.<body>")>
    X-ClientHunter-Signature: sha256=<hex HMAC-SHA256(secret, body)>  (legacy)
Verify v1 and reject timestamps older than five minutes (verify_signature
below is the reference implementation).

RETRIES: the first attempt, then 5 retries backing off 30s, 2m, 10m, 30m and
2h -- 6 attempts in all (MAX_ATTEMPTS). Any 2xx is success. (It used to stop
after 5 attempts in all, so the 2-hour retry was never reached.)
A 410 Gone deactivates the target -- the Zapier REST-hook convention for "this
Zap was turned off" -- and stops retrying.

SSRF: targets must be https, may not be IP literals in private, loopback or
link-local space, and every delivery re-resolves the host and refuses a
non-public address. Redirects are not followed (a public URL that 302s to
169.254.169.254 would otherwise walk straight into the metadata service).
There is a residual DNS-rebinding window between the check and the connect;
closing it fully needs an egress proxy.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import secrets
import socket
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.crypto import decrypt, encrypt
from app.db.models import WebhookDelivery, WebhookTarget

logger = logging.getLogger(__name__)

EVENT_DESCRIPTIONS: dict[str, str] = {
    "meeting_booked": "A lead booked a meeting (Calendly or the Vectorae calendar).",
    "reply_received": "A lead replied on any channel, with the reply's classification.",
    "reply_interested": "A reply was classified as interested.",
    "lead_sourced": "A sourcing batch finished; carries its verified leads.",
    "campaign_paused": "A campaign was paused (bounce-rate auto-pause or by hand).",
    "strategy_mutated": "An idle campaign's strategy was rewritten.",
    "deal_won": "A deal was marked closed-won.",
    "call_completed": "An AI call ended and was analysed.",
    "objection_spike": "A campaign's objection rate spiked week over week.",
    # Alerts that always built a payload but could not be subscribed to.
    "domain_blacklisted": "A sending domain was newly listed on a blocklist "
                          "(its campaigns were paused).",
    "deliverability_warning": "A sending domain's health fell below the threshold, "
                              "or a mailbox's spam-rate brake engaged.",
    "mailbox_paused": "A sending mailbox was paused for poor deliverability health.",
    "send_needs_review": "A message was held in the send-review queue for approval.",
    "reengagement_due": "A 'not now' prospect's return date arrived.",
    "reply_draft_approved": "A suggested reply to a prospect was approved "
                            "(carries the approved draft text).",
}
# The four the spec names for Zapier / Make.
ZAPIER_EVENTS = ("meeting_booked", "reply_received", "lead_sourced", "campaign_paused")
ALIASES = {"lead_replied": "reply_received"}
LEGACY_EVENTS = frozenset({"strategy_complete", "variant_promoted"})
EVENT_TYPES = frozenset(EVENT_DESCRIPTIONS) | LEGACY_EVENTS | frozenset(ALIASES)

RETRY_DELAYS_SECONDS = [30, 120, 600, 1800, 7200]
#: The first attempt plus one retry per delay above.
MAX_ATTEMPTS = 1 + len(RETRY_DELAYS_SECONDS)
TIMEOUT_SECONDS = 10
SIGNATURE_TOLERANCE_SECONDS = 300
MAX_TARGETS_PER_USER = 25

_SAMPLE_LEAD = {
    "lead_id": "00000000-0000-4000-8000-000000000001",
    "strategy_id": "00000000-0000-4000-8000-000000000002",
    "full_name": "Sam Carter", "email": "sam@carterandco.example",
    "company": "Carter & Co", "title": "Founder", "phone": None,
    "linkedin_url": "https://www.linkedin.com/in/samcarter", "status": "replied",
}
SAMPLES: dict[str, dict] = {
    "meeting_booked": {"lead": {**_SAMPLE_LEAD, "status": "meeting_booked"},
                       "source": "calendly", "start_time": "2026-09-15T15:00:00+00:00"},
    "reply_received": {"lead": _SAMPLE_LEAD, "channel": "email",
                       "classification": "interested"},
    "reply_interested": {"lead": _SAMPLE_LEAD, "channel": "email",
                         "classification": "interested"},
    "lead_sourced": {"batch_id": "00000000-0000-4000-8000-000000000003",
                     "strategy_id": _SAMPLE_LEAD["strategy_id"],
                     "counts": {"verified": 1}, "leads": [{**_SAMPLE_LEAD,
                                                           "status": "verified"}]},
    "campaign_paused": {"strategy_id": _SAMPLE_LEAD["strategy_id"], "trigger": "bounce_rate",
                        "reason": "bounce rate 4.2% exceeded 3% (21/500)"},
    "strategy_mutated": {"strategy_id": _SAMPLE_LEAD["strategy_id"], "version_no": 2,
                         "trigger": "idle_no_replies"},
    "deal_won": {"deal_id": "00000000-0000-4000-8000-000000000004",
                 "lead_id": _SAMPLE_LEAD["lead_id"], "value": 5000.0, "currency": "USD"},
    "call_completed": {"call_id": "00000000-0000-4000-8000-000000000005",
                       "lead_id": _SAMPLE_LEAD["lead_id"], "outcome": "interested",
                       "duration_seconds": 184},
    "objection_spike": {"strategy_id": _SAMPLE_LEAD["strategy_id"],
                        "week_start": "2026-09-07", "objection_rate": 0.6,
                        "previous_objection_rate": 0.2, "replies": 10},
    # The payload shapes the emitting code builds (keep in step with it).
    "domain_blacklisted": {"domain": "carterandco.example",
                           "listed_on": ["zen.spamhaus.org"], "campaigns_paused": 2},
    "deliverability_warning": {"domain": "carterandco.example", "score": 62,
                               "reasons": ["DMARC record missing"]},
    "mailbox_paused": {"mailbox_ref": "00000000-0000-4000-8000-000000000006",
                       "address": "sam@carterandco.example", "score": 35,
                       "reasons": ["bounce rate 6.1% over 7 days"]},
    "send_needs_review": {"review_id": "00000000-0000-4000-8000-000000000007",
                          "lead_id": _SAMPLE_LEAD["lead_id"], "triggers": ["vip_title"]},
    "reengagement_due": {"plan_id": "00000000-0000-4000-8000-000000000008",
                         "lead_id": _SAMPLE_LEAD["lead_id"], "reason_kind": "contract",
                         "status": "due"},
    "reply_draft_approved": {"reply_id": "00000000-0000-4000-8000-000000000009",
                             "lead_id": _SAMPLE_LEAD["lead_id"], "category": "question",
                             "next_action": "Answer the pricing question",
                             "draft_response": "Hi Sam, happy to share pricing..."},
}


class TargetRejected(ValueError):
    pass


# --------------------------------------------------------------------------
# Targets
# --------------------------------------------------------------------------


def normalize_events(events) -> list[str]:
    out: list[str] = []
    for raw in events or []:
        name = ALIASES.get(str(raw).strip(), str(raw).strip())
        if name not in EVENT_TYPES:
            raise TargetRejected(f"unknown event {raw!r}; valid: {sorted(EVENT_DESCRIPTIONS)}")
        if name not in out:
            out.append(name)
    if not out:
        raise TargetRejected("subscribe to at least one event")
    return out


def _non_public(ip: str) -> bool:
    """True unless `ip` is a globally routable address. Anything unparsable
    (e.g. an IPv6 link-local with a %zone suffix) counts as non-public."""
    try:
        return not ipaddress.ip_address(ip.split("%", 1)[0]).is_global
    except ValueError:
        return True


def validate_target_url(url: str) -> str:
    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise TargetRejected("webhook URLs must use https")
    host = (parsed.hostname or "").lower()
    if not host:
        raise TargetRejected("the URL has no host")
    if parsed.username or parsed.password:
        raise TargetRejected("credentials in the URL are not allowed; use the signing secret")
    if host == "localhost" or host.endswith((".localhost", ".internal", ".local")):
        raise TargetRejected("internal hostnames are not allowed")
    # Parse first, check second. TargetRejected IS a ValueError, so raising it
    # inside a `try/except ValueError` meant for "not an IP literal" swallowed
    # the rejection and let https://10.0.0.5/ register (caught by the tests).
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        literal = None   # a hostname; resolved and checked at delivery time
    if literal is not None and not literal.is_global:
        raise TargetRejected("private, loopback and link-local addresses are not allowed")
    return url


def _resolve(host: str) -> list[str]:
    """Factory tests monkeypatch."""
    return list({info[4][0] for info in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)})


def public_destination(url: str) -> bool:
    host = urlparse(url).hostname or ""
    try:
        addresses = _resolve(host)
    except OSError:
        return False
    return bool(addresses) and not any(_non_public(a) for a in addresses)


def new_secret() -> str:
    return "whsec_" + secrets.token_urlsafe(32)


def create_target(db: Session, user_id, url: str, events, *, secret: str | None = None,
                  description: str | None = None, source: str = "api") -> tuple[WebhookTarget, str]:
    url = validate_target_url(url)
    events = normalize_events(events)
    active = db.execute(
        select(func.count(WebhookTarget.id))
        .where(WebhookTarget.user_id == user_id, WebhookTarget.active.is_(True))
    ).scalar_one()
    if active >= MAX_TARGETS_PER_USER:
        raise TargetRejected(f"at most {MAX_TARGETS_PER_USER} active webhooks per account")
    secret = secret or new_secret()
    target = WebhookTarget(user_id=user_id, url=url, secret_encrypted=encrypt(secret),
                           event_types=events, active=True,
                           description=(description or None) and description[:500],
                           source=source)
    db.add(target)
    db.commit()
    db.refresh(target)
    return target, secret


def target_out(target: WebhookTarget) -> dict:
    return {
        "id": str(target.id), "url": target.url,
        "events": list(target.event_types or []), "active": bool(target.active),
        "description": target.description, "source": target.source,
        "disabled_reason": target.disabled_reason,
        "created_at": target.created_at.isoformat() if target.created_at else None,
    }


# --------------------------------------------------------------------------
# Signing
# --------------------------------------------------------------------------


def sign(secret: str, body: bytes, timestamp: int) -> str:
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={mac}"


def verify_signature(secret: str, body: bytes, header: str | None, *,
                     now: float | None = None) -> bool:
    """Reference verifier for receivers (and the tests)."""
    if not header:
        return False
    parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
    try:
        ts = int(parts["t"])
    except (KeyError, ValueError):
        return False
    if abs((time.time() if now is None else now) - ts) > SIGNATURE_TOLERANCE_SECONDS:
        return False
    return hmac.compare_digest(sign(secret, body, ts).split("v1=", 1)[1], parts.get("v1", ""))


def _legacy_signature(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _body(delivery: WebhookDelivery) -> bytes:
    return json.dumps(delivery.payload_json, separators=(",", ":"), sort_keys=True,
                      default=str).encode()


# --------------------------------------------------------------------------
# Fan-out and delivery
# --------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _schedule(delivery_id, countdown: int = 0) -> None:
    from app.workers import webhook_tasks  # noqa: PLC0415

    webhook_tasks.enqueue_delivery(delivery_id, countdown)


def create_delivery(db: Session, target: WebhookTarget, event: str, payload: dict,
                    *, now: datetime | None = None, enqueue: bool = True) -> WebhookDelivery:
    now = now or _now()
    delivery_id = uuid.uuid4()
    delivery = WebhookDelivery(
        id=delivery_id, target_id=target.id, event_type=event,
        payload_json={"id": str(delivery_id), "event": event,
                      "occurred_at": now.isoformat(), "data": payload},
        status="pending", attempts=0, next_attempt_at=now,
    )
    db.add(delivery)
    db.commit()
    if enqueue:
        _schedule(delivery_id, 0)
    return delivery


def fire_event(db: Session, user_id, event: str, payload: dict) -> list[str]:
    """One delivery per active target of `user_id` subscribed to `event`."""
    event = ALIASES.get(event, event)
    uid = user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(str(user_id))
    targets = db.execute(
        select(WebhookTarget).where(WebhookTarget.user_id == uid,
                                    WebhookTarget.active.is_(True))
    ).scalars().all()
    ids = []
    for target in targets:
        subscribed = {ALIASES.get(e, e) for e in (target.event_types or [])}
        if event in subscribed:
            ids.append(str(create_delivery(db, target, event, payload).id))
    return ids


def send_test(db: Session, target: WebhookTarget) -> WebhookDelivery:
    events = [ALIASES.get(e, e) for e in (target.event_types or [])]
    event = next((e for e in events if e in SAMPLES), "reply_received")
    return create_delivery(db, target, event, {**SAMPLES[event], "test": True})


def _http() -> httpx.Client:
    """Factory tests monkeypatch."""
    return httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=False)


def deliver(db: Session, delivery_id, *, now: datetime | None = None) -> str:
    """One attempt. Returns delivered | retry | exhausted | gone | blocked |
    cancelled | skipped. Schedules its own retry."""
    now = now or _now()
    did = delivery_id if isinstance(delivery_id, uuid.UUID) else uuid.UUID(str(delivery_id))
    delivery = db.get(WebhookDelivery, did)
    if delivery is None or delivery.status != "pending":
        return "skipped"
    target = db.get(WebhookTarget, delivery.target_id)
    if target is None or not target.active:
        delivery.status = "cancelled"
        db.commit()
        return "cancelled"

    delivery.attempts = (delivery.attempts or 0) + 1
    delivery.last_attempt_at = now
    if not public_destination(target.url):
        delivery.status = "failed"
        delivery.last_error = "blocked: the host does not resolve to a public address"
        delivery.next_attempt_at = None
        db.commit()
        return "blocked"

    body = _body(delivery)
    secret = decrypt(target.secret_encrypted)
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Vectorae-Webhooks/1.0",
        "X-LeadPilot-Event": delivery.event_type,
        "X-LeadPilot-Delivery": str(delivery.id),
        "X-LeadPilot-Signature": sign(secret, body, int(now.timestamp())),
        "X-ClientHunter-Signature": _legacy_signature(secret, body),
    }
    error = None
    try:
        with _http() as client:
            resp = client.post(target.url, content=body, headers=headers)
        delivery.last_status_code = resp.status_code
        if 200 <= resp.status_code < 300:
            delivery.status = "delivered"
            delivery.delivered_at = now
            delivery.next_attempt_at = None
            delivery.last_error = None
            db.commit()
            return "delivered"
        if resp.status_code == 410:
            target.active = False
            target.disabled_reason = "the receiver answered 410 Gone"
            delivery.status = "cancelled"
            delivery.next_attempt_at = None
            db.commit()
            return "gone"
        error = f"HTTP {resp.status_code}"
    except httpx.HTTPError as exc:
        error = f"{type(exc).__name__}: {exc}"[:1000]

    delivery.last_error = error
    if delivery.attempts >= MAX_ATTEMPTS:
        delivery.status = "exhausted"
        delivery.next_attempt_at = None
        db.commit()
        _record_exhaustion(target, delivery, error or "")
        return "exhausted"
    delay = RETRY_DELAYS_SECONDS[delivery.attempts - 1]
    delivery.next_attempt_at = now + timedelta(seconds=delay)
    db.commit()
    _schedule(delivery.id, delay)
    return "retry"


def _record_exhaustion(target: WebhookTarget, delivery: WebhookDelivery, error: str) -> None:
    """A task_errors row for the admin panel. Best effort."""
    from app.db.base import SessionLocal  # noqa: PLC0415

    logger.error("webhook delivery %s to %s exhausted: %s", delivery.id, target.url[:100], error)
    try:
        from app.models.task_error import TaskError  # noqa: PLC0415

        session = SessionLocal()
        try:
            session.add(TaskError(
                task_name="deliver_webhook", task_id=str(delivery.id),
                args_summary=f"target={target.id} url={target.url[:100]}",
                error_type="WebhookExhausted", error_message=error[:2000], traceback="",
                ts=datetime.utcnow(),
            ))
            session.commit()
        finally:
            session.close()
    except Exception:  # noqa: BLE001 -- logging above is the floor
        logger.warning("could not record webhook exhaustion for %s", delivery.id)


def _delivery_out(d: WebhookDelivery, url: str | None = None) -> dict:
    return {
        "id": str(d.id), "target_id": str(d.target_id), "event": d.event_type,
        "status": d.status, "attempts": d.attempts, "last_status_code": d.last_status_code,
        "last_error": d.last_error,
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "last_attempt_at": d.last_attempt_at.isoformat() if d.last_attempt_at else None,
        "next_attempt_at": d.next_attempt_at.isoformat() if d.next_attempt_at else None,
        "delivered_at": d.delivered_at.isoformat() if d.delivered_at else None,
        **({"target_url": url} if url is not None else {}),
    }


def list_deliveries(db: Session, user_id, limit: int = 50) -> list[dict]:
    rows = db.execute(
        select(WebhookDelivery, WebhookTarget.url)
        .join(WebhookTarget, WebhookTarget.id == WebhookDelivery.target_id)
        .where(WebhookTarget.user_id == user_id)
        .order_by(WebhookDelivery.created_at.desc()).limit(limit)
    ).all()
    return [_delivery_out(d, url) for d, url in rows]


def get_deliveries(db_session: Session, status: str | None = None, limit: int = 50) -> list[dict]:
    """Every user's deliveries (admin)."""
    query = (select(WebhookDelivery, WebhookTarget.url)
             .join(WebhookTarget, WebhookTarget.id == WebhookDelivery.target_id))
    if status:
        query = query.where(WebhookDelivery.status == status)
    rows = db_session.execute(query.order_by(WebhookDelivery.created_at.desc())
                              .limit(limit)).all()
    return [_delivery_out(d, url) for d, url in rows]
