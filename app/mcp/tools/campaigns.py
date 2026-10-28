"""Write tools (Phase 3, DECISIONS.md CONN-WRITES): create_strategy,
pause_campaign, resume_campaign. Plus the read tool list_products, which
create_strategy needs to be usable.

Each write tool is `destructiveHint: true` (the client asks the person
before every call), needs the `leadpilot:write` scope, and is a thin wrapper
over the service the web app's routes call -- strategy_write.create and
campaign_control.pause/resume -- so the plan limit (402), the daily AI cap
and the F07 monthly forecast (429), the deliverability preflight (409), the
send-time gate and the per-user rate limit are exactly the web app's. A
refusal reaches the model as the same detail the web app shows.

Every call, done or refused, writes one security_audit_events row:
action "mcp.<tool>", details {actor_channel: "mcp", oauth_client_id,
outcome} (DECISIONS.md CONN-AUDIT).

Nothing here sends or schedules a message. Resuming lets an already
approved campaign's scheduled messages continue through the full send-time
gate, daily send approval included.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import HTTPException
from pydantic import Field

from app.core.rate_limiting import enforce_rate_limit
from app.db.models import FlowType
from app.mcp import scopes
from app.mcp.context import ToolCall, http_detail, tool_call
from app.mcp.tools import MAX_LIMIT, parse_id, read_only
from app.mcp.tools.strategies import strategy_url
from app.services import (
    campaign_control,
    crm_service,
    security_audit,
    strategy_read,
    strategy_write,
)
from mcp.types import ToolAnnotations

WRITE_TOOLS = frozenset({"create_strategy", "pause_campaign", "resume_campaign"})


def destructive(title: str, *, idempotent: bool) -> ToolAnnotations:
    return ToolAnnotations(title=title, read_only_hint=False, destructive_hint=True,
                           idempotent_hint=idempotent, open_world_hint=False)


def audit(call: ToolCall, tool: str, *, target_type: str, target_id, outcome: str,
          detail: str | None = None) -> None:
    details = {"actor_channel": "mcp", "oauth_client_id": call.token.client_id,
               "outcome": outcome}
    if detail:
        details["detail"] = detail[:300]
    security_audit.record(call.db, action=security_audit.MCP_TOOL_PREFIX + tool,
                          user_id=call.user.id, owner_user_id=call.user.id,
                          target_type=target_type, target_id=target_id, details=details)
    call.db.commit()


def refused(call: ToolCall, tool: str, exc: HTTPException, *, target_type: str,
            target_id) -> HTTPException:
    """Record the refusal, then hand the same HTTPException back to
    tool_call, which turns it into the tool error the model reads."""
    call.db.rollback()
    audit(call, tool, target_type=target_type, target_id=target_id,
          outcome=f"refused_{exc.status_code}", detail=http_detail(exc))
    return exc


def register(server) -> None:

    @server.tool(
        name="list_products",
        title="List products",
        description=(
            "Lists the signed-in user's Vectorae products and skills (what a "
            "strategy is created for), with how many past clients and strategies "
            "each has. Use offset/limit (limit 1-50) to page."),
        annotations=read_only("List products"),
    )
    def list_products(
        limit: Annotated[int, Field(ge=1, le=MAX_LIMIT)] = 20,
        offset: Annotated[int, Field(ge=0)] = 0,
    ) -> dict[str, Any]:
        with tool_call() as call:
            page = strategy_read.list_products(call.db, call.user, limit=limit, offset=offset)
        has_more = offset + len(page["items"]) < page["total"]
        return {
            "summary": (f"{len(page['items'])} of {page['total']} products "
                        f"(offset {offset}); has_more={has_more}."),
            "total": page["total"], "limit": limit, "offset": offset,
            "has_more": has_more, "items": page["items"],
        }

    @server.tool(
        name="create_strategy",
        title="Create strategy",
        description=(
            "Creates a new strategy for one of the user's products and starts its "
            "AI research pipeline, which spends AI budget: up to max_budget_usd "
            "(server default when omitted), within the plan's strategy limit and the "
            "account's daily and monthly AI spend caps. flow_type: with_clients "
            "(needs past clients on the product) or no_clients; omitted, it is "
            "inferred. Does not find leads, write messages or send anything. "
            "Limited to the same hourly number of new strategies as the web app."),
        annotations=destructive("Create strategy", idempotent=False),
    )
    def create_strategy(
        product_id: str,
        flow_type: Literal["with_clients", "no_clients"] | None = None,
        max_budget_usd: Annotated[float | None, Field(gt=0, le=1000)] = None,
    ) -> dict[str, Any]:
        product_uuid = parse_id(product_id, "product_id")
        with tool_call(scope=scopes.WRITE) as call:
            try:
                enforce_rate_limit(str(call.user.id), strategy_write.RATE_LIMIT_SCOPE,
                                   strategy_write.RATE_LIMIT_SETTING)
                strategy = strategy_write.create(
                    call.db, call.user, product_uuid,
                    flow_type=FlowType(flow_type) if flow_type else None,
                    max_budget_usd=max_budget_usd)
            except HTTPException as exc:
                raise refused(call, "create_strategy", exc, target_type="product",
                              target_id=product_uuid) from None
            status = strategy_read.pipeline_status(call.db, strategy)
            audit(call, "create_strategy", target_type="strategy", target_id=strategy.id,
                  outcome="created")
            out = {
                "id": str(strategy.id), "product_id": str(strategy.product_id),
                "flow_type": strategy.flow_type.value, "status": strategy.status.value,
                "budget_usd": status["budget_usd"], "planned_steps": status["planned_steps"],
                "url": strategy_url(strategy.id),
            }
        budget = (f" with a budget of ${out['budget_usd']:.2f}"
                  if out["budget_usd"] is not None else "")
        return {"summary": (f"Strategy {out['id']} created ({out['flow_type']}){budget}; "
                            f"research has started."), **out}

    @server.tool(
        name="pause_campaign",
        title="Pause campaign",
        description=(
            "Pauses the outreach campaign of one strategy: no further message of "
            "that strategy is sent until it is resumed. Messages already sent are "
            "not affected."),
        annotations=destructive("Pause campaign", idempotent=True),
    )
    def pause_campaign(strategy_id: str) -> dict[str, Any]:
        strategy_uuid = parse_id(strategy_id, "strategy_id")
        with tool_call(scope=scopes.WRITE) as call:
            try:
                strategy = crm_service.owned_strategy(call.db, strategy_uuid, call.user)
                state = campaign_control.pause(call.db, strategy, call.user)
            except HTTPException as exc:
                raise refused(call, "pause_campaign", exc, target_type="strategy",
                              target_id=strategy_uuid) from None
            audit(call, "pause_campaign", target_type="strategy", target_id=strategy_uuid,
                  outcome="paused")
        return {"summary": f"Campaign of strategy {strategy_uuid} paused.",
                "strategy_id": str(strategy_uuid), **state}

    @server.tool(
        name="resume_campaign",
        title="Resume campaign",
        description=(
            "Resumes the paused outreach campaign of one strategy, after the "
            "deliverability preflight passes. Its scheduled messages then continue "
            "to send, each still subject to daily send approval and every "
            "compliance check at send time. Refused with the reason when the "
            "preflight blocks it."),
        annotations=destructive("Resume campaign", idempotent=True),
    )
    def resume_campaign(strategy_id: str) -> dict[str, Any]:
        strategy_uuid = parse_id(strategy_id, "strategy_id")
        with tool_call(scope=scopes.WRITE) as call:
            try:
                strategy = crm_service.owned_strategy(call.db, strategy_uuid, call.user)
                state = campaign_control.resume(call.db, strategy)
            except HTTPException as exc:
                raise refused(call, "resume_campaign", exc, target_type="strategy",
                              target_id=strategy_uuid) from None
            audit(call, "resume_campaign", target_type="strategy", target_id=strategy_uuid,
                  outcome="resumed")
        return {"summary": f"Campaign of strategy {strategy_uuid} resumed.",
                "strategy_id": str(strategy_uuid), **state}
