"""
Admin API — M8-C3 base + M8-C5 additions.

New endpoints:
  GET  /admin/rate-limits/{user_id}        — current Redis counters
  POST /admin/rate-limits/reset/{user_id}  — clear counters
  GET  /admin/webhook-deliveries           — delivery log with retry detail
  POST /admin/users/{id}/plan             — update user plan (with audit log)

All others from M8-C3 unchanged.
"""
from __future__ import annotations

import uuid
from typing import Any, Optional
from datetime import datetime, timezone as datetime_timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.db.models import User
from app.services import tutorials as catalogue
from app.core.database import get_db
from app.core.logging import get_logger
from app.services.admin_service import AdminService
from app.core.circuit_breaker import get_all_states, _registry as cb_registry
from app.workers.monitoring import get_celery_stats
from app.core.plans import PLANS
from app.core.rate_limiting import get_user_rate_limit_state, reset_user_rate_limits

logger = get_logger("api.admin")
router = APIRouter(prefix="/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class UserSummary(BaseModel):
    id: str
    email: str
    is_admin: bool
    is_suspended: bool
    plan: Optional[str]
    created_at: Optional[datetime]
    strategy_count: int
    active_campaign_count: int
    last_active_at: Optional[datetime]
    # Where the account came from (app/services/signup_watch.py). None for
    # accounts created before the identity step (migration 0039).
    signup_ip: Optional[str] = None
    signup_country: Optional[str] = None
    declared_country: Optional[str] = None
    company_name: Optional[str] = None
    company_country: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


class SuspendRequest(BaseModel):
    reason: str = ""


class TaskErrorSummary(BaseModel):
    id: str
    task_name: str
    task_id: Optional[str]
    error_type: Optional[str]
    error_message: Optional[str]
    args_summary: Optional[str]
    ts: datetime
    resolved_at: Optional[datetime]
    resolved_by: Optional[str]
    resolution_note: Optional[str]
    model_config = ConfigDict(from_attributes=True)


class TaskErrorResolveRequest(BaseModel):
    resolution_note: str


class SuppressionEntry(BaseModel):
    # `Optional[str]` WITHOUT a default is still a required field in pydantic
    # v2 - it only permits the value None. Both were therefore mandatory, so
    # the admin UI's addSuppression({email, reason}) got
    # "phone: Field required" 422 on every call. Suppression needs either an
    # email or a phone (the model enforces that with a CheckConstraint), so
    # both default to None.
    email: Optional[str] = None
    phone: Optional[str] = None
    reason: str
    source: str = "admin"


class PlanUpdateRequest(BaseModel):
    plan: str
    reason: str = ""


# ---------------------------------------------------------------------------
# User management
# ---------------------------------------------------------------------------

@router.get("/users", response_model=list[UserSummary])
async def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
) -> list[Any]:
    return AdminService.list_users(db, page=page, page_size=page_size)


