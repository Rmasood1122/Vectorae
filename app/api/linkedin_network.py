"""Private LinkedIn network (Growth plan and up).

Every query below is filtered on current_user.id. There is no endpoint, flag or
parameter that reads another account's connections.
"""

from __future__ import annotations

import uuid
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.metering import metered
from app.core.plans import PLANS, PlanLimitExceeded, plan_limit_response
from app.db.base import get_db
from app.db.models import (
    LinkedInConnection, LinkedInConnectionMatch, LinkedInImport, LinkedInNetworkMessage,
    Product, User,
)
from app.core.rate_limiting import client_ip
from app.services import linkedin_consent
from app.services import linkedin_network as ln
from app.services import network_messages as nm
from app.services import security_audit
from app.workers.lead_tasks import is_suppressed

router = APIRouter(prefix="/linkedin-network", tags=["linkedin-network"])

UPGRADE_TO = "growth"


class ImportIn(BaseModel):
    csv_text: str = Field(min_length=1, description="Contents of LinkedIn's Connections.csv")
    # L3 attestation (evals/vectorae Phase 5B): the version of the notice the
    # user read (GET /linkedin-network/status -> consent.version) and the
    # lawful basis they rely on. Both required; nothing is stored without them.
    consent_text_version: str | None = Field(default=None, max_length=40)
    lawful_basis: str | None = Field(default=None, max_length=40)


def _require_plan(user: User) -> None:
    if ln.is_eligible(user):
        return
    plan = getattr(user.plan, "value", user.plan) or "free"
    raise HTTPException(status_code=402, detail=plan_limit_response(PlanLimitExceeded(
        limit_key="linkedin_network", current_plan=plan, upgrade_to=UPGRADE_TO,
        message=f"{PLANS.get(plan, PLANS['free'])['display_name']} plan does not include "
                "the LinkedIn network. Upgrade to Growth or higher.")))


@router.get("/status")
def status(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict:
    """Readable on every plan, so the page can explain the upgrade."""
    plan = getattr(current_user.plan, "value", current_user.plan) or "free"
    last_import = db.execute(select(func.max(LinkedInImport.created_at))
                             .where(LinkedInImport.user_id == current_user.id)).scalar_one()
    last_analyzed = db.execute(select(func.max(LinkedInConnectionMatch.analyzed_at))
                               .where(LinkedInConnectionMatch.user_id == current_user.id)
                               ).scalar_one()
    count = db.execute(select(func.count(LinkedInConnection.id))
                       .where(LinkedInConnection.user_id == current_user.id)).scalar_one()
    return {
        "eligible": ln.is_eligible(current_user), "plan": plan,
        "upgrade_to": None if ln.is_eligible(current_user) else UPGRADE_TO,
        "connection_count": count,
        "last_import_at": last_import, "last_analyzed_at": last_analyzed,
        "needs_new_export": ln.needs_new_export(db, current_user),
        "max_bytes": ln.MAX_BYTES, "max_rows": ln.MAX_ROWS,
        "consent": _consent_out(db, current_user),
    }


def _consent_out(db: Session, user: User) -> dict:
    text = linkedin_consent.current_text(linkedin_consent.CONNECTIONS_IMPORT)
    live = linkedin_consent.current(db, user.id, linkedin_consent.CONNECTIONS_IMPORT)
    return {"scope": text.scope, "version": text.version, "purpose": text.purpose,
            "text": text.text, "lawful_bases": list(linkedin_consent.LAWFUL_BASES),
            "given": live is not None,
            "lawful_basis": live.lawful_basis if live else None,
            "granted_at": live.granted_at if live else None}


@router.post("/import")
def import_export(body: ImportIn, request: Request, db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)) -> dict:
    _require_plan(current_user)
    if len(body.csv_text.encode("utf-8")) > ln.MAX_BYTES:
        raise HTTPException(status_code=413,
                            detail=f"File is larger than {ln.MAX_BYTES // 1_000_000} MB.")
    # Attest BEFORE parsing: without a lawful basis the file is not read.
    try:
        consent = linkedin_consent.record(
            db, user_id=current_user.id, scope=linkedin_consent.CONNECTIONS_IMPORT,
            text_version=body.consent_text_version or "", lawful_basis=body.lawful_basis,
            ip=client_ip(request))
    except linkedin_consent.ConsentRequired as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc))
    try:
        parsed = ln.parse_connections_csv(body.csv_text)
    except ln.NetworkImportError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc))
    out = ln.import_connections(db, current_user, parsed)
    analysis = ln.analyze_user(db, current_user)
    # Counts only: never names, companies or addresses.
    security_audit.record(db, action=security_audit.LINKEDIN_NETWORK_IMPORTED,
                          request=request, user_id=current_user.id,
                          target_type="linkedin_network",
                          details={**out, "consent_text_version": consent.consent_text_version,
                                   "lawful_basis": consent.lawful_basis})
    db.commit()
    return {**out, "matched": analysis["matches"]}


