"""Engagement Hub Celery tasks: what happens around a booking and a meeting.

Every `_impl` takes a Session and an explicit `now`, matching
app/workers/outreach_tasks.py, so the test suite drives them directly on
SQLite without a broker.

WHY THE POST-BOOKING WORK IS A TASK AND NOT PART OF THE REQUEST
The public booking endpoint is the last thing a prospect touches before they
believe they have a meeting. Everything after the row is written -- two
emails, a lead status change, an outcome, a CRM activity, pausing whatever
sequence was mid-flight -- is work that must not be able to fail their
booking. A confirmation email that times out has to leave a booked meeting
behind, not a 500 and a prospect who tries again.

So the endpoint commits the booking and returns; this module does the rest,
with retries. If the whole task fails permanently the booking still exists and
is visible on the host's calendar -- degraded, not lost.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.db.base import SessionLocal
from app.db.models import (
    BookingStatus,
    CalendarBooking,
    CalendarBookingPage,
    Lead,
    LeadStatus,
    Meeting,
    MeetingStatus,
    Outcome,
    OutcomeEvent,
    User,
)
from app.services import crm_events
from app.services import sequence_engine as engine
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

CHANNEL = "leadpilot_calendar"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    """UTC-aware view of a timestamptz column. See outreach_tasks._aware."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _format_when(moment: datetime, tz_name: str | None) -> str:
    """A human time string in `tz_name`, always naming the zone.

    The zone is always printed, even when it is UTC. A confirmation that says
    "Tuesday at 15:00" without saying where is the single most reliable way to
    have somebody miss a call.
    """
    from app.services.calendar_service import resolve_timezone  # noqa: PLC0415

    tz = resolve_timezone(tz_name)
    local = _aware(moment).astimezone(tz)
    return f"{local.strftime('%A %d %B %Y, %H:%M')} ({tz_name or 'UTC'})"


# ---------------------------------------------------------------------------
# Emails
# ---------------------------------------------------------------------------


def _send(to: str | None, subject: str, lines: list[str]) -> None:
    """One transactional email through the existing gateway. Never raises.

    app/services/email_sender.py::send_email raises EmailSendError on a
    transport failure, and here that must not undo the booking -- the same
    call the signup path makes for the same reason (see auth.py). A failed
    confirmation is a resend; a rolled-back booking is a lost customer.
    """
    if not to:
        return
    from app.services.email_sender import send_email  # noqa: PLC0415

    text = "\n".join(lines)
    html = "<p>" + "</p><p>".join(
        line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        for line in lines if line.strip()
    ) + "</p>"
    try:
        send_email(to=to, subject=subject, html=html, text=text)
    except Exception as exc:  # noqa: BLE001
        logger.warning("calendar email to %s failed (%s): %s", to,
                       type(exc).__name__, exc)


def _confirmation_emails(session: Session, booking: CalendarBooking,
                         page: CalendarBookingPage) -> None:
    host = session.get(User, page.user_id)
    host_tz = _host_timezone(session, page)
    when_invitee = _format_when(booking.start_at, booking.invitee_timezone or host_tz)
    when_host = _format_when(booking.start_at, host_tz)
    link = booking.meeting_link or "(the host will send a link)"

    _send(
        booking.invitee_email,
        f"Confirmed: {page.title}",
        [
            f"Hi {booking.invitee_name},",
            f"Your {page.duration_minutes}-minute {page.title} is confirmed for "
            f"{when_invitee}.",
            f"Join link: {link}",
            "Reply to this email if you need to move it.",
        ],
    )
    _send(
        getattr(host, "email", None),
        f"New booking: {booking.invitee_name} — {page.title}",
        [
            f"{booking.invitee_name} ({booking.invitee_email}) booked "
            f"{page.title}.",
            f"When: {when_host}",
            f"Phone: {booking.invitee_phone or '—'}",
            f"Join link: {link}",
            _answers_block(booking),
        ],
    )


def _answers_block(booking: CalendarBooking) -> str:
    answers = booking.answers or {}
    if not answers:
        return ""
    return "Answers:\n" + "\n".join(f"- {k}: {v}" for k, v in answers.items())


