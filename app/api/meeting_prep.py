"""Feature Group 7 API — meeting prep briefs, meeting outcomes, follow-up drafts.

Conventions match the rest of app/api: `Depends(get_current_user)` on every
route, ownership answered as 404 (never 403, so existence cannot be probed),
and `enforce_rate_limit` as the first statement of any handler that spends a
model call.

GENERATION IS ASYNCHRONOUS FOR BRIEFS, SYNCHRONOUS FOR FOLLOW-UPS
A brief takes the whole lead context and eleven sections; the endpoints queue
it and return 202, and the lead page polls the brief's `status`. The
follow-up email is one short email the user is waiting to review, so logging
an outcome generates it inline -- AFTER the outcome itself is committed, so a
model failure costs the draft, never the outcome (see meeting_followup).
"""

# NOTE: deliberately NO `from __future__ import annotations` -- see the same
# note in app/api/crm.py: FastAPI resolves these annotations at runtime.

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.metering import metered
from app.api.leads import _owned_lead
from app.core.rate_limiting import enforce_rate_limit
from app.services import local_business
from app.db.base import get_db
from app.db.models import (
    Meeting,
    MeetingOutcome,
    MeetingOutcomeKind,
    MeetingPrepBrief,
    MeetingPrepStatus,
    User,
)
from app.services import meeting_followup, meeting_prep
from app.workers import meeting_prep_tasks

router = APIRouter(tags=["meeting-prep"])

_AI_LIMIT = "RATE_LIMIT_AI_ACTION"
GMAIL_DRAFTS_URL = "https://mail.google.com/mail/u/0/#drafts"


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class BriefOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    lead_id: uuid.UUID
    source: str
    status: MeetingPrepStatus
    meeting_start_at: datetime | None
    meeting_url: str | None
    content_md: str | None
    sections_json: dict | None
    profile_json: dict | None
    opening_script: str | None
    error: str | None
    generated_at: datetime | None
    reminder_24h_sent_at: datetime | None
    reminder_1h_sent_at: datetime | None
    cancelled_at: datetime | None
    created_at: datetime


class BriefSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source: str
    status: MeetingPrepStatus
    meeting_start_at: datetime | None
    cancelled_at: datetime | None
    created_at: datetime


class LeadPrepOut(BaseModel):
    brief: BriefOut | None
    history: list[BriefSummary]


class PrepRequestIn(BaseModel):
    meeting_id: uuid.UUID | None = None


class OutcomeIn(BaseModel):
    outcome: MeetingOutcomeKind
    notes: str | None = Field(default=None, max_length=20_000)
    meeting_id: uuid.UUID | None = None
    deal_value: float | None = Field(default=None, ge=0, le=1e12)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    deal_name: str | None = Field(default=None, max_length=200)
    generate_followup: bool = True

    @field_validator("currency")
    @classmethod
    def _upper(cls, value: str) -> str:
        if not value.isalpha():
            raise ValueError("currency must be a 3-letter ISO code")
        return value.upper()


class OutcomeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    lead_id: uuid.UUID
    meeting_id: uuid.UUID | None
    deal_id: uuid.UUID | None
    outcome: MeetingOutcomeKind
    notes: str | None
    previous_status: str | None
    new_status: str | None
    followup_subject: str | None
    followup_body: str | None
    draft_status: str
    draft_error: str | None
    gmail_draft_id: str | None
    sent_at: datetime | None
    created_at: datetime
    gmail_drafts_url: str = GMAIL_DRAFTS_URL


class DraftEditIn(BaseModel):
    subject: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=6000)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _owned_brief(db: Session, brief_id: uuid.UUID, user: User) -> MeetingPrepBrief:
    brief = db.get(MeetingPrepBrief, brief_id)
    if brief is None:
        raise HTTPException(status_code=404, detail="brief not found")
    try:
        _owned_lead(db, brief.lead_id, user)
    except HTTPException:
        raise HTTPException(status_code=404, detail="brief not found") from None
    return brief


