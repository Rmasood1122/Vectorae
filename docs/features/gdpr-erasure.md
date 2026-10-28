# GDPR erasure — account deletion and complete lead erasure

**Status:** built (branch `claude/gdpr-erasure`). **Legal/safety behaviour: yes**
— this adds a legal duty's implementation (GDPR Art. 17) and changes legal
text. Every legal reading below is the builder's, not a lawyer's, and is marked
**needs founder/legal confirmation**. The privacy texts changed here
(`docs/PRIVACY_POLICY.md`, `frontend/src/app/privacy/page.tsx`) **need
founder/legal review before merge**.

Founder instruction (2026-10-11): "fix every open gap". Decisions E1–E3 were
open; the defaults below are built and listed under "Decisions to confirm".

## Problem

`docs/program/BASELINE.md` row 1 (HIGH, not downgraded by P0-D): no route,
service or task deletes or anonymises a `users` row; an account-holder's
erasure request could only be met by hand in the database. Verified in code
before the change:

1. Only `POST /me/data-export` existed (`app/api/account_data.py`).
2. `_gdpr_erase_lead` (`app/api/leads.py`) cleared the lead row but not the
   lead's `inbound_replies` (sender, subject, body, AI draft answer) or the
   subject/body of `messages` to the person, and kept `provenance_json` value
   snapshots of the erased fields.
3. `billing_subscriptions.user_id` and `billable_meetings.user_id` were
   `ON DELETE CASCADE`: deleting a user row would destroy billing records.
4. `docs/PRIVACY_POLICY.md` claimed bcrypt (code: PBKDF2-HMAC-SHA256, 600,000
   iterations, `app/services/auth.py`), in-app account deletion (absent),
   "Settings → Export" (the export is on **Profile**), "message bodies are not
   stored" (they are: `messages.body`, `inbound_replies.body`), 90/30-day
   retention (no such jobs; the only retention job deletes support chats after
   30 days, `app/workers/support_tasks.py`), and an unfilled contact email.
   `frontend/src/app/privacy/page.tsx` §5 claimed Google analytics (no
   analytics code exists in `frontend/` or `app/`) and §8 said account deletion
   is by email request.

## Recon (what already existed)

- `app/services/account_export.py` — `OWNERSHIP` (every tenant table's FK path
  to `users.id`) and `owner_clause()`. **Reused** as the single source of truth
  for what erasure removes; not duplicated.
- `consent.record_erasure` — the "write the proof before the data goes"
  pattern; the account-level record follows it (see "The erasure record").
- `billing.cancel` (period-end cancel), `stripe_billing.cancel_subscription_now`.
- `auth_sessions`, `api_keys`, per-provider disconnects (all of which are local
  row deletes today — see "Unverified").
- Password re-check + `INVALID_PASSWORD` contract and the `password_confirm`
  rate-limit bucket (data export, strategy deletion).

So this is "extend account data + extend lead erasure", not a parallel system.

## Behaviour

### A. `DELETE /me` — delete my account

Body `{"password": "...", "confirm": "DELETE"}`. Order of checks:

| Step | Refusal |
|---|---|
| Personal API key | 403 (a person's act, like the export) |
| Rate limit (`acct:<id>`, bucket `password_confirm`, `RATE_LIMIT_AUTH`) | 429 |
| `confirm` must be exactly `DELETE` (case-sensitive) | 422 `CONFIRMATION_REQUIRED` |
| Administrator account | 409 (an operator, not a customer; deleting the last admin would lock operators out) |
| Password re-check | 403 `INVALID_PASSWORD`, `account.delete_denied` audited and committed |
| A subscription that exists in Stripe (Stripe id, not a stub) cancelled **now** (`account_erasure.cancel_billing`) | 502 — **nothing is erased** — when the cancellation cannot be confirmed: no `STRIPE_SECRET_KEY` on this server (review fix 1: this used to cancel only locally while Stripe kept charging), or Stripe refuses / is unreachable **and** a follow-up `GET` does not show the subscription `canceled`/`incomplete_expired` or gone (404). An already-cancelled or missing subscription never blocks deletion. |
| LinkedIn accounts disconnected at Unipile (`disconnect_providers`, `DELETE /accounts/{id}`) | never refuses: best effort, the outcome is on the erasure record |

`/me` is a personal route (`app/services/rbac.py`): the `X-Workspace-Id`
header is ignored, so a workspace owner's account can only be deleted by that
owner, and a member can only ever delete their own account.

Then, in one transaction (`app/services/account_erasure.py::erase_account`):

1. **Stop outreach** — every SCHEDULED, AWAITING_REVIEW or guard-held message
   is cancelled; ACTIVE/PAUSED enrollments are stopped (counts recorded). A
   message already SENDING is deleted with the rest; its send task re-asserts
   its claim right before transmitting and fails closed (see "The send race").
2. **Kept rows lose their links** to rows about to be deleted (e.g.
   `consent_events.lead_id`, `billable_meetings.lead_id/strategy_id`,
   `compliance_audit_log.lead_id/message_id`).
3. **Kept rows are scrubbed** (see decisions) — including
   `account_security_events` rows recorded with **no user id** but the
   person's email (a VPN-blocked sign-in), matched by email (review fix 3).
4. **Delete** every row of every `DELETE` table owned by the account, children
   before parents, using `account_export.owner_clause`. This includes sessions
   and refresh tokens (`auth_sessions`), API keys, Gmail/LinkedIn (Unipile)
   accounts, CRM connections and links, integration tokens, tool integration
   settings, webhook targets, device tokens, verification tokens/codes.
5. **Other accounts' rows that point at this person** (author, approver,
   assignee, reviewer: every `ON DELETE SET NULL` FK to `users.id` that is not
   a table's ownership column) are set to NULL — what a hard delete would do.
6. **Tombstone** the `users` row.
7. **Record** the erasure.

After the commit, the account's files are removed from disk (best effort,
logged; collected BEFORE the rows go): uploaded theme backgrounds
(`media/bg_<user hex>_*`), generated voicemail audio of its calls
(`media/voicemails/<32 hex>.mp3` — the name is validated, a URL cannot steer
the deletion elsewhere) and its workspaces' logos
(`media/branding/<workspace hex>-*`). The response clears the refresh
cookie and returns `{deleted, deleted_at, kept, billing_retention_years}`.

### Decisions to confirm (built as defaults)

