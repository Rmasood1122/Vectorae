# Email Verification on Signup (Feature 1)

Status: **implemented, verified locally, and committed** (`e602e6b`).
Live send, domain verification and the production migration are **DEFERRED by
decision** until real credentials exist — see [Outstanding](#outstanding).

---

## What it does

A new account cannot use Vectorae until the person behind it proves they
control the email address.

1. `POST /auth/signup` creates the account with `users.email_verified = false`
   and emails a single-use link.
2. Every authenticated endpoint answers **403 `EMAIL_NOT_VERIFIED`** until that
   link is clicked. The frontend routes such a user to `/check-email`.
3. `GET /auth/verify?token=…` marks the account verified and **302-redirects**
   to `{FRONTEND_URL}/login?verified=true`.
4. The link expires after **24 hours**. `POST /auth/resend-verification` issues
   a new one and kills the old one.

Accounts that existed **before** this feature were backfilled to verified by
migration `0015_email_verification`. Nobody who already had an account was
locked out.

---

## Design decisions worth knowing

| Decision | Why |
|---|---|
| The gate lives in `get_current_user` **only** | Every authenticated route resolves its user through that one dependency (`app/api/deps.py` re-exports it rather than reimplementing). One chokepoint covers routes added later that nobody remembered to guard. The alternative — a dependency per router — is ~30 files and ~30 chances to miss one, and a miss is a silent hole. |
| **403**, not 401 | The credentials are valid. A 401 sends `frontend/src/lib/api/client.ts` into refresh-then-retry, which succeeds at refreshing, retries, gets 401 again, and clears the session — logging out a user whose only problem is an unread email. |
| Signup still returns tokens | They cannot do anything (the gate refuses every route), but they let `/check-email` exist as a signed-in page and mean the user is already signed in the instant the link is clicked — no second login. |
| **Login succeeds** while unverified | Otherwise there is no session from which to press "resend". The routing decision is made client-side in `login/page.tsx`. |
| Only `sha256(token)` is stored | A verification link is a bearer credential. A leaked database dump must not hand an attacker a working link for every pending signup. The raw value exists only in the email and in the click. |
| Issuing a token burns the previous ones | Five "resend" clicks would otherwise leave five independently valid links alive for 24 hours each. |
| The link points at the **API**, not the frontend | `frontend/next.config.js` sets `output: 'export'` — a static export with no server, no middleware and no route handlers. It physically cannot receive a token, call the API and redirect. |
| A dead mail relay does **not** fail signup | Failing with a 500 would destroy an account the user just created: the address is now taken, the password is lost, and the signup is unrepeatable. The response carries `verification_email_sent: false` and the UI leads with "resend". |
| `REQUIRE_EMAIL_VERIFICATION` kill switch | This is the highest-blast-radius line in the feature. If it ever wrongly locks real users out of production, the fix must be an env var and a restart — not a code change and a deploy. |

---

## Files

**New**

| File | Purpose |
|---|---|
| `alembic/versions/0015_email_verification.py` | Schema + backfill |
| `app/services/email_sender.py` | The only transactional-email gateway. 4 transports. |
| `app/services/email_verification.py` | Issue / send / consume tokens |
| `frontend/src/app/(auth)/check-email/page.tsx` | "Check your email" + resend |
| `tests/test_email_verification.py` | 28 tests |

**Modified**

| File | Change |
|---|---|
| `app/config.py` | Email settings + `frontend_url` + kill switch |
| `app/db/models.py` | `users.email_verified`, `users.email_verified_at`, `EmailVerificationToken` |
| `app/services/auth.py` | The gate in `get_current_user`; `EMAIL_NOT_VERIFIED` constant |
| `app/api/auth.py` | Signup sends mail; `GET /auth/verify`; `POST /auth/resend-verification` |
| `app/core/production_guard.py` | Refuses to boot production that cannot send mail |
| `frontend/src/lib/api/auth.ts` | `resendVerification`, `isUnverifiedError`, signup returns the bundle |
| `frontend/src/lib/api/types.ts` | `email_verified` on `UserOut` |
| `frontend/src/app/(auth)/signup/page.tsx` | Redirects to `/check-email`, not `/pipeline` |
| `frontend/src/app/(auth)/login/page.tsx` | Banner for verify outcomes; routes unverified users |
| `frontend/src/components/shell/Shell.tsx` | Gate for bookmarked/second-tab dashboard loads |
| `tests/conftest.py` | `mailbox` fixture, verification helpers, verified canonical user |
| `tests/test_m5_backend.py`, `tests/test_production_guard.py` | Updated for the new behaviour |

---

## Environment variables

Full reference: [`docs/setup/environment-variables.md`](../setup/environment-variables.md).

| Variable | Default | Notes |
|---|---|---|
| `EMAIL_PROVIDER` | `console` | `resend` \| `smtp` \| `console` \| `memory`. **Production must be `resend` or `smtp`.** |
| `EMAIL_FROM` | `noreply@calendarharvest.com` | Domain must be verified in Resend |
| `EMAIL_FROM_NAME` | `Vectorae` | |
| `RESEND_API_KEY` | *(empty)* | Required when `EMAIL_PROVIDER=resend` |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` / `SMTP_STARTTLS` | Mailtrap sandbox host, `2525`, empty, empty, `true` | Required when `EMAIL_PROVIDER=smtp` |
| `EMAIL_VERIFICATION_TTL_HOURS` | `24` | |
| `REQUIRE_EMAIL_VERIFICATION` | `true` | **Kill switch.** `false` disables enforcement platform-wide. |
| `PUBLIC_BASE_URL` | `http://localhost:8000` | The origin baked into the link. Must be the **public API origin**. |
| `FRONTEND_URL` | `http://localhost:3000` | Where `/auth/verify` redirects afterwards |

---

## How to test it locally

### Option A — no credentials needed (`EMAIL_PROVIDER=console`)

The link is written to the uvicorn log instead of being delivered.

```bash
# .env
EMAIL_PROVIDER=console
PUBLIC_BASE_URL=http://localhost:8000
FRONTEND_URL=http://localhost:3000

python -m app.db.migrate
python -m uvicorn app.main:app --port 8000

# 1. sign up
curl -s -X POST http://localhost:8000/auth/signup \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com","password":"hunter22!"}'
#    -> 201, "email_verification_required": true, "verification_email_sent": true

# 2. the link is in the server log:
#    EMAIL NOT SENT (EMAIL_PROVIDER=console). to=you@example.com ...
#    http://localhost:8000/auth/verify?token=<43 chars>

# 3. blocked until it is clicked
curl -s http://localhost:8000/auth/me -H "Authorization: Bearer <access_token>"
#    -> {"detail":"EMAIL_NOT_VERIFIED"}  HTTP 403

# 4. click it
curl -si "http://localhost:8000/auth/verify?token=<token>" | grep -i location
#    -> location: http://localhost:3000/login?verified=true

# 5. now allowed
curl -s http://localhost:8000/auth/me -H "Authorization: Bearer <access_token>"
#    -> {... "email_verified": true}  HTTP 200
```

### Option B — a real inbox (`EMAIL_PROVIDER=smtp`, Mailtrap sandbox)

```bash
EMAIL_PROVIDER=smtp
SMTP_HOST=sandbox.smtp.mailtrap.io
SMTP_PORT=2525
SMTP_USER=<your mailtrap username>
SMTP_PASSWORD=<your mailtrap password>
SMTP_STARTTLS=true
```

Sign up, then open the Mailtrap inbox. The message renders as HTML with a
"Verify my email" button and a plain-text fallback carrying the same URL.

### The test suite

```bash
python -m pytest tests/test_email_verification.py -v   # 28 tests
python -m pytest tests/ --ignore=tests/integration     # 509 pass, 5 skip
cd frontend && npx vitest run                          # 156 pass
cd frontend && NEXT_PUBLIC_API_URL=https://api.example.com npx next build
```

The suite runs on `EMAIL_PROVIDER=memory` (set in `tests/conftest.py`), so it
reads the actual rendered message body — a broken email template fails the
tests rather than passing them.

---

## How to test it in production

Run this **immediately after the first deploy**, before announcing anything.

```bash
API=https://<your-render-api>.onrender.com

# 0. The guard should already have refused to boot if email is misconfigured.
curl -s $API/health
```

1. **Sign up with an address you control** (not your admin account).
   Expect `201` with `"verification_email_sent": true`.
2. **Check the inbox** — including spam. If it landed in spam, the sending
   domain's SPF/DKIM records are not published; fix that in Resend before
   inviting anyone.
3. **Confirm you are blocked**: `GET /auth/me` with the returned token must be
   `403 EMAIL_NOT_VERIFIED`.
4. **Click the link.** The browser must land on `{FRONTEND_URL}/login?verified=true`
   with a green "Email verified" notice.
5. **Confirm you are unblocked**: `GET /auth/me` must be `200` with
   `"email_verified": true`.
6. **Click the same link again** — it must land on `?verified=already`, not
   re-verify.
7. **Verify the existing users were backfilled:**
   ```sql
   SELECT email, email_verified FROM users WHERE email_verified = false;
   -- must contain ONLY accounts created after the deploy
   ```
8. **Delete the test account** when done.

> **Render free tier:** both services sleep after ~15 minutes idle. A link
> clicked against a sleeping service takes ~30–60 s to answer while the
> container cold-starts. That is not a bug in this feature, but it is what a
> user will experience. See `DEPLOY_RENDER_FREE.md` for the required pinger.

---

## Common errors and fixes

| Symptom | Cause | Fix |
|---|---|---|
| API refuses to start: *"EMAIL_PROVIDER is not set… would never receive a link"* | Production guard working as designed | Set `EMAIL_PROVIDER=resend` and `RESEND_API_KEY` |
| Signup returns `"verification_email_sent": false` | Transport rejected the message | Check the log line `verification email FAILED for <addr>: <reason>`. Usually a bad key or an unverified sending domain. |
| `EmailSendError: EMAIL_PROVIDER='resnd' is not a known transport` | Typo | Use `resend`, `smtp`, `console` or `memory` |
| Email never arrives, log says `EMAIL NOT SENT (EMAIL_PROVIDER=console)` | Console transport is active | Set a real transport |
| Email arrives but lands in spam | Sending domain not verified | Publish Resend's SPF + DKIM records for `calendarharvest.com` |
| Link 302s to `?error=invalid` | Token unknown — usually the link was truncated by a mail client, or `PUBLIC_BASE_URL` changed after the mail went out | Press resend |
| Link 302s to `?error=expired` | Older than 24 h, **or** superseded by a later resend | Press resend |
| Link goes to `http://localhost:8000/...` in production | `PUBLIC_BASE_URL` still at its default | Set it to the public API origin and redeploy |
| Verification succeeds but the browser lands on a 404 | `FRONTEND_URL` wrong, or the frontend has no `/login` route at that origin | Fix `FRONTEND_URL` |
| Everyone is suddenly locked out with `EMAIL_NOT_VERIFIED` | Backfill did not run, or new rows were created outside the ORM | **Immediately** set `REQUIRE_EMAIL_VERIFICATION=false` and restart, then investigate |
| CORS error on `/auth/resend-verification` | `CORS_ORIGINS` missing the frontend origin | Add it (plain comma-separated list, not JSON) |

---

## Rollback procedure

Three levels, cheapest first. **Level 1 fixes a lockout in under a minute and
loses nothing** — try it before anything else.

### Level 1 — disable enforcement (no deploy, no data change)

```
REQUIRE_EMAIL_VERIFICATION=false
```
Set it in the Render dashboard and restart the service. Everyone can log in
again immediately. The column, the tokens and the endpoints all stay; only the
gate in `get_current_user` stops firing. **This is the intended emergency
lever.**

### Level 2 — revert the code, keep the schema

```bash
git revert <feature-1-commit>        # or: git reset --hard bd7d54c
```

`bd7d54c` is the anchor commit `BACKUP: Pre-email-verification anchor —
20260829_170115`. File-level copies of all 15 modified files also live at
`C:\tmp\leadpilot-backups\20260829_170115_pre-email-verification\`.

Leaving migration 0015 applied is **safe and recommended**: the extra columns
and the token table are simply unused by the reverted code.

### Level 3 — revert the schema too

```bash
alembic downgrade 0014_strategy_pattern_inputs
```

Verified on PostgreSQL 16: drops `users.email_verified`,
`users.email_verified_at` and `email_verification_tokens`, leaving all user
rows intact.

> **This is not a round trip.** The downgrade discards *which* users had
> verified — there is nowhere to store it in the old schema. Re-upgrading
> afterwards backfills everyone to verified again. That is the safe direction
> (nobody is locked out), but it is not the same data.

**Take a database backup first** — see
[`docs/backup/backup-procedure.md`](../backup/backup-procedure.md).

---

## Outstanding

Items 1–3 are **DEFERRED by decision** (2026-08-29), not blocked by anything in
the code. `EMAIL_PROVIDER=console` until real credentials exist, which means
**no email is delivered to anyone** — that is the intended state for now.

| # | Item | Status |
|---|---|---|
| 1 | **Live send through Resend is unverified.** No real message has reached a real inbox; the supplied credentials were placeholder text. Everything up to the transport call is proven. | **DEFERRED** — verify when a real `RESEND_API_KEY` is added |
| 2 | **`calendarharvest.com` is not verified in Resend** (SPF + DKIM unpublished), so live mail would be spam-foldered. | **DEFERRED** — do together with #1 |
| 3 | **The live Neon database is still at revision `0014`.** Migration 0015 has only ever run against throwaway databases. Production is untouched apart from a read-only probe. | **DEFERRED** — do not migrate until instructed |
| 4 | Render free-tier cold starts make a clicked link take ~30–60 s to answer. | Known, documented |
| 5 | Password reset now exists and sends through the same `email_sender.send_email` gateway (`app/services/password_reset.py`, `docs/features/password-reset.md`). | Done 2026-10-11 |

### Turning it on later

1. Put the real key in the environment and switch the transport:
   ```
   EMAIL_PROVIDER=resend
   RESEND_API_KEY=<real key>
   ```
2. Verify `calendarharvest.com` in the Resend dashboard (publish SPF + DKIM).
3. Back up and migrate production:
   ```bash
   pg_dump "<DIRECT Neon URL>" -Fc -f leadpilot_pre0015.dump
   pg_restore --list leadpilot_pre0015.dump | head    # verify the dump
   python -m app.db.migrate
   ```
4. Run the production checklist in
   [How to test it in production](#how-to-test-it-in-production).

Nothing in the code changes for any of this — step 1 is two environment
variables.

---

## Test coverage

| Layer | Tests | What it proves |
|---|---|---|
| `tests/test_email_verification.py` | 28 | Signup state, token hashing, 24 h expiry (incl. the `<= now` boundary), the gate across three routes, 403-not-401, kill switch, backfilled users, click/replay/expire/garbage, resend + invalidation, no account enumeration, transport misconfiguration |
| `tests/test_production_guard.py` | 8 new (49 total) | Production refuses to boot on a transport that delivers nothing |
| `tests/` (whole suite) | 509 | No regression from the `get_current_user` change |
| `tests/integration/` | 116 | Real PostgreSQL + Redis, whole app |
| `frontend/e2e/email-verification.spec.ts` | 28 (14 × Chromium + Mobile Safari) | Signup → check-email routing, failed-send messaging, resend + rate limit, all four `/auth/verify` redirect outcomes, login routing by verification state, the Shell gate on a bookmarked URL, and that a 403 never triggers a token refresh or clears the session |

Run them all:

```bash
python -m pytest tests/ --ignore=tests/integration
TEST_DATABASE_URL=<pg> TEST_REDIS_URL=<redis> python -m pytest tests/integration
cd frontend && npx vitest run
cd frontend && NEXT_PUBLIC_API_URL=https://api.example.com npx next build   && npx playwright test e2e/email-verification.spec.ts
```

> The Playwright spec sets a 90 s per-test timeout. WebKit hydrating a static
> export at `workers: 2` regularly needs 5–15 s per navigation, and the suite
> runs with `retries: 0` — so the budget is raised to something the slow
> browser can actually meet rather than retrying until it looks green.
> Measured: the file completes in ~50 s on an idle machine, and three
> consecutive full runs passed 28/28.
>
> **Unrelated pre-existing flakiness:** `e2e/happy-paths.spec.ts` fails on
> Mobile Safari independently of this feature — measured on a clean tree with
> Feature 1 removed: **7 failed / 7 passed**. With Feature 1 present the same
> file does better, not worse. It is not caused by this work and is not fixed
> by it.
