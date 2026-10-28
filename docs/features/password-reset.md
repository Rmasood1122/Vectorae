# Forgotten-password reset

Status: built 2026-10-11 on `claude/platform-gaps` (platform-gaps item 1).
Evidence: `docs/program/fixes/platform-gaps.md`.

## Problem

There was no way to recover an account whose password was forgotten. Help
center articles 23 and 29 told users "There is no 'forgot password' flow.
Contact support", and support had no tool either: the only fix was an
operator editing `users.password_hash` by hand. Found as
help-center-discoveries #25. A locked-out paying customer is a churn and
support-cost problem; a hand-edited hash is a security problem.

## Recon (what already existed)

Built from existing parts, no parallel duplicates:

| Need | Existing building block reused |
|---|---|
| Hash-only, single-use, expiring link | `app/services/email_verification.py` (same 32-byte token, sha256 storage, "burn outstanding links first", commit-before-send) |
| Transactional email | `app/services/email_sender.send_email` (resend / smtp / console / memory transports) |
| Password rules + hashing | `app/services/auth.hash_password` (PBKDF2-SHA256, 600k, min 8) and `ChangePasswordIn` (8–256) |
| Sign out everywhere | `auth_sessions.revoke_all_for_user` (what change-password uses) |
| Audit | `app/services/security_audit.record` |
| Rate limiting | `app/core/rate_limiting.enforce_rate_limit` + `RATE_LIMIT_AUTH` (10 / 15 min) |
| No-enumeration answer | `/auth/resend-verification` pattern |

## Behaviour

**Request** — `POST /auth/password-reset/request` `{email}`

- Always **200** `{"status": "accepted", "detail": "If that address has a
  Vectorae account, we have emailed it a link to reset the password."}` —
  for an unknown address, a passwordless pre-auth row, a suspended account and
  a real account alike.
- Rate limited before any lookup: per IP (`password_reset_ip`) and per
  normalised address (`password_reset_account`), `RATE_LIMIT_AUTH` per 15
  minutes each, on their **own** buckets so reset traffic cannot exhaust a
  person's sign-in allowance. A 429 is keyed on the address string, never on
  whether it has an account.
- Eligible accounts only: has a password and is not suspended
  (`password_reset.eligible`). A passwordless pre-auth row is never claimable
  through reset — that would side-step the sign-up policy (`SIGNUP_ENABLED`,
  allowed domains).
- Cooldown: if a link was issued for the account less than 60 s ago, nothing
  new is issued or sent, and the outstanding link keeps working (mail-bomb
  guard on top of the rate limit).
- Daily limit: at most 5 reset emails per account per rolling 24 hours
  (`DAILY_EMAIL_LIMIT`). Past it: the same 200, no email, nothing burnt, and a
  warning log line (`password reset: daily limit …`).
- Issuing locks the account row (`SELECT … FOR UPDATE`), so two concurrent
  requests for one address run one after the other and can never mint two
  live links (PostgreSQL race test).
- Otherwise: every outstanding reset link for the account is burnt, a new one
  is stored as sha256 only, an `auth.password_reset_requested` audit row is
  written, the transaction commits, and the email is sent **after the
  response** (FastAPI BackgroundTasks) so a slow relay does not make "has an
  account" measurably slower. A transport failure is logged
  (`password reset email FAILED for …`) and the answer is still 200.

**Email** — subject "Reset your Vectorae password"; link
`FRONTEND_URL/reset-password#token=<token>`. The token is in the URL
**fragment**: browsers never send a fragment to any server (static host, CDN
logs, Referer), so the token only travels in the confirm request's JSON body.

**Confirm** — `POST /auth/password-reset/confirm` `{token, new_password}`

- `new_password`: 8–256 characters (same rules as change-password; 422
  otherwise, and a 422 does not burn the link).
- Rate limited per IP (`password_reset_confirm_ip`).
- The password is hashed only after the link has proved valid, so invented
  tokens cost a hash lookup, not 600k PBKDF2 rounds.
- The link is spent by a conditional `UPDATE … SET used_at = now WHERE id = …
  AND used_at IS NULL` (rowcount must be 1). Two requests with the same link:
  on PostgreSQL the second blocks on the row, then matches nothing and is
  answered as a dead link — the password changes once (PostgreSQL race test;
  before the fix the last commit silently won).
- Valid link: new hash set, **every** outstanding reset link burnt, and the
  account signed out everywhere exactly as change-password does
  (`app/api/auth.py` `_sign_out_everywhere`): every refresh session revoked
  (`revoked_reason = "password_reset"`), `users.password_changed_at` stamped so
  every refresh or access token issued before it is refused — legacy
  session-less refresh tokens included (`auth_sessions._issued_before_password_change`,
  checked on every `rotate()` path and in `get_current_user`) — and every
  personal API key revoked. Audit row `auth.password_reset`
  (`{"sessions_revoked": n, "api_keys_revoked": k}`), one commit. Returns
  `{"status": "reset", "signed_out_everywhere": true, "api_keys_revoked": k}`
  and **no tokens** — the person signs in normally (limiter + network guard
  apply). The email and the page both say API keys are revoked.
- Any bad link — unknown, used, superseded, expired, or the account became
  suspended since the request — gets **400** with one message ("This reset
  link is invalid or has expired. Request a new one."). Expired and
  ineligible links are burnt; the reason is kept in an
  `auth.password_reset_refused` audit row (`{"reason": "expired" |
  "ineligible"}`), never in the response.
- Deleted accounts: the user row's deletion cascades to its reset tokens (FK
  `ON DELETE CASCADE` + ORM cascade), so their links are unknown tokens. The
  product has no user tombstone/soft-delete; that is the whole "deleted" case.

