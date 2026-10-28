"""
Vectorae — FastAPI application entry point.
Combined build: all milestones M1–M8 wired.

M1–M4  core API, pipeline, verification, Gmail, WhatsApp
M5     web UI support (themes, analytics, ui-support)
M6     GET /strategies (CLI list endpoint — sdk/backend_additions)
M7     Capacitor CORS origins + /devices router (FCM tokens)
M8     admin, playbook, onboarding, webhook targets, rich health,
       request-ID logging, global error handler
"""

import logging
from contextlib import asynccontextmanager
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import get_settings

# M1–M5 routers (module-level absolute paths, no prefixes)
from app.api.auth import router as auth_router
from app.api.analytics import router as analytics_router
from app.api.ui_support import router as ui_support_router
from app.api.integrations import router as integrations_router
from app.api.sequences import router as sequences_router
from app.api.unsubscribe import router as unsubscribe_router
from app.api.webhooks import router as webhooks_router                    # Calendly inbound (M3)
from app.api.webhooks_whatsapp import router as whatsapp_webhooks_router  # WhatsApp inbound (M4)
from app.api.whatsapp_templates import router as whatsapp_templates_router
from app.api.whatsapp_optin import router as whatsapp_optin_router
from app.api.leads import router as leads_router
from app.api.products import router as products_router
from app.api.strategies import router as strategies_router
from app.api.tutorials import router as tutorials_router   # Feature 2
from app.api.support import router as support_router       # Feature 3
from app.api.linkedin_network import router as linkedin_network_router
from app.api.email_network import router as email_network_router
from app.api.crm import router as crm_router           # M9 native CRM
from app.api.calendar import router as calendar_router   # Engagement Hub F2
from app.api.meetings import router as meetings_router   # Engagement Hub F3
# Feature expansion
from app.api.meeting_prep import router as meeting_prep_router   # FG7
from app.api.deals import router as deals_router                 # FG3/FG7
from app.api.admin_integrations import router as admin_integrations_router
from app.api.ai_intelligence import router as ai_intelligence_router   # FG1
from app.api.personalization import router as personalization_router   # FG2
from app.api.linkedin import router as linkedin_router                 # FG5
from app.api.calls import router as calls_router                       # FG6
from app.api.revenue_analytics import router as revenue_analytics_router  # FG3
from app.api.reengagement import router as reengagement_router  # Feature 5
from app.api.recording import router as recording_router  # Feature 3 (transcripts)
from app.api.benchmarks import router as benchmarks_router  # Feature 6
from app.api.compliance_rules import router as compliance_rules_router  # Feature 8
from app.api.tracking import router as tracking_router                 # FG3
from app.api.api_keys import router as api_keys_router                 # FG4
from app.api.crm_integrations import router as crm_integrations_router # FG4
from app.api.slack_integration import router as slack_router           # FG4
from app.api.tool_integrations import router as tool_integrations_router  # AegisAudit / PostIQ / SIGNALFORGE
from app.api.website_builder import router as website_builder_router  # SEO site
from app.api.workspaces import router as workspaces_router             # FG8
from app.api.trust import router as trust_router                       # FG9
from app.api.billing import router as billing_router                   # Section E
from app.api.claims import router as claims_router                     # Feature A1
from app.api.audit import router as audit_router                       # Feature A2
from app.api.conversations import router as conversations_router       # Feature A4
from app.api.conversion import router as conversion_router             # Feature A5
from app.api.share_links import router as share_links_router           # Feature A6
from app.api.sequence_reviews import router as sequence_reviews_router # Feature A7
from app.api.send_reviews import router as send_reviews_router       # Part 1 Feature 5
from app.api.inbox import router as inbox_router                      # Part 1 Feature 6
from app.api.attribution import router as attribution_router          # Part 1 Feature 8
from app.api.meetings_ledger import router as meetings_ledger_router  # F08
from app.api.consent import router as consent_router                  # Part 1 Feature 9
from app.api.client_workspaces import router as clients_router        # Part 1 Feature 11
from app.api.practice import router as practice_router                # Part 2
from app.api.quickstart import router as quickstart_router            # F06
from app.api.reply_copilot import router as reply_copilot_router      # F04
from app.api.pilots import router as pilots_router                    # F09

# M7
from app.api.devices import router as devices_router