def _owned_outcome(db: Session, outcome_id: uuid.UUID, user: User) -> MeetingOutcome:
    row = db.get(MeetingOutcome, outcome_id)
    if row is None:
        raise HTTPException(status_code=404, detail="meeting outcome not found")
    try:
        _owned_lead(db, row.lead_id, user)
    except HTTPException:
        raise HTTPException(status_code=404, detail="meeting outcome not found") from None
    return row


def _owned_meeting(db: Session, meeting_id: uuid.UUID | None, user: User,
                   lead_id: uuid.UUID) -> Meeting | None:
    if meeting_id is None:
        return None
    meeting = db.get(Meeting, meeting_id)
    if meeting is None or meeting.host_user_id != user.id or (
        meeting.lead_id is not None and meeting.lead_id != lead_id
    ):
        raise HTTPException(status_code=404, detail="meeting not found")
    return meeting


def _queue(brief: MeetingPrepBrief, db: Session) -> None:
    brief.status = MeetingPrepStatus.PENDING
    brief.error = None
    db.commit()
    meeting_prep_tasks.enqueue_generation(brief.id)


# ---------------------------------------------------------------------------
# Meeting prep briefs
# ---------------------------------------------------------------------------


@router.get("/leads/{lead_id}/meeting-prep", response_model=LeadPrepOut)
def get_lead_meeting_prep(lead_id: uuid.UUID, db: Session = Depends(get_db),
                          current_user: User = Depends(get_current_user)) -> LeadPrepOut:
    _owned_lead(db, lead_id, current_user)
    brief = meeting_prep.latest_for_lead(db, lead_id)
    history = db.execute(
        select(MeetingPrepBrief).where(MeetingPrepBrief.lead_id == lead_id)
        .order_by(MeetingPrepBrief.created_at.desc()).limit(20)
    ).scalars().all()
    return LeadPrepOut(
        brief=BriefOut.model_validate(brief) if brief else None,
        history=[BriefSummary.model_validate(b) for b in history],
    )


@router.post("/leads/{lead_id}/meeting-prep", response_model=BriefOut, status_code=202)
def request_lead_meeting_prep(lead_id: uuid.UUID, body: PrepRequestIn,
                              db: Session = Depends(get_db),
                              current_user: User = Depends(get_current_user)) -> MeetingPrepBrief:
    """A brief on demand -- for a call booked outside Vectorae, or a lead the
    user simply wants to prepare for. Tied to a meeting when one is given, so
    the reminders follow that meeting's time."""
    enforce_rate_limit(str(current_user.id), "ai_action", _AI_LIMIT)
    lead = _owned_lead(db, lead_id, current_user)
    local_business.refuse_if_customer(lead, "A meeting prep brief")
    meeting = _owned_meeting(db, body.meeting_id, current_user, lead.id)
    brief = meeting_prep.upsert_brief(
        db, lead, user_id=current_user.id, source=meeting_prep.SOURCE_MANUAL,
        external_ref=f"manual:{meeting.id if meeting else lead.id}",
        meeting_start_at=meeting.start_at if meeting else None,
        meeting_url=meeting.meeting_url if meeting else None,
        meeting_id=meeting.id if meeting else None,
    )
    _queue(brief, db)
    return brief


@router.get("/meeting-prep/{brief_id}", response_model=BriefOut)
def get_meeting_prep(brief_id: uuid.UUID, db: Session = Depends(get_db),
                     current_user: User = Depends(get_current_user)) -> MeetingPrepBrief:
    return _owned_brief(db, brief_id, current_user)


@router.post("/meeting-prep/{brief_id}/regenerate", response_model=BriefOut,
             status_code=202)
def regenerate_meeting_prep(brief_id: uuid.UUID, db: Session = Depends(get_db),
                            current_user: User = Depends(get_current_user)) -> MeetingPrepBrief:
    enforce_rate_limit(str(current_user.id), "ai_action", _AI_LIMIT)
    brief = _owned_brief(db, brief_id, current_user)
    if brief.status is MeetingPrepStatus.GENERATING:
        raise HTTPException(status_code=409, detail="this brief is already being generated")
    _queue(brief, db)
    return brief


# ---------------------------------------------------------------------------
# Meeting outcomes + follow-up
# ---------------------------------------------------------------------------


