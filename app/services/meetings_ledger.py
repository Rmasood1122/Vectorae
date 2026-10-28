"""F08 -- Meetings-per-Dollar Ledger (docs/features/f08-meetings-per-dollar.md).

Buyers judge outbound on meetings per dollar and pipeline, not on emails sent.
Per campaign this answers: what did it cost, how many qualified conversations,
booked meetings and held meetings did that buy, what did each cost -- and how
sure are we that the campaign earned them?

BUILT ON, NOT BESIDE, TWO EXISTING MODULES
  cost        revenue_analytics.report() -- recorded costs, metered AI, call
              minutes, pay-per-meeting fees and account-wide costs spread by
              sends. This module only DECOMPOSES its per-campaign total and
              adds one explicit, admin-set infrastructure line.
  confidence  the attribution ledger (attribution_entries): every counted
              booking or positive reply carries that ledger's method and
              confidence; an outcome the sweep has not reached yet is
              "pending", never guessed.

COUNTS ARE DISTINCT LEADS, campaign = the lead's campaign (leads.strategy_id),
exactly like revenue analytics -- so "meetings booked" here IS its "meetings".

INFRASTRUCTURE IS NEVER INVENTED. `meetings_ledger_infra_cents_per_1k_sends`
(US cents per 1,000 sends) defaults to 0, and a ledger with a 0 rate says the
line is not included.

`build_ledger` is the contract F09 (30-Day Pilot Mode) reuses for its day-30
verdict: no flag check, no commit, every query owner-filtered.
"""

from __future__ import annotations

import csv
import io
import unicodedata
import uuid
from collections import Counter
from collections.abc import Iterable
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.db.models import (
    ApiUsage,
    AttributionEntry,
    BillableMeeting,
    Call,
    CampaignCost,
    Deal,
    DealStage,
    Lead,
    Meeting,
    MeetingOutcome,
    MeetingStatus,
    Outcome,
    OutcomeEvent,
)
from app.services import attribution, revenue_analytics

FLAG = "meetings_ledger_enabled"
INFRA_SETTING = "meetings_ledger_infra_cents_per_1k_sends"

PENDING = "pending"
#: Every method an event can carry: the attribution ledger's four, plus
#: "pending" for an outcome the quarter-hourly sweep has not credited yet.
METHODS = (*attribution.METHODS, PENDING)
METHOD_LABELS = {**attribution.METHOD_LABELS,
                 PENDING: "Not attributed yet (the attribution sweep runs every 15 minutes)"}

QUALIFIED, BOOKED, HELD = "qualified_conversation", "meeting_booked", "meeting_held"

DEFINITIONS = {
    QUALIFIED: ("Distinct leads with an interested reply (the attribution ledger's "
                "positive-reply rule) or a booked meeting in the period."),
    BOOKED: "Distinct leads with a booked-meeting outcome in the period.",
    HELD: ("Distinct leads with a meeting marked completed in Vectorae, or a "
           "logged meeting outcome, in the period."),
    "total_cost": ("AI (metered model spend, AI-call minutes, recorded AI costs) + "
                   "data (recorded data costs) + other recorded costs + a share of "
                   "account-wide costs by sends + pay-per-meeting fees + "
                   "infrastructure allocation."),
    "infrastructure": ("Sends x the admin's infrastructure rate (US cents per 1,000 "
                       "sends). 0 means not included."),
    "confidence": ("From the attribution ledger: direct_reply is a fact (1.0), "
                   "thread_match is evidence (0.85), last_touch is a guess that "
                   "decays with time, none means nothing was sent first."),
}

_CSV_UNSAFE = ("=", "+", "-", "@", "\t", "\r")


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _per(cost: int, n: int) -> int | None:
    return round(cost / n) if n else None


def is_enabled(db: Session) -> bool:
    from app.services import system_settings  # noqa: PLC0415

    return bool(system_settings.get(db, FLAG))


def infra_rate(db: Session) -> int:
    from app.services import system_settings  # noqa: PLC0415

    return int(system_settings.get(db, INFRA_SETTING))


# --------------------------------------------------------------------------
# Events
# --------------------------------------------------------------------------


