# API Endpoints

Live, always-current reference: **`/docs`** (Swagger) and **`/openapi.json`**
on the running service. This file documents the auth surface in detail and
indexes the rest.

Base URL: `PUBLIC_BASE_URL` (production) / `http://localhost:8000` (dev).

---

## Authentication model

- **Bearer JWT**, HS256. `Authorization: Bearer <access_token>`.
- Access tokens live 15 minutes; refresh tokens 14 days.
- A `typ` claim separates the two — a refresh token never works as an access
  token, or vice versa.
- Every authenticated route resolves its user through **one** dependency,
  `app/services/auth.py::get_current_user`.

### Failure codes shared by every authenticated route

| Code | `detail` | Meaning | What the client should do |
|---|---|---|---|
| 401 | `missing bearer token` | No `Authorization` header | Send to `/login` |
| 401 | `token expired` | Access token past `exp` | Refresh, then retry once |
| 401 | `invalid token` | Signature/format bad | Clear session, `/login` |
| 401 | `not a access token` | A refresh token was sent as an access token | Clear session |
| 401 | `user not found` | Token valid, row gone | Clear session |
| **403** | **`EMAIL_NOT_VERIFIED`** | **Credentials are valid; the address is unverified** | **Send to `/check-email`. Do NOT refresh and do NOT log out.** |
| 403 | `admin privileges required` | Non-admin on `/admin/*` | |
| 403 | `account suspended` | Suspended admin | |
| 429 | *(object)* | Rate limited; `Retry-After` header set | Back off |

> `EMAIL_NOT_VERIFIED` is deliberately a **403, not a 401**. A 401 would send
> `frontend/src/lib/api/client.ts` into its refresh-then-retry path, which
> succeeds at refreshing, retries, gets 401 again, and finally clears the
> session — logging out a user whose only problem is an unread email.
> The string is a **contract**; `frontend/src/lib/api/auth.ts` matches on it.

---

## Auth

### `POST /auth/signup` → `201`

Creates an account (or claims a pre-auth row that has no password yet) and
emails a verification link.

Rate limited per IP (`RATE_LIMIT_AUTH`, 15-min window). The limiter runs
*after* body validation, so a malformed payload costs nothing.

```jsonc
// request
{ "email": "user@example.com", "password": "at least 8 chars" }
```
```jsonc
// 201
{
  "user": { "id": "uuid", "email": "user@example.com", "plan": "free",
            "is_admin": false, "email_verified": false },
  "access_token": "eyJ…", "refresh_token": "eyJ…",
  "token_type": "bearer", "expires_in": 900,
  "email_verification_required": true,
  "verification_email_sent": true
}
```

| Field | Meaning |
|---|---|
| `email_verification_required` | Always `true`. The tokens are real but every authenticated route will answer 403 until the link is clicked. |
| `verification_email_sent` | `false` when the account was created but the mail transport failed. **The account still exists** — show "resend", not "check your inbox". |

Errors: `409 account already exists` · `422` (password < 8 chars, bad email) ·
`429`.

> Signup deliberately does **not** fail when the mail transport is down.
> Returning a 500 would destroy an account the user just created: the address
> is taken, the password is lost, and the signup is unrepeatable.

---

### `POST /auth/login` → `200`

Two independent limits, both governed by `RATE_LIMIT_AUTH`: per IP (one host
brute-forcing) and per account (credential stuffing spread across many IPs).

```jsonc
{ "email": "user@example.com", "password": "…" }
```
Returns the same `user` + token bundle as signup (without the two verification
fields).

**Login succeeds for an unverified account** — otherwise there is no session
from which to request a new link. The client must branch on
`user.email_verified` and route to `/check-email` when it is `false`.

Errors: `401 invalid email or password` (one message for both wrong-password
and unknown-account — no account enumeration) · `429`.

---

### `GET /auth/verify?token=<token>` → `302`

The target of the link in the verification email. **Unauthenticated** — it is
clicked from an inbox, often in a browser with no session.

