# F06 — Quickstart

**Flag** `quickstart_enabled` (system setting, **default off**) ·
**Migration** `0076_quickstart` · **Service** `app/services/quickstart.py`,
`app/services/safe_fetch.py` · **Worker** `app/workers/quickstart_tasks.py` ·
**API** `app/api/quickstart.py` · **UI** `frontend/src/app/(app)/quickstart/page.tsx`,
`frontend/src/lib/quickstart.ts`, `frontend/src/lib/api/quickstart.ts`

Status: built in the same PR as this spec (founder decision 2026-10-10:
"build straight away"; the founders review spec and code together at merge).

## Problem

Competitor complaint (STAR A.6, F06): **weeks-long onboarding** — Clay takes
2–4 weeks to set up, Outreach implementations run for months — and the churn
that follows when a buyer sees a demo but never sees *their own* first useful
output.

Vectorae's own path to a first message is long by design: product intake →
a 72/144-step research pipeline → 10 verification passes → lead sourcing →
sequence → send. That path is the product's quality guarantee and it stays.
What a new account lacks is something reviewable *while it runs*.

## Recon — what already exists (this is "compose existing", not a new engine)

| Building block | Where | Used how |
|---|---|---|
| Product + strategy creation, plan `max_strategies`, daily AI cap pre-check, budget planning | `app/api/strategies.py::create_strategy` | Its checks were extracted into two helpers (`check_new_strategy_allowed`, `plan_budget`) that both `create_strategy` and Quickstart call. `create_strategy`'s behaviour and check order are unchanged. |
| ICP extraction | `app/services/icp_extraction.py` | Same system prompt, same output schema, same normalisation. Refactored so the model call + normalisation (`criteria_from_research`) takes research text; `extract_icp_criteria` (strategy research) and Quickstart (website + offer) both call it. |
| Lead sourcing chain (source → enrich → find email → verify → finalize, suppression at every stage, `max_leads_per_strategy` per lead) | `app/workers/lead_tasks.py` | Reused unchanged: Quickstart creates an ordinary `LeadBatch` and runs the same five tasks. |
| Message drafting | `app/services/message_personalization.py::render_message` | Reused unchanged, with an unsaved `SequenceStep` carrying the Quickstart brief. |
| Claim verification (Feature A1) | `app/services/claim_verification.py::enforce` | Run on every draft (`message=None`), exactly like the send path. |
| Usage meter / per-scope budget / account daily cap | `app/services/usage_meter.py` | One scope per Quickstart stage, purpose `quickstart`. |
| SSRF classification of addresses | `app/services/webhook_delivery.py::_non_public` | Reused by the new `safe_fetch` module (there was no general-purpose safe page fetcher; `market_intel._http` follows redirects to anywhere and is only ever pointed at fixed hosts). |
| Untrusted-text delimiting (F-AI-02) | `app/services/prompt_safety.py` | The website text goes into the ICP prompt inside `block()` with `NOTICE`. |

New code is the orchestration (run record, two Celery tasks, API, review UI)
and the safe fetcher.

## Behavior

1. The user enters a **website URL** and a **one-line offer** (≤ 300 chars) on
   `/quickstart`.
2. `POST /quickstart` (flag on) validates the URL shape (see SSRF below), runs
   the same plan / daily-AI-cap / budget checks as `create_strategy`, then
   creates — in one commit — a `Product` (name = given name or the site's
   host, description = the offer + the URL), a `Strategy` (Flow 2,
   `no_clients`, default pipeline budget), and a `QuickstartRun`. It then
   enqueues **both** the normal `run_pipeline` (the full research + 10-pass
   verification, untouched) and `leadpilot.quickstart.start`.
3. `start` (worker, `default` queue): fetches the page through `safe_fetch`,
   extracts ICP criteria from *offer + page text* with the existing ICP
   extractor, creates a `LeadBatch` for `min(quickstart_lead_target (25),
   leads_max_per_run, plan room)` leads, and starts the **existing** lead chain
   with one extra link at the end: `leadpilot.quickstart.draft`. The chain is
   published to the `default` queue for this run only, because the `pipeline`
   queue (concurrency 2) is occupied by the full pipeline this same request
   just enqueued — sourcing behind it would miss the 15-minute target by
   hours.
4. `draft` (worker): for each lead in the batch with status `VERIFIED` (email
   verified deliverable by the verifier; `risky`/`unknown` are FLAGGED and are
   not drafted), re-checks suppression, renders a first-touch email with
   `render_message`, runs claim verification, and stores a `QuickstartDraft`.
   Each draft is committed on its own; the first one stamps
   `first_draft_at`.