def _attribution(entry: AttributionEntry | None) -> dict:
    if entry is None:
        return {"method": PENDING, "method_label": METHOD_LABELS[PENDING],
                "confidence": None, "is_certain": False, "attribution_id": None}
    return {"method": entry.method,
            "method_label": METHOD_LABELS.get(entry.method, entry.method),
            "confidence": entry.confidence,
            "is_certain": entry.method == attribution.DIRECT_REPLY,
            "attribution_id": str(entry.id)}


def _event(kind: str, strategy_id, lead_id, at: datetime, source: str, outcome_id,
           entry: AttributionEntry | None) -> dict:
    return {"kind": kind, "strategy_id": str(strategy_id), "lead_id": str(lead_id),
            "at": at.isoformat(), "source": source,
            "outcome_id": str(outcome_id) if outcome_id else None,
            **_attribution(entry)}


def _collect_events(db: Session, user_id, owned_ids: list, start: datetime,
                    end: datetime) -> list[dict]:
    """Every counted event in the period: one per (kind, lead), the earliest."""
    if not owned_ids:
        return []

    outcomes = db.execute(
        select(Outcome, Lead.strategy_id)
        .join(Lead, Lead.id == Outcome.lead_id)
        .where(Lead.strategy_id.in_(owned_ids),
               Outcome.event.in_((OutcomeEvent.BOOKED, OutcomeEvent.REPLIED)),
               Outcome.ts >= start, Outcome.ts < end)
    ).all()
    outcomes.sort(key=lambda row: (_aware(row[0].ts), str(row[0].id)))

    # The attribution ledger's verdicts for these outcomes. Owner-filtered,
    # and bounded by the same period: attribution.record() stores the
    # outcome's own timestamp as outcome_at.
    entries = {
        (e.outcome_kind, str(e.outcome_id)): e
        for e in db.execute(
            select(AttributionEntry).where(
                AttributionEntry.user_id == user_id,
                AttributionEntry.outcome_kind.in_((attribution.MEETING_BOOKED,
                                                   attribution.POSITIVE_REPLY)),
                AttributionEntry.outcome_at >= start, AttributionEntry.outcome_at < end)
        ).scalars()
    }

    events: list[dict] = []
    booked_leads: set = set()
    qualified_leads: set = set()
    for outcome, sid in outcomes:
        at = _aware(outcome.ts)
        if outcome.event is OutcomeEvent.BOOKED:
            entry = entries.get((attribution.MEETING_BOOKED, str(outcome.id)))
            if outcome.lead_id not in booked_leads:
                booked_leads.add(outcome.lead_id)
                events.append(_event(BOOKED, sid, outcome.lead_id, at, "booked_outcome",
                                     outcome.id, entry))
            if outcome.lead_id not in qualified_leads:
                qualified_leads.add(outcome.lead_id)
                events.append(_event(QUALIFIED, sid, outcome.lead_id, at, "booked_outcome",
                                     outcome.id, entry))
        elif outcome.lead_id not in qualified_leads \
                and attribution.is_positive_reply(db, outcome):
            qualified_leads.add(outcome.lead_id)
            events.append(_event(QUALIFIED, sid, outcome.lead_id, at, "positive_reply",
                                 outcome.id,
                                 entries.get((attribution.POSITIVE_REPLY, str(outcome.id)))))

    events.extend(_held_events(db, user_id, owned_ids, start, end))
    return events


