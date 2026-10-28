# PROGRESS — overnight build (started 2026-09-16)

Branch: `feature/8-new-features`. Never merged to `master`, never force-pushed.

---

## FINAL SUMMARY — read this first

**Everything in Part 1 (all 12 features) and Part 2 (all 3 pieces) is built,
tested and pushed.** Twelve migrations, `0053` through `0064`, each additive and
each with its own migration test that asserts the schema matches the models and
that the downgrade round-trips.

### Test status

| Suite | Result |
|-------|--------|
| Backend, `tests/*.py` (135 modules, ~2,800 tests) | **green** — run in five batches for memory; every batch exited 0 |
| Frontend, `vitest` (49 files) | **657 passed** |
| `tsc --noEmit` | **clean** |
| `tests/integration/` (124 tests) | **not run — needs PostgreSQL on localhost:5433, which is not running here.** See Known issues. |

New tests added: 22 modules, roughly 700 assertions.

### The three things I would look at first

1. **Feature 5's VIP trigger, for your ICP.** Vectorae sells to boutique
   agency *owners*, and "owner" is a VIP title — so with
   `send_review_vip_titles` on (the current default) every message to your core
   persona lands in the human review queue. Correct for someone selling into
   enterprises, wrong for you. One setting in `/admin/settings`.
2. **Feature 4's complaint rate is a documented proxy**, not a real
   feedback-loop signal. It is labelled `proxy` everywhere it appears. Worth
   deciding whether to connect Google Postmaster Tools.
3. **Feature 11 wants multiple mailboxes per account** to be fully useful. The
   sending-domain pool currently *refuses* wrong-domain sends rather than
   *routing* between several mailboxes, because `GmailAccount.user_id` is
   UNIQUE. I did not relax that constraint — see Known issues for why.

---

## Scope

**Part 1 — 12 differentiation features**

