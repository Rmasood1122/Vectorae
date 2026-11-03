"""Daily send approval endpoints (docs/features/daily-send-approval.md).

GET  /send-approval/today     today's state, timezone, queued counts per
                              channel, the LinkedIn warning (any member)
POST /send-approval/approve   approve today's automatic sending on channels
                              (owner / manager). LinkedIn needs
                              linkedin_risk_acknowledged: true + the warning
                              version shown, otherwise 422 (409 if stale)
POST /send-approval/decline   no automatic sending today (owner / manager);
                              approving later today is still possible

`current_user` is the workspace OWNER (app/services/auth.py resolves the
selected workspace), so every read and write is keyed by the account owner's
id; the person who decided is `request.state.actor`. None of these routes
sends anything: approving returns deferred messages to the queue, and the send
task re-checks every gate.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.rate_limiting import enforce_rate_limit
from app.db.base import get_db
from app.db.models import User
from app.services import daily_send_approval as approval
from app.services import rbac

router = APIRouter(tags=["send-approval"])


class ApproveIn(BaseModel):
    channels: list[str] = Field(min_length=1, max_length=len(approval.ALL_CHANNELS))
    #: Required (true) whenever "linkedin" is in `channels`: the person read
    #: that Vectorae only queues LinkedIn tasks they send themselves.
    linkedin_risk_acknowledged: bool = False
    #: The LinkedIn warning version the person was shown.
    linkedin_warning_version: str | None = Field(default=None, max_length=40)


class DeclineIn(BaseModel):
    note: str | None = Field(default=None, max_length=500)


def _role(request: Request) -> str:
    return getattr(request.state, "workspace_role", None) or "owner"


def _require_manager(request: Request) -> None:
    if not rbac.at_least(_role(request), "manager"):
        raise HTTPException(status_code=403,
                            detail="Approving automatic sending needs an owner or manager "
                                   "in this workspace.")


#: The founder's rule is that a PERSON decides every day. A personal API key
#: (Zapier, Make, a script) is not a person, so it can read the state but can
#: never approve -- least of all acknowledge LinkedIn's restriction risk.
API_KEY_REFUSED = ("Approving or declining automatic sending needs a person signed in; "
                   "a personal API key cannot make this decision.")


def _require_person(request: Request) -> None:
    if getattr(request.state, "auth_via", None) == "api_key":
        raise HTTPException(status_code=403, detail=API_KEY_REFUSED)


def _actor_id(request: Request, current_user: User):
    actor = getattr(request.state, "actor", None)
    return getattr(actor, "id", None) or current_user.id


@router.get("/send-approval/today")
def get_today(request: Request, db: Session = Depends(get_db),
              current_user: User = Depends(get_current_user)) -> dict:
    out = approval.state(db, current_user.id)
    out["can_decide"] = (rbac.at_least(_role(request), "manager")
                         and getattr(request.state, "auth_via", None) != "api_key")
    return out


@router.post("/send-approval/approve")
def approve_today(body: ApproveIn, request: Request, db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)) -> dict:
    _require_person(request)
    _require_manager(request)
    enforce_rate_limit(str(current_user.id), "send_approval_decision", "RATE_LIMIT_CRM_WRITE")
    try:
        out = approval.approve(db, current_user.id, actor_id=_actor_id(request, current_user),
                               channels=body.channels,
                               linkedin_risk_acknowledged=body.linkedin_risk_acknowledged,
                               linkedin_warning_version=body.linkedin_warning_version)
    except approval.ApprovalError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    out["can_decide"] = True
    return out


@router.post("/send-approval/decline")
def decline_today(body: DeclineIn, request: Request, db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)) -> dict:
    _require_person(request)
    _require_manager(request)
    enforce_rate_limit(str(current_user.id), "send_approval_decision", "RATE_LIMIT_CRM_WRITE")
    out = approval.decline(db, current_user.id, actor_id=_actor_id(request, current_user),
                           note=body.note)
    out["can_decide"] = True
    return out
