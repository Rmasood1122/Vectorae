"""L0: assisted LinkedIn tasks. Vectorae drafts; the USER acts on LinkedIn.

Phase 5B tier L0 (evals/vectorae). There is no third path:
  * Vectorae never sends, clicks, connects, comments or reads anything on
    LinkedIn. This module makes NO network call and imports no LinkedIn
    client; tests/test_linkedin_guard.py fails if one appears.
  * A task holds text Vectorae drafted (already claim-checked by the caller)
    and the prospect's PUBLIC profile URL (from the user's own connections
    export or a licensed data source, never scraped).
  * The user approves ONE task at a time (no bulk approve), the approval
    expires after 24 hours, and "open" returns a plain profile link plus the
    text to copy. The user sends it themselves and marks the task done,
    skipped or replied.

SAFETY RULES (each has a test in tests/test_linkedin_tasks.py)
  * Suppression is global (lead_tasks.is_suppressed): checked when a task is
    created AND again when it is opened. consent.suppress_everywhere cancels
    open tasks for the person immediately.
  * Daily caps protect the USER's LinkedIn account: connection notes <= 20,
    messages + comments <= 50 per user per local day, counted on "open".
    The admin settings can lower these, never raise them.
  * Business hours only, in the user's account timezone (the send window).
  * LinkedIn approval is separate from the email daily approval: approving a
    task here approves only that task.

Every state change writes a security-audit event (who, what, when, task id;
never the message text).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import LinkedInTask, User
from app.services import linkedin_profile, security_audit

APPROVAL_TTL = timedelta(hours=24)
KINDS = ("connection_note", "message", "comment")
SOURCES = ("network", "sequence")
OPEN_STATES = ("pending", "approved", "opened")
OUTCOMES = ("done", "skipped", "replied")
CONNECTION_NOTE_MAX = 300   # LinkedIn's limit for an invitation note

# Hard ceilings. Settings can only lower them (_cap).
HARD_CAPS = {"connection_note": 20, "message": 50}
_CAP_SETTING = {"connection_note": "linkedin_daily_connection_limit",
                "message": "linkedin_daily_message_limit"}

# Audit actions (security_audit.record).
TASK_CREATED = "linkedin_task.created"
TASK_APPROVED = "linkedin_task.approved"
TASK_OPENED = "linkedin_task.opened"
TASK_COMPLETED = "linkedin_task.completed"
TASK_CANCELLED = "linkedin_task.cancelled"
TASK_EXPIRED = "linkedin_task.expired"


class TaskError(Exception):
    """A refused action. `status` is the HTTP status the API answers."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _cap_class(kind: str) -> str:
    return "connection_note" if kind == "connection_note" else "message"


def _cap(db: Session, cap_class: str) -> int:
    from app.services import system_settings  # noqa: PLC0415

    hard = HARD_CAPS[cap_class]
    try:
        configured = int(system_settings.get(db, _CAP_SETTING[cap_class]))
    except (TypeError, ValueError, KeyError):
        configured = hard
    return max(0, min(hard, configured))


def _suppressed(db: Session, profile_url: str) -> bool:
    from app.workers.lead_tasks import is_suppressed  # noqa: PLC0415

    return is_suppressed(db, linkedin=profile_url)


def _audit(db: Session, action: str, task: LinkedInTask, request=None, **details) -> None:
    security_audit.record(db, action=action, request=request, user_id=task.user_id,
                          target_type="linkedin_task", target_id=task.id,
                          details={"kind": task.kind, "source": task.source,
                                   "status": task.status, **details})


def own(db: Session, user: User, task_id) -> LinkedInTask:
    try:
        task_id = task_id if isinstance(task_id, uuid.UUID) else uuid.UUID(str(task_id))
    except ValueError:
        raise TaskError(404, "Task not found.") from None
    task = db.get(LinkedInTask, task_id)
    if task is None or task.user_id != user.id:
        raise TaskError(404, "Task not found.")
    return task


# --------------------------------------------------------------------------
# Lifecycle
# --------------------------------------------------------------------------


