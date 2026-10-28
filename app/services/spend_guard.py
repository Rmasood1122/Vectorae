"""F07 -- Spend Guard + Cost Forecast (docs/features/f07-spend-guard.md).

The competitor complaint: credit burn -- credits spent per attempt, with no
warning before a run and no ceiling the customer controls. This module adds:

  forecast   what a run is expected to cost BEFORE it starts, from the cost
             model that already exists (app/pipeline/budget_plan.py for the
             pipeline, the admin's model prices, per-provider data credits)
  monthly    a customer-set monthly ceiling, enforced three ways:
  cap          * refusal before a run whose forecast exceeds what is left
               * the existing per-call model gateway (usage_meter.check_budget
                 asks the Guard armed on its scope -- a four-line hook there)
               * every paid data-provider call (plumbing.BaseHttpAdapter.call asks
                 before the request and meters after it)
  alerts     50 / 80 / 100 % of the cap, once per threshold per UTC month
             (a UNIQUE constraint is the guarantee), in-app + transactional
             email -- never the outreach send path
  measure    reconcile(): a stored forecast vs what the run actually cost, so
             "forecast within +/-25% of actual" can be measured, not promised

The monthly cap is ADDITIONAL: it never raises or replaces the plan's daily AI
cap (P0-5) or a strategy's budget (F-AI-01). All three are checked; the first
one reached stops the work. Like P0-5 it is enforced only for
usage_meter.ACCOUNT_CAPPED_PURPOSES -- a send or a meeting brief is counted
toward the month but never stopped halfway.

ONE LEDGER PER KIND OF SPEND, NOT A SECOND ONE
AI spend is api_usage (usage_meter). Data credits are F01's enrichment_attempts:
the waterfall ledgers its own verifier calls; this module meters every OTHER
paid provider call into the same table with step = "spend_guard". Prices are
the admin's settings (model prices already existed; data prices are new and
default to 0 = counted, not priced).

TENANCY: every query below filters by the account's own id. Workers resolve
the account through notifications.owner_of_strategy (the central resolver).
"""

from __future__ import annotations

import logging
import math
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.services import usage_meter

logger = logging.getLogger(__name__)

FLAG = "spend_guard_enabled"
#: provider -> the system setting holding its USD price per credit. These are
#: also the only providers whose calls are metered as data credits (an
#: adapter for a calendar or a CRM is not a data purchase).
PRICE_SETTINGS = {
    "apollo": "spend_guard_apollo_usd_per_credit",
    "hunter": "spend_guard_hunter_usd_per_credit",
}
THRESHOLDS = (50, 80, 100)
TOLERANCE = 0.25
METER_STEP = "spend_guard"          # enrichment_attempts.step of rows this module writes

STRATEGY_RUN = "strategy_run"
ENRICHMENT_RUN = "enrichment_run"
QUICKSTART = "quickstart"
KINDS = (STRATEGY_RUN, ENRICHMENT_RUN, QUICKSTART)

ALLOWED = "allowed"
REFUSED = "refused"

#: Credits one requested lead is expected to consume, before the account has
#: history of its own. Apollo: one search result + one enrich. Hunter: the
#: finder for leads without an address + the verifier. UNVERIFIED estimates
#: (see the spec); calibrate_credits() replaces them after real runs.
DEFAULT_CREDITS_PER_LEAD = {"apollo": 2.0, "hunter": 1.5}
CALIBRATION_MIN_RUNS = 3
CALIBRATION_MAX_RUNS = 10

#: Lead scoring (app/services/lead_scoring.py): one model call per 15 leads,
#: max_tokens=2048. Tokens per call until the account has its own average.
LEAD_SCORING_BATCH = 15
LEAD_SCORING_DEFAULT_TOKENS = (2500, 1000)
LEAD_SCORING_MIN_CALLS = 3
HISTORY_DAYS = 90

SENDS_NOTE = ("This run sends nothing. Sends happen later through the send "
              "chokepoint; their AI cost counts toward the month, but a send "
              "is never stopped halfway by a cap.")
STOP_PREFIX = "monthly spend cap reached"


class MonthlySpendCapReached(usage_meter.BudgetExceededError):
    """The next model or data call could take the ACCOUNT past its own
    monthly cap. Raised BEFORE the call, so nothing is spent on it. A
    BudgetExceededError, so every path that already stops cleanly on a
    strategy budget (no retry, strategy -> budget_exhausted, lead scoring ->
    heuristic, Quickstart -> stopped) does the same here."""


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def enabled(db: Session) -> bool:
    from app.services import system_settings  # noqa: PLC0415

    return bool(system_settings.get(db, FLAG))


