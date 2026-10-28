# Daily send approval — a person says "yes" to automatic sending once per day

Status: built (branch `claude/send-approval`). **This is a legal/safety guard**
in the send chokepoint. Founder decision, not an optional feature: the flag
defaults **on**.

## Founder decision (Rehan, 2026-10-11, verbatim)

> "users decide this every time when Vectorae have to send automatically
> Messages Vectorae send mail to user notify on mobile Desktop and App also To
> make Decission Tell LinkedIn can be restrict with this action do you want to
> proceed user select this ones a day"

Read as: before Vectorae sends anything **automatically** on a given day, the
account owner (or a workspace owner/manager) is notified by email and on the
phone/desktop app and makes the decision; for LinkedIn they are told plainly
that LinkedIn can restrict the account for this and asked whether to proceed;
the choice is made once a day.

## Problem

Vectorae sends outreach automatically: sequence steps on a schedule, automatic
follow-ups, post-sequence re-engagement, "not now" return visits, and (when an
account opts in) Reply Copilot auto-answers. LinkedIn sends go out through
Unipile using the customer's own LinkedIn account. LinkedIn's User Agreement
restricts automated tools (see Compliance review), so automatic LinkedIn
sending carries a real risk of the customer's LinkedIn account being restricted
or suspended — a risk the customer must take knowingly, not by default. The
founders' rule is zero tolerance for legal risk; an automatic send nobody
decided on that day is not acceptable.

## Recon — what already existed

| Building block | Where | Used how |
|---|---|---|
| Send chokepoint with fail-closed guards (F05/F10/F01/F03) | `app/workers/outreach_tasks.py::send_message_impl` | The gate is one more guard in the same style |
| Deferral convention ("deferred, never dropped") | caps, window, F05 lookup error | Same: message stays `SCHEDULED`, `scheduled_at` moves |
| Send-review queue (per-message human approval) | `app/services/send_review.py` | An APPROVED review = a human decision -> not gated |
| Reply Copilot drafts with `auto_sent` | `app/services/reply_copilot.py`, `reply_drafts` | Person-approved answer = human; auto-sent answer = automatic |
| Notification hub (FCM push + Slack + webhooks) | `app/services/event_bus.py`, `app/workers/notification_tasks.py`, `app/services/notifications.py`, `device_tokens` | New event `send_approval_needed` |
| Transactional email gateway | `app/services/email_sender.py` | The owner email. **Never** the outreach (Gmail) path |
| Compliance audit log | `compliance_audit_log`, `app/services/compliance_audit.py` | One row per decision |
| Workspace RBAC (owner/manager/sdr/viewer) | `app/services/rbac.py` | Decisions need owner or manager |
| System settings flags | `app/services/system_settings.py` | Two new keys |

Nothing in the codebase approved automatic sending per day, so this is new; it
extends the chokepoint rather than adding a parallel send path.

**Not present (said plainly):** there is **no web push / desktop push**
service (no service worker, no VAPID keys) and **no in-app notification bell**.
"Desktop" is therefore covered by (a) the transactional email and (b) the
in-app banner shown on every app page while today's decision is outstanding.
No web-push service was built (out of scope by instruction). Push reaches the
Android/Capacitor app through the existing FCM device tokens.

**Account timezone:** no user/account timezone column exists. The only place a
user states their own timezone is their calendar availability
(`calendar_availability.timezone`, per row). The approval day is therefore the
calendar day in the timezone of the owner's earliest active availability row
(ordered by `created_at`, then `id`, so two rows created in the same instant
still give one deterministic answer); with no such row (or an invalid zone name) it is **UTC**, and the API and the
approval page say so (`timezone_source: "utc_fallback"`).

## Behavior

### Settings (`app/services/system_settings.py` DEFAULTS)

| Key | Default | Meaning |
|---|---|---|
| `daily_send_approval_enabled` | **True** (founder decision) | Gate automatic sends on today's approval |
| `daily_send_approval_channels` | `"email,linkedin,whatsapp,phone"` | Channels whose automatic sends need it. Unknown names are ignored; a value with no valid name gates **every** channel (fails closed) |

Flag off = exactly the previous behaviour: the chokepoint does one settings read
and nothing else changes (tested).

### What counts as automatic vs human (decided from the code)

