"""Central application configuration.

Every value comes from environment variables (or a local .env file in
development). Nothing secret is ever hard-coded. See `.env.example` at the
repo root for documentation of every variable.
"""

import os
from functools import lru_cache
from typing import Optional
from urllib.parse import urlparse

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- App -----------------------------------------------------------
    app_name: str = "Victorae"
    app_env: str = "development"  # development | production | test
    # DEDICATED flag for app/api/debug.py. Deliberately separate from app_env,
    # is_test and log_level so that no unrelated configuration change can turn
    # the test-only debug router on as a side effect. Defaults False; it is set
    # nowhere in .env.production.example, docker-compose.prod.yml or the
    # Dockerfile, and app/main.py additionally refuses to mount the router when
    # app_env is production. Never set this true in a deployed environment.
    enable_debug_routes: bool = False
    api_v1_prefix: str = "/api/v1"
    log_level: str = "INFO"

    # --- Database ------------------------------------------------------
    database_url: str = "postgresql+psycopg2://leadpilot:leadpilot@localhost:5432/leadpilot"

    # --- Redis / Celery --------------------------------------------------
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str | None = None   # defaults to redis_url if unset
    celery_result_backend: str | None = None  # defaults to redis_url if unset

    # --- Anthropic (the ONLY external API used in Milestone 1) ----------
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"
    anthropic_max_tokens: int = 4096
    # Retry behaviour for transient API errors inside pipeline steps
    anthropic_max_retries: int = 3

    # --- F-AI-01: dollar budget for one strategy run ----------------------
    # A strategy's pipeline (72 or 144 steps) plus its 10-pass verification loop
    # is the most expensive thing the product does. Every strategy carries a
    # budget (strategies.budget_micros) and the model gateway refuses a call
    # that could take the run past it. The caller may pick the budget when
    # creating the strategy; these two settings bound that choice.
    #   default  used when the request does not say. Set above a typical full
    #            run so ordinary use is unaffected while a runaway is not.
    #   ceiling  the most a request may ask for (422 above it).
    pipeline_max_usd_default: float = 15.0
    pipeline_max_usd_ceiling: float = 50.0

    # F-AI-01b. How the pipeline fits itself to a budget (app/pipeline/budget_plan.py).
    # ESTIMATES, not measurements: one pipeline step is priced at this many input
    # and output tokens at the admin-set model prices. Deliberately on the high
    # side so a plan errs towards finishing inside the budget; the hard cap from
    # F-AI-01 remains the safety net if a step costs more.
    #   verification_reserve  held back for the 10-pass verification loop
    #   budget_safety         the fraction of the budget the plan may plan to use
    pipeline_est_step_input_tokens: int = 7000
    pipeline_est_step_output_tokens: int = 1500
    pipeline_verification_reserve_usd: float = 1.0
    pipeline_budget_safety: float = 0.9

    # --- External API plumbing (M2+: Apollo, Hunter, ...) ----------------
    external_max_retries: int = 3
    external_backoff_base_seconds: float = 2.0
    circuit_failure_threshold: int = 5
    circuit_cooldown_seconds: int = 300
    cache_ttl_search_seconds: int = 86400        # 1 day
    cache_ttl_enrichment_seconds: int = 2592000  # 30 days — never pay twice

    # --- Lead sourcing providers (M2) ------------------------------------
    apollo_api_key: str = ""
    hunter_api_key: str = ""
    lead_source_provider: str = "apollo"
    email_verifier_provider: str = "hunter"
    leads_max_per_run: int = 200
    apollo_page_size: int = 25  # per_page for paginated search

    # --- Gmail OAuth (M3) --------------------------------------------------
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/integrations/gmail/callback"
    # NOTE: OAuth-token encryption goes through app/core/crypto.py, which
    # also reads this same ENCRYPTION_KEY var. Aliased here (rather than to
    # the old TOKEN_ENCRYPTION_KEY name) so both config modules always agree
    # on one key — this field itself is not read anywhere, it's kept only
    # for backward-compat callers.
    token_encryption_key: str = Field(default="", alias="ENCRYPTION_KEY")
    # Refresh the access token when it expires within this many seconds.
    gmail_token_refresh_leeway_seconds: int = 120

    # --- Outreach compliance & sending (M3) --------------------------------
    public_base_url: str = "http://localhost:8000"  # used in unsubscribe links
    sender_identity: str = "Vectorae User, 123 Main St, City, Country"  # CAN-SPAM footer identity
    gmail_daily_cap: int = 100
    gmail_warmup_start_sends: int = 10
    gmail_warmup_daily_increment: int = 10
    send_window_start_hour: int = 9
    send_window_end_hour: int = 17
    send_window_timezone: str = "UTC"   # fallback when a lead has no timezone
    send_window_skip_weekends: bool = True
    bounce_rate_pause_threshold: float = 0.03
    bounce_min_sends: int = 10          # don't judge bounce rate on tiny samples
    # Celery beat runs every sub-hourly database sweep (dispatch, reply
    # polling, follow-ups, reminders, plan holds, CRM sync...) together, once
    # every `beat_sweep_minutes`, at the same minutes of the hour; hourly
    # sweeps join the first burst of the hour. Between bursts the worker sends
    # the database nothing, so a database that suspends when idle (Neon's free
    # plan, after 5 minutes) can sleep. Must divide 60 and be at least 5: each
    # burst spreads its jobs over minutes +0..+4. Lower it for faster sends and
    # reply pickup on a database that does not bill for idle time.
    beat_sweep_minutes: int = Field(default=30, ge=5, le=60)
    # GET /health and GET /status/public reuse a successful database check for
    # this long instead of running SELECT 1 again. Render probes /health every
    # few seconds; without this the probe alone kept the database awake around
    # the clock. A failed check is never reused. 0 = check on every request.
    health_db_check_ttl_seconds: int = Field(default=1800, ge=0)
    ooo_reschedule_days: int = 7        # out-of-office: pause + retry after N days

    # --- Calendly (M3) -------------------------------------------------------
    calendly_api_token: str = ""
    # Aliased to CALENDLY_WEBHOOK_SECRET — the single env var name used by
    # both config modules and documented in .env.example. (Previously this
    # field looked for a different name, CALENDLY_WEBHOOK_SIGNING_KEY, which
    # silently left it empty and made every Calendly webhook 401.)
    calendly_webhook_signing_key: str = Field(default="", alias="CALENDLY_WEBHOOK_SECRET")
    # F-OPS-02: how old a signed Calendly delivery's timestamp may be before
    # it is refused as a replay. Env: CALENDLY_WEBHOOK_TOLERANCE_SECONDS.
    # F-OPS-02. Calendly's own signature docs demonstrate a 3-minute
    # tolerance zone; Stripe, HubSpot and Salesforce use 300s. 1800 left a
    # captured delivery replayable for half an hour, which is ten times the
    # window the provider itself illustrates. Override per deployment if a
    # slow queue genuinely needs more.
    calendly_webhook_tolerance_seconds: int = 180

    # --- WhatsApp Business Cloud API (M4) -------------------------------------
    whatsapp_access_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_business_account_id: str = ""
    # Token YOU invent and paste into Meta's webhook config (GET handshake).
    # Aliased to WHATSAPP_VERIFY_TOKEN — the single env var name used by both
    # config modules and documented in .env.example. (Previously this field
    # looked for WHATSAPP_WEBHOOK_VERIFY_TOKEN, which silently left it empty
    # and made Meta's webhook verification handshake always fail. The field was
    # fixed then, but docker-compose.yml, `clienthunter serve`'s .env template,
    # docs/DEPLOY.md and tests/conftest.py all kept SETTING the dead name, so
    # the handshake still failed everywhere except a hand-written .env. Those
    # are fixed too -- if you add a new setter, use WHATSAPP_VERIFY_TOKEN.)
    whatsapp_webhook_verify_token: str = Field(default="", alias="WHATSAPP_VERIFY_TOKEN")
    # Meta App secret — used to validate X-Hub-Signature-256 on deliveries.
    whatsapp_app_secret: str = ""
    # TODO: verify against current WhatsApp Cloud API docs (latest version)
    whatsapp_api_version: str = "v20.0"
    # TODO: verify against current WhatsApp docs (body component limit)
    whatsapp_template_body_max_chars: int = 1024
    # Per-channel daily cap + warm-up (M4 Chunk 3). Deferred, never dropped.
    whatsapp_daily_cap: int = 100
    whatsapp_warmup_start_sends: int = 10
    whatsapp_warmup_daily_increment: int = 10

    # --- Auth (M5) ---------------------------------------------------------
    # Aliased to SECRET_KEY — the single env var name used by both config
    # modules and documented in .env.example. (Previously this field looked
    # for JWT_SECRET, which was never set, so every server restart silently
    # generated a fresh random secret and logged every user out.)
    jwt_secret: str = Field(default="", alias="SECRET_KEY")  # REQUIRED in production
    jwt_access_ttl_seconds: int = 900
    # Persistent sign-in ("keep me signed in"). A SLIDING window: every
    # refresh rotates the token and restarts it, so an account in regular use
    # is never asked to sign in again, while one left idle this long is.
    jwt_refresh_ttl_seconds: int = 2592000  # 30 days
    # "Keep me signed in" unticked: the cookie is a browser-session cookie and
    # the server-side session also dies after this long, whichever is first.
    auth_ephemeral_session_ttl_seconds: int = 43200  # 12 hours
    # Web clients receive the refresh token ONLY as an HttpOnly cookie (see
    # app/services/auth_sessions.py). PATH scopes it to the refresh/logout
    # routes -- set it to e.g. /api/v1/auth when a proxy mounts the API under a
    # prefix. DOMAIN empty = host-only. SAMESITE lax suits a frontend and API
    # on the same site (leadpilot.com + api.leadpilot.com); a frontend on a
    # different site needs "none", which forces Secure.
    auth_refresh_cookie_name: str = "lp_refresh"
    auth_refresh_cookie_path: str = "/auth"
    auth_refresh_cookie_domain: str = ""
    auth_refresh_cookie_samesite: str = "lax"
    # None = Secure in production, not in development (http://localhost).
    auth_refresh_cookie_secure: bool | None = None
    # Two tabs refreshing at the same moment present the same token; the loser
    # of that race is inside this window and gets an access token instead of
    # tripping reuse detection (which would sign the user out everywhere).
    auth_refresh_reuse_grace_seconds: int = 30
    # Refresh tokens issued before server-side sessions existed carry no
    # session id. Accepting them (once -- they are exchanged for a tracked
    # session) keeps everyone signed in across the deploy. Turn this off once
    # jwt_refresh_ttl_seconds has passed since that deploy.
    auth_accept_legacy_refresh_tokens: bool = True

    # --- Claude / ChatGPT connector (app/mcp/, DECISIONS.md CONN-*) ---------
    # Off until the OAuth authorization server is deployed: /mcp and its
    # metadata answer 404 while this is false.
    mcp_enabled: bool = False
    # The MCP URL exactly as a user types it into Claude or ChatGPT; RFC 9728
    # `resource` and every connector token's `aud` must equal it.
    # Production: https://api.calendarharvest.com/mcp (CONN-HOST). Empty =
    # PUBLIC_BASE_URL + "/mcp".
    mcp_resource_url: str = ""
    mcp_access_ttl_seconds: int = 3600
    # Host headers /mcp accepts besides the resource URL's own host
    # (DNS-rebinding protection), comma-separated -- e.g. "localhost:8000"
    # for MCP Inspector against a local server. "host:*" allows any port.
    mcp_allowed_hosts: str = ""
    # Origin headers /mcp accepts, comma-separated. Claude and ChatGPT call
    # from their servers and send none; a request with no Origin passes.
    mcp_allowed_origins: str = ""
    # Where theme background images are stored + served from.
    media_dir: str = "media"

    # --- Email verification (Feature 1) ------------------------------------
    # Where the browser is sent AFTER GET /auth/verify has done its work.
    # FRONTEND_URL is already set in .env / render.yaml but was never read by
    # this module; it is read here so the redirect target is configuration,
    # not a constant compiled into the redirect handler.
    frontend_url: str = "http://localhost:3000"
    # Feature Group 8 (white label): a workspace is reachable at
    # <slug>.<white_label_base_domain>, and a custom domain verifies by a TXT
    # record or by CNAME-ing to white_label_cname_target. Empty = off.
    white_label_base_domain: str = ""
    white_label_cname_target: str = ""

    # Which transport actually delivers mail. Deliberately explicit rather than
    # "use resend if a key is present": a production process with a typo'd key
    # name must FAIL LOUDLY, not silently degrade to logging every
    # verification link to stdout where no user will ever see it.
    #   resend  — HTTPS POST to api.resend.com (production)
    #   smtp    — plain SMTP; this is the Mailtrap sandbox path for local tests
    #   console — log the message, deliver nothing (local dev without creds)
    #   memory  — append to an in-process list (the test suite asserts on it)
    email_provider: str = "console"
    email_send_timeout_seconds: float = 15.0

    # Resend
    resend_api_key: str = ""
    resend_api_url: str = "https://api.resend.com/emails"
    # MUST be on a domain verified in the Resend dashboard, or every message is
    # accepted by the API and then dropped or spam-foldered downstream.
    email_from: str = "noreply@calendarharvest.com"
    email_from_name: str = "Vectorae"

    # SMTP (Mailtrap sandbox in development; any SMTP relay in production)
    smtp_host: str = ""
    smtp_port: int = 2525
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_starttls: bool = True

    # Verification token lifetime. 24h is the product requirement; it is a
    # setting so an expiry test does not have to wait a day.
    email_verification_ttl_hours: int = 24
    # Password-reset link lifetime (app/services/password_reset.py). Short on
    # purpose: a live reset link is an account-takeover credential sitting in
    # an inbox. 60 minutes is long enough for a slow mail relay and a person
    # who steps away; a setting so the expiry test does not have to wait.
    password_reset_ttl_minutes: int = 60

    # --- AI support chat (Feature 3) ---------------------------------------
    # AI_MODE — console | mock | live. EMPTY MEANS AUTO, and auto is the
    # default deliberately: with an absent or invalid ANTHROPIC_API_KEY the
    # chat used to show an error to every user who opened it, which is a
    # broken widget on a product that has not launched yet. Auto resolves to
    # "live" when a key is present and "mock" when it is not, so the widget
    # answers from the curated FAQ instead of failing, and starts using the
    # model the moment a real key lands -- with no code change.
    #
    # Set it explicitly to pin a mode: "mock" keeps the model off even with a
    # key present (useful for demos and for capping spend), "live" forces the
    # model and fails loudly if the key is bad rather than quietly degrading.
    ai_mode: str = ""
    # Output ceiling for one support reply. Small on purpose: the system prompt
    # caps the answer at 120 words, and a low ceiling is the second line of
    # defence against a runaway generation billed to your key.
    support_chat_max_tokens: int = 1024
    # How many prior turns are replayed for context. Every turn is re-sent on
    # every message, so this is a direct multiplier on input cost -- 6 is
    # enough for "what about the second one?" without carrying a whole session.
    support_chat_history_turns: int = 6
    # Below this self-reported confidence the answer is DISCARDED and the user
    # is offered a ticket. A wrong answer delivered confidently is the failure
    # this feature must not produce; an unnecessary ticket is an annoyance.
    support_chat_min_confidence: float = 0.5
    # Mock mode only (Phase 2 help center). The best help-center chunk is
    # returned verbatim only when BOTH clear; otherwise the user is offered a
    # ticket. Defaults are help_center.MOCK_MIN_SCORE and
    # MOCK_MIN_HEADING_COVERAGE -- see there for the golden-set measurement
    # (tests/help_center pins that these defaults equal those constants).
    # Replaces support_chat_mock_min_score, the keyword threshold of the old
    # nine-entry FAQ, which no longer decides anything.
    support_chat_mock_min_bm25: float = 6.0
    support_chat_mock_min_heading_coverage: float = 0.60
    # Chat history is deleted after this many days by the nightly purge task.
    support_chat_retention_days: int = 30
    # Kill switch, matching REQUIRE_EMAIL_VERIFICATION. Turns the chat off
    # without a deploy if it ever starts costing or saying something it should
    # not. The widget hides itself and the endpoint returns 503.
    support_chat_enabled: bool = True

    # --- Help AI v2: embeddings + rerank (Voyage) -------------------------
    # The ONLY embedding vendor (docs/program/DECISIONS.md HELPAI-VENDOR), and
    # app/services/embeddings_client.py is the only module that calls it.
    voyage_api_key: str = ""
    # EMBEDDINGS_MODE -- mock | live. EMPTY MEANS AUTO, exactly like AI_MODE:
    # "live" when VOYAGE_API_KEY is set, "mock" otherwise. Mock vectors are
    # deterministic feature hashes (no network); they exist for tests and
    # development and are never written to a production index unless asked
    # (scripts/help_center/reindex.py --allow-mock).
    embeddings_mode: str = ""
    # Contextualized chunk embeddings: each chunk is embedded with its whole
    # article as context, which replaces a generated "context sentence".
    voyage_embed_model: str = "voyage-context-4"
    # Must equal the help_chunks.embedding column width (halfvec(1024),
    # alembic 0094). voyage-context-4 supports 256/512/1024/2048.
    voyage_embed_dimension: int = 1024
    voyage_rerank_model: str = "rerank-3-lite"
    # Per HTTP attempt. A query embedding sits in front of a support answer,
    # so a slow vendor must fall back to BM25 quickly rather than hang the
    # widget; the reindex job tolerates the same bound per batch.
    voyage_timeout_seconds: float = 15.0
    # Attempts per request, transient failures only (network, timeout, 429,
    # 5xx). Backoff doubles from 1s.
    voyage_max_attempts: int = 3

    # KILL SWITCH. get_current_user rejects unverified users only while this is
    # true. It exists because that dependency is the single chokepoint every
    # authenticated route shares: if verification ever locks real users out,
    # this turns enforcement off from the Render dashboard with a restart,
    # instead of needing a code change and a redeploy.
    require_email_verification: bool = True

    # --- Engagement Hub: calendar (Feature 2) -----------------------------
    # How far ahead a public booking page offers slots. 30 days is the brief's
    # figure. It is also the width of the window every slot query scans, so it
    # is the one knob that trades "book me in three months" against the cost
    # of an unauthenticated request.
    calendar_slot_days_ahead: int = 30
    # A slot closer than this is not offered. Without it the page happily
    # sells the host a call starting in ninety seconds, in a meeting they are
    # already in, having never seen the notification.
    calendar_min_notice_minutes: int = 60

    # --- Engagement Hub: meeting platforms (Feature 3) --------------------
    # Google Calendar / Meet event creation. Deliberately SEPARATE fields from
    # google_client_id above: that pair is the Gmail send credential, scoped
    # to gmail.send, and a calendar event needs calendar.events. In practice
    # both usually come from the same Google Cloud project, so an empty value
    # here falls back to the Gmail pair (see google_meet_client_id below)
    # rather than failing on a deployment that only ever configured one.
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    zoom_oauth_client_id: str = ""
    zoom_oauth_client_secret: str = ""
    # Zoom server-to-server OAuth needs the account id alongside the client
    # pair; without it the token request is rejected with an opaque 400.
    zoom_account_id: str = ""
    # Shared secret on POST /meetings/{id}/transcript. That endpoint is how a
    # browser extension or a Recall.ai webhook streams transcript chunks in,
    # and neither can hold a user JWT, so it authenticates by HMAC over the
    # body instead. EMPTY MEANS THE ENDPOINT IS CLOSED -- it answers 503
    # rather than accepting unauthenticated writes to a meeting transcript.
    meeting_recording_webhook_secret: str = ""
    # Output ceiling for one meeting summary. Larger than the support chat's
    # 1024 because this call returns five structured fields over a full
    # transcript, and a truncated summary is a summary with the action items
    # missing -- see anthropic_client.TruncatedResponseError.
    meeting_ai_max_tokens: int = 4096

    # --- Engagement Hub: automated follow-up (Feature 1) ------------------
    # check_followup_due runs in every beat burst (beat_sweep_minutes, 30 by
    # default): the delays it enforces are measured in days.
    # TTL on the Redis idempotency lock a follow-up send takes. Two hours: far
    # longer than any send can take, far shorter than the shortest useful
    # follow-up delay, so a crashed worker's lock cannot survive to block the
    # next legitimate attempt.
    followup_lock_ttl_seconds: int = 7200

    # --- Feature Group 7: meeting preparation -----------------------------
    # Output ceiling for one prep brief: eleven structured sections over the
    # full lead context. A truncated brief is a brief with the opening script
    # missing -- see anthropic_client.TruncatedResponseError.
    meeting_prep_max_tokens: int = 4096
    # The post-meeting follow-up is one short email.
    meeting_followup_max_tokens: int = 1500
    # The 24h / 1h reminder sweep runs in every beat burst
    # (beat_sweep_minutes), so the one-hour reminder lands between one hour
    # and one sweep interval before the meeting.

    # --- Pipeline engine -------------------------------------------------
    pipeline_step_timeout_seconds: int = 300
    # Budget for ONE run_pipeline / run_verification TASK (up to 144 steps, or
    # 10 verification passes), not one step. The global Celery limit above is a
    # per-step figure; applied to the whole task it SIGKILLed every full run at
    # 360s (seen live 2026-10-05: killed at step 63, status stuck "researching").
    pipeline_task_timeout_seconds: int = 7200
    # Verification loop: max fix→re-run attempts per pass before the
    # strategy is marked needs_human_review (project knowledge, section D).
    verification_max_retries_per_pass: int = 3
    # Output ceiling for the verification FIXER, which is asked to emit the
    # complete revised strategy document.
    #
    # Was hardcoded at 8000 against a document measured at ~8,234 tokens, so the
    # rewrite could not physically fit: 7 of 8 fixer calls in the 2026-08-19 run
    # returned exactly out_tokens=8000 and each truncated result was written
    # over strategy_document.
    #
    # 18,432 = ~2.2x the largest real document observed (8,234 tokens), and
    # deliberately just under the SDK's non-streaming cut-off. The SDK refuses a
    # non-streaming request whose max_tokens implies >10 minutes of generation
    # (measured: 21,333 accepted, 32,000 refused), and 32,000 made every
    # run_pipeline call crash-retry until the integration suite caught it.
    #
    # This is one of three layers, not a lone guess: if a document ever outgrows
    # this, _generate_fix retries once at double (which streams, since it is
    # past the cut-off), and if THAT truncates the loop refuses to persist the
    # partial text. The model's own cap is 128,000, confirmed live via the
    # Models API, so there is room to raise this — but raising it past ~20,000
    # means the primary call streams, and the integration mock does not speak
    # SSE yet.
    verification_fixer_max_tokens: int = 18_432

    # --- CRM real-time stream (M9) --------------------------------------
    # How long ONE Server-Sent Events connection is held before the server
    # asks the client to reconnect. Not a timeout in the failure sense: the
    # stream is working fine at the moment it fires. It exists because an SSE
    # connection is a worker slot held open for as long as the tab is open,
    # and a dashboard left up over a weekend would otherwise hold one for
    # days -- through a deploy, through a Redis failover, through every
    # transient the process lives past.
    #
    # An hour, because the browser's own EventSource reconnect makes the cost
    # of cycling a connection close to nothing (it retries automatically, and
    # the client re-mints a stream ticket first), while the benefit is that
    # no connection outlives a normal working session.
    #
    # 0 (or any non-positive value) is a KILL SWITCH, matching
    # support_chat_enabled: GET /crm/stream answers 503 and every client falls
    # back to its React Query poll. The lever to pull if the stream ever
    # misbehaves in production; no deploy required. It refuses the connection
    # rather than accepting one and immediately asking for a reconnect, which
    # would put every open tab into a tight connect/disconnect loop.
    crm_stream_max_seconds: int = 3600

    # --- Identity & location verification (onboarding, migration 0039) ----
    # Which IP-geolocation service answers "what country is this request
    # from?" at onboarding. The answer is a RISK SIGNAL compared with the
    # country the user declares -- a mismatch is flagged for admin review and
    # never blocks signup (VPNs and travel are normal).
    #   ipapi_co  https://ipapi.co   -- no key needed, ~1k lookups/day free
    #   ipinfo    https://ipinfo.io  -- GEOLOCATION_API_KEY is the token
    #   disabled  never look anything up (the test suite)
    geolocation_provider: str = "ipapi_co"
    geolocation_api_key: str = ""
    geolocation_timeout_seconds: float = 4.0

    # --- VPN / proxy / datacenter blocking at signup and login -------------
    # UNSET MEANS "ON IN PRODUCTION, OFF EVERYWHERE ELSE" (see
    # vpn_blocking_active below). Setting it explicitly wins in both
    # directions, so a dev box can exercise the block and a production
    # incident can switch it off with an env var and a restart.
    vpn_block_enabled: bool | None = None
    #   proxycheck      https://proxycheck.io (key optional; 1k/day without)
    #   ipqualityscore  https://www.ipqualityscore.com (key required)
    #   disabled        never classify (treated as clean)
    vpn_detection_provider: str = "proxycheck"
    vpn_detection_api_key: str = ""
    vpn_detection_timeout_seconds: float = 4.0
    # When the provider is DOWN: false (default) lets the request through and
    # logs it, because failing closed turns a third-party outage into "nobody
    # can log in". Set true only if a VPN getting through is the worse outcome.
    vpn_detection_fail_closed: bool = False
    # One lookup per IP per hour is plenty: an IP's VPN status does not change
    # between a failed and a retried login, and every lookup spends quota.
    vpn_detection_cache_seconds: int = 3600
    # Comma-separated IPs that are never blocked (an office egress that a
    # provider misclassifies as hosting, a monitoring probe).
    vpn_allowlist_ips: str = ""

    # --- Public sign-up (P0-D-ENF) ----------------------------------------
    # P0-D: each deployment serves ONE customer organisation. An open
    # POST /auth/signup lets any visitor create an account, which makes a
    # deployment multi-tenant in practice. Two switches, both read on every
    # sign-up (an env change + restart takes effect, no deploy):
    #   SIGNUP_ENABLED=false               nobody can self-register; accounts
    #                                      come from the admin CLI only
    #   SIGNUP_ALLOWED_EMAIL_DOMAINS=a.com,b.co
    #                                      only these exact domains may register
    #                                      (empty = any domain)
    # Defaults keep today's behaviour (open, any domain); whether production
    # closes it is a founder decision (DECISIONS.md P0-D-ENF). Existing accounts
    # can always log in.
    signup_enabled: bool = True
    signup_allowed_email_domains: str = ""

    # --- Phone verification (SMS one-time code) ----------------------------
    #   twilio   Twilio Messages API (TWILIO_* below)
    #   console  log a MASKED notice, deliver nothing (local dev)
    #   memory   in-process list the test suite reads the code from
    # Explicit rather than inferred from which keys exist, for the same reason
    # as email_provider: a typo'd key must fail loudly, not silently log codes.
    sms_provider: str = "console"
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    # E.164 sender, or a Messaging Service SID (starts "MG").
    twilio_from_number: str = ""
    sms_send_timeout_seconds: float = 10.0
    phone_otp_ttl_seconds: int = 600
    phone_otp_max_attempts: int = 5
    phone_otp_resend_cooldown_seconds: int = 60
    # KILL SWITCH, matching REQUIRE_EMAIL_VERIFICATION: while true, accounts
    # created after migration 0039 cannot start outreach, take a paid plan or
    # mint public share links until their phone is verified.
    require_phone_verification: bool = True

    # --- Billing: Stripe (Section E, migration 0040) -----------------------
    # EMPTY SECRET KEY = STUB MODE. Checkout activates the chosen plan locally
    # and marks it is_stub; per-meeting charges are marked charged with
    # is_stub. Every stub path logs "TODO: connect live Stripe keys". See
    # BUILD_BLOCKERS.md.
    stripe_secret_key: str = ""
    # whsec_... from the Stripe dashboard webhook endpoint for /webhooks/stripe.
    # EMPTY MEANS THE WEBHOOK IS CLOSED (503), never "accept unsigned events".
    stripe_webhook_secret: str = ""
    stripe_api_base: str = "https://api.stripe.com"
    stripe_timeout_seconds: float = 20.0
    # Optional pre-created recurring Price ids per monthly tier. Unset, checkout
    # sends inline price_data built from app/core/billing_catalog.py, so the
    # page and the charge cannot disagree.
    stripe_price_starter: str = ""
    stripe_price_growth: str = ""
    stripe_price_scale: str = ""
    stripe_price_enterprise: str = ""
    # Same for the annual plans (interval "year"). If set, the Price's amount
    # MUST equal monthly x ANNUAL_MONTHS_CHARGED (app/core/billing_catalog.py);
    # nothing checks it against the catalog. Unset = inline price_data.
    stripe_price_starter_annual: str = ""
    stripe_price_growth_annual: str = ""
    stripe_price_scale_annual: str = ""
    stripe_price_enterprise_annual: str = ""
    # Seconds of clock skew tolerated on a Stripe-Signature timestamp.
    stripe_webhook_tolerance_seconds: int = 300

    # --- Feature A2: tamper-evident audit trail ----------------------------
    # Base64 of a 32-byte Ed25519 seed that signs audit-trail exports. EMPTY =
    # derived from SECRET_KEY (stable for a deployment). Set it explicitly
    # before rotating SECRET_KEY, or reports signed earlier will no longer
    # verify against GET /audit/public-key.
    audit_signing_key: str = ""

    @field_validator("beat_sweep_minutes")
    @classmethod
    def _sweep_divides_the_hour(cls, v: int) -> int:
        # The bursts sit at fixed minutes of every hour (app/workers/
        # celery_app.py); a value that does not divide 60 would leave a short
        # gap at the top of each hour.
        if 60 % v:
            raise ValueError("BEAT_SWEEP_MINUTES must divide 60 (5, 6, 10, 12, 15, 20, 30 or 60)")
        return v

    # ===================================================================
    # Moved unchanged from app/core/config.py (F-OPS-03: one Settings).
    # Names, types, defaults and env vars are as they were there; every
    # caller still writes settings.RATE_LIMIT_AUTH etc. The fields that
    # module shared with this one are not repeated: they are the read-only
    # aliases defined after the class (_CORE_ALIASES).
    # ===================================================================
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30
    ADMIN_EMAIL: Optional[str] = None

    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 10

    CELERY_ALWAYS_EAGER: bool = False
    CELERY_EAGER_PROPAGATES: bool = False
    CELERY_CONCURRENCY: int = 4

    # ---------------------------------------------------------------------------
    # Gmail OAuth
    # ---------------------------------------------------------------------------
    GMAIL_CLIENT_ID: str = ""
    GMAIL_CLIENT_SECRET: str = ""
    GMAIL_REDIRECT_URI: str = "http://localhost:8000/integrations/gmail/callback"
    """Must match a redirect URI registered in Google Cloud Console.

    NOT /api/v1/... — app/main.py mounts every router UNPREFIXED, so the
    real path is /integrations/gmail/callback. The /api/v1 form pointed at
    a route that has never existed (verified: 0 of 78 mounted paths start
    with /api/v1), which meant Google would redirect the user to a 404 and
    the OAuth flow could never complete."""

    WHATSAPP_WABA_ID: str = ""
    CALENDLY_CLIENT_ID: str = ""
    CALENDLY_CLIENT_SECRET: str = ""

    # ---------------------------------------------------------------------------
    # Firebase
    # ---------------------------------------------------------------------------
    FIREBASE_PROJECT_ID: str = ""
    FIREBASE_CREDENTIALS_JSON: str = "{}"

    ENCRYPTION_KEY_PREVIOUS: Optional[str] = None

    # ---------------------------------------------------------------------------
    # Circuit breaker (M8-C3)
    # ---------------------------------------------------------------------------
    CIRCUIT_BREAKER_FAILURE_THRESHOLD: int = 5
    CIRCUIT_BREAKER_RECOVERY_TIMEOUT: int = 60

    # ---------------------------------------------------------------------------
    # Pipeline monitoring (M8-C3)
    # ---------------------------------------------------------------------------
    PIPELINE_STEP_TIMEOUT_MINUTES: int = 10

    # ---------------------------------------------------------------------------
    # Learning loop (M8-C1+C2)
    # ---------------------------------------------------------------------------
    PLAYBOOK_MIN_SAMPLE: int = 30
    AB_MIN_LIFT: float = 0.10
    AB_SIGNIFICANCE_THRESHOLD: float = 0.05
    # Max harm-metric rate (bounces + spam + opt-outs per send) the winning
    # variant may carry before auto-promotion is blocked -- ab_testing.py
    # gate 4. Undeclared until now, so extra="ignore" dropped the env var
    # and getattr() always fell through to its hardcoded 0.05 default:
    # AB_HARM_CEILING in .env has never had any effect.
    AB_HARM_CEILING: float = 0.05
    PLAYBOOK_AGGREGATION_UTC_HOUR: int = 2  # 02:00 UTC / 07:00 PKT

    # ---------------------------------------------------------------------------
    # Score decay (M8-C4)
    # ---------------------------------------------------------------------------
    PLAYBOOK_SCORE_HALF_LIFE_DAYS: int = 90
    """
    Half-life for exponential score decay.
    A score from PLAYBOOK_SCORE_HALF_LIFE_DAYS ago counts half as much as today.
    Default: 90 days. Lower = faster forgetting; higher = more weight on history.
    """

    # ---------------------------------------------------------------------------
    # Multi-variate testing (M8-C4)
    # ---------------------------------------------------------------------------
    MV_MAX_VARIANTS: int = 5
    """Maximum number of variants in a multi-variate test. Default: 5."""

    # ---------------------------------------------------------------------------
    # Rate limiting (M8-C5)
    # ---------------------------------------------------------------------------
    RATE_LIMIT_STRATEGIES: int = 2
    """Max POST /products/*/strategies calls per user per hour. Default: 2.

    RAISE THIS ONCE REAL USAGE IS KNOWN — it is deliberately conservative for
    launch, and this docstring plus .env.production.example are the two places
    to change.

    Lowered from 10 on 2026-08-20. Two reasons, both measured rather than
    guessed:

    1. The limit scale in this class is ordered by cost — wa_templates (one
       Claude call) 20/hr, leads_source (paid external API) 5/hr,
       playbook_recompute (heavy DB aggregation, admin-only) 3/hr. Strategy
       generation is a 72- or 144-step pipeline plus 10 verification passes:
       the 2026-08-19 Phase C run measured ~150 Claude calls for ONE strategy,
       and up to ~180 when passes retry. At 10/hr it was the MOST permissive
       limit in the system while being by far the most expensive operation —
       the scale was inverted.
    2. Throughput makes a high limit meaningless anyway. That same run took
       ~62 minutes of pipeline time (144 steps at ~26s) plus ~10 minutes of
       verification. One strategy occupies a pipeline worker for over an hour,
       so allowing 10 per hour per user only builds a multi-hour queue.

    2 (not 1) so a user who misconfigures a product can immediately retry
    without a support ticket, while capping worst-case spend at two full
    pipelines per user per hour.
    """

    RATE_LIMIT_LEADS_SOURCE: int = 5
    """Max POST /strategies/*/leads/source calls per user per hour. Default: 5."""

    RATE_LIMIT_PLAYBOOK_RECOMPUTE: int = 3
    """Max POST /playbook/recompute calls per admin per hour. Default: 3."""

    RATE_LIMIT_WA_TEMPLATES: int = 20
    """Max POST /whatsapp/templates/generate calls per user per hour. Default: 20."""

    RATE_LIMIT_PUBLIC_BOOKING: int = 30
    """Max public booking-page writes per IP per hour (Engagement Hub).

    Scoped to the IP, not a user, because there is no user: POST
    /calendar/booking-pages/{slug}/book is the one write in this API that an
    unauthenticated stranger can make. Without a limit, a booking page is a
    free way to fill a founder's calendar with junk and their inbox with
    confirmation emails sent from OUR domain -- a deliverability problem as
    much as a nuisance.

    30/hour is loose for a human (who books once) and tight for a script. The
    slots endpoint is NOT limited by this: it is a read, it is what the page
    calls on every date click, and limiting it would break the booking flow
    for a person who is simply browsing dates.

    NOTE: read from app/core/config.py, not app/config.py.
    """

    RATE_LIMIT_CALENDAR_WRITE: int = 300
    """Max authenticated calendar/meeting writes per user per hour.

    Sized like RATE_LIMIT_CRM_WRITE and for the same reason -- these are rows
    in PostgreSQL, not external spend -- but half of it, because the traffic
    is a person editing availability and typing meeting notes rather than
    working through a grid of five thousand leads. The meeting-notes autosave
    fires at most once every 10 seconds per open meeting, which is 360/hour
    for somebody in back-to-back calls all day; the two ceilings that matter
    are set above that.

    NOTE: read from app/core/config.py, not app/config.py.
    """

    RATE_LIMIT_PUBLIC_VIDEO: int = 120
    # Feature Group 3: loads of ONE open pixel per hour (keyed by token, not
    # IP -- Gmail proxies every recipient's images through shared IPs).
    RATE_LIMIT_OPEN_PIXEL: int = 30
    """Max GET /public/video per client IP per hour (Feature Group 2). The
    endpoint is unauthenticated -- a prospect opens it from an email -- and
    returns only a first name, a company and a Loom embed id, behind an
    encrypted token; the limit stops it being used to brute-force tokens.

    NOTE: read from app/core/config.py, not app/config.py.
    """

    RATE_LIMIT_AI_ACTION: int = 60
    """Max user-triggered AI generations per user per hour (feature expansion):
    regenerating a meeting prep brief, logging a meeting outcome (which drafts
    a follow-up), rescoring a lead, extracting a style profile. Each is one
    Claude call billed to the deployment's key, so this is a spend ceiling
    like RATE_LIMIT_SUPPORT_CHAT, sized for a busy day of calls rather than
    for a script.

    NOTE: read from app/core/config.py, not app/config.py.
    """

    RATE_LIMIT_AGENT_RUN: int = 120
    """Max Victorae agent runs (POST /agents/{name}/run) per user per hour.
    No model call, but a run reads up to a few thousand rows."""

    RATE_LIMIT_CRM_WRITE: int = 600
    """Max CRM write calls per user per hour (M9). Default: 600.

    Deliberately the LOOSEST limit in this class, because it is the only one
    whose governed operation costs nothing external. Every other RATE_LIMIT_*
    here is a spend ceiling -- strategies burn ~150 Claude calls, leads_source
    spends Apollo and Hunter credits, support_chat spends the Anthropic key.
    A CRM write is one row in PostgreSQL.

    600/hour is 10 per minute sustained. The traffic shape this governs is
    inline cell editing in the data grid: a user working a list of leads
    types a value, tabs to the next cell, types again. A burst of twenty
    edits in a minute is somebody doing their job, not abuse, and a limit
    tight enough to catch abuse here would mostly catch that user instead --
    with a 429 in the middle of a row they were halfway through editing.

    What it does still bound: a runaway client loop, and a script pointed at
    the bulk endpoint. Note that ONE bulk call may touch up to 500 leads and
    costs ONE slot -- the limit counts requests, not rows, so the honest
    high-volume path (select many, act once) is the cheap one and the abusive
    path (500 individual PATCHes) is the expensive one. That is the right way
    round.

    Raise it if a legitimate user reports a 429 while editing; it is a single
    env var, and nothing about it protects a paid resource.
    """

    RATE_LIMIT_GET: int = 300
    """Max GET endpoint calls per user per minute. Default: 300."""

    RATE_LIMIT_OTP_SEND: int = 5
    """Max SMS verification codes per HOUR, applied to TWO keys: the account
    and the destination number. Every send costs money (Twilio bills per
    segment) and an unlimited resend is an SMS-pumping fraud vector, so this is
    a spend ceiling first. 5/hour covers a mistyped number plus two resends.

    NOTE: read from app/core/config.py, not app/config.py.
    """

    RATE_LIMIT_OTP_VERIFY: int = 10
    """Max code-verification attempts per account per 15 minutes. A six-digit
    code has a million values; each code additionally dies after
    PHONE_OTP_MAX_ATTEMPTS wrong guesses, so this bounds guessing ACROSS
    resends rather than within one code.

    NOTE: read from app/core/config.py, not app/config.py.
    """

    RATE_LIMIT_PUBLIC_SHARE: int = 120
    """Max public ROI share-link views per client IP per hour. The dashboard is
    unauthenticated by design (a link a founder forwards to a client), so the
    limit is what stops it being used to brute-force tokens.

    NOTE: read from app/core/config.py, not app/config.py.
    """

    RATE_LIMIT_PUBLIC_STATUS: int = 600
    """Max GET /status/public calls per client IP per hour (Phase 3, the
    public status page). Unauthenticated by design: the page at
    www.calendarharvest.com/status/ calls it from each visitor's browser every
    30 s while the tab is visible (120/h per open tab), and the optional
    status-page/ app calls it from its server every 60 s. Each call is cheap (a
    30 s in-process response cache sits behind the limiter), so the limit only
    stops a flood from one address; 600/h is ten a minute. Visitors behind one
    shared address (office NAT, carrier-grade NAT) share the budget; the page
    treats a 429 as "slow down", keeps its last result and backs off to 2 min,
    and never reports it as an outage.

    NOTE: read from app/core/config.py, not app/config.py.
    """

    RATE_LIMIT_AUTH: int = 10
    # Feature 3: AI support chat messages per user per DAY. Every message
    # spends the account's Anthropic key, so this is a cost ceiling as much as
    # an abuse control. Window is 86400s, set at the call site.
    #
    # 20, lowered from 30 on 2026-08-30 at the product owner's instruction.
    # The case for 30 was that a user troubleshooting a real problem sends
    # 15-20 messages in one sitting, and cutting them off mid-thread pushes
    # them into a support ticket -- the outcome the chat exists to avoid. At 20
    # that user is now cut off right at the edge of a normal session, so expect
    # some tickets that a higher cap would have absorbed. The ceiling was
    # chosen over that risk deliberately; raise this value if ticket volume
    # from exhausted sessions shows up.
    #
    # The cap is enforced identically in mock mode, where a message costs
    # nothing. That is on purpose: a user must not build a habit against a
    # limit that tightens the day a real key is added.
    # Connector OAuth (app/api/oauth.py): client registrations and authorize
    # requests per IP per HOUR. Deliberately far above RATE_LIMIT_AUTH:
    # Claude registers from Anthropic's shared egress range (160.79.104.0/21)
    # for every user who connects, so a per-IP limit sized for one person
    # would lock every Claude user out. It only stops a flood.
    RATE_LIMIT_OAUTH: int = 600
    RATE_LIMIT_SUPPORT_CHAT: int = 20
    """Max login/signup attempts per 15-minute window. Default: 10.

    Wired 2026-08-20 (it had been inert since M8-C5: defined, documented, and
    referenced by zero routes, so login and signup accepted unlimited
    credential guesses). Applied in app/api/auth.py to TWO independent keys,
    both governed by this one number:

      per IP       "ip:<addr>"    — one host brute-forcing passwords
      per account  "acct:<email>" — credential stuffing spread across many
                                    IPs at a single account, which a per-IP
                                    limit alone does nothing about

    Why 10, security-based rather than cost-based:

    * A legitimate user needs 1-3 attempts. Mistyping a password four times
      in 15 minutes already means going to a reset, not a fifth guess, so 10
      leaves ~3x headroom over the worst realistic honest case.
    * An attacker gets 10 guesses per 15 minutes = 960/day per key. Against
      even a weak six-character lowercase password (~3.1e8 combinations) that
      is on the order of a million years. The point is not to make guessing
      hard, it is to make ONLINE guessing useless, and any limit in this range
      does that; 10 buys it without inconveniencing real users.
    * The window is 15 minutes, not an hour, so a locked-out honest user waits
      minutes rather than most of an hour. See AUTH_WINDOW_SECONDS in
      app/core/rate_limiting.py.

    KNOWN TRADE-OFF: attempts are counted whether they succeed or fail, so an
    office behind one NAT IP shares the per-IP bucket — 11 people logging in
    inside the same 15 minutes would see a 429 with Retry-After. Real logins
    are roughly once or twice a day (clients hold a refresh token rather than
    re-authenticating), so this is unlikely but not impossible. If a customer
    reports it, RAISE THIS VALUE — it is a single env var. The alternative,
    counting only failed attempts, needs a check-then-conditionally-record
    split the limiter does not have and would mean two divergent mechanisms.

    /auth/refresh is deliberately NOT limited: legitimate clients hit it every
    time a 15-minute access token expires, and a signed refresh token is not
    brute-forceable.
    """

    # Sending limits live in app/config.py (gmail_daily_cap,
    # gmail_warmup_start_sends, gmail_warmup_daily_increment,
    # bounce_rate_pause_threshold, bounce_min_sends). The M8-C3 fields
    # GMAIL_DAILY_SEND_CAP / GMAIL_BOUNCE_PAUSE_THRESHOLD /
    # GMAIL_WARMUP_START_DAILY that stood here were read by nothing and were
    # removed (gaps-sweep-2 #12); extra="ignore" keeps an old .env loading.

    # ---------------------------------------------------------------------------
    # Frontend / CORS
    # CORS_ORIGINS has TWO readers that disagreed on format, and each format
    # broke the other:
    #   * app/main.py (the one that actually configures CORSMiddleware) does
    #     os.getenv("CORS_ORIGINS").split(",")  -> wants comma-separated
    #   * this field, typed list[str], made pydantic-settings JSON-decode it
    #     -> wanted ["https://a.com","https://b.com"]
    # A JSON value satisfied pydantic but produced origins like '["https://a.com"'
    # in main.py, which never match a browser Origin: the API returns 200, every
    # healthcheck passes, and only the browser sees a CORS error. A plain CSV
    # value fixed main.py but made this field raise SettingsError at import.
    # Comma-separated is now the single canonical format.
    #
    # Typed `str`, NOT `list[str]`, on purpose: for complex types
    # pydantic-settings json.loads() the value inside the env/dotenv SOURCE,
    # before any field_validator can normalise it, so a comma-separated value
    # raised SettingsError at import no matter what validator was attached.
    # Keeping it a plain string means the source never decodes it; use
    # `cors_origins_list` for the parsed form. app/main.py remains the
    # authoritative reader that configures CORSMiddleware.
    CORS_ORIGINS: str = "http://localhost:3000"

    # ---------------------------------------------------------------------------
    # Android
    # ---------------------------------------------------------------------------
    ANDROID_PACKAGE_NAME: str = "io.clienthunter.app"

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"

    @property
    def is_testing(self) -> bool:
        return self.APP_ENV == "testing" or os.getenv("TESTING") == "1"

    @property
    def cors_origins_list(self) -> list[str]:
        """CORS_ORIGINS parsed the same way app/main.py parses it.

        Tolerates the legacy JSON-array form so an old .env does not silently
        yield origins with brackets baked into them.
        """
        raw = (self.CORS_ORIGINS or "").strip()
        if not raw:
            return []
        if raw.startswith("["):
            import json

            try:
                return [str(o).strip() for o in json.loads(raw)]
            except (ValueError, TypeError):
                return []
        return [origin.strip() for origin in raw.split(",") if origin.strip()]

    @classmethod
    def validate_encryption_key(cls, v: str) -> str:
        if not v and os.getenv("TESTING") != "1" and os.getenv("APP_ENV") != "testing":
            import warnings
            warnings.warn(
                "ENCRYPTION_KEY is not set — storing or reading an integration token "
                "will FAIL (fail-closed; there is no plaintext fallback). "
                "Set ENCRYPTION_KEY to a Fernet key.",
                stacklevel=2,
            )
        return v

    @property
    def billing_stub_mode(self) -> bool:
        return not (self.stripe_secret_key or "").strip()

    @property
    def vpn_blocking_active(self) -> bool:
        if self.vpn_block_enabled is not None:
            return bool(self.vpn_block_enabled)
        return (self.app_env or "").strip().lower() in {"production", "prod", "live"}

    @property
    def signup_allowed_domains(self) -> frozenset[str]:
        """SIGNUP_ALLOWED_EMAIL_DOMAINS parsed: lower-case, no '@', no blanks."""
        raw = self.signup_allowed_email_domains or ""
        return frozenset(
            d.strip().lower().lstrip("@") for d in raw.split(",")
            if d.strip().lstrip("@")
        )

    @property
    def broker_url(self) -> str:
        return self.celery_broker_url or self.redis_url

    @property
    def result_backend(self) -> str:
        return self.celery_result_backend or self.redis_url

    @property
    def is_test(self) -> bool:
        return self.app_env == "test"

    @property
    def debug_routes_allowed(self) -> bool:
        """May the test-only /debug router exist in this process?

        F9: an ALLOWLIST. The dedicated flag must be on AND the environment
        must be development, local or test. It used to be "anything except
        production/prod/live", which let staging -- or a misspelt environment
        name -- serve routes that fabricate leads and mint unsubscribe tokens.
        """
        env = (self.app_env or "").strip().lower()
        return bool(self.enable_debug_routes) and env in DEBUG_ROUTE_ENVS

    @property
    def mcp_resource(self) -> str:
        return (self.mcp_resource_url or
                f"{(self.public_base_url or '').rstrip('/')}/mcp").strip()

    @property
    def mcp_issuer(self) -> str:
        """The OAuth issuer: the MCP URL's origin, no trailing slash. Clients
        compare it to the metadata `issuer` and to `iss` byte for byte."""
        parsed = urlparse(self.mcp_resource)
        return f"{parsed.scheme}://{parsed.netloc}"

    @property
    def mcp_extra_allowed_hosts(self) -> tuple[str, ...]:
        return tuple(h.strip() for h in (self.mcp_allowed_hosts or "").split(",") if h.strip())

    @property
    def mcp_allowed_origins_list(self) -> tuple[str, ...]:
        return tuple(o.strip() for o in (self.mcp_allowed_origins or "").split(",")
                     if o.strip())

    @property
    def refresh_cookie_samesite(self) -> str:
        value = (self.auth_refresh_cookie_samesite or "").strip().lower()
        return value if value in {"lax", "strict", "none"} else "lax"

    @property
    def refresh_cookie_secure(self) -> bool:
        # Browsers drop a SameSite=None cookie that is not Secure, so "none"
        # always implies it regardless of the explicit setting.
        if self.refresh_cookie_samesite == "none":
            return True
        if self.auth_refresh_cookie_secure is not None:
            return bool(self.auth_refresh_cookie_secure)
        return (self.app_env or "").strip().lower() in {"production", "prod", "live"}


