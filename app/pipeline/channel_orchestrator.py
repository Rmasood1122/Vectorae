"""Channel stagnation: a pipeline step that moves a quiet lead to its next best
channel (Feature A4).

The research pipeline in engine.py builds a strategy; this step runs over the
EXECUTING side of it -- every active enrollment -- once an hour (Celery beat,
app/workers/channel_tasks.py) and on demand.

STAGNANT means: the lead's last N sends on one channel -- all sent after the
lead's last GENUINE reply on any channel -- went unanswered, and the newest of
them is at least `stagnation_min_hours` old. N is `stagnation_email_sends` for
email (default 3) and `stagnation_other_sends` for everything else (default 2).
Out-of-office notices, auto-responders and bounces are not replies.

THE NEXT CHANNEL is the first that is actually usable for THIS lead:
  * allowed by the owner's plan (app/core/plans.py channels);
  * reachable: an email address / a LinkedIn URL plus a connected LinkedIn
    account / a number with recorded call consent while AI calling is enabled
    / a WhatsApp opt-in;
  * not a channel the lead is already stagnant on.
The order is `ranking` (Feature A5's outcome-based channel ranking when
available) and otherwise a fixed preference: from email -> LinkedIn -> phone ->
WhatsApp; from anything else -> email first.

WHAT HAPPENS. A `channel_suggestions` row, always. With
`stagnation_auto_switch_enabled`, the lead's NEXT SCHEDULED message is moved to
the new channel in place -- it keeps its step and brief, is rendered for the new
channel at send time, and still passes every send-time gate (suppression,
consent, caps, windows, the claim engine). Nothing is ever scheduled twice.
WhatsApp is suggest-only: a cold WhatsApp touch needs an approved template, a
choice a person makes.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    ChannelSuggestion,
    ChannelType,
    EnrollmentStatus,
    InboundReply,
    Lead,
    Message,
    MessageStatus,
    OptInStatus,
    SequenceEnrollment,
    User,
)

logger = logging.getLogger(__name__)

_PREFERENCE = {
    "email": ["linkedin", "phone", "whatsapp"],
    "linkedin": ["email", "phone", "whatsapp"],
    "whatsapp": ["email", "linkedin", "phone"],
    "phone": ["email", "linkedin", "whatsapp"],
}
_PLAN_CHANNEL = {"email": "gmail", "linkedin": "linkedin", "whatsapp": "whatsapp",
                 "phone": "phone"}
AUTO_SWITCHABLE = {"email", "linkedin", "phone"}
_NOT_A_REPLY = ("automated_response", "out_of_office", "bounce")
_RECENT_DECISION = timedelta(days=14)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _setting(session: Session, key: str):
    from app.services import system_settings  # noqa: PLC0415

    return system_settings.get(session, key)


def last_genuine_reply_at(session: Session, lead_id) -> datetime | None:
    rows = session.execute(
        select(InboundReply.received_at, InboundReply.created_at, InboundReply.classification,
               InboundReply.authenticity_kind)
        .where(InboundReply.lead_id == lead_id)
    ).all()
    times = [_aware(received or created) for received, created, cls, kind in rows
             if cls not in _NOT_A_REPLY and kind in (None, "genuine")]
    return max(times) if times else None


def unanswered_sends(session: Session, lead_id) -> dict[str, dict]:
    """{channel: {"count", "last_sent_at"}} for sends after the last genuine reply."""
    since = last_genuine_reply_at(session, lead_id)
    query = select(Message.channel, Message.sent_at).where(
        Message.lead_id == lead_id, Message.status == MessageStatus.SENT,
        Message.sent_at.isnot(None))
    out: dict[str, dict] = {}
    for channel, sent_at in session.execute(query).all():
        sent_at = _aware(sent_at)
        if since is not None and sent_at <= since:
            continue
        entry = out.setdefault(getattr(channel, "value", channel),
                               {"count": 0, "last_sent_at": None})
        entry["count"] += 1
        if entry["last_sent_at"] is None or sent_at > entry["last_sent_at"]:
            entry["last_sent_at"] = sent_at
    return out


def _threshold(session: Session, channel: str) -> int:
    return int(_setting(session, "stagnation_email_sends" if channel == "email"
                        else "stagnation_other_sends"))


def candidate_channels(session: Session, lead: Lead, owner: User | None, *,
                       current: str, exclude: set[str],
                       ranking: list[str] | None = None) -> list[tuple[str, str]]:
    """[(channel, why it is usable)] in preference order."""
    from app.core.plans import get_plan  # noqa: PLC0415
    from app.services import linkedin_profile, phone_calls  # noqa: PLC0415
    from app.services import whatsapp_optin as optin_svc  # noqa: PLC0415

    plan_key = getattr(getattr(owner, "plan", None), "value", getattr(owner, "plan", None))
    allowed = set(get_plan(plan_key or "free").get("channels") or ["gmail"])
    order = [c for c in (ranking or []) if c != current] or _PREFERENCE.get(current, [])
    out: list[tuple[str, str]] = []
    for channel in order:
        if channel in exclude or channel == current or _PLAN_CHANNEL.get(channel) not in allowed:
            continue
        if channel == "email" and lead.email:
            out.append((channel, "has a verified email address"))
        elif channel == "linkedin" and owner is not None \
                and linkedin_profile.public_profile_url(lead.linkedin_url):
            # L0: no account to connect; the step becomes a task YOU send.
            out.append((channel, "has a LinkedIn profile; you send it yourself as a task"))
        elif channel == "phone" and _setting(session, "phone_calling_enabled") \
                and phone_calls.e164(lead.phone) and phone_calls.consent_ok(session, lead):
            out.append((channel, "has a dialable number with recorded call consent"))
        elif channel == "whatsapp" and lead.phone \
                and optin_svc.current_status(session, lead.id) is OptInStatus.OPTED_IN:
            out.append((channel, "has opted in to WhatsApp"))
    return out


def next_scheduled_message(session: Session, lead_id, enrollment_id=None) -> Message | None:
    query = select(Message).where(Message.lead_id == lead_id,
                                  Message.status == MessageStatus.SCHEDULED)
    if enrollment_id is not None:
        enrollment = session.get(SequenceEnrollment, enrollment_id)
        if enrollment is not None:
            query = query.where(Message.sequence_id == enrollment.sequence_id)
    return session.execute(query.order_by(Message.scheduled_at)).scalars().first()


def apply_switch(session: Session, suggestion: ChannelSuggestion,
                 now: datetime | None = None) -> Message | None:
    """Move the lead's next scheduled message to the suggested channel. No commit."""
    if suggestion.to_channel not in AUTO_SWITCHABLE:
        return None
    message = next_scheduled_message(session, suggestion.lead_id, suggestion.enrollment_id)
    if message is None:
        return None
    message.channel = ChannelType(suggestion.to_channel)
    message.whatsapp_kind = None
    message.whatsapp_template_id = None
    message.error = None
    suggestion.switched_message_id = message.id
    suggestion.decided_at = now or _now()
    return message


