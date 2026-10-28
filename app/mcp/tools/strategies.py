"""Strategy tools: list_strategies, get_strategy, get_pipeline_status.

A strategy is one research run for a product plus its outreach campaign
(there is no separate Campaign model: the campaign's state is
Strategy.campaign_state). Reads go through strategy_read and
crm_service.owned_strategy (tenant filter on Product.user_id).
"""

from __future__ import annotations

from typing import Any, Annotated

from pydantic import Field

from app.config import settings
from app.db.models import Product, Strategy
from app.mcp import untrusted
from app.mcp.context import tool_call
from app.mcp.tools import MAX_LIMIT, iso, parse_id, read_only
from app.services import crm_service, strategy_read


def strategy_url(strategy_id) -> str:
    return f"{settings.frontend_url.rstrip('/')}/strategies/detail?id={strategy_id}"


def strategy_out(db, strategy: Strategy) -> dict:
    product = db.get(Product, strategy.product_id)
    status = strategy_read.pipeline_status(db, strategy)
    return {
        "id": str(strategy.id),
        "product_id": str(strategy.product_id),
        "product_name": product.name if product else None,
        "flow_type": strategy.flow_type.value,
        "status": strategy.status.value,
        "campaign_state": strategy.campaign_state,
        "campaign_pause_reason": strategy.campaign_pause_reason,
        "created_at": iso(strategy.created_at),
        "budget_usd": status["budget_usd"],
        "spent_usd": status["spent_usd"],
        "planned_steps": status["planned_steps"],
        "strategy_document_ready": status["strategy_document_ready"],
        "gtm_document_ready": status["gtm_document_ready"],
        "url": strategy_url(strategy.id),
    }


def register(server) -> None:

    @server.tool(
        name="list_strategies",
        title="List strategies",
        description=(
            "Lists the signed-in user's Vectorae strategies (research runs and "
            "their outreach campaigns), newest first, with pipeline status and "
            "campaign state (active, paused_manual or paused_bounce_rate). "
            "Use offset/limit (limit 1-50) to page."),
        annotations=read_only("List strategies"),
    )
    def list_strategies(
        limit: Annotated[int, Field(ge=1, le=MAX_LIMIT)] = 20,
        offset: Annotated[int, Field(ge=0)] = 0,
    ) -> dict[str, Any]:
        with tool_call() as call:
            page = strategy_read.list_owned(call.db, call.user, limit=limit, offset=offset)
        items = [{**row, "url": strategy_url(row["id"])} for row in page["items"]]
        has_more = offset + len(items) < page["total"]
        return {
            "summary": (f"{len(items)} of {page['total']} strategies "
                        f"(offset {offset}); has_more={has_more}."),
            "total": page["total"], "limit": limit, "offset": offset,
            "has_more": has_more, "items": items,
        }

    @server.tool(
        name="get_strategy",
        title="Get strategy",
        description=(
            "Returns one strategy by id: product, flow type, pipeline status, "
            "campaign state and pause reason, budget and spend in USD, and whether "
            "the strategy and GTM documents are ready."),
        annotations=read_only("Get strategy"),
    )
    def get_strategy(strategy_id: str) -> dict[str, Any]:
        strategy_uuid = parse_id(strategy_id, "strategy_id")
        with tool_call() as call:
            out = strategy_out(
                call.db, crm_service.owned_strategy(call.db, strategy_uuid, call.user))
        return {
            "summary": (f"Strategy {out['id']}: status {out['status']}, campaign "
                        f"{out['campaign_state']}, spent ${out['spent_usd']:.2f}"
                        + (f" of ${out['budget_usd']:.2f}." if out["budget_usd"] is not None
                           else ".")),
            **out,
        }

    @server.tool(
        name="get_pipeline_status",
        title="Get pipeline status",
        description=(
            "Returns the research-pipeline progress of one strategy: steps done per "
            "pipeline and phase, verification passes, the error if the run failed, "
            "and budget versus spend."),
        annotations=read_only("Get pipeline status"),
    )
    def get_pipeline_status(strategy_id: str) -> dict[str, Any]:
        strategy_uuid = parse_id(strategy_id, "strategy_id")
        with tool_call() as call:
            strategy = crm_service.owned_strategy(call.db, strategy_uuid, call.user)
            status = strategy_read.pipeline_status(call.db, strategy)
        verification = []
        notes = []
        for entry in status["verification"]:
            if not isinstance(entry, dict):
                continue
            verification.append({k: entry.get(k) for k in
                                 ("pass_no", "name", "attempt", "result", "ts")})
            if entry.get("fix_description"):
                notes.append({"pass_no": entry.get("pass_no"),
                              "fix_description": entry["fix_description"]})
        done = sum(p["done"] for p in status["progress"])
        out = {
            "id": str(status["id"]),
            "flow_type": status["flow_type"].value,
            "status": status["status"].value,
            "steps_done": done,
            "planned_steps": status["planned_steps"],
            "progress": status["progress"],
            "verification": verification,
            "error": status["error"],
            "budget_usd": status["budget_usd"],
            "spent_usd": status["spent_usd"],
            "steps_per_phase": status["steps_per_phase"],
        }
        if notes:
            out.update(untrusted.envelope({"verification_fix_notes": notes}))
            out["untrusted_content_notice"] = untrusted.NOTICE
        return {
            "summary": (f"Strategy {out['id']} pipeline: {out['status']}, "
                        f"{done} of {out['planned_steps']} steps done."),
            **out,
        }
