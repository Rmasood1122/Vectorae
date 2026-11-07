"""Billing — monthly and annual subscriptions, and pay-per-meeting (Section E).

    start_checkout()        choose a plan: a Stripe Checkout URL, or (stub
                            mode) the plan activated locally
    handle_webhook_event()  Stripe is the source of truth; its events move
                            the subscription row and users.plan
    charge_due_meetings()   the sweep that charges pay-per-meeting usage once
                            its cancellation grace period has passed
    send_renewal_notices()  the daily sweep of auto-renewal notices
                            (acknowledgment, trial ending, annual renewal)
    dispute_meeting() / resolve_dispute() / cancel()

ANNUAL (docs/features/annual-billing.md, flag `annual_billing_enabled`): the
same tiers and entitlements as monthly, billed yearly. The flag only gates NEW
annual checkouts; webhooks, cancel and notices for an existing annual plan
never depend on it. Nothing converts an account to annual on its own.

PILOT-SAFE TRIALS. A subscription checkout made while the account has an
active F09 pilot gets a trial that ends no earlier than the pilot's verdict
(trial_end = max(normal trial end, verdict_final_at)), so nothing is charged
during a pilot. This module READS pilot_mode; pilot_mode never imports billing.

METERING. A booked meeting is an Outcome(event=BOOKED). Those are written from
more than one place (the Calendly webhook, the native calendar's booking task)
and more will come, so -- exactly like app/services/crm_events.py -- a SESSION
LISTENER sees every one of them: `before_flush` hands each new BOOKED outcome
to usage_meter.record_meeting_usage, which adds a BillableMeeting row to the
SAME flush when the lead's owner pays per meeting. The booking and its billing
record commit together or not at all. The listener never raises: a metering
failure must not turn a booking into an error (it is logged instead).

STUB MODE. No STRIPE_SECRET_KEY -> checkout activates the plan locally
(is_stub=True) and the sweep marks meetings charged with is_stub=True. Every
stub path logs STUB_TODO so it is greppable. See BUILD_BLOCKERS.md.
"""

from __future__ import annotations

import logging
import math
import uuid
from datetime import datetime, timedelta, timezone

from html import escape

from sqlalchemy import event, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.core import billing_catalog as catalog
from app.db.models import (
    BillableMeeting,
    BillingRenewalNotice,
    BillingSubscription,
    Lead,
    LeadStatus,
    Outcome,
    PlanTier,
    User,
)
from app.integrations import stripe_billing as stripe

logger = logging.getLogger(__name__)

STUB_TODO = "TODO: connect live Stripe keys"
ANNUAL_FLAG = "annual_billing_enabled"
# Stripe refuses a subscription trial_end less than 48 hours ahead. One hour
# of margin covers the time between computing it here and Stripe checking it
# (the customer may sit on the checkout page); the value is rounded UP to a
# whole second, so the unix seconds sent are never short of it.
STRIPE_MIN_TRIAL = timedelta(hours=48)
TRIAL_FLOOR = STRIPE_MIN_TRIAL + timedelta(hours=1)
# A paid annual year: changing plan before it ends would forfeit the rest of
# the year (the switch path cancels the old subscription with no refund).
PAID = ("active", "past_due")

# Statuses under which a subscription grants its plan. past_due keeps access
# while Stripe retries the card (its dunning decides when to cancel).
GRANTING = ("trialing", "active", "past_due")
_STRIPE_STATUS = {
    "trialing": "trialing", "active": "active", "past_due": "past_due",
    "unpaid": "past_due", "paused": "past_due", "canceled": "canceled",
    "incomplete": "incomplete", "incomplete_expired": "canceled",
}
# A meeting is still chargeable at the end of its grace period when the lead
# got at least as far as a booked meeting. Every later stage means the meeting
# happened (or was never cancelled); REPLIED is what a Calendly cancellation
# writes, so it -- and anything before a booking -- is waived.
_BOOKED_OR_LATER = {LeadStatus.MEETING_BOOKED, LeadStatus.PROPOSAL_SENT,
                    LeadStatus.OPPORTUNITY, LeadStatus.CLOSED_WON,
                    LeadStatus.CLOSED_LOST, LeadStatus.DISQUALIFIED}


class BillingError(ValueError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _ts(unix) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(unix), tz=timezone.utc) if unix else None
    except (TypeError, ValueError):
        return None


def get_subscription(db: Session, user_id) -> BillingSubscription | None:
    return db.execute(
        select(BillingSubscription).where(BillingSubscription.user_id == user_id)
    ).scalar_one_or_none()


def _sync_plan(db: Session, sub: BillingSubscription) -> None:
    """users.plan follows the subscription: its tier while it grants, free after."""
    user = db.get(User, sub.user_id)
    if user is None:
        return
    plan_key = catalog.plan_for(sub.billing_model, sub.tier) if sub.status in GRANTING else None
    user.plan = PlanTier(plan_key) if plan_key else PlanTier.FREE


# --------------------------------------------------------------------------
# Checkout
# --------------------------------------------------------------------------


def _validate(billing_model: str, tier: str | None) -> tuple[str, str]:
    model = (billing_model or "").strip().lower()
    if model == catalog.PAY_PER_MEETING:
        return model, catalog.PAY_PER_MEETING
    if model == catalog.MONTHLY:
        chosen = catalog.monthly_tier(tier)
        if chosen is None:
            raise BillingError("Choose one of the monthly tiers: "
                               + ", ".join(t["id"] for t in catalog.MONTHLY_TIERS))
        return model, chosen["id"]
    if model == catalog.ANNUAL:
        chosen = catalog.annual_tier(tier)
        if chosen is None:
            raise BillingError("Choose one of the tiers: "
                               + ", ".join(t["id"] for t in catalog.annual_tiers()))
        return model, chosen["id"]
    raise BillingError("billing_model must be 'monthly', 'annual' or 'pay_per_meeting'")


def annual_enabled(db: Session) -> bool:
    from app.services import system_settings  # noqa: PLC0415

    return bool(system_settings.get(db, ANNUAL_FLAG))


def pilot_trial_end(db: Session, user_id, now: datetime) -> datetime | None:
    """The verdict time of the account's ACTIVE F09 pilot, if it is still in
    the future -- the earliest moment a new subscription may charge."""
    from app.services import pilot_mode  # noqa: PLC0415

    pilot = pilot_mode.get_active(db, user_id)
    if pilot is None:
        return None
    final_at = pilot_mode.verdict_final_at(pilot, pilot_mode.grace(db))
    return final_at if final_at > now else None


def trial_end_for(db: Session, user_id, *, had_trial: bool,
                  now: datetime) -> tuple[datetime | None, bool]:
    """(when the trial of a new subscription ends, whether a pilot set it).

    Normal: now + TRIAL_DAYS for a first trial, no trial (None) after one.
    During an active pilot: max(normal end, the pilot's verdict time), at
    least STRIPE_MIN_TRIAL ahead -- never earlier than the verdict, so nothing
    is charged during a pilot, even for an account that used its trial."""
    normal = None if had_trial else now + timedelta(days=catalog.TRIAL_DAYS)
    pilot_end = pilot_trial_end(db, user_id, now)
    if pilot_end is None or (normal is not None and normal >= pilot_end):
        return normal, False
    return _ceil_second(max(pilot_end, now + TRIAL_FLOOR)), True


def _ceil_second(value: datetime) -> datetime:
    return datetime.fromtimestamp(math.ceil(value.timestamp()), tz=timezone.utc)