def _uuid(value) -> uuid.UUID | None:
    if value is None or isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def month_start(now: datetime | None = None) -> datetime:
    now = _aware(now) or _now()
    now = now.astimezone(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def next_month_start(now: datetime | None = None) -> datetime:
    start = month_start(now)
    return (start + timedelta(days=32)).replace(day=1)


def period_label(now: datetime | None = None) -> str:
    return month_start(now).strftime("%Y-%m")


def usd(micros: int | None) -> float | None:
    return None if micros is None else round(micros / 1_000_000, 4)


def unit_prices(db: Session) -> dict[str, float]:
    """{provider: USD per credit} from the admin's settings (never negative)."""
    from app.services import system_settings  # noqa: PLC0415

    return {provider: max(float(system_settings.get(db, key)), 0.0)
            for provider, key in PRICE_SETTINGS.items()}


def unit_micros(db: Session) -> dict[str, int]:
    return {p: int(round(v * 1_000_000)) for p, v in unit_prices(db).items()}


def price_units(per_unit: dict[str, int], units: dict[str, int]) -> int:
    """Micro-dollars for {provider: credits}. An unknown provider is 0 (and
    shown as unpriced), never guessed."""
    return int(sum(int(n) * per_unit.get(p, 0) for p, n in (units or {}).items()))


# --------------------------------------------------------------------------
# What the account has spent (owner-filtered)
# --------------------------------------------------------------------------


def ai_spent_micros(db: Session, user_id, since: datetime,
                    until: datetime | None = None) -> int:
    from app.db.models import ApiUsage  # noqa: PLC0415

    q = select(func.coalesce(func.sum(ApiUsage.cost_micros), 0)).where(
        ApiUsage.user_id == _uuid(user_id), ApiUsage.ts >= since)
    if until is not None:
        q = q.where(ApiUsage.ts < until)
    return int(db.execute(q).scalar_one() or 0)


def data_units(db: Session, user_id, since: datetime,
               until: datetime | None = None) -> dict[str, int]:
    """Credits consumed per provider (cache hits are 0 in the ledger)."""
    from app.db.models import EnrichmentAttempt  # noqa: PLC0415

    q = (select(EnrichmentAttempt.provider, func.coalesce(func.sum(EnrichmentAttempt.cost_units), 0))
         .where(EnrichmentAttempt.user_id == _uuid(user_id),
                EnrichmentAttempt.created_at >= since)
         .group_by(EnrichmentAttempt.provider))
    if until is not None:
        q = q.where(EnrichmentAttempt.created_at < until)
    return {p: int(n) for p, n in db.execute(q).all() if int(n or 0) > 0}


def month_spend(db: Session, user_id, now: datetime | None = None,
                per_unit: dict[str, int] | None = None) -> dict:
    since, until = month_start(now), next_month_start(now)
    ai = ai_spent_micros(db, user_id, since, until)
    units = data_units(db, user_id, since, until)
    per_unit = per_unit if per_unit is not None else unit_micros(db)
    data = price_units(per_unit, units)
    return {"period": period_label(now), "since": since, "ai_micros": ai,
            "data_units": units, "data_micros": data, "total_micros": ai + data}


# --------------------------------------------------------------------------
# The cap
# --------------------------------------------------------------------------


def cap_row(db: Session, user_id):
    from app.db.models import SpendCap  # noqa: PLC0415

    return db.execute(select(SpendCap).where(SpendCap.user_id == _uuid(user_id))
                      ).scalar_one_or_none()


def cap_micros(db: Session, user_id) -> int | None:
    row = cap_row(db, user_id)
    if row is None or row.monthly_cap_micros is None or row.monthly_cap_micros <= 0:
        return None
    return int(row.monthly_cap_micros)


def set_cap(db: Session, user_id, *, cap_usd: float | None, email_alerts: bool,
            actor_id=None):
    """Create or update the account's cap. Commits."""
    from app.db.models import SpendCap  # noqa: PLC0415

    row = cap_row(db, user_id)
    if row is None:
        row = SpendCap(user_id=_uuid(user_id))
        db.add(row)
    row.monthly_cap_micros = None if cap_usd is None else int(round(float(cap_usd) * 1_000_000))
    row.email_alerts = bool(email_alerts)
    row.updated_by_user_id = _uuid(actor_id)
    db.commit()
    return row


# --------------------------------------------------------------------------
# Forecasts
# --------------------------------------------------------------------------


@dataclass
class Forecast:
    kind: str
    ai_purposes: tuple[str, ...]
    ai_input_tokens: int = 0
    ai_output_tokens: int = 0
    ai_micros: int = 0
    data_units: dict = field(default_factory=dict)
    data_micros: int = 0
    requested_leads: int | None = None
    detail: dict = field(default_factory=dict)

    @property
    def total_micros(self) -> int:
        return int(self.ai_micros) + int(self.data_micros)

    def merged(self, other: "Forecast", kind: str) -> "Forecast":
        units = dict(self.data_units)
        for p, n in other.data_units.items():
            units[p] = units.get(p, 0) + n
        purposes = tuple(dict.fromkeys(self.ai_purposes + other.ai_purposes))
        return Forecast(
            kind=kind, ai_purposes=purposes,
            ai_input_tokens=self.ai_input_tokens + other.ai_input_tokens,
            ai_output_tokens=self.ai_output_tokens + other.ai_output_tokens,
            ai_micros=self.ai_micros + other.ai_micros, data_units=units,
            data_micros=self.data_micros + other.data_micros,
            requested_leads=other.requested_leads or self.requested_leads,
            detail={**self.detail, **other.detail})

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "ai": {"purposes": list(self.ai_purposes),
                   "input_tokens": self.ai_input_tokens,
                   "output_tokens": self.ai_output_tokens,
                   "usd": usd(self.ai_micros)},
            "data": {"credits": dict(self.data_units), "usd": usd(self.data_micros)},
            "sends": {"count": 0, "usd": 0.0, "note": SENDS_NOTE},
            "requested_leads": self.requested_leads,
            "total_usd": usd(self.total_micros),
            "basis": dict(self.detail),
        }


def forecast_strategy_run(db: Session, *, flow_type, steps_per_phase: int | None,
                          done_steps: int = 0, include_pipeline: bool = True,
                          include_verification: bool = True,
                          market_intel_pending: bool = True) -> Forecast:
    """The pipeline + verification forecast, from budget_plan's own model:
    planned steps x estimate_step_micros + the verification reserve -- the
    exact numbers a strategy's budget is planned with (F-AI-01b)."""
    from app.config import settings  # noqa: PLC0415
    from app.pipeline import budget_plan  # noqa: PLC0415
    from app.pipeline.engine import pipelines_for_flow  # noqa: PLC0415
    from app.services import system_settings  # noqa: PLC0415

    prices = usage_meter.price_table(db)
    planned = (len(budget_plan.planned_step_nos(steps_per_phase))
               * len(pipelines_for_flow(flow_type))) if include_pipeline else 0
    steps = max(planned - max(int(done_steps), 0), 0)
    reserve = (int(round(settings.pipeline_verification_reserve_usd * 1_000_000))
               if include_verification else 0)
    purposes = (("pipeline",) if include_pipeline else ()) + (
        ("verification",) if include_verification else ())
    units: dict[str, int] = {}
    if (include_pipeline and steps > 0 and market_intel_pending
            and system_settings.get(db, "competitor_intel_enabled")):
        units["apollo"] = 1     # market_intel's one organization search
    return Forecast(
        kind=STRATEGY_RUN, ai_purposes=purposes or ("pipeline",),
        ai_input_tokens=steps * settings.pipeline_est_step_input_tokens,
        ai_output_tokens=steps * settings.pipeline_est_step_output_tokens,
        ai_micros=steps * budget_plan.estimate_step_micros(prices) + reserve,
        data_units=units, data_micros=price_units(unit_micros(db), units),
        detail={"ai": (f"{steps} pipeline steps x the per-step estimate "
                       f"({settings.pipeline_est_step_input_tokens} in / "
                       f"{settings.pipeline_est_step_output_tokens} out tokens)"
                       + (f" + ${settings.pipeline_verification_reserve_usd:.2f} "
                          "verification reserve" if include_verification else "")),
                "planned_steps": planned, "steps_to_run": steps,
                "data": ("1 Apollo organization search for market signals"
                         if units else "no data-provider calls")})


