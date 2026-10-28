"""
notifications.py — Vectorae (M7 Chunk 3)

Firebase Cloud Messaging notification service.

Design decisions:
  • Lazy Firebase init: only initialises on first use; raises a clear error if
    neither FIREBASE_CREDENTIALS_JSON nor FIREBASE_CREDENTIALS_PATH is set
    (F-OPS-04). This lets the backend start normally
    even if Firebase is not configured (notifications are a feature, not a
    critical-path requirement for existing M1–M6 functionality).
  • Invalid tokens are marked is_valid=False in device_tokens rather than
    deleted immediately — keeps the audit trail.
  • Deep link URLs are built from constants in deeplinks.ts (documented inline);
    the backend uses the same pattern to ensure client and server agree.
  • Every notification event is logged to the outcomes table.

Wiring into existing services:
  This file provides functions. Call them AFTER the relevant state change
  in existing service code. Do not restructure existing services — just add
  one call at the end of the relevant function. The call sites are marked
  with exact patterns below.

  WIRE IN app/services/pipeline.py (strategy complete / verification fail):
    from app.services.notifications import notify_strategy_ready, notify_strategy_needs_review
    # After marking strategy status = 'complete':
    await notify_strategy_ready(user_id=strategy.user_id, strategy_id=strategy.id)
    # After a verification pass FAIL with no remaining retries:
    await notify_strategy_needs_review(user_id=strategy.user_id, strategy_id=strategy.id)

  WIRE IN app/api/webhooks.py (Calendly meeting booked):
    from app.services.notifications import notify_meeting_booked
    # Inside the Calendly webhook handler, after updating lead status:
    await notify_meeting_booked(user_id=..., lead_id=..., attendee_name=event_name)

  WIRE IN app/services/outreach.py (new reply received):
    from app.services.notifications import notify_new_reply
    # After inserting a new 'replied' outcome event:
    await notify_new_reply(user_id=..., lead_id=..., company=lead.company)

  WIRE IN app/api/campaigns.py (campaign auto-paused on bounce):
    from app.services.notifications import notify_campaign_paused
    await notify_campaign_paused(user_id=..., campaign_id=...)

  WIRE IN app/services/whatsapp.py (template status webhook from Meta):
    from app.services.notifications import notify_whatsapp_template_status
    await notify_whatsapp_template_status(user_id=..., template_name=..., approved=True/False)
"""

from __future__ import annotations

import json
import logging
import uuid
import os
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

# ── Firebase lazy init ────────────────────────────────────────────────────────

_firebase_app = None


def _get_firebase_app():
    """Initialise and return the Firebase Admin app (singleton, lazy)."""
    global _firebase_app
    if _firebase_app is not None:
        return _firebase_app

    try:
        import firebase_admin  # noqa: PLC0415
        from firebase_admin import credentials  # noqa: PLC0415
    except ImportError:
        raise ImportError(
            "firebase-admin is not installed. "
            "Run: pip install firebase-admin"
        )

    # F-OPS-04. Two names existed for one secret: app/core/config.py declares
    # FIREBASE_CREDENTIALS_JSON (the service-account JSON inline) while this
    # function read FIREBASE_CREDENTIALS_PATH (a file on disk). An operator who
    # set the documented variable got a RuntimeError at the first push, and the
    # inline form is the one actually configured in the deployments -- a hosted
    # container has nowhere to put the file.
    #
    # Both are accepted. JSON wins when both are set, because it needs no
    # filesystem. config.py defaults the JSON var to "{}", so an empty object
    # counts as unset rather than as a credential with no fields.
    cred_json = (os.getenv("FIREBASE_CREDENTIALS_JSON") or "").strip()
    cred_path = (os.getenv("FIREBASE_CREDENTIALS_PATH") or "").strip()

    if cred_json and cred_json != "{}":
        try:
            cred = credentials.Certificate(json.loads(cred_json))
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "FIREBASE_CREDENTIALS_JSON is set but is not valid JSON "
                f"({exc}). It must hold the service-account JSON itself, not "
                "a path -- use FIREBASE_CREDENTIALS_PATH for a file."
            ) from exc
    elif cred_path:
        cred = credentials.Certificate(cred_path)
    else:
        raise RuntimeError(
            "Firebase credentials are not configured. Set "
            "FIREBASE_CREDENTIALS_JSON to the service-account JSON itself "
            "(preferred: it needs no filesystem), or "
            "FIREBASE_CREDENTIALS_PATH to a file holding it."
        )

    _firebase_app = firebase_admin.initialize_app(cred)
    log.info("Firebase Admin SDK initialised.")
    return _firebase_app