def had_trial(sub: BillingSubscription | None) -> bool:
    """Has this account had its one trial? trial_used survives a replaced
    subscription (whose trial dates are cleared); trial_ends_at covers rows
    from before that column existed."""
    return bool(sub is not None and (sub.trial_used or sub.trial_ends_at is not None))


def checkout_terms(db: Session, user: User, now: datetime | None = None) -> dict:
    """The trial a subscription checkout started NOW would get -- the same
    decision start_checkout makes, so the pricing page's disclosure and button
    state the real terms instead of guessing (docs/features/annual-billing.md)."""
    now = now or _now()
    sub = get_subscription(db, user.id)
    used = had_trial(sub)
    trial_end, pilot_set = trial_end_for(db, user.id, had_trial=used, now=now)
    pilot_end = pilot_trial_end(db, user.id, now)
    return {
        "trial_eligible": not used,
        "trial_days": 0 if used else catalog.TRIAL_DAYS,
        "trial_ends_at": _iso(trial_end),
        "pilot_extended": pilot_set,
        "pilot_verdict_at": _iso(pilot_end),
        "first_charge_at": _iso(trial_end or now),
        "disclosure_version": catalog.DISCLOSURE_VERSION,
        "annual_locked_until": (_iso(sub.current_period_end) if _paid_annual(sub) else None),
    }


def _paid_annual(sub: BillingSubscription | None) -> bool:
    return bool(sub is not None and sub.billing_model == catalog.ANNUAL
                and sub.status in PAID and not sub.is_stub)


def _annual_lock(sub: BillingSubscription | None, model: str, tier_id: str) -> str | None:
    """Why this account may not start a different checkout online, or None.
    A PAID annual year (even one set to cancel at its end) is locked: the
    switch path cancels the old subscription at once and refunds nothing."""
    if sub is None or sub.billing_model != catalog.ANNUAL or sub.status not in PAID:
        return None
    if model == sub.billing_model and tier_id == sub.tier and not sub.cancel_at_period_end:
        return None   # "already on this plan" answers it
    until = _aware(sub.current_period_end)
    when = f"{until:%B} {until.day}, {until:%Y}" if until else "the end of its year"
    return (f"Your annual plan is paid until {when}. Changing it before then needs a "
            "prorated refund, which is not available online yet - contact support, or "
            "cancel it and choose a new plan when the year ends.")


def _check_consent(model: str, accept_renewal_terms: bool,
                   disclosure_version: str | None) -> None:
    """Every auto-renewing subscription (monthly AND annual) needs the
    affirmative consent next to the current disclosure wording. Needs
    founder/legal confirmation: CA Bus. & Prof. Code 17602(a)(2), NY GBL
    527-a, ROSCA 15 U.S.C. 8403(2)."""
    if model not in catalog.SUBSCRIPTION_MODELS:
        return
    if not accept_renewal_terms:
        raise BillingError("Please confirm the renewal terms (accept_renewal_terms) to "
                           "continue.")
    if disclosure_version != catalog.DISCLOSURE_VERSION:
        raise BillingError("The renewal terms have changed since this page was loaded. "
                           "Reload the page and review them again.", status_code=409)


def _consent_metadata(model: str, tier_id: str, now: datetime) -> dict:
    return {"accept_renewal_terms": "true",
            "disclosure_version": catalog.DISCLOSURE_VERSION,
            "consented_at": now.astimezone(timezone.utc).isoformat(),
            "consent_price_cents": str(catalog.price_cents(model, tier_id) or ""),
            "consent_interval": catalog.interval_for(model)}


def _record_consent(sub: BillingSubscription, meta: dict, session_id: str | None,
                    fallback_at: datetime) -> None:
    """Store the consent a COMPLETED checkout carried (never an abandoned one)."""
    if str(meta.get("accept_renewal_terms") or "") != "true":
        return
    try:
        at = datetime.fromisoformat(str(meta.get("consented_at")))
    except ValueError:
        at = fallback_at
    try:
        price = int(meta.get("consent_price_cents"))
    except (TypeError, ValueError):
        price = None
    sub.renewal_consent_at = _aware(at)
    sub.consent_disclosure_version = str(meta.get("disclosure_version") or "")[:40] or None
    sub.consent_price_cents = price
    sub.consent_interval = str(meta.get("consent_interval") or "")[:10] or None
    sub.consent_checkout_session_id = session_id


def start_checkout(db: Session, user: User, *, billing_model: str, tier: str | None = None,
                   accept_renewal_terms: bool = False, disclosure_version: str | None = None,
                   now: datetime | None = None) -> dict:
    now = now or _now()
    model, tier_id = _validate(billing_model, tier)
    if model == catalog.ANNUAL and not annual_enabled(db):
        raise BillingError("Annual billing is not offered right now.", status_code=409)
    _check_consent(model, accept_renewal_terms, disclosure_version)
    sub = get_subscription(db, user.id)
    locked = _annual_lock(sub, model, tier_id)
    if locked:
        raise BillingError(locked, status_code=409)
    if (sub is not None and sub.status in GRANTING and sub.billing_model == model
            and sub.tier == tier_id and not sub.cancel_at_period_end):
        raise BillingError("You are already on this plan.", status_code=409)
    used_trial = had_trial(sub)
    trial_end, pilot_set = (trial_end_for(db, user.id, had_trial=used_trial, now=now)
                            if model in catalog.SUBSCRIPTION_MODELS else (None, False))
    if pilot_set:
        logger.info("billing: user %s has an active pilot; trial runs to %s", user.id,
                    trial_end.isoformat())

    if settings.billing_stub_mode:
        logger.warning("billing stub mode (%s): activating %s/%s for user %s without Stripe",
                       STUB_TODO, model, tier_id, user.id)
        if sub is None:
            sub = BillingSubscription(user_id=user.id, billing_model=model, tier=tier_id)
            db.add(sub)
        sub.billing_model, sub.tier, sub.is_stub = model, tier_id, True
        sub.cancel_at_period_end, sub.canceled_at = False, None
        sub.price_unit_amount_cents = catalog.price_cents(model, tier_id)
        sub.price_interval = (catalog.interval_for(model)
                              if model in catalog.SUBSCRIPTION_MODELS else None)
        if model in catalog.SUBSCRIPTION_MODELS:
            # A stub checkout completes here and now.
            _record_consent(sub, _consent_metadata(model, tier_id, now), None, now)
        if model in catalog.SUBSCRIPTION_MODELS and trial_end is not None:
            sub.status = "trialing"
            sub.trial_used = True
            sub.trial_started_at, sub.trial_ends_at = now, trial_end
            sub.current_period_end = trial_end
        elif model in catalog.SUBSCRIPTION_MODELS:
            days = (catalog.ANNUAL_PERIOD_DAYS if model == catalog.ANNUAL
                    else catalog.MONTHLY_PERIOD_DAYS)
            sub.status, sub.current_period_end = "active", now + timedelta(days=days)
        else:
            sub.status, sub.current_period_end = "active", None
        db.flush()
        _sync_plan(db, sub)
        db.commit()
        return {"mode": "stub", "checkout_url": None, "subscription": subscription_out(sub)}

    frontend = settings.frontend_url.rstrip("/")
    success_url = f"{frontend}/settings?billing=success"
    cancel_url = f"{frontend}/pricing?checkout=cancelled"
    if sub is not None and sub.stripe_checkout_session_id:
        # Close the previous Checkout Session if it is still open, so an old
        # tab cannot complete a checkout this one replaces.
        try:
            stripe.expire_checkout_session(sub.stripe_checkout_session_id)
        except stripe.StripeError as exc:
            logger.info("checkout session %s not expired (already closed?): %s",
                        sub.stripe_checkout_session_id, exc)
    try:
        customer_id = (sub.stripe_customer_id if sub is not None else None) or \
            stripe.create_customer(email=user.email, user_id=str(user.id))["id"]
        if model in catalog.SUBSCRIPTION_MODELS:
            price_setting = (f"stripe_price_{tier_id}_annual" if model == catalog.ANNUAL
                             else f"stripe_price_{tier_id}")
            session = stripe.create_subscription_checkout(
                customer_id=customer_id, user_id=str(user.id),
                tier=catalog.monthly_tier(tier_id),
                success_url=success_url, cancel_url=cancel_url,
                price_id=getattr(settings, price_setting, "") or None,
                billing_model=model, interval=catalog.interval_for(model),
                unit_amount_cents=catalog.price_cents(model, tier_id),
                trial_end=int(trial_end.timestamp()) if pilot_set else None,
                trial_days=0 if used_trial else catalog.TRIAL_DAYS,
                session_metadata={
                    **_consent_metadata(model, tier_id, now),
                    "trial_end": str(int(trial_end.timestamp())) if trial_end else ""})
        else:
            session = stripe.create_setup_checkout(
                customer_id=customer_id, user_id=str(user.id),
                success_url=success_url, cancel_url=cancel_url)
    except stripe.StripeError as exc:
        logger.error("stripe checkout for user %s failed: %s", user.id, exc)
        raise BillingError("Payments are unavailable right now. Please try again shortly.",
                           status_code=502) from exc

    if sub is None:
        # A brand-new row starts `incomplete`; the webhook activates it. An
        # EXISTING row keeps its current plan until the webhook confirms the
        # switch -- abandoning checkout must not downgrade anyone.
        sub = BillingSubscription(user_id=user.id, billing_model=model, tier=tier_id,
                                  status="incomplete")
        db.add(sub)
    sub.stripe_customer_id = customer_id
    sub.stripe_checkout_session_id = session.get("id")
    sub.is_stub = False
    db.commit()
    return {"mode": "stripe", "checkout_url": session.get("url"),
            "subscription": subscription_out(sub)}


