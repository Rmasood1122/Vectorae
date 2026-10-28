"""Connector write tools (Phase 3): create_strategy, pause_campaign,
resume_campaign -- and list_products, which create_strategy needs.

What must hold: each is destructive and needs leadpilot:write (HTTP 403
scope challenge otherwise); each goes through the same service as the web
app's route, so a plan limit, a rate limit or a deliverability block refuses
it with the same detail the route gives; another account's strategy is "not
found" and untouched; every call is audited with actor_channel="mcp" and
the OAuth client id.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db import models as m
from tests.mcp.conftest import call_tool, data, error_text, list_tools, mcp_token

WRITE_TOOLS = {"create_strategy", "pause_campaign", "resume_campaign"}
WRITE = "leadpilot:read leadpilot:write"
MCP_HEADERS = {"Accept": "application/json, text/event-stream",
               "Content-Type": "application/json", "MCP-Protocol-Version": "2025-11-25"}


@pytest.fixture()
def pipelines(monkeypatch, enqueued):
    """Pipeline enqueues are recorded, never sent to Celery."""
    from app.workers import tasks as worker_tasks

    monkeypatch.setattr(worker_tasks.run_pipeline, "delay",
                        lambda strategy_id: enqueued.append(strategy_id), raising=False)
    return enqueued


def _audit_rows(db, action):
    db.expire_all()
    return db.execute(select(m.SecurityAuditEvent)
                      .where(m.SecurityAuditEvent.action == action)).scalars().all()


def _http_call(client, token, name, arguments):
    return client.post("/mcp", headers={**MCP_HEADERS, "Authorization": f"Bearer {token}"},
                       json={"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                             "params": {"name": name, "arguments": arguments}})


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------


def test_write_tools_are_destructive_and_titled(world):
    tools = {t.name: t for t in list_tools(world.me).tools}
    assert WRITE_TOOLS <= set(tools)
    for name in WRITE_TOOLS:
        annotations = tools[name].annotations
        assert tools[name].title and annotations.title == tools[name].title
        assert annotations.destructive_hint is True, name
        assert annotations.read_only_hint is False, name
    assert tools["list_products"].annotations.read_only_hint is True
    assert "send" not in {n for n in tools if "send" in n}  # no send/schedule tool exists
    assert not {n for n in tools if "send" in n or "schedule" in n or "approve" in n}


# ---------------------------------------------------------------------------
# list_products
# ---------------------------------------------------------------------------


def test_list_products_is_tenant_scoped(world):
    out = data(call_tool(world.me, "list_products"))
    assert [p["id"] for p in out["items"]] == [str(world.mine.product.id)]
    assert out["items"][0]["strategies"] == 1
    other = data(call_tool(world.other, "list_products"))
    assert [p["id"] for p in other["items"]] == [str(world.theirs.product.id)]
    assert "limit" in error_text(call_tool(world.me, "list_products", {"limit": 51}))


# ---------------------------------------------------------------------------
# create_strategy
# ---------------------------------------------------------------------------


def test_create_strategy_creates_and_enqueues(world, pipelines, db_session):
    out = data(call_tool(world.me, "create_strategy",
                         {"product_id": str(world.mine.product.id), "max_budget_usd": 5},
                         scope=WRITE))
    assert out["flow_type"] == "no_clients" and out["status"] == "pending"
    assert out["budget_usd"] == 5.0
    assert pipelines == [out["id"]]
    row = db_session.get(m.Strategy, __import__("uuid").UUID(out["id"]))
    assert row is not None and row.product_id == world.mine.product.id
    rows = _audit_rows(db_session, "mcp.create_strategy")
    assert len(rows) == 1
    assert rows[0].details_json == {"actor_channel": "mcp", "oauth_client_id": "test-client",
                                    "outcome": "created"}
    assert rows[0].user_id == world.me.id and str(rows[0].target_id) == out["id"]


def test_create_strategy_invalid_input(world, pipelines):
    assert "product_id must be a Vectorae id" in error_text(
        call_tool(world.me, "create_strategy", {"product_id": "x"}, scope=WRITE))
    assert "flow_type" in error_text(call_tool(
        world.me, "create_strategy",
        {"product_id": str(world.mine.product.id), "flow_type": "both"}, scope=WRITE))
    # Flow 1 needs past clients: the route's own 422 text.
    text = error_text(call_tool(
        world.me, "create_strategy",
        {"product_id": str(world.mine.product.id), "flow_type": "with_clients"}, scope=WRITE))
    assert "requires at least one past client" in text
    assert pipelines == []


def test_create_strategy_on_another_accounts_product_is_not_found(world, pipelines, db_session):
    text = error_text(call_tool(world.me, "create_strategy",
                                {"product_id": str(world.theirs.product.id)}, scope=WRITE))
    assert "product not found" in text
    assert pipelines == []
    assert db_session.execute(select(m.Strategy).where(
        m.Strategy.product_id == world.theirs.product.id)).scalars().all() == \
        [world.theirs.strategy]
    rows = _audit_rows(db_session, "mcp.create_strategy")
    assert [r.details_json["outcome"] for r in rows] == ["refused_404"]


def test_plan_limit_gives_the_same_error_as_the_http_route(client, world, pipelines,
                                                          db_session):
    from app.core.plans import PLANS

    limit = PLANS[world.me.plan.value if hasattr(world.me.plan, "value") else world.me.plan][
        "max_strategies"]
    assert limit > 0
    for _ in range(limit - 1):           # world.mine already has one strategy
        db_session.add(m.Strategy(product_id=world.mine.product.id,
                                  flow_type=m.FlowType.NO_CLIENTS))
    db_session.commit()

    route = client.post(f"/products/{world.mine.product.id}/strategies", json={})
    assert route.status_code == 402
    detail = route.json()["detail"]
    assert detail["error"] == "plan_limit_exceeded"

    text = error_text(call_tool(world.me, "create_strategy",
                                {"product_id": str(world.mine.product.id)}, scope=WRITE))
    # The SDK frames a tool error as "Error executing tool <name>: <detail>".
    assert text.endswith(": " + detail["message"])
    assert pipelines == []
    rows = _audit_rows(db_session, "mcp.create_strategy")
    assert rows[-1].details_json["outcome"] == "refused_402"


def test_rate_limit_is_shared_with_the_web_app(client, world, pipelines, monkeypatch,
                                               fake_redis):
    from app.core.config import settings as core_settings

    monkeypatch.setattr("app.core.redis_client.get_sync_redis", lambda: fake_redis)
    monkeypatch.setattr(core_settings, "RATE_LIMIT_STRATEGIES", 1)
    first = client.post(f"/products/{world.mine.product.id}/strategies", json={})
    assert first.status_code == 202
    text = error_text(call_tool(world.me, "create_strategy",
                                {"product_id": str(world.mine.product.id)}, scope=WRITE))
    assert "rate limit exceeded" in text and "retry_after=" in text
    assert len(pipelines) == 1


def test_create_strategy_without_write_scope_is_refused(world, pipelines):
    text = error_text(call_tool(world.me, "create_strategy",
                                {"product_id": str(world.mine.product.id)}))
    assert "leadpilot:write" in text
    assert pipelines == []


def test_http_scope_challenge_for_a_read_only_token(client, world, pipelines):
    r = _http_call(client, mcp_token(world.me), "create_strategy",
                   {"product_id": str(world.mine.product.id)})
    assert r.status_code == 403
    header = r.headers["www-authenticate"]
    assert 'error="insufficient_scope"' in header
    assert 'scope="leadpilot:read leadpilot:write"' in header
    assert 'resource_metadata="http://testserver/.well-known/oauth-protected-resource/mcp"' \
        in header
    assert pipelines == []
    # A read tool with the same token is unaffected, and a write token passes.
    assert _http_call(client, mcp_token(world.me), "list_products", {}).status_code == 200
    ok = _http_call(client, mcp_token(world.me, scope=WRITE), "create_strategy",
                    {"product_id": str(world.mine.product.id)})
    assert ok.status_code == 200 and ok.json()["result"]["isError"] is False


# ---------------------------------------------------------------------------
# pause / resume
# ---------------------------------------------------------------------------


def test_pause_then_resume(world, db_session, monkeypatch):
    from app.workers import notification_tasks

    events = []
    monkeypatch.setattr(notification_tasks, "enqueue_event",
                        lambda *a, **k: events.append((a, k)))
    sid = str(world.mine.strategy.id)
    out = data(call_tool(world.me, "pause_campaign", {"strategy_id": sid}, scope=WRITE))
    assert out["campaign_state"] == "paused_manual"
    db_session.expire_all()
    assert db_session.get(m.Strategy, world.mine.strategy.id).campaign_state == "paused_manual"
    assert events and events[0][0][1] == "campaign_paused"     # same webhook as the route

    out = data(call_tool(world.me, "resume_campaign", {"strategy_id": sid}, scope=WRITE))
    assert out["campaign_state"] == "active"
    db_session.expire_all()
    assert db_session.get(m.Strategy, world.mine.strategy.id).campaign_state == "active"
    assert [r.details_json["outcome"] for r in _audit_rows(db_session, "mcp.pause_campaign")] \
        == ["paused"]
    assert [r.details_json["outcome"] for r in _audit_rows(db_session, "mcp.resume_campaign")] \
        == ["resumed"]


def test_resume_blocked_by_the_preflight_gives_the_routes_409_detail(client, world,
                                                                      monkeypatch, db_session):
    from app.services import deliverability_autopilot

    monkeypatch.setattr(deliverability_autopilot, "launch_block",
                        lambda db, strategy, action: "SPF record missing for example.com")
    route = client.post(f"/strategies/{world.mine.strategy.id}/campaign/resume")
    assert route.status_code == 409
    text = error_text(call_tool(world.me, "resume_campaign",
                                {"strategy_id": str(world.mine.strategy.id)}, scope=WRITE))
    assert text.endswith(": " + route.json()["detail"])
    assert _audit_rows(db_session, "mcp.resume_campaign")[-1].details_json["outcome"] == \
        "refused_409"


@pytest.mark.parametrize("tool", ["pause_campaign", "resume_campaign"])
def test_another_accounts_campaign_is_not_found_and_untouched(world, db_session, tool):
    theirs = world.theirs.strategy
    theirs.campaign_state = "paused_manual"
    db_session.commit()
    text = error_text(call_tool(world.me, tool, {"strategy_id": str(theirs.id)}, scope=WRITE))
    assert "strategy not found" in text
    db_session.expire_all()
    assert db_session.get(m.Strategy, theirs.id).campaign_state == "paused_manual"


@pytest.mark.parametrize("tool,args", [
    ("pause_campaign", {"strategy_id": "nope"}),
    ("resume_campaign", {}),
])
def test_invalid_input(world, tool, args):
    text = error_text(call_tool(world.me, tool, args, scope=WRITE))
    assert "strategy_id" in text


@pytest.mark.parametrize("tool", sorted(WRITE_TOOLS))
def test_suspended_account_is_refused(world, db_session, pipelines, tool):
    world.me.is_suspended = True
    db_session.commit()
    args = ({"product_id": str(world.mine.product.id)} if tool == "create_strategy"
            else {"strategy_id": str(world.mine.strategy.id)})
    assert "account suspended" in error_text(call_tool(world.me, tool, args, scope=WRITE))
    db_session.expire_all()
    assert db_session.get(m.Strategy, world.mine.strategy.id).campaign_state == "active"
    assert pipelines == []