def _host_timezone(session: Session, page: CalendarBookingPage) -> str:
    from sqlalchemy import select  # noqa: PLC0415

    from app.db.models import CalendarAvailability  # noqa: PLC0415

    return session.execute(
        select(CalendarAvailability.timezone)
        .where(CalendarAvailability.user_id == page.user_id,
               CalendarAvailability.is_active.is_(True))
        .order_by(CalendarAvailability.day_of_week,
                  CalendarAvailability.start_time)
    ).scalars().first() or "UTC"


# ---------------------------------------------------------------------------
# Booking created
# ---------------------------------------------------------------------------


def on_booking_created_impl(session: Session, booking_id: uuid.UUID,
                            now: datetime | None = None) -> str:
    """Everything that follows a confirmed booking, in one transaction.

    Order matters. The lead's status and the outcome row are written and
    committed BEFORE the emails go out, so a mail relay having a bad minute
    cannot leave the CRM believing no meeting was booked.
    """
    now = now or _now()
    booking = session.get(CalendarBooking, booking_id)
    if booking is None:
        return "missing"
    page = session.get(CalendarBookingPage, booking.booking_page_id)
    if page is None:
        return "orphan_booking"

    lead = session.get(Lead, booking.lead_id) if booking.lead_id else None
    if lead is not None:
        previous = lead.status
        lead.status = LeadStatus.MEETING_BOOKED
        session.add(Outcome(
            lead_id=lead.id,
            strategy_id=lead.strategy_id,
            event=OutcomeEvent.BOOKED,
            channel=CHANNEL,
            meta_json={"booking_id": str(booking.id),
                       "booking_page": page.slug,
                       "start_at": _aware(booking.start_at).isoformat(),
                       "from_status": previous.value if previous else None},
        ))
        crm_events.on_booking_created(
            session, lead.id, booking.id,
            when=_aware(booking.start_at), page_title=page.title,
        )
        session.commit()

        # PAUSE, not stop -- see sequence_engine.pause_for_meeting for why
        # this path differs from the Calendly one.
        engine.pause_for_meeting(
            session, lead,
            until=_aware(booking.end_at) + engine.MEETING_PAUSE_GRACE,
            now=now,
        )
        _notify_owner(session, lead, booking)

        # Feature Group 7: queue the meeting prep brief. Keyed on the booking
        # id, so this task's retries update one brief rather than making more.
        from app.services.meeting_prep import SOURCE_CALENDAR  # noqa: PLC0415
        from app.workers.meeting_prep_tasks import request_prep  # noqa: PLC0415

        request_prep(
            session, lead, source=SOURCE_CALENDAR, external_ref=str(booking.id),
            meeting_start_at=_aware(booking.start_at),
            meeting_url=booking.meeting_link, booking_id=booking.id,
            user_id=page.user_id,
        )
    else:
        session.commit()

    _confirmation_emails(session, booking, page)
    return "ok"


def _notify_owner(session: Session, lead: Lead, booking: CalendarBooking) -> None:
    """Push notification to the lead's owner. Never raises (dispatch swallows).

    Reuses the M7 notification the Calendly path already sends, so a booking
    made on Vectorae's own calendar reaches the user's phone the same way a
    Calendly one does rather than being the quiet kind of booking.
    """
    from app.services import notifications  # noqa: PLC0415

    owner = notifications.owner_of_lead(session, lead)
    if owner is None:
        logger.warning("lead %s has no resolvable owner — no booking "
                       "notification", lead.id)
        return
    notifications.dispatch(notifications.notify_meeting_booked(
        owner, lead.id, attendee_name=booking.invitee_name,
    ))
    # Feature Group 4: Slack + outbound webhooks (push=False: sent above).
    from app.services import event_bus  # noqa: PLC0415
    from app.workers import notification_tasks  # noqa: PLC0415

    notification_tasks.enqueue_event(
        owner, "meeting_booked", push=False, title="Meeting booked",
        body=f"{booking.invitee_name or lead.full_name or 'A lead'} booked a meeting "
             f"on your Vectorae calendar.",
        deep_link=f"/leads/detail?id={lead.id}", data={"leadId": str(lead.id)},
        webhook_payload={"lead": event_bus.lead_payload(lead), "source": "leadpilot_calendar",
                         "booking_id": str(booking.id)},
    )


