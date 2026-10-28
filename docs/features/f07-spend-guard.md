# F07 — Spend Guard + Cost Forecast

**Flag** `spend_guard_enabled` (System Settings, default **off**) ·
**Settings** `spend_guard_apollo_usd_per_credit`, `spend_guard_hunter_usd_per_credit`
(default **0.0** = data credits counted but not priced) ·
**Service** `app/services/spend_guard.py` ·
**API** `app/api/spend_guard.py` ·
**Worker** `app/workers/spend_guard_tasks.py` (alert sweep, every worker burst: 30 minutes by default) ·
**UI** `/analytics/usage` (`frontend/src/components/analytics/SpendGuardPanel.tsx`,
helpers `frontend/src/lib/spendGuard.ts`) ·
**Migration** `0078_spend_guard` (three new tables, create-only).

Status: built behind the flag, default OFF. Spec and code ship in the same PR
(founder decision 2026-10-10); the founders review at merge. Branch
`feat/f07-spend-guard`, stacked on F05 → F01 → F02 → F03 → F06 → F08.

---

## Problem

Competitor complaint it answers (STAR row F07): *Clay credit burn; Apollo
credits consumed by stale records.* Buyers of outbound tools are surprised by
the bill: credits are spent per attempt whether or not the attempt produced
anything, and nothing tells them before a run what it is about to cost.

What that looks like in Vectorae today:

1. **No forecast.** Creating a strategy or sourcing leads starts spending at
   once. The strategy budget (F-AI-01) and the plan's daily AI cap (P0-5)
   stop a run, but nobody is told *beforehand* what the run will cost.
2. **Data credits are invisible.** Apollo search and enrich calls are only
   structured log lines (`external_call`); F01 ledgers Hunter calls made by
   the waterfall but explicitly left dollar pricing, forecasts and caps to F07
   ("one price table, not two").
3. **No customer ceiling.** The only account-level ceiling is the plan's
   *daily* AI cap, set by Vectorae. A customer cannot say "never spend more
   than $X this month", and is never warned as they approach a limit.
4. **No success/attempt split.** Nothing shows how much was spent on attempts
   that produced nothing versus results that were verified.

## Recon — this is "extend usage_meter + F01's ledger", not a new system

| Part of F07 | Existing code | Verdict |
|---|---|---|
| AI cost model (prices) | `system_settings` `claude_input/output_cost_per_mtok`, `usage_meter.price_table` | **Exists** — reused as-is |
| AI forecast model ("spend-cap model") | `app/pipeline/budget_plan.py` (`planned_step_nos`, `estimate_step_micros`, verification reserve; settings `pipeline_est_step_*`) | **Exists** — reused for every strategy-run forecast |
| Per-call gateway check | `usage_meter.check_budget` (strategy budget F-AI-01 + plan daily cap P0-5), called by `anthropic_client` before every model call | **Exists** — F07 adds a third ceiling through a tiny hook |
| AI spend ledger | `api_usage` (`usage_meter.scope`, `cost_micros`) | **Exists** — reused |
| Data-credit ledger | F01 `enrichment_attempts` (`cost_units`, provider, batch) | **Half** — Hunter waterfall calls only; F07 meters the rest into the **same table** |
| Data-call gateway | `app/integrations/plumbing.py` `BaseHttpAdapter.call` (cache → HTTP, `cost_units` per call) | **Exists** — F07 adds a pre-call check + post-call meter hook |
| Pre-run refusal pattern | `strategies._refuse_if_daily_ai_limit_reached` (429 `daily_ai_limit_reached`) | **Exists** — same shape for the monthly cap |
| Transactional email | `app/services/email_sender.send_email` (never the outreach path) | **Exists** — reused for alerts |
| Usage page | none (`/billing` shows pay-per-meeting usage only) | **Missing** — new page `/analytics/usage` |
| Customer credit system | none — billing is monthly tiers or pay-per-meeting (`billable_meetings`, outcome-based) | **Does not exist** — see Non-goals |

