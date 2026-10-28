# F10 — Champion Tracker + Staleness Check

Status: **built behind one flag, default OFF** (`champion_tracker_enabled`).
Spec and code ship in the same PR (founder decision 2026-10-10); the founders
review at merge. Branch `feat/f10-champion-tracker`. STAR Phase 4, row F10.

This is an **"extend X" feature**. It adds no new data vendor, no new send
path and no new scheduler pattern. It extends:

* **F01's enrichment chain** — the same Apollo `LeadSource.enrich` adapter
  (30-day Redis cache), the same `EnrichmentAttempt` cost ledger, and the F01
  waterfall (`waterfall_enrichment.run_for_lead`) to find and verify a new
  address at the new company;
* **re-engagement memory** (`reengagement_memory.py`, `ReengagementPlan`) — a
  champion who moved gets a dated plan with the existing consent model;
* **the send chokepoint** (`outreach_tasks.send_message_impl`) — two guards in
  the F05 / F01 style.

---

## Problem

Competitor complaint it answers (STAR row F10): *stale titles; job changes are
a strong trigger.*

What that looks like in Vectorae today:

1. **Titles are never re-checked.** A lead's title and company are written
   once, at sourcing (`lead_tasks.enrich_leads_impl`), and provenance records
   when (`provenance_json.title.observed_at`). A sequence that starts four
   months later writes to "VP Sales at Acme" whether or not they still are.
2. **A person who left is still emailed at the old company.** Their old
   address may bounce (deliverability damage), or — worse — be forwarded to a
   successor or an IT mailbox (a message about a named person to a third
   party).
3. **The strongest trigger is thrown away.** A past positive replier or a
   customer who moves to a new company is the warmest lead an outbound team
   will ever get. Nothing notices the move.

## Recon — what already existed

| Part of F10 | Existing code | Verdict |
|---|---|---|
| Re-verify a person via enrichment | `app/integrations/apollo.py` `ApolloAdapter.enrich` (`/people/match`, 30-day `ResponseCache`), `LeadSource` interface | **Exists** — reused as is |
| Per-attempt cost ledger | F01 `EnrichmentAttempt` + `waterfall_enrichment._ledger` / `_call_cost` | **Exists** — reused (new `step` values only) |
| Find + verify an address at the new company | F01 `waterfall_enrichment.run_for_lead` (suppression per person, stop at first valid, ledger) | **Exists** — reused with two F10 finder steps |
| How old a title is | Part 1 F10 provenance (`provenance_json.title.observed_at`) | **Exists** — read |
| "Come back later" memory | `ReengagementPlan`, `reengagement_memory.py` (CRM next-action mirror, due sweep, notification, auto-send setting) | **Exists** — extended with one plan kind (`job_change`) |
| Positive repliers | Part 1 F1 `InboundReply.intent_label == "interested"` | **Exists** — read |
| Customers | `LeadStatus.CLOSED_WON`, `Deal.stage == WON` | **Exists** — read |
| Held-and-releasable message state | F01 Bounce Shield (`FAILED` + error prefix, `requeue_blocked`) | **Exists** — same pattern, own prefix |
| Send-time role check, departure block, move detection, re-engagement lead | — | **Missing** — new `app/services/champion_tracker.py` |

About half exists; the new code is the decision logic, the guard and the
sweep, with one small table.

### What "customer" means in the data model

F05's CRM-synced exclusions (`GuardrailExclusion`, reason `customer`) are
**domains / companies / contacts**, not people with a title — there is no one
to track there. A *customer champion* is therefore a **lead** that is
`CLOSED_WON`, or that a `WON` `Deal` points at. F05's customer list still
matters: a re-engagement lead is never created at a company the account has
excluded (see Compliance).

---

## Behavior

### Flags and settings (`app/services/system_settings.py` DEFAULTS)

