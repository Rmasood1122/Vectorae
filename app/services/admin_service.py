"""
AdminService — business logic for admin endpoints.

All methods are synchronous (called from FastAPI async handlers via run_in_executor
or directly because SQLAlchemy is synchronous in this stack).
"""
from __future__ import annotations

import datetime
import uuid
from typing import Any, Optional

from sqlalchemy import func, desc
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.task_error import TaskError

logger = get_logger("services.admin")



def _strategy_count(db: Session, user_id) -> int:
    from app.models.strategy import Strategy
    from app.db.models import Product
    return (
        db.query(func.count(Strategy.id))
        .join(Product, Product.id == Strategy.product_id)
        .filter(Product.user_id == user_id)
        .scalar() or 0
    )


def _active_campaign_count(db: Session, user_id) -> int:
    """Campaign state lives on Strategy.campaign_state (M3); user scoping
    goes Strategy -> Product -> user_id. (Original M8-C3 file referenced a
    Campaign model that does not exist in the M1-M7 schema.)"""
    from app.models.strategy import Strategy
    from app.db.models import Product
    return (
        db.query(func.count(Strategy.id))
        .join(Product, Product.id == Strategy.product_id)
        .filter(Product.user_id == user_id, Strategy.campaign_state == "active")
        .scalar() or 0
    )


def _set_campaign_state(db: Session, user_id, from_state: str, to_state: str, reason: str) -> int:
    from app.models.strategy import Strategy
    from app.db.models import Product
    ids = [
        row[0] for row in
        db.query(Strategy.id)
        .join(Product, Product.id == Strategy.product_id)
        .filter(Product.user_id == user_id, Strategy.campaign_state == from_state)
        .all()
    ]
    if not ids:
        return 0
    db.query(Strategy).filter(Strategy.id.in_(ids)).update(
        {"campaign_state": to_state, "campaign_pause_reason": reason},
        synchronize_session=False,
    )
    return len(ids)


def _plan_of(user) -> str | None:
    """PlanTier -> str for the admin schemas (they declare `plan: str | None`)."""
    plan = getattr(user, "plan", None)
    return getattr(plan, "value", plan)


def _origin_fields(user, origin: Optional[dict]) -> dict[str, Any]:
    """Where an account came from, for admins (app/services/signup_watch.py).

    signup_ip / signup_country come from the account's sign-up row; accounts
    created before that row existed fall back to users.signup_ip and the
    country the identity step detected. declared_country is what the person
    typed. All None for accounts older than the identity step (0039)."""
    origin = origin or {}
    return {
        "signup_ip": origin.get("ip") or getattr(user, "signup_ip", None),
        "signup_country": origin.get("country") or getattr(user, "geo_detected_country", None),
        "declared_country": getattr(user, "personal_country", None),
        "company_name": getattr(user, "company_name", None),
        "company_country": getattr(user, "company_country", None),
    }