def calibrate_credits(db: Session, user_id) -> tuple[dict[str, float], str]:
    """Credits per requested lead, per provider: this account's own finished
    lead batches when it has enough of them, else the documented defaults."""
    from app.db.models import (  # noqa: PLC0415
        BatchStage, EnrichmentAttempt, LeadBatch, SpendForecast,
    )

    rows = db.execute(
        select(SpendForecast.batch_id, SpendForecast.requested_leads)
        .join(LeadBatch, LeadBatch.id == SpendForecast.batch_id)
        .where(SpendForecast.user_id == _uuid(user_id),
               SpendForecast.kind == ENRICHMENT_RUN,
               SpendForecast.decision == ALLOWED,
               SpendForecast.requested_leads > 0,
               LeadBatch.stage == BatchStage.FINALIZED)
        .order_by(SpendForecast.created_at.desc())
        .limit(CALIBRATION_MAX_RUNS)
    ).all()
    if len(rows) < CALIBRATION_MIN_RUNS:
        return dict(DEFAULT_CREDITS_PER_LEAD), "default estimate (fewer than 3 finished runs)"
    requested = sum(int(r.requested_leads) for r in rows)
    used = db.execute(
        select(EnrichmentAttempt.provider,
               func.coalesce(func.sum(EnrichmentAttempt.cost_units), 0))
        .where(EnrichmentAttempt.user_id == _uuid(user_id),
               EnrichmentAttempt.batch_id.in_([r.batch_id for r in rows]))
        .group_by(EnrichmentAttempt.provider)
    ).all()
    rates = {p: 0.0 for p in DEFAULT_CREDITS_PER_LEAD}
    for provider, units in used:
        rates[provider] = int(units or 0) / requested
    return rates, f"calibrated from this account's last {len(rows)} lead batches"


def lead_scoring_tokens(db: Session, user_id, now: datetime | None = None
                        ) -> tuple[tuple[int, int], str]:
    from app.db.models import ApiUsage  # noqa: PLC0415

    since = (_aware(now) or _now()) - timedelta(days=HISTORY_DAYS)
    calls, tin, tout = db.execute(
        select(func.coalesce(func.sum(ApiUsage.calls), 0),
               func.coalesce(func.sum(ApiUsage.input_tokens), 0),
               func.coalesce(func.sum(ApiUsage.output_tokens), 0))
        .where(ApiUsage.user_id == _uuid(user_id), ApiUsage.purpose == "lead_scoring",
               ApiUsage.ts >= since)
    ).one()
    calls = int(calls or 0)
    if calls >= LEAD_SCORING_MIN_CALLS:
        return ((int(round(int(tin) / calls)), int(round(int(tout) / calls))),
                f"this account's average over {calls} scoring calls")
    return LEAD_SCORING_DEFAULT_TOKENS, "default estimate"


def forecast_enrichment_run(db: Session, user_id, requested_leads: int,
                            now: datetime | None = None) -> Forecast:
    """Lead sourcing: data credits per provider + lead scoring at the end."""
    from app.services import system_settings  # noqa: PLC0415

    requested = max(int(requested_leads or 0), 0)
    rates, credit_basis = calibrate_credits(db, user_id)
    units = {p: int(math.ceil(requested * r)) for p, r in rates.items()
             if math.ceil(requested * r) > 0}
    tin = tout = ai = 0
    scoring_basis = "lead scoring is off"
    if requested and system_settings.get(db, "lead_scoring_enabled"):
        (per_in, per_out), scoring_basis = lead_scoring_tokens(db, user_id, now)
        calls = int(math.ceil(requested / LEAD_SCORING_BATCH))
        price_in, price_out = usage_meter.price_table(db).get("anthropic", (0.0, 0.0))
        tin, tout = calls * per_in, calls * per_out
        ai = int(round(tin * price_in + tout * price_out))
        scoring_basis = f"{calls} scoring calls; {scoring_basis}"
    return Forecast(
        kind=ENRICHMENT_RUN, ai_purposes=("lead_scoring",), ai_input_tokens=tin,
        ai_output_tokens=tout, ai_micros=ai, data_units=units,
        data_micros=price_units(unit_micros(db), units), requested_leads=requested,
        detail={"data": f"{requested} leads x credits per lead ({credit_basis})",
                "credits_per_lead": {p: round(r, 3) for p, r in rates.items()},
                "lead_scoring": scoring_basis})


def forecast_quickstart(db: Session, user_id, *, steps_per_phase: int | None,
                        requested_leads: int, budget_micros: int,
                        now: datetime | None = None) -> Forecast:
    """A Quickstart starts a full no-clients pipeline AND a lead batch AND its
    own drafts (capped by its own budget, which is the best estimate there is)."""
    from app.db.models import FlowType  # noqa: PLC0415

    pipeline = forecast_strategy_run(db, flow_type=FlowType.NO_CLIENTS,
                                     steps_per_phase=steps_per_phase)
    sourcing = forecast_enrichment_run(db, user_id, requested_leads, now=now)
    own = Forecast(kind=QUICKSTART, ai_purposes=("quickstart",),
                   ai_micros=int(budget_micros or 0),
                   detail={"quickstart": "the Quickstart run's own AI budget "
                                         "(ICP extraction + drafts)"})
    combined = pipeline.merged(sourcing, QUICKSTART).merged(own, QUICKSTART)
    combined.detail["data"] = sourcing.detail["data"]
    return combined


# --------------------------------------------------------------------------
# Decision before a run
# --------------------------------------------------------------------------


@dataclass
class Decision:
    allowed: bool
    forecast: Forecast
    spend: dict
    cap_micros: int | None

    @property
    def remaining_micros(self) -> int | None:
        return None if self.cap_micros is None else self.cap_micros - self.spend["total_micros"]

    def out(self) -> dict:
        return {"would_allow": self.allowed, "forecast": self.forecast.to_dict(),
                "cap_usd": usd(self.cap_micros),
                "spent_this_month_usd": usd(self.spend["total_micros"]),
                "remaining_usd": usd(self.remaining_micros)}

    def refusal(self) -> dict:
        remaining = max(self.remaining_micros or 0, 0)
        return {
            "error": "monthly_spend_cap_reached",
            "forecast_usd": usd(self.forecast.total_micros),
            "remaining_usd": usd(remaining),
            "cap_usd": usd(self.cap_micros),
            "spent_usd": usd(self.spend["total_micros"]),
            "message": (f"This run is forecast to cost ${self.forecast.total_micros / 1e6:.2f}, "
                        f"but only ${remaining / 1e6:.2f} of your "
                        f"${(self.cap_micros or 0) / 1e6:.2f} monthly spend cap is left. "
                        "Nothing was started. Raise the cap on the Usage page, or wait "
                        "for the new month (UTC)."),
        }


