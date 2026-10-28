"""Feature 5 — client ROI dashboard.

What Vectorae actually produced for the founder using it, in six numbers they
would recognise as their own business results rather than as software metrics.

    meetings_booked      leads that got past step 3 and booked
    pipeline_value       money sitting in open + won deals RIGHT NOW
    messages_sent        every outgoing message, every channel
    reply_rate           unique leads who replied / leads contacted
    time_saved_hours     messages x 15 min + meetings x 90 min
    revenue_attributed   money from leads that closed won

FLOW VS STOCK -- READ THIS BEFORE CHANGING A DATE FILTER. Four of the six are
FLOWS and are strictly bounded by the date range: messages_sent, reply_rate,
meetings_booked, revenue_attributed. `pipeline_value` is a STOCK -- "what is in
the pipeline right now" -- and is deliberately NOT date-filtered, because the
value of the open pipeline on a range that ended last month is not a number any
founder has ever wanted. The API labels it as such so the two are never read as
the same kind of thing.

WHY leads.status AND NOT deals. app/services/revenue_analytics.py already
reports on the `deals` table with its own currencies and close dates; that is
the finance view. This is the retention artefact -- the proof card a founder
forwards to a peer -- and it reads the lead statuses the founder themselves
drags around the CRM board, which is the pipeline they believe in. The two can
disagree, and when they do the deals table is right about money and this is
right about what the founder thinks they have.

MONEY IS Decimal END TO END. Nothing here converts to float. A proof card that
disagrees with the CRM by a cent is a proof card that loses an argument.
"""

import logging
import uuid as uuid_module
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    Lead,
    LeadStatus,
    Message,
    MessageStatus,
    Outcome,
    OutcomeEvent,
    ROISnapshot,
    Sequence,
    SequenceEnrollment,
    Strategy,
)

logger = logging.getLogger(__name__)

# Hours saved per unit of work Vectorae did instead of the founder.
# 15 minutes to research and write one message by hand; 90 minutes of prep
# and admin around one booked meeting.
HOURS_PER_MESSAGE = 0.25
HOURS_PER_MEETING = 1.5

# The step a lead must have reached for a meeting to count as this system's
# doing. A meeting booked off step 1 is a warm intro, not outbound working.
MEETING_MIN_STEP = 3

# Statuses whose estimated_deal_value counts as pipeline.
PIPELINE_STATUSES = (LeadStatus.MEETING_BOOKED, LeadStatus.PROPOSAL_SENT,
                     LeadStatus.CLOSED_WON)

ZERO = Decimal("0.00")

METRIC_KEYS = ("meetings_booked", "pipeline_value", "messages_sent",
               "reply_rate", "time_saved_hours", "revenue_attributed")

_EMPTY = {"meetings_booked": 0, "pipeline_value": ZERO, "messages_sent": 0,
          "reply_rate": 0.0, "time_saved_hours": 0.0, "revenue_attributed": ZERO}


def _window(date_from: date, date_to: date) -> tuple[datetime, datetime]:
    """[start, end) as aware UTC datetimes; `date_to` is INCLUSIVE."""
    start = datetime.combine(date_from, time.min, tzinfo=timezone.utc)
    end = datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=timezone.utc)
    return start, end