| Key | Default | Meaning |
|---|---|---|
| `champion_tracker_enabled` | `False` | The staleness hold at send time, the re-verification sweeps, and move handling. |
| `champion_staleness_days` | `90` | Role data older than this is re-verified before a send. Clamped to **60–365** (below 60 a re-check could be answered from the 30-day enrichment cache and prove nothing). |
| `champion_refresh_run_cap` | `100` | Most provider role checks one sweep run may make (all accounts). |
| `champion_account_monthly_units` | `200` | Most provider units F10 may spend per account in a rolling 30 days, counted from the `EnrichmentAttempt` ledger. |

Not plan-gated: the departure block is a safety guard (never a paid tier),
and the spend is capped by the two caps above. If the founders want the
champion refresh to be a paid capability, that is a `PLANS` key added to
every plan in a follow-up — not done silently here.

### 1. Departure block — SAFETY GUARD (always on once a move is recorded)

In `send_message_impl`, right after the F05 exclusion check, **every
channel**: if the account has an un-dismissed **champion move** whose old
lead is this lead, **or whose old address (SHA-256) is this lead's address**,
the message is `CANCELLED` (`error = "champion_tracker: left <company> ..."`),
a `compliance_audit_log` row `blocked_left_company` is written, the
enrollment is stopped (`suppressed_left_company` — the SUPPRESSED category),
and the task returns `cancelled_left_company`. Nothing is rendered, no model
call, no cap slot.

This check is **not** behind the flag. A move can only be recorded while the
flag is on; once recorded it is a fact, and switching the flag off must not
re-open a departed person's old company address. With no move rows (the
state of every deployment until the flag is first turned on) the outcome is
identical to before — one indexed lookup, nothing else. A lookup error
**fails closed** (deferred one hour, like F05).

The address match means another lead of the same account carrying the same
old address (the same person in a second campaign) is blocked too.

**The person, not the record.** A lead created *from* a move inherits the
person's opt-outs recorded on any **earlier** record, checked **at send
time**: if the old lead is suppressed (email, phone or LinkedIn), has an
unsubscribe / opt-out outcome or the `unsubscribed` kill signal, or was
erased, the new lead's message is cancelled (`cancelled_suppressed`, audit
`blocked_suppressed`, enrollment stopped). The chain of moves is followed
back (owner-scoped, up to 10 links). Send-time was chosen over copying the
opt-out when it is recorded: there are several places an opt-out is written,
and a send-time check cannot be forgotten by any of them. Also not behind
the flag.

**Fails closed when the owner cannot be resolved.** `active_move` raises
`OwnerUnresolved` instead of returning "no move"; the chokepoint defers the
message an hour, like any other lookup error.

The same block guards the one cold contact path outside the chokepoint,
`POST /leads/{id}/call` (the manual AI call, where F05 also added its check):
409 before any voice provider is touched.

### 2. Staleness hold (flag on)

Also in the chokepoint, right after the departure block, every channel. The
role's **observed-at** is, in order: the last role check that confirmed the
role (`enrichment_json.role_check`), else the provenance timestamp of the
title, else the lead's `created_at`. If it is older than
`champion_staleness_days`, or the last role check found a **different title
at the same company** (`title_changed`), the message is **held**:

* `status = FAILED`, `error = "champion_tracker: <reason>"` — the F01
  pattern: kept, visible on the message and in `GET /champions/held`, and
  releasable. It is never silently sent and never silently dropped.
* audit row `held_role_unverified`; the enrollment is **not** stopped;
  nothing is rendered; result `held_role_unverified`.

**No provider call is made in the send path.** A held message *is* the
re-verification request: the `champion-reverify-held` sweep (every 15
minutes) picks up held leads and re-verifies them, within the caps.

**A held step is still pending.** The follow-up sweep
(`outreach_tasks._has_pending_message`) counts a message held by any send
guard — F01's `bounce_shield: ` and F10's `champion_tracker: ` prefixes, one
shared list in `app/services/send_holds.py` — as pending for its step, so it
never creates a second copy of a held step. A release (`send_holds.release`,
used by both guards) puts back **at most one** message per (sequence, step)
and cancels any extra held copy, and any held copy of a step that already
has a scheduled / sending / sent / awaiting-review message.