def purchase_ppm_credits(db: Session, user: User, amount_cents: int,
                         now: datetime | None = None) -> dict:
    """Purchase additional credits for pay-per-meeting plan.

    Creates a one-time Stripe Checkout for the specified amount. When the
    checkout completes (via webhook), the user's ppm_credits_limit_cents
    is increased by the purchased amount.
    """
    now = now or _now()
    sub = get_subscription(db, user.id)

    if sub is None or sub.billing_model != "pay_per_meeting":
        raise BillingError(
            "Credit purchases are only available for pay-per-meeting plans.",
            status_code=409)

    if sub.status not in GRANTING:
        raise BillingError(
            "Your pay-per-meeting plan is not active. Please reactivate it first.",
            status_code=409)

    if amount_cents < 100:  # Minimum $1
        raise BillingError("Minimum purchase amount is $1.00.", status_code=422)
    if amount_cents > 50000:  # Maximum $500
        raise BillingError("Maximum purchase amount is $500.00.", status_code=422)

    if settings.billing_stub_mode:
        logger.warning("billing stub mode (%s): purchasing %d credits for user %s without Stripe",
                       STUB_TODO, amount_cents, user.id)
        # In stub mode, just increase the limit immediately
        if sub.ppm_credits_limit_cents is None:
            sub.ppm_credits_limit_cents = 6000
        sub.ppm_credits_limit_cents += amount_cents
        db.commit()
        return {
            "mode": "stub",
            "checkout_url": None,
            "new_limit_cents": sub.ppm_credits_limit_cents,
            "subscription": subscription_out(sub)
        }

    if not sub.stripe_customer_id:
        raise BillingError(
            "No payment method on file. Set up a payment method first.",
            status_code=409)

    frontend = settings.frontend_url.rstrip("/")
    success_url = f"{frontend}/usage?credits=purchased"
    cancel_url = f"{frontend}/usage?credits=cancelled"

    try:
        session = stripe.create_one_time_checkout(
            customer_id=sub.stripe_customer_id,
            user_id=str(user.id),
            amount_cents=amount_cents,
            currency="usd",
            description="Additional Vectorae pay-per-meeting credits",
            success_url=success_url,
            cancel_url=cancel_url,
            session_metadata={
                "user_id": str(user.id),
                "purchase_type": "ppm_credits",
                "amount_cents": str(amount_cents)
            }
        )
    except stripe.StripeError as exc:
        logger.error("stripe credit purchase checkout for user %s failed: %s", user.id, exc)
        raise BillingError(
            "Could not create checkout. Please try again.",
            status_code=502) from exc

    # Store the checkout session for tracking
    sub.stripe_checkout_session_id = session.get("id")
    db.commit()

    return {
        "mode": "stripe",
        "checkout_url": session.get("url"),
        "amount_cents": amount_cents,
        "new_limit_cents": (sub.ppm_credits_limit_cents or 6000) + amount_cents
    }


def cancel(db: Session, user: User, now: datetime | None = None) -> dict:
    """Monthly and annual: cancel at period end (access continues until then;
    no renewal). PAST DUE (the renewal payment failed): cancel NOW and void
    the open invoice, so a period the customer cancelled is never collected.
    Pay per meeting: stops immediately; meetings already booked are still
    billed. Stub rows: end now (nothing was charged).

    FAIL CLOSED: a real Stripe subscription (is_stub false) with no Stripe
    key configured is NOT cancelled locally -- Stripe would keep billing it
    while the app said "cancelled". 503, nothing changes."""
    now = now or _now()
    sub = get_subscription(db, user.id)
    if sub is None or sub.status not in GRANTING or sub.cancel_at_period_end:
        raise BillingError("There is no active plan to cancel.", status_code=409)
    stripe_sub = (sub.billing_model in catalog.SUBSCRIPTION_MODELS and not sub.is_stub
                  and bool(sub.stripe_subscription_id))
    if stripe_sub and settings.billing_stub_mode:
        logger.error("cancel refused for subscription %s: no Stripe key configured", sub.id)
        raise BillingError("Payments are not configured, so the plan cannot be cancelled "
                           "online right now. Nothing has been changed - contact support.",
                           status_code=503)
    live = not (settings.billing_stub_mode or sub.is_stub)
    if stripe_sub and sub.status == "past_due":
        try:
            stripe.cancel_subscription_now(sub.stripe_subscription_id)
            voided = stripe.void_open_invoices(sub.stripe_subscription_id)
        except stripe.StripeError as exc:
            raise BillingError("Could not reach the payment provider. Try again.",
                               status_code=502) from exc
        logger.info("past-due subscription %s cancelled now; voided invoices %s",
                    sub.id, voided)
        sub.status, sub.canceled_at = "canceled", now
        _sync_plan(db, sub)
    elif stripe_sub:
        try:
            stripe.cancel_subscription_at_period_end(sub.stripe_subscription_id)
        except stripe.StripeError as exc:
            raise BillingError("Could not reach the payment provider. Try again.",
                               status_code=502) from exc
        sub.cancel_at_period_end = True
    else:
        if not live:
            logger.warning("billing stub mode (%s): cancelling %s locally", STUB_TODO, sub.id)
        sub.status, sub.canceled_at = "canceled", now
        _sync_plan(db, sub)
    db.commit()
    return subscription_out(sub)


