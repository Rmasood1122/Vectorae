"""Connector read tools through the SDK's in-memory client.

For every tool: a valid call, an invalid call, a cross-tenant call (nothing
of another account's comes back), a suspended account (refused), and the
pagination boundary for list tools. Plus the untrusted-content contract:
prospect text appears only under `untrusted_content`, escaped.
"""

from __future__ import annotations

import json

import pytest

from tests.mcp.conftest import INJECTION, call_tool, data, error_text, list_tools

READ_TOOLS = {
    "list_leads", "get_lead", "list_strategies", "get_strategy",
    "get_pipeline_status", "list_messages", "get_message",
    "get_attribution_summary", "get_claim_verification", "search", "fetch",
    "list_products",
}


# ---------------------------------------------------------------------------
# Tool metadata (directory-review requirements)
# ---------------------------------------------------------------------------


WRITE_TOOLS = {"create_strategy", "pause_campaign", "resume_campaign"}


def test_every_read_tool_is_listed_with_a_title_and_read_only_hint(world):
    tools = {t.name: t for t in list_tools(world.me).tools}
    assert set(tools) == READ_TOOLS | WRITE_TOOLS
    for name, tool in tools.items():
        if name in WRITE_TOOLS:
            continue      # tests/mcp/test_write_tools.py
        assert len(name) <= 64
        assert tool.title, name
        assert tool.description and len(tool.description) > 40, name
        assert tool.annotations.read_only_hint is True, name
        assert tool.annotations.destructive_hint is False, name
        assert tool.annotations.title == tool.title, name


def test_no_tool_takes_a_free_form_method_or_path(world):
    for tool in list_tools(world.me).tools:
        props = set((tool.input_schema or {}).get("properties", {}))
        assert not props & {"method", "path", "url", "sql", "endpoint"}, tool.name


def test_list_tools_paging_params_are_capped_at_50(world):
    for tool in list_tools(world.me).tools:
        limit = (tool.input_schema or {}).get("properties", {}).get("limit")
        if limit is not None:
            assert limit.get("maximum") == 50, tool.name


# ---------------------------------------------------------------------------
# Valid calls
# ---------------------------------------------------------------------------


def test_list_leads_returns_only_my_leads_best_first(world):
    out = data(call_tool(world.me, "list_leads"))
    assert out["total"] == 3
    ids = [i["id"] for i in out["items"]]
    assert ids == [str(lead.id) for lead in world.mine.leads]   # fpta 90, 89, 88
    assert out["items"][0]["fpta_band"] == "strong"


def test_list_leads_filters(world):
    out = data(call_tool(world.me, "list_leads", {"min_fpta": 89}))
    assert out["total"] == 2
    out = data(call_tool(world.me, "list_leads", {"search": "Company a2"}))
    assert [i["id"] for i in out["items"]] == [str(world.mine.leads[2].id)]
    out = data(call_tool(world.me, "list_leads", {"status": "contacted"}))
    assert out["total"] == 0
    out = data(call_tool(world.me, "list_leads",
                         {"strategy_id": str(world.mine.strategy.id)}))
    assert out["total"] == 3


def test_get_lead_returns_score_and_research(world):
    lead = world.mine.leads[1]
    out = data(call_tool(world.me, "get_lead", {"lead_id": str(lead.id)}))
    assert out["id"] == str(lead.id)
    assert out["fpta"]["overall"] == 89
    assert out["fpta"]["fit"] == 80
    assert out["untrusted_content"]["company"] == "Company a1"
    assert out["untrusted_content"]["enrichment"]["organization"]["short_description"]


def test_list_strategies(world):
    out = data(call_tool(world.me, "list_strategies"))
    assert [i["id"] for i in out["items"]] == [str(world.mine.strategy.id)]
    assert out["items"][0]["campaign_state"] == "active"