More than half of the machinery exists, so F07 **extends** it: one forecast
function per run kind built on `budget_plan`, a monthly ceiling that rides the
existing gateway check, F01's ledger completed with the calls it did not
cover, and a usage page.

---

## Behavior

### Flag and settings (`app/services/system_settings.py` DEFAULTS)

| Key | Default | Meaning |
|---|---|---|
| `spend_guard_enabled` | `False` | Forecast every strategy / enrichment / Quickstart run, enforce the account's monthly cap, meter all data-provider credits, send 50/80/100% alerts, show the usage page. |
| `spend_guard_apollo_usd_per_credit` | `0.0` | USD per Apollo credit (search result or enrich). 0 = counted, not priced; the page says so. |
| `spend_guard_hunter_usd_per_credit` | `0.0` | USD per Hunter request (finder or verifier). Same rule. |

Off = no behaviour change (tested): no forecast row, no refusal, no metering
hook fires, no alert, the API answers 409. Not plan-gated: a spending
ceiling is a safety control and must never be a paid tier.

### Forecast before every run

A forecast is computed **before** each of these, with the existing cost model,
and stored as a `spend_forecasts` row (allowed *or* refused):

| Run | Endpoint | Forecast kind | AI line (model) | Data line | Sends |
|---|---|---|---|---|---|
| New strategy | `POST /products/{id}/strategies` | `strategy_run` | planned steps (`budget_plan.planned_step_nos(steps_per_phase)` × pipelines for the flow) × `estimate_step_micros` + `pipeline_verification_reserve_usd` | Apollo 1 credit for the market-intel org search when `competitor_intel_enabled` | 0 |
| Resume | `POST /strategies/{id}/resume` | `strategy_run` | planned steps **not yet done** (`research_steps` rows) × step estimate + verification reserve | as above, only if the strategy has no market snapshot yet | 0 |
| Re-verify | `POST /strategies/{id}/reverify` | `strategy_run` | verification reserve only | 0 | 0 |
| Lead sourcing (enrichment run) | `POST /strategies/{id}/leads/source` | `enrichment_run` | lead scoring: ⌈leads ÷ 15⌉ model calls × per-call tokens (only when `lead_scoring_enabled`) | credits per provider = requested leads × credits-per-lead | 0 |
| Quickstart | `POST /quickstart` | `quickstart` | full pipeline (as a new strategy) + the Quickstart run's own AI budget + lead scoring | as lead sourcing, for the Quickstart lead target | 0 |

**Credits per lead** (data line): from this account's own reconciled history
when it has ≥ 3 finished enrichment forecasts (actual credits ÷ requested
leads, per provider, last 10 runs), otherwise the documented defaults Apollo
2.0 (1 search result + 1 enrich) and Hunter 1.5 (finder for some leads +
verifier) — **UNVERIFIED** against real batches; the calibration replaces them
as soon as real runs exist. **Lead-scoring tokens per call**: the account's
own `api_usage` average for `lead_scoring` when it has ≥ 3 calls in 90 days,
else 2,500 input / 1,000 output tokens (UNVERIFIED estimate).

**Sends are 0 for every run kind, deliberately.** None of these runs sends a
message; sends happen later when a sequence runs, through the send chokepoint.
Their model spend (`purpose = "send"`) is metered and counts toward the
monthly total and its alerts, but a send is never stopped mid-flight by a cap
(the P0-5 rule: stopping a send halfway leaves a message claimed but unsent).
The forecast says so in its `sends.note`.

The forecast is an **expected** cost (what the ±25% metric is measured on).
It is not the guarantee: the hard guarantee is the per-call check below.

### Monthly cap (customer-set) — a hard ceiling

* Per account, optional: `monthly_cap_usd` (null = no monthly cap). Set by the
  account owner or a workspace manager. It is an **additional** ceiling: it
  never raises or replaces the plan's daily cap or a strategy's budget — all
  three are checked, the first one reached stops the work.
* Period: the UTC calendar month. Month spend = AI (`api_usage.cost_micros`,
  all purposes) + data (`enrichment_attempts.cost_units` × the provider's
  price), both filtered by the account's own id.
