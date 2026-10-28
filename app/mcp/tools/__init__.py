"""Connector tools, one module per domain. Each module has `register(server)`.

Conventions every tool follows (Anthropic directory review, recon §9):
  * a `title`, and `readOnlyHint: true` (reads) or `destructiveHint: true`;
  * one purpose per tool: no generic method/path tools;
  * paginated with `limit <= 50`, and results bounded;
  * invalid input -> a ToolError that says what to change;
  * third-party text only inside `untrusted_content` (app/mcp/untrusted.py).
"""

from __future__ import annotations

import json
import uuid

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

MAX_LIMIT = 50


def read_only(title: str) -> ToolAnnotations:
    return ToolAnnotations(title=title, read_only_hint=True, destructive_hint=False,
                           idempotent_hint=True, open_world_hint=False)


def plain_arg(value):
    """ChatGPT sometimes sends an argument JSON-encoded a second time
    ('"abc"' or '{"query": "abc"}'). Unwrap one level; anything else is
    returned unchanged."""
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text or text[0] not in '"{':
        return value
    try:
        decoded = json.loads(text)
    except ValueError:
        return value
    if isinstance(decoded, str):
        return decoded
    if isinstance(decoded, dict) and len(decoded) == 1:
        inner = next(iter(decoded.values()))
        if isinstance(inner, (str, int)):
            return str(inner)
    return value


def parse_id(value, what: str) -> uuid.UUID:
    """A Vectorae id from a tool argument, or an actionable ToolError."""
    value = plain_arg(value)
    try:
        return uuid.UUID(str(value).strip())
    except (TypeError, ValueError):
        shown = str(value)[:60]
        raise ToolError(
            f"{what} must be a Vectorae id (a UUID such as "
            f"3f2b9c1e-0000-4000-8000-000000000000); got {shown!r}.") from None


def optional_id(value, what: str) -> uuid.UUID | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return parse_id(value, what)


def iso(value) -> str | None:
    return value.isoformat() if value is not None else None
