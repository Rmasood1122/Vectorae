"""Who signed up, and from where -- visible to administrators only.

Founder request (2026-10-30): "how can I know who and from where makes an
account on Vectorae". Three pieces, all admin-only:

* every sign-up writes one `signup` row to the account security log
  (account_security_events) with the address it came from, in the same
  transaction as the account;
* after the account is committed, a background job looks the address up
  (country only, the identity step's geolocation provider) and tells every
  admin by email and push. It runs off the request path so a slow lookup or a
  mail outage can never fail or slow down a sign-up;
* GET /admin/signups lists sign-ups and refused sign-up attempts (VPN blocks)
  newest first, and GET /admin/users shows each account's sign-up address and
  countries.

Personal data: the address and country are already collected at sign-up
(users.signup_ip, migration 0039) and the log rows are scrubbed when the
account is erased (account_erasure: account_security_events KEEP_SCRUBBED).
"""

from __future__ import annotations

import asyncio
import html
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import AccountSecurityEvent, User

logger = logging.getLogger(__name__)

EVENT_SIGNUP = "signup"
# What the sign-up log page lists: accounts created, and attempts refused
# before an account existed (network_guard writes `<action>_blocked_vpn` and
# `<action>_vpn_check_failed` with action "signup").
SIGNUP_EVENTS = (EVENT_SIGNUP, "signup_blocked_vpn", "signup_vpn_check_failed")
OUTCOMES = {
    EVENT_SIGNUP: "created",
    "signup_blocked_vpn": "refused: VPN, proxy or datacenter address",
    "signup_vpn_check_failed": "refused: network check unavailable",
}


_UTM_KEYS = ("utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content")
_MAX_VALUE = 120


def _clean_text(value) -> str | None:
    if not isinstance(value, str):
        return None
    text = "".join(ch for ch in value if ch.isprintable()).strip()
    return text[:_MAX_VALUE] or None


def clean_source(raw: dict | None) -> dict | None:
    """The sign-up source worth keeping, minimised (Admin > Growth).

    UTM tags as sent; the referrer cut to its HOST (a full referring URL can
    carry someone's search terms or a token in its query); the landing page cut
    to its PATH (no query, no fragment). Anything else is dropped. None when
    nothing is left, so a direct visit stores no source at all.
    """
    from urllib.parse import urlsplit  # noqa: PLC0415

    if not isinstance(raw, dict):
        return None
    out = {k: v for k in _UTM_KEYS if (v := _clean_text(raw.get(k)))}
    referrer = _clean_text(raw.get("referrer"))
    if referrer:
        try:
            host = (urlsplit(referrer if "//" in referrer else f"//{referrer}")
                    .hostname or "")
        except ValueError:
            host = ""
        if host:
            out["referrer_host"] = host[:_MAX_VALUE]
    landing = _clean_text(raw.get("landing_path"))
    if landing:
        try:
            path = urlsplit(landing).path or ""
        except ValueError:
            path = ""
        if path.startswith("/"):
            out["landing_path"] = path[:_MAX_VALUE]
    return out or None


def source_label(source: dict | None) -> str:
    """One word for "where from": utm_source, else the referring site, else direct."""
    if not source:
        return "direct"
    return source.get("utm_source") or source.get("referrer_host") or "direct"


def record_signup(db: Session, user: User, ip: str | None,
                  source: dict | None = None) -> AccountSecurityEvent:
    """Add (not commit) the sign-up row; it rides the sign-up transaction."""
    from app.services import identity  # noqa: PLC0415

    if user.id is None:
        db.flush()  # a brand-new account gets its id at flush
    cleaned = clean_source(source)
    return identity.record_event(db, event=EVENT_SIGNUP, user=user, ip=ip,
                                 details={"source": cleaned} if cleaned else None)


def admin_emails(db: Session) -> list[str]:
    return [e for e in db.execute(
        select(User.email).where(User.is_admin.is_(True))).scalars() if e]