def test_get_strategy(world):
    out = data(call_tool(world.me, "get_strategy",
                         {"strategy_id": str(world.mine.strategy.id)}))
    assert out["status"] == "pending"
    assert out["budget_usd"] == 15.0
    assert out["product_name"] == "Product a"


def test_get_pipeline_status(world):
    out = data(call_tool(world.me, "get_pipeline_status",
                         {"strategy_id": str(world.mine.strategy.id)}))
    assert out["steps_done"] == 0
    assert out["planned_steps"] > 0
    assert out["progress"][0]["phases"][0]["phase"] == 1


def test_list_messages_without_text(world):
    out = data(call_tool(world.me, "list_messages"))
    assert out["total"] == 3
    assert {i["id"] for i in out["items"]} == {str(x.id) for x in world.mine.messages}
    assert all("body" not in i and "subject" not in i for i in out["items"])
    out = data(call_tool(world.me, "list_messages",
                         {"lead_id": str(world.mine.leads[0].id), "status": "sent"}))
    assert out["total"] == 1


def test_get_message_has_its_text_as_untrusted(world):
    msg = world.mine.messages[0]
    out = data(call_tool(world.me, "get_message", {"message_id": str(msg.id)}))
    assert out["status"] == "sent"
    assert "body" not in out
    assert "ignore previous instructions" in out["untrusted_content"]["body"]


def test_get_attribution_summary(world):
    out = data(call_tool(world.me, "get_attribution_summary"))
    assert out["total"] == 1 and out["certain"] == 1
    assert out["recent"][0]["lead_id"] == str(world.mine.leads[0].id)


def test_get_claim_verification(world):
    out = data(call_tool(world.me, "get_claim_verification",
                         {"lead_id": str(world.mine.leads[0].id)}))
    assert out["verdict_counts"] == {"stripped": 1}
    assert out["items"][0]["verdict"] == "stripped"


def test_search_and_fetch(world):
    from app.config import settings

    out = data(call_tool(world.me, "search", {"query": "Company a1"}))
    assert out["results"] == [{
        "id": f"lead:{world.mine.leads[1].id}",
        "title": "Lead a1 — Company a1",
        "url": (f"{settings.frontend_url.rstrip('/')}/leads/detail"
                f"?id={world.mine.leads[1].id}"),
    }]
    fetched = data(call_tool(world.me, "fetch", {"id": out["results"][0]["id"]}))
    assert fetched["id"] == f"lead:{world.mine.leads[1].id}"
    assert fetched["metadata"]["fpta_overall"] == 89
    assert "<<<EXTERNAL_TEXT" in fetched["text"]
    assert fetched["url"].endswith(f"/leads/detail?id={world.mine.leads[1].id}")


def test_chatgpt_double_encoded_arguments_are_accepted(world):
    lead_id = f"lead:{world.mine.leads[1].id}"
    out = data(call_tool(world.me, "search", {"query": json.dumps({"query": "Company a1"})}))
    assert out["results"][0]["id"] == lead_id
    out = data(call_tool(world.me, "fetch", {"id": json.dumps(lead_id)}))
    assert out["id"] == lead_id
    out = data(call_tool(world.me, "list_leads", {"limit": "2"}))
    assert len(out["items"]) == 2


# ---------------------------------------------------------------------------
# Invalid input -> actionable error, never a crash
# ---------------------------------------------------------------------------

BAD_ID_CALLS = [
    ("get_lead", {"lead_id": "not-an-id"}, "lead_id must be a Vectorae id"),
    ("get_strategy", {"strategy_id": "42"}, "strategy_id must be a Vectorae id"),
    ("get_pipeline_status", {"strategy_id": "x"}, "strategy_id must be a Vectorae id"),
    ("get_message", {"message_id": "x"}, "message_id must be a Vectorae id"),
    ("get_claim_verification", {"lead_id": "x"}, "lead_id must be a Vectorae id"),
    ("fetch", {"id": "lead:nope"}, "id must be a Vectorae id"),
    ("list_leads", {"strategy_id": "x"}, "strategy_id must be a Vectorae id"),
    ("list_messages", {"lead_id": "x"}, "lead_id must be a Vectorae id"),
    ("get_attribution_summary", {"strategy_id": "x"}, "strategy_id must be a Vectorae id"),
    ("search", {"query": "   "}, "query must contain"),
    ("list_leads", {"status": "won"}, "status"),
    ("list_messages", {"channel": "fax"}, "channel"),
    ("list_leads", {"min_fpta": 101}, "min_fpta"),
    ("get_lead", {}, "lead_id"),
]


