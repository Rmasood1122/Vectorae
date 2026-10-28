---
id: research-pipeline
title: The research pipeline
audience: customer
last_verified: 2026-10-10
owner: help-center
sources:
  - app/pipeline/engine.py
  - app/pipeline/registry.py
  - app/pipeline/budget_plan.py
  - app/workers/tasks.py
  - app/api/strategies.py
  - app/api/schemas.py
  - app/db/models.py
  - app/services/usage_meter.py
  - app/services/market_intel.py
  - app/verification/loop.py
  - app/core/plans.py
  - app/config.py
  - app/services/notifications.py
  - frontend/src/app/(app)/strategies/new/page.tsx
  - frontend/src/app/(app)/strategies/detail/page.tsx
  - frontend/src/components/strategy/ResumeStrategyButton.tsx
  - frontend/src/lib/api/strategies.ts
  - frontend/src/lib/api/client.ts
  - docs/program/DECISIONS.md
---

# The research pipeline

When you launch a strategy, Vectorae runs a research pipeline: a fixed series of AI research steps that ends in a written strategy document. This article explains how many steps run, what the statuses mean, how budgets and the daily AI limit work, and how to resume a run that stopped.

## What does the research pipeline do?

The pipeline is organized into 8 phases of 9 steps each. The ninth step of every phase is a summary step that pulls together the eight before it. The strategy document is built from those 8 phase summaries.

The 8 strategy phases are: Product decomposition, ICP definition, Market sizing, Competitor analysis, Channel analysis, Messaging & offer, Objections & proof, and Execution plan.

Before the first phase, Vectorae fetches recent market signals once for the strategy: news headlines from the last 30 days (from Google News) and, where available, company funding and headcount signals from Apollo. They are labelled as headlines to check, not facts to quote. If this fetch fails, the pipeline continues without them.

The pipeline runs **once per strategy**. It does not run again for each prospect.

## Why do some strategies have 72 steps and others 144?

It depends on your answer to "Do you have any past clients for this?" when you create the strategy.

| You said | What runs | Steps |
|---|---|---|
| Yes, and added past clients | The strategy pipeline, anchored on your past clients | 72 |
| No | The strategy pipeline, then a go-to-market (GTM) pipeline | 144 |

The GTM pipeline's 8 phases are: Positioning, Pricing, Launch sequencing, Channel budget, Content plan, Partnerships, Sales motion, and Funnel metrics. When both run, the strategy pipeline runs first and the GTM pipeline second.

## How long does it take?

There is no fixed duration; it depends on the AI service's response times. The research runs on Vectorae's servers, so you can close the browser tab. The strategy page shows one progress card for each pipeline (the strategy pipeline, and the GTM pipeline when it runs) with the steps done out of the total, and under it each of the 8 phases by name with its own steps done out of the total. When verification finishes, Vectorae sends a push notification ("Your strategy is ready" or "Strategy needs review") to devices registered for notifications.

Each pipeline run has a time limit of 2 hours. If a run is interrupted, for example by that limit or a temporary error, Vectorae retries it automatically up to 3 times, 30 seconds apart, and each retry continues from the last finished step. If the error cannot succeed on retry, or the retries run out, the strategy is marked `failed` and the reason is shown on the strategy page.

## What do the strategy statuses mean?

| Status | Meaning |
|---|---|
| `pending` | Created; research has not started yet. |
| `researching` | The research steps are running. |
| `verifying` | Research is done; the 10 verification checks are running. |
| `verified` | All 10 checks passed (or a person signed off failed checks; see the verification article). You can now find leads and launch outreach. |
| `needs_human_review` | At least one check still failed after its fix attempts. |
| `executing` | Reserved for a strategy with live outreach. The current release does not set it: a strategy stays `verified` while its outreach runs. |
| `failed` | The run stopped on an error it could not recover from. |
| `budget_exhausted` | The run stopped because the next AI call could have gone over the strategy's budget or your daily AI limit. Everything already done is saved. |

## What is a strategy budget?

Every strategy has a dollar budget for the AI spending of its research and verification. Writing outreach messages later does not count against it. Before each AI call, Vectorae checks whether the call could take the run past its budget, assuming the worst case. If it could, the call is not made and the strategy stops as `budget_exhausted`. Nothing is spent on the refused call.

- The default budget is $15. Through the API you can choose any amount from $0.50 up to the server's ceiling, which is $50 by default. The New strategy form uses the default.
- If the budget cannot cover the full pipeline, Vectorae plans a shorter run instead of stopping partway: it always keeps each phase's summary step and runs fewer of the supporting research steps (between 2 and 8 steps per phase, instead of the full 9). A shorter run also gets at most 2 attempts per verification check.
- If the budget cannot cover even 2 steps per phase, Vectorae refuses to create the strategy and tells you the minimum needed.

## What is the daily AI limit?

Separately from each strategy's budget, every plan has a limit on how much AI spending your whole account can run up per day. The day resets at 00:00 UTC.

| Plan | Daily AI limit |
|---|---|
| Free | $10 |
| Starter | $25 |
| Growth | $60 |
| Pay per meeting | $60 |
| Scale | $150 |
| Pro | $150 |
| Enterprise | $400 |

These are the amounts in the current release. They have not been finalized and may change.

All AI use on your account counts toward the day's total. The limit is enforced on research, verification and lead scoring. Message writing, meeting briefs and call analysis are not stopped partway when the limit is reached.

When the limit is used up:

- Starting a new strategy is refused with a message like "Your Free plan's daily AI limit ($10.00) is used up. It resets at 00:00 UTC."
- A strategy that is already running stops as `budget_exhausted`. Resume it after the reset, or upgrade your plan.
- Resuming (and re-verifying) is refused until the reset, with the same message.

## How do I resume a strategy that stopped?

Press **Resume** on the strategy page. It appears when the status is `pending`, `researching`, `verifying`, `failed` or `budget_exhausted`. Then press **Confirm resume**.

- The run continues from the last finished step. Finished steps are not repeated or paid for again.
- If the status is `pending`, `researching` or `verifying`, only resume if progress has really stopped. Resuming a run that is still working can duplicate work.
- For a `budget_exhausted` strategy you can enter a **New budget $**. It must be more than what the strategy has already spent.
- Resume is refused for `verified` and `needs_human_review` strategies. For `needs_human_review`, use **Re-verify** instead (see the verification article).

## Are there limits on how many strategies I can start?

Yes. By default you can start at most 2 strategies per hour; this is a deployment setting. Your plan also limits the total number of strategies you can have: 3 on Free, 10 on Starter and on Pay per meeting, 25 on Growth, and no limit on Scale, Pro and Enterprise.

## Can I read the research?

You can read the finished documents. The outputs of individual research steps are not shown on the strategy page; they are compiled into the documents. When the documents are ready, **View documents** opens the strategy document (and the GTM document for 144-step strategies).

## Related
- [Getting started](03-getting-started.md)
- [Strategy verification](05-strategy-verification.md)
- [Plans, limits and billing](22-plans-limits-billing.md)
- [Troubleshooting](27-troubleshooting.md)
- [Glossary](28-glossary.md)
