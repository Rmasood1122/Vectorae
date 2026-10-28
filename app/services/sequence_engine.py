"""The sequence engine — all outreach business rules live HERE, not in
channels and not in templates.

Hard stop conditions (engine-enforced, per project spec):
    replied -> stop | unsubscribed -> stop + suppression | bounced -> stop
    meeting_booked -> stop.  A stopped enrollment can NEVER send again.

Compliance core (chunk 3):
    - suppression re-checked immediately before EVERY send, no exceptions
    - visible unsubscribe footer with sender identity (CAN-SPAM) +
      List-Unsubscribe / List-Unsubscribe-Post one-click headers
    - per-account daily caps with a configurable warm-up ramp; sends over
      today's allowance are DEFERRED, never dropped
    - business-hours send window in the lead's timezone (fallback config)
    - bounce-rate monitor: campaign auto-pauses above the threshold

Every time-dependent function takes an explicit `now` so behavior is
deterministic and fully testable.
"""

import logging
import uuid
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import (
    ChannelType,
    EnrollmentStatus,
    GmailAccount,
    Lead,
    LeadStatus,
    Message,
    MessageStatus,
    Outcome,
    OutcomeEvent,
    Sequence,
    SequenceEnrollment,
    SequenceStep,
    Strategy,
    SuppressionEntry,
)
from app.services import crypto
from app.workers.lead_tasks import is_suppressed

logger = logging.getLogger(__name__)

CAMPAIGN_ACTIVE = "active"
CAMPAIGN_PAUSED_BOUNCE = "paused_bounce_rate"

#: Stop reasons (categorised by sequence_completion._PREFIXES).
STOP_LEAD_REMOVED = "lead_removed"          # the account removed the lead
STOP_NO_ADDRESS = "no_address_for_channel"  # nothing to send this step to

#: Lead statuses that END every sequence the lead is in, whoever sets them:
#: removed (DROPPED), and the closed outcomes. A status writer calls
#: stop_for_status() after setting one of these (app/api/ui_support.py,
#: app/api/crm.py single and bulk patch, crm_sync.apply_lead_status), and the
#: send chokepoint refuses a sequence step to any of them (outreach_tasks), so
#: a writer that forgets cannot send. Before this only DROPPED was stopped by
#: the Pipeline board and blocked at send time; a lead closed or disqualified
#: from the CRM grid kept receiving its sequence.
CLOSED_STATUSES: frozenset[LeadStatus] = frozenset({
    LeadStatus.CLOSED_WON, LeadStatus.CLOSED_LOST, LeadStatus.DISQUALIFIED})
STOPPING_STATUSES: frozenset[LeadStatus] = CLOSED_STATUSES | {LeadStatus.DROPPED}


def status_stop_reason(status: LeadStatus) -> str:
    """The stop reason for a status in STOPPING_STATUSES set inside
    Vectorae: "lead_removed" for DROPPED, "crm_<status>" for a close (both
    categorised by sequence_completion: lead_removed / crm_closed)."""
    if status is LeadStatus.DROPPED:
        return STOP_LEAD_REMOVED
    return f"crm_{status.value}"

#: The lead statuses a sequence may enroll. VERIFIED only: the address passed
#: verification and nobody has decided otherwise. Deliberately NOT
#:   flagged   -- a risky / unknown verdict. The product leaves it to a person
#:                to decide, per lead, by moving it to verified (Pipeline board,
#:                ui_support._ALLOWED_TRANSITIONS); the app's own launch form
#:                enrolls ["verified"] only (frontend/src/lib/api/outreach.ts).
#:   dropped   -- removed by the account, undeliverable, suppressed or erased.
#:   contacted / replied / meeting_booked / later -- already in a conversation
#:                or a sequence; a second sequence would double the cadence.
#:   sourced / enriched / email_found -- not verified yet.
#: Enforced here and at the API (app/api/sequences.EnrollRequest).
ENROLLABLE_STATUSES: frozenset[LeadStatus] = frozenset({LeadStatus.VERIFIED})


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------
# Send window (lead timezone, business hours, weekend skip)
# --------------------------------------------------------------------------


def lead_timezone(lead: Lead) -> str:
    """The lead's IANA zone: recorded, else resolved from its own location,
    else the strategy's region / the account's zone / the deployment default
    (app/services/lead_timezone.py -- never written back on read)."""
    from app.services import lead_timezone as lead_tz  # noqa: PLC0415

    return lead_tz.resolve(lead)[0]