def evaluate(db: Session, user_id, forecast: Forecast, now: datetime | None = None) -> Decision:
    spend = month_spend(db, user_id, now)
    cap = cap_micros(db, user_id)
    allowed = True
    if cap is not None:
        remaining = cap - spend["total_micros"]
        allowed = remaining > 0 and forecast.total_micros <= remaining
    return Decision(allowed=allowed, forecast=forecast, spend=spend, cap_micros=cap)


def record(db: Session, user_id, decision: Decision, *, strategy_id=None, batch_id=None,
           now: datetime | None = None, commit: bool = True):
    """Store the forecast (allowed or refused). commit=False only flushes: the
    caller's own commit saves it (and a failed request drops it)."""
    from app.db.models import SpendForecast  # noqa: PLC0415

    f = decision.forecast
    at = _aware(now) or _now()
    row = SpendForecast(
        user_id=_uuid(user_id), kind=f.kind,
        decision=ALLOWED if decision.allowed else REFUSED,
        strategy_id=_uuid(strategy_id), batch_id=_uuid(batch_id),
        requested_leads=f.requested_leads, ai_purposes=list(f.ai_purposes),
        ai_input_tokens=f.ai_input_tokens, ai_output_tokens=f.ai_output_tokens,
        ai_micros=f.ai_micros, data_units_json=dict(f.data_units), data_micros=f.data_micros,
        total_micros=f.total_micros, month_spent_micros=decision.spend["total_micros"],
        cap_micros=decision.cap_micros, detail_json=dict(f.detail),
        created_at=at, updated_at=at,
    )
    db.add(row)
    if commit:
        db.commit()
    else:
        db.flush()
    return row


def link(db: Session, row, *, strategy_id=None, batch_id=None) -> None:
    """Attach the run's strategy / batch once their rows exist. No-op for None."""
    if row is None:
        return
    if strategy_id is not None:
        row.strategy_id = _uuid(strategy_id)
    if batch_id is not None:
        row.batch_id = _uuid(batch_id)
    db.commit()


# --------------------------------------------------------------------------
# The per-call guard (model calls via usage_meter, data calls via plumbing)
# --------------------------------------------------------------------------


@dataclass
class Guard:
    """The monthly cap for one run (an outermost account-capped model scope,
    a lead-batch stage, or a single paid endpoint).

    Every check re-reads the month's COMMITTED spend (one aggregate query) and
    adds this scope's own model calls that are noted but not yet written to
    api_usage. So spend that other runs committed after this run started is
    always seen. What a check cannot see is other runs' spend that is not
    committed yet: their noted-but-unflushed model calls (at most one pipeline
    step's worth -- the engine flushes after every step) and a call another run
    is making at that very moment. That is the residual window (spec)."""

    session: Session
    user_id: uuid.UUID
    strategy_id: uuid.UUID | None
    batch_id: uuid.UUID | None
    cap_micros: int | None
    per_unit: dict[str, int]
    #: providers whose calls are already ledgered by their caller (F01's
    #: waterfall and re-verify ledger every verifier call) -- checked, not
    #: metered twice.
    self_ledgered: frozenset = frozenset()
    #: set by batch_scope: the cap already stopped this batch.
    stopped: bool = False
    #: a check refused a call: the scope fires the 100% alert when it closes.
    refused: bool = False

    def spent(self, active=None) -> int:
        return (committed_month_micros(self.session, self.user_id, self.per_unit)
                + (_pending_micros(active) if active is not None else 0))

    def _refuse(self, spent: int, reserve: int, what: str) -> None:
        self.refused = True
        raise MonthlySpendCapReached(
            f"{STOP_PREFIX}: ${self.cap_micros / 1e6:.2f} this month, "
            f"${spent / 1e6:.2f} used; the next {what} could cost up to "
            f"${reserve / 1e6:.2f} more. Nothing was spent on it. Raise the cap on "
            "the Usage page or wait for the new month (UTC), then resume.",
            budget_micros=int(self.cap_micros or 0), spent_micros=spent)

    def check_model_call(self, active, input_chars: int, max_output_tokens: int,
                         provider: str) -> None:
        """Called by usage_meter.check_budget. Worst-case price, like the
        other two ceilings."""
        if self.cap_micros is None:
            return
        price_in, price_out = active.prices.get(provider, (0.0, 0.0))
        reserve = int(round(
            (max(int(input_chars), 0) / usage_meter._CHARS_PER_TOKEN_CONSERVATIVE) * price_in
            + max(int(max_output_tokens), 0) * price_out))
        spent = self.spent(active)
        if spent >= self.cap_micros or spent + reserve > self.cap_micros:
            self._refuse(spent, reserve, "model call")

    def check_data_call(self, active, provider: str, cost_units: int) -> None:
        if self.cap_micros is None:
            return
        cost = int(cost_units) * self.per_unit.get(provider, 0)
        spent = self.spent(active)
        if spent >= self.cap_micros or spent + cost > self.cap_micros:
            self._refuse(spent, cost, f"{provider} call")

    def meter_data_call(self, provider: str, endpoint: str, cost_units: int) -> None:
        """One ledger row per paid call, committed now (F01's rule: a crash
        later must not erase the record of a call already paid for). The
        insert runs in a SAVEPOINT, so a failure loses only this row -- never
        the caller's pending work."""
        from app.db.models import EnrichmentAttempt  # noqa: PLC0415

        if provider in self.self_ledgered:
            return
        operation = (endpoint or "").rstrip("/").rsplit("/", 1)[-1] or "call"
        try:
            with self.session.begin_nested():
                self.session.add(EnrichmentAttempt(
                    user_id=self.user_id, strategy_id=self.strategy_id,
                    batch_id=self.batch_id, lead_id=None, step=METER_STEP,
                    provider=(provider or "unknown")[:50], operation=operation[:20],
                    outcome="ok", cached=False, cost_units=int(cost_units)))
        except Exception:  # noqa: BLE001 -- the call has been paid for; keep going
            logger.exception("spend_guard: could not meter a %s call", provider)
            return
        try:
            self.session.commit()
        except Exception:  # noqa: BLE001
            logger.exception("spend_guard: could not commit the %s meter row", provider)
            self.session.rollback()


