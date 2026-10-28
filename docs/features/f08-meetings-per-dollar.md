# F08 — Meetings-per-Dollar Ledger

**Flag** `meetings_ledger_enabled` (System Settings, default **off**) ·
**Setting** `meetings_ledger_infra_cents_per_1k_sends` (default **0**) ·
**Service** `app/services/meetings_ledger.py` (`build_ledger`, `to_csv`, `to_pdf`) ·
**API** `app/api/meetings_ledger.py` ·
**UI** `frontend/src/components/analytics/MeetingsLedgerPanel.tsx` on
Analytics › Revenue & ROI, helpers in `frontend/src/lib/meetingsLedger.ts` ·
**Migration** none.

## Problem

Competitor complaint (STAR A.6, F08): *buyers judge on meetings per dollar and
pipeline, not emails sent.* Outbound tools report sends, opens and reply rates —
vanity metrics. What a buyer actually asks is "what did each meeting cost me,
and how sure are you that this campaign earned it?"

## Recon — this is "extend revenue analytics + attribution", not a new system

More than half of this feature already existed, so F08 is built **on top of**
two existing modules and duplicates neither:

| Already existed | Where | What F08 takes from it |
|---|---|---|
| Per-campaign cost (recorded costs, metered AI, AI-call minutes, pay-per-meeting fees, account-wide costs spread by sends), cost per meeting, revenue, currency discipline | `app/services/revenue_analytics.py` (`report`) | The whole cost side, unchanged. F08 calls `report()` and only **decomposes** its numbers and adds the infrastructure line. |
| Which touch earned an outcome, method + confidence, the positive-reply rule | `app/services/attribution.py` (ledger entries, `METHODS`, `METHOD_LABELS`) | Confidence of every booked meeting and qualified conversation, read from `attribution_entries` by `(outcome_kind, outcome_id)`; the "interested reply" rule via the new public `attribution.is_positive_reply` (a one-line wrapper around the existing private rule). |
| Meeting held | `meetings.status = completed` (set by End Meeting or the stale-meeting sweep only when the meeting was actually started), `meeting_outcomes` ("Log Meeting Outcome") | The "held" count. |
| PDF rendering (Pillow, no system libraries) | `app/services/audit_trail.render_pdf` | Its paging loop is extracted to `audit_trail.render_lines_pdf`; the audit PDF now calls it too (byte-for-byte identical layout). |

New: the per-campaign three-stage funnel (qualified → booked → held) with a
cost per stage, the cost decomposition AI / data / other / shared /
platform fees / infrastructure, an independent reconciliation block, CSV and
PDF export, and one clean service function for F09.

## Behavior

For a period (default: the last 30 days) and optionally one campaign, per
campaign (a strategy = one campaign, as everywhere in the schema):

### Counts — every count is DISTINCT LEADS

| Metric | Definition |
|---|---|
| **Qualified conversations** | Leads with, in the period, a `replied` outcome that passes the attribution ledger's own positive-reply rule (an `interested` reply at or before the outcome — `attribution.is_positive_reply`), **or** a `booked` outcome. A booking is a qualified conversation even without a classified reply, so the funnel is monotonic: qualified ≥ booked. |
| **Meetings booked** | Leads with a `booked` outcome in the period — exactly the `meetings` figure of `revenue_analytics.report`, so cost per meeting agrees with the Revenue & ROI page when the infrastructure rate is 0. |
| **Meetings held** | Leads with, in the period, a `meetings` row hosted by the account with status `completed` (dated by `actual_start_at`, else `start_at`), **or** a "Log Meeting Outcome" submission by the account (dated by its creation). Each row records which source counted it. |

The campaign of an event is the lead's campaign (`leads.strategy_id`), as in
revenue analytics.

### Confidence (reused, never re-derived)

Each counted event carries the attribution ledger's verdict for the outcome
that counted it: `direct_reply` (1.0, a fact), `thread_match` (0.85),
`last_touch` (decaying guess), `none` (nothing was sent before it), or
**`pending`** — the outcome exists but the half-hourly attribution sweep has
not written its ledger entry yet. Pending is shown as pending, never guessed.
A held meeting inherits the lead's latest `meeting_booked` entry at or before
the meeting; with none, it is `pending`.

Per campaign: meetings booked broken down by method, the `certain` count
(`direct_reply` only), and the mean confidence of the attributed ones. The UI
raises a caveat when fewer than half of the booked meetings are certain —
the same rule as the attribution panel.

### Cost

`total_cost = revenue_analytics total cost + infrastructure allocation`,
decomposed:

| Line | Source |
|---|---|
| AI | metered model spend (`api_usage`) + AI-call minutes + recorded costs with category `ai` |
| Data | recorded costs with category `data` against the campaign |
| Other recorded | the campaign's remaining recorded costs (tools, time, ads, other) |
| Shared | its share of account-wide costs, by sends (revenue analytics' rule) |
| Platform fees | pay-per-meeting fees (pending + charged) |
| Infrastructure | `sends × meetings_ledger_infra_cents_per_1k_sends ÷ 1000` |

**Infrastructure allocation is never invented.** It is an explicit admin
setting, `meetings_ledger_infra_cents_per_1k_sends`, in **US cents per 1,000
sends**, default **0** = not included (the ledger says so in the JSON, the UI
and both exports). It is a per-send rate rather than a monthly total so that
it cannot be double-counted across accounts on one deployment and needs no
proration rule. Like metered AI, it is priced in USD and is only added when
the report currency is USD (otherwise it is reported as excluded).

### Ratios

`cost_per_qualified_conversation_cents`, `cost_per_meeting_booked_cents`,
`cost_per_meeting_held_cents` (null when the denominator is 0 — "no meetings"
never renders as "meetings cost nothing"), and
`meetings_booked_per_1k_spend` (booked meetings per 1,000 units of the report
currency; null when there is no cost). Revenue, deals won (from revenue
analytics) and open pipeline (open deals linked to the campaign, **as of
now**, not bounded by the period) sit beside them.

### Reconciliation (the success metric, in the output itself)

Every ledger carries a `reconciliation` block computed **independently from
the raw rows**, account-wide even when one campaign is selected:

* `raw_cost_cents` = sum of `campaign_costs` (period, report currency) +
  (USD only) `api_usage` micros, call minutes × rate and pay-per-meeting fees,
  each summed once from the raw table;
* `ledger_cost_cents` = sum of every campaign's cost excluding infrastructure
  + the unallocated account-wide remainder;
* `difference_cents`, `rounding_tolerance_cents` (revenue analytics rounds
  API micros and call minutes per day × campaign group; the tolerance is
  `groups // 2 + 1`, 0 when there are no groups) and `balanced`;
* `decomposition_balanced`: AI + data + other + shared + platform equals the
  revenue-analytics total for every campaign;
* `booked_outcome_leads` (raw distinct booked leads) vs the ledger's booked
  total, and `attributed` + `pending` = booked.

## Non-goals

* No currency conversion (revenue analytics' rule stands: other currencies are
  excluded and counted).
* No new cost sources. Data-provider credits are only in the ledger when they
  are recorded as costs (`POST /costs`, category `data`); per-attempt data
  cost metering is F01/F07's job.
* No change to how attribution is computed, how revenue analytics allocates,
  or to any send path. The ledger only reads.
* No scheduled job, no stored snapshot (computed live, like the revenue report).
* Not plan-gated: it is a reporting view over data every plan already has, and
  F09 (30-Day Pilot Mode) must be able to show it to a pilot account.

## Data model and migration

None. Reads `outcomes`, `leads`, `strategies`, `products`, `attribution_entries`,
`inbound_replies`, `meetings`, `meeting_outcomes`, `deals`, `campaign_costs`,
`api_usage`, `calls`, `billable_meetings`. Two new `system_settings` keys
(rows only when an admin changes them).

## API

| Method | Path | Notes |
|---|---|---|
| `GET` | `/analytics/meetings-ledger?date_from&date_to&strategy_id` | JSON ledger. Default period: last 30 days; at most 3 years. |
| `GET` | `/analytics/meetings-ledger/export?format=csv\|pdf&view=campaigns\|events&date_from&date_to&strategy_id` | `Content-Disposition: attachment`, `Cache-Control: private, no-store`. CSV `view=campaigns` (default): one row per campaign + a TOTAL row; `view=events`: one row per counted event with its attribution. PDF: summary, per-campaign blocks, attribution mix, reconciliation, definitions. |

Flag off → **409** `"The meetings-per-dollar ledger is disabled by the
administrator"` (the existing disabled-feature pattern, `app/api/calls.py`).
A `strategy_id` the caller does not own → **404** (`_owned_strategy`).

### Service contract for F09

```python
from app.services import meetings_ledger
ledger = meetings_ledger.build_ledger(db, user_id, date_from, date_to,
                                      strategy_ids=[...] | None)
meetings_ledger.to_csv(ledger, view="campaigns")   # -> str
meetings_ledger.to_pdf(ledger)                     # -> bytes
meetings_ledger.pdf_lines(ledger)                  # -> [(text, style)], the body to_pdf renders (F09)
```

`build_ledger` does not check the flag (F09 decides its own gating) and does
not commit; it filters every query by `user_id` and silently drops
`strategy_ids` the account does not own.

## UI

A "Meetings per dollar" card on Analytics › Revenue & ROI, sharing the page's
period selector: totals tiles (cost per qualified conversation / booked /
held), a per-campaign table with the funnel, cost per stage and the booked
meetings' attribution mix, the infrastructure line ("not included — rate is 0"
when 0), a certainty caveat, a reconciliation status line, and CSV / PDF
download buttons. When the flag is off the card shows one line saying the
admin can turn it on, and nothing else.

