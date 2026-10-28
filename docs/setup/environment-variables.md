# Environment Variables

Canonical reference for every variable the Vectorae backend reads. Templates
live at `.env.example` (development) and `.env.production.example`.

**Never commit a real `.env`.** `.gitignore` excludes `.env` and `.env.*`, with
the two `*.example` templates as explicit exceptions.

> **Two settings modules exist.** `app/config.py` (`settings`) and
> `app/core/config.py` (`settings`) are both live, read different variables,
> and are *not* interchangeable. The table below names which one owns each
> variable. Patching the wrong one is a real trap — e.g. `RATE_LIMIT_AUTH`
> lives on `app.core.config` only, so setting `rate_limit_auth` on
> `app.config.settings` changes nothing.

---

## Email delivery — signup verification (Feature 1)

Owner: `app/config.py`. See
[`docs/features/email-verification.md`](../features/email-verification.md).

| Variable | Required | Default | Description |
|---|---|---|---|
| `EMAIL_PROVIDER` | **yes in production** | `console` | Which transport delivers mail. One of `resend`, `smtp`, `console`, `memory`. **Explicit on purpose** — never inferred from which keys happen to be set, so a misspelled key name fails loudly instead of silently logging every verification link. |
| `EMAIL_FROM` | **yes in production** | `noreply@calendarharvest.com` | From address. Must be on a domain **verified in Resend** (SPF + DKIM published) or messages are accepted and then dropped/spam-foldered. |
| `EMAIL_FROM_NAME` | no | `Vectorae` | Display name in the From header |
| `RESEND_API_KEY` | when `EMAIL_PROVIDER=resend` | *(empty)* | **Secret.** From the Resend dashboard. |
| `RESEND_API_URL` | no | `https://api.resend.com/emails` | Override only for testing against a mock |
| `SMTP_HOST` | when `EMAIL_PROVIDER=smtp` | *(empty)* | Mailtrap sandbox: `sandbox.smtp.mailtrap.io` |
| `SMTP_PORT` | no | `2525` | |
| `SMTP_USER` / `SMTP_PASSWORD` | when the relay requires auth | *(empty)* | **Secret.** |
| `SMTP_STARTTLS` | no | `true` | |
| `EMAIL_SEND_TIMEOUT_SECONDS` | no | `15.0` | Per-message transport timeout |
| `EMAIL_VERIFICATION_TTL_HOURS` | no | `24` | Link lifetime. 24 is the product requirement; it is a setting so expiry can be tested without waiting a day. |
| `PASSWORD_RESET_TTL_MINUTES` | no | `60` | Password-reset link lifetime (`app/services/password_reset.py`). The link goes to `FRONTEND_URL/reset-password`, so `FRONTEND_URL` must be the public web origin. |
| `REQUIRE_EMAIL_VERIFICATION` | no | `true` | **KILL SWITCH.** While true, `get_current_user` refuses every unverified account on every authenticated route. Set `false` to disable enforcement with a restart and no deploy. |

### Transport cheat-sheet

| Value | Delivers? | Use for |
|---|---|---|
| `resend` | yes, via HTTPS | production |
| `smtp` | yes, via SMTP | Mailtrap sandbox locally; any relay in production |
| `console` | **no** — logs the message | local dev with no credentials |
| `memory` | **no** — appends to an in-process list | the pytest suite |

`console` and `memory` are **rejected at startup** when `APP_ENV=production`
(`app/core/production_guard.py`). Both accept a send and deliver nothing, so
without that check signup would keep returning 201 while not one user ever
received a link — invisible to every health check.

---

## AI support chat (Feature 3)

See [`docs/features/ai-support-chat.md`](../features/ai-support-chat.md).

| Variable | Owner | Default | Description |
|---|---|---|---|
| `SUPPORT_CHAT_ENABLED` | `app/config.py` | `true` | **KILL SWITCH.** `false` makes `POST /support/chat` return 503 and the widget offer tickets only. The lever to pull if the assistant ever says something it should not. |
| `SUPPORT_CHAT_MIN_CONFIDENCE` | `app/config.py` | `0.5` | Below this the model's answer is **discarded** and a ticket offered. A hedged wrong answer still reads as an answer. |
| `SUPPORT_CHAT_MAX_TOKENS` | `app/config.py` | `1024` | Output ceiling per reply — a second line of defence against a runaway generation billed to your key. |
| `SUPPORT_CHAT_HISTORY_TURNS` | `app/config.py` | `6` | Prior turns replayed. History is re-sent on every message, so this is a direct multiplier on input cost. |
| `SUPPORT_CHAT_RETENTION_DAYS` | `app/config.py` | `30` | Chat history lifetime, enforced nightly at 03:20 UTC by `app.workers.support_tasks.purge_old_chats`. `0` disables it. Tickets are never purged. |
| `RATE_LIMIT_SUPPORT_CHAT` | **`app/core/config.py`** | `30` | Messages per user **per day**. Note the owner — that is the settings object `enforce_rate_limit` actually reads. |

