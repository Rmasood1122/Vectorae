---
id: benchmarks-what-good-looks-like
title: Benchmarks and what good looks like
audience: customer
last_verified: 2026-10-18
owner: help-center
sources:
  - app/services/pipeline_health.py
  - app/services/benchmarks.py
  - app/services/system_settings.py
  - app/services/sequence_engine.py
  - app/services/compliance_rules.py
  - app/services/crm_service.py
  - app/services/mailbox_health.py
  - app/services/deliverability.py
  - app/services/funnel.py
  - app/services/reply_sentiment.py
  - app/config.py
  - frontend/src/lib/campaignBounce.ts
  - frontend/src/app/(app)/campaigns/page.tsx
  - frontend/src/components/campaigns/FunnelHeatmap.tsx
  - frontend/src/components/campaigns/SentimentTrend.tsx
  - frontend/src/components/analytics/BenchmarkPanel.tsx
  - frontend/src/components/strategy/PipelineHealthCard.tsx
  - frontend/src/components/settings/MailboxHealthCard.tsx
  - docs/features/benchmarks.md
  - docs/features/mailbox-health.md
---

# Benchmarks and what good looks like

This article collects the numbers Vectorae itself uses to judge a campaign: the reply, bounce and meeting figures it compares or acts on, and the score bands of its health scores. For each one it says what Vectorae treats the number as and where you see it. How the scores are calculated is in [Analytics, health score and ROI](18-analytics-health-score-roi.md) and [Deliverability and mailbox health](19-deliverability-and-mailbox-health.md).

## Is there a reply rate Vectorae considers good?

No. Vectorae has no fixed target reply rate and never labels your reply rate as good or bad on its own. It gives you three ways to judge it instead:

- **Against other accounts.** On **Analytics**, **How you compare** shows your reply rate next to the median and the "Middle half" (25th to 75th percentile) of the accounts on your Vectorae deployment (yours included) over the last 90 days. Your own rate is shown only once you have at least 30 sends on that channel. Vectorae treats this as an observation about its own customers, not an industry statistic.
- **Against your other steps.** On **Campaigns**, the **Funnel** tab names your **Best converting step**: the step with the highest reply rate, counted only once a step has been sent to at least 20 leads.
- **Inside the Pipeline Health Score.** Reply rate is 35% of the score and counts as its raw percentage, so a 10% reply rate earns 10 of 100 points on that part.

A reply rate also counts replies such as "remove me". The **Positive reply rate** on **Analytics** counts only replies labelled interested, which is the better signal.

## At what bounce rate does Vectorae stop sending?

There are two automatic stops, at different levels:

**A whole campaign** is paused when its bounce rate goes over 3%, judged only once at least 10 messages have been sent. The rate is bounces divided by all messages the campaign has sent. A compliance rule in your workspace can set a lower limit, never a higher one. On **Campaigns** the **Bounce rate** turns red when it is over the limit, and a paused campaign shows "Bounce rate auto-pause: … is above the …% limit. Fix deliverability issues, then resume." You also get a notification. The campaign stays paused until a person resumes it.

**A single mailbox** is judged on its own bounces over the last 30 days:

- 2% or more takes 35 points off its health score. On its own that leaves 65, which is **throttled** (sending slowed to a quarter of its normal allowance).
- 5% or more takes 65 points off. On its own that leaves 35, which is **paused**: that mailbox sends nothing until you click **Resume**.

Missing DNS records or a high complaint rate take further points off, so a mailbox can be paused at a lower bounce rate. The mailbox check has no minimum number of sends, so on a new mailbox a single bounce can move the rate a lot. Vectorae treats a complaint rate (its proxy: unsubscribes plus stop requests) of 0.1% as a warning and 0.3% as the danger line.

## What health score should I aim for?

Vectorae has three scores out of 100, each with its own bands:

| Score | Where you see it | Bands |
|---|---|---|
| Pipeline Health Score (one strategy) | **Pipeline health** card on a verified strategy's page | 0–40 critical, 41–60 low, 61–80 moderate, 81–100 strong |
| Mailbox health (one sending mailbox) | **Mailbox health** card under **Settings** | 70 or more healthy, 40–69 throttled, under 40 paused |
| Email health (your sending domain) | **Email health** card under **Settings** | under 70 gives a warning, also when you launch a campaign |

For the Pipeline Health Score, Vectorae treats 61 and up as healthy ("Pipeline healthy. Maintain your cadence.") and 81 and up as strong. Because the reply part uses the raw percentage, a normal cold-outreach campaign earns few points there, so a score in the moderate band is not a bad result. Look at the four parts on the card, not only the total.

For a mailbox, 70 is the line between full speed and slowed down. A mailbox missing SPF, DKIM and DMARC but with no bounce or complaint problems scores 45: throttled, not paused. For the domain, 70 is the threshold for a warning; once your account's bounce rate across all your sends over the last 30 days is above 2% (the same figure counts against each of your sending domains), it costs one point for every 0.1 percentage point of the whole rate (2.5% costs 25 points), up to 30 points.

## Is there a meeting rate Vectorae treats as a target?

No. Vectorae does not set a target for meetings booked, and nothing pauses or alerts on a low meeting rate. You can compare it in two places:

- **How you compare** on **Analytics** shows your **Meeting-booked rate** (meetings booked divided by messages sent or bounced) next to the median and middle half of the accounts on your deployment (yours included), under the same rules as reply rate: the last 90 days, at least 30 sends from you, and enough accounts to publish a comparison. If too few accounts qualify, you see "Not enough accounts yet to publish a benchmark for this channel."
- The **Funnel** tab on **Campaigns** shows a booking rate for each step of each sequence, so you can see which step meetings come from.

Bounce rate is the one metric in **How you compare** where lower is better.

## When does Vectorae warn me that replies are turning negative?

On **Campaigns**, the **Sentiment** tab shows the share of human replies by week (out-of-office, bounces and automated responses are left out). Vectorae raises an **Objection spike** alert when, comparing the last complete week with the week before:

- both weeks have at least 10 human replies,
- the share of objections rose by more than 20% (relative), and
- it also rose by at least 5 percentage points.

If the earlier week had no objections, the 5-point rise alone is enough. Each campaign week alerts once. Vectorae treats a smaller change as normal week-to-week noise, so no alert means the change was below these lines, not that every reply was positive. Your administrator can change the 20% and the 10-reply minimum for the whole deployment.

## Related
- [Analytics, health score and ROI](18-analytics-health-score-roi.md)
- [Deliverability and mailbox health](19-deliverability-and-mailbox-health.md)
- [Email sending through Gmail](09-email-sending-gmail.md)
- [Replies and the unified inbox](13-replies-and-unified-inbox.md)
- [Reply copilot and classification](38-reply-copilot-and-classification.md)
- [Pause, stop and the kill switch](25-pause-stop-kill-switch.md)