* **Pre-check (refusal).** Before each run above (after the endpoint's own
  validation, before anything is created or changed): when a cap is set and
  `forecast > cap − month spend`, the request is refused with **429**
  `{"error": "monthly_spend_cap_reached", "forecast_usd", "remaining_usd",
  "cap_usd", "spent_usd", "message"}`, the request's pending changes are
  rolled back and nothing is created or queued (the refused forecast row is
  committed as the audit record). An allowed forecast row is only flushed and
  is saved by the endpoint's own commit, so a request that fails a later check
  leaves no forecast behind.
* **Per-call check (hard cap), AI.** Inside every metered scope whose purpose
  is account-capped (`usage_meter.ACCOUNT_CAPPED_PURPOSES`: pipeline,
  verification, lead scoring, quickstart), the existing `check_budget` also
  asks the monthly guard: `the month's COMMITTED spend, re-read now (one
  aggregate query over api_usage + priced enrichment_attempts) + this scope's
  own noted-but-unflushed model calls + the call's worst-case price > cap` →
  `MonthlySpendCapReached` (a
  `BudgetExceededError`) before the call is made. Every existing budget path
  handles it already: the strategy becomes `budget_exhausted` (resumable),
  lead scoring falls back to the heuristic, Quickstart stops cleanly.
* **Per-call check (hard cap), data.** Every paid provider call goes through
  `BaseHttpAdapter.call`. On a cache miss, inside a Spend Guard scope, the call's
  `cost_units` × price is checked against the cap **before** the request,
  with the same fresh figure (committed month spend re-read now, plus the
  enclosing model scope's unflushed calls when the data call happens inside
  one, e.g. market intel in a pipeline). A refused data call raises
  `MonthlySpendCapReached`; the lead
  batch stops with `stage = failed` and
  `error = "monthly spend cap reached: …"` and the remaining chain stages skip
  (no retries, nothing spent).
* **Single paid endpoints outside a run** are guarded the same way through
  `spend_guard.data_scope`: the F02 market-signal refresh task
  (`intelligence_tasks.refresh_market_signals`, an Apollo organization search
  — checked and metered) and the F01 single-lead re-verify
  (`POST /leads/{id}/email-verdict/refresh` — checked; F01 ledgers it, so not
  metered twice; a refusal answers 429 `monthly_spend_cap_reached`).
* **A refusal is never an F01 attempt.** `waterfall_enrichment.verify_address`
  and `run_for_lead` re-raise `MonthlySpendCapReached` before their generic
  provider-error handler, so no `outcome = "error"` row is written and
  `/bounce-shield/metrics` is not polluted (a real provider failure is still
  ledgered as before).
* Purposes that are not account-capped (send, briefs, call analysis, …) are
  **counted, never stopped** — same reasoning as P0-5.
* **What "hard" means precisely (residual window).** A check sees everything
  committed and its own scope's pending calls. It cannot see spend another
  run has not committed yet: that run's noted-but-unflushed model calls (the
  pipeline flushes after every step, so at most one step's worth) and the
  call that run is making at that instant (bounded by its own worst-case
  reserve). Two overlapping runs can therefore cross the cap by at most that
  much; each run individually never makes a call its fresh check refuses.

### Metering data credits (completing F01's ledger)

Inside a lead-batch stage (`lead_tasks._run_stage`) and inside an
account-capped model scope, each **paid** (non-cached) `BaseHttpAdapter.call`
writes one `enrichment_attempts` row: `step = "spend_guard"`, `provider`,
`operation` = the endpoint's last path segment (`search`, `match`,
`email-finder`, …, 20 chars), `outcome = "ok"`, `cost_units`, `batch_id`,
`strategy_id`, no lead, no address, no name — and commits it at once (F01's
rule: a crash later must not erase a call already paid for).

**No double counting.** When the waterfall is on, F01 already ledgers every
call it makes to the verifier provider (Hunter finder + verifier), so inside a
lead batch the meter **skips** that provider's rows (it still checks the cap).
With the waterfall off, the legacy Hunter calls are metered by F07. F01's
`/bounce-shield/metrics` ledger totals exclude `step = "spend_guard"` rows so
F01's metric keeps its meaning.