def _held_events(db: Session, user_id, owned_ids: list, start: datetime,
                 end: datetime) -> list[dict]:
    held: dict = {}   # lead_id -> (at, strategy_id, source)

    def _offer(lead_id, sid, at, source):
        if at is None or not (start <= at < end):
            return
        current = held.get(lead_id)
        if current is None or at < current[0]:
            held[lead_id] = (at, sid, source)

    for meeting, sid in db.execute(
        select(Meeting, Lead.strategy_id)
        .join(Lead, Lead.id == Meeting.lead_id)
        .where(Meeting.host_user_id == user_id,
               Meeting.status == MeetingStatus.COMPLETED,
               Lead.strategy_id.in_(owned_ids),
               or_(and_(Meeting.actual_start_at.isnot(None),
                        Meeting.actual_start_at >= start, Meeting.actual_start_at < end),
                   and_(Meeting.actual_start_at.is_(None),
                        Meeting.start_at >= start, Meeting.start_at < end)))
    ).all():
        _offer(meeting.lead_id, sid,
               _aware(meeting.actual_start_at) or _aware(meeting.start_at), "meeting_completed")

    for logged, sid in db.execute(
        select(MeetingOutcome, Lead.strategy_id)
        .join(Lead, Lead.id == MeetingOutcome.lead_id)
        .where(MeetingOutcome.user_id == user_id,
               Lead.strategy_id.in_(owned_ids),
               MeetingOutcome.created_at >= start, MeetingOutcome.created_at < end)
    ).all():
        _offer(logged.lead_id, sid, _aware(logged.created_at), "meeting_outcome_logged")

    if not held:
        return []

    # A held meeting inherits the attribution of the lead's latest booking at
    # or before it -- the booking is what the campaign earned.
    bookings: dict = {}
    for entry in db.execute(
        select(AttributionEntry).where(
            AttributionEntry.user_id == user_id,
            AttributionEntry.outcome_kind == attribution.MEETING_BOOKED,
            AttributionEntry.lead_id.in_(list(held)))
    ).scalars():
        bookings.setdefault(entry.lead_id, []).append(entry)

    out = []
    for lead_id, (at, sid, source) in held.items():
        prior = [e for e in bookings.get(lead_id, []) if _aware(e.outcome_at) <= at]
        entry = max(prior, key=lambda e: _aware(e.outcome_at)) if prior else None
        out.append(_event(HELD, sid, lead_id, at, source,
                          entry.outcome_id if entry else None, entry))
    return out


# --------------------------------------------------------------------------
# Costs
# --------------------------------------------------------------------------


def _recorded_by_category(db: Session, user_id, owned_ids: list, date_from: date,
                          date_to: date, currency: str) -> dict:
    """{strategy_id: Counter(category -> cents)} for costs recorded against a
    campaign. Same rows revenue analytics counts as `direct`."""
    out: dict = {}
    if not owned_ids:
        return out
    for sid, category, cents in db.execute(
        select(CampaignCost.strategy_id, CampaignCost.category,
               func.sum(CampaignCost.amount_cents))
        .where(CampaignCost.user_id == user_id,
               CampaignCost.strategy_id.in_(owned_ids),
               CampaignCost.currency == currency,
               CampaignCost.incurred_on >= date_from,
               CampaignCost.incurred_on <= date_to)
        .group_by(CampaignCost.strategy_id, CampaignCost.category)
    ).all():
        out.setdefault(str(sid), Counter())[category or "other"] += int(cents or 0)
    return out


def _open_pipeline(db: Session, user_id, currency: str) -> Counter:
    return Counter({str(sid): int(cents or 0) for sid, cents in db.execute(
        select(Deal.strategy_id, func.sum(Deal.value_cents))
        .where(Deal.user_id == user_id, Deal.stage == DealStage.OPEN,
               Deal.currency == currency, Deal.strategy_id.isnot(None))
        .group_by(Deal.strategy_id)
    ).all()})


