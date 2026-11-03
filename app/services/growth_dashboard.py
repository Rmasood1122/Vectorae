"""Admin > Growth: who signed up, from where, and who bought what.

Founder request (2026-11-03): one live page, visible only to the addresses on
GROWTH_DASHBOARD_VIEWERS (app/api/deps.require_growth_viewer), showing every
sign-up (when, from which address and country, from which source) and every
subscription purchase (which account, which plan, when, how much, where the
account came from).

Read-only. Built only from rows the product already keeps:

* sign-ups      account_security_events (event "signup"; signup_watch.py),
                with the account's identity answers from users;
* purchases     billing_subscriptions, one row per account (Stripe mirrors).

Money: a stub subscription (is_stub, activated with no Stripe key) is listed
but never counted as revenue, and recurring revenue is summed PER CURRENCY --
adding USD to EUR would be a made-up number.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import billing_catalog
from app.db.models import AccountSecurityEvent, BillingSubscription, User
from app.services import signup_watch

PAYING = ("active", "past_due")
TRIALING = "trialing"
MAX_WINDOW_DAYS = 365
MAX_ROWS = 200


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _iso(value: datetime | None) -> str | None:
    value = _aware(value)
    return value.isoformat() if value else None


def purchased_at(sub: BillingSubscription) -> datetime | None:
    """When the account bought: the completed checkout's consent time, else the
    row's creation (pay per meeting and stub rows carry no consent)."""
    return _aware(sub.renewal_consent_at) or _aware(sub.created_at)


def monthly_cents(sub: BillingSubscription) -> int | None:
    """What this subscription is worth per month, in its own currency's cents."""
    amount = sub.price_unit_amount_cents
    if amount is None:
        amount = sub.consent_price_cents
    if amount is None:
        return None
    interval = (sub.price_interval or sub.consent_interval or "").lower()
    if interval == "year" or (not interval and sub.billing_model == billing_catalog.ANNUAL):
        return round(amount / 12)
    return amount


def is_paying(sub: BillingSubscription) -> bool:
    return (not sub.is_stub and sub.status in PAYING
            and sub.billing_model in billing_catalog.SUBSCRIPTION_MODELS)


def _signup_rows(db: Session, since: datetime) -> list[AccountSecurityEvent]:
    return list(db.execute(
        select(AccountSecurityEvent)
        .where(AccountSecurityEvent.event == signup_watch.EVENT_SIGNUP,
               AccountSecurityEvent.ts >= since)
        .order_by(AccountSecurityEvent.ts.desc())
    ).scalars())


def _count_signups(db: Session, since: datetime) -> int:
    return int(db.execute(
        select(func.count(AccountSecurityEvent.id))
        .where(AccountSecurityEvent.event == signup_watch.EVENT_SIGNUP,
               AccountSecurityEvent.ts >= since)
    ).scalar() or 0)


def _source_of(event: AccountSecurityEvent | None) -> dict | None:
    details = (event.details_json or {}) if event is not None else {}
    source = details.get("source") if isinstance(details, dict) else None
    return source if isinstance(source, dict) else None


def _signup_out(event: AccountSecurityEvent, user: User | None,
                sub: BillingSubscription | None) -> dict:
    source = _source_of(event)
    return {
        "signed_up_at": _iso(event.ts),
        "user_id": str(event.user_id) if event.user_id else None,
        "email": event.email,
        "full_name": user.full_name if user else None,
        "ip": event.ip,
        "ip_country": event.country,
        "declared_country": user.personal_country if user else None,
        "account_type": user.account_type if user else None,
        "company_name": user.company_name if user else None,
        "company_country": user.company_country if user else None,
        "source": signup_watch.source_label(source),
        "utm_source": (source or {}).get("utm_source"),
        "utm_medium": (source or {}).get("utm_medium"),
        "utm_campaign": (source or {}).get("utm_campaign"),
        "referrer_host": (source or {}).get("referrer_host"),
        "landing_path": (source or {}).get("landing_path"),
        "email_verified": bool(user.email_verified) if user else None,
        "phone_verified": bool(user.phone_verified) if user else None,
        "identity_review": user.geo_review_status if user else None,
        "is_suspended": bool(user.is_suspended) if user else None,
        "plan": (user.plan.value if hasattr(user.plan, "value") else user.plan) if user else None,
        "subscription_status": sub.status if sub else None,
        "last_active_at": _iso(user.last_active_at) if user else None,
        "account_deleted": user is None,
    }