- **E1 — billing kept, anonymised, 7 years.** `billing_subscriptions` and
  `billable_meetings` are kept: amounts, currency, dates, statuses, Stripe ids
  (customer, subscription, invoice). They never held a name or email; links to
  deleted prospects/strategies are cleared, and the free-text
  `resolution_note` (a dispute reason can name a prospect) is cleared.
  `BILLING_RETENTION_YEARS = 7` (`account_erasure.py`). Reason: tax and
  accounting record-keeping. Relied on: GDPR Art. 17(3)(b) (erasure does not
  apply where processing is needed to comply with a legal obligation) and
  record-keeping rules that vary by country (US IRS guidance: 3 years
  generally, up to 7 in some cases; UK: 6 years; some EU states longer, e.g.
  Germany 10). **Needs founder/legal confirmation** of the period and of the
  jurisdictions that apply. **No purge job exists**: nothing deletes these rows
  after 7 years yet; that is a separate reviewed change.
  Pay-per-meeting meetings still inside their grace period (pending/disputed)
  are **waived** so the charge sweep never bills a deleted account. A monthly
  plan is cancelled immediately in Stripe; **no refund** of the current period
  is issued (Stripe's default) — founder decision to confirm.
  The database now refuses to cascade billing away (migration 0083, below).
- **E2 — consent proof kept.** `consent_events` (opt-ins, opt-outs, erasures)
  and `compliance_audit_log` (per-send lawful-basis decisions) are kept with
  links to deleted prospects/messages cleared, as proof that an opt-out was
  honoured and a send was lawful. The **global suppression lists**
  (`suppression_list`, `linkedin_suppressions`) are not account data and are
  never touched, so a person who unsubscribed stays unsubscribed. Hashed form:
  **not supported** by the suppression model (it stores the address itself),
  so `consent_events.identifier` stays in clear text — kept as is, stated
  here. Relied on: Art. 17(3)(e) (legal claims), Art. 21(3) (objection to
  direct marketing must be honoured, which requires remembering it), CAN-SPAM
  (15 U.S.C. §7704(a)(4), opt-outs honoured), CASL s.13 (sender bears the
  burden of proving consent). **Needs founder/legal confirmation.**
- **E3 — workspaces.** An owner's deletion removes the owner's workspaces and
  every membership and invitation in them; members' own user accounts and data
  are untouched. A member's deletion removes only the member's own data and
  memberships, plus invitations addressed to their email in anyone's
  workspace; the owner's data stays and just stops pointing at them.
- **E4 (builder's choice, confirm) — other kept records.**
  `activity_audit_events` (hash-chained trail) is kept untouched: payload is
  ids/steps/classifications, PostgreSQL refuses DELETE on it (migration 0042).
  `security_audit_events` and `account_security_events` are kept as a record
  of what happened to the account, with IP address, user agent, email,
  country and free-form details cleared.
- **Users row: tombstone, not delete.** Chosen because billing (E1) and the
  consent proof (E2) reference `users.id`, and because an access token issued
  before the erasure then resolves to an account that answers 401 "user not
  found" (`app/services/auth.py`) rather than to nothing. Every column except
  `id, created_at, updated_at, email, plan, is_suspended, suspended_at,
  suspended_reason` is reset to NULL/false/empty; `email` becomes
  `deleted-<id hex>@deleted.invalid` (RFC 2606 reserved — undeliverable, never
  collides, frees the real address so it can sign up again); `password_hash`
  is NULL (sign-in impossible); `suspended_reason = account_deleted`. A new
  `users` column is cleared by default; a new NOT NULL column without an
  obvious empty value makes erasure raise until someone decides.

### After the erasure (review fixes 2, 6, 7)

- **Refresh tokens.** `auth_sessions.rotate` refuses a tombstoned account
  (401) on the legacy (no-`jti`) path, the grace-window path and the normal
  path, so no session (with IP and user agent) is ever recreated for it. A
  suspended account is refused the same way (403 `account suspended`) instead
  of being handed fresh tokens the chokepoint then refuses; the pinned test
  `tests/test_account_suspension.py::test_a_token_from_refresh_is_refused`
  now asserts the refusal at the refresh itself.
- **Signed-state callbacks.** Gmail, HubSpot, Salesforce and Slack OAuth
  callbacks and the Unipile hosted-auth notify refuse a tombstoned account
  before any provider call, so an open consent screen cannot re-attach a
  connection to an erased account.
- **Unsubscribes.** Since this change the unsubscribe token also carries the
  recipient address (Fernet-encrypted, like the rest of the token). A click
  whose lead was erased or whose account was deleted still adds the address to
  the global suppression list and writes a consent-ledger event (no lead, no
  account) — CAN-SPAM 15 U.S.C. §7704(a)(4) and CASL require the request to be
  honoured. **Limit:** a token minted before this change carries no address;
  if its lead row is gone, nothing can be recorded (the page still confirms,
  truthfully, that nothing more can be sent through Vectorae). The stored
  copy `messages.unsubscribe_token` is cleared on lead erasure.

### Outside the database (review fix 3)

- **Application log:** `security_audit` no longer writes IP addresses to the
  log line (the table keeps them, and erasure scrubs them there).
- **Hosting/access logs:** uvicorn's access log and the host's (Render)
  request logs record client IPs; their retention is set by the host, not by
  this code. Stated in both privacy texts; **founders must state the period**.
- **Redis:** provider responses are cached by request content, not by
  account: enrichment/verification `cache_ttl_enrichment_seconds` = 2,592,000
  s (30 days), search `cache_ttl_search_seconds` = 86,400 s (1 day). They are
  not purged on erasure (a key cannot be attributed to an account) and expire
  on their own; stated in both privacy texts and pinned by
  `tests/test_privacy_docs_truth.py`.
- **Unipile:** disconnected via the API (above).
- **Stripe Customer object** (name, email, payment methods): the adapter has
  no delete/redact call and deleting it is a **founder decision** (Stripe
  keeps invoices either way). Not done; stated in both privacy texts and the
  in-app copy.
- **Google / HubSpot / Salesforce / Slack grants:** the stored tokens are
  deleted; the grant is not revoked on the provider's side (no adapter support
  today). Stated.

### The erasure record

One `security_audit_events` row, action `account.deleted`, written in the same
transaction: `user_id` = the tombstone id, `details` = rows deleted per table,
outreach counts, billing outcome, provider disconnects, kept decisions,
retention years. No email,
no IP, no user agent (`request=None`). It survives because that table has no
FKs by design.

### B. Lead erasure (`DELETE /leads/{id}`) completed

In the same transaction as the existing lead erasure
(`account_erasure.erase_lead_correspondence`, `scrub_provenance`):

- `messages` of the lead: SCHEDULED, AWAITING_REVIEW, guard-held **and
  SENDING** messages are **cancelled first**. Then `subject`, `body`,
  `personalization_json`, `unsubscribe_token` → NULL, and `error` (a bounce
  text names the address) → NULL except on the messages this erasure
  cancelled, which carry the non-personal `lead erased on request (GDPR)`.
  Kept: status, step, channel, variant, template (the step brief), send/open
  times and counts, provider id.
- Every table in `account_erasure.LEAD_SCRUB` (review fix 4), found by
  `lead_id`, by the lead's messages (`message_id`) or by the lead's meetings
  (`meetings.lead_id`, or a booking of the lead):

  | Table | Erased | Kept |
  |---|---|---|
  | `inbound_replies` | sender → `[erased]`, body → `[erased]`, subject, AI draft, intent reason, signals, thread ref | channel, classifications, scores, times |
  | `send_reviews` | subject/body snapshots, edited subject/body, decision note, triggers | status, content hash |
  | `attribution_entries` | subject/body snapshots, evidence | outcome kind, method, times |
  | `claim_verification_log` | claim text → `[erased]`, evidence excerpt/source, replacement, unsupported tokens | verdict, category, field |
  | `meeting_outcomes` | notes, follow-up subject/body, draft error | outcome, statuses |
  | `calls` | number → `[erased]`, transcript, voicemail text/URL, recording URL, script, analysis, error; **voicemail audio file deleted** | status, duration, times |
  | `calendar_bookings` | invitee name → `[erased]`, email → `erased@deleted.invalid`, phone, timezone, notes, answers, meeting link | times, status |
  | `meetings` | title, URL, raw notes, transcript, recording URL, AI notes, summary, action items, key points, next steps, sentiment | times, status |
  | `meeting_participants` | name, email of every non-host participant | role, times; the host row untouched |
  | `meeting_prep_briefs` | content, sections, profile, opening script, script, error, URL | status, times |
  | `deals` | notes | name, amount, stage |
  | `reengagement_plans` | reason text; a scheduled/due plan is **cancelled** | due date |
  | `crm_activities` | from/to values, meta | kind, time |
  | `roleplay_sessions` | persona, objectives, feedback | status |
  | `whatsapp_optins` | phone → `[erased]`, evidence, consent text; the opt-in is marked opted-out | status, source, times |
  | `crm_notes`, `displacement_alerts`, `crm_custom_field_values` | **rows deleted** (free text about the person; no count reads them) | — |

  **whatsapp_optins decision:** erased, not kept as proof. The proof that the
  number must never be contacted again is the suppression-list entry and the
  consent-ledger erasure event written by the same request; a second copy of
  the number in the opt-in row adds no protection.
- `provenance_json`: each field keeps `source`, `confidence`, `observed_at`;
  `value` and `detail` → NULL.
- Not covered: data already synced into HubSpot/Salesforce (the CRM owner's
  to erase), `outcomes.meta_json` (ids and step/variant only).

### The send race (review fix 4)

A send task claims a message (SCHEDULED → SENDING, conditional UPDATE),
renders and commits its copy, then transmits. An erasure landing in between
used to be undone (the render re-persisted the copy) and the task still sent
to the address it had loaded. Now, immediately before `channel.send`,
`send_message_impl` re-asserts the claim the same way it took it —
`UPDATE messages SET status='sending' WHERE id=:id AND status='sending'` —
and raises `SendClaimLost` when it matches 0 rows (the erasure cancelled or
deleted the row). The handler sends nothing, never writes status or content
to a row it no longer owns, and, if the lead is erased, re-runs the lead
scrub to remove what its render re-persisted (`cancelled_lead_erased`);
otherwise it returns `lost_claim_race`. On PostgreSQL the confirming UPDATE
also row-locks the message until the task commits after transmitting, so an
erasure that starts during the transmit waits for it and then clears the
copy. A transmit already under way cannot be recalled.

## Non-goals

- Erasure **inside third-party systems** other than Unipile: Stripe keeps its
  Customer object (founder decision); Google/HubSpot/Salesforce/Slack grants
  are not revoked remotely; data synced to HubSpot/Salesforce stays there;
  Recall/Zoom/Vapi recordings stay with the provider.
- Retention/purge jobs (none are invented; see E1).
- A grace period / undo. Deletion is immediate and irreversible.
- Admin-initiated deletion of someone else's account.

## Data model and migration

Migration **`0083_billing_retention`** (`down_revision = 0080_champion_tracker`):
`billing_subscriptions.user_id` and `billable_meetings.user_id` FK
`ON DELETE CASCADE → RESTRICT` (same constraint names). No data touched;
downgrade restores CASCADE exactly. Works on SQLite (batch mode) and
PostgreSQL. `scripts/activate_with_keys.py` `TARGET_REVISION` updated. No new
columns: the tombstone uses existing columns.

## API

`DELETE /me` (above). Response 200. Contracts the frontend matches on:
`INVALID_PASSWORD` (403), `CONFIRMATION_REQUIRED` (422), 429, 409, 502.

## UI

Profile → **Delete account** danger zone: password field, "type DELETE"
field, and copy listing exactly what is kept (E1/E2) — sourced from
`frontend/src/lib/profile.ts` so the vitest suite pins it. On success the
client clears its session and goes to `/login`.

## Flag

**None, deliberately.** This is a statutory right, not an optional feature,
and the privacy policy now says the button exists; a default-off flag would
make the policy false. (Brief rule 3 targets F01–F10 features.)

## Tenancy

Every statement is scoped by `account_export.owner_clause(table, user_id)` or
by `user_id` directly; the only cross-account writes are E3's (memberships and
invitations in the owner's own workspaces; invitations to the deleted
person's own address) and the SET NULL emulation of pointers **to** the
deleted person. Tests prove another account's row in every table survives.

## Compliance review

- GDPR Art. 17 (right to erasure) — now self-serve for account holders.
  Art. 12(3) one-month response — immediate. Art. 28(3)(g) (processor deletes
  customer personal data at end of service) — prospect data is deleted with
  the account. **Needs founder/legal confirmation.**
- CAN-SPAM / CASL / PDPA — opt-outs survive account deletion (E2); erasure
  never lifts a suppression; nothing is sent after deletion (pending messages
  cancelled and deleted).
- Legal texts updated to match the code (founder decision D1, code is the
  truth); **needs founder/legal review before merge**.

## AI cost and budget impact

None: no model calls.

## Test plan (offline SQLite, plus PostgreSQL integration)

- `tests/test_account_erasure.py` — decision map completeness (a new OWNERSHIP
  table without a decision fails); one generated row in **every** owned table
  for two accounts, erase one, every table checked; the same with SQLite
  foreign keys **ON** (PostgreSQL-like enforcement of order and RESTRICT);
  tombstone; erasure record has no personal data; endpoint refusals (password,
  confirmation, API key, admin, unauthenticated, rate limit); old access and
  refresh tokens fail, login fails, the email can sign up again; credentials
  gone; nothing scheduled remains; E1 live/stub/unreachable Stripe and meeting
  anonymisation/waiver; E2; E3 owner, member, workspace header.
- `tests/test_lead_erasure_correspondence.py` — B: every `LEAD_SCRUB`
  table filled for two leads, one erased, every listed column checked (and
  the other lead untouched); re-engagement cancelled; WhatsApp opt-in revoked;
  voicemail files; stored unsubscribe token; the send race (erasure between
  render and transmit: nothing sent, copy gone) and a claim lost for another
  reason (nothing sent, row untouched).
- `tests/test_account_erasure.py` review classes — fail-closed billing (no
  key, unreachable, still active) and already-gone subscriptions; email-only
  security events; Unipile disconnect (and failure recorded); log line without
  IP; legacy and grace-window refresh after erasure; Gmail/HubSpot/Salesforce/
  Slack callbacks and Unipile notify refuse a tombstone; unsubscribe after
  account and lead erasure; files removed (and not other accounts'); `_populate`
  now fills nullable JSON columns so `details_json` scrubbing is really tested;
  re-sign-up goes through `POST /auth/signup`.
- `tests/integration/test_gdpr_erasure_pg.py` — the every-table erasure, the
  member-then-owner case and the lead-level scrub on **real PostgreSQL**
  migrated to head (RESTRICT on billing proven by an IntegrityError).
- `tests/test_billing_retention_migration.py` — 0081 on top of 0040, model
  match, downgrade.
- `tests/test_privacy_docs_truth.py` — the removed false claims cannot return;
  every `app/integrations/` module is a named processor in the policy (a new
  adapter fails until it is listed); the stated cache TTL is the configured
  one. The review banner is deliberately NOT pinned, so removing it after
  review does not fail CI.
- `tests/test_account_export.py` — owner-FK rule updated for RESTRICT on the
  two billing tables (contract change, explained there).
- Frontend: `frontend/src/tests/profile.test.ts` (wire + logic + copy);
  `frontend/src/tests/websiteClaims.test.ts` bans the removed analytics and
  "email us to delete your account" claims on the public pages.

## Success metric

Account erasure requests handled by hand: target 0 (measured from support
tickets; not promised). Every erasure leaves one `account.deleted` record.

## Unverified

- PostgreSQL: run locally (`tests/integration/test_gdpr_erasure_pg.py`), not
  against the production Neon database. The PG trigger on
  `activity_audit_events` is respected by never touching it.
- Not run against live Stripe; the 404 / `canceled` detection after a failed
  cancel is from Stripe's documented responses.
- Unipile `DELETE /accounts/{id}` is marked "verify against current Unipile
  docs" in the adapter, like its other endpoints; not run against Unipile.
- Remote revocation of Google/HubSpot/Salesforce/Slack grants is not done.
- Stripe Customer object (name/email) is not deleted — founder decision.
- Redis cache entries expire on their own TTLs (30 days / 1 day); not swept.
- Hosting/access-log retention for IP addresses is the host's; not stated by
  the code — founders to state it.
- `account_security_events` rows with no user id and no email (IP only) are
  not matched; matching by IP would scrub other people's records behind a
  shared address.
- No purge job enforces the 7-year billing retention.