# ---------------------------------------------------------------------------
# Booking cancelled
# ---------------------------------------------------------------------------


def on_booking_cancelled_impl(session: Session, booking_id: uuid.UUID,
                              now: datetime | None = None,
                              reason: str | None = None) -> str:
    """Cancellation email, and put the lead back into its sequence.

    The resume is the reason pause_for_meeting exists. A cancelled meeting on
    Vectorae's own calendar is a fact we own, so the lead goes back to where
    they were instead of sitting in the terminal state a Calendly cancellation
    would have left them in.
    """
    now = now or _now()
    booking = session.get(CalendarBooking, booking_id)
    if booking is None:
        return "missing"
    page = session.get(CalendarBookingPage, booking.booking_page_id)
    host = session.get(User, page.user_id) if page else None

    lead = session.get(Lead, booking.lead_id) if booking.lead_id else None
    if lead is not None:
        if lead.status is LeadStatus.MEETING_BOOKED:
            # Back to REPLIED, matching what the Calendly cancellation path
            # does: they engaged enough to book, and that is not undone by the
            # calendar entry going away.
            lead.status = LeadStatus.REPLIED
        session.commit()
        engine.resume_after_meeting_cancelled(session, lead, now=now)
        # Feature Group 7: no reminders for a meeting that is not happening.
        from app.services.meeting_prep import cancel_briefs  # noqa: PLC0415

        cancel_briefs(session, lead.id, booking_id=booking.id, now=now)
        session.commit()

    # ONE TIME PER READER, not one time for both. The confirmation path
    # (_confirmation_emails) already renders the invitee's zone for the
    # invitee and the host's for the host; this used to compute a single
    # `when` in the INVITEE's zone and send it to both, so a host whose
    # calendar said 09:30 UTC was told their 10:30 Europe/London meeting was
    # cancelled. The zone label made it unambiguous rather than wrong, but it
    # still made the host convert their own calendar by hand -- on the one
    # email whose whole job is "the slot you were holding is free again".
    host_tz = _host_timezone(session, page) if page else "UTC"
    when_invitee = _format_when(booking.start_at,
                                booking.invitee_timezone or host_tz)
    when_host = _format_when(booking.start_at, host_tz)
    title = page.title if page else "your meeting"
    _send(booking.invitee_email, f"Cancelled: {title}", [
        f"Hi {booking.invitee_name},",
        f"Your {title} on {when_invitee} has been cancelled.",
        (reason or "").strip(),
        "You are welcome to book another time.",
    ])
    _send(getattr(host, "email", None), f"Cancelled: {booking.invitee_name} — {title}", [
        f"The booking with {booking.invitee_name} ({booking.invitee_email}) "
        f"on {when_host} was cancelled.",
        (reason or "").strip(),
    ])
    return "ok"


# ---------------------------------------------------------------------------
# Meeting summary
# ---------------------------------------------------------------------------


