"""Tamper-evident activity audit trail (Feature A2).

GET  /audit/trail                  the account's records (paged, newest first)
GET  /audit/trail/verify           seal, then verify the whole chain
GET  /audit/trail/export           signed report: ?format=json|pdf, optional
                                   lead_id / date_from / date_to
GET  /audit/public-key             PUBLIC: the Ed25519 key reports are signed with
POST /audit/verify                 PUBLIC: check a report someone was handed

The account is the workspace: in a workspace, get_current_user resolves the
OWNER, so a manager exporting "our" trail gets the workspace's trail.
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.rate_limiting import client_ip, enforce_rate_limit
from app.db.base import get_db
from app.db.models import ActivityAuditEvent, User
from app.services import audit_trail

router = APIRouter(tags=["audit"])

_MAX_VERIFY_RECORDS = 100_000


@router.get("/audit/trail")
def list_trail(lead_id: uuid.UUID | None = None,
               limit: int = Query(default=200, ge=1, le=1000),
               offset: int = Query(default=0, ge=0),
               db: Session = Depends(get_db),
               current_user: User = Depends(get_current_user)) -> dict:
    query = select(ActivityAuditEvent).where(ActivityAuditEvent.user_id == current_user.id)
    if lead_id is not None:
        query = query.where(ActivityAuditEvent.lead_ref == lead_id)
    rows = db.execute(query.order_by(ActivityAuditEvent.occurred_at.desc(),
                                     ActivityAuditEvent.created_at.desc())
                      .offset(offset).limit(limit)).scalars().all()
    return {"records": [audit_trail.record_out(r) for r in rows], "offset": offset,
            "limit": limit}


@router.get("/audit/trail/verify")
def verify_trail(db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)) -> dict:
    audit_trail.seal(db, current_user.id)
    return audit_trail.verify_chain(db, current_user.id)


@router.get("/audit/trail/export")
def export_trail(format: str = Query(default="json", pattern="^(json|pdf)$"),  # noqa: A002
                 lead_id: uuid.UUID | None = None,
                 date_from: date | None = None, date_to: date | None = None,
                 db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)) -> Response:
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from must not be after date_to")
    report = audit_trail.build_report(db, current_user, lead_id=lead_id,
                                      date_from=date_from, date_to=date_to)
    stamp = report["generated_at"][:10]
    headers = {"Cache-Control": "private, no-store"}
    if format == "pdf":
        headers["Content-Disposition"] = f'attachment; filename="vectorae-audit-{stamp}.pdf"'
        return Response(content=audit_trail.render_pdf(report), media_type="application/pdf",
                        headers=headers)
    headers["Content-Disposition"] = f'attachment; filename="vectorae-audit-{stamp}.json"'
    return JSONResponse(content=report, headers=headers)


@router.get("/audit/public-key")
def get_public_key() -> dict:
    return audit_trail.public_key()


@router.post("/audit/verify")
def verify_report(request: Request, report: dict = Body(...)) -> dict:
    """Unauthenticated on purpose: the enterprise buyer holding a report has no
    account here. It reads nothing from the database -- it checks the report's
    own signature and hashes -- and is rate limited per IP."""
    enforce_rate_limit(f"ip:{client_ip(request)}", "audit_verify", "RATE_LIMIT_PUBLIC_SHARE")
    if len(report.get("records") or []) > _MAX_VERIFY_RECORDS:
        raise HTTPException(status_code=413, detail="report too large to verify online")
    return audit_trail.verify_report(report)