# M8 (prefixed routers)
from app.api.health import router as health_router
from app.api.status_public import router as status_public_router  # Phase 3
from app.api.admin import router as admin_router
from app.api.playbook import router as playbook_router
from app.api.onboarding import router as onboarding_router
from app.api.webhook_targets import router as webhook_targets_router
from app.api.strategies_advanced import router as strategies_advanced_router
from app.api.strategies_advanced import plans_router
from app.api.account_data import router as account_data_router
from app.api.guardrails import router as guardrails_router  # F05 Guardrails Studio
from app.api.bounce_shield import router as bounce_shield_router
from app.api.spend_guard import router as spend_guard_router  # F07
from app.api.signals import router as signals_router                   # F02 Signal Radar
from app.api.champions import router as champions_router               # F10 Champion Tracker
from app.api.send_approval import router as send_approval_router       # daily send approval
from app.api.autopilot import router as autopilot_router               # Autopilot launch + best time
from app.api.local_business import router as local_business_router     # local business mode
from app.api.voice_twin import router as voice_twin_router               # voice twin
from app.api.oauth import router as oauth_router                         # connector OAuth 2.1 AS
from app.api.agents import router as agents_router                       # Vectorae agents + master

# M9: registers the SQLAlchemy session listeners that publish CRM events
# to Redis. Imported for its SIDE EFFECT -- install() is a no-op marker so
# this line does not read as an unused import to a future cleanup. Removing
# it disables the real-time feed with no test failing, because every
# consumer falls back to polling.
from app.services.crm_events import install as _install_crm_events

_install_crm_events()

# Section E: registers the before_flush listener that meters booked meetings
# for pay-per-meeting accounts. Imported for its side effect, like the line above.
from app.services.billing import install as _install_billing_meter  # noqa: E402

_install_billing_meter()

# Feature A2: the before_flush listener that records every send/open/click/
# reply/booking into the hash-chained audit trail, and its append-only guard.
from app.services.audit_trail import install as _install_audit_trail  # noqa: E402

_install_audit_trail()

from app.core.errors import global_exception_handler
from app.core.logging import RequestIDMiddleware

@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """Application startup/shutdown.

    `@app.on_event("startup")` is deprecated on FastAPI 0.111, so new startup
    work goes here.

    ADMIN_EMAIL bootstrap: without it a freshly deployed database has no admin
    at all and no way to make one over HTTP (signup cannot self-promote, by
    design), so every /admin route and /playbook/aggregate is unreachable.
    Idempotent, never downgrades another admin, and never blocks startup — see
    app/core/admin_bootstrap.py for why it promotes but does not create.
    """
    from app.core.admin_bootstrap import bootstrap_admin
    from app.core.production_guard import validate_production_config

    # BEFORE anything else: refuse to serve traffic from a production process
    # whose SECRET_KEY / ENCRYPTION_KEY / CORS_ORIGINS are unset or unsafe.
    # Each of those fails quietly rather than crashing — see production_guard.
    # No-op outside APP_ENV=production/prod/live.
    validate_production_config()
    # Non-fatal problems an admin must still see (also on GET
    # /admin/config-warnings): e.g. phone verification required but no SMS
    # transport that can deliver a code.
    from app.services.identity import log_config_warnings  # noqa: PLC0415

    log_config_warnings()

    bootstrap_admin()
    # Claude / ChatGPT connector (app/mcp/server.py): a fresh MCP server per
    # lifespan, run in this process so it shares settings and the DB session
    # factory. Answers 404 while MCP_ENABLED is false.
    from app.mcp import server as mcp_server  # noqa: PLC0415

    async with mcp_server.running():
        yield


