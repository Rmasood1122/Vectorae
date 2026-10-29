"""Feature Group 6 API — AI calls, phone consent, provider webhooks.

  GET    /strategies/{id}/calls         the campaign's Calls tab
  GET    /leads/{id}/calls              the lead's calls
  GET    /calls/{id}                    one call, with transcript + analysis
  POST   /leads/{id}/call               "Call now" (same gates as a sequence call)
  PUT    /leads/{id}/phone-consent      record consent (source + evidence)
  DELETE /leads/{id}/phone-consent      revoke it
  POST   /webhooks/vapi                 PUBLIC: status + end-of-call reports
  POST   /webhooks/vapi/inbound         PUBLIC: voice twin -- assistant-request for
                                        an inbound call, tool-calls (book, message)
  POST   /webhooks/elevenlabs           PUBLIC: post-call transcription
  POST   /calls/{id}/takeover           voice twin: transfer a live AI call to the owner

WEBHOOK AUTHENTICATION (fail closed -- no secret configured, no delivery accepted)
  vapi        `x-vapi-secret` (the serverUrlSecret we set on every call) or
              X-LeadPilot-Signature: sha256=HMAC(secret, body), against the
              `vapi.webhook_secret` credential
  elevenlabs  ElevenLabs-Signature: t=<ts>,v0=HMAC_SHA256(secret, "<ts>.<body>"),
              against `elevenlabs.webhook_secret`
"""

# NOTE: deliberately NO `from __future__ import annotations` (FastAPI).

import hashlib
import hmac
import json
import logging
import time
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.leads import _owned_lead, _owned_strategy
from app.core.plans import PlanLimitExceeded, check_send_channel, plan_limit_response
from app.core.rate_limiting import enforce_rate_limit
from app.services import local_business
from app.db.base import get_db
from app.db.models import Call, CallOutcome, CrmActivityKind, Lead, ProcessedWebhook, Strategy, User
from app.services import credentials, crm_events, phone_calls, system_settings, usage_meter
from app.workers import call_tasks

logger = logging.getLogger(__name__)
router = APIRouter(tags=["calls"])


class CallOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    # None only for an inbound call from a number that is no lead (voice twin).
    lead_id: uuid.UUID | None
    direction: str = "outbound"
    voice_clone_used: bool = False
    from_number: str | None = None
    provider: str
    to_number: str
    status: str
    outcome: CallOutcome | None
    duration_seconds: int | None
    recording_url: str | None
    voicemail_audio_url: str | None
    ended_reason: str | None
    analysis_json: dict | None
    error: str | None
    created_at: datetime
    ended_at: datetime | None
    lead_name: str | None = None


class CallDetail(CallOut):
    script_json: dict | None
    voicemail_text: str | None
    transcript: str | None


class ConsentIn(BaseModel):
    source: str = Field(min_length=3, max_length=200,
                        description="How consent was obtained, e.g. 'web form 2026-09-01'")


class CallNowIn(BaseModel):
    brief: str = Field(default="Introduce the offer and ask for a short meeting.",
                       max_length=2000)


def _with_names(db: Session, calls: list[Call]) -> list[CallOut]:
    out = []
    for call in calls:
        item = CallOut.model_validate(call)
        lead = db.get(Lead, call.lead_id) if call.lead_id is not None else None
        item.lead_name = (lead.full_name or lead.company) if lead else None
        out.append(item)
    return out


def _owned_call(db: Session, call_id: uuid.UUID, current_user: User) -> Call:
    """A call of this account (through its lead, or -- an inbound call from a
    number that is no lead -- directly through user_id). 404 otherwise."""
    from app.services import account_export  # noqa: PLC0415

    call = db.execute(select(Call).where(
        Call.id == call_id, account_export.owner_clause("calls", current_user.id))
    ).scalar_one_or_none()
    if call is None:
        raise HTTPException(status_code=404, detail="call not found")
    return call


