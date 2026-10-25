---
id: troubleshooting
title: Troubleshooting
audience: customer
last_verified: 2026-10-11
owner: help-center
sources:
  - render.yaml
  - app/api/strategies.py
  - app/workers/tasks.py
  - app/db/models.py
  - app/config.py
  - app/core/config.py
  - app/core/rate_limiting.py
  - app/core/plans.py
  - app/services/usage_meter.py
  - app/api/leads.py
  - app/api/billing.py
  - app/api/integrations.py
  - app/api/support.py
  - app/services/network_guard.py
  - app/services/auth.py
  - app/services/identity.py
  - app/services/rbac.py
  - app/services/sequence_engine.py
  - app/workers/outreach_tasks.py
  - app/services/mailbox_health.py
  - app/api/health.py
  - frontend/src/components/strategy/ResumeStrategyButton.tsx
  - frontend/src/components/strategy/ReverifyStrategyButton.tsx
  - frontend/src/app/(app)/settings/page.tsx
  - frontend/src/app/(app)/campaigns/page.tsx
  - frontend/src/lib/api/client.ts
  - frontend/src/components/support/ChatWidget.tsx
  - frontend/src/app/(app)/profile/page.tsx
  - frontend/src/lib/profile.ts
  - app/workers/celery_app.py
  - docs/program/BASELINE.md
  - app/services/daily_send_approval.py
  - app/services/send_diagnostics.py
  - frontend/src/components/strategy/SendDiagnosticsPanel.tsx
---

# Troubleshooting

This article covers the problems customers run into most: a strategy that seems stuck, a strategy that failed verification, Gmail that needs reconnecting, error codes such as 402, 429 and 503, outreach that is not sending, and bounces. Each answer says what the cause usually is and what you can do.

## My strategy seems stuck. What do I do?

A strategy moves through these statuses: pending, researching, verifying, then verified (or needs human review). Research for one strategy is long: it runs 72 or 144 steps once per strategy, so "researching" for a while is normal.

If progress has truly stopped:

1. Open the strategy. If its status is pending, researching, verifying, failed or budget exhausted, a **Resume** button is shown.
2. Press **Resume**, then **Confirm resume**. The run continues from the last finished step; finished steps are not repeated and not paid for again.
3. For a strategy that still looks active (pending, researching or verifying), the app warns: "Only resume if progress has stopped. Resuming a run that is still working can duplicate work."

Other things to know:

- **Budget exhausted** means the next AI step could have gone over the strategy's budget or your plan's daily AI limit. You can enter a larger **New budget $** before confirming. A strategy budget can be between $0.50 and $50.00 (the default is $15); the upper limit is a deployment setting.
- A single research or verification run has a time limit of 2 hours (a deployment setting). A run that hits it, or hits a temporary error, is retried up to 3 times and resumes where it stopped.
- If an error can never succeed on retry (for example the AI service key is invalid), or retries are used up, the strategy becomes **failed** with the error saved on it.
- A strategy whose campaign is already running cannot be resumed: "pause the campaign before resuming the pipeline".
- If nothing moves at all, even after resuming, the background worker may not be running. See "Nothing is sending" below.

## My strategy failed verification. Now what?

If one of the 10 verification checks keeps failing after its retries, the strategy stops at **needs human review**. Leads cannot be sourced until it passes. You can press **Re-verify** to run the checks again, or a person can pass specific failed checks by hand, with a written reason that is recorded. Details are in the strategy verification article.

## How do I reconnect Gmail?

Open **Settings** › **Integrations** and find the **Gmail** card.

- "Not connected" – press **Connect** and sign in with Google.
- Connected but not working – press **Test**. If the result is unhealthy, press **Reconnect**.
- "Calendar access missing — reconnect to create Google Meet links. Your Gmail access is kept." – press **Reconnect for Calendar**.
- "Gmail is not configured on the server: …" – the deployment has not set up Google sign-in. Contact your administrator.

## What do error codes 402, 429 and 503 mean?

**402 – plan limit.** Your plan does not allow the action. The message names the limit, for example "Free plan allows 3 strategies. You have 3." It is used for: number of strategies, leads per strategy, sequence steps, channels, and features such as the playbook, A/B results or the LinkedIn network. Upgrade, or stay within the limit.