def create(db: Session, user: User, *, source: str, kind: str, text: str, profile_url: str,
           claim_checked: bool, connection_id=None, lead_id=None,
           request=None) -> LinkedInTask:
    """Queue one task for the user. `claim_checked` must be True: the caller
    ran the same claim check email uses on `text` (network_messages drafts
    through _without_unsupported; the sequence writer through the claim
    verifier). Refuses a suppressed person, a non-profile URL, an empty or
    over-long text."""
    if not claim_checked:
        raise ValueError("LinkedIn task text must pass the claim check before it is queued")
    if source not in SOURCES or kind not in KINDS:
        raise ValueError(f"bad task source/kind {source!r}/{kind!r}")
    url = linkedin_profile.public_profile_url(profile_url)
    if url is None:
        raise TaskError(422, "This is not a LinkedIn member profile link.")
    text = (text or "").strip()
    if not text:
        raise TaskError(422, "The task has no text.")
    if kind == "connection_note" and len(text) > CONNECTION_NOTE_MAX:
        raise TaskError(422, f"A connection note is at most {CONNECTION_NOTE_MAX} characters.")
    if _suppressed(db, url):
        raise TaskError(409, "This person is on the do-not-contact list.")
    task = LinkedInTask(id=uuid.uuid4(), user_id=user.id, source=source, kind=kind,
                        connection_id=connection_id, lead_id=lead_id, profile_url=url,
                        profile_key=linkedin_profile.normalize_profile(url), text=text,
                        status="pending")
    db.add(task)
    db.flush()
    _audit(db, TASK_CREATED, task, request)
    return task


def edit(db: Session, user: User, task_id, text: str) -> LinkedInTask:
    """The user edits the draft. Editing withdraws an existing approval: what
    was approved is no longer what will be sent."""
    task = own(db, user, task_id)
    if task.status not in ("pending", "approved"):
        raise TaskError(409, "Only a task that has not been opened can be edited.")
    text = (text or "").strip()
    if not text:
        raise TaskError(422, "The task has no text.")
    if task.kind == "connection_note" and len(text) > CONNECTION_NOTE_MAX:
        raise TaskError(422, f"A connection note is at most {CONNECTION_NOTE_MAX} characters.")
    task.text = text
    task.status, task.approved_at, task.approval_expires_at = "pending", None, None
    return task


def approve(db: Session, user: User, task_id, *, now: datetime | None = None,
            request=None) -> LinkedInTask:
    """Approve ONE task. There is deliberately no bulk variant."""
    now = now or _now()
    task = own(db, user, task_id)
    if task.status != "pending":
        raise TaskError(409, f"This task is {task.status}.")
    if _suppressed(db, task.profile_url):
        _cancel(db, task, "suppressed", request)
        raise TaskError(409, "This person is on the do-not-contact list.")
    task.status, task.approved_at = "approved", now
    task.approval_expires_at = now + APPROVAL_TTL
    _audit(db, TASK_APPROVED, task, request,
           expires_at=task.approval_expires_at.isoformat())
    return task


def opened_today(db: Session, user_id, cap_class: str, now: datetime) -> int:
    from app.services import daily_send_approval as dsa  # noqa: PLC0415

    tz_name, _ = dsa.account_timezone(db, user_id)
    _, start, end = dsa.local_day(now, tz_name)
    kinds = ("connection_note",) if cap_class == "connection_note" else ("message", "comment")
    return db.execute(
        select(func.count(LinkedInTask.id))
        .where(LinkedInTask.user_id == user_id, LinkedInTask.kind.in_(kinds),
               LinkedInTask.opened_at.is_not(None),
               LinkedInTask.opened_at >= start, LinkedInTask.opened_at < end)
    ).scalar_one()


def waiting_count(db: Session, user_id, cap_class: str) -> int:
    """Tasks of this cap class the user has not acted on yet (pending or
    approved). The sequence engine stops queuing more once this reaches the
    daily cap, so the queue never outgrows what the user may open today."""
    kinds = ("connection_note",) if cap_class == "connection_note" else ("message", "comment")
    return db.execute(
        select(func.count(LinkedInTask.id))
        .where(LinkedInTask.user_id == user_id, LinkedInTask.kind.in_(kinds),
               LinkedInTask.status.in_(("pending", "approved")))
    ).scalar_one()


def open_task(db: Session, user: User, task_id, *, now: datetime | None = None,
              request=None) -> dict:
    """The user is about to act on LinkedIn. Re-checks everything, counts the
    task against today's cap, and returns the plain profile link and text."""
    from app.services import daily_send_approval as dsa  # noqa: PLC0415
    from app.services import sequence_engine  # noqa: PLC0415

    now = now or _now()
    task = own(db, user, task_id)
    if task.status == "opened":
        return _open_payload(task)            # re-opening the same task is free
    if task.status != "approved":
        raise TaskError(409, "Approve this task first.")
    if _aware(task.approval_expires_at) is None or _aware(task.approval_expires_at) <= now:
        task.status = "expired"
        _audit(db, TASK_EXPIRED, task, request)
        raise TaskError(409, "The approval expired after 24 hours. Approve it again.")
    if _suppressed(db, task.profile_url):
        _cancel(db, task, "suppressed", request)
        raise TaskError(409, "This person is on the do-not-contact list.")
    tz_name, _ = dsa.account_timezone(db, user.id)
    if not sequence_engine.in_send_window(now, tz_name):
        raise TaskError(409, "LinkedIn tasks open during business hours only.")
    cap_class = _cap_class(task.kind)
    cap = _cap(db, cap_class)
    if opened_today(db, user.id, cap_class, now) >= cap:
        noun = "connection notes" if cap_class == "connection_note" else "messages"
        raise TaskError(429, f"Today's limit of {cap} LinkedIn {noun} is reached. "
                             "It protects your LinkedIn account. Try again tomorrow.")
    task.status, task.opened_at = "opened", now
    _audit(db, TASK_OPENED, task, request)
    return _open_payload(task)


