"""Voice twin API -- "Your voice on calls" (docs/features/voice-twin.md).

PERSONAL (always the signed-in person, whatever workspace is selected --
app/services/rbac.py PERSONAL_PREFIXES "/me"): a workspace member can never
reach another person's clone, and nobody but the voice's owner can consent,
upload, verify, preview, use or revoke it.
  GET    /me/voice-clone                   consent text, clone status, switches
  POST   /me/voice-clone/consent           written consent (BEFORE any upload)
  POST   /me/voice-clone/samples           one recording, raw audio body,
                                           streamed to ElevenLabs, never stored
  POST   /me/voice-clone/submit            done uploading -> verification
  GET    /me/voice-clone/verification      the text to read (or instructions)
  POST   /me/voice-clone/verification      the person's reading, raw audio body
  POST   /me/voice-clone/refresh           read the provider's status
  GET    /me/voice-clone/preview           the opening line in the clone (audio)
  PUT    /me/voice-clone/use               "Use my voice for AI calls"
  DELETE /me/voice-clone                   revoke: unusable now, deleted at ElevenLabs

ACCOUNT (the workspace's owner account; owner or manager to change):
  GET    /voice-twin/settings
  PUT    /voice-twin/settings              answer inbound, transfer number, notes
  POST   /voice-twin/resume                resume AI calls after an incident
  GET    /voice-twin/live-calls            the in-app "AI call live" banner
  GET    /voice-twin/inbound-calls         calls the AI answered

ADMIN:
  PUT    /admin/voice-twin/numbers/{user_id}  give one account its OWN number
"""

# NOTE: deliberately NO `from __future__ import annotations` (FastAPI).

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_admin
from app.core.rate_limiting import enforce_rate_limit
from app.db.base import get_db
from app.db.models import Lead, User
from app.services import security_audit, voice_twin

logger = logging.getLogger(__name__)
router = APIRouter(tags=["voice-twin"])