5. Every draft is labelled **"Quickstart draft — full research running"**.
6. **Supersede.** When the strategy reaches `verified`,
   `needs_human_review` or `executing`, the run is marked superseded
   (`superseded_at`, stamped the first time the run is read after that) and
   the drafts are relabelled **"Quickstart draft — superseded by the verified
   strategy"**. From then on the normal flow applies: sequences are created
   from the verified strategy and every message is re-rendered by the send
   path against the full research. Quickstart drafts are never re-used as
   message copy.
7. **Budget-capped.** Each run has an AI budget, `quickstart_budget_usd`
   (default $1.00), enforced *before* each model call by the usage meter. When
   it is reached, drafting stops cleanly (`budget_reached` recorded on the
   run); drafts already written stay. The account's plan daily AI cap is
   enforced on top (`quickstart` is an `ACCOUNT_CAPPED_PURPOSES` purpose).

### Drafts are for REVIEW only — nothing is sent

This is the core safety property:

- A draft is a `quickstart_drafts` row. It is **not** a `Message`, has no
  sequence, no enrollment and no `scheduled_at`. The dispatcher
  (`dispatch_due_messages_impl`) selects only `messages` rows, so there is no
  path from a draft to `send_message_impl`.
- The UI offers **Copy** and **Discard** only. There is no approve, send or
  schedule action, and no API that turns a draft into a message.
- The `/strategies/{id}/leads/source` gate (strategy must be `VERIFIED`) is
  **not weakened**: that route is untouched. Quickstart sources its own batch
  through the same chain, but the leads it adds can only ever be contacted
  through `/sequences/{id}/enroll`, whose `_launch_checks` still require a
  `VERIFIED` strategy with an active campaign, plus the red-team review, plan
  channel checks, and then every send-time gate (suppression, compliance
  rules, consent, windows, caps, mailbox health, claim verification, the
  human review queue). So: **nothing reaches a prospect until the normal
  pipeline and its 10-pass verification have completed.**
- Not reusing the send-review queue is deliberate: that queue holds
  `messages` rows with a `SCHEDULED → AWAITING_REVIEW → SCHEDULED` lifecycle,
  so approving an item *sends* it. Putting Quickstart drafts there would
  create exactly the send path this feature must not have.

## Non-goals

- No sending, scheduling or sequence creation from Quickstart.
- No crawling: one page (the URL given, after at most 3 validated redirects).
- No new lead provider, verifier or personalisation engine.
- No change to the full pipeline, its verification, its budget, or the
  `/leads/source` gate.
- LinkedIn / WhatsApp / phone drafts (email first-touch only).
- Plan-gating: Quickstart is onboarding, available on every plan; it consumes
  the plan's existing limits (`max_strategies`, `max_leads_per_strategy`,
  daily AI cap) rather than adding a new paid capability.

## Data model and migration

`0076_quickstart` (down-revision `0075_deliverability_autopilot`), create-only,
SQLite + PostgreSQL types (`JSON`, not `JSONB`). Downgrade drops the two tables
— lossless for every other table; the Quickstart runs and drafts themselves
are lost on downgrade (they are review artefacts, superseded by the pipeline;
the products, strategies, batches and leads they created are ordinary rows and
survive).

`quickstart_runs` — one per Quickstart request:
`id, user_id (FK users, CASCADE), product_id (FK products, CASCADE),
strategy_id (FK strategies, CASCADE), lead_batch_id (FK lead_batches, SET
NULL), website_url, offer, status, error, icp_criteria_json,
website_meta_json, website_text (≤ 20 000 chars), lead_target,
budget_micros, detail_json, fetched_at, icp_at, sourcing_started_at,
first_draft_at, ready_at, superseded_at, created_at, updated_at`.

`status`: `queued → fetching → extracting → sourcing → drafting → ready`, or
`failed` (with `error`). Superseded is a flag (`superseded_at`), not a status,
so a run's own outcome stays readable.

`quickstart_drafts` — one per (run, lead), UNIQUE:
`id, run_id (FK, CASCADE), user_id (FK users, CASCADE), lead_id (FK leads,
CASCADE), subject, body, status (draft | discarded), claims_changed,
created_at, updated_at`.

## API

All routes require auth; every query filters on the caller's own `user_id`.

