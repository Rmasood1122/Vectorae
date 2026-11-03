"""M9 — native CRM API: dashboard aggregates, data grid, notes, tags,
saved views, custom fields, and the SSE real-time stream.

CONVENTIONS THIS ROUTER FOLLOWS (none of them invented here)

  auth        `Depends(get_current_user)` from app/api/deps.py on every route
              except the SSE stream, which authenticates by ticket -- see
              `crm_stream` for why it cannot use the header.
  ownership   app/services/crm_service.py::owned_lead / owned_strategy, the
              same lead -> strategy -> product -> user_id chain
              app/api/leads.py walks. Always 404, never 403, so a probe
              cannot distinguish "not yours" from "does not exist".
  rate limit  `enforce_rate_limit(...)` as the handler's FIRST statement, not
              a route dependency. app/core/rate_limiting.py documents why at
              length: as a dependency it runs before FastAPI validates the
              body, so a typo'd payload burns a slot having created nothing.
              tests/test_rate_limit_ordering.py pins that ordering.
"""

# NOTE: deliberately NO `from __future__ import annotations` here, matching
# every other router in this package. With postponed evaluation, FastAPI's
# get_typed_return_annotation resolves a `-> None` return annotation to the
# NoneType *class*, which is truthy, so it becomes a response model -- and
# every 204 route in this file then fails at import with "Status code 204 must
# not have a response body". Without it, Python stores a literal `None`, which
# is falsy, and FastAPI correctly reads it as "no response model".

import asyncio
import json
import secrets
import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.metering import metered
from app.core.logging import get_logger
from app.core.rate_limiting import enforce_rate_limit
from app.db.base import get_db
from app.db.models import (
    CrmActivityKind,
    CrmCustomField,
    CrmCustomFieldValue,
    CrmFieldType,
    CrmLeadMeta,
    CrmLeadTag,
    CrmNote,
    CrmSavedView,
    CrmTag,
    CrmViewType,
    InboundReply,
    Lead,
    LeadStatus,
    User,
)
from app.services import crm_events, crm_service, lead_assignment, rbac, reply_intent

logger = get_logger("api.crm")

router = APIRouter(prefix="/crm", tags=["crm"])

_WRITE_LIMIT = "RATE_LIMIT_CRM_WRITE"

# The status transition map is NOT redefined here. app/api/ui_support.py owns
# it, the kanban already validates against it, and a second copy would be a
# second thing to update -- and the moment the two disagree, the same drag is
# legal on one screen and rejected on the other.
from app.api.ui_support import _ALLOWED_TRANSITIONS  # noqa: E402


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class NoteIn(BaseModel):
    body: str = Field(min_length=1, max_length=10_000)


class TagIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    # A theme token name, never a hex value: the app recolors from CSS
    # variables and a stored hex would be the one element ignoring the preset.
    color_token: str = Field(default="muted", max_length=20)


class LeadPatchIn(BaseModel):
    """Inline edit from the grid. Every field optional; only what is sent is
    written, so the grid can PATCH a single cell without reading the row
    first and racing another editor's change to a different column."""

    status: LeadStatus | None = None
    owner_user_id: uuid.UUID | None = None
    priority: str | None = Field(default=None, max_length=20)
    next_action_at: datetime | None = None
    custom: dict | None = None


class BulkPatchIn(BaseModel):
    lead_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)
    status: LeadStatus | None = None
    add_tag_ids: list[uuid.UUID] | None = None
    remove_tag_ids: list[uuid.UUID] | None = None
    # Manual bulk assignment. Presence (model_fields_set), not truthiness,
    # decides whether to touch it: an explicit null means "unassign".
    owner_user_id: uuid.UUID | None = None


class RoundRobinIn(BaseModel):
    """Distribute unassigned leads across the team.

    Exactly one of `lead_ids` (the grid's selection) or `strategy_id` (every
    unassigned lead in one campaign). `roles` narrows who receives leads --
    by default only SDRs, since handing a manager half the list is rarely what
    "distribute to the team" means.
    """

    lead_ids: list[uuid.UUID] | None = Field(default=None, min_length=1, max_length=500)
    strategy_id: uuid.UUID | None = None
    roles: list[str] = Field(default_factory=lambda: list(
        lead_assignment.DEFAULT_ROUND_ROBIN_ROLES), min_length=1, max_length=3)


class SavedViewIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    view_type: CrmViewType = CrmViewType.GRID
    filters_json: dict = Field(default_factory=dict)
    sort_json: list = Field(default_factory=list)
    columns_json: list = Field(default_factory=list)
    is_default: bool = False


class CustomFieldIn(BaseModel):
    key: str = Field(min_length=1, max_length=60, pattern=r"^[a-z0-9_]+$")
    label: str = Field(min_length=1, max_length=100)
    field_type: CrmFieldType = CrmFieldType.TEXT
    options_json: list | None = None
    sort_order: int = 0


class GridQueryIn(BaseModel):
    """POSTed rather than sent as a query string.

    A saved view's filter set is a nested structure ({"company": {"op":
    "contains", "value": "..."}}), and encoding that into query parameters
    means inventing a serialisation, hitting URL length limits on a wide
    filter set, and writing filter values into every access log. POST with a
    JSON body avoids all three. It is a read, so it is idempotent and
    deliberately not rate-limited as a write.
    """

    strategy_id: uuid.UUID | None = None
    filters: dict = Field(default_factory=dict)
    sort: list[dict] = Field(default_factory=list)
    search: str | None = Field(default=None, max_length=200)
    tag_ids: list[uuid.UUID] = Field(default_factory=list)
    limit: int = Field(default=100, ge=1, le=crm_service.MAX_GRID_LIMIT)
    offset: int = Field(default=0, ge=0)


# ---------------------------------------------------------------------------
# Dashboard (4 pages)
# ---------------------------------------------------------------------------