@router.get("/users/{user_id}", response_model=UserSummary)
async def get_user_detail(
    user_id: str,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
) -> Any:
    user = AdminService.get_user_detail(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.post("/users/{user_id}/suspend")
async def suspend_user(
    user_id: str,
    body: SuspendRequest,
    db: Session = Depends(get_db),
    admin=Depends(require_admin),
) -> dict:
    success = AdminService.suspend_user(db, user_id=user_id, reason=body.reason, suspended_by=admin.email)
    if not success:
        raise HTTPException(status_code=404, detail="User not found")
    logger.info("admin.user_suspended", user_id=user_id, by=admin.email)
    return {"status": "suspended", "user_id": user_id}


@router.post("/users/{user_id}/unsuspend")
async def unsuspend_user(
    user_id: str,
    db: Session = Depends(get_db),
    admin=Depends(require_admin),
) -> dict:
    success = AdminService.unsuspend_user(db, user_id=user_id, resumed_by=admin.email)
    if not success:
        raise HTTPException(status_code=404, detail="User not found")
    return {"status": "active", "user_id": user_id}


@router.post("/users/{user_id}/plan")
async def update_user_plan(
    user_id: str,
    body: PlanUpdateRequest,
    db: Session = Depends(get_db),
    admin=Depends(require_admin),
) -> dict:
    """Update user plan. Validates against PLANS. Writes audit log entry."""
    if body.plan not in PLANS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown plan '{body.plan}'. Valid plans: {list(PLANS.keys())}",
        )
    from sqlalchemy import text
    import datetime
    row = db.execute(text("SELECT id, email FROM users WHERE id = :uid"), {"uid": user_id}).first()
    if not row:
        raise HTTPException(status_code=404, detail="User not found")

    db.execute(text("UPDATE users SET plan = :plan, updated_at = NOW() WHERE id = :uid"),
               {"plan": body.plan, "uid": user_id})

    # Audit log
    db.execute(text("""
        INSERT INTO task_errors (task_name, task_id, args_summary, error_type, error_message, ts)
        VALUES ('admin.plan_update', :uid, :summary, 'AuditLog', :msg, NOW())
    """), {
        "uid": user_id,
        "summary": f"user_id={user_id}",
        "msg": f"Plan changed to '{body.plan}' by {admin.email}. Reason: {body.reason}",
    })
    db.commit()
    logger.info("admin.plan_updated", user_id=user_id, plan=body.plan, by=admin.email)
    return {"status": "updated", "user_id": user_id, "plan": body.plan}


# ---------------------------------------------------------------------------
# Suppression list
# ---------------------------------------------------------------------------

@router.get("/suppression-list")
async def list_suppression(
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
) -> dict:
    items, total = AdminService.list_suppression(db, page=page, page_size=page_size)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.post("/suppression-list", status_code=201)
async def add_to_suppression(
    entry: SuppressionEntry,
    db: Session = Depends(get_db),
    admin=Depends(require_admin),
) -> dict:
    AdminService.add_suppression(db, email=entry.email, phone=entry.phone,
                                 reason=entry.reason, source="admin", added_by=admin.email)
    return {"status": "added"}


@router.delete("/suppression-list/{email}")
async def remove_from_suppression(
    email: str,
    db: Session = Depends(get_db),
    admin=Depends(require_admin),
) -> dict:
    removed = AdminService.remove_suppression(db, email=email, removed_by=admin.email)
    if not removed:
        raise HTTPException(status_code=404, detail="Email not in suppression list")
    return {"status": "removed", "email": email}


# ---------------------------------------------------------------------------
# Task errors
# ---------------------------------------------------------------------------

@router.get("/task-errors", response_model=list[TaskErrorSummary])
async def list_task_errors(
    unresolved_only: bool = Query(True),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
) -> list[Any]:
    return AdminService.list_task_errors(db, unresolved_only=unresolved_only, page=page, page_size=page_size)


@router.post("/task-errors/{error_id}/resolve")
async def resolve_task_error(
    error_id: str,
    body: TaskErrorResolveRequest,
    db: Session = Depends(get_db),
    admin=Depends(require_admin),
) -> dict:
    success = AdminService.resolve_task_error(db, error_id=error_id, resolved_by=admin.email, note=body.resolution_note)
    if not success:
        raise HTTPException(status_code=404, detail="Task error not found")
    return {"status": "resolved", "error_id": error_id}


# ---------------------------------------------------------------------------
# Circuit breakers
# ---------------------------------------------------------------------------

@router.get("/circuit-breakers")
async def list_circuit_breakers(_admin=Depends(require_admin)) -> list[dict]:
    return get_all_states()


@router.post("/circuit-breakers/{provider}/reset")
async def reset_circuit_breaker(provider: str, admin=Depends(require_admin)) -> dict:
    cb = cb_registry.get(provider)
    if not cb:
        raise HTTPException(status_code=404, detail=f"No circuit breaker for '{provider}'")
    cb.record_success()
    logger.info("admin.circuit_breaker_reset", provider=provider, by=admin.email)
    return {"status": "reset", "provider": provider, "new_state": "CLOSED"}


