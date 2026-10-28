"""Daily send approval -- a person says "yes" to automatic sending once a day.

Founder decision (2026-10-11): before Vectorae sends anything AUTOMATICALLY on
a given day, the account owner (or a workspace owner/manager) is notified and
decides, per channel. For LinkedIn they are told plainly that LinkedIn may
restrict or suspend accounts that use automation. Spec:
docs/features/daily-send-approval.md. THIS IS A LEGAL/SAFETY GUARD.

WHERE IT IS ENFORCED. `send_hold()` is called by
app/workers/outreach_tasks.py::send_message_impl after every cancel/skip gate
and after the send-window check, before caps, claim and render. A message it
holds stays SCHEDULED with `scheduled_at` moved and `error` prefixed
ERROR_PREFIX -- deferred, never failed, never dropped, never silently sent.
Any error inside fails CLOSED: the caller defers the message an hour.

AUTOMATIC vs HUMAN. A message a person approved one by one is already a human
decision and is not gated: an APPROVED send review, or a Reply Copilot answer
a person approved (draft.auto_sent is false). Everything else -- sequence
steps, automatic follow-ups, re-engagement, "not now" return visits, Reply
Copilot auto-sends -- is automatic. Unknown = automatic (fail closed).

THE DAY. The calendar day in the account's timezone. There is no account
timezone column; the owner's earliest active calendar availability row is the
only place they state one. Without it the day is UTC (and the API says so).
An approval is valid until the end of that local day.
"""

from __future__ import annotations

import logging
import uuid as uuid_module
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from html import escape
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import (
    CalendarAvailability,
    ComplianceAuditLog,
    DailySendApproval,
    EnrollmentStatus,
    Lead,
    Message,
    MessageStatus,
    Product,
    ReplyDraft,
    SendReview,
    SequenceEnrollment,
    Strategy,
    User,
)

logger = logging.getLogger(__name__)

FLAG = "daily_send_approval_enabled"
CHANNELS_SETTING = "daily_send_approval_channels"

#: Every channel the send path can send on automatically.
ALL_CHANNELS: tuple[str, ...] = ("email", "linkedin", "whatsapp", "phone")
CHANNEL_LABELS = {"email": "Email", "linkedin": "LinkedIn", "whatsapp": "WhatsApp",
                  "phone": "AI phone calls"}

PENDING, APPROVED, DECLINED = "pending", "approved", "declined"

#: messages.error prefix of a message this gate deferred -- what the release
#: on approval looks for, and what the lead timeline shows.
ERROR_PREFIX = "daily_send_approval: "

SEND_RESULT_AWAITING = "deferred_awaiting_send_approval"
SEND_RESULT_DECLINED = "deferred_send_approval_declined"
SEND_RESULT_CHANNEL = "deferred_channel_not_approved"
SEND_RESULT_UNAVAILABLE = "deferred_send_approval_unavailable"

#: How long a message waits when the approval could not be checked at all.
UNAVAILABLE_RETRY = timedelta(hours=1)

AUDIT_APPROVED = "send_approval_approved"
AUDIT_DECLINED = "send_approval_declined"
AUDIT_CHANNEL = "daily_approval"

EVENT = "send_approval_needed"
APPROVAL_PATH = "/send-approval"

#: The LinkedIn risk disclosure. Bump the VERSION whenever the TEXT changes:
#: the version a person saw is recorded with their decision.
LINKEDIN_WARNING_VERSION = "li-automation-2026-10-11"
LINKEDIN_QUESTION = "Do you want to proceed with automatic LinkedIn sending today?"
LINKEDIN_WARNING_TEXT = (
    "LinkedIn's User Agreement does not allow automated tools to send messages or "
    "connection requests. LinkedIn may restrict or suspend LinkedIn accounts that use "
    "automation, including Vectorae's automatic LinkedIn sending through your connected "
    f"account. {LINKEDIN_QUESTION}"
)