# ── Deep link URL builder (mirrors frontend/src/lib/deeplinks.ts) ─────────────
# TODO: confirm production domain — must match APP_LINKS_HOST in deeplinks.ts

_DEEP_LINK_SCHEME = "clienthunter"
_APP_LINKS_HOST   = "app.clienthunter.com"  # TODO: confirm production domain


def _custom_link(path: str) -> str:
    """Custom scheme URL for push notification data payload.

    Emits the 3-slash "no authority" form (clienthunter:///strategies/42).
    This used to build `scheme:/` + `/path` = only two slashes, which puts the
    first path segment in the URL's authority position instead of the path, so
    AndroidManifest.xml's intent-filter and parseDeepLink() both fail to route
    it - every push notification deep link was dead on device.

    frontend/src/lib/deeplinks.ts buildCustomSchemeLink() was already fixed to
    the 3-slash form; that fix does nothing until the sender agrees, because
    the backend is what actually populates the notification's deepLink field.
    Keep the two in step.
    """
    clean = path if path.startswith("/") else f"/{path}"
    return f"{_DEEP_LINK_SCHEME}://{clean}"


def _app_link(path: str) -> str:
    """App Links HTTPS URL for email embeds."""
    clean = path if path.startswith("/") else f"/{path}"
    return f"https://{_APP_LINKS_HOST}{clean}"


# ── Core send functions ───────────────────────────────────────────────────────

async def send_to_device(
    token: str,
    title: str,
    body: str,
    data: dict[str, str] | None = None,
    db: Session | None = None,
) -> bool:
    """
    Send an FCM push notification to a single device token.

    Returns True on success, False on failure.
    On unregistered-token error: marks the token invalid in DB (if db is provided).
    """
    # Configuration errors are NOT send failures and must not be swallowed:
    # a missing firebase-admin install or unconfigured Firebase credentials
    # means push is not set up at all. Caught here it would surface as a
    # per-token WARNING indistinguishable from transient FCM noise, so a
    # deployment with push silently disabled looks exactly like a flaky
    # network. These propagate; send_to_user turns them into one loud ERROR.
    _get_firebase_app()
    from firebase_admin import messaging  # noqa: PLC0415

    try:
        # `token=` emits a DeprecationWarning on firebase-admin 7.5.0
        # ("use Message.fid instead"). DO NOT swap it for fid: they are
        # different identifiers, not a rename.
        #   token = an FCM REGISTRATION token, what the mobile client gets
        #           from getToken() and POSTs to /devices/register
        #           (frontend/src/hooks/usePushNotifications.ts), stored in
        #           DeviceToken.token - which is what we hold here.
        #   fid   = a Firebase INSTALLATION ID, from the Installations API.
        # _messaging_encoder.MessageEncoder emits them as two distinct wire
        # fields and rejects a message carrying both, so putting a
        # registration token in fid would simply fail to deliver. Switching
        # would require the client to register installation IDs instead - a
        # mobile-side change, not a backend one. `token` is still fully
        # supported; this is a soft deprecation, warning only.
        message = messaging.Message(
            notification=messaging.Notification(title=title, body=body),
            data={k: str(v) for k, v in (data or {}).items()},
            android=messaging.AndroidConfig(
                priority="high",
                notification=messaging.AndroidNotification(
                    click_action="FLUTTER_NOTIFICATION_CLICK",
                    sound="default",
                ),
            ),
            token=token,
        )

        messaging.send(message)
        log.debug("FCM sent to token %s…", token[-8:])
        return True

    except Exception as exc:
        exc_str = str(exc)
        log.warning("FCM send failed: %s", exc_str)

        # Mark token invalid on unregistered / invalid-argument errors
        if db is not None and any(
            kw in exc_str.lower()
            for kw in ("unregistered", "invalid-argument", "not-found")
        ):
            _mark_token_invalid(token, db)

        return False