`ANTHROPIC_API_KEY` and `ANTHROPIC_MODEL` are shared with the strategy
pipeline; the chat introduces no new credential.

---

## Application

Owner: `app/config.py`.

| Variable | Required | Default | Description |
|---|---|---|---|
| `APP_ENV` | yes | `development` | `development` \| `test` \| `production` (also `prod`, `live`). Production enables the startup guard. |
| `LOG_LEVEL` | no | `INFO` | |
| `ENABLE_DEBUG_ROUTES` | no | `false` | Mounts the test-only `/debug` router. **Never true in a deployed environment.** Deliberately separate from `APP_ENV` so no unrelated change can enable it as a side effect. |
| `ADMIN_EMAIL` | recommended | *(empty)* | Promotes an existing user to admin at startup. Without it a fresh database has no admin and no way to make one over HTTP. Promotes; never creates. |

## URLs

| Variable | Owner | Required | Default | Description |
|---|---|---|---|---|
| `PUBLIC_BASE_URL` | `app/config.py` | **yes in production** | `http://localhost:8000` | The public origin of **this API**. Baked into unsubscribe links, WhatsApp opt-in links **and verification links**. Left at localhost in production, every emailed link is dead. |
| `FRONTEND_URL` | `app/config.py` | **yes in production** | `http://localhost:3000` | Where `GET /auth/verify` redirects after doing its work. |
| `CORS_ORIGINS` | read directly in `app/main.py` | **yes in production** | `http://localhost:3000` | **Plain comma-separated list, NOT a JSON array.** `main.py` splits on commas, so brackets and quotes become part of the origin and never match a browser `Origin` header. |

## Secrets

| Variable | Owner | Required | Description |
|---|---|---|---|
| `SECRET_KEY` | `app/config.py` (aliased to `jwt_secret`) | **yes in production** | HS256 JWT signing key, ≥32 chars. Unset, each process generates its own random secret and every restart logs all users out. |
| `ENCRYPTION_KEY` | `app/core/crypto.py` | **yes in production** | Fernet key for stored OAuth tokens. **Lose it and every stored token is unreadable.** |
| `ENCRYPTION_KEY_PREVIOUS` | `app/core/crypto.py` | no | Previous key during rotation |

## Database and queue

| Variable | Required | Default | Description |
|---|---|---|---|
| `DATABASE_URL` | yes | local postgres | Neon **pooled** endpoint in production |
| `MIGRATION_DATABASE_URL` | production | falls back to `DATABASE_URL` | Neon **DIRECT** (non-pooled) endpoint. `pg_advisory_lock` is session-scoped and does **not** serialise through PgBouncer — measured 2026-08-20: a second migrator acquired a lock the first was holding. |
| `REDIS_URL` | yes | `redis://localhost:6379/0` | Upstash `rediss://` in production. Missing fails loudly. |
| `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` | no | fall back to `REDIS_URL` | Upstash needs `?ssl_cert_reqs=required` |

## Auth and rate limiting

| Variable | Owner | Default | Description |
|---|---|---|---|
| `JWT_ACCESS_TTL_SECONDS` | `app/config.py` | `900` | 15 min |
| `JWT_REFRESH_TTL_SECONDS` | `app/config.py` | `1209600` | 14 days |
| `RATE_LIMIT_AUTH` | **`app/core/config.py`** | `10` | Per 15-min window, on **two** independent keys: `ip:<addr>` and `acct:<email>`. Governs `/auth/login`, `/auth/signup` **and `/auth/resend-verification`**. |
| `RATE_LIMIT_GET`, `RATE_LIMIT_STRATEGIES`, `RATE_LIMIT_LEADS_SOURCE`, `RATE_LIMIT_WA_TEMPLATES`, `RATE_LIMIT_PLAYBOOK_RECOMPUTE` | `app/core/config.py` | see `.env.example` | |
| `RATE_LIMIT_SUPPORT_CHAT` | `app/core/config.py` | `30` | Per user per **day** — a cost ceiling on the Anthropic key |