| Route | Purpose |
|---|---|
| `GET /quickstart/config` | `{enabled, lead_target, budget_usd, label}` — always 200 so the UI can hide itself when the flag is off. |
| `POST /quickstart` `{website_url, offer, product_name?}` | 202 + run. 404 when the flag is off; 422 bad URL/offer; 402 plan `max_strategies`; 429 daily AI cap (same body as `create_strategy`) or rate limit. Rate-limited with the **strategies** bucket (`RATE_LIMIT_STRATEGIES`), because it creates a strategy and starts its full pipeline. URL/offer validation runs in the request model and the flag check in a route dependency, so a 422 or a 404 never costs a slot; `enforce_rate_limit` is the handler's first statement (the `test_rate_limit_*` tripwires list this route). |
| `GET /quickstart` | The caller's 20 most recent runs. |
| `GET /quickstart/{run_id}` | Run + drafts (+ lead name/title/company/email), timings, supersede state. 404 for another account's run. |
| `POST /quickstart/drafts/{draft_id}/discard` | Marks a draft discarded (review feedback; nothing else changes). |
| `GET /admin/quickstart/timing?days=30` | Admin only: n, p50, p90 of seconds-to-first-draft over completed runs — the success metric's measurement. |

With the flag off every route except `/quickstart/config` answers 404 and the
workers do nothing (a run already queued is marked `failed: "Quickstart is
turned off"`).

## UI

`/quickstart` (app shell): a two-field form (website, one-line offer, optional
product name) and, below it, the selected run: a status stepper
(fetching → ICP → sourcing → drafting → ready), the ICP that was derived,
the elapsed time to first draft, and the draft cards. Each card shows the
label badge, lead, subject and body, with **Copy** and **Discard**. A banner
explains that drafts are for review only and that sending happens through the
verified strategy. The page polls every 10 s while the run is in progress.
When `/quickstart/config` says disabled, the page says Quickstart is not
enabled on this deployment. The entry point is a **Quickstart** button next to
**New strategy** on the Strategies page, rendered only when the flag is on (no
new sidebar item, so nothing changes for a deployment with the flag off).

## Tenancy

- Runs and drafts carry `user_id`; every read filters `user_id ==
  current_user.id` and answers 404 (never 403) otherwise.
- The worker loads only the run's own strategy, batch and leads (`Lead.batch_id
  == run.lead_batch_id`).
- Both tables are in `account_export.OWNERSHIP` (GDPR export).
- GDPR erasure of a lead (`DELETE /leads/{id}`) now also deletes that lead's
  Quickstart drafts — the draft body contains the person's name and company.

## Compliance review (CAN-SPAM / GDPR / CASL / PDPA — zero tolerance)

- **No send path** (see above). CAN-SPAM/CASL/PDPA obligations attach to
  sending; every send still goes through the unchanged chokepoint, which
  appends the compliant footer and List-Unsubscribe headers.
- **Suppression**: enforced by the reused lead chain at sourcing, enrichment
  and email discovery, and re-checked immediately before each draft is
  written; a suppressed lead gets no draft.
- **Fabrication**: every draft goes through the existing claim verification
  engine; unsupported claims are stripped/rewritten before the draft is
  stored, and the log rows are written as on the send path.
- **GDPR**: lead data comes from the same providers and lawful basis as normal
  sourcing; drafts are erased with the lead; both tables are in the account
  export; drafts are deleted with the account (FK CASCADE).
- **The fetched website** is a public business page the user names as their
  own; one GET, identified by `User-Agent: Vectorae/1.0 quickstart`. Its text
  is stored (≤ 20 000 chars) so the derived ICP stays explainable. It is never
  placed into a draft prompt — drafts are written from the offer (the user's
  own words) and the lead's stored data only, so nothing from an unverified
  page can end up as a claim to a prospect.

## Security — the website fetch (SSRF) and prompt injection

`app/services/safe_fetch.py`:

- https only (`http://` and bare hosts are upgraded to `https://`), port 443
  only, no credentials in the URL, no `localhost` / `.local` / `.internal` /
  single-label hosts, no IP literal that is not globally routable.
- Every hop **re-resolves** the host and refuses if *any* address is
  non-public (`webhook_delivery._non_public`: private, loopback, link-local
  incl. 169.254.169.254, reserved, multicast, CGNAT…).
- Redirects are followed **manually**, at most 3, each re-validated.
- 8 s total timeout, 512 KB read cap (truncated, not failed), `text/html` or
  `text/plain` only.
- Residual risk (same as webhook delivery): a DNS-rebinding window between the
  check and httpx's own resolution. Closing it fully needs an egress proxy.
- The page text is untrusted (F-AI-02): stripped of scripts/styles, wrapped in
  `prompt_safety.block()` with `prompt_safety.NOTICE`, and only ever used to
  derive closed-schema ICP lists (titles, industries, …), which are then
  passed to the lead provider as search filters.

## AI cost and budget impact