TZ_SOURCE_CALENDAR = "calendar_availability"
TZ_SOURCE_UTC = "utc_fallback"


class ApprovalError(ValueError):
    """A decision the API must refuse. `status` is the HTTP code."""

    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


@dataclass
class Hold:
    """What the send path does with a held message."""
    result: str
    error: str
    until: datetime
    user_id: uuid_module.UUID
    local_date: date
    tz_name: str
    notify: bool


# --------------------------------------------------------------------------
# Settings, timezone, the day
# --------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    """A UTC-aware datetime (stored instants are compared in UTC)."""
    if value is None:
        return None
    value = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _uuid(value) -> uuid_module.UUID:
    return value if isinstance(value, uuid_module.UUID) else uuid_module.UUID(str(value))


def enabled(db: Session) -> bool:
    from app.services import system_settings  # noqa: PLC0415

    return bool(system_settings.get(db, FLAG))


def gated_channels(db: Session) -> tuple[str, ...]:
    """The channels whose automatic sends need today's approval. Unknown
    names are ignored; a value with no valid name gates EVERY channel."""
    from app.services import system_settings  # noqa: PLC0415

    raw = str(system_settings.get(db, CHANNELS_SETTING) or "")
    wanted = {part.strip().lower() for part in raw.split(",")}
    chosen = tuple(c for c in ALL_CHANNELS if c in wanted)
    return chosen or ALL_CHANNELS


def account_timezone(db: Session, user_id) -> tuple[str, str]:
    """(IANA zone, source). The timezone of the owner's EARLIEST active
    calendar availability row -- ordered by created_at, then id, so two rows
    created in the same instant still give one deterministic answer -- else
    UTC (also for an invalid zone name)."""
    tz_name = db.execute(
        select(CalendarAvailability.timezone)
        .where(CalendarAvailability.user_id == _uuid(user_id),
               CalendarAvailability.is_active.is_(True))
        .order_by(CalendarAvailability.created_at, CalendarAvailability.id)
        .limit(1)
    ).scalars().first()
    if tz_name:
        try:
            ZoneInfo(tz_name)
            return tz_name, TZ_SOURCE_CALENDAR
        except (ZoneInfoNotFoundError, ValueError):
            logger.warning("account %s has an invalid calendar timezone %r -- using UTC",
                           user_id, tz_name)
    return "UTC", TZ_SOURCE_UTC


def local_day(now: datetime, tz_name: str) -> tuple[date, datetime, datetime]:
    """(local date, start of that day in UTC, end of it = next local midnight
    in UTC). DST-safe: both midnights are resolved in the zone itself."""
    zone = ZoneInfo(tz_name)
    local = _aware(now).astimezone(zone)
    today = local.date()
    start = datetime.combine(today, time(0, 0), tzinfo=zone)
    end = datetime.combine(today + timedelta(days=1), time(0, 0), tzinfo=zone)
    return today, start.astimezone(timezone.utc), end.astimezone(timezone.utc)


# --------------------------------------------------------------------------
# Automatic vs human
# --------------------------------------------------------------------------


def is_automatic(db: Session, message: Message) -> bool:
    """False only for a message a person approved one by one."""
    from app.services import reply_copilot, send_review  # noqa: PLC0415

    review_status = db.execute(
        select(SendReview.status).where(SendReview.message_id == message.id)
    ).scalars().first()
    if review_status == send_review.APPROVED:
        return False
    if message.origin == reply_copilot.ORIGIN:
        auto_sent = db.execute(
            select(ReplyDraft.auto_sent).where(ReplyDraft.message_id == message.id)
        ).scalars().first()
        # No draft row found -> treated as automatic (fails closed).
        return auto_sent is not False
    return True