@router.get("/strategies/{strategy_id}/calls", response_model=list[CallOut])
def strategy_calls(strategy_id: uuid.UUID, limit: int = Query(default=100, ge=1, le=500),
                   db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)) -> list[CallOut]:
    _owned_strategy(db, strategy_id, current_user)
    rows = db.execute(select(Call).where(Call.strategy_id == strategy_id)
                      .order_by(Call.created_at.desc()).limit(limit)).scalars().all()
    return _with_names(db, list(rows))


@router.get("/leads/{lead_id}/calls", response_model=list[CallDetail])
def lead_calls(lead_id: uuid.UUID, db: Session = Depends(get_db),
               current_user: User = Depends(get_current_user)) -> list:
    _owned_lead(db, lead_id, current_user)
    return list(db.execute(select(Call).where(Call.lead_id == lead_id)
                           .order_by(Call.created_at.desc())).scalars().all())


@router.get("/calls/{call_id}", response_model=CallDetail)
def get_call(call_id: uuid.UUID, db: Session = Depends(get_db),
             current_user: User = Depends(get_current_user)) -> Call:
    return _owned_call(db, call_id, current_user)


@router.put("/leads/{lead_id}/phone-consent")
def record_consent(lead_id: uuid.UUID, body: ConsentIn, db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)) -> dict:
    lead = _owned_lead(db, lead_id, current_user)
    local_business.refuse_channel(lead, "phone")
    lead.phone_consent_at = datetime.now(timezone.utc)
    lead.phone_consent_source = body.source.strip()
    crm_events.record_activity(db, lead.id, CrmActivityKind.FIELD_CHANGED,
                               actor_user_id=current_user.id, to_value="phone consent recorded",
                               meta={"field": "phone_consent", "source": lead.phone_consent_source})
    db.commit()
    return {"phone_consent_at": lead.phone_consent_at, "phone_consent_source": lead.phone_consent_source}


@router.delete("/leads/{lead_id}/phone-consent", status_code=204)
def revoke_consent(lead_id: uuid.UUID, db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)) -> None:
    lead = _owned_lead(db, lead_id, current_user)
    lead.phone_consent_at = None
    lead.phone_consent_source = None
    crm_events.record_activity(db, lead.id, CrmActivityKind.FIELD_CHANGED,
                               actor_user_id=current_user.id, to_value="phone consent revoked",
                               meta={"field": "phone_consent"})
    db.commit()


