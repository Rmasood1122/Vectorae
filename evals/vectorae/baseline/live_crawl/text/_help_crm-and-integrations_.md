Server temporarily unreachableWe can't reach Vectorae's server right now, so we can't confirm whether scheduled sends are going out. We'll reconnect automatically. [Check service status](https://www.calendarharvest.com/status/)

[Vectorae](https://www.calendarharvest.com/)

- [How it Works](https://www.calendarharvest.com/how-it-works/)
- [Compare](https://www.calendarharvest.com/compare/)
- [Help](https://www.calendarharvest.com/help/)
- [About](https://www.calendarharvest.com/about/)
- [Pricing](https://www.calendarharvest.com/pricing/)
- [Contact](https://www.calendarharvest.com/contact/)

Help center

# CRM and integrations

Vectorae has its own CRM, and it can also connect to HubSpot, Salesforce, Slack, and tools like Zapier or Make. This article explains what each connection does, how to set it up, and which ones have been checked against the live service. In short: none of the HubSpot, Salesforce or Slack connections has a recorded test against the live vendor yet.

Not live yet: LinkedIn steps; Stripe checkout (paying for a plan online). Where this article describes them, they are built and tested only against simulated services. Free trials and online plan changes described here start through online checkout, which is not live yet; to start, [book a call](https://www.calendarharvest.com/contact/). Features marked off by default must be switched on by an administrator. See [known limitations](https://www.calendarharvest.com/help/known-limitations/).Last checked: 2026-10-28.

## Does Vectorae have its own CRM?

Yes. **CRM** in the main menu opens a CRM over the same leads your campaigns use, with dashboards and a spreadsheet-style table you edit directly. You can edit cells inline, bulk-edit up to 500 rows, add notes, tags, saved views and custom fields, and see each lead's activity and meetings. **Pipeline** shows the same leads as a kanban board.

You can download your leads with **Export CSV**. Export is one-way: there is no CSV import back into the table.

## Which integrations have been verified against the live service?

Be aware of this before you rely on any of them:

- **HubSpot:** the sign-in, contact search, linking deals to contacts and webhook signature were checked against HubSpot's public documentation on 2026-10-28, without a HubSpot account. Sign-in uses HubSpot's dated 2026-03 OAuth endpoints, which replace the deprecated v1 ones. This integration has not yet been verified against the live service.
- **Salesforce:** there is no record of a test against a live Salesforce account. Treat it as not yet verified against the live service.
- **Slack:** there is no record of a test against a live Slack workspace. Treat it as not yet verified against the live service.
- **Outbound webhooks and API keys** are Vectorae's own features and do not depend on a vendor.

All three vendor connections also need your Vectorae administrator to register the app first (Admin › Integrations). Until they do, the card shows "An admin must add the … app under Admin › Integrations first." and **Connect** is disabled.

## How do I connect HubSpot or Salesforce?

Go to **Settings**, open the **Integrations** tab, and click **Connect** on the HubSpot or Salesforce card. You sign in on the vendor's site. Once connected, the card shows the account, how many leads and deals are linked, when data was last pushed and pulled, **Sync now** and **Disconnect** buttons, and the last error if a sync failed ("reconnect to restore the sync").

Vectorae now also asks HubSpot for permission to read **companies**, which the do-not-contact check ( **Sync from CRM** in **Guardrails Studio**) uses to find your customers and open deals. A HubSpot connection made before this change does not have that permission: the check then says "HubSpot did not allow reading companies", and customers and open deals from HubSpot are not excluded until you disconnect and connect HubSpot again. Disconnecting removes the links between your Vectorae leads and HubSpot records and any custom lifecycle-stage settings for this check; connecting again starts a full sync.

## What does the CRM sync send and receive?

**From Vectorae to your CRM:**

- Leads that engaged (replied, meeting booked, opportunity, closed, or disqualified) become HubSpot **contacts** or Salesforce **leads**, matched by email.
- Tick "Also push every verified lead (by default only leads that replied or further)" to send verified and contacted leads as well (see [HubSpot and Salesforce sync in detail](https://www.calendarharvest.com/help/crm-sync-field-mapping/)).
- Deals become HubSpot **deals** (linked to the contact) or Salesforce **opportunities**.

Changes are pushed straight away on key events (meeting booked, reply, call, deal won, a kanban move, a deal edit) and also in a sweep every 30 minutes.

**From your CRM to Vectorae**, only for records Vectorae linked, and only forward:

- HubSpot lead status "Unqualified" makes the lead disqualified; an open deal or the "opportunity" lifecycle stage makes it an opportunity; the "customer" lifecycle stage makes it closed won.
- A deal or opportunity closed won or lost marks the deal and the lead won or lost. Deal amount and close date are copied.
- A converted Salesforce lead becomes an opportunity; "Closed - Not Converted" becomes disqualified.
- A HubSpot deal created on a linked contact is imported as a Vectorae deal.

A CRM change never moves a lead backwards and never reopens a closed lead. Closing a lead in this way stops its sequences. The 30-minute sweep reads linked records back without any setup in your CRM; your CRM admin can also add webhooks to make changes arrive immediately.

## How do I get Slack notifications?

On the **Slack** card under **Settings**, **Integrations**, click **Connect**, approve the app in Slack, then choose a **Channel**. You can send a test message. Vectorae posts meeting bookings, interested replies, campaigns paused automatically for a high bounce rate (manual pauses are not posted), and strategy changes on idle campaigns. It also posts meeting prep briefs, meeting reminders, objection spikes and deals won.

Public channels work straight away. For a private channel, invite the bot to it before choosing it. If Slack is unavailable, the notification is skipped; it never undoes the booking or event that caused it.

## How do I connect Zapier, Make or my own system with webhooks?

On the **Webhooks (Zapier, Make)** card, click **Add webhook**, enter an **HTTPS URL**, choose the **Events**, and click **Add**. The signing secret is shown once: "Copy it now — it will not be shown again."

Available events include meeting booked, reply received, lead sourced and campaign paused, plus interested reply, deal won, call completed, strategy changed and objection spike, and these alerts: domain blacklisted, email health warning, mailbox paused, message held for review, re-engagement due and reply draft approved. Every delivery carries an `X-LeadPilot-Signature` header so your system can confirm it came from Vectorae.

Delivery rules:

- Any 2xx response counts as delivered.
- A failed delivery is tried 6 times in total: once, then again after 30 seconds, 2 minutes, 10 minutes, 30 minutes and 2 hours.
- A **410 Gone** response turns the webhook off (the Zapier convention for a turned-off Zap).
- The URL must be public HTTPS. Private or internal addresses are refused, and redirects are not followed.

The card's **Recent deliveries** list shows each event, its status, attempts, last response and time.

## How do API keys work?

On the **API keys** card, enter a **Name** and click **Create key**. The key starts with `lpk_` and is shown only once; Vectorae stores only a scrambled (hashed) form. Tools like Zapier and Make use it as `Authorization: Bearer lpk_…`.

You can have up to 10 active keys; revoke one to make another. **Revoke** stops a key from working. A key acts as you for the normal API, but it can never reach admin pages, even if it belongs to an admin.

## Related

- [Booking meetings and your calendar](https://www.calendarharvest.com/help/booking-and-calendar/)
- [Meetings, prep briefs, the meeting room and transcripts](https://www.calendarharvest.com/help/meetings-prep-room-transcripts/)
- [Analytics, health score and ROI](https://www.calendarharvest.com/help/analytics-health-score-roi/)
- [Account security](https://www.calendarharvest.com/help/account-security/)
- [Workspaces and agency mode](https://www.calendarharvest.com/help/workspaces-and-agency-mode/)
- [Known limitations](https://www.calendarharvest.com/help/known-limitations/)

## Related

- [All help articles](https://www.calendarharvest.com/help/)
- [Vectorae FAQ](https://www.calendarharvest.com/faq/)

[Book a call](https://www.calendarharvest.com/contact/) [See how it works](https://www.calendarharvest.com/how-it-works/)

## Company

- [About](https://www.calendarharvest.com/about/)
- [Contact](https://www.calendarharvest.com/contact/)
- [Pricing](https://www.calendarharvest.com/pricing/)
- [Status](https://www.calendarharvest.com/status/)

## Who it's for

- [Agencies](https://www.calendarharvest.com/for/agencies/)
- [B2B service firms](https://www.calendarharvest.com/for/b2b-service-firms/)
- [Local businesses](https://www.calendarharvest.com/for/local-businesses/)

## Compare

- [vs Apollo](https://www.calendarharvest.com/compare/leadpilot-vs-apollo/)
- [vs Instantly](https://www.calendarharvest.com/compare/leadpilot-vs-instantly/)
- [vs Smartlead](https://www.calendarharvest.com/compare/leadpilot-vs-smartlead/)
- [vs Clay](https://www.calendarharvest.com/compare/leadpilot-vs-clay/)
- [vs Artisan](https://www.calendarharvest.com/compare/leadpilot-vs-artisan/)
- [All comparisons](https://www.calendarharvest.com/compare/)

## Resources

- [What is Vectorae](https://www.calendarharvest.com/what-is-leadpilot/)
- [FAQ](https://www.calendarharvest.com/faq/)
- [Help center](https://www.calendarharvest.com/help/)
- [Outbound statistics](https://www.calendarharvest.com/outbound-statistics/)

## Legal

- [Privacy Policy](https://www.calendarharvest.com/privacy/)
- [Terms of Service](https://www.calendarharvest.com/terms/)

© 2026 Rehan Masood. Vectorae is operated by Rehan Masood.