"""`search` and `fetch`: the two read-only tools ChatGPT needs for Deep
Research and for use outside Developer Mode. Names and result shapes are
fixed by OpenAI: search(query) -> {results: [{id, title, url}]} and
fetch(id) -> {id, title, text, url, metadata}.

Search covers leads (crm_service.grid_page, tenant-scoped). Ids are
"lead:<uuid>"; fetch also accepts a bare lead UUID.
"""

from __future__ import annotations

from typing import Any

import json

from mcp.server.mcpserver.exceptions import ToolError

from app.mcp import untrusted
from app.mcp.context import tool_call
from app.mcp.tools import parse_id, plain_arg, read_only
from app.mcp.tools.leads import lead_detail, lead_summary, lead_url
from app.services import crm_service

SEARCH_LIMIT = 10
LEAD_PREFIX = "lead:"


def _title(full_name, company) -> str:
    parts = [p for p in (full_name, company) if p]
    return untrusted.clean(" — ".join(parts) or "Unnamed lead", max_text=200)


def register(server) -> None:

    @server.tool(
        name="search",
        title="Search Vectorae",
        description=(
            "Searches the signed-in user's Vectorae leads by name, company, email "
            "or title. Returns up to 10 results, each with an id for fetch, a title "
            "and a link."),
        annotations=read_only("Search Vectorae"),
    )
    def search(query: str) -> dict[str, Any]:
        needle = str(plain_arg(query) or "").strip()[:200]
        if not needle:
            raise ToolError("query must contain the text to search for.")
        with tool_call() as call:
            page = crm_service.grid_page(call.db, call.user, search=needle,
                                         sort=[{"key": "fpta_overall", "dir": "desc"}],
                                         limit=SEARCH_LIMIT)
        return {"results": [
            {"id": f"{LEAD_PREFIX}{row['id']}",
             "title": _title(row["full_name"], row["company"]),
             "url": lead_url(row["id"])}
            for row in page["items"]
        ]}

    @server.tool(
        name="fetch",
        title="Fetch Vectorae record",
        description=(
            "Returns the full record for one id from search: the lead's status, "
            "F-P-T-A score and research as text, plus a link and metadata."),
        annotations=read_only("Fetch Vectorae record"),
    )
    def fetch(id: str) -> dict[str, Any]:  # noqa: A002 -- the name OpenAI requires
        raw = str(plain_arg(id) or "").strip()
        lead_uuid = parse_id(raw.removeprefix(LEAD_PREFIX), "id")
        with tool_call() as call:
            out = lead_detail(crm_service.owned_lead(call.db, lead_uuid, call.user))
        third_party = out.pop(untrusted.KEY)
        text = "\n".join([
            lead_summary(out),
            "F-P-T-A: " + json.dumps(out["fpta"]),
            untrusted.NOTICE,
            untrusted.as_text(third_party),
        ])
        return {
            "id": f"{LEAD_PREFIX}{out['id']}",
            "title": _title(third_party.get("full_name"), third_party.get("company")),
            "text": text,
            "url": out["url"],
            "metadata": {"type": "lead", "status": out["status"],
                         "strategy_id": out["strategy_id"],
                         "fpta_overall": out["fpta"]["overall"],
                         "fpta_band": out["fpta"]["band"]},
        }
