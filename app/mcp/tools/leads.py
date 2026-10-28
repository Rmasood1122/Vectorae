"""Lead tools: list_leads, get_lead, get_claim_verification.

Data comes only through crm_service.grid_page / owned_lead (tenant filter
on Product.user_id) and fpta_scoring / claim_verification output helpers,
which are safe once owned_lead has resolved the lead.
"""

from __future__ import annotations

import json
from typing import Any, Annotated, Literal

from pydantic import Field

from app.config import settings
from app.db.models import Lead
from app.mcp import untrusted
from app.mcp.context import tool_call
from app.mcp.tools import MAX_LIMIT, iso, optional_id, parse_id, plain_arg, read_only
from app.services import claim_verification, crm_service, fpta_scoring

LeadStatusArg = Literal[
    "sourced", "enriched", "email_found", "verified", "flagged", "dropped",
    "contacted", "replied", "meeting_booked", "proposal_sent", "opportunity",
    "closed_won", "closed_lost", "disqualified",
]
SortArg = Literal["fpta", "booking_likelihood", "newest"]

_SORTS = {
    "fpta": [{"key": "fpta_overall", "dir": "desc"}],
    "booking_likelihood": [{"key": "ai_booking_likelihood", "dir": "desc"}],
    "newest": [{"key": "created_at", "dir": "desc"}],
}

# Bounds on research blobs returned by get_lead.
_MAX_POSTS = 5
_MAX_NEWS = 5
_MAX_POST_TEXT = 700
_MAX_ENRICHMENT_CHARS = 4000


def lead_url(lead_id) -> str:
    return f"{settings.frontend_url.rstrip('/')}/leads/detail?id={lead_id}"


def lead_list_item(row: dict) -> dict:
    """A grid_page row: structured fields + the provider fields as untrusted."""
    return {
        "id": row["id"],
        "strategy_id": row["strategy_id"],
        "status": row["status"],
        "source": row["source"],
        "fpta_overall": row["fpta_overall"],
        "fpta_band": fpta_scoring.band(row["fpta_overall"]),
        "ai_booking_likelihood": row["ai_booking_likelihood"],
        "created_at": row["created_at"],
        "url": lead_url(row["id"]),
        **untrusted.envelope({
            "full_name": row["full_name"],
            "title": row["title"],
            "company": row["company"],
            "email": row["email"],
        }),
    }


def lead_detail(lead: Lead) -> dict:
    """Everything get_lead / fetch return for one owned lead."""
    fpta = fpta_scoring.detail(lead)
    posts = [
        {"text": (p.get("text") or "")[:_MAX_POST_TEXT], "posted_at": p.get("posted_at"),
         "url": p.get("url")}
        for p in (lead.linkedin_posts_json or [])[:_MAX_POSTS] if isinstance(p, dict)
    ]
    news = [
        {k: n.get(k) for k in ("headline", "summary", "source", "url", "published_at")}
        for n in (lead.company_news_json or [])[:_MAX_NEWS] if isinstance(n, dict)
    ]
    enrichment = lead.enrichment_json or {}
    return {
        "id": str(lead.id),
        "strategy_id": str(lead.strategy_id),
        "status": lead.status.value,
        "source": lead.source,
        "created_at": iso(lead.created_at),
        "updated_at": iso(lead.updated_at),
        "url": lead_url(lead.id),
        "fpta": {
            "overall": fpta["overall"],
            "band": fpta["band"],
            "fit": fpta["fit"],
            "problem": fpta["problem"],
            "timing": fpta["timing"],
            "access": fpta["access"],
            "method": fpta["method"],
            "weights": fpta["weights"],
            "scored_at": fpta["scored_at"],
        },
        "ai_booking_likelihood": lead.ai_booking_likelihood,
        **untrusted.envelope({
            "full_name": lead.full_name,
            "title": lead.title,
            "company": lead.company,
            "email": lead.email,
            "phone": lead.phone,
            "ai_score_reason": lead.ai_score_reason,
            "fpta_reasons": [
                {"dimension": d["key"], "reason": d["reason"], "signals": d["signals"]}
                for d in fpta["dimensions"]
            ],
            "linkedin_posts": posts,
            "company_news": news,
            "enrichment": _bounded_json(enrichment),
        }),
    }


def _bounded_json(value) -> object:
    text = json.dumps(value, default=str, ensure_ascii=False)
    if len(text) <= _MAX_ENRICHMENT_CHARS:
        return value
    return text[:_MAX_ENRICHMENT_CHARS] + "…"