# --------------------------------------------------------------------------
# Webhooks
# --------------------------------------------------------------------------


def handle_webhook_event(db: Session, payload: dict) -> str:
    kind = payload.get("type") or ""
    obj = (payload.get("data") or {}).get("object") or {}
    handlers = {
        "checkout.session.completed": _on_checkout_completed,
        "customer.subscription.created": _on_subscription_changed,
        "customer.subscription.updated": _on_subscription_changed,
        "customer.subscription.deleted": _on_subscription_deleted,
        "invoice.paid": lambda d, o: _on_invoice(d, o, paid=True),
        "invoice.payment_failed": lambda d, o: _on_invoice(d, o, paid=False),
    }
    handler = handlers.get(kind)
    if handler is None:
        return "ignored"
    return handler(db, obj)


def _user_from_metadata(db: Session, obj: dict) -> User | None:
    raw = (obj.get("metadata") or {}).get("user_id") or obj.get("client_reference_id")
    try:
        return db.get(User, uuid.UUID(str(raw))) if raw else None
    except ValueError:
        return None


def _record_support_case(db: Session, sub: BillingSubscription, note: str) -> None:
    """A billing state a person must resolve: on the subscription row and in
    the admin panel's task_errors list (the existing admin warning list)."""
    from app.models.task_error import TaskError  # noqa: PLC0415

    logger.error("billing support case for subscription %s: %s", sub.id, note)
    sub.support_note = note[:500]
    db.add(TaskError(task_name="billing", task_id=str(sub.id),
                     args_summary=f"user={sub.user_id}", error_type="BillingNeedsSupport",
                     error_message=note[:2000], traceback="",
                     ts=datetime.now(timezone.utc).replace(tzinfo=None)))


def _period_end(obj: dict) -> datetime | None:
    """current_period_end: top level up to Stripe API 2025-02-24; on the
    subscription ITEMS from 2025-03-31.basil on. Either shape is read."""
    top = _ts(obj.get("current_period_end"))
    if top is not None:
        return top
    try:
        items = (obj.get("items") or {}).get("data") or []
        return _ts(items[0].get("current_period_end")) if items else None
    except (AttributeError, IndexError, TypeError):
        return None


def _price_of(obj: dict) -> dict:
    try:
        items = (obj.get("items") or {}).get("data") or []
        return (items[0].get("price") or {}) if items else {}
    except (AttributeError, IndexError, TypeError):
        return {}


def _apply_subscription(sub: BillingSubscription, obj: dict) -> None:
    """Mirror a Stripe subscription object onto the row: status, period,
    trial, cancel flag and the price Stripe actually bills."""
    tier = (obj.get("metadata") or {}).get("tier")
    if catalog.monthly_tier(tier):
        sub.billing_model, sub.tier = _model_of(obj), tier
    sub.status = _STRIPE_STATUS.get(str(obj.get("status")), sub.status)
    sub.current_period_end = _period_end(obj) or sub.current_period_end
    trial_end = _ts(obj.get("trial_end"))
    if trial_end is not None:
        sub.trial_ends_at, sub.trial_used = trial_end, True
    sub.trial_started_at = _ts(obj.get("trial_start")) or sub.trial_started_at
    sub.cancel_at_period_end = bool(obj.get("cancel_at_period_end"))
    price = _price_of(obj)
    if price.get("unit_amount") is not None:
        try:
            sub.price_unit_amount_cents = int(price["unit_amount"])
        except (TypeError, ValueError):
            pass
    interval = (price.get("recurring") or {}).get("interval")
    if interval:
        sub.price_interval = str(interval)[:10]
    if price.get("currency"):
        sub.price_currency = str(price["currency"])[:3]


def _retrieve(subscription_id: str | None) -> dict:
    if not subscription_id:
        return {}
    try:
        return stripe.retrieve_subscription(subscription_id) or {}
    except stripe.StripeError as exc:
        logger.warning("could not read subscription %s from Stripe: %s", subscription_id, exc)
        return {}


def _on_credit_purchase_completed(db: Session, user: User, obj: dict) -> str:
    """A pay-per-meeting credit purchase checkout completed. Increase the user's
    monthly credit limit by the purchased amount."""
    meta = obj.get("metadata") or {}
    try:
        amount_cents = int(meta.get("amount_cents", 0))
    except (TypeError, ValueError):
        return "bad_metadata"

    if amount_cents <= 0:
        return "invalid_amount"

    sub = get_subscription(db, user.id)
    if sub is None or sub.billing_model != "pay_per_meeting":
        logger.warning(
            "credit purchase completed for user %s but subscription not found or not ppm",
            user.id)
        return "invalid_subscription"

    # Increase the credit limit
    if sub.ppm_credits_limit_cents is None:
        sub.ppm_credits_limit_cents = 6000
    sub.ppm_credits_limit_cents += amount_cents

    # Clear the checkout session
    if sub.stripe_checkout_session_id == obj.get("id"):
        sub.stripe_checkout_session_id = None

    db.commit()
    logger.info("billing: user %s purchased %d credits, new limit: %d cents",
               user.id, amount_cents, sub.ppm_credits_limit_cents)
    return "credit_purchase_completed"


def _on_checkout_completed(db: Session, obj: dict) -> str:
    """A checkout finished. Stripe delivers events in ANY order, so this
    never assumes it is first: it reads the subscription's own state from
    Stripe, and if that read fails it never downgrades a status a subscription
    event already set for the same subscription."""
    user = _user_from_metadata(db, obj)
    if user is None:
        return "no_user"
    meta = obj.get("metadata") or {}

    # Handle pay-per-meeting credit purchases (one-time payments)
    if meta.get("purchase_type") == "ppm_credits":
        return _on_credit_purchase_completed(db, user, obj)

    try:
        model, tier_id = _validate(meta.get("billing_model"), meta.get("tier"))
    except BillingError:
        return "bad_metadata"
    now = _now()
    sub = get_subscription(db, user.id)
    if sub is None:
        sub = BillingSubscription(user_id=user.id, billing_model=model, tier=tier_id)
        db.add(sub)
    previous = sub.stripe_subscription_id
    new_subscription = obj.get("subscription") if obj.get("mode") == "subscription" else None
    replacing = bool(previous and previous != new_subscription)

    if replacing and _paid_annual(sub):
        # A checkout opened earlier completed after this annual year was paid.
        # Cancelling the paid year now would forfeit it (no refund path), and
        # cancelling the new subscription could forfeit what was just paid for
        # it. Keep BOTH: the account stays on its paid annual plan, the new
        # subscription is set to end at its own period end (so it never
        # renews), and a person resolves it (refund/proration) from the admin
        # panel.
        if new_subscription:
            try:
                stripe.cancel_subscription_at_period_end(new_subscription)
            except stripe.StripeError as exc:
                logger.error("could not stop renewal of %s: %s", new_subscription, exc)
        until = _aware(sub.current_period_end)
        _record_support_case(db, sub, (
            f"Checkout {obj.get('id')} completed a {model}/{tier_id} "
            f"{'subscription ' + new_subscription if new_subscription else 'card setup'} "
            f"while annual subscription {previous} is paid until "
            f"{until.isoformat() if until else 'its period end'}. Both kept; the new one "
            "will not renew. Contact the customer about a refund or a prorated change."))
        db.commit()
        return "kept_paid_annual"

    if replacing:
        # The account switched plans: end the subscription being replaced so
        # nobody is billed twice.
        try:
            stripe.cancel_subscription_now(previous)
        except stripe.StripeError as exc:
            logger.error("could not cancel replaced subscription %s: %s", previous, exc)
        # Nothing of the old subscription's trial, period or price may leak
        # into the new one's notices.
        sub.trial_ends_at = sub.trial_started_at = sub.current_period_end = None
        sub.price_unit_amount_cents = sub.price_interval = sub.price_currency = None
    sub.billing_model, sub.tier, sub.is_stub = model, tier_id, False
    sub.stripe_customer_id = obj.get("customer") or sub.stripe_customer_id
    sub.stripe_subscription_id = new_subscription
    if sub.stripe_checkout_session_id == obj.get("id"):
        sub.stripe_checkout_session_id = None
    sub.cancel_at_period_end, sub.canceled_at = False, None
    if model in catalog.SUBSCRIPTION_MODELS:
        _record_consent(sub, meta, obj.get("id"), now)

    fetched = _retrieve(new_subscription)
    if fetched.get("status"):
        _apply_subscription(sub, fetched)
    elif model not in catalog.SUBSCRIPTION_MODELS:
        sub.status = "active"           # a card saved for pay per meeting
    elif replacing or sub.status not in GRANTING:
        # Placeholder until a subscription event lands: trialing only when
        # the checkout asked for a trial (its own metadata says so).
        trial_end = _ts(meta.get("trial_end"))
        if trial_end is not None:
            sub.status, sub.trial_ends_at, sub.trial_used = "trialing", trial_end, True
        else:
            sub.status = "active"
    # else: a subscription event for THIS subscription already set its real
    # status (events out of order) -- keep it.
    db.flush()
    _sync_plan(db, sub)
    db.commit()
    return "activated"