@router.post("/analyze")
def analyze(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict:
    _require_plan(current_user)
    return ln.analyze_user(db, current_user)


@router.get("/recommendations")
def recommendations(
    product_id: uuid.UUID | None = None,
    min_score: int = Query(default=ln.MATCH_THRESHOLD, ge=0, le=100),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    _require_plan(current_user)
    q = select(Product).where(Product.user_id == current_user.id).order_by(Product.created_at)
    if product_id:
        q = q.where(Product.id == product_id)
    out = []
    for product in db.execute(q).scalars().all():
        rows = db.execute(
            select(LinkedInConnectionMatch, LinkedInConnection)
            .join(LinkedInConnection, LinkedInConnection.id == LinkedInConnectionMatch.connection_id)
            .where(LinkedInConnectionMatch.user_id == current_user.id,
                   LinkedInConnection.user_id == current_user.id,
                   LinkedInConnectionMatch.product_id == product.id,
                   LinkedInConnectionMatch.score >= min_score)
            .order_by(LinkedInConnectionMatch.score.desc(), LinkedInConnection.full_name)
        ).all()
        # A person on the do-not-contact list is not recommended, whichever
        # channel they opted out on (lead_tasks.is_suppressed is global).
        rows = [(match, c) for match, c in rows
                if not (c.profile_url and is_suppressed(db, linkedin=c.profile_url))]
        segments = Counter(match.segment for match, _ in rows)
        out.append({
            "product_id": str(product.id), "product_name": product.name,
            "total_matches": len(rows),
            "segments": [{"segment": s, "count": n} for s, n in segments.most_common()],
            "matches": [{
                "connection_id": str(c.id), "full_name": c.full_name,
                "position": c.position, "company": c.company,
                "profile_url": c.profile_url, "connected_on": c.connected_on,
                "score": match.score, "segment": match.segment,
                "reasons": match.reasons_json,
            } for match, c in rows[:limit]],
        })
    return {"products": out}


@router.delete("")
def erase(request: Request, db: Session = Depends(get_db),
          current_user: User = Depends(get_current_user)) -> dict:
    """Delete everything this account uploaded. Never plan-gated: a downgraded
    account can always remove its data."""
    out = ln.erase_user_network(db, current_user)
    # Deleting the network withdraws the attestation too: a new upload asks again.
    out["consents_revoked"] = linkedin_consent.revoke(
        db, current_user.id, linkedin_consent.CONNECTIONS_IMPORT)
    security_audit.record(db, action=security_audit.LINKEDIN_NETWORK_ERASED,
                          request=request, user_id=current_user.id,
                          target_type="linkedin_network", details=out)
    db.commit()
    return out


# --------------------------------------------------------------------------
# Messages to your own connections
# --------------------------------------------------------------------------


class DraftIn(BaseModel):
    connection_id: uuid.UUID
    product_id: uuid.UUID


class SaveIn(BaseModel):
    text: str = Field(max_length=5000)


class BulkIn(BaseModel):
    ids: list[uuid.UUID] = Field(max_length=nm.BULK_MAX)


def _msg_out(m: LinkedInNetworkMessage) -> dict:
    return {"id": str(m.id), "connection_id": str(m.connection_id),
            "product_id": str(m.product_id), "text": m.text, "status": m.status,
            "channel": m.channel, "error": m.error, "sent_at": m.sent_at}


def _run(fn, *args):
    try:
        return fn(*args)
    except nm.MessageError as exc:
        if exc.status == 402:
            raise HTTPException(status_code=402, detail=plan_limit_response(PlanLimitExceeded(
                limit_key="linkedin_network", current_plan="free", upgrade_to=UPGRADE_TO,
                message=exc.detail)))
        raise HTTPException(status_code=exc.status, detail=exc.detail)


@router.get("/messages")
def list_messages(db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)) -> dict:
    rows = db.execute(select(LinkedInNetworkMessage)
                      .where(LinkedInNetworkMessage.user_id == current_user.id)
                      .order_by(LinkedInNetworkMessage.created_at)).scalars().all()
    return {"messages": [_msg_out(r) for r in rows]}


@router.post("/messages/draft")
def draft_message(body: DraftIn, db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)) -> dict:
    _require_plan(current_user)
    with metered(db, current_user, "drafting"):
        return _msg_out(_run(nm.draft, db, current_user, body.connection_id, body.product_id))


@router.put("/messages/{message_id}")
def save_message(message_id: uuid.UUID, body: SaveIn, db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)) -> dict:
    _require_plan(current_user)
    return _msg_out(_run(nm.save, db, current_user, message_id, body.text))


@router.post("/messages/{message_id}/mark-sent")
def mark_sent(message_id: uuid.UUID, db: Session = Depends(get_db),
              current_user: User = Depends(get_current_user)) -> dict:
    _require_plan(current_user)
    return _msg_out(_run(nm.mark_sent_manually, db, current_user, message_id))


@router.post("/messages/{message_id}/send")
def send_message(message_id: uuid.UUID, db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)) -> dict:
    _require_plan(current_user)
    msg = _run(nm.own_message, db, current_user, message_id)
    return _msg_out(_run(nm.deliver, db, current_user, msg, "unipile"))


@router.post("/messages/bulk-send")
def bulk_send(body: BulkIn, db: Session = Depends(get_db),
              current_user: User = Depends(get_current_user)) -> dict:
    """Queue SAVED messages. They go out one at a time, spaced apart, each one
    re-checked (plan, suppression, daily cap, still a 1st-degree connection)
    at the moment it is sent."""
    _require_plan(current_user)
    from app.workers import network_tasks  # noqa: PLC0415

    rows = _run(nm.queue_bulk, db, current_user, body.ids)
    for i, row in enumerate(rows):
        network_tasks.send_queued.apply_async(
            args=[str(row.id)], countdown=i * nm.BULK_SPACING_SECONDS)
    return {"queued": len(rows), "spacing_seconds": nm.BULK_SPACING_SECONDS}