def next_window_slot(dt: datetime, tz_name: str,
                     window: tuple[int, int, bool] | None = None) -> datetime:
    """Earliest instant >= dt inside the send window (returned in UTC).

    `window` is (start_hour, end_hour, skip_weekends) from a resolved
    compliance rule (Feature 8); None is the deployment baseline. The caller
    must not pass an empty window -- compliance_rules.Effective.window_empty.
    """
    tz = ZoneInfo(tz_name)
    local = dt.astimezone(tz)
    if window is None:
        window = (settings.send_window_start_hour, settings.send_window_end_hour,
                  settings.send_window_skip_weekends)
    start_h, end_h, skip_weekends = window

    for _ in range(14):  # never loops more than two weeks
        is_weekend = local.weekday() >= 5 and skip_weekends
        if not is_weekend:
            if local.time() < time(start_h):
                local = local.replace(hour=start_h, minute=0, second=0, microsecond=0)
                return local.astimezone(timezone.utc)
            if time(start_h) <= local.time() < time(end_h):
                return local.astimezone(timezone.utc)
        # past today's window (or weekend): next day at window start
        local = (local + timedelta(days=1)).replace(
            hour=start_h, minute=0, second=0, microsecond=0
        )
    raise RuntimeError("could not find a send window slot within 14 days")


def in_send_window(dt: datetime, tz_name: str,
                   window: tuple[int, int, bool] | None = None) -> bool:
    return next_window_slot(dt, tz_name, window) <= dt


# --------------------------------------------------------------------------
# Daily caps + warm-up
# --------------------------------------------------------------------------


#: F03 Deliverability Autopilot: the stepped new-mailbox ramp. Each entry is
#: (from this many days after the mailbox's FIRST send, at most this many
#: sends a day). From RAMP_SCHEDULE_DAYS on there is no extra ceiling -- the
#: linear ramp and the daily cap still apply. Applied only with the Autopilot on.
RAMP_SCHEDULE: tuple[tuple[int, int], ...] = ((0, 10), (7, 20), (14, 30), (21, 40))
RAMP_SCHEDULE_DAYS = 28


def ramp_ceiling(days_since_first_send: int) -> int | None:
    """The stepped ceiling for a mailbox this many days into sending, or None
    once it has graduated."""
    if days_since_first_send >= RAMP_SCHEDULE_DAYS:
        return None
    ceiling = RAMP_SCHEDULE[0][1]
    for from_day, per_day in RAMP_SCHEDULE:
        if days_since_first_send >= from_day:
            ceiling = per_day
    return ceiling


def first_send_date(session: Session, sender_ref: str) -> date | None:
    """The UTC date of the first message Vectorae sent from this mailbox."""
    first = session.execute(
        select(func.min(Message.sent_at)).where(
            Message.sender_ref == sender_ref,
            Message.status.in_([MessageStatus.SENT, MessageStatus.BOUNCED]),
            Message.sent_at.is_not(None),
        )
    ).scalar_one()
    if first is None:
        return None
    if isinstance(first, str):  # SQLite aggregate over a DateTime column
        first = datetime.fromisoformat(first)
    if first.tzinfo is not None:
        first = first.astimezone(timezone.utc)
    return first.date()


def daily_allowance(account: GmailAccount, on_date: date, cap: int | None = None, *,
                    stepped: bool = False, first_sent_on: date | None = None) -> int:
    """Warm-up ramp: start low, add the increment each day, cap at the
    configured daily cap. Day 0 = the day the account was connected.
    `cap` (Feature 8 compliance rule) can only lower the ceiling.

    `stepped` (F03 Autopilot) additionally applies RAMP_SCHEDULE counted from
    `first_sent_on` -- the first day the mailbox actually sent, or `on_date`
    for a mailbox that never has (it is new however long ago it was
    connected). Combined by min(): it can only lower the allowance."""
    connected = account.created_at.date() if account.created_at else on_date
    age_days = max(0, (on_date - connected).days)
    ramped = settings.gmail_warmup_start_sends + settings.gmail_warmup_daily_increment * age_days
    ceiling = settings.gmail_daily_cap if cap is None else min(cap, settings.gmail_daily_cap)
    allowance = min(ceiling, ramped)
    if stepped:
        anchor = first_sent_on or on_date
        step = ramp_ceiling(max(0, (on_date - anchor).days))
        if step is not None:
            allowance = min(allowance, step)
    return allowance


def ramp_allowance(session: Session, account: GmailAccount, on_date: date,
                   cap: int | None = None, *, autopilot: bool = False) -> int:
    """daily_allowance with the Autopilot's stepped ramp when it is on -- the
    one place the send path, the health refresh and the stats page get
    today's cap from, so the number shown is the number enforced."""
    if not autopilot:
        return daily_allowance(account, on_date, cap)
    return daily_allowance(account, on_date, cap, stepped=True,
                           first_sent_on=first_send_date(session, str(account.id)))


def sends_today(session: Session, sender_ref: str, now: datetime) -> int:
    day_start = now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return session.execute(
        select(func.count(Message.id)).where(
            Message.sender_ref == sender_ref,
            Message.status == MessageStatus.SENT,
            Message.sent_at >= day_start,
        )
    ).scalar_one()