def valid_tokens_for_users(session: Session, user_ids: list) -> list[str]:
    """Every valid FCM token belonging to the given users.

    THE canonical device-token query. There used to be two: this module ran
    `await db.execute(...)` against what it annotated as an AsyncSession, while
    NotificationService._tokens_for_user_ids ran the same query synchronously
    against SessionLocal. `get_db` yields a SYNC Session and SessionLocal is
    sync, so the async one could never have executed - it would raise
    "object ChunkedIteratorResult can't be used in 'await' expression" on the
    first call, exactly like the dead /devices router did (session update 2,
    bug 1). Every notify_* helper was therefore not merely uncalled but
    unrunnable. Both paths now come through here.
    """
    from app.db.models import DeviceToken  # noqa: PLC0415

    if not user_ids:
        return []
    rows = session.execute(
        select(DeviceToken.token).where(
            DeviceToken.user_id.in_(user_ids),
            DeviceToken.is_valid == True,  # noqa: E712
        )
    ).all()
    return [r[0] for r in rows]


async def send_to_user(
    user_id,
    title: str,
    body: str,
    data: dict[str, str] | None = None,
    db: Session | None = None,
) -> int:
    """
    Send a push notification to ALL valid device tokens for a user.

    Returns the number of devices successfully notified. `db` is the app's
    SYNC Session; when it is None a short-lived one is opened from
    SessionLocal, so callers with no session handy still work (this used to
    log a warning and return 0, which is why passing db=None silently sent
    nothing).
    """
    owns_session = db is None
    if owns_session:
        from app.db.base import SessionLocal  # noqa: PLC0415

        db = SessionLocal()

    try:
        tokens = valid_tokens_for_users(db, [user_id])

        if not tokens:
            log.debug("No valid device tokens for user %s", user_id)
            return 0

        return await _fan_out(tokens, title, body, data, db, user_id)
    finally:
        if owns_session:
            db.close()


async def _fan_out(tokens: list[str], title: str, body: str,
                   data: dict[str, str] | None, db: Session, user_id) -> int:

    sent = 0
    for token in tokens:
        try:
            ok = await send_to_device(token, title, body, data, db)
        except (RuntimeError, ImportError) as exc:
            # Push is not configured on this deployment. Report it once, at
            # ERROR, and stop -- retrying the remaining tokens would fail
            # identically. Returning (rather than raising) keeps the contract
            # that a push failure never breaks the business operation that
            # triggered it.
            log.error(
                "Push notifications are not configured -- dropping %d "
                "notification(s) for user %s: %s",
                len(tokens) - sent, user_id, exc,
            )
            return sent
        if ok:
            sent += 1

    return sent


def _mark_token_invalid(token: str, db: Session) -> None:
    from app.db.models import DeviceToken  # noqa: PLC0415

    db.execute(
        update(DeviceToken)
        .where(DeviceToken.token == token)
        .values(is_valid=False)
    )
    db.commit()
    log.info("Marked FCM token invalid: %s…", token[-8:])


# ── Recipient resolution (tenancy) ───────────────────────────────────────────
#
# A notification must reach the ONE user who owns the thing it is about, and
# ownership in this schema always runs through the product:
#     Strategy -> Product.user_id
#     Lead -> Strategy -> Product.user_id
# Both rules live here so every call site resolves its recipient the same way.
# This project has shipped cross-tenant bugs from ad-hoc lookups twice already
# (ui_support.py's shadowed /strategies route, and route_inbound_impl matching
# replies by email across every tenant), so the resolution is centralised and
# returns None rather than guessing when the chain is broken.


def owner_of_strategy(session: Session, strategy) -> "uuid.UUID | None":
    """user_id that owns a Strategy, or None if the chain is broken."""
    from app.db.models import Product  # noqa: PLC0415

    if strategy is None:
        return None
    product = session.get(Product, strategy.product_id)
    return product.user_id if product is not None else None


def owner_of_lead(session: Session, lead) -> "uuid.UUID | None":
    """user_id that owns a Lead, via its strategy's product."""
    from app.db.models import Strategy  # noqa: PLC0415

    if lead is None or lead.strategy_id is None:
        return None
    return owner_of_strategy(session, session.get(Strategy, lead.strategy_id))


# ── Notification event functions ──────────────────────────────────────────────
# These are what the rest of the codebase calls. Each wraps send_to_user with
# the correct title, body, and deep link for that event type.

async def notify_strategy_ready(
    user_id: int,
    strategy_id: int,
    db: Session | None = None,
) -> None:
    """Strategy pipeline completed — all 72 steps + 10 verification passes done."""
    await send_to_user(
        user_id=user_id,
        title="Your strategy is ready",
        body="Research and verification complete. Review and launch when ready.",
        data={"deepLink": _custom_link(f"/strategies/{strategy_id}")},
        db=db,
    )


