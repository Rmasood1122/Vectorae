---
id: analytics-health-score-roi
title: Analytics, health score and ROI
audience: customer
last_verified: 2026-10-10
owner: help-center
sources:
  - app/services/pipeline_health.py
  - app/workers/health_tasks.py
  - app/api/analytics.py
  - app/services/roi_calculator.py
  - app/services/revenue_analytics.py
  - app/api/revenue_analytics.py
  - app/services/attribution.py
  - app/services/benchmarks.py
  - app/services/system_settings.py
  - app/services/share_links.py
  - app/api/share_links.py
  - app/config.py
  - frontend/src/app/(app)/analytics/page.tsx
  - frontend/src/components/strategy/PipelineHealthCard.tsx
  - frontend/src/app/(app)/analytics/revenue/page.tsx
  - frontend/src/components/analytics/AttributionPanel.tsx
  - frontend/src/components/analytics/BenchmarkPanel.tsx
  - frontend/src/components/settings/ShareLinksCard.tsx
  - docs/features/attribution-ledger.md
  - docs/features/benchmarks.md
  - docs/features/revenue-analytics.md
  - docs/features/positive-reply-classifier.md
---

# Analytics, health score and ROI

Victorae reports on your campaigns in several places: the **Analytics** page, the **Revenue & ROI** page, a shareable ROI dashboard, and a Pipeline Health Score. This article explains how each number is calculated so you know what it does and does not tell you.

## What is the Pipeline Health Score?

A single number from 0 to 100 for one campaign, made from four parts. Each part is scored 0 to 100, then weighted:

- **Reply rate** (35%): replies divided by sends, as a percentage. A 10% reply rate scores 10 on this part.
- **Sequence completion** (25%): finished enrollments divided by all enrollments.
- **Lead freshness** (20%): full marks if your newest lead is up to 3 days old, zero at 30 days or older, sliding in between.
- **Open conversations** (20%): 10 points for each lead sitting at **Replied**, up to 100.

The total falls into a band with a short message:

- 0–40 **critical**: "Pipeline empty. Add leads immediately."
- 41–60 **low**: "Pipeline thinning. Add 10 leads this week."
- 61–80 **moderate**: "Pipeline healthy. Maintain your cadence."
- 81–100 **strong**: "Pipeline strong. Focus on open replies."

Because the reply-rate part uses the raw percentage, a campaign with a normal cold-outreach reply rate earns only a few points from it. Read the four parts, not just the total. If the calculation fails, the score shows 0 and "critical" with an error flag, so a failed measurement can be told apart from an empty pipeline.

The score is refreshed every six hours for verified and running campaigns, and recalculated live when requested. It is available on every plan. On a verified strategy's page, the **Pipeline health** card shows the score out of 100, its band and message, and the four parts. It is also available through the Victorae API.

## What does the Analytics page show?

Choose a campaign on **Analytics** to see, among other panels: reply quality (the raw reply rate next to the positive reply rate), sequence completion, attribution ("What earned the outcomes"), and benchmarks ("How you compare"). The page also links to **Revenue & ROI**.

The positive reply rate counts only replies labelled **interested**. Replies that could not be classified are counted as unclassified, never guessed.

## How does attribution decide which message earned a meeting?

Every booked meeting, won deal and **interested** reply is credited to a message, and Victorae always records how it decided and how sure it is:

- **Direct reply:** their reply is linked to a specific message. Confidence 1.0.
- **Thread match:** their reply is in the same email thread as one of your messages. Confidence 0.85.
- **Last touch:** nothing is linked, so the most recent message sent **before** the outcome gets the credit. This is a guess, and its confidence drops with time: 0.7 within a day, 0.55 within 3 days, 0.4 within a week, 0.25 within a month, 0.1 after that.
- **None:** nothing was sent before the outcome, so nothing is credited.

The panel groups results by step and by channel, and each group shows how many entries are confirmed. When fewer than half are confirmed, a caveat appears above the totals. Attribution is updated every 30 minutes, and **Recompute** runs it now. A message sent after the outcome is never credited.

## How are benchmarks calculated, and what are they not?

**How you compare** shows your reply rate, meeting-booked rate and bounce rate per channel next to the spread across **other accounts on this Victorae deployment** over the last 90 days. It is not an industry statistic.

Rules that apply (defaults, set by your administrator):

- An account counts only with at least 30 sends in that group.
- A group is shown only if at least 10 accounts are in it. This can never be set below 5. Smaller groups are not stored at all.
- Only the 25th, 50th (median) and 75th percentiles are shown, rounded to half a percentage point. The number of accounts is shown as a band (10+, 25+, 100+).
- Suspended accounts are excluded.

If not enough accounts qualify, you will not see a comparison.

## How is ROI calculated on the Revenue & ROI page?

Open **Analytics**, then **Revenue & ROI**, and pick a period (30 days, 90 days, 12 months or year to date). Per campaign you see:

- **Revenue:** deals marked won, dated by close date.
- **Meetings:** distinct leads with a booked meeting in the period.
- **Cost:** costs you record with **Add cost** (data, tools, ads, or hours × rate), metered AI usage priced with your administrator's estimates, AI call minutes, and a share of account-wide costs spread by each campaign's sends.
- **Cost / meeting**, **Cost / deal**, and **ROI** = (revenue − cost) ÷ cost. ROI is blank when there is no cost.

The report uses your most common deal currency (USD if you have no deals). Amounts in other currencies are left out and counted in a notice, never converted. AI costs are in USD, so they are only included in a USD report.

## What is on the shareable ROI dashboard?

In **Settings** you can create a share link with a title, one campaign or **All campaigns**, and an expiry (default 30 days, up to 365). The link is shown once. Anyone with it can view a read-only page until it expires or you click **Revoke**. Creating a link requires a verified phone number when your deployment requires phone verification, which is the default.

It shows six numbers:

- **Meetings booked:** booked meetings from leads whose sequence reached step 3 or later. Meetings booked off steps 1 or 2 are not counted.
- **Pipeline value:** the estimated deal value of leads currently at Meeting booked, Proposal sent or Closed won (not limited to the date range). Leads at Opportunity are not included.
- **Messages sent**, and **reply rate** (unique leads who replied ÷ leads contacted).
- **Time saved:** an estimate of 15 minutes per message plus 90 minutes per meeting.
- **Revenue attributed:** value from leads that closed won.

These are based on lead statuses, so they can differ from the deal-based Revenue & ROI page. For money, the deal-based page is the more exact one.

## Related
- [Replies and the unified inbox](13-replies-and-unified-inbox.md)
- [The learning loop and A/B tests](17-learning-loop-and-ab-tests.md)
- [Deliverability and mailbox health](19-deliverability-and-mailbox-health.md)
- [Plans, limits and billing](22-plans-limits-billing.md)
- [Glossary](28-glossary.md)