def detect_stagnation(session: Session, now: datetime | None = None, *, user_id=None,
                      ranking_for=None) -> dict:
    """Run the step. `user_id` limits it to one account (the "check now"
    button); `ranking_for(session, lead)` may supply a channel ranking."""
    from app.services import notifications  # noqa: PLC0415

    now = now or _now()
    result = {"checked": 0, "suggested": 0, "auto_switched": 0}
    if not _setting(session, "stagnation_detection_enabled"):
        return result
    min_age = timedelta(hours=int(_setting(session, "stagnation_min_hours")))
    auto = bool(_setting(session, "stagnation_auto_switch_enabled"))

    enrollments = session.execute(
        select(SequenceEnrollment).where(SequenceEnrollment.status == EnrollmentStatus.ACTIVE)
    ).scalars().all()
    seen_leads: set = set()
    for enrollment in enrollments:
        if enrollment.lead_id in seen_leads:
            continue
        seen_leads.add(enrollment.lead_id)
        lead = session.get(Lead, enrollment.lead_id)
        if lead is None:
            continue
        owner_id = notifications.owner_of_lead(session, lead)
        if user_id is not None and owner_id != user_id:
            continue
        result["checked"] += 1

        sends = unanswered_sends(session, lead.id)
        stagnant = {ch for ch, info in sends.items()
                    if info["count"] >= _threshold(session, ch)
                    and now - info["last_sent_at"] >= min_age}
        if not stagnant:
            continue
        current = max(stagnant, key=lambda ch: sends[ch]["last_sent_at"])
        recent = session.execute(
            select(ChannelSuggestion).where(ChannelSuggestion.lead_id == lead.id)
            .order_by(ChannelSuggestion.created_at.desc())
        ).scalars().first()
        if recent is not None and (recent.status == "suggested" or
                                   now - _aware(recent.created_at) < _RECENT_DECISION):
            continue

        owner = session.get(User, owner_id) if owner_id else None
        ranking = ranking_for(session, lead) if ranking_for else None
        candidates = candidate_channels(session, lead, owner, current=current,
                                        exclude=stagnant, ranking=ranking)
        if not candidates:
            continue
        to_channel, why = candidates[0]
        count = sends[current]["count"]
        suggestion = ChannelSuggestion(
            lead_id=lead.id, user_id=owner_id, enrollment_id=enrollment.id,
            from_channel=current, to_channel=to_channel, sends_without_reply=count,
            reason=(f"{count} {current} touches with no reply; {to_channel} is next because "
                    f"this lead {why}.")[:300],
            status="suggested",
        )
        session.add(suggestion)
        session.flush()
        result["suggested"] += 1
        if auto and apply_switch(session, suggestion, now) is not None:
            suggestion.status = "auto_switched"
            result["auto_switched"] += 1
    session.commit()
    logger.info("channel stagnation step: %s", result)
    return result