### 3. Re-verification (one role check)

`champion_tracker.reverify(lead)`:

1. Refuses an **erased** (GDPR tombstone) or **dropped** lead outright: no
   call, no ledger row, and nothing — not even role-check state — is ever
   written onto an erasure tombstone. Skips (no spend) a lead already
   recorded as moved (under itself or under its address), or whose person is
   suppressed or opted out on an earlier record; its held messages are
   released so the chokepoint's departure block / suppression checks cancel
   them with the ordinary handling and audit.
2. Checks the account's rolling 30-day F10 units against
   `champion_account_monthly_units` — exhausted ⇒ no call, retry tomorrow,
   messages stay held.
3. Calls `LeadSource.enrich` with the provider person id, else the email,
   else name + company (exactly the sourcing call; cached 30 days). Ledger
   row: `step="champion_role"`, `operation="role_check"`, the outcome,
   `cached`, `cost_units`, company domain only.
4. Compares the provider's **own** `person.title` and `person.organization`
   (never the adapter's merged fields, which fall back to what we already
   had and would always "confirm" the old company):

| Result | Rule | Effect |
|---|---|---|
| `current` | same company (domain, else normalised name) and same normalised title | observed-at = now (a cached answer counts as 30 days old); held messages released (back to `SCHEDULED`, due now — every gate runs again) |
| `title_changed` | same company, different title | stays held; a person confirms (and may adopt the new title) |
| `left` | a **different** company is identified | **move** (below) |
| `not_found` / `inconclusive` | no person, or no comparable company | stays held; retried after 7 days (fails closed) |
| `error` | provider exception | stays held; retried after 1 day |

### 4. A move

On `left`:

1. A `champion_moves` row (old lead, from/to company and title, old address
   hash, champion kind, trigger), **committed as `pending` first** — the row
   alone already blocks the old address. `UNIQUE(old_lead_id)` makes it
   idempotent. Steps 2–8 then run and set the final outcome. If they crash,
   the held sweep finds the `pending` move without a derived lead and
   finishes it (`retry_pending_moves`, for 7 days; after that it stays
   `pending`, visible in `GET /champions/moves`; a move whose old lead no
   longer exists ends `failed`). The retry re-runs step 2 idempotently and
   only writes another audit row if it cancelled or stopped something.
2. **Old address closed**: every `SCHEDULED`, `AWAITING_REVIEW` and F10-held
   message of the old lead (and of the account's other leads with the same
   address) is cancelled, their enrollments stopped, the old lead's open
   re-engagement plans cancelled ("left the company"), audit row
   `left_company_detected`. From then on the chokepoint guard blocks any new
   message to that address.
3. **Champion?** Only a past positive replier (`intent_label = interested`)
   or a customer (`CLOSED_WON` / won deal) gets a re-engagement lead. Anyone
   else: the move is recorded (`not_champion`) and nothing is created — a cold
   prospect at a new company is new prospecting, not re-engagement.
4. **Opt-outs carry over**: if the person is suppressed on any identifier
   (email, phone, LinkedIn profile), unsubscribed / opted out on any outcome,
   or has kill signal `unsubscribed`, **no lead is created** (`suppressed`),
   and the new address the provider returned (if any) is added to the
   suppression list (`reason="opt_out_carried_over"`). Suppression is keyed by
   address, so without this a person who opted out under their old address
   would be reachable under their new one.
5. **Guardrails** (when F05 is on): a contact exclusion on the person's old
   identity, or any exclusion covering the new company / domain / profile ⇒
   no lead (`excluded`).
6. An existing lead of the account at the new company for the same person
   (same email or same LinkedIn profile) is linked, not duplicated
   (`existing_lead`).