def _automatic_clause(user_id: uuid_module.UUID):
    """SQL twin of is_automatic, for counting and releasing."""
    from app.services import send_review  # noqa: PLC0415

    approved_reviews = select(SendReview.message_id).where(
        SendReview.user_id == user_id, SendReview.status == send_review.APPROVED,
        SendReview.message_id.is_not(None))
    human_copilot = select(ReplyDraft.message_id).where(
        ReplyDraft.user_id == user_id, ReplyDraft.auto_sent.is_(False),
        ReplyDraft.message_id.is_not(None))
    return and_(Message.id.not_in(approved_reviews), Message.id.not_in(human_copilot))


def _owned_messages(user_id: uuid_module.UUID):
    """SQL: the ids of this account's messages (messages -> leads ->
    strategies -> products.user_id)."""
    return (select(Message.id)
            .join(Lead, Lead.id == Message.lead_id)
            .join(Strategy, Strategy.id == Lead.strategy_id)
            .join(Product, Product.id == Strategy.product_id)
            .where(Product.user_id == user_id))


# --------------------------------------------------------------------------
# Rows
# --------------------------------------------------------------------------


def approval_for(db: Session, user_id, local_date: date) -> DailySendApproval | None:
    return db.execute(
        select(DailySendApproval).where(DailySendApproval.user_id == _uuid(user_id),
                                        DailySendApproval.local_date == local_date)
    ).scalar_one_or_none()


def _get_or_create(db: Session, user_id, local_date: date, tz_name: str) -> DailySendApproval:
    row = approval_for(db, user_id, local_date)
    if row is not None:
        return row
    row = DailySendApproval(user_id=_uuid(user_id), local_date=local_date, timezone=tz_name,
                            status=PENDING, channels_json=[])
    try:
        with db.begin_nested():
            db.add(row)
    except IntegrityError:
        # Another worker created today's row first.
        return approval_for(db, user_id, local_date)
    return row