| `messages.origin` / situation | Created by | Automatic? |
|---|---|---|
| `NULL` — ordinary sequence step | `sequence_engine` on enrollment / after each send | **Automatic** |
| `NULL` — automatic follow-up / stalled-step recovery | `send_followup_impl` (beat sweep, `outcome_source="auto_followup"`) | **Automatic** |
| `reengagement` | Feature 5 sweep (`send_reengagement_impl`) | **Automatic** |
| `notnow_memory` | re-engagement memory sweep (`reengagement_memory.schedule_touch`, auto-send setting) | **Automatic** |
| `reply_copilot`, draft `auto_sent = false` | a person pressed Approve on that exact answer (F04) | Human — not gated |
| `reply_copilot`, draft `auto_sent = true` | F04 auto-send of a "send me a time" answer | **Automatic** |
| `reply_copilot`, no draft row found | (should not happen) | **Automatic** (fail closed) |
| any message with an **APPROVED** send review | a person approved that exact message in the review queue | Human — not gated |
| `/debug/schedule-send` rows (test-only router, never mounted in production) | `app/api/debug.py` | Automatic (no exception made) |

Not every send goes through `messages`. Every outbound send call site in
`app/` (grep for `channel.send(`, `start_chat(`, `send_draft(`, `create_call(`)
and how it is treated:

| Path | Code | Who triggers it | Gated? |
|---|---|---|---|
| Sequence / follow-up / re-engagement / not-now / Reply Copilot sends | `outreach_tasks.send_message_impl` | the scheduler | per the table above |
| **LinkedIn network BULK send** — up to 25 Celery jobs, 90 s apart, sending through Unipile on the customer's LinkedIn account | `POST /linkedin-network/messages/bulk-send` -> `network_tasks.send_queued` -> `network_messages.deliver` | a click queues them; **the jobs send later, unattended** | **Yes — automatic LinkedIn sending**: needs today's LinkedIn approval (with the acknowledgement) before each job transmits |
| LinkedIn network single send ("Send" on one saved message) | `POST /linkedin-network/messages/{id}/send` -> `network_messages.deliver` | a person, one message, now | No — a person's action |
| LinkedIn network copy / mark sent | `mark-sent` | a person (Vectorae sends nothing) | n/a |
| AI "Call now" on one lead | `POST /leads/{id}/call` (`app/api/calls.py`) -> `PhoneChannel.send` | a person, one call, now | No — a person's action (every other gate still applies there) |
| Meeting follow-up email | `meeting_followup.send_followup` -> `gmail.send_draft` | a person presses Send on a draft they reviewed | No — a person's action |
| Debug WhatsApp sends | `app/api/debug.py` (test-only router, refused outside dev/test) | developer | n/a in production |

Anything not positively identified as a person's decision is treated as
automatic.

### The gate (in `send_message_impl`) — SAFETY GUARD

Position: **after** every cancel/skip gate (suppression, F05 exclusions, F10,
F01, re-engagement switch, conversion gate, WhatsApp opt-in/window/template,
consent rule) and **after the send-window check**, **before** the daily cap /
LinkedIn account reservation / claim / render. So:

* it never spends a model call, a LinkedIn slot or cap allowance on a message
  it then holds;
* a message is only "due" for this gate when it could otherwise go out now —
  which is what makes "the first automatic send of the day is due" accurate
  (no notification at 00:00 for a message that cannot send before 09:00);
* everything after it (caps, warm-up, send review, forbidden phrases,
  one-click headers) still applies once approved.

Decision, for an automatic message on a gated channel:

| Today's state (account's local day) | Result | Message |
|---|---|---|
| approved, channel included | proceed | — |
| approved, channel **not** included | `deferred_channel_not_approved` | `SCHEDULED`, `scheduled_at` = next local midnight, `error` = `daily_send_approval: …` |
| no decision yet | `deferred_awaiting_send_approval` | same; **and** the owner is notified (once per day) |
| declined | `deferred_send_approval_declined` | same |
| lookup error (settings, owner, row, anything) | `deferred_send_approval_unavailable` | `scheduled_at` = now + 1 hour (fails **closed**) |

Never `FAILED`, never `CANCELLED`, never silently sent. A deferred message keeps
counting as "pending for its step", so the follow-up sweep does not create a
duplicate (`send_holds.pending_clause` counts `SCHEDULED`; tested end to end).