app = FastAPI(
    lifespan=_lifespan,
    title="Vectorae API",
    version="1.0.0",
    description=(
        "End-to-end client acquisition: strategy → research → outreach → booked "
        "meeting. Runs 24/7 in the cloud — clients (web, mobile, CLI) are windows "
        "into it, not the engine."
    ),
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# ── Middleware ────────────────────────────────────────────────────────────────
# Request-ID first so every log line (including CORS rejections) carries one.
app.add_middleware(RequestIDMiddleware)

# CORS: env-configured web origins + Capacitor origins (M7).
_ENV_ORIGINS: list[str] = [
    o.strip()
    for o in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")
    if o.strip()
]
_CAPACITOR_ORIGINS: list[str] = [
    "https://localhost",       # Capacitor Android (androidScheme: 'https')
    "capacitor://localhost",   # Capacitor fallback scheme
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=list({*_ENV_ORIGINS, *_CAPACITOR_ORIGINS}),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Total-Count", "X-Request-ID"],
)

# ── Error handling (M8-C3): safe responses, full structured logs ─────────────
# The catch-all is registered for the LAST-RESORT case only. Registering the
# application's own exception types explicitly matters: a handler bound to bare
# `Exception` is served by Starlette's ServerErrorMiddleware, which re-raises
# after building the response, so a ComplianceError surfaced as an unhandled
# 500-path exception instead of the documented 422 under any ASGI transport
# that propagates app exceptions (httpx ASGITransport, TestClient). Bound by
# concrete type they go through ExceptionMiddleware and return a real response.
from app.core.exceptions import (  # noqa: E402
    ClientHunterError,
    ComplianceError,
    ForbiddenError,
    ResourceNotFoundError,
)

for _exc_type in (ComplianceError, ResourceNotFoundError, ForbiddenError,
                  ClientHunterError):
    app.add_exception_handler(_exc_type, global_exception_handler)
app.add_exception_handler(Exception, global_exception_handler)

# ── Static media (theme background uploads) ───────────────────────────────────
_MEDIA_DIR = Path(os.getenv("MEDIA_DIR", "media"))
_MEDIA_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=str(_MEDIA_DIR)), name="media")

