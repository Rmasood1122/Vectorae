# Free-tier deployment — Render + Neon + Upstash + Vercel

A $0, no-credit-card path to a live deployment.

**This is additive.** `railway/*.json` and `DEPLOY_RAILWAY.md` remain the
documented paid-tier path and are untouched. Nothing here replaces them; if you
later move to Railway, that config still works.

| Piece | Provider | Free plan reality |
|---|---|---|
| Frontend | Vercel | Static export, no sleep |
| API | Render Web Service (Docker) | **Sleeps after 15 min idle**, 30–60s cold start |
| Worker + beat | Render Web Service (Docker) | **Sleeps after 15 min idle — needs an external pinger** |
| PostgreSQL | Neon | 0.5 GB, autosuspends |
| Redis | Upstash | 256 MB, 500k commands/month |

> Read **KNOWN LIMITATIONS OF THIS FREE STACK** at the bottom before relying on
> this for anything with a paying customer attached. The worker is the sharp
> edge.

---

## Part 1 — Render: the API service

`render.yaml` at the repo root is a Render **Blueprint**: point Render at the
GitHub repo and it creates both services from that file rather than from
dashboard clicks. Same reasoning as `railway/*.json` — start commands and
health-check paths live in a reviewed file, not in a text box nobody can diff.

The API builds the Dockerfile's named `production` stage.

### Verified before writing this

Built locally with `docker build --target production` on 2026-08-20 and
inspected the result. Three real defects surfaced; all are fixed, and all of
them would have broken the Railway deploy too:

1. **`app.main` did not import inside the image at all.** `structlog` is
   imported unconditionally by `app/core/logging.py`, which six `app/api/*`
   modules import — and it was never declared in `pyproject.toml`, so
   `pip install .` never installed it. The API could not boot in *any*
   container. It only worked locally because dev machines had structlog from
   somewhere else. `typer` and `rich` were missing the same way, which would
   have broken the documented `python -m app.cli.create_admin` step.
2. **No `.dockerignore` existed**, so `COPY . .` copied the whole working tree:
   `/code` was **877 MB** and contained `/code/.env` with the live
   `ANTHROPIC_API_KEY`, `SECRET_KEY` and `ENCRYPTION_KEY`. See the note under
   *Scope of the `.env` finding* below — it is narrower than it first looks,
   but it is still worth closing. After adding `.dockerignore`: **4.8 MB**, no
   `.env`, build context down from 213 MB+ to 22 kB.
3. **`app/workers/webhook_tasks.py` imported `services.webhook_delivery`** —
   there is no top-level `services` package; the module is
   `app/services/webhook_delivery.py`. Every *signed* outbound webhook raised
   `ModuleNotFoundError` and retried forever, while unsigned deliveries worked
   fine and hid it.

#### Scope of the `.env` finding

Render builds from a **git clone**, and `.env` is gitignored (verified: 398
tracked files, 3.6 MB, and `.env` has never been committed — the key does not
appear in any commit). So `.env` was never going to reach Render's build
context.

The exposure was for builds from a **local working tree**, which is exactly
what `docker-compose.prod.yml` does (`context: .`). Anyone running
`docker compose -f docker-compose.prod.yml build` produced an image with live
secrets baked into a layer. `.dockerignore` closes that, and as a bonus makes
local builds byte-for-byte comparable to what Render produces.

### Blueprint schema — three fields that Render rejects

The first Blueprint import failed with Render's generic *"A Blueprint file was
found, but there was an issue."* — no line number, no message. The YAML itself
was valid (confirmed with a local parser); the problem was three fields checked
against Render's current Blueprint spec:

| What we had | Why Render rejects it | Fix |
|---|---|---|
| `dockerTarget: production` | **No such field exists.** Build-stage targeting has been an open feature request since 2021 and is not in the spec. | Removed |
| `startCommand: …` | Spec: *"Docker-based services set the optional `dockerCommand` field instead of this field."* | `dockerCommand:` |
| `preDeployCommand: …` | Spec: *"The pre-deploy command is available for **paid** web services…"* — a paid-only feature on a `plan: free` service | Removed; migrations moved into the entrypoint script |
| `dockerCommand: sh -c "… && …"` | Render exited **127**, looking up the ENTIRE string as one program name — its tokenizer does not preserve nested quotes, so the shell never saw `&&` | Replaced with `bash scripts/render_api_entrypoint.sh` |