def allowance_left(session: Session, account: GmailAccount, now: datetime,
                   cap: int | None = None, *, autopilot: bool = False) -> int:
    return ramp_allowance(session, account, now.astimezone(timezone.utc).date(), cap,
                          autopilot=autopilot) - sends_today(session, str(account.id), now)


# --------------------------------------------------------------------------
# WhatsApp daily cap + warm-up (M4 Chunk 3) — same defer-never-drop rule
# --------------------------------------------------------------------------


def whatsapp_sends_today(session: Session, now: datetime) -> int:
    day_start = now.astimezone(timezone.utc).replace(hour=0, minute=0,
                                                     second=0, microsecond=0)
    return session.execute(
        select(func.count(Message.id)).where(
            Message.channel == ChannelType.WHATSAPP,
            Message.status == MessageStatus.SENT,
            Message.sent_at >= day_start,
        )
    ).scalar_one()


def whatsapp_daily_allowance(session: Session, on_date: date, cap: int | None = None) -> int:
    """Warm-up ramp for the WhatsApp number, anchored to the date of the
    FIRST WhatsApp message ever sent (there is no per-user account row
    like Gmail's — one business number per deployment)."""
    first_sent = session.execute(
        select(func.min(Message.sent_at)).where(
            Message.channel == ChannelType.WHATSAPP,
            Message.status == MessageStatus.SENT,
        )
    ).scalar_one()
    ceiling = (settings.whatsapp_daily_cap if cap is None
               else min(cap, settings.whatsapp_daily_cap))
    if first_sent is None:
        return min(ceiling, settings.whatsapp_warmup_start_sends)
    first_date = first_sent.date() if isinstance(first_sent, datetime) else on_date
    age_days = max(0, (on_date - first_date).days)
    ramped = (settings.whatsapp_warmup_start_sends
              + settings.whatsapp_warmup_daily_increment * age_days)
    return min(ceiling, ramped)


def whatsapp_allowance_left(session: Session, now: datetime, cap: int | None = None) -> int:
    return whatsapp_daily_allowance(
        session, now.astimezone(timezone.utc).date(), cap
    ) - whatsapp_sends_today(session, now)


# --------------------------------------------------------------------------
# Enrollment lifecycle
# --------------------------------------------------------------------------


def enroll_leads(
    session: Session,
    sequence: Sequence,
    lead_statuses: list[LeadStatus] | None = None,
    now: datetime | None = None,
    lead_ids=None,
) -> int:
    """Enroll matching leads and schedule their step-1 message. Idempotent:
    already-enrolled leads are skipped (unique constraint backs this).

    `lead_ids` (Autopilot launch) narrows the candidates to those leads; every
    other rule below still applies to them. None = every matching lead, as
    before."""
    now = now or _now()
    lead_statuses = lead_statuses or [LeadStatus.VERIFIED]
    not_enrollable = sorted(LeadStatus(s).value for s in lead_statuses
                            if LeadStatus(s) not in ENROLLABLE_STATUSES)
    if not_enrollable:
        raise ValueError(f"lead status(es) {', '.join(not_enrollable)} cannot be enrolled; "
                         f"allowed: {', '.join(sorted(s.value for s in ENROLLABLE_STATUSES))}")
    steps = sorted(sequence.steps, key=lambda s: s.step_no)
    if not steps:
        raise ValueError("sequence has no steps")

    # The address the FIRST step needs. Feature Group 5: a sequence that opens
    # on LinkedIn needs a profile URL, not an email -- a lead Apollo found no
    # email for is still reachable. Likewise a sequence that opens on WhatsApp
    # (or an AI call) needs a phone number: a phone-only lead is reachable
    # there, and an email-only lead is not (it would be enrolled only to be
    # stopped at its first send for want of an address).
    first_channel = steps[0].effective_channel(sequence)
    if first_channel is ChannelType.LINKEDIN:
        address = Lead.linkedin_url.isnot(None)
    elif first_channel in (ChannelType.WHATSAPP, ChannelType.PHONE):
        address = Lead.phone.isnot(None)
    else:
        address = Lead.email.isnot(None)
    # Local business mode, Part B -- SAFETY GUARD: the two audiences never mix.
    # A customer (win-back) sequence enrolls only the business's imported,
    # opted-in customers (any status but removed/disqualified -- they are not
    # email-verified prospects); every other sequence never enrolls them. The
    # send path re-checks this (local_business.customer_send_block).
    from app.services import local_business  # noqa: PLC0415

    customers = getattr(sequence, "audience", None) == local_business.AUDIENCE_CUSTOMERS
    if customers:
        who = (Lead.source == local_business.CUSTOMER_SOURCE,
               Lead.status.not_in((LeadStatus.DROPPED, LeadStatus.DISQUALIFIED)))
    else:
        who = (Lead.status.in_(lead_statuses),
               Lead.source != local_business.CUSTOMER_SOURCE)
    query = select(Lead).where(
        Lead.strategy_id == sequence.strategy_id,
        *who,
        address,
    )
    if lead_ids is not None:
        wanted = list(lead_ids)
        if not wanted:
            return 0
        query = query.where(Lead.id.in_(wanted))
    leads = session.execute(query).scalars().all()

    enrolled = 0
    enrolled_ids: list = []
    existing = {
        e.lead_id for e in session.execute(
            select(SequenceEnrollment).where(SequenceEnrollment.sequence_id == sequence.id)
        ).scalars()
    }
    # F05 Guardrails Studio: the owning account's exclusion lists. None when
    # the feature is off -- then this loop is exactly what it was.
    from app.services import guardrails  # noqa: PLC0415

    guard_owner = guardrails.active_owner(session, sequence.strategy_id)
    for lead in leads:
        if lead.id in existing or is_suppressed(session, lead.email, lead.phone,
                                                linkedin=lead.linkedin_url):
            continue
        if guard_owner is not None and guardrails.exclusion_for_lead(
                session, lead, user_id=guard_owner) is not None:
            continue
        # Part 1 Feature 3: the plan this prospect was enrolled INTO, frozen
        # here. Editing the sequence later must not retroactively turn a
        # completed enrollment into an incomplete one.
        enrollment = SequenceEnrollment(sequence_id=sequence.id, lead_id=lead.id,
                                        planned_steps=len(steps))
        session.add(enrollment)
        session.flush()
        _schedule_step_message(session, sequence, enrollment, lead, steps[0], base_time=now)
        enrolled += 1
        enrolled_ids.append(lead.id)
    session.commit()

    # Part 1 Feature 2: F-P-T-A is scored AT ENROLLMENT, because that is the
    # moment the four questions become actionable -- and it runs AFTER the
    # commit above, so a scoring failure can never undo an enrollment that
    # already succeeded. Leads already carrying a score are left alone.
    from app.services import fpta_scoring  # noqa: PLC0415

    # Not for customers: F-P-T-A is a B2B prospect score (a model call about
    # the person); a consumer's own-customer record is never profiled.
    if not customers:
        fpta_scoring.score_for_enrollment(session, enrolled_ids, now=now)
    return enrolled