async def notify_strategy_needs_review(
    user_id: int,
    strategy_id: int,
    db: Session | None = None,
) -> None:
    """One or more verification passes failed and could not be auto-fixed."""
    await send_to_user(
        user_id=user_id,
        title="Strategy needs review",
        body="A verification check needs your input before launch.",
        data={"deepLink": _custom_link(f"/strategies/{strategy_id}")},
        db=db,
    )


async def notify_new_reply(
    user_id: int,
    lead_id: int,
    company: str | None = None,
    db: Session | None = None,
) -> None:
    """A lead replied to an outreach message."""
    company_label = company or "a prospect"
    await send_to_user(
        user_id=user_id,
        title=f"New reply from {company_label}",
        body="View the thread and decide your next step.",
        data={"deepLink": _custom_link(f"/leads/{lead_id}")},
        db=db,
    )


async def notify_meeting_booked(
    user_id: int,
    lead_id: int,
    attendee_name: str | None = None,
    db: Session | None = None,
) -> None:
    """Calendly webhook: a meeting was successfully booked."""
    name_label = attendee_name or "a prospect"
    await send_to_user(
        user_id=user_id,
        title="Meeting booked!",
        body=f"Calendly confirmed a meeting with {name_label}.",
        data={"deepLink": _custom_link(f"/leads/{lead_id}")},
        db=db,
    )


async def notify_campaign_paused(
    user_id: int,
    campaign_id: int,
    db: Session | None = None,
) -> None:
    """Campaign auto-paused because the bounce rate exceeded the 3% threshold."""
    await send_to_user(
        user_id=user_id,
        title="Campaign paused — action needed",
        body="Bounce rate exceeded the safe threshold. Review and resume when ready.",
        data={"deepLink": _custom_link(f"/campaigns/{campaign_id}")},
        db=db,
    )


async def notify_whatsapp_template_status(
    user_id: int,
    template_name: str,
    approved: bool,
    db: Session | None = None,
) -> None:
    """Meta approved or rejected a WhatsApp message template."""
    status = "approved" if approved else "rejected"
    body = (
        f"Template \"{template_name}\" was approved by Meta and is ready to use."
        if approved
        else f"Template \"{template_name}\" was rejected by Meta. Review and resubmit."
    )
    await send_to_user(
        user_id=user_id,
        title=f"WhatsApp template {status}",
        body=body,
        data={"deepLink": _custom_link("/campaigns")},
        db=db,
    )


# ── Dispatch from ordinary (non-async) application code ──────────────────────


def dispatch(coro) -> None:
    """Run one notify_* coroutine to completion. NEVER raises.

    Every trigger point for these notifications is ordinary synchronous code -
    the verification loop, the reply router, the bounce-rate check - but two of
    them (the Calendly and WhatsApp webhook handlers) are `async def` while
    still holding a SYNC Session. The established convention in this codebase
    is `try: asyncio.run(NotificationService.send_*(...)) except: log.warning`
    (see core/circuit_breaker.py and workers/webhook_tasks.py), and bare
    asyncio.run() raises RuntimeError inside a running loop - so copying that
    line into the webhook handlers would have silently swallowed every
    notification they tried to send. This helper handles both cases and is the
    ONLY thing the six call sites use, so a wrong trigger point or a changed
    convention is a one-line fix rather than six.

    Failure is logged and dropped: a push notification must never break the
    business operation that triggered it (the same contract
    NotificationService documents).

    IMPORTANT: do not close over the caller's Session in `coro`. In the
    running-loop branch the coroutine executes on a WORKER THREAD, and a
    SQLAlchemy Session is not thread-safe. Every call site therefore passes
    db=None and lets send_to_user open its own short-lived session (which is
    also how NotificationService has always worked). Resolve the recipient on
    the caller's thread with the caller's session, then hand over only ids.
    """
    import asyncio  # noqa: PLC0415

    try:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(coro)          # ordinary sync caller
            return

        # Already inside an event loop (async FastAPI handler). Run the
        # coroutine on its own loop in a worker thread and WAIT, so the
        # behaviour and ordering are identical to the sync path - these
        # handlers already block the loop on sync DB calls.
        from concurrent.futures import ThreadPoolExecutor  # noqa: PLC0415

        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(asyncio.run, coro).result()
    except Exception as exc:  # noqa: BLE001 - deliberately broad
        log.warning("push notification dispatch failed: %s", exc)