Removing `dockerTarget` costs nothing: **the Dockerfile has exactly one stage**,
`production`, which is also the final stage — so Render builds precisely the
image we wanted anyway.

Dropping `preDeployCommand` means migrations run at container start instead.
The race pre-deploy avoided is covered by the advisory lock in
`app/db/migrate.py` — and that lock only actually works when
`MIGRATION_DATABASE_URL` points at Neon's **direct** endpoint (see Part 3).

#### Keep every command field to bare tokens

The first attempt put the compound command inline:

```yaml
dockerCommand: sh -c "python -m app.db.migrate && uvicorn app.main:app …"
```

Render deployed that as **status 127**, reporting the whole string — flags,
`&&` and all — as a single missing program. Its tokenizer did not preserve the
nested double quotes, so no shell ever parsed the `&&`. The spec documents no
escaping rules for `dockerCommand`, and every example it gives is a single
operator-free command, so there is nothing to "get right" here.

**Both services therefore point at a script**, and every command field in
`render.yaml` is now two bare tokens with no quotes, `$`, `&&` or `;`:

| Service | `dockerCommand` |
|---|---|
| leadpilot-api | `bash scripts/render_api_entrypoint.sh` |
| leadpilot-worker | `bash scripts/render_worker_entrypoint.sh` |