7. Otherwise a **new lead** in the same campaign: `source="champion"`, name,
   new title and company, the same LinkedIn profile (so a LinkedIn opt-out
   still applies), **no phone**, and an email **only after verification**:
   the F01 waterfall runs with two F10 steps (`champion_apollo` = the
   address the role check already returned, cost 0; `champion_hunter` = the
   Hunter finder), every candidate suppression-checked and verified. The
   old address is never a candidate. The address the waterfall settles on —
   Hunter's included — gets the same F05 exclusion check as the provider's
   (when F05 is on): excluded ⇒ the lead is `DROPPED`, outcome `excluded`,
   no plan. Valid → `VERIFIED`; catch-all / unknown
   → `FLAGGED` (Bounce Shield rules apply when on); invalid / none / no
   verifier / no budget → no email, `ENRICHED`. A suppressed candidate drops
   the person (`suppressed`).
8. A **re-engagement plan** (`reason_kind="job_change"`) is created for the
   new lead, already `due`, mirrored onto the CRM next action, and the owner
   is notified (`reengagement_due`). Outcome `lead_created` — this is what
   the "champion re-engagements" metric counts.

### 5. Consent model — no auto-enrollment

Nothing in F10 enrolls a lead in a sequence or schedules a message. The new
lead waits for a person. Re-engagement memory's existing setting
`reengagement_memory_auto_send` (default **off**) only acts on `scheduled`
plans whose lead already has a sent message (`schedule_touch` needs one); a
new champion lead has none, and its plan is created `due`, so the sweep never
touches it. If a user later reschedules the plan *and* has enrolled the lead
*and* auto-send is on, the touch is an ordinary message through every gate —
exactly the existing re-engagement memory behaviour, with a brief that says
"they changed jobs" and never pretends the prospect said anything.

### 6. Scheduled refresh (flag on)

Beat `champion-refresh` daily 04:40 UTC (`leadpilot.champion.refresh`,
`default` queue — not `pipeline`, whose two slots run 144-step builds): per account (owner-filtered queries), champions whose role
is older than `champion_staleness_days`, not moved, **not carrying an
address another lead was recorded as having left** (already blocked — a
check would only spend), not erased or dropped, not suppressed or opted out
on an earlier record, not in a retry back-off, oldest first — within the run cap and each account's monthly
unit cap. Idempotent: a checked lead is fresh for N days; a move is unique
per old lead; a plan is created once per move.

Beat `champion-reverify-held` in every worker burst (30 minutes by default, at +2)
(`leadpilot.champion.reverify_held`, `default` queue): leads with held
messages — released when no longer stale, otherwise re-verified within the
same caps. **With the flag off this sweep releases every remaining hold**, so
switching the feature off returns the send path to exactly what it was (the
departure block, a recorded fact, stays).
Moves whose follow-through crashed (see §4) are finished first, and an
erased or dropped lead's holds are **cancelled** — never released, never
looked up.

### 7. Human actions

* **Confirm still in role** (owner/manager, reason required, audited
  `role_check_confirmed`): records a manual confirmation (fresh for N days),
  optionally adopts the provider's new title, releases held messages.
  Refused (409) when a move is recorded — dismiss the move instead.
* **Dismiss a move** (owner/manager, reason required, audited
  `champion_move_dismissed`): for a wrong detection. Lifts the departure
  block for **future** messages. Cancelled messages and stopped enrollments
  stay cancelled (a stop is irreversible by design); the re-engagement lead,
  if created, is left for the user to keep or delete.
* **Re-verify now**: runs one role check immediately (rate limited, caps
  apply).

---

## Non-goals

* No new data vendor; no LinkedIn scraping for job changes.
* No auto-enrollment, no auto-send (see §5).
* No re-engagement lead for non-champions.
* No automatic title update without a person (title drift is held, not
  rewritten).
* No reversal of cancelled messages when a move is dismissed.
* No dollar pricing (units, like F01; dollars are F07).

## Data model and migration

Migration `0080_champion_tracker` (`down_revision = "0079_pilot_mode"`),
**create-only**, one table:

`champion_moves`

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| user_id | UUID FK users CASCADE, indexed | owner (tenancy) |
| old_lead_id | UUID FK leads SET NULL, **UNIQUE** | idempotency |
| new_lead_id | UUID FK leads SET NULL, indexed, nullable | the re-engagement lead |
| old_email_sha256 | String(64), indexed, nullable | the departure block by address — a hash, not the address |
| champion_kind | String(20), nullable | `positive_reply` / `customer` / NULL |
| from_company, from_title, to_company, to_title | String(200), nullable | |
| to_domain | String(255), nullable | company domain |
| trigger | String(20) | `send_hold` / `refresh` / `manual` |
| outcome | String(30) | `pending` (follow-through not finished) / `lead_created` / `not_champion` / `suppressed` / `excluded` / `existing_lead` / `no_company` / `failed` |
| detail | String(300), nullable | |
| detected_at | timestamptz | |
| dismissed_at, dismissed_by_user_id (FK users SET NULL), dismiss_reason | | |
| created_at / updated_at | | |

Per-lead state lives in `leads.enrichment_json.role_check`
(`{state, checked_at, observed_at, attempted_at, retry_after, source,
observed_title, observed_company, by, reason}`) — wiped by GDPR delete with
the rest of `enrichment_json`. Downgrade drops the table (moves are lost; the
departure block with them — documented, not lossless).

`account_export.OWNERSHIP` gains `champion_moves: ["user_id"]`;
`scripts/activate_with_keys.py` `TARGET_REVISION = "0080_champion_tracker"`.

## API

All under the existing auth chokepoint; leads resolved with
`app/api/leads.py::_owned_lead` (another account's lead → 404); every move
query filters `user_id`.

| Method | Path | Notes |
|---|---|---|
| GET | `/leads/{id}/role-check` | role state, observed-at and age, the send decision, held messages, the move (if any), this lead's F10 ledger rows, settings |
| POST | `/leads/{id}/role-check/refresh` | one role check now. Rate limited (`champion_refresh`, `RATE_LIMIT_AI_ACTION`). 409 when the flag is off, the lead is erased or dropped, or no lead-source credentials exist |
| POST | `/leads/{id}/role-check/confirm` | `{reason, adopt_title?}` owner/manager; 409 when a move is recorded |
| GET | `/champions/moves?limit=&offset=` | the account's moves |
| POST | `/champions/moves/{id}/dismiss` | `{reason}` owner/manager; 404 for another account's move |
| GET | `/champions/held` | the account's held messages |
| GET | `/champions/metrics?days=` | the success metric (below) + budget |

## UI

Lead detail → **Role check** panel
(`frontend/src/components/leads/ChampionPanel.tsx`): whether the next
message would be held and why, the role check result (with the provider's
title/company when it differs), held messages count, the move (new company,
link to the new lead), **Re-verify now**, **Confirm still in role** (reason).
Hidden while the flag is off and nothing is recorded. The re-engagement
memory panel shows a `job_change` plan as "Changed jobs" with an opener that
acknowledges the move. The metric endpoint has no page yet (F09's scorecard
is the intended consumer).

## Tenancy

* `champion_moves.user_id` on every read and write; the departure block
  matches only the sending account's moves.
* Champion selection joins `Lead → Strategy → Product.user_id == owner`;
  deals additionally filter `Deal.user_id == owner`.
* Held-message listing and metrics join through the same ownership chain.
* Ledger rows carry the owner (`notifications.owner_of_strategy`).
* The Redis response cache is shared across accounts, as it already was
  (single-tenant per P0-D).

## Compliance review (CAN-SPAM / GDPR / CASL / PDPA)

* **Only removes sends.** Both guards cancel or hold; a release returns a
  message to `SCHEDULED` and it passes suppression, F05, Bounce Shield,
  consent rules, window, caps, mailbox health and the review queue again.
  The suppression check still runs **before** both guards.
* **Never the old company address.** Departure ⇒ cancellation of every
  pending message + a permanent, flag-independent guard keyed by lead and by
  address hash. The new address is never the old one.
* **Opt-outs follow the person** (§4.4): no new lead, and the new address is
  suppressed. A LinkedIn opt-out covers the new lead because the profile is
  carried over. Contact-level F05 exclusions carry over too.
* **No auto-enrollment / no auto-send** (§5): CASL and GDPR both make the
  first contact at a new employer a fresh decision; F10 puts that decision in
  front of a person instead of making it.
* **GDPR erasure follows the person.** `DELETE /leads/{id}` on either the
  old lead or the re-engagement lead also erases every lead of the same
  account linked to it through moves (old ↔ new, transitively), using the
  same erase steps (suppression of email / phone / LinkedIn, tombstone,
  signals, drafts, CRM link). Erasure **cancels** any message a guard holds
  for an erased lead (F01's or F10's), and no sweep, refresh or endpoint
  ever looks up or writes to an erased (or dropped) lead again.
* **Opt-outs follow the person after the move too** — see the departure
  block section: checked at send time against every earlier record.
* **GDPR minimisation.** No phone carried over; the ledger stores domains
  only; the move stores an address **hash**. GDPR delete of either lead
  scrubs the move's companies and titles (the row stays as a non-personal
  tombstone so counts survive). Deleting the **old** lead also drops the
  hash — that delete suppresses the address itself; deleting the **new** lead
  keeps it, because nothing else would keep the old address closed. The move
  is in the account export.