# ── Routers ───────────────────────────────────────────────────────────────────
# M8 health router (prefix=/health) replaces the M1 inline /health endpoint.
app.include_router(health_router)
# Phase 3: GET /status/public, unauthenticated, for the public status page
# (www.calendarharvest.com/status/, fetched from the visitor's browser).
# Built from the same checks as /health (app/services/health_checks.py).
app.include_router(status_public_router)
app.include_router(auth_router)
app.include_router(analytics_router)
app.include_router(ui_support_router)
app.include_router(products_router)
app.include_router(strategies_router)            # M5 core strategy routes
app.include_router(strategies_advanced_router)   # M8-C4/C5 additions (/strategies/*)
app.include_router(plans_router)                 # GET /plans (public)
app.include_router(leads_router)
app.include_router(integrations_router)
app.include_router(sequences_router)
app.include_router(unsubscribe_router)
app.include_router(webhooks_router)              # POST /webhooks/calendly (M3)
app.include_router(whatsapp_webhooks_router)     # GET/POST /webhooks/whatsapp (M4)
app.include_router(webhook_targets_router)       # /webhooks/targets (M8-C5)
app.include_router(whatsapp_templates_router)
app.include_router(whatsapp_optin_router)
app.include_router(devices_router, prefix="/devices", tags=["devices"])  # M7
app.include_router(admin_router)                 # /admin/* (M8-C3)
app.include_router(playbook_router)              # /playbook/* (M8)
app.include_router(onboarding_router)            # /onboarding/* (M8-C5)
app.include_router(tutorials_router)             # /tutorials/* (Feature 2)
app.include_router(support_router)               # /support/* (Feature 3)
app.include_router(linkedin_network_router)      # /linkedin-network/* (Growth+)
app.include_router(email_network_router)         # /email-network/* (Growth+, flag)
app.include_router(crm_router)                   # /crm/* (M9)
# Engagement Hub. /calendar/* carries the only two unauthenticated
# endpoints added since the unsubscribe link: the public booking page's
# slot list and its book action, both documented in app/api/calendar.py.
app.include_router(calendar_router)              # /calendar/* (Feature 2)
app.include_router(meetings_router)              # /meetings/* (Feature 3)
# Feature expansion. None of these paths collide with an earlier router (the
# ui_support.py shadowing lesson): /leads/{id}/meeting-prep and
# /leads/{id}/meeting-outcome(s) are new sub-paths, /deals and /meeting-prep
# are new prefixes, and /admin/integrations + /admin/system-settings are not
# declared by app/api/admin.py.
app.include_router(meeting_prep_router)          # FG7 briefs + outcomes
app.include_router(deals_router)                 # /deals
app.include_router(admin_integrations_router)    # /admin/integrations, /admin/system-settings
app.include_router(ai_intelligence_router)       # FG1: /strategies/{id}/intelligence, versions, rescore
# FG2. /public/video is the one unauthenticated route it adds (token + IP limit).
app.include_router(personalization_router)       # /me/style-profile, /leads/{id}/personalization, /public/video
# FG5. Its /integrations/linkedin/* paths do not collide with ui_support's
# /integrations/status or /integrations/{provider}/test (different segments).
app.include_router(linkedin_router)              # /integrations/linkedin/*, /webhooks/unipile
app.include_router(calls_router)                 # FG6: /calls, /leads/{id}/call, /webhooks/vapi|elevenlabs
app.include_router(revenue_analytics_router)     # FG3: /analytics/revenue, /costs, funnel, send-time, sentiment
app.include_router(tracking_router)              # FG3: /t/o/{token}.gif (public open pixel)
app.include_router(api_keys_router)              # FG4: /me/api-keys (Zapier / Make auth)
app.include_router(slack_router)                 # FG4: /integrations/slack/*
app.include_router(tool_integrations_router)     # /integrations/tools/*
app.include_router(website_builder_router)        # /site/* incl. public sitemap.xml + robots.txt
app.include_router(crm_integrations_router)      # FG4: HubSpot / Salesforce + their webhooks
app.include_router(workspaces_router)            # FG8: /workspaces/*, public /branding
app.include_router(trust_router)                 # FG9: /deliverability, /compliance/audit
# Section E. /billing/* and /webhooks/stripe are new paths; /admin/billing/* is
# not declared by app/api/admin.py. /billing/catalog and the signature-verified
# /webhooks/stripe are the only unauthenticated routes it adds.
app.include_router(billing_router)               # /billing/*, /webhooks/stripe
# Feature A1. /leads/{id}/claim-checks is a new sub-path; /claim-checks is new.
app.include_router(claims_router)                # /leads/{id}/claim-checks, /claim-checks/summary
# Feature A2. /audit/* is new. /audit/public-key and /audit/verify are
# unauthenticated by design (the buyer verifying a report has no account).
app.include_router(audit_router)                 # /audit/trail*, /audit/public-key, /audit/verify
# Feature A4. /leads/{id}/conversation and /leads/{id}/channel-suggestions are
# new sub-paths; /channel-suggestions is a new prefix.
app.include_router(conversations_router)         # unified thread + stagnation suggestions
# Feature A5. /leads/{id}/conversion*, /conversion/at-risk and
# /strategies/{id}/channel-performance are new paths (no collision with
# strategies.py / strategies_advanced.py, checked by route enumeration).
app.include_router(conversion_router)            # live conversion probability + kill signals
# Feature A6. /share-links is new; /public/roi/{token} is the one
# unauthenticated route it adds (256-bit token, per-IP limit, uniform 404).
app.include_router(share_links_router)           # /share-links, /public/roi/{token}
# Feature A7. /sequences/{id}/review* are new sub-paths under sequences.py's
# /sequences/{id} (GET) -- a longer literal path, so no collision.
app.include_router(sequence_reviews_router)      # pre-send adversarial review
# Part 1 Feature 5. /send-reviews is a new prefix -- it does not collide with
# sequence_reviews.py's /sequences/{id}/review*, which reviews a sequence's
# CONTENT before launch rather than one MESSAGE at send time.
app.include_router(send_reviews_router)          # human review queue
# Part 1 Feature 6. /inbox is a new prefix; it does not collide with
# /crm/replies (Feature A3), which lists replies by REPLY with their
# authenticity rather than threading them by prospect.
app.include_router(inbox_router)                 # unified cross-channel inbox
# Part 1 Feature 8. /attribution is a new prefix and
# /leads/{id}/attribution a new sub-path. Nothing here writes an outcome; it
# only explains the ones that already happened.
app.include_router(attribution_router)           # transparent attribution ledger
# F08. /analytics/meetings-ledger is a new sub-path beside /analytics/revenue;
# read-only, behind meetings_ledger_enabled (default off).
app.include_router(meetings_ledger_router)       # meetings-per-dollar ledger
# Part 1 Feature 9. /compliance/consent and /compliance/requirements are new
# sub-paths under trust.py's /compliance/audit; /leads/{id}/consent* are new.
app.include_router(consent_router)               # consent ledger + cross-channel withdrawal
# Part 1 Feature 11. /clients is a new prefix; /strategies/{id}/client is a new
# sub-path under strategies.py's /strategies/{id}.
app.include_router(clients_router)               # per-client agency workspaces
# Part 2. /practice is a new prefix; the /meeting-prep/{id}/script,
# /readiness and /practice-required paths are longer literals under
# meeting_prep.py's /meeting-prep/{brief_id}, so there is no collision.
app.include_router(practice_router)              # call script + roleplay practice
app.include_router(reengagement_router)          # Feature 5: /strategies/{id}/reengagement
app.include_router(recording_router)             # Feature 3: recording bots, /webhooks/recall
app.include_router(benchmarks_router)            # Feature 6: /benchmarks
app.include_router(compliance_rules_router)      # Feature 8: /admin/compliance-rules
app.include_router(account_data_router)          # GDPR: POST /me/data-export
# F05. /guardrails is a new prefix; nothing else is mounted under it.
app.include_router(guardrails_router)            # F05: exclusion lists + claims library
app.include_router(bounce_shield_router)         # F01: /leads/{id}/email-verdict|override, /bounce-shield/metrics
app.include_router(signals_router)               # F02: /leads/{id}/signals, /signals/report
app.include_router(quickstart_router)            # F06: /quickstart* (flag: quickstart_enabled)
app.include_router(reply_copilot_router)         # F04: /reply-copilot* (flag: reply_copilot_enabled)
app.include_router(spend_guard_router)           # F07: /spend-guard/* (flag: spend_guard_enabled)
# F09. /pilots is a new prefix; nothing else is mounted under it.
app.include_router(pilots_router)                # F09: /pilots* (flag: pilot_mode_enabled)
# F10. /champions is a new prefix; /leads/{id}/role-check* are new sub-paths.
app.include_router(champions_router)             # F10: /leads/{id}/role-check*, /champions/*
app.include_router(send_approval_router)         # daily send approval: /send-approval/*
# Autopilot launch. /autopilot is a new prefix; /strategies/{id}/autopilot,
# /best-time and /send-diagnostics are new sub-paths of /strategies/{id}.
app.include_router(autopilot_router)             # /strategies/{id}/autopilot|best-time|send-diagnostics
# Local business mode: /local-business/* is a new prefix; /strategies/{id}/
# local-business and /strategies/{id}/customers/import are new sub-paths.
app.include_router(local_business_router)        # local business mode + win-back import
app.include_router(voice_twin_router)            # voice twin: /me/voice-clone*, /voice-twin/*
app.include_router(oauth_router)                 # connector OAuth: /oauth/*, /.well-known/oauth-authorization-server
app.include_router(agents_router)                # Vectorae agents: /agents, /agents/{name}/run

