"""Remote MCP server: Vectorae as a Claude / ChatGPT connector.

Mounted on the main FastAPI app (app/main.py), so it shares the process,
settings and database session factory -- not a second service. Streamable
HTTP, stateless: no in-process session map and no reliance on
Mcp-Session-Id; every request carries its bearer token and is answered on
its own (docs/program/plans/connector-00-recon.md section 3).

  server.py      builds the MCPServer, routes /mcp and the RFC 9728 metadata
  auth.py        bearer-token verification -> the Vectorae User
  context.py     one DB session + resolved principal per tool call
  untrusted.py   the `untrusted_content` envelope for third-party text
  scopes.py      the OAuth scopes the connector understands
  tools/         one module per domain; read tools only (Phase 1)

Every tool reaches data only through app/services functions that carry the
tenant filter (crm_service.owned_*, strategy_read, message_read, ...).
"""
