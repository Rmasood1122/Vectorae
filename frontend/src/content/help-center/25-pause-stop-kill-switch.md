---
id: pause-stop-kill-switch
title: Pausing and stopping outreach
audience: customer
last_verified: 2026-10-11
owner: help-center
sources:
  - app/api/analytics.py
  - app/workers/outreach_tasks.py
  - app/services/sequence_engine.py
  - app/services/admin_service.py
  - app/api/admin.py
  - app/services/auth.py
  - app/api/unsubscribe.py
  - app/api/webhooks_whatsapp.py
  - app/services/whatsapp_optin.py
  - app/api/leads.py
  - app/api/ui_support.py
  - app/services/conversion_probability.py
  - app/services/system_settings.py
  - app/services/mailbox_health.py
  - app/api/trust.py
  - app/services/crm_sync.py
  - app/config.py
  - frontend/src/app/(app)/campaigns/page.tsx
  - frontend/src/components/settings/MailboxHealthCard.tsx
  - frontend/src/components/settings/LinkedInAccountsCard.tsx
  - frontend/src/components/leads/ConversionCard.tsx
  - frontend/src/app/(app)/settings/page.tsx
  - docs/features/send-review-queue.md
  - FEATURES.md
---

# Pausing and stopping outreach

Vectorae sends outreach automatically from a server, so closing the app does not stop it. This article explains every way to pause or stop sending: for one contact, one campaign, one mailbox or LinkedIn account, a whole account, and the whole deployment. It also explains the difference between a pause (can be undone) and a stop (cannot).

## What is the difference between pausing and stopping?

- A **pause** holds messages. Nothing is deleted, and sending continues from the same place when the pause is lifted.
- A **stop** ends a contact's sequence for good. A stopped sequence can never send again to that contact. To contact them again, they must be enrolled in a sequence again.

Every message is checked against these rules at the moment it is about to be sent, not only when it was scheduled. So a pause takes effect for messages already in the queue.

## How do I pause one campaign?

1. Open **Campaigns** and select the strategy.
2. On the **Overview** tab, press **Pause campaign**.

You see "Campaign paused". While a campaign is paused it sends nothing, including automatic follow-ups. To restart, press **Resume** in the "Campaign paused" banner on the same page.

Vectorae also pauses campaigns by itself in some cases, and they stay paused until a person presses **Resume**:

- **High bounce rate:** once a campaign has sent at least 10 messages, it pauses if more than 3% of them bounced. Both numbers are deployment defaults, and a workspace compliance rule can set a lower bounce limit. The banner says "Bounce rate auto-pause". Fix the cause before resuming.
- **Sending domain on a blocklist:** the campaign pauses and the banner shows the reason.

A strategy whose campaign is still sending cannot be deleted. Pause it first.

## How do I stop or pause one contact?

There is no single "stop this contact" button for a normal user today. These are the ways a contact's outreach stops or pauses:

- **They reply.** A reply stops or pauses their sequence automatically, depending on what kind of reply it is (an out-of-office reply pauses and retries later).
- **They unsubscribe.** The unsubscribe link in every email works without signing in and takes effect immediately. It adds them to the suppression list and stops every sequence for them.
- **They send STOP on WhatsApp.** Words such as "stop", "unsubscribe" and "opt out" are treated as an opt-out, effective immediately, and stop their sequences.
- **They book a meeting.** A booking stops the sequence (for Calendly) or pauses it (for Vectorae's own calendar, so a cancelled meeting can resume it).
- **You erase them.** **Delete (GDPR erasure)** on the lead in Pipeline adds them to the suppression list, so they are not contacted again, and erases their personal details. This cannot be undone.
- **The lead goes cold.** Vectorae estimates each lead's chance of converting before every send. After at least 4 unanswered messages, a lead below 5% is marked "cooling" and its sequence is paused for 21 days; below 1.5% it is archived and its sequence is stopped. These are deployment defaults. **Reactivate lead** on the lead's page resumes a cooling lead now, or clears an archived lead (its stopped sequence stays stopped).
- **Your CRM closes the lead.** If a connected HubSpot or Salesforce marks the lead as won, lost or disqualified, the sequence stops.
- **An administrator adds them to the suppression list.** In **Settings**, the **Suppression** tab is visible to administrators only. Entries cannot be removed from the Settings screen; only a platform administrator can remove one.
- **You remove the lead.** **Remove** (or moving the lead to `dropped`) stops its sequences straight away, and a removed lead is never sent to.

Other status changes by hand on the Pipeline board do not by themselves stop a sequence.

To hold individual messages for a person to check first, see the send-review queue.

## How do I pause one mailbox or LinkedIn account?

- **Gmail mailbox:** Vectorae scores each mailbox's sending health. Below 70 it slows sending down; below 40 it pauses the mailbox and sends nothing from it. Messages are deferred, not cancelled. A paused mailbox is resumed only by a person: **Settings** › **Deliverability**, then **Resume** on that mailbox. A later better score never resumes it on its own.
- **LinkedIn account:** in **Settings** › **Integrations**, each connected LinkedIn account has a **Pause** button (and **Resume** to undo it).

## How do I pause everything in my account?

There is no single "pause everything" switch for an account owner in this version. Pause each campaign with **Pause campaign**.

An administrator can suspend an account. Suspension pauses all of that account's active campaigns and blocks the account from the app. When the suspension is lifted, those campaigns become active again.

## Is there a kill switch for the whole platform?

Not as a single setting in the app in this version. What exists:

- Administrators can suspend individual accounts, as above.
- All sending is done by Vectorae's background worker on the server. If the deployment's operator stops that worker, nothing is sent until it runs again. Messages wait; they are not lost.
- Administrators can switch individual features off in system settings, for example the AI phone channel, which is off by default.

If you need all outreach stopped urgently, pause your campaigns and contact support.

## Related
- [Send review queue](12-send-review-queue.md)
- [Sequences and follow-ups](08-sequences-and-follow-ups.md)
- [Replies and the unified inbox](13-replies-and-unified-inbox.md)
- [Deliverability and mailbox health](19-deliverability-and-mailbox-health.md)
- [Compliance](20-compliance.md)
- [Privacy, data retention and deletion](21-privacy-data-retention-deletion.md)
- [Troubleshooting](27-troubleshooting.md)