@router.get("/dashboard/pipeline")
def dashboard_pipeline(
    strategy_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Page 1 — funnel, stage conversion, bookings this week/month, trend."""
    if strategy_id is not None:
        crm_service.owned_strategy(db, strategy_id, current_user)
    return crm_service.dashboard_pipeline(db, current_user, strategy_id)


@router.get("/dashboard/leads")
def dashboard_leads(
    strategy_id: uuid.UUID | None = None,
    stuck_after_days: int = Query(default=7, ge=1, le=365),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Page 2 — velocity, source mix, verification quality, stuck leads."""
    if strategy_id is not None:
        crm_service.owned_strategy(db, strategy_id, current_user)
    return crm_service.dashboard_leads(db, current_user, strategy_id,
                                       stuck_after_days)


@router.get("/dashboard/campaigns")
def dashboard_campaigns(
    strategy_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Page 3 — per-sequence/channel rates, A/B variants, bounce vs threshold."""
    if strategy_id is not None:
        crm_service.owned_strategy(db, strategy_id, current_user)
    return crm_service.dashboard_campaigns(db, current_user, strategy_id)


@router.get("/dashboard/activity")
def dashboard_activity(
    strategy_id: uuid.UUID | None = None,
    lead_id: uuid.UUID | None = None,
    kind: list[str] | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    # An OPAQUE cursor from a previous response's `next_before`, not a bare
    # timestamp: activity timestamps tie routinely, so the cursor has to carry
    # the whole sort key. See crm_service.dashboard_activity.
    before: str | None = Query(default=None, max_length=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Page 4 — the account-wide activity feed."""
    if strategy_id is not None:
        crm_service.owned_strategy(db, strategy_id, current_user)
    return crm_service.dashboard_activity(
        db, current_user, strategy_id=strategy_id, lead_id=lead_id,
        kinds=kind, limit=limit, before=before,
    )


# ---------------------------------------------------------------------------
# Grid
# ---------------------------------------------------------------------------


@router.post("/grid")
def crm_grid(
    body: GridQueryIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    if body.strategy_id is not None:
        crm_service.owned_strategy(db, body.strategy_id, current_user)
    return crm_service.grid_page(
        db, current_user,
        strategy_id=body.strategy_id, filters=body.filters, sort=body.sort,
        search=body.search, tag_ids=body.tag_ids,
        limit=body.limit, offset=body.offset,
    )


@router.patch("/leads/{lead_id}")
def patch_lead(
    lead_id: uuid.UUID,
    body: LeadPatchIn,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Inline cell edit.

    Sits ALONGSIDE app/api/ui_support.py's PATCH /leads/{id} rather than
    replacing it: the kanban's contract is `{status}` in, `{id, status}` out,
    and widening that response would change a shape the pipeline page and the
    mobile app already consume. Status changes here run through the same
    _ALLOWED_TRANSITIONS map, so the two endpoints cannot diverge on what a
    legal move is.
    """
    enforce_rate_limit(str(current_user.id), "crm_write", _WRITE_LIMIT)

    lead = crm_service.owned_lead(db, lead_id, current_user)
    changed: list[str] = []

    if body.status is not None and body.status is not lead.status:
        if body.status not in _ALLOWED_TRANSITIONS.get(lead.status, set()):
            raise HTTPException(
                status_code=422,
                detail=(f"cannot move a lead from {lead.status.value} to "
                        f"{body.status.value}"),
            )
        previous = lead.status.value
        lead.status = body.status
        crm_service.log_activity(
            db, lead, CrmActivityKind.STATUS_CHANGED, actor=current_user,
            from_value=previous, to_value=body.status.value,
        )
        # The moment the CURRENT stage was entered -- the measurement the
        # velocity dashboard needs and that nothing before M9 recorded.
        meta = crm_service.get_or_create_meta(db, lead)
        meta.stage_entered_at = datetime.now(timezone.utc)
        changed.append("status")

    if "owner_user_id" in body.model_fields_set:
        meta = crm_service.get_or_create_meta(db, lead)
        previous_owner = meta.owner_user_id
        # The assignee must belong to the workspace that owns this lead -- an
        # arbitrary id would let one account write another account's user into
        # its rows. And the role rule is judged against the person who ACTED
        # (request.state.actor), not current_user, which inside a workspace is
        # the owner. See app/services/lead_assignment.py.
        lead_assignment.require_assignable(db, current_user, body.owner_user_id)
        who = lead_assignment.assigner_for(request, current_user)
        reason = lead_assignment.refusal(who, previous_owner, body.owner_user_id)
        if reason is not None:
            raise HTTPException(status_code=403, detail=reason)
        meta.owner_user_id = body.owner_user_id
        crm_service.log_activity(
            db, lead, CrmActivityKind.OWNER_CHANGED, actor=who.actor,
            from_value=str(previous_owner) if previous_owner else None,
            to_value=str(body.owner_user_id) if body.owner_user_id else None,
        )
        changed.append("owner_user_id")

    if "priority" in body.model_fields_set:
        meta = crm_service.get_or_create_meta(db, lead)
        previous_priority = meta.priority
        meta.priority = body.priority
        crm_service.log_activity(
            db, lead, CrmActivityKind.FIELD_CHANGED, actor=current_user,
            from_value=previous_priority, to_value=body.priority,
            meta={"field": "priority"},
        )
        changed.append("priority")

    if "next_action_at" in body.model_fields_set:
        meta = crm_service.get_or_create_meta(db, lead)
        meta.next_action_at = body.next_action_at
        changed.append("next_action_at")

    if body.custom:
        fields = {
            field.key: field
            for field in db.execute(
                select(CrmCustomField)
                .where(CrmCustomField.user_id == current_user.id)
            ).scalars().all()
        }
        for key, raw in body.custom.items():
            field = fields.get(key)
            if field is None:
                raise HTTPException(status_code=422,
                                    detail=f"unknown custom field '{key}'")
            value_text, value_json = crm_service.coerce_field_value(field, raw)
            row = db.execute(
                select(CrmCustomFieldValue).where(
                    CrmCustomFieldValue.field_id == field.id,
                    CrmCustomFieldValue.lead_id == lead.id,
                )
            ).scalars().first()
            previous = (crm_service.decode_field_value(row)
                        if row is not None else None)
            if row is None:
                row = CrmCustomFieldValue(field_id=field.id, lead_id=lead.id)
                db.add(row)
            row.value_text = value_text
            row.value_json = value_json
            crm_service.log_activity(
                db, lead, CrmActivityKind.FIELD_CHANGED, actor=current_user,
                from_value=str(previous) if previous is not None else None,
                to_value=str(raw) if raw is not None else None,
                meta={"field": key},
            )
            changed.append(f"custom.{key}")

    if "status" in changed:
        # Removed / closed / disqualified ends the lead's sequences, the same
        # rule the Pipeline board applies (sequence_engine.stop_for_status) --
        # in the SAME transaction as the status change.
        from app.services import sequence_engine  # noqa: PLC0415

        sequence_engine.stop_for_status(db, lead, commit=False)
    db.commit()
    db.refresh(lead)
    return {"id": str(lead.id), "status": lead.status.value, "changed": changed}


@router.post("/leads/bulk")
def bulk_patch_leads(
    body: BulkPatchIn,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Bulk status change, tag add/remove and assignment from the grid's selection.

    PARTIAL SUCCESS IS THE CONTRACT, deliberately. Selecting 200 rows and
    moving them all to `contacted` will legitimately include some rows whose
    current status makes that transition illegal. Failing the whole batch on
    the first one means the user has to find and deselect it by hand with no
    indication of which it was. So legal moves are applied, illegal ones are
    reported per-lead in `skipped`, and the response says how many of each.
    """
    enforce_rate_limit(str(current_user.id), "crm_write", _WRITE_LIMIT)

    updated: list[str] = []
    skipped: list[dict] = []
    moved: list = []

    # Resolve every lead first: an id belonging to another account must 404
    # the whole request rather than being quietly skipped, because a silent
    # skip is indistinguishable from "that lead had nothing to change" and
    # would make cross-tenant probing free.
    leads = [crm_service.owned_lead(db, lead_id, current_user)
             for lead_id in body.lead_ids]

    owned_tag_ids: set[uuid.UUID] = set()
    requested_tags = list(body.add_tag_ids or []) + list(body.remove_tag_ids or [])
    if requested_tags:
        owned_tag_ids = set(db.execute(
            select(CrmTag.id).where(CrmTag.id.in_(requested_tags),
                                    CrmTag.user_id == current_user.id)
        ).scalars().all())
        missing = set(requested_tags) - owned_tag_ids
        if missing:
            raise HTTPException(status_code=404, detail="tag not found")

    # An assignee outside the workspace fails the WHOLE request, like a foreign
    # lead id: it is a bad request, not a per-row condition. A role that may not
    # make a particular reassignment is per-row, and lands in `skipped`.
    assign = "owner_user_id" in body.model_fields_set
    who = lead_assignment.assigner_for(request, current_user)
    if assign:
        lead_assignment.require_assignable(db, current_user, body.owner_user_id)

    for lead in leads:
        if body.status is not None and body.status is not lead.status:
            if body.status not in _ALLOWED_TRANSITIONS.get(lead.status, set()):
                skipped.append({
                    "lead_id": str(lead.id),
                    "reason": (f"cannot move from {lead.status.value} to "
                               f"{body.status.value}"),
                })
                continue
            previous = lead.status.value
            lead.status = body.status
            crm_service.log_activity(
                db, lead, CrmActivityKind.STATUS_CHANGED, actor=current_user,
                from_value=previous, to_value=body.status.value,
                meta={"bulk": True},
            )
            meta = crm_service.get_or_create_meta(db, lead)
            meta.stage_entered_at = datetime.now(timezone.utc)
            moved.append(lead)

        if assign:
            meta = crm_service.get_or_create_meta(db, lead)
            previous_owner = meta.owner_user_id
            reason = lead_assignment.refusal(who, previous_owner, body.owner_user_id)
            if reason is not None:
                skipped.append({"lead_id": str(lead.id), "reason": reason})
                continue
            if previous_owner != body.owner_user_id:
                meta.owner_user_id = body.owner_user_id
                crm_service.log_activity(
                    db, lead, CrmActivityKind.OWNER_CHANGED, actor=who.actor,
                    from_value=str(previous_owner) if previous_owner else None,
                    to_value=str(body.owner_user_id) if body.owner_user_id else None,
                    meta={"bulk": True},
                )

        for tag_id in body.add_tag_ids or []:
            exists = db.execute(
                select(CrmLeadTag.id).where(CrmLeadTag.lead_id == lead.id,
                                            CrmLeadTag.tag_id == tag_id)
            ).scalars().first()
            if exists is None:
                db.add(CrmLeadTag(lead_id=lead.id, tag_id=tag_id))
                crm_service.log_activity(
                    db, lead, CrmActivityKind.TAG_ADDED, actor=current_user,
                    to_value=str(tag_id), meta={"bulk": True},
                )

        for tag_id in body.remove_tag_ids or []:
            row = db.execute(
                select(CrmLeadTag).where(CrmLeadTag.lead_id == lead.id,
                                         CrmLeadTag.tag_id == tag_id)
            ).scalars().first()
            if row is not None:
                db.delete(row)
                crm_service.log_activity(
                    db, lead, CrmActivityKind.TAG_REMOVED, actor=current_user,
                    from_value=str(tag_id), meta={"bulk": True},
                )

        updated.append(str(lead.id))

    # Removed / closed / disqualified ends each moved lead's sequences -- the
    # same rule as every other status writer (sequence_engine.stop_for_status)
    # -- and the status changes and the stops commit TOGETHER: a failure part
    # way can no longer leave closed leads whose sequences still run
    # (gaps-sweep-2 review L4).
    from app.services import sequence_engine  # noqa: PLC0415

    for lead in moved:
        sequence_engine.stop_for_status(db, lead, commit=False)
    db.commit()
    return {"updated": updated, "updated_count": len(updated),
            "skipped": skipped, "skipped_count": len(skipped)}


# Upper bound on one round-robin call over a whole campaign, matching the bulk
# endpoint's 500-row cap: the response says when more remain.
_ROUND_ROBIN_MAX = 500


@router.post("/leads/assign-round-robin")
def assign_round_robin(
    body: RoundRobinIn,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Spread unassigned leads across the team, least-loaded first.

    Managers and owners only: it assigns leads to OTHER people. Idempotent --
    only unassigned leads are touched, each with a conditional write, so a
    retry or a double click reports the rest as `skipped` and moves nothing.
    See app/services/lead_assignment.py.
    """
    enforce_rate_limit(str(current_user.id), "crm_write", _WRITE_LIMIT)

    who = lead_assignment.assigner_for(request, current_user)
    if not rbac.at_least(who.role, "manager"):
        raise HTTPException(status_code=403,
                            detail="Distributing leads needs a manager in this workspace.")
    if (body.lead_ids is None) == (body.strategy_id is None):
        raise HTTPException(status_code=422,
                            detail="send exactly one of lead_ids or strategy_id")
    unknown = set(body.roles) - set(rbac.ROLES)
    if unknown:
        raise HTTPException(status_code=422, detail=f"unknown role(s): {sorted(unknown)}")

    more_remaining = False
    if body.lead_ids is not None:
        # Every id resolved first: a foreign lead 404s the whole request.
        leads = [crm_service.owned_lead(db, lead_id, current_user)
                 for lead_id in body.lead_ids]
    else:
        crm_service.owned_strategy(db, body.strategy_id, current_user)
        # Open, unassigned leads only. A closed or dropped lead has nobody to
        # work it, and counting it would unbalance the distribution.
        leads = list(db.execute(
            select(Lead)
            .outerjoin(CrmLeadMeta, CrmLeadMeta.lead_id == Lead.id)
            .where(Lead.strategy_id == body.strategy_id,
                   CrmLeadMeta.owner_user_id.is_(None),
                   Lead.status.notin_(list(crm_service.TERMINAL_STAGES)))
            .order_by(Lead.id)
            .limit(_ROUND_ROBIN_MAX + 1)
        ).scalars().all())
        more_remaining = len(leads) > _ROUND_ROBIN_MAX
        leads = leads[:_ROUND_ROBIN_MAX]

    assigned, skipped = lead_assignment.round_robin(db, current_user, leads, body.roles)
    per_member: dict[str, int] = {}
    for lead, assignee in assigned:
        crm_service.log_activity(
            db, lead, CrmActivityKind.OWNER_CHANGED, actor=who.actor,
            to_value=str(assignee), meta={"round_robin": True},
        )
        per_member[str(assignee)] = per_member.get(str(assignee), 0) + 1
    db.commit()
    return {
        "assigned": [{"lead_id": str(lead.id), "owner_user_id": str(assignee)}
                     for lead, assignee in assigned],
        "assigned_count": len(assigned),
        "skipped": skipped,
        "skipped_count": len(skipped),
        "per_member": per_member,
        "more_remaining": more_remaining,
    }


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------


@router.get("/leads/{lead_id}/notes")
def list_notes(
    lead_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    crm_service.owned_lead(db, lead_id, current_user)
    rows = db.execute(
        select(CrmNote).where(CrmNote.lead_id == lead_id)
        .order_by(CrmNote.created_at.desc())
    ).scalars().all()
    return {"items": [
        {
            "id": str(note.id),
            "lead_id": str(note.lead_id),
            "body": note.body,
            "author_user_id": (str(note.author_user_id)
                               if note.author_user_id else None),
            "created_at": note.created_at.isoformat() if note.created_at else None,
            "updated_at": note.updated_at.isoformat() if note.updated_at else None,
        }
        for note in rows
    ]}


@router.post("/leads/{lead_id}/notes", status_code=201)
def create_note(
    lead_id: uuid.UUID,
    body: NoteIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    enforce_rate_limit(str(current_user.id), "crm_write", _WRITE_LIMIT)

    lead = crm_service.owned_lead(db, lead_id, current_user)
    note = CrmNote(lead_id=lead.id, author_user_id=current_user.id,
                   body=body.body)
    db.add(note)
    db.flush()
    crm_service.log_activity(
        db, lead, CrmActivityKind.NOTE_ADDED, actor=current_user,
        # The note's first line, not its body: the feed is a timeline, and a
        # 10,000-character note pasted into it makes every other entry
        # unreadable. The full text is one click away on the lead.
        to_value=body.body.splitlines()[0][:200] if body.body else None,
        meta={"note_id": str(note.id)},
    )
    db.commit()
    db.refresh(note)
    return {"id": str(note.id), "lead_id": str(note.lead_id),
            "body": note.body,
            "author_user_id": str(note.author_user_id),
            "created_at": note.created_at.isoformat() if note.created_at else None}


@router.delete("/notes/{note_id}", status_code=204)
def delete_note(
    note_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    enforce_rate_limit(str(current_user.id), "crm_write", _WRITE_LIMIT)

    note = db.get(CrmNote, note_id)
    if note is None:
        raise HTTPException(status_code=404, detail="note not found")
    # Ownership is the LEAD's, not the note author's: the note hangs off a
    # lead, and reaching it requires owning that lead.
    lead = crm_service.owned_lead(db, note.lead_id, current_user)
    crm_service.log_activity(
        db, lead, CrmActivityKind.NOTE_DELETED, actor=current_user,
        from_value=note.body.splitlines()[0][:200] if note.body else None,
    )
    db.delete(note)
    db.commit()


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------


@router.get("/tags")
def list_tags(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    rows = db.execute(
        select(CrmTag).where(CrmTag.user_id == current_user.id)
        .order_by(CrmTag.name)
    ).scalars().all()
    return {"items": [
        {"id": str(tag.id), "name": tag.name, "color_token": tag.color_token}
        for tag in rows
    ]}


@router.post("/tags", status_code=201)
def create_tag(
    body: TagIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    enforce_rate_limit(str(current_user.id), "crm_write", _WRITE_LIMIT)

    existing = db.execute(
        select(CrmTag).where(CrmTag.user_id == current_user.id,
                             CrmTag.name == body.name)
    ).scalars().first()
    if existing is not None:
        # Idempotent rather than 409. The grid creates a tag by typing its
        # name; typing one that already exists means "use that one", not
        # "show me an error".
        return {"id": str(existing.id), "name": existing.name,
                "color_token": existing.color_token, "created": False}
    tag = CrmTag(user_id=current_user.id, name=body.name,
                 color_token=body.color_token)
    db.add(tag)
    db.commit()
    db.refresh(tag)
    return {"id": str(tag.id), "name": tag.name,
            "color_token": tag.color_token, "created": True}


@router.delete("/tags/{tag_id}", status_code=204)
def delete_tag(
    tag_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    enforce_rate_limit(str(current_user.id), "crm_write", _WRITE_LIMIT)

    tag = db.get(CrmTag, tag_id)
    if tag is None or tag.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="tag not found")
    # Detach explicitly rather than leaning on ON DELETE CASCADE.
    #
    # The FK does declare the cascade, and PostgreSQL honours it -- but SQLite
    # does not enforce foreign keys unless PRAGMA foreign_keys=ON, and the test
    # suite runs on SQLite with it off. So the cascade path is exercised
    # nowhere except production, which is the worst place to find out it was
    # wrong. Deleting the associations here makes the behaviour identical on
    # both dialects and leaves the FK cascade as a backstop rather than the
    # mechanism.
    db.execute(delete(CrmLeadTag).where(CrmLeadTag.tag_id == tag.id))
    db.delete(tag)
    db.commit()


@router.post("/leads/{lead_id}/tags/{tag_id}", status_code=201)
def add_tag_to_lead(
    lead_id: uuid.UUID,
    tag_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    enforce_rate_limit(str(current_user.id), "crm_write", _WRITE_LIMIT)

    lead = crm_service.owned_lead(db, lead_id, current_user)
    tag = db.get(CrmTag, tag_id)
    if tag is None or tag.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="tag not found")
    exists = db.execute(
        select(CrmLeadTag).where(CrmLeadTag.lead_id == lead.id,
                                 CrmLeadTag.tag_id == tag.id)
    ).scalars().first()
    if exists is not None:
        return {"lead_id": str(lead.id), "tag_id": str(tag.id), "created": False}
    db.add(CrmLeadTag(lead_id=lead.id, tag_id=tag.id))
    crm_service.log_activity(db, lead, CrmActivityKind.TAG_ADDED,
                             actor=current_user, to_value=tag.name)
    db.commit()
    return {"lead_id": str(lead.id), "tag_id": str(tag.id), "created": True}


@router.delete("/leads/{lead_id}/tags/{tag_id}", status_code=204)
def remove_tag_from_lead(
    lead_id: uuid.UUID,
    tag_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    enforce_rate_limit(str(current_user.id), "crm_write", _WRITE_LIMIT)

    lead = crm_service.owned_lead(db, lead_id, current_user)
    tag = db.get(CrmTag, tag_id)
    if tag is None or tag.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="tag not found")
    row = db.execute(
        select(CrmLeadTag).where(CrmLeadTag.lead_id == lead.id,
                                 CrmLeadTag.tag_id == tag.id)
    ).scalars().first()
    if row is not None:
        db.delete(row)
        crm_service.log_activity(db, lead, CrmActivityKind.TAG_REMOVED,
                                 actor=current_user, from_value=tag.name)
        db.commit()


# ---------------------------------------------------------------------------
# Activity (per lead)
# ---------------------------------------------------------------------------


@router.get("/leads/{lead_id}/activity")
def lead_activity(
    lead_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=200),
    before: str | None = Query(default=None, max_length=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    return crm_service.dashboard_activity(
        db, current_user, lead_id=lead_id, limit=limit, before=before
    )


# ---------------------------------------------------------------------------
# Saved views
# ---------------------------------------------------------------------------


def _view_out(view: CrmSavedView) -> dict:
    return {
        "id": str(view.id),
        "name": view.name,
        "view_type": view.view_type.value,
        "filters_json": view.filters_json,
        "sort_json": view.sort_json,
        "columns_json": view.columns_json,
        "is_default": view.is_default,
        "created_at": view.created_at.isoformat() if view.created_at else None,
    }


def _clear_other_defaults(db: Session, user: User, view_type: CrmViewType,
                          keep_id: uuid.UUID | None) -> None:
    """At most one default per (user, view_type).

    Enforced here rather than by a partial unique index: SQLite and
    PostgreSQL both support one, but with divergent syntax that would need
    two dialect branches in the migration to express a rule that is one
    UPDATE at the call site.
    """
    others = db.execute(
        select(CrmSavedView).where(
            CrmSavedView.user_id == user.id,
            CrmSavedView.view_type == view_type,
            CrmSavedView.is_default.is_(True),
        )
    ).scalars().all()
    for other in others:
        if keep_id is None or other.id != keep_id:
            other.is_default = False


@router.get("/views")
def list_views(
    view_type: CrmViewType | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    query = select(CrmSavedView).where(CrmSavedView.user_id == current_user.id)
    if view_type is not None:
        query = query.where(CrmSavedView.view_type == view_type)
    rows = db.execute(query.order_by(CrmSavedView.name)).scalars().all()
    return {"items": [_view_out(view) for view in rows]}


@router.post("/views", status_code=201)
def create_view(
    body: SavedViewIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    enforce_rate_limit(str(current_user.id), "crm_write", _WRITE_LIMIT)

    clash = db.execute(
        select(CrmSavedView).where(CrmSavedView.user_id == current_user.id,
                                   CrmSavedView.view_type == body.view_type,
                                   CrmSavedView.name == body.name)
    ).scalars().first()
    if clash is not None:
        raise HTTPException(status_code=409,
                            detail=f"a {body.view_type.value} view named "
                                   f"'{body.name}' already exists")
    view = CrmSavedView(
        user_id=current_user.id, name=body.name, view_type=body.view_type,
        filters_json=body.filters_json, sort_json=body.sort_json,
        columns_json=body.columns_json, is_default=body.is_default,
    )
    db.add(view)
    db.flush()
    if body.is_default:
        _clear_other_defaults(db, current_user, body.view_type, view.id)
    db.commit()
    db.refresh(view)
    return _view_out(view)


@router.put("/views/{view_id}")
def update_view(
    view_id: uuid.UUID,
    body: SavedViewIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    enforce_rate_limit(str(current_user.id), "crm_write", _WRITE_LIMIT)

    view = db.get(CrmSavedView, view_id)
    if view is None or view.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="view not found")
    view.name = body.name
    view.view_type = body.view_type
    view.filters_json = body.filters_json
    view.sort_json = body.sort_json
    view.columns_json = body.columns_json
    view.is_default = body.is_default
    if body.is_default:
        _clear_other_defaults(db, current_user, body.view_type, view.id)
    db.commit()
    db.refresh(view)
    return _view_out(view)


@router.delete("/views/{view_id}", status_code=204)
def delete_view(
    view_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    enforce_rate_limit(str(current_user.id), "crm_write", _WRITE_LIMIT)

    view = db.get(CrmSavedView, view_id)
    if view is None or view.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="view not found")
    db.delete(view)
    db.commit()


# ---------------------------------------------------------------------------
# Custom fields
# ---------------------------------------------------------------------------


@router.get("/fields")
def list_fields(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    rows = db.execute(
        select(CrmCustomField).where(CrmCustomField.user_id == current_user.id)
        .order_by(CrmCustomField.sort_order, CrmCustomField.label)
    ).scalars().all()
    return {"items": [
        {"id": str(field.id), "key": field.key, "label": field.label,
         "field_type": field.field_type.value,
         "options_json": field.options_json, "sort_order": field.sort_order}
        for field in rows
    ]}


@router.post("/fields", status_code=201)
def create_field(
    body: CustomFieldIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    enforce_rate_limit(str(current_user.id), "crm_write", _WRITE_LIMIT)

    if body.field_type is CrmFieldType.SELECT and not body.options_json:
        raise HTTPException(status_code=422,
                            detail="a select field requires options_json")
    clash = db.execute(
        select(CrmCustomField).where(CrmCustomField.user_id == current_user.id,
                                     CrmCustomField.key == body.key)
    ).scalars().first()
    if clash is not None:
        raise HTTPException(status_code=409,
                            detail=f"field '{body.key}' already exists")
    field = CrmCustomField(
        user_id=current_user.id, key=body.key, label=body.label,
        field_type=body.field_type, options_json=body.options_json,
        sort_order=body.sort_order,
    )
    db.add(field)
    db.commit()
    db.refresh(field)
    return {"id": str(field.id), "key": field.key, "label": field.label,
            "field_type": field.field_type.value,
            "options_json": field.options_json,
            "sort_order": field.sort_order}


@router.delete("/fields/{field_id}", status_code=204)
def delete_field(
    field_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    """Deletes the definition AND every value stored under it.

    There is no soft-delete: a hidden field that still owns data is a field
    the user cannot see, cannot edit and cannot get rid of.

    The values are removed explicitly for the same reason as in delete_tag --
    SQLite does not enforce the FK cascade, so leaning on it would leave the
    behaviour untested everywhere except production.
    """
    enforce_rate_limit(str(current_user.id), "crm_write", _WRITE_LIMIT)

    field = db.get(CrmCustomField, field_id)
    if field is None or field.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="field not found")
    db.execute(
        delete(CrmCustomFieldValue)
        .where(CrmCustomFieldValue.field_id == field.id)
    )
    db.delete(field)
    db.commit()


# ---------------------------------------------------------------------------
# Real-time stream (SSE)
# ---------------------------------------------------------------------------

# Ticket TTL. Long enough to survive a slow page load between minting and
# connecting, short enough that a ticket leaked through a log or a Referer
# header is useless by the time anyone reads it.
STREAM_TICKET_TTL_SECONDS = 60
_TICKET_PREFIX = "crm:stream:ticket:"


def _monotonic() -> float:
    """Wall-clock source for the connection cap, as a seam.

    Not inlined as `asyncio.get_event_loop().time()` so that the cap can be
    tested deterministically. The underlying clock is time.monotonic(), whose
    resolution on Windows is ~15.6ms -- two calls in the same tick return an
    identical value, so a test that sets the cap low and waits for it to fire
    is a race against the clock granularity rather than an assertion about
    the code.
    """
    return asyncio.get_event_loop().time()


@router.post("/stream/ticket")
def create_stream_ticket(
    current_user: User = Depends(get_current_user),
) -> dict:
    """Mint a single-use ticket for GET /crm/stream.

    WHY A TICKET AND NOT THE BEARER TOKEN

    `EventSource` cannot set request headers -- that is a limitation of the
    browser API, not a choice here -- so the credential has to travel in the
    URL. Putting the access token there writes a JWT valid for the next hour
    into the server access log, the browser history, and any Referer sent by
    a page the stream URL appears on.

    A ticket is random, single-use, expires in 60 seconds, and grants exactly
    one capability: read this user's CRM event channel. Leaking one costs
    nothing meaningful.

    Not rate-limited as a write because it writes no application data, and
    limiting it would break the reconnect path the moment a flaky mobile
    connection needs several tickets in a row -- which is exactly when the
    feature has to keep working.
    """
    ticket = secrets.token_urlsafe(32)
    try:
        from app.core.redis_client import get_sync_redis

        get_sync_redis().setex(
            f"{_TICKET_PREFIX}{ticket}", STREAM_TICKET_TTL_SECONDS,
            str(current_user.id),
        )
    except Exception as exc:
        # No Redis means no pub/sub either, so the stream could not deliver
        # anything. Say so plainly instead of handing out a ticket that will
        # be rejected a moment later; the client falls back to polling.
        logger.warning("crm.stream_ticket_failed", error=str(exc))
        raise HTTPException(
            status_code=503,
            detail="real-time stream unavailable; the client should poll",
        )
    return {"ticket": ticket, "expires_in": STREAM_TICKET_TTL_SECONDS}


def _redeem_ticket(ticket: str) -> str:
    """Exchange a ticket for a user id, consuming it. Raises 401 if invalid.

    GETDEL, not GET-then-DELETE: two commands leave a window where the same
    ticket can be redeemed twice concurrently.
    """
    from app.core.redis_client import get_sync_redis

    try:
        user_id = get_sync_redis().getdel(f"{_TICKET_PREFIX}{ticket}")
    except Exception as exc:
        logger.warning("crm.stream_redeem_failed", error=str(exc))
        raise HTTPException(status_code=503,
                            detail="real-time stream unavailable")
    if not user_id:
        raise HTTPException(status_code=401,
                            detail="invalid or expired stream ticket")
    return user_id


@router.get("/stream")
async def crm_stream(request: Request, ticket: str = Query(...)) -> StreamingResponse:
    """Server-Sent Events: this user's CRM changes, as they commit.

    UNGATED BY PLAN, deliberately. A subscription costs one Redis connection,
    and the React Query poll fallback would hand a free-tier user the same
    data thirty seconds later anyway -- so a plan gate here would buy no
    saving and cost the free tier a worse product. The cost controls that do
    apply are the per-user channel and MAX_STREAM_SECONDS below.

    NOT `Depends(get_current_user)`: EventSource cannot send an
    Authorization header. Authentication is the ticket minted by
    POST /crm/stream/ticket, which is single-use and expires in 60s.

    SSE rather than WebSocket: every event here travels server -> client, the
    browser reconnects on its own, and it is plain HTTP -- so it passes
    through the same nginx/Railway path as the rest of the API with no
    upgrade handling to configure.
    """
    from app.config import settings

    max_seconds = int(getattr(settings, "crm_stream_max_seconds", 3600))
    if max_seconds <= 0:
        # KILL SWITCH, matching support_chat_enabled: a non-positive cap turns
        # the stream off without a deploy. It refuses the connection rather
        # than accepting one and immediately asking for a reconnect -- that
        # would put every open tab into a tight connect/disconnect loop, which
        # is worse than no real-time at all. 503 is the signal the client
        # already handles by falling back to polling.
        raise HTTPException(
            status_code=503,
            detail="real-time stream disabled; the client should poll",
        )

    user_id = _redeem_ticket(ticket)
    keepalive_seconds = 20

    async def event_stream():
        from app.core.redis_client import get_redis

        redis = get_redis()
        pubsub = redis.pubsub()
        await pubsub.subscribe(crm_events.channel_for(user_id))
        started = _monotonic()
        try:
            # Tells the client the stream is live before any event arrives, so
            # the UI can show "connected" rather than sitting in an
            # indeterminate state until the first change happens to occur.
            yield f"event: ready\ndata: {json.dumps({'user_id': user_id})}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                if _monotonic() - started > max_seconds:
                    # Ask the client to reconnect rather than holding a worker
                    # slot for a tab someone left open over a weekend.
                    yield "event: reconnect\ndata: {}\n\n"
                    break
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=keepalive_seconds,
                )
                if message is None:
                    # A comment line: keeps proxies and mobile radios from
                    # deciding an idle connection is a dead one.
                    yield ": keepalive\n\n"
                    continue
                payload = message.get("data")
                if not payload:
                    continue
                try:
                    parsed = json.loads(payload)
                    name = parsed.get("event", "message")
                    data = json.dumps(parsed.get("data", {}))
                except (TypeError, ValueError):
                    continue
                yield f"event: {name}\ndata: {data}\n\n"
        except asyncio.CancelledError:  # client went away mid-await
            raise
        finally:
            try:
                await pubsub.unsubscribe()
                # redis-py renamed PubSub.close() to aclose() in 5.0.1 and
                # deprecated the old name. The pin is >=5.0,<6.0, which spans
                # the rename, so prefer the new name and fall back.
                closer = getattr(pubsub, "aclose", None) or pubsub.close
                await closer()
            except Exception:  # already gone; nothing to clean up
                pass

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            # nginx buffers proxied responses by default, which holds every
            # event until the buffer fills -- turning a live feed into a
            # batch delivery. nginx/ has a proxy_buffering directive for the
            # deployed path; this header covers any proxy that honours it.
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ---------------------------------------------------------------------------
# Feature 2 — reply intelligence
# ---------------------------------------------------------------------------
#
# The reply row itself is written by whichever channel received it (the Gmail
# poller, the WhatsApp webhook); these two routes are the
# only place a human touches the AI's reading of it.
#
# NOTHING HERE SENDS ANYTHING. `approved_draft` puts the draft in front of a
# person -- an activity row on the lead's timeline, a note holding the copy,
# and a notification -- it does not put it in an outbox. An outbound system
# that answers a human by itself is one bad classification away from an
# apology, so approval is a handoff, not a trigger.


class ReplyIntelligenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    reply_id: uuid.UUID
    lead_id: uuid.UUID | None
    # NULL on all five: the reply has not been classified (yet, or at all).
    # That is a different fact from "classified as nothing".
    category: str | None
    confidence: float | None
    next_action: str | None
    draft_response: str | None
    reschedule_date: date | None
    classified_at: datetime | None


class ReplyIntelligenceIn(BaseModel):
    reschedule_date: date | None = None
    approved_draft: bool | None = None


def _owned_reply(db: Session, reply_id: uuid.UUID, current_user: User) -> tuple:
    """(reply, lead) for a reply this user owns, else 404.

    An UNMATCHED reply (lead_id NULL -- an inbound the poller could not tie to
    any lead) is 404 for everybody: it has no owner, so there is no account it
    can safely be shown to. Ownership of a matched reply is the lead's, via
    the same lead -> strategy -> product -> user chain the rest of this router
    walks, and answers 404 rather than 403 so existence cannot be inferred.
    """
    reply = db.get(InboundReply, reply_id)
    if reply is None or reply.lead_id is None:
        raise HTTPException(status_code=404, detail="reply not found")
    lead = crm_service.owned_lead(db, reply.lead_id, current_user)
    return reply, lead


def _intelligence_out(reply: InboundReply) -> ReplyIntelligenceOut:
    return ReplyIntelligenceOut(
        reply_id=reply.id,
        lead_id=reply.lead_id,
        category=reply.reply_category,
        confidence=reply.category_confidence,
        next_action=reply.ai_next_action,
        draft_response=reply.ai_draft_response,
        reschedule_date=reply.reschedule_date,
        classified_at=reply.classified_at,
    )


# ---------------------------------------------------------------------------
# Feature A3: the reply inbox with real-time authenticity
# ---------------------------------------------------------------------------


def _authenticity_out(reply: InboundReply, lead: Lead | None = None) -> dict:
    scored = reply.authenticity_kind is not None
    return {
        "reply_id": str(reply.id),
        "lead": ({"id": str(lead.id), "full_name": lead.full_name, "company": lead.company,
                  "status": lead.status.value if lead.status else None} if lead else None),
        "channel": reply.channel, "from_address": reply.from_address,
        "subject": reply.subject, "body_preview": (reply.body or "")[:280],
        "classification": reply.classification,
        "reply_category": reply.reply_category,
        "received_at": (reply.received_at or reply.created_at).isoformat()
        if (reply.received_at or reply.created_at) else None,
        "authenticity": {
            "kind": reply.authenticity_kind,
            "authenticity_score": reply.authenticity_score,
            "buyer_intent_score": reply.buyer_intent_score,
            "confidence": reply.authenticity_confidence,
            "signals": reply.authenticity_signals_json or [],
            "scored_at": reply.authenticity_scored_at.isoformat()
            if reply.authenticity_scored_at else None,
        } if scored else None,
        # Part 1 Feature 1. Always present (never None), because "not
        # classified" is a state the inbox has to be able to show.
        "intent": reply_intent.intent_out(reply),
    }


@router.get("/replies")
def list_replies(
    kind: str | None = Query(default=None,
                             pattern="^(genuine|out_of_office|auto_responder|bot|bounce|automated)$"),
    min_intent: float | None = Query(default=None, ge=0.0, le=1.0),
    channel: str | None = Query(default=None, max_length=20),
    sort: str = Query(default="newest", pattern="^(newest|intent)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Every reply on the account's leads, with its authenticity. `kind=automated`
    is every non-genuine kind together -- the "noise" view."""
    from sqlalchemy import func as _func, select as _select  # noqa: PLC0415

    from app.db.models import Product, Strategy  # noqa: PLC0415

    query = (_select(InboundReply, Lead)
             .join(Lead, Lead.id == InboundReply.lead_id)
             .join(Strategy, Strategy.id == Lead.strategy_id)
             .join(Product, Product.id == Strategy.product_id)
             .where(Product.user_id == current_user.id))
    if kind == "automated":
        query = query.where(InboundReply.authenticity_kind.in_(
            ("out_of_office", "auto_responder", "bot", "bounce")))
    elif kind:
        query = query.where(InboundReply.authenticity_kind == kind)
    if min_intent is not None:
        query = query.where(InboundReply.buyer_intent_score >= min_intent)
    if channel:
        query = query.where(InboundReply.channel == channel)
    total = db.execute(_select(_func.count()).select_from(query.subquery())).scalar_one()
    order = ((InboundReply.buyer_intent_score.desc(), InboundReply.created_at.desc())
             if sort == "intent" else (InboundReply.created_at.desc(),))
    rows = db.execute(query.order_by(*order).offset(offset).limit(limit)).all()
    return {"total": int(total), "limit": limit, "offset": offset,
            "items": [_authenticity_out(reply, lead) for reply, lead in rows]}


@router.get("/replies/{reply_id}/authenticity")
def get_reply_authenticity(reply_id: uuid.UUID, db: Session = Depends(get_db),
                           current_user: User = Depends(get_current_user)) -> dict:
    reply, lead = _owned_reply(db, reply_id, current_user)
    return _authenticity_out(reply, lead)


@router.post("/replies/{reply_id}/authenticity/rescore")
def rescore_reply_authenticity(reply_id: uuid.UUID, db: Session = Depends(get_db),
                               current_user: User = Depends(get_current_user)) -> dict:
    """Re-run the deterministic scorer -- for replies stored before Feature A3,
    or after its rules change. No model call, so it is not rate limited."""
    from app.services import reply_authenticity  # noqa: PLC0415

    reply, lead = _owned_reply(db, reply_id, current_user)
    reply_authenticity.apply(db, reply)
    db.commit()
    return _authenticity_out(reply, lead)


@router.get("/replies/{reply_id}/intent")
def get_reply_intent(reply_id: uuid.UUID, db: Session = Depends(get_db),
                     current_user: User = Depends(get_current_user)) -> dict:
    """Part 1 Feature 1: was this reply positive, and how sure are we?"""
    reply, _lead = _owned_reply(db, reply_id, current_user)
    return reply_intent.intent_out(reply)


@router.post("/replies/{reply_id}/intent/reclassify")
def reclassify_reply_intent(reply_id: uuid.UUID, db: Session = Depends(get_db),
                            current_user: User = Depends(get_current_user)) -> dict:
    """Re-run the intent classifier over one reply.

    For replies stored before this feature, and for the case a person
    disagrees with the label. Costs one model call unless the rules decide
    it, so it is rate limited with the other write paths."""
    enforce_rate_limit(str(current_user.id), "crm_write", _WRITE_LIMIT)
    reply, _lead = _owned_reply(db, reply_id, current_user)
    with metered(db, current_user, "reply_handling"):  # counted, never capped
        outcome = reply_intent.classify_and_apply(db, reply, force=True)
    if outcome["status"] == "failed":
        raise HTTPException(status_code=503,
                            detail="the classifier could not be reached — the reply is unchanged")
    return reply_intent.intent_out(reply)


@router.get("/replies/{reply_id}/intelligence", response_model=ReplyIntelligenceOut)
def get_reply_intelligence(
    reply_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ReplyIntelligenceOut:
    """What the AI made of one inbound reply: category, next action, draft.

    Every field is nullable. A reply whose classification task has not run yet
    (or failed) returns nulls rather than a placeholder category -- the UI must
    be able to show "not classified" honestly.
    """
    reply, _lead = _owned_reply(db, reply_id, current_user)
    return _intelligence_out(reply)


@router.patch("/replies/{reply_id}/intelligence", response_model=ReplyIntelligenceOut)
def patch_reply_intelligence(
    reply_id: uuid.UUID,
    body: ReplyIntelligenceIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ReplyIntelligenceOut:
    """Move a NOT_NOW follow-up date, and/or approve the draft for sending.

    `reschedule_date` is how 30 days becomes 60 or 90. It may not be in the
    past: a follow-up date behind today is either a typo or a way to make the
    lead look due forever, and neither is worth accepting silently.

    `approved_draft=true` hands the draft to a human, in three places at once:
    an activity row on the lead's timeline, a note holding the copy itself so
    it is editable where the user already edits copy, and a notification
    through the hub. It does NOT send, schedule or enqueue an outbound
    message -- see the note at the top of this section.
    """
    enforce_rate_limit(str(current_user.id), "crm_write", _WRITE_LIMIT)
    reply, lead = _owned_reply(db, reply_id, current_user)

    if body.reschedule_date is None and body.approved_draft is None:
        raise HTTPException(status_code=422,
                            detail="give reschedule_date, approved_draft, or both")

    if body.reschedule_date is not None:
        if body.reschedule_date < datetime.now(timezone.utc).date():
            raise HTTPException(status_code=422,
                                detail="reschedule_date must not be in the past")
        previous = reply.reschedule_date
        reply.reschedule_date = body.reschedule_date
        crm_service.log_activity(
            db, lead, CrmActivityKind.FIELD_CHANGED, actor=current_user,
            from_value=previous.isoformat() if previous else None,
            to_value=body.reschedule_date.isoformat(),
            meta={"field": "reply.reschedule_date", "reply_id": str(reply.id)},
        )

    approved = False
    if body.approved_draft:
        if not (reply.ai_draft_response or "").strip():
            raise HTTPException(status_code=409,
                                detail="this reply has no draft to approve")
        db.add(CrmNote(
            lead_id=lead.id, author_user_id=current_user.id,
            body=("Approved reply draft "
                  f"({reply.reply_category or 'uncategorised'}):\n\n"
                  f"{reply.ai_draft_response}"),
        ))
        crm_service.log_activity(
            db, lead, CrmActivityKind.TASK_CREATED, actor=current_user,
            to_value="reply draft approved",
            meta={"reply_id": str(reply.id), "category": reply.reply_category,
                  "next_action": reply.ai_next_action},
        )
        approved = True

    db.commit()

    if approved:
        from app.workers import notification_tasks  # noqa: PLC0415

        notification_tasks.enqueue_event(
            current_user.id, "reply_draft_approved", push=True, slack=True,
            title="Reply draft ready to send",
            body=f"{lead.full_name or lead.email or 'A lead'} - "
                 f"{reply.reply_category or 'reply'}",
            deep_link=f"/crm/leads/{lead.id}",
            webhook_payload={"reply_id": str(reply.id), "lead_id": str(lead.id),
                             "category": reply.reply_category,
                             "next_action": reply.ai_next_action,
                             "draft_response": reply.ai_draft_response},
        )

    return _intelligence_out(reply)
