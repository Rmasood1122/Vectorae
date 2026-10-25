---
id: email-sending-gmail
title: Sending email through Gmail
audience: customer
last_verified: 2026-10-10
owner: help-center
sources:
  - frontend/src/content/product-facts.json
  - docs/program/ai-visibility/REPORT.md
  - app/api/integrations.py
  - app/integrations/gmail.py
  - app/integrations/google_meet.py
  - app/services/sequence_engine.py
  - app/workers/outreach_tasks.py
  - app/workers/celery_app.py
  - app/services/reply_classification.py
  - app/services/compliance_rules.py
  - app/services/compliance_audit.py
  - app/services/compliance_region.py
  - app/services/open_tracking.py
  - app/services/mailbox_health.py
  - app/services/send_review.py
  - app/services/system_settings.py
  - app/services/client_workspaces.py
  - app/core/production_guard.py
  - app/config.py
  - docs/features/compliance-rules.md
  - docs/features/mailbox-health.md
  - docs/VERIFICATION_REPORT.md
  - docs/program/BASELINE.md
  - frontend/src/app/(app)/settings/page.tsx
  - frontend/src/app/(app)/campaigns/page.tsx
  - frontend/src/lib/meeting-prep.ts
---

# Sending email through Gmail

Victorae sends outreach email from the Gmail account you connect, automatically, inside a send window and a daily limit that grows slowly while the account warms up. This article covers connecting and reconnecting Gmail, the daily caps, send windows, what is added to every email, and what happens when emails bounce.

## How do I connect Gmail?

1. Go to **Settings** and open the **Integrations** tab.
2. On the **Gmail** card, press **Connect**.
3. Google asks you to approve access. Victorae requests permission to send email, read email (to detect replies and bounces), create calendar events (used to create Google Meet links), and create drafts (used for post-meeting follow-up drafts).
4. After you approve, Google sends you back to Victorae, which records the connection and returns you to **Settings → Integrations** with the message "Gmail connected". The Gmail card says "Connected:" followed by your address. If the connection failed, you see "Gmail connection failed" with the reason instead.

Victorae keeps one Gmail connection per user account. Connecting again updates that same connection. Your Google access tokens are stored encrypted and refreshed automatically.

If you see "Gmail is not configured on the server", your Victorae deployment has not been set up with Google sign-in credentials yet. Contact your administrator.

## When do I need to reconnect?

Press **Reconnect** on the Gmail card when:

- you removed Victorae's access in your Google account;
- the card says "Calendar access missing — reconnect to create Google Meet links. Your Gmail access is kept." The button then reads **Reconnect for Calendar**. Accounts connected before calendar access was added need this once;
- Victorae asks you to "Reconnect Gmail in Settings to allow saving drafts" after a meeting.

Use **Test** on the Gmail card to check whether the connection is healthy. If no Gmail account is connected, email steps are not sent.

## How many emails will Victorae send per day?

Each connected Gmail account follows a warm-up ramp, counted per UTC day:

- **10 emails** on the day you connect it;
- **10 more each day** after that;
- up to a ceiling of **100 emails per day**.

These are the deployment's default settings. Reconnecting the same account does not restart the ramp. Two things can only lower the cap, never raise it:

- your workspace's **compliance rules**, set by an administrator;
- **mailbox health**: a Gmail account with poor deliverability health can be throttled to a lower cap, or paused.

When the day's allowance is used up, or the mailbox is paused, the remaining emails move to the next day's send window. They are deferred, never dropped. On **Campaigns**, the **Overview** tab's **Gmail** card shows "Today / cap". The cap shown includes a lower cap set by your workspace's compliance rules for all regions and a lower cap from mailbox health; a rule for one region can lower it further for the leads in that region.

## When are emails sent?

Only inside the send window, in each **lead's own time zone**:

- 09:00 to 17:00 by default;
- weekends skipped by default;
- leads with no known time zone use the deployment's fallback zone, UTC by default.

An administrator can change the window for a workspace, a recipient region or a channel, but only within 07:00 to 20:00 local time. Victorae checks for due messages every 30 minutes by default and re-checks every rule at the moment of sending.

## What does Victorae add to every email?

- **A footer** with your sender identity (name and postal address) and the line "Don't want these emails? Unsubscribe instantly:" followed by a personal unsubscribe link. The sender identity is a deployment setting; a production deployment refuses to start while it is missing or still the placeholder address.
- **One-click unsubscribe headers** (`List-Unsubscribe` and `List-Unsubscribe-Post`), which let email apps show their own unsubscribe button.
- **A regional notice** for some regions: for leads in the EU or UK, a line about legitimate interest and the right to object; for leads in Canada, a line naming the sender and how to unsubscribe.
- **An open-tracking pixel**, in an HTML copy of the email, unless open tracking is turned off or the lead is in the EU, EEA or UK. **Click tracking** is off by default.

Clicking the unsubscribe link, or replying with a request to stop, suppresses that person immediately and stops all of their sequences.

## What happens when an email bounces?

Victorae checks each connected inbox for replies every 30 minutes by default. A delivery-failure notice that it can match to a sent message is treated as a bounce: the lead is dropped and all of their sequences stop.

After each bounce, Victorae checks the campaign's bounce rate (bounces divided by messages sent across the strategy). Once at least 10 messages have been sent, a bounce rate above **3%** pauses the whole campaign automatically. Your workspace's compliance rules may set a lower threshold, never a higher one. You get a notification, and nothing more is sent until a person resumes the campaign.

To resume, go to **Campaigns**, open the **Overview** tab, and press **Resume** in the "Campaign paused" notice, which asks you to fix deliverability issues before resuming. The notice shows the campaign's bounce rate and the threshold that applies to it, for example "Bounce rate auto-pause: 2.5% is above the 2% limit."

## What if a send fails?

- If Google rejects the message outright, it is marked failed and is not retried automatically.
- Other errors are retried later.
- If a send was interrupted at the moment of sending, the message is flagged and never resent automatically, because it may already have gone out.
- If you use client workspaces, an email is blocked when your Gmail address's domain is not in that client's sending-domain list.

## Why is a message waiting instead of sending?

Besides the window and the cap, some messages are held for a person to approve. By default Victorae holds a message when the lead's last reply was an objection, when an open deal with the lead is past its close date, when the lead's job title contains founder or co-founder, owner, a C-level title (CEO, COO, CFO, CTO, CMO, CRO or "Chief …"), president, managing director, partner, VP or vice president, "head of", board member or chair, or when the written copy trips the tone and spam rules. Approved messages send as approved. If most of your prospects are owners or founders, expect many of their messages to wait for approval; an administrator can turn the job-title rule off. See the send-review queue article.

## Has the Gmail integration been verified against Google's live service?

The founders confirmed email sending through Gmail as working end to end in production on 2026-10-13. The automated tests still run against simulated responses, and some request details are still marked in the code as needing confirmation against Google's current documentation.

## Related
- [Sequences and follow-ups](08-sequences-and-follow-ups.md)
- [The send-review queue](12-send-review-queue.md)
- [Replies and the unified inbox](13-replies-and-unified-inbox.md)
- [Deliverability and mailbox health](19-deliverability-and-mailbox-health.md)
- [Compliance](20-compliance.md)
- [Pausing, stopping and the kill switch](25-pause-stop-kill-switch.md)
- [Troubleshooting](27-troubleshooting.md)