* **Lawful basis** (legitimate interest, B2B): re-checking a known
  relationship's employer with the same vendor and fields used at sourcing is
  the same processing purpose; creating a lead for a **champion** (someone
  who replied positively or bought) rests on that existing relationship,
  which is why cold prospects get no new lead. *This is an engineering
  reading, not legal advice — founders' review item.*
* **Out-of-scope finding (reported, not fixed):** GDPR delete of one lead
  still does not erase a *different* lead row for the same person that F10
  did not link (the same person sourced into two campaigns — pre-existing).
  The leads F10 links by a move **are** erased together (above).

## AI cost and budget impact

**No model call.** F10 makes no Claude/OpenAI call, so no `usage_meter`
scope is needed (and `app/api/metering.py` / PR #61 is not involved). Data
spend: one Apollo enrich per role check (cached 30 days), plus — only for a
champion who moved — at most one Hunter finder and two verifications. Hard
caps: `champion_refresh_run_cap` per run and `champion_account_monthly_units`
per account per 30 days, metered from the `EnrichmentAttempt` ledger
(`step LIKE 'champion%'`). The send path spends nothing.

## Test plan (all offline: SQLite, scripted sources/verifiers, no network)

`tests/test_champion_tracker.py`:
* flag off = send byte-identical, no ledger rows, no holds; the departure
  block still applies to a recorded move;
* staleness: fresh sends; stale is held (FAILED + prefix, audit, enrollment
  alive, nothing rendered); provenance and role_check observed-at; clamp;
  title change held;
* re-verify: current releases and the next send goes out; title change
  stays held; not found stays held with back-off; error ledgered;
  budget exhausted makes no call; cached answer ages 30 days;
* move: old messages cancelled (scheduled, held, awaiting review), other
  same-address leads blocked, enrollments stopped, old plans cancelled; a
  *new* message to the old lead / old address is cancelled at the chokepoint;
* champion: positive replier and customer get a new lead with verified email,
  `job_change` plan due, notification; non-champion gets none; suppressed
  (email, LinkedIn, unsubscribe outcome) gets none and the new address is
  suppressed; a suppressed new candidate drops; excluded new company gets
  none; existing lead linked; the old address is never used;
* sweep: champions only, owner-filtered, oldest first, run cap and account cap,
  idempotent second run;
* API: tenancy (another account's lead / move → 404), RBAC (SDR → 403),
  refresh 409 when off, confirm releases, dismiss lifts the block,
  metrics math (sends after departure = 0, re-engagement count).
* Review fixes (same file): a held step (F10 **and** F01) is pending for the
  follow-up sweep and a release puts back one message per step; erased /
  dropped leads are never looked up or written to and their holds are
  cancelled; erasure and opt-outs reach the linked lead; an unresolvable
  owner defers; a crashed move is finished by the sweep; address-blocked
  twins cost no lookup; a Hunter-found address is F05-checked.
* `tests/test_champion_tracker_migration.py` — table == model, unique, FKs,
  downgrade.
* Existing: `test_account_export`, `test_celery_routing` (beat tasks
  registered and routed), `test_rate_limit_ordering` (new paid route added),
  `test_reengagement_memory`.
* Frontend vitest `championTracker.test.ts`; `reengagementMemory.test.ts`
  gains the `job_change` cases.

## Success metric

*Zero sends to people confirmed to have left; count of champion
re-engagements* — **measured, not promised**, by `GET /champions/metrics`:

* `sends_after_departure`: `SENT` outcomes on messages to a moved lead (or a
  lead with the moved address) with `sent_at > detected_at`, un-dismissed
  moves. Target 0.
* `champion_reengagements`: moves with outcome `lead_created` in the window,
  with the new leads' progress (contacted / replied / meeting booked).

**Current value: UNMEASURED** — no pilot has run.

## Unverified

* **Apollo `/people/match` fields** (`person.title`, `person.organization.name`,
  `primary_domain`, `person.email`) are the existing adapter's
  `# TODO: verify` mapping, not checked against current Apollo docs here.
  Apollo's `employment_history[].current` would be a stronger signal and is
  **not used** for that reason.
* **Whether Apollo refreshes a person's employer promptly** after a job
  change is unknown — the feature detects a move only as fast as the vendor
  learns about it.
* **Credit cost per call** (1 unit per enrich / finder / verify) is a unit
  count, not vendor billing (same as F01).
* **Title normalisation** is a heuristic (case, punctuation, "of/and/the");
  "VP Sales" vs "Head of Sales" counts as a change and is held for a person.
* **No live vendor run**: nothing here was exercised against real Apollo or
  Hunter, or in production.

## Help-center article to write

**"Why a message is held for a role check, and what happens when a champion
changes jobs"**

* With `champion_tracker_enabled` on, a message to a prospect whose title and
  company were last confirmed more than `champion_staleness_days` (90) ago is
  held, not sent (`app/workers/outreach_tasks.py:227`;
  `app/services/champion_tracker.py:355` `staleness_hold`).
* A held message shows `champion_tracker: …` as its error and is listed at
  `GET /champions/held` (`app/api/champions.py:146`); it is re-checked within
  about 15 minutes (`app/workers/celery_app.py:428`, `champion-reverify-held`).
* If the prospect still holds the role, the message is released and every
  normal check runs again (`app/services/champion_tracker.py:388`
  `release_held`).
* If they changed title at the same company, an owner or manager confirms the
  role on the lead's Role check panel, optionally adopting the new title
  (`app/api/champions.py:100`; `app/services/champion_tracker.py:1088`).
* If they left, every pending message to them is cancelled and their old
  company address is never messaged again unless the move is dismissed
  (`app/services/champion_tracker.py:829` `close_old_address`, `:330`
  `departure_block`).
* A past positive replier or customer who moved gets a new lead at the new
  company with a "Changed jobs" re-engagement plan — never enrolled or sent
  automatically (`app/services/champion_tracker.py:991`;
  `app/services/reengagement_memory.py:490` `plan_for_job_change`).
* Someone who opted out is never re-created at a new company, and their new
  address is suppressed (`app/services/champion_tracker.py:461`
  `opt_out_reason`).
* Spend is capped per run and per account per 30 days
  (`app/services/system_settings.py:316` `champion_refresh_run_cap`, `:318`
  `champion_account_monthly_units`).