### Alerts at 50 / 80 / 100 %

* Fired once per threshold per account per calendar month — enforced by a
  UNIQUE (user_id, period, threshold) constraint, so two workers racing write
  one row (tested). Raising the cap mid-month does not re-arm a threshold
  already fired that month (documented, deliberate: no alert storms).
* **The 100% alert fires whenever the cap stops work**, even though a
  refusal keeps spend just under 100%: a refused model call (when its usage
  scope closes, `spend_guard.after_scope`), a refused data call (when its
  batch/data scope closes) and a refused run (the pre-check) all call
  `evaluate_alerts(force_cap_reached=True)`. Same unique key, so still once
  per month. Its email says "Your monthly spend cap is stopping work".
* Otherwise evaluated after every lead-batch stage, when the cap is changed,
  and by a beat sweep every 30 minutes (each worker burst) over accounts with a cap (an allowed
  pre-check commits nothing before the endpoint's own commit, so it leaves
  alerts to the sweep). Each alert insert runs in a SAVEPOINT, so a racing
  duplicate rolls back only itself.
* **In-app**: every alert of the current month is listed on the usage page
  with a banner for the highest threshold reached.
* **Email** (when the account's `email_alerts` is on and its email is
  verified): one transactional email via `email_sender.send_email` — Vectorae's
  own system mail to the account owner, **never** the outreach send path. No
  prospect data in it. The result (`sent` / `failed` / `skipped`) is stored on
  the alert row; a failed email is not retried (the in-app alert stands).

### "Charge credits only on successful verified results"

**Vectorae has no customer credit system.** Billing is either a monthly tier
or pay-per-meeting (`billable_meetings`, which already charges only on a
booked meeting — an outcome, never an attempt). F07 does **not** build a
billing system. The honest minimum it ships is the split on the usage page:

* **Billable verified results** — leads that reached `verified` in the
  account's lead batches finalized this month (`lead_batches.summary_json`).
* **Internal attempt cost** — every paid data attempt (successful or not) and
  its priced cost, plus AI spend, logged regardless of outcome.
* **Cost per verified result** = data cost ÷ verified results.

So a customer can see what a verified lead actually cost them, including the
attempts that produced nothing. If credit-based billing is ever introduced,
the "verified results" figure is the meter it should charge on.

### Forecast accuracy (the ±25% metric)

`spend_guard.reconcile(db, forecast_row)` compares a stored forecast with what
the run actually cost:

* Window: from the forecast's creation until the next allowed forecast of the
  same kind for the same strategy (so a resume is not counted twice); for a
  Quickstart, the next allowed forecast of any kind for its strategy.
* AI actual: `api_usage` rows of the account, the forecast's strategy and its
  purposes, inside the window.
* Data actual (`enrichment_attempts` of the account, per provider, in credits
  and priced): an enrichment run = its own batch's rows (batch-specific, no
  end time needed); a strategy run = the strategy's rows **not** tied to any
  batch (the market-intel search), inside the window — a later lead-sourcing
  batch is never counted against the pipeline; a Quickstart = all of its
  strategy's rows inside the window.
* Output: per line forecast vs actual, `error_pct = (forecast − actual) ÷
  actual`, `within_tolerance = |error_pct| ≤ 0.25`, and `complete` (the run
  has finished: strategy no longer pending/researching/verifying; batch
  finalized or failed). Credits are reconciled in units too, so the metric is
  measurable while data prices are 0.

`spend_guard.accuracy(db, user_id)` summarises complete, allowed forecasts:
count, within tolerance, share. Shown on the usage page and returned by the API.

---

## Non-goals

* **No billing or credit system** (see above). No change to Stripe, plans or
  pay-per-meeting.
* **No change to the plan's daily cap or a strategy's budget** — the monthly
  cap is only ever an extra, lower ceiling.
* **No stopping of sends** at the cap (P0-5 rule). No change to the outreach
  gate or the send chokepoint; F07 creates no send path.