@router.post("/leads/{lead_id}/call", response_model=CallDetail, status_code=201)
def call_now(lead_id: uuid.UUID, body: CallNowIn, db: Session = Depends(get_db),
             current_user: User = Depends(get_current_user)) -> Call:
    """Place one AI call now -- through every gate a sequence call passes
    except the send window (the user is pressing the button, on purpose)."""
    enforce_rate_limit(str(current_user.id), "ai_action", "RATE_LIMIT_AI_ACTION")
    lead = _owned_lead(db, lead_id, current_user)
    local_business.refuse_channel(lead, "phone")
    from app.services import voice_twin  # noqa: PLC0415

    # Voice twin guard 2: a brief that tells the AI to hide that it is an AI
    # is refused, with the reason.
    try:
        voice_twin.refuse_concealment(body.brief, voice_twin.owner_names(db, current_user.id))
    except voice_twin.VoiceTwinError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    from app.integrations import voice_providers  # noqa: PLC0415
    from app.integrations.outreach_base import OutboundMessage  # noqa: PLC0415
    from app.integrations.phone_channel import PhoneChannel  # noqa: PLC0415
    from app.workers.lead_tasks import is_suppressed  # noqa: PLC0415

    if not system_settings.get(db, "phone_calling_enabled"):
        raise HTTPException(status_code=409, detail="AI calling is disabled by the administrator")
    if voice_twin.calls_paused(db, current_user.id):
        raise HTTPException(status_code=409,
                            detail="AI calls are paused after a disclosure incident -- read "
                                   "the transcript and resume them in Settings > Voice")
    try:  # plan channels: only Scale and Enterprise include phone
        check_send_channel(current_user, "phone")
    except PlanLimitExceeded as exc:
        raise HTTPException(status_code=402, detail=plan_limit_response(exc))
    if not phone_calls.e164(lead.phone):
        raise HTTPException(status_code=422, detail="this lead has no dialable phone number")
    if is_suppressed(db, lead.email, lead.phone, linkedin=lead.linkedin_url):
        raise HTTPException(status_code=409, detail="this contact is on the suppression list")
    # F05 Guardrails Studio: this is the one cold send that does not pass
    # through send_message_impl, so the account's exclusion lists are checked
    # here too (no-op while guardrails_enabled is off).
    from app.services import guardrails  # noqa: PLC0415

    if guardrails.send_block(db, lead, db.get(Strategy, lead.strategy_id)) is not None:
        raise HTTPException(status_code=409, detail="this contact is on your exclusion list")
    # F10 Champion Tracker: a person confirmed to have left the company this
    # lead was sourced at is never contacted through the old lead again (same
    # SAFETY GUARD as the send chokepoint; not behind the flag).
    from app.services import champion_tracker  # noqa: PLC0415

    if champion_tracker.departure_block(db, lead, db.get(Strategy, lead.strategy_id)):
        raise HTTPException(status_code=409,
                            detail="this person has left the company -- contact them at "
                                   "their new company instead")
    if champion_tracker.derived_opt_out(db, lead, current_user.id):
        raise HTTPException(status_code=409,
                            detail="this person opted out under an earlier record")
    if not phone_calls.consent_ok(db, lead):
        raise HTTPException(status_code=409,
                            detail="no phone consent recorded for this lead -- record it first")
    vapi, eleven = voice_providers.get_clients(db, current_user.id)
    if vapi is None and not (eleven is not None and eleven.can_call):
        raise HTTPException(status_code=503, detail="no AI voice provider is configured")
    if not phone_calls.reserve_call_slot(db, current_user.id):
        raise HTTPException(status_code=429, detail="daily AI call limit reached")

    strategy = db.get(Strategy, lead.strategy_id)
    try:
        # P0-5b: the call script is a model call, counted to the account.
        with usage_meter.user_scope(db, current_user.id, "outreach"):
            call, meta = phone_calls.prepare_call(db, strategy, lead, body.brief,
                                                  owner_id=current_user.id)
        db.commit()
        result = PhoneChannel(session=db, vapi=vapi, eleven=eleven).send(OutboundMessage(
            message_id=str(uuid.uuid4()), lead_id=str(lead.id), to_address=call.to_number,
            body=call.script_json["first_message"], metadata=meta))
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        phone_calls.release_call_slot(current_user.id)
        from app.core.errors import public_detail  # noqa: PLC0415

        why = public_detail(exc, "the voice provider returned an error",
                            event="api.call_now_failed", lead_id=str(lead.id))
        raise HTTPException(status_code=502, detail=f"the call could not be placed: {why}") from exc
    if not result.ok:
        phone_calls.mark_send_failed(db, call, result.error or "call failed", current_user.id)
        # The provider's own error text stays on the call record and in the
        # log; the response says what happened, not the provider's body.
        logger.warning("call_now: provider refused call %s: %s", call.id, result.error)
        raise HTTPException(status_code=502, detail="the call could not be placed: the voice "
                                                    "provider refused it (see the call record)")
    call.provider_call_id = result.provider_message_id
    call.provider = (result.raw or {}).get("provider") or call.provider
    call.status = "ringing"
    call.control_url = (result.raw or {}).get("control_url") or None
    db.commit()
    return call