def generate_summary_impl(session: Session, meeting_id: uuid.UUID) -> str:
    """Run the AI summary for a meeting and file the results in the CRM.

    Callable from the endpoint (which waits for it, because the user pressed a
    button and is watching a spinner) and from the task queue (which runs it
    after End Meeting, because the user has already walked away). One
    implementation so the two cannot drift.
    """
    from app.services import meeting_ai  # noqa: PLC0415

    meeting = session.get(Meeting, meeting_id)
    if meeting is None:
        return "missing"

    from app.services import usage_meter  # noqa: PLC0415

    # P0-5b: metered to the host's account (on its daily AI limit). Joins the
    # endpoint's scope when called inline from POST /generate-summary.
    with usage_meter.user_scope(session, meeting.host_user_id, "meeting_summary"):
        result = meeting_ai.generate_meeting_summary(
            meeting.transcript, meeting.raw_notes,
            meeting_ai.context_for_meeting(session, meeting),
        )
    if not result.get("ok"):
        logger.info("no summary generated for meeting %s: %s", meeting_id,
                    result.get("error"))
        return f"skipped: {result.get('error')}"

    meeting.summary = result["summary"]
    meeting.key_points = result["key_points"]
    meeting.next_steps = result["next_steps"]
    meeting.sentiment = result["sentiment"]
    # Merge, do not replace: a user may already have ticked items off a
    # previous generation, and regenerating must not silently un-tick them.
    meeting.action_items = _merge_action_items(meeting.action_items,
                                               result["action_items"])
    meeting.ai_notes = _as_notes(result)
    # Feature 3: what this summary was built from, so the panel can say
    # "from your notes -- the transcript is still coming" and upgrade it later.
    meeting.summary_source = "transcript" if (meeting.transcript or "").strip() else "notes"
    session.commit()

    if meeting.lead_id:
        crm_events.on_meeting_completed(
            session, meeting.lead_id, meeting.id,
            summary=meeting.summary, host_user_id=meeting.host_user_id,
        )
        for item in meeting.action_items or []:
            # Only OUR commitments become tasks. An action item the client
            # owns is information; turning it into a to-do on the user's list
            # is how a task list becomes noise nobody reads.
            if item.get("owner") == "us" and not item.get("done"):
                crm_events.on_action_item_created(
                    session, meeting.lead_id, item, meeting_id=meeting.id,
                    actor_user_id=meeting.host_user_id,
                )
        session.commit()
    return "ok"


def _merge_action_items(existing: list | None, generated: list) -> list:
    """Generated items, with `done` preserved from any matching existing one.

    Matched on the item text. Crude, and deliberately so: the alternative is
    giving each item an id, which would then have to survive a regeneration
    that produces different items entirely. Text matching keeps a ticked box
    ticked in the only case that matters -- the same commitment, re-extracted.
    """
    done = {str(item.get("text", "")).strip().lower()
            for item in (existing or []) if item.get("done")}
    merged = []
    for item in generated:
        entry = dict(item)
        if str(entry.get("text", "")).strip().lower() in done:
            entry["done"] = True
        merged.append(entry)
    # Anything the user added by hand that the model did not re-extract stays.
    generated_texts = {str(i.get("text", "")).strip().lower() for i in generated}
    for item in existing or []:
        if str(item.get("text", "")).strip().lower() not in generated_texts:
            merged.append(dict(item))
    return merged


def _as_notes(result: dict) -> str:
    """The model's output as readable prose, for the transcript-adjacent view."""
    parts = [result.get("summary") or ""]
    if result.get("key_points"):
        parts.append("Key points:\n" + "\n".join(f"- {p}" for p in result["key_points"]))
    if result.get("next_steps"):
        parts.append("Next steps:\n" + "\n".join(f"- {p}" for p in result["next_steps"]))
    return "\n\n".join(p for p in parts if p).strip()


# ---------------------------------------------------------------------------
# Housekeeping sweep
# ---------------------------------------------------------------------------