def committed_month_micros(db: Session, user_id, per_unit: dict[str, int],
                           now: datetime | None = None) -> int:
    """The account's committed spend this month -- AI micros + priced data
    credits -- in ONE aggregate statement (it runs before every guarded call)."""
    from app.db.models import ApiUsage, EnrichmentAttempt  # noqa: PLC0415

    since, until = month_start(now), next_month_start(now)
    uid = _uuid(user_id)
    ai = (select(func.coalesce(func.sum(ApiUsage.cost_micros), 0))
          .where(ApiUsage.user_id == uid, ApiUsage.ts >= since, ApiUsage.ts < until)
          .scalar_subquery())
    priced = [(EnrichmentAttempt.provider == p, v) for p, v in per_unit.items() if v]
    if not priced:
        return int(db.execute(select(ai)).scalar_one() or 0)
    data = (select(func.coalesce(func.sum(EnrichmentAttempt.cost_units
                                          * case(*priced, else_=0)), 0))
            .where(EnrichmentAttempt.user_id == uid, EnrichmentAttempt.created_at >= since,
                   EnrichmentAttempt.created_at < until)
            .scalar_subquery())
    return int(db.execute(select(ai + data)).scalar_one() or 0)


def _pending_micros(active) -> int:
    """This scope's model calls noted but not yet written to api_usage
    (flush_active clears the notes it writes, so nothing is counted twice)."""
    total = 0
    for provider, _model, tin, tout in list(getattr(active, "notes", None) or []):
        price_in, price_out = active.prices.get(provider, (0.0, 0.0))
        total += int(round(tin * price_in + tout * price_out))
    return total


_batch_guard: ContextVar[Guard | None] = ContextVar("spend_guard_batch", default=None)


def arm(session: Session, active) -> None:
    """usage_meter.scope() hook: arm the monthly guard on a fresh outermost
    scope. A no-op unless the purpose is account-capped, the account is known
    and the flag is on (the flag is the ONE query paid while it is off).

    Never raises. The F07 reads run in a SAVEPOINT: if they fail (say the flag
    was switched on before migration 0077 ran), only the savepoint is rolled
    back -- the caller's PostgreSQL transaction is not left aborted -- and the
    run continues under the plan's daily cap and the strategy budget."""
    if active.user_id is None or active.purpose not in usage_meter.ACCOUNT_CAPPED_PURPOSES:
        return
    try:
        if not enabled(session):
            return
        with session.begin_nested():
            per_unit = unit_micros(session)
            cap = cap_micros(session, active.user_id)
            if not active.prices:
                active.prices = usage_meter.price_table(session)
        active.monthly_guard = Guard(
            session=session, user_id=active.user_id, strategy_id=active.strategy_id,
            batch_id=None, cap_micros=cap, per_unit=per_unit)
    except Exception:  # noqa: BLE001
        logger.exception("spend_guard: could not arm the monthly guard")


def after_scope(session: Session, active, failed: bool) -> None:
    """usage_meter.scope() hook at close: a refused call fires the 100% alert
    ("the cap is stopping work"). Nothing happens if nothing was refused."""
    guard = getattr(active, "monthly_guard", None)
    if guard is None or not guard.refused:
        return
    _fire_after_refusal(session, guard.user_id, failed)


def _fire_after_refusal(session: Session, user_id, failed: bool) -> None:
    try:
        if failed:
            session.rollback()      # the refused work is being abandoned anyway
        evaluate_alerts(session, user_id, force_cap_reached=True)
    except Exception:  # noqa: BLE001
        logger.exception("spend_guard: the cap-reached alert could not be recorded")
        session.rollback()


def _active_guard() -> tuple[Guard | None, object | None]:
    # usage_meter's scope is read through its module-level ContextVar; the
    # hook there stays small (PR #61 rewrites that file).
    active = usage_meter._current.get()
    guard = getattr(active, "monthly_guard", None) if active is not None else None
    if guard is not None:
        return guard, active
    batch = _batch_guard.get()
    if batch is not None:
        return batch, None
    return None, None


def before_data_call(provider: str, cost_units: int) -> None:
    """plumbing.BaseHttpAdapter.call hook, on a cache miss, BEFORE the request.
    Raises MonthlySpendCapReached; a no-op outside a guarded scope."""
    if provider not in PRICE_SETTINGS:      # calendars, CRMs, ... are not data credits
        return
    guard, active = _active_guard()
    if guard is None or int(cost_units or 0) <= 0:
        return
    guard.check_data_call(active, provider, int(cost_units))


def after_data_call(provider: str, endpoint: str, cost_units: int) -> None:
    """plumbing.BaseHttpAdapter.call hook, after a successful PAID request. Never
    raises: the call has succeeded and been paid for."""
    if provider not in PRICE_SETTINGS:
        return
    guard, _ = _active_guard()
    if guard is None or int(cost_units or 0) <= 0:
        return
    guard.meter_data_call(provider, endpoint, int(cost_units))


@contextmanager
def _guarded(session: Session, guard: Guard | None):
    """Install a data Guard for the block; at close, alerts are evaluated --
    with the 100% "cap reached" alert forced when a call was refused. A block
    that fails for any OTHER reason is left entirely to its caller."""
    if guard is None or guard.stopped:
        yield guard
        return
    token = _batch_guard.set(guard)
    failed = False
    try:
        yield guard
    except BaseException:
        failed = True
        raise
    finally:
        _batch_guard.reset(token)
        if guard.refused:
            _fire_after_refusal(session, guard.user_id, failed)
        elif not failed:
            try:
                evaluate_alerts(session, guard.user_id)
            except Exception:  # noqa: BLE001
                logger.exception("spend_guard: alert evaluation failed")
                session.rollback()