@pytest.mark.parametrize("name,args,expected", BAD_ID_CALLS,
                         ids=[f"{c[0]}-{i}" for i, c in enumerate(BAD_ID_CALLS)])
def test_invalid_input_gets_an_actionable_error(world, name, args, expected):
    text = error_text(call_tool(world.me, name, args))
    assert expected in text
    assert "Traceback" not in text and "Internal Server Error" not in text


# ---------------------------------------------------------------------------
# Cross-tenant: another account's ids answer "not found"; lists never leak
# ---------------------------------------------------------------------------


def _theirs(world):
    t = world.theirs
    return [
        ("get_lead", {"lead_id": str(t.leads[0].id)}, "lead not found"),
        ("get_claim_verification", {"lead_id": str(t.leads[0].id)}, "lead not found"),
        ("fetch", {"id": f"lead:{t.leads[0].id}"}, "lead not found"),
        ("get_strategy", {"strategy_id": str(t.strategy.id)}, "strategy not found"),
        ("get_pipeline_status", {"strategy_id": str(t.strategy.id)}, "strategy not found"),
        ("get_message", {"message_id": str(t.messages[0].id)}, "message not found"),
        ("list_leads", {"strategy_id": str(t.strategy.id)}, "strategy not found"),
        ("list_messages", {"strategy_id": str(t.strategy.id)}, "strategy not found"),
        ("list_messages", {"lead_id": str(t.leads[0].id)}, "lead not found"),
        ("get_attribution_summary", {"strategy_id": str(t.strategy.id)},
         "strategy not found"),
    ]


def test_another_accounts_ids_are_not_found(world):
    for name, args, expected in _theirs(world):
        assert expected in error_text(call_tool(world.me, name, args)), name


def test_lists_and_search_never_include_another_account(world):
    their_ids = ({str(x.id) for x in world.theirs.leads}
                 | {str(x.id) for x in world.theirs.messages}
                 | {str(world.theirs.strategy.id)})
    for name, args in [("list_leads", {}), ("list_strategies", {}),
                       ("list_messages", {}), ("get_attribution_summary", {}),
                       ("search", {"query": "Company b"})]:
        blob = json.dumps(data(call_tool(world.me, name, args)))
        assert not any(i in blob for i in their_ids), name
    assert data(call_tool(world.me, "search", {"query": "Company b"}))["results"] == []
    # ...and the other account sees only its own.
    assert data(call_tool(world.other, "list_leads"))["total"] == 2


# ---------------------------------------------------------------------------
# Account state: suspended / unverified / erased are refused on every tool
# ---------------------------------------------------------------------------


def _valid_calls(world):
    mine = world.mine
    return [
        ("list_leads", {}), ("get_lead", {"lead_id": str(mine.leads[0].id)}),
        ("list_strategies", {}), ("get_strategy", {"strategy_id": str(mine.strategy.id)}),
        ("get_pipeline_status", {"strategy_id": str(mine.strategy.id)}),
        ("list_messages", {}), ("get_message", {"message_id": str(mine.messages[0].id)}),
        ("get_attribution_summary", {}),
        ("get_claim_verification", {"lead_id": str(mine.leads[0].id)}),
        ("search", {"query": "Lead"}), ("fetch", {"id": f"lead:{mine.leads[0].id}"}),
        ("list_products", {}),
    ]