def _purchase_out(sub: BillingSubscription, user: User | None,
                  signup: AccountSecurityEvent | None) -> dict:
    bought = purchased_at(sub)
    signed_up = _aware(signup.ts) if signup is not None else _aware(
        user.created_at if user else None)
    source = _source_of(signup)
    return {
        "subscription_id": str(sub.id),
        "user_id": str(sub.user_id),
        "email": user.email if user else None,
        "company_name": user.company_name if user else None,
        "tier": sub.tier,
        "billing_model": sub.billing_model,
        "status": sub.status,
        "purchased_at": _iso(bought),
        "amount_cents": sub.price_unit_amount_cents if sub.price_unit_amount_cents is not None
        else sub.consent_price_cents,
        "currency": (sub.price_currency or "").upper() or None,
        "interval": sub.price_interval or sub.consent_interval,
        "monthly_value_cents": monthly_cents(sub),
        "counts_as_revenue": is_paying(sub),
        "is_stub": bool(sub.is_stub),
        "trial_started_at": _iso(sub.trial_started_at),
        "trial_ends_at": _iso(sub.trial_ends_at),
        "current_period_end": _iso(sub.current_period_end),
        "cancel_at_period_end": bool(sub.cancel_at_period_end),
        "canceled_at": _iso(sub.canceled_at),
        "stripe_customer_id": sub.stripe_customer_id,
        "signup_ip": signup.ip if signup is not None else (user.signup_ip if user else None),
        "signup_country": signup.country if signup is not None else None,
        "signup_source": signup_watch.source_label(source),
        "days_signup_to_purchase": (bought - signed_up).days
        if bought and signed_up else None,
        "needs_attention": bool(sub.support_note),
    }


def build(db: Session, *, days: int = 30, now: datetime | None = None) -> dict:
    """The whole dashboard for the last `days` days (1..365)."""
    now = _aware(now) or datetime.now(timezone.utc)
    days = max(1, min(int(days), MAX_WINDOW_DAYS))
    since = now - timedelta(days=days)

    signups = _signup_rows(db, since)
    subs = list(db.execute(select(BillingSubscription)).scalars())
    user_ids = {e.user_id for e in signups if e.user_id} | {s.user_id for s in subs}
    users = {u.id: u for u in db.execute(
        select(User).where(User.id.in_(user_ids))).scalars()} if user_ids else {}
    sub_by_user = {s.user_id: s for s in subs}
    # Every purchaser's own sign-up row, even when it is older than the window.
    first_signup: dict = {}
    if sub_by_user:
        for e in db.execute(
            select(AccountSecurityEvent)
            .where(AccountSecurityEvent.event == signup_watch.EVENT_SIGNUP,
                   AccountSecurityEvent.user_id.in_(list(sub_by_user)))
            .order_by(AccountSecurityEvent.ts)
        ).scalars():
            first_signup.setdefault(e.user_id, e)

    purchases = sorted(
        (s for s in subs if (purchased_at(s) or since) >= since),
        key=lambda s: purchased_at(s) or since, reverse=True)

    mrr: dict[str, int] = defaultdict(int)
    for s in subs:
        cents = monthly_cents(s)
        if is_paying(s) and cents is not None:
            mrr[(s.price_currency or "usd").upper()] += cents

    window_user_ids = {e.user_id for e in signups if e.user_id}
    converted = sum(1 for uid in window_user_ids
                    if uid in sub_by_user and not sub_by_user[uid].is_stub
                    and sub_by_user[uid].status in (*PAYING, TRIALING))
    verified = sum(1 for uid in window_user_ids
                   if users.get(uid) is not None and users[uid].email_verified)
    refused = int(db.execute(
        select(func.count(AccountSecurityEvent.id))
        .where(AccountSecurityEvent.event.in_(signup_watch.SIGNUP_EVENTS[1:]),
               AccountSecurityEvent.ts >= since)
    ).scalar() or 0)

    daily_signups = Counter(_aware(e.ts).date().isoformat() for e in signups)
    daily_purchases = Counter(purchased_at(s).date().isoformat()
                              for s in purchases if not s.is_stub)
    series = []
    for i in range(min(days, 90) - 1, -1, -1):
        day = (now - timedelta(days=i)).date().isoformat()
        series.append({"date": day, "signups": daily_signups.get(day, 0),
                       "purchases": daily_purchases.get(day, 0)})

    return {
        "generated_at": now.isoformat(),
        "window_days": days,
        "totals": {
            "accounts": int(db.execute(select(func.count(User.id))
                                       .where(User.password_hash.isnot(None))).scalar() or 0),
            "signups_24h": _count_signups(db, now - timedelta(hours=24)),
            "signups_7d": _count_signups(db, now - timedelta(days=7)),
            "signups_window": len(signups),
            "verified_in_window": verified,
            "refused_attempts_window": refused,
            "paying_subscriptions": sum(1 for s in subs if is_paying(s)),
            "trialing_subscriptions": sum(1 for s in subs
                                          if s.status == TRIALING and not s.is_stub),
            "purchases_window": sum(1 for s in purchases if not s.is_stub),
            "canceled_window": sum(1 for s in subs if s.canceled_at
                                   and _aware(s.canceled_at) >= since),
            "converted_in_window": converted,
            "mrr_cents_by_currency": dict(sorted(mrr.items())),
        },
        "top_countries": Counter(e.country or "unknown" for e in signups).most_common(10),
        "top_sources": Counter(signup_watch.source_label(_source_of(e))
                               for e in signups).most_common(10),
        "daily": series,
        "signups": [_signup_out(e, users.get(e.user_id), sub_by_user.get(e.user_id))
                    for e in signups[:MAX_ROWS]],
        "purchases": [_purchase_out(s, users.get(s.user_id), first_signup.get(s.user_id))
                      for s in purchases[:MAX_ROWS]],
    }
