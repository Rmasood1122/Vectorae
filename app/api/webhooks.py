"""Webhooks — Calendly booking events (M3 Chunk 5).

invitee.created  -> lead meeting_booked, sequences stop, `booked` outcome
invitee.canceled -> lead back to replied, event recorded
Deliveries are signature-verified and idempotent (Calendly retries).

NOTE FOR FEATURE 2 (reply intelligence). This module handles BOOKINGS; it
never creates an InboundReply, so there is nothing here to classify. The
`process_inbound_reply` dispatch lives at the three places that actually
create one:
    app/workers/outreach_tasks.py   the Gmail reply poller
    app/api/linkedin.py             the Unipile message_received webhook
    app/api/webhooks_whatsapp.py    the WhatsApp inbound webhook
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.db.models import (
    EnrollmentStatus,
    Lead,
    LeadStatus,
    Outcome,
    OutcomeEvent,
    ProcessedWebhook,
    Product,
    SequenceEnrollment,
    Strategy,
)
from app.integrations.calendly import (
    extract_event_id,
    tenant_id_from_payload,
    verify_webhook_signature,
)
from app.services import notifications
from app.services import sequence_engine as engine
from app.services import webhook_claims

logger = logging.getLogger(__name__)

router = APIRouter(tags=["webhooks"])


def _calendly_event_ref(invitee: dict, fallback: str) -> str:
    """The id that ties a booking's created and canceled deliveries together.

    The SCHEDULED EVENT's URI, not the delivery's event_id: the created and
    canceled deliveries are two different events about the same meeting, and
    the brief they both address has to be found by the thing they share.
    payload.scheduled_event is an object with `uri` (Calendly webhook
    payload reference).
    """
    scheduled = invitee.get("scheduled_event") or {}
    ref = scheduled.get("uri") or invitee.get("event") or invitee.get("uri") or fallback
    return str(ref)[:200]


def _parse_iso(value):
    """Calendly's ISO-8601 start_time as an aware datetime, or None."""
    from datetime import datetime, timezone  # noqa: PLC0415

    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@router.post("/webhooks/calendly")
async def calendly_webhook(request: Request, db: Session = Depends(get_db)) -> dict:
    raw = await request.body()
    signature = request.headers.get("Calendly-Webhook-Signature")
    if not verify_webhook_signature(raw, signature):
        raise HTTPException(status_code=401, detail="invalid webhook signature")

    payload = await request.json()
    event_id = extract_event_id(payload, raw)

    # Idempotency: record first, act once. A retried delivery hits the
    # unique (provider, event_id) constraint and is acknowledged as a dupe.
    db.add(ProcessedWebhook(provider="calendly", event_id=event_id))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return {"ok": True, "duplicate": True}

    # NEW-03: if processing raises, the claim above is RELEASED before the
    # error propagates. It used to stay, so Calendly's retry of a delivery
    # that had answered 500 was skipped as a duplicate and the booking was
    # never recorded.
    try:
        return _process_calendly_event(db, payload, event_id)
    except Exception:
        webhook_claims.release(db, "calendly", event_id)
        raise


def _already_recorded(db: Session, lead: Lead, event: OutcomeEvent, event_id: str) -> bool:
    """True if this lead already has this outcome for this Calendly event --
    a retry of a delivery that failed after the outcome was committed."""
    outcomes = db.execute(
        select(Outcome).where(Outcome.lead_id == lead.id, Outcome.event == event,
                              Outcome.channel == "calendly")
    ).scalars().all()
    return any((o.meta_json or {}).get("calendly_event") == event_id for o in outcomes)