Model calls per run: 1 ICP extraction (≤ ~3k input tokens) + per draft 1
personalisation call and, with `claim_model_extraction_enabled`, 1 claim
extraction call. At the admin price defaults ($3 / $15 per Mtok) that is
roughly $0.01–0.03 per draft, so 25 drafts ≈ $0.40–0.75. The run budget
`quickstart_budget_usd` ($1.00 default) caps it hard; the plan's daily cap
applies on top. Lead scoring at batch finalisation is the existing
`lead_scoring` scope against the strategy (unchanged). Quickstart spend is
recorded in `api_usage` with `purpose="quickstart"` and the strategy's id, so
it shows in per-campaign cost, but it does **not** count toward the
strategy's *pipeline* budget (`BUDGETED_PURPOSES` is unchanged) — the run has
its own cap. Data-provider cost: up to 25 Apollo search/enrich results and
Hunter verifications per run, counted against `max_leads_per_strategy`.

`usage_meter.scope()` gained an optional `prior_micros` argument so a scope
with a budget can be measured against something other than the strategy's
pipeline spend. `quickstart` was added to `ACCOUNT_CAPPED_PURPOSES`.

## Timing instrumentation (for the success metric)

The run stores `created_at`, `fetched_at`, `icp_at`, `sourcing_started_at`,
`first_draft_at`, `ready_at`. The API returns `seconds_to_first_draft`; each
stage logs a `quickstart.timing` line with the run id and seconds since
creation; `GET /admin/quickstart/timing` reports n / p50 / p90.

## Test plan (offline: SQLite, FakeClaude, fake lead source/verifier, mocked HTTP)

- `tests/test_safe_fetch.py` — URL validation (schemes, ports, credentials,
  internal names, private/loopback/link-local/metadata literals), resolution
  refusal, redirect re-validation and limit, size cap, content-type refusal,
  HTML-to-text (scripts dropped, title kept).
- `tests/test_quickstart.py` —
  - flag off: every route 404 (config 200, `enabled:false`), nothing created,
    workers no-op;
  - happy path end to end: run → ICP from website (prompt carries the
    delimited untrusted block and NOTICE) → batch → chain impls → drafts for
    VERIFIED leads only, labelled, claim-verified, usage rows with purpose
    `quickstart`, timings stamped;
  - **no send path**: no `messages`, `sequences` or enrollments exist after a
    full run; the `/leads/source` 409 gate for an unverified strategy still
    holds;
  - suppression re-check at draft time; budget cap stops drafting cleanly;
    `max_leads_per_strategy` caps the batch; plan `max_strategies` → 402;
  - SSRF: private URL → 422 at the API; a host resolving to a private
    address → run `failed`, nothing sourced;
  - tenancy: another account's run/draft → 404; list shows own runs only;
  - supersede: strategy VERIFIED → `superseded_at` set and relabelled;
  - GDPR: lead erasure deletes its drafts;
  - admin timing: p50 over runs; non-admin 403;
  - Celery: the chain is the existing five lead tasks + `draft`, all on
    `default`; the start task publishes it;
  - cascades with SQLite FK enforcement ON: deleting a lead removes its
    draft, deleting the strategy removes the run and drafts.
- `tests/test_quickstart_migration.py` — migration creates exactly the model's
  tables.
- Existing: `test_rate_limit_wiring.py` / `test_rate_limit_ordering.py` list
  the new route; `test_account_export.py` covers the new tables automatically.
- Frontend: `src/tests/quickstart.test.ts` (URL normalisation, stage
  progress, labels, timing format, write bodies are JSON objects).

## Success metric (measured, never promised)

**p50 time to first reviewable draft ≤ 15 minutes in staging with real
providers.** Measured by `GET /admin/quickstart/timing` over real staging
runs. **Not measured in this PR** — see Unverified.

## Unverified

- The p50 ≤ 15 min target: no staging run with real Apollo, Hunter, Anthropic
  and a real Celery `default` worker has been made. Only the instrumentation
  exists.
- Real-world fetch behaviour (bot protection, JS-only sites that render no
  text, sites that block the user agent). A JS-only page yields little text;
  the offer alone then drives the ICP.
- Whether the ICP derived from one page + one line is good enough for Apollo
  to return 25 relevant people; and the Apollo/Hunter yield (how many of 25
  sourced end up `VERIFIED`).
- Queue latency on the shared `default` worker (concurrency 2, also serving
  the nightly `learning` jobs).
- DNS-rebinding window in `safe_fetch` (documented residual risk).
- If a lead-chain stage exhausts its Celery retries, the chain stops before
  the `draft` link and the run stays `sourcing`; the run view shows the
  batch's `error` so the user is not left guessing, but there is no automatic
  sweep that marks such a run failed.
- No `scripts/gate/e2e_journeys.py` journey was added: Quickstart is an
  onboarding side path, not one of the core journeys J1–J10, and its "no
  send" property is covered by the offline tests.
- Help-center article: `docs/help-center/` does not exist on this branch
  (Phase 2 not merged here), so no article was added; one is needed when it
  lands.