def close_stale_meetings_impl(session: Session, now: datetime | None = None,
                              grace: timedelta | None = None) -> int:
    """Mark long-finished meetings completed, and no-show their bookings.

    A meeting nobody pressed End on stays `in_progress` forever: its timer
    keeps running in the UI, it keeps blocking slots in the availability
    calculation, and its lead's enrollment never resumes. This closes any
    meeting whose scheduled end is more than `grace` in the past.

    It deliberately does NOT generate a summary for them. Nobody was there to
    take notes, so there is nothing to summarise, and spending a model call per
    abandoned meeting to be told so is a bill with no reader.
    """
    from sqlalchemy import select  # noqa: PLC0415

    now = now or _now()
    grace = grace or timedelta(hours=6)
    cutoff = now - grace

    stale = session.execute(
        select(Meeting).where(
            Meeting.status.in_([MeetingStatus.SCHEDULED,
                                MeetingStatus.IN_PROGRESS]),
            Meeting.end_at < cutoff,
        )
    ).scalars().all()
    for meeting in stale:
        # Only a meeting somebody actually started counts as completed. One
        # that was never started is exactly what "no show" means, and
        # recording it as completed would put a meeting that did not happen
        # into the conversion numbers.
        started = meeting.actual_start_at is not None
        meeting.status = (MeetingStatus.COMPLETED if started
                          else MeetingStatus.CANCELLED)
        if started and meeting.actual_end_at is None:
            meeting.actual_end_at = _aware(meeting.end_at)
        if not started and meeting.booking_id:
            booking = session.get(CalendarBooking, meeting.booking_id)
            if booking is not None and booking.status in (
                BookingStatus.PENDING, BookingStatus.CONFIRMED
            ):
                booking.status = BookingStatus.NO_SHOW
                # Release the slot: a no-show should not keep the host's
                # calendar blocked for a call that did not happen.
                booking.slot_key = None
    session.commit()
    if stale:
        logger.info("closed %d stale meeting(s)", len(stale))
    return len(stale)


# ---------------------------------------------------------------------------
# Celery wrappers
# ---------------------------------------------------------------------------


@celery_app.task(name="app.workers.calendar_tasks.on_booking_created",
                 bind=True, max_retries=3)
def on_booking_created(self, booking_id: str) -> str:
    session = SessionLocal()
    try:
        return on_booking_created_impl(session, uuid.UUID(booking_id))
    except Exception as exc:
        session.rollback()
        logger.exception("post-booking work failed for %s — retrying", booking_id)
        raise self.retry(exc=exc, countdown=60)
    finally:
        session.close()


@celery_app.task(name="app.workers.calendar_tasks.on_booking_cancelled",
                 bind=True, max_retries=3)
def on_booking_cancelled(self, booking_id: str, reason: str | None = None) -> str:
    session = SessionLocal()
    try:
        return on_booking_cancelled_impl(session, uuid.UUID(booking_id),
                                         reason=reason)
    except Exception as exc:
        session.rollback()
        logger.exception("cancellation work failed for %s — retrying", booking_id)
        raise self.retry(exc=exc, countdown=60)
    finally:
        session.close()


@celery_app.task(name="app.workers.calendar_tasks.generate_meeting_summary",
                 bind=True, max_retries=2)
def generate_meeting_summary(self, meeting_id: str) -> str:
    session = SessionLocal()
    try:
        return generate_summary_impl(session, uuid.UUID(meeting_id))
    except Exception as exc:
        session.rollback()
        logger.exception("summary generation failed for meeting %s — retrying",
                         meeting_id)
        raise self.retry(exc=exc, countdown=120)
    finally:
        session.close()


@celery_app.task(name="app.workers.calendar_tasks.close_stale_meetings")
def close_stale_meetings() -> int:
    session = SessionLocal()
    try:
        return close_stale_meetings_impl(session)
    finally:
        session.close()


# Feature 3 -- recording-provider transcripts. See app/services/meeting_recording.py.


@celery_app.task(name="app.workers.calendar_tasks.fetch_meeting_transcript",
                 bind=True, max_retries=3)
def fetch_meeting_transcript(self, meeting_id: str, transcript_id: str | None = None) -> str:
    from app.services import meeting_recording  # noqa: PLC0415

    session = SessionLocal()
    try:
        return meeting_recording.fetch_transcript(session, uuid.UUID(meeting_id), transcript_id)
    except Exception as exc:
        session.rollback()
        logger.exception("transcript fetch failed for meeting %s — retrying", meeting_id)
        raise self.retry(exc=exc, countdown=120)
    finally:
        session.close()


@celery_app.task(name="app.workers.calendar_tasks.expire_transcript_waits")
def expire_transcript_waits() -> int:
    from app.services import meeting_recording  # noqa: PLC0415

    session = SessionLocal()
    try:
        return meeting_recording.expire_waits(session)
    finally:
        session.close()