def _on_subscription_changed(db: Session, obj: dict) -> str:
    sub = db.execute(select(BillingSubscription).where(
        BillingSubscription.stripe_subscription_id == obj.get("id"))).scalar_one_or_none()
    if sub is None:
        user = _user_from_metadata(db, obj)
        sub = get_subscription(db, user.id) if user else None
        if sub is None or (sub.stripe_subscription_id not in (None, obj.get("id"))):
            return "ignored_unknown"
        sub.stripe_subscription_id = obj.get("id")
    _apply_subscription(sub, obj)
    _sync_plan(db, sub)
    db.commit()
    return f"status_{sub.status}"


def _price_interval(obj: dict) -> str | None:
    try:
        items = (obj.get("items") or {}).get("data") or []
        return str(((items[0].get("price") or {}).get("recurring") or {}).get("interval")
                   or "") or None
    except (AttributeError, IndexError, TypeError):
        return None


def _model_of(obj: dict) -> str:
    """monthly | annual for a Stripe subscription object. Stripe's own price
    interval wins; then the metadata the checkout wrote; then monthly -- the
    only model there was before annual existed. So an event can never turn a
    monthly subscription into an annual one unless Stripe bills it yearly."""
    interval = _price_interval(obj)
    if interval == "year":
        return catalog.ANNUAL
    if interval == "month":
        return catalog.MONTHLY
    meta_model = str((obj.get("metadata") or {}).get("billing_model") or "")
    return catalog.ANNUAL if meta_model == catalog.ANNUAL else catalog.MONTHLY


def _on_subscription_deleted(db: Session, obj: dict) -> str:
    sub = db.execute(select(BillingSubscription).where(
        BillingSubscription.stripe_subscription_id == obj.get("id"))).scalar_one_or_none()
    if sub is None:
        return "ignored_unknown"   # e.g. a subscription we replaced and already moved off
    sub.status, sub.canceled_at = "canceled", _now()
    _sync_plan(db, sub)
    db.commit()
    return "canceled"


def _on_invoice(db: Session, obj: dict, *, paid: bool) -> str:
    meeting_id = (obj.get("metadata") or {}).get("billable_meeting_id")
    if meeting_id:
        try:
            meeting = db.get(BillableMeeting, uuid.UUID(str(meeting_id)))
        except ValueError:
            meeting = None
        if meeting is None:
            return "ignored_unknown"
        if paid:
            meeting.status = "charged"
            meeting.charged_at = meeting.charged_at or _now()
            meeting.failure_reason = None
        else:
            meeting.status = "failed"
            meeting.failure_reason = "card declined (invoice.payment_failed)"
        db.commit()
        return "meeting_paid" if paid else "meeting_failed"

    sub = db.execute(select(BillingSubscription).where(
        BillingSubscription.stripe_subscription_id == obj.get("subscription"))).scalar_one_or_none()
    if sub is None:
        return "ignored_unknown"
    if not paid:
        sub.status = "past_due"
    elif sub.status == "past_due":
        sub.status = "active"
    _sync_plan(db, sub)
    db.commit()
    return "subscription_paid" if paid else "subscription_past_due"


# --------------------------------------------------------------------------
# Pay-per-meeting: metering listener, charging sweep, disputes
# --------------------------------------------------------------------------


@event.listens_for(Session, "before_flush")
def _meter_booked_meetings(session: Session, _flush_context, _instances) -> None:
    try:
        booked = [obj for obj in session.new
                  if isinstance(obj, Outcome) and obj.lead_id is not None
                  and getattr(obj.event, "value", obj.event) == "booked"]
        if not booked:
            return
        from app.services import usage_meter  # noqa: PLC0415

        with session.no_autoflush:
            for outcome in booked:
                usage_meter.record_meeting_usage(session, outcome)
    except Exception:  # noqa: BLE001 -- metering never breaks a booking
        logger.exception("billing: could not meter a booked meeting")


def install() -> None:
    """No-op marker: importing this module registers the metering listener
    (see app/services/crm_events.py::install for why the call exists)."""
    return None


def charge_due_meetings(db: Session, now: datetime | None = None, limit: int = 500) -> dict:
    now = now or _now()
    rows = db.execute(
        select(BillableMeeting)
        .where(BillableMeeting.status == "pending", BillableMeeting.charge_after <= now)
        .order_by(BillableMeeting.charge_after).limit(limit)
    ).scalars().all()
    counts: dict[str, int] = {}
    for meeting in rows:
        try:
            result = _charge_one(db, meeting, now)
        except Exception:  # noqa: BLE001 -- one bad row must not end the sweep
            logger.exception("billing: charging meeting %s failed", meeting.id)
            db.rollback()
            result = "error"
        counts[result] = counts.get(result, 0) + 1
    return {"processed": len(rows), **counts}


def _get_monthly_charged_amount(db: Session, user_id: uuid.UUID, now: datetime) -> int:
    """Total amount (in cents) charged this month for a user (status='charged')."""
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    result = db.execute(
        select(func.sum(BillableMeeting.amount_cents))
        .where(
            BillableMeeting.user_id == user_id,
            BillableMeeting.status == "charged",
            BillableMeeting.charged_at >= month_start
        )
    ).scalar()
    return int(result) if result else 0