class SuggestionError(ValueError):
    def __init__(self, message: str, status_code: int = 409):
        super().__init__(message)
        self.status_code = status_code


def accept(session: Session, suggestion: ChannelSuggestion, now: datetime | None = None) -> dict:
    if suggestion.status != "suggested":
        raise SuggestionError("This suggestion has already been decided.")
    if suggestion.to_channel not in AUTO_SWITCHABLE:
        raise SuggestionError("A WhatsApp touch needs an approved template: add a WhatsApp "
                              "step to the sequence instead.", status_code=422)
    message = apply_switch(session, suggestion, now)
    suggestion.status = "accepted"
    suggestion.decided_at = suggestion.decided_at or now or _now()
    session.commit()
    return {"status": suggestion.status,
            "switched_message_id": str(message.id) if message else None,
            "note": None if message else "No message is scheduled for this lead; add a step "
                                         "on the suggested channel to act on it."}


def dismiss(session: Session, suggestion: ChannelSuggestion, now: datetime | None = None) -> None:
    if suggestion.status != "suggested":
        raise SuggestionError("This suggestion has already been decided.")
    suggestion.status = "dismissed"
    suggestion.decided_at = now or _now()
    session.commit()


def suggestion_out(row: ChannelSuggestion) -> dict:
    return {
        "id": str(row.id), "lead_id": str(row.lead_id), "from_channel": row.from_channel,
        "to_channel": row.to_channel, "sends_without_reply": row.sends_without_reply,
        "reason": row.reason, "status": row.status,
        "switched_message_id": str(row.switched_message_id) if row.switched_message_id else None,
        "created_at": _aware(row.created_at).isoformat() if row.created_at else None,
        "decided_at": _aware(row.decided_at).isoformat() if row.decided_at else None,
    }
