"""Build the connector's MCPServer and serve it from the main FastAPI app.

Three facts about mcp 2.x shape this module (recon §3):

  * `MCPServer.streamable_http_app()` returns a Starlette app whose
    StreamableHTTPSessionManager may be `run()` only ONCE per instance, and a
    mounted sub-app's lifespan is never run by FastAPI. So a fresh server is
    built each time the FastAPI lifespan starts (`running()`, entered from
    app/main.py::_lifespan) -- the test client starts that lifespan once per
    test -- and `McpEndpoint`, a stable ASGI object registered at the MCP
    path, forwards to whichever instance is live.
  * The SDK serves RFC 9728 metadata inside that sub-app, with
    `scopes_supported` taken from the endpoint's required scopes. We route
    only the MCP path into the sub-app and publish the metadata ourselves
    (`protected_resource_metadata`), so it can list every scope we grant.
  * Stateless: `stateless_http=True` gives every request its own transport
    and no session map; `json_response=True` answers with one JSON body.

MCP_ENABLED=false (the default) answers 404 at the MCP path and its
metadata path, so the endpoint stays dark until the authorization server
(connector Phase 2) is deployed.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from mcp.server import MCPServer
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.auth import ProtectedResourceMetadata
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import settings
from app.mcp import context, scopes
from app.mcp.auth import McpTokenVerifier
from app.mcp.stepup import ScopeChallenge
from app.mcp.tools import campaigns, chatgpt, insights, leads, messages, strategies

logger = logging.getLogger(__name__)

SERVER_NAME = "leadpilot"


def resource_path() -> str:
    """The path part of the MCP URL (`/mcp` for https://api.../mcp)."""
    return urlparse(settings.mcp_resource).path or "/"


def metadata_path() -> str:
    """RFC 9728 §3.1: the well-known prefix inserted before the resource path."""
    path = resource_path()
    return "/.well-known/oauth-protected-resource" + ("" if path == "/" else path)


def protected_resource_metadata() -> dict:
    return ProtectedResourceMetadata(
        resource=settings.mcp_resource,
        authorization_servers=[settings.mcp_issuer],
        scopes_supported=list(scopes.SUPPORTED),
        resource_name="Vectorae",
    ).model_dump(mode="json", exclude_none=True)


def build_server() -> MCPServer:
    server = MCPServer(
        name=SERVER_NAME,
        title="Vectorae",
        description=("Read the Vectorae account (leads, strategies, outreach, attribution) "
                     "and create strategies or pause and resume campaigns."),
        website_url=settings.frontend_url,
        token_verifier=McpTokenVerifier(lambda: context.session_factory()),
        auth=AuthSettings(
            issuer_url=settings.mcp_issuer,
            resource_server_url=settings.mcp_resource,
            required_scopes=[scopes.READ],
            validate_token_resource=True,
        ),
    )
    for module in (leads, strategies, messages, insights, chatgpt, campaigns):
        module.register(server)
    return server


def build_http_app(server: MCPServer) -> Starlette:
    resource_host = urlparse(settings.mcp_resource).netloc
    return server.streamable_http_app(
        streamable_http_path=resource_path(),
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[resource_host, *settings.mcp_extra_allowed_hosts],
            allowed_origins=list(settings.mcp_allowed_origins_list),
        ),
    )


class McpEndpoint:
    """The ASGI app routed at the MCP path. Starlette treats a class
    instance (not a function) as a raw ASGI endpoint."""

    def __init__(self) -> None:
        self.http_app: ASGIApp | None = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not settings.mcp_enabled:
            await JSONResponse({"detail": "Not Found"}, status_code=404)(scope, receive, send)
            return
        if self.http_app is None:
            await JSONResponse({"detail": "MCP server is starting"},
                               status_code=503)(scope, receive, send)
            return
        await self.http_app(scope, receive, send)


endpoint = McpEndpoint()


async def _metadata(_request) -> JSONResponse:
    if not settings.mcp_enabled:
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    return JSONResponse(protected_resource_metadata())


def routes() -> list[Route]:
    """Added to the FastAPI app's router by app/main.py."""
    return [
        Route(resource_path(), endpoint=endpoint),
        Route(metadata_path(), endpoint=_metadata, methods=["GET"]),
    ]


@asynccontextmanager
async def running() -> AsyncIterator[None]:
    """Build a fresh server and run its session manager for one app lifespan."""
    server = build_server()
    # Write tools without leadpilot:write get the MCP scope challenge (403).
    endpoint.http_app = ScopeChallenge(build_http_app(server), campaigns.WRITE_TOOLS)
    try:
        async with server.session_manager.run():
            yield
    finally:
        endpoint.http_app = None