* No change to F01's waterfall, verdicts or Bounce Shield. One line in F01's
  metrics filters F07's rows out of F01's waterfall totals.
* No vendor-invoice reconciliation: prices are the admin's settings.
* No per-workspace-member caps — the cap is per account (the workspace owner).
* No forecast inside the "find leads" dialog itself; the usage page offers a
  forecast preview, and a refused run returns the forecast in its message.

## Data model and migration

Migration **`0078_spend_guard`** (`down_revision = "0077_reply_copilot"`),
create-only, types valid on SQLite and PostgreSQL (JSON, BigInteger, Uuid):

`spend_caps` — one row per account that set a cap or alert preference.

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| user_id | UUID FK users CASCADE, **unique** | the account |
| monthly_cap_micros | BigInteger, nullable | null = no monthly cap |
| email_alerts | Boolean, default true | |
| updated_by_user_id | UUID FK users SET NULL, nullable | who changed it (workspace actor) |
| created_at / updated_at | timestamps | |

`spend_alerts` — one row per fired threshold.

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| user_id | UUID FK users CASCADE, indexed | |
| period | String(7) | `YYYY-MM` (UTC) |
| threshold | Integer | 50 / 80 / 100 |
| cap_micros / spent_micros | BigInteger | at the time it fired |
| email_status | String(20) | `sent` / `failed` / `skipped` |
| created_at / updated_at | timestamps | |
| | UNIQUE (user_id, period, threshold) `uq_spend_alert_user_period_threshold` | the idempotency guarantee |

`spend_forecasts` — one row per forecast taken before a run.

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| user_id | UUID FK users CASCADE | |
| kind | String(20) | `strategy_run` / `enrichment_run` / `quickstart` |
| decision | String(10) | `allowed` / `refused` |
| strategy_id | UUID FK strategies CASCADE, nullable, indexed | linked after the strategy row exists |
| batch_id | UUID FK lead_batches SET NULL, nullable, indexed | linked after the batch row exists |
| requested_leads | Integer, nullable | |
| ai_purposes | JSON | purposes reconciled against |
| ai_input_tokens / ai_output_tokens / ai_micros | BigInteger | |
| data_units_json | JSON | `{provider: credits}` |
| data_micros / total_micros | BigInteger | |
| month_spent_micros | BigInteger | month spend when forecast |
| cap_micros | BigInteger, nullable | cap when forecast |
| detail_json | JSON | basis of each line (defaults vs calibrated), notes |
| created_at / updated_at | timestamps | index (user_id, created_at) |

No personal data in any of them. `account_export.OWNERSHIP` gains all three
(`["user_id"]`). `scripts/activate_with_keys.py` `TARGET_REVISION` →
`0078_spend_guard`. **Downgrade** drops the three tables: caps, alert history
and forecast history are lost; nothing else is touched (documented, not
lossless). `enrichment_attempts` is not altered — F07 rows use the existing
columns.

## API

All behind `get_current_user`; every query filtered by the caller's own id.
Flag off → **409** `"Spend Guard is disabled by the administrator"`.

| Method | Path | Notes |
|---|---|---|
| GET | `/spend-guard/usage` | month spend (AI, data per provider, total), cap and % used, remaining, plan daily cap and today's spend, prices (and which providers are unpriced), billable verified results vs internal attempt cost, this month's alerts, the 10 latest forecasts each with its reconciliation, accuracy summary |
| PUT | `/spend-guard/cap` | `{monthly_cap_usd: number ≥ 1 and ≤ 1,000,000 or null, email_alerts: bool}`; owner/manager only (403 otherwise); evaluates alerts at once |
| POST | `/spend-guard/forecast` | preview, stores nothing: `{kind: "strategy_run", flow_type, max_budget_usd?}` or `{kind: "enrichment_run", strategy_id, max_leads?}` → forecast lines + `would_allow` + remaining. A strategy that is not yours → 404 |
| GET | `/spend-guard/forecasts/{id}` | one forecast with its reconciliation; another account's → 404 |

