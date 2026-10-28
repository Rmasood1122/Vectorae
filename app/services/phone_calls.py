"""Feature Group 6 — AI cold calls: consent, scripts, voicemail, results.

READ THIS BEFORE ENABLING THE CHANNEL
Since the FCC's February 2024 declaratory ruling, AI-generated voices are an
"artificial or prerecorded voice" under the US Telephone Consumer Protection
Act. Calling a US mobile number with one -- or dropping a prerecorded
voicemail on it -- needs the called party's PRIOR EXPRESS CONSENT (written
consent for telemarketing). Other jurisdictions have their own rules. This
module therefore:

  * keeps the channel OFF until an admin enables it (`phone_calling_enabled`);
  * refuses to call a lead with no recorded consent (`phone_consent_at`) while
    `phone_require_consent` is on -- the default -- skipping the step so the
    sequence continues, exactly as a WhatsApp step skips a lead with no
    opt-in;
  * opens every call with a disclosure that the caller is an AI assistant
    calling on the user's behalf, and honours a stop request by suppressing
    the number;
  * calls only inside the lead's local send window (weekdays, 9-17 by
    default) and within a per-user daily call limit.

It does NOT check national Do-Not-Call registries. That is the operator's
obligation before enabling the channel, and the admin setting says so.

WHAT A CALL IS MADE OF
  script     Claude writes it per lead from the step brief, the strategy's
             messaging research, the ICP and the lead's own posts and news,
             in the sender's voice: a first line (with the AI disclosure),
             the objective, talking points, objection handling, questions and
             a close -- plus a short personalised VOICEMAIL in the same call.
  voicemail  If ElevenLabs is configured, the voicemail is also rendered to
             audio (TTS) and hosted, so the user can hear exactly what was
             left. The provider detects a machine and speaks the voicemail in
             the configured ElevenLabs voice.
  result     The provider's end-of-call webhook stores recording, duration,
             transcript and ended reason. A voicemail ends as
             `voicemail_dropped`. A conversation is analysed by Claude
             (outcome, objections, interest signals, next step) on the
             outreach queue -- never inline in the webhook.

ROUTING A RESULT
  interested / not_interested   a human engaged: the sequence stops, like a
                                reply; interested also notifies (push/Slack)
  voicemail / no answer         the sequence continues
  a stop request in the call    number suppressed + sequence stopped
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import (
    Call,
    CallOutcome,
    CrmActivityKind,
    Lead,
    LeadStatus,
    Message,
    Outcome,
    OutcomeEvent,
    Strategy,
)
from app.services import anthropic_client, prompt_safety

logger = logging.getLogger(__name__)

SCRIPT_SYSTEM = (
    "You are Vectorae's cold call script writer. You prepare an AI voice "
    "assistant for ONE outbound sales call. Respond with ONLY a JSON object: "
    '{"first_message": "...", "objective": "...", "talking_points": ["..."], '
    '"objection_handling": [{"objection": "...", "response": "..."}], '
    '"questions": ["..."], "close": "...", "voicemail": "..."}. RULES. '
    "`first_message` is what the assistant says the moment the call connects: "
    "it MUST say plainly that it is an AI assistant calling on behalf of the "
    "named seller, then give one specific reason for calling, then ask if now "
    "is a bad time -- under 45 words. The goal of the call is ONE thing: book "
    "a short meeting, never close a sale on the phone. `voicemail` is under 40 "
    "words, also discloses it is an AI assistant, names the seller and one "
    "reason to call back. Never invent facts about the prospect; no pressure, "
    "no false urgency."
)

ANALYSIS_SYSTEM = (
    "You are Vectorae's call transcript analyst. From the transcript of one "
    "AI sales call, report what happened. Respond with ONLY a JSON object: "
    '{"outcome": "interested"|"not_interested"|"answered", "summary": "...", '
    '"objections": ["..."], "interest_signals": ["..."], "next_step": "...", '
    '"stop_request": true|false, "sentiment": "positive"|"neutral"|'
    '"negative"}. `interested` only if the prospect agreed to a meeting or '
    "asked for one; `not_interested` if they declined; otherwise `answered`. "
    "`stop_request` is true if they asked not to be called again. Quote the "
    "prospect's own words in objections and interest signals where possible; "
    "never invent them."
)

# Output contracts (Phase 3 item 2): what write_script and analyze read.
SCRIPT_SCHEMA = {
    "type": "object",
    "properties": {
        "first_message": {"type": "string"},
        "objective": {"type": "string"},
        "talking_points": {"type": "array", "items": {"type": "string"}},
        "objection_handling": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"objection": {"type": "string"},
                               "response": {"type": "string"}},
                "required": ["objection", "response"],
                "additionalProperties": False,
            },
        },
        "questions": {"type": "array", "items": {"type": "string"}},
        "close": {"type": "string"},
        "voicemail": {"type": "string"},
    },
    "required": ["first_message"],
    "additionalProperties": False,
}

# `outcome` is a closed set the code maps exactly (anything else is
# `answered`); `sentiment` is stored as given, so it stays a free string.
ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "outcome": {"type": "string", "enum": ["interested", "not_interested", "answered"]},
        "summary": {"type": "string"},
        "objections": {"type": "array", "items": {"type": "string"}},
        "interest_signals": {"type": "array", "items": {"type": "string"}},
        "next_step": {"type": "string"},
        "stop_request": {"type": "boolean"},
        "sentiment": {"type": "string"},
    },
    "required": ["outcome"],
    "additionalProperties": False,
}

VOICEMAIL_DIR = "voicemails"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def e164(phone: str | None) -> str | None:
    """'+1 (555) 010-2000' -> '+15550102000'. None when it cannot be a number."""
    if not phone:
        return None
    digits = re.sub(r"[^\d]", "", phone)
    if len(digits) < 8 or len(digits) > 15:
        return None
    return f"+{digits}"


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------


def consent_ok(session: Session, lead: Lead) -> bool:
    from app.services import local_business, system_settings  # noqa: PLC0415

    # Local business mode: no phone consent ever counts for a business's own
    # customer -- calls are not a customer channel (channel_refusal).
    if local_business.channel_refusal(lead, "phone"):
        return False
    if not system_settings.get(session, "phone_require_consent"):
        return True
    return lead.phone_consent_at is not None


def _redis():
    from app.core import redis_client  # noqa: PLC0415

    return redis_client.get_sync_redis()


def reserve_call_slot(session: Session, user_id, now: datetime | None = None) -> bool:
    from app.services import system_settings  # noqa: PLC0415

    limit = system_settings.get(session, "phone_daily_call_limit")
    day = (now or _now()).astimezone(timezone.utc).strftime("%Y-%m-%d")
    key = f"calls:usage:{user_id}:{day}"
    redis = _redis()
    count = redis.incr(key)
    redis.expire(key, 36 * 3600)
    if count > limit:
        redis.decr(key)
        return False
    return True


def release_call_slot(user_id, now: datetime | None = None) -> None:
    day = (now or _now()).astimezone(timezone.utc).strftime("%Y-%m-%d")
    key = f"calls:usage:{user_id}:{day}"
    redis = _redis()
    if int(redis.get(key) or 0) > 0:
        redis.decr(key)


# ---------------------------------------------------------------------------
# Script + voicemail
# ---------------------------------------------------------------------------


def _clean_script(data: dict) -> dict:
    data = data if isinstance(data, dict) else {}

    def _s(v, n=800):
        return str(v).strip()[:n] if isinstance(v, (str, int, float)) else ""

    def _l(v, n=8):
        return [_s(x, 300) for x in (v or []) if isinstance(x, str) and x.strip()][:n]

    objections = []
    for item in data.get("objection_handling") or []:
        if isinstance(item, dict) and _s(item.get("objection")):
            objections.append({"objection": _s(item.get("objection"), 300),
                               "response": _s(item.get("response"), 500)})
    return {"first_message": _s(data.get("first_message"), 500),
            "objective": _s(data.get("objective")),
            "talking_points": _l(data.get("talking_points")),
            "objection_handling": objections[:6],
            "questions": _l(data.get("questions")),
            "close": _s(data.get("close")),
            "voicemail": _s(data.get("voicemail"), 500)}


def _seller_name(session: Session, strategy: Strategy) -> str:
    from app.db.models import Product, User  # noqa: PLC0415

    product = session.get(Product, strategy.product_id)
    user = session.get(User, product.user_id) if product else None
    name = getattr(user, "display_name", None) or (user.email.split("@")[0] if user else "the team")
    return f"{name} ({product.name})" if product else name


def write_script(session: Session, strategy: Strategy, lead: Lead, brief: str) -> dict:
    from app.services import personalization_context, style_profile  # noqa: PLC0415
    from app.services.message_personalization import _playbook  # noqa: PLC0415

    context, _ = personalization_context.prompt_block(lead)
    prompt = "\n".join([
        f"SELLER (the assistant calls on their behalf): {_seller_name(session, strategy)}",
        f"STEP BRIEF: {brief}",
        f"MESSAGING PLAYBOOK:\n{_playbook(session, strategy)[:6000]}",
        f"IDEAL CUSTOMER PROFILE: {strategy.pattern_inputs_json or '(not extracted)'}",
        # Name/title/company come from enrichment (outside text): wrapped,
        # and one notice for the prompt -- personalization_context already
        # opens its block with it, so only add it when that block is empty.
        *([] if context else [prompt_safety.NOTICE]),
        f"PROSPECT: {prompt_safety.inline(lead.full_name or 'unknown')} — "
        f"{prompt_safety.inline(lead.title or '')} at {prompt_safety.inline(lead.company or '')}",
        context,
        "Return the JSON object now.",
    ])
    data = anthropic_client.get_client().complete_json(
        system=SCRIPT_SYSTEM + style_profile.suffix_for_strategy(session, strategy),
        prompt=prompt, max_tokens=2048, schema=SCRIPT_SCHEMA)
    script = _clean_script(data)
    if not script["first_message"]:
        raise ValueError("the call script came back without a first message")
    if "ai" not in script["first_message"].lower().split() and \
            "assistant" not in script["first_message"].lower():
        # The disclosure is not optional. A model that dropped it gets a
        # prefix rather than a call that pretends to be a person.
        script["first_message"] = (f"Hi, this is an AI assistant calling on behalf of "
                                   f"{_seller_name(session, strategy)}. "
                                   + script["first_message"])
    return script


def system_prompt(script: dict, lead: Lead, identity: dict | None = None) -> str:
    """The outbound assistant's prompt. Voice twin: it always STARTS with the
    identity rules (never claim to be human; say plainly it is an AI) and the
    natural-delivery rules -- whatever the script says."""
    from app.services import voice_twin  # noqa: PLC0415

    identity = identity or {}
    names = tuple(identity.get("owner_names") or ()) + (str(identity.get("owner_name") or ""),)

    def said(text) -> str:
        # H1: any line the assistant may say aloud loses a sentence that
        # claims to BE the owner or a human, before it reaches the prompt.
        return voice_twin.strip_identity_claims(str(text or ""), names)

    # What the prospect may say is quoted as data; the reply after it is a
    # line the assistant speaks, so it stays ours (and identity-checked).
    objections = "\n".join(f"- If they say {prompt_safety.inline(o['objection'])}: "
                           f"{said(o['response'])}"
                           for o in script.get("objection_handling") or [])
    rules = voice_twin.identity_rules(
        str(identity.get("owner_name") or "the seller"),
        clone=bool(identity.get("voice_clone")),
        can_transfer=bool(identity.get("transfer_number")))
    return "\n".join([
        rules,
        "",
        voice_twin.STYLE_RULES,
        "",
        "You are an AI assistant making one outbound call on behalf of a seller. "
        "You have already disclosed you are an AI; if asked again, confirm it. "
        "Be brief and polite. Your only goal: book a short meeting. If the person "
        "asks not to be called again, apologise, confirm they will not be called "
        "again, and end the call.",
        prompt_safety.NOTICE,
        f"Prospect: {prompt_safety.inline(lead.full_name or 'unknown')}, "
        f"{prompt_safety.inline(lead.title or '')} at {prompt_safety.inline(lead.company or '')}.",
        f"Objective: {script.get('objective')}",
        "Talking points:\n" + "\n".join(f"- {said(p)}" for p in script.get("talking_points") or []),
        f"Objections:\n{objections}",
        "Questions to ask:\n" + "\n".join(f"- {said(q)}" for q in script.get("questions") or []),
        f"Close: {said(script.get('close'))}",
    ])


def render_voicemail_audio(eleven, text: str) -> str | None:
    """TTS the voicemail and host it under MEDIA_DIR. Never raises."""
    if eleven is None or not eleven.voice_id or not text:
        return None
    try:
        audio = eleven.tts(text)
        folder = Path(settings.media_dir) / VOICEMAIL_DIR
        folder.mkdir(parents=True, exist_ok=True)
        name = f"{uuid.uuid4().hex}.mp3"
        (folder / name).write_bytes(audio)
        return f"{settings.public_base_url.rstrip('/')}/media/{VOICEMAIL_DIR}/{name}"
    except Exception as exc:  # noqa: BLE001
        logger.warning("voicemail TTS failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


_VOICEMAIL_REASONS = ("voicemail",)
_NO_ANSWER_REASONS = ("customer-did-not-answer", "customer-busy", "no-answer", "busy",
                      "silence-timed-out")


def outcome_from_ended_reason(reason: str | None, transcript: str | None,
                              voicemail_text: str | None) -> CallOutcome | None:
    """The outcome the provider's data settles on its own, or None when the
    transcript needs analysing."""
    reason = (reason or "").lower()
    if any(r in reason for r in _VOICEMAIL_REASONS):
        return CallOutcome.VOICEMAIL_DROPPED if voicemail_text else CallOutcome.VOICEMAIL
    if any(r in reason for r in _NO_ANSWER_REASONS):
        return CallOutcome.NO_ANSWER
    if "error" in reason or "failed" in reason:
        return CallOutcome.FAILED
    if not (transcript or "").strip():
        return CallOutcome.NO_ANSWER
    return None


def record_end_of_call(session: Session, call: Call, *, ended_reason: str | None,
                       transcript: str | None, recording_url: str | None,
                       duration_seconds: int | None, started_at: datetime | None = None,
                       ended_at: datetime | None = None) -> bool:
    """Store the provider's result. Returns True when the transcript still
    needs analysing. Commits."""
    call.status = "ended"
    call.ended_reason = (ended_reason or "")[:80] or None
    call.transcript = (transcript or "")[:200_000] or None
    call.recording_url = (recording_url or "")[:1000] or None
    call.duration_seconds = duration_seconds
    call.started_at = started_at or call.started_at
    call.ended_at = ended_at or _now()
    settled = outcome_from_ended_reason(ended_reason, transcript, call.voicemail_text)
    lead = session.get(Lead, call.lead_id) if call.lead_id is not None else None
    if settled is not None:
        call.outcome = settled
        _stamp_lead(lead, call)
    session.commit()
    return settled is None


def _stamp_lead(lead: Lead | None, call: Call) -> None:
    if lead is None or call.outcome is None:
        return
    lead.last_call_at = call.ended_at or _now()
    lead.last_call_outcome = call.outcome.value


def analysis_prompt(call: Call) -> str:
    """The transcript, as data. F-AI-02: it is whatever the person on the
    other end of the call said, so it is delimited, escaped and labelled."""
    return (f"{prompt_safety.NOTICE}\n\n"
            f"TRANSCRIPT:\n{prompt_safety.block((call.transcript or '')[:60_000])}\n\n"
            "Return the JSON object now.")


def analyze(session: Session, call: Call) -> dict:
    """Classify a conversation with Claude and route the lead. Commits."""
    from app.services import crm_events, event_bus  # noqa: PLC0415
    from app.services import sequence_engine as engine  # noqa: PLC0415
    from app.services import voice_twin  # noqa: PLC0415

    # Consent backstop, BEFORE the model call: a spoken stop in the other
    # party's own turns is recorded whatever the model then does (outage,
    # bad JSON, a missed stop_request). The webhook's post-call check does the
    # same first; suppress_caller is idempotent, so neither it nor a Celery
    # retry records the stop twice.
    _assistant_turns, caller_turns = voice_twin.split_turns(call.transcript)
    spoken_stop = voice_twin.caller_asked_to_stop(caller_turns)
    if spoken_stop:
        voice_twin.suppress_caller(session, call)
        session.commit()

    data = anthropic_client.get_client().complete_json(
        system=ANALYSIS_SYSTEM,
        prompt=analysis_prompt(call),
        max_tokens=1500, schema=ANALYSIS_SCHEMA)
    data = data if isinstance(data, dict) else {}
    outcome = str(data.get("outcome") or "answered").lower()
    call.outcome = {"interested": CallOutcome.INTERESTED,
                    "not_interested": CallOutcome.NOT_INTERESTED}.get(outcome, CallOutcome.ANSWERED)
    analysis = {
        "outcome": call.outcome.value,
        "summary": str(data.get("summary") or "")[:2000],
        "objections": [str(x)[:300] for x in data.get("objections") or [] if isinstance(x, str)][:8],
        "interest_signals": [str(x)[:300] for x in data.get("interest_signals") or []
                             if isinstance(x, str)][:8],
        "next_step": str(data.get("next_step") or "")[:500],
        "sentiment": str(data.get("sentiment") or "neutral")[:20],
        "stop_request": bool(data.get("stop_request")) or spoken_stop,
    }
    call.analysis_json = analysis
    lead = session.get(Lead, call.lead_id) if call.lead_id is not None else None
    _stamp_lead(lead, call)
    session.commit()
    if lead is None:
        # A party who is no lead and asked not to be called is suppressed by
        # number (the same stop handling; idempotent).
        if analysis["stop_request"]:
            voice_twin.suppress_caller(session, call)
            session.commit()
        return analysis

    if analysis["stop_request"]:
        # The lead's unsubscribe, unless this stop is already recorded.
        voice_twin.suppress_caller(session, call)
        return analysis

    if call.outcome in (CallOutcome.INTERESTED, CallOutcome.NOT_INTERESTED):
        engine.stop_live_enrollments(session, lead.id, reason=f"call_{call.outcome.value}")
        lead.status = LeadStatus.REPLIED
        session.add(Outcome(lead_id=lead.id, strategy_id=lead.strategy_id,
                            event=OutcomeEvent.REPLIED, channel="phone",
                            meta_json={"call_id": str(call.id),
                                       "classification": call.outcome.value}))
        crm_events.record_activity(session, lead.id, CrmActivityKind.REPLY_RECEIVED,
                                   to_value=f"call: {call.outcome.value}",
                                   meta={"call_id": str(call.id)})
        session.commit()
        if call.outcome is CallOutcome.INTERESTED and call.user_id:
            event_bus.emit(
                session, call.user_id, "reply_interested",
                title=f"Interested on the phone: {lead.full_name or lead.company or 'a lead'}",
                body=analysis["summary"][:300] or "The AI call ended with interest.",
                deep_link=f"/leads/detail?id={lead.id}&tab=calls",
                webhook_payload={"lead_id": str(lead.id), "call_id": str(call.id),
                                 "channel": "phone", "classification": "interested"},
            )
    if call.user_id:
        # The webhook payload is the documented shape (webhook_delivery.SAMPLES
        # ["call_completed"]). Without it emit() skipped webhooks entirely, so
        # a target subscribed to call_completed never received anything.
        event_bus.emit(session, call.user_id, "call_completed",
                       title=f"Call ended: {lead.full_name or 'lead'} — {call.outcome.value.replace('_', ' ')}",
                       body=analysis["summary"][:300], deep_link=f"/leads/detail?id={lead.id}&tab=calls",
                       push=False,
                       webhook_payload={"call_id": str(call.id), "lead_id": str(lead.id),
                                        "outcome": call.outcome.value,
                                        "duration_seconds": call.duration_seconds})
    return analysis


def prepare_call(session: Session, strategy: Strategy, lead: Lead, brief: str, *,
                 owner_id, message: Message | None = None) -> tuple[Call, dict]:
    """Write the script + voicemail and the Call row (status `queued`).

    Shared by the sequence send path and "Call now" so both place exactly the
    same kind of call. Returns (call, OutboundMessage metadata). Does not
    dial and does not commit.
    """
    from app.integrations import voice_providers  # noqa: PLC0415
    from app.services import credentials  # noqa: PLC0415

    from app.core.exceptions import ComplianceError  # noqa: PLC0415
    from app.services import voice_twin  # noqa: PLC0415

    # Voice twin guard 2: a brief that tells the assistant to hide that it is
    # an AI never becomes a call (refused on save too; this catches old rows).
    names = voice_twin.owner_names(session, owner_id)
    if voice_twin.concealment_problems(brief, names):
        raise ComplianceError("the call brief asks the AI to hide that it is an AI",
                              compliance_code="AI_CONCEALMENT")
    vapi, eleven = voice_providers.get_clients(session, owner_id)
    ident = voice_twin.call_identity(session, owner_id, greet=lead.full_name)
    ident.meta["owner_names"] = list(names)
    script = write_script(session, strategy, lead, brief)
    # Guard 1: the disclosure + recording notice open every call and the
    # voicemail, whatever the model wrote.
    script["first_message"] = voice_twin.enforce_opening(script["first_message"], ident.opening,
                                                         names)
    voicemail = script.get("voicemail") or None
    if voicemail:
        voicemail = voice_twin.enforce_opening(voicemail, ident.voicemail_opening, names)
        script["voicemail"] = voicemail
    call = Call(
        lead_id=lead.id, strategy_id=strategy.id, user_id=owner_id,
        message_id=message.id if message is not None else None,
        provider="vapi" if vapi is not None else "elevenlabs",
        to_number=e164(lead.phone) or (lead.phone or ""),
        status="queued", script_json=script, voicemail_text=voicemail,
        direction="outbound", voice_clone_used=ident.clone is not None,
        # A voicemail in the owner's cloned voice is not rendered to a hosted
        # file (data minimisation): the provider speaks it on the call.
        voicemail_audio_url=(None if ident.clone is not None
                             else render_voicemail_audio(eleven, voicemail or "")),
    )
    session.add(call)
    voice_twin.mark_clone_used(ident.clone)
    session.flush()
    base = settings.public_base_url.rstrip("/")
    meta = {
        "call_id": str(call.id),
        "system_prompt": system_prompt(script, lead, ident.meta),
        "first_message": script["first_message"],
        "voicemail_text": voicemail,
        "customer_name": lead.full_name,
        "server_url": f"{base}/webhooks/{call.provider}",
        "server_secret": credentials.get_secret(session, call.provider, "webhook_secret"),
        "identity": ident.meta,
        **ident.meta,
    }
    return call, meta


def mark_send_failed(session: Session, call: Call | None, error: str, owner_id,
                     now: datetime | None = None) -> None:
    if call is not None:
        call.status = "failed"
        call.outcome = CallOutcome.FAILED
        call.error = error[:2000]
    if owner_id is not None:
        release_call_slot(owner_id, now)
    session.commit()


def call_for_message(session: Session, message: Message) -> Call | None:
    return session.execute(select(Call).where(Call.message_id == message.id)
                           .order_by(Call.created_at.desc())).scalars().first()