class AdminService:
    # ---------------------------------------------------------------------------
    # User management
    # ---------------------------------------------------------------------------

    @staticmethod
    def list_users(db: Session, page: int = 1, page_size: int = 50) -> list[Any]:
        from app.models.user import User

        offset = (page - 1) * page_size
        users = db.query(User).order_by(desc(User.created_at)).offset(offset).limit(page_size).all()

        from app.services import signup_watch  # noqa: PLC0415

        origins = signup_watch.signup_origin(db, [u.id for u in users])
        result = []
        for user in users:
            strategy_count = _strategy_count(db, user.id)
            active_campaign_count = _active_campaign_count(db, user.id)
            result.append({
                "id": str(user.id),
                "email": user.email,
                "is_admin": getattr(user, "is_admin", False),
                "is_suspended": getattr(user, "is_suspended", False),
                # UserSummary declares `plan`, and omitting it made FastAPI
                # raise ResponseValidationError -> GET /admin/users answered
                # 500 for every request. PlanTier is an enum; the schema wants
                # a string.
                "plan": _plan_of(user),
                "created_at": user.created_at,
                "strategy_count": strategy_count,
                "active_campaign_count": active_campaign_count,
                "last_active_at": getattr(user, "last_active_at", None),
                **_origin_fields(user, origins.get(str(user.id))),
            })
        return result

    @staticmethod
    def get_user_detail(db: Session, user_id: str) -> Optional[dict[str, Any]]:
        from app.models.user import User

        try:
            # A UUID, not the path string: SQLite's UUID column rejects a str
            # (PostgreSQL casts it), and a malformed id is a 404, not a 500.
            user_uuid = uuid.UUID(str(user_id))
        except ValueError:
            return None
        user = db.query(User).filter_by(id=user_uuid).first()
        if not user:
            return None
        strategy_count = _strategy_count(db, user.id)
        active_campaign_count = _active_campaign_count(db, user.id)
        from app.services import signup_watch  # noqa: PLC0415

        origin = signup_watch.signup_origin(db, [user.id]).get(str(user.id))
        return {
            "id": str(user.id),
            "email": user.email,
            "is_admin": getattr(user, "is_admin", False),
            "is_suspended": getattr(user, "is_suspended", False),
            "plan": _plan_of(user),
            "created_at": getattr(user, "created_at", None),
            "strategy_count": strategy_count,
            "active_campaign_count": active_campaign_count,
            "last_active_at": getattr(user, "last_active_at", None),
            **_origin_fields(user, origin),
        }

    @staticmethod
    def suspend_user(db: Session, user_id: str, reason: str, suspended_by: str) -> bool:
        from app.db.models import User

        user = db.query(User).filter_by(id=user_id).first()
        if not user:
            return False
        user.is_suspended = True
        user.suspended_at = datetime.datetime.now(datetime.timezone.utc)
        user.suspended_reason = reason
        # Pause all active campaigns (Strategy.campaign_state — see helpers above)
        _set_campaign_state(db, user_id, "active", "paused_admin",
                            reason=f"account suspended: {reason}")
        db.commit()
        return True

    @staticmethod
    def unsuspend_user(db: Session, user_id: str, resumed_by: str) -> bool:
        from app.db.models import User

        user = db.query(User).filter_by(id=user_id).first()
        if not user:
            return False
        user.is_suspended = False
        user.suspended_at = None
        user.suspended_reason = None
        # Re-activate paused-by-admin campaigns (Strategy.campaign_state)
        _set_campaign_state(db, user_id, "paused_admin", "active", reason="")
        db.commit()
        return True

    # ---------------------------------------------------------------------------
    # Suppression list
    # ---------------------------------------------------------------------------

    @staticmethod
    def list_suppression(
        db: Session, page: int = 1, page_size: int = 100
    ) -> tuple[list[dict[str, Any]], int]:
        from app.models.suppression import SuppressionList

        offset = (page - 1) * page_size
        total = db.query(func.count(SuppressionList.id)).scalar() or 0
        # SuppressionEntry's timestamp column is `ts`, and the model has no
        # `source` / `user_id` / `created_at` at all (see app/db/models.py).
        # Ordering by created_at raised
        #   AttributeError: type object 'SuppressionEntry' has no attribute
        #   'created_at'
        # so GET /admin/suppression-list answered 500 for every request. The
        # response still exposes `created_at` because that is the key the admin
        # UI reads; it is sourced from `ts`.
        items = (
            db.query(SuppressionList)
            .order_by(desc(SuppressionList.ts))
            .offset(offset)
            .limit(page_size)
            .all()
        )
        return [
            {
                "id": str(s.id),
                "email": s.email,
                "phone": getattr(s, "phone", None),
                "reason": s.reason,
                "source": getattr(s, "source", "user"),
                "user_id": str(s.user_id) if getattr(s, "user_id", None) else None,
                "created_at": getattr(s, "ts", None),
            }
            for s in items
        ], total

    @staticmethod
    def add_suppression(
        db: Session,
        email: Optional[str],
        phone: Optional[str],
        reason: str,
        source: str,
        added_by: str,
    ) -> None:
        from app.models.suppression import SuppressionList
        from app.workers.lead_tasks import is_suppressed, suppression_phone

        # Check for existing
        if email:
            if db.query(SuppressionList).filter_by(email=email).first():
                return  # already suppressed
        elif phone:
            # NEW-04: the same number in another format is already suppressed.
            if is_suppressed(db, phone=phone):
                return
        phone = suppression_phone(phone)

        # Only these four are real columns; source/user_id/created_at are not
        # on the model and would raise "invalid keyword argument", so adding a
        # suppression entry from the admin UI failed too. `ts` defaults to
        # now() server-side.
        db.add(SuppressionList(
            email=email,
            phone=phone,
            reason=reason,
        ))
        db.commit()

    @staticmethod
    def remove_suppression(db: Session, email: str, removed_by: str) -> bool:
        from app.models.suppression import SuppressionList

        count = db.query(SuppressionList).filter_by(email=email).delete()
        db.commit()
        return count > 0

    # ---------------------------------------------------------------------------
    # Task errors
    # ---------------------------------------------------------------------------

    @staticmethod
    def list_task_errors(
        db: Session,
        unresolved_only: bool = True,
        page: int = 1,
        page_size: int = 50,
    ) -> list[Any]:
        offset = (page - 1) * page_size
        q = db.query(TaskError).order_by(desc(TaskError.ts))
        if unresolved_only:
            q = q.filter(TaskError.resolved_at.is_(None))
        return q.offset(offset).limit(page_size).all()

    @staticmethod
    def resolve_task_error(
        db: Session, error_id: str, resolved_by: str, note: str
    ) -> bool:
        row = db.query(TaskError).filter_by(id=error_id).first()
        if not row:
            return False
        row.resolved_at = datetime.datetime.utcnow()
        row.resolved_by = resolved_by
        row.resolution_note = note
        db.commit()
        return True
