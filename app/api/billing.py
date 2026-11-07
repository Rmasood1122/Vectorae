"""Billing endpoints (Section E).

GET  /billing/catalog                  PUBLIC. The pricing options (monthly,
                                       annual when `annual_billing_enabled`,
                                       pay per meeting), with the
                                       entitlements each tier grants
GET  /billing                          the account's plan, subscription, usage,
                                       and the trial a checkout would get now
GET  /billing/checkout-terms           just that trial decision (the pricing
                                       page's disclosure is driven by it)
POST /billing/checkout                 choose a plan -> Stripe Checkout URL (or,
                                       in stub mode, the plan activated locally)
POST /billing/cancel
GET  /billing/meetings                 pay-per-meeting usage rows
POST /billing/meetings/{id}/dispute    inside the 48h grace period only
POST /admin/billing/meetings/{id}/resolve   admin: waive or charge a dispute
POST /webhooks/stripe                  Stripe events, signature-verified

`/billing` is a PERSONAL route (app/services/rbac.py): a workspace member
acting in someone else's workspace still buys and cancels only for their OWN
account -- choosing a plan for the owner is not a thing a teammate can do.
Checkout is phone-gated (PHONE_NOT_VERIFIED), see BUILD_DECISIONS.md C1.
"""

from __future__ import annotations

import json
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_admin
from app.config import settings
from app.core import billing_catalog, production_guard
from app.db.base import get_db
from app.db.models import BillableMeeting, ProcessedWebhook, User
from app.integrations import stripe_billing
from app.services import billing
from app.services import identity as identity_svc

logger = logging.getLogger(__name__)
router = APIRouter(tags=["billing"])


class CheckoutIn(BaseModel):
    billing_model: str = Field(pattern="^(monthly|annual|pay_per_meeting)$")
    tier: str | None = Field(default=None, max_length=20)
    # Monthly and annual: the account ticked "I agree to these renewal terms"
    # next to the auto-renewal disclosure whose version it echoes back
    # (docs/features/annual-billing.md). Not needed for pay per meeting.
    accept_renewal_terms: bool = False
    disclosure_version: str | None = Field(default=None, max_length=40)


class DisputeIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class ResolveIn(BaseModel):
    decision: str = Field(pattern="^(waive|charge)$")
    note: str = Field(default="", max_length=500)


class PurchaseCreditsIn(BaseModel):
    amount_cents: int = Field(gt=0, description="Amount in cents (e.g. 5000 for $50)")
    phone_verified: bool = Field(default=False, description="Confirms phone is verified")


def _raise(exc: billing.BillingError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get("/billing/catalog")
def get_catalog(db: Session = Depends(get_db)) -> dict:
    return billing_catalog.catalog(annual_enabled=billing.annual_enabled(db))


@router.get("/billing")
def get_billing(db: Session = Depends(get_db),
                current_user: User = Depends(get_current_user)) -> dict:
    return billing.overview(db, current_user)


@router.get("/billing/checkout-terms")
def get_checkout_terms(db: Session = Depends(get_db),
                       current_user: User = Depends(get_current_user)) -> dict:
    return billing.checkout_terms(db, current_user)


@router.post("/billing/checkout")
def checkout(body: CheckoutIn, db: Session = Depends(get_db),
             current_user: User = Depends(get_current_user)) -> dict:
    identity_svc.require_verified_phone(current_user)
    # Stub mode activates the plan locally without payment. That is for
    # development and test only: in production it would hand out any tier
    # for free, so with no Stripe key checkout fails closed before anything
    # is written.
    if settings.billing_stub_mode and production_guard.is_production(settings.app_env):
        raise HTTPException(status_code=503,
                            detail="payments are not configured; plans cannot be changed right now")
    try:
        return billing.start_checkout(db, current_user, billing_model=body.billing_model,
                                      tier=body.tier,
                                      accept_renewal_terms=body.accept_renewal_terms,
                                      disclosure_version=body.disclosure_version)
    except billing.BillingError as exc:
        raise _raise(exc)


@router.post("/billing/cancel")
def cancel(db: Session = Depends(get_db),
           current_user: User = Depends(get_current_user)) -> dict:
    try:
        return billing.cancel(db, current_user)
    except billing.BillingError as exc:
        raise _raise(exc)


@router.get("/billing/meetings")
def list_meetings(status: str | None = Query(default=None,
                                             pattern="^(pending|charged|waived|disputed|failed)$"),
                  limit: int = Query(default=100, ge=1, le=500),
                  db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)) -> list[dict]:
    query = select(BillableMeeting).where(BillableMeeting.user_id == current_user.id)
    if status:
        query = query.where(BillableMeeting.status == status)
    rows = db.execute(query.order_by(BillableMeeting.occurred_at.desc()).limit(limit)).scalars()
    return [billing.meeting_out(r) for r in rows]