def _charge_one(db: Session, meeting: BillableMeeting, now: datetime) -> str:
    lead = db.get(Lead, meeting.lead_id) if meeting.lead_id else None
    if lead is None or lead.status not in _BOOKED_OR_LATER:
        meeting.status = "waived"
        meeting.resolution_note = "Meeting was no longer booked when the grace period ended."
        db.commit()
        return "waived"

    if settings.billing_stub_mode or meeting.is_stub:
        logger.warning("billing stub mode (%s): meeting %s marked charged without Stripe",
                       STUB_TODO, meeting.id)
        meeting.status, meeting.is_stub, meeting.charged_at = "charged", True, now
        db.commit()
        return "charged_stub"

    sub = get_subscription(db, meeting.user_id)
    if sub is None or not sub.stripe_customer_id:
        meeting.status = "failed"
        meeting.failure_reason = "No saved payment method for this account."
        db.commit()
        return "failed"

    # Check pay-per-meeting credit limit
    if (sub.billing_model == "pay_per_meeting" and
        sub.ppm_credits_limit_cents is not None):
        charged_this_month = _get_monthly_charged_amount(db, meeting.user_id, now)
        if charged_this_month + int(meeting.amount_cents) > sub.ppm_credits_limit_cents:
            meeting.status = "blocked_by_credit_limit"
            meeting.failure_reason = (
                f"Monthly credit limit (${sub.ppm_credits_limit_cents/100:.2f}) reached. "
                "Purchase additional credits to continue booking meetings."
            )
            db.commit()
            return "blocked_by_credit_limit"

    try:
        result = stripe.charge_meeting(
            customer_id=sub.stripe_customer_id, amount_cents=int(meeting.amount_cents),
            currency=meeting.currency,
            description=f"Vectorae: meeting booked {_aware(meeting.occurred_at):%Y-%m-%d}",
            meeting_id=str(meeting.id))
    except stripe.StripeError as exc:
        meeting.status, meeting.failure_reason = "failed", str(exc)[:1000]
        db.commit()
        return "failed"
    meeting.stripe_invoice_id = result.get("invoice_id")
    meeting.stripe_invoice_item_id = result.get("invoice_item_id")
    meeting.status, meeting.charged_at = "charged", now
    db.commit()
    return "charged"


def owned_meeting(db: Session, user: User, meeting_id) -> BillableMeeting:
    try:
        meeting = db.get(BillableMeeting, uuid.UUID(str(meeting_id)))
    except ValueError:
        meeting = None
    if meeting is None or meeting.user_id != user.id:
        raise BillingError("meeting not found", status_code=404)
    return meeting


def dispute_meeting(db: Session, user: User, meeting_id, reason: str,
                    now: datetime | None = None) -> BillableMeeting:
    now = now or _now()
    meeting = owned_meeting(db, user, meeting_id)
    if meeting.status != "pending" or _aware(meeting.charge_after) <= now:
        raise BillingError("Only a meeting still inside its grace period can be disputed. "
                           "For a charged meeting, contact support.", status_code=409)
    meeting.status = "disputed"
    meeting.resolution_note = (reason or "").strip()[:500] or None
    db.commit()
    return meeting


def resolve_dispute(db: Session, meeting: BillableMeeting, *, decision: str, note: str = "",
                    now: datetime | None = None) -> BillableMeeting:
    now = now or _now()
    if meeting.status != "disputed":
        raise BillingError("This meeting is not disputed.", status_code=409)
    if decision == "waive":
        meeting.status = "waived"
    elif decision == "charge":
        meeting.status, meeting.charge_after = "pending", now   # the sweep charges it
    else:
        raise BillingError("decision must be 'waive' or 'charge'")
    meeting.resolution_note = (note or "").strip()[:500] or meeting.resolution_note
    db.commit()
    return meeting


# --------------------------------------------------------------------------
# Auto-renewal notices (docs/features/annual-billing.md, "Notices")
# --------------------------------------------------------------------------
#
# NEEDS FOUNDER/LEGAL CONFIRMATION. Built against: California Automatic
# Renewal Law (Bus. & Prof. Code 17600-17606: acknowledgment; notice 15-45
# days before a renewal of a term of 1 year or more; 3-21 days before a free
# trial longer than 31 days ends) and New York GOL 5-903 (notice 15-30 days
# before renewal). The windows are constants in app/core/billing_catalog.py.

ACKNOWLEDGMENT, TRIAL_END, RENEWAL = "acknowledgment", "trial_end", "renewal"
NOTICE_KINDS = (ACKNOWLEDGMENT, TRIAL_END, RENEWAL)
NOTICE_SENDING, NOTICE_SENT, NOTICE_FAILED = "sending", "sent", "failed"
MAX_NOTICE_ATTEMPTS = 3


#: A `sending` row older than this is stuck (a crash mid-send), not in flight.
STUCK_AFTER = timedelta(hours=1)


def _long_trial(sub: BillingSubscription) -> bool:
    start, end = _aware(sub.trial_started_at), _aware(sub.trial_ends_at)
    return bool(start and end and end - start > timedelta(days=catalog.LONG_TRIAL_DAYS))


def period_key(value: datetime) -> str:
    """A charge time as a dedup key: UTC, whole seconds, explicit Z -- the
    same instant gives the same key whatever timezone the driver returned."""
    return _aware(value).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def charge_amount(sub: BillingSubscription) -> int | None:
    """What the next charge is: the price Stripe actually bills (stored from
    the subscription webhook). The catalog is used only for stub rows --
    a live row whose Stripe price is unknown gets None, never a guess."""
    if sub.is_stub:
        return catalog.price_cents(sub.billing_model, sub.tier)
    return sub.price_unit_amount_cents


def _interval_word(sub: BillingSubscription) -> str:
    interval = sub.price_interval or catalog.interval_for(sub.billing_model)
    return "year" if interval == "year" else "month"


def due_notices(sub: BillingSubscription, now: datetime) -> list[dict]:
    """The notices this subscription is due right now (pure; no I/O).

    Stub subscriptions get none (nothing is charged, so a notice saying it
    will be would be false). Each item: kind, period_key, charge_at,
    amount_cents (None when the Stripe price is not known yet)."""
    if sub.is_stub or sub.billing_model not in catalog.SUBSCRIPTION_MODELS:
        return []
    amount = charge_amount(sub)
    out: list[dict] = []
    annual = sub.billing_model == catalog.ANNUAL
    trial_end = _aware(sub.trial_ends_at)
    period_end = _aware(sub.current_period_end)
    trialing = sub.status == "trialing"
    if annual and sub.status in GRANTING:
        ref = sub.stripe_subscription_id or sub.stripe_checkout_session_id or "-"
        out.append({"kind": ACKNOWLEDGMENT,
                    "period_key": f"{sub.billing_model}:{sub.tier}:{ref}",
                    "charge_at": trial_end if trialing else period_end,
                    "amount_cents": amount, "trialing": trialing})
    if sub.cancel_at_period_end:
        return out
    if (trialing and trial_end is not None and trial_end > now
            and now >= trial_end - timedelta(days=catalog.TRIAL_NOTICE_DAYS)
            and (annual or _long_trial(sub))):
        out.append({"kind": TRIAL_END, "period_key": period_key(trial_end),
                    "charge_at": trial_end, "amount_cents": amount})
    if (annual and sub.status in PAID and period_end is not None and period_end > now
            and now >= period_end - timedelta(days=catalog.RENEWAL_NOTICE_DAYS)):
        out.append({"kind": RENEWAL, "period_key": period_key(period_end),
                    "charge_at": period_end, "amount_cents": amount})
    return out


def _date(value: datetime | None) -> str:
    """'October 25, 2026 (UTC)' -- the date is the UTC date, and says so."""
    if value is None:
        return "the end of the current period"
    utc = _aware(value).astimezone(timezone.utc)
    return f"{utc:%B} {utc.day}, {utc:%Y} (UTC)"