def _err(exc: voice_twin.VoiceTwinError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


def _actor(request: Request, user: User) -> User:
    return getattr(request.state, "actor", None) or user


def _require_manager(request: Request) -> None:
    from app.services import rbac  # noqa: PLC0415

    if not rbac.at_least(getattr(request.state, "workspace_role", "owner"), "manager"):
        raise HTTPException(status_code=403,
                            detail="Only the account owner or a manager can change this.")


async def _read_audio(request: Request, cap: int) -> bytes:
    """The raw request body, in memory only, refused past `cap` bytes."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > cap:
        raise HTTPException(status_code=413, detail="That recording is too large.")
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > cap:
            raise HTTPException(status_code=413, detail="That recording is too large.")
        chunks.append(chunk)
    return b"".join(chunks)


# --------------------------------------------------------------------------
# Personal: the clone
# --------------------------------------------------------------------------


class ConsentIn(BaseModel):
    signature: str = Field(min_length=2, max_length=200,
                           description="Your full name, typed as your signature")
    own_voice: bool = Field(description="I confirm this is my own voice")
    consent_version: str = Field(max_length=10)
    # L4: the exact text the page showed, with the typed signature in it. The
    # server rebuilds it and refuses a mismatch, so what is stored is exactly
    # what was read.
    consent_text: str = Field(max_length=5000)


class UseIn(BaseModel):
    enabled: bool


def _state(db: Session, user: User) -> dict:
    clone = voice_twin.live_clone(db, user.id) or voice_twin.latest_clone(db, user.id)
    row = voice_twin.settings_for(db, user.id)
    name = voice_twin.owner_first_name(db, user.id, clone)
    return {
        "feature_enabled": voice_twin.enabled(db),
        "consent": {"version": voice_twin.CONSENT_VERSION,
                    # {name} is replaced, on the page, by the signature typed.
                    "template": voice_twin.CONSENT_TEXT,
                    "text": voice_twin.consent_text(user.full_name or "<your name>")},
        "limits": {"max_samples": voice_twin.MAX_SAMPLES,
                   "max_sample_bytes": voice_twin.MAX_SAMPLE_BYTES,
                   "max_total_bytes": voice_twin.MAX_TOTAL_BYTES,
                   "types": sorted(voice_twin.ALLOWED_AUDIO)},
        "clone": voice_twin.clone_out(clone),
        "settings": voice_twin.settings_out(db, row),
        "opening_line": voice_twin.opening_line(name, clone=True),
        "verify_on_elevenlabs": voice_twin.VERIFY_ON_ELEVENLABS,
    }


@router.get("/me/voice-clone")
def get_voice_clone(db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_user)) -> dict:
    return _state(db, current_user)


@router.post("/me/voice-clone/consent", status_code=201)
def consent(body: ConsentIn, db: Session = Depends(get_db),
            current_user: User = Depends(get_current_user)) -> dict:
    try:
        voice_twin.record_consent(db, current_user, signature=body.signature,
                                  own_voice=body.own_voice, version=body.consent_version,
                                  text_shown=body.consent_text)
    except voice_twin.VoiceTwinError as exc:
        raise _err(exc) from exc
    return _state(db, current_user)


@router.post("/me/voice-clone/samples", status_code=201)
async def upload_sample(request: Request, db: Session = Depends(get_db),
                        current_user: User = Depends(get_current_user)) -> dict:
    """Body: the raw audio file. Headers: Content-Type (audio/*) and an
    optional X-Filename. Read into memory (capped), sent to ElevenLabs,
    dropped -- never written to our disk or database."""
    from urllib.parse import unquote  # noqa: PLC0415

    enforce_rate_limit(str(current_user.id), "ai_action", "RATE_LIMIT_AI_ACTION")
    try:     # L3: flag, consent and status BEFORE a 25 MB body is read
        voice_twin.assert_can_upload(db, current_user)
    except voice_twin.VoiceTwinError as exc:
        raise _err(exc) from exc
    content = await _read_audio(request, voice_twin.MAX_SAMPLE_BYTES)
    try:
        # L2: the page sends the name percent-encoded (headers are Latin-1).
        voice_twin.upload_sample(db, current_user,
                                 filename=unquote(request.headers.get("x-filename") or ""),
                                 content_type=request.headers.get("content-type") or "",
                                 content=content)
    except voice_twin.VoiceTwinError as exc:
        raise _err(exc) from exc
    finally:
        del content
    return _state(db, current_user)


@router.post("/me/voice-clone/submit")
def submit(db: Session = Depends(get_db),
           current_user: User = Depends(get_current_user)) -> dict:
    try:
        voice_twin.submit(db, current_user)
    except voice_twin.VoiceTwinError as exc:
        raise _err(exc) from exc
    return _state(db, current_user)


@router.get("/me/voice-clone/verification")
def verification(db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)) -> dict:
    try:
        return voice_twin.verification_challenge(db, current_user)
    except voice_twin.VoiceTwinError as exc:
        raise _err(exc) from exc


@router.post("/me/voice-clone/verification")
async def verify(request: Request, db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)) -> dict:
    enforce_rate_limit(str(current_user.id), "ai_action", "RATE_LIMIT_AI_ACTION")
    try:     # L3: status before the body is read
        voice_twin.assert_can_verify(db, current_user)
    except voice_twin.VoiceTwinError as exc:
        raise _err(exc) from exc
    content = await _read_audio(request, voice_twin.MAX_SAMPLE_BYTES)
    try:
        voice_twin.verify(db, current_user,
                          content_type=request.headers.get("content-type") or "",
                          content=content)
    except voice_twin.VoiceTwinError as exc:
        raise _err(exc) from exc
    finally:
        del content
    return _state(db, current_user)


@router.post("/me/voice-clone/refresh")
def refresh(db: Session = Depends(get_db),
            current_user: User = Depends(get_current_user)) -> dict:
    try:
        voice_twin.refresh(db, current_user)
    except voice_twin.VoiceTwinError as exc:
        raise _err(exc) from exc
    return _state(db, current_user)


@router.get("/me/voice-clone/preview")
def preview(db: Session = Depends(get_db),
            current_user: User = Depends(get_current_user)) -> Response:
    enforce_rate_limit(str(current_user.id), "ai_action", "RATE_LIMIT_AI_ACTION")
    try:
        audio = voice_twin.preview_audio(db, current_user)
    except voice_twin.VoiceTwinError as exc:
        raise _err(exc) from exc
    return Response(content=audio, media_type="audio/mpeg",
                    headers={"Cache-Control": "no-store"})


@router.put("/me/voice-clone/use")
def use_my_voice(body: UseIn, request: Request, db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)) -> dict:
    """"Use my voice for AI calls". Only the voice's owner (this route is
    personal: the signed-in person, never a workspace's owner)."""
    if body.enabled:
        if not voice_twin.enabled(db):
            raise HTTPException(status_code=409,
                                detail="Voice cloning is not enabled on this Vectorae.")
        if voice_twin.ready_clone(db, current_user.id) is None:
            raise HTTPException(status_code=409, detail="Your voice clone is not ready yet.")
    row = voice_twin.settings_for(db, current_user.id, create=True)
    row.use_clone_for_calls = body.enabled
    security_audit.record(db, action=security_audit.VOICE_TWIN_SETTINGS_CHANGED,
                          request=request, user_id=current_user.id,
                          target_type="voice_twin_settings", target_id=row.id,
                          details={"use_clone_for_calls": body.enabled})
    db.commit()
    return _state(db, current_user)


@router.delete("/me/voice-clone")
def revoke(db: Session = Depends(get_db),
           current_user: User = Depends(get_current_user)) -> dict:
    """Revoke consent. Always allowed (even with the feature switched off):
    unusable immediately; deleted at ElevenLabs, retried until it is."""
    clone = voice_twin.revoke(db, current_user.id, actor_id=current_user.id)
    if clone is None:
        raise HTTPException(status_code=404, detail="You have no voice clone.")
    return _state(db, current_user)


# --------------------------------------------------------------------------
# Account: inbound answering, transfer number, pause, live calls
# --------------------------------------------------------------------------


class SettingsIn(BaseModel):
    answer_inbound: bool | None = None
    transfer_number: str | None = Field(default=None, max_length=40)
    assistant_notes: str | None = Field(default=None, max_length=2000)


class ResumeIn(BaseModel):
    acknowledge: bool = Field(description="I read the transcript of the call that was flagged")


def _is_owner(request: Request | None) -> bool:
    role = getattr(request.state, "workspace_role", "owner") if request is not None else "owner"
    return role == "owner"


@router.get("/voice-twin/settings")
def get_settings(request: Request, db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)) -> dict:
    out = voice_twin.settings_out(db, voice_twin.settings_for(db, current_user.id),
                                  is_owner=_is_owner(request))
    out["feature_enabled"] = voice_twin.enabled(db)
    return out


@router.put("/voice-twin/settings")
def put_settings(body: SettingsIn, request: Request, db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)) -> dict:
    from app.services import phone_calls  # noqa: PLC0415

    _require_manager(request)
    fields = body.model_fields_set
    if "transfer_number" in fields and not _is_owner(request):
        # L9: calls are put through to this number (takeover, "ask for the
        # owner"); only the owner may say where their calls go.
        raise HTTPException(status_code=403,
                            detail="Only the account owner can change the transfer number.")
    if "assistant_notes" in fields:
        try:     # guard 2: no instruction to hide that it is an AI
            voice_twin.refuse_concealment(body.assistant_notes,
                                          voice_twin.owner_names(db, current_user.id))
        except voice_twin.VoiceTwinError as exc:
            raise _err(exc) from exc
    row = voice_twin.settings_for(db, current_user.id, create=True)
    changed: dict = {}
    if "transfer_number" in fields:
        number = None
        if body.transfer_number and body.transfer_number.strip():
            number = phone_calls.e164(body.transfer_number)
            if not number:
                raise HTTPException(status_code=422,
                                    detail="Enter your phone number with its country code, "
                                           "e.g. +1 555 010 2000.")
        row.transfer_number = number
        changed["transfer_number"] = voice_twin.mask_number(number)
    if "assistant_notes" in fields:
        row.assistant_notes = (body.assistant_notes or "").strip() or None
        changed["assistant_notes"] = bool(row.assistant_notes)
    if "answer_inbound" in fields and body.answer_inbound is not None:
        if body.answer_inbound:
            if not voice_twin.enabled(db):
                raise HTTPException(status_code=409, detail="AI answering is not enabled on "
                                                            "this Vectorae.")
            number_id = voice_twin.own_number(db, current_user.id)   # M4: no shared fallback
            if not number_id:
                raise HTTPException(
                    status_code=409,
                    detail="This account has no phone number of its own to answer on. Ask "
                           "your Vectorae administrator to assign one to your account "
                           "(the deployment's shared number cannot answer for one account).")
            from sqlalchemy import select  # noqa: PLC0415

            from app.db.models import VoiceTwinSettings  # noqa: PLC0415

            taken = db.execute(select(VoiceTwinSettings.id).where(
                VoiceTwinSettings.inbound_phone_number_id == number_id,
                VoiceTwinSettings.user_id != current_user.id)).first()
            if taken is not None:
                raise HTTPException(status_code=409, detail="That phone number already "
                                                            "answers for another account.")
            row.inbound_phone_number_id = number_id
            row.answer_inbound = True
        else:
            row.answer_inbound = False
            row.inbound_phone_number_id = None
        changed["answer_inbound"] = row.answer_inbound
    if changed:
        security_audit.record(db, action=security_audit.VOICE_TWIN_SETTINGS_CHANGED,
                              request=request, user_id=_actor(request, current_user).id,
                              owner_user_id=current_user.id,
                              target_type="voice_twin_settings", target_id=row.id,
                              details=changed)
    db.commit()
    return get_settings(request, db, current_user)


@router.post("/voice-twin/resume")
def resume(body: ResumeIn, request: Request, db: Session = Depends(get_db),
           current_user: User = Depends(get_current_user)) -> dict:
    """A person re-enables AI calls after a disclosure incident. Audited."""
    _require_manager(request)
    if not body.acknowledge:
        raise HTTPException(status_code=422, detail="Confirm you read the flagged call first.")
    row = voice_twin.settings_for(db, current_user.id)
    if row is None or row.paused_at is None:
        raise HTTPException(status_code=409, detail="AI calls are not paused.")
    security_audit.record(db, action=security_audit.VOICE_TWIN_RESUMED, request=request,
                          user_id=_actor(request, current_user).id,
                          owner_user_id=current_user.id, target_type="voice_twin_settings",
                          target_id=row.id, details={"paused_reason": row.paused_reason})
    row.paused_at = None
    row.paused_reason = None
    db.commit()
    return get_settings(request, db, current_user)


@router.get("/voice-twin/inbound-calls")
def inbound_calls(db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)) -> list:
    """Inbound calls the AI answered for this account (newest first)."""
    from sqlalchemy import select  # noqa: PLC0415

    from app.api.calls import _with_names  # noqa: PLC0415
    from app.db.models import Call  # noqa: PLC0415
    from app.services import account_export  # noqa: PLC0415

    rows = db.execute(select(Call).where(account_export.owner_clause("calls", current_user.id),
                                         Call.direction == "inbound")
                      .order_by(Call.created_at.desc()).limit(50)).scalars().all()
    return [item.model_dump(mode="json") for item in _with_names(db, list(rows))]


@router.get("/voice-twin/live-calls")
def live_calls(request: Request, db: Session = Depends(get_db),
               current_user: User = Depends(get_current_user)) -> dict:
    """{calling_enabled, calls}. With AI calling off there is nothing to poll
    for, and the banner stops polling (L7)."""
    from app.integrations import voice_providers  # noqa: PLC0415
    from app.services import rbac, system_settings  # noqa: PLC0415

    if not system_settings.get(db, "phone_calling_enabled"):
        return {"calling_enabled": False, "calls": []}
    row = voice_twin.settings_for(db, current_user.id)
    can_act = rbac.at_least(getattr(request.state, "workspace_role", "owner"), "manager")
    out = []
    for call in voice_twin.live_calls(db, current_user.id):
        lead = db.get(Lead, call.lead_id) if call.lead_id is not None else None
        out.append({
            "id": str(call.id), "direction": call.direction, "status": call.status,
            "voice_clone_used": call.voice_clone_used,
            "lead_id": str(call.lead_id) if call.lead_id else None,
            "who": (lead.full_name or lead.company) if lead else
                   (voice_twin.mask_number(call.from_number) or "Unknown caller"),
            "started_at": call.created_at.isoformat() if call.created_at else None,
            "can_take_over": bool(can_act and row is not None and row.transfer_number
                                  and call.provider == "vapi"
                                  and voice_providers.control_url_ok(call.control_url)),
        })
    return {"calling_enabled": True, "calls": out}


# --------------------------------------------------------------------------
# Admin: give an account its OWN number to answer on (review fix M4)
# --------------------------------------------------------------------------


class NumberIn(BaseModel):
    phone_number_id: str | None = Field(default=None, max_length=120,
                                        description="The Vapi phone-number id; empty removes it")


@router.put("/admin/voice-twin/numbers/{user_id}")
def assign_number(user_id: str, body: NumberIn, request: Request,
                  db: Session = Depends(get_db),
                  admin: User = Depends(require_admin)) -> dict:
    """A deployment administrator assigns one Vapi number to one account
    (user-scoped credential). Unassigning also switches that account's AI
    answering off. Audited."""
    import uuid as _uuid  # noqa: PLC0415

    from app.integrations.token_store import TokenStore  # noqa: PLC0415

    try:
        uid = _uuid.UUID(user_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="user not found") from exc
    if db.get(User, uid) is None:
        raise HTTPException(status_code=404, detail="user not found")
    value = (body.phone_number_id or "").strip()
    if value:
        TokenStore.set(db, uid, "vapi", "phone_number_id", value)
    else:
        TokenStore.delete(db, uid, "vapi", "phone_number_id")
    row = voice_twin.settings_for(db, uid)
    if row is not None and row.inbound_phone_number_id and row.inbound_phone_number_id != value:
        row.answer_inbound = False
        row.inbound_phone_number_id = None
    security_audit.record(db, action=security_audit.VOICE_TWIN_NUMBER_ASSIGNED, request=request,
                          user_id=_actor(request, admin).id, owner_user_id=uid,
                          target_type="user", target_id=uid,
                          details={"assigned": bool(value)})
    db.commit()
    return {"user_id": str(uid), "number_assigned": bool(value)}