def _process_calendly_event(db: Session, payload: dict, event_id: str) -> dict:
    event_type = payload.get("event")  # "invitee.created" | "invitee.canceled"
    invitee = payload.get("payload") or {}
    email = (invitee.get("email") or "").lower().strip()
    if not email:
        return {"ok": True, "matched": False, "reason": "no invitee email"}

    # WHICH ACCOUNT does this booking belong to? A Calendly delivery carries no
    # tenant identity, so we put ours into the booking link as a UTM parameter
    # and read it back here (app/integrations/calendly.py::TENANT_TRACKING_PARAM).
    #
    # If it is absent or unparseable we QUARANTINE the booking: acknowledge the
    # delivery so Calendly stops retrying, log it loudly, and match nothing.
    # The alternative - falling back to the old global `Lead.email ==` lookup -
    # is precisely the cross-tenant bug this replaces: with two customers
    # prospecting the same person it would flip the wrong account's lead, stop
    # their sequences and write a booking into their learning loop. Never guess
    # a tenant.
    tenant_id = tenant_id_from_payload(payload)
    if tenant_id is None:
        logger.warning(
            "calendly booking for %s carried no usable tenant id (event=%s, "
            "id=%s) - quarantined, no lead matched. A link generated before "
            "tenant tagging, or an organically-found one.",
            email, event_type, event_id,
        )
        return {"ok": True, "matched": False, "reason": "no tenant id",
                "quarantined": True}

    lead = db.execute(
        select(Lead)
        .join(Strategy, Strategy.id == Lead.strategy_id)
        .join(Product, Product.id == Strategy.product_id)
        .where(Lead.email == email, Product.user_id == tenant_id)
    ).scalars().first()
    if lead is None:
        return {"ok": True, "matched": False}

    if event_type == "invitee.created":
        lead.status = LeadStatus.MEETING_BOOKED
        if not _already_recorded(db, lead, OutcomeEvent.BOOKED, event_id):
            db.add(Outcome(lead_id=lead.id, event=OutcomeEvent.BOOKED,
                           channel="calendly",
                           meta_json={"calendly_event": event_id}))
        db.commit()
        enrollments = db.execute(
            select(SequenceEnrollment).where(SequenceEnrollment.lead_id == lead.id)
        ).scalars().all()
        for e in enrollments:
            if e.status is not EnrollmentStatus.STOPPED:
                engine.stop_enrollment(db, e, reason="meeting_booked")

        # Push to the lead's owner. This handler is `async def`, so the
        # asyncio.run() convention used elsewhere would raise inside the
        # running loop - notifications.dispatch handles that and never raises,
        # so the booking stays recorded regardless.
        owner = notifications.owner_of_lead(db, lead)
        if owner is not None:
            notifications.dispatch(notifications.notify_meeting_booked(
                owner, lead.id,
                attendee_name=invitee.get("name") or lead.full_name,
            ))
            # Feature Group 4: Slack + outbound webhooks. push=False -- the M7
            # push above already went, and a second one would double-buzz.
            from app.services import event_bus  # noqa: PLC0415
            from app.workers import notification_tasks  # noqa: PLC0415

            notification_tasks.enqueue_event(
                owner, "meeting_booked", push=False, title="Meeting booked",
                body=f"{invitee.get('name') or lead.full_name or 'A lead'} booked a meeting "
                     f"via Calendly.",
                deep_link=f"/leads/detail?id={lead.id}", data={"leadId": str(lead.id)},
                webhook_payload={"lead": event_bus.lead_payload(lead), "source": "calendly",
                                 "calendly_event": event_id},
            )
        else:
            logger.warning("lead %s has no resolvable owner - no booking "
                           "notification", lead.id)

        # Feature Group 7: queue the meeting prep brief. request_prep never
        # raises and only ENQUEUES -- the model call happens on the
        # notifications worker, so Calendly still gets its fast 200.
        from app.services.meeting_prep import SOURCE_CALENDLY  # noqa: PLC0415
        from app.workers.meeting_prep_tasks import request_prep  # noqa: PLC0415

        scheduled = invitee.get("scheduled_event") or {}
        request_prep(
            db, lead, source=SOURCE_CALENDLY,
            external_ref=_calendly_event_ref(invitee, event_id),
            meeting_start_at=_parse_iso(scheduled.get("start_time")),
            meeting_url=(scheduled.get("location") or {}).get("join_url"),
            user_id=tenant_id,
        )
        return {"ok": True, "matched": True, "action": "booked"}

    if event_type == "invitee.canceled":
        lead.status = LeadStatus.REPLIED
        if not _already_recorded(db, lead, OutcomeEvent.REPLIED, event_id):
            db.add(Outcome(lead_id=lead.id, event=OutcomeEvent.REPLIED,
                           channel="calendly",
                           meta_json={"calendly_event": event_id, "canceled": True}))
        # Stop the brief's reminders: nobody wants a "meeting in 1 hour" push
        # for a call that was cancelled yesterday.
        from app.services.meeting_prep import SOURCE_CALENDLY, cancel_briefs  # noqa: PLC0415

        cancel_briefs(db, lead.id, source=SOURCE_CALENDLY,
                      external_ref=_calendly_event_ref(invitee, event_id))
        db.commit()
        return {"ok": True, "matched": True, "action": "canceled"}

    return {"ok": True, "matched": True, "action": "ignored", "event": event_type}