Always redirects; never renders. `302`, not 307: a GET answered with a GET is
what every mail client and link scanner handles predictably.

| Outcome | `Location` |
|---|---|
| Verified now | `{FRONTEND_URL}/login?verified=true` |
| Was already verified | `{FRONTEND_URL}/login?verified=already` |
| Expired, or superseded by a resend | `{FRONTEND_URL}/login?error=expired` |
| Unknown / missing / malformed token | `{FRONTEND_URL}/login?error=invalid` |

Not rate limited: the token is 43 URL-safe characters of `os.urandom` inside a
24-hour window, so there is nothing to brute force, and an IP-keyed limiter
would punish the shared corporate NAT of exactly the customer we want.

Single use — `used_at` is stamped on the first success, so a forwarded email or
a mail-scanner prefetch cannot re-verify an account.

---

### `POST /auth/resend-verification` → `202`

```jsonc
// request
{ "email": "user@example.com" }
// 202 — ALWAYS this body, whatever the address
{ "status": "accepted",
  "detail": "If that address has an unverified account, a verification email has been sent." }
```

**Always 202**, identical body, for every syntactically valid address.
Reporting "no such account" would hand anyone an account-existence oracle for
any address they cared to try — and `/auth/login` goes to the trouble of
avoiding exactly that.

Mail is actually sent only when the address has an account, that account has a
password, and it is not already verified.

Issuing a new link **invalidates every outstanding one** for that user.

Rate limited on both `ip:` and `acct:` keys — otherwise this is a free
mail-bomb aimed at any address you name.

Errors: `422` (not an email address) · `429`.

---

### `POST /auth/refresh` → `200`

```jsonc
{ "refresh_token": "eyJ…" }
```
Returns a fresh token pair. **Not** rate limited: a legitimate client hits it
every 15 minutes, and guessing a signed refresh token is not a brute-forceable
attack.

Not gated on verification — it resolves the user directly, not through
`get_current_user`.

Errors: `401 not a refresh token` · `401 token expired` · `401 user not found`.

---

### `GET /auth/me` → `200` 🔒

```jsonc
{ "id": "uuid", "email": "user@example.com", "plan": "free",
  "is_admin": false, "email_verified": true }
```
The cheapest request that goes through `get_current_user`, which makes it the
canonical probe for "is this session usable?". `Shell.tsx` calls it once before
rendering the dashboard.

---

## Learn Vectorae — tutorials 🔒 (Feature 2)

The video catalogue is **not** a database table — it lives in
`app/services/tutorials.py`. Endpoints therefore address tutorials by **slug**,
never by a row id, and an unknown slug is a clean `404 unknown tutorial`
rather than a silently-stored orphan row. See
[`docs/features/tutorial-section.md`](../features/tutorial-section.md).

### `GET /tutorials?q=&level=` → `200`

| Param | Notes |
|---|---|
| `q` | Case-insensitive substring over title **and** description. Blank is ignored. |
| `level` | `beginner` \| `intermediate` \| `advanced`. An unknown value returns an empty list, **not** a 422 — it is a browse filter, and a validation error is a strange answer to a typo in a shared URL. |

```jsonc
{
  "tutorials": [{
    "slug": "understanding-your-icp",
    "title": "Understanding Your ICP",
    "description": "…",
    "level": "beginner",
    "order": 3,
    "youtube_id": null,        // null until the real video exists
    "duration_seconds": null,
    "is_placeholder": true,    // => render "coming soon", NOT an <iframe>
    "progress": {
      "position_seconds": 0, "duration_seconds": null, "percent": 0.0,
      "completed": false, "completed_at": null, "last_watched_at": null,
      "started": false         // no progress row exists — not an error
    }
  }],
  "levels":  [{ "level": "beginner", "label": "Beginner" }],
  "summary": { "total": 9, "completed": 0, "percent": 0.0,
               "by_level": { "beginner": { "label": "Beginner", "total": 3, "completed": 0 } } },
  "badges":  [{ "slug": "beginner-complete", "label": "Beginner Complete",
                "description": "…", "level": "beginner", "earned": false,
                "earned_at": null, "required_total": 3, "required_completed": 0 }],
  "query":   { "q": null, "level": null }
}
```