# Remote MCP server for the Claude / ChatGPT connector: the MCP path and its
# RFC 9728 metadata (app/mcp/server.py). Plain routes, not a Mount, so the
# rest of the app's routing (404/405 answers) is unchanged.
from app.mcp.server import routes as _mcp_routes  # noqa: E402

app.router.routes.extend(_mcp_routes())


# ---------------------------------------------------------------------------
# Test-only debug router — NEVER mounted in a deployed environment.
# ---------------------------------------------------------------------------
# Gated on a DEDICATED flag (settings.enable_debug_routes, app/config.py) that
# controls nothing else, so no unrelated config change can enable it as a side
# effect — notably NOT app_env/is_test/log_level. The import lives INSIDE the
# conditional: when the flag is off, app.api.debug is never imported, so its
# routes cannot exist on this ASGI app under any code path. The flag alone is
# not enough: Settings.debug_routes_allowed also requires app_env to be a
# development, local or test environment (F9 -- an allowlist, so staging is
# refused as well as production), and app/api/debug.py raises at import time
# anywhere else.
_debug_settings = get_settings()
if _debug_settings.debug_routes_allowed:
    from app.api.debug import router as debug_router

    app.include_router(debug_router)
    logging.getLogger(__name__).warning(
        "TEST-ONLY /debug routes are mounted (enable_debug_routes=True, "
        "app_env=%s). This must never happen in production.",
        _debug_settings.app_env,
    )


@app.get("/", include_in_schema=False)
async def root() -> dict:
    return {"name": "Vectorae API", "docs": "/docs", "health": "/health"}