Refusals from the guarded run endpoints: **429** with
`detail.error = "monthly_spend_cap_reached"` and a `message` (the frontend
client already surfaces `detail.message`).

AI spend for request handlers: F07's own endpoints make **no model calls**.
The guarded run endpoints only enqueue work; the workers' existing
`usage_meter.scope` / `owner_scope` carry the monthly guard. (`app/api/metering.py`
from PR #61 is not on this branch — nothing here needs it.)

## The `usage_meter.py` hook (kept tiny on purpose — PR #61 rewrites parts of it)

Four additive edits, all marked `F07` (`note()` is untouched):

1. `_Scope` gains one field, `monthly_guard: object | None = None`.
2. `scope()` calls `spend_guard.arm(session, active)` after the P0-5 block —
   for a non-capped purpose or an unknown account it returns without a query;
   otherwise it reads the flag (the one query paid while the flag is off) and,
   when on, reads prices and the cap inside a SAVEPOINT. `arm` never raises,
   and a failed read (e.g. the flag switched on before migration 0078 ran)
   rolls back only its savepoint — the caller's PostgreSQL transaction is not
   left aborted — and the run continues under the existing caps.
3. `check_budget()` asks `active.monthly_guard.check_model_call(...)` first.
4. `scope()`'s `finally` calls `spend_guard.after_scope(...)` when a guard is
   armed: if a call was refused it fires the 100% alert; otherwise nothing.

All monthly-cap logic lives in `app/services/spend_guard.py`. Merging with
PR #61 means re-applying these lines to the rewritten functions.

Other hooks (each one or two lines, marked `F07`): `plumbing.BaseHttpAdapter.call`
(pre-call check on cache miss, post-call meter), `lead_tasks._run_stage`
(batch scope, clean stop on the cap), `strategies.py` create/resume/reverify,
`leads.py` source, `quickstart.py` create (pre-check + link),
`bounce_shield.metrics` (exclude F07 rows), `api/bounce_shield.py` re-verify
and `workers/intelligence_tasks.refresh_market_signals` (data scope),
`waterfall_enrichment.verify_address` / `run_for_lead` (re-raise a refusal),
`celery_app` (include + route + beat).

**Cost while the flag is off** (pinned by a query-counting test): one
`system_settings` read per outermost account-capped model scope, one per
lead-batch stage (the stopped-batch check only runs when the flag is on), one
per guarded endpoint. Nothing for non-capped scopes.

**Failure isolation.** Every F07 read in `arm`, `batch_scope` and `data_scope`,
the meter insert after a paid data call, and each alert insert run in a
SAVEPOINT (`session.begin_nested()`): a failure rolls back only the savepoint,
never the caller's pending work.

## UI

`/analytics/usage` ("Usage & spend", linked from Analytics beside Revenue &
ROI): flag-off → one quiet line naming the setting. Otherwise: alert banner
for the highest threshold reached; this month's spend vs cap with a progress
bar marked at 50/80/100%; AI / data / total tiles with credits per provider
("not priced" when a price is 0); the plan's daily cap for context; the
monthly-cap form (USD or "no cap", email alerts toggle; managers/owners);
billable verified results vs internal attempt cost; a forecast preview for
sourcing N leads on a chosen strategy; the latest forecasts with forecast vs
actual and ±25% verdict.

## Tenancy

Every query has an ownership filter: `api_usage.user_id`,
`enrichment_attempts.user_id`, `spend_caps/alerts/forecasts.user_id` equal to
the caller (or, in workers, the strategy owner resolved through
`notifications.owner_of_strategy` — the centralised resolver). Lead batches
for "verified results" via `lead_batches → strategies → products.user_id`.
Strategy ids in the preview go through the existing `_owned_strategy` (404).
Reconciliation filters by the forecast's own `user_id` as well as its
strategy/batch, so a strategy id can never pull another account's spend.
Tests assert another account's usage, attempts, caps, alerts and forecasts
never appear and never count toward this account's cap.

## Compliance review (CAN-SPAM / GDPR / CASL / PDPA)

* **No send path.** Nothing here sends outreach. Alert emails are Vectorae's
  own transactional mail to the account holder about their own account
  (`email_sender`, the same path as verification email) — not commercial
  messages to prospects, so CAN-SPAM/CASL outreach rules do not apply and the
  outreach gate is untouched. A test asserts no `messages` row is created and
  the outreach channel is never called.
* **No prompt.** No model call, so no untrusted text enters a prompt.
* **Data minimisation (GDPR/PDPA).** The new tables and F07's ledger rows hold
  no personal data of prospects — provider, operation, credits, ids only.
  The alert email contains amounts and a link, nothing about prospects.
* **Erasure / export.** All three tables are owned by `user_id` (CASCADE) and
  are in the account data export (`account_export.OWNERSHIP`). A GDPR-erased
  lead was never referenced by F07 rows.
* **Fail-safe direction.** A cap refusal never blocks an opt-out, a
  suppression or a compliance check — it only stops spending. Lead sourcing
  stopped by the cap leaves leads in their last committed state; suppression
  and exclusion checks already ran per lead.

## AI cost and budget impact

F07 makes **no model calls**. It *reduces* risk of spend: a third ceiling on
the account-capped purposes. Cost while on: per metered scope open, the
flag, prices and cap reads; per guarded model call and per paid data call,
one aggregate query (committed month spend); per paid data call one insert +
commit. Negligible next to a model or vendor call.

## Test plan

`tests/test_spend_guard.py` (offline, SQLite, FakeClaude, `httpx.MockTransport`
for the adapter path — no socket):

* flag off: no rows, no refusal, hooks inert, API 409, `check_budget`
  identical;
* forecast: strategy run equals `budget_plan`'s own numbers (planned steps ×
  step estimate + reserve), resume counts only undone steps, reverify = reserve;
  enrichment credits from defaults, then from calibrated history; lead-scoring
  calls = ⌈n/15⌉; sends 0 with note;
