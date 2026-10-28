"""F04 Reply Copilot -- the draft queue (docs/features/f04-reply-copilot.md).

GET   /reply-copilot/drafts                 the account's drafts (pending: oldest first)
GET   /reply-copilot/drafts/{id}            one draft, with its delivery state
POST  /reply-copilot/drafts/{id}/approve    EMAIL: approve (optionally edited) -> the
                                            send chokepoint; nothing is sent here
POST  /reply-copilot/drafts/{id}/copy       LINKEDIN: record the copy; nothing is sent
POST  /reply-copilot/drafts/{id}/discard    discard (the reply stays "needs reply")
GET   /reply-copilot/settings               the account's auto-send opt-in and
PUT   /reply-copilot/settings               stop-attribution setting
                                            (answer_earlier_replies)
GET   /reply-copilot/metrics?days=30        the success metric, measured

Behind `reply_copilot_enabled` (System Settings, default off): every route is
404 while it is off. Tenancy: every query filters reply_drafts.user_id =
current_user.id; another account's draft is 404, never 403.

Approval-time claim verification may make one claim-extraction model call; it
runs inside `usage_meter.scope(..., purpose="reply_copilot")` opened by the
service (app/api/metering.py from PR #61 is not on this branch).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.rate_limiting import enforce_rate_limit
from app.db.base import get_db
from app.db.models import User
from app.services import reply_copilot

router = APIRouter(tags=["reply-copilot"])

NOT_ENABLED = "Reply Copilot is not enabled on this deployment."


def require_enabled(db: Session = Depends(get_db)) -> None:
    """404 while the flag is off. A dependency, so it answers before the
    handler body (and its rate limit) runs."""
    if not reply_copilot.enabled(db):
        raise HTTPException(status_code=404, detail=NOT_ENABLED)


class ApproveIn(BaseModel):
    #: "Edit" is approve-with-changes. Omitted = the draft as generated.
    subject: str | None = Field(default=None, max_length=500)
    body: str | None = Field(default=None, max_length=reply_copilot.MAX_FINAL_CHARS)


class CopyIn(BaseModel):
    body: str | None = Field(default=None, max_length=reply_copilot.MAX_FINAL_CHARS)


class SettingsIn(BaseModel):
    """Either field may be sent alone; at least one is required."""
    auto_send_meeting_time: bool | None = None
    answer_earlier_replies: bool | None = None


def _owned(db: Session, draft_id: uuid.UUID, current_user: User):
    draft = reply_copilot.owned_draft(db, current_user.id, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="draft not found")
    return draft


def _error(exc: reply_copilot.CopilotError) -> JSONResponse:
    content: dict = {"detail": exc.detail}
    if exc.problems:
        content["problems"] = exc.problems
    return JSONResponse(status_code=exc.status, content=content)


@router.get("/reply-copilot/drafts", dependencies=[Depends(require_enabled)])
def list_drafts(
    status: str = Query(default=reply_copilot.PENDING,
                        pattern="^(pending|approved|copied|discarded|superseded|all)$"),
    lead_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Pending drafts oldest first -- a queue, like the inbox's needs-reply."""
    return reply_copilot.list_drafts(db, current_user.id, status=status, lead_id=lead_id,
                                     limit=limit, offset=offset)


@router.get("/reply-copilot/drafts/{draft_id}", dependencies=[Depends(require_enabled)])
def get_draft(draft_id: uuid.UUID, db: Session = Depends(get_db),
              current_user: User = Depends(get_current_user)) -> dict:
    return reply_copilot.draft_out(db, _owned(db, draft_id, current_user))


@router.post("/reply-copilot/drafts/{draft_id}/approve",
             dependencies=[Depends(require_enabled)])