def _message(event: AccountSecurityEvent) -> tuple[str, str, str]:
    where = event.country or "unknown country"
    subject = f"New Vectorae account: {event.email}"
    text = (f"A new account was created on Vectorae.\n\n"
            f"Email: {event.email}\n"
            f"Signed up from: {event.ip or 'unknown address'} ({where})\n"
            f"When: {event.ts:%Y-%m-%d %H:%M} UTC\n\n"
            f"See every sign-up under Admin > Sign-ups.\n")
    rows = "".join(f"<tr><td style='padding:2px 12px 2px 0'><b>{html.escape(k)}</b></td>"
                   f"<td>{html.escape(v)}</td></tr>"
                   for k, v in (("Email", event.email or ""),
                                ("Signed up from", f"{event.ip or 'unknown address'} ({where})"),
                                ("When", f"{event.ts:%Y-%m-%d %H:%M} UTC")))
    body = (f"<p>A new account was created on Vectorae.</p><table>{rows}</table>"
            f"<p>See every sign-up under Admin &gt; Sign-ups.</p>")
    return subject, body, text


def alert_admins(db: Session, event_id: str | uuid.UUID) -> dict:
    """Fill in the country, then email and push every admin. Never raises.

    Returns what happened, for the worker log and the tests:
    {"country", "emailed", "pushed", "skipped"}.
    """
    from app.integrations import geolocation  # noqa: PLC0415
    from app.services.email_sender import send_email  # noqa: PLC0415

    event = db.get(AccountSecurityEvent, uuid.UUID(str(event_id)))
    if event is None or event.event != EVENT_SIGNUP:
        return {"skipped": "no such sign-up"}
    if event.country is None and event.ip:
        event.country = geolocation.lookup_country(event.ip)
        db.commit()
    result = {"country": event.country, "emailed": 0, "pushed": 0}
    if not settings.signup_alerts_enabled:
        result["skipped"] = "SIGNUP_ALERTS_ENABLED is off"
        return result

    subject, body, text = _message(event)
    for to in admin_emails(db):
        if to == event.email:
            continue  # the first admin creating their own account
        try:
            send_email(to=to, subject=subject, html=body, text=text)
            result["emailed"] += 1
        except Exception as exc:  # noqa: BLE001 -- one admin's mail must not stop the rest
            logger.warning("sign-up alert email to an admin failed: %s", type(exc).__name__)
    try:
        from app.services.notification_service import NotificationService  # noqa: PLC0415

        result["pushed"] = asyncio.run(NotificationService.send_to_admins(
            "New Vectorae account",
            f"{event.email} from {event.country or 'an unknown country'}",
            {"type": "signup", "deepLink": "/admin/signups"}))
    except Exception as exc:  # noqa: BLE001 -- push is best effort
        logger.warning("sign-up alert push failed: %s", type(exc).__name__)
    return result


def list_signups(db: Session, page: int = 1, page_size: int = 50) -> list[dict]:
    rows = db.execute(
        select(AccountSecurityEvent)
        .where(AccountSecurityEvent.event.in_(SIGNUP_EVENTS))
        .order_by(AccountSecurityEvent.ts.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ).scalars().all()
    return [{
        "ts": r.ts, "event": r.event, "outcome": OUTCOMES.get(r.event, r.event),
        "email": r.email, "ip": r.ip, "country": r.country,
        "user_id": str(r.user_id) if r.user_id else None,
    } for r in rows]


def signup_origin(db: Session, user_ids: list) -> dict[str, dict]:
    """{user_id: {"ip", "country"}} from each user's sign-up row, if any."""
    if not user_ids:
        return {}
    rows = db.execute(
        select(AccountSecurityEvent)
        .where(AccountSecurityEvent.event == EVENT_SIGNUP,
               AccountSecurityEvent.user_id.in_(user_ids))
        .order_by(AccountSecurityEvent.ts)
    ).scalars().all()
    return {str(r.user_id): {"ip": r.ip, "country": r.country} for r in rows}