def _money(cents: int | None) -> str:
    if cents is None:
        return "the plan price"
    return f"${cents // 100:,}" + (f".{cents % 100:02d}" if cents % 100 else "")


def notice_content(sub: BillingSubscription, notice: dict) -> tuple[str, list[str]]:
    """(subject, lines) of one notice. Every notice states the plan, the
    amount, the date, that it renews automatically, and how to cancel online."""
    tier = catalog.monthly_tier(sub.tier) or {"name": sub.tier}
    term = _interval_word(sub)
    annual = term == "year"
    plan = f"Vectorae {tier['name']} ({'annual' if annual else 'monthly'})"
    amount = _money(notice.get("amount_cents"))
    when = _date(notice.get("charge_at"))
    cancel_url = f"{settings.frontend_url.rstrip('/')}/profile"
    how_to_cancel = (f"To cancel, open {cancel_url} and press Cancel plan in the Plan card. "
                     "Cancelling stops the renewal; you keep your plan until the end of the "
                     "period you have paid for. Nothing else is needed.")
    kind = notice["kind"]
    if kind == ACKNOWLEDGMENT:
        trialing = notice.get("trialing", sub.status == "trialing")
        subject = f"Your {plan} plan: renewal terms"
        lines = [f"Thank you. Your {plan} plan is active.",
                 (f"Price: {amount} per {term}. Your free trial ends on {when}; the first "
                  f"{amount} is charged then, and again every {term} on that date."
                  if trialing else
                  f"Price: {amount} per {term}. Your next charge is on {when}, and again "
                  f"every {term} on that date."),
                 f"Your plan renews automatically every {term} at {amount} until you cancel. "
                 f"We email you {catalog.RENEWAL_NOTICE_DAYS} days before each renewal.",
                 how_to_cancel,
                 "Keep this email: it is the record of your plan's renewal terms."]
    elif kind == TRIAL_END:
        subject = f"Your Vectorae trial ends on {when}: {amount} will be charged"
        lines = [f"Your free trial of {plan} ends on {when}.",
                 f"Unless you cancel before then, {amount} will be charged on {when}, and "
                 f"your plan then renews automatically every {term} at {amount} until you "
                 "cancel.",
                 how_to_cancel]
    else:
        subject = f"Your {plan} plan renews on {when} for {amount}"
        lines = [f"Your {plan} plan renews automatically on {when}.",
                 f"On that date {amount} will be charged for another {term}, and the plan "
                 f"keeps renewing every {term} until you cancel.",
                 how_to_cancel]
    lines.append("This is a required notice about your Vectorae subscription, sent to the "
                 "account owner. It is not marketing.")
    return subject, lines


def _owner_address(db: Session, sub: BillingSubscription) -> str | None:
    """The account owner's login address. A suspended owner is still mailed:
    they are still billed."""
    owner = db.get(User, sub.user_id)
    return getattr(owner, "email", None) or None


def _send_notice_email(to: str, subject: str, lines: list[str]) -> None:
    from app.services.email_sender import send_email  # noqa: PLC0415

    text = "\n\n".join(lines)
    html = "<p>" + "</p><p>".join(escape(line) for line in lines) + "</p>"
    send_email(to=to, subject=subject, html=html, text=text)


def _escalate(db: Session, sub: BillingSubscription, row: BillingRenewalNotice,
              why: str, now: datetime) -> None:
    """A notice that will not go out on its own: ERROR log + one task_errors
    row for the admin panel, once per notice."""
    from app.models.task_error import TaskError  # noqa: PLC0415

    if row.escalated_at is not None:
        return
    message = (f"{row.kind} notice for subscription {sub.id} (charge "
               f"{row.charge_at.isoformat() if row.charge_at else '-'}) {why}; the customer "
               "has NOT been notified. Send it by hand or fix the cause.")
    logger.error("billing: %s", message)
    row.escalated_at = now
    db.add(TaskError(task_name="billing_renewal_notice", task_id=str(row.id),
                     args_summary=f"user={sub.user_id} subscription={sub.id} kind={row.kind}",
                     error_type="RenewalNoticeNotSent", error_message=message[:2000],
                     traceback="", ts=datetime.now(timezone.utc).replace(tzinfo=None)))
    db.commit()


def _claim_notice(db: Session, sub: BillingSubscription, notice: dict,
                  now: datetime) -> BillingRenewalNotice | str:
    """Claim the notice BEFORE sending (pilot_scorecards' pattern). Returns
    the claimed row, or why it was not claimed. A row stuck in `sending` or
    out of attempts is escalated to the admins (once)."""
    row = db.execute(select(BillingRenewalNotice).where(
        BillingRenewalNotice.subscription_id == sub.id,
        BillingRenewalNotice.user_id == sub.user_id,
        BillingRenewalNotice.kind == notice["kind"],
        BillingRenewalNotice.period_key == notice["period_key"])).scalar_one_or_none()
    charge_at = notice.get("charge_at")
    days_before = (charge_at - now).days if charge_at is not None else None
    if row is None:
        row = BillingRenewalNotice(
            user_id=sub.user_id, subscription_id=sub.id, kind=notice["kind"],
            period_key=notice["period_key"], charge_at=charge_at,
            amount_cents=notice.get("amount_cents"), days_before=days_before,
            status=NOTICE_SENDING, attempts=1)
        db.add(row)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return "already_claimed"
        return row
    if row.status == NOTICE_FAILED and row.attempts < MAX_NOTICE_ATTEMPTS:
        claimed = db.execute(
            update(BillingRenewalNotice)
            .where(BillingRenewalNotice.id == row.id,
                   BillingRenewalNotice.status == NOTICE_FAILED,
                   BillingRenewalNotice.attempts == row.attempts)
            .values(status=NOTICE_SENDING, attempts=row.attempts + 1,
                    days_before=days_before, amount_cents=notice.get("amount_cents"))
            .execution_options(synchronize_session=False)
        ).rowcount
        db.commit()
        if claimed != 1:
            return "already_claimed"
        db.refresh(row)
        return row
    if row.status == NOTICE_SENT:
        return "already_sent"
    if row.status == NOTICE_FAILED:
        _escalate(db, sub, row, f"failed {row.attempts} times ({row.error})", now)
        return "gave_up"
    # `sending`: in flight elsewhere, or a crash mid-send. Never re-sent (a
    # missing duplicate beats a double notice) -- but a stuck one is raised.
    touched = _aware(row.updated_at or row.created_at)
    if touched is not None and now - touched > STUCK_AFTER:
        _escalate(db, sub, row, "is stuck in 'sending' (a crash mid-send?)", now)
        return "stuck"
    return "skipped"


def _refresh_price(db: Session, sub: BillingSubscription) -> None:
    """A live row with no stored Stripe price: read it from Stripe once."""
    if sub.is_stub or not sub.stripe_subscription_id or settings.billing_stub_mode:
        return
    fetched = _retrieve(sub.stripe_subscription_id)
    if fetched:
        _apply_subscription(sub, fetched)
        db.commit()