`scripts/render_api_entrypoint.sh` uses `set -e` (a failed migration aborts the
boot, so a half-migrated schema never serves traffic) and `exec uvicorn` (so
uvicorn is PID 1 and Render's SIGTERM reaches it rather than a bash wrapper).

Verified locally against the built image with the real Neon and Upstash
credentials, running the exact `dockerCommand` Render runs:

```
[api-entrypoint] running database migrations
migrate: acquiring advisory lock … / lock acquired, upgrading to head
[api-entrypoint] migrations complete; starting uvicorn on 0.0.0.0:10000
INFO:     Application startup complete.
```

`/health` returned `database: ok, redis: ok`; `/proc/1/cmdline` confirmed
uvicorn is PID 1; and a deliberately broken `DATABASE_URL` exited **1** with
uvicorn never starting — the `&&` guarantee, proven rather than assumed.

Verified before re-import: the exact `dockerCommand` above was run against the
built image with the real Neon and Upstash credentials — advisory lock
acquired, migration to head, uvicorn up, `/health` reporting
`database: ok, redis: ok`.

### Environment variables — Render dashboard → leadpilot-api → Environment

Every name below is what the code actually reads (`app/config.py`; `app/core/config.py` now
re-exports it), not a guess.

| Variable | Value | Notes |
|---|---|---|
| `APP_ENV` | `production` | Already in `render.yaml`. Turns on `production_guard`. |
| `LOG_LEVEL` | `INFO` | Already in `render.yaml`. |
| `DATABASE_URL` | Neon connection string | See Part 3. |
| `REDIS_URL` | Upstash `rediss://…` | Cache, rate limiter, heartbeat. |
| `CELERY_BROKER_URL` | Upstash + `?ssl_cert_reqs=required` | See Part 3 — **the suffix is mandatory**. |
| `CELERY_RESULT_BACKEND` | same as broker | Same suffix. |
| `SECRET_KEY` | 48+ random bytes | `python -c "import secrets;print(secrets.token_urlsafe(48))"` |
| `ENCRYPTION_KEY` | Fernet key | `python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"` |
| `ANTHROPIC_API_KEY` | your key | |
| `CORS_ORIGINS` | `https://your-app.vercel.app` | **Comma-separated, never a JSON array.** |
| `PUBLIC_BASE_URL` | `https://leadpilot-api.onrender.com` | Builds unsubscribe links (CAN-SPAM). |
| `FRONTEND_URL` | your Vercel URL | |
| `SENDER_IDENTITY` | real postal address | CAN-SPAM footer. A placeholder is rejected at boot. |
| `ADMIN_EMAIL` | your email | Promotes an existing user; does not create one. |

`app/core/production_guard.py` refuses to boot when `APP_ENV=production` and
any of `SECRET_KEY`, `ENCRYPTION_KEY`, `CORS_ORIGINS`, `PUBLIC_BASE_URL` or
`SENDER_IDENTITY` is missing or unsafe, and reports every problem at once.
Low-entropy values (`xxxx…`) are rejected too.

**First-deploy admin sequence** (unchanged from Railway): deploy with
`ADMIN_EMAIL` set → sign up once with that address → restart the api service.

---

## Part 2 — Render: Celery worker + beat in ONE free service

Render's free plan has **no free background-worker type** — `type: worker` is
paid-only. A free Web Service must bind `$PORT` and answer HTTP. So the worker
service runs three processes:

```
scripts/render_worker_entrypoint.sh
  ├── celery worker -Q pipeline,outreach,learning,default   (background)
  ├── celery beat                                            (background)
  └── uvicorn app.worker_health:app --port $PORT             (background)
      └── wait -n  →  if ANY child exits, kill the rest and exit non-zero
```

`app/worker_health.py` serves exactly one route and no application traffic. The
real API is deliberately **not** mounted there — that would publish all ~97
routes on a second origin whose CORS, rate limits and admin surface nobody
reasoned about.

### The `-Q` list is load-bearing

`-Q pipeline,outreach,learning,default` lists every queue
`app/workers/celery_app.py` routes to. Omitting one strands every task routed
to it, with no error anywhere. That exact bug — nothing consuming the queue
tasks were published to — was a total production outage found in session
update 9 of `CLAUDE_CODE_HANDOFF.md`. **If you add a route to `task_routes`,
add its queue here.**

### Two failure modes, and which one is handled

| Failure | Handled? |
|---|---|
| Worker process crashes | **Yes.** `wait -n` exits the service → Render restarts it. `/health` also returns 503 (zombie-aware) as a backstop. |
| Service asleep from no traffic | **No — this is on you.** See the pinger below. |

Verified live on 2026-08-20 against the built image: worker reported
`celery@… ready` bound to all four queues, `/health` returned
`{"worker":{"status":"ok"},"beat":{"status":"ok"}}`, a `leadpilot.ping`
round-tripped through the container (`received` → `succeeded: 'pong'` in its own
log, with no other worker running), and `kill -9` on the worker made the
container exit 137 instead of sitting there looking healthy.

The zombie check matters: an earlier run had both Celery processes dead on the
missing-`structlog` error while `/health` cheerfully reported both `ok`,
because `os.kill(pid, 0)` succeeds for an unreaped process. `/health` now reads
`/proc/<pid>/stat` and treats state `Z` as down.

### Environment variables — leadpilot-worker

**Every variable from the API table above, with identical values**, plus
`CELERY_CONCURRENCY=2` (already in `render.yaml`; free instances are 512 MB and
each Celery child is a full Python process).

A first-deploy failure here is almost always a variable set on the api and
forgotten on the worker — the production guard runs on both.

---

## Part 2b — THE PINGER IS REQUIRED, NOT OPTIONAL

> A free Render Web Service is spun down after **~15 minutes** with no inbound
> HTTP request. **A sleeping service runs no processes.** The Celery worker is
> not paused — it does not exist. Queued tasks are simply not consumed.
>
> **Nothing errors. No alert fires.** Queue depth in `/admin/celery-stats`
> grows and every dashboard stays green. If the pinger stops, task processing
> stops silently.
>
> This is an accepted trade-off of running for $0, **not a bug to fix in
> code.** The fix is $7/mo (below).

### Set this up at cron-job.org (you, in a browser)

1. Create a free account at <https://cron-job.org>.
2. **Create cronjob** →
   - **URL**: `https://leadpilot-worker.onrender.com/health`
     *(your worker service's URL — the worker, not the api)*
   - **Schedule**: every **10 minutes** (under the 15-minute sleep threshold
     with margin for a missed run)
   - **Enable "Notify on failure"** — you will then hear about a dead worker,
     since `/health` returns 503 when the Celery children are gone.
3. Optionally add a second job against the **api**'s `/health` to keep
   first-request latency down. Cosmetic, not correctness.

#### The instance-hour arithmetic — read before choosing a ping schedule

Render gives **750 free instance hours per calendar month, shared across the
whole workspace**, and a service consumes them **only while awake**
(*"spun-down services don't consume Free instance hours"*).

A calendar month is about **730 hours**. So:

| Setup | Hours/month | Fits in 750? |
|---|---|---|
| Worker pinged 24/7 | ~730 | Yes, but uses **97%** of the allowance |
| Worker + API both awake 24/7 | ~1,460 | **No — nearly double** |
| Worker pinged 12h/day + API on real traffic | ~365 + usage | Comfortably |

**Pinging the worker around the clock consumes almost the entire monthly
allowance and leaves roughly 20 hours for the API.** When the 750 are gone,
*"Render suspends all of your Free web services until the start of the next
month"* — the whole stack goes down, not just the worker.

So do **not** ping both services 24/7. Pick one:

* **Recommended:** ping the worker only during the hours you actually expect
  work — e.g. 08:00–20:00 gives ~365 h/month and leaves ~385 h for the API.
  cron-job.org supports restricting a job to a time window.
* Or ping the worker 24/7 and accept that the API is effectively unavailable
  once the allowance runs out.
* Or pay the $7/mo for a Background Worker, whose hours are not drawn from the
  free pool at all.

This is the single biggest planning constraint of the free stack, and it is
arithmetic, not opinion.

---

## Part 3 — Neon (Postgres) and Upstash (Redis)

Both verified live on 2026-08-20 against the real providers.

### Which env var name goes where

Read from `app/config.py` (`app/core/config.py` re-exports it) — these are the names the
code actually reads, not guesses.

| Provider value | Env var(s) | Required suffix |
|---|---|---|
| Neon connection string | `DATABASE_URL` | `?sslmode=require` |
| Neon **direct** (non-pooled) string | `MIGRATION_DATABASE_URL` | `?sslmode=require` |
| Upstash TCP string | `REDIS_URL` | none — bare `rediss://` |
| Upstash TCP string | `CELERY_BROKER_URL` | **`?ssl_cert_reqs=required`** |
| Upstash TCP string | `CELERY_RESULT_BACKEND` | **`?ssl_cert_reqs=required`** |

`settings.broker_url` falls back to `redis_url` when `CELERY_BROKER_URL` is
unset — so setting only `REDIS_URL` would hand Celery a bare `rediss://` and
break the worker at boot. Set all three explicitly.

### Why Celery needs `?ssl_cert_reqs=required`

Not a guess — reproduced directly. Celery's Redis **result backend** refuses a
bare `rediss://`:

```
ValueError: A rediss:// URL must have parameter ssl_cert_reqs and this must be
set to CERT_REQUIRED, CERT_OPTIONAL, or CERT_NONE
```

With the suffix it resolves to `SSLConnection` / `ssl_cert_reqs=2`
(`CERT_REQUIRED`), which is what you want — certificates verified.

The **broker** tolerates a bare `rediss://`, and so does `redis-py`, which is
why plain `REDIS_URL` needs nothing. Only the result backend is strict.

### Neon: `sslmode=require` needs no code change

`app/db/base.py` passes the URL straight to `create_engine`, psycopg2 handles
`sslmode` natively, and `pool_pre_ping=True` was already set — which is what
makes Neon's autosuspend survivable rather than a source of dead connections.

`pg_stat_ssl` reports `ssl = false` on Neon. That is a reporting artifact of
Neon terminating TLS at its proxy, **not** an unencrypted connection —
confirmed by attempting `sslmode=disable`, which Neon **refuses**. TLS is
mandatory on the wire.

### Migrations must NOT run through the Neon POOLER

The single most important finding of Part 3.

`app/db/migrate.py` serialises concurrent migrators with
`pg_advisory_lock`, which is **session-scoped**. Neon's pooled endpoint
(`…-pooler…`) is PgBouncer in transaction mode: it hands each statement
whatever server connection is free, so the lock is taken on one backend and
the next statement runs on another.

Measured against the live database, two migrators behaving exactly as
`migrate.py` does:

| Endpoint | Second migrator's `pg_try_advisory_lock` | Verdict |
|---|---|---|
| Pooled (`-pooler`) | **acquired the lock A was holding** | lock does nothing |
| Direct (no `-pooler`) | correctly refused | works |

So the race protection added in session update 11 is silently inert on the
pooled endpoint. Fix: set **`MIGRATION_DATABASE_URL`** to Neon's *direct*
connection string while `DATABASE_URL` stays pooled for normal traffic.
`app/db/migrate.py::_migration_url()` prefers it and passes it through to
Alembic, so the lock and the migration act on the same endpoint. Unset, the
behaviour is exactly as before.

Neon's own guidance matches: direct connection for migrations and DDL, pooled
for application traffic.

### Verified results

```
Neon PostgreSQL       connect OK   server_version 18.6   database neondb
Upstash (broker)      connect OK   transport rediss
Upstash (backend)     PING PONG    SSLConnection   ssl_cert_reqs=2
Upstash (app client)  PING PONG    SSLConnection
```

Alembic chain applied clean on the first run — all 17 revisions,
`0001_initial_models` → `0014_strategy_pattern_inputs`, exit 0, advisory lock
acquired and released. Resulting schema: **26 tables, 85 indexes, 25 foreign
keys**, and zero model tables missing versus `Base.metadata`.

## Part 4 — Vercel (frontend)

The API went live at `https://leadpilot-api-ph3t.onrender.com` on 2026-08-20
(`/health` 200 in 0.89s, `database: ok`, `redis: ok`).

### Static export stays — do not switch to Vercel's Next.js runtime

`next.config.js` sets `output: 'export'` with `trailingSlash: true` and
`images.unoptimized`. That is **required by the Capacitor mobile build**
(`npm run mobile:build` runs `next build && cap sync`, which needs a real `out/`
directory of static files). Switching to Vercel's server runtime would break
the Android path, so Vercel serves the exported static site instead. No feature
is lost here — the app has no SSR, no API routes and no ISR; it is a pure SPA
talking to the Render API.

### Vercel project settings

| Setting | Value | Why |
|---|---|---|
| Framework Preset | **Next.js** | Auto-detected. |
| **Root Directory** | **`frontend`** | The Next app is not at the repo root. Settings → Build and Deployment → Root Directory → Save. |
| Build Command | *leave default* | Vercel uses the `build` script, which is exactly `next build`. |
| Output Directory | *leave default* | *"If Vercel detects a framework, the output directory will automatically be configured."* With `output: 'export'` Next writes `out/` and the Next.js preset picks it up. If a deploy ever serves a blank page, override it to `out` — that is the only fallback needed. |
| Install Command | *leave default* | |
| Node.js Version | default | `package.json` pins no `engines`; Next 14.2.35 is fine on Vercel's default. |

### The one environment variable

| Name | Value | Environments |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | `https://leadpilot-api-ph3t.onrender.com` | Production, Preview, Development |

Set it under Project Settings → **Environment Variables** *before* the first
deploy. It is **baked in at build time**, not read at runtime, so changing it
later requires a redeploy, not just a restart.

`frontend/.env` (which holds the localhost value locally) is **gitignored and
untracked**, so Vercel never sees it. That means an unset variable on Vercel is
genuinely unset — and the build guard fails loudly rather than silently baking
a broken origin.

### Verified locally before any of the above was written

Built with the real Render URL and inspected the output:

* URL baked into **8 bundle files** as `a="https://leadpilot-api-ph3t.onrender.com"` — correct scheme, no trailing slash.
* **Zero** files containing `localhost:8000` or `127.0.0.1:8000`.
* 20 HTML pages, `out/index.html` present, 2.2 MB total, `<title>Vectorae</title>`, manifest name `Vectorae`.
* Served the export over HTTP: `/`, `/login/`, `/strategies/`, `/settings/` all 200.

Build guard exercised in all three states:

| `NEXT_PUBLIC_API_URL` | Result |
|---|---|
| the real Render URL | builds, URL baked in |
| `http://localhost:8000` | `BUILD FAILED: … is a local origin` |
| unset (with `frontend/.env` moved aside, i.e. Vercel's situation) | `BUILD FAILED: … is not set` |

### Live domain and the env vars that actually matter

Production frontend: **`https://lead-pilot-coral.vercel.app`** (live 2026-08-20;
the deployed bundle was confirmed baked with the Render API URL).

Traced through the code rather than assumed — the three variables do NOT all
belong on both services:

| Variable | leadpilot-api | leadpilot-worker | Why |
|---|---|---|---|
| `CORS_ORIGINS` | **REQUIRED** — the Vercel domain | **not used** | `app/main.py:101` reads it via `os.getenv(...).split(",")`. The worker runs `app.worker_health`, which has no CORS middleware and never imports `app.main`. |
| `PUBLIC_BASE_URL` | the **API's own** URL | **REQUIRED** — the API's own URL | `app/services/sequence_engine.py:383` builds every unsubscribe link from it, and that runs on the worker's send path. A wrong value puts a dead link in every outbound email (CAN-SPAM). Also drives `/media/*` and WhatsApp opt-in links. **Never** the Vercel domain. |
| `FRONTEND_URL` | **REQUIRED** — the Vercel domain | **REQUIRED** — the Vercel domain | `settings.frontend_url` (`app/config.py`) builds every link that opens the web app. **API:** email verification and password-reset links (`app/api/auth.py`, `app/services/password_reset.py`), workspace invites, ROI share links, OAuth consent, billing and integration return pages, MCP links. **Worker:** links that go out in sends — the booking link in reply drafts (`reply_copilot.py`), the Loom video page in outreach (`loom_video.py` via `message_personalization.py`), pilot mails and notification links (`event_bus.py`); `message_personalization.py` also treats this host as our own when it strips foreign links. Unset, it defaults to `http://localhost:3000`, so every one of those links lands on the recipient's own machine and nothing fails at boot. (This row said "inert" before 2026-10-26; that was wrong.) |

**Correction to an earlier claim in this file:** it previously said
"production_guard runs on the worker too". It does not —
`validate_production_config()` is called only from `app/main.py:74`, and the
worker never imports that module. The worker still needs `PUBLIC_BASE_URL` for
the reason above, but it will not refuse to boot without it.

`CORS_ORIGINS` is a **plain comma-separated string** — never a JSON array, never
quoted. `app/main.py` splits on commas and unions the result with the Capacitor
origins.

### REDIS_URL must be set on the WORKER too — and it fails silently if not

Symptom seen on 2026-08-20: the worker deployed cleanly, its logs showed
`[entrypoint] starting Celery beat` and `Your service is live`, and yet the
API's `/health` sat at `celery: {"status":"degraded","reason":"no heartbeat key
found"}` indefinitely. Both services independently reported `redis: ok`.

**Why "redis: ok" on both sides proves nothing.** It only proves each process
can reach *a* Redis — not the *same* one.

The trap, confirmed in code:

* Celery reads **`CELERY_BROKER_URL`** / **`CELERY_RESULT_BACKEND`**.
* Everything else — the beat heartbeat, the response cache, the circuit
  breakers, the auth rate limiter — goes through
  `app/core/redis_client.py::get_sync_redis()`, which reads **`REDIS_URL`**.
* `_redis_url()` returns `settings.redis_url`, and `app/config.py` gives that a
  **non-empty default of `redis://localhost:6379/0`**. So the `if url:` check
  is satisfied and the `os.getenv` fallback never runs.

Net effect: **an unset `REDIS_URL` does not fail.** The process connects to a
localhost Redis that does not exist inside the container. Celery is completely
unaffected, so beat keeps scheduling the heartbeat every 60 seconds and every
write fails — while the deploy, the logs and the worker's own health endpoint
all look fine.

`REDIS_URL`, `CELERY_BROKER_URL` and `CELERY_RESULT_BACKEND` are three separate
`sync: false` entries in `render.yaml`, prompted **per service**. Setting them
on the api does nothing for the worker. Render does not share environment
variables between services unless you use an env group.

Two code changes now make this loud instead of silent:

1. `app/worker_health.py` reports a **`redis`** component alongside `worker` and
   `beat`, including whether the heartbeat key is present. Previously it checked
   PIDs only — which is exactly why two live-but-useless Celery processes read
   as healthy.
2. `app/core/production_guard.py` rejects an unset or loopback `REDIS_URL` in
   production. (API only — the worker does not run the guard; change 1 covers it.)

**Diagnosing this class of problem:** connect to the Redis you *believe* both
services use and run `DBSIZE`. A Celery-connected Redis is never empty — the
broker creates `_kombu.binding.*` keys on connection. An empty database means
nothing is talking to it.

### If a service returns a bare 502: check its Docker Command first

`leadpilot-worker` returned a consistent 502 (0.7–2.5s, so not a cold start)
while the api was fine. Reproduced locally: **a service whose Docker Command is
empty falls back to the Dockerfile's `CMD`.** That used to hardcode port 8000,
so the container bound 8000 while Render routed to `$PORT` — a bare 502 with
nothing useful in the logs — and it ran `app.main` (the API) instead of Celery,
so no task was ever consumed and no beat heartbeat was ever written.

Two things came out of that:

1. **The Dockerfile `CMD` now honours `$PORT`**
   (`uvicorn … --port ${PORT:-8000}`), so a service that loses its command
   answers on the right port instead of failing invisibly. The 8000 fallback
   keeps docker-compose working unchanged. Verified: with `PORT=10000` and no
   command override the container now reports
   `Uvicorn running on http://0.0.0.0:10000` and `/health` returns 200.
2. **That is a safety net, not the fix.** A worker running the API is still the
   wrong process. Confirm in Render → **leadpilot-worker → Settings → Deploy →
   Docker Command** that it reads exactly:

   ```
   bash scripts/render_worker_entrypoint.sh
   ```

   A Blueprint sync does not always overwrite a setting on an already-created
   service, so this can silently stay empty after the yaml is corrected.

Note that a first boot against Neon takes roughly **60 seconds** before
`Application startup complete` appears — measured locally. Do not judge a
deploy failed at 20 seconds.

### Env var changes need a deploy, not just a save

Render's save dialog offers three choices:

* **Save, rebuild, and deploy** — full rebuild. Only needed if code changed.
* **Save and deploy** — redeploys the existing build. **Use this** for an env-var-only change.
* **Save only** — *"Your service will not use the new variables until its next deploy."* The change silently does nothing.

`CORS_ORIGINS` is read at **module import time**, so a running process never
picks up a new value — the service must actually restart.

### Historical note on the first placeholder

`CORS_ORIGINS` was initially set to a guessed `https://leadpilot.vercel.app`;
Vercel assigned `lead-pilot-coral.vercel.app` instead. Worth recording that the
guessed value was verified **not** to be a wildcard — `evil.example.com`,
`attacker.test` and `random-9f2k.vercel.app` were all refused while only the
configured origin was echoed. That matters because the API sends
`access-control-allow-credentials: true`, where echoing any origin would be a
serious hole.

---

## KNOWN LIMITATIONS OF THIS FREE STACK

Read this before pointing a customer at it.

### 1. Worker depends on an external pinger — highest risk
No pinger, no task processing, no error. Everything upstream keeps accepting
work. This is the single most fragile part of the stack.

**Fix first, before any other paid upgrade: move the worker to a Render
Background Worker at $7/mo.** It never sleeps, needs no pinger, needs no health
server, and removes an entire failure mode. Do this the moment there is any
revenue.

### 2. API cold starts: 30–60 seconds
After 15 minutes idle the first request hangs while the container boots. A
visitor hitting the site cold may think it is broken. Strategy generation is a
~60-minute pipeline, so the cold start is irrelevant *once work is queued* —
it is a first-impression problem, not a throughput one.

### 3. Neon free tier
0.5 GB storage and the compute autosuspends after ~5 minutes idle, adding a
few hundred ms to the first query. `create_engine(..., pool_pre_ping=True)` is
already set in `app/db/base.py`, which is what makes a dropped idle connection
reconnect instead of erroring — this stack would be flaky without it.

### 4. Upstash free tier
256 MB and **500,000 commands/month**. Celery polls the broker continuously;
an always-awake worker can burn through that. Ironically the 15-minute sleep
helps here. Watch the Upstash dashboard in week one.

### 5. No horizontal scale, no persistent disk
One instance each. Render's free disk is ephemeral — anything written to it is
gone on restart, which is why beat's schedule lives in `/tmp` and is rebuilt
from code on boot. Uploaded theme backgrounds (`media/`) will **not** survive a
restart; that needs object storage before it is a real feature.

### 6. Migrations run from the api service only
`preDeployCommand: python -m app.db.migrate` is on the api, not the worker, so
they cannot race. It is wrapped in a `pg_advisory_lock` regardless (session
update 11: 3 of 4 concurrent migrators died without it).

### BILLING SAFETY — what a card on file changes

You added a card because Render required one for identity verification. That
card changes Render's overage behaviour for **one** of the two limits. Both
figures below are from Render's own docs.

| Limit | Amount | Exceeded WITHOUT a card | Exceeded WITH a card |
|---|---|---|---|
| Free instance hours | **750 / month / workspace** | Free services suspended until next month | **Same — suspended.** Render's wording is unconditional: *"If you consume all of your Free instance hours during a given month, Render suspends all of your Free web services until the start of the next month."* |
| Outbound bandwidth | **100 GB / month** | *"Render instead suspends all of your Free services for the remainder of the month"* | **BILLED** — *"Render bills you for a supplementary amount"*, at **$0.15/GB** |

**So the one and only way this stack can charge the card is exceeding 100 GB of
outbound bandwidth in a month.** Instance-hour exhaustion suspends; it does not
bill.

How realistic is 100 GB? Render serves only the JSON API here — the frontend is
on Vercel and does not touch this allowance. The keep-alive pinger sends a few
hundred bytes every 10 minutes (~4,300 requests/month, a rounding error).
Reaching 100 GB of API JSON would take roughly a million heavy responses. Under
normal use this is not a realistic risk. The realistic paths to it are abnormal:
a scraping loop, a runaway client retrying forever, or a DDoS.

**There is no account-wide spending cap.** Render offers a spend limit only for
build-pipeline minutes (Workspace Settings → Build Pipeline → *Set spend
limit*); an account-level limit covering bandwidth is still an open feature
request. Render does send email warnings as you approach and exceed a limit.

#### What to check in the dashboard yourself

1. **Usage**: Render Dashboard → your **Workspace** → **Billing** →
   *Monthly Included Usage*. This shows instance hours and bandwidth consumed
   against the included amounts. Check it in week one, then monthly.
2. **Build pipeline spend limit**: Workspace Settings → Build Pipeline →
   *Set spend limit*. Set it to the minimum. It does not cover bandwidth, but
   it closes the one capped-spend surface Render does expose.
3. **Confirm the workspace is on the free/hobby plan**, not a trial that lapses
   into a paid plan.
4. **Verify email notifications are on** so the usage warnings actually reach
   you.
5. If you ever want the hard guarantee back: **removing the payment method**
   restores "suspend" behaviour for bandwidth too. Only do that if Render lets
   you remove it after verification — it may not.

`render.yaml` itself is audited clean: both services are `plan: free`, there is
no `disk:`, no `numInstances`, no `type: worker`/`pserv`/`cron`, and the one
paid-only field that was present (`preDeployCommand`) has been removed. Nothing
in the file can select a paid resource.

### Upgrade order, when money exists
1. **Render Background Worker — $7/mo.** Removes the pinger dependency.
2. Render API to a paid instance — removes cold starts.
3. Neon / Upstash paid tiers — only when you actually hit the caps.
