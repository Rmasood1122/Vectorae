# F09 — 30-Day Pilot Mode

**Flag** `pilot_mode_enabled` (System Settings, default **off**) ·
**Setting** `pilot_mode_verdict_grace_hours` (default **24**, clamped to 0–168) ·
**Service** `app/services/pilot_mode.py` ·
**API** `app/api/pilots.py` (`/pilots*`) ·
**Worker** `app/workers/pilot_tasks.py` (`run_pilot_sweep`, hourly at :55) ·
**UI** `frontend/src/app/(app)/pilot/page.tsx`, helpers in `frontend/src/lib/pilot.ts` ·
**Migration** `0079_pilot_mode` (tables `pilots`, `pilot_scorecards`) ·
**Not plan-gated.**

## Problem

Competitor complaint (STAR A.6, F09): *annual lock-in; buyers are advised to
pilot 30 days first.* Outbound vendors sell annual contracts, and the advice
buyers get is "run a 30-day pilot with written success criteria before you
sign anything". A pilot only protects the buyer if (1) the criteria are
written down **before** the results come in, (2) the results are measured by
the same numbers the vendor would use to sell, (3) the buyer gets everything
out at the end, and (4) nothing converts on its own when the 30 days end.

## Recon — this is "extend F08 + the GDPR export", not a new reporting system

| Already existed | Where | What F09 takes from it |
|---|---|---|
| Per-campaign cost, qualified conversations, meetings booked/held, cost per stage, reconciliation, CSV/PDF | `app/services/meetings_ledger.py` (F08: `build_ledger`, `to_csv`, `to_pdf`) | **Every number in a scorecard and a verdict.** F09 never recomputes a count or a cost; it calls `build_ledger` for the pilot's period and compares the totals to the criteria. The PDF body is F08's (its line builder is now public as `pdf_lines`; `to_pdf` is unchanged byte-for-byte). |
| Full account export (every owned table, credentials redacted, password-confirmed, audited) | `app/services/account_export.py`, `POST /me/data-export` (PR #57) | **The end-of-pilot "full data export".** The verdict page offers it as is (same password re-check, API-key refusal, rate limit and security audit). The two new tables are added to `OWNERSHIP`, so the pilot itself is in that export. |
| Transactional (system) email | `app/services/email_sender.py::send_email` | The weekly scorecard and the verdict notice. This is Vectorae's own account mail (verification, invitations, booking confirmations), **not** the outreach send path. |
| Periodic work | `app/workers/celery_app.py` beat schedule + `task_routes` | One hourly sweep, routed to `default`. |
| Workspaces / agency mode | `docs/features/workspaces.md`, `agency-mode.md` | Tenancy: a pilot belongs to the workspace owner's account (`current_user` is the owner when a member works in the workspace). An agency piloting for one client scopes the pilot to that client's campaigns (`strategy_ids`). |
| Plans and billing | `app/core/plans.py`, `app/core/billing_catalog.py`, `app/services/billing.py`, `app/integrations/stripe_billing.py` | Read only, to state the truth below. F09 adds no PLANS key and touches no billing code. |

New: the pilot record (dates, criteria locked on day 1, scope), criteria
validation, the verdict (criterion by criterion, with the raw numbers), the
weekly scorecard, the hourly sweep that ends pilots and sends mail, the
verdict export, and the page.

### The truth about annual plans and auto-renewal (checked in code)

*Updated 2026-10-11 — the founders added an annual plan
(`docs/features/annual-billing.md`).*

* **There is an annual plan, but only as a checkout the account makes.**
  `billing_catalog.BILLING_MODELS` is `("monthly", "annual", "pay_per_meeting")`;
  annual is offered only while `annual_billing_enabled` is on, needs an
  explicit "I agree to these renewal terms", and bills with Stripe interval
  `year`. Nothing converts a monthly or pilot account to annual; webhooks take
  the model from Stripe's own price interval (tests in
  `tests/test_annual_billing.py::TestNeverAutoConvert`).
