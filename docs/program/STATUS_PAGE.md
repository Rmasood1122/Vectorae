# Vectorae status page — Phase 3

**Primary location (2026-10-11): https://www.calendarharvest.com/status/** — a public page
inside the main frontend (`frontend/src/app/status/`), deployed by the existing Vercel project
"lead-pilot" that builds `frontend/` as a static export. `calendarharvest.com` 308-redirects to
`www`, so both reach it. No DNS record, no second Vercel project, no secret is needed.

The separate `status-page/` app for `status.calendarharvest.com` (Part B below) is now
**optional**. It is built and drilled but not deployed (no DNS). It adds what the primary page
does not have: UptimeRobot data (90-day uptime bars, an independent "Website & app" check),
RSS/Atom feeds and per-incident pages. Deploy it only if the founders want those.

Principle (both pages): the page must not depend on what it reports on. If the API is down,
the page still loads and says so. If it cannot check something, it says "No data", never
"Operational".

# Part A — www.calendarharvest.com/status (primary)

## A1. How it works

```
 browser ──► www.calendarharvest.com/status/   (Vercel project "lead-pilot", static export:
               │                                out/status/index.html, no server code)
               │  on load, then every 30 s while the tab is visible; 8 s timeout
               └─► Vectorae API  GET ${NEXT_PUBLIC_API_URL}/status/public   (Render)
                     the same checks as GET /health (app/services/health_checks.py)
```

- The HTML is static and served by Vercel's CDN, independent of Render. The live part
  (`frontend/src/components/status/StatusBoard.tsx`) runs in the visitor's browser.
- **Polling:** once on load, then every 30 s; paused while the tab is hidden; when the tab
  comes back it checks at once if 30 s have passed, otherwise waits only the remainder.
  A "Check now" button forces a check. One request in flight at a time.