def stop_enrollment(session: Session, enrollment: SequenceEnrollment, reason: str,
                    now: datetime | None = None, *, commit: bool = True) -> None:
    """HARD STOP. Cancels every pending message. Irreversible by design —
    a stopped enrollment can never send again.

    Part 1 Feature 3: also records WHEN it stopped and WHAT KIND of stop it
    was. The category is decided here, at write time, so a call site that
    invents a new reason lands in `other` and is logged — rather than
    silently widening the "dropped without a decision" bucket that the
    completion metric reports.

    "Pending" is send_holds.pending_clause(): scheduled, being sent, waiting
    in the human review queue (AWAITING_REVIEW) or held by a send-time guard.
    A held message used to survive the stop: it was never sent (approval or
    release re-runs the gates, which refuse a stopped enrollment), but it sat
    in the review queue as if a decision were still needed. Its review is
    closed here too (send_review.close_for_cancelled).
    """
    from app.services import sequence_completion  # noqa: PLC0415

    now = now or _now()
    enrollment.status = EnrollmentStatus.STOPPED
    enrollment.stop_reason = reason
    enrollment.stopped_at = now
    enrollment.stop_category = sequence_completion.categorize(reason)
    _cancel_pending(session, enrollment, f"enrollment stopped: {reason}", now)
    if commit:   # commit=False: the caller commits it with its own change
        session.commit()


def _cancel_pending(session: Session, enrollment: SequenceEnrollment, note: str,
                    now: datetime) -> int:
    """Cancel this enrollment's pending messages (send_holds.pending_clause:
    scheduled, sending, in the review queue, or guard-held) and close their
    pending reviews. Changes nothing else. Does not commit. Returns the count."""
    from app.services import send_holds, send_review  # noqa: PLC0415

    pending = session.execute(
        select(Message).where(
            Message.sequence_id == enrollment.sequence_id,
            Message.lead_id == enrollment.lead_id,
            send_holds.pending_clause(),
        )
    ).scalars().all()
    for msg in pending:
        msg.status = MessageStatus.CANCELLED
        msg.error = note
    send_review.close_for_cancelled(session, [msg.id for msg in pending], note=note, now=now)
    return len(pending)


def pause_enrollment(session: Session, enrollment: SequenceEnrollment,
                     until: datetime) -> None:
    """Soft pause (out-of-office): pending messages move past `until`."""
    enrollment.status = EnrollmentStatus.PAUSED
    enrollment.paused_until = until
    lead = session.get(Lead, enrollment.lead_id)
    pending = session.execute(
        select(Message).where(
            Message.sequence_id == enrollment.sequence_id,
            Message.lead_id == enrollment.lead_id,
            Message.status == MessageStatus.SCHEDULED,
        )
    ).scalars().all()
    for msg in pending:
        msg.scheduled_at = next_window_slot(until, lead_timezone(lead))
    session.commit()


