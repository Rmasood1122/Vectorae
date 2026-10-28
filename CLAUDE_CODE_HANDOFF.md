# Vectorae — Claude Code Handoff Brief

## Your task
Take this project from its current state to fully cloud-deployment-ready.
Work directly in this repo — read files, edit them, run commands, run
tests, iterate. Don't ask for permission on routine steps; just do the
work and report back when you hit something that genuinely needs a human
decision (API keys, hosting choice, domain name, etc.).

**Critical instruction on file edits:** previous work on this project was
done through a chat interface where the user had to copy-paste file
contents into PowerShell, which repeatedly introduced BOM (byte-order-mark)
corruption and encoding mojibake when `[System.Text.Encoding]::UTF8` was
used. You don't have this problem — edit files directly with your own
tools. Always verify a file's syntax after editing it (`python -m py_compile`
for Python, `npx tsc --noEmit` for TypeScript) before moving on.

**Verification discipline:** don't mark anything done without actually
running it. Restart the affected server, hit the actual endpoint or page,
confirm the real behavior. This project's audit found 8+ security bugs
that were "probably fine" until actually tested — assume nothing.

---

## What this project is
Vectorae — a self-learning B2B client-acquisition
platform. FastAPI + PostgreSQL + Celery backend, Next.js (static export,
for Capacitor mobile bundling) frontend. Backend at repo root
(`app/`), frontend in `frontend/`.

## What's already been fixed (don't redo these — verify they're still intact)

### Backend security (Phase A — all done)
A systemic pattern was found: 8 separate API route files had zero
ownership/tenancy checks (any logged-in user could read/edit/delete other
users' data, in some cases even pause/sabotage another account's active
campaign). All fixed with a consistent `_owned_strategy`/`_owned_lead`
helper pattern:
- `app/api/products.py`, `strategies.py`, `leads.py`, `sequences.py` — owner
  checks added.
- `app/api/ui_support.py` — **the most important fix**: this router is
  registered in `app/main.py` BEFORE `strategies_router`, so its `/strategies`
  GET route silently shadowed the "fixed" one in `strategies.py` — the
  first fix was dead code until this was found. If you ever add a new
  route, check `app/main.py`'s `include_router()` order for collisions.