**Approval window:** every decision stores `valid_until` = the next local
midnight **as a UTC instant**, computed in the timezone in force when the
decision was made. The send path asks "which decision is in force at this
instant?" (`effective_decision`: the most recently decided row whose
`valid_until` is still ahead) and never re-derives a local date, so changing
the account's timezone afterwards cannot stretch (or shift) an approval. The
API returns that `valid_until`.

**Race guard:** after tagging a message as held, the send path re-reads the
decision under a row lock on today's row (`confirm_hold`; created if needed,
`SELECT … FOR UPDATE` on PostgreSQL) before committing. Approve and decline
take the same lock. So an approval that lands between "hold computed" and
"hold committed" is seen and the message is not parked; and an approval that
waits for the lock runs its release after the hold is committed, and releases
it. Approve's read-merge-write runs under the same lock, so two approvals for
different channels at the same moment keep both channels.

**Release:** approving releases, in the same transaction, the account's
messages deferred by this gate on the approved channels (`scheduled_at = now`,
`error = NULL`). They then go through every gate again on the next dispatch
tick (send window, caps, review, …). Approving LinkedIn also re-queues the
account's held LinkedIn network messages (spaced 90 s apart, after the commit);
one the broker refuses keeps its tag and is released by the next approval.

### LinkedIn network bulk send

`network_tasks.send_queued` checks `network_hold` before `deliver`. Without
today's LinkedIn approval the message stays `queued`, `error` =
`daily_send_approval: …`, nothing reaches Unipile, and the owner is notified
(the same once-a-day notification). Approving LinkedIn queues it again. A
lookup error holds it and re-checks in an hour (fails closed). Held network
messages are counted in today's LinkedIn count.

### Who may decide

Owner or manager (RBAC), and only a **person**: a request authenticated with a
personal API key (`request.state.auth_via == "api_key"`) gets 403 on approve
and decline, like the account export. GET still works (`can_decide: false`).

### Acknowledgement is per decision

A decline clears the LinkedIn acknowledgement. The row's
`linkedin_risk_acknowledged` is true exactly when LinkedIn is in the channels
approved for the day (acknowledged by this decision or an earlier one today
that was not declined since). Each audit row records what THAT decision
acknowledged (`linkedin_risk_acknowledged`, `linkedin_warning_version`,
`linkedin_warning_text` are null/false unless this decision approved LinkedIn),
plus `linkedin_approved` and `linkedin_acknowledged_earlier_today`.

### Queued counts

Counted only where the send path would actually send: `SCHEDULED`, due before
the end of the local day (or already held by this gate), campaign `active`,
enrollment not paused and not stopped (a Reply Copilot auto-answer may answer
the stop, as in the send path), automatic only; plus queued LinkedIn network
messages in the LinkedIn count.

### Notification (idempotent: at most once per account per local day)

Triggered from the gate the first time an automatic send is due with no
decision for the day. One `daily_send_approvals` row per (account, local date);
the notification is claimed with a conditional `UPDATE … WHERE notified_at IS
NULL`, so concurrent workers send it once. It is never retried in a loop and a
failure never affects the deferral.

1. **Email** to the account owner's address via `email_sender.send_email`
   (transactional gateway, Vectorae's own domain). Never the outreach path.
   Never to members or prospects.
2. **Push** through `event_bus` event `send_approval_needed` (queued on the
   `notifications` queue): FCM to the owner's registered devices (Android /
   Capacitor app), plus Slack if connected (existing hub behaviour). Deep link
   `/send-approval`.
3. **In-app** banner on every app page (web, desktop browser and the Android
   app — same web UI) while today's decision is outstanding and something is
   queued.

All three show today's queued automatic counts per channel and link to the
approval page; when LinkedIn is gated the email and push state the LinkedIn
restriction risk.

### LinkedIn warning (versioned)

Version `li-automation-2026-10-11`. Text shown on the approval page and in the
email:

> LinkedIn's User Agreement does not allow automated tools to send messages or
> connection requests. LinkedIn may restrict or suspend LinkedIn accounts that
> use automation, including Vectorae's automatic LinkedIn sending through your
> connected account. Do you want to proceed with automatic LinkedIn sending
> today?