def _reconcile(db: Session, user_id, owned_ids: list, date_from: date, date_to: date,
               start: datetime, end: datetime, currency: str, ledger_cost: int) -> dict:
    """The raw tables, summed once each, against the ledger -- account-wide."""
    from app.services import system_settings  # noqa: PLC0415

    usd = currency == "USD"
    recorded = int(db.execute(
        select(func.coalesce(func.sum(CampaignCost.amount_cents), 0))
        .where(CampaignCost.user_id == user_id, CampaignCost.currency == currency,
               CampaignCost.incurred_on >= date_from, CampaignCost.incurred_on <= date_to)
    ).scalar_one() or 0)

    usage_filter = or_(ApiUsage.strategy_id.in_(owned_ids) if owned_ids else False,
                       and_(ApiUsage.strategy_id.is_(None), ApiUsage.user_id == user_id))
    api_micros = int(db.execute(
        select(func.coalesce(func.sum(ApiUsage.cost_micros), 0))
        .where(usage_filter, ApiUsage.ts >= start, ApiUsage.ts < end)
    ).scalar_one() or 0)
    api_groups = len(db.execute(
        select(func.date(ApiUsage.ts), ApiUsage.strategy_id)
        .where(usage_filter, ApiUsage.ts >= start, ApiUsage.ts < end)
        .group_by(func.date(ApiUsage.ts), ApiUsage.strategy_id)
    ).all())

    voice_seconds, voice_groups = 0, 0
    if owned_ids:
        voice_seconds = int(db.execute(
            select(func.coalesce(func.sum(Call.duration_seconds), 0))
            .where(Call.strategy_id.in_(owned_ids), Call.created_at >= start,
                   Call.created_at < end)
        ).scalar_one() or 0)
        voice_groups = len(db.execute(
            select(func.date(Call.created_at), Call.strategy_id)
            .where(Call.strategy_id.in_(owned_ids), Call.created_at >= start,
                   Call.created_at < end)
            .group_by(func.date(Call.created_at), Call.strategy_id)
        ).all())
    rate = system_settings.get(db, "voice_cost_per_minute")

    platform = int(db.execute(
        select(func.coalesce(func.sum(BillableMeeting.amount_cents), 0))
        .where(BillableMeeting.user_id == user_id,
               BillableMeeting.status.in_(("pending", "charged")),
               BillableMeeting.occurred_at >= start, BillableMeeting.occurred_at < end)
    ).scalar_one() or 0)

    api_cents = round(api_micros / revenue_analytics.MICROS_PER_CENT)
    voice_cents = round(voice_seconds / 60 * rate * 100)
    raw = recorded + ((api_cents + voice_cents + platform) if usd else 0)
    groups = (api_groups + voice_groups) if usd else 0
    tolerance = groups // 2 + 1 if groups else 0
    difference = ledger_cost - raw
    return {
        "raw_recorded_cost_cents": recorded,
        "raw_api_cost_micros": api_micros,
        "raw_call_seconds": voice_seconds,
        "raw_platform_fee_cents": platform,
        "raw_cost_cents": raw,
        "ledger_cost_cents": ledger_cost,
        "difference_cents": difference,
        "rounding_tolerance_cents": tolerance,
        "balanced": abs(difference) <= tolerance,
    }


# --------------------------------------------------------------------------
# The ledger
# --------------------------------------------------------------------------


def _empty_mix() -> dict:
    return {name: 0 for name in METHODS}


def _row_metrics(row: dict) -> dict:
    cost = row["total_cost_cents"]
    booked = row["meetings_booked"]
    row["cost_per_qualified_conversation_cents"] = _per(cost, row["qualified_conversations"])
    row["cost_per_meeting_booked_cents"] = _per(cost, booked)
    row["cost_per_meeting_held_cents"] = _per(cost, row["meetings_held"])
    # Booked meetings per 1,000 units of the report currency (100,000 cents).
    row["meetings_booked_per_1k_spend"] = (round(booked * 100_000 / cost, 2) if cost else None)
    mix = row["booked_by_method"]
    attributed = booked - mix[PENDING]
    row["booked_certain"] = mix[attribution.DIRECT_REPLY]
    row["booked_attributed"] = attributed
    return row