def resume_due_enrollments(session: Session, now: datetime | None = None) -> int:
    now = now or _now()
    due = session.execute(
        select(SequenceEnrollment).where(
            SequenceEnrollment.status == EnrollmentStatus.PAUSED,
            SequenceEnrollment.paused_until <= now,
        )
    ).scalars().all()
    for e in due:
        e.status = EnrollmentStatus.ACTIVE
        e.paused_until = None
    session.commit()
    return len(due)


def _schedule_step_message(
    session: Session,
    sequence: Sequence,
    enrollment: SequenceEnrollment,
    lead: Lead,
    step: SequenceStep,
    base_time: datetime,
) -> Message:
    """Create the SCHEDULED message row for a step (body rendered at send
    time; the send task persists it before transmitting)."""
    when = next_window_slot(base_time + timedelta(days=step.delay_days if step.step_no > 1 else 0),
                            lead_timezone(lead))
    channel = step.effective_channel(sequence)
    # Autopilot launch / best time to send (docs/features/autopilot-launch.md):
    # with best_time_scheduling_enabled on (default), move the step to the best
    # slot in the prospect's local time at or after `when` -- never earlier,
    # bounded by the send-time horizon. Flag off: `when` unchanged.
    from app.services import send_time, send_windows  # noqa: PLC0415

    when = send_time.place(session, sequence.strategy, lead, channel, when,
                           window=send_time.resolved_window(session, lead, channel,
                                                            sequence.strategy),
                           spread_key=lead.id, now=base_time)
    # Feature Group 3: with smart send time on, hold the step for the
    # campaign's next top engagement window (bounded; a no-op otherwise). A
    # person's own override window wins over it.
    if not send_time.override_windows(sequence.strategy):
        when = send_windows.next_smart_slot(session, sequence.strategy, when,
                                            lead_timezone(lead), spread_key=lead.id)
    msg = Message(
        sequence_id=sequence.id,
        lead_id=lead.id,
        # M4 Chunk 3: the step's channel wins; NULL falls back to the
        # sequence's channel (all M3 sequences behave exactly as before).
        channel=channel,
        step_no=step.step_no,
        template=step.template,
        variant=step.variant,
        whatsapp_kind=step.whatsapp_kind,
        whatsapp_template_id=step.whatsapp_template_id,
        status=MessageStatus.SCHEDULED,
        scheduled_at=when,
    )
    session.add(msg)
    session.commit()
    return msg


def schedule_next_step(session: Session, message: Message, now: datetime,
                       counts_as_sent: bool = True) -> Message | None:
    """After a successful send: schedule the following step, or complete
    the enrollment when there is none.

    `counts_as_sent=False` is the SKIP path (app/services/sequence_engine
    ::skip_message): the sequence advances, but the prospect did not receive
    that step, so it must not count towards Feature 3's completion.
    """
    enrollment = session.execute(
        select(SequenceEnrollment).where(
            SequenceEnrollment.sequence_id == message.sequence_id,
            SequenceEnrollment.lead_id == message.lead_id,
        )
    ).scalar_one()
    # Only ADVANCING counts: a transient failure that re-sends the same step
    # arrives here once, when the step finally lands.
    if counts_as_sent and message.step_no > enrollment.current_step:
        enrollment.steps_sent = (enrollment.steps_sent or 0) + 1
    enrollment.current_step = message.step_no

    sequence = session.get(Sequence, message.sequence_id)
    next_steps = [s for s in sequence.steps if s.step_no > message.step_no]
    if enrollment.status is not EnrollmentStatus.ACTIVE:
        session.commit()
        return None
    if not next_steps:
        enrollment.status = EnrollmentStatus.COMPLETED
        enrollment.completed_at = now
        _flag_if_all_skipped(session, enrollment)
        session.commit()
        return None
    step = min(next_steps, key=lambda s: s.step_no)
    lead = session.get(Lead, message.lead_id)
    return _schedule_step_message(session, sequence, enrollment, lead, step, base_time=now)


def skip_message(session: Session, message: Message,
                 reason: str, now: datetime) -> Message | None:
    """M4 Chunk 3 engine rule: mark a message SKIPPED (with the reason,
    e.g. 'skipped_no_optin') and advance the sequence to its NEXT step —
    a lead without WhatsApp opt-in loses the WhatsApp touch, not the whole
    sequence. Returns the next scheduled message, if any."""
    message.status = MessageStatus.SKIPPED
    message.error = reason
    session.commit()
    logger.info("message %s skipped: %s (lead %s)",
                message.id, reason, message.lead_id)
    # Part 1 Feature 3: a skipped step advances the sequence but was never
    # received, so it does not count towards completion.
    return schedule_next_step(session, message, now=now, counts_as_sent=False)


