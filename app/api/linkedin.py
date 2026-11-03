"""Feature Group 5 API — a lead's LinkedIn state.

  GET /leads/{id}/linkedin    what the USER reported on this lead's LinkedIn
                              tasks (connection pending / connected)

Since 2026-11-02 (evals/vectorae, option a) there is no LinkedIn account
connection, no Unipile hosted auth and no Unipile webhook: Vectorae never acts
on LinkedIn. LinkedIn steps are tasks the user performs (/linkedin-tasks).
"""

# NOTE: deliberately NO `from __future__ import annotations` (FastAPI).

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.leads import _owned_lead
from app.db.base import get_db
from app.db.models import User
from app.services import linkedin_outreach

router = APIRouter(tags=["linkedin"])


@router.get("/leads/{lead_id}/linkedin")
def lead_linkedin(lead_id: uuid.UUID, db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)) -> dict:
    return linkedin_outreach.lead_state(_owned_lead(db, lead_id, current_user))