@contextmanager
def batch_scope(session: Session, batch_id):
    """Guard one lead-batch stage (lead_tasks._run_stage). Yields the Guard
    (guard.stopped = the cap already stopped this batch: run nothing), or None
    when the flag is off -- then the flag read is the only query -- or the
    owner cannot be resolved. The reads run in a SAVEPOINT (see arm)."""
    guard = None
    try:
        if enabled(session):
            from app.db.models import BatchStage, LeadBatch, Strategy  # noqa: PLC0415
            from app.services import notifications  # noqa: PLC0415
            from app.services import waterfall_enrichment  # noqa: PLC0415

            with session.begin_nested():
                batch = session.get(LeadBatch, _uuid(batch_id))
                strategy = (session.get(Strategy, batch.strategy_id)
                            if batch is not None else None)
                owner = notifications.owner_of_strategy(session, strategy) if strategy else None
                if owner is not None:
                    ledgered = (frozenset({batch.verifier_provider})
                                if waterfall_enrichment.enabled(session) else frozenset())
                    guard = Guard(session=session, user_id=_uuid(owner),
                                  strategy_id=strategy.id, batch_id=batch.id,
                                  cap_micros=cap_micros(session, owner),
                                  per_unit=unit_micros(session), self_ledgered=ledgered,
                                  stopped=(batch.stage is BatchStage.FAILED
                                           and (batch.error or "").startswith(STOP_PREFIX)))
    except Exception:  # noqa: BLE001
        logger.exception("spend_guard: could not open the batch guard for %s", batch_id)
        guard = None
    with _guarded(session, guard) as g:
        yield g


@contextmanager
def data_scope(session: Session, user_id, *, strategy_id=None, self_ledgered=()):
    """Guard a single paid endpoint outside any run (F02 market-signal refresh,
    F01 single-lead re-verify): the cap is checked before the provider call
    and the call is metered. Yields the Guard or None (flag off)."""
    guard = None
    try:
        if enabled(session):
            with session.begin_nested():
                guard = Guard(session=session, user_id=_uuid(user_id),
                              strategy_id=_uuid(strategy_id), batch_id=None,
                              cap_micros=cap_micros(session, user_id),
                              per_unit=unit_micros(session),
                              self_ledgered=frozenset(p for p in self_ledgered if p))
    except Exception:  # noqa: BLE001
        logger.exception("spend_guard: could not open a data guard for %s", user_id)
        guard = None
    with _guarded(session, guard) as g:
        yield g


def stop_batch(session: Session, batch_id, exc: Exception) -> None:
    """A clean stop, not a failure to retry: nothing more is spent."""
    from app.db.models import BatchStage, LeadBatch  # noqa: PLC0415

    session.rollback()
    batch = session.get(LeadBatch, _uuid(batch_id))
    if batch is None:
        return
    message = str(exc)
    batch.stage = BatchStage.FAILED
    batch.error = message if message.startswith(STOP_PREFIX) else f"{STOP_PREFIX}: {message}"
    session.commit()
    logger.warning("batch %s stopped by the monthly spend cap: %s", batch_id, message)


# --------------------------------------------------------------------------
# Alerts (50 / 80 / 100 %, once per threshold per UTC month)
# --------------------------------------------------------------------------


def _alert_email(user_email: str, threshold: int, spent: int, cap: int) -> dict:
    from app.services import event_bus  # noqa: PLC0415

    link = event_bus.web_link("/analytics/usage")
    headline = ("Your monthly spend cap is stopping work" if threshold >= 100
                else f"{threshold}% of your monthly spend cap is used")
    tail = ("New strategy, lead-sourcing and Quickstart runs that do not fit in what "
            "is left are refused, and running ones stop before any paid call that "
            "would cross the cap, until the cap is raised or the month ends (UTC). "
            "Scheduled sends continue." if threshold >= 100
            else "Runs keep going until the cap is reached.")
    text = (f"{headline}.\n\nUsed this month: ${spent / 1e6:.2f} of ${cap / 1e6:.2f}.\n"
            f"{tail}\n\nReview usage or change the cap: {link}\n")
    html = (f"<p><strong>{headline}.</strong></p>"
            f"<p>Used this month: ${spent / 1e6:.2f} of ${cap / 1e6:.2f}.</p>"
            f"<p>{tail}</p><p><a href=\"{link}\">Review usage or change the cap</a></p>")
    return {"to": user_email, "subject": f"Vectorae: {headline}", "text": text, "html": html}


def evaluate_alerts(db: Session, user_id, now: datetime | None = None, *,
                    force_cap_reached: bool = False) -> list:
    """Fire every threshold the month's spend has crossed and that has not
    fired yet this month. `force_cap_reached`: the cap just refused work (a
    model call, a data call or a run's forecast), so the 100% alert fires even
    though the refusal kept spend under 100%. Idempotent: the UNIQUE
    (user_id, period, threshold) constraint absorbs a racing duplicate (each
    insert runs in a SAVEPOINT). One email covers every threshold fired by the
    same evaluation. Commits; call it at a clean point."""
    from app.db.models import SpendAlert, User  # noqa: PLC0415

    if not enabled(db):
        return []
    row = cap_row(db, user_id)
    cap = cap_micros(db, user_id)
    if cap is None:
        return []
    uid = _uuid(user_id)
    period = period_label(now)
    already = set(db.execute(select(SpendAlert.threshold).where(
        SpendAlert.user_id == uid, SpendAlert.period == period)).scalars())
    spent = month_spend(db, uid, now)["total_micros"]
    fired = []
    for threshold in THRESHOLDS:
        crossed = spent * 100 >= threshold * cap or (force_cap_reached and threshold == 100)
        if threshold in already or not crossed:
            continue
        alert = SpendAlert(user_id=uid, period=period, threshold=threshold,
                           cap_micros=cap, spent_micros=spent, email_status="pending")
        try:
            with db.begin_nested():
                db.add(alert)
        except IntegrityError:
            continue            # another worker fired it first: exactly once
        fired.append(alert)
    if not fired:
        return []
    db.commit()

    top = max(fired, key=lambda a: a.threshold)
    status = "skipped"
    user = db.get(User, uid)
    if row is not None and row.email_alerts and user is not None and user.email \
            and user.email_verified:
        from app.services import email_sender  # noqa: PLC0415

        try:
            email_sender.send_email(**_alert_email(user.email, top.threshold, spent, cap))
            status = "sent"
        except Exception as exc:  # noqa: BLE001 -- the in-app alert stands
            logger.warning("spend alert email for %s failed: %s", uid, exc)
            status = "failed"
    for alert in fired:
        alert.email_status = status if alert is top else (
            "merged" if status == "sent" else status)
    db.commit()
    return fired


def sweep_alerts(db: Session, now: datetime | None = None) -> int:
    """Beat sweep: every account with a cap. Returns alerts fired."""
    from app.db.models import SpendCap  # noqa: PLC0415

    if not enabled(db):
        return 0
    user_ids = list(db.execute(select(SpendCap.user_id).where(
        SpendCap.monthly_cap_micros.is_not(None))).scalars())
    fired = 0
    for uid in user_ids:
        try:
            fired += len(evaluate_alerts(db, uid, now))
        except Exception:  # noqa: BLE001
            logger.exception("spend_guard: alert sweep failed for %s", uid)
            db.rollback()
    return fired


