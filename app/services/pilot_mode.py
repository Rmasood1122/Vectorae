"""F09 -- 30-Day Pilot Mode (docs/features/f09-pilot-mode.md).

Buyers are told to pilot an outbound tool for 30 days before they sign
anything. A pilot only protects the buyer if the success criteria are written
down BEFORE the results come in, the results are the same numbers the vendor
sells on, everything comes out at the end, and nothing converts on its own.

    start_pilot()          day 1: validated criteria, locked (no edit path)
    send_due_scorecard()   weeks 1-4: one email per pilot per week, claimed
                           against UNIQUE (pilot_id, week) before it sends
    end_pilot()            THE ONLY WAY A PILOT ENDS (the sweep after the
                           grace period, "end now", or starting the next
                           pilot): always writes the verdict -- criteria vs
                           the F08 ledger, with the raw numbers and a snapshot
    sweep()                the hourly worker entry point

GRACE PERIOD. The 30 days end at `ends_at`, but the verdict is frozen only at
`ends_at + pilot_mode_verdict_grace_hours` (default 24): a meeting marked held
or a reply classified just after midnight on day 30 still counts. In between,
reads show "verdict pending" with the time the numbers become final. READS
NEVER WRITE: only the sweep and an owner/manager action end a pilot.

BUILT ON F08, NEVER BESIDE IT. Every count and cost is
meetings_ledger.build_ledger for the pilot's period; this module only compares
the ledger's totals with the buyer's targets.

NOTHING HERE TOUCHES BILLING. No function in this module writes users.plan or
billing_subscriptions (tests pin it). The pilot records the plan at start and
end for the verdict page and changes neither; ending a pilot never starts,
renews, upgrades or converts a subscription (there is an annual plan since
2026-10-11, and the only way to it is a checkout the account makes). The one
link runs the other way: billing READS get_active/verdict_final_at so a
checkout during a pilot gets a trial that ends no earlier than the verdict
(docs/features/annual-billing.md).

EMAIL is Vectorae's own transactional gateway (email_sender.send_email) to the
account owner's login address. It is not, and cannot become, an outreach send:
no Gmail, no sequence, no `messages` row.

No model calls, so no usage scope.
"""

from __future__ import annotations

import csv
import io
import logging
import textwrap
import uuid
from collections.abc import Iterable
from datetime import date, datetime, time, timedelta, timezone
from html import escape

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import Pilot, PilotScorecard, Product, Strategy, User
from app.services import meetings_ledger, revenue_analytics

logger = logging.getLogger(__name__)

FLAG = "pilot_mode_enabled"
GRACE_SETTING = "pilot_mode_verdict_grace_hours"
MAX_GRACE_HOURS = 168
PILOT_DAYS = 30
WEEKS = (1, 2, 3, 4)              # scorecards after 7, 14, 21 and 28 days
MAX_EMAIL_ATTEMPTS = 3

ACTIVE, ENDED = "active", "ended"
VERDICT_PENDING = "verdict_pending"          # a presentation phase, not a status
COMPLETED, ENDED_EARLY = "completed", "ended_early"
MET, NOT_MET, NOT_COMPARABLE = "met", "not_met", "not_comparable"
SENDING, SENT, FAILED = "sending", "sent", "failed"

VERDICT_VERSION = "leadpilot.pilot-verdict.v1"
BILLING_STATEMENT = ("The pilot did not change your plan or billing. Ending a pilot "
                     "never starts, renews, upgrades or converts a subscription - "
                     "monthly or annual - and a plan bought during a pilot is not "
                     "charged before the verdict.")

_MAX_COUNT = 100_000
_MAX_MONEY_CENTS = 100_000_000    # 1,000,000.00 in the report currency

# The pilot template: what a buyer can hold Vectorae to. Each `metric` is a
# key of meetings_ledger's totals -- F08 is the only source of numbers. No
# target is pre-filled anywhere: a vendor-suggested target is a sales claim.
CRITERIA: dict[str, dict] = {
    "meetings_booked_min": {
        "label": "Meetings booked", "kind": "min", "unit": "count",
        "metric": "meetings_booked", "max_value": _MAX_COUNT,
    },
    "meetings_held_min": {
        "label": "Meetings held", "kind": "min", "unit": "count",
        "metric": "meetings_held", "max_value": _MAX_COUNT,
    },
    "qualified_conversations_min": {
        "label": "Qualified conversations", "kind": "min", "unit": "count",
        "metric": "qualified_conversations", "max_value": _MAX_COUNT,
    },
    "max_cost_per_meeting_booked_cents": {
        "label": "Cost per booked meeting", "kind": "max", "unit": "money",
        "metric": "cost_per_meeting_booked_cents", "max_value": _MAX_MONEY_CENTS,
        "denominator": "booked meetings",
    },
    "max_cost_per_qualified_conversation_cents": {
        "label": "Cost per qualified conversation", "kind": "max", "unit": "money",
        "metric": "cost_per_qualified_conversation_cents", "max_value": _MAX_MONEY_CENTS,
        "denominator": "qualified conversations",
    },
}
# Ticked in the setup form by default (still with an empty target field).
DEFAULT_SELECTED = ("meetings_booked_min", "qualified_conversations_min",
                    "max_cost_per_meeting_booked_cents")