# F-OPS-03: the names app/core/config.py used for settings this class already
# has, kept as read-only aliases so settings.REDIS_URL and the rest still read
# the one value. Each reads the env var named on the right's field.
_CORE_ALIASES = {
    "APP_ENV": "app_env",
    "SECRET_KEY": "jwt_secret",
    "LOG_LEVEL": "log_level",
    "DATABASE_URL": "database_url",
    "REDIS_URL": "redis_url",
    "CELERY_BROKER_URL": "celery_broker_url",
    "CELERY_RESULT_BACKEND": "celery_result_backend",
    "ANTHROPIC_API_KEY": "anthropic_api_key",
    "ANTHROPIC_MODEL": "anthropic_model",
    "ANTHROPIC_MAX_TOKENS": "anthropic_max_tokens",
    "APOLLO_API_KEY": "apollo_api_key",
    "HUNTER_API_KEY": "hunter_api_key",
    "WHATSAPP_PHONE_NUMBER_ID": "whatsapp_phone_number_id",
    "WHATSAPP_ACCESS_TOKEN": "whatsapp_access_token",
    "WHATSAPP_VERIFY_TOKEN": "whatsapp_webhook_verify_token",
    "WHATSAPP_APP_SECRET": "whatsapp_app_secret",
    "CALENDLY_WEBHOOK_SECRET": "calendly_webhook_signing_key",
    "ENCRYPTION_KEY": "token_encryption_key",
    "FRONTEND_URL": "frontend_url",
}
for _alias, _field in _CORE_ALIASES.items():
    setattr(Settings, _alias, property(lambda self, _f=_field: getattr(self, _f)))


#: The only environments in which app/api/debug.py may be mounted (F9).
DEBUG_ROUTE_ENVS = frozenset({"development", "dev", "local", "test", "testing"})


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor — import this everywhere, never Settings()."""
    return Settings()


settings = get_settings()