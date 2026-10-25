"""Transparent attribution ledger (Part 1, Feature 8).

GET  /attribution                        the ledger, newest outcome first
GET  /attribution/summary                which step and channel earned them
POST /attribution/recompute              run the sweep now (back-fills history)
GET  /leads/{lead_id}/attribution        what earned this prospect's outcomes

New paths. Nothing here writes an outcome; it only explains the ones that
already happened.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, is_admin
from app.core.rate_limiting import enforce_rate_limit
from app.db.base import get_db
from app.db.models import User
from app.services import attribution, crm_service

router = APIRouter(tags=["attribution"])


@router.get("/attribution")
def list_attribution(
    outcome_kind: str | None = Query(default=None,
                                     pattern="^(meeting_booked|positive_reply|won)$"),
    strategy_id: uuid.UUID | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Every credited outcome with the touch behind it.

    Each row carries its `method` and `confidence`: a booking the prospect
    replied into is a fact, and "the last thing we sent before they booked" is
    a guess. Both are useful; presenting them identically is not."""
    return attribution.ledger(db, current_user.id, outcome_kind=outcome_kind,
                              strategy_id=strategy_id, limit=limit, offset=offset)


@router.get("/attribution/summary")
def attribution_summary(
    strategy_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Which step and which channel actually earn outcomes.

    Every bucket carries a `certain` count beside its total, so an aggregate
    built mostly from last-touch guesses cannot be mistaken for one built from
    replies."""
    return attribution.summary(db, current_user.id, strategy_id=strategy_id)


@router.post("/attribution/recompute")
def recompute_attribution(
    request: Request,
    limit: int = Query(default=500, ge=1, le=2000),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Run the sweep now instead of waiting for the quarter-hourly one.

    Idempotent: UNIQUE (outcome_kind, outcome_id) means it can never
    double-credit, so this is safe to press repeatedly.

    Scoped to the caller's own outcomes (NEW-02). Only an admin gets the
    deployment-wide sweep the scheduler runs; before this, any account could
    trigger it, writing other accounts' ledgers and reading their volume back
    in the counts."""
    enforce_rate_limit(str(current_user.id), "attribution_recompute", "RATE_LIMIT_AI_ACTION")
    if is_admin(request, current_user):
        return attribution.run_sweep(db, limit=limit)
    return attribution.run_sweep(db, limit=limit, user_id=current_user.id)


@router.get("/leads/{lead_id}/attribution")
def lead_attribution(lead_id: uuid.UUID, db: Session = Depends(get_db),
                     current_user: User = Depends(get_current_user)) -> list[dict]:
    """What earned this prospect's outcomes, with the message text itself."""
    lead = crm_service.owned_lead(db, lead_id, current_user)
    return attribution.for_lead(db, lead.id)