LinkedIn is a separate explicit choice: its checkbox is never pre-ticked, and
the API refuses (422) a LinkedIn approval without `linkedin_risk_acknowledged:
true` and the warning version the person saw (409 if that version is stale).

## Non-goals

* No web-push service, no notification bell (not present; not built).
* No per-campaign or per-message approval (send-review queue already exists).
* No change to human-approved sends (send review, Reply Copilot approvals).
* Not a legal opinion about LinkedIn; the warning is a risk disclosure.

## Data model and migration

Migration `0081_daily_send_approval` (down_revision `0080_champion_tracker`),
create-only. `scripts/activate_with_keys.py` TARGET_REVISION updated.

`daily_send_approvals`: `id`, `user_id` (account owner, FK users CASCADE,
indexed), `local_date` (DATE), `timezone`, `status`
(`pending|approved|declined`), `channels_json` (approved channels),
`linkedin_risk_acknowledged`, `linkedin_warning_version`, `queued_counts_json`
(at decision time), `decided_at`, `valid_until` (UTC instant the decision
stops counting), `decided_by_user_id` (the actual person —
owner or manager — FK SET NULL), `notified_at`, `notify_result_json`,
timestamps. `UNIQUE(user_id, local_date)`.

The durable audit is in `compliance_audit_log` (append-only, survives account
deletion with user_id nulled): decision `send_approval_approved` /
`send_approval_declined`, channel `daily_approval`, `checks_json` = approver,
time, local date, timezone (+source), channels approved, channels gated,
queued counts, LinkedIn acknowledgement, warning version **and its exact
text**, valid-until. Rows contain no prospect personal data.

Downgrade drops the table (loses the per-day state; the audit rows stay in
`compliance_audit_log`).

## API (`app/api/send_approval.py`)

| Method | Path | Who | What |
|---|---|---|---|
| GET | `/send-approval/today` | any workspace member (read) | state, timezone (+source), valid_until, approved channels, gated channels, queued counts per channel, LinkedIn warning, `can_decide` |
| POST | `/send-approval/approve` | owner / manager, signed in as a person (API key -> 403) | `{channels: [...], linkedin_risk_acknowledged?: bool, linkedin_warning_version?: str}`. Empty or unknown channel -> 422; LinkedIn without acknowledgement or version -> 422; stale version -> 409. Channels add to anything already approved today (and still in force). Returns `released` (messages) and `released_network` (LinkedIn network messages re-queued) |
| POST | `/send-approval/decline` | owner / manager, signed in as a person (API key -> 403) | `{note?}`. Records the decline (clears the LinkedIn acknowledgement); automatic sending stays off today; approving later today is still possible |

Every query filters by the account owner's id (`current_user` is the workspace
owner; the approver recorded is the actual person, `request.state.actor`).
Decisions are rate limited like other write actions.

## UI

* `SendApprovalBanner` mounted once in the app shell: visible while enabled,
  today's status is not approved (or a gated channel with queued messages is
  not approved) and something is queued; links to `/send-approval`.
* `/send-approval` page: date, timezone (with the UTC-fallback note), queued
  counts per channel, one checkbox per gated channel (email/WhatsApp/phone
  pre-ticked only if already approved today; **LinkedIn never pre-ticked**),
  the LinkedIn warning and its own acknowledgement checkbox, Approve / Decline.
  Members without owner/manager see the state and "ask an owner or manager".
* Pure logic in `frontend/src/lib/sendApproval.ts`, tested with vitest.
* The push deep link `clienthunter:///send-approval` is routed by
  `frontend/src/lib/deeplinks.ts` (its route map is an allow-list; without the
  entry, tapping the push would do nothing).
* No sidebar entry was added (the bottom-tab bar is already full); the page is
  reached from the banner, the email link and the push.

## Tenancy

Rows keyed by owner `user_id`; GET/POST read and write only the caller's
account row; queued counts and the release join `messages -> leads ->
strategies -> products` filtered on `products.user_id = owner`. Tests prove
another account's approval never releases or counts this account's messages.

## Compliance review (CAN-SPAM / GDPR / CASL / PDPA / platform terms)

