"""Vectorae agents: five advisory agents and the master supervisor.

GET   /agents                       the master's view: every agent, which core
                                    runs it (native C++/Rust or the Python
                                    fallback), circuit state and health
POST  /agents/{name}/run            run one agent for the current account
POST  /agents/claim_check/check     check one draft's wording, no database

Every agent reads only the caller's own data (lead -> strategy -> product ->
user, app/agents/base.py::owned_lead_ids; mailboxes by user_id) and changes
nothing: no send, pause, suppression or edit. Spec:
docs/features/vectorae-agents.md.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agents import claim_check, supervisor
from app.api.deps import get_current_user
from app.core.rate_limiting import enforce_rate_limit
from app.db.base import get_db
from app.db.models import User

router = APIRouter(prefix="/agents", tags=["agents"])


class RunIn(BaseModel):
    params: dict = Field(default_factory=dict)


class ClaimCheckIn(BaseModel):
    subject: str = Field(default="", max_length=500)
    body: str = Field(default="", max_length=20000)
    first_touch: bool = True


@router.get("")
def list_agents(db: Session = Depends(get_db),
                current_user: User = Depends(get_current_user)) -> dict:
    return supervisor.status(db)


@router.post("/claim_check/check")
def check_claims(payload: ClaimCheckIn,
                 current_user: User = Depends(get_current_user)) -> dict:
    return claim_check.check_text(payload.subject, payload.body, payload.first_touch)


@router.post("/{name}/run")
def run_agent(name: str, payload: RunIn | None = None, db: Session = Depends(get_db),
              current_user: User = Depends(get_current_user)) -> dict:
    if name not in supervisor.AGENTS:
        raise HTTPException(status_code=404, detail="unknown agent")
    enforce_rate_limit(str(current_user.id), "agent_run", "RATE_LIMIT_AGENT_RUN")
    try:
        return supervisor.run_agent(db, current_user, name, payload.params if payload else {})
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except supervisor.CircuitOpen as exc:
        raise HTTPException(status_code=503, detail={"error": "agent_circuit_open",
                                                     "health": exc.health}) from None
    except supervisor.AgentFailed as exc:
        raise HTTPException(status_code=502, detail={"error": "agent_failed",
                                                     "message": str(exc)}) from None
