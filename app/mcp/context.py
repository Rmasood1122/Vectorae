"""One database session and one resolved principal per tool call.

The app's DB layer is synchronous (app/db/base.py); tools are plain `def`
functions, which the SDK runs in a worker thread, so a tool call never
blocks the event loop. Each call opens its own session from the same
factory the REST API uses and closes it before returning -- nothing is held
between calls, which is what keeps the server stateless.

HTTPException from a service function (404 "lead not found", 402 plan
limit, ...) becomes a ToolError carrying the same detail, so the model gets
the actionable message the web app would show, never a stack trace.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from fastapi import HTTPException
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy.orm import Session

from app.db.base import SessionLocal
from app.db.models import User
from app.mcp import scopes
from app.mcp.auth import PrincipalRejected, load_principal

logger = logging.getLogger(__name__)

# Swapped by tests for a factory bound to their in-memory engine.
session_factory: Callable[[], Session] = SessionLocal


@dataclass(frozen=True)
class ToolCall:
    db: Session
    user: User
    token: AccessToken


def http_detail(exc: HTTPException) -> str:
    """The text the web app would show for this refusal. A structured detail
    without a `message` (e.g. the rate limiter's) is spelled out."""
    detail = exc.detail
    if isinstance(detail, dict):
        if detail.get("message"):
            return str(detail["message"])
        if detail.get("error"):
            rest = ", ".join(f"{k}={v}" for k, v in detail.items() if k != "error")
            return str(detail["error"]).replace("_", " ") + (f" ({rest})" if rest else "")
        return json.dumps(detail, default=str)
    return str(detail)


@contextmanager
def tool_call(*, scope: str = scopes.READ) -> Iterator[ToolCall]:
    token = get_access_token()
    if token is None:
        raise ToolError("Not signed in to Vectorae. Reconnect the Vectorae connector.")
    if scope not in token.scopes:
        raise ToolError(
            f"This action needs the '{scope}' permission, which this connection "
            "was not granted. Reconnect Vectorae and approve it.")
    db = session_factory()
    try:
        try:
            user = load_principal(db, {"sub": token.subject, **(token.claims or {})})
        except PrincipalRejected as exc:
            raise ToolError(f"Vectorae refused this account: {exc}.") from None
        try:
            yield ToolCall(db=db, user=user, token=token)
        except HTTPException as exc:
            db.rollback()
            raise ToolError(http_detail(exc)) from None
    finally:
        db.close()