def _flag_if_all_skipped(session: Session, enrollment: SequenceEnrollment) -> None:
    """An enrollment that completed without a single successful send —
    every step skipped — is flagged for user attention via stop_reason
    (visible in the campaign overview); typical cause: an all-WhatsApp
    sequence and a lead who never opted in."""
    statuses = session.execute(
        select(Message.status).where(
            Message.sequence_id == enrollment.sequence_id,
            Message.lead_id == enrollment.lead_id,
        )
    ).scalars().all()
    if statuses and all(st is MessageStatus.SKIPPED for st in statuses):
        from app.services import sequence_completion  # noqa: PLC0415

        enrollment.stop_reason = "completed_all_skipped_needs_attention"
        enrollment.stop_category = sequence_completion.ALL_STEPS_SKIPPED


# --------------------------------------------------------------------------
# Compliant email assembly (chunk 3)
# --------------------------------------------------------------------------


def make_unsubscribe_token(lead_id: uuid.UUID, email: str | None = None) -> str:
    """The token in every email's unsubscribe link. It also carries the
    recipient address (encrypted, like the rest of the token), so an
    unsubscribe still reaches the suppression list after the lead -- or the
    whole sending account -- has been erased (docs/features/gdpr-erasure.md)."""
    payload = {"lead_id": str(lead_id), "purpose": "unsubscribe"}
    if email:
        payload["to"] = email.strip().lower()
    return crypto.encrypt_json(payload)


def parse_unsubscribe_payload(token: str) -> tuple[uuid.UUID, str | None]:
    """(lead id, recipient address or None for tokens minted before 2026-10-11)."""
    data = crypto.decrypt_json(token)
    if data.get("purpose") != "unsubscribe":
        raise ValueError("not an unsubscribe token")
    return uuid.UUID(data["lead_id"]), (data.get("to") or None)


def parse_unsubscribe_token(token: str) -> uuid.UUID:
    return parse_unsubscribe_payload(token)[0]


def compliance_footer(unsubscribe_url: str) -> str:
    return (
        "\n\n--\n"
        f"{settings.sender_identity}\n"
        f"Don't want these emails? Unsubscribe instantly: {unsubscribe_url}"
    )