> **`summary` and `badges` describe the WHOLE catalogue and deliberately
> ignore `q`/`level`.** The summary answers "how far through the course am I",
> so it must not move while the user types in the search box. Only `tutorials`
> is filtered.

> **Badges are derived from progress, never stored.** A stored badge can
> disagree with the progress meant to justify it. This is also why
> `DELETE .../progress` correctly revokes one.

### `GET /tutorials/{slug}` → `200`

One tutorial in the same shape as a `tutorials[]` entry. `404 unknown tutorial`
for a slug not in the catalogue.

### `PUT /tutorials/{slug}/progress` → `200`

```jsonc
{ "position_seconds": 108, "duration_seconds": 120 }   // duration optional
```

Called repeatedly by the player; cheap and safe to repeat. One row per user per
video is guaranteed by `UNIQUE (user_id, tutorial_slug)`.

| Field | Update rule | Why |
|---|---|---|
| `position_seconds` | **latest** value wins | It answers "where do I resume" — scrubbing back should resume back. |
| `percent` | **maximum** value wins | It answers "how much have I seen" — scrubbing back must not erase watched progress or undo a completion. |

Crossing **90%** marks the tutorial complete (`COMPLETION_THRESHOLD_PERCENT`).
Not 100%: almost nobody reaches the final frame, and a 100% rule strands users
at "8 of 9" and teaches them to scrub. Completion is never revoked here — only
`DELETE .../progress` does that. `completed_at` keeps the **first** completion
time, so rewatching does not move it.

Errors: `404` unknown slug · `422` negative or absurd position.

### `POST /tutorials/{slug}/complete` → `200`

Marks finished without watching to the threshold. Needed for two real cases,
not convenience: a placeholder video cannot be watched at all, and a user who
already knows the material must still be able to reach the badge. Idempotent.

### `DELETE /tutorials/{slug}/progress` → `200`

Resets one tutorial to not-started and deletes the row, so "not started" has
exactly one representation. Returns the reset tutorial (not `204`) so the
client can update its cache without a second round trip. Not an error on a
tutorial that was never started.

### `GET /admin/tutorials/completions` → `200` 🔒 admin

```jsonc
{
  "active_learners": 1,        // distinct users with any progress row
  "total_completions": 2,
  "tutorials": [{ "slug": "…", "title": "…", "level": "beginner",
                  "started_count": 1, "completed_count": 1,
                  "in_progress_count": 0 }]
}
```

**Numbers only.** The response carries no user id, no email, and no way to
learn which videos a named person watched — that was the product decision, and
it is enforced by the payload containing no user identifier at all rather than
by a UI that declines to render one. `403` for non-admins.

---

## AI support chat (Feature 3)

All routes require auth. See
[`docs/features/ai-support-chat.md`](../features/ai-support-chat.md).

### `GET /support/faq`

The curated knowledge base plus `chat_enabled`. Exposed so the widget can
offer real starter questions, and so "what can this thing help with?" has an
honest answer: it is the same list the model is restricted to.

### `POST /support/chat`

```jsonc
// request
{ "message": "How does outreach work?", "session_id": "uuid (optional)" }
// 200
{
  "session_id": "uuid",
  "answer": {
    "text": "...", "on_topic": true, "confidence": 0.92,
    "faq_ids": ["how-outreach-works"], "suggest_ticket": false,
    "reason": "answered"
  },
  "message": { "id": "...", "role": "assistant" }
}
```

`reason` records WHY the user got this text, and is persisted so a confusing
answer can be explained later without reproducing it:

| `reason` | Meaning | What `text` contains |
|---|---|---|
| `answered` | Grounded, above the confidence floor | the model's answer |
| `off_topic` | Not about Vectorae | the **refusal constant** |
| `low_confidence` | On topic, model unsure | the ticket suggestion |
| `model_error` | API unreachable or failed | the ticket suggestion |
| `malformed_response`, `empty_answer` | Unusable response | the ticket suggestion |