def _open_payload(task: LinkedInTask) -> dict:
    return {"id": str(task.id), "profile_url": task.profile_url, "text": task.text,
            "kind": task.kind,
            "instructions": "Open the profile on LinkedIn, paste the text and send it "
                            "yourself, then mark the task done."}


def complete(db: Session, user: User, task_id, outcome: str, *,
             now: datetime | None = None, request=None) -> LinkedInTask:
    if outcome not in OUTCOMES:
        raise TaskError(422, f"Outcome must be one of {', '.join(OUTCOMES)}.")
    task = own(db, user, task_id)
    allowed = OPEN_STATES if outcome == "skipped" else ("opened",)
    if task.status not in allowed:
        raise TaskError(409, f"This task is {task.status}.")
    task.status, task.completed_at = outcome, now or _now()
    if task.source == "network" and task.connection_id and outcome in ("done", "replied"):
        # The user sent it themselves: the network message reads as sent
        # ("copy" = by hand), so it is never queued or drafted again.
        from app.db.models import LinkedInNetworkMessage  # noqa: PLC0415

        msg = db.execute(select(LinkedInNetworkMessage).where(
            LinkedInNetworkMessage.user_id == task.user_id,
            LinkedInNetworkMessage.connection_id == task.connection_id)).scalars().first()
        if msg is not None and msg.status != "sent":
            msg.status, msg.channel, msg.sent_at = "sent", "copy", task.completed_at
    if task.source == "sequence" and task.lead_id:
        from app.db.models import Lead  # noqa: PLC0415
        from app.services import linkedin_outreach  # noqa: PLC0415

        lead = db.get(Lead, task.lead_id)
        if lead is not None:
            linkedin_outreach.after_task(db, lead, task.kind, outcome, task.completed_at)
    _audit(db, TASK_COMPLETED, task, request, outcome=outcome)
    return task


def _cancel(db: Session, task: LinkedInTask, reason: str, request=None) -> None:
    task.status, task.cancel_reason = "cancelled", reason[:60]
    _audit(db, TASK_CANCELLED, task, request, reason=reason)


def cancel_for_profile(db: Session, url_or_slug: str, reason: str = "suppressed") -> int:
    """Cancel every open task, in every account, for one person. Called when
    the person is suppressed (unsubscribe, "stop" reply, GDPR delete)."""
    key = linkedin_profile.normalize_profile(url_or_slug)
    if not key:
        return 0
    rows = db.execute(select(LinkedInTask).where(
        LinkedInTask.profile_key == key, LinkedInTask.status.in_(OPEN_STATES))).scalars().all()
    for task in rows:
        _cancel(db, task, reason)
    return len(rows)


def list_for(db: Session, user: User, statuses: tuple[str, ...] | None = None) -> list[LinkedInTask]:
    q = select(LinkedInTask).where(LinkedInTask.user_id == user.id)
    if statuses:
        q = q.where(LinkedInTask.status.in_(statuses))
    return list(db.execute(q.order_by(LinkedInTask.created_at)).scalars().all())


def out(task: LinkedInTask, now: datetime | None = None) -> dict:
    now = now or _now()
    expires = _aware(task.approval_expires_at)
    return {
        "id": str(task.id), "source": task.source, "kind": task.kind,
        "connection_id": str(task.connection_id) if task.connection_id else None,
        "lead_id": str(task.lead_id) if task.lead_id else None,
        "profile_url": task.profile_url, "text": task.text, "status": task.status,
        "approved_at": task.approved_at, "approval_expires_at": task.approval_expires_at,
        "approval_expired": bool(task.status == "approved" and expires and expires <= now),
        "opened_at": task.opened_at, "completed_at": task.completed_at,
        "cancel_reason": task.cancel_reason, "created_at": task.created_at,
    }
