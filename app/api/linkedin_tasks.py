"""L0 assisted LinkedIn tasks: the user's queue (app/services/linkedin_tasks.py).

Every query is filtered on current_user.id. There is no bulk approve, bulk
open or bulk complete endpoint, on purpose: each LinkedIn action is one
person's decision.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.base import get_db
from app.db.models import LinkedInConnection, User
from app.services import linkedin_tasks as lt
from app.services import network_messages as nm

router = APIRouter(prefix="/linkedin-tasks", tags=["linkedin-tasks"])


class EditIn(BaseModel):
    text: str = Field(min_length=1, max_length=5000)


class CompleteIn(BaseModel):
    outcome: str = Field(pattern="^(done|skipped|replied)$")


class FromMessageIn(BaseModel):
    message_id: uuid.UUID
    kind: str = Field(default="message", pattern="^(connection_note|message)$")


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except lt.TaskError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from None


@router.get("")
def list_tasks(status: str | None = Query(default=None, max_length=60),
               db: Session = Depends(get_db),
               current_user: User = Depends(get_current_user)) -> dict:
    statuses = tuple(s for s in (status or "").split(",") if s) or None
    return {"tasks": [lt.out(t) for t in lt.list_for(db, current_user, statuses)],
            "limits": {"connection_note": lt._cap(db, "connection_note"),
                       "message": lt._cap(db, "message")},
            "approval_hours": int(lt.APPROVAL_TTL.total_seconds() // 3600)}


@router.post("/from-network-message", status_code=201)
def from_network_message(body: FromMessageIn, request: Request, db: Session = Depends(get_db),
                         current_user: User = Depends(get_current_user)) -> dict:
    """Queue a saved message to one of the user's own connections as an L0
    task. The draft already went through network_messages' claim containment
    (_without_unsupported), so it is claim-checked."""
    try:
        msg = nm.own_message(db, current_user, body.message_id)
    except nm.MessageError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from None
    if msg.status != "approved":
        raise HTTPException(status_code=409, detail="Save the message first.")
    conn = db.get(LinkedInConnection, msg.connection_id)
    if conn is None or conn.user_id != current_user.id or not conn.profile_url:
        raise HTTPException(status_code=409,
                            detail="This connection has no LinkedIn profile link.")
    task = _call(lt.create, db, current_user, source="network", kind=body.kind, text=msg.text,
                 profile_url=conn.profile_url, claim_checked=True, connection_id=conn.id,
                 request=request)
    db.commit()
    return lt.out(task)


@router.put("/{task_id}")
def edit_task(task_id: uuid.UUID, body: EditIn, db: Session = Depends(get_db),
              current_user: User = Depends(get_current_user)) -> dict:
    task = _call(lt.edit, db, current_user, task_id, body.text)
    db.commit()
    return lt.out(task)


@router.post("/{task_id}/approve")
def approve_task(task_id: uuid.UUID, request: Request, db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)) -> dict:
    try:
        task = lt.approve(db, current_user, task_id, request=request)
    except lt.TaskError as exc:
        db.commit()                      # keep a cancellation the check made
        raise HTTPException(status_code=exc.status, detail=exc.detail) from None
    db.commit()
    return lt.out(task)


@router.post("/{task_id}/open")
def open_task(task_id: uuid.UUID, request: Request, db: Session = Depends(get_db),
              current_user: User = Depends(get_current_user)) -> dict:
    try:
        payload = lt.open_task(db, current_user, task_id, request=request)
    except lt.TaskError as exc:
        db.commit()                      # keep an expiry/cancellation the check made
        raise HTTPException(status_code=exc.status, detail=exc.detail) from None
    db.commit()
    return payload


@router.post("/{task_id}/complete")
def complete_task(task_id: uuid.UUID, body: CompleteIn, request: Request,
                  db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)) -> dict:
    task = _call(lt.complete, db, current_user, task_id, body.outcome, request=request)
    db.commit()
    return lt.out(task)