- `app/api/integrations.py` — Gmail OAuth `/auth-url` and `/status` used
  to accept an unauthenticated `user_email` query param (anyone could
  probe anyone's Gmail connection status). Fixed to JWT-only.
- `app/api/analytics.py` — anyone could pause/resume ANY account's
  campaign. Fixed.
- `app/api/whatsapp_templates.py` — templates are a legitimately shared
  deployment-wide resource (one Meta WhatsApp Business Account per
  install), so these are login-required but NOT owner-scoped — that's
  correct, don't "fix" it into per-user scoping.
- `app/api/whatsapp_optin.py` — CSV bulk import had zero auth and matched
  leads by phone across ALL users' data. Fixed to scope to current_user's
  own leads.

### Config/architecture duplication (recurring pattern — check for more)
This codebase has repeatedly turned out to have TWO parallel
implementations of the same thing, with the wrong one silently active:
- Two Settings classes (`app/config.py` old, `app/core/config.py` new) —
  4 fields aliased to agree on one env var name each.
- Two crypto modules — `app/services/crypto.py` now delegates to
  `app/core/crypto.py`.
- Two `/strategies` GET routes — see ui_support.py note above.
- Two auth-token storage systems (frontend) — `client.ts` used to write
  raw `localStorage` with its own key names, completely bypassing the
  tested `storage.ts`/`auth-session.ts` (sessionStorage-on-web /
  Capacitor-Preferences-on-native) system. Fixed — `client.ts` now
  delegates to `auth-session.ts`.
- Two ThemeProvider implementations — the one actually mounted in
  `app/layout.tsx` (`components/providers/ThemeProvider.tsx`) was a
  complete no-op stub; the real, working implementation lived in
  `lib/theme/ThemeProvider.tsx` but was never rendered. This meant theme
  preset selection had ZERO effect in production. Fixed — the stub now
  re-exports the real one.
**Before declaring any area "done," grep for a second implementation of
the same concern.** This pattern has repeated at least 6 times.

### Frontend routing (Phase B — mostly done)
- `output: 'export'` (static export, required for the Capacitor mobile
  build — see `Dockerfile`, `next.config.js`) means dynamic path-segment
  routes (`/strategies/[id]`) CANNOT serve arbitrary runtime IDs — no
  server exists to render them. Converted `/strategies/[id]` →
  `/strategies/detail?id=` and `/strategies/[id]/document` →
  `/strategies/document?id=`. **If you ever see a `[dynamic]` folder
  under `src/app`, this is broken — convert to query params.**
- `QueryProvider` (`components/providers/QueryProvider.tsx`) was also a
  no-op stub — fixed to a real TanStack Query provider.
- `leads.py`'s response field is `items`, not `leads` — frontend
  (`lib/api/leads.ts`, `pipeline/page.tsx`) was fixed to match. Grep for
  any other frontend/backend response-shape mismatches — this bug class
  (found via a runtime crash) likely has siblings nobody's hit yet.
- `campaigns/page.tsx` had literal `&quot;` HTML entities inside JS
  expression contexts (invalid syntax) — fixed.

### Tests (Phase B11/B12)
- Vitest: 144/144 passing. Found 2 real production bugs via test
  failures: `deeplinks.ts` had a URL slash-count bug that would have
  broken every mobile push-notification deep link on-device;
  `platform.ts` used `require()` instead of ESM `import`, which broke
  bundler/test-mock correctness both.
- Playwright E2E: was stuck at several flaky failures. Root cause found:
  `next.config.js`'s dev server was file-watching `test-results/` —
  Playwright's own screenshot/trace writes during a test run triggered
  Next.js Fast Refresh full-page-reloads mid-test, wiping React state
  (the logged-in session, wizard step) and causing exactly the symptoms
  seen (random bounces to `/login`, elements "disappearing"). Fixed via
  a `webpack.watchOptions.ignored` config excluding `test-results/` and
  `playwright-report/`. **This fix was applied but not yet confirmed with
  a clean test run** — do that first (see Immediate Next Steps).
- `playwright.config.ts`: `trace`/`screenshot` were set to `on-first-retry`
  with `retries: 0` — meaning NO diagnostic artifact was EVER captured on
  failure. Fixed to `retain-on-failure`/`only-on-failure`.
- Multiple E2E tests had `loginViaUI(page)` immediately followed by
  `page.goto(...)` to a page login's own redirect was already navigating
  toward — a race Chromium tolerated but WebKit reported as "navigation
  interrupted." Fixed by adding `page.waitForURL(/\/pipeline\/?$/)`
  between login and any subsequent navigation. **If you add new E2E
  tests, always wait for the post-login redirect before navigating
  again.**

## Session update — 2026-08-19

### Frontend: green and stable
`npm run test:e2e` is **14/14 (Chromium + Mobile Safari)**, confirmed on two
consecutive runs. Vitest still 144/144. `npx tsc --noEmit` clean.

- **Chromium intake-wizard failure: resolved.** The Fast-Refresh-watcher fix
  plus serving the static export (`scripts/static-server.js`) instead of
  `next dev` cleared it. No further action.
- **Mobile Safari failures: root-caused and fixed.** Not a timing quirk. The
  suite runs against the STATIC EXPORT, so forms are fillable in the
  pre-rendered HTML *before* React hydrates. A value typed into that gap
  looks correct — `page.fill()` writes the DOM value directly, so even
  `expect(field).toHaveValue()` passes — but React never saw an `onChange`,
  its state is still `""`, and the hydration re-render writes that empty
  state back over the field. On `/login` the wiped field is `required`, so
  native validation blocks submit, `submit()` never runs, and the test sits
  on `/login` until timeout. Only WebKit hit it because it hydrates slowest:
  failure snapshots showed an empty email beside a correctly filled password.
  Fixed with `hydratedFill()` in `e2e/happy-paths.spec.ts`, which waits for
  React 18's `__reactFiber$<n>` property on the target node before typing.
  **Any new E2E test that fills a form must use `hydratedFill`, not
  `page.fill`** — a bare `page.fill` right after `page.goto` is this bug.

### Done this session
- **B5** — `strategies/detail/page.tsx` no longer renders "undefined. Phase
  undefined" when a pipeline errors before `progress` populates; `phase`,
  `done` and `total` are all guarded.
- **B7** — Next.js 14.2.3 → **14.2.35** (also `eslint-config-next`). Patch
  bump inside 14.2.x, not the 16.x jump `npm audit fix --force` wants:
  `output:'export'` + App Router + Capacitor makes a major upgrade real work.
  Verified: tsc clean, 144/144 vitest, 14/14 E2E.
- **B9** — `public/manifest.json` created (`icons: []`; there are no icon
  assets in the repo yet — populate before a real PWA/store build).

### B8 — npm audit triage (20 vulns: 4 critical, 11 high, 5 moderate)
Every remaining advisory is **dev/build-time only**, none reachable in the
shipped artifact:
- `esbuild`/`vite`/`vitest`, `glob`/`eslint-config-next`, `minimatch`/
  `@typescript-eslint` — test and lint toolchain.
- `tar`/`@capacitor/cli`, `sharp`/`@capacitor/assets`, `uuid`/`xcode`/
  `@trapezedev/project` — mobile build tooling, run on a developer machine.
The production output is a static export served by nginx; none of this ships.
Clearing them needs `vitest` 4.x, `eslint-config-next` 16.x and
`@capacitor/cli` 8.x — all breaking majors. **Recommend deferring until after
launch**, then upgrading deliberately, one toolchain at a time.

## Backend test suite — was never executable; now mostly is

This is the significant find of the session, and it is the same
"two parallel implementations, wrong one active" pattern documented above.

`tests/conftest.py` had been **replaced** by a 175-line real-PostgreSQL
harness. Consequences:
- 14 of 26 root test modules could not even import — they need `NOW`,
  `FakeLeadSource`, `FakeVerifier`, `make_raw`, `wa_outbound`,
  `wa_signed_post`, which that file does not define.
- Its `api_client` fixture imports `create_app` from `app.main` and `get_db`
  from `app.core.database`. **Neither exists** — `app/main.py` has no
  `create_app`, and `get_db` lives in `app/db/base.py`. That fixture has
  never run.
- Its `ENCRYPTION_KEY` value (`"test-fernet-key-32bytes-padded!!!"`) is not
  valid base64-url, so `app/core/crypto.py` would reject it anyway.

The 624-line SQLite fixture library those 22 modules are written against
exists in the sibling checkout `Downloads/clienthunter-complete/tests/`.
All 22 shared test modules are **byte-identical** between the two copies;
only `conftest.py` differed.

**This means the Phase A ownership/tenancy security fixes had zero automated
verification.** They are now under test.

### What was changed
- `tests/conftest.py` — restored the SQLite fixture library; added the env
  defaults the enterprise app needs (a *generated* `ENCRYPTION_KEY`, plus
  `SECRET_KEY`, Celery-eager and API-key placeholders).
- `tests/integration/conftest.py` — the PostgreSQL/Redis/respx/Celery harness
  moved here. Every one of `pg_engine` / `sync_redis` / `redis_client` /
  `api_client` / `mock_transports` is referenced only from this directory, so
  nothing else regressed. Integration tests need `docker compose up db redis`;
  the root suite deliberately needs neither.
- `tests/conftest.py` — added `test_user`, `auth_headers()` and `anon_client`.
  Phase A means an unauthenticated client never reaches a handler, and a
  client authed as a *different* user than the fixture data's owner gets 404
  instead — so `client`, `leads_client` and every object builder now share
  one canonical user, authenticated with a real signed JWT (the auth path
  stays under test rather than being stubbed out).
- `requirements.txt` / `pyproject.toml` — declared `respx` and
  `pytest-asyncio`; `respx` was imported by conftest but declared nowhere, so
  a fresh environment could not run the suite at all.

### Result
`python -m pytest tests --ignore=tests/integration`:
**257 passed, 17 failed, 8 errors** — up from 72 passed with 14 modules
unable to import.

### Backend — what is still open, by module
1. **`tests/test_devices.py`** (8 errors) — needs a `user_tokens` fixture
   that has never existed. Note before writing it: this module calls
   `await db_session.execute(...)` and uses `user_id=99999` (an int), but the
   root `db_session` is a **sync** Session and the model's user id is a UUID.
   The module was written against a different DB layer; decide whether the
   module or the app is authoritative before patching either.
2. **`tests/test_regression.py`** (8 failures) — pure import/interface
   assertions against paths that do not exist in this layout: `app.schemas`,
   `app.integrations.interfaces`, `app.services.playbook`, the `clienthunter`
   SDK, `ApolloLeadSource`, `HunterEmailVerifier`. Each needs a per-item call:
   module genuinely renamed (update the test) or genuinely missing (a real
   gap). Do not bulk-delete these — they are the architecture's tripwires.
3. **`tests/test_notifications.py`** (8 failures) — Firebase mock assertions
   plus a `sqlite3.OperationalError: ON CONFLICT clause does not match any
   PRIMARY KEY`, i.e. Postgres-dialect upsert SQL running on SQLite.
4. **`tests/test_whatsapp_webhooks.py::test_verification_handshake`** (1).
5. **`tests/integration/`** — untried; needs Postgres + Redis up. Docker
   Desktop was not running this session.

## Session update 2 — 2026-08-19 (integration tests, Docker up)

Run the backend suites like this:

    docker compose up -d db redis
    # db is on host 5433, redis on 6380 (docker-compose.yml now maps it;
    # 6379 was already taken on this host). Test DB: clienthunter_test.
    export ANTHROPIC_API_KEY=sk-ant-fake-key-for-tests   # never really called
    python -m pytest tests --ignore=tests/integration    # SQLite, no services
    python -m pytest tests/integration                   # needs db + redis

`TEST_DATABASE_URL` / `TEST_REDIS_URL` override the defaults. Do **not** point
`TEST_DATABASE_URL` at localhost:5432 — that is an unrelated project's
Postgres on this machine, and `pg_engine` ends its session with
`DROP SCHEMA public CASCADE`.

### Three production bugs found — all fixed, all verified by a test

These only surfaced because the tests finally run against real PostgreSQL.

1. **The entire `/devices` router was dead.** `app/api/devices.py` annotated
   `db: AsyncSession = Depends(get_db)` and used `await db.execute(...)`, but
   `get_db` yields a **synchronous** `Session`. FastAPI does not enforce the
   annotation, so every request raised `TypeError: object
   ChunkedIteratorResult can't be used in 'await' expression`. Both endpoints,
   every call. Converted to sync `def` handlers with `Session`, matching every
   other router in `app/api/`.
2. **`POST /devices/register` also 500'd on the upsert.** It does
   `ON CONFLICT (token)`, but the model and migration `0009_device_tokens.py`
   only ever declared `UNIQUE (user_id, token)`. PostgreSQL: "there is no
   unique or exclusion constraint matching the ON CONFLICT specification".
   Push-notification registration has never worked.
   **A schema decision was made here — reverse it before Phase D if you
   disagree.** The handler's docstring documents token transfer ("Tokens from
   OTHER users that match are updated to this user"), which requires `token`
   to be globally unique, so migration `0013_device_token_unique.py` makes it
   so and the model follows. The alternative — retarget the upsert to
   `(user_id, token)` and drop the transfer — would let two accounts hold rows
   for the same physical device and both receive its push notifications. An
   FCM token identifies one app install, so global uniqueness is the correct
   model. No deployed database exists yet, so this is free to change now.
3. **Every push-notification deep link was dead on device.**
   `app/services/notifications.py` built `f"{scheme}:/{clean}"` — two slashes
   — which puts the first path segment in the URL's authority position instead
   of the path, so AndroidManifest's intent-filter and `parseDeepLink()` both
   fail to route it. This is the SAME bug that was already fixed in
   `frontend/src/lib/deeplinks.ts`, and that frontend fix was inert: the
   backend is what populates the notification's `deepLink` field. Both sides
   now emit the 3-slash no-authority form (`clienthunter:///strategies/42`).
   **If you change one, change the other.**

### Test harness repairs
- `api_client` (integration) — it had never run. Imported `create_app` from
  `app.main` (does not exist; there is a module-level `app`) and `get_db` from
  `app.core.database` (it lives in `app/db/base.py`), and passed `app=` to
  `AsyncClient`, which httpx removed in 0.28. Now uses `ASGITransport`, and a
  **sync** override for `get_db`.
- `pg_engine` runs `alembic upgrade head` in a **subprocess**. Setting
  `sqlalchemy.url` on an in-process `AlembicConfig` does nothing:
  `alembic/env.py:20` overwrites it with `settings.database_url`, which was
  built at import from the root conftest's `DATABASE_URL="sqlite://"`. Every
  migration was being applied to SQLite and dying on the first ALTER of a
  named constraint.
- `pg_engine` also rebinds the app's own `SessionLocal` to the test engine via
  `sessionmaker.configure(bind=...)`. Overriding the `get_db` dependency only
  covers HTTP requests; eager Celery tasks, `circuit_breaker` and
  `notification_service` open sessions straight from `SessionLocal` and were
  hitting an empty in-memory SQLite DB. `configure()` mutates in place, so
  modules that already did `from app.db.base import SessionLocal` (lead_tasks
  does, at module level) pick it up — rebinding the attribute would not reach
  them.
- `tests/integration/mocks/*` used `respx.pattern.M`; the module is
  `respx.patterns`.
- Integration tests called `/api/v1/...` and `/auth/register`. Neither exists:
  the app mounts routers unprefixed and the route is `/auth/signup`. The
  **frontend** agrees with the app (`frontend/src/lib/api/auth.ts` calls
  `/auth/login`), so the tests were the outlier and were corrected.
- Assorted contract corrections, each verified against the app: strategy
  creation returns **202** (async pipeline kickoff), not 201; past-clients
  takes ONE post with all clients under a `clients` key; `GET /me/theme`
  wraps its payload as `{"theme": {...}}`; leads list returns
  `{total, limit, offset, items}`; webhook signature rejection is **401** in
  both handlers, not 403; `GET /products` does not exist (POST-only
  collection), so auth tests now probe `/auth/me`.
- `tests/test_devices.py` — added the `user_tokens` fixture (never existed),
  converted its `await db_session.execute(...)` to sync, and replaced
  `user_id=99999` with a real `User` row (the FK is a UUID). The five tests
  that perform the upsert now request a `pg_upsert` guard that skips on
  SQLite, because `devices.py` uses the PostgreSQL dialect's `ON CONFLICT`
  by design; device ownership is covered on real Postgres by
  `tests/integration/test_security.py`.

### Where the suites stand
- **Root** (`tests`, SQLite, no services): **265 passed, 12 failed, 5 skipped,
  0 errors.** Was 72 passed with 14 modules unable to import.
- **Integration** (`tests/integration`, real Postgres + Redis): **57 passed,
  44 failed.** Was completely unrunnable.
- **`test_security.py` specifically: 18/20 passed.** All five JWT-lifecycle
  tests and all five cross-tenant isolation tests (strategy, product, lead,
  campaign, theme) now pass. **The Phase A ownership fixes are, for the first
  time, actually verified.**

### What is still open
1. ~~`/debug/*` endpoints do not exist~~ — **DONE**, see "Session update 3"
   above. `app/api/debug.py`, gated on `settings.enable_debug_routes`, guard
   verified live by `scripts/verify_debug_router_guard.py`.
2. **`tests/test_regression.py`** (8) — import/interface assertions against
   `app.schemas`, `app.integrations.interfaces`, `app.services.playbook`, the
   `clienthunter` SDK, `ApolloLeadSource`, `HunterEmailVerifier`. Per-item
   call: renamed (fix the test) or genuinely missing (a real gap). These are
   architecture tripwires — do not bulk-delete them.
3. **`test_flows.py`** (19) — lead sourcing returns an empty list, so tests
   index `[0]` into nothing; `seed_outcomes` references `OutcomeEvent.sent`,
   which the model does not define.
4. **`tests/test_notifications.py`** (3) and
   `test_whatsapp_webhooks.py::test_verification_handshake` (1).
5. Phase C proper still needs a **real** `ANTHROPIC_API_KEY` — every run so
   far mocks Anthropic at the transport layer, so the actual
   strategy-generation pipeline remains unexercised.

## Session update 3 — 2026-08-19 (test-only debug router: DONE)

### The /debug router exists and is guarded

`app/api/debug.py` implements all 12 endpoints the suites call:
`send-email-raw`, `schedule-send`, `flush-scheduled-sends`, `deferred-sends`,
`set-send-cap`, `send-whatsapp-freeform`, `send-whatsapp-template`,
`whatsapp-status`, `simulate-bounce-rate`, `inject-verification-fail`,
`simulate-pipeline-crash`, `generate-unsubscribe-token`.

**All 12 drive real production code paths.** `flush-scheduled-sends` calls the
genuine `outreach_tasks.send_message_impl()`, so suppression, campaign-pause,
send-window and daily-cap rules are enforced by production code, not
reimplemented. `simulate-bounce-rate` writes real SENT messages and BOUNCED
outcomes and then calls `sequence_engine.check_bounce_rate()` — it never sets
`campaign_state` directly. The WhatsApp endpoints go through
`WhatsAppChannel.send()`'s real `_compliance_guard`.

### GUARD MECHANISM — read this before adding any debug route

Three independent layers, all must hold:

1. **`settings.enable_debug_routes`** (`app/config.py`) — a **dedicated**
   boolean, default `False`, that controls nothing else. Deliberately NOT
   `app_env` / `is_test` / `log_level`, so no unrelated configuration change
   can enable the router as a side effect. Env var: `ENABLE_DEBUG_ROUTES`.
2. **`app/main.py` imports the module INSIDE the conditional.** When the flag
   is off, `app.api.debug` is never imported, so its routes cannot exist on
   the ASGI app under any code path. The same conditional also refuses when
   `app_env` is production/prod/live.
3. **`app/api/debug.py` raises at import time** (`_refuse_in_production()`) if
   `app_env` is production — an operator who wrongly sets the flag in prod
   gets a loud startup failure, not a live debug surface.

The flag is set in exactly ONE place in the repo: `tests/conftest.py`. It
appears in no `.env*`, no `docker-compose*.yml` and no Dockerfile.

**Verified live, not by code review** —
`python scripts/verify_debug_router_guard.py` boots a real uvicorn per
scenario and issues real HTTP requests:

```
[1. PRODUCTION, flag unset]     APP_ENV='production' ENABLE_DEBUG_ROUTES='<unset>'
   PASS  GET  /debug/whatsapp-status?message_id=x  -> 404
   PASS  GET  /debug/deferred-sends                -> 404
   PASS  POST /debug/inject-verification-fail      -> 404
[2. PRODUCTION, ENABLE_DEBUG_ROUTES=true]
   PASS  (all three)                               -> 404
[3. DEVELOPMENT, flag unset]
   PASS  (all three)                               -> 404
[4. TEST, ENABLE_DEBUG_ROUTES=true]   <- CONTROL
   PASS  GET  /debug/whatsapp-status?message_id=x  -> 500
   PASS  GET  /debug/deferred-sends                -> 401
   PASS  POST /debug/inject-verification-fail      -> 401
RESULT: GUARD VERIFIED
```

Scenario 4 is the control and it matters: without it, four 404s could just as
easily mean a dead server. A 401/403 in scenarios 1-3 would be a FAILURE, not
a pass — it would mean the router mounted and merely rejected the caller.

### Production bugs found while wiring this up (all fixed)

4. **ComplianceError never reached its documented 422 under a test/ASGI
   transport.** `app/main.py` registered `global_exception_handler` only for
   bare `Exception`, which Starlette serves via `ServerErrorMiddleware` — it
   builds the response and then RE-RAISES, so httpx's ASGITransport and
   TestClient both saw an unhandled exception instead of a 422. The app's own
   exception types (`ComplianceError`, `ResourceNotFoundError`,
   `ForbiddenError`, `VectoraeError`) are now registered by concrete type
   so they go through `ExceptionMiddleware` and return real responses.
5. **`app/integrations/whatsapp.py` declares its OWN `ComplianceError`**
   instead of importing `app.core.exceptions.ComplianceError` — the eighth
   instance of this project's duplicate-implementation pattern. The global
   handler does not recognise it, so a WhatsApp compliance block surfaces as
   a **500 instead of 422** anywhere the adapter is reached through the API.
   `app/api/debug.py` translates it at the boundary; **the duplicate class
   itself is still there and should be collapsed.**

### Test-harness repairs made along the way
- `tests/integration/mocks/gmail_mock.py` never decoded the RFC-2822 payload.
  `GmailChannel.send()` transmits `{"raw": urlsafe_b64(mime)}`, so the
  recipient, subject and List-Unsubscribe header were invisible: any test
  filtering on `call["to"]` or looking for an address matched nothing and read
  as "no email was sent". It now decodes to `to` / `subject` / `headers` /
  `text`.
- `tests/integration/mocks/anthropic_mock.py` returned a generic
  `{"result": "ok"}` for message-personalization calls, so the send path died
  on `ValueError("personalization returned an empty body")` and no email ever
  reached the Gmail mock. It now answers personalization with subject+body.
- The integration mocks sign webhooks with `wh-app-secret-test`, but the ROOT
  conftest sets `WHATSAPP_APP_SECRET=test-app-secret` and, importing first,
  won — every signed integration webhook was rejected 401. A session fixture
  in `tests/integration/conftest.py` now aligns them.
- `test_idempotency.py` had a dead `headers = _auth_headers({})` line that
  raised `KeyError` on the empty dict before its own assertion could run; the
  value was never used (that request is deliberately unauthenticated).

### Results
- **Integration: 65 passed, 36 failed** (was 57/44).
- **Root: 265 passed, 12 failed, 5 skipped** — unchanged, no regression.
- Of the 12 tests that call a `/debug/` endpoint, **7 now pass**. Every one of
  the 12 debug endpoints itself works: in each of the 5 still-failing tests
  the `/debug/` call asserts its own status successfully and the test fails
  LATER, on a non-debug endpoint.

### The 5 still-failing debug-using tests are blocked by MISSING PRODUCTION ENDPOINTS

Not by the debug router. These routes do not exist in `app/api/`:

| Missing route | Blocks |
|---|---|
| `GET /strategies/{id}/campaigns` | bounce-rate auto-pause test |
| `POST /strategies/{id}/resume` | pipeline-resume idempotency test |
| `POST /leads/{id}/reply` | unsubscribe-reply compliance test |
| `GET /unsubscribe?token=` (app has `/unsubscribe/{token}`) | signed-token test |
| `POST /suppression` (app has `/admin/suppression-list`) | STOP-message test |
| `GET /strategies/{id}/leads` returns `{items:[...]}`, tests index `[0]` | several |

Deciding whether to build those endpoints or correct the tests is the next
open question — it is the same "tests written against an API surface that
never shipped" pattern as the `/api/v1` prefix.

## Session update 4 — 2026-08-19 (both suites, and what they were hiding)

Run the suites exactly as documented in session update 2. Nothing new is
required beyond `docker compose up -d db redis`.

### Where the suites stand

| Suite | Before this session | Now |
|---|---|---|
| Root (`tests`, SQLite, no services) | 265 passed, 12 failed, 5 skipped | **277 passed, 5 skipped, 0 failed** |
| Integration (`tests/integration`, PG + Redis) | 65 passed, 36 failed | **102 passed, 1 xfailed, 0 failed** |

`tests/test_regression.py` is 16/16 and the `regression` marker is registered
in `pyproject.toml`.

### The headline: a test that was green while verifying nothing

`test_campaign_cross_tenant_isolation` requested
`GET /strategies/{id}/campaigns` — **plural**, a path that matches no route.
It collected the router's 404, asserted "403 or 404", and passed. It had been
counted as one of the passing cross-tenant tests since the Phase A audit.

The endpoint it was supposed to be covering,
`GET /strategies/{strategy_id}/campaign`, **had no authentication dependency
at all** — not owner-scoping, no `get_current_user`, nothing. Anyone who knew
or guessed a strategy UUID could read that account's lead counts by status,
send volume against its daily cap, reply/bounce/booking rates and per-channel
breakdown, unauthenticated. Verified live before the fix: User B got HTTP 200
on User A's campaign overview.

Fixed in `app/api/strategies.py` (now uses the file's new `_owned_strategy`
helper, 404 on a foreign id). The test now hits the real path and also asserts
an anonymous request is refused.

**Sweep done, and worth repeating after any router change:** every route in
`app/main.py` was enumerated and checked for an auth dependency. Fifteen have
none; fourteen are correct (`/health`, `/`, `/plans`, `/auth/*`, the OAuth
callback, the signed-token `/unsubscribe/{token}` and `/optin/whatsapp/{token}`
pages, and the signature-verified webhooks). `/strategies/{id}/campaign` was
the only real hole.

A generalized version of the false-green check now exists as a one-off script
pattern: match every `api_client.<verb>("...")` in the suite against the live
route table. It found **22 requests to 9 paths that have never existed**. All
are now either pointed at the real surface or backed by a new endpoint (below);
the count is zero.

### Production bugs found and fixed (continuing the numbering)

6. **`GET /strategies/{id}/campaign` had no auth.** Above.

7. **Inbound email replies were matched across ALL tenants.**
   `outreach_tasks.route_inbound_impl()` resolved a reply with
   `select(Lead).where(Lead.email == from_address)` — no tenancy filter — and
   took the first row in the table. Two customers prospecting the same person
   is routine in B2B, so a reply arriving in one account's Gmail could flip
   another customer's lead to REPLIED, stop their sequences, write a REPLIED
   outcome into their learning loop, and — on an "unsubscribe_request" —
   suppress their lead. Both the thread and address lookups are now scoped to
   the leads owned by the account that received the message. Covered by
   `test_inbound_reply_never_routes_to_another_users_lead`, which was confirmed
   to fail against the old code before the fix was kept.

8. **The entire M8 learning loop was inert: nothing ever wrote
   `Strategy.pattern_key`.** The column documents itself as "SHA-256 of sorted
   canonical ICP/tactic JSON — links a strategy to its playbook_scores rows",
   and every consumer filters on it:
   `score_decay.compute_scores_with_decay()` joins
   `WHERE s.pattern_key IS NOT NULL` (so it always returned `{}` and
   `playbook_scores` was never written), `auto_promote_winners()` had no scores
   to read (so `default_variant` was never set and no A/B test ever concluded),
   and `PlaybookService.get_insights()` joins on it (so the Phase 6/8 playbook
   injection was permanently the "no data yet" block). The product's headline
   self-learning feature did nothing.
   `icp_extraction.compute_pattern_key()` / `ensure_pattern_key()` now derive
   it, called from `run_pipeline` once the Phase 2/3 research exists and again
   (idempotently) at lead sourcing.
   **Review this if you disagree with the canonical form** — it hashes the five
   `CRITERIA_KEYS` lists, each lower-cased, de-duplicated and sorted. It is
   deliberately in one function so changing it plus a backfill is easy.

9. **Reply rate was pinned at 1.0, so no A/B test could ever separate a
   winner.** `compute_scores_with_decay()` decided whether a send had been
   replied to by asking "is there *any* reply within 10 days of it?" — not
   attribution at all. One reply inside the window marked every send in that
   window as replied, so 50 sends with 8 replies scored 0.16 as **1.0**. Every
   variant scored ~1.0, so promotion could never find a difference. Now
   attributed per lead (`outcomes.lead_id`, which `ix_outcomes_lead_event`
   exists to serve).

10. **Every send-time-optimized send was scheduled one day late.**
    `send_time_optimizer.pick_next_send_datetime()` compared
    `candidate.weekday()` (Python: Monday=0) against a `day_of_week` that comes
    from `EXTRACT(DOW ...)` (Postgres: Sunday=0). The `% 7` in the code was a
    no-op on 0..6 and left both conventions mixed. A Monday slot fired Tuesday.

11. **Webhook delivery made 6 attempts, not the documented 5.**
    `mark_failed_attempt()` used `attempt >= len(RETRY_DELAYS_SECONDS)` where
    `attempt` is the count of attempts *already made*, so the fifth failure
    reported "retries remain", scheduled a sixth run reusing the 2h delay, and
    only then marked the row exhausted.

12. **Meta's WhatsApp webhook verification could never succeed in Docker.**
    `app/config.py` reads `WHATSAPP_VERIFY_TOKEN` (a comment there records this
    exact bug being fixed once already, in the Settings field). But every place
    that *sets* the variable still used the dead name
    `WHATSAPP_WEBHOOK_VERIFY_TOKEN`: `docker-compose.yml`, the `.env` template
    `clienthunter serve` writes, `docs/DEPLOY.md` (twice) and
    `tests/conftest.py`. The token was therefore empty everywhere except a
    hand-written `.env`, and the GET handshake always refused. All setters
    fixed; a repo-wide grep for the dead name now returns only the explanatory
    comment.

13. **`AB_HARM_CEILING` in `.env` never had any effect.**
    `ab_testing.py` reads it with `getattr(settings, "AB_HARM_CEILING", 0.05)`,
    but no Settings class declared the field — and both use `extra="ignore"`,
    so the env var was dropped and the gate was hard-pinned at 0.05. Declared
    in `app/core/config.py`; verified that `AB_HARM_CEILING=0.017` now reaches
    the app.

14. **A missing push-notification configuration looked like a flaky network.**
    `notifications.send_to_device()` wrapped `_get_firebase_app()` in the same
    `except Exception` that absorbs transient FCM errors, so an unset
    `FIREBASE_CREDENTIALS_PATH` produced a per-token WARNING indistinguishable
    from noise. Configuration errors (`RuntimeError`, `ImportError`) now
    propagate out of `send_to_device`; `send_to_user` reports them once at
    ERROR and returns, preserving the rule that a push failure never breaks the
    operation that triggered it.

### Endpoints added — and the ones deliberately NOT added

The suite called nine paths that do not exist. The rule applied was: build it
only where a real client needs it or a real capability has no trigger;
otherwise the test was written against an API surface that never shipped and
the test is what changes.

**Built:**
- `POST /strategies/{id}/resume` — the engine has always been resumable (it
  asks for the first step with no persisted row), but **nothing could trigger
  a resume**: Celery's retry gives up after `max_retries`, and
  `detect_stale_pipeline_steps` is deliberately detection-only. A strategy
  whose worker died stayed stuck in `researching` forever. 409s on
  verified/needs-review (nothing to resume) and on executing (would reset
  status under a live campaign).
- `GET /strategies/{strategy_id}/leads/{lead_id}` — the **published SDK** calls
  this (`ch.leads.get(strategy_id, lead_id)`); the route did not exist, so that
  method always raised `NotFoundError`. Ownership is checked on both ids.
- `POST /debug/inbound-reply` (test-only router) — email replies have no HTTP
  entry point in production; they arrive on a Celery beat poll of the Gmail
  API. This feeds the same `InboundMessage` into the real
  `route_inbound_impl`, so classification, the unified stop rules and the
  REPLIED outcome write are production code.

**Not built** (tests corrected instead): `/strategies/{id}/steps`,
`/leads/{id}/outcomes`, `/strategies/{id}/outcomes`, `/leads/{id}/tombstone`,
`/leads/{id}/reply`, `/whatsapp/opt-in`, `/whatsapp/opt-out-audit`,
`GET /devices`, `GET /unsubscribe?token=`. Research steps and outcomes are
internal invariants with no client (the UI renders progress from
`GET /strategies/{id}`), the tombstone *is* the surviving lead row, and the
rest already exist under different paths. `tests/integration/conftest.py` grew
`research_steps` / `lead_outcome_events` / `strategy_outcomes` /
`enrollment_statuses` / `strategy_row` / `set_plan` for the direct-DB
assertions.

### Test-harness repairs (these were hiding the bugs above)

- **The integration suite had no database isolation.** `db_session` opened a
  savepoint and rolled it back, which isolates nothing: the app never uses that
  session (HTTP goes through the `get_db` override; eager Celery tasks, the
  circuit breaker and `notification_service` open their own from
  `SessionLocal`), and the tests commit too. State accumulated across the whole
  run, so tests only passed in a particular order — an earlier GDPR-delete or
  suppression test put `alice@`/`carol@` on the global suppression list, after
  which sourcing correctly refused to insert them and later tests failed with
  `KeyError` on leads they had just sourced. Every table is now truncated
  before each test.
- **The app's cache and circuit breaker were pointed at the wrong Redis.**
  `tests/integration/conftest.py` sets `REDIS_URL`, but the root conftest is
  imported first and pulls in `app.config`, freezing `settings.redis_url` at
  `redis://localhost:6379/0` — not the compose Redis, which is on **6380**. The
  response cache therefore lived in some unrelated service's Redis that
  `flushdb` never cleared, and a cached Apollo search satisfied later tests
  with no HTTP call at all. `_retarget_redis()` now rebinds both Settings
  objects and clears the cached clients.
- **The Anthropic mock answered the wrong prompt.** It regexed the combined
  text for `step_no: N` / `pass N`. The pipeline's prompt template contains no
  step number — only `CURRENT PHASE: <title>` — so the number it found came out
  of the injected context block, where prior steps' JSON outputs carry their
  own `step_no`: step 46 was answered with Phase 1's output. And the judge
  prompt says `CRITERION #n`, never `pass n`, so **all 10 verification passes
  failed 3 attempts each on every strategy** and nothing ever reached VERIFIED
  — which is why every lead-sourcing test 409'd. The mock now dispatches on the
  caller's system prompt and resolves phase by matching `CURRENT PHASE:`
  against the real registry, so mock and pipeline cannot drift silently. It
  also gained the pattern-recognition, ICP-extraction and reply-classifier
  branches it never had.
- **Apollo was mocked at the wrong URL** (`/v1` vs the adapter's `/api/v1`), so
  sourcing raised `AllMockedAssertionError` and the Celery task retried.
- **Calendly was mocked with the wrong signature scheme and payload shape.**
  `sign_payload` emitted Stripe/Meta-style `sha256=<hmac(body)>`; the app
  implements Calendly's real `t=<unix>,v1=<hmac(t.body)>` and rejected all of
  them (401). The booking payload also nested the invitee email under
  `payload.invitee.email`, while Calendly v2 — and `webhooks.py` — put it flat
  on `payload`, so accepted deliveries matched no lead and returned
  `{"matched": false}` with a 200.
- **Signed webhooks were re-serialized after signing.** Both handlers verify
  against `await request.body()`; the tests passed `json=payload` and let httpx
  re-serialize with different separators. Both mocks now expose
  `signed_request(payload)` returning the exact signed bytes.
- **Nothing in the suite was actually an admin.** `/auth/signup` takes
  `Credentials` — email and password — so the `is_admin: True` key several
  tests (and the `admin_tokens` fixture) sent was silently dropped. That is
  correct behaviour; admins are made out of band by
  `python -m app.cli.create_admin`. The fixture now promotes the column
  directly and the inline signup blocks were replaced with it.
- Debug-router preconditions were made faithful: `simulate-pipeline-crash` now
  leaves the strategy in RESEARCHING (what `run_pipeline`'s except branch
  writes) rather than VERIFIED, and `inject-verification-fail` sets
  NEEDS_HUMAN_REVIEW — a FAIL entry alone is not the precondition, since the
  loop logs a FAIL for every failed attempt and still ends VERIFIED when a
  later attempt passes.
- A/B seed data was too weak to be significant: promotion compares booking
  rates through a two-proportion z-test at alpha=0.05, and 2/50 vs 6/50 gives
  p=0.14 — correctly refused. Seeds are now 4/200 vs 30/200.
- `tests/test_notifications.py` leaked mock state: `reset_mock()` does not
  clear `side_effect`, so a `ConnectionError` set by one test broke the next
  two.

### Still open

1. ~~The M7 notification event layer is dead code~~ — **DONE**, see session
   update 5. All six helpers are wired to real events with per-call-site tests,
   the duplicate token-lookup was collapsed, and `firebase_mock` now intercepts
   at the transport firebase_admin actually uses. The `xfail(strict=True)`
   marker is gone.
2. ~~Calendly bookings match leads across tenants~~ — **DONE**, see session
   update 6. The booking link carries the tenant id in `utm_content` and the
   webhook resolves on it, quarantining anything untagged.
3. **`ADMIN_EMAIL` in `app/core/config.py` is read by nothing.** Either wire it
   to auto-promote that address at signup or drop it; right now
   `app/cli/create_admin.py` is the only way to make an admin, which is worth
   knowing before the first production deploy.
4. **`app/integrations/whatsapp.py` still declares its own `ComplianceError`**
   instead of importing `app.core.exceptions.ComplianceError` (carried over
   from session update 3). The global handler does not recognise it, so a
   WhatsApp compliance block surfaces as 500 instead of 422 anywhere the
   adapter is reached through the API other than `/debug`.
5. **Phase C still needs a real `ANTHROPIC_API_KEY`.** Every run so far mocks
   Anthropic at the transport layer. The pipeline, the 10 verification passes
   and personalization have never run against the real model.
6. Phase D as previously written (hosting choice, domain, prod secrets).

### Notes for whoever picks this up

- If you add a route, re-run the two audits in this section: the auth-dependency
  sweep over `app.routes`, and the test-request-vs-route-table check. Both
  found real bugs that code review had missed twice.
- The recurring pattern is now at ten instances: two Settings classes, two
  crypto modules, two `/strategies` GET routes, two token stores, two
  ThemeProviders, two conftests, a duplicate `ComplianceError`, two
  notification layers (one dead), a mock URL that disagreed with its adapter,
  and an env var whose readers and writers disagreed. **Before declaring any
  area done, grep for a second implementation of the same concern — and check
  that the writers and readers of every setting agree on the name.**

## Session update 5 — 2026-08-19 (M7 notifications: DONE)

### Where the suites stand

| Suite | Before | Now |
|---|---|---|
| Root (`tests`, SQLite, no services) | 277 passed, 5 skipped | **277 passed, 5 skipped** |
| Integration (PG + Redis) | 102 passed, 1 **xfailed** | **112 passed, 0 xfailed** |

The `xfail(strict=True)` placeholder from session update 4 is gone — the test
it guarded now passes for real, and nine more cover the rest of the wiring.
`scripts/verify_debug_router_guard.py` re-run: GUARD VERIFIED.

### M7 is wired. All six helpers had zero callers.

`notify_strategy_ready`, `notify_strategy_needs_review`, `notify_new_reply`,
`notify_meeting_booked`, `notify_campaign_paused` and
`notify_whatsapp_template_status` are now called from the real events, each
covered by a test that drives the production path rather than the helper:

| Helper | Trigger point | Test |
|---|---|---|
| `notify_strategy_ready` | `verification/loop.py` — loop ends VERIFIED | `test_notifications_wiring.py::TestStrategyNotifications::test_pipeline_completion_notifies_owner_only` |
| `notify_strategy_needs_review` | same line — ends NEEDS_HUMAN_REVIEW | `…::test_exhausted_verification_notifies_needs_review` |
| `notify_new_reply` (email) | `outreach_tasks.route_inbound_impl` | `…::TestReplyNotifications::test_email_reply_notifies_lead_owner_only` |
| `notify_new_reply` (WhatsApp) | `outreach_tasks.route_whatsapp_inbound_impl` | `…::test_whatsapp_reply_notifies_lead_owner` |
| `notify_meeting_booked` | `webhooks.calendly_webhook` invitee.created | `test_flows.py::TestFlow1WithClients::test_booking_sends_push_notification` |
| `notify_campaign_paused` | `sequence_engine.check_bounce_rate` on auto-pause | `…::TestCampaignPausedNotification::test_bounce_rate_pause_notifies_owner` |
| `notify_whatsapp_template_status` | `whatsapp_templates.apply_meta_status` | `…::TestTemplateStatusNotification::test_meta_approval_notifies_admins_not_users` + `test_pending_status_sends_nothing` |

Every one of those tests asserts the recipient **token**, the title and the
deep link, and the owner-scoped ones also assert a second user registered on
the same install received nothing.

### Instance #10 of "two parallel implementations, the wrong one active"

Flagging this explicitly, as asked. It is not merely that M7's helpers had no
callers — **they could not have run if they had.**

`notifications.send_to_user()` annotated its session as `AsyncSession` and did
`await db.execute(...)`. `get_db` yields a SYNC `Session` and `SessionLocal` is
sync, so the first call would have raised
`TypeError: object ChunkedIteratorResult can't be used in 'await' expression`
— the exact defect that made the whole `/devices` router dead (session update
2, bug 1). Meanwhile `NotificationService._tokens_for_user_ids()` ran the *same
device-token query* synchronously, and that is the one every live caller
(circuit breaker, task monitoring, webhook delivery) used. Two token lookups;
the reachable one was the copy.

Collapsed rather than worked around:
- `notifications.valid_tokens_for_users(session, user_ids)` is now the single
  canonical query, and `NotificationService._tokens_for_user_ids` delegates to
  it.
- `send_to_user` / `_mark_token_invalid` use the app's sync `Session`; the
  `AsyncSession` annotations are gone from the module.
- `send_to_user(db=None)` now opens a short-lived `SessionLocal` instead of
  logging a warning and returning 0 — that old behaviour meant any caller
  without a session handy silently sent nothing.
- `tests/test_notifications.py` had encoded the unrunnable async contract
  (`db.execute = AsyncMock()`); its four `send_to_user` tests now assert the
  sync one.

### One dispatch helper, not six try/excepts

`notifications.dispatch(coro)` is the only thing the call sites use. It exists
because the established convention —
`try: asyncio.run(NotificationService.send_*(...)) except: log.warning` from
`core/circuit_breaker.py` and `workers/webhook_tasks.py` — **breaks in two of
the six trigger points**: the Calendly and WhatsApp webhook handlers are
`async def` (while still holding a sync Session), and bare `asyncio.run()`
raises `RuntimeError` inside a running loop. Copying that line into them would
have caught the RuntimeError and silently dropped every notification.

`dispatch()` runs the coroutine directly when there is no loop, and on a
worker thread (waiting for the result, so ordering is unchanged) when there is.
It never raises.

**Rule that goes with it:** call sites resolve the recipient on their own
thread with their own session, then pass only ids — `db=None`. A SQLAlchemy
Session is not thread-safe, and in the running-loop branch the coroutine
executes on a different thread. `send_to_user` opens its own session, which is
how `NotificationService` has always worked.

Recipient resolution is centralised in `notifications.owner_of_strategy()` and
`owner_of_lead()` (Strategy → Product.user_id; Lead → Strategy → Product
.user_id), returning None rather than guessing when the chain is broken. Given
this project has shipped two cross-tenant bugs from ad-hoc lookups, no call
site does its own.

### Firebase mock: fixed at the transport the SDK actually uses

**Verified, not assumed.** `messaging.send()` resolves to
`_MessagingService._client` = `_http_client.JsonHttpClient` → `HttpClient
.__init__` → `google.auth.transport.requests.AuthorizedSession` — i.e.
**requests**. firebase-admin 7.x does ship an httpx client, but only
`HttpxAsyncClient`, used by `send_each_async()`. respx patches httpx only, so
the old respx routes on `fcm.googleapis.com` could never have fired.

**Approach taken: (a) mock at the boundary the app calls — not (b).** Option
(b), moving production onto `send_each_async` so respx could see the traffic,
was rejected: it is a different API (async, batched, `dry_run=True` by
default), so it would change real sending behaviour to suit a test. That is
also the pattern the root suite already uses for Firebase, rather than a new
one.

`tests/integration/mocks/firebase_mock.py` now:
- replaces `firebase_admin.messaging.send` with a recorder, so
  `messaging.Message` / `Notification` / `AndroidConfig` are still built and
  validated by the real SDK (a malformed payload still fails the test — the
  suite currently surfaces a genuine `DeprecationWarning: Message.token is
  deprecated, use Message.fid instead` from firebase-admin 7.5.0, see open
  items);
- stubs `notifications._get_firebase_app`, since all that remains below the
  recorder is credential parsing — `credentials.Certificate()` wants a real
  service-account JSON with a real RSA key and google-auth would mint an OAuth
  token over the network. That is Google's code, not this application's.
- exposes `get_all_notifications()` and `notifications_for_tokens(tokens)`
  returning `SentNotification(token, title, body, data)` with a `.deep_link`
  property.

It is installed by an autouse `capture_push_notifications` fixture, so every
integration test observes pushes without opting in.

### A push failure can never break the operation that triggered it

Asserted, not assumed — two tests in
`TestNotificationFailureIsolation`:
- `messaging.send` raising `RuntimeError` → the Calendly booking is still
  recorded (lead `meeting_booked`, `booked` outcome written), handler returns
  200;
- `_get_firebase_app` raising (an unconfigured deployment) → same. This matters
  because `send_to_device` deliberately *raises* on configuration errors rather
  than swallowing them (session update 4, bug 14); `dispatch` is what contains
  that.

### Also fixed along the way

- `admin_tokens` returned only the token pair while `user_a_tokens` /
  `user_b_tokens` return the whole login body, so any helper resolving
  `tokens["user"]["id"]` (`set_plan`, the new `register_device_token`) raised
  `KeyError` on an admin. It now returns the full body like the others.

### Notes / open items from this work

1. **`Message.token` is deprecated in firebase-admin 7.5.0** ("use
   `Message.fid`"). An FCM registration token and a Firebase installation ID
   are not the same identifier, and changing it affects what the mobile client
   must register, so it was left alone. Worth resolving before a real mobile
   release — it is now visible as a warning in every notification test.
2. **`notify_whatsapp_template_status` goes to admins, not a user.**
   `whatsapp_templates` has **no owner column**: one Meta WABA per install
   makes templates a deployment-wide shared resource (which is why that API is
   login-required but deliberately not owner-scoped). There is no single user
   to notify, and pushing to every user would leak the template's existence to
   accounts that never touched it. Admins are the people who submit and fix
   templates. Reverse this if templates ever become per-user — it is one loop
   in `apply_meta_status`.
3. Only terminal Meta decisions notify (APPROVED / REJECTED); PENDING is
   deliberately silent, covered by `test_pending_status_sends_nothing`.
4. Untouched on purpose, per the scope boundary: the Calendly cross-tenant
   matching question and the `pattern_key` derivation question, both still open
   in session update 4.

## Session update 6 — 2026-08-19 (Calendly tenancy + pattern_key tactics: DONE)

### Where the suites stand

| Suite | Before | Now |
|---|---|---|
| Root (`tests`, SQLite, no services) | 277 passed, 5 skipped | **290 passed, 5 skipped** |
| Integration (PG + Redis) | 112 passed | **115 passed, 0 failed** |

Three decisions were closed. WhatsApp template notifications stay on admins
(confirmed correct — `whatsapp_templates` has no owner column).

---

### 1. Calendly cross-tenant matching — FIXED

**Mechanism: the booking link carries the tenant, and the webhook reads it
back.** Checked first for an existing mechanism, as instructed: there was none
in the app. The only thing that existed was a convention in the *test mock*
(`tracking.utm_source` carrying a lead id) that no production code ever read,
so this extends that same mechanism rather than inventing a second one.

**FIELD NAME — this is the new integration detail future work needs:**

```
app/integrations/calendly.py:  TENANT_TRACKING_PARAM = "utm_content"
outgoing:  <booking_url>?...&utm_content=<user_id>
incoming:  payload["payload"]["tracking"]["utm_content"]
```

`utm_content`, not `utm_source`, deliberately: `Sequence.booking_url` is a URL
the **customer pastes in themselves** and may already carry their own
`utm_source` / `utm_campaign` for their own attribution. Overwriting it to
smuggle an internal id would silently corrupt their reporting. `utm_content` is
the least likely of the standard set to be in use, and Calendly forwards the
whole UTM set into `payload.tracking`. Anything already on the customer's URL
is preserved — `tag_booking_url()` merges rather than replaces, and is
idempotent.

This needs no Calendly plan upgrade (per-tenant webhook subscriptions require
Enterprise) and does not depend on email matching.

**Where links get tagged.** `outreach_tasks._render_email_outbound` — verified
to be the *only* path that puts a booking link in front of a lead. The WhatsApp
text path passes `booking_url=None` and WhatsApp templates carry no scheduling
link, so there is one choke point, not several.

**Missing / malformed tenant id → QUARANTINE, never a guess.**
`tenant_id_from_payload()` returns None for an absent tracking block, an absent
parameter, or a non-UUID value. The handler then logs a warning naming the
invitee and event id, returns `{"ok": true, "matched": false,
"quarantined": true}` — a 200, so Calendly stops retrying — and matches
nothing. Falling back to the old global `Lead.email ==` lookup would be exactly
the bug being fixed.

**Tests** (`test_security.py::TestCrossTenantIsolation`, mirroring the existing
strategy/product/lead/campaign pattern):
- `test_calendly_booking_never_routes_to_another_users_lead` — two tenants hold
  a lead with the *same* email; a booking tagged for B books B's lead and
  leaves A's untouched. **Verified to fail against the old code** before the
  fix was kept (A's lead was the one that got booked).
- `test_calendly_booking_without_tenant_id_is_quarantined` — an untagged
  booking matches nothing and the lead stays VERIFIED.

**MIGRATION CONSIDERATION FOR PHASE D:** any booking link already in a
prospect's inbox from before this change carries no `utm_content`, so a
booking made through it hits the quarantine path — acknowledged, logged, not
matched, and it will need to be reconciled by hand. There is no deployed
environment yet, so today this is theoretical; if that changes before launch,
grep the logs for "carried no usable tenant id". New links from any send after
this change are tagged automatically; nothing needs regenerating in the DB
because the tag is applied at render time, not stored.

---

### 2. pattern_key now hashes ICP **and** tactics

The column always documented "SHA-256 of sorted canonical ICP/tactic JSON";
the derivation was ICP-only, so two strategies aimed at the same buyer through
completely different channels and cadences shared one playbook bucket and had
their outcomes averaged together.

**The constraint that shaped this: cardinality.** The point of `pattern_key` is
aggregating outcomes ACROSS strategies, so a key that is unique per strategy
buckets nothing and learns nothing. That rules out hashing the Phase 5/8
research directly — those steps are prompted for "clear markdown, 300-600
words", so every strategy's tactic prose is unique and every strategy would
become its own bucket. The tactic half is therefore a small **closed
vocabulary**, extracted once by `extract_tactic_profile()` and filtered to the
allowed values, so an unexpected model answer degrades to "unknown" (dropped)
instead of minting a new bucket:

```
TACTIC_CHANNELS = email | whatsapp | linkedin | phone | referral | inbound
TACTIC_MOTIONS  = cold_outbound | warm_intro | inbound_led | product_led | partner_led
TACTIC_CADENCES = light | standard | aggressive
plus flow_type  = with_clients | no_clients
```

`flow_type` participates because a Flow 1 strategy (anchored on proven
past-client patterns) and a Flow 2 strategy are not the same play even against
an identical ICP.

The hashed structure is now:
`{"icp": {…5 CRITERIA_KEYS…}, "tactics": {channels, sales_motion, cadence,
flow_type}}`, every value lower-cased, stripped, de-duplicated and sorted.

**Cost:** one extra Anthropic call per strategy, once, at pipeline end.
`ensure_pattern_key` short-circuits on an already-set key *before* classifying,
so the lead-sourcing path adds no call.

**Tripwire test:** `tests/test_pattern_key.py` (13 tests, `regression` marker)
pins the exact canonical payload dict AND the serialization
(`sort_keys=True, separators=(",", ":")`, sha256), so a future field addition
or reordering fails immediately instead of silently re-bucketing every score.
It also asserts each tactic dimension actually changes the key, and that the
vocabularies stay small and lowercase.

**Nothing depended on the old value.** Audited every `pattern_key` reference in
`app/`, `sdk/` and `alembic/`: every consumer (`playbook.py`,
`playbook_service.py`, `score_decay.py`, `learning_tasks.py`, `admin.py`,
`admin_service.py`) treats it as an opaque join key read fresh from
`strategies`. No literal comparison anywhere, and no Redis key is derived from
it. The only thing keyed on it is stored `playbook_scores` rows.

**Backfill:** `scripts/backfill_pattern_key.py` — dry run by default,
`--apply` to write, `--delete-orphans` to drop `playbook_scores` rows no
strategy points at any more. Orphans are *reported* by default rather than
deleted, and they are harmless either way: `get_insights()` filters on live
strategy keys so it stops seeing them, and the nightly aggregation rebuilds
scores under the new keys from `outcomes`, which is the real source of truth.

Result of running it against the dev database
(`postgresql://…@localhost:5433/leadpilot`):

```
total 4 | changed 0 | unchanged 0 | skipped 4 | orphaned_scores 0
```

Reported honestly: **nothing was actually backfilled.** All four dev strategies
are stuck in `researching` and have `pattern_key = NULL` (they predate the
pattern_key work entirely), and recomputation was skipped on all four because
it needs a real `ANTHROPIC_API_KEY`. `clienthunter_test` has no schema between
runs. So there is no stale data anywhere that needs migrating — but the dev run
only proves the script executes cleanly and degrades per-row without aborting.

The backfill *logic* is proven against real data instead, by
`test_notifications_wiring.py::TestPatternKeyBackfill`: it plants a stale key
and its orphaned score row, then asserts the dry run writes nothing, the apply
reproduces exactly the key the live pipeline computes, orphans are reported but
not deleted without the flag, and a re-run is a no-op.

**A gap worth knowing:** `pattern_key`'s inputs are not persisted anywhere —
they are re-derived from research steps by the model — so a backfill costs two
Anthropic calls per strategy and cannot run offline. Storing the criteria and
tactic profile on the strategy row would remove that; not done here because it
is a schema change beyond this task.

---

### Also updated

- `tests/integration/mocks/calendly_mock.py::build_booking_webhook` takes
  `tenant_id` and imports `TENANT_TRACKING_PARAM` from the app, so mock and
  handler cannot drift. `utm_source` now carries "clienthunter" (an honest
  source) instead of an internal id.
- `tests/test_calendly.py` and `tests/test_whatsapp_cross_channel.py` tag their
  payloads with the lead's owner; the untagged ones remain, exercising the
  quarantine path.
- The Anthropic mock gained a `go-to-market classifier` branch for the tactic
  extraction.

## Session update 7 — 2026-08-19 (the five small items: DONE)

### Where the suites stand

| Suite | Before | Now |
|---|---|---|
| Root (`tests`, SQLite, no services) | 290 passed, 5 skipped | **308 passed, 5 skipped** |
| Integration (PG + Redis) | 115 passed | **116 passed, 0 failed** |

---

### 1. `Message.token` deprecation — INVESTIGATED, deliberately NOT changed

It is a **soft deprecation: a `DeprecationWarning` only**. `token` is still
accepted, still stored and still sent; nothing is being removed or renamed, and
there is no naming collision with this project's other token concepts (JWT
access/refresh, unsubscribe tokens, WhatsApp opt-in tokens).

Crucially it is **not a rename**. `_messaging_encoder.MessageEncoder` emits
`fid` and `token` as two DISTINCT wire fields and rejects a message carrying
both:

* `token` = an FCM **registration token** — what the mobile client gets from
  `getToken()` and POSTs to `/devices/register`
  (`frontend/src/hooks/usePushNotifications.ts`), stored in `DeviceToken.token`.
  That is exactly what we hold and pass.
* `fid` = a Firebase **installation ID**, from the Installations API. A
  different identifier entirely.

Putting a registration token in `fid` would simply fail to deliver. Adopting
`fid` would require the mobile client to register installation IDs instead — a
client-side change, not a backend one. The finding is recorded as a comment at
the call site in `app/services/notifications.py` so nobody "fixes" it later by
swapping the keyword. **Action: none, by design.**

---

### 2. PWA icons — placeholders generated, manifest valid

There is **no brand asset anywhere in the repo** (a search for
`*.png/*.svg/*.ico/*.jpg/*.webp` outside `node_modules` returns nothing), so
this took option (b).

`scripts/generate_placeholder_icons.py` writes three icons into
`frontend/public/icons/` and rewrites the manifest's `icons` array:

| file | size | purpose |
|---|---|---|
| `icon-192.png` | 192×192 | any |
| `icon-512.png` | 512×512 | any |
| `icon-maskable-512.png` | 512×512 | maskable |

They are a solid `theme_color` (#0f172a) square with the initials "CH" in
`background_color` (#ffffff), so they match the splash screen. The maskable one
insets the mark to the middle 80% so it survives Android's circular crop.

Verified after generation: every file exists, is a real PNG, and its actual
pixel dimensions match what the manifest declares; the manifest has 192 and 512
icons, a maskable icon, legal `purpose` values, name/short_name/start_url and a
standalone display — i.e. it now meets the installability criteria it failed
before.

**These are placeholders.** Real brand artwork is still needed before a store
or PWA launch; re-running the script regenerates them and would overwrite
anything hand-made.

---

### 3. Unpersisted `pattern_key` inputs — CONFIRMED, and fixed by persisting them

Confirmed the reading: only the 64-character hash was stored. The ICP criteria
and tactic profile that produced it were derived during the pipeline run and
then discarded. Three consequences:

1. **The bucket could not be audited.** Two strategies either share a playbook
   bucket or they do not, and there was no way to see why.
2. **The backfill had to re-derive**, at two Anthropic calls per strategy, so
   it needed a live API key and could not run offline.
3. **Worst: re-derivation is not deterministic.** The model can answer the ICP
   and tactic prompts differently on a later run or a later model version, so a
   "recompute" could produce a different key for an unchanged strategy and
   silently move it to a new bucket, orphaning its `playbook_scores` for
   reasons that had nothing to do with the strategy changing.

**Fix:** migration `0014_strategy_pattern_inputs` adds
`strategies.pattern_inputs_json` (JSON, nullable), and `ensure_pattern_key`
stores the exact canonical dict it hashed. A new `pattern_key_of(payload)` is
now the single place the hash is taken, so a stored payload and its key cannot
disagree. `scripts/backfill_pattern_key.py` rehashes offline from the stored
payload (`rehashed` counter) and only falls back to model re-derivation for
pre-0014 rows (`rederived` counter), backfilling the payload as it goes so it
happens at most once per row.

Migration applied cleanly to a real database (dev DB is now at head
`0014_strategy_pattern_inputs`; the column reads back as `json`).

Round-trip test — `TestPatternKeyBackfill::test_pattern_inputs_round_trip`:
persist → reload from Postgres → rehash → matches the stored `pattern_key`; the
stored payload is already canonical (re-canonicalizing it is a no-op, so an
audit can trust it as the literal hash input); and the backfill reports
`rehashed >= 1, rederived == 0`, proving it needs no model call.

---

### 4. Duplicate `ComplianceError` — REMOVED (instance #8 closed)

`app/integrations/whatsapp.py` declared its own `class
ComplianceError(Exception)`. `app/main.py` registers the global handler against
`app.core.exceptions.ComplianceError` **by concrete type**, so the look-alike
was never matched: a WhatsApp compliance block fell through to the bare
`Exception` handler and surfaced as **500 instead of 422**.

* The duplicate class is gone; the module imports the canonical one.
* Every raise site now carries `rule` and `compliance_code`
  (`SUPPRESSED`, `WHATSAPP_NO_OPTIN`, `WHATSAPP_TEMPLATE_NOT_APPROVED`,
  `WHATSAPP_WINDOW_CLOSED`, `WHATSAPP_BLOCKED`).
* The debug router's boundary workaround is deleted — both the
  `except AdapterComplianceError → re-raise` block and
  `_whatsapp_compliance_code()`, which reverse-engineered the code by
  string-matching the refusal prose. The codes now come from the raise sites.
* `outreach_tasks.send_message_impl` imports the canonical class instead of the
  adapter's. **Side effect worth knowing:** that `isinstance` branch now also
  catches a compliance block raised on the EMAIL render path, where
  `NEEDS_TEMPLATE` (a WhatsApp state) would be nonsense — so the status it
  records is now channel-aware (`NEEDS_TEMPLATE` for WhatsApp, `FAILED`
  otherwise). Nothing on the email path raises it today; this closes the trap
  before it opens.

**Verification, and an honest note on scope.** No non-debug HTTP endpoint
reaches the send guard — every `ComplianceError` in `whatsapp.py` is raised from
`send()`/`_compliance_guard()`, and in production those run inside the Celery
send task, not a request (`submit_template` / `fetch_template_status` /
`health_check` raise only `WhatsAppNotConfigured`). So rather than invent an
endpoint, `tests/test_whatsapp_guard.py::TestComplianceErrorIsCanonical`
asserts the real contract: the adapter exports the canonical class; **exactly
one** `ComplianceError` class exists under `app/` (a tripwire — this pattern has
regrown ten times); the guard's raises carry the machine-readable code; and the
exception the adapter actually raises, handed to the app's registered
`global_exception_handler`, produces a **422 carrying
`compliance_code=WHATSAPP_WINDOW_CLOSED`**. The four
`TestWhatsAppComplianceRules` integration tests still pass end-to-end over HTTP,
now with the translation layer deleted.

---

### 5. `ADMIN_EMAIL` — WIRED (this one has follow-on implications)

**The admin model already existed**, so this was wiring, not a design change:
`User.is_admin` (a real column), `require_admin` in `app/api/deps.py` gating
every `/admin` route, and `app/cli/create_admin.py`. Nothing read `ADMIN_EMAIL`.

`app/core/admin_bootstrap.py` + a **lifespan** handler in `app/main.py`
(`@app.on_event` is deprecated on FastAPI 0.111, so new startup work uses
`lifespan=`). On startup, if `ADMIN_EMAIL` names an existing user, it is
flagged admin. Idempotent, case-insensitive, never creates duplicates, and
**never clears `is_admin` on anyone**, so an admin promoted by the CLI is not
downgraded when a different `ADMIN_EMAIL` is configured. It never blocks
startup: an unmigrated or unreachable database is caught and logged.

**DELIBERATE DEVIATION — it promotes but does not CREATE.** There is no
password-reset flow anywhere in this application. An account created at startup
would need either a password nobody knows (unloggable — the operator still has
to run the CLI, so it removes nothing) or a generated one written to the logs
(a credential in the log stream, which is worse). A missing `ADMIN_EMAIL` user
is therefore reported as a loud, actionable ERROR naming the exact command
(`python -m app.cli.create_admin --email …`). Sign up once, or run the CLI, and
the next restart promotes it.

**Follow-on implication for Phase D:** the deploy sequence is now *(1)* set
`ADMIN_EMAIL`, *(2)* create that account once — via signup or the CLI — *(3)*
restart. Setting `ADMIN_EMAIL` alone on a fresh database still yields no admin,
by design; the ERROR log is the signal. If you would rather it self-create, that
needs a password-reset flow first.

13 tests in `tests/test_admin_bootstrap.py` cover: promotion; that the promoted
user actually satisfies `require_admin` (not just that a column flipped);
case-insensitivity; idempotence; no duplicate rows; no downgrade of another
admin; a missing user being reported rather than invented; unset/blank/whitespace
`ADMIN_EMAIL` being a silent no-op on a fresh DB; startup surviving an
unreachable database; and — driving the **real lifespan** through
`TestClient` — that startup actually performs the promotion.

---

### 6. npm advisories — deferral STANDS, but the stated reason was imprecise

Re-audited: **17** advisories now (3 critical, 9 high, 5 moderate), down from 20
— the Next.js 14.2.35 bump in B7 cleared three.

B8 recorded these as "dev/build-time only, none reachable in the shipped
artifact". That is no longer literally true as written: `next` and `postcss` now
appear, and `next` is not a dev-only package. The **conclusion still holds**,
for a more precise reason: this frontend is `output: 'export'` — a static export
served by nginx — so there is no Next.js server in production, and essentially
every `next` advisory is a server-side feature (Image Optimizer, Server
Components/Actions, middleware/proxy, rewrites, HTTP request smuggling, RSC
cache poisoning, WebSocket upgrades). Verified the two client-side XSS
advisories are inapplicable as well: the app uses no `next/script` /
`beforeInteractive`, no CSP nonces, and no `next/image` (images are
`unoptimized`, as `output: 'export'` requires).

Clearing them still needs breaking majors (`vitest` 4.x,
`eslint-config-next` 16.x, `@capacitor/cli` 8.x, and Next 15.5.16+).
**Deferral to post-launch stands** — but re-check it if the frontend ever stops
being a static export, because that assumption is now the whole argument.

---

### Found while verifying: an intermittently-green security test

`test_tampered_signature_returns_401` failed once in a full run and passed in
isolation. It was **not** an auth bug — the auth path was correct, and it was
correctly returning 200 for a **validly signed token**.

The test flipped the LAST character of the JWT signature. An HS256 signature is
32 bytes = 43 base64url characters, and 43 × 6 = 258 bits, so the final
character carries only 2 significant bits — its low 4 bits are discarded on
decode. For 4 of the 64 possible values (`Y`, `Z`, `a`, `b`) the flip decodes to
**identical bytes**, leaving the token perfectly valid. Roughly 6% of runs.

Fixed to tamper the FIRST signature character (all 6 bits significant) and to
assert that the tampering actually changed the decoded bytes, so it can never
silently degrade into testing nothing again. Ran it repeatedly to confirm.

## Session update 8 — 2026-08-19 (full system run on the dev machine)

Backend + Celery worker + beat + frontend were all brought up together against
the compose Postgres/Redis and clicked through. Four bugs surfaced that only
appear when the whole thing actually runs. All four are fixed and verified live.

### 1. `create_admin` produced admins who could never log in (TWO reasons)

This is the documented way to create the first admin on a fresh deployment,
and it was broken three ways over:

* **Wrong hash algorithm.** The CLI built its own
  `passlib CryptContext(schemes=["bcrypt"])`, while `app/services/auth.py`
  hashes with `pbkdf2_sha256` and `verify_password()` parses exactly
  `pbkdf2_sha256$iters$salt$digest`. A bcrypt hash does not parse, so
  verification returned False. Proven: `verify_password(pw, "<bcrypt hash>")`
  → `False`. **Instance #11 of the duplicate-implementation pattern**, and
  `passlib` was used in this one file and nowhere else in `app/`.
* **Wrong column.** It wrote `hashed_password`; the mapped column is
  `password_hash`. `User(hashed_password=…)` raises "invalid keyword
  argument", and the assignment form would have silently persisted NULL.
* **Crashed on Windows after committing.** `passlib` 1.7.4 reads
  `bcrypt.__about__`, removed in bcrypt 4.1+ (5.0.0 installed), which
  surfaced as the bogus "password cannot be longer than 72 bytes"; and the
  `✓` in the success message raised `UnicodeEncodeError` on a cp1252 console.

Now uses `app.services.auth.hash_password` and `password_hash`, with ASCII
output. Verified end to end: `create-admin` exits 0, and the created account
logs in and comes back `is_admin: True`.

### 2. `GET /admin/users` → 500 for every request

`UserSummary` declares `plan`, but `AdminService.list_users()` never returned
it, so FastAPI raised `ResponseValidationError`. `get_user_detail()` had the
same gap and `/admin/users/{id}` uses the same schema. Both now return
`plan` (PlanTier → its string value).

### 3. `GET /admin/suppression-list` → 500, and adding an entry → 422

* The service ordered by and returned `created_at`, `source` and `user_id`;
  `SuppressionEntry` has none of them — its timestamp column is `ts`. Hence
  `AttributeError: type object 'SuppressionEntry' has no attribute
  'created_at'`. `add_suppression()` also passed those three as constructor
  kwargs, so adding an entry could never have worked either.
* `SuppressionEntry` (the pydantic schema) declared `email: Optional[str]` and
  `phone: Optional[str]` **without defaults**. In pydantic v2 that is still a
  REQUIRED field that merely permits None, so the admin UI's
  `addSuppression({email, reason})` got `phone: Field required` on every call.

Both fixed; the response still exposes `created_at` (sourced from `ts`)
because that is the key the admin UI reads. Full CRUD verified live:
POST → 201, GET → 200 with real rows, DELETE → 200.

### 4. Redis/Celery pointed at another project's instance — see item 0 above

### What was verified working

* `GET /health` → `{"status":"ok"}` — database, redis and celery all ok.
* 20 frontend-facing API endpoints swept: all 200 except the two
  `/playbook/*` routes, which correctly return **402** (plan-gated;
  the test account is on `free`).
* Every real frontend page renders 200: `/`, `/login`, `/signup`, `/pipeline`,
  `/campaigns`, `/settings`, `/strategies`, `/strategies/new`, `/analytics`,
  `/privacy`, and all six `/admin/*` pages. NOTE: bare `/admin` and
  `/onboarding` 404 because neither exists as a page — `(admin)` is a route
  group whose real pages are `/admin/users`, `/admin/health`, etc.
* Local login for checking the system: `admin@example.com` / `CheckMe123!`
  (admin), and `owner@example.com` / `OwnerPass123!` (created through the
  fixed CLI).

Still blocked on credits: anything that calls Claude — strategy generation,
verification passes, personalization.

## Session update 9 — 2026-08-20 (PHASE C — credits arrived, and a production outage found)

### The blocker is gone

The Anthropic account now has credits. The `ANTHROPIC_API_KEY` already in
`.env` (108 chars, `sk-ant-api03…`) authenticates AND bills:

```
messages.create(model="claude-sonnet-4-6", max_tokens=16) -> "OK"  (14 in / 4 out)
```

`claude-sonnet-4-6` is a current, valid model id — no change needed.
Item 0 of "Open items as of session update 7" is CLOSED.

### PRODUCTION OUTAGE FOUND — no Celery task would have run in prod

This is the most serious bug found in the project so far, and Phase C is what
surfaced it. **It would have taken down the entire async backend on the first
production deploy, silently.**

`app/workers/celery_app.py` defined **no `task_routes` and no
`task_default_queue`**, so every task was published to Celery's implicit
default queue, **`celery`**. Meanwhile:

* `docker-compose.prod.yml` starts its three workers bound to
  `-Q pipeline`, `-Q outreach`, and `-Q learning,default`;
* `app/workers/monitoring.py::get_celery_stats()` (behind
  `/admin/celery-stats`) reports depth for those same four names — and had a
  `TODO: verify queue name configuration matches your celery_app.py
  task_routes` sitting directly above it, which is exactly the check nobody
  had run.

So **nothing consumed `celery`, and nothing was published to the four declared
queues.** In production all three worker containers would have idled on
permanently empty queues while every task — strategy generation, verification,
lead sourcing, outreach dispatch, reply polling, the nightly learning loop and
the beat heartbeat — piled up unconsumed in `celery`. No crash, no error log:
tasks just never execute. `/admin/celery-stats` would have shown four queues at
depth 0, and `/health` would have reported celery degraded forever (no
heartbeat key), which is the only symptom that would have surfaced.

It passed unnoticed in dev purely by luck: `docker-compose.yml` started its
worker **without `-Q`**, which makes Celery consume `celery` — the one queue
prod does not consume.

Verified against the live broker, not by reading:

```
task_default_queue = celery      task_routes = None
run_pipeline -> <unbound Queue celery -> Exchange celery(direct) -> celery>
redis -n 1 KEYS *  ->  celery, unacked, _kombu.binding.celery, ...   (no pipeline/outreach/learning/default)
```

**Fixed.** `celery_app.py` now declares explicit `task_routes` for all 16
registered tasks plus `task_default_queue="default"`:

| queue | tasks |
|---|---|
| `pipeline` | `leadpilot.run_pipeline`, `leadpilot.run_verification`, `leadpilot.leads.*` |
| `outreach` | `leadpilot.outreach.*`, `app.workers.send_tasks.*` |
| `learning` | `app.workers.learning_tasks.*` |
| `default` | `app.workers.beat_heartbeat.*`, `app.workers.webhook_tasks.*`, `leadpilot.ping` |

`docker-compose.yml`'s worker was also changed to
`-Q pipeline,outreach,learning,default`, because once routing exists a worker
with no `-Q` consumes only `celery` and would process **nothing** — the dev
environment would have broken in exactly the way prod was already broken.
`docker-compose.prod.yml` needed no change: its three workers already cover all
four queues.

**If you start a worker by hand, you must now pass `-Q`:**

```
celery -A app.workers.celery_app worker -Q pipeline,outreach,learning,default --loglevel=info
```

### The tripwire that would have caught it

`tests/test_celery_routing.py` (7 tests) asserts that the queue a task is
PUBLISHED to is a queue some worker in each compose file actually CONSUMES —
parsing `-Q` straight out of `docker-compose.yml` and `docker-compose.prod.yml`
rather than trusting a constant. It also asserts the mirror image (no worker
burning a container on a queue nothing publishes to), that every beat-scheduled
task is registered and routed, and that the default queue is not Celery's
implicit `celery`.

**The tripwire was verified by reintroducing the bug**, not by assuming it
works: with `task_routes` set back to `None`, 5 of the 7 fail, including
`test_every_task_lands_in_a_consumed_queue` on BOTH compose files. Restored, 7/7
pass. Do not delete these tests; if you add a task, add its route.

This is the recurring project pattern at instance eleven — writers and readers
of a name disagreeing — and it is the first instance that was fatal rather than
merely wrong.

## Session update 10 — 2026-08-20 (Phase C completed; deployment-readiness review)

### Phase C — the pipeline ran against the real model, end to end

The 144-step run completed: **STRATEGY 72/72 + GTM 72/72, zero errors, zero
retries** across ~150 real Anthropic calls (`claude-sonnet-4-6`). ICP
extraction, the tactic classifier and `pattern_key` derivation all ran off real
Phase 2/3/5/8 output, so `pattern_key` is set for the first time from genuine
model output rather than a mock. Personalization and WhatsApp draft generation
were exercised separately and both pass.

Everything Phase C set out to test has now run for real. See the verification
finding below for the one thing that did not come out clean.

### Harnesses added

* `scripts/phase_c_services.py` — the standalone Claude-backed services
  (gateway, pattern recognition, reply classification). 5/5 pass; reply
  classification got 6/6 routing classes right and degrades to `question` on
  garbage input.
* `scripts/phase_c_pipeline.py` — reports on a real run from the DATABASE
  (steps persisted, pattern_key, verification attempts, final status), then
  exercises the two services the pipeline chain never reaches: message
  personalization and WhatsApp template drafting. It deletes the template rows
  it creates.

### FINDING: the verification loop oscillates and cannot converge

All 10 passes ran. **7 cleared; 3 exhausted all 3 attempts** — so the strategy
lands in `NEEDS_HUMAN_REVIEW` rather than `VERIFIED`. 18 attempts, 8 fixes
applied, 26 Anthropic calls.

| pass | criterion | attempts | final |
|---|---|---|---|
| 1 | factual_accuracy | 3 | **FAIL** |
| 2 | icp_fit | 1 | PASS |
| 3 | channel_message_fit | 2 | PASS |
| 4 | legal_compliance | 3 | **FAIL** |
| 5 | deliverability_risk | 1 | PASS |
| 6 | competitive_realism | 1 | PASS |
| 7 | resource_feasibility | 1 | PASS |
| 8 | kpi_realism | 1 | PASS |
| 9 | personalization_quality | 2 | PASS |
| 10 | gtm_consistency | 3 | **FAIL** |

This is not a crash and not a mock-vs-real wiring problem — it is a design issue
that only a real model run could expose, because the transport mock always
returned a canned verdict. Note the shape: every pass that failed did so by
exhausting all three attempts, while every pass that succeeded took one or two.
Nothing partially converges — a pass either clears quickly or never clears.

What happened, from `verified_passes_json`:

1. Attempt 1 — judge: Phase 1 claims pricing of "$2,500-$7,500/month" but the
   Phase 2 pricing synthesis establishes $3,000 (Foundation) and $5,500
   (Growth). Fix applied -> document changed to $3,000-$5,500.
2. Attempt 2 — judge: the document now says $3,000-$5,500 but the **Phase 1
   source research** says $2,500-$7,500, so they disagree. Fix applied ->
   changed back to $2,500-$7,500 plus a parenthetical.
3. Attempt 3 — judge: the document now presents both ranges without clearly
   labelling which is superseded. Attempts exhausted.

Traced in detail on pass 1 (`factual_accuracy`); the same exhaustion shape
repeats on `legal_compliance` and `gtm_consistency`.

The fix generator reverted its own previous fix. The root cause is that the
judge treats **all** research phases as equally authoritative supporting
context, so when an early phase carries an estimate that a later phase
supersedes — which is the normal, intended shape of a 72-step funnel — the
criterion is unsatisfiable. No wording can match both.

Two separate defects sit underneath this:

* **No supersession rule.** `app/verification/loop.py::_support_block()` feeds
  the judge raw research with no notion that later phases refine earlier ones.
* **No oscillation detection.** The loop applies a fix that undoes the previous
  fix and burns the remaining attempts. It never compares a proposed fix against
  the previous document state.

**Impact:** `NEEDS_HUMAN_REVIEW` is the designed safe outcome, so nothing is
broken or unsafe — but in practice real strategies may land there routinely for
an unsatisfiable reason rather than for genuinely bad output, and each wasted
attempt is a full-document rewrite at `max_tokens=8000`. This is a product
quality and cost issue to fix before customers see it. It is NOT a deployment
blocker.

### PRODUCTION BUG: CORS_ORIGINS had two readers that disagreed on format

Instance **twelve** of this project's recurring pattern, and the second one this
week that would have been invisible in production.

* `app/main.py` (the reader that actually configures `CORSMiddleware`) does
  `os.getenv("CORS_ORIGINS").split(",")` — wants comma-separated.
* `app/core/config.py` typed it `CORS_ORIGINS: list[str]`, which made
  pydantic-settings **json.loads()** the value inside the dotenv source — wanted
  `["https://a.com","https://b.com"]`.

`.env.production.example` shipped the JSON form. Verified live against a running
server: with the JSON value the API returns **200 to curl, every healthcheck
passes, and there is no `access-control-allow-origin` header at all** — so every
browser request from the real frontend is blocked, with no server-side trace.
With the comma-separated value the header is correct.

Switching the templates to comma-separated then broke startup the other way:
`Settings` raised `SettingsError` at import, taking the whole app down. **Each
format broke one of the two readers.**

Why it never showed up in dev: nothing calls `load_dotenv`, so `.env` values
never reach `os.environ`, and `os.getenv("CORS_ORIGINS")` returned `None` — dev
silently used the correct hardcoded default. In Docker/Railway the value IS a
real env var, so only production would break. Same shape as the Celery outage:
the local environment masked it.

**Fixed:** comma-separated is now the single canonical format.
`app/core/config.py` types the field `str` (a `list[str]` is JSON-decoded in the
settings source, before any validator can normalise it) and exposes
`cors_origins_list` for the parsed form, tolerating the legacy JSON shape.
`.env`, `.env.example` and `.env.production.example` all corrected.
`tests/test_cors_format.py` (12 tests) pins both readers to one format and
asserts the shipped templates never regress to the JSON form.

### NEW: production startup guard

`app/core/production_guard.py` refuses to boot when `APP_ENV` is
production/prod/live and any of these is wrong, reporting **all** problems at
once rather than one per redeploy:

| variable | quiet failure it prevents |
|---|---|
| `SECRET_KEY` | `auth.py::_secret()` invents a RANDOM PER-PROCESS key. With `--workers 2` plus separate worker containers, a token minted by one process is rejected by the next — intermittent, unreproducible 401s, nothing logged. |
| `ENCRYPTION_KEY` | raises only on the FIRST OAuth token access, potentially days after deploy. Now validated as a real Fernet key at boot. |
| `CORS_ORIGINS` | unset falls back to `http://localhost:3000`; also rejects the JSON-array form and plaintext `http://` origins. |

**Verified live, not by unit test**: production with the vars unset refuses to
start and prints all three problems together; the control — production with
valid values — starts normally (`Application startup complete`).
`tests/test_production_guard.py` adds 26 tests.

Note `app/core/config.py`'s `SECRET_KEY = "change-me-in-production"` was
investigated and is **dead config** — nothing reads it; the real JWT path uses
`app/config.py`. Left in place, but it should be deleted: it invites exactly
this class of bug.

### docker-compose.prod.yml — full line-by-line review

Reviewed and fixed. **Caveat: Railway does not read this file.** Its value is as
the source of truth for each Railway service's start command and env, and as the
self-host fallback; `nginx`, published `ports:` and named volumes have no
Railway equivalent.

| # | Finding | Action |
|---|---|---|
| 1 | `NEXT_PUBLIC_API_URL` defaulted to `https://api.yourdomain.com` — a placeholder domain baked into the frontend at BUILD time. The frontend would build and deploy successfully with every API call pointing at a nonexistent host. | **Fixed** — now `:?` required, build fails loudly |
| 2 | No healthchecks on any of the 4 Celery services. A worker on the WRONG `-Q` is a live, idle process that passes liveness — exactly how the queue outage hid. | **Fixed** — `celery inspect ping` on all 3 workers, schedule-file check on beat |
| 3 | `api` published `0.0.0.0:8000` and `frontend` `0.0.0.0:3000`, bypassing nginx and TLS. Combined with `--forwarded-allow-ips='*'`, a direct caller could spoof `X-Forwarded-For` — which the IP rate limiter keys on. | **Fixed** — both bound to `127.0.0.1` |
| 4 | No resource limits on any service. | **Fixed** — memory limits on all 9 (starting points; tune against real traffic) |
| 5 | `celery-beat` wrote its PersistentScheduler file to ephemeral `/tmp`; losing it on restart re-fires the nightly aggregation early. | **Fixed** — moved to a `celerybeat_data` volume |
| 6 | `POSTGRES_PASSWORD` / `REDIS_PASSWORD` had no defaults but also no guard; an empty `REDIS_PASSWORD` would make redis parse the next flag as the password. | **Fixed** — `:?` required guards |
| 7 | `nginx` depended on `api`/`frontend` merely started, not healthy -> 502s during the migration window. | **Fixed** — `condition: service_healthy` |
| 8 | `version: "3.9"` obsolete in Compose v2. | **Fixed** — removed |
| 9 | Restart policies — all 9 services already had `unless-stopped`. | No change needed |
| 10 | `api` runs `alembic upgrade head` inline. Safe at ONE replica; concurrent replicas would race. | **Flagged, not changed** — judgment call; move to a one-shot init job if ever scaled |

Validated with `docker compose config`: exit 0, **9 services / 9 healthchecks /
9 memory limits**, and the `-Q` flags intact.

### Rate limits

`.env.production.example` defined **no** `RATE_LIMIT_*` at all, so production
silently inherited the `app/core/config.py` defaults. They are now listed
explicitly so they can be tuned without reading source.

**Judgment call flagged, not decided:** `RATE_LIMIT_STRATEGIES=10` per hour per
user. Each strategy is a 72/144-step pipeline plus 10 verification passes — the
run measured this session was ~150 calls. Ten per hour per user is a large,
uncapped spend. Lower it or add billing controls before opening public signups.

### Fresh-database migration test

Against a genuinely empty throwaway Postgres container (not dev, not the test
DB): **0 public tables -> 26**, full chain to `0014_strategy_pattern_inputs`,
no errors.

`ADMIN_EMAIL` bootstrap verified on that fresh DB under `APP_ENV=production`:

1. Boot with `ADMIN_EMAIL` set and no such user -> logs a clear, actionable
   message and promotes nothing (documented behaviour — it promotes, it does not
   create).
2. Sign up that address over real HTTP -> 201, `is_admin: false` (signup cannot
   self-promote, by design).
3. Restart -> `Promoted founder@example.com to admin via ADMIN_EMAIL`, confirmed
   by direct SQL (`is_admin = t`); then login -> `/auth/me` shows
   `is_admin: True`, `/admin/users` returns 200 with the token and 401 without.

The deploy sequence is confirmed: **set ADMIN_EMAIL -> create the account once
-> restart.** Container removed afterwards.

### Celery routing fix — now verified LIVE, not just by unit test

Session update 9 fixed the routing; this session proved it against real
processes. A worker started with `-Q pipeline,outreach,learning,default` bound
to all four queues; `run_verification` routed to `pipeline` and was consumed
immediately; beat was restarted and published to `default`/`outreach`.

It also demonstrated **why the queue separation matters**: with a single worker,
the long verification task starved the heartbeat, `default` backed up to 7
queued heartbeats, and `/health` reported celery `degraded`. Starting a second
worker on `-Q learning,default` — the prod topology — drained it and `/health`
went fully **`ok`** (`last_beat_seconds_ago: 16`) while the pipeline worker
stayed busy. That is precisely the failure prod's three-worker split prevents.

**Migration note:** changing routing orphans anything already queued on the old
`celery` queue. A fresh deploy has no backlog, but do not change routing on a
live system with queued work without draining first.

**Operational gotcha:** Git Bash `pkill -f` does NOT reliably kill Windows
`python.exe` Celery processes. Two stale workers kept running (and kept spending
on Anthropic) after an apparently successful `pkill`. Use PowerShell
`Get-CimInstance Win32_Process` + `Stop-Process -Force`, and verify the process
list afterwards.

### .gitignore hardened

`.gitignore` covered only the literal `.env` — not `.env.local`,
`.env.production` or `.env.prod`. This repo is about to be pushed to GitHub for
Railway. It now covers `.env.*` with `!.env.example` / `!.env.production.example`
negations, plus `*-secrets.txt` and `.phasec/`. **Verified** in a throwaway git
repo: all real env files ignored, both templates still tracked.

### Production secrets

Generated fresh (never used in dev or test) and written OUTSIDE the repo to
`C:\Users\devel\clienthunter-prod-secrets.txt`, ACL-restricted to the owner
account: `SECRET_KEY`, `ENCRYPTION_KEY`, `POSTGRES_PASSWORD`, `REDIS_PASSWORD`,
`WHATSAPP_VERIFY_TOKEN`, `CALENDLY_WEBHOOK_SECRET`. The file also lists the 13
third-party credentials that must come from each provider's dashboard.

The generated `SECRET_KEY` and `ENCRYPTION_KEY` were verified to satisfy the
production guard, and the Fernet key round-trips a real encrypt/decrypt.

### Where the suites stand

| Suite | Before | Now |
|---|---|---|
| Root (`tests`, SQLite, no services) | 308 passed / 5 skipped | **353 passed / 5 skipped** |
| Integration (PG + Redis) | 116 passed | **116 passed** |

New: `test_celery_routing.py` (7), `test_production_guard.py` (26),
`test_cors_format.py` (12).

## Session update 11 — 2026-08-20 (pre-deployment gate: four items + three new bugs)

Domain confirmed: **leadpilot.com** (frontend) / **api.leadpilot.com** (backend).

### 1. Verification-loop oscillation — FIXED

Two defects, both fixed, behind the 3-of-10 `NEEDS_HUMAN_REVIEW` result.

**Source-of-truth precedence.** `app/verification/passes.py::PRECEDENCE_RULE` is
a new explicit block injected into BOTH the judge and the fixer prompts: later
research phases supersede earlier ones for the same fact; a document stating the
latest value is correct; do not ask for the superseded value back. It is stated
as a rule rather than left to per-call inference, so behaviour is deterministic.

`_support_block()` now labels every research block with an ordinal and a
supersedes note (`[strategy research | source 2 of 8 | phase 2 synthesis |
supersedes strategy sources 1-1]`) so the rule is mechanically applicable. Phase
numbers alone were ambiguous once two pipelines exist — STRATEGY and GTM both
have a phase 2.

**Oscillation detection.** `_apply_fix` was split into `_generate_fix` (produces
the revision) and an explicit apply step, so a revision can be rejected *before*
it overwrites the document. Each pass keeps a history of the document states it
has produced, seeded with the state it started from. If a proposed fix reverts
to any earlier state — exact match after whitespace normalisation, or ≥0.98
similarity for a near-revert — the pass ends immediately with
`outcome: "conflicting_sources"`, recording both competing fix descriptions and
which attempt it reverted to, for the review layer to adjudicate.

The blocking `pg_advisory_lock` analogue here is deliberate: stopping early does
NOT downgrade the strategy to VERIFIED. It still reaches NEEDS_HUMAN_REVIEW —
it just stops paying for two more full-document rewrites at `max_tokens=8000`
to rediscover a conflict already proven.

**Verified:** `tests/test_verification_oscillation.py` (18 tests) reproduces the
exact Phase C pattern — judge alternates between demanding $3,000-$5,500 and
$2,500-$7,500, fixer flip-flops. Confirmed the tests genuinely catch the bug:
with detection disabled **4 of 18 fail**, including the assertion that the pass
stops after 2 judge calls instead of spending all 3.

**Item 1.4 — re-run on the real strategy: ATTEMPTED, BLOCKED.** The re-run
against the actual Phase C strategy failed with
`401 authentication_error: API key is invalid` — the user's key rotation had
taken effect. The Phase C evidence (18 attempts, NEEDS_HUMAN_REVIEW, document)
was snapshotted before the attempt and restored intact afterwards.

Fell back to mock-driven verification, which the task explicitly permitted.
**Stated limitation:** `PRECEDENCE_RULE` is a prompt change, and mocks cannot
prove a real judge now accepts a superseded value. The oscillation detection is
fully proven (it is deterministic code); the precedence rule is not. Re-running
verification on strategy `9ed7fde1` once a valid key is in `.env` is the
outstanding confirmation — roughly 26 calls.

### 2. RATE_LIMIT_STRATEGIES — and the limiter was never wired at all

**The setting was inert.** While tightening the value, the limiter turned out to
be dead code. `app/core/rate_limiting.py` implements a decorator, a dependency
helper and two admin endpoints for inspecting/resetting counters — and **not one
route was ever limited**. Verified live: six consecutive
`POST /products/{id}/strategies` against a limit of 2 returned **six 202s**.

Two reasons it could never have worked:

* the `rate_limit` decorator's wrapper is `async def` and `await`s the endpoint,
  but every route needing a limit (`create_strategy`, `source_leads_for_strategy`)
  is a sync `def`;
* it raises `app.core.rate_limiting.RateLimitExceeded`, which has **no exception
  handler**, so it would surface as 500 rather than 429. (There is also a
  SECOND, unrelated `RateLimitExceeded` in `app/integrations/plumbing.py` —
  instance **thirteen** of the duplicate-name pattern.)

**Fixed** with `rate_limit_dependency(scope, setting_name)`, a dependency
factory that works on sync and async routes and raises a proper
`HTTPException(429)`. Applied to `POST /products/{id}/strategies` and
`POST /strategies/{id}/leads/source` — the two endpoints that spend money.
The limit is read from settings at request time, not import time.

**Verified live after the fix:** calls 1-2 → 202, calls 3+ → **429** with
`retry-after: 3586` and body
`{"error":"rate_limit_exceeded","retry_after":3586,"limit":2,"window":3600}`,
backed by a per-user Redis counter `rate:{user_id}:strategies`.

**Final value: 2/hour/user (was 10).** Reasoning, measured not guessed:

| operation | cost | limit |
|---|---|---|
| wa_templates | 1 Claude call | 20/hr |
| leads_source | paid external API | 5/hr |
| playbook_recompute | heavy DB aggregation, admin | 3/hr |
| **strategies** | **~150 Claude calls, ~72 min** | **10/hr → 2/hr** |

The existing scale is ordered by cost, and strategies was the *most permissive*
limit while being by far the most expensive operation — inverted. Throughput
makes a high limit meaningless anyway: the Phase C run took ~62 min of pipeline
plus ~10 min of verification, so one strategy occupies a pipeline worker for
over an hour and 10/hour only builds a queue. 2 rather than 1 so a user who
misconfigures a product can retry without a support ticket.

Raise it in `app/core/config.py::RATE_LIMIT_STRATEGIES` (rationale in the
docstring) and `.env.production.example`. `tests/test_rate_limit_wiring.py`
(6 tests) asserts the dependency stays attached and the value stays ≤3.

### 3. Inline `alembic upgrade head` race — FIXED

`app/db/migrate.py` wraps the upgrade in a PostgreSQL advisory lock
(`pg_advisory_lock`, blocking — not `pg_try_advisory_lock`, because a replica
that cannot get the lock must WAIT rather than boot against the old schema).
Session-scoped, released in a `finally`, and dropped by PostgreSQL if the
connection dies, so a killed migrator cannot wedge future deploys.
Non-PostgreSQL URLs (the SQLite unit suite) skip the lock.

**Verified by simulation, with a control:** four migrators started
simultaneously against a genuinely fresh database.

* **Without the lock: 3 of 4 exited non-zero** with `IntegrityError` on the
  `alembic_version` insert. In a container that is a crash-loop on every deploy.
* **With the lock: 4 of 4 exited 0**, 26 tables, one `alembic_version` row at
  `0014_strategy_pattern_inputs`, no duplicate/deadlock errors.

**Railway pattern — verified against Railway's docs, not assumed.** Railway's
native mechanism is `deploy.preDeployCommand`, which runs in a *separate
container* after build and before the new version starts, with the service's
env vars available; a non-zero exit aborts the deployment. Configured in the new
`railway.json`. Railway's docs do **not** state whether it runs once per
deployment or once per replica, which is precisely why the advisory lock is kept
as layer 2 — and it also covers the compose path, which has no pre-deploy
concept. `docker-compose.prod.yml` now calls `python -m app.db.migrate`.

New `DEPLOY_RAILWAY.md` documents the six-service topology, the exact `-Q` flags
per worker, env-var placement, the first-deploy ADMIN_EMAIL sequence, and
rollback. `tests/test_migration_lock.py` (13 tests) pins the lock id, the
blocking semantics, and that neither deployment config runs bare alembic.

### 4. Final sweep — two more production bugs found

**`GMAIL_REDIRECT_URI` pointed at a route that does not exist.** All four
definitions used `/api/v1/integrations/gmail/callback`. `app/main.py` mounts
every router **unprefixed** — the real path is `/integrations/gmail/callback`,
and **0 of the app's 78 mounted paths start with `/api/v1`**. Google would have
redirected users to a 404 and the Gmail OAuth flow could never complete, with
nothing failing until someone tried to connect an account. Fixed in
`app/core/config.py`, `.env`, `.env.example`, `.env.production.example`.
`tests/test_config_paths_match_routes.py` (4 tests) now asserts every config URL
resolves to a mounted route.
**Remember to register `https://api.leadpilot.com/integrations/gmail/callback`
in Google Cloud Console.**

**`PUBLIC_BASE_URL` and `SENDER_IDENTITY` were absent from every env template.**
Production would have inherited the code defaults:

* `PUBLIC_BASE_URL` → `http://localhost:8000`, which is what **unsubscribe
  links** are built from (`sequence_engine.py:383`), plus WhatsApp opt-in links
  and media URLs. Real marketing email would have shipped with an unsubscribe
  link no recipient could use — a CAN-SPAM violation and a fast route to the
  spam folder.
* `SENDER_IDENTITY` → the literal placeholder
  `"Vectorae User, 123 Main St, City, Country"`, which is the **CAN-SPAM
  physical postal address** printed in every email footer.

Both added to `.env.production.example` and both now enforced by
`production_guard`, which rejects a localhost/plaintext `PUBLIC_BASE_URL` and a
placeholder-looking `SENDER_IDENTITY`.

Also: `app/core/config.py::SECRET_KEY` no longer defaults to
`"change-me-in-production"`. Nothing reads it (the real key is
`app/config.py::jwt_secret`), but a publicly-known default on an importable
Settings field is a trap for the first code that reaches for it. Now `""`.

### Domain configuration

| variable | value |
|---|---|
| `CORS_ORIGINS` | `https://leadpilot.com,https://www.leadpilot.com` |
| `NEXT_PUBLIC_API_URL` | `https://api.leadpilot.com` (**build time**) |
| `PUBLIC_BASE_URL` | `https://api.leadpilot.com` |
| `FRONTEND_URL` | `https://leadpilot.com` |
| `GMAIL_REDIRECT_URI` | `https://api.leadpilot.com/integrations/gmail/callback` |
| `ADMIN_EMAIL` | `admin@leadpilot.com` |

Production guard verified live against these exact values: accepted.

### Residual risks, flagged not fixed

1. **Frontend localhost fallback.** `frontend/src/lib/api/client.ts` and
   `pipeline/page.tsx` use
   `process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"`. If the build
   variable is missing on Railway the bundle builds green with localhost
   compiled in, deploys green, and every visitor's browser calls their own
   machine. Compose guards this with `:?`; Railway cannot. Documented
   prominently in `DEPLOY_RAILWAY.md` — verify the variable before the first
   frontend build.
2. **`PRECEDENCE_RULE` unproven against the real model** (see item 1).
3. **Dev `.env` still has `SECRET_KEY=change-me-in-production`.** Correct for
   dev, and `production_guard` blocks it from ever booting production —
   confirmed live.
4. `app/cli/create_admin.py` contains `admin@yourdomain.com` in a docstring
   usage example. Documentation only.

### Where the suites stand

| Suite | Before | Now |
|---|---|---|
| Root (SQLite, no services) | 353 passed / 5 skipped | **403 passed / 5 skipped** |
| Integration (PG + Redis) | 116 passed | **116 passed** |

New this session: `test_verification_oscillation.py` (18),
`test_migration_lock.py` (13), `test_rate_limit_wiring.py` (6),
`test_config_paths_match_routes.py` (4), plus 9 added to
`test_production_guard.py` (26 → 35).

## Session update 12 — 2026-08-20 (branding, build guard, Railway config-as-code)

Domain: **leadpilot.com** / **api.leadpilot.com**.

### Logo assets

Source `image.png` measured before use: PNG, RGBA, **528x532**, fully opaque,
flat `#100F14` ground. It is a LOCKUP — a blue ascending arrow (bbox
113,123-460,346) above a "VECTORAE" wordmark (bbox y 381-397, only 17px tall,
~3% of the canvas).

That measurement drove the design: **icons use the arrow mark only.** At a 192px
icon the wordmark would be ~6px tall and at 16px sub-pixel, so shipping the
lockup as an icon produces a dark square with an illegible smudge. The product
name travels in the manifest `name` field instead. The full lockup is used only
on the login hero, where it renders at 132px and actually reads.

| asset | path | notes |
|---|---|---|
| favicon | `frontend/src/app/favicon.ico` | multi-size **16/32/48**, Next App Router convention |
| apple touch | `frontend/src/app/apple-icon.png` | 180x180 |
| PWA any | `frontend/public/icons/icon-192.png`, `icon-512.png` | mark at 78% |
| PWA maskable | `frontend/public/icons/icon-maskable-512.png` | mark at 62% |
| lockup | `frontend/public/brand/logo-full.png` | byte-identical to the source |

**Maskable was feasible** and was verified numerically, not by eye: content max
radius **187px** against a safe-zone radius of **205px** (0.40 x 512). The flat
brand ground meant the mark could be padded rather than cropped. The "any" icon
deliberately reaches 236px — nothing crops it.

`manifest.json` icons now resolve: all three fetched over HTTP, **200, sizes
matching the declared values, no 404s**. `background_color` corrected to the
brand ground `#100f14`.

In-app usage via a new `frontend/src/components/ui/Logo.tsx` (`LogoMark` /
`LogoLockup`, one place to change sizing): sidebar and mobile header in
`Shell.tsx`, the admin sidebar (which had no brand anchor at all), and the login
page, where the lockup replaces the words and the title drops to "Sign in".
The mark is `alt=""` + `aria-hidden` beside real text; the lockup carries
`alt="Vectorae"`.

**No og:image was added.** The project has no `openGraph` metadata at all, and
inventing a social-sharing setup was out of scope.

### Risk 1 — frontend localhost fallback: FIXED

`frontend/next.config.js` now fails the **production build**:

```
BUILD FAILED: NEXT_PUBLIC_API_URL is a local origin (http://localhost:8000).
```

The first version of this guard only checked for *unset* — and it did not fire,
because **`frontend/.env` ships `NEXT_PUBLIC_API_URL=http://localhost:8000`** and
Next loads `.env` into `process.env` before evaluating `next.config.js`. A guard
defeated by a file already in the repo is no guard, so it now rejects loopback
origins too.

Scoped to `PHASE_PRODUCTION_BUILD` so `next dev` still works. `client.ts` also
throws at module load if the value is missing in a production bundle, and
`pipeline/page.tsx` now imports `BASE` instead of re-deriving the origin — a
second copy of the fallback is a second way to ship localhost.

**Verified with real builds:** unset/local -> `exit 1`, **no `out/` emitted**;
`https://api.leadpilot.com` -> `exit 0`, origin baked in, and **zero occurrences
of `localhost:8000` anywhere in the bundle**.

### Risk 2 — PRECEDENCE_RULE: STILL UNVERIFIED (blocked)

The re-run could not be performed. `.env` still contains the **pre-rotation**
key: 108 chars, no BOM, no quotes, no stray whitespace, ending in the same four
characters as the old value, and it returns
`401 authentication_error: API key is invalid`. Only one `.env` exists in the
tree. The rotated key never reached the file.

The oscillation DETECTION is deterministic code and is fully proven by
`tests/test_verification_oscillation.py` (18 tests; 4 fail when the detection is
disabled). `PRECEDENCE_RULE` is a prompt change and remains unproven against a
real judge. Re-run `run_verification_loop` on strategy `9ed7fde1` (~26 calls)
once a working key is in `.env`.

### Risk 3 — worker -Q flags: moved OUT of the dashboard

Railway's docs confirm per-service config files ARE supported (service settings
-> Config File Path, absolute repo path) and that **config-as-code overrides
dashboard values**. So every start command, including the `-Q` lists, now lives
in a committed file:

```
railway/api.json               preDeployCommand: python -m app.db.migrate
railway/worker-pipeline.json   -Q pipeline -c 2
railway/worker-outreach.json   -Q outreach -c 4
railway/worker-learning.json   -Q learning,default -c 2
railway/beat.json              (no -Q: beat publishes only)
```

**The root `railway.json` was deleted deliberately.** A service without an
explicit config path inherits the root file — a worker would have picked up the
API's uvicorn `startCommand` and served HTTP while consuming no queues.
`tests/test_celery_routing.py` grew 6 tests that parse `railway/*.json` and fail
if a worker lacks `-Q`, if the queues do not cover every routed task, if beat
gains a `-Q`, if more than one service migrates, or if a root `railway.json`
reappears.

`DEPLOY_RAILWAY.md` gained a copy-pasteable command block for the manual
fallback and a **POST-DEPLOY verification section** — `/health` celery status,
`inspect ping`, `inspect active_queues` (the check that actually catches a wrong
`-Q`), `/admin/celery-stats` queue depths, and an end-to-end strategy check.

### NEW BUG: docker-compose.prod.yml could not build at all

Every backend service builds with `target: production`, but the Dockerfile had a
**single unnamed stage**. `docker compose -f docker-compose.prod.yml build api`
failed with:

```
failed to solve: target stage "production" could not be found
```

`docker compose config` never caught it — that validates and renders, it does
not resolve build targets, which is why the earlier line-by-line review passed
it. Fixed by naming the stage (`FROM python:3.11-slim AS production`); the image
now builds, and was checked to contain both `app.db.migrate` and the correct
task routing.

### Where the suites stand

| Suite | Before | Now |
|---|---|---|
| Root | 403 passed / 5 skipped | **409 passed / 5 skipped** |
| Integration | 116 passed | **116 passed** |


## Session update 13 — 2026-08-20 (BLOCKER: the fixer truncates the strategy document)

Risk 2 was probed with a controlled A/B against the real model. It did not close,
and it surfaced a worse bug underneath.

### The probe

For each pass that exhausted its attempts in the Phase C run, the SAME real judge
was asked the SAME question about the SAME real document and support block,
twice — once without `PRECEDENCE_RULE`, once with it. Everything else held
constant. **6 calls, $0.4318 actual spend** (cap was $9).

| pass | without rule | with rule |
|---|---|---|
| factual_accuracy | FAIL | **FAIL** |
| legal_compliance | PASS | PASS |
| gtm_consistency | FAIL | **FAIL** |

`legal_compliance` passes now either way — its earlier exhaustion was marginal,
not a precedence problem. The other two still fail **with** the rule, so
`PRECEDENCE_RULE` is NOT sufficient. But the judges' reasons show why, and it is
not what we assumed.

### BLOCKER: `_generate_fix` silently truncates the document it rewrites

`gtm_consistency`'s verdict pointed at the document "cutting off mid-sentence".
It does. `strategy.strategy_document` ends, literally:

```
## strategy research — phase 1 synthesis

**Pricing range:** $3,000–
```

Mechanism, confirmed from the run's own logs:

* the document is **32,937 chars ≈ 8,234 tokens**;
* `app/verification/loop.py::_generate_fix` asks the model to "Output the
  complete revised document" with **`max_tokens=8000`** — the document cannot
  fit, so the response is cut off at the ceiling;
* `ClaudeClient.complete()` **never checks `stop_reason`**. A `max_tokens`
  truncation is returned exactly like a complete answer;
* the loop then does `strategy.strategy_document = revised`, writing the
  truncated text over the good document.

Direct evidence from `.phasec/worker2.log`: **7 of the 8 fixer calls returned
exactly `out_tokens=8000`**, the ceiling, and one returned 7,980. So seven fix
applications each destroyed the tail of the document, and the trailing
`## strategy research — phase 1 synthesis` heading is support-block content the
model had begun echoing before it was cut off.

**This reframes the earlier "oscillation" finding.** The loop was not only
arguing with itself about which price was authoritative — each "fix" was also
lopping the end off the document, manufacturing fresh inconsistencies for the
next judge to find. Oscillation detection (session update 11) is still correct
and still proven, but it was treating a symptom.

**Severity: HIGH.** The verification step silently corrupts the primary artifact
the customer receives, and every strategy long enough to matter is affected. It
is data loss, not a quality wobble.

Not fixed in this session — stopped and reported per instruction rather than
spending further budget iterating. The shape of the fix is clear enough to note:
raise the fixer ceiling well above the document size, check `stop_reason` in
`ClaudeClient.complete()` and refuse to persist a truncated revision, and
consider having the fixer emit a targeted patch rather than a full rewrite.

### Risk 2 status

**STILL OPEN.** `PRECEDENCE_RULE` alone does not make the failing passes
converge, and the document is corrupted, so a full end-to-end re-run would be
measuring a damaged input. Re-test after the truncation bug is fixed and the
document is regenerated.

Spend this session: **$0.4318** (plus a ~$0.00004 auth probe).


## Session update 14 — 2026-08-20 (truncation fixed; Risk 2 blocked on credits)

### The three-layer truncation fix

**Layer 1 — ceiling.** `settings.verification_fixer_max_tokens = 18_432`,
replacing a hardcoded `8000`. That is ~2.2x the largest real document (8,234
tokens). It is deliberately NOT larger: the SDK refuses a non-streaming request
whose `max_tokens` implies >10 minutes of generation, raising
`ValueError("Streaming is required ...")` locally before sending. Measured
against this SDK version: **21,333 accepted, 32,000 refused**. The model's own
cap is 128,000 (confirmed live via the Models API).

**Layer 2 — gateway.** `ClaudeClient.complete()` now raises
`TruncatedResponseError` when `stop_reason == "max_tokens"`. Previously it
concatenated the text blocks and returned, so a cutoff was indistinguishable
from a finished answer. `complete_json()` inherits the check, so judges are
covered too. `stop_reason` is now in the log line. The gateway also switches to
the streaming endpoint above `_NON_STREAMING_MAX_TOKENS = 20_000`, which is what
makes the doubled retry possible at all.

**Layer 3 — loop.** `_generate_fix` retries ONCE at double the ceiling (36,864,
which streams). If that also truncates, `run_verification_loop` catches the
error, **leaves `strategy_document` exactly as it was**, logs the attempt with
outcome `fix_truncated` (distinct from `conflicting_sources`) carrying the
ceiling and the competing fix descriptions, and ends that pass. No path writes
partial text.

**Layer 4 — noted, not built.** A targeted patch mechanism (emit a diff for the
one line that is wrong, rather than regenerating 8,000+ tokens to change a price
range) would be cheaper and would sidestep ceilings entirely. Out of scope here;
worth doing before this loop runs at volume.

**Reproduction test:** `tests/test_fix_truncation.py` (17 tests). Verified
fail-old/pass-new by reverting all three layers: **7 of 12 failed** against the
old code, including the decisive
`test_document_is_left_untouched_when_every_ceiling_truncates`. Zero real API
calls — a fake client reproduces the `stop_reason` the API returns.

**A regression the unit tests could not see.** The first version of this fix
used a 32,000 ceiling, which the SDK refuses without streaming — every
`run_pipeline` call crash-retried. Unit tests passed; the INTEGRATION suite
caught it (`test_exhausted_verification_notifies_needs_review`). Fixing that by
streaming then hit a second wall: the integration mock serves plain JSON, not
SSE, so `get_final_message()` asserted. Final design keeps the primary call
under the cut-off and reserves streaming for the retry.

### Document repaired

`assemble_documents()` is pure — it concatenates the intact phase syntheses with
no API calls — so the corrupted document was regenerated for **$0**. It went
from 32,937 chars ending mid-sentence at `**Pricing range:** $3,000-` to 33,141
chars ending in a complete sentence. The corrupted version is archived at
`.phasec/corrupted_document.bak`. The 144 research steps were never damaged;
only the assembled document was.

### Risk 2 — PARTIALLY closed, then blocked

A/B against the repaired document (**$0.2837**, 4 calls):

| pass | without rule | with rule |
|---|---|---|
| gtm_consistency | **PASS** | **PASS** |
| factual_accuracy | FAIL | FAIL |

`gtm_consistency` was failing **purely because of the truncation** — repairing
the document fixed it. With `legal_compliance` (which passes either way), that
is **2 of the 3 original failures resolved**.

`factual_accuracy` still fails, but correctly: the reassembled document carries
the Phase 1 estimate `$2,500-$7,500`, and the judge flagged it as contradicting
the authoritative GTM Phase 2 floor of $3,000 — citing "source 2 of 8", which is
the ordinal labelling added to `_support_block`. So the precedence scaffolding
IS reaching the judge and shaping its reasoning; the document genuinely states a
superseded figure, so failing it is right.

That means the A/B did not actually test PRECEDENCE_RULE. The rule is about the
opposite case: a document stating the LATER value while an earlier phase still
carries the old one. The decisive probe — same document with the price corrected
to `$3,000-$5,500`, judged with and without the rule — **could not run**:

```
400 invalid_request_error: Your credit balance is too low
```

Confirmed account-level: even a 1-token call fails. **Risk 2 remains open**,
needing 2 calls (~$0.15) once credits are topped up.

### Suites

| Suite | Before | Now |
|---|---|---|
| Root | 421 passed / 5 skipped | **426 passed / 5 skipped** |
| Integration | 116 passed | **116 passed** |

Spend this session: **$0.2837**.


## Session update 15 — 2026-08-20 (full-stack run; terminal-state + retry + rate-limit fixes)

The whole stack was brought up locally and exercised end to end: Postgres
(5433), Redis (6380), the API, three Celery workers bound to `pipeline` /
`outreach` / `learning,default`, beat, and the Next dev server. `/health`
reported `last_beat_seconds_ago`, which proves beat -> Redis -> API actually
round-trips rather than each side reporting its own guess. Queue isolation was
confirmed from the worker logs: each worker executed only tasks belonging to
its own queues, and `/admin/celery-stats` reports the same four names the
workers bind to.

That run surfaced four defects. All four are fixed below.

### 1. No strategy could ever reach a terminal FAILED state

`StrategyStatus.FAILED` existed in the enum, in `StrategyStatusOut`, in the
frontend's `types.ts` and in `badge.tsx` (which renders a red "destructive"
badge for it) — and **nothing in `app/` ever assigned it.** Only RESEARCHING,
VERIFYING and NEEDS_HUMAN_REVIEW were ever written. A pipeline that gave up
permanently left its strategy at `researching`, showing the owner an
in-progress spinner for work that had stopped.

This is instance **fourteen** of this project's recurring "two things that must
agree, don't" pattern — here the writer side simply never existed, so the
reader side was unreachable code.

Live database when found: **seven** strategies stranded, not the three first
reported (the initial sample only covered the five most recent). Four carried a
401 from the key revoked ~3 days earlier, two carried a 400 credit-exhaustion
error, and one had **no error recorded at all** — a worker killed mid-run after
22 committed steps, where no Python-level handler ever ran.

Fixed in `app/workers/tasks.py`: a new `_fail_strategy()` sets
`StrategyStatus.FAILED` and records the reason in the **existing** `error`
column (no new field) at both give-up points in `run_pipeline` AND
`run_verification`. The original exception is re-raised rather than swallowed,
because `monitoring.register_task_failure_signal()` only writes the
`task_errors` row and alerts admins on a real task failure.

FAILED is terminal but **not** final: `run_pipeline` refuses only VERIFIED and
NEEDS_HUMAN_REVIEW, so `POST /strategies/{id}/resume` still restarts a FAILED
strategy and the engine resumes from its last committed step. A test pins that.

Cleanup: `scripts/fail_stuck_strategies.py` (dry-run by default,
`--min-idle-hours` guard so a live pipeline is never touched) moved all 7 to
FAILED with honest reasons — the recorded error verbatim plus a
"reclassified" note, or an explicit "worker died without recording an error"
for the one with no cause. Re-running it now finds nothing.

### 2. Permanent errors burned three retries before giving up

The gateway already knew a revoked key or an exhausted balance can never
succeed on retry — those errors are simply absent from the retryable tuple —
but the Celery layer retried *every* exception, so a permanent failure spent
90 seconds on three doomed attempts before landing in the limbo above.

`app/services/anthropic_client.py` now exposes the classification explicitly:
`RETRYABLE_API_ERRORS`, `PERMANENT_API_ERRORS` and `is_permanent_error()`.
Unknown exception types deliberately keep the old retry behaviour — a wrong
"permanent" verdict would turn a recoverable blip into a dead strategy.

While making the distinction explicit: **`OverloadedError` (529) was not being
retried.** It is not a subclass of `InternalServerError` (both derive straight
from `APIStatusError`), so listing only 5xx left the single most retryable
response the API sends unretried. It is now in `RETRYABLE_API_ERRORS`.

### 3. Failed validation burned a rate-limit slot

The limiter was a route-level `dependencies=[...]`, which FastAPI resolves
**before** it validates the request body. A malformed payload that never became
a real request still incremented the counter. With `RATE_LIMIT_STRATEGIES=2`,
two typo'd submissions locked a user out for an hour having created nothing.
Verified live: 422, 422, 429, 429 with zero strategies created.

`enforce_rate_limit()` (new, sync) is now called as the first statement inside
`create_strategy` and `source_leads_for_strategy`. FastAPI validates body/path
params before executing a handler body, so only well-formed requests can spend
quota. Business-rule rejections further down (404, 409) still consume, which is
intended — those did real lookup work.

`tests/test_rate_limit_wiring.py` was UPDATED, not deleted: its tripwire now
asserts the handler calls `enforce_rate_limit` with the right setting.

### 4. Browser tab and PWA name still said "Vectorae"

Display strings only, per instruction. `layout.tsx` title, `manifest.json`
`name`/`short_name`, the privacy page's title/description/body copy, the
onboarding checklist copy, the `Exit Vectorae?` back-button dialog (and its
test), and `capacitor.config.ts` `appName` all now say **Vectorae**.
`appId` (`com.clienthunter.app`) is untouched — it cannot change after a Play
Store publish — and file-header comments were left alone. Changing `appName`
only reaches Android after `npx cap sync`.

### Also found, deliberately NOT fixed — flag before deploying

* **Four of six `RATE_LIMIT_*` settings are wired to nothing.**
  `RATE_LIMIT_PLAYBOOK_RECOMPUTE`, `RATE_LIMIT_WA_TEMPLATES`, `RATE_LIMIT_GET`
  and `RATE_LIMIT_AUTH` are documented and defaulted but referenced by zero
  routes. **`RATE_LIMIT_AUTH` inert means login/signup have no brute-force
  protection** — security-relevant for a public deploy. Same pattern class as
  the two above.
* **The Celery task bodies had zero test coverage** — conftest stubs
  `run_pipeline.delay`, so nothing ever executed them. That is why a latent
  string-vs-`Uuid` mismatch (`session.get(Strategy, "<str>")`, which PostgreSQL
  accepts and SQLite rejects) survived unnoticed. `_pk()` now coerces; the new
  tests are the first coverage these bodies have ever had.
* **A worker killed mid-run still records nothing** — no in-task handler can
  catch SIGKILL. `monitoring.detect_stale_pipeline_steps` notices but is
  deliberately detection-only. One of the seven stuck strategies was this case.
* **User-facing contact addresses still reference old domains**:
  `privacy@clienthunter.app` (privacy page, already marked TODO) and
  `upgrade@clienthunter.io` (PlanCard). Left alone because pointing them at
  `leadpilot.com` presumes those mailboxes exist. `DEEP_LINK_SCHEME` and
  `APP_LINKS_HOST` likewise need a decision, not a guess.

### How each fix was verified

* **Fail-old/pass-new, proven not assumed.** With only the task behaviour
  reverted (UUID coercion kept, so failures could not be for the wrong reason),
  **8 of 19** `tests/test_terminal_failure.py` tests failed — exactly the
  permanent-fails-fast, never-calls-retry, exhaustion-lands-in-FAILED and
  resumable cases. The retryable-still-retries tests passed in BOTH versions,
  confirming retry behaviour was not changed. Restored: 19/19.
* **Finding 3 fail-old:** restoring the route-level dependency failed 4 of 7
  `tests/test_rate_limit_ordering.py` tests, including the behavioural
  "a 422 costs nothing" case. Restored: 7/7.
* **Live, against the running stack, $0 API spend:** rate limiter now returns
  422, 422, 202, 202, 429 (was 422, 422, 429, 429). A real strategy creation
  hitting the genuine 400 credit error failed in ~2.5s with **zero retry lines**
  (previously four retries over 90s) and landed in `failed` with the reason
  recorded. `GET /strategies` returns `"status": "failed"`, and
  `statusTone("failed")` resolves to `destructive` — the red badge is reachable
  with real data for the first time.
* **Finding 4 in a real built artifact**, not just source: production build
  served over HTTP shows `<title>Vectorae</title>` on every page,
  `Privacy Policy — Vectorae`, and a manifest with `name`/`short_name` of
  `Vectorae`.

Celery's eager mode re-executes retries inline, so `apply()` can never show an
intermediate RETRY state — "retries" and "does not retry" look identical from
the final result. The tests spy on the `retry` call itself instead; see
`_spy_on_retry`.

Note: running `npm run build` while `next dev` is live corrupts the shared
`.next` directory and the dev server starts returning 500. Stop the dev server
before building.

## Session update 16 — 2026-08-20 (RATE_LIMIT_AUTH wired; login/signup brute-force protection)

Session update 15 flagged that four of six `RATE_LIMIT_*` settings were
defined, documented, and referenced by zero routes. The most serious of those
— `RATE_LIMIT_AUTH` — is now wired. **Login and signup previously accepted
unlimited credential guesses.**

### Full wiring status, verified by grep over app/api/ (not assumed)

| Setting | Protects | Status |
|---|---|---|
| `RATE_LIMIT_STRATEGIES` = 2/hr | `POST /products/{id}/strategies` | WIRED — strategies.py:70 |
| `RATE_LIMIT_LEADS_SOURCE` = 5/hr | `POST /strategies/{id}/leads/source` | WIRED — leads.py:74 |
| `RATE_LIMIT_AUTH` = 10/15min | `POST /auth/login`, `POST /auth/signup` | **WIRED (this session)** — auth.py:103, 146, 152 |
| `RATE_LIMIT_PLAYBOOK_RECOMPUTE` = 3/hr | `POST /playbook/recompute` (admin) | **INERT** |
| `RATE_LIMIT_WA_TEMPLATES` = 20/hr | `POST /whatsapp/templates/generate` | **INERT** |
| `RATE_LIMIT_GET` = 300/min | "GET endpoints" — no specific route | **INERT** |

### What was wired, and how

Same mechanism and ordering as session update 15's Finding 3: a synchronous
`enforce_rate_limit()` call as the handler's first statement, so FastAPI has
already validated the body and a malformed request cannot spend quota. No new
mechanism was invented.

Auth needed one genuine extension. `enforce_rate_limit`'s first argument was
named `user_id`, but auth endpoints are **unauthenticated** — there is no
current user to key on. The parameter is now `identity`, documented as "the
thing the limit is scoped to", and a new `client_ip(request)` helper reads
`request.client.host`.

Two independent keys, both governed by the single `RATE_LIMIT_AUTH` value:

* `ip:<addr>` — stops one host brute-forcing passwords. Applied to login AND
  signup, which therefore share one budget: an attacker cannot alternate
  between the two endpoints to double their allowance.
* `acct:<email>` — login only. Stops **credential stuffing**, where attempts
  against one account are spread across many IPs. A per-IP limit alone does
  nothing about that, which is the reason both keys exist rather than just the
  obvious one. Keyed on the normalised (lowercased, stripped) address so
  casing cannot multiply the allowance.

`client_ip` is only trustworthy because the API is not directly reachable:
uvicorn runs with `--proxy-headers --forwarded-allow-ips='*'`, and
docker-compose.prod.yml binds it to `127.0.0.1` with nginx as the sole public
entrypoint precisely so a direct caller cannot spoof `X-Forwarded-For`. That
compose comment already referred to "the IP rate limiter" — which did not
exist until now. If the API is ever exposed publicly, per-IP limiting becomes
bypassable.

### Why 10 per 15 minutes

Security-based reasoning, unlike `RATE_LIMIT_STRATEGIES` (cost/throughput):

* A legitimate user needs 1-3 attempts. Mistyping four times in 15 minutes
  already means going to a password reset, not a fifth guess — so 10 leaves
  roughly 3x headroom over the worst honest case.
* An attacker gets 10 guesses per 15 minutes = 960/day per key. Against even a
  weak six-character lowercase password (~3.1e8 combinations) that is on the
  order of a million years. The goal is not to make guessing hard but to make
  ONLINE guessing useless; any limit in this range achieves that, and 10 buys
  it without inconveniencing real users.
* The window is 15 minutes rather than an hour so a locked-out honest user
  waits minutes, not most of an hour.

The existing documented default was already 10 per 15 minutes, so this
confirms the configured value rather than changing it.

**Known trade-off, recorded rather than hidden:** attempts are counted whether
they succeed or fail, so an office behind one NAT IP shares the per-IP bucket —
11 people logging in within the same 15 minutes would see a 429. Real logins
are roughly once or twice a day (clients hold a refresh token rather than
re-authenticating), so this is unlikely but possible. If a customer reports it,
RAISE `RATE_LIMIT_AUTH`; it is a single env var. Counting only failed attempts
would be gentler but needs a check-then-conditionally-record split the limiter
does not have, and adding one would mean two divergent limiter mechanisms.

`/auth/refresh` is deliberately NOT limited: legitimate clients hit it every
time a 15-minute access token expires, so it is the one auth route where normal
use approaches the threshold, and a signed refresh token is not brute-forceable.
A test pins this so it cannot be "fixed" by accident.

### Verification

**Live, against the running stack, $0 API spend.** Ten wrong-password POSTs to
`/auth/login` returned 401; the eleventh and twelfth returned **429 with
`Retry-After: 875`** and a body of
`{"error":"rate_limit_exceeded","retry_after":...,"limit":10,"window":900}`.
A correct password on the first attempt returned **200**, and returned 200
again once the window was cleared. A password under the 8-character minimum
returned 422 and created **no** Redis counter — the Finding 3 ordering
guarantee holding on auth too.

Both independent dimensions were confirmed live by exhausting one key and
leaving the other empty: with only the account bucket spent, a request from a
fresh IP still got 429 (IP rotation does not help an attacker); with only the
IP bucket spent, a request against a different account still got 429.

**Fail-old/pass-new, proven not assumed.** With all three
`enforce_rate_limit` calls stripped from auth.py, **9 of 15**
`tests/test_rate_limit_auth.py` tests failed — both structural tripwires and
every brute-force case. The 6 that still passed are exactly the ones that must
pass either way: legitimate login, typo-then-success, 422-costs-nothing,
refresh-unlimited, and the two no-route-level-dependency guards. Restored:
15/15.

The behavioural tests drive a real bcrypt-hashed account through real HTTP with
a controllable source IP. `TestClient` hard-codes the ASGI scope's client
address and `httpx.ASGITransport` (which accepts one) is async-only on httpx
0.28, so a small test-only ASGI shim sets `scope["client"]` from a header —
leaving `client_ip()` itself exercised unmodified rather than stubbed.

### A test-isolation landmine this exposed (fixed)

Wiring the limiter to signup broke
`test_m5_backend.py::TestTheme::test_background_upload_serves_url` — it got a
429 where it expected a 201. That failure was real and worth keeping:

* Starlette's `TestClient` reports a client host of `"testclient"` for every
  request in the process, so ALL root tests shared one
  `rate:ip:testclient:auth_ip` bucket. Dozens of them sign a user up as setup;
  past the tenth, the rest were rate-limited. Pure test-ordering landmine.
* Worse, `get_sync_redis()` connects to the REAL Redis, so the root suite —
  documented as the in-process SQLite library that deliberately needs neither
  PostgreSQL nor Redis — had quietly acquired a live Redis dependency through
  the limiter, and the counters survived between runs.

Fixed with an `autouse` `isolated_rate_limiter` fixture in `tests/conftest.py`
that points `get_sync_redis` at a per-test `fakeredis`. Autouse so it cannot be
forgotten when the next limited endpoint is added; tests that assert on limiter
behaviour simply patch it again with their own instance.

### Still INERT — flagged, not guessed at

Each needs a judgment call rather than a mechanical wiring, so none were
touched:

* `RATE_LIMIT_PLAYBOOK_RECOMPUTE` — admin-only and already the cheapest to
  abuse of the three, but "which admin route" needs confirming against
  app/api/admin*.py.
* `RATE_LIMIT_WA_TEMPLATES` — the docstring names
  `POST /whatsapp/templates/generate` (one Claude call per request, so this one
  costs real money when unbounded), but there are several template routes and
  which of them should share a budget is a product decision.
* `RATE_LIMIT_GET` — documented as "GET endpoints per minute per user", which
  is not a route but a policy. Applying it means either middleware over every
  GET (a different mechanism from `enforce_rate_limit`) or picking a subset by
  hand. This is the one that genuinely cannot be wired without a decision.

None is security-critical the way `RATE_LIMIT_AUTH` was; the paid-endpoint and
credential-attack surfaces are now both covered.

## Session update 17 — 2026-08-20 (free-tier stack: Render + Neon + Upstash + Vercel)

Additive deployment path alongside Railway. `railway/*.json` and
`DEPLOY_RAILWAY.md` are untouched. New files: `render.yaml`, `.dockerignore`,
`app/worker_health.py`, `scripts/render_worker_entrypoint.sh`,
`DEPLOY_RENDER_FREE.md`.

### Three defects the Docker build exposed — all would have broken Railway too

The image had never actually been built and run before this session.

1. **`app.main` could not import inside the image.** `structlog` is imported
   unconditionally by `app/core/logging.py`, which six `app/api/*` modules
   import — and it was never declared in `pyproject.toml`, so `pip install .`
   never installed it. The API could not boot in ANY container. It worked
   locally only because dev machines had structlog from elsewhere. `typer` and
   `rich` were missing the same way, which would have broken the documented
   `python -m app.cli.create_admin` first-deploy step. All three now declared.

2. **No `.dockerignore`.** `COPY . .` produced an 877 MB `/code` containing
   `/code/.env`. Now 4.8 MB with no `.env`; build context 213 MB+ → 22 kB.
   **Scope correction:** Render builds from a git clone and `.env` is
   gitignored (verified: never committed, key absent from all history), so
   Render was never going to receive it. The real exposure was builds from a
   local working tree — which is what `docker-compose.prod.yml` does
   (`context: .`).

3. **`app/workers/webhook_tasks.py` imported `services.webhook_delivery`** —
   no such top-level package; it is `app/services/webhook_delivery.py`. Every
   SIGNED outbound webhook raised `ModuleNotFoundError` and retried forever
   while unsigned deliveries worked and hid it. One-line fix.

### The worker runs as a "web service" because Render's free tier has no worker

`scripts/render_worker_entrypoint.sh` runs Celery worker
(`-Q pipeline,outreach,learning,default`), beat, and a one-route health server
on `$PORT`, under a `wait -n` supervisor: if any child exits, the rest are
killed and the service exits non-zero so Render restarts it.

`app/worker_health.py` deliberately does NOT mount `app.main` — that would
publish all ~97 routes on a second origin nobody reasoned about.

**`/health` reports real liveness, not a constant 200.** An earlier test run
had both Celery processes dead on the missing-structlog error while `/health`
happily reported `{"worker":"ok","beat":"ok"}` — because `os.kill(pid, 0)`
succeeds for an unreaped zombie. It now reads `/proc/<pid>/stat` and treats
state `Z` as down. Exactly the silent-green-while-broken shape this project
keeps hitting.

Verified against the built image: worker `ready` on all four queues,
`leadpilot.ping` round-tripped through the container with no other worker
running, `kill -9` → container exit 137.

**The free worker sleeps after ~15 min idle and a sleeping service runs no
processes.** An external cron-job.org ping on `/health` every ~10 min is
REQUIRED, not optional. Documented in both the script and
`DEPLOY_RENDER_FREE.md` as an accepted $0 trade-off, not a bug to fix in code.
The real fix is a $7/mo Render Background Worker — the first paid upgrade to
make.

### Part 3 — Neon and Upstash, verified live

All four connections confirmed working: Neon (server 18.6, `neondb`), Celery
broker, Celery result backend (`PING → PONG`, `SSLConnection`,
`ssl_cert_reqs=2`), and the app's own Redis client.

Alembic chain applied clean on the first run — 17 revisions,
`0001_initial_models` → `0014_strategy_pattern_inputs`, exit 0. Resulting
schema: 26 tables, 85 indexes, 25 foreign keys, zero model tables missing.

**`?ssl_cert_reqs=required` is mandatory for Celery** and was reproduced, not
assumed: the Redis result backend raises
`ValueError: A rediss:// URL must have parameter ssl_cert_reqs` on a bare
`rediss://`. The broker and `redis-py` both tolerate it, so plain `REDIS_URL`
needs nothing — only `CELERY_BROKER_URL` and `CELERY_RESULT_BACKEND`.

**`pg_stat_ssl` reports `ssl = false` on Neon.** Not an unencrypted connection
— proved by attempting `sslmode=disable`, which Neon refuses. TLS is mandatory
on the wire; the false reading is Neon terminating TLS at its proxy.

### MIGRATION_DATABASE_URL — the important Part 3 finding

`pg_advisory_lock` is **session-scoped**, and Neon's pooled endpoint is
PgBouncer in transaction mode, which hands each statement whatever server
connection is free. Measured against the live database with two migrators
behaving exactly as `app/db/migrate.py` does:

| Endpoint | second migrator's `pg_try_advisory_lock` | verdict |
|---|---|---|
| pooled (`-pooler`) | **acquired a lock the first was holding** | lock does nothing |
| direct (no `-pooler`) | correctly refused | works |

So the migration-race protection added in session update 11 is **silently
inert** on the pooled endpoint — instance fifteen of this project's
"two things that must agree, don't" pattern, here between a session-scoped
lock and a transaction-pooling proxy.

Fix: `app/db/migrate.py::_migration_url()` prefers `MIGRATION_DATABASE_URL`
(Neon's DIRECT endpoint) while `DATABASE_URL` stays pooled for app traffic,
and `_run_alembic_upgrade(url)` threads it into Alembic via
`config.attributes["migration_url"]` so the lock and the migration act on the
same endpoint. `alembic/env.py` falls back to `settings.database_url` when the
attribute is absent, so every existing caller is unaffected. Unset, behaviour
is exactly as before.

`tests/test_migration_lock.py` updated (not weakened): its source tripwire
still requires the unlock in a `finally` around the upgrade call, now allowing
the call to carry an argument.

### Also worth knowing

The project acquired a `.git` repo with a GitHub remote
(`Rmasood1122/LeadPilot`) partway through the session — Render and Vercel both
deploy from it. Verified: one commit, `.env` never committed, the live key
absent from all history, `.env` and `.runlogs/` both ignored.

Root suite after all of the above: **467 passed / 5 skipped**, no regression.

### Blueprint import failed — three schema errors (fixed)

Render rejected the first import with its generic *"A Blueprint file was found,
but there was an issue."* The YAML was valid; three fields were not, checked
against Render's current Blueprint spec rather than guessed:

* `dockerTarget` — **no such field exists** (build-stage targeting is still an
  open feature request). Harmless to drop: the Dockerfile has exactly one
  stage, `production`, which is also the final stage.
* `startCommand` — Docker services must use `dockerCommand`.
* `preDeployCommand` — *"available for PAID web services"*, so it is a
  paid-only feature on a `plan: free` service. Migrations moved inline into
  `dockerCommand`, as docker-compose.prod.yml already does; the advisory lock
  (with MIGRATION_DATABASE_URL on Neon's direct endpoint) covers the race.

Verified by running the exact new `dockerCommand` against the built image with
the real Neon and Upstash credentials: lock acquired, migrated to head, uvicorn
up, `/health` = database ok / redis ok.

### Billing: 750 instance-hours is the real constraint

750 free instance hours per month **per workspace**, consumed only while awake.
A month is ~730 hours, so pinging the worker 24/7 to keep it from sleeping
burns ~97% of the allowance and leaves ~20 hours for the API — and exhausting
the pool suspends **all** free services until the next month. Ping the worker
on a restricted daily window instead.

With a card on file, the ONLY billable overage is outbound bandwidth
(100 GB/month, then $0.15/GB). Instance-hour exhaustion suspends rather than
bills, card or not. There is no account-wide spending cap on Render — only a
build-pipeline-minutes limit.


## Open items as of session update 7 — the full list

> **SUPERSEDED 2026-08-20 (session update 9).** Item 0's credit blocker is
> CLEARED and Phase C has run against the real model. Item 2 (hosting +
> domain) is the only external blocker left. The text below is kept for the
> record — read session update 9 for current state.

Session 7 closed every code-level item below (3-8). **Only the two external
blockers remain.**

**External (need the user):**

0. **~~BLOCKER FOUND 2026-08-19: the Anthropic account has no credits.~~ CLOSED 2026-08-20 — credits added, verified billing.**
   `.env` already contains a **valid** `ANTHROPIC_API_KEY` (108 chars,
   `sk-ant-` prefix). It authenticates — the failure is not a 401. Every call
   returns:

   ```
   400 invalid_request_error: Your credit balance is too low to access the
   Anthropic API. Please go to Plans & Billing to upgrade or purchase credits.
   ```

   Verified against `claude-sonnet-4-6`, `claude-sonnet-4-5` and
   `claude-3-5-haiku-latest` — same result, so it is account-level, not a model
   or key problem. **Phase C cannot run until credits are added** at
   console.anthropic.com → Plans & Billing.

   **Everything else on the Phase C path is verified working (2026-08-19).**
   The full stack was brought up for real — uvicorn + celery worker + beat
   against the compose Postgres and Redis — and driven end to end:

   * `GET /health` → `{"status":"ok"}` with database, redis AND celery all ok.
   * `smoke_test.py` → every check passes (health, signup, login, /auth/me,
     product creation). NOTE: it deliberately STOPS before strategy creation,
     so it is a pre-Claude smoke test, not a full Phase C harness.
   * A real strategy was created (`POST /products/{id}/strategies` → 202), the
     `leadpilot.run_pipeline` Celery task picked it up, and it reached a live
     Anthropic call. The ONLY error in the worker log is
     `invalid_request_error` (billing) — no authentication_error, no
     permission_error, no rate_limit_error. The pipeline recorded the error on
     the strategy, left it in `researching`, and scheduled `Retry in 30s`,
     which is the documented resumable behaviour.

   So the wiring is proven; credits are the single remaining gap. When they are
   added, re-running the same flow is the whole of Phase C's first step.

   **Config bug found and fixed while doing this:** `.env` and `.env.example`
   pointed `REDIS_URL` and both Celery URLs at `localhost:6379`, but
   docker-compose publishes this project's Redis on **6380** (6379 is taken by
   an unrelated project on this host). Confirmed live: `localhost:6379` is
   `traceabilityon-redis-1`, and it was ALREADY holding this app's
   `cache:apollo:*`, `cache:hunter:*` and `learning_loop:*` keys — so the
   response cache, circuit-breaker state and the **Celery broker** were all
   running through another project's Redis. Both files now use 6380.
   `.env.production.example` was already correct (it uses the compose hostname
   `redis:6379`) and was left alone.
   Leftover Vectorae keys still sit in that other project's Redis; they are
   only cache entries, and deleting data from another project's datastore is
   the user's call, so they were left in place.

1. ~~A real `ANTHROPIC_API_KEY` for Phase C~~ — present and valid; see item 0.
   The original wording of this item: The 72/144-step pipeline, the 10
   verification passes, ICP extraction, the new tactic classifier, reply
   classification and personalization have only ever run against a
   transport-layer mock.
2. **Hosting choice + domain for Phase D.**

**Code-level — ALL CLOSED in session update 7:**
3. ~~duplicate `ComplianceError` in whatsapp.py~~ — removed; one canonical
   class, codes at the raise sites, debug workaround deleted, 422 verified.
4. ~~`ADMIN_EMAIL` read by nothing~~ — wired via a startup lifespan hook.
   **Read session update 7 item 5 before deploying:** it promotes an existing
   user, it does not create one, so the deploy sequence is set ADMIN_EMAIL →
   create the account once → restart.
5. ~~`Message.token` deprecation~~ — investigated; a soft DeprecationWarning,
   and `fid` is a different identifier, not a rename. Deliberately unchanged,
   with the reasoning recorded at the call site.
6. ~~empty PWA icons~~ — placeholder set generated and the manifest validated.
   Real brand artwork still needed before a store/PWA launch.
7. ~~`pattern_key` inputs not persisted~~ — migration 0014 stores the canonical
   payload; the backfill now rehashes offline.
8. ~~npm advisories~~ — re-audited (17, down from 20); deferral stands, with a
   corrected justification (static export, not "dev-only"). Re-check if the
   frontend ever stops being a static export.

## Older items, still accurate

## Blocked (need external input — ask the user, don't guess)

- **Phase C (integration testing)**: needs a real `ANTHROPIC_API_KEY` to
  test the actual strategy-generation pipeline end-to-end (the core
  product feature — currently completely untested beyond auth/CRUD).
  Optionally also Apollo/Hunter/Gmail keys for lead-sourcing features
  (can launch without these, gate the features until keys exist).
- **Phase D (deployment)**: needs the user's hosting choice (VPS /
  Render / Railway / etc.), domain name, and confirmation before you
  generate and hand over production secrets.

## Phase D — deployment checklist (only start after Phase A/B/C are clean)

1. Generate strong prod-only secrets — never reuse dev values:
   `SECRET_KEY` (`python -c "import secrets; print(secrets.token_urlsafe(48))"`),
   `ENCRYPTION_KEY` (`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`).
2. Review `docker-compose.prod.yml` line-by-line — not yet reviewed this
   session.
3. Set `CORS_ORIGINS` to the real production frontend domain, not
   localhost.
4. `NEXT_PUBLIC_API_URL` is baked into the frontend at BUILD time (static
   export limitation) — you cannot change it at runtime; build once per
   target environment.
5. Review `BACKUP.md` (exists in repo) for the database backup story.
6. Run migrations against a genuinely fresh prod DB (only tested against
   local dev DB so far).
7. Review rate limits for real traffic (currently dev defaults).
8. Firebase/push notifications — the wiring is done (session update 5) and
   six events now push, so this is purely a credentials step: set
   `FIREBASE_CREDENTIALS_PATH` to a real service-account JSON, or leave it
   unset and accept that `send_to_user` logs one ERROR per event and drops the
   push (it will not break anything). Nothing has ever run against real FCM.
9. Walk `LAUNCH_CHECKLIST.md` (exists in repo, has its own list) item by
   item — reconcile with this brief, don't ignore it.

## Immediate next steps, in order

Updated 2026-08-20 (session update 16). All suites green — root **467
passed / 5 skipped**, integration **116 passed, no xfails**, frontend **156
passed**. Phase C has run
against the real model, and the pre-deployment gate (session update 11) is
closed: verification oscillation, rate limiting, the migration race and the
leadpilot.com domain config are all done.

1. ~~Ask the user for a real `ANTHROPIC_API_KEY`~~ — **DONE.** Credits were
   added; Phase C ran end to end. See session update 9.
2. ~~Wire the M7 notification layer~~ — **DONE** (session update 5).
3. ~~Decide the Calendly cross-tenant matching question~~ — **DONE**
   (session update 6).
4. ~~Collapse the duplicate `ComplianceError`~~ — **DONE** (session update 7).
5. **Ask the user for the hosting choice and domain to start Phase D.** This is
   now the ONLY thing blocking the project.

Nothing is open at code level. Do not start Phase D without the hosting
decision — but note that session update 9 fixed a Celery queue-routing bug that
would have made the first production deploy a silent total outage, so re-read
that section before deploying anything.

Do not skip ahead to Phase D while Phase A/B has open items — that's
exactly how the `ui_support.py` shadow-routing security bug almost
shipped unnoticed earlier in this project.