* **Monthly and annual subscriptions auto-renew**, and each comes with a
  14-day trial once per account. **The defect this section used to record is
  fixed in billing, not here:** a subscription checkout made while the account
  has an ACTIVE pilot gets `trial_end = max(normal trial end,
  verdict_final_at)` (at least 48 h ahead, Stripe's minimum), so nothing is
  charged before the verdict, even for an account that already used its
  trial. A subscription that was already running when the pilot started keeps
  renewing as before — F09 still never touches billing.
* **Enforced:** `app/services/pilot_mode.py` never writes `users.plan` or
  `billing_subscriptions` and never imports billing (test pinned); billing
  *reads* `pilot_mode.get_active` / `verdict_final_at`. Tests pin that starting
  a pilot, sending scorecards, and ending it leave both exactly as they were,
  and that the monthly checkout still bills monthly. The verdict records the
  plan at start and at end and says in words that the pilot changed neither
  (the statement no longer says "Vectorae has no annual plan" — verdicts
  stored before 2026-10-11 keep the old sentence, which was true then).

## Behavior

### Starting a pilot (day 1)

An owner or manager opens **Pilot** (`/pilot`), sets the success criteria and
(optionally) which campaigns count, and starts. The template is the set of
criteria below; **no target is pre-filled** — the buyer writes their own
numbers, because a vendor-suggested target is a sales claim.

| Criterion key | Meaning | Met when |
|---|---|---|
| `meetings_booked_min` | Meetings booked (F08 `meetings_booked`) | actual ≥ target |
| `meetings_held_min` | Meetings held (F08 `meetings_held`) | actual ≥ target |
| `qualified_conversations_min` | Qualified conversations (F08 `qualified_conversations`) | actual ≥ target |
| `max_cost_per_meeting_booked_cents` | Cost per booked meeting (F08 `cost_per_meeting_booked_cents`) | actual ≤ target |
| `max_cost_per_qualified_conversation_cents` | Cost per qualified conversation | actual ≤ target |

Validation (422 otherwise): at least one criterion; only these keys; each a
whole number (not a boolean, not a float); counts 1–100,000; money
1–100,000,000 cents. Money targets are in the account's report currency at
the start (`revenue_analytics.primary_currency`), stored on the pilot.

* **Criteria are locked once the pilot starts.** There is no edit endpoint:
  moving goalposts is exactly what a pilot exists to prevent. A buyer who set
  them wrong ends the pilot (they get a verdict) and starts another.
* One active pilot per account (409 otherwise), enforced by a unique column
  that is set only while the pilot is active (`active_owner_id`), so two
  concurrent starts cannot both succeed.
* **Period:** day 1 is the UTC date of the start; the pilot covers 30 UTC
  calendar days (`start_date` … `end_date = start_date + 29`) and ends at
  00:00 UTC after day 30 (`ends_at`). The ledger is date-granular, so the
  period is whole days.
* `strategy_ids` (optional): campaigns the account owns; a campaign it does not
  own is a 404. Omitted = every campaign of the account.
* `scorecard_emails` (default true): the weekly email can be switched off at
  start or later (`PATCH /pilots/{id}`); it is the only mutable field.

### Weekly scorecard

Once 7, 14, 21 and 28 days have passed since the start (weeks 1–4, i.e. on
days 8, 15, 22 and 29), the owner's address gets one email built from
`build_ledger(start_date … today)`: each criterion's target and the
number so far, total cost, cost per booked meeting, days left, and a link to
the pilot page. It says "so far" — no projection, no forecast.

* **Idempotent per pilot per week:** the sweep claims `(pilot_id, week)` with an
  INSERT against a unique constraint before sending, so a re-run, an
  overlapping beat, or two workers cannot send twice. A failed send is retried
  on later sweeps (compare-and-set on the attempt count), at most 3 attempts.
* Only the **current** week is sent: if the worker was down across two
  boundaries, the owner gets the latest scorecard, not a burst of stale ones.
* Never after the pilot has ended; never with `scorecard_emails` off; never
  with the flag off.
* **Recipient:** the account owner's login email (`users.email` of the
  pilot's `user_id`). Never a prospect, never a workspace member's address,
  never through Gmail/outreach. A **suspended** owner is not mailed (the
  scorecard is skipped; the verdict is still stored, its notice is held).
* The pilot's status is re-read from the database before and after the claim,
  so a pilot another request has just ended gets no scorecard (a claim made
  for a pilot that ended in between is released, unsent).
* The copy says "a 30-day pilot is running on your Vectorae account" — not
  "you started", because a manager may have started it.

### Day 30 — the verdict

**Grace period.** When `ends_at` passes, the 30 days are over but the verdict
is **not frozen yet**: a meeting marked held, or a reply classified, just after
midnight on day 30 must still count. For `pilot_mode_verdict_grace_hours`
(admin setting, default 24, clamped to 0–168) the pilot shows **"verdict
pending — final numbers at <ends_at + grace>"** (`phase = verdict_pending`,
`verdict_final_at` in the API) with the live numbers for the full 30 days. No
scorecard is sent in that window. Once `ends_at + grace` has passed, the hourly
sweep ends the pilot: `status = ended`, `end_reason = completed`, and stores
the **verdict** over the full 30 days:

* per criterion: target, actual (the raw F08 number), `met` / `not_met` /
  `not_comparable`, and a note;
  * a maximum-cost criterion with a zero denominator (no meetings booked) is
    **not met** — "no meetings" never reads as "meetings cost nothing" (F08's
    null rule);
  * a money criterion whose currency differs from the ledger's currency at
    the end (the account's most common deal currency changed) is
    `not_comparable`, which counts as not met, with the reason;
* overall `met` only when every criterion is met;
* a **snapshot of the F08 ledger** for the period (totals, per-campaign rows,
  infrastructure line, reconciliation, notes — aggregates only; the per-event
  list with lead ids is deliberately not stored);
* `plan_at_start`, `plan_at_end`, and the sentence "The pilot did not change
  your plan or billing."

The verdict is stored, so **the verdict page stays readable after the pilot
ends**, exactly as it was on day 30, even if attribution later moves or a lead
is erased.

The owner then gets one **verdict email** (N of M criteria met, link to the
verdict page, how to download the export). It uses the scorecard's
claim-before-send pattern: `verdict_email_status` is set to `sending` by a
compare-and-set *before* the send; success → `sent`, an exception → `failed`
(retried by later sweeps, at most 3 attempts in all). A `sending` row — a
worker that crashed between sending and recording — is **never retried
automatically**: a missing notice is better than a duplicate, and the verdict
page is there either way.

**Ending by hand (`POST /pilots/{id}/end`, owner or manager):**
* before day 30 → `end_reason = ended_early`, verdict over `start_date … today`;
* after day 30 (in the grace period, or past it before the sweep got there) →
  `end_reason = completed`, the full 30-day verdict, frozen now (the person
  chose not to wait for the rest of the grace period);
* either way the answer is **200 with the ended pilot and its verdict**; only a
  pilot that had already ended is a 409.

**Starting the next pilot** (`POST /pilots`) while the previous one is still
`active`: if its 30 days *and* grace period are over, it is finalized first
(`completed`, verdict) and the new pilot starts; inside the grace period the
start is a 409 that names the time the verdict becomes final (or end it by
hand first); before day 30 it is the usual "already running" 409.

**Every ending path goes through one function** (`pilot_mode.end_pilot`,
compare-and-set on `status`), which always writes the verdict — this is what
makes the success metric true by construction. When two paths race, exactly
one verdict is stored and the loser returns the winner's.

**Reads never write.** `GET /pilots`, `/pilots/current`, `/pilots/{id}` and
the export never end a pilot; the sweep is the finalizer, and a stopped beat
leaves the pilot visibly "verdict pending" until it runs again or an owner or
manager ends it by hand.

**Why UTC calendar days (and not 30×24h from the start timestamp).** The F08
ledger is date-granular (`date_from` … `date_to`, whole UTC days); a 30×24h
window would start and end mid-day, and the ledger cannot count part of a day.
So day 1 is the UTC date of the start and day 30 is 29 days later. The cost:
a pilot started late in the UTC day gets a short day 1 (started at 23:59 UTC it
runs 29 days and 1 minute of wall-clock time). The setup form says so in plain
words and shows the exact dates before the buyer starts.

### End-of-pilot export

On the verdict page (ended pilots only; 409 while active or verdict pending):

1. **Verdict PDF** — the verdict (criteria, targets, actuals, outcome, plan
   statement) followed by F08's ledger PDF body for the snapshot.
2. **Verdict CSV** — `view=verdict` (one row per criterion) or
   `view=campaigns` (F08's campaign CSV of the snapshot).
3. **Full account data (JSON)** — the existing `POST /me/data-export`, with its
   password re-check. It is a personal route: it exports the signed-in
   person's own account, so the page shows the button to the workspace owner
   and tells a member that only the owner can download the account's data.

The first verdict download **by an owner or manager** sets `exported_at`
(instrumentation for the success metric: "the buyer exported"). A viewer's or
SDR's download is a read and writes nothing.

### Flag off

`pilot_mode_enabled` off (default): every `/pilots` route answers **409**
"Pilot mode is disabled by the administrator" (the F08 pattern), the sweep does
nothing (returns `{"skipped": "disabled"}`), and no other endpoint changes.
Pilots that were active when the flag went off stay as they are; the sweep
finishes them (verdict + email, after the grace period) once it is back on.

## Non-goals

* No billing change of any kind from this module: no trial, no discount, no
  plan switch, no conversion, no cancellation. (Decided 2026-10-11: billing
  itself pushes a NEW subscription's trial past an active pilot's verdict —
  `docs/features/annual-billing.md`.)
* No new metric: every number is F08's. No projection ("on track to…").
* No editing criteria after the start.
* No per-client (agency) pilot UI beyond scoping to campaigns.
* No scorecard to anyone but the account owner; no Slack/push copy.
* Not added to `e2e_journeys.py`: the pilot reads the core flow's results but
  is not a step in it.

## Data model and migration

Migration `0079_pilot_mode` (down-revision `0078_spend_guard`), create-only,
JSON (not JSONB) so it runs on SQLite (tests) and PostgreSQL.

`pilots`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `user_id` | UUID FK users CASCADE, indexed | the account (workspace owner) |
| `started_by_user_id` | UUID FK users SET NULL | the person who started it (actor) |
| `active_owner_id` | UUID, **unique**, nullable | = `user_id` while active, NULL once ended → one active pilot per account |
| `status` | varchar(20), indexed | `active` \| `ended` |
| `start_date`, `end_date` | date | day 1 and day 30 (inclusive) |
| `started_at`, `ends_at` | timestamptz | `ends_at` = 00:00 UTC after day 30 |
| `criteria_json` | JSON | validated targets |
| `currency` | varchar(3) | currency of money targets |
| `strategy_ids_json` | JSON nullable | scope; NULL = all campaigns |
| `scorecard_emails` | bool | default true |
| `plan_at_start`, `plan_at_end` | varchar(30) | |
| `ended_at`, `end_reason` | timestamptz, varchar(20) | `completed` \| `ended_early` |
| `verdict_json` | JSON nullable | criteria results + ledger snapshot |
| `verdict_emailed_at`, `verdict_email_attempts` | timestamptz, int | |
| `verdict_email_status` | varchar(20) nullable | NULL \| `sending` \| `sent` \| `failed`; claimed as `sending` before the send |
| `exported_at` | timestamptz | first verdict download |
| `created_at`, `updated_at` | timestamptz | |

`pilot_scorecards` — one row per (pilot, week): `pilot_id` (FK CASCADE),
`user_id` (FK CASCADE), `week` (1–4), `status` (`sending` \| `sent` \|
`failed`), `attempts`, `sent_at`, `error`, `summary_json` (the figures that
were emailed), timestamps. **Unique `(pilot_id, week)`** — the idempotency key.

Downgrade drops both tables (pilot history and verdicts are lost; nothing
else is touched). Documented in the migration.

## API

| Method | Path | Who | Notes |
|---|---|---|---|
| `GET` | `/pilots/template` | any member | the criteria catalogue (keys, labels, kind, unit, bounds), pilot length, `scorecard_after_days` |
| `POST` | `/pilots` | owner, manager | `{criteria, strategy_ids?, scorecard_emails?}` → 201 pilot. 409 when one is active, 404 foreign campaign, 422 invalid criteria |
| `GET` | `/pilots` | any member | this account's pilots, newest first |
| `GET` | `/pilots/current` | any member | `{pilot: …}` the active pilot, else the latest one, else `null`. Read-only |
| `GET` | `/pilots/{id}` | any member | the pilot with `phase` (`active` \| `verdict_pending` \| `ended`) and `verdict_final_at`; `progress` (live F08 numbers) while active, `verdict` once ended; another account's id → 404. Read-only |
| `PATCH` | `/pilots/{id}` | owner, manager | `{scorecard_emails}` only |
| `POST` | `/pilots/{id}/end` | owner, manager | end now → 200 with the verdict (`ended_early` before day 30, `completed` after); 409 only if it had already ended |
| `GET` | `/pilots/{id}/export?format=pdf\|csv&view=verdict\|campaigns` | any member | ended pilots only (409 while active or verdict pending); `attachment`, `Cache-Control: private, no-store`; sets `exported_at` only for an owner/manager |

SDRs are refused writes under `/pilots` by the RBAC gate
(`rbac.SDR_BLOCKED_PREFIXES`): starting or ending a commercial evaluation is a
manager's call. Viewers are read-only as everywhere.

## UI

`/pilot` (linked from Analytics › Revenue & ROI, next to the F08 card; not a
new sidebar item — the mobile bar is already at its width limit):

* **No pilot:** the setup form — a checkbox + number field per criterion
  (money in currency units, converted to cents), optional campaign selection,
  "email me a weekly scorecard", the exact dates (*day 1 is today, UTC; the
  pilot runs 30 UTC calendar days, to <date>; starting late in the UTC day
  gives a short day 1*), and plain text: *criteria are locked once the pilot
  starts; ending the pilot changes nothing in your plan or billing.*
* **Active:** day N of 30, each criterion's target vs the number so far, the
  scorecards sent, and "End pilot now" (with a confirm).
* **Verdict pending** (30 days over, inside the grace period): "Verdict
  pending — final numbers at <time>", the live 30-day numbers, and
  "Finalize the verdict now" (with a confirm).
* **Ended:** the verdict page — outcome banner (N of M criteria met), the
  criteria table with target / actual / result / note, the F08 summary
  (cost, qualified, booked, held, cost per stage), plan at start and end, the
  plan statement, and the three downloads.
* **Flag off:** one line saying an administrator can turn it on.

Pure logic (criteria form ↔ API payload, validation messages, day counter,
verdict labels, export paths) lives in `frontend/src/lib/pilot.ts` with vitest
tests.

## Tenancy

* Every query on `pilots` and `pilot_scorecards` filters `user_id ==
  current_user.id` (the workspace owner); a foreign id is a 404, never a 403
  that confirms existence.
* Campaign scope is checked against the account's own strategies
  (`products.user_id`) at start; F08 additionally intersects `strategy_ids`
  with the owned set at every build.
* All numbers come from `build_ledger(db, pilot.user_id, …)`, which
  owner-filters every query (F08 spec, Tenancy).
* The sweep iterates pilots and passes each pilot's own `user_id`; no query in
  it crosses accounts.
* Tests: another account's pilot is invisible and 404 on every route; its
  outcomes never appear in our scorecard or verdict.

## Compliance review (CAN-SPAM / GDPR / CASL / PDPA)

* **No send path to prospects.** Scorecards and verdict notices go through
  `email_sender.send_email` (Vectorae's transactional gateway) to the account
  owner's own login address. They never touch `outreach_tasks.send_message_impl`,
  Gmail, sequences or `messages`, so the outreach gate, suppression and send
  review are neither bypassed nor needed. A test asserts the only recipient is
  the owner and that no `messages` row is created.
* **CAN-SPAM / CASL:** these are transactional/relationship messages about a
  service the account holder started (CAN-SPAM "transactional or relationship
  message"; CASL s.6(6) exemption for messages about an ongoing
  subscription/use). Even so, the weekly scorecard can be switched off
  (`scorecard_emails`) and the email says how. Sender is the configured
  Vectorae from-address; no misleading subject lines (the subject states the
  week and the count of criteria met).
* **GDPR / PDPA data minimisation:** scorecards, verdicts and verdict exports
  hold aggregates and campaign names only — no prospect names, emails, lead
  ids or message text (the F08 event list is not snapshotted). The full
  account export (personal data) stays behind the existing password-confirmed,
  audited `/me/data-export`.
* **Right of access / erasure:** both tables are in `account_export.OWNERSHIP`
  (the completeness test enforces it) and cascade on account deletion. An
  erased lead drops out of live numbers; a stored verdict holds only
  aggregates, so it contains no personal data of the erased person.
* **CSV injection:** the verdict CSV reuses F08's `_cell` guard for every
  text cell; the campaign CSV is F08's.
* **Legal/safety guard added?** No new send block. The billing-state
  invariance test is a contractual guard ("no automatic conversion"); the
  orchestrator decides whether it is sealed.

## AI cost and budget impact

**Zero model calls**, so no usage scope is opened. Cost is database reads:
one `build_ledger` per pilot per scorecard (≤ 4 per pilot), one at the
verdict, one per `GET /pilots/{id}` of an active pilot (live progress). The
hourly sweep itself queries only active pilots and un-notified ended ones.
Email: at most 5 transactional emails per pilot.

## Test plan

`tests/test_pilot_mode.py` (offline, SQLite, memory email transport):

* criteria validation: unknown key, bool, float, zero, negative, over-limit,
  empty set → 422; valid set stored as given; locked (no edit route);
* lifecycle: dates (day 1 … day 30, `ends_at`); one active pilot (second
  start 409; DB unique column, and the race path's IntegrityError → 409 while
  any other IntegrityError propagates); end early writes a verdict; **every
  ended pilot has a verdict** (sweep, end after day 30, end early, start of
  the next pilot);
* grace period: the sweep does not freeze before `ends_at + grace` and does at
  it (both sides of the boundary); the setting moves the boundary and is
  clamped; a meeting marked held after midnight for day 30 counts; start
  inside the grace → 409 with the time, after it → previous finalized and
  201; end after day 30 → 200 `completed`;
* reads never write: viewer/SDR GETs and downloads of a due pilot leave it
  active; their downloads do not set `exported_at`, a manager's does;
* stale objects and races: no scorecard for a pilot another request ended
  (stale object), a claim released when the pilot ends after it; the
  `end_pilot` and verdict-email compare-and-set losers send/overwrite
  nothing; a `sending` verdict email is never retried;
* verdict against F08: the actuals equal `build_ledger`'s totals for the
  period (seeded outcomes, meetings and costs); min/max rules; zero
  denominator → not met; currency change → not comparable; overall met only
  when all are met; snapshot survives later data changes;
* weekly scorecard: sent on week boundaries only, once per week even when the
  sweep runs twice or the row exists; failed send retried, capped at 3; only
  the current week; not with `scorecard_emails` off; not after end; email
  figures equal the ledger's; recipient is the owner only; no `messages` row;
  copy does not say "you started"; a suspended owner is not mailed;
* **no automatic conversion:** starting, scorecards, ending on schedule and
  ending early leave `users.plan` and the `billing_subscriptions` row
  unchanged (with a trialing subscription and with none); the monthly Stripe
  checkout uses a monthly interval and an annual price exists only for an
  explicit annual checkout (`test_annual_exists_only_as_an_explicit_checkout`);
* tenancy: another account's pilot is 404 on every route and never in the
  list; its outcomes never in our numbers; foreign `strategy_ids` 404;
* flag off: every route 409, sweep `{"skipped": "disabled"}` with no rows or
  email; F08 ledger endpoint unaffected;
* RBAC: an SDR cannot start or end (403), can read; a viewer can read;
* export: 409 while active; PDF starts `%PDF`; verdict CSV rows + injection
  guard; campaigns CSV equals F08's `to_csv` of the snapshot; `exported_at`
  set; headers private/no-store;
* account export includes `pilots` and `pilot_scorecards`;
* Celery: the task is routed to `default` and on the beat schedule.

`tests/test_pilot_mode_migration.py`: migration tables equal the models;
revision chain; the unique constraints exist.

`frontend/src/tests/pilot.test.ts` (vitest): form → payload conversion
(currency units → cents), validation messages, day counter, verdict labels,
export paths/filenames, disabled-error detection.

## Success metric

STAR row: **"Every pilot ends with a verdict page and export."** Measured:

* in tests — every ending path (`completed` by the sweep after the grace
  period, `completed` by "end now" after day 30 or by starting the next pilot,
  `ended_early`) leaves `verdict_json` set and the export endpoint answering
  200 for that pilot;
* in production — the read-only audit query
  `SELECT count(*) FROM pilots WHERE status='ended' AND verdict_json IS NULL`
  must be 0, and `count(exported_at IS NOT NULL) / count(status='ended')`
  is the share of buyers (owner or manager downloads only) who actually
  downloaded it (reported, not promised).

## Unverified

* ~~Founder decision — trial length vs pilot length.~~ **Decided 2026-10-11**
  ("Add Annual Plan"): a subscription checkout during an active pilot gets a
  trial that ends no earlier than the verdict (`app/services/billing.py`
  `trial_end_for`; `docs/features/annual-billing.md`). Still open: a
  subscription that was already running when the pilot started keeps
  renewing (and charging) during the pilot.
* Email delivery is through the configured transactional provider (Resend in
  production); whether `calendarharvest.com` is verified in Resend with
  SPF/DKIM is the open GAP-007 founder check — until then scorecards may land
  in spam. Not tested against a live provider.
* Numbers inherit F08's unverified points (admin-set AI/call prices and
  infrastructure rate; held meetings only when tracked in Vectorae;
  production-scale performance not measured).
* Sweep latency: a verdict lands within about an hour of `ends_at + grace`
  (default 24h), or at once when an owner/manager ends the pilot by hand. If
  the beat is down the pilot stays "verdict pending". Not measured under the
  production beat. Whether 24h is the right grace is a product judgement; it is
  an admin setting.
* CAN-SPAM/CASL classification of the scorecard as transactional is this
  spec's reading of the rules, not a lawyer's opinion.

## Help-center article to write

**"Run a 30-day pilot before you commit"**

* Turn on: an admin enables `pilot_mode_enabled` (off by default) —
  `app/services/system_settings.py` (DEFAULTS, F09 entry).
* You choose the criteria on day 1 and they are locked; no targets are
  pre-filled — `app/services/pilot_mode.py` (`CRITERIA`, `validate_criteria`).
* The pilot runs 30 UTC calendar days; day 1 is the start date (UTC), so a
  pilot started late in the UTC day has a short day 1 —
  `app/services/pilot_mode.py` (`PILOT_DAYS`, `period_for`).
* After day 30 the verdict stays "pending" for a grace period (default 24
  hours, admin setting) so late-recorded meetings and replies still count;
  then it is frozen — `app/services/system_settings.py`
  (`pilot_mode_verdict_grace_hours`), `app/services/pilot_mode.py`
  (`verdict_due`, `sweep`).
* You can end a pilot early, or finalize a pending verdict at once, with
  "End pilot now" — `app/api/pilots.py` (`POST /pilots/{id}/end`).
* A scorecard email arrives after 7, 14, 21 and 28 days, at most once per week,
  and can be switched off — `app/services/pilot_mode.py` (`send_due_scorecard`),
  `app/workers/pilot_tasks.py`.
* Every number is the Meetings-per-Dollar ledger's —
  `app/services/meetings_ledger.py` (`build_ledger`).
* When the verdict is final you get a verdict page that stays readable
  afterwards, a PDF/CSV verdict and your full account data export — `app/api/pilots.py`
  (`/pilots/{id}/export`), `app/api/account_data.py` (`/me/data-export`).
* Ending the pilot never changes your plan or billing, and a plan bought
  during a pilot is not charged before the verdict —
  `app/services/billing.py` (`trial_end_for`), `tests/test_pilot_mode.py`
  (billing invariance tests), `tests/test_annual_billing.py` (`TestPilotTrial`).
* Only owners and managers can start or end a pilot —
  `app/services/rbac.py` (`SDR_BLOCKED_PREFIXES`).
