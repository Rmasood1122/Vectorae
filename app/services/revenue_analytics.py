"""Revenue analytics (Feature Group 3): what each campaign earned, what it
cost, and what a meeting and a closed deal cost to get.

REVENUE   won deals (Deal.stage = won), dated by close_date (else the day
          they were last updated), attributed by Deal.strategy_id.
MEETINGS  distinct leads with a BOOKED outcome in the period.
COST      four sources, all integer cents:
  direct     CampaignCost rows recorded against the campaign
  shared     CampaignCost rows with no campaign (subscriptions, tools),
             spread across campaigns by their share of sends in the period
  api        metered Claude/OpenAI spend (usage_meter / api_usage)
  voice      AI-call minutes x the admin's voice_cost_per_minute
  platform   Vectorae's own pay-per-meeting fees (billable_meetings, Section
             E): pending and charged meetings, by the day they were booked.
             Waived, disputed and failed meetings are not a cost.

CURRENCY
The report is in one currency: the user's most common deal currency (USD if
there are no deals). Deals and recorded costs in any other currency are left
out and COUNTED in `notes`, never silently converted -- an exchange rate
nobody chose is a number nobody can defend. API and voice costs are priced in
USD, so they are included only when the report currency is USD (and flagged
in `notes` when not).

ROI = (revenue - cost) / cost. NULL, not infinity, when there is no cost.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.db.models import (
    ApiUsage,
    BillableMeeting,
    Call,
    CampaignCost,
    Deal,
    DealStage,
    Lead,
    Outcome,
    OutcomeEvent,
    Product,
    Strategy,
)

MICROS_PER_CENT = 10_000


def _day(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _month(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _months(start: date, end: date) -> list[str]:
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def primary_currency(session: Session, user_id) -> str:
    counts = Counter(session.execute(
        select(Deal.currency).where(Deal.user_id == user_id)
    ).scalars().all())
    return counts.most_common(1)[0][0] if counts else "USD"


def _per(cost: int, n: int) -> int | None:
    return round(cost / n) if n else None


def _roi(revenue: int, cost: int) -> float | None:
    return round((revenue - cost) / cost, 4) if cost else None


def report(session: Session, user_id, date_from: date, date_to: date) -> dict:
    from app.services import system_settings  # noqa: PLC0415

    start = datetime.combine(date_from, time.min, tzinfo=timezone.utc)
    end = datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=timezone.utc)
    currency = primary_currency(session, user_id)
    usd = currency == "USD"

    owned = session.execute(
        select(Strategy.id, Product.name, Strategy.campaign_state)
        .join(Product, Product.id == Strategy.product_id)
        .where(Product.user_id == user_id)
        .order_by(Strategy.created_at)
    ).all()
    ids = [sid for sid, _, _ in owned]

    def _counts(event: OutcomeEvent, distinct: bool) -> dict:
        if not ids:
            return {}
        col = func.count(func.distinct(Outcome.lead_id)) if distinct else func.count(Outcome.id)
        return dict(session.execute(
            select(Lead.strategy_id, col).join(Lead, Lead.id == Outcome.lead_id)
            .where(Lead.strategy_id.in_(ids), Outcome.event == event,
                   Outcome.ts >= start, Outcome.ts < end)
            .group_by(Lead.strategy_id)
        ).all())

    sends = _counts(OutcomeEvent.SENT, distinct=False)
    meetings = _counts(OutcomeEvent.BOOKED, distinct=True)

    # ---- revenue ---------------------------------------------------------
    revenue_items: list[tuple[date, object, int]] = []
    won_count: Counter = Counter()
    excluded_deals = 0
    for deal in session.execute(
        select(Deal).where(Deal.user_id == user_id, Deal.stage == DealStage.WON)
    ).scalars():
        day = _day(deal.close_date) or _day(deal.updated_at)
        if day is None or not (date_from <= day <= date_to):
            continue
        if deal.currency != currency:
            excluded_deals += 1
            continue
        revenue_items.append((day, deal.strategy_id, deal.value_cents or 0))
        won_count[deal.strategy_id] += 1
    open_pipeline = session.execute(
        select(func.coalesce(func.sum(Deal.value_cents), 0))
        .where(Deal.user_id == user_id, Deal.stage == DealStage.OPEN, Deal.currency == currency)
    ).scalar_one()

    # ---- costs -----------------------------------------------------------
    cost_items: list[tuple[date, object, int, str]] = []   # (day, strategy|None, cents, kind)
    excluded_costs = 0
    for cost in session.execute(
        select(CampaignCost).where(CampaignCost.user_id == user_id,
                                   CampaignCost.incurred_on >= date_from,
                                   CampaignCost.incurred_on <= date_to)
    ).scalars():
        if cost.currency != currency:
            excluded_costs += 1
            continue
        cost_items.append((_day(cost.incurred_on), cost.strategy_id, cost.amount_cents or 0,
                           "direct" if cost.strategy_id else "shared"))

    api_usd_cents = voice_usd_cents = 0
    usage_filter = or_(ApiUsage.strategy_id.in_(ids) if ids else False,
                       and_(ApiUsage.strategy_id.is_(None), ApiUsage.user_id == user_id))
    for day, sid, micros in session.execute(
        select(func.date(ApiUsage.ts), ApiUsage.strategy_id, func.sum(ApiUsage.cost_micros))
        .where(usage_filter, ApiUsage.ts >= start, ApiUsage.ts < end)
        .group_by(func.date(ApiUsage.ts), ApiUsage.strategy_id)
    ).all():
        cents = round((micros or 0) / MICROS_PER_CENT)
        api_usd_cents += cents
        if usd:
            cost_items.append((_day(day), sid, cents, "api" if sid else "shared"))

    rate = system_settings.get(session, "voice_cost_per_minute")
    if ids:
        for day, sid, seconds in session.execute(
            select(func.date(Call.created_at), Call.strategy_id,
                   func.sum(Call.duration_seconds))
            .where(Call.strategy_id.in_(ids), Call.created_at >= start, Call.created_at < end)
            .group_by(func.date(Call.created_at), Call.strategy_id)
        ).all():
            cents = round((seconds or 0) / 60 * rate * 100)
            voice_usd_cents += cents
            if usd:
                cost_items.append((_day(day), sid, cents, "voice"))

    # Section E: pay-per-meeting fees are priced in USD like API and voice
    # spend, so the same currency rule applies.
    platform_usd_cents = 0
    for day, sid, cents in session.execute(
        select(func.date(BillableMeeting.occurred_at), BillableMeeting.strategy_id,
               func.sum(BillableMeeting.amount_cents))
        .where(BillableMeeting.user_id == user_id,
               BillableMeeting.status.in_(("pending", "charged")),
               BillableMeeting.occurred_at >= start, BillableMeeting.occurred_at < end)
        .group_by(func.date(BillableMeeting.occurred_at), BillableMeeting.strategy_id)
    ).all():
        platform_usd_cents += int(cents or 0)
        if usd:
            cost_items.append((_day(day), sid, int(cents or 0),
                               "platform" if sid else "shared"))

    # ---- per campaign ----------------------------------------------------
    total_sends = sum(sends.values())
    shared_total = sum(c for _, sid, c, kind in cost_items if kind == "shared")
    by_kind: dict = {}
    for _, sid, cents, kind in cost_items:
        if kind != "shared":
            by_kind.setdefault(sid, Counter())[kind] += cents
    revenue_by: Counter = Counter()
    for _, sid, cents in revenue_items:
        revenue_by[sid] += cents

    campaigns = []
    allocated_total = 0
    for sid, name, state in owned:
        kinds = by_kind.get(sid, Counter())
        allocated = round(shared_total * sends.get(sid, 0) / total_sends) if total_sends else 0
        allocated_total += allocated
        total_cost = (kinds["direct"] + kinds["api"] + kinds["voice"] + kinds["platform"]
                      + allocated)
        revenue = revenue_by.get(sid, 0)
        booked = meetings.get(sid, 0)
        won = won_count.get(sid, 0)
        campaigns.append({
            "strategy_id": str(sid), "name": name, "campaign_state": state,
            "sends": sends.get(sid, 0), "meetings": booked, "deals_won": won,
            "revenue_cents": revenue,
            "direct_cost_cents": kinds["direct"], "api_cost_cents": kinds["api"],
            "voice_cost_cents": kinds["voice"], "platform_cost_cents": kinds["platform"],
            "allocated_cost_cents": allocated,
            "total_cost_cents": total_cost,
            "cost_per_meeting_cents": _per(total_cost, booked),
            "cost_per_deal_cents": _per(total_cost, won),
            "roi": _roi(revenue, total_cost),
        })

    unattributed_revenue = revenue_by.get(None, 0)
    unallocated = shared_total - allocated_total
    total_revenue = sum(revenue_by.values())
    total_cost = sum(c["total_cost_cents"] for c in campaigns) + unallocated
    total_meetings = sum(meetings.values())
    total_won = sum(won_count.values())

    monthly = {m: {"month": m, "revenue_cents": 0, "cost_cents": 0}
               for m in _months(date_from, date_to)}
    for day, _, cents in revenue_items:
        if _month(day) in monthly:
            monthly[_month(day)]["revenue_cents"] += cents
    for day, _, cents, _ in cost_items:
        if day and _month(day) in monthly:
            monthly[_month(day)]["cost_cents"] += cents

    return {
        "currency": currency,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "campaigns": campaigns,
        "totals": {
            "revenue_cents": total_revenue, "cost_cents": total_cost,
            "meetings": total_meetings, "deals_won": total_won,
            "sends": total_sends,
            "cost_per_meeting_cents": _per(total_cost, total_meetings),
            "cost_per_deal_cents": _per(total_cost, total_won),
            "roi": _roi(total_revenue, total_cost),
            "open_pipeline_cents": int(open_pipeline or 0),
            "unattributed_revenue_cents": unattributed_revenue,
            "unallocated_cost_cents": unallocated,
        },
        "monthly": list(monthly.values()),
        "notes": {
            "excluded_deals": excluded_deals,
            "excluded_costs": excluded_costs,
            "usd_costs_excluded": not usd and (api_usd_cents + voice_usd_cents
                                               + platform_usd_cents) > 0,
            "api_cost_usd_cents": api_usd_cents,
            "voice_cost_usd_cents": voice_usd_cents,
            "platform_fee_usd_cents": platform_usd_cents,
        },
        "api_usage": usage_breakdown(session, user_id, ids, start, end),
    }


def usage_breakdown(session: Session, user_id, ids, start: datetime, end: datetime) -> list[dict]:
    usage_filter = or_(ApiUsage.strategy_id.in_(ids) if ids else False,
                       and_(ApiUsage.strategy_id.is_(None), ApiUsage.user_id == user_id))
    rows = session.execute(
        select(ApiUsage.provider, ApiUsage.purpose, func.sum(ApiUsage.calls),
               func.sum(ApiUsage.input_tokens), func.sum(ApiUsage.output_tokens),
               func.sum(ApiUsage.cost_micros))
        .where(usage_filter, ApiUsage.ts >= start, ApiUsage.ts < end)
        .group_by(ApiUsage.provider, ApiUsage.purpose)
        .order_by(func.sum(ApiUsage.cost_micros).desc())
    ).all()
    return [{"provider": p, "purpose": purpose, "calls": int(calls or 0),
             "input_tokens": int(tin or 0), "output_tokens": int(tout or 0),
             "cost_usd_cents": round((micros or 0) / MICROS_PER_CENT)}
            for p, purpose, calls, tin, tout, micros in rows]