@router.post("/calls/{call_id}/takeover")
def take_over(call_id: uuid.UUID, request: Request, db: Session = Depends(get_db),
              current_user: User = Depends(get_current_user)) -> dict:
    """Voice twin: put the live AI call through to the owner's own phone
    (the provider's live call control). Owner or workspace manager; audited."""
    from app.services import rbac, voice_twin  # noqa: PLC0415

    if not rbac.at_least(getattr(request.state, "workspace_role", "owner"), "manager"):
        raise HTTPException(status_code=403, detail="Only the owner or a manager can take over "
                                                    "a call.")
    call = _owned_call(db, call_id, current_user)
    actor = getattr(request.state, "actor", current_user)
    try:
        return voice_twin.take_over(db, call, actor_id=actor.id, owner_id=current_user.id)
    except voice_twin.VoiceTwinError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


# ---------------------------------------------------------------------------
# Provider webhooks
# ---------------------------------------------------------------------------


def _hmac_ok(secret: str, raw: bytes, header: str | None) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header.removeprefix("sha256="), expected)


def verify_vapi(raw: bytes, headers, secret: str | None) -> bool:
    if not secret:
        return False
    static = headers.get("x-vapi-secret")
    if static:
        return hmac.compare_digest(static, secret)
    return _hmac_ok(secret, raw, headers.get("X-LeadPilot-Signature"))


ELEVENLABS_SIGNATURE_TOLERANCE_SECONDS = 30 * 60


def verify_elevenlabs(raw: bytes, header: str | None, secret: str | None) -> bool:
    if not secret or not header:
        return False
    try:
        parts = dict(p.split("=", 1) for p in header.split(","))
        ts, given = parts["t"], parts["v0"]
        age = abs(time.time() - int(ts))
    except (ValueError, KeyError):
        return False
    # ElevenLabs post-call webhooks: validate "both the hash and timestamp"
    # and reject anything older than 30 minutes, so a captured delivery
    # cannot be replayed later.
    if age > ELEVENLABS_SIGNATURE_TOLERANCE_SECONDS:
        return False
    expected = hmac.new(secret.encode(), ts.encode() + b"." + raw,
                        hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, given)


def _dedupe(db: Session, provider: str, event_id: str) -> bool:
    db.add(ProcessedWebhook(provider=provider, event_id=event_id[:200]))
    try:
        db.commit()
        return True
    except IntegrityError:
        db.rollback()
        return False