def build_compliant_email(lead: Lead, subject: str, body: str) -> tuple[str, str, dict, str]:
    """Returns (subject, body_with_footer, headers, token). Headers carry
    RFC 8058 one-click unsubscribe."""
    token = make_unsubscribe_token(lead.id, lead.email)
    url = f"{settings.public_base_url.rstrip('/')}/unsubscribe/{token}"
    headers = {
        "List-Unsubscribe": f"<{url}>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }
    # Feature Group 9: GDPR / UK and CASL recipients get the notice their
    # regime asks for (right to object / sender + unsubscribe route).
    from app.services import compliance_audit  # noqa: PLC0415

    notice = compliance_audit.region_notice(lead)
    if notice:
        body = f"{body}\n\n{notice}"
    return subject, body + compliance_footer(url), headers, token


# --------------------------------------------------------------------------
# Unsubscribe + bounce handling (shared by endpoint, send task, replies)
# --------------------------------------------------------------------------


def _enrollments_for_lead(session: Session, lead_id: uuid.UUID) -> list[SequenceEnrollment]:
    return session.execute(
        select(SequenceEnrollment).where(SequenceEnrollment.lead_id == lead_id)
    ).scalars().all()


def stop_live_enrollments(session: Session, lead_id: uuid.UUID, reason: str,
                          now: datetime | None = None, *, commit: bool = True) -> int:
    """stop_enrollment() every LIVE (active or paused) enrollment of
    `lead_id`. Returns how many were stopped. Commits unless commit=False
    (then the caller commits it in the same transaction as its own change --
    the CRM bulk patch, gaps-sweep-2 review L4).

    A COMPLETED enrollment keeps its status: it received every step, and
    overwriting it as STOPPED would move it from "completed" to "stopped
    early" in the completion metric (gaps-sweep-2 review M2). Anything still
    pending on it (a Reply Copilot answer, a re-engagement touch, a held
    message) is cancelled all the same, with the reason. STOPPED ones are left
    as they are."""
    now = now or _now()
    stopped = 0
    for enrollment in _enrollments_for_lead(session, lead_id):
        if enrollment.status in (EnrollmentStatus.ACTIVE, EnrollmentStatus.PAUSED):
            stop_enrollment(session, enrollment, reason=reason, now=now, commit=False)
            stopped += 1
        elif enrollment.status is EnrollmentStatus.COMPLETED:
            _cancel_pending(session, enrollment, f"lead status: {reason}", now)
    if commit:
        session.commit()
    return stopped


def stop_for_status(session: Session, lead: Lead, *, reason: str | None = None,
                    now: datetime | None = None, commit: bool = True) -> int:
    """The ONE rule every lead-status writer applies after setting a status:
    a status in STOPPING_STATUSES stops every live enrollment of the lead (and
    so cancels its pending messages). Any other status is a no-op. `reason`
    overrides status_stop_reason() (crm_sync passes "crm_<provider>").
    Returns how many enrollments were stopped."""
    if lead.status not in STOPPING_STATUSES:
        return 0
    return stop_live_enrollments(session, lead.id,
                                 reason or status_stop_reason(lead.status), now=now,
                                 commit=commit)


def _outcome(session: Session, lead: Lead, event: OutcomeEvent,
             message_id: uuid.UUID | None = None, meta: dict | None = None,
             channel: str = "email") -> None:
    session.add(Outcome(lead_id=lead.id, message_id=message_id, event=event,
                        channel=channel, meta_json=meta or {}))


def unsubscribe_lead(session: Session, lead: Lead, source: str,
                     message_id: uuid.UUID | None = None,
                     channel: str = "email") -> None:
    """One action for BOTH the unsubscribe click and an
    unsubscribe_request reply: instant suppression + hard stop.

    Part 1 Feature 9: the per-channel suppression that used to live here is
    now `consent.suppress_everywhere`, for two reasons. It also revokes
    WHATSAPP -- which this function did not, so an unsubscribe by email left
    `whatsapp_opted_in` True and the next WhatsApp step went out anyway. And
    it writes the consent ledger, so "when did they tell you to stop, and
    what did you do about it?" has an answer that outlives the lead row.
    """
    from app.services import consent  # noqa: PLC0415

    consent.suppress_everywhere(session, lead, source=source,
                                reason=f"unsubscribed_{source}",
                                detail=f"unsubscribed via {source} on {channel}")
    _outcome(session, lead, OutcomeEvent.UNSUBSCRIBED, message_id,
             {"source": source}, channel=channel)
    session.commit()
    for enrollment in _enrollments_for_lead(session, lead.id):
        if enrollment.status is not EnrollmentStatus.STOPPED:
            stop_enrollment(session, enrollment, reason="unsubscribed")
    session.commit()
    # Email network: the address leaves every account's email contacts now,
    # not at the next weekly refresh. After the commits above; never raises.
    from app.services import email_network  # noqa: PLC0415

    email_network.purge_address(session, lead.email)


def record_bounce(session: Session, lead: Lead, message: Message | None = None,
                  meta: dict | None = None, channel: str = "email") -> None:
    """Bounce: outcome + stop + drop lead + campaign bounce-rate check."""
    if message is not None:
        message.status = MessageStatus.BOUNCED
    lead.status = LeadStatus.DROPPED
    _outcome(session, lead, OutcomeEvent.BOUNCED,
             message.id if message else None, meta, channel=channel)
    session.commit()
    for enrollment in _enrollments_for_lead(session, lead.id):
        if enrollment.status is not EnrollmentStatus.STOPPED:
            stop_enrollment(session, enrollment, reason="bounced")
    strategy = session.get(Strategy, lead.strategy_id)
    check_bounce_rate(session, strategy)


def check_bounce_rate(session: Session, strategy: Strategy) -> bool:
    """Auto-pause the whole campaign above the threshold (config), once at
    least BOUNCE_MIN_SENDS messages have been sent. Returns True if paused."""
    sent = session.execute(
        select(func.count(Message.id))
        .join(Lead, Lead.id == Message.lead_id)
        .where(Lead.strategy_id == strategy.id,
               Message.status.in_([MessageStatus.SENT, MessageStatus.BOUNCED]))
    ).scalar_one()
    if sent < settings.bounce_min_sends:
        return False
    bounced = session.execute(
        select(func.count(Outcome.id))
        .join(Lead, Lead.id == Outcome.lead_id)
        .where(Lead.strategy_id == strategy.id, Outcome.event == OutcomeEvent.BOUNCED)
    ).scalar_one()
    rate = bounced / sent
    # Feature 8: the workspace's compliance rule may lower the threshold,
    # never raise it past the deployment baseline.
    from app.services import compliance_rules  # noqa: PLC0415

    threshold = compliance_rules.bounce_threshold(session, strategy)
    if rate > threshold and strategy.campaign_state == CAMPAIGN_ACTIVE:
        strategy.campaign_state = CAMPAIGN_PAUSED_BOUNCE
        strategy.campaign_pause_reason = (
            f"bounce rate {rate:.1%} exceeded {threshold:.1%} "
            f"({bounced}/{sent}) — human review required"
        )
        session.commit()
        logger.warning("campaign paused for strategy %s: %s",
                       strategy.id, strategy.campaign_pause_reason)

        # The owner has to know: an auto-paused campaign sends nothing until a
        # human resumes it. dispatch() never raises, so the pause stands even
        # if the push fails.
        from app.services import notifications  # noqa: PLC0415

        owner = notifications.owner_of_strategy(session, strategy)
        if owner is not None:
            notifications.dispatch(
                notifications.notify_campaign_paused(owner, strategy.id)
            )
            # Feature Group 4: Slack ("campaign auto-paused") + webhooks;
            # push=False because the M7 push above already went.
            from app.workers import notification_tasks  # noqa: PLC0415

            notification_tasks.enqueue_event(
                owner, "campaign_paused", push=False, title="Campaign auto-paused",
                body=strategy.campaign_pause_reason or "Bounce rate over the limit.",
                deep_link=f"/campaigns?strategy={strategy.id}",
                data={"strategyId": str(strategy.id)},
                webhook_payload={"strategy_id": str(strategy.id), "trigger": "bounce_rate",
                                 "reason": strategy.campaign_pause_reason,
                                 "bounce_rate": round(rate, 4)},
            )
        else:
            logger.warning("strategy %s has no resolvable owner - no pause "
                           "notification", strategy.id)
        return True
    return False


# --------------------------------------------------------------------------
# Engagement Hub (Feature 4) — a pending meeting pauses outreach
# --------------------------------------------------------------------------
#
# WHY PAUSE AND NOT STOP
# The M3 rule is that a booked meeting is a hard stop (see the module
# docstring, and app/api/webhooks.py where the Calendly path applies it). That
# is right for Calendly, where Vectorae finds out a meeting exists and nothing
# ever tells it the meeting fell through: a link the invitee cancels produces a
# separate `invitee.canceled` delivery that may or may not arrive, so treating
# the booking as terminal is the safe reading.
#
# A booking made on Vectorae's OWN calendar is different. We own the row. We
# know the moment it is cancelled, because the cancellation is a request to
# this API. So the honest state is PAUSED -- "do not send while a meeting is
# pending" -- and a cancelled meeting puts the lead back into the sequence
# where it left off, instead of stranding a warm lead in a state the engine
# defines as irreversible.
#
# stop_enrollment stays exactly as it is and keeps its irreversibility. This is
# an additional state, not a weakening of that one.

MEETING_PAUSE_REASON = "meeting_pending"

# How long after a meeting ends before the sequence is allowed to resume on
# its own. A day, because the useful outcome of a call is recorded by a human
# within a day of it happening (won, lost, replied), and any of those already
# stops the enrollment through the existing rules. This timer only matters for
# the call nobody wrote up -- and there, resuming outreach a full day later is
# better than never touching the lead again.
MEETING_PAUSE_GRACE = timedelta(days=1)


def pause_for_meeting(session: Session, lead: Lead,
                      until: datetime | None = None,
                      now: datetime | None = None) -> int:
    """Pause every ACTIVE enrollment for `lead` while a meeting is pending.

    Returns how many were paused. Never touches STOPPED enrollments -- a lead
    who unsubscribed and later booked through a colleague's link must not have
    their outreach quietly resurrected by this.

    `until` defaults to one day after `now`; callers that know the meeting's
    end time pass end + MEETING_PAUSE_GRACE, so the resume sweep
    (resume_due_enrollments, already run on every dispatch tick) picks the
    lead back up on its own if nothing else happened.
    """
    now = now or _now()
    until = until or (now + MEETING_PAUSE_GRACE)
    paused = 0
    for enrollment in _enrollments_for_lead(session, lead.id):
        if enrollment.status is not EnrollmentStatus.ACTIVE:
            continue
        pause_enrollment(session, enrollment, until=until)
        enrollment.stop_reason = MEETING_PAUSE_REASON
        paused += 1
    session.commit()
    if paused:
        logger.info("paused %d enrollment(s) for lead %s until %s "
                    "(meeting pending)", paused, lead.id, until.isoformat())
    return paused


def resume_after_meeting_cancelled(session: Session, lead: Lead,
                                   now: datetime | None = None) -> int:
    """Undo pause_for_meeting when the meeting is cancelled.

    Only resumes enrollments THIS feature paused -- the stop_reason check is
    what keeps a cancelled meeting from also un-pausing an out-of-office pause
    that has nothing to do with it, which would send into an inbox the engine
    has been told is unattended.
    """
    now = now or _now()
    resumed = 0
    for enrollment in _enrollments_for_lead(session, lead.id):
        if enrollment.status is not EnrollmentStatus.PAUSED:
            continue
        if enrollment.stop_reason != MEETING_PAUSE_REASON:
            continue
        enrollment.status = EnrollmentStatus.ACTIVE
        enrollment.paused_until = None
        enrollment.stop_reason = None
        # The pause pushed every scheduled message out to `paused_until`.
        # Pulling them back to the next send window is what makes "resume"
        # mean resume rather than "resume, tomorrow".
        pending = session.execute(
            select(Message).where(
                Message.sequence_id == enrollment.sequence_id,
                Message.lead_id == enrollment.lead_id,
                Message.status == MessageStatus.SCHEDULED,
            )
        ).scalars().all()
        for msg in pending:
            msg.scheduled_at = next_window_slot(now, lead_timezone(lead))
        resumed += 1
    session.commit()
    return resumed