def alerts_for(db: Session, user_id, now: datetime | None = None) -> list[dict]:
    from app.db.models import SpendAlert  # noqa: PLC0415

    rows = db.execute(select(SpendAlert).where(
        SpendAlert.user_id == _uuid(user_id), SpendAlert.period == period_label(now))
        .order_by(SpendAlert.threshold)).scalars().all()
    return [{"threshold": a.threshold, "fired_at": _iso(a.created_at),
             "spent_usd": usd(a.spent_micros), "cap_usd": usd(a.cap_micros),
             "email_status": a.email_status} for a in rows]


def _iso(value: datetime | None) -> str | None:
    value = _aware(value)
    return value.isoformat() if value else None


# --------------------------------------------------------------------------
# Billable verified results vs internal attempt cost
# --------------------------------------------------------------------------


def billable_summary(db: Session, user_id, now: datetime | None = None,
                     per_unit: dict[str, int] | None = None) -> dict:
    """Vectorae has no customer credit system (spec, Non-goals). The honest
    minimum: verified results this month beside the cost of every attempt."""
    from app.db.models import (  # noqa: PLC0415
        BatchStage, EnrichmentAttempt, LeadBatch, Product, Strategy,
    )

    since = month_start(now)
    uid = _uuid(user_id)
    summaries = db.execute(
        select(LeadBatch.summary_json)
        .join(Strategy, Strategy.id == LeadBatch.strategy_id)
        .join(Product, Product.id == Strategy.product_id)
        .where(Product.user_id == uid, LeadBatch.stage == BatchStage.FINALIZED,
               LeadBatch.updated_at >= since)
    ).scalars().all()
    verified = sum(int(((s or {}).get("counts") or {}).get("verified") or 0) for s in summaries)
    attempts, paid = db.execute(
        select(func.count(EnrichmentAttempt.id),
               func.coalesce(func.sum(case((EnrichmentAttempt.cost_units > 0, 1), else_=0)), 0))
        .where(EnrichmentAttempt.user_id == uid, EnrichmentAttempt.created_at >= since)
    ).one()
    per_unit = per_unit if per_unit is not None else unit_micros(db)
    units = data_units(db, uid, since)
    data_micros = price_units(per_unit, units)
    return {
        "verified_results": verified,
        "finalized_batches": len(summaries),
        "data_attempts": int(attempts or 0),
        "paid_data_attempts": int(paid or 0),
        "data_credits": units,
        "internal_data_usd": usd(data_micros),
        "cost_per_verified_result_usd": (usd(round(data_micros / verified))
                                         if verified and data_micros else None),
        "note": ("Vectorae does not sell credits: nothing here is billed per "
                 "attempt. Verified results are leads that passed verification in "
                 "lead batches finalized this month; internal cost includes every "
                 "attempt, successful or not."),
    }


# --------------------------------------------------------------------------
# Reconciliation (the +/-25% metric)
# --------------------------------------------------------------------------


def _window_end(db: Session, row) -> datetime | None:
    """When the run's window closes: the next allowed forecast for the same
    strategy -- of the same kind (a resume after a strategy run), or of ANY
    kind for a Quickstart (its strategy-wide window must not swallow a later,
    separately forecast lead-sourcing run)."""
    from app.db.models import SpendForecast  # noqa: PLC0415

    if row.strategy_id is None:
        return None
    q = select(func.min(SpendForecast.created_at)).where(
        SpendForecast.user_id == row.user_id, SpendForecast.strategy_id == row.strategy_id,
        SpendForecast.decision == ALLOWED,
        SpendForecast.created_at > row.created_at, SpendForecast.id != row.id)
    if row.kind != QUICKSTART:
        q = q.where(SpendForecast.kind == row.kind)
    return db.execute(q).scalar_one_or_none()


def _data_target(row):
    """Which ledger rows are this run's data spend.
    enrichment: its own batch (batch-specific, so no time end is needed);
    strategy run: the strategy's rows NOT tied to any batch (market intel);
    quickstart: everything on its strategy (its pipeline and its own batch)."""
    from app.db.models import EnrichmentAttempt  # noqa: PLC0415

    if row.kind == ENRICHMENT_RUN:
        return (EnrichmentAttempt.batch_id == row.batch_id
                if row.batch_id is not None else None), False
    if row.strategy_id is None:
        return None, True
    if row.kind == STRATEGY_RUN:
        return ((EnrichmentAttempt.strategy_id == row.strategy_id)
                & EnrichmentAttempt.batch_id.is_(None)), True
    return EnrichmentAttempt.strategy_id == row.strategy_id, True


def _complete(db: Session, row) -> bool:
    from app.db.models import BatchStage, LeadBatch, Strategy, StrategyStatus  # noqa: PLC0415

    if row.kind == ENRICHMENT_RUN:
        batch = db.get(LeadBatch, row.batch_id) if row.batch_id else None
        return batch is not None and batch.stage in (BatchStage.FINALIZED, BatchStage.FAILED)
    strategy = db.get(Strategy, row.strategy_id) if row.strategy_id else None
    return strategy is not None and strategy.status not in (
        StrategyStatus.PENDING, StrategyStatus.RESEARCHING, StrategyStatus.VERIFYING)


def _err(forecast: int, actual: int) -> float | None:
    return None if actual <= 0 else round((forecast - actual) / actual, 4)


