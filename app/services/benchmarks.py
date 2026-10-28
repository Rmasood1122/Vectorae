"""Anonymised benchmarks: your numbers against other Vectorae accounts (Feature 6).

WHAT A BENCHMARK IS -- AND IS NOT
The spread of reply, meeting-booked and bounce rates ACROSS ACCOUNTS on this
deployment, for one industry and channel, over a trailing window. It is an
observation about Vectorae's own customers. It is not an industry statistic,
and nothing that renders it may call it one (FEATURES.md Part 5: "no public
benchmark numbers").

ANONYMITY, AND ITS LIMITS
  * The unit is the ACCOUNT (a product owner), not the message. Each account
    contributes one rate per bucket, and only once it has
    benchmark_min_account_sends sends there -- so one high-volume account
    cannot BE the benchmark, and a 3-send account's 33% reply rate is not a
    data point.
  * A bucket is published only with at least benchmark_min_accounts accounts,
    and never below HARD_MIN_ACCOUNTS whatever the setting says. Suppressed
    buckets are not written at all.
  * Only rounded percentiles leave the job (p25 / p50 / p75, to half a
    percentage point) -- no pooled totals, no min/max, and the API shows the
    account count as a band.
  * What this does NOT guarantee: in a small bucket a percentile is, by
    construction, one account's rate (rounded). The threshold and rounding make
    that account unidentifiable, not absent. Accounts controlling several
    workspaces could also stack a bucket; that is outside what a threshold can
    detect.

DEFINITIONS -- identical to crm_service.dashboard_campaigns, so a user's own
number here matches the one on their campaigns dashboard:
  dispatched    messages SENT or BOUNCED (a bounce was dispatched)
  reply_rate    REPLIED outcomes / dispatched
  meeting_rate  BOOKED outcomes / dispatched
  bounce_rate   BOUNCED outcomes / dispatched

INDUSTRY comes from the strategy's canonical ICP (pattern_inputs_json.icp.
industries): one industry -> that industry, several -> "multiple", none ->
"unspecified". "*" is every industry together, published under the same rules.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db.models import (
    BenchmarkBucket,
    Lead,
    Message,
    MessageStatus,
    Outcome,
    OutcomeEvent,
    Product,
    Strategy,
    User,
)
from app.services import system_settings

logger = logging.getLogger(__name__)

ALL_INDUSTRIES = "*"
MULTIPLE = "multiple"
UNSPECIFIED = "unspecified"
METRICS = ("reply_rate", "meeting_rate", "bounce_rate")
EVENT_FOR = {"reply_rate": OutcomeEvent.REPLIED,
             "meeting_rate": OutcomeEvent.BOOKED,
             "bounce_rate": OutcomeEvent.BOUNCED}
DISPATCHED = (MessageStatus.SENT, MessageStatus.BOUNCED)
# A floor below which no setting can push the publishing threshold: the check
# that must not be configurable away.
HARD_MIN_ACCOUNTS = 5
ROUND_TO = 0.005


def _now() -> datetime:
    return datetime.now(timezone.utc)


def limits(db: Session) -> dict:
    return {
        "min_accounts": max(HARD_MIN_ACCOUNTS, int(system_settings.get(db, "benchmark_min_accounts"))),
        "min_account_sends": max(1, int(system_settings.get(db, "benchmark_min_account_sends"))),
        "window_days": max(7, min(365, int(system_settings.get(db, "benchmark_window_days")))),
    }


def industry_of(strategy: Strategy | None) -> str:
    industries = (((strategy.pattern_inputs_json or {}).get("icp") or {}).get("industries")
                  if strategy is not None else None) or []
    cleaned = sorted({str(i).strip().lower() for i in industries if str(i).strip()})
    if not cleaned:
        return UNSPECIFIED
    if len(cleaned) > 1:
        return MULTIPLE
    return cleaned[0][:120]


def _round(value: float) -> float:
    return round(round(value / ROUND_TO) * ROUND_TO, 4)


def _percentile(values: list[float], q: float) -> float:
    """Linear interpolation between closest ranks (numpy's default)."""
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def account_band(count: int) -> str:
    for floor in (100, 25, 10):
        if count >= floor:
            return f"{floor}+"
    return f"{HARD_MIN_ACCOUNTS}+"


# --------------------------------------------------------------------------
# Counting
# --------------------------------------------------------------------------


def _counts(db: Session, since: datetime, *, user_id: uuid.UUID | None = None,
            strategy_id: uuid.UUID | None = None) -> dict:
    """{(user_id, strategy_id, channel): {"dispatched": n, "<metric>": n}}.

    Every query walks message/outcome -> lead -> strategy -> product, so a
    user-scoped call can only ever count that user's rows.
    """
    def _scope(query):
        query = (query.join(Strategy, Strategy.id == Lead.strategy_id)
                 .join(Product, Product.id == Strategy.product_id))
        if user_id is not None:
            query = query.where(Product.user_id == user_id)
        if strategy_id is not None:
            query = query.where(Strategy.id == strategy_id)
        return query

    out: dict = {}
    sent = db.execute(_scope(
        select(Product.user_id, Strategy.id, Message.channel, func.count(Message.id))
        .select_from(Message).join(Lead, Lead.id == Message.lead_id)
    ).where(Message.status.in_(DISPATCHED), Message.sent_at >= since)
     .group_by(Product.user_id, Strategy.id, Message.channel)).all()
    for owner, strategy, channel, count in sent:
        key = (owner, strategy, channel.value if hasattr(channel, "value") else str(channel))
        out.setdefault(key, {"dispatched": 0, **{m: 0 for m in METRICS}})["dispatched"] += count

    events = db.execute(_scope(
        select(Product.user_id, Strategy.id, Outcome.channel, Outcome.event,
               func.count(Outcome.id))
        .select_from(Outcome).join(Lead, Lead.id == Outcome.lead_id)
    ).where(Outcome.ts >= since, Outcome.event.in_(list(EVENT_FOR.values())))
     .group_by(Product.user_id, Strategy.id, Outcome.channel, Outcome.event)).all()
    metric_of = {event: metric for metric, event in EVENT_FOR.items()}
    for owner, strategy, channel, event, count in events:
        key = (owner, strategy, str(channel))
        out.setdefault(key, {"dispatched": 0, **{m: 0 for m in METRICS}})[metric_of[event]] += count
    return out


def _rates(counts: dict) -> dict:
    dispatched = counts["dispatched"]
    return {m: (min(1.0, counts[m] / dispatched) if dispatched else None) for m in METRICS}


# --------------------------------------------------------------------------
# The nightly snapshot
# --------------------------------------------------------------------------


def compute_snapshot(db: Session, now: datetime | None = None) -> dict:
    """Recompute every bucket and REPLACE the table in one transaction."""
    now = now or _now()
    limit = limits(db)
    since = now - timedelta(days=limit["window_days"])
    counts = _counts(db, since)

    suspended = set(db.execute(select(User.id).where(User.is_suspended.is_(True))).scalars())
    industries = {sid: industry_of(s) for sid, s in (
        (s.id, s) for s in db.execute(
            select(Strategy).where(Strategy.id.in_({k[1] for k in counts}))
        ).scalars()
    )}

    # Sum each account's strategies into its (industry, channel) and (*, channel).
    per_account: dict[tuple, dict] = {}
    for (owner, strategy, channel), c in counts.items():
        if owner in suspended:
            continue
        for industry in {industries.get(strategy, UNSPECIFIED), ALL_INDUSTRIES}:
            slot = per_account.setdefault((industry, channel, owner),
                                          {"dispatched": 0, **{m: 0 for m in METRICS}})
            for field in ("dispatched", *METRICS):
                slot[field] += c[field]

    buckets: dict[tuple, list[dict]] = {}
    for (industry, channel, _owner), c in per_account.items():
        if c["dispatched"] >= limit["min_account_sends"]:
            buckets.setdefault((industry, channel), []).append(_rates(c))

    rows, suppressed = [], 0
    for (industry, channel), accounts in buckets.items():
        if len(accounts) < limit["min_accounts"]:
            suppressed += 1
            continue
        for metric in METRICS:
            values = [a[metric] for a in accounts]
            rows.append(BenchmarkBucket(
                industry=industry, channel=channel, metric=metric,
                account_count=len(accounts),
                p25=_round(_percentile(values, 0.25)),
                p50=_round(_percentile(values, 0.50)),
                p75=_round(_percentile(values, 0.75)),
                window_days=limit["window_days"], computed_at=now,
            ))

    db.execute(delete(BenchmarkBucket))
    db.add_all(rows)
    db.commit()
    published = len(rows) // len(METRICS)
    logger.info("benchmarks: %d bucket(s) published, %d suppressed", published, suppressed)
    return {"published": published, "suppressed": suppressed}


# --------------------------------------------------------------------------
# The comparison a user sees
# --------------------------------------------------------------------------


def _published(db: Session, industry: str) -> dict:
    out: dict = {}
    for row in db.execute(select(BenchmarkBucket).where(
            BenchmarkBucket.industry == industry)).scalars():
        entry = out.setdefault(row.channel, {"industry": industry,
                                             "accounts": account_band(row.account_count),
                                             "window_days": row.window_days,
                                             "computed_at": row.computed_at.isoformat(),
                                             "metrics": {}})
        entry["metrics"][row.metric] = {"p25": row.p25, "p50": row.p50, "p75": row.p75}
    return out


def comparison(db: Session, user: User, strategy: Strategy | None = None,
               now: datetime | None = None) -> dict:
    """The user's own rates next to the published buckets, per channel."""
    now = now or _now()
    limit = limits(db)
    since = now - timedelta(days=limit["window_days"])
    mine: dict[str, dict] = {}
    for (_owner, _strategy, channel), c in _counts(
            db, since, user_id=user.id,
            strategy_id=strategy.id if strategy is not None else None).items():
        slot = mine.setdefault(channel, {"dispatched": 0, **{m: 0 for m in METRICS}})
        for field in ("dispatched", *METRICS):
            slot[field] += c[field]

    industry = industry_of(strategy) if strategy is not None else ALL_INDUSTRIES
    for_industry = _published(db, industry)
    for_all = _published(db, ALL_INDUSTRIES) if industry != ALL_INDUSTRIES else for_industry

    channels = []
    for channel in sorted(set(mine) | set(for_industry) | set(for_all)):
        own = mine.get(channel, {"dispatched": 0, **{m: 0 for m in METRICS}})
        enough = own["dispatched"] >= limit["min_account_sends"]
        channels.append({
            "channel": channel,
            "yours": {
                "dispatched": own["dispatched"],
                "enough_data": enough,
                **({m: round(v, 4) if v is not None else None
                    for m, v in _rates(own).items()} if own["dispatched"] else
                   {m: None for m in METRICS}),
            },
            "industry": for_industry.get(channel),
            "all_industries": for_all.get(channel),
        })
    return {
        "industry": industry,
        "window_days": limit["window_days"],
        "min_accounts": limit["min_accounts"],
        "min_account_sends": limit["min_account_sends"],
        "channels": channels,
        "definitions": {
            "reply_rate": "replies / messages dispatched (sent or bounced)",
            "meeting_rate": "meetings booked / messages dispatched",
            "bounce_rate": "bounces / messages dispatched",
        },
        "note": ("Observed across Vectorae accounts on this deployment in the window. "
                 "Not an industry statistic."),
    }