@router.get("/billing/usage")
def get_usage(limit: int = Query(default=20, ge=1, le=100),
              db: Session = Depends(get_db),
              current_user: User = Depends(get_current_user)) -> dict:
    """Usage page: summary stats + recent billable meetings."""
    summary = billing.usage_summary(db, current_user)
    query = (select(BillableMeeting)
             .where(BillableMeeting.user_id == current_user.id)
             .order_by(BillableMeeting.occurred_at.desc())
             .limit(limit))
    meetings = db.execute(query).scalars()
    return {
        **summary,
        "meetings": [billing.meeting_out(m) for m in meetings],
    }


@router.post("/billing/meetings/{meeting_id}/dispute")
def dispute(meeting_id: uuid.UUID, body: DisputeIn, db: Session = Depends(get_db),
            current_user: User = Depends(get_current_user)) -> dict:
    try:
        return billing.meeting_out(billing.dispute_meeting(db, current_user, meeting_id,
                                                           body.reason))
    except billing.BillingError as exc:
        raise _raise(exc)


@router.post("/admin/billing/meetings/{meeting_id}/resolve")
def resolve(meeting_id: uuid.UUID, body: ResolveIn, db: Session = Depends(get_db),
            _admin: User = Depends(require_admin)) -> dict:
    meeting = db.get(BillableMeeting, meeting_id)
    if meeting is None:
        raise HTTPException(status_code=404, detail="meeting not found")
    try:
        return billing.meeting_out(billing.resolve_dispute(db, meeting, decision=body.decision,
                                                           note=body.note))
    except billing.BillingError as exc:
        raise _raise(exc)


@router.post("/billing/purchase-credits")
def purchase_credits(body: PurchaseCreditsIn, db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_user)) -> dict:
    """Purchase additional credits for pay-per-meeting plan.

    Only available for users on pay_per_meeting billing model. Increases their
    monthly credit limit by the specified amount (in cents). Requires phone
    verification and returns a Stripe Checkout URL (or stub mode response).
    """
    identity_svc.require_verified_phone(current_user)

    try:
        return billing.purchase_ppm_credits(db, current_user, body.amount_cents)
    except billing.BillingError as exc:
        raise _raise(exc)


@router.post("/webhooks/stripe", include_in_schema=False)
async def stripe_webhook(request: Request, db: Session = Depends(get_db)) -> dict:
    """Verified against the RAW body (re-serialising would change the bytes the
    signature covers), then recorded in processed_webhooks before acting, so a
    retried delivery is acknowledged as a duplicate and never applied twice.

    No STRIPE_WEBHOOK_SECRET -> 503: an endpoint that accepted unsigned events
    would let anyone grant themselves a plan."""
    if not settings.stripe_webhook_secret:
        raise HTTPException(status_code=503, detail="stripe webhooks are not configured")
    raw = await request.body()
    if not stripe_billing.verify_webhook(raw, request.headers.get("Stripe-Signature")):
        raise HTTPException(status_code=401, detail="invalid webhook signature")
    try:
        payload = json.loads(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="malformed payload")
    event_id = str(payload.get("id") or "")
    if not event_id:
        raise HTTPException(status_code=400, detail="event has no id")

    db.add(ProcessedWebhook(provider="stripe", event_id=event_id))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return {"ok": True, "duplicate": True}
    result = billing.handle_webhook_event(db, payload)
    logger.info("stripe webhook %s (%s): %s", event_id, payload.get("type"), result)
    return {"ok": True, "result": result}