def approve_draft(draft_id: uuid.UUID, body: ApproveIn | None = None,
                  db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)):
    """Approve an email draft, optionally edited. 409: not pending, LinkedIn
    (copy-only), suppressed/excluded contact, nothing to send through. 422: a
    forbidden phrase or a claim that cannot be backed -- nothing is queued.
    On success the answer is queued for the send chokepoint; it may still be
    held there (send window, caps, review) or blocked (suppression)."""
    enforce_rate_limit(str(current_user.id), "crm_write", "RATE_LIMIT_CRM_WRITE")
    draft = _owned(db, draft_id, current_user)
    payload = body or ApproveIn()
    try:
        reply_copilot.approve(db, draft, actor=current_user,
                              subject=payload.subject, body=payload.body)
    except reply_copilot.CopilotError as exc:
        return _error(exc)
    return reply_copilot.draft_out(db, draft)


@router.post("/reply-copilot/drafts/{draft_id}/copy", dependencies=[Depends(require_enabled)])
def copy_draft(draft_id: uuid.UUID, body: CopyIn | None = None,
               db: Session = Depends(get_db),
               current_user: User = Depends(get_current_user)):
    """LinkedIn: record that the (optionally edited) draft was copied to the
    clipboard. Vectorae sends nothing. Same checks as approve."""
    enforce_rate_limit(str(current_user.id), "crm_write", "RATE_LIMIT_CRM_WRITE")
    draft = _owned(db, draft_id, current_user)
    try:
        reply_copilot.copy(db, draft, actor=current_user, body=(body or CopyIn()).body)
    except reply_copilot.CopilotError as exc:
        return _error(exc)
    return reply_copilot.draft_out(db, draft)


@router.post("/reply-copilot/drafts/{draft_id}/discard",
             dependencies=[Depends(require_enabled)])
def discard_draft(draft_id: uuid.UUID, db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)):
    enforce_rate_limit(str(current_user.id), "crm_write", "RATE_LIMIT_CRM_WRITE")
    draft = _owned(db, draft_id, current_user)
    try:
        reply_copilot.discard(db, draft, actor=current_user)
    except reply_copilot.CopilotError as exc:
        return _error(exc)
    return reply_copilot.draft_out(db, draft)


@router.get("/reply-copilot/settings", dependencies=[Depends(require_enabled)])
def get_settings(db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)) -> dict:
    return reply_copilot.settings_out(db, current_user.id)


@router.put("/reply-copilot/settings", dependencies=[Depends(require_enabled)])
def put_settings(body: SettingsIn, db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)):
    """Turning auto-send ON needs the deployment switch
    `reply_copilot_auto_send_allowed` (409 otherwise). OFF always works.
    `answer_earlier_replies` (default true) is the account's own choice:
    false = an answer passes only the stop its own reply caused. A body with
    neither field is 422."""
    enforce_rate_limit(str(current_user.id), "crm_write", "RATE_LIMIT_CRM_WRITE")
    if body.auto_send_meeting_time is None and body.answer_earlier_replies is None:
        return JSONResponse(status_code=422, content={
            "detail": "send auto_send_meeting_time and/or answer_earlier_replies"})
    try:
        if body.auto_send_meeting_time is not None:
            reply_copilot.set_auto_send(db, current_user.id, body.auto_send_meeting_time,
                                        actor_user_id=current_user.id)
        if body.answer_earlier_replies is not None:
            reply_copilot.set_answer_earlier_replies(db, current_user.id,
                                                     body.answer_earlier_replies,
                                                     actor_user_id=current_user.id)
    except reply_copilot.CopilotError as exc:
        return _error(exc)
    return reply_copilot.settings_out(db, current_user.id)


@router.get("/reply-copilot/metrics", dependencies=[Depends(require_enabled)])
def get_metrics(days: int = Query(default=30, ge=1, le=365), db: Session = Depends(get_db),
                current_user: User = Depends(get_current_user)) -> dict:
    """Median reply-to-approval time and the share of drafts sent unedited --
    measured from this account's rows only."""
    return reply_copilot.metrics(db, current_user.id, days=days)