# ---------------------------------------------------------------------------
# Playbook
# ---------------------------------------------------------------------------

@router.get("/playbook/scores")
async def list_playbook_scores(
    user_id: Optional[uuid.UUID] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    degrading_only: bool = Query(False, description="Show only patterns where effective_n < min_sample"),
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
) -> dict:
    """Returns all playbook scores, including decay metadata. Supports degrading_only filter.

    F-DB-01 (migration 0093): rows are owned (playbook_scores.user_id), so
    ?user_id= returns that account's own rows. Rows written before 0093 have
    user_id NULL (pooled across accounts); they appear only in the unfiltered
    list, with user_id null. A DB error propagates as a 500 -- an empty list
    here must mean "no data", never "the query failed".
    """
    from app.core.config import settings
    from app.db.models import PlaybookScore

    ps = PlaybookScore
    min_sample = settings.PLAYBOOK_MIN_SAMPLE
    stmt = select(
        ps.user_id, ps.pattern_key, ps.variant, ps.reply_rate, ps.booking_rate,
        ps.sample_size, ps.effective_sample_size, ps.is_reliable,
        ps.decay_half_life_days, ps.oldest_outcome_ts, ps.trend,
        ps.last_aggregated_at,
    )
    if user_id:
        stmt = stmt.where(ps.user_id == user_id)
    if degrading_only:
        stmt = stmt.where(ps.effective_sample_size < min_sample, ps.sample_size >= min_sample)
    stmt = (stmt.order_by(ps.last_aggregated_at.desc().nulls_last())
            .limit(page_size).offset((page - 1) * page_size))

    rows = db.execute(stmt).mappings().all()
    return {"scores": [dict(r) for r in rows]}


@router.post("/playbook/recompute")
async def recompute_playbook(admin=Depends(require_admin)) -> dict:
    from app.workers.learning_tasks import run_strategy_aggregation
    run_strategy_aggregation.delay()
    logger.info("admin.playbook_recompute_triggered", by=admin.email)
    return {"status": "triggered"}


# ---------------------------------------------------------------------------
# Rate limiting (NEW — C5)
# ---------------------------------------------------------------------------

@router.get("/rate-limits/{user_id}")
async def get_rate_limits(
    user_id: str,
    _admin=Depends(require_admin),
) -> dict:
    """Current Redis rate-limit counters for a user."""
    return {"user_id": user_id, "limits": get_user_rate_limit_state(user_id)}


@router.post("/rate-limits/reset/{user_id}")
async def reset_rate_limits(
    user_id: str,
    admin=Depends(require_admin),
) -> dict:
    """Clear all rate-limit counters for a user (support action)."""
    deleted = reset_user_rate_limits(user_id)
    logger.info("admin.rate_limits_reset", user_id=user_id, keys_deleted=deleted, by=admin.email)
    return {"status": "reset", "user_id": user_id, "keys_deleted": deleted}


# ---------------------------------------------------------------------------
# Webhook deliveries (NEW — C5)
# ---------------------------------------------------------------------------

@router.get("/webhook-deliveries")
async def list_webhook_deliveries(
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
) -> list[dict]:
    """Outbound webhook delivery log with retry counts and error details."""
    from app.services.webhook_delivery import get_deliveries
    return get_deliveries(db, status=status, limit=limit)


# ---------------------------------------------------------------------------
# Celery stats
# ---------------------------------------------------------------------------