* **LinkedIn User Agreement** — section 8.2 ("Don'ts") lists using bots or other
  automated methods to access the Services, add or download contacts, or send
  or redirect messages. Our reading is that automatic LinkedIn sending through
  a connected account is within what LinkedIn restricts and can lead to account
  restriction. **Needs founder/legal confirmation** — this is a contract-terms
  risk disclosure, not a reading presented as settled law, and the section
  number must be checked against the current published agreement.
* **CAN-SPAM / CASL / GDPR / PDPA** — unchanged: the gate only holds messages;
  every existing obligation (unsubscribe, identity, suppression, region rules,
  consent) is still enforced after approval. The owner notification is a
  transactional/service message to the account holder about their own account
  (not commercial outreach), sent via the transactional gateway. **Needs
  founder/legal confirmation** that it is treated as transactional under
  CAN-SPAM (15 U.S.C. 7702(17)) and CASL s.6(6).
* **GDPR** — the new table holds the account's own decision data (no prospect
  data); it is included in the Art. 15/20 account export
  (`app/services/account_export.py` OWNERSHIP); deleting the account cascades
  it; the audit row survives with the user reference nulled, matching the
  existing `compliance_audit_log` policy.

## AI cost and budget impact

None. The gate runs before render; no model call is made for a held message.
The notification uses no model.

## Test plan (offline: SQLite, fake channels, memory email transport)

`tests/test_daily_send_approval.py` (+ `tests/test_daily_send_approval_migration.py`):
flag default True; flag off = unchanged send; no approval -> deferred (still
SCHEDULED, not failed, not sent, error prefix, next local midnight); approval ->
sends through the remaining gates; email-only approval -> LinkedIn deferred;
LinkedIn approval without acknowledgement -> 422 (and stale version -> 409);
approval expires at local midnight (timezone from calendar availability, UTC
fallback); decline then approve same day; notification once per day (second
due send does not re-notify), email via transactional memory transport only to
the owner, never via the outreach channel, push event queued; tenancy (other
account's rows invisible, approval releases only own messages); human-approved
origins (send review approved, Reply Copilot approved) unaffected while
Reply Copilot auto-sent answers are gated; lookup error -> deferred 1 hour;
audit row contents; RBAC (sdr cannot decide). Migration matches the model.
Review fixes: a personal API key gets 403 on approve/decline; an approval
landing between "hold computed" and "hold committed" does not strand the
message; a timezone change cannot stretch an approval (`valid_until`); the
availability tie-break is by id; a parallel approval for another channel is
not lost; a decline clears the acknowledgement and each audit row records only
what its own decision acknowledged; queued counts skip paused campaigns and
paused/stopped enrollments.

`tests/test_daily_send_approval_e2e.py` (flag ON, real send path): LinkedIn,
WhatsApp and phone each held -> approved -> sent; the follow-up sweep makes no
copy of a held step; re-engagement (`send_reengagement_impl`) and "not now"
memory (`reengagement_memory.run_due`) sends held then sent; F01 Bounce Shield
and F10 Champion Tracker releases go back INTO the gate; a Reply Copilot
auto-sent answer through `reply_tasks.send_reply_draft_impl` is held then sent.

`tests/test_daily_send_approval_network.py`: a bulk-queued LinkedIn network
message is held without today's LinkedIn approval (owner notified, counted),
an email-only approval does not release it, a LinkedIn approval re-queues it
and it sends, a lookup error holds it and re-checks in an hour, and a single
interactive send is not gated.

`tests/conftest.py` gets an autouse `daily_send_approval_off` fixture, the same
pattern as the existing `send_review_off`: every pre-existing suite tests other
gates and asserts `"sent"`; with the founder default on they would all see
`deferred_awaiting_send_approval`. The guard is switched back on in its own
test module, which also asserts the production default is True.

## Success metric

Share of automatic sends that went out on a day with a recorded approval for
their channel = 100% (measured: `compliance_audit_log` decisions joined to
`outcomes` SENT rows by account and local date). Measured, never promised.

## Unverified

* FCM push delivery to a real device (the hub is unit-tested; Firebase is not
  exercised offline).
* Resend delivery of the notification email in production (memory transport in
  tests; `calendarharvest.com` Resend/SPF/DKIM verification is still an open
  founder check under GAP-007).
* LinkedIn's actual enforcement behaviour; the User Agreement section reference.
* Calendar-availability timezone as the account's day boundary is a proxy; a
  dedicated account-timezone setting does not exist.