# The aggregate keys of an F08 ledger kept in a verdict. `events` (lead ids,
# companies) is deliberately NOT kept: a verdict outlives erasure requests.
_SNAPSHOT_KEYS = ("version", "generated_at", "currency", "date_from", "date_to",
                  "strategy_ids", "infrastructure", "campaigns", "totals",
                  "reconciliation", "notes")


class PilotError(ValueError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _aware(value).isoformat()
    return value.isoformat()


def is_enabled(db: Session) -> bool:
    from app.services import system_settings  # noqa: PLC0415

    return bool(system_settings.get(db, FLAG))


def grace(db: Session) -> timedelta:
    """The verdict grace period (admin setting, clamped to 0..168 hours)."""
    from app.services import system_settings  # noqa: PLC0415

    hours = int(system_settings.get(db, GRACE_SETTING))
    return timedelta(hours=max(0, min(hours, MAX_GRACE_HOURS)))


def verdict_final_at(pilot: Pilot, grace_period: timedelta) -> datetime:
    return _aware(pilot.ends_at) + grace_period


def _plan_name(user: User | None) -> str | None:
    if user is None:
        return None
    plan = getattr(user, "plan", None)
    return str(getattr(plan, "value", plan)) if plan is not None else None


# --------------------------------------------------------------------------
# Template and validation
# --------------------------------------------------------------------------


def template() -> dict:
    return {
        "pilot_days": PILOT_DAYS,
        "scorecard_after_days": [w * 7 for w in WEEKS],
        "criteria": [{"key": key, "label": spec["label"], "kind": spec["kind"],
                      "unit": spec["unit"], "min_value": 1, "max_value": spec["max_value"],
                      "default_selected": key in DEFAULT_SELECTED}
                     for key, spec in CRITERIA.items()],
        "billing_statement": BILLING_STATEMENT,
    }


def validate_criteria(raw) -> dict[str, int]:
    """Whole-number targets for known criteria only, at least one. Raises
    PilotError(422) with a message a person can act on."""
    if not isinstance(raw, dict) or not raw:
        raise PilotError("Set at least one success criterion.")
    unknown = sorted(str(k) for k in raw if k not in CRITERIA)
    if unknown:
        raise PilotError(f"Unknown criterion: {', '.join(unknown)}.")
    out: dict[str, int] = {}
    for key in CRITERIA:
        if key not in raw:
            continue
        value = raw[key]
        label = CRITERIA[key]["label"]
        # bool is an int subclass: refuse it explicitly. Floats are refused
        # too -- money is whole cents, counts are whole meetings.
        if isinstance(value, bool) or not isinstance(value, int):
            raise PilotError(f"{label}: the target must be a whole number.")
        if value < 1:
            raise PilotError(f"{label}: the target must be at least 1.")
        if value > CRITERIA[key]["max_value"]:
            raise PilotError(f"{label}: the target may be at most "
                             f"{CRITERIA[key]['max_value']:,}.")
        out[key] = value
    return out


def period_for(started_at: datetime) -> tuple[date, date, datetime]:
    """Day 1 is the UTC date of the start; 30 calendar days; the pilot ends at
    00:00 UTC after day 30."""
    start = _aware(started_at).astimezone(timezone.utc).date()
    end = start + timedelta(days=PILOT_DAYS - 1)
    ends_at = datetime.combine(end + timedelta(days=1), time.min, tzinfo=timezone.utc)
    return start, end, ends_at


# --------------------------------------------------------------------------
# Reads (every query filtered by the account)
# --------------------------------------------------------------------------


def get_owned(db: Session, user_id, pilot_id) -> Pilot | None:
    try:
        pid = uuid.UUID(str(pilot_id))
    except ValueError:
        return None
    return db.execute(
        select(Pilot).where(Pilot.id == pid, Pilot.user_id == user_id)
    ).scalar_one_or_none()


def get_active(db: Session, user_id) -> Pilot | None:
    return db.execute(
        select(Pilot).where(Pilot.user_id == user_id, Pilot.status == ACTIVE)
    ).scalars().first()


def list_pilots(db: Session, user_id) -> list[Pilot]:
    return list(db.execute(
        select(Pilot).where(Pilot.user_id == user_id)
        .order_by(Pilot.started_at.desc(), Pilot.id)
    ).scalars())


def current(db: Session, user_id) -> Pilot | None:
    """The active pilot, else the most recent one, else None."""
    active = get_active(db, user_id)
    if active is not None:
        return active
    pilots = list_pilots(db, user_id)
    return pilots[0] if pilots else None


def scorecards(db: Session, pilot: Pilot) -> list[PilotScorecard]:
    return list(db.execute(
        select(PilotScorecard).where(PilotScorecard.pilot_id == pilot.id,
                                     PilotScorecard.user_id == pilot.user_id)
        .order_by(PilotScorecard.week)
    ).scalars())


def _owned_strategy_ids(db: Session, user_id, wanted: Iterable) -> list[str]:
    ids = []
    for raw in wanted:
        try:
            ids.append(uuid.UUID(str(raw)))
        except ValueError as exc:
            raise PilotError("campaign not found", status_code=404) from exc
    if not ids:
        raise PilotError("Choose at least one campaign, or leave the scope empty for all.")
    owned = {str(sid) for sid in db.execute(
        select(Strategy.id).join(Product, Product.id == Strategy.product_id)
        .where(Product.user_id == user_id, Strategy.id.in_(ids))
    ).scalars()}
    if len(owned) != len(set(ids)):
        raise PilotError("campaign not found", status_code=404)
    return sorted(owned)


# --------------------------------------------------------------------------
# Start
# --------------------------------------------------------------------------


def start_pilot(db: Session, owner: User, *, criteria, strategy_ids: Iterable | None = None,
                scorecard_emails: bool = True, actor_id=None,
                now: datetime | None = None) -> Pilot:
    """Start a 30-day pilot for `owner`'s account. Commits."""
    now = _aware(now) or _now()
    targets = validate_criteria(criteria)
    scope = None if strategy_ids is None else _owned_strategy_ids(db, owner.id, strategy_ids)
    previous = get_active(db, owner.id)
    if previous is not None:
        final_at = verdict_final_at(previous, grace(db))
        if now >= final_at:
            # Its 30 days and the grace period are over; the sweep has just not
            # reached it yet. Finalize it (verdict) and carry on.
            end_pilot(db, previous, reason=COMPLETED, now=now)
        elif now >= _aware(previous.ends_at):
            raise PilotError(
                "The previous pilot's verdict is pending until "
                f"{final_at.strftime('%Y-%m-%d %H:%M')} UTC. End it from the pilot page "
                "to finalize it now, then start a new one.", status_code=409)
        else:
            raise PilotError("A pilot is already running for this account. End it first.",
                             status_code=409)
    start, end, ends_at = period_for(now)
    pilot = Pilot(
        user_id=owner.id, started_by_user_id=actor_id or owner.id,
        active_owner_id=owner.id, status=ACTIVE,
        start_date=start, end_date=end, started_at=now, ends_at=ends_at,
        criteria_json=targets,
        currency=revenue_analytics.primary_currency(db, owner.id),
        strategy_ids_json=scope, scorecard_emails=bool(scorecard_emails),
        plan_at_start=_plan_name(owner),
    )
    db.add(pilot)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        # Two starts raced; uq_pilots_active_owner_id let exactly one through.
        # Anything else (a foreign key, say) is a real error, not "running".
        if "active_owner_id" not in str(getattr(exc, "orig", exc)):
            raise
        raise PilotError("A pilot is already running for this account. End it first.",
                         status_code=409) from exc
    db.refresh(pilot)
    return pilot


def set_scorecard_emails(db: Session, pilot: Pilot, enabled: bool) -> Pilot:
    """The only mutable field of a pilot. Criteria stay locked."""
    pilot.scorecard_emails = bool(enabled)
    db.commit()
    db.refresh(pilot)
    return pilot


# --------------------------------------------------------------------------
# Numbers: F08 only
# --------------------------------------------------------------------------


def _ledger(db: Session, pilot: Pilot, date_to: date, now: datetime) -> dict:
    return meetings_ledger.build_ledger(
        db, pilot.user_id, pilot.start_date, date_to,
        strategy_ids=pilot.strategy_ids_json, now=now)


def _so_far_date(pilot: Pilot, now: datetime) -> date:
    today = _aware(now).astimezone(timezone.utc).date()
    return max(pilot.start_date, min(today, pilot.end_date))


def evaluate(criteria: dict, currency: str, ledger: dict) -> dict:
    """Each criterion against the ledger's totals, with the raw number."""
    totals = ledger["totals"]
    ledger_currency = ledger["currency"]
    rows = []
    for key, spec in CRITERIA.items():
        if key not in criteria:
            continue
        target = int(criteria[key])
        actual = totals.get(spec["metric"])
        money = spec["unit"] == "money"
        if money and ledger_currency != currency:
            status = NOT_COMPARABLE
            note = (f"The target is in {currency} but the ledger now reports in "
                    f"{ledger_currency}, so they were not compared.")
        elif actual is None:
            status = NOT_MET
            note = (f"No {spec.get('denominator', 'results')} in the period, so there is "
                    f"no {spec['label'].lower()}.")
        elif spec["kind"] == "min":
            status = MET if actual >= target else NOT_MET
            note = f"{actual} against a target of at least {target}."
        else:
            status = MET if actual <= target else NOT_MET
            note = (f"{_money(actual, ledger_currency)} against a maximum of "
                    f"{_money(target, currency)}.")
        rows.append({"key": key, "label": spec["label"], "kind": spec["kind"],
                     "unit": spec["unit"], "metric": spec["metric"], "target": target,
                     "actual": actual, "status": status, "met": status == MET,
                     "note": note})
    met = sum(1 for r in rows if r["met"])
    return {"criteria": rows, "met_count": met, "criteria_count": len(rows),
            "outcome": MET if rows and met == len(rows) else NOT_MET,
            "currency": currency, "ledger_currency": ledger_currency}


def _snapshot(ledger: dict) -> dict:
    return {key: ledger[key] for key in _SNAPSHOT_KEYS if key in ledger}


def progress(db: Session, pilot: Pilot, now: datetime | None = None) -> dict:
    """Live numbers so far for an active pilot (nothing stored)."""
    now = _aware(now) or _now()
    date_to = _so_far_date(pilot, now)
    ledger = _ledger(db, pilot, date_to, now)
    result = evaluate(pilot.criteria_json, pilot.currency, ledger)
    result.update({"date_from": pilot.start_date.isoformat(), "date_to": date_to.isoformat(),
                   "totals": ledger["totals"]})
    return result


# --------------------------------------------------------------------------
# End -- the one path, always with a verdict
# --------------------------------------------------------------------------


def is_over(pilot: Pilot, now: datetime | None = None) -> bool:
    """The 30 days are over (the verdict may still be in its grace period)."""
    return (_aware(now) or _now()) >= _aware(pilot.ends_at)


def verdict_due(db: Session, pilot: Pilot, now: datetime | None = None) -> bool:
    """Active, 30 days over AND the grace period passed: freeze the verdict."""
    return (pilot.status == ACTIVE
            and (_aware(now) or _now()) >= verdict_final_at(pilot, grace(db)))


def end_pilot(db: Session, pilot: Pilot, *, reason: str, now: datetime | None = None) -> Pilot:
    """End a pilot and store its verdict. Idempotent: ending an ended pilot
    returns it unchanged. Compare-and-set on status, so the sweep and an
    "end now" racing on the same pilot store exactly one verdict (the loser
    returns the winner's). Commits.

    Writes ONLY the pilots row -- never users.plan, never billing."""
    if reason not in (COMPLETED, ENDED_EARLY):
        raise ValueError(f"unknown end reason {reason!r}")
    if pilot.status != ACTIVE:
        return pilot
    now = _aware(now) or _now()
    date_to = pilot.end_date if reason == COMPLETED else _so_far_date(pilot, now)
    ledger = _ledger(db, pilot, date_to, now)
    owner = db.get(User, pilot.user_id)
    plan_at_end = _plan_name(owner)
    verdict = evaluate(pilot.criteria_json, pilot.currency, ledger)
    verdict.update({
        "version": VERDICT_VERSION,
        "computed_at": now.isoformat(),
        "date_from": pilot.start_date.isoformat(),
        "date_to": date_to.isoformat(),
        "end_reason": reason,
        "plan_at_start": pilot.plan_at_start,
        "plan_at_end": plan_at_end,
        "billing_statement": BILLING_STATEMENT,
        "ledger": _snapshot(ledger),
    })
    result = db.execute(
        update(Pilot)
        .where(Pilot.id == pilot.id, Pilot.user_id == pilot.user_id, Pilot.status == ACTIVE)
        .values(status=ENDED, active_owner_id=None, ended_at=now, end_reason=reason,
                verdict_json=verdict, plan_at_end=plan_at_end)
        .execution_options(synchronize_session=False)
    )
    db.commit()
    db.refresh(pilot)
    if result.rowcount:
        logger.info("pilot %s ended (%s): %s of %s criteria met", pilot.id, reason,
                    verdict["met_count"], verdict["criteria_count"])
    return pilot


# --------------------------------------------------------------------------
# Presentation
# --------------------------------------------------------------------------


def day_number(pilot: Pilot, now: datetime | None = None) -> int | None:
    """1..30 while active, None once ended."""
    if pilot.status != ACTIVE:
        return None
    today = (_aware(now) or _now()).astimezone(timezone.utc).date()
    return max(1, min(PILOT_DAYS, (today - pilot.start_date).days + 1))


def current_week(pilot: Pilot, now: datetime | None = None) -> int:
    """Completed weeks since the start, capped at the last scorecard week."""
    elapsed = (_aware(now) or _now()) - _aware(pilot.started_at)
    return min(max(elapsed.days // 7, 0), WEEKS[-1])


def pilot_out(db: Session, pilot: Pilot, now: datetime | None = None, *,
              with_progress: bool = False) -> dict:
    now = _aware(now) or _now()
    final_at = verdict_final_at(pilot, grace(db))
    if pilot.status != ACTIVE:
        phase = ENDED
    elif is_over(pilot, now):
        phase = VERDICT_PENDING
    else:
        phase = ACTIVE
    criteria = [{"key": key, "label": CRITERIA[key]["label"], "kind": CRITERIA[key]["kind"],
                 "unit": CRITERIA[key]["unit"], "target": pilot.criteria_json[key]}
                for key in CRITERIA if key in (pilot.criteria_json or {})]
    out = {
        "id": str(pilot.id),
        "status": pilot.status,
        # active | verdict_pending (30 days over, numbers final at
        # verdict_final_at) | ended
        "phase": phase,
        "verdict_final_at": final_at.isoformat() if pilot.status == ACTIVE else None,
        "start_date": pilot.start_date.isoformat(),
        "end_date": pilot.end_date.isoformat(),
        "started_at": _iso(pilot.started_at),
        "ends_at": _iso(pilot.ends_at),
        "day": day_number(pilot, now),
        "pilot_days": PILOT_DAYS,
        "criteria": criteria,
        "currency": pilot.currency,
        "strategy_ids": pilot.strategy_ids_json,
        "scorecard_emails": bool(pilot.scorecard_emails),
        "plan_at_start": pilot.plan_at_start,
        "plan_at_end": pilot.plan_at_end,
        "ended_at": _iso(pilot.ended_at),
        "end_reason": pilot.end_reason,
        "exported_at": _iso(pilot.exported_at),
        "export_available": pilot.status == ENDED and pilot.verdict_json is not None,
        "verdict": pilot.verdict_json if pilot.status == ENDED else None,
        "scorecards": [{"week": s.week, "status": s.status, "sent_at": _iso(s.sent_at)}
                       for s in scorecards(db, pilot)],
        "billing_statement": BILLING_STATEMENT,
    }
    if with_progress and pilot.status == ACTIVE:
        out["progress"] = progress(db, pilot, now)
    return out


def _money(cents: int | None, currency: str) -> str:
    return "-" if cents is None else f"{cents / 100:,.2f} {currency}"


def _value(row: dict, currency: str) -> str:
    if row["unit"] == "money":
        return _money(row["actual"], currency)
    return "-" if row["actual"] is None else str(row["actual"])


def _target(row: dict, currency: str) -> str:
    if row["unit"] == "money":
        return f"at most {_money(row['target'], currency)}"
    return f"at least {row['target']}"


# --------------------------------------------------------------------------
# Email (transactional gateway, account owner only)
# --------------------------------------------------------------------------


def _pilot_link() -> str:
    return f"{settings.frontend_url.rstrip('/')}/pilot"


def _send(to: str, subject: str, lines: list[str]) -> None:
    """One transactional email to the account owner. Raises on failure so
    the caller can record it."""
    from app.services.email_sender import send_email  # noqa: PLC0415

    text = "\n".join(lines)
    html = "<p>" + "</p><p>".join(escape(line) for line in lines if line.strip()) + "</p>"
    send_email(to=to, subject=subject, html=html, text=text)


_SO_FAR = {MET: "met so far", NOT_MET: "not yet", NOT_COMPARABLE: "not comparable"}
_FINAL = {MET: "MET", NOT_MET: "NOT MET", NOT_COMPARABLE: "NOT COMPARABLE"}


def scorecard_lines(pilot: Pilot, week: int, result: dict, days_left: int) -> list[str]:
    totals = result["totals"]
    cur = result["ledger_currency"]
    lines = [f"Week {week} of {len(WEEKS)} of the 30-day Vectorae pilot on your account "
             f"({pilot.start_date.isoformat()} to {pilot.end_date.isoformat()}).",
             f"Numbers so far ({result['date_from']} to {result['date_to']}), from the "
             "meetings-per-dollar ledger:"]
    for row in result["criteria"]:
        lines.append(f"- {row['label']}: {_value(row, cur)} (target "
                     f"{_target(row, pilot.currency)}) - {_SO_FAR[row['status']]}")
    lines += [
        f"Total cost {_money(totals['total_cost_cents'], cur)}; qualified conversations "
        f"{totals['qualified_conversations']}; meetings booked {totals['meetings_booked']}; "
        f"meetings held {totals['meetings_held']}.",
        f"{days_left} day(s) left. The verdict is computed after day 30 against the "
        "criteria set on day 1.",
        f"See the pilot: {_pilot_link()}",
        "Ending the pilot changes nothing in your plan or billing.",
        "You get this weekly scorecard because a 30-day pilot is running on your "
        "LeadPilot account. To stop it, untick 'Email me a weekly scorecard' on the "
        "pilot page.",
    ]
    return lines


def _recipient(db: Session, pilot: Pilot) -> tuple[str | None, str | None]:
    """(address, None) or (None, reason). The account owner only; a
    suspended owner is not mailed."""
    owner = db.get(User, pilot.user_id)
    if owner is None or not getattr(owner, "email", None):
        return None, "no_recipient"
    if getattr(owner, "is_suspended", False):
        return None, "owner_suspended"
    return owner.email, None


def _fresh_status(db: Session, pilot: Pilot) -> str | None:
    """The pilot's status as the database has it now (the object may be
    stale: another request may have ended it)."""
    return db.execute(select(Pilot.status).where(
        Pilot.id == pilot.id, Pilot.user_id == pilot.user_id)).scalar_one_or_none()


def send_due_scorecard(db: Session, pilot: Pilot, now: datetime | None = None) -> str:
    """Send this week's scorecard if it is due and not sent. Returns what
    happened. Claimed against UNIQUE (pilot_id, week) BEFORE sending, so it is
    idempotent per pilot per week under re-runs and concurrent workers; the
    pilot's status is re-read from the database before and after the claim,
    so a pilot ended by another request gets no scorecard."""
    now = _aware(now) or _now()
    if (pilot.status != ACTIVE or _fresh_status(db, pilot) != ACTIVE
            or is_over(pilot, now)):
        return "not_active"
    if not pilot.scorecard_emails:
        return "opted_out"
    week = current_week(pilot, now)
    if week < 1:
        return "not_due"
    to, why_not = _recipient(db, pilot)
    if not to:
        return why_not

    row = db.execute(select(PilotScorecard).where(
        PilotScorecard.pilot_id == pilot.id, PilotScorecard.user_id == pilot.user_id,
        PilotScorecard.week == week)).scalar_one_or_none()
    if row is None:
        row = PilotScorecard(pilot_id=pilot.id, user_id=pilot.user_id, week=week,
                             status=SENDING, attempts=1)
        db.add(row)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return "already_claimed"
    elif row.status == FAILED and row.attempts < MAX_EMAIL_ATTEMPTS:
        claimed = db.execute(
            update(PilotScorecard)
            .where(PilotScorecard.id == row.id, PilotScorecard.status == FAILED,
                   PilotScorecard.attempts == row.attempts)
            .values(status=SENDING, attempts=row.attempts + 1)
            .execution_options(synchronize_session=False)
        ).rowcount
        db.commit()
        if claimed != 1:
            return "already_claimed"
        db.refresh(row)
    else:
        # sent, given up, or in flight on another worker (a crash mid-send
        # leaves `sending`: better one missing scorecard than a duplicate).
        return "already_sent" if row.status == SENT else "skipped"

    if _fresh_status(db, pilot) != ACTIVE:
        # Ended between the first check and the claim: release the claim.
        db.delete(row)
        db.commit()
        return "not_active"
    try:
        result = progress(db, pilot, now)
        days_left = max(0, (pilot.end_date - _so_far_date(pilot, now)).days)
        subject = (f"Pilot week {week} of {len(WEEKS)}: {result['met_count']} of "
                   f"{result['criteria_count']} criteria met so far")
        _send(to, subject, scorecard_lines(pilot, week, result, days_left))
    except Exception as exc:  # noqa: BLE001 -- recorded, retried on a later sweep
        db.rollback()
        row.status, row.error = FAILED, f"{type(exc).__name__}: {exc}"[:300]
        db.commit()
        logger.warning("pilot %s week %s scorecard failed: %s", pilot.id, week, exc)
        return "failed"
    row.status, row.sent_at, row.error = SENT, now, None
    row.summary_json = {"date_from": result["date_from"], "date_to": result["date_to"],
                        "met_count": result["met_count"],
                        "criteria_count": result["criteria_count"],
                        "criteria": [{"key": r["key"], "target": r["target"],
                                      "actual": r["actual"], "status": r["status"]}
                                     for r in result["criteria"]]}
    db.commit()
    return SENT


def verdict_lines(pilot: Pilot) -> list[str]:
    v = pilot.verdict_json or {}
    cur = v.get("ledger_currency") or pilot.currency
    how = "ended early" if pilot.end_reason == ENDED_EARLY else "finished its 30 days"
    lines = [f"The Vectorae pilot on your account {how} "
             f"({v.get('date_from')} to {v.get('date_to')}).",
             f"Verdict: {v.get('met_count', 0)} of {v.get('criteria_count', 0)} success "
             f"criteria met - {'all criteria met' if v.get('outcome') == MET else 'not all criteria met'}."]
    for row in v.get("criteria", []):
        lines.append(f"- {row['label']}: {_value(row, cur)} (target "
                     f"{_target(row, pilot.currency)}) - {_FINAL[row['status']]}")
    lines += [
        f"The verdict page stays available: {_pilot_link()}",
        "From that page you can download the verdict (PDF or CSV) and your full account "
        "data (JSON).",
        BILLING_STATEMENT,
    ]
    return lines


def _verdict_email_claimable():
    """SQL condition: never tried, or failed with attempts left. `sending`
    (claimed, outcome unknown) and `sent` are never picked up again."""
    return or_(Pilot.verdict_email_status.is_(None),
               and_(Pilot.verdict_email_status == FAILED,
                    Pilot.verdict_email_attempts < MAX_EMAIL_ATTEMPTS))


def send_verdict_email(db: Session, pilot: Pilot, now: datetime | None = None) -> str:
    """The once-only verdict notice, claimed BEFORE sending (the scorecard's
    pattern): the row is set to `sending` by a compare-and-set; success ->
    `sent`, an exception -> `failed` (retried, at most 3 attempts in all).
    A crash between send and record leaves `sending`, which is never retried
    automatically -- a missing notice is better than a duplicate one, and the
    verdict page itself is always there."""
    now = _aware(now) or _now()
    if pilot.status != ENDED or pilot.verdict_json is None:
        return "not_due"
    if pilot.verdict_email_status == SENT:
        return "already_sent"
    if pilot.verdict_email_status == SENDING:
        return "skipped"
    attempts = int(pilot.verdict_email_attempts or 0)
    if attempts >= MAX_EMAIL_ATTEMPTS:
        return "gave_up"
    to, why_not = _recipient(db, pilot)
    if not to:
        return why_not
    claimed = db.execute(
        update(Pilot)
        .where(Pilot.id == pilot.id, Pilot.user_id == pilot.user_id,
               Pilot.status == ENDED, Pilot.verdict_email_attempts == attempts,
               _verdict_email_claimable())
        .values(verdict_email_status=SENDING, verdict_email_attempts=attempts + 1)
        .execution_options(synchronize_session=False)
    ).rowcount
    db.commit()
    db.refresh(pilot)
    if claimed != 1:
        return "already_claimed"
    v = pilot.verdict_json
    subject = (f"Your 30-day pilot verdict: {v['met_count']} of {v['criteria_count']} "
               "criteria met")
    try:
        _send(to, subject, verdict_lines(pilot))
    except Exception as exc:  # noqa: BLE001 -- retried on a later sweep
        logger.warning("pilot %s verdict email failed: %s", pilot.id, exc)
        db.rollback()
        pilot.verdict_email_status = FAILED
        db.commit()
        return "failed"
    pilot.verdict_email_status, pilot.verdict_emailed_at = SENT, now
    db.commit()
    return SENT


# --------------------------------------------------------------------------
# The sweep
# --------------------------------------------------------------------------


def sweep(db: Session, now: datetime | None = None) -> dict:
    """Hourly: freeze the verdict of pilots whose 30 days AND grace period are
    over, send due scorecards, send verdict notices. Flag off -> does nothing
    at all. Each pilot is handled with its own user_id; one failing pilot never
    stops the others."""
    if not is_enabled(db):
        return {"skipped": "disabled"}
    now = _aware(now) or _now()
    counts = {"ended": 0, "scorecards_sent": 0, "verdict_emails_sent": 0, "errors": 0}
    active_ids = list(db.execute(select(Pilot.id).where(Pilot.status == ACTIVE)).scalars())
    for pid in active_ids:
        try:
            pilot = db.get(Pilot, pid)
            if pilot is None or pilot.status != ACTIVE:
                continue
            if verdict_due(db, pilot, now):
                end_pilot(db, pilot, reason=COMPLETED, now=now)
                counts["ended"] += 1
            elif send_due_scorecard(db, pilot, now) == SENT:
                counts["scorecards_sent"] += 1
        except Exception:  # noqa: BLE001
            db.rollback()
            counts["errors"] += 1
            logger.exception("pilot sweep failed for pilot %s", pid)
    pending = list(db.execute(select(Pilot.id).where(
        Pilot.status == ENDED, _verdict_email_claimable())).scalars())
    for pid in pending:
        try:
            pilot = db.get(Pilot, pid)
            if pilot is not None and send_verdict_email(db, pilot, now) == SENT:
                counts["verdict_emails_sent"] += 1
        except Exception:  # noqa: BLE001
            db.rollback()
            counts["errors"] += 1
            logger.exception("pilot verdict email failed for pilot %s", pid)
    return counts


# --------------------------------------------------------------------------
# End-of-pilot export (verdict PDF / CSV)
# --------------------------------------------------------------------------

VERDICT_COLUMNS = ("criterion", "label", "kind", "target", "actual", "unit", "result", "note")


def verdict_csv(pilot: Pilot, view: str = "verdict") -> str:
    v = pilot.verdict_json or {}
    if view == "campaigns":
        return meetings_ledger.to_csv(v["ledger"], view="campaigns")
    if view != "verdict":
        raise ValueError(f"unknown view {view!r}")
    cur = v.get("ledger_currency") or pilot.currency
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(VERDICT_COLUMNS)
    for row in v.get("criteria", []):
        money = row["unit"] == "money"
        writer.writerow([
            row["key"], meetings_ledger._cell(row["label"]), row["kind"],
            meetings_ledger._money(row["target"]) if money else row["target"],
            (meetings_ledger._money(row["actual"]) if money
             else ("" if row["actual"] is None else row["actual"])),
            (pilot.currency if money else "count"), row["status"],
            meetings_ledger._cell(row["note"]),
        ])
    writer.writerow(["OUTCOME", "", "", v.get("criteria_count", 0), v.get("met_count", 0),
                     "criteria met", v.get("outcome", ""), ""])
    if cur != pilot.currency:
        writer.writerow(["NOTE", "", "", "", "", "", "",
                         f"targets in {pilot.currency}; ledger in {cur}"])
    return buffer.getvalue()


def verdict_pdf(pilot: Pilot) -> bytes:
    from app.services.audit_trail import render_lines_pdf  # noqa: PLC0415

    v = pilot.verdict_json or {}
    cur = v.get("ledger_currency") or pilot.currency
    lines: list[tuple[str, str]] = [
        ("Vectorae 30-day pilot verdict", "title"),
        (f"Pilot {v.get('date_from')} to {v.get('date_to')}  |  "
         f"{'ended early' if pilot.end_reason == ENDED_EARLY else 'completed'}  |  "
         f"computed {str(v.get('computed_at', ''))[:19]} UTC", "body"),
        (f"Outcome: {v.get('met_count', 0)} of {v.get('criteria_count', 0)} criteria met "
         f"({'MET' if v.get('outcome') == MET else 'NOT MET'})", "head"),
    ]
    for row in v.get("criteria", []):
        lines.append((f"{row['label']}: {_value(row, cur)}  (target "
                      f"{_target(row, pilot.currency)})  {_FINAL[row['status']]}", "body"))
        lines += [(f"    {part}", "body")
                  for part in textwrap.wrap(meetings_ledger._ascii(row["note"]), 110)]
    lines.append((f"Plan at start: {v.get('plan_at_start') or '-'}  |  plan at end: "
                  f"{v.get('plan_at_end') or '-'}", "body"))
    lines += [(part, "body") for part in textwrap.wrap(BILLING_STATEMENT, 110)]
    lines.append(("", "body"))
    lines += meetings_ledger.pdf_lines(v["ledger"])
    return render_lines_pdf(lines)


def mark_exported(db: Session, pilot: Pilot, now: datetime | None = None) -> None:
    if pilot.exported_at is None:
        pilot.exported_at = _aware(now) or _now()
        db.commit()