def _parse_ts(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


_VAPI_STATUS = {"queued": "queued", "ringing": "ringing", "in-progress": "in_progress",
                "forwarding": "in_progress", "ended": "ended"}


@router.post("/webhooks/vapi")
async def vapi_webhook(request: Request, db: Session = Depends(get_db)) -> dict:
    """Envelope `message.type` + `message.call` verified against docs.vapi.ai
    (server events) on 2026-10-12; see the voice-twin spec for the rest."""
    raw = await request.body()
    if not verify_vapi(raw, request.headers, credentials.get_secret(db, "vapi", "webhook_secret")):
        raise HTTPException(status_code=401, detail="invalid webhook signature")
    payload = json.loads(raw or b"{}")
    return _vapi_event(db, payload.get("message") or payload)


def _vapi_event(db: Session, msg: dict) -> dict:
    from app.integrations import voice_providers  # noqa: PLC0415
    from app.services import voice_twin  # noqa: PLC0415

    kind = msg.get("type")
    call_obj = msg.get("call") or {}
    call = db.execute(select(Call).where(Call.provider == "vapi",
                                         Call.provider_call_id == call_obj.get("id"))
                      ).scalar_one_or_none() if call_obj.get("id") else None
    if call is None:
        return {"ok": True, "matched": False}
    monitor = call_obj.get("monitor") if isinstance(call_obj.get("monitor"), dict) else {}
    if not call.control_url and voice_providers.control_url_ok(monitor.get("controlUrl")):
        call.control_url = monitor["controlUrl"][:500]

    if kind == "status-update":
        call.status = _VAPI_STATUS.get(str(msg.get("status")), call.status)
        db.commit()
        if call.status == "in_progress" and call.user_id and _dedupe(db, "vapi",
                                                                     f"live:{call_obj['id']}"):
            # Voice twin: the owner hears that an AI call is live, with
            # "Take over" (push + the in-app banner, which polls).
            voice_twin.notify_live(db, call)
        return {"ok": True, "status": call.status}

    if kind == "tool-calls" and call.direction == "inbound":
        return {"results": _tool_results(db, call, msg)}

    if kind == "end-of-call-report":
        if not _dedupe(db, "vapi", f"eoc:{call_obj['id']}"):
            return {"ok": True, "duplicate": True}
        artifact = msg.get("artifact") or {}
        started, ended = _parse_ts(msg.get("startedAt")), _parse_ts(msg.get("endedAt"))
        duration = msg.get("durationSeconds")
        if duration is None and started and ended:
            duration = int((ended - started).total_seconds())
        needs_analysis = phone_calls.record_end_of_call(
            db, call, ended_reason=msg.get("endedReason"),
            transcript=msg.get("transcript") or artifact.get("transcript"),
            recording_url=msg.get("recordingUrl") or artifact.get("recordingUrl"),
            duration_seconds=int(duration) if duration is not None else None,
            started_at=started, ended_at=ended)
        # Voice twin guard 3 (every AI call): did the assistant claim to be
        # human? Deterministic, before any analysis.
        messages = artifact.get("messages") if isinstance(artifact.get("messages"), list) else None
        check = _safe_post_call_check(db, call, messages)
        if needs_analysis:
            call_tasks.enqueue_analysis(call.id)
        return {"ok": True, "outcome": call.outcome.value if call.outcome else None,
                "analysis_queued": needs_analysis, "disclosure_incident": bool(check["claims"])}

    return {"ok": True, "action": "ignored", "type": kind}


def _safe_post_call_check(db: Session, call: Call, messages: list | None) -> dict:
    """L8: the end-of-call row and its dedupe marker are already committed; a
    failure in the check must not 500 (the provider would not retry -- it is a
    duplicate now) and must not stop the analysis being queued. It is logged
    loudly and reported in the response."""
    from app.services import voice_twin  # noqa: PLC0415

    try:
        return voice_twin.post_call_check(db, call, messages)
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("voice twin: post-call check failed for call %s", call.id)
        return {"claims": [], "check_failed": True}


def _tool_results(db: Session, call: Call, msg: dict) -> list[dict]:
    """Vapi `tool-calls` -> `results` [{name, toolCallId, result}] (verified
    shape). Only our own inbound tools are run, only for this call's account."""
    from app.services import voice_twin  # noqa: PLC0415

    results = []
    for item in (msg.get("toolCallList") or [])[:5]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or (item.get("function") or {}).get("name") or "")
        args = item.get("parameters") or item.get("arguments") or \
            (item.get("function") or {}).get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {}
        try:
            result = voice_twin.run_tool(db, call, name, args)
        except Exception:  # noqa: BLE001 -- the caller is on the line; answer something
            db.rollback()
            logger.exception("voice twin tool %s failed on call %s", name, call.id)
            result = "That did not work; offer to take a message instead."
        results.append({"name": name, "toolCallId": item.get("id"), "result": result})
    return results


