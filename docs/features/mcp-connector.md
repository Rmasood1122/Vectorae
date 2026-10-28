# Vectorae connector for Claude and ChatGPT (remote MCP server)

Status: **Phase 3 — read tools, OAuth 2.1 authorization server, three write
tools.** Off by default (`MCP_ENABLED=false`); a deployment switches it on
(see "Turning it on" below). Decisions:
`docs/program/DECISIONS.md` CONN-AS, CONN-WRITES, CONN-HOST, CONN-SERVICES,
CONN-DEPS, CONN-WORKSPACE, CONN-AUDIT. Recon: `docs/program/plans/connector-00-recon.md`.

## What it is

A remote [MCP](https://modelcontextprotocol.io) server served by the Vectorae
API at **`https://api.calendarharvest.com/mcp`**. Claude (claude.ai, Desktop,
mobile, Cowork, Claude Code) and ChatGPT connect to it from their own servers
over HTTPS and act in the signed-in user's account. A Skill
(`.claude/skills/leadpilot-outbound/`, Phase 4) only teaches Claude when and
how to use these tools; the server is what reaches the data.

## Behaviour (Phase 1)

| | |
|---|---|
| Transport | Streamable HTTP, **stateless**: no session map, no `Mcp-Session-Id`; JSON responses. Serves MCP protocol revisions up to 2026-07-28 (mcp 2.3.0). |
| Where it runs | Inside the API process (`app/main.py` lifespan + routes, `app/mcp/server.py`); same settings and DB session factory as the REST API. |
| Off switch | `MCP_ENABLED=false` (default) → `/mcp` and its metadata answer 404. |
| Unauthenticated | 401 with `WWW-Authenticate: Bearer error="invalid_token", ..., resource_metadata="https://<host>/.well-known/oauth-protected-resource/mcp"`. |
| Metadata | `GET /.well-known/oauth-protected-resource/mcp` (RFC 9728): `resource` = the MCP URL exactly, `authorization_servers` = [the API origin], `scopes_supported` = `leadpilot:read`, `leadpilot:write`, `offline_access`. |
| Tokens accepted | Only connector access tokens: JWT `typ="mcp_access"`, `aud` = the MCP URL, scope `leadpilot:read` at least. A web session token, a refresh token and a personal API key (`lpk_…`) are refused (401). A connector token is refused by the REST API (401). |
| Account checks | The same as every REST route (`auth.check_account_usable`): erased → refused, suspended → refused, unverified email → refused; a token issued before a password change → refused. At the HTTP layer these are 401; inside a tool call, an error naming the reason. |
| Whose data | The signed-in user's own account only (CONN-WORKSPACE); no workspace switching. |
| Host check | DNS-rebinding protection: only the MCP URL's host (plus `MCP_ALLOWED_HOSTS`) is served; others get 421. |

### Tools (all `readOnlyHint: true`, all tenant-scoped)

| Tool | What it returns | Service it reads through |
|---|---|---|
| `list_leads` | leads with status and F-P-T-A; filters `strategy_id`, `status`, `min_fpta`, `search`; sort `fpta` / `booking_likelihood` / `newest` | `crm_service.grid_page` |
| `get_lead` | one lead: F-P-T-A sub-scores, band, method; score reasons and signals, LinkedIn posts (≤5), company news (≤5), enrichment (≤4,000 chars) | `crm_service.owned_lead`, `fpta_scoring.detail` |
| `list_strategies` | strategies, newest first, with status and campaign state | `strategy_read.list_owned` |
| `get_strategy` | one strategy: product, status, campaign state, budget/spend, documents ready | `crm_service.owned_strategy`, `strategy_read.pipeline_status` |
| `get_pipeline_status` | steps done per pipeline and phase, verification passes, error | `strategy_read.pipeline_status` |
| `list_messages` | outreach messages without their text; filters `status`, `channel`, `strategy_id`, `lead_id` | `message_read.list_owned` |
| `get_message` | one message with subject and body | `message_read.owned_message` |
| `get_attribution_summary` | outcomes by step / channel / method, certain vs inferred, median hours, recent outcomes (0–20) | `attribution.summary`, `attribution.ledger` |
| `get_claim_verification` | a lead's claim-verification log (verdict counts and rows) | `claim_verification.lead_log` after `owned_lead` |
| `search` | ChatGPT shape: `{results: [{id, title, url}]}`, ≤10 leads | `crm_service.grid_page(search=…)` |
| `fetch` | ChatGPT shape: `{id, title, text, url, metadata}` for `lead:<uuid>` | as `get_lead` |

Every list has `limit` 1–50 (default 20) and `offset`, and returns `total` and
`has_more`. Invalid input returns an error that names the argument and what
is expected; another account's id returns "… not found" (never "forbidden").

### Untrusted content

Prospect and third-party text (names, titles, companies, emails, scraped
posts, news, enrichment, F-P-T-A reasons, message bodies, attribution
snapshots, claim text) appears **only** under an `untrusted_content` key, with
the `<<<EXTERNAL_TEXT` / `EXTERNAL_TEXT>>>` delimiters of
`app/services/prompt_safety.py` escaped inside it. The `summary` and every
other field are built from ids, enums, numbers and timestamps. `fetch` puts
the third-party text in one delimited block after a notice.

## Authorization server (Phase 2)

Built into the API (CONN-AS); no external identity provider. Every rule is in
`app/services/oauth_server.py`; the HTTP surface is `app/api/oauth.py`.

| Endpoint | What it does |
|---|---|
| `GET /.well-known/oauth-authorization-server` | RFC 8414 metadata. `issuer` = the API origin (`https://api.calendarharvest.com`), byte-identical to the resource metadata's `authorization_servers[0]`. `code_challenge_methods_supported: ["S256"]`, `token_endpoint_auth_methods_supported: ["none"]`, `authorization_response_iss_parameter_supported: true`, scopes `leadpilot:read`, `leadpilot:write`, `offline_access`. |
| `POST /oauth/register` | RFC 7591 Dynamic Client Registration (JSON). Public clients only: the answer is always `token_endpoint_auth_method: "none"`, no secret. Redirect URIs are allowlisted (below). 600 per IP per hour (`RATE_LIMIT_OAUTH`): Claude registers from Anthropic's shared egress range. |
| `GET /oauth/authorize` | Validates client, redirect URI, `response_type=code`, PKCE `S256`, `resource` (must equal the MCP URL), scope; records the request (10 minutes) and sends the browser to the consent page. An unknown client or unregistered redirect URI is answered 400 and **never** redirected. Every redirect back to the client carries `state` and `iss` (RFC 9207). |
| `GET /oauth/requests/{id}`, `POST /oauth/requests/{id}/decision` | Used by the consent page (`/oauth/consent/`), signed in as the person (web session; API keys refused; workspace header ignored — `/oauth` is a personal path). Shows the app name, the redirect host and the scopes in plain words; Allow returns the client redirect with a single-use code (2 minutes, stored as a SHA-256 hash); Decline returns `error=access_denied`. Both are written to `security_audit_events` (`oauth.consent_granted` / `oauth.consent_denied`). |
| `POST /oauth/token` | `application/x-www-form-urlencoded`. `authorization_code`: code + `code_verifier` (PKCE S256) + the same `redirect_uri` + `client_id`; a code redeemed twice is refused and the session the first redemption started is revoked. `refresh_token`: rotates; a reused refresh token is `invalid_grant` and revokes the whole session family; a refresh cannot add scopes. Refresh tokens are issued only when `offline_access` was granted. `Cache-Control: no-store`. |
| `POST /oauth/revoke` | RFC 7009; ends the session family of a refresh or access token. |

Allowed redirect URIs: `https://claude.ai/api/mcp/auth_callback` (exact; every
hosted Claude app), `https://chatgpt.com/connector_platform_oauth_redirect`
(exact; ChatGPT's stable URI for servers that return `iss`), and
`http://localhost/callback` / `http://127.0.0.1/callback` with any port
(Claude Code; RFC 8252 §7.3). Nothing else can be registered.

Sessions: a connector token is a row in `auth_sessions` with
`oauth_client_id` and `scope` set (migration `0091_oauth_connector`). Access
tokens (`typ="mcp_access"`, 1 hour) name their session and family; `/mcp`
refuses them once the family is revoked, the password changes, or the account
is suspended, erased or unverified. Refresh tokens (`typ="mcp_refresh"`, 30
days sliding) are refused by `/auth/refresh`, and web refresh tokens are
refused by `/oauth/token`. A password change revokes every session, connector
sessions included.

Not supported in v1: Client ID Metadata Documents (CIMD), client
authentication (`private_key_jwt`), `client_credentials`, static API keys at
`/mcp`.

GDPR: `oauth_authorization_codes` is account-owned (exported; deleted on
erasure). `oauth_clients` has no owner (registration is anonymous) and is
listed as excluded from export with that reason.

## Write tools (Phase 3, CONN-WRITES)

| Tool | Does | Through | Refused when |
|---|---|---|---|
| `list_products` (read) | the user's products with past-client and strategy counts | `strategy_read.list_products` | — |
| `create_strategy` | creates a strategy and starts its AI research | `strategy_write.create` (the same function `POST /products/{id}/strategies` calls) | rate limit (`RATE_LIMIT_STRATEGIES`, shared with the web app), product not owned (404), plan `max_strategies` (402), today's AI cap (429), budget too small (422), `with_clients` without past clients (422), F07 monthly forecast (429) |
| `pause_campaign` | `campaign_state=paused_manual`; same outbound webhook as the web app | `campaign_control.pause` | strategy not owned (404) |
| `resume_campaign` | `campaign_state=active` | `campaign_control.resume` | strategy not owned (404), deliverability preflight blocked (409) |

All three are `destructiveHint: true` (Claude asks the person before every
call) and need `leadpilot:write`. A token without it gets, at the HTTP layer,
`403` with `WWW-Authenticate: Bearer error="insufficient_scope",
scope="leadpilot:read leadpilot:write", resource_metadata="…"` (the MCP scope
challenge, `app/mcp/stepup.py`); the tool also checks the scope itself. A
refusal reaches the model as the web app's own detail text (the SDK frames it
as `Error executing tool <name>: <detail>`).

`create_strategy` never opts into Autopilot, and no connector tool sends,
schedules or approves a message (`approve_message` is out of v1: approving a
review item releases it to send). Resuming lets an approved campaign's
scheduled messages continue; each still passes the full send-time gate in
`app/workers/outreach_tasks.py::send_message_impl`, daily send approval
included.

Audit (CONN-AUDIT): every write call, done or refused, writes one
`security_audit_events` row: `action="mcp.<tool>"`,
`details={"actor_channel": "mcp", "oauth_client_id": …, "outcome": "created"
| "paused" | "resumed" | "refused_<status>"}`.

## Turning it on (a deployment)

1. Deploy a build containing migration `0091_oauth_connector`.
2. Set `MCP_ENABLED=true` and `MCP_RESOURCE_URL=https://api.calendarharvest.com/mcp`
   on the API service (Render: `leadpilot-api`), and on the worker too so both
   read the same settings.
3. Check `https://api.calendarharvest.com/.well-known/oauth-protected-resource/mcp`
   and `https://api.calendarharvest.com/.well-known/oauth-authorization-server`
   answer 200, and that `https://<frontend>/oauth/consent/` loads.
4. Connect from claude.ai (Settings → Connectors → Add custom connector) and
   from ChatGPT (Developer Mode) — `docs/connectors/README.md`.

## Not in v1

- Sending, scheduling or approving messages from a connector.
- Workspaces: a connection acts on the signed-in person's own account only.
- CIMD client registration and `private_key_jwt` client authentication.

## Code

`app/mcp/` (server, auth, context, untrusted, scopes, tools/),
`app/services/strategy_read.py` and `app/services/message_read.py` (moved
out of `app/api/ui_support.py` and `app/api/strategies.py`, which now call
them), `app/services/auth.py` (`check_account_usable`, `issue_mcp_access_token`,
`decode_mcp_access_token`, `issue_mcp_refresh_token`), `app/config.py` (`MCP_*`
settings), `app/services/oauth_server.py`, `app/api/oauth.py`,
`app/services/auth_sessions.py` (connector sessions),
`alembic/versions/0091_oauth_connector.py`, `frontend/src/app/oauth/consent/page.tsx`,
`frontend/src/lib/oauthConsent.ts`, `app/services/strategy_write.py`,
`app/services/campaign_control.py`, `app/mcp/tools/campaigns.py`,
`app/mcp/stepup.py`. Tests: `tests/mcp/`,
`tests/test_oauth_connector_migration.py`, `frontend/src/tests/oauthConsent.test.ts`.

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `MCP_ENABLED` | `false` | Serve `/mcp` and its metadata. |
| `MCP_RESOURCE_URL` | `PUBLIC_BASE_URL` + `/mcp` | The MCP URL exactly as users type it. Production: `https://api.calendarharvest.com/mcp`. |
| `MCP_ACCESS_TTL_SECONDS` | `3600` | Connector access-token lifetime. |
| `MCP_ALLOWED_HOSTS` | empty | Extra Host headers to serve (e.g. `localhost:8000` for MCP Inspector). |
| `MCP_ALLOWED_ORIGINS` | empty | Origin headers to accept (requests without one always pass). |