def test_suspended_account_is_refused_by_every_tool(world, db_session):
    calls = _valid_calls(world)
    assert {c[0] for c in calls} == READ_TOOLS
    world.me.is_suspended = True
    db_session.commit()
    for name, args in calls:
        assert "account suspended" in error_text(call_tool(world.me, name, args)), name


def test_unverified_account_is_refused(world, db_session):
    world.me.email_verified = False
    db_session.commit()
    assert "EMAIL_NOT_VERIFIED" in error_text(call_tool(world.me, "list_leads"))


def test_token_without_read_scope_is_refused(world):
    text = error_text(call_tool(world.me, "list_leads", scope="offline_access"))
    assert "leadpilot:read" in text


# ---------------------------------------------------------------------------
# Pagination boundaries
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name,total", [("list_leads", 3), ("list_messages", 3)])
def test_pagination_boundary(world, name, total):
    first = data(call_tool(world.me, name, {"limit": 2}))
    assert len(first["items"]) == 2 and first["has_more"] is True
    second = data(call_tool(world.me, name, {"limit": 2, "offset": 2}))
    assert len(second["items"]) == total - 2 and second["has_more"] is False
    assert not {i["id"] for i in first["items"]} & {i["id"] for i in second["items"]}
    past = data(call_tool(world.me, name, {"limit": 2, "offset": total}))
    assert past["items"] == [] and past["has_more"] is False
    assert data(call_tool(world.me, name, {"limit": 50}))["total"] == total
    assert "limit" in error_text(call_tool(world.me, name, {"limit": 51}))
    assert "limit" in error_text(call_tool(world.me, name, {"limit": 0}))


def test_list_strategies_pagination_boundary(world, db_session):
    from app.db import models as m

    for _ in range(2):
        db_session.add(m.Strategy(product_id=world.mine.product.id,
                                  flow_type=m.FlowType.NO_CLIENTS))
    db_session.commit()
    first = data(call_tool(world.me, "list_strategies", {"limit": 2}))
    assert first["total"] == 3 and len(first["items"]) == 2 and first["has_more"]
    rest = data(call_tool(world.me, "list_strategies", {"limit": 2, "offset": 2}))
    assert len(rest["items"]) == 1 and not rest["has_more"]
    assert "limit" in error_text(call_tool(world.me, "list_strategies", {"limit": 51}))


# ---------------------------------------------------------------------------
# Untrusted content
# ---------------------------------------------------------------------------


def test_prospect_text_only_appears_escaped_under_untrusted_content(world):
    lead = world.mine.leads[0]           # full_name and post carry INJECTION
    out = data(call_tool(world.me, "get_lead", {"lead_id": str(lead.id)}))
    assert "Dana" not in out["summary"]
    outside = {k: v for k, v in out.items() if k != "untrusted_content"}
    assert "ignore previous" not in json.dumps(outside)
    name = out["untrusted_content"]["full_name"]
    assert "ignore previous instructions" in name
    assert "<<<" not in name and ">>>" not in name       # delimiters neutralised
    post = out["untrusted_content"]["linkedin_posts"][0]["text"]
    assert "<<<" not in post and ">>>" not in post


def test_list_items_keep_names_under_untrusted_content(world):
    out = data(call_tool(world.me, "list_leads"))
    for item in out["items"]:
        assert "full_name" not in item and "company" not in item
        assert "full_name" in item["untrusted_content"]
    assert "ignore previous" not in out["summary"]


def test_fetch_text_wraps_third_party_content_in_one_delimited_block(world):
    lead = world.mine.leads[0]
    out = data(call_tool(world.me, "fetch", {"id": f"lead:{lead.id}"}))
    text = out["text"]
    assert text.count("<<<EXTERNAL_TEXT") == 1 and text.count("EXTERNAL_TEXT>>>") == 1
    block = text[text.index("<<<EXTERNAL_TEXT"):]
    assert "ignore previous instructions" in block
    assert "ignore previous" not in text[:text.index("<<<EXTERNAL_TEXT")]
    assert INJECTION not in out["title"]