| # | Feature | Status |
|---|---------|--------|
| 1 | Positive Reply Classifier | **done** (0053) |
| 2 | F-P-T-A Scoring Engine | **done** (0054) |
| 3 | Sequence Completion Guarantee + metric | **done** (0055) |
| 4 | Deliverability Health Score (per mailbox) | **done** (0056) |
| 5 | Human Review Queue for high-risk sends | **done** (0057) |
| 6 | Unified Cross-Channel Inbox | **done** (0058) |
| 7 | Re-engagement Memory ("not now" ≠ "never") | **done** (0059) |
| 8 | Transparent Attribution Ledger | **done** (0060) |
| 9 | Compliance & Consent Layer | **done** (0061) |
| 10 | Data Provenance Tags | **done** (0062) |
| 11 | Founder/Agency Mode (multi-client workspaces) | **done** (0063) |
| 12 | "Why This Prospect" Explainability Panel | **done** (built on #2, no storage of its own) |

**Part 2 — Meeting Prep & Training module**

| # | Piece | Status |
|---|-------|--------|
| P2.1 | Auto-generated meeting brief + script | **done** (0064) |
| P2.2 | Mock interview / roleplay mode + feedback | **done** (0064) |
| P2.3 | Standalone practice + pre-meeting checklist | **done** |

### Docs written

`docs/features/` — `positive-reply-classifier.md`, `fpta-scoring.md`,
`why-this-prospect.md`, `sequence-completion.md`, `mailbox-health.md`,
`send-review-queue.md`, `unified-inbox.md`, `reengagement-memory.md`,
`attribution-ledger.md`, `consent-layer.md`, `data-provenance.md`,
`agency-mode.md`, `meeting-practice.md`.

Each one leads with *why* rather than *what*, and records the decisions that
are not obvious from the code.

---

## Two bugs found and fixed along the way

These were pre-existing or self-inflicted, and are worth knowing about:

1. **An unsubscribe by email never revoked WhatsApp consent.**
   `sequence_engine.unsubscribe_lead` suppressed email, phone and LinkedIn but
   left `whatsapp_opted_in` True, so the next WhatsApp step in a sequence went
   out to someone who had asked to be left alone. Fixed in Feature 9:
   suppression is now one act over the whole person.
2. **Double-stringified request bodies in two new API modules I wrote.**
   `api()` stringifies `body` itself; passing `JSON.stringify(...)` sends a
   JSON *string* and FastAPI answers 422 — the exact bug documented at the
   bottom of `client.ts`. Caught before shipping;
   `frontend/src/tests/apiWriteBodies.test.ts` now guards every new write
   helper against it.

---

## Baseline survey (what already existed on 2026-09-16)

Read before writing anything, so nothing gets rebuilt that is already there:

- **Replies** — `InboundReply` already carries `classification` (routing: unsubscribe /
  bounce / out-of-office), `reply_category` (BUYING_SIGNAL | OBJECTION | NOT_NOW |
  WRONG_PERSON, feature 0034) and `authenticity_kind` (0043). None of these is the
  5-way interested/neutral/objection/not-now/unsubscribe taxonomy feature 1 asks for,
  and no "positive reply rate" metric exists. → new, additive column set.
- **Scoring** — `lead_scoring.py` produces `ai_booking_likelihood` (a single 0-100
  booking-likelihood number). No F-P-T-A decomposition. → new service, new columns.
- **Sequences** — `sequence_engine.py` already hard-stops only on explicit conditions,
  but there is no record of *planned* step count, no completion timestamp, and no
  completion-rate metric. → columns + metric.
- **Deliverability** — `deliverability.py` scores per *domain* per user (SPF/DKIM/DMARC
  + blacklist + bounce rate). Nothing per *mailbox*, no spam-complaint rate, no
  auto-throttle. → new per-mailbox table + scheduled refresh + send-path throttle.
- **Reviews** — `SequenceReview` (0047) reviews sequence *content* before launch. There
  is no per-*message* human approval queue. → new table.
- **Conversation** — `conversation_thread.py` builds one lead's cross-channel thread.
  There is no cross-*prospect* inbox list. → new endpoint + page on top of it.
- **Re-engagement** — `ReengagementAttempt` (0048) exists for post-sequence touches;
  `InboundReply.reschedule_date` exists but nothing auto-schedules from a "not now".
- **Compliance** — `compliance_region.py`, `compliance_audit.py`, `ComplianceAuditLog`
  and `SuppressionEntry` exist. Suppression is email/phone only (no LinkedIn/WhatsApp
  in the same act) and there is no consent-event ledger.
- **Workspaces** — `workspaces.py` has members/roles/branding/domain verification.
  No per-workspace sending-domain pool, reporting scope or billing view.
- **Meeting prep** — `MeetingPrepBrief` (FG7) generates a brief. No script structure,
  no objection list, no roleplay.

---

## Decisions log

(Appended as decisions are made — each entry says *why*, so the choice can be argued
with later.)

- **2026-09-16 — Additive-only migrations.** Every new column is nullable or has a
  server default, and every new table is standalone. Rationale: this branch will be
  merged into a database that already has production rows; a NOT NULL column with no
  default cannot be added to a live table without a rewrite, and the repo's existing
  migrations (0024–0052) all follow this rule.
- **2026-09-16 — New columns instead of overloading existing ones.** Feature 1's
  5-way intent taxonomy gets its own columns rather than widening `reply_category`,
  because `reply_category` already drives the FG1 reply-intelligence UI and drafts;
  changing its value set would silently break that feature's consumers.

---

## Log

- **2026-09-16 -- Part 2 done (all three pieces).** Migration
  `0064_meeting_practice` (script columns on `meeting_prep_briefs`, new
  `roleplay_sessions` and `roleplay_turns`), `app/services/roleplay.py`,
  `app/services/meeting_practice.py`, `app/api/practice.py`, F-P-T-A signals
  added to the existing FG7 prep prompt, and `CallScriptPanel` +
  `RoleplayPanel` on the lead page's Meeting Prep tab. Tests:
  `tests/test_practice.py` (51), `tests/test_practice_migration.py` (10),
  `frontend/src/tests/practice.test.ts` (27). Regression: 131 tests across the
  meeting suites green after one fix (below); `tsc --noEmit` clean.
  Decisions: the script is its OWN column, not another key in `sections_json`,
  because that blob is model output rewritten on every regenerate and an edit
  a regeneration overwrites is an edit nobody makes twice -- `ensure_script`
  seeds only while empty and never overwrites; seeding is NOT a second model
  call, since asking again would produce a script that disagrees with the
  brief above it; the checklist is DERIVED every time, because a stored
  checklist goes stale and then lies; practice blocks only when
  `practice_required` is set per meeting, since requiring a rehearsal for
  every call turns the checklist into something people click through; the
  roleplay prospect NEVER breaks character to coach (the prompt forbids it and
  `_clean_reply` strips it anyway) because a prospect nicer than the real one
  teaches the wrong lesson; a failed model turn keeps the seller's line; a
  session with fewer than two seller lines is recorded but NOT scored, so the
  improvement chart is not polluted; turns are ROWS with a UNIQUE
  (session, turn_no) rather than a JSON array, because a live UI appending one
  line at a time loses a line to read-modify-write whenever two requests
  overlap; scores are COLUMNS so the trend can be charted without
  backend-specific JSON extraction. Docs:
  `docs/features/meeting-practice.md`.
  Also fixed `tests/test_meeting_prep_migration.py`: it compared migration
  0023's output against the CURRENT models, so the four columns 0064 adds to
  `meeting_prep_briefs` read as columns 0023 had forgotten. It now excludes
  later-added columns by name, which keeps the test able to catch a change to
  0023 while making the dependency visible.

- **2026-09-16 -- Feature 11 done.** Migration `0063_client_workspaces` (new
  `client_workspaces` and `client_sending_domains` tables, plus
  `strategies.client_workspace_id`), `app/services/client_workspaces.py`,
  `app/api/client_workspaces.py`, a send-path gate in
  `outreach_tasks.send_message_impl`, and a new `/clients` page in the nav.
  Tests: `tests/test_client_workspaces.py` (45),
  `tests/test_client_workspaces_migration.py` (10),
  `frontend/src/tests/clients.test.ts` (19). Regression: 61 tests across
  workspaces/sequences/regression still green; `tsc --noEmit` clean.
  Decisions: a NEW table rather than reusing `Workspace`, because that is a
  TEAM around one owner with a UNIQUE `owner_user_id` that thirteen modules'
  ownership resolution rests on -- an agency's SDRs are shared across clients
  while the clients are separate books of business, so a client hangs OFF the
  team workspace rather than replacing it; the sending pool is defined over
  DOMAINS and enforced in the SEND PATH, because an isolation rule that lives
  in a form is an isolation rule that leaks; an EMPTY pool means no
  restriction, so an account that never creates a client behaves exactly as it
  does today; unassigned campaigns are the agency's own work and are surfaced
  rather than hidden, because work that belongs to nobody stops being
  invoiced; the billing view shows its working, since a retainer plus a
  per-meeting count is an invoice a client will query.
  Docs: `docs/features/agency-mode.md`. **Known limitation recorded there and
  below:** `GmailAccount.user_id` is UNIQUE (one mailbox per account), so a
  pool currently REFUSES wrong-domain sends rather than ROUTING between
  several mailboxes.

- **2026-09-16 -- Feature 10 done.** Migration `0062_data_provenance`
  (`provenance_json`, `provenance_updated_at` on `leads`),
  `app/services/provenance.py`, tags written from the enrichment, email-finding,
  verification and personalization-refresh stages,
  `GET /leads/{id}/provenance`, and a `ProvenancePanel` (plus a reusable
  `ProvenanceTag` badge) on the lead page. Tests:
  `tests/test_provenance.py` (38), `tests/test_provenance_migration.py` (5),
  `frontend/src/tests/provenance.test.ts` (16). `tsc --noEmit` clean.
  Decisions: a JSON column rather than the EAV table this schema uses for
  CUSTOM FIELDS, and for the opposite reason -- custom values are sorted and
  filtered server-side where JSON extraction differs between SQLite and
  PostgreSQL, whereas provenance is read once for one prospect and never
  queried, so a per-field table would add a join to the lead page for data
  nobody filters on; the confidence is a documented CONSTANT per source, not
  model output, because it is a claim about the source rather than the value;
  an unknown source is scored as `inferred` (the safe direction); AGE is kept
  separate from confidence because a weak source and an old fact need
  different fixes and one blended number hides which; the verifier's three
  verdicts are three SOURCES, since deliverable, risky and unknown are
  different claims. Docs: `docs/features/data-provenance.md`.

- **2026-09-16 -- Feature 9 done.** Migration `0061_consent_ledger` (new
  append-only `consent_events` table), `app/services/consent.py`,
  `app/api/consent.py`, and a `ConsentPanel` on the lead page.
  `sequence_engine.unsubscribe_lead`, the WhatsApp opt-out and opt-in paths,
  and the GDPR delete endpoint all now go through it. Tests:
  `tests/test_consent.py` (31), `tests/test_consent_migration.py` (7),
  `frontend/src/tests/consent.test.ts` (18). Regression: 99 tests across the
  compliance/WhatsApp/leads/sequence suites still green; `tsc --noEmit` clean.
  **This fixed a real gap:** `unsubscribe_lead` suppressed email, phone and
  LinkedIn but left `whatsapp_opted_in` True, so someone who unsubscribed by
  email could still be messaged on WhatsApp. It is now one act over the whole
  person. Other decisions: a separate ledger from `compliance_audit_log`,
  because that answers "on what basis did you SEND this?" (one row per send)
  and this answers "when did they tell you to stop, and what did you do about
  it?"; `lead_id` is SET NULL and the erasure event is written BEFORE the
  delete, so the proof survives the erasure it is proof of; grants are
  recorded as well as withdrawals, since a ledger of only withdrawals cannot
  show that contact was lawful to begin with; `requirements_for()` states each
  regime's demands in ONE place that the send path, the UI and the tests all
  read. Docs: `docs/features/consent-layer.md`.

- **2026-09-16 -- Feature 8 done.** Migration `0060_attribution_ledger` (new
  `attribution_entries` table), `app/services/attribution.py`,
  `app/api/attribution.py`, a quarter-hourly Celery sweep, and an
  `AttributionPanel` on the analytics page. Tests:
  `tests/test_attribution.py` (38), `tests/test_attribution_migration.py` (7),
  `frontend/src/tests/attribution.test.ts` (19). `tsc --noEmit` clean.
  Decisions: a ledger rather than a column on `outcomes`, because that table
  is an immutable log whose `message_id` is only populated for events the send
  path writes itself, while attribution is a later, re-computable judgement
  that must record its own uncertainty; the METHOD and CONFIDENCE are stored
  and always displayed, because "they replied to step 2" and "step 2 was the
  last thing we sent three weeks earlier" are different claims and presenting
  them identically is how users end up building a strategy on coincidence;
  last-touch confidence decays with the gap; a SWEEP rather than hooks,
  because outcomes are created in a dozen places and the thirteenth would be
  forgotten -- and the sweep back-fills history for free; only replies Feature
  1 labelled `interested` earn an entry, so "stop emailing me" never lands in
  a ledger of positive outcomes. Docs: `docs/features/attribution-ledger.md`.

- **2026-09-16 -- Feature 7 done.** Migration `0059_reengagement_memory` (new
  `reengagement_plans` table), `app/services/reengagement_memory.py`, a hook in
  `reply_tasks.process_inbound_reply` right after the intent label is decided,
  a daily 05:50 sweep, four new endpoints under `/reengagement/plans` and
  `/leads/{id}/reengagement-plans`, three `system_settings` keys, and a
  `ReengagementMemoryPanel` on the lead page. Tests:
  `tests/test_reengagement_memory.py` (51),
  `tests/test_reengagement_memory_migration.py` (7),
  `frontend/src/tests/reengagementMemory.test.ts` (17). Regression: 172 tests
  across the reply/CRM suites still green; `tsc --noEmit` clean. Decisions: a
  NEW table rather than `reengagement_attempts` (0048), because that row is a
  once-per-enrollment CLAIM designed to be consumed and forgotten while this
  one outlives its enrollment and carries the prospect's words; the reason is
  stored twice (a `kind` to group by, the TEXT to quote) because a hook built
  on their sentence survives being read back nine months later; the interval
  varies BY REASON (a contract renews on a different clock from a busy month)
  and a date the prospect named always wins; appropriateness is re-checked
  when the plan comes DUE, not when it was made; `auto_send` defaults OFF
  because most people want to read a nine-month-old promise before acting on
  it, and when on the touch is an ORDINARY message so suppression, compliance,
  mailbox health and the review queue all still apply. Docs:
  `docs/features/reengagement-memory.md`.
  **Also fixed a real bug I introduced in Features 5 and 6:**
  `frontend/src/lib/api/{sendReview,inbox}.ts` passed
  `body: JSON.stringify(...)`, but `api()` stringifies `body` itself -- the
  exact double-stringify bug documented at the bottom of `client.ts`, which
  would have sent a JSON *string* and got a 422 from FastAPI on every
  approve/reject/handled call. Both fixed, and
  `frontend/src/tests/apiWriteBodies.test.ts` (8) now guards every new write
  helper against it.

- **2026-09-16 -- Feature 6 done.** Migration `0058_inbox_handling`
  (`handled_at`, `handled_by_user_id` on `inbound_replies`),
  `app/services/inbox.py`, `app/api/inbox.py`, a new `/inbox` page threaded by
  prospect with the existing `ConversationThread` as the detail pane, and an
  Inbox entry second in the main nav. Tests: `tests/test_inbox.py` (34),
  `tests/test_inbox_migration.py` (5), `frontend/src/tests/inbox.test.ts` (18).
  `tsc --noEmit` clean. Decisions: `thread_detail` reuses
  `conversation_thread.build_thread` rather than re-merging the tables, so the
  lead page and the inbox can never disagree about a prospect's history;
  "handled" is a STORED flag because the two commonest ways a reply gets dealt
  with (answered from Gmail, or needs no answer) leave no outbound row, and
  inferring it from a later send would clear the thread the moment the next
  sequence step went out; it is per REPLY because a prospect who writes twice
  has two things to answer; `needs_reply` is ordered OLDEST first; the badge
  counts threads, not messages. Also fixed a latent bug in the migration-test
  helper: stripping a column from the pre-migration schema has to strip its
  foreign key too, or the prereq CREATE TABLE references a column that is not
  there. Docs: `docs/features/unified-inbox.md`.

- **2026-09-16 — Feature 5 done.** Migration `0057_send_reviews` (new
  `send_reviews` table; `MessageStatus.AWAITING_REVIEW` needed no column
  change because that enum is VARCHAR-backed),
  `app/services/send_review.py`, the gate inside
  `outreach_tasks.send_message_impl` after rendering,
  `app/api/send_reviews.py`, five new `system_settings` keys, and a new
  "Review" tab on the Campaigns page. Tests:
  `tests/test_send_review.py` (55), `tests/test_send_reviews_migration.py`
  (6), `frontend/src/tests/sendReview.test.ts` (18). Regression: 139 tests
  across engine/CRM/LinkedIn/phone suites still green; `tsc --noEmit` clean.
  Decisions: the approved SNAPSHOT is authoritative -- an approved review
  sends the words the reviewer read, discarding the next render, because
  rendering is a model call that never repeats itself and comparing would
  re-queue the message forever; rejecting cancels the MESSAGE but leaves the
  ENROLLMENT running, since one badly-timed email should not end the
  relationship; the gate fails OPEN, because a broken review system must not
  silently stop outreach; `tone_flag` reuses
  `adversarial_review._text_findings` verbatim so the pre-launch gate and this
  one cannot disagree. Docs: `docs/features/send-review-queue.md`.
  **Needs a decision from Rehan** -- see Known issues.

- **2026-09-16 — Feature 4 done.** Migration `0056_mailbox_health` (new
  `mailbox_health` table, one upserted row per mailbox),
  `app/services/mailbox_health.py`, a four-hourly Celery beat sweep
  (`refresh_mailbox_health`), the send gate in
  `outreach_tasks.send_message_impl` (paused -> defer, throttled -> lower cap),
  `/deliverability/mailboxes[/refresh]` and `.../{ref}/resume`, and
  `MailboxHealthCard` above the existing per-domain card in Settings ->
  Deliverability. Tests: `tests/test_mailbox_health.py` (50),
  `tests/test_mailbox_health_migration.py` (5),
  `frontend/src/tests/mailboxHealth.test.ts` (19). Regression: 58 tests across
  trust/sequences/celery-routing still green; `tsc --noEmit` clean.
  Decisions: a NEW table rather than more rows in `deliverability_checks`,
  because that log is per DOMAIN and per domain is the wrong grain to throttle
  on; `mailbox_ref` mirrors `messages.sender_ref` exactly so volume and
  complaints are countable per mailbox; the deduction weights are sized so a
  fully unauthenticated but quiet mailbox THROTTLES (45) while real recipient
  harm PAUSES (a complaint or bounce rate past the danger line costs 65 on its
  own); a pause DEFERS messages, never cancels them; resuming is a human
  decision only, matching the bounce and blacklist pauses.
  **Known limitation worth Rehan's attention:** there is no feedback-loop /
  Postmaster Tools integration, so `complaint_rate` is a documented PROXY
  (unsubscribes + "stop" replies over sends). It is labelled `proxy` in every
  payload and in the UI. Connecting a real FBL later changes only that number
  and the label. Docs: `docs/features/mailbox-health.md`.

- **2026-09-16 — Feature 3 done.** Migration `0055_sequence_completion`
  (`planned_steps`, `steps_sent`, `completed_at`, `stopped_at`,
  `stop_category` on `sequence_enrollments`),
  `app/services/sequence_completion.py`, `stop_enrollment` now records when
  and what kind, `schedule_next_step` counts only advancing sends,
  `skip_message` explicitly does not count, endpoints
  `/strategies/{id}/completion`, `/strategies/{id}/completion/dropped`,
  `/sequences/{id}/completion`, a `completion` block on strategy analytics,
  and `CompletionPanel` on the analytics page. Tests:
  `tests/test_sequence_completion.py` (49),
  `tests/test_sequence_completion_migration.py` (6),
  `frontend/src/tests/sequenceCompletion.test.ts` (15). Regression: 130 tests
  across the engine-touching suites still green; `tsc --noEmit` clean.
  Decisions: `planned_steps` is a SNAPSHOT because counting a sequence's steps
  at read time means adding a step next month retroactively un-completes every
  finished enrollment; `stop_category` is decided at WRITE time so a new call
  site inventing a reason lands in `other` and is logged rather than silently
  widening a bucket; running enrollments are in neither half of the rate (a
  campaign on step 2 of 5 has not failed, it has not finished);
  `dropped_unauthorised` is reported separately from the rate because 60%
  completion is fine if the rest replied and alarming if the system dropped
  them. Docs: `docs/features/sequence-completion.md`.

- **2026-09-16 — Features 2 and 12 done.** Migration `0054_fpta_scoring`
  (8 nullable columns on `leads`, `fpta_overall` indexed),
  `app/services/fpta_scoring.py`, scored at enrollment from
  `sequence_engine.enroll_leads` (after the commit, and only for prospects
  with no score yet), `GET/POST /leads/{id}/fpta[/rescore]`, a strategy
  backfill endpoint, `sort=fpta` on the lead list, F-P-T-A columns on the CRM
  grid, `FptaBadges` on the lead header / kanban card / grid, and
  `WhyThisProspectPanel` (Feature 12) on the lead detail page. Tests:
  `tests/test_fpta_scoring.py` (46), `tests/test_fpta_scoring_migration.py`
  (5), `frontend/src/tests/fpta.test.ts` (20). `tsc --noEmit` clean; CRM
  suites (87) still green. Decisions: four columns rather than widening
  `ai_booking_likelihood`, because the two answer different questions and a
  single number erases which of the four is broken; the model may move a
  baseline by at most ±15 points and the clamp is enforced in code; a
  dimension with no evidence says so rather than borrowing confidence from a
  neighbour; Feature 12 adds NO storage and no model call — an explanation
  that needed its own generation step would be a second opinion about the
  score rather than an explanation of it. Docs:
  `docs/features/fpta-scoring.md`, `docs/features/why-this-prospect.md`.
  Also updated `tests/test_reply_intelligence.py`: the reply task's result now
  carries an extra `intent` key (Feature 1).

- **2026-09-16 — Feature 1 done.** Migration `0053_reply_intent` (5 nullable
  columns on `inbound_replies`), `app/services/reply_intent.py`, wired into
  `reply_tasks.process_inbound_reply`, `reply_quality` on strategy analytics +
  a dedicated `/strategies/{id}/reply-quality`, `intent` block on every CRM
  reply, `ReplyQualityPanel` on the analytics page and an intent badge in the
  reply inbox. Tests: `tests/test_reply_intent.py` (36),
  `tests/test_reply_intent_migration.py` (5),
  `frontend/src/tests/replyIntent.test.ts` (21). `tsc --noEmit` clean.
  Decisions: only `interested` counts as positive (`not_now` is a future
  opportunity, not a win for *this* campaign); a failed model call leaves the
  reply unclassified rather than guessing `neutral`, because a guessed label
  silently moves a headline metric; rates are `null` not `0.0` on an empty
  denominator. Docs: `docs/features/positive-reply-classifier.md`.
- **2026-09-16 (start)** — Surveyed the codebase (models, services, API, frontend,
  test layout, migration chain head = `0052_auth_sessions`). Confirmed backend tests
  run (`pytest tests/test_compliance_rules.py` → 31 passed). Wrote this file.

## Known issues / left for Rehan

- **`tests/integration/` needs a PostgreSQL on localhost:5433, which is not
  running on this machine.** All 124 of those tests error at connection time
  with `psycopg2.OperationalError: connection refused`, before any application
  code runs. This is an environment dependency and predates this branch --
  `tests/integration/conftest.py` is the real-PostgreSQL harness, deliberately
  separate from the SQLite one the 135 root modules use. Start the container
  (`docker compose up -d postgres`) and re-run `pytest tests/integration` to
  cover them. Nothing in Part 1 or Part 2 is specific to PostgreSQL; every
  migration added here was written and round-trip tested on SQLite with
  `batch_alter_table` where the backends differ.

- **Feature 11 depends on multi-mailbox support to be fully useful.**
  `GmailAccount.user_id` is UNIQUE -- one connected mailbox per account. A
  client's sending pool can name several domains, but the account still has
  one mailbox, so the pool's practical effect today is to REFUSE a send from
  the wrong domain rather than to ROUTE between several. Allowing several
  mailboxes per account is the change that makes pools fully useful; nothing
  in this feature needs to change when it lands. I did not make that change
  because relaxing a UNIQUE constraint that `_account_for_strategy` reads with
  `scalar_one_or_none` would silently change which mailbox every existing
  campaign sends from.

- **Feature 5: decide whether the VIP-title trigger should be ON for your
  ICP.** Vectorae sells to boutique agency OWNERS, and "owner"/"founder" is a
  VIP title — so with `send_review_vip_titles` on (the current default), every
  message to your core persona lands in the review queue. That is correct
  behaviour for someone selling into enterprises and wrong for you. Turn it
  off in `/admin/settings` (`send_review_vip_titles = false`) unless you
  genuinely want to hand-approve every send. The other three triggers
  (objection, stalled deal, tone) are exceptional by nature and are fine on.
  This is also why the test suite disables the whole gate by default — see the
  note at the end of `docs/features/send-review-queue.md`.

- **Feature 4 complaint rate is a proxy.** No FBL / Google Postmaster feed is
  connected, so the spam-complaint rate is approximated from unsubscribes plus
  replies asking to stop. Labelled `proxy` everywhere it appears. If you want
  a true complaint rate, connecting Google Postmaster Tools or a relay with an
  FBL is the change — the score, thresholds and send gate would not move.