def build_ledger(db: Session, user_id, date_from: date, date_to: date, *,
                 strategy_ids: Iterable | None = None,
                 now: datetime | None = None) -> dict:
    """The meetings-per-dollar ledger for one account and period.

    `strategy_ids` narrows the campaigns shown (ids the account does not own
    are dropped silently); costs are still allocated across every campaign,
    and `reconciliation` is always account-wide. Does not check the feature
    flag and does not commit."""
    start = datetime.combine(date_from, time.min, tzinfo=timezone.utc)
    end = datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=timezone.utc)

    base = revenue_analytics.report(db, user_id, date_from, date_to)
    currency = base["currency"]
    usd = currency == "USD"
    owned_ids = [c["strategy_id"] for c in base["campaigns"]]
    owned_uuid = [uuid.UUID(sid) for sid in owned_ids]

    selected = None
    if strategy_ids is not None:
        wanted = {str(s) for s in strategy_ids}
        selected = [sid for sid in owned_ids if sid in wanted]

    rate = infra_rate(db)
    recorded = _recorded_by_category(db, user_id, owned_uuid, date_from, date_to, currency)
    pipeline = _open_pipeline(db, user_id, currency)
    events = _collect_events(db, user_id, owned_uuid, start, end)

    counts: dict = {}
    mixes: dict = {}
    for ev in events:
        counts.setdefault(ev["strategy_id"], Counter())[ev["kind"]] += 1
        if ev["kind"] == BOOKED:
            mix = mixes.setdefault(ev["strategy_id"], _empty_mix())
            mix[ev["method"]] = mix.get(ev["method"], 0) + 1
    confidences: dict = {}
    for ev in events:
        if ev["kind"] == BOOKED and ev["confidence"] is not None:
            confidences.setdefault(ev["strategy_id"], []).append(ev["confidence"])

    campaigns = []
    decomposition_ok = True
    infra_excluded_cents = 0
    for c in base["campaigns"]:
        sid = c["strategy_id"]
        cats = recorded.get(sid, Counter())
        data_cents = cats.get("data", 0)
        recorded_ai = cats.get("ai", 0)
        other_cents = sum(v for k, v in cats.items() if k not in ("data", "ai"))
        ai_cents = c["api_cost_cents"] + c["voice_cost_cents"] + recorded_ai
        infra_cents = round(c["sends"] * rate / 1000)
        if not usd:
            infra_excluded_cents += infra_cents
            infra_cents = 0
        parts = (ai_cents + data_cents + other_cents + c["allocated_cost_cents"]
                 + c["platform_cost_cents"])
        # Two independent checks: the per-category query agrees with revenue
        # analytics' `direct` figure, and the lines add up to its total.
        decomposition_ok = (decomposition_ok
                            and sum(cats.values()) == c["direct_cost_cents"]
                            and parts == c["total_cost_cents"])
        n = counts.get(sid, Counter())
        conf = confidences.get(sid, [])
        campaigns.append(_row_metrics({
            "strategy_id": sid, "name": c["name"], "campaign_state": c["campaign_state"],
            "sends": c["sends"],
            "qualified_conversations": n[QUALIFIED],
            "meetings_booked": n[BOOKED],
            "meetings_held": n[HELD],
            "booked_by_method": mixes.get(sid, _empty_mix()),
            "booked_mean_confidence": round(sum(conf) / len(conf), 3) if conf else None,
            "ai_cost_cents": ai_cents,
            "data_cost_cents": data_cents,
            "other_recorded_cost_cents": other_cents,
            "shared_cost_cents": c["allocated_cost_cents"],
            "platform_fee_cents": c["platform_cost_cents"],
            "infrastructure_cost_cents": infra_cents,
            "total_cost_cents": c["total_cost_cents"] + infra_cents,
            "revenue_cents": c["revenue_cents"],
            "deals_won": c["deals_won"],
            "open_pipeline_cents": pipeline.get(sid, 0),
        }))

    ledger_cost_ex_infra = (sum(c["total_cost_cents"] - c["infrastructure_cost_cents"]
                                for c in campaigns)
                            + base["totals"]["unallocated_cost_cents"])
    reconciliation = _reconcile(db, user_id, owned_uuid, date_from, date_to, start, end,
                                currency, ledger_cost_ex_infra)
    booked_events = [e for e in events if e["kind"] == BOOKED]
    reconciliation.update({
        "decomposition_balanced": decomposition_ok,
        "revenue_analytics_cost_cents": base["totals"]["cost_cents"],
        "booked_outcome_leads": _raw_booked_leads(db, owned_uuid, start, end),
        "ledger_meetings_booked": len(booked_events),
        "revenue_analytics_meetings": base["totals"]["meetings"],
        "attributed": sum(1 for e in booked_events if e["method"] != PENDING),
        "pending": sum(1 for e in booked_events if e["method"] == PENDING),
    })

    shown = campaigns if selected is None else [c for c in campaigns
                                                if c["strategy_id"] in set(selected)]
    shown_ids = {c["strategy_id"] for c in shown}
    unallocated = base["totals"]["unallocated_cost_cents"] if selected is None else 0
    totals_mix = _empty_mix()
    for c in shown:
        for name, value in c["booked_by_method"].items():
            totals_mix[name] = totals_mix.get(name, 0) + value
    all_conf = [e["confidence"] for e in booked_events
                if e["strategy_id"] in shown_ids and e["confidence"] is not None]
    totals = _row_metrics({
        "sends": sum(c["sends"] for c in shown),
        "qualified_conversations": sum(c["qualified_conversations"] for c in shown),
        "meetings_booked": sum(c["meetings_booked"] for c in shown),
        "meetings_held": sum(c["meetings_held"] for c in shown),
        "booked_by_method": totals_mix,
        "booked_mean_confidence": (round(sum(all_conf) / len(all_conf), 3)
                                   if all_conf else None),
        "ai_cost_cents": sum(c["ai_cost_cents"] for c in shown),
        "data_cost_cents": sum(c["data_cost_cents"] for c in shown),
        "other_recorded_cost_cents": sum(c["other_recorded_cost_cents"] for c in shown),
        "shared_cost_cents": sum(c["shared_cost_cents"] for c in shown),
        "platform_fee_cents": sum(c["platform_fee_cents"] for c in shown),
        "infrastructure_cost_cents": sum(c["infrastructure_cost_cents"] for c in shown),
        "unallocated_cost_cents": unallocated,
        "total_cost_cents": sum(c["total_cost_cents"] for c in shown) + unallocated,
        "revenue_cents": sum(c["revenue_cents"] for c in shown),
        "deals_won": sum(c["deals_won"] for c in shown),
        "open_pipeline_cents": sum(c["open_pipeline_cents"] for c in shown),
    })

    names = {c["strategy_id"]: c["name"] for c in campaigns}
    shown_events = sorted(
        (dict(e, campaign=names.get(e["strategy_id"], "")) for e in events
         if e["strategy_id"] in shown_ids),
        key=lambda e: (e["campaign"], e["strategy_id"], e["at"], e["kind"]))
    _add_companies(db, shown_events)

    return {
        "version": "leadpilot.meetings-ledger.v1",
        "generated_at": (now or datetime.now(timezone.utc)).isoformat(),
        "currency": currency,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "strategy_ids": selected,
        "infrastructure": {
            "cents_per_1k_sends": rate,
            "included": rate > 0 and usd,
            "excluded_non_usd_cents": infra_excluded_cents,
            "note": (f"{rate} US cents per 1,000 sends (admin setting {INFRA_SETTING})"
                     if rate > 0 else
                     f"Not included: the admin rate {INFRA_SETTING} is 0"),
        },
        "campaigns": shown,
        "totals": totals,
        "events": shown_events,
        "reconciliation": reconciliation,
        "notes": base["notes"],
        "method_labels": METHOD_LABELS,
        "definitions": DEFINITIONS,
    }


