---
id: guide-solo-founder
title: "Running Vectorae on your own: a solo founder's routine"
audience: customer
last_verified: 2026-10-18
owner: help-center
sources:
  - frontend/src/app/(app)/strategies/page.tsx
  - frontend/src/app/(app)/strategies/new/page.tsx
  - frontend/src/components/strategy/OutreachPanel.tsx
  - frontend/src/components/strategy/PipelineHealthCard.tsx
  - frontend/src/app/(app)/send-approval/page.tsx
  - app/api/send_approval.py
  - app/services/daily_send_approval.py
  - frontend/src/app/(app)/inbox/page.tsx
  - frontend/src/lib/inbox.ts
  - frontend/src/components/campaigns/ReviewQueuePanel.tsx
  - frontend/src/lib/sendReview.ts
  - frontend/src/app/(app)/campaigns/page.tsx
  - frontend/src/components/settings/MailboxHealthCard.tsx
  - frontend/src/components/settings/DeliverabilityCard.tsx
  - frontend/src/components/analytics/AttributionPanel.tsx
  - frontend/src/components/analytics/BenchmarkPanel.tsx
  - frontend/src/app/(app)/analytics/revenue/page.tsx
  - frontend/src/components/shell/nav.ts
  - frontend/src/components/shell/Shell.tsx
  - frontend/src/components/campaigns/SendApprovalBanner.tsx
  - docs/help-center/26-mobile-app.md
  - frontend/src/components/shell/WorkspaceSwitcher.tsx
  - app/services/workspaces.py
  - app/api/sequences.py
  - app/db/models.py
  - app/core/plans.py
  - docs/help-center/03-getting-started.md
  - docs/help-center/04-research-pipeline.md
  - docs/help-center/12-send-review-queue.md
  - docs/help-center/13-replies-and-unified-inbox.md
  - docs/help-center/18-analytics-health-score-roi.md
  - docs/help-center/19-deliverability-and-mailbox-health.md
  - docs/help-center/25-pause-stop-kill-switch.md
---

# Running Vectorae on your own: a solo founder's routine

This guide is for one person running Vectorae alone: one product, one strategy, and a short list of screens to check each day and each week. It links to the detailed articles instead of repeating them.

## How should I set up Vectorae for a single offer?

One product and one strategy. Go to **Strategies**, press **New strategy**, and fill in the form once. Describe what you sell, and choose **A product** or **A skill / service** (if what you sell is a service, choose the second). Every time you complete that form, Vectorae creates a new product and a new strategy, so if you only sell one thing, complete it once and keep working that strategy. The Free plan allows 3 strategies, so one strategy leaves room to test a second angle later.

Before outreach can go out you also need to:

- verify your phone (launching outreach needs it);
- connect Gmail under **Settings**, on the **Integrations** tab;
- wait until the strategy shows `verified`, then use **1. Find leads** and **2. Write the email and launch** on the strategy page.

You can ignore **Team** and **Clients**. Your own workspace is created for you automatically, the **Workspace** switcher only appears once you belong to a second workspace, and Team approvals only apply to people you invite with the SDR role. As the owner, your own launches never wait for approval. See [Getting started](03-getting-started.md) for the full first session.

## Which screens do I check each day, and in what order?

Five stops, in this order. Each one either releases work or tells you something stopped.

1. **Today's automatic sending.** Vectorae sends nothing automatically until you approve the day. While automatic messages are waiting, a banner at the top of the app shows today's queued counts. Open the page, tick the channels, and press **Approve selected channels for today** (or **Decline for today**). See [Daily send approval and notifications](39-daily-send-approval-and-notifications.md).
2. **Inbox, Needs reply.** Replies from real people, oldest first. Answer them, then press **Mark handled**. A thread gets an **Overdue** badge once its reply has waited more than two days. See [Replies and the unified inbox](13-replies-and-unified-inbox.md).
3. **Campaigns, Review tab.** Messages held for a person to check (objections, stalled deals, executive titles, flagged copy). Filter on **Pending**, then approve, edit or reject each one. An item pending for more than 3 days shows **Going stale**. See [The send-review queue](12-send-review-queue.md).
4. **Settings, Deliverability.** On the **Mailbox health** card, look for a mailbox that is throttled or paused. A paused mailbox stays paused until you press **Resume**. See [Deliverability and mailbox health](19-deliverability-and-mailbox-health.md).
5. **Strategy and campaign status.** On **Strategies**, each card shows the strategy's status. `researching` and `verifying` mean it is still working; `needs_human_review`, `budget_exhausted` and `failed` mean it is waiting for you (see [The research pipeline](04-research-pipeline.md) for what to press). On **Campaigns**, the **Overview** tab shows a "Campaign paused" banner if Vectorae paused the campaign itself (for example for a high bounce rate). See [Pausing and stopping outreach](25-pause-stop-kill-switch.md).

## How long will the daily check take?

Vectorae does not measure this, and the time depends on your volume, so this guide gives no fixed number. What sets it is how many replies are waiting in **Needs reply**, how many messages are held under **Review**, and whether anything was paused overnight. With one strategy and nothing held, the morning check is mostly the daily approval.

A lot runs without you. Research and verification run on Vectorae's servers, so you can close the browser tab. Sending, follow-ups and reply checks run on a schedule from the server, so your computer does not need to be on.

If you skip a day, nothing is lost, it just waits. Automatic messages stay queued until you approve a day; they are not failed or deleted. Held messages stay under **Pending**. Replies stay under **Needs reply** until someone marks them handled. The cost of skipping is timing: a prospect who replied is waiting, and a held message that waits too long may no longer fit its timing (the **Going stale** badge).

## What should I look at once a week?

- **Pipeline health.** On the verified strategy's page, the **Pipeline health** card gives a score out of 100, a band and a short message, built from reply rate, sequence completion, lead freshness and open conversations. Read the four parts, not just the total. Lead freshness falls as your newest lead gets older, so a falling score often just means it is time to source more leads with **1. Find leads**. See [Analytics, health score and ROI](18-analytics-health-score-roi.md).
- **Analytics.** Choose your campaign to see reply quality (the raw reply rate next to the positive reply rate), "What earned the outcomes" and "How you compare". **Revenue & ROI** shows what the campaign brought in.
- **Email health.** On **Settings**, **Deliverability**, check the **Email health** card for missing SPF, DKIM or DMARC records or a blocklisting. **Run check now** checks immediately. Domain fixes happen at your DNS provider, not in Vectorae.
- **Cleared threads.** A quick look at the Inbox **Cleared** filter catches a thread you marked handled by mistake (**Put back** returns it).
- **Plan limits.** If launches are being refused, check [Plans, limits and billing](22-plans-limits-billing.md) for your plan's daily AI limit and strategy count.

## Related
- [Getting started with Vectorae](03-getting-started.md)
- [The research pipeline](04-research-pipeline.md)
- [The send-review queue and approvals](12-send-review-queue.md)
- [Replies and the unified inbox](13-replies-and-unified-inbox.md)
- [Analytics, health score and ROI](18-analytics-health-score-roi.md)
- [Deliverability and mailbox health](19-deliverability-and-mailbox-health.md)
- [Pausing and stopping outreach](25-pause-stop-kill-switch.md)
- [Daily send approval and notifications](39-daily-send-approval-and-notifications.md)
- [Product profile and ideal customer](33-product-profile-and-icp.md)