class TutorialIn(BaseModel):
    """Create payload. `slug` is required and permanent."""

    slug: str = Field(min_length=3, max_length=100,
                      pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    title: str = Field(min_length=3, max_length=200)
    description: str = Field(min_length=10)
    level: str
    youtube_id: Optional[str] = Field(default=None, max_length=32)
    duration_seconds: Optional[int] = Field(default=None, ge=1, le=86_400)
    sort_order: int = 0
    is_published: bool = False


class TutorialPatch(BaseModel):
    """Update payload. Every field optional; `slug` is NOT among them.

    Renaming a slug orphans every tutorial_progress row that points at it, and
    there is no foreign key to stop it (see migration 0018). Making the field
    absent from this model means the API cannot do it at all, rather than
    relying on a validator somebody later relaxes.
    """

    title: Optional[str] = Field(default=None, min_length=3, max_length=200)
    description: Optional[str] = Field(default=None, min_length=10)
    level: Optional[str] = None
    # Explicit sentinel handling: `youtube_id: null` means "clear it", which is
    # different from omitting the field. See _apply_patch.
    youtube_id: Optional[str] = Field(default=None, max_length=32)
    duration_seconds: Optional[int] = Field(default=None, ge=1, le=86_400)
    sort_order: Optional[int] = None
    is_published: Optional[bool] = None


def _tutorial_admin_out(row) -> dict:
    from app.services import tutorials as catalogue

    return {**catalogue.tutorial_out(row), "id": str(row.id)}


def _get_catalogue_row(db: Session, slug: str):
    from app.db.models import TutorialCatalogue

    row = db.query(TutorialCatalogue).filter(
        TutorialCatalogue.slug == slug).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Tutorial not found")
    return row


@router.get("/tutorials")
def admin_list_tutorials(
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> dict:
    """Every tutorial, published or not, in display order."""
    from app.services import tutorials as catalogue

    rows = catalogue.list_tutorials(db, include_unpublished=True)
    return {
        "tutorials": [_tutorial_admin_out(r) for r in rows],
        "levels": [{"level": lv, "label": catalogue.LEVEL_LABELS[lv]}
                   for lv in catalogue.LEVELS],
        "published_count": sum(1 for r in rows if r.is_published),
        "total_count": len(rows),
    }


@router.post("/tutorials", status_code=201)
def admin_create_tutorial(
    payload: TutorialIn,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> dict:
    from app.db.models import TutorialCatalogue
    from app.services import tutorials as catalogue

    if not catalogue.is_valid_level(payload.level):
        raise HTTPException(status_code=422,
                            detail=f"level must be one of {catalogue.LEVELS}")
    if db.query(TutorialCatalogue).filter(
            TutorialCatalogue.slug == payload.slug).first():
        raise HTTPException(status_code=409, detail="slug already exists")

    row = TutorialCatalogue(
        slug=payload.slug,
        title=payload.title,
        description=payload.description,
        level=payload.level.strip().lower(),
        youtube_id=payload.youtube_id or None,
        duration_seconds=payload.duration_seconds,
        sort_order=payload.sort_order,
        is_published=payload.is_published,
    )
    db.add(row)
    db.commit()
    logger.info("admin created tutorial %s", row.slug)
    return _tutorial_admin_out(row)


@router.put("/tutorials/{slug}")
def admin_update_tutorial(
    slug: str,
    payload: TutorialPatch,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> dict:
    """Update a tutorial. The slug itself cannot be changed -- see TutorialPatch.

    Uses exclude_unset so omitting a field leaves it alone while sending
    `null` clears it. Without that distinction there is no way to remove a
    wrong youtube_id: every PUT would either overwrite untouched fields with
    None, or make null meaningless.
    """
    from app.services import tutorials as catalogue

    row = _get_catalogue_row(db, slug)
    provided = payload.model_dump(exclude_unset=True)

    if "level" in provided and not catalogue.is_valid_level(provided["level"]):
        raise HTTPException(status_code=422,
                            detail=f"level must be one of {catalogue.LEVELS}")

    for field_name, value in provided.items():
        if field_name == "level" and value:
            value = value.strip().lower()
        if field_name == "youtube_id" and value == "":
            value = None
        setattr(row, field_name, value)

    db.commit()
    logger.info("admin updated tutorial %s: %s", slug, sorted(provided))
    return _tutorial_admin_out(row)


@router.delete("/tutorials/{slug}")
def admin_delete_tutorial(
    slug: str,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> dict:
    """Delete a tutorial.

    User PROGRESS IS DELIBERATELY NOT DELETED. Progress rows reference the
    catalogue by slug with no foreign key, so they survive -- and re-creating
    a tutorial with the same slug restores every user's history. Cascading
    would make an accidental delete unrecoverable and silent.

    The orphaned rows are invisible to users (every user-facing query joins
    against the catalogue) and are counted in the response so the decision is
    not hidden from whoever pressed the button.
    """
    from app.db.models import TutorialProgress

    row = _get_catalogue_row(db, slug)
    orphaned = db.query(func.count(TutorialProgress.id)).filter(
        TutorialProgress.tutorial_slug == slug).scalar() or 0
    db.delete(row)
    db.commit()
    logger.info("admin deleted tutorial %s (%d progress rows kept)",
                slug, orphaned)
    return {
        "deleted": slug,
        "progress_rows_kept": int(orphaned),
        "note": ("User progress was kept. Re-creating a tutorial with this "
                 "slug restores it."),
    }


@router.post("/tutorials/{slug}/publish")
def admin_publish_tutorial(
    slug: str,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> dict:
    """Make a tutorial visible to users.

    REFUSES without a youtube_id. Publishing one would put a permanent
    "coming soon" card in the Learn tab, which reads as a broken feature
    rather than an incomplete one -- and it is the whole reason the seeded
    rows start unpublished.
    """
    row = _get_catalogue_row(db, slug)
    if not row.youtube_id:
        raise HTTPException(
            status_code=409,
            detail=("Cannot publish without a youtube_id — it would show users "
                    "a permanent 'coming soon' card. Add the video id first."))
    row.is_published = True
    db.commit()
    logger.info("admin published tutorial %s", slug)
    return _tutorial_admin_out(row)


@router.post("/tutorials/{slug}/unpublish")
def admin_unpublish_tutorial(
    slug: str,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> dict:
    """Hide a tutorial from users. Progress is untouched and returns on
    republish."""
    row = _get_catalogue_row(db, slug)
    row.is_published = False
    db.commit()
    logger.info("admin unpublished tutorial %s", slug)
    return _tutorial_admin_out(row)


@router.get("/tutorials/completions")
def tutorial_completions(
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> dict:
    """Aggregate tutorial completion counts. NUMBERS ONLY.

    Deliberately scoped: an admin sees HOW MANY users finished each video and
    how many are in progress. There is no endpoint anywhere that tells an
    admin which videos a NAMED user watched, or how far through one they got
    -- that was the explicit product decision (progress is private per user,
    admins see completion counts only), and it is enforced by this endpoint
    returning no user identifier at all rather than by a UI that declines to
    render one.

    `active_learners` is a distinct count of users with any progress row, so
    the denominator is "people who opened the section", not "people who have
    an account".
    """
    from app.db.models import TutorialProgress

    completed_rows = dict(
        db.query(TutorialProgress.tutorial_slug, func.count(TutorialProgress.id))
        .filter(TutorialProgress.completed.is_(True))
        .group_by(TutorialProgress.tutorial_slug)
        .all()
    )
    started_rows = dict(
        db.query(TutorialProgress.tutorial_slug, func.count(TutorialProgress.id))
        .group_by(TutorialProgress.tutorial_slug)
        .all()
    )
    active_learners = (
        db.query(func.count(func.distinct(TutorialProgress.user_id))).scalar() or 0
    )

    # include_unpublished: an admin needs the counts for a tutorial they just
    # unpublished, otherwise the numbers vanish exactly when someone is asking
    # why engagement dropped.
    per_tutorial = []
    for tutorial in catalogue.list_tutorials(db, include_unpublished=True):
        started = int(started_rows.get(tutorial.slug, 0))
        completed = int(completed_rows.get(tutorial.slug, 0))
        per_tutorial.append({
            "slug": tutorial.slug,
            "title": tutorial.title,
            "level": tutorial.level,
            "is_published": tutorial.is_published,
            "started_count": started,
            "completed_count": completed,
            # Not "started minus completed" as a separate concept: a completed
            # row is also a started row, so in_progress is the remainder.
            "in_progress_count": max(0, started - completed),
        })

    return {
        "active_learners": int(active_learners),
        "tutorials": per_tutorial,
        "total_completions": int(sum(completed_rows.values())),
    }


@router.get("/support/tickets")
def list_support_tickets(
    status: Optional[str] = Query(default=None,
                                  description="open | resolved; omit for all"),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> dict:
    """The support ticket queue (Feature 3).

    Unlike /admin/tutorials/completions, this one DOES carry a user identifier
    — a support ticket you cannot reply to is useless, so the requester's email
    is the entire point. That is a different privacy question from tutorial
    watch history: the user wrote this text deliberately, addressed to support.

    Ordered oldest-first within open tickets, because the queue is worked from
    the front and the oldest unanswered ticket is the one closest to breaking
    the 24-hour promise made in the chat.
    """
    from app.db.models import SupportTicket

    query = db.query(SupportTicket)
    if status:
        query = query.filter(SupportTicket.status == status.strip().lower())
    rows = query.order_by(SupportTicket.created_at.asc()).limit(limit).all()

    def _iso(value):
        if value is None:
            return None
        return (value if value.tzinfo else value.replace(tzinfo=datetime_timezone.utc)).isoformat()

    users = {}
    if rows:
        user_rows = db.query(User.id, User.email).filter(
            User.id.in_({r.user_id for r in rows})
        ).all()
        users = {uid: email for uid, email in user_rows}

    open_count = db.query(func.count(SupportTicket.id)).filter(
        SupportTicket.status == "open").scalar() or 0

    return {
        "open_count": int(open_count),
        "tickets": [{
            "id": str(t.id),
            "user_id": str(t.user_id),
            "user_email": users.get(t.user_id),
            "subject": t.subject,
            "body": t.body,
            "status": t.status,
            "chat_session_id": str(t.chat_session_id) if t.chat_session_id else None,
            "created_at": _iso(t.created_at),
            "resolved_at": _iso(t.resolved_at),
            "resolution_note": t.resolution_note,
        } for t in rows],
    }


class TicketResolveRequest(BaseModel):
    note: str = ""


@router.post("/support/tickets/{ticket_id}/resolve")
def resolve_support_ticket(
    ticket_id: str,
    payload: TicketResolveRequest,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> dict:
    """Close a ticket. Idempotent — resolving an already-resolved ticket keeps
    the ORIGINAL resolved_at, so the response-time record cannot be rewritten
    by a second click."""
    import uuid as _uuid

    from app.db.models import SupportTicket

    try:
        parsed = _uuid.UUID(ticket_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Ticket not found")

    ticket = db.query(SupportTicket).filter(SupportTicket.id == parsed).first()
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")

    if ticket.status != "resolved":
        ticket.status = "resolved"
        ticket.resolved_at = datetime.now(datetime_timezone.utc)
    if payload.note:
        ticket.resolution_note = payload.note
    db.commit()
    return {"id": str(ticket.id), "status": ticket.status}


@router.get("/celery-stats")
async def celery_stats(_admin=Depends(require_admin)) -> dict:
    return get_celery_stats()


# ---------------------------------------------------------------------------
# Encryption key rotation
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Identity reviews (Section B, migration 0039)
# ---------------------------------------------------------------------------
# A declared-vs-detected country mismatch at onboarding lands here as
# geo_review_status="pending". The admin clears it (travel, VPN, remote
# founder) or confirms it as a risk; neither suspends the account on its own --
# suspension stays the explicit action it already is above.


class IdentityReviewDecision(BaseModel):
    decision: str = Field(pattern="^(cleared|confirmed_risk)$")
    note: str = Field(default="", max_length=500)


class SignupRow(BaseModel):
    ts: datetime
    event: str
    outcome: str
    email: Optional[str]
    ip: Optional[str]
    country: Optional[str]
    user_id: Optional[str]


@router.get("/signups", response_model=list[SignupRow])
def list_signups(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
) -> list[dict]:
    """Every sign-up and refused sign-up attempt, newest first (admins only)."""
    from app.services import signup_watch  # noqa: PLC0415

    return signup_watch.list_signups(db, page=page, page_size=page_size)


@router.get("/identity-reviews")
def list_identity_reviews(
    status: str = Query("pending", pattern="^(pending|cleared|confirmed_risk|all)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
) -> dict:
    from sqlalchemy import select  # noqa: PLC0415
    from app.services import identity as identity_svc  # noqa: PLC0415

    query = select(User).where(User.geo_review_status.isnot(None))
    if status != "all":
        query = query.where(User.geo_review_status == status)
    rows = db.execute(query.order_by(User.identity_submitted_at.desc())
                      .offset((page - 1) * page_size).limit(page_size)).scalars().all()
    pending = db.query(func.count(User.id)).filter(
        User.geo_review_status == identity_svc.REVIEW_PENDING).scalar() or 0
    return {"pending_count": int(pending),
            "reviews": [identity_svc.review_out(u) for u in rows]}


@router.post("/identity-reviews/{user_id}")
def decide_identity_review(
    user_id: str,
    body: IdentityReviewDecision,
    db: Session = Depends(get_db),
    admin=Depends(require_admin),
) -> dict:
    import uuid as _uuid  # noqa: PLC0415
    from app.services import identity as identity_svc  # noqa: PLC0415

    try:
        user = db.get(User, _uuid.UUID(user_id))
    except ValueError:
        user = None
    if user is None or user.geo_review_status is None:
        raise HTTPException(status_code=404, detail="No identity review for that user")
    identity_svc.review(db, user, decision=body.decision, reviewer_email=admin.email,
                        note=body.note)
    logger.info("admin.identity_review", user_id=user_id, decision=body.decision,
                by=admin.email)
    return identity_svc.review_out(user)


@router.get("/security-events")
def list_security_events(
    user_id: Optional[str] = None,
    event: Optional[str] = Query(None, max_length=40),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
) -> list[dict]:
    import uuid as _uuid  # noqa: PLC0415
    from sqlalchemy import select  # noqa: PLC0415
    from app.db.models import AccountSecurityEvent  # noqa: PLC0415

    query = select(AccountSecurityEvent)
    if user_id:
        try:
            query = query.where(AccountSecurityEvent.user_id == _uuid.UUID(user_id))
        except ValueError:
            raise HTTPException(status_code=422, detail="user_id must be a UUID")
    if event:
        query = query.where(AccountSecurityEvent.event == event)
    rows = db.execute(query.order_by(AccountSecurityEvent.ts.desc()).limit(limit)).scalars()
    return [{"id": str(r.id), "user_id": str(r.user_id) if r.user_id else None,
             "email": r.email, "event": r.event, "ip": r.ip, "country": r.country,
             "details": r.details_json, "ts": r.ts.isoformat() if r.ts else None}
            for r in rows]


@router.post("/rotate-encryption-key")
async def rotate_encryption_key(
    db: Session = Depends(get_db),
    admin=Depends(require_admin),
) -> dict:
    from app.core.crypto import rotate_all_secrets
    logger.info("admin.encryption_key_rotation_started", by=admin.email)
    result = rotate_all_secrets(db)
    logger.info("admin.encryption_key_rotation_completed", **result, by=admin.email)
    return {**result, "status": "ok"}


@router.get("/config-warnings")
def config_warnings(_admin: User = Depends(require_admin)) -> list[dict]:
    """Deployment misconfigurations an admin must act on (product defect #5:
    phone verification required but undeliverable). Shown as a banner on every
    admin page."""
    from app.services import identity  # noqa: PLC0415

    return identity.config_warnings()