def _raw_booked_leads(db: Session, owned_ids: list, start: datetime, end: datetime) -> int:
    if not owned_ids:
        return 0
    return int(db.execute(
        select(func.count(func.distinct(Outcome.lead_id)))
        .join(Lead, Lead.id == Outcome.lead_id)
        .where(Lead.strategy_id.in_(owned_ids), Outcome.event == OutcomeEvent.BOOKED,
               Outcome.ts >= start, Outcome.ts < end)
    ).scalar_one() or 0)


def _add_companies(db: Session, events: list[dict]) -> None:
    """Company only -- no names, no emails: an export of this ledger is the
    kind of file that gets forwarded (data minimisation)."""
    ids = list({uuid.UUID(e["lead_id"]) for e in events})
    companies: dict = {}
    for chunk in range(0, len(ids), 500):
        for lead_id, company in db.execute(
            select(Lead.id, Lead.company).where(Lead.id.in_(ids[chunk:chunk + 500]))
        ).all():
            companies[str(lead_id)] = company
    for e in events:
        e["company"] = companies.get(e["lead_id"])


# --------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------


def _cell(value) -> object:
    """Spreadsheet formula-injection guard for text cells."""
    if isinstance(value, str) and value.startswith(_CSV_UNSAFE):
        return "'" + value
    return value


def _money(cents: int | None) -> str:
    return "" if cents is None else f"{cents / 100:.2f}"