**429 – too many, or daily AI limit used up.**

- "Your … plan's daily AI limit ($…) is used up. It resets at 00:00 UTC." – wait for the reset, or upgrade. Resume, Re-verify, lead sourcing and launch all show this message.
- "This run is forecast to cost $…, but only $… of your $… monthly spend cap is left. …" – your monthly spend cap would be exceeded. Raise it on the Usage page or wait for the new month (UTC).
- A plain "429 Too Many Requests" is a rate limit. Common defaults: 2 new strategies per hour, 5 lead-sourcing requests per hour, 10 sign-in attempts per 15 minutes, 20 support chat messages per day (Victorae's hosted service raises this to 30). Wait and try again. These are deployment settings.
- "Too many attempts. Wait a while and try again." – too many password confirmations (for example when exporting data).

**503 – temporarily unavailable.** Something on the server side is switched off or not reachable:

- "payments are not configured; plans cannot be changed right now" – payments are not set up on this deployment.
- "Support chat is temporarily unavailable. Submit a ticket instead." – the chat assistant is switched off; tickets still work.
- "We couldn't verify your network just now. Please try again in a minute." – the VPN check service did not answer.

## Why am I blocked with a 403?

- **Email not verified** – confirm your email address using the link sent to you; you are sent to the check-email screen.
- **Phone not verified** – some actions, such as choosing a plan, need a verified phone. You are sent to the verification screen.
- **VPN, proxy or hosting network** – turn off your VPN and sign in from your normal network.
- **"account suspended"** – an administrator suspended the account. Contact support.
- **Workspace role** – for example "Viewers have read-only access to this workspace." or "Deleting needs a manager in this workspace."

## Why can't I source leads?

Leads can only be sourced for a strategy that passed all 10 verification checks. Otherwise you see "leads can only be sourced for a strategy that passed all 10 verification passes". If the strategy is full for your plan, you get a 402 instead.

## Nothing is sending. Why?

Open the strategy and look at the **Why isn't this sending?** panel. It lists, for each channel, everything that is stopping messages right now, in plain words, with what to do and a link to fix it. It uses the same checks as sending itself. You can also check these in order:

1. **Is the campaign paused?** On **Campaigns**, a "Campaign paused" banner shows the reason. Press **Resume** when ready.
2. **Is it inside the send window?** By default, messages go out between 09:00 and 17:00 in the lead's time zone (UTC if unknown), on weekdays only.
3. **Has today's limit been reached?** By default a Gmail mailbox sends at most 100 messages a day. A newly connected mailbox starts at 10 a day and adds 10 each day until it reaches that limit.
4. **Is the mailbox paused or slowed?** Look at **Settings** › **Deliverability**. A paused mailbox needs **Resume**.
5. **Is a message waiting for review?** Held messages wait in the send-review queue until approved.
   **Has today's automatic sending been approved?** Automatic messages wait until the account owner or a manager approves today's sending for that channel (the banner at the top of each page links to the approval page). Approving only email leaves LinkedIn waiting.
6. **Is the channel in your plan, and is WhatsApp allowed?** WhatsApp needs a recorded opt-in and an approved template or an open 24-hour window.
7. **Is the background worker running?** All sending is done by a background worker on the server, which checks for due messages every 30 minutes by default. If it is not running, nothing is sent and strategies do not progress, but nothing is lost. On a deployment you run yourself, the server's `/health` address reports "degraded" when the worker has not checked in for more than 2 minutes. Otherwise, contact support.

## My emails are bouncing. What happens?

When an email bounces, that lead's sequence stops. Once a campaign has sent at least 10 messages, it pauses automatically if more than 3% of them bounced, and stays paused until a person resumes it. Before resuming, check your domain's email setup and the quality of your lead list. See the deliverability article for details.

## Related
- [Research pipeline](04-research-pipeline.md)
- [Strategy verification](05-strategy-verification.md)
- [Email sending with Gmail](09-email-sending-gmail.md)
- [Deliverability and mailbox health](19-deliverability-and-mailbox-health.md)
- [Plans, limits and billing](22-plans-limits-billing.md)
- [Pausing and stopping outreach](25-pause-stop-kill-switch.md)
- [Contact support and status](30-contact-support-and-status.md)