def lead_summary(lead_detail_out: dict) -> str:
    fpta = lead_detail_out["fpta"]
    score = ("unscored" if fpta["overall"] is None
             else f"{fpta['overall']}/100 ({fpta['band']})")
    return (f"Lead {lead_detail_out['id']}: status {lead_detail_out['status']}, "
            f"F-P-T-A {score}, source {lead_detail_out['source']}.")


def register(server) -> None:

    @server.tool(
        name="list_leads",
        title="List leads",
        description=(
            "Lists the signed-in user's Vectorae leads (prospects), one page at a "
            "time, with status and F-P-T-A score. Optional filters: strategy_id, "
            "status, min_fpta (0-100) and search (matches name, company, email or "
            "title). Use offset/limit (limit 1-50) to page; `has_more` says whether "
            "another page exists."),
        annotations=read_only("List leads"),
    )
    def list_leads(
        strategy_id: str | None = None,
        status: LeadStatusArg | None = None,
        min_fpta: Annotated[int | None, Field(ge=0, le=100)] = None,
        search: Annotated[str | None, Field(max_length=200)] = None,
        sort: SortArg = "fpta",
        limit: Annotated[int, Field(ge=1, le=MAX_LIMIT)] = 20,
        offset: Annotated[int, Field(ge=0)] = 0,
    ) -> dict[str, Any]:
        strategy_uuid = optional_id(strategy_id, "strategy_id")
        filters: dict = {}
        if status:
            filters["status"] = {"op": "eq", "value": status}
        if min_fpta is not None:
            filters["fpta_overall"] = {"op": "gte", "value": min_fpta}
        with tool_call() as call:
            if strategy_uuid is not None:
                crm_service.owned_strategy(call.db, strategy_uuid, call.user)
            page = crm_service.grid_page(
                call.db, call.user, strategy_id=strategy_uuid, filters=filters,
                sort=_SORTS[sort], search=plain_arg(search) or None,
                limit=limit, offset=offset)
        items = [lead_list_item(row) for row in page["items"]]
        return {
            "summary": (f"{len(items)} of {page['total']} leads "
                        f"(offset {offset}); has_more={page['has_more']}."),
            "total": page["total"], "limit": limit, "offset": offset,
            "has_more": page["has_more"], "items": items,
            "untrusted_content_notice": untrusted.NOTICE,
        }

    @server.tool(
        name="get_lead",
        title="Get lead",
        description=(
            "Returns one lead by id: status, the F-P-T-A score (overall, fit, "
            "problem, timing, access, band, method), and its research: the score "
            "reasons and signals, recent LinkedIn posts, company news and "
            "enrichment data."),
        annotations=read_only("Get lead"),
    )
    def get_lead(lead_id: str) -> dict[str, Any]:
        lead_uuid = parse_id(lead_id, "lead_id")
        with tool_call() as call:
            out = lead_detail(crm_service.owned_lead(call.db, lead_uuid, call.user))
        return {"summary": lead_summary(out), **out,
                "untrusted_content_notice": untrusted.NOTICE}

    @server.tool(
        name="get_claim_verification",
        title="Get claim verification",
        description=(
            "Returns the claim-verification log for one lead: each factual claim "
            "Vectorae checked in a drafted message, the verdict (verified, "
            "stripped or rewritten), how it was checked, and the evidence. "
            "Newest first; limit 1-50."),
        annotations=read_only("Get claim verification"),
    )
    def get_claim_verification(
        lead_id: str,
        limit: Annotated[int, Field(ge=1, le=MAX_LIMIT)] = 20,
    ) -> dict[str, Any]:
        lead_uuid = parse_id(lead_id, "lead_id")
        with tool_call() as call:
            crm_service.owned_lead(call.db, lead_uuid, call.user)
            rows = claim_verification.lead_log(call.db, lead_uuid, limit=limit)
        counts: dict[str, int] = {}
        items = []
        for row in rows:
            counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
            items.append({
                "id": row["id"], "message_id": row["message_id"],
                "channel": row["channel"], "field": row["field"],
                "category": row["category"], "verdict": row["verdict"],
                "extractor": row["extractor"], "evidence_source": row["evidence_source"],
                "created_at": row["created_at"],
                **untrusted.envelope({
                    "claim_text": row["claim_text"],
                    "evidence_excerpt": row["evidence_excerpt"],
                    "replacement_text": row["replacement_text"],
                    "unsupported": row["unsupported"],
                }),
            })
        verdicts = ", ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "none"
        return {
            "summary": f"{len(items)} claim checks for lead {lead_uuid} ({verdicts}).",
            "lead_id": str(lead_uuid), "verdict_counts": counts, "items": items,
            "untrusted_content_notice": untrusted.NOTICE,
        }