CAMPAIGN_COLUMNS = (
    "campaign", "strategy_id", "currency", "sends", "qualified_conversations",
    "meetings_booked", "meetings_held", "total_cost", "ai_cost", "data_cost",
    "other_recorded_cost", "shared_cost", "platform_fees", "infrastructure_cost",
    "cost_per_qualified_conversation", "cost_per_meeting_booked",
    "cost_per_meeting_held", "meetings_booked_per_1k_spend",
    "booked_direct_reply", "booked_thread_match", "booked_last_touch", "booked_none",
    "booked_pending", "booked_mean_confidence", "revenue", "deals_won",
    "open_pipeline_now",
)

EVENT_COLUMNS = ("campaign", "strategy_id", "kind", "at", "lead_id", "company",
                 "source", "attribution_method", "confidence", "is_certain", "outcome_id")


def _campaign_csv_row(name: str, sid: str, currency: str, row: dict) -> list:
    mix = row["booked_by_method"]
    return [
        _cell(name), sid, currency, row["sends"], row["qualified_conversations"],
        row["meetings_booked"], row["meetings_held"], _money(row["total_cost_cents"]),
        _money(row["ai_cost_cents"]), _money(row["data_cost_cents"]),
        _money(row["other_recorded_cost_cents"]), _money(row["shared_cost_cents"]),
        _money(row["platform_fee_cents"]), _money(row["infrastructure_cost_cents"]),
        _money(row["cost_per_qualified_conversation_cents"]),
        _money(row["cost_per_meeting_booked_cents"]),
        _money(row["cost_per_meeting_held_cents"]),
        "" if row["meetings_booked_per_1k_spend"] is None else row["meetings_booked_per_1k_spend"],
        mix[attribution.DIRECT_REPLY], mix[attribution.THREAD_MATCH],
        mix[attribution.LAST_TOUCH], mix[attribution.NONE], mix[PENDING],
        "" if row["booked_mean_confidence"] is None else row["booked_mean_confidence"],
        _money(row["revenue_cents"]), row["deals_won"], _money(row["open_pipeline_cents"]),
    ]