* pre-check: refused when forecast > remaining (429 body, nothing created —
  no strategy, no batch, nothing enqueued), allowed under it, no cap = allowed;
  refused row kept;
* per-call AI gate: a scope near the cap raises `MonthlySpendCapReached`
  before the model call; the strategy goes `budget_exhausted`; the plan's
  daily cap is still enforced with the monthly cap set higher;
* per-call data gate: `BaseHttpAdapter.call` refused before the HTTP request (the
  mock transport records zero requests); a paid call writes one ledger row;
  a cached call writes none; the waterfall's own provider is not
  double-counted; a capped lead stage stops the batch and later stages skip;
* alerts: 50/80/100 fire once each per month (call twice → one row each),
  a racing duplicate insert is absorbed by the unique constraint, email via
  the memory transport, never via the outreach channel, `email_alerts=false`
  → skipped, a new month fires again;
* reconciliation: forecast vs fixture `api_usage` + `enrichment_attempts`
  rows, error and ±25% verdict, window ends at the next forecast, another
  account's rows ignored;
* tenancy across every endpoint; RBAC (SDR cannot change the cap);
* F01 metrics unchanged by F07 rows.
* review fixes: spend committed by another run mid-scope is seen; own flushed
  calls are not counted twice; data calls inside a model scope use the fresh
  figure; committed spend is one statement; the 100% alert fires on a refused
  model call (including one caught inside the scope), data call and run, once
  per month; the market-signal refresh and single-lead re-verify honour the
  cap; a refusal inside the real waterfall (`_run_stage` →
  `find_missing_emails_impl`) writes no `error` attempt while a real provider
  error still does; flag-off query counts; savepoints (flag on before the
  migration; a failing meter insert keeps the caller's pending work).

`tests/test_spend_guard_migration.py`: migration creates exactly the model
tables, unique constraint present, downgrade round-trips.
`frontend/src/tests/spendGuard.test.ts` (vitest): percent/tone/threshold
logic, cap input validation, accuracy and error labels, the flag-off 409.

## Success metric

STAR row: **"Forecast within ±25% of actual in staging; zero runs above cap."**

* ±25%: measured by `reconcile` / `accuracy` on real staging runs — not
  measured here (no staging run has been made from this branch). The tests
  prove the reconciliation arithmetic on fixtures, not the forecast's
  accuracy. **UNVERIFIED until staging.**
* Zero runs above cap: enforced per call (AI and data) and tested offline.
  In production it holds to within the limits listed under Unverified.

## Unverified

* **Forecast accuracy** (±25%) — no staging measurement yet. The step
  estimates (`pipeline_est_step_*`), the default credits per lead (Apollo 2.0,
  Hunter 1.5) and lead-scoring tokens per call (2,500 / 1,000) are estimates.
* **Vendor prices** — `spend_guard_*_usd_per_credit` default 0; what Apollo
  and Hunter actually bill per call (and whether a search page costs one
  credit per result, as the adapter's `cost_units = per_page` assumes) is not
  verified against vendor billing. Data prices are applied at read time, so
  changing a price re-prices the month (unlike AI rows, priced at call time).
* **Concurrency** — every check re-reads committed spend, so the residual
  window is only other runs' *uncommitted* spend (their unflushed notes, at
  most one pipeline step, plus a call in flight); see "What hard means". Not
  measured under load on PostgreSQL. (The plan's DAILY cap, P0-5, still uses
  the spend at scope open; F07 does not change it.)
* **Lead-scoring attribution** — `api_usage` rows carry the strategy, not the
  batch, so two lead-sourcing runs on one strategy that overlap in time can
  attribute each other's scoring spend in `reconcile` (data credits are
  per batch and unaffected).