@router.post("/leads/{lead_id}/meeting-outcome", response_model=OutcomeOut,
             status_code=201)
def log_meeting_outcome(lead_id: uuid.UUID, body: OutcomeIn,
                        db: Session = Depends(get_db),
                        current_user: User = Depends(get_current_user)) -> MeetingOutcome:
    if body.generate_followup:
        enforce_rate_limit(str(current_user.id), "ai_action", _AI_LIMIT)
    lead = _owned_lead(db, lead_id, current_user)
    if body.generate_followup:
        # Logging an outcome is local; a model-drafted follow-up to a
        # consumer is not offered.
        local_business.refuse_if_customer(lead, "A drafted follow-up email")
    meeting = _owned_meeting(db, body.meeting_id, current_user, lead.id)
    row = meeting_followup.log_outcome(
        db, user=current_user, lead=lead, outcome=body.outcome, notes=body.notes,
        meeting_id=meeting.id if meeting else None, deal_value=body.deal_value,
        currency=body.currency, deal_name=body.deal_name,
    )
    if body.generate_followup:
        # The outcome is already logged; a draft refused on the daily AI limit
        # is marked failed and can be regenerated, so no 429 here.
        with metered(db, current_user, "drafting", refuse_after=False):
            meeting_followup.draft_followup(db, row, user=current_user)
    db.refresh(row)
    return row


@router.get("/leads/{lead_id}/meeting-outcomes", response_model=list[OutcomeOut])
def list_meeting_outcomes(lead_id: uuid.UUID, db: Session = Depends(get_db),
                          current_user: User = Depends(get_current_user)) -> list[MeetingOutcome]:
    _owned_lead(db, lead_id, current_user)
    return list(db.execute(
        select(MeetingOutcome).where(MeetingOutcome.lead_id == lead_id)
        .order_by(MeetingOutcome.created_at.desc())
    ).scalars().all())


@router.put("/meeting-outcomes/{outcome_id}/draft", response_model=OutcomeOut)
def edit_followup_draft(outcome_id: uuid.UUID, body: DraftEditIn,
                        db: Session = Depends(get_db),
                        current_user: User = Depends(get_current_user)) -> MeetingOutcome:
    """Save the user's edits and push them to the Gmail draft."""
    row = _owned_outcome(db, outcome_id, current_user)
    if row.sent_at is not None:
        raise HTTPException(status_code=409, detail="this follow-up was already sent")
    row.followup_subject = body.subject.strip()
    row.followup_body = body.body.strip()
    db.commit()
    return meeting_followup.save_gmail_draft(db, row, user=current_user)


@router.post("/meeting-outcomes/{outcome_id}/draft/regenerate", response_model=OutcomeOut)
def regenerate_followup_draft(outcome_id: uuid.UUID, db: Session = Depends(get_db),
                              current_user: User = Depends(get_current_user)) -> MeetingOutcome:
    enforce_rate_limit(str(current_user.id), "ai_action", _AI_LIMIT)
    row = _owned_outcome(db, outcome_id, current_user)
    if row.sent_at is not None:
        raise HTTPException(status_code=409, detail="this follow-up was already sent")
    with metered(db, current_user, "drafting"):
        return meeting_followup.draft_followup(db, row, user=current_user)


@router.post("/meeting-outcomes/{outcome_id}/recheck", response_model=OutcomeOut)
def recheck_followup(outcome_id: uuid.UUID, db: Session = Depends(get_db),
                     current_user: User = Depends(get_current_user)) -> MeetingOutcome:
    """"Check again" on a blocked follow-up (after the cause was removed)."""
    row = _owned_outcome(db, outcome_id, current_user)
    try:
        return meeting_followup.recheck(db, row, user=current_user)
    except meeting_followup.FollowupNotSendable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/meeting-outcomes/{outcome_id}/send", response_model=OutcomeOut)
def send_followup(outcome_id: uuid.UUID, db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)) -> MeetingOutcome:
    row = _owned_outcome(db, outcome_id, current_user)
    try:
        return meeting_followup.send_followup(db, row, user=current_user)
    except meeting_followup.FollowupNotSendable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