def send_notice(db: Session, sub: BillingSubscription, notice: dict,
                now: datetime | None = None) -> str:
    now = _aware(now) or _now()
    to = _owner_address(db, sub)
    if not to:
        return "no_recipient"
    if notice.get("amount_cents") is None:
        _refresh_price(db, sub)
        notice = {**notice, "amount_cents": charge_amount(sub)}
    claimed = _claim_notice(db, sub, notice, now)
    if isinstance(claimed, str):
        return claimed
    row = claimed
    if notice.get("amount_cents") is None:
        # Never state a guessed amount: fail (retried; escalated after 3).
        row.status, row.error = NOTICE_FAILED, "the price Stripe bills is not known yet"
        db.commit()
        logger.warning("billing: %s notice for subscription %s held: price unknown",
                       notice["kind"], sub.id)
        return "failed"
    if (notice["kind"] in (RENEWAL, TRIAL_END) and row.days_before is not None
            and row.days_before < (catalog.NOTICE_LATE_DAYS if notice["kind"] == RENEWAL
                                   else 3)):
        logger.error("billing: %s notice for subscription %s is LATE (%s days before the "
                     "charge) - outside the statutory window; sending anyway",
                     notice["kind"], sub.id, row.days_before)
    try:
        subject, lines = notice_content(sub, notice)
        _send_notice_email(to, subject, lines)
    except Exception as exc:  # noqa: BLE001 -- recorded, retried on a later sweep
        db.rollback()
        row.status, row.error = NOTICE_FAILED, f"{type(exc).__name__}: {exc}"[:300]
        db.commit()
        logger.warning("billing: %s notice for subscription %s failed: %s",
                       notice["kind"], sub.id, exc)
        return "failed"
    row.status, row.sent_at, row.error = NOTICE_SENT, now, None
    db.commit()
    return NOTICE_SENT


def send_renewal_notices(db: Session, now: datetime | None = None) -> dict:
    """The daily sweep. Not behind the annual flag: an annual plan that
    exists keeps getting its notices. One failing subscription never stops
    the others."""
    now = _aware(now) or _now()
    counts: dict[str, int] = {"sent": 0, "failed": 0, "escalated": 0, "errors": 0}
    ids = list(db.execute(select(BillingSubscription.id).where(
        BillingSubscription.billing_model.in_(catalog.SUBSCRIPTION_MODELS),
        BillingSubscription.status.in_(GRANTING),
        BillingSubscription.is_stub.is_(False))).scalars())
    for sid in ids:
        try:
            sub = db.get(BillingSubscription, sid)
            if sub is None:
                continue
            for notice in due_notices(sub, now):
                result = send_notice(db, sub, notice, now)
                if result in ("sent", "failed"):
                    counts[result] += 1
                elif result in ("gave_up", "stuck"):
                    counts["escalated"] += 1
        except Exception:  # noqa: BLE001
            db.rollback()
            counts["errors"] += 1
            logger.exception("billing: renewal notices failed for subscription %s", sid)
    return counts


# --------------------------------------------------------------------------
# Read models
# --------------------------------------------------------------------------


def _iso(value: datetime | None) -> str | None:
    return _aware(value).isoformat() if value else None


def subscription_out(sub: BillingSubscription | None) -> dict | None:
    if sub is None:
        return None
    return {
        "billing_model": sub.billing_model, "tier": sub.tier, "status": sub.status,
        "grants_plan": sub.status in GRANTING,
        "trial_ends_at": _iso(sub.trial_ends_at),
        "current_period_end": _iso(sub.current_period_end),
        "cancel_at_period_end": bool(sub.cancel_at_period_end),
        "canceled_at": _iso(sub.canceled_at), "is_stub": bool(sub.is_stub),
        # What the next renewal charges: Stripe's own price once known, the
        # catalog before that (None for pay per meeting).
        "price_cents": (sub.price_unit_amount_cents
                        if sub.price_unit_amount_cents is not None
                        else catalog.price_cents(sub.billing_model, sub.tier)),
        "interval": (sub.price_interval or catalog.interval_for(sub.billing_model)
                     if sub.billing_model in catalog.SUBSCRIPTION_MODELS else None),
        "needs_support": bool(sub.support_note),
    }


def meeting_out(meeting: BillableMeeting) -> dict:
    return {
        "id": str(meeting.id), "lead_id": str(meeting.lead_id) if meeting.lead_id else None,
        "strategy_id": str(meeting.strategy_id) if meeting.strategy_id else None,
        "source": meeting.source, "amount_cents": int(meeting.amount_cents),
        "currency": meeting.currency, "status": meeting.status,
        "occurred_at": _iso(meeting.occurred_at), "charge_after": _iso(meeting.charge_after),
        "charged_at": _iso(meeting.charged_at), "is_stub": bool(meeting.is_stub),
        "failure_reason": meeting.failure_reason, "resolution_note": meeting.resolution_note,
    }


def usage_summary(db: Session, user: User, now: datetime | None = None) -> dict:
    now = now or _now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    rows = db.execute(
        select(BillableMeeting.status, func.count(BillableMeeting.id),
               func.coalesce(func.sum(BillableMeeting.amount_cents), 0))
        .where(BillableMeeting.user_id == user.id, BillableMeeting.occurred_at >= month_start)
        .group_by(BillableMeeting.status)
    ).all()
    by_status = {status: (int(count), int(total)) for status, count, total in rows}
    billable = ("pending", "charged")
    return {
        "period_start": month_start.isoformat(),
        "meetings_booked": sum(c for s, (c, _) in by_status.items() if s != "waived"),
        "amount_cents": sum(t for s, (_, t) in by_status.items() if s in billable),
        "by_status": {s: c for s, (c, _) in by_status.items()},
        "price_per_meeting_cents": catalog.meeting_price_cents(),
        "currency": catalog.CURRENCY,
    }


def plan_access(db: Session, user: User) -> dict:
    """Does this account have a plan that lets it use the product?

    Returned on every sign-in, refresh and /auth/me, computed from the
    database each time rather than cached on the token. The frontend sends a
    user without one to /pricing straight after an explicit sign-in only.

    True when any of:
      * its own subscription is in a GRANTING status (trialing counts: the
        trial IS a purchased plan -- a card or checkout was completed);
      * users.plan is not free -- _sync_plan keeps it in step with the
        subscription, and it also covers plans set before billing existed
        (legacy pro/enterprise rows) or granted by an operator;
      * it is an operator (is_admin), who must reach the admin panel;
      * it is a member of someone else's workspace whose OWNER has a plan --
        a member works on the owner's account and is never asked to buy one.
    """
    sub = get_subscription(db, user.id)
    status = sub.status if sub is not None else None
    plan = str(getattr(user.plan, "value", user.plan) or "free")
    active = status in GRANTING or plan != PlanTier.FREE.value or bool(user.is_admin)
    if not active:
        from app.db.models import Workspace, WorkspaceMember  # noqa: PLC0415

        owners = db.execute(
            select(User.plan, BillingSubscription.status)
            .select_from(WorkspaceMember)
            .join(Workspace, Workspace.id == WorkspaceMember.workspace_id)
            .join(User, User.id == Workspace.owner_user_id)
            .outerjoin(BillingSubscription, BillingSubscription.user_id == User.id)
            .where(WorkspaceMember.user_id == user.id, Workspace.owner_user_id != user.id)
        ).all()
        active = any(
            owner_status in GRANTING
            or str(getattr(owner_plan, "value", owner_plan) or "free") != PlanTier.FREE.value
            for owner_plan, owner_status in owners
        )
    return {"has_active_plan": bool(active), "subscription_status": status}


def overview(db: Session, user: User) -> dict:
    return {
        "plan": getattr(user.plan, "value", user.plan) or "free",
        "subscription": subscription_out(get_subscription(db, user.id)),
        "usage": usage_summary(db, user),
        "stub_mode": settings.billing_stub_mode,
        "checkout_terms": checkout_terms(db, user),
    }