* **Forecast window on SQLite** — `api_usage.ts` / `enrichment_attempts.created_at`
  default to the database clock; on SQLite (tests only) a row written in the
  same second as the forecast can compare as earlier. Production PostgreSQL
  compares real timestamps. Tests set timestamps explicitly.
* **Spend outside guarded scopes** — sends, meeting briefs and call analysis
  are counted when metered but never refused by the cap (deliberately). The
  single-lead re-verify and the market-signal refresh are now guarded.
* **Consensus engine** (`consensus_enabled`, GPT-4o second opinion) is not in
  the strategy forecast.
* **Alert email delivery** — depends on the deployment's `EMAIL_PROVIDER`;
  Resend domain verification for `calendarharvest.com` is still a founder
  action (GAP-007).
* Not run against production PostgreSQL data or a live Celery beat.

## Help-center article to write

Title: **"Spend Guard: see a run's cost before it starts and cap your monthly spend"**

(Line numbers as of this PR.)

* Every strategy, lead-sourcing and Quickstart run is forecast first; if a
  monthly cap is set and the forecast exceeds what is left, the run is refused
  with a message and nothing is created or started
  (`app/api/spend_guard.py:52` `_guard`).
* The monthly cap is in addition to the plan's daily AI limit and each
  strategy's budget; it can never raise them
  (`app/services/spend_guard.py:21`; `app/services/usage_meter.py:263`
  `check_budget`).
* Alerts at 50%, 80% and 100% of the cap, once each per calendar month (UTC),
  in the app and by email (`app/services/spend_guard.py:64` `THRESHOLDS`,
  `:851` `evaluate_alerts`).
* The cap is checked before every AI call and every paid data call, so a run
  stops at the cap rather than after it; a stopped strategy can be resumed
  (`app/services/spend_guard.py:566` `check_model_call`, `:580`
  `check_data_call`; `app/workers/tasks.py:72` `_stop_for_budget`).
* Scheduled sends are never stopped by the cap; their AI cost still counts
  toward the month (`app/services/usage_meter.py:58` `ACCOUNT_CAPPED_PURPOSES`).
* The usage page separates verified results from the cost of every attempt,
  including attempts that found nothing (`app/services/spend_guard.py:949`
  `billable_summary`).
* Data credits are shown per provider; dollar amounts appear only once an
  administrator sets a price per credit (`app/services/system_settings.py:314`;
  `app/services/spend_guard.py:60` `PRICE_SETTINGS`).
* Only the account owner or a workspace manager can change the cap
  (`app/api/spend_guard.py:139` `set_cap`).
