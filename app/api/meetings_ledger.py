"""F08 -- Meetings-per-Dollar Ledger (docs/features/f08-meetings-per-dollar.md).

GET /analytics/meetings-ledger?date_from&date_to&strategy_id
GET /analytics/meetings-ledger/export?format=csv|pdf&view=campaigns|events&...

Read-only: nothing here writes a row, calls a model or contacts a prospect.
Behind `meetings_ledger_enabled` (System Settings, default off): when it is
off both routes answer 409, the codebase's "disabled by the administrator"
pattern (app/api/calls.py). A campaign that is not yours is a 404.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.api.analytics import _owned_strategy
from app.api.deps import get_current_user
from app.db.base import get_db
from app.db.models import User
from app.services import meetings_ledger

router = APIRouter(tags=["meetings-ledger"])

_DEFAULT_DAYS = 30
_MAX_SPAN_DAYS = 3 * 366
DISABLED_DETAIL = "The meetings-per-dollar ledger is disabled by the administrator"


def _ledger(db: Session, user: User, date_from: date | None, date_to: date | None,
            strategy_id: uuid.UUID | None) -> dict:
    if not meetings_ledger.is_enabled(db):
        raise HTTPException(status_code=409, detail=DISABLED_DETAIL)
    today = datetime.now(timezone.utc).date()
    date_to = date_to or today
    date_from = date_from or (date_to - timedelta(days=_DEFAULT_DAYS - 1))
    if date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from must not be after date_to")
    if (date_to - date_from).days > _MAX_SPAN_DAYS:
        raise HTTPException(status_code=422, detail="the period may span at most 3 years")
    strategy_ids = None
    if strategy_id is not None:
        _owned_strategy(strategy_id, db, user)
        strategy_ids = [strategy_id]
    return meetings_ledger.build_ledger(db, user.id, date_from, date_to,
                                       strategy_ids=strategy_ids)


@router.get("/analytics/meetings-ledger")
def get_meetings_ledger(
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    strategy_id: uuid.UUID | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Per campaign: total cost ÷ qualified conversations, booked meetings and
    held meetings, with each booking's attribution confidence and a
    reconciliation against the raw cost and outcome rows. Default period:
    the last 30 days."""
    return _ledger(db, current_user, date_from, date_to, strategy_id)


@router.get("/analytics/meetings-ledger/export",
            response_class=Response,
            responses={200: {"content": {"text/csv": {}, "application/pdf": {}}}})
def export_meetings_ledger(
    format: str = Query(default="csv", pattern="^(csv|pdf)$"),  # noqa: A002
    view: str = Query(default="campaigns", pattern="^(campaigns|events)$"),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    strategy_id: uuid.UUID | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    """The same ledger as a CSV (`view=campaigns`: one row per campaign plus a
    TOTAL row; `view=events`: one row per counted event, lead id and company
    only) or a PDF summary. Private, never cached."""
    ledger = _ledger(db, current_user, date_from, date_to, strategy_id)
    stem = f"vectorae-meetings-ledger-{ledger['date_from']}-{ledger['date_to']}"
    headers = {"Cache-Control": "private, no-store"}
    if format == "pdf":
        headers["Content-Disposition"] = f'attachment; filename="{stem}.pdf"'
        return Response(content=meetings_ledger.to_pdf(ledger),
                        media_type="application/pdf", headers=headers)
    suffix = "" if view == "campaigns" else "-events"
    headers["Content-Disposition"] = f'attachment; filename="{stem}{suffix}.csv"'
    return Response(content=meetings_ledger.to_csv(ledger, view=view),
                    media_type="text/csv; charset=utf-8", headers=headers)