def to_csv(ledger: dict, view: str = "campaigns") -> str:
    """`campaigns`: one row per campaign plus TOTAL. `events`: one row per
    counted event with its attribution. Amounts in currency units."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    if view == "events":
        writer.writerow(EVENT_COLUMNS)
        for e in ledger["events"]:
            writer.writerow([
                _cell(e["campaign"]), e["strategy_id"], e["kind"], e["at"], e["lead_id"],
                _cell(e.get("company") or ""), e["source"], e["method"],
                "" if e["confidence"] is None else e["confidence"],
                "yes" if e["is_certain"] else "no", e["outcome_id"] or "",
            ])
        return buffer.getvalue()
    if view != "campaigns":
        raise ValueError(f"unknown view {view!r}")
    currency = ledger["currency"]
    writer.writerow(CAMPAIGN_COLUMNS)
    for c in ledger["campaigns"]:
        writer.writerow(_campaign_csv_row(c["name"], c["strategy_id"], currency, c))
    writer.writerow(_campaign_csv_row("TOTAL", "", currency, ledger["totals"]))
    return buffer.getvalue()


def _fmt(cents: int | None, currency: str) -> str:
    return "-" if cents is None else f"{cents / 100:,.2f} {currency}"


def _ascii(text: str) -> str:
    """Campaign names are user text; the PDF fallback font is a bitmap face
    that cannot draw everything, so fold accents and drop the rest."""
    folded = unicodedata.normalize("NFKD", text or "")
    return folded.encode("ascii", "ignore").decode("ascii")


def _mix_text(mix: dict) -> str:
    return ", ".join(f"{name} {mix.get(name, 0)}" for name in METHODS)


def to_pdf(ledger: dict) -> bytes:
    """A readable one-to-several page summary. ASCII only (see _ascii)."""
    from app.services.audit_trail import render_lines_pdf  # noqa: PLC0415

    return render_lines_pdf(pdf_lines(ledger))


def pdf_lines(ledger: dict) -> list[tuple[str, str]]:
    """The (text, style) lines of `to_pdf`. Public so F09's verdict PDF can
    put its verdict above the same ledger body without re-deriving it. Uses
    only the aggregate keys (not `events`), so a stored snapshot renders."""
    cur = ledger["currency"]
    t = ledger["totals"]
    rec = ledger["reconciliation"]
    lines: list[tuple[str, str]] = []

    def add(text: str, style: str = "body") -> None:
        lines.append((text, style))

    def money(cents: int | None) -> str:
        return _fmt(cents, cur)

    add("LeadPilot meetings-per-dollar ledger", "title")
    add(f"Period {ledger['date_from']} to {ledger['date_to']}  |  currency {cur}  |  "
        f"generated {ledger['generated_at'][:19]} UTC")
    add(f"Infrastructure: {ledger['infrastructure']['note']}")
    add("")
    add("Totals", "head")
    add(f"Total cost {money(t['total_cost_cents'])}  |  sends {t['sends']}")
    add(f"Qualified conversations {t['qualified_conversations']}  |  meetings booked "
        f"{t['meetings_booked']}  |  meetings held {t['meetings_held']}")
    add(f"Cost per qualified conversation {money(t['cost_per_qualified_conversation_cents'])}"
        f"  |  per meeting booked {money(t['cost_per_meeting_booked_cents'])}"
        f"  |  per meeting held {money(t['cost_per_meeting_held_cents'])}")
    add(f"Booked meetings by attribution: {_mix_text(t['booked_by_method'])}"
        f"  (certain = direct_reply = {t['booked_certain']})")
    add(f"Revenue {money(t['revenue_cents'])} from {t['deals_won']} won deal(s)  |  "
        f"open pipeline now {money(t['open_pipeline_cents'])}")
    if t["unallocated_cost_cents"]:
        add(f"Includes {money(t['unallocated_cost_cents'])} account-wide cost not allocated "
            "to a campaign (no sends in the period).")
    add("")
    add("By campaign", "head")
    for c in ledger["campaigns"]:
        add(f"{_ascii(c['name'] or 'Campaign')[:70]}  ({c['campaign_state'] or 'n/a'})", "head")
        add(f"  cost {money(c['total_cost_cents'])} = AI {money(c['ai_cost_cents'])}"
            f" + data {money(c['data_cost_cents'])}"
            f" + other {money(c['other_recorded_cost_cents'])}"
            f" + shared {money(c['shared_cost_cents'])}")
        add(f"         + platform fees {money(c['platform_fee_cents'])}"
            f" + infrastructure {money(c['infrastructure_cost_cents'])}")
        add(f"  sends {c['sends']}  |  qualified {c['qualified_conversations']}  |  booked "
            f"{c['meetings_booked']}  |  held {c['meetings_held']}")
        add(f"  per qualified {money(c['cost_per_qualified_conversation_cents'])}  |  "
            f"per booked {money(c['cost_per_meeting_booked_cents'])}  |  "
            f"per held {money(c['cost_per_meeting_held_cents'])}")
        confidence = c["booked_mean_confidence"]
        add(f"  booked by attribution: {_mix_text(c['booked_by_method'])}"
            + (f"  |  mean confidence {confidence}" if confidence is not None else ""))
    add("")
    add("Reconciliation (account-wide, from the raw rows)", "head")
    add(f"Raw cost {money(rec['raw_cost_cents'])} vs ledger {money(rec['ledger_cost_cents'])}"
        f" (excluding infrastructure): difference {rec['difference_cents']} cents, "
        f"rounding tolerance {rec['rounding_tolerance_cents']}: "
        f"{'BALANCED' if rec['balanced'] else 'NOT BALANCED'}")
    add(f"Booked-meeting leads in raw outcomes {rec['booked_outcome_leads']} vs ledger "
        f"{rec['ledger_meetings_booked']}; attributed {rec['attributed']}, "
        f"pending {rec['pending']}")
    add("")
    add("Definitions", "head")
    for key, text in DEFINITIONS.items():
        current = f"{key}: "
        for word in text.split():
            if len(current) + len(word) + 1 > 120:
                add(current.rstrip())
                current = "    "
            current += word + " "
        add(current.rstrip())
    return lines