- **Unreachable:** network error, CORS rejection, 8 s timeout, any non-2xx other than 429
  (for example the host's error page while the service is down), or a body that is not the expected
  JSON → headline **"Vectorae API unreachable — major outage"**, API shown as Major outage,
  the other components "No data", plus one plain sentence (e.g. "did not answer within 8
  seconds"). Earlier good data is dropped, never shown as current.
- **429** (rate limited) is not an outage: the last good result stays, marked with its time,
  and the next check waits 2 minutes. With no earlier result the headline is "Some systems
  could not be checked".
- **Never Operational for something unchecked:** an unknown status value, or a known component
  missing from the answer, shows "No data", and the headline then reads "Some systems could not
  be checked" rather than "All systems operational".
- **Nothing internal reaches the page:** only `id`, `name` and `status` are read from the
  response; no status codes, hostnames or error text are rendered (tests below).
- Shows: overall headline, the 4 components from `/status/public`, "Last checked <local
  time>", active incidents, past incidents ("No incidents reported." when empty).
- **Accessibility:** the summary is a polite live region; every state has a text label and its
  own icon shape (check, triangle, minus-circle, octagon, wrench, question mark, wifi-off), never
  colour alone; status colours are tested ≥ 4.5:1 on the page background. Layout is one column,
  `max-w-3xl` with a 16 px gutter on phones; component rows wrap.
- **Styling:** follows the marketing pages (about, how-it-works), which are dark-only
  (`bg-[#0a0a0a]`); there is no light variant of the marketing site to follow.
- **Without JavaScript:** a `<noscript>` line says JavaScript is needed for the live check.
- Links: footer "Status" (Company column) and the support widget's "Service status" icon, both
  `/status/` (`frontend/src/lib/statusPage.ts`, `SHOW_STATUS_LINK = true`). The widget link
  opens in the same tab (a relative `target="_blank"` link is unreliable in the Capacitor app).
- The Android app bundles the same export, so `/status/` also works inside the app; its origin
  (`https://localhost`) is always allowed by CORS (`app/main.py`).

| File | What |
|---|---|
| `frontend/src/app/status/page.tsx` | The route (outside the authenticated `(app)` group), nav, footer |
| `frontend/src/components/status/StatusBoard.tsx` | Fetching schedule and rendering only |
| `frontend/src/lib/statusBoard.ts` | Pure logic: parsing, overall state, unreachable/429 handling, refresh decisions, colours |
| `frontend/src/lib/statusIncidents.ts` | The incident list and its validator |
| `frontend/src/tests/statusBoard.test.ts`, `statusBoardPage.test.tsx`, `statusIncidents.test.ts`, `statusPageLink.test.tsx` | vitest |
| `tests/test_status_page_frontend_contract.py` | Component ids/names and status vocabulary in `statusBoard.ts` must equal `app/api/status_public.py`; CORS answers a configured origin and no other |

## A2. CORS and rate limit (backend, unchanged)

- The browser calls the API cross-origin from `https://www.calendarharvest.com`. No backend
  change was made: `CORSMiddleware` in `app/main.py` allows the comma-separated `CORS_ORIGINS`
  env var, which must already contain the frontend origin or the signed-in app could not call
  the API at all. The page sends a CORS "simple" GET (no credentials, no custom headers), so
  there is no preflight. **Founder check (not verifiable from the repo):** Render →
  leadpilot-api → Environment → `CORS_ORIGINS` contains `https://www.calendarharvest.com`
  exactly (scheme, `www`, no trailing slash). Quick check from any terminal:
  `curl -s -o /dev/null -D - -H "Origin: https://www.calendarharvest.com" https://leadpilot-api-ph3t.onrender.com/status/public | grep -i access-control-allow-origin`
  must print that origin. If it prints nothing, add the origin to `CORS_ORIGINS` and redeploy.
- `RATE_LIMIT_PUBLIC_STATUS` (default 600/hour per client IP). One open tab makes 120
  requests/hour, so about 5 tabs behind one shared address (office NAT, carrier-grade NAT) reach
  the limit; the page then backs off as described above. Raise the env var on Render if that
  happens in practice; no code change is needed.

## A3. Posting an incident (primary page)

A pull request that edits one file: `frontend/src/lib/statusIncidents.ts`.

1. Branch, add an object to `INCIDENTS` (the commented example in the file shows the shape):
   `id` = `YYYY-MM-DD-<slug>` (UTC date of the first update), `title` (≤ 120 characters, what
   customers noticed), `impact` (`none | minor | major | critical | maintenance`), `components`
   (`website`, `api`, `background-processing`, `ai-features`, `booking-pages`), `updates`
   oldest first, each `{status, at, text}` with `status` one of
   `investigating | identified | monitoring | resolved` and `at` in UTC ending in `Z`.
2. `cd frontend && npx vitest run src/tests/statusIncidents.test.ts` — every entry must pass
   `validateIncident` (UTC times, not in the future, in order, at most one `resolved` and only
   last, id date = first update's date, known components).
3. Open the PR, merge with `scripts/gate/safe_merge.sh <PR>`. Vercel redeploys `frontend/`; the
   incident is live when the deploy finishes.
4. For each new update, append to `updates` in the same object. Resolve with a final
   `resolved` update. Never change a published `id`.

While unresolved, an incident shows each listed component at least as bad as its impact
(minor → Degraded, major → Partial outage, critical → Major outage, maintenance → Under
maintenance only while the checks say Operational); it never makes a component look better.
Writing rules as in `status-page/incidents/README.md`: facts only, UTC times, no internal
hostnames, error messages, queue names, customer names or counts.

A typo in a field value fails `tsc`, which `next build` runs, so Vercel will not deploy it. The
validator runs in `npm test`, which the `verify-frontend` CI job runs on PRs touching
`frontend/`; that job is **not** in `scripts/gate/required_checks.txt`, so check it is green
before merging. Same limitation as Part B: the sealed `gate` job (~9 minutes) runs on every PR.
If the optional subdomain app is ever deployed, post the incident there too (Part B §7); the
two lists are separate.

## A4. Verification done (2026-10-11, build sandbox)

- vitest: the four files above, including malformed JSON, timeout (fake timers, 8 s), network
  failure, 5xx page, 429, degraded component, all ok, missing/unknown component, tab hidden →
  no polling → visible → immediate check.
- `NEXT_PUBLIC_API_URL=https://api.example.test npm run build`: the export contains
  `out/status/index.html` (title "Vectorae Status", static "Checking
  status…" and "No incidents reported."), the API origin is compiled into the page chunk, and
  `out/about/index.html` links `href="/status/"`. (Google Fonts is blocked in the sandbox; the
  build used Next's `NEXT_FONT_GOOGLE_MOCKED_RESPONSES` test hook for the Inter font.)
- **Not done (needs a browser and production):** opening the page on the Vercel preview or
  production, the CORS founder check above, a real outage drill (stop the API, watch the page
  switch within one 30 s cycle + 8 s timeout), and a Lighthouse accessibility score.

## A5. Go-live checklist (primary)

- [ ] Merge; Vercel deploys `frontend/`; https://www.calendarharvest.com/status/ loads.
- [ ] CORS founder check (A2) prints the www origin; the page shows the four components.
- [ ] UptimeRobot clean-up and keyword fix (section 4) — independent of this page but it is the
      only thing that alerts the founders; this page alerts nobody.
- [ ] Lighthouse accessibility ≥ 95 on the live URL, recorded here.
- [x] `SHOW_STATUS_LINK = true` (footer + support widget link to `/status/`).
- [x] Help-center articles 30 and 29 describe this page.

# Part B — optional: status.calendarharvest.com (`status-page/` app)

Status: built and drilled locally on branch `claude/status-page` (2026-10-11). **Not live,
and no longer required.** To deploy it the founders would still have to supply the UptimeRobot
read-only key and the DNS record (decision D6 itself is resolved, 2026-10-11, by Part A) and decide D2 (the production worker), and the CI job must
land in its own `gate/` PR (section 9). Spec: `STAR_PROMPT.md` §A.5. If it is deployed, the
site links can stay on `/status/` (Part A) or point at the subdomain; decide then.

## 1. Architecture

```
 browser ──► status.calendarharvest.com   (Vercel project #2, root dir status-page/)
               │  Next.js 15 App Router, server-rendered, revalidated every 60 s (ISR)
               │
               ├─► UptimeRobot API v2 getMonitors      read-only key, server-side env only
               │     6 external monitors (section 4): up/down now + 90 daily uptime values
               │
               └─► Vectorae API  GET /status/public   5 s timeout
                     the same checks as GET /health (app/services/health_checks.py)
```

- **Separate.** `status-page/` imports nothing from `frontend/` or `app/`. It has its own
  `package.json` (next, react, react-dom, yaml) and its own Vercel project. `.dockerignore`
  keeps it out of the API image.
- **Key stays on the server.** `UPTIMEROBOT_API_KEY` is read only by `lib/config.ts` and sent
  only in the POST body to UptimeRobot. Tests prove it is not in the snapshot, the HTML,
  `/api/status` or any client bundle (`tests/hygiene.test.ts`, `tests/snapshot.test.ts`; drill
  in section 8 checked the built `.next/static`).
- **Rate.** The home page and `/api/status` regenerate at most once a minute however many
  people visit, so UptimeRobot's free limit (10 requests/minute) is never approached. In
  drill (a) the served site answered about 10 page requests between 20:02:21 and 20:03:27 UTC
  and the stub UptimeRobot received one `getMonitors` call in that window (the 20:03:17
  regeneration); its other calls came from the two builds and one CLI drill run.
- **Without JavaScript.** Everything is server-rendered. JavaScript adds only the local-time
  tooltip on timestamps.

### Pages

| Path | What |
|---|---|
| `/` | Data notice (when a source is missing), overall banner, active incidents, 5 components with status icon + label, 90-day uptime bars (per-day tooltip; days below 100% listed as text), the last 14 days of incidents |
| `/incidents/<file name without .md>` | One incident with every update |
| `/history` | All incidents by month |
| `/history.rss`, `/history.atom` | Feeds, built at deploy time |
| `/api/status` | The home page's data as JSON (no key in it) |

Quality points: status by text and a distinct icon shape (check, triangle, half-filled square,
octagon with cross, clock, question mark), never colour alone; colours in `lib/palette.ts` are
tested for WCAG AA (4.5:1 text, 3:1 graphics) in light and dark mode
(`tests/palette.test.ts`); dark mode follows the OS setting; the layout uses a 16 px gutter and
flexible bars, so it fits 360 px; all times are UTC with a local-time tooltip.
**Lighthouse accessibility was not measured**: there is no browser in the build sandbox and
the Playwright browser download is outside the allowed registries. Run it on the Vercel
preview URL (section 10).

## 2. Components and what measures each

| Component | Normal source | When `/status/public` cannot be read |
|---|---|---|
| Website & app | UptimeRobot: Website + App login monitors. All up = Operational; one down = Partial outage; both down = Major outage; otherwise No data | same |
| API | Backend: DB `SELECT 1` + Redis PING. Both ok = Operational; Redis down = Partial outage; DB down = Major outage | "Vectorae - API" monitor: up = Operational, down = Major outage, else No data. Note: "Detail unavailable — status from external monitoring" |
| Background processing | Backend: Celery heartbeat age. ≤120 s = Operational; 120–300 s = Degraded; missing, unreadable or Redis down = Major outage | Keyword monitor up = Operational; worker monitor down, or API up but keyword down = Major outage; else No data |
| AI features | Backend: Anthropic Models API with the gateway key (no model call, no tokens billed). 200 = Operational; 429/5xx/529/timeout = Degraded; 401/403/404 or no key = Major outage | Never derived: No data |
| Booking pages | Backend: DB reachable and the 3 booking tables queryable = Operational, else Major outage. Also Major outage if the "Booking page" monitor is down | API monitor down or booking monitor down = Major outage; else No data |

The overall banner is the worst known status. If any component is No data and nothing is
worse than Operational, the banner reads "Some systems could not be checked" — never "All
systems operational".

**AI check blind spots.** The Models API does not use credit, so an exhausted credit balance
or a wrong `ANTHROPIC_MODEL` still reads Operational; those surface in the pipeline's
terminal-failure path. The probe result is cached 5 minutes per API process.

**Detection time.** Backend-detected problems: up to 30 s (endpoint cache) + up to 2 × 60 s
(page regeneration). A dead API: UptimeRobot's 5-minute interval + up to 2 × 60 s. If the
status site cannot reach the API but UptimeRobot can, the API shows Operational (derived,
labelled), because the independent check says it answers.

## 3. `GET /status/public` (backend)

`app/api/status_public.py`, registered in `app/main.py`. Unauthenticated. No auth middleware
exists in this app (auth is per-route), so nothing needed allow-listing. Callers: visitors'
browsers on www.calendarharvest.com/status/ (Part A; CORS via the existing `CORS_ORIGINS`, see
A2) and, if deployed, this app's server (no CORS involved).

```json
{"components":[
 {"id":"api","name":"API","status":"operational","checked_at":"2026-10-10T20:01:36Z"},
 {"id":"background-processing","name":"Background processing","status":"operational","checked_at":"2026-10-10T20:01:36Z"},
 {"id":"ai-features","name":"AI features","status":"major_outage","checked_at":"2026-10-10T20:01:36Z"},
 {"id":"booking-pages","name":"Booking pages","status":"operational","checked_at":"2026-10-10T20:01:36Z"}]}
```
(Real response from the local drill; AI is `major_outage` there because no Anthropic key was set.)

- Statuses: `operational | degraded | partial_outage | major_outage | maintenance`. The backend
  never emits `maintenance`; an incident file with `impact: maintenance` does.
- No error strings, hostnames, queue names, counts, ages or tenant data:
  `tests/test_status_public.py` asserts the exact key set and scans every failure-mode body for
  internal text.
- Cached in-process for 30 s (`Cache-Control: public, max-age=30`).
- Rate limit: `enforce_rate_limit` as the handler's first statement, per client IP,
  `RATE_LIMIT_PUBLIC_STATUS` (default 600/hour), same pattern as `/public/roi`.
- `GET /health` is unchanged. Its checks moved to `app/services/health_checks.py`; its exact
  response bytes are pinned by `TestHealthUnchanged` (9 tests), which passed against the original
  `health.py` before the move and again after.

## 4. UptimeRobot — what the founders do (optional; worth doing for either page)

Names must match exactly (case ignored): the site finds monitors by name.

**Current account (read 2026-10-11 through the UptimeRobot connector, read-only):**

| id | name | type | state |
|---|---|---|---|
| 804163676 | Vectorae API /health (keyword) | Keyword, alert if `"status":"ok"` does NOT exist | UP for 7 days |
| 804162778 | Vectorae API | HTTP | PAUSED (duplicate) |
| 804162687 | Vectorae API /health | HTTP | PAUSED (duplicate) |

**The active keyword monitor cannot detect the outage it was made for.** A degraded `/health`
body still contains `"status":"ok"` inside its healthy sub-components:
`{"status":"degraded","components":{"database":{"status":"ok"},...}}`. That is why it stayed UP
through the 2026-10-10 production degradation (`BASELINE.md`). The keyword must be the start of
the healthy body, which only the top-level status produces:

```
{"status":"ok","components"
```
`tests/test_status_public.py` pins that prefix (healthy body starts with it; degraded body does
not contain it).

This keyword belongs to the **`/health`** monitor. `GET /status/public` never contains it: its
body is `{"components":[...]}` with no top-level `status`, so a keyword monitor on
`/status/public` set to `{"status":"ok","components"` would be permanently DOWN. Keep the
keyword monitor on `/health` (steps below). If a monitor on `/status/public` is wanted too,
make it a plain HTTP(s) monitor (it answers 200 whenever the API is up; component detail is
for the status page, not for alerting).

Monitors run independently of both status pages, and they are the only thing that **alerts**
the founders: the Part A page only shows status to whoever opens it.

Steps:
1. **Delete** the two paused duplicates (804162778, 804162687).
2. **Edit** 804163676: rename to `Vectorae - API healthy`; keyword value
   `{"status":"ok","components"`; keep "alert when keyword does not exist", 5-minute interval.
   Expect it to go DOWN immediately while the worker is asleep — that is the truth (D2).
3. **Create** five monitors (5-minute interval, HTTP(s), follow redirects):

| Friendly name | Type | URL | Notes |
|---|---|---|---|
| `Vectorae - Website` | HTTP(s) | `https://calendarharvest.com/` | Marketing home |
| `Vectorae - App login` | HTTP(s) | `https://calendarharvest.com/login/` | Static export uses trailing slashes |
| `Vectorae - Booking page` | HTTP(s) | `https://calendarharvest.com/book/` | The public booking shell (`/book/<slug>` rewrites to it). Proves the page is served; the backend half is in `/status/public` |
| `Vectorae - API` | HTTP(s) | `https://leadpilot-api-ph3t.onrender.com/health` | Down on 503 (DB/Redis down) or no answer. 200 for "degraded", so this means "API answers" |
| `Vectorae - Worker` | HTTP(s) | `https://leadpilot-worker-708k.onrender.com/health` | **Needs D2 first.** The worker does expose HTTP: `render.yaml` runs it as a free web service, `scripts/render_worker_entrypoint.sh` starts `app/worker_health.py`, which answers 503 when a Celery child died. A 5-minute ping keeps the free worker awake (D2 option A) but uses ~730 of the 750 free instance-hours per month shared with the API (`BASELINE.md` §6, unverified against Render's current terms). If D2 = paid Background Worker, the service has no HTTP; skip this monitor and the page derives background status from the keyword monitor. |

4. **Read-only API key** (only needed for the optional Part B app; Part A does not use UptimeRobot): UptimeRobot dashboard → Integrations & API (older dashboards: My
   Settings → API Settings) → Read-Only API Key → create. Menu labels unverified; the key type is
   "read-only" (it can call `get*` methods only). Do not use the main or a monitor-specific key.
5. Do **not** create an UptimeRobot public status page; www.calendarharvest.com/status/ replaces it.
6. Optional: an HTTP(s) monitor `Vectorae - Status page` on `https://www.calendarharvest.com/status/`
   (proves the page itself is served; it is static, so it does not test the API).

## 5. Vercel — what the founders do (optional Part B only)

1. vercel.com → Add New → Project → import `Rmasood1122/LeadPilot` (same repo as the frontend;
   a second project).
2. **Root Directory: `status-page`**. Framework preset: Next.js. Build command, output and
   install: defaults (`npm install`/`npm ci`, `next build`). Node.js version: 22.x.
3. Environment variables (Production and Preview), **none prefixed `NEXT_PUBLIC_`**:

| Name | Value |
|---|---|
| `UPTIMEROBOT_API_KEY` | the read-only key from step 4 above (mark Sensitive) |
| `LEADPILOT_API_URL` | `https://leadpilot-api-ph3t.onrender.com` |
| `STATUS_SITE_URL` | `https://status.calendarharvest.com` (optional; this is the default, used in feeds) |

4. Deploy; open the preview URL; check: banner, five components, bars, `/history.rss`.
5. Settings → Domains → add `status.calendarharvest.com`. Vercel then shows the exact DNS record.
   For a subdomain it is normally **CNAME `status` → the target Vercel displays** (often
   `cname.vercel-dns.com`); use the value Vercel shows, not this one. Add it at the DNS host of
   `calendarharvest.com`. Leave the apex and existing records alone (email SPF/DKIM live there).
6. Optional: Settings → Git → Ignored Build Step so only `status-page/` changes rebuild it.
   (Incident files are inside `status-page/`, so they still deploy.)

## 6. Go-live checklist (optional Part B)

- [ ] D2 decided; worker monitor created or deliberately skipped (section 4).
- [ ] Monitors renamed/created, duplicates deleted, keyword fixed.
- [ ] Vercel project, env vars, domain, DNS (section 5); page loads on its domain.
- [ ] Lighthouse accessibility ≥ 95 on the live URL recorded here.
- [ ] `gate/` PR with the `verify-status-page` job merged (section 9).
- [ ] Decide whether the site links (`frontend/src/lib/statusPage.ts`, now `/status/`) stay on
      Part A or move to the subdomain, and update help-center article 30 to match.

## 7. Posting an incident (optional Part B)

For the primary page see A3. For this app: a pull request that adds or edits one file. Format and rules: `status-page/incidents/README.md`.

1. Branch, add `status-page/incidents/YYYY-MM-DD-<slug>.md` (date = UTC date of the first update).
2. `cd status-page && npm run incident-lint` (≈1 s).
3. Open the PR, merge with `scripts/gate/safe_merge.sh <PR>`. Vercel redeploys; the incident is
   on the page and in both feeds once the deploy finishes.
4. For each new update, append to `updates:` in the same file. Resolve with a final
   `status: resolved` update. Never rename the file (its URL is in feed readers).

An invalid incident file fails `incident-lint` and also fails `next build`, so a broken file can
never deploy silently. **Limitation:** an incident-only PR still runs the `gate` job of the
sealed `eval-gate.yml` (~9 minutes; it has no path filter and cannot be changed in a feature PR),
so an incident cannot be merged through `safe_merge` faster than that. The `verify-*` jobs
skip-but-succeed in under a minute except `verify-status-page` (~1–2 min, estimated) and
`verify-security` (~1 min).

## 8. Exit-gate 3 drills (performed locally, 2026-10-10 UTC)

Set-up: the real API (`uvicorn app.main:app`, SQLite with all tables, local Redis on 6390,
heartbeat key set by hand, no Anthropic key) on `127.0.0.1:8011`; a stub of UptimeRobot's
`getMonitors` on `127.0.0.1:9911` (via `UPTIMEROBOT_API_URL`); the status site built and served
with `next start` on port 3100. The stub is a drill aid kept outside the repo.

**(a) API stopped.** Baseline page: API, Background processing, Booking pages Operational
(source: backend); AI features Major outage (no key — correct). API killed at 20:02:37; the stub
then reported the API monitors down, as UptimeRobot would on its next check. Page polled every
10 s:

```
20:02:37    +0s api=operational  page_generated=20:02:10
...
20:03:17   +41s api=operational  page_generated=20:02:10   (this request triggered regeneration)
20:03:27   +51s api=major_outage bg=unknown booking=major_outage ai=unknown
                 notice='Detail unavailable'  page_generated=20:03:17
notes: 'Detail unavailable — status from external monitoring', 'Detail unavailable'
```
Outage shown 51 s after the stop: within two refresh cycles. AI features and Background
processing showed "No data" rather than Operational. Data layer alone (`npm run drill` with the
API down):
```
sources        uptimerobot=ok backend=unreachable
DATA NOTICE    Detail unavailable: Vectorae's own component checks could not be reached. ...
BANNER         Major outage
  Website & app          Operational           source=external
  API                    Major outage          source=external Detail unavailable — status from external monitoring
  Background processing  No data               source=none     Detail unavailable
  AI features            No data               source=none     Detail unavailable
  Booking pages          Major outage          source=external Detail unavailable — status from external monitoring
```

**(b) UptimeRobot key removed.** Rebuilt and served without `UPTIMEROBOT_API_KEY`: every route
200 (`/`, `/api/status`, `/history`, `/history.rss`, `/history.atom`), no error in the server log.
```
notice: Status data unavailable — External monitoring is not configured for this page, so uptime
        history is missing and the Website & app component cannot be checked. The other
        components show Vectorae's own checks.
Website & app = unknown (No data); API, Background processing, Booking pages = operational
(backend); AI features = major_outage (backend); uptime bars rendered: 0
```
With no env vars at all (the CI build), the page renders "Status data unavailable" and
"Current status unknown".

**(c) Incident file added.** A file marked "DRILL (not a real incident)" (impact `major`,
component `background-processing`, two updates) was added, linted (`ok`), built and served:
home page showed it under Active incidents and raised Background processing to Partial outage
with "Set by an open incident below"; `/incidents/2026-10-10-drill-…` rendered both updates and
the body; `/history` listed it; `/history.rss` had one `<item>` with title, permalink, pubDate
19:45 GMT and both updates; `/history.atom` had one `<entry>`. The file was then deleted. A
deliberately broken file made `incident-lint` exit 1 with five specific errors.

**Still needed for exit gate 3:** a Vercel preview URL, the same three drills against the real
UptimeRobot account and production API once the key and DNS exist (Part B only), the Lighthouse score, and founder
confirmation of DNS and D2.

## 9. CI job (its own `gate/` PR)

`.claude/hooks/protect_sealed.py` allows `.github/workflows/*` changes only on `gate/` or
`claude/gate-` branches, so the job is **not** in the `claude/status-page` diff. Add this job to
`.github/workflows/verify.yml` (before `verify-image`) in a `gate/verify-status-page` PR, then add
`verify-status-page` to `scripts/gate/required_checks.txt` once it has run green on `master`:

```yaml
  verify-status-page:
    if: github.event_name == 'push' || github.event.pull_request.draft == false
    runs-on: ubuntu-latest
    timeout-minutes: 15
    defaults:
      run:
        working-directory: status-page
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4.4.0
        with:
          fetch-depth: 0
      - name: Scope
        id: scope
        working-directory: .
        run: |
          if [ "${{ github.event_name }}" = "push" ]; then echo "run=true" >> "$GITHUB_OUTPUT"; exit 0; fi
          base="${{ github.event.pull_request.base.sha }}"
          if git diff --name-only "$base" HEAD | grep -qE '^status-page/'; then
            echo "run=true" >> "$GITHUB_OUTPUT"
          else
            echo "run=false" >> "$GITHUB_OUTPUT"
            echo "No status-page changes: skipped (success)." >> "$GITHUB_STEP_SUMMARY"
          fi
      - uses: actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020 # v4.4.0
        if: steps.scope.outputs.run == 'true'
        with:
          node-version: "22"   # 22.18+ runs the TypeScript tests and scripts natively
          cache: npm
          cache-dependency-path: status-page/package-lock.json
      - name: npm ci
        if: steps.scope.outputs.run == 'true'
        run: npm ci --no-audit --no-fund
      - name: Incident lint
        if: steps.scope.outputs.run == 'true'
        run: npm run incident-lint
      - name: Type-check
        if: steps.scope.outputs.run == 'true'
        run: npm run type-check
      - name: Unit tests (data layer, incidents, feeds, contrast)
        if: steps.scope.outputs.run == 'true'
        run: npm test
      - name: Production build (no env vars)
        if: steps.scope.outputs.run == 'true'
        run: npm run build
```
Same pinned SHAs and skip-but-succeed pattern as the existing jobs. Until it lands, nothing in CI
builds or tests `status-page/` (the `gate` job runs Python only).

## 10. Local commands

```bash
cd status-page
npm ci
npm run incident-lint      # incident files
npm run type-check         # next typegen && tsc --noEmit
npm test                   # node:test, offline (fake fetch)
npm run build              # must pass with no env vars
UPTIMEROBOT_API_KEY=... LEADPILOT_API_URL=https://leadpilot-api-ph3t.onrender.com npm run drill
```
There is no ESLint in `status-page/` (kept to 4 runtime dependencies); `tsc` runs in strict mode
with unused-code checks, and `npm run lint` = type-check + incident-lint.

`package.json` overrides `postcss` to 8.5.29: Next 15.5.27 pins postcss 8.4.31, which
`npm audit` reports as high (GHSA-qx2v-qp2m-jg93 and three source-map advisories), and npm's
only suggested fix is Next 16. postcss runs at build time on this project's own CSS, so the real
exposure was low; with the override the build passes and `npm audit --omit=dev` reports 0.
Remove the override when Next ships a fixed postcss.

## 11. Out of scope / follow-ups

- Email/SMS subscriptions (need double opt-in and unsubscribe handling).
- Scheduled-maintenance announcements ahead of time (an incident with `impact: maintenance`
  covers "in progress" only).
- UptimeRobot v2 is a legacy API ("no longer receiving updates"; no end date announced). Moving
  to v3 is a contained change in `lib/uptimerobot.ts`.
- ~~`GET /health` exposes raw exception text in its `celery.error` field (pre-existing, unchanged
  here on purpose; reported for triage).~~ Fixed 2026-10-11 (platform-gaps item 4): the field is
  now the fixed string `"heartbeat check failed"` and the exception is logged
  (`app/services/health_checks.py` `check_celery_heartbeat`); two `TestHealthUnchanged` byte pins
  were updated for that one field. See `docs/program/fixes/platform-gaps.md`.