**Frontend**

- Sign-in page: "Forgot password?" link → `/forgot-password`.
- `/forgot-password`: email form; after any successful request shows the same
  confirmation (`REQUEST_SENT_MESSAGE`); 429 says wait.
- `/reset-password`: reads the token from the fragment once, strips it from
  the address bar (`history.replaceState`), checks the rules client-side,
  calls confirm, clears any local session on success, links back to sign in.
  A dead link shows the single 400 message with "Request a new link".
- Logic in `frontend/src/lib/passwordReset.ts`; API calls in
  `frontend/src/lib/api/auth.ts`.

## Non-goals

- Two-factor sign-in (still not available; article 23 says so).
- A "your password was changed" notification email (worth adding; not
  required by the gap).
- Marking an unverified account verified on reset (the link does prove
  mailbox control; kept out to keep blast radius small — founders may decide).

## Data model and migration

Migration `0084_password_reset` (down `0083_billing_retention`) adds
`users.password_changed_at` (nullable, no backfill; NULL = nothing refused),
bounded by `lock_timeout = 15s` on PostgreSQL, and creates
`password_reset_tokens`:
`id`, `user_id` (FK users, CASCADE, indexed), `token_hash` (String 64, unique
index), `expires_at`, `used_at`, `requested_ip` (String 64), `created_at`.
Portable types (SQLite + PostgreSQL). Downgrade drops the table (outstanding
links die; no password or session is touched). Excluded from the GDPR account
export as one-way hashes (`account_export.EXCLUDED`); each request is exported
as its `security_audit_events` row.

## API

| Method | Path | Auth | Success | Errors |
|---|---|---|---|---|
| POST | `/auth/password-reset/request` | none | 200 (always) | 422 malformed email, 429 |
| POST | `/auth/password-reset/confirm` | none | 200 | 400 bad link, 422 password rules, 429 |

## Settings

`PASSWORD_RESET_TTL_MINUTES` (`password_reset_ttl_minutes`, default **60**,
`app/config.py`). `FRONTEND_URL` must be the public web origin in any
environment where a real person resets a password (production sets it in the
Render dashboard, `render.yaml` `sync: false`). Not behind a feature flag: this
closes a lockout gap and adds no outreach behaviour; the endpoints are inert
until someone asks for a link.

## Tenancy

Every query is keyed on the account the token belongs to (`user_id`) or on the
exact normalised email address. Tests prove another account's password and
sessions are untouched.

## Security / compliance review

- OWASP-style reset properties: no account enumeration (same body, same
  status, send after response), hash-only token storage, 256-bit token,
  single use, 60-minute life, one live link per account, sessions revoked,
  audit trail, rate limits on both endpoints, no tokens issued by confirm.
- Not an outreach feature: CAN-SPAM / CASL / PDPA do not apply to a
  transactional email the account holder requested. GDPR: `requested_ip` is
  personal data held for security (legitimate interest, Art. 6(1)(f)) and is
  deleted with the account. **Needs founder/legal confirmation** that this
  reading is acceptable and whether a retention limit for burnt token rows is
  wanted (none exists for `email_verification_tokens` either).
- Email deliverability depends on `calendarharvest.com` being verified in
  Resend (SPF/DKIM) — the same open item as verification email (GAP-007).

## AI cost

None. No model call anywhere in the flow.

## Test plan (all offline)

- `tests/test_password_reset.py` (43): no enumeration (known / unknown /
  suspended / pre-auth), normalisation, per-address limit across rotating IPs,
  per-IP limit, own buckets vs login, cooldown, fragment link, hash-only
  storage, TTL, requester IP, reset + sign-in, no tokens, single use, unknown
  / expired / superseded links, 422 does not burn, sessions revoked with
  reason, other accounts untouched, audit rows without secrets, suspended
  after request, deleted account, confirm rate limit, email transport failure.
- `tests/test_platform_gaps_migrations.py::TestPasswordResetMigration` (4):
  table equals the model, unique hash, cascade, downgrade.
- PostgreSQL: `tests/integration/test_password_reset_race_pg.py` (2: two
  redeems of one link change the password once; two concurrent requests mint
  one link), and the integration suite runs `alembic upgrade head` through
  0081.
- Review additions in `tests/test_password_reset.py`: API keys revoked on reset
  and on change-password (others' untouched), `password_changed_at` stamped,
  legacy refresh and old access tokens refused after a reset, sign-in right
  after works, whole-second helper, stale second redeemer, 5-per-24h limit,
  cooldown keeps the first link.
- `frontend/src/tests/passwordReset.test.tsx` (13): token parsing, rules,
  messages, both pages, the sign-in link.

## Success metric

Measured, not promised: support tickets tagged "locked out / forgot password"
per month (target: zero needing a hand-edited hash), and the count of
`auth.password_reset` audit rows vs `auth.password_reset_requested` (completion
rate). Neither is measured yet.

## Limitations

- Token age is compared at whole-second resolution (a JWT `iat` is an
  integer): a token minted in the same second as the change is treated as
  after it, so signing in right after a reset works. Every session row is
  revoked as well, so only a legacy session-less token minted in that same
  second could survive.
- `next build` (static export) could not be run offline here (Google Fonts
  fetch); `tsc`, ESLint and vitest pass.

## Unverified

- Delivery through the live Resend account and inbox placement of the reset
  email (GAP-007: domain SPF/DKIM not confirmed).
- That production `FRONTEND_URL` is set to the public web origin.
- The pages under the real static host (Vercel) — routes `/forgot-password`
  and `/reset-password` exist in the export like `/check-email`.