## Tenancy

Every query has an ownership filter: campaigns via `products.user_id`;
outcomes and meetings via the lead's campaign being owned **and**
(`meetings.host_user_id`, `meeting_outcomes.user_id`,
`attribution_entries.user_id`) equal to the caller; costs, usage, calls and
fees through revenue analytics' existing owner filters, repeated in the raw
reconciliation queries. `strategy_ids` are intersected with the owned set.
Tests assert another account's rows never appear in the counts, costs,
events or exports.

## Compliance review (CAN-SPAM / GDPR / CASL / PDPA)

* **No send path.** The ledger reads; nothing it does can contact a prospect,
  so the outreach gate and send chokepoint are untouched.
* **No model call.** No prompt, so no untrusted text enters a prompt.
* **Personal data in exports (GDPR/PDPA data minimisation).** The events CSV
  carries the lead id and company only — no names, emails or message text —
  because a cost report is the kind of file that gets forwarded. The campaign
  CSV and the PDF carry aggregates and campaign names only. Exports are
  `private, no-store`.
* **Erasure.** A GDPR-erased lead's outcomes cascade away with the lead, so it
  drops out of the counts; its deals and fees remain as aggregates (existing
  `SET NULL` behaviour) — unchanged by F08.
* **CSV injection.** Text cells starting with `=`, `+`, `-`, `@`, tab or CR
  are prefixed with `'` (campaign names are user-controlled).

## AI cost and budget impact

Zero. No model calls, so no usage scope is needed. Cost is database reads: one
revenue report plus a handful of grouped queries and one positive-reply check
per `replied` outcome of a not-yet-qualified lead in the period (the
attribution rule is reused as-is rather than re-implemented; acceptable at
current volumes, noted under Unverified).

## Test plan

`tests/test_meetings_ledger.py` (offline, SQLite):

* reconciliation: the ledger's cost total equals the raw `campaign_costs` +
  `api_usage` + `calls` + `billable_meetings` rows; every campaign's
  decomposition equals revenue analytics' total; cost per booked meeting
  equals revenue analytics' when the rate is 0;
* attribution: booked counts by method equal the `attribution_entries` rows;
  an outcome without an entry is `pending`; certain = direct_reply only;
  a held meeting inherits the booking's entry;
* funnel rules: unclassified or non-interested replies are not qualified; a
  booking without a reply is; an uncompleted / cancelled meeting is not held;
  a logged meeting outcome is; distinct leads; period boundaries;
* infrastructure: 0 by default and labelled; a rate adds
  `sends × rate ÷ 1000`; excluded (and flagged) in a non-USD report;
* tenancy: another account's outcomes, meetings, entries and costs never
  appear; a foreign `strategy_ids` value yields nothing; foreign
  `strategy_id` on the API is 404;
* flag off: both endpoints 409, and no other endpoint changes (revenue report
  identical with the flag on or off);
* exports: CSV headers/rows/TOTAL and formula-injection guard; events CSV has
  no names or emails; PDF starts `%PDF`; audit-trail PDF still renders.

`frontend/src/tests/meetingsLedger.test.ts` (vitest): formatting, the
certainty caveat, the infrastructure label, the export URL builder.

## Success metric

STAR row: **"Ledger reconciles to raw cost and attribution rows in tests."**
Measured by the reconciliation tests above — they compare the ledger to the
raw tables, not to the ledger's own intermediate numbers. Scorecard row 8
("cost per meeting reported per campaign") is met in code; whether customers
use it is not measured here.

## Unverified

* Not run against production PostgreSQL data; per-row positive-reply checks
  could be slow for an account with tens of thousands of replies in a period
  (UNVERIFIED — no production-scale measurement).
* "Meetings held" depends on the meeting being started/ended in Vectorae or
  an outcome being logged; a meeting held entirely in an external calendar
  with neither is not counted (undercount, by design — no guessing).
* AI and call costs are the admin's configured price estimates, not vendor
  invoices (inherited from revenue analytics; UNVERIFIED against invoices).
* The infrastructure rate is whatever the admin enters; Vectorae does not
  verify it against a hosting bill.
* Help-center article: `docs/help-center/` does not exist on this branch
  (Phase 2 not merged here); the article must be added when it lands.