def reconcile(db: Session, row, per_unit: dict[str, int] | None = None) -> dict:
    """A stored forecast vs what its run actually cost.

    AI: api_usage of the forecast's account, strategy and purposes from the
    forecast until the next allowed forecast of the same kind for the same
    strategy. Data: enrichment_attempts of the account for the forecast's
    batch (or strategy, for strategy runs / Quickstart) in the same window."""
    from app.db.models import ApiUsage, EnrichmentAttempt  # noqa: PLC0415

    per_unit = per_unit if per_unit is not None else unit_micros(db)
    base = {"forecast_id": str(row.id), "kind": row.kind, "decision": row.decision,
            "created_at": _iso(row.created_at),
            "strategy_id": str(row.strategy_id) if row.strategy_id else None,
            "batch_id": str(row.batch_id) if row.batch_id else None,
            "forecast_usd": usd(row.total_micros), "tolerance": TOLERANCE}
    if row.decision != ALLOWED:
        return {**base, "reconcilable": False, "reason": "refused runs never started"}
    start, end = row.created_at, _window_end(db, row)

    ai_actual = tin = tout = 0
    purposes = list(row.ai_purposes or [])
    if row.strategy_id is not None and purposes:
        q = select(func.coalesce(func.sum(ApiUsage.cost_micros), 0),
                   func.coalesce(func.sum(ApiUsage.input_tokens), 0),
                   func.coalesce(func.sum(ApiUsage.output_tokens), 0)).where(
            ApiUsage.user_id == row.user_id, ApiUsage.strategy_id == row.strategy_id,
            ApiUsage.purpose.in_(purposes), ApiUsage.ts >= start)
        if end is not None:
            q = q.where(ApiUsage.ts < end)
        ai_actual, tin, tout = (int(v or 0) for v in db.execute(q).one())

    actual_units: dict[str, int] = {}
    target, windowed = _data_target(row)
    if target is not None:
        q = (select(EnrichmentAttempt.provider,
                    func.coalesce(func.sum(EnrichmentAttempt.cost_units), 0))
             .where(EnrichmentAttempt.user_id == row.user_id, target,
                    EnrichmentAttempt.created_at >= start)
             .group_by(EnrichmentAttempt.provider))
        if windowed and end is not None:
            q = q.where(EnrichmentAttempt.created_at < end)
        actual_units = {p: int(n) for p, n in db.execute(q).all() if int(n or 0) > 0}

    forecast_units = {p: int(n) for p, n in (row.data_units_json or {}).items()}
    data_forecast = price_units(per_unit, forecast_units)
    data_actual = price_units(per_unit, actual_units)
    forecast_total = int(row.ai_micros) + data_forecast
    actual_total = ai_actual + data_actual
    error = _err(forecast_total, actual_total)
    credits = {}
    for p in sorted(set(forecast_units) | set(actual_units)):
        f, a = forecast_units.get(p, 0), actual_units.get(p, 0)
        e = _err(f, a)
        credits[p] = {"forecast": f, "actual": a, "error_pct": e,
                      "within_tolerance": None if e is None else abs(e) <= TOLERANCE}
    ai_error = _err(int(row.ai_micros), ai_actual)
    return {
        **base, "reconcilable": True, "complete": _complete(db, row),
        "window_end": _iso(end),
        "ai": {"forecast_usd": usd(row.ai_micros), "actual_usd": usd(ai_actual),
               "forecast_tokens": [int(row.ai_input_tokens), int(row.ai_output_tokens)],
               "actual_tokens": [tin, tout], "error_pct": ai_error,
               "within_tolerance": None if ai_error is None else abs(ai_error) <= TOLERANCE},
        "data": {"forecast_usd": usd(data_forecast), "actual_usd": usd(data_actual),
                 "credits": credits},
        "forecast_usd": usd(forecast_total),     # re-priced at today's data prices
        "actual_usd": usd(actual_total),
        "error_pct": error,
        "within_tolerance": None if error is None else abs(error) <= TOLERANCE,
    }


def accuracy(db: Session, user_id, limit: int = 50) -> dict:
    """The success metric over this account's finished, allowed forecasts."""
    from app.db.models import SpendForecast  # noqa: PLC0415

    rows = db.execute(select(SpendForecast).where(
        SpendForecast.user_id == _uuid(user_id), SpendForecast.decision == ALLOWED)
        .order_by(SpendForecast.created_at.desc()).limit(limit)).scalars().all()
    per_unit = unit_micros(db)
    measured = within = 0
    for row in rows:
        rec = reconcile(db, row, per_unit)
        if rec.get("complete") and rec.get("error_pct") is not None:
            measured += 1
            within += int(bool(rec["within_tolerance"]))
    return {"measured_runs": measured, "within_tolerance": within,
            "share_within_tolerance": round(within / measured, 4) if measured else None,
            "tolerance": TOLERANCE}


def owned_forecast(db: Session, forecast_id, user_id):
    from app.db.models import SpendForecast  # noqa: PLC0415

    return db.execute(select(SpendForecast).where(
        SpendForecast.id == _uuid(forecast_id), SpendForecast.user_id == _uuid(user_id))
    ).scalar_one_or_none()


# --------------------------------------------------------------------------
# The usage page
# --------------------------------------------------------------------------


def usage(db: Session, user, now: datetime | None = None) -> dict:
    from app.db.models import SpendForecast  # noqa: PLC0415

    uid = user.id
    per_unit = unit_micros(db)
    prices = unit_prices(db)
    spend = month_spend(db, uid, now, per_unit=per_unit)
    row = cap_row(db, uid)
    cap = cap_micros(db, uid)
    daily_cap, plan_name = usage_meter.account_daily_cap(db, uid)
    recent = db.execute(select(SpendForecast).where(SpendForecast.user_id == uid)
                        .order_by(SpendForecast.created_at.desc()).limit(10)).scalars().all()
    providers = sorted(set(PRICE_SETTINGS) | set(spend["data_units"]))
    return {
        "enabled": True,
        "period": {"label": spend["period"], "start": _iso(spend["since"]),
                   "end": _iso(next_month_start(now))},
        "cap": {"monthly_cap_usd": usd(cap),
                "email_alerts": bool(row.email_alerts) if row is not None else True,
                "thresholds": list(THRESHOLDS)},
        "spend": {"ai_usd": usd(spend["ai_micros"]), "data_usd": usd(spend["data_micros"]),
                  "total_usd": usd(spend["total_micros"]),
                  "data_credits": spend["data_units"],
                  "percent_of_cap": (round(spend["total_micros"] * 100 / cap, 1)
                                     if cap else None),
                  "remaining_usd": usd(cap - spend["total_micros"]) if cap else None},
        "daily": {"plan": plan_name, "limit_usd": usd(daily_cap),
                  "spent_today_usd": usd(usage_meter.account_spent_today_micros(db, uid, now))},
        "pricing": {"providers": [{"provider": p, "usd_per_credit": prices.get(p, 0.0),
                                   "priced": per_unit.get(p, 0) > 0} for p in providers],
                    "ai": {k: list(v) for k, v in usage_meter.price_table(db).items()}},
        "billable": billable_summary(db, uid, now, per_unit=per_unit),
        "alerts": alerts_for(db, uid, now),
        "forecasts": [reconcile(db, f, per_unit) for f in recent],
        "accuracy": accuracy(db, uid),
    }