## Outreach, integrations, compliance

Unchanged by Feature 1 — see `.env.example` for the full annotated list:
`ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`, `APOLLO_API_KEY`, `HUNTER_API_KEY`,
`GMAIL_CLIENT_ID` / `GMAIL_CLIENT_SECRET` / `GMAIL_REDIRECT_URI`,
`CALENDLY_WEBHOOK_SECRET`, `WHATSAPP_*`, `FIREBASE_*`, `SENDER_IDENTITY`,
`SEND_WINDOW_*`.

Sending limits (`app/config.py`): `GMAIL_DAILY_CAP` (default 100 per mailbox per
day), `GMAIL_WARMUP_START_SENDS` (10) and `GMAIL_WARMUP_DAILY_INCREMENT` (10 per
day since the mailbox was connected, up to the cap), `BOUNCE_RATE_PAUSE_THRESHOLD`
(0.03; a workspace compliance rule can only lower it) and `BOUNCE_MIN_SENDS` (10).
`GMAIL_DAILY_SEND_CAP`, `GMAIL_BOUNCE_PAUSE_THRESHOLD` and `GMAIL_WARMUP_START_DAILY`
are not settings: nothing reads them (removed from the examples 2026-10-11).

> `SENDER_IDENTITY` is the CAN-SPAM physical postal address in **outreach**
> mail. It is unrelated to `EMAIL_FROM`, which is the From address on
> **system** mail such as verification. Both are checked by the production
> guard, separately.

## Frontend

Owner: `frontend/next.config.js` + `frontend/src/lib/api/client.ts`.

| Variable | Required | Description |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | **yes at BUILD time** | The API origin, **compiled into the bundle** — this is a static export, so it cannot be changed by editing an env var afterwards. `next build` fails outright if it is unset *or* a loopback origin. On Render/Vercel this is a **build** variable. |
| `NEXT_PUBLIC_BUILD_TARGET` | no | `web` \| `mobile-dev` \| `mobile-prod` |

---

## Production startup guard

`app/core/production_guard.py` runs before the API serves traffic when
`APP_ENV` is `production`/`prod`/`live`, and **refuses to start** listing every
problem at once. It currently checks:

- `SECRET_KEY` — set, ≥32 chars, not a placeholder
- `ENCRYPTION_KEY` — set and a valid Fernet key
- `CORS_ORIGINS` — set, not a JSON array, not loopback
- `PUBLIC_BASE_URL` — set, not loopback
- `REDIS_URL` — set, not loopback
- `SENDER_IDENTITY` — set, not the placeholder address
- **`EMAIL_PROVIDER` / `RESEND_API_KEY` / `SMTP_HOST` / `EMAIL_FROM`** — a
  transport that actually delivers, with the credentials it needs

Verify locally before deploying:

```bash
APP_ENV=production python -c "from app.core.production_guard import validate_production_config as v; v(); print('production config OK')"
```

---

## Minimum production set

```
APP_ENV=production
SECRET_KEY=<48+ random chars>
ENCRYPTION_KEY=<Fernet key>
DATABASE_URL=<Neon POOLED>
MIGRATION_DATABASE_URL=<Neon DIRECT>
REDIS_URL=<Upstash rediss://>
CELERY_BROKER_URL=<Upstash rediss://?ssl_cert_reqs=required>
CELERY_RESULT_BACKEND=<same>
ANTHROPIC_API_KEY=<key>
PUBLIC_BASE_URL=https://<api-host>
FRONTEND_URL=https://<frontend-host>
CORS_ORIGINS=https://<frontend-host>
SENDER_IDENTITY=<real postal address>
ADMIN_EMAIL=<your address>

# Feature 1
EMAIL_PROVIDER=resend
RESEND_API_KEY=<key>
EMAIL_FROM=noreply@calendarharvest.com
EMAIL_FROM_NAME=Vectorae
EMAIL_VERIFICATION_TTL_HOURS=24
PASSWORD_RESET_TTL_MINUTES=60
REQUIRE_EMAIL_VERIFICATION=true
```

Frontend, at **build** time: `NEXT_PUBLIC_API_URL=https://<api-host>`
