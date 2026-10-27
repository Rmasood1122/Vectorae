"""
Playbook API — M8-C1+C2 base + C4 additions.

New endpoints (C4):
  GET /playbook/send-times       — send-time recommendation for channel+ICP
  GET /playbook/subject-patterns — top subject line patterns by channel
  GET /playbook/scores           — playbook scores (with decay metadata)
  GET /playbook/similar-strategies — TF-IDF similarity
  POST /playbook/aggregate       — trigger nightly aggregation (admin)
  POST /playbook/promote-winners — trigger A/B promotion sweep (admin)
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_admin
from app.core.database import get_db
from app.core.logging import get_logger
from app.core.plans import check_feature, plan_limit_response, PlanLimitExceeded

logger = get_logger("api.playbook")
router = APIRouter(prefix="/playbook", tags=["playbook"])


# ---------------------------------------------------------------------------
# Send-time optimizer (C4)
# ---------------------------------------------------------------------------

@router.get("/send-times")
async def get_send_times(
    channel: str = Query("gmail"),
    icp_industry: Optional[str] = Query(None),
    icp_company_size: Optional[str] = Query(None),
    current_user=Depends(get_current_user),
) -> dict:
    """
    Recommended send-time slots for a channel+ICP combination, from the
    caller's own sends only. Returns up to 5 slots sorted by expected_reply_rate.
    """
    try:
        check_feature(current_user, "playbook_access")
    except PlanLimitExceeded as e:
        raise HTTPException(status_code=402, detail=plan_limit_response(e))

    from app.services.send_time_optimizer import get_send_time_recommendation
    from dataclasses import asdict

    rec = get_send_time_recommendation(channel, icp_industry, icp_company_size,
                                       owner_id=current_user.id)
    return {
        "channel": rec.channel,
        "icp_industry": rec.icp_industry,
        "icp_company_size": rec.icp_company_size,
        "confidence": rec.confidence,
        "fallback_used": rec.fallback_used,
        "recommended_slots": rec.recommended_slots,
    }


# ---------------------------------------------------------------------------
# Subject line patterns (C4)
# ---------------------------------------------------------------------------

@router.get("/subject-patterns")
async def get_subject_patterns(
    channel: str = Query("gmail"),
    top_n: int = Query(5, ge=1, le=10),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> list[dict]:
    """
    Top subject line patterns by meeting rate for a channel.
    Only returns patterns with is_reliable=True. The rates are pooled across
    every account on this install, so no example subject line is returned.
    """
    try:
        check_feature(current_user, "playbook_access")
    except PlanLimitExceeded as e:
        raise HTTPException(status_code=402, detail=plan_limit_response(e))

    from app.services.subject_intelligence import get_top_patterns
    from dataclasses import asdict

    patterns = get_top_patterns(db, channel=channel, top_n=top_n)
    return [
        {
            "pattern_type": p.pattern_type,
            "avg_reply_rate": p.avg_reply_rate,
            "avg_meeting_rate": p.avg_meeting_rate,
            "sample_size": p.sample_size,
            "is_reliable": p.is_reliable,
            "channel": p.channel,
        }
        for p in patterns
    ]


# ---------------------------------------------------------------------------
# Playbook scores (with decay metadata)
# ---------------------------------------------------------------------------

@router.get("/scores")
async def get_playbook_scores(
    strategy_id: Optional[uuid.UUID] = Query(None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> list[dict]:
    """User's playbook scores. Includes decay metadata (effective_n, trend).

    F-DB-01: only rows the caller owns (playbook_scores.user_id), joined to
    the caller's strategies on pattern_key for the strategy_id label.

    Written in SQLAlchemy Core so the ids bind through the Uuid type: the
    raw-SQL version bound str(current_user.id), which matched nothing on
    SQLite (Uuid is stored there as 32 undashed hex digits). A malformed
    strategy_id is a 422. A DB error propagates as a 500 -- an empty list
    here must mean "no data", never "the query failed".
    """
    try:
        check_feature(current_user, "playbook_access")
    except PlanLimitExceeded as e:
        raise HTTPException(status_code=402, detail=plan_limit_response(e))

    from app.db.models import PlaybookScore, Product, Strategy

    ps = PlaybookScore
    stmt = (
        select(
            ps.pattern_key, ps.variant, ps.reply_rate, ps.booking_rate,
            ps.sample_size, ps.effective_sample_size, ps.is_reliable,
            ps.decay_half_life_days, ps.trend, ps.last_aggregated_at,
            Strategy.id.label("strategy_id"),
        )
        .distinct()
        .join(Strategy, Strategy.pattern_key == ps.pattern_key)
        .join(Product, Product.id == Strategy.product_id)
        .where(Product.user_id == current_user.id, ps.user_id == current_user.id)
    )
    if strategy_id:
        stmt = stmt.where(Strategy.id == strategy_id)
    stmt = stmt.order_by(ps.reply_rate.desc().nulls_last()).limit(200)

    rows = db.execute(stmt).mappings().all()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Strategy similarity (C2 — unchanged)
# ---------------------------------------------------------------------------

@router.get("/similar-strategies")
async def similar_strategies(
    query: str = Query(..., min_length=5),
    top_n: int = Query(5, ge=1, le=20),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> list[dict]:
    from app.services.similarity import StrategySimilarityIndex
    results = StrategySimilarityIndex.find_similar(query, db, user_id=str(current_user.id), top_n=top_n)
    return results


# ---------------------------------------------------------------------------
# Admin actions (aggregate + promote)
# ---------------------------------------------------------------------------

@router.post("/aggregate")
async def trigger_aggregate(admin=Depends(require_admin)) -> dict:
    from app.workers.learning_tasks import run_strategy_aggregation
    run_strategy_aggregation.delay()
    return {"status": "triggered"}


@router.post("/promote-winners")
async def trigger_promote(admin=Depends(require_admin)) -> dict:
    from app.workers.learning_tasks import auto_promote_winners
    auto_promote_winners.delay()
    return {"status": "triggered"}