@router.post("/webhooks/vapi/inbound")
async def vapi_inbound_webhook(request: Request, db: Session = Depends(get_db)) -> dict:
    """Voice twin: the phone number's server URL. Signed like /webhooks/vapi
    (fail closed). `assistant-request` must be answered within 7.5 s (docs):
    no model call happens here, only database reads.

    The account is found ONLY by the provider's phone-number id
    (voice_twin.inbound_account); an unknown number is refused."""
    from app.config import settings  # noqa: PLC0415
    from app.services import voice_twin  # noqa: PLC0415

    raw = await request.body()
    secret = credentials.get_secret(db, "vapi", "webhook_secret")
    if not verify_vapi(raw, request.headers, secret):
        raise HTTPException(status_code=401, detail="invalid webhook signature")
    payload = json.loads(raw or b"{}")
    msg = payload.get("message") or payload
    if msg.get("type") != "assistant-request":
        return _vapi_event(db, msg)

    call_obj = msg.get("call") or {}
    if call_obj.get("type") not in (None, "inboundPhoneCall"):
        raise HTTPException(status_code=422, detail="not an inbound phone call")
    row = voice_twin.inbound_account(db, call_obj.get("phoneNumberId"))
    if row is None:
        logger.warning("voice twin: assistant-request for an unknown number %s",
                       call_obj.get("phoneNumberId"))
        raise HTTPException(status_code=404, detail="unknown phone number")
    refusal = voice_twin.answer_refusal(row, db)
    if refusal is not None:
        logger.info("voice twin: not answering for %s (%s)", row.user_id, refusal)
        if row.transfer_number:
            return {"destination": {"type": "number", "number": row.transfer_number}}
        return {"error": "This is an automated message. Nobody can take your call right "
                         "now. Please call back later."}
    if not call_obj.get("id"):
        raise HTTPException(status_code=422, detail="call id missing")
    customer = call_obj.get("customer") if isinstance(call_obj.get("customer"), dict) else {}
    phone = msg.get("phoneNumber") if isinstance(msg.get("phoneNumber"), dict) else {}
    base = settings.public_base_url.rstrip("/")
    call, assistant = voice_twin.build_inbound(
        db, row, provider_call_id=str(call_obj["id"])[:120],
        from_number=customer.get("number"), to_number=phone.get("number"),
        server_url=f"{base}/webhooks/vapi", tool_url=f"{base}/webhooks/vapi/inbound",
        secret=secret, model=settings.anthropic_model)
    return {"assistant": assistant}


@router.post("/webhooks/elevenlabs")
async def elevenlabs_webhook(request: Request, db: Session = Depends(get_db)) -> dict:
    """# TODO: verify against current ElevenLabs docs (post_call_transcription)"""
    raw = await request.body()
    if not verify_elevenlabs(raw, request.headers.get("ElevenLabs-Signature"),
                             credentials.get_secret(db, "elevenlabs", "webhook_secret")):
        raise HTTPException(status_code=401, detail="invalid webhook signature")
    payload = json.loads(raw or b"{}")
    data = payload.get("data") or {}
    conversation_id = data.get("conversation_id")
    call = db.execute(select(Call).where(Call.provider == "elevenlabs",
                                         Call.provider_call_id == conversation_id)
                      ).scalar_one_or_none() if conversation_id else None
    if call is None:
        return {"ok": True, "matched": False}
    if not _dedupe(db, "elevenlabs", f"eoc:{conversation_id}"):
        return {"ok": True, "duplicate": True}
    turns = data.get("transcript") or []
    transcript = "\n".join(f"{t.get('role', '?')}: {t.get('message', '')}"
                           for t in turns if isinstance(t, dict))
    meta = data.get("metadata") or {}
    needs_analysis = phone_calls.record_end_of_call(
        db, call, ended_reason=str(meta.get("termination_reason") or data.get("status") or ""),
        transcript=transcript, recording_url=None,
        duration_seconds=meta.get("call_duration_secs"))
    # Voice twin guard 3, on the STRUCTURED turns (M6): a multi-line agent
    # turn is one turn, so its second line is still the agent speaking.
    messages = [{"role": str(t.get("role") or ""), "message": str(t.get("message") or "")}
                for t in turns if isinstance(t, dict)]
    check = _safe_post_call_check(db, call, messages)
    if needs_analysis:
        call_tasks.enqueue_analysis(call.id)
    return {"ok": True, "analysis_queued": needs_analysis,
            "disclosure_incident": bool(check["claims"])}