def _lock(db: Session, row_id) -> DailySendApproval:
    """Fresh read of a row, row-locked until the transaction ends
    (SELECT ... FOR UPDATE on PostgreSQL; SQLite ignores the lock but the
    read is still fresh). Every read-merge-write and every hold commit goes
    through this, so two decisions -- or a decision and a hold -- serialise."""
    return db.execute(
        select(DailySendApproval).where(DailySendApproval.id == row_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()


def effective_decision(db: Session, user_id, now: datetime) -> DailySendApproval | None:
    """The decision in force at `now`: the most recently DECIDED row whose
    stored `valid_until` instant is still ahead. Never re-derived from the
    current timezone, so a timezone change cannot stretch a decision."""
    now = _aware(now)
    return db.execute(
        select(DailySendApproval)
        .where(DailySendApproval.user_id == _uuid(user_id),
               DailySendApproval.status.in_([APPROVED, DECLINED]),
               DailySendApproval.valid_until.is_not(None),
               DailySendApproval.valid_until > now)
        .order_by(DailySendApproval.decided_at.desc(), DailySendApproval.id.desc())
        .limit(1)
        .execution_options(populate_existing=True)
    ).scalars().first()


def approved_channels(row: DailySendApproval | None) -> list[str]:
    if row is None or row.status != APPROVED:
        return []
    return [c for c in (row.channels_json or []) if c in ALL_CHANNELS]


def _channel_hold(db: Session, owner: uuid_module.UUID, channel: str,
                  now: datetime) -> Hold | None:
    """The decision for one automatic send on `channel` for `owner` at `now`."""
    tz_name, _source = account_timezone(db, owner)
    today, _start, end = local_day(now, tz_name)
    decision = effective_decision(db, owner, now)
    label = CHANNEL_LABELS[channel]
    if decision is not None and decision.status == APPROVED:
        if channel in approved_channels(decision):
            return None
        return Hold(SEND_RESULT_CHANNEL,
                    f"{ERROR_PREFIX}{label} was not approved for automatic sending today "
                    f"({decision.local_date.isoformat()}, {decision.timezone}) -- waiting",
                    _aware(decision.valid_until), owner, today, tz_name, notify=False)
    if decision is not None:
        return Hold(SEND_RESULT_DECLINED,
                    f"{ERROR_PREFIX}automatic sending was declined for today "
                    f"({decision.local_date.isoformat()}, {decision.timezone}) -- waiting",
                    _aware(decision.valid_until), owner, today, tz_name, notify=False)
    row = approval_for(db, owner, today)
    return Hold(SEND_RESULT_AWAITING,
                f"{ERROR_PREFIX}waiting for today's approval of automatic sending "
                f"({today.isoformat()}, {tz_name})",
                end, owner, today, tz_name, notify=row is None or row.notified_at is None)


# --------------------------------------------------------------------------
# The send-path gate
# --------------------------------------------------------------------------


def send_hold(db: Session, message: Message, strategy: Strategy,
              now: datetime | None = None) -> Hold | None:
    """None = this message may continue through the remaining gates. A Hold =
    defer it. RAISES on any lookup failure; the caller fails closed."""
    now = _aware(now or _now())
    if not enabled(db):
        return None
    channel = message.channel.value
    if channel not in gated_channels(db):
        return None
    if not is_automatic(db, message):
        return None

    from app.services import notifications  # noqa: PLC0415

    owner = notifications.owner_of_strategy(db, strategy)
    if owner is None:
        raise LookupError(f"no account owner for strategy {strategy.id}")
    return _channel_hold(db, owner, channel, now)


def confirm_hold(db: Session, hold: Hold, channel: str, now: datetime | None = None) -> bool:
    """Called AFTER the caller tagged the message, BEFORE it commits the hold.

    Closes the race "a worker computed a hold, then the owner approved and
    the release ran before the tag was committed -> the message sits parked
    until midnight although today is approved". Today's row is created if
    needed and LOCKED (approve/decline lock the same row), then the decision
    is read fresh. True = still hold; False = an approval for this channel
    landed meanwhile, do not hold. RAISES on error (the caller holds)."""
    now = _aware(now or _now())
    row = _get_or_create(db, hold.user_id, hold.local_date, hold.tz_name)
    _lock(db, row.id)
    decision = effective_decision(db, hold.user_id, now)
    return not (decision is not None and decision.status == APPROVED
                and channel in approved_channels(decision))


def network_hold(db: Session, user_id, now: datetime | None = None) -> Hold | None:
    """The gate for a QUEUED LinkedIn network message (bulk send: up to 25
    Celery jobs sending through Unipile on the customer's LinkedIn account --
    automation, so it needs today's LinkedIn approval). A single interactive
    send is a person's action and does not come through here. RAISES on
    error; the caller fails closed."""
    now = _aware(now or _now())
    if not enabled(db) or "linkedin" not in gated_channels(db):
        return None
    return _channel_hold(db, _uuid(user_id), "linkedin", now)


# --------------------------------------------------------------------------
# Counts and state
# --------------------------------------------------------------------------


def _network_queued(db: Session, user_id: uuid_module.UUID) -> int:
    from app.db.models import LinkedInNetworkMessage  # noqa: PLC0415

    return int(db.execute(
        select(func.count(LinkedInNetworkMessage.id))
        .where(LinkedInNetworkMessage.user_id == user_id,
               LinkedInNetworkMessage.status == "queued")
    ).scalar_one())


def queued_counts(db: Session, user_id, now: datetime | None = None,
                  tz_name: str | None = None) -> dict[str, int]:
    """Automatic messages queued for today per gated channel -- only what the
    send path would actually send: SCHEDULED, due before the end of the local
    day (or already deferred by this gate), in an ACTIVE campaign, on an
    enrollment that is not paused and not stopped (a Reply Copilot auto-answer
    may answer the stop, as in the send path). LinkedIn also counts queued
    LinkedIn network (bulk) messages."""
    from app.services import reply_copilot  # noqa: PLC0415
    from app.services import sequence_engine as engine  # noqa: PLC0415

    now = _aware(now or _now())
    uid = _uuid(user_id)
    tz_name = tz_name or account_timezone(db, uid)[0]
    _today, _start, end = local_day(now, tz_name)
    gated = gated_channels(db)
    rows = db.execute(
        select(Message.channel, func.count(Message.id))
        .join(Lead, Lead.id == Message.lead_id)
        .join(Strategy, Strategy.id == Lead.strategy_id)
        .join(Product, Product.id == Strategy.product_id)
        .join(SequenceEnrollment, and_(SequenceEnrollment.sequence_id == Message.sequence_id,
                                       SequenceEnrollment.lead_id == Message.lead_id))
        .where(Product.user_id == uid,
               Strategy.campaign_state == engine.CAMPAIGN_ACTIVE,
               SequenceEnrollment.status != EnrollmentStatus.PAUSED,
               or_(SequenceEnrollment.status != EnrollmentStatus.STOPPED,
                   Message.origin == reply_copilot.ORIGIN),
               Message.status == MessageStatus.SCHEDULED,
               or_(Message.scheduled_at < end, Message.scheduled_at.is_(None),
                   Message.error.like(f"{ERROR_PREFIX}%")),
               _automatic_clause(uid))
        .group_by(Message.channel)
    ).all()
    counts = {c: 0 for c in gated}
    for channel, n in rows:
        value = getattr(channel, "value", channel)
        if value in counts:
            counts[value] = int(n)
    if "linkedin" in counts:
        counts["linkedin"] += _network_queued(db, uid)
    return counts


def linkedin_warning() -> dict:
    return {"version": LINKEDIN_WARNING_VERSION, "text": LINKEDIN_WARNING_TEXT,
            "question": LINKEDIN_QUESTION}


def state(db: Session, user_id, now: datetime | None = None) -> dict:
    """Today's approval state for the account, for the API and the banner.
    Status and channels are the decision IN FORCE (effective_decision)."""
    now = _aware(now or _now())
    uid = _uuid(user_id)
    tz_name, source = account_timezone(db, uid)
    today, _start, end = local_day(now, tz_name)
    decision = effective_decision(db, uid, now)
    row = approval_for(db, uid, today)
    counts = queued_counts(db, uid, now, tz_name)
    approved = decision is not None and decision.status == APPROVED
    return {
        "enabled": enabled(db),
        "date": today.isoformat(),
        "timezone": tz_name,
        "timezone_source": source,
        "valid_until": (_aware(decision.valid_until) if decision else end).isoformat(),
        "status": decision.status if decision is not None else PENDING,
        "approved_channels": approved_channels(decision),
        "gated_channels": list(gated_channels(db)),
        "queued": counts,
        "queued_total": sum(counts.values()),
        "linkedin_risk_acknowledged": bool(approved and decision.linkedin_risk_acknowledged),
        "linkedin_warning_version_acknowledged": (decision.linkedin_warning_version
                                                  if approved else None),
        "decided_at": decision.decided_at.isoformat() if decision and decision.decided_at
        else None,
        "decided_by_user_id": (str(decision.decided_by_user_id)
                               if decision and decision.decided_by_user_id else None),
        "notified_at": row.notified_at.isoformat() if row and row.notified_at else None,
        "linkedin_warning": linkedin_warning(),
        "channel_labels": dict(CHANNEL_LABELS),
    }


# --------------------------------------------------------------------------
# Decisions
# --------------------------------------------------------------------------


def _audit(db: Session, user_id: uuid_module.UUID, decision: str, checks: dict) -> None:
    """compliance_audit_log row, in the caller's transaction. No lead, no
    message: this is an account-level decision."""
    db.add(ComplianceAuditLog(user_id=user_id, lead_id=None, message_id=None,
                              channel=AUDIT_CHANNEL, region=None, regime=None,
                              decision=decision, checks_json=checks))


def _release(db: Session, user_id: uuid_module.UUID, channels: list[str],
             now: datetime) -> int:
    """Messages this gate deferred, on `channels`, back to due-now. They go
    through EVERY gate again. Not committed here."""
    from app.db.models import ChannelType  # noqa: PLC0415

    if not channels:
        return 0
    result = db.execute(
        update(Message)
        .where(Message.id.in_(_owned_messages(user_id)),
               Message.status == MessageStatus.SCHEDULED,
               Message.error.like(f"{ERROR_PREFIX}%"),
               Message.channel.in_([ChannelType(c) for c in channels]))
        .values(scheduled_at=now, error=None)
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)


def _held_network(db: Session, user_id: uuid_module.UUID) -> list:
    """Queued LinkedIn network messages this gate held, oldest first."""
    from app.db.models import LinkedInNetworkMessage  # noqa: PLC0415

    return list(db.execute(
        select(LinkedInNetworkMessage)
        .where(LinkedInNetworkMessage.user_id == user_id,
               LinkedInNetworkMessage.status == "queued",
               LinkedInNetworkMessage.error.like(f"{ERROR_PREFIX}%"))
        .order_by(LinkedInNetworkMessage.created_at, LinkedInNetworkMessage.id)
    ).scalars())


def _enqueue_network(db: Session, rows: list) -> int:
    """After the approval commit: queue the held network messages again,
    spaced like a bulk send. One that cannot be queued keeps its hold tag, so
    the next approval releases it."""
    from app.services import network_messages as nm  # noqa: PLC0415
    from app.workers import network_tasks  # noqa: PLC0415

    queued = 0
    for i, row in enumerate(rows):
        if network_tasks.enqueue_send(row.id, countdown=i * nm.BULK_SPACING_SECONDS):
            queued += 1
        else:
            row.error = f"{ERROR_PREFIX}released but could not be queued -- waiting"
    db.commit()
    return queued


def _base_checks(row: DailySendApproval, tz_source: str, end: datetime, actor_id,
                 now: datetime, counts: dict, gated: tuple[str, ...]) -> dict:
    return {
        "approver_user_id": str(actor_id) if actor_id else None,
        "decided_at": now.isoformat(),
        "local_date": row.local_date.isoformat(),
        "timezone": row.timezone,
        "timezone_source": tz_source,
        "valid_until": end.isoformat(),
        "channels_gated": list(gated),
        "queued_counts": counts,
    }


def approve(db: Session, user_id, *, actor_id, channels: list[str],
            linkedin_risk_acknowledged: bool = False,
            linkedin_warning_version: str | None = None,
            now: datetime | None = None) -> dict:
    """Approve today's automatic sending on `channels` (added to anything
    already approved today and still in force). Releases the deferred
    messages on those channels. Raises ApprovalError (422 / 409) without
    changing anything."""
    now = _aware(now or _now())
    uid = _uuid(user_id)
    chosen = []
    for channel in channels or []:
        name = str(channel).strip().lower()
        if name not in ALL_CHANNELS:
            raise ApprovalError(f"unknown channel {channel!r}")
        if name not in chosen:
            chosen.append(name)
    if not chosen:
        raise ApprovalError("choose at least one channel to approve, or decline instead")
    if "linkedin" in chosen:
        if linkedin_risk_acknowledged is not True:
            raise ApprovalError("approving LinkedIn needs linkedin_risk_acknowledged: true -- "
                                "LinkedIn may restrict or suspend accounts that use automation")
        if not linkedin_warning_version:
            raise ApprovalError("approving LinkedIn needs the linkedin_warning_version shown")
        if linkedin_warning_version != LINKEDIN_WARNING_VERSION:
            raise ApprovalError("the LinkedIn warning has changed since this page was loaded "
                                "-- reload and read it again", status=409)

    tz_name, source = account_timezone(db, uid)
    today, _start, end = local_day(now, tz_name)
    gated = gated_channels(db)
    counts = queued_counts(db, uid, now, tz_name)
    # Read-merge-write under a row lock: two approvals for different channels
    # at the same moment serialise, so neither loses the other's channel.
    row = _lock(db, _get_or_create(db, uid, today, tz_name).id)
    still_valid = (row.status == APPROVED and row.valid_until is not None
                   and _aware(row.valid_until) > now)
    earlier = approved_channels(row) if still_valid else []
    merged = [c for c in ALL_CHANNELS if c in set(earlier) | set(chosen)]
    acknowledged_now = "linkedin" in chosen
    row.status = APPROVED
    row.channels_json = merged
    row.decided_at = now
    row.decided_by_user_id = _uuid(actor_id) if actor_id else None
    row.valid_until = end
    row.timezone = tz_name
    row.queued_counts_json = counts
    # Invariant: the row says "acknowledged" exactly when LinkedIn is approved
    # (now, or earlier today by a decision not since declined).
    row.linkedin_risk_acknowledged = "linkedin" in merged
    if acknowledged_now:
        row.linkedin_warning_version = LINKEDIN_WARNING_VERSION
    elif "linkedin" not in merged:
        row.linkedin_warning_version = None
    checks = _base_checks(row, source, end, actor_id, now, counts, gated)
    checks.update({
        "channels_requested": chosen,
        "channels_approved": merged,
        "linkedin_approved": "linkedin" in merged,
        # What THIS decision acknowledged -- never inherited from an earlier one.
        "linkedin_risk_acknowledged": acknowledged_now,
        "linkedin_acknowledged_earlier_today": ("linkedin" in earlier) and not acknowledged_now,
        "linkedin_warning_version": LINKEDIN_WARNING_VERSION if acknowledged_now else None,
        "linkedin_warning_text": LINKEDIN_WARNING_TEXT if acknowledged_now else None,
    })
    _audit(db, uid, AUDIT_APPROVED, checks)
    released = _release(db, uid, merged, now)
    network = _held_network(db, uid) if "linkedin" in merged else []
    for msg in network:
        msg.error = None
    db.commit()
    released_network = _enqueue_network(db, network) if network else 0
    logger.info("daily send approval: account %s approved %s for %s (released %d + %d network)",
                uid, merged, today, released, released_network)
    return {"released": released, "released_network": released_network,
            **state(db, uid, now)}


def decline(db: Session, user_id, *, actor_id, note: str | None = None,
            now: datetime | None = None) -> dict:
    """Record a decline: no automatic sending today (anything approved earlier
    today stops too, and its LinkedIn acknowledgement is cleared). Approving
    later the same day is still possible -- with a fresh acknowledgement."""
    now = _aware(now or _now())
    uid = _uuid(user_id)
    tz_name, source = account_timezone(db, uid)
    today, _start, end = local_day(now, tz_name)
    gated = gated_channels(db)
    counts = queued_counts(db, uid, now, tz_name)
    row = _lock(db, _get_or_create(db, uid, today, tz_name).id)
    previous = approved_channels(row)
    row.status = DECLINED
    row.channels_json = []
    row.decided_at = now
    row.decided_by_user_id = _uuid(actor_id) if actor_id else None
    row.valid_until = end
    row.timezone = tz_name
    row.queued_counts_json = counts
    row.linkedin_risk_acknowledged = False
    row.linkedin_warning_version = None
    checks = _base_checks(row, source, end, actor_id, now, counts, gated)
    checks.update({"channels_previously_approved": previous,
                   "linkedin_risk_acknowledged": False,
                   "note": (note or "")[:500] or None})
    _audit(db, uid, AUDIT_DECLINED, checks)
    db.commit()
    return {"released": 0, "released_network": 0, **state(db, uid, now)}


# --------------------------------------------------------------------------
# Notification (once per account per local day)
# --------------------------------------------------------------------------


def _summary(counts: dict[str, int]) -> str:
    parts = [f"{CHANNEL_LABELS[c]}: {n}" for c, n in counts.items()]
    return ", ".join(parts) if parts else "nothing queued"


def approval_link() -> str:
    from app.services import event_bus  # noqa: PLC0415

    return event_bus.web_link(APPROVAL_PATH)


def email_content(counts: dict[str, int], local_date: date, tz_name: str,
                  linkedin_gated: bool) -> dict:
    """Subject / text / html of the owner email. Pure (tested)."""
    lines = [
        f"Vectorae has automatic messages ready to send today ({local_date.isoformat()}, "
        f"{tz_name}), and nothing automatic will be sent until you approve it.",
        "Queued for today: " + _summary(counts) + ".",
        f"Approve or decline here: {approval_link()}",
        "You choose the channels. An approval is valid until the end of the day; "
        "tomorrow Vectorae will ask again.",
    ]
    if linkedin_gated:
        lines.append("LinkedIn: " + LINKEDIN_WARNING_TEXT)
    lines.append("Messages a person already approved one by one (the send-review queue, "
                 "Reply Copilot answers you approved) are not affected.")
    lines.append("You get this email because daily send approval is on for your "
                 "Vectorae account.")
    text = "\n\n".join(lines)
    html = "".join(f"<p>{escape(line)}</p>" for line in lines)
    return {"subject": "Approve today's automatic sending in Vectorae",
            "text": text, "html": html}


def notify_if_needed(db: Session, hold: Hold, now: datetime | None = None) -> bool:
    """Notify the owner that today's automatic sending is waiting -- at most
    once per account per local day. Returns True if THIS call notified.
    Never raises: a notification failure must not undo a deferral."""
    if not hold.notify:
        return False
    now = _aware(now or _now())
    try:
        row = _get_or_create(db, hold.user_id, hold.local_date, hold.tz_name)
        if row.status != PENDING or row.notified_at is not None:
            db.commit()
            return False
        claimed = db.execute(
            update(DailySendApproval)
            .where(DailySendApproval.id == row.id, DailySendApproval.notified_at.is_(None))
            .values(notified_at=now)
            .execution_options(synchronize_session=False)
        )
        db.commit()
        if claimed.rowcount != 1:
            return False
        db.refresh(row)
    except Exception:  # noqa: BLE001 -- see the docstring
        logger.exception("daily send approval: could not claim today's notification "
                         "for account %s", hold.user_id)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return False

    result: dict = {"email": "skipped", "push": "skipped"}
    try:
        counts = queued_counts(db, hold.user_id, now, hold.tz_name)
        linkedin_gated = "linkedin" in counts
        result["counts"] = counts
        owner = db.get(User, hold.user_id)
        if owner is not None and owner.email and not owner.is_suspended:
            from app.services import email_sender  # noqa: PLC0415

            content = email_content(counts, hold.local_date, hold.tz_name, linkedin_gated)
            try:
                email_sender.send_email(to=owner.email, **content)
                result["email"] = "sent"
            except Exception as exc:  # noqa: BLE001 -- push and banner still stand
                logger.warning("daily send approval email for %s failed: %s",
                               hold.user_id, exc)
                result["email"] = "failed"
        from app.workers import notification_tasks  # noqa: PLC0415

        body = f"Queued today: {_summary(counts)}. Nothing automatic sends until you approve."
        if linkedin_gated:
            body += " LinkedIn may restrict accounts that use automation."
        queued = notification_tasks.enqueue_event(
            hold.user_id, EVENT,
            title="Approve today's automatic sending",
            body=body,
            deep_link=APPROVAL_PATH,
            data={"date": hold.local_date.isoformat(),
                  **{f"queued_{c}": n for c, n in counts.items()}},
            webhook_payload=None)
        result["push"] = "queued" if queued else "inline"
    except Exception:  # noqa: BLE001
        logger.exception("daily send approval: notification for %s failed", hold.user_id)
        result["error"] = "notification failed"
    try:
        row.notify_result_json = result
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
    return True