def _aware(value):
    """A stored timestamp as aware UTC (SQLite hands them back naive)."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _in_window(value, start: datetime, end: datetime) -> bool:
    value = _aware(value)
    return value is not None and start <= value < end


def _money(value) -> Decimal:
    """A Numeric column (or None) as a 2dp Decimal. Never raises."""
    if value is None:
        return ZERO
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except Exception:  # noqa: BLE001 -- a corrupt cell is worth zero, not a 500
        return ZERO


def compute_roi_snapshot(db: Session, strategy_id, date_from: date,
                         date_to: date) -> dict:
    """The six ROI metrics for one strategy over an inclusive date range.

    WHAT IT DOES. Counts sends and replies from the message and outcome logs
    inside the window, counts booked meetings that got past step
    MEETING_MIN_STEP, sums estimated deal value by lead status, and derives
    the hours saved from the first two.

    WHAT IT RETURNS. {"meetings_booked": int, "pipeline_value": Decimal,
    "messages_sent": int, "reply_rate": float (0-100, 2dp),
    "time_saved_hours": float, "revenue_attributed": Decimal,
    "leads_contacted": int, "leads_replied": int, "date_from": date,
    "date_to": date}. On failure the same keys with every metric zeroed and an
    extra "error" key.

    WHAT IT NEVER RAISES. Anything. This backs both a dashboard and an
    unattended nightly sweep across every campaign; a bad row or a database
    blip must degrade one campaign's card to zeros, not 500 the page or abort
    the sweep for everyone after it.
    """
    result = dict(_EMPTY)
    result.update({"leads_contacted": 0, "leads_replied": 0,
                   "date_from": date_from, "date_to": date_to})
    try:
        if not isinstance(strategy_id, uuid_module.UUID):
            strategy_id = uuid_module.UUID(str(strategy_id))
        if date_from > date_to:
            raise ValueError("date_from is after date_to")
        start, end = _window(date_from, date_to)

        # --- messages_sent: every channel, anything that actually left ------
        messages_sent = db.execute(
            select(func.count(Message.id))
            .join(Lead, Lead.id == Message.lead_id)
            .where(Lead.strategy_id == strategy_id,
                   Message.status == MessageStatus.SENT,
                   Message.sent_at >= start, Message.sent_at < end)
        ).scalar_one() or 0

        # --- reply_rate: unique people, not unique messages -----------------
        def _distinct_leads(event: OutcomeEvent) -> int:
            return db.execute(
                select(func.count(func.distinct(Outcome.lead_id)))
                .join(Lead, Lead.id == Outcome.lead_id)
                .where(Lead.strategy_id == strategy_id, Outcome.event == event,
                       Outcome.ts >= start, Outcome.ts < end)
            ).scalar_one() or 0

        leads_contacted = _distinct_leads(OutcomeEvent.SENT)
        leads_replied = _distinct_leads(OutcomeEvent.REPLIED)
        reply_rate = round((leads_replied / leads_contacted) * 100, 2) if leads_contacted else 0.0

        # --- meetings_booked -------------------------------------------------
        # "Reached step 3+" is read from the enrollment, not from the message
        # log, because current_step is what the engine itself maintains.
        #
        # A lead with two qualifying enrollments must be counted once. This
        # deduplicates with IN (a subquery on enrollment.lead_id) rather than
        # by JOIN + SELECT DISTINCT, deliberately: PostgreSQL cannot apply
        # DISTINCT to a row containing `json` columns ("could not identify an
        # equality operator for type json") and `leads` has several, so the
        # JOIN + DISTINCT form raises ProgrammingError on PostgreSQL while
        # passing on SQLite. IN needs no row comparison at all.
        booked_enrollment_leads = (
            select(SequenceEnrollment.lead_id)
            .join(Sequence, Sequence.id == SequenceEnrollment.sequence_id)
            .where(Sequence.strategy_id == strategy_id,
                   SequenceEnrollment.current_step >= MEETING_MIN_STEP)
        )
        booked_leads = db.execute(
            select(Lead).where(Lead.strategy_id == strategy_id,
                               Lead.status == LeadStatus.MEETING_BOOKED,
                               Lead.id.in_(booked_enrollment_leads))
        ).scalars().all()
        meetings_booked = sum(
            1 for lead in booked_leads
            if _dated_in_window(db, lead, OutcomeEvent.BOOKED, start, end)
        )

        # --- money ------------------------------------------------------------
        # pipeline_value is a STOCK: what is in the pipeline NOW. See the
        # module docstring -- date-filtering it would answer a question nobody
        # asks. revenue_attributed IS a flow and is dated like the meetings.
        pipeline_value = _money(db.execute(
            select(func.sum(Lead.estimated_deal_value))
            .where(Lead.strategy_id == strategy_id,
                   Lead.status.in_(PIPELINE_STATUSES))
        ).scalar_one_or_none())

        won_leads = db.execute(
            select(Lead).where(Lead.strategy_id == strategy_id,
                               Lead.status == LeadStatus.CLOSED_WON)
        ).scalars().all()
        revenue_attributed = sum(
            (_money(lead.estimated_deal_value) for lead in won_leads
             if _dated_in_window(db, lead, OutcomeEvent.WON, start, end)),
            ZERO,
        )

        result.update({
            "meetings_booked": meetings_booked,
            "pipeline_value": pipeline_value,
            "messages_sent": messages_sent,
            "reply_rate": reply_rate,
            "time_saved_hours": round(
                messages_sent * HOURS_PER_MESSAGE + meetings_booked * HOURS_PER_MEETING,
                2),
            "revenue_attributed": revenue_attributed,
            "leads_contacted": leads_contacted,
            "leads_replied": leads_replied,
        })
        return result
    except Exception as exc:  # noqa: BLE001 -- see the docstring
        logger.warning("ROI snapshot for strategy %s failed: %s: %s",
                       strategy_id, type(exc).__name__, exc)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        result["error"] = f"{type(exc).__name__}: {exc}"[:200]
        return result


def _dated_in_window(db: Session, lead: Lead, event: OutcomeEvent,
                     start: datetime, end: datetime) -> bool:
    """Did this lead reach `event` inside the window?

    Anchored on the outcome row's timestamp when there is one -- that is the
    moment the thing actually happened. A lead whose status was set by hand on
    the CRM board has no outcome row, so `updated_at` stands in: without the
    fallback, every manually-managed pipeline would report zero meetings and
    zero revenue, which is worse than approximate. Never raises.
    """
    try:
        ts = db.execute(
            select(func.max(Outcome.ts))
            .where(Outcome.lead_id == lead.id, Outcome.event == event)
        ).scalar_one_or_none()
        return _in_window(ts if ts is not None else lead.updated_at, start, end)
    except Exception:  # noqa: BLE001
        logger.exception("ROI: could not date %s for lead %s", event, lead.id)
        return False


def upsert_daily_snapshot(db: Session, strategy_id,
                          snapshot_date: date | None = None) -> ROISnapshot | None:
    """Compute today's metrics for one strategy and upsert the day's row.

    WHAT IT DOES. Runs compute_roi_snapshot over the single day
    `snapshot_date` (today in UTC by default) and writes the result to the one
    roi_snapshots row for (strategy, day), inserting it if absent.

    WHAT IT RETURNS. The persisted ROISnapshot, or None when the metrics could
    not be computed (in which case an existing row for that day is left
    untouched -- a failed measurement must never overwrite a real day with
    zeros).

    WHAT IT NEVER RAISES. Anything. The UNIQUE (strategy_id, snapshot_date)
    makes this idempotent by construction: running it twice for the same day
    updates the same row, so the nightly sweep is safe to retry.
    """
    snapshot_date = snapshot_date or datetime.now(timezone.utc).date()
    try:
        if not isinstance(strategy_id, uuid_module.UUID):
            strategy_id = uuid_module.UUID(str(strategy_id))
        metrics = compute_roi_snapshot(db, strategy_id, snapshot_date, snapshot_date)
        if metrics.get("error"):
            return None

        snapshot = db.execute(
            select(ROISnapshot).where(ROISnapshot.strategy_id == strategy_id,
                                      ROISnapshot.snapshot_date == snapshot_date)
        ).scalars().first()
        if snapshot is None:
            snapshot = ROISnapshot(strategy_id=strategy_id, snapshot_date=snapshot_date)
            db.add(snapshot)
        for key in METRIC_KEYS:
            setattr(snapshot, key, metrics[key])
        db.commit()
        return snapshot
    except Exception:  # noqa: BLE001 -- see the docstring
        logger.exception("could not upsert the ROI snapshot for strategy %s",
                         strategy_id)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return None


def scorable_strategy_ids(db: Session) -> list:
    """Every strategy the nightly sweep should snapshot. Never raises."""
    from app.db.models import StrategyStatus  # noqa: PLC0415

    try:
        return list(db.execute(
            select(Strategy.id).where(
                Strategy.status.in_((StrategyStatus.VERIFIED,
                                     StrategyStatus.EXECUTING)))
        ).scalars().all())
    except Exception:  # noqa: BLE001
        logger.exception("ROI: could not list strategies")
        return []
