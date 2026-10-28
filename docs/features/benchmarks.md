# Feature 6 — Anonymised benchmarks

## What it does
**Analytics › How you compare** shows your reply rate, meeting-booked rate and
bounce rate for each channel, next to the spread of the same rates across other
Vectorae accounts on this deployment. Where possible the comparison uses your
campaign's industry, and otherwise all industries.

It is an observation about this deployment's accounts over a trailing window.
**It is not an industry statistic**, and the panel and API say so.

## How a benchmark is built (nightly, `learning` queue)
1. Count, for each account, strategy and channel over the last
   `benchmark_window_days` (90):
   - dispatched messages (sent + bounced)
   - REPLIED, BOOKED and BOUNCED outcomes

   These are the same definitions as the CRM campaigns dashboard, so your number
   here matches the one there.
2. Assign each strategy to an industry from its canonical ICP: one industry →
   that industry, several → `multiple`, none → `unspecified`. Every account also
   counts toward `*` (all industries).
3. Sum an account's strategies within each (industry, channel). An account
   counts only with at least `benchmark_min_account_sends` (30) sends there.
   Suspended accounts are excluded.
4. Publish a bucket only if it has at least `benchmark_min_accounts` accounts
   (default 10; **never below 5**, whatever the setting). Suppressed buckets are
   **not written**.
5. For each metric, store the 25th, 50th and 75th percentile across accounts,
   rounded to half a percentage point. The whole table is replaced in one
   transaction, so a re-run is idempotent.

## Anonymity — what it does and does not guarantee
| Protection | Why |
|---|---|
| The account, not the message, is the unit | One high-volume account is one data point, not the benchmark |
| Minimum sends per account | A 3-send account's 33% is noise, not a data point |
| Minimum accounts per bucket, with a hard floor | Small buckets are never stored, so nothing downstream can read them |
| Rounded percentiles only; no totals, min or max | Less to triangulate from |
| Account count shown as a band (10+ / 25+ / 100+) | |
| No user, strategy, lead or product id in `benchmark_buckets` | Pinned by a test |

**Not guaranteed:** in a small bucket a percentile is still, by construction,
one account's rounded rate. It is unidentifiable, not absent. An operator who
controls several accounts could stack a bucket; no threshold can detect that.

## API
`GET /benchmarks?strategy_id=` returns:
- per channel: your dispatched count and rates, `enough_data`, the published
  industry bucket (or `null`) and the all-industries bucket (or `null`)
- the window, the thresholds, the metric definitions, and the "not an industry
  statistic" note

The route is `/benchmarks`, not `/api/benchmarks`: no route in this API has an
`/api` prefix. A campaign that is not yours returns 404.

## Settings (Admin › System Settings)
`benchmark_min_accounts` (10, floor 5), `benchmark_min_account_sends` (30),
`benchmark_window_days` (90, clamped to 7–365).

## Files
`app/services/benchmarks.py`, `app/api/benchmarks.py`,
`app/workers/benchmark_tasks.py` (beat `compute-benchmarks`, :40 after the
aggregation hour), migration `0050_benchmarks.py`,
`frontend/src/components/analytics/BenchmarkPanel.tsx`.
Tests: `tests/test_benchmarks.py`, `tests/test_benchmarks_migration.py`.
