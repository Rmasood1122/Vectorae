---
id: crm-and-integrations
title: CRM and integrations
audience: customer
last_verified: 2026-10-11
owner: help-center
sources:
  - app/integrations/hubspot.py
  - app/integrations/salesforce.py
  - app/integrations/slack.py
  - app/workers/crm_tasks.py
  - app/workers/celery_app.py
  - app/services/webhook_delivery.py
  - app/services/api_keys.py
  - app/api/api_keys.py
  - frontend/src/components/settings/CrmCards.tsx
  - frontend/src/components/settings/SlackCard.tsx
  - frontend/src/components/settings/WebhooksCard.tsx
  - frontend/src/app/(app)/settings/page.tsx
  - frontend/src/app/(app)/crm/page.tsx
  - frontend/src/components/crm/grid/GridToolbar.tsx
  - frontend/src/components/shell/nav.ts
  - docs/features/crm-ecosystem.md
  - docs/FULL_PRODUCT_AUDIT.md
  - FEATURES.md
---

# CRM and integrations

Victorae has its own CRM, and it can also connect to HubSpot, Salesforce, Slack, and tools like Zapier or Make. This article explains what each connection does, how to set it up, and which ones have been checked against the live service. In short: none of the HubSpot, Salesforce or Slack connections has a recorded test against the live vendor yet.

## Does Victorae have its own CRM?

Yes. **CRM** in the main menu opens a CRM over the same leads your campaigns use, with dashboards and a spreadsheet-style table you edit directly. You can edit cells inline, bulk-edit up to 500 rows, add notes, tags, saved views and custom fields, and see each lead's activity and meetings. **Pipeline** shows the same leads as a kanban board.

You can download your leads with **Export CSV**. Export is one-way: there is no CSV import back into the table.

## Which integrations have been verified against the live service?

Be aware of this before you rely on any of them:

- **HubSpot:** several parts of the code (the sign-in token exchange, the contact search, and linking deals to contacts) are marked as not yet checked against HubSpot's current documentation. This integration has not yet been verified against the live service.
- **Salesforce:** there is no record of a test against a live Salesforce account. Treat it as not yet verified against the live service.
- **Slack:** there is no record of a test against a live Slack workspace. Treat it as not yet verified against the live service.
- **Outbound webhooks and API keys** are Victorae's own features and do not depend on a vendor.

All three vendor connections also need your Victorae administrator to register the app first (Admin › Integrations). Until they do, the card shows "An admin must add the … app under Admin › Integrations first." and **Connect** is disabled.

## How do I connect HubSpot or Salesforce?

Go to **Settings**, open the **Integrations** tab, and click **Connect** on the HubSpot or Salesforce card. You sign in on the vendor's site. Once connected, the card shows the account, how many leads and deals are linked, when data was last pushed and pulled, **Sync now** and **Disconnect** buttons, and the last error if a sync failed ("reconnect to restore the sync").

## What does the CRM sync send and receive?

**From Victorae to your CRM:**

- Leads that engaged (replied, meeting booked, opportunity, closed, or disqualified) become HubSpot **contacts** or Salesforce **leads**, matched by email.
- Tick "Also push every verified lead (by default only leads that replied or further)" to send verified and contacted leads as well (see [HubSpot and Salesforce sync in detail](36-crm-sync-field-mapping.md)).
- Deals become HubSpot **deals** (linked to the contact) or Salesforce **opportunities**.

Changes are pushed straight away on key events (meeting booked, reply, call, deal won, a kanban move, a deal edit) and also in a sweep every 30 minutes.

**From your CRM to Victorae**, only for records Victorae linked, and only forward:

- HubSpot lead status "Unqualified" makes the lead disqualified; an open deal or the "opportunity" lifecycle stage makes it an opportunity; the "customer" lifecycle stage makes it closed won.
- A deal or opportunity closed won or lost marks the deal and the lead won or lost. Deal amount and close date are copied.
- A converted Salesforce lead becomes an opportunity; "Closed - Not Converted" becomes disqualified.
- A HubSpot deal created on a linked contact is imported as a Victorae deal.

A CRM change never moves a lead backwards and never reopens a closed lead. Closing a lead in this way stops its sequences. The 30-minute sweep reads linked records back without any setup in your CRM; your CRM admin can also add webhooks to make changes arrive immediately.

## How do I get Slack notifications?

On the **Slack** card under **Settings**, **Integrations**, click **Connect**, approve the app in Slack, then choose a **Channel**. You can send a test message. Victorae posts meeting bookings, interested replies, campaigns paused automatically for a high bounce rate (manual pauses are not posted), and strategy changes on idle campaigns. It also posts meeting prep briefs, meeting reminders, objection spikes and deals won.

Public channels work straight away. For a private channel, invite the bot to it before choosing it. If Slack is unavailable, the notification is skipped; it never undoes the booking or event that caused it.

## How do I connect Zapier, Make or my own system with webhooks?

On the **Webhooks (Zapier, Make)** card, click **Add webhook**, enter an **HTTPS URL**, choose the **Events**, and click **Add**. The signing secret is shown once: "Copy it now — it will not be shown again."

Available events include meeting booked, reply received, lead sourced and campaign paused, plus interested reply, deal won, call completed, strategy changed and objection spike, and these alerts: domain blacklisted, email health warning, mailbox paused, message held for review, re-engagement due and reply draft approved. Every delivery carries an `X-LeadPilot-Signature` header so your system can confirm it came from Victorae.

Delivery rules:

- Any 2xx response counts as delivered.
- A failed delivery is tried 6 times in total: once, then again after 30 seconds, 2 minutes, 10 minutes, 30 minutes and 2 hours.
- A **410 Gone** response turns the webhook off (the Zapier convention for a turned-off Zap).
- The URL must be public HTTPS. Private or internal addresses are refused, and redirects are not followed.

The card's **Recent deliveries** list shows each event, its status, attempts, last response and time.

## How do API keys work?

On the **API keys** card, enter a **Name** and click **Create key**. The key starts with `lpk_` and is shown only once; Victorae stores only a scrambled (hashed) form. Tools like Zapier and Make use it as `Authorization: Bearer lpk_…`.

You can have up to 10 active keys; revoke one to make another. **Revoke** stops a key from working. A key acts as you for the normal API, but it can never reach admin pages, even if it belongs to an admin.

## Related
- [Booking meetings and your calendar](14-booking-and-calendar.md)
- [Meetings, prep briefs, the meeting room and transcripts](15-meetings-prep-room-transcripts.md)
- [Analytics, health score and ROI](18-analytics-health-score-roi.md)
- [Account security](23-account-security.md)
- [Workspaces and agency mode](24-workspaces-and-agency-mode.md)
- [Known limitations](29-known-limitations.md)