> **The model never writes its own refusal.** On `off_topic` the answer it
> produced is discarded and a constant returned — given the chance to write a
> sentence about a topic, a model writes a sentence about that topic.

Omitting `session_id` continues the most recent conversation. Both turns are
persisted, refusals included.

Rate limited **per user per day** (`RATE_LIMIT_SUPPORT_CHAT`, default 30) — a
cost ceiling first, since every message spends the account's Anthropic key.
Keyed on the user id, not the IP, so colleagues behind one office NAT do not
share a budget. The limiter runs after validation, so an invalid body costs
no quota.

Errors: `422` blank or over-long message, `429` daily budget spent,
`503` kill switch (`SUPPORT_CHAT_ENABLED=false`).

### `GET|POST /support/chat/sessions` and `GET|DELETE /support/chat/sessions/{id}`

List, start, read and delete conversations.

Transcripts are ordered by `chat_messages.seq`, **not** `created_at`: both
turns of one exchange are written in a single request and can share a
timestamp, which left a random UUID as the tiebreak and could render the
answer before the question.

Another user's session returns **404, not 403** — otherwise the status code is
an oracle for "does this session id exist?".

Deleting a session cascades its messages but **nulls**, never deletes, any
ticket raised from it.

### `POST /support/tickets` and `GET /support/tickets`

```jsonc
{ "subject": "min 3 chars", "body": "min 10 chars",
  "chat_session_id": "uuid (optional, must be yours)" }
```

Stored in the database; **not emailed anywhere**. Deliberately **not** subject
to the chat budget — someone who has run out of messages is exactly the person
who most needs a human.

### `GET /admin/support/tickets?status=` (admin)

Carries `user_email`, unlike `/admin/tutorials/completions`. Not an
inconsistency: a ticket you cannot reply to is useless, and the user wrote
that text deliberately, addressed to support. Ordered oldest-first, because
the queue is worked from the front.

### `POST /admin/support/tickets/{id}/resolve` (admin)

Idempotent — resolving twice keeps the original `resolved_at`, so a second
click cannot rewrite the response-time record. `404` on an unknown or
malformed id.

---

## Theme 🔒

| Endpoint | Notes |
|---|---|
| `GET /me/theme` | `{ "theme": {…} }` |
| `PUT /me/theme` | Validates `#rrggbb`; persists `contrast_warnings` so the user was demonstrably informed |
| `POST /me/theme/background` | multipart; png/jpeg/webp; ≤5 MB; returns a `/media/*` URL |

## Other routers

All 🔒 routes are subject to the `EMAIL_NOT_VERIFIED` gate.

| Prefix | Router | Auth |
|---|---|---|
| `/health` | `app/api/health.py` | public |
| `/plans` | `app/api/strategies_advanced.py` | public |
| `/unsubscribe` | `app/api/unsubscribe.py` | public (token in URL) |
| `/webhooks/calendly`, `/webhooks/whatsapp` | `webhooks.py`, `webhooks_whatsapp.py` | signature-verified |
| `/products`, `/strategies`, `/leads`, `/sequences`, `/analytics`, `/integrations`, `/onboarding`, `/devices`, `/webhooks/targets`, `/whatsapp/*` | respective modules | 🔒 |
| `/tutorials` | `app/api/tutorials.py` | 🔒 |
| `/support` | `app/api/support.py` | 🔒 |
| `/admin/*`, `/playbook/*` | `admin.py`, `playbook.py` | 🔒 + `is_admin` |
| `/debug/*` | `debug.py` | **test only** — never mounted in a deployed environment |

## Conventions

- Errors are `{"detail": "..."}`; validation errors put a list in `detail`.
- `X-Request-ID` on every response (`RequestIDMiddleware`).
- `429` bodies carry `{error, retry_after, limit, window}` plus a `Retry-After`
  header.
- Rate limiting is called **inside** handlers, never as a route dependency, so
  a `422` never burns quota.
