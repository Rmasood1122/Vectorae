---
id: crm-sync-field-mapping
title: HubSpot and Salesforce sync in detail
audience: customer
last_verified: 2026-10-18
owner: help-center
sources:
  - app/services/crm_sync.py
  - app/integrations/hubspot.py
  - app/integrations/salesforce.py
  - app/api/crm_integrations.py
  - app/workers/crm_tasks.py
  - app/workers/celery_app.py
  - app/services/guardrails.py
  - frontend/src/components/settings/CrmCards.tsx
  - docs/features/crm-ecosystem.md
  - docs/FULL_PRODUCT_AUDIT.md
  - docs/help-center/16-crm-and-integrations.md
---

# HubSpot and Salesforce sync in detail

This article lists exactly which fields Victorae writes to HubSpot and Salesforce, which changes it reads back, how often, and what happens when a record changes on both sides. To connect a CRM in the first place, see [CRM and integrations](16-crm-and-integrations.md).

## Which Victorae fields are written to HubSpot?

Each synced lead becomes a HubSpot **contact**, found by email or created if none exists:

| Victorae | HubSpot property |
|---|---|
| Email | `email` |
| Full name (last word) | `lastname` |
| Full name (everything before the last word) | `firstname` |
| Company | `company` |
| Title | `jobtitle` |
| Phone | `phone` |
| Lead status | `hs_lead_status` (table below) |
| Lead status, only when opportunity or closed won | `lifecyclestage` |

Lead status is sent as: verified → `NEW`; contacted → `ATTEMPTED_TO_CONTACT`; replied → `CONNECTED`; meeting booked → `IN_PROGRESS`; opportunity and closed won → `OPEN_DEAL`; closed lost → `BAD_TIMING`; disqualified → `UNQUALIFIED`. The lifecycle stage is `opportunity` for an opportunity and `customer` for closed won. HubSpot does not let a lifecycle stage move backwards; if it refuses one, Victorae sends the rest of the update without it.

Each Victorae deal becomes a HubSpot **deal** with `dealname`, `amount` (two decimals), `dealstage` (open → `appointmentscheduled`, won → `closedwon`, lost → `closedlost`), `pipeline` (`default` unless changed), `deal_currency_code` and `closedate` when the deal has one. A new deal is linked to the lead's contact.

Empty Victorae fields are left out, so a blank field never clears a value in HubSpot.

## Which fields does Victorae write to Salesforce?

Victorae leads become Salesforce **Leads**, not Contacts; converting them stays your Salesforce users' decision. Victorae looks for an existing Lead with the same email (it does not search Contacts), otherwise it creates one.

| Victorae | Salesforce Lead field |
|---|---|
| Email | `Email` |
| Full name (everything before the last word) | `FirstName` |
| Full name (last word) | `LastName` (`Unknown` if there is no name) |
| Company | `Company` (`Unknown` if empty) |
| Title | `Title` |
| Phone | `Phone` |
| Lead status | `Status` |
| (new Leads only) | `LeadSource` = `Vectorae` |

`Status` is sent as: verified → `Open - Not Contacted`; contacted, replied, meeting booked and opportunity → `Working - Contacted`; closed won → `Closed - Converted`; closed lost and disqualified → `Closed - Not Converted`. Once a Lead is converted in Salesforce it is read-only, and Victorae stops updating it.

Each Victorae deal becomes an **Opportunity** with `Name` (up to 120 characters), `StageName` (open → `Prospecting`, won → `Closed Won`, lost → `Closed Lost`), `Amount`, `CloseDate` and a `Description` with Victorae's internal deal ID and the lead's email. Salesforce requires a close date, so an open deal without one gets a date 30 days out for your team to adjust. The Opportunity is not linked to the Lead record.

## Which leads are sent, and how often?

The 30-minute sweep sends leads that replied or went further: replied, meeting booked, opportunity, closed won, closed lost and disqualified. The checkbox "Also push every verified lead (by default only leads that replied or further)" on the CRM card adds verified and contacted leads. The proposal-sent stage has no status mapping and is not in either list. A lead without an email is never sent. Every deal is sent.

There are two triggers:

- **Straight away**, when a meeting is booked, a reply arrives, a reply is interested, a call completes, a deal is won, you move a lead on the **Pipeline** board, or you create or edit a deal. These pushes send the lead whatever its status, even one the sweep would skip. Edits you make in the CRM table, inline or in bulk, are not pushed straight away; the sweep picks them up.
- **Every 30 minutes**, a sweep sends what changed since the previous sweep, up to 500 leads and 500 deals per run.

**Sync now** on the CRM card sends every eligible record, not only changed ones, up to the same 500 leads and 500 deals, then reads linked records back. It is rate limited: by default 60 times per hour for each CRM.

Known limitation: if more than 500 leads or deals changed between sweeps, the ones past the cap are not sent by later sweeps until they change again.

The same person sourced into two campaigns is one CRM record: the first lead keeps the link and later pushes update that record.

## Do changes made in HubSpot or Salesforce come back into Victorae?

Some do, and only for records Victorae created or linked. Records that exist only in your CRM are not imported. The one exception: a HubSpot deal created on a contact Victorae linked is imported as a Victorae deal. Salesforce Opportunities created in Salesforce are not imported.

What Victorae reads back:

- **HubSpot contact:** `hs_lead_status` `UNQUALIFIED` → disqualified, `OPEN_DEAL` → opportunity; `lifecyclestage` `opportunity` → opportunity, `customer` → closed won.
- **HubSpot deal:** `dealstage` `closedwon` or `closedlost` → deal won or lost (and the lead closed won or lost); `amount` and `closedate` are copied.
- **Salesforce Lead:** converted → opportunity; `Status` `Closed - Converted` → opportunity, `Closed - Not Converted` → disqualified.
- **Salesforce Opportunity:** won → deal won, closed but not won → deal lost (and the lead with it); `Amount` and `CloseDate` are copied.

Name, email, company, title and phone are never read back.

Changes arrive by **polling** without any setup: the same 30-minute sweep reads every linked record back. **Webhooks** make it immediate. HubSpot sends its own signed webhooks once your admin subscribes the app to them. Salesforce has no built-in equivalent, so your Salesforce admin must build a Flow or Apex callout that posts changes to Victorae with a signature. The exact setup is in `docs/features/crm-ecosystem.md`.

Separately, if your administrator has turned on guardrails, a daily job reads customer and open-deal companies from your CRM into your exclusion lists, which are checked before a message is sent.

## Who wins when a record changes on both sides?

Victorae uses fixed rules, not timestamps:

- **Contact details** (name, email, company, title, phone) belong to Victorae. Each push overwrites the CRM value with Victorae's, so an edit made only in the CRM is replaced the next time that lead is pushed. CRM edits to these fields never reach Victorae.
- **Lead status** only moves forward. A CRM change that would move a lead backwards is ignored, and a closed lead (won, lost or disqualified) is never reopened. Victorae keeps sending its own status on later pushes. This is also what stops the two systems overwriting each other in a loop.
- **Deals:** during the 30-minute sweep Victorae pushes its changes before it reads the CRM back, so a deal edited in both places since the last sweep ends up with Victorae's values. A change that arrives by CRM webhook is applied when it arrives. A deal reopened in the CRM after being won or lost is not reopened in Victorae by default.
- **Deleted in the CRM:** Victorae creates the record again on its next push.

When the CRM closes a lead, its sequences stop.

## Can I change the status mapping for custom picklists?

Yes, but only through the API; the settings screen has no editor for it. `PUT /integrations/hubspot/settings` or `PUT /integrations/salesforce/settings` accepts:

- `lead_status_map`: Victorae status → your CRM value, up to 20 entries. Keys must be Victorae statuses that sync (verified, contacted, replied, meeting_booked, opportunity, closed_won, closed_lost, disqualified).
- `deal_stage_map`: `open`, `won` or `lost` → your deal stage, up to 5 entries.
- `pipeline` (HubSpot): the pipeline new deals go into, up to 100 characters.

Your values replace the defaults for the keys you give and keep the defaults for the rest. They change what Victorae sends. For incoming changes, only a HubSpot `deal_stage_map` is used; incoming lead statuses and Salesforce stages always use the defaults above.

## Has the sync been tested against real HubSpot and Salesforce accounts?

No. Victorae's tests run against simulated versions of both CRMs, and there is no record of a test against a live HubSpot portal or a live Salesforce org. Several HubSpot calls (the sign-in token exchange, the contact search by email, linking a deal to a contact, and search paging) are marked in the code as not yet checked against HubSpot's current documentation. The daily guardrails read from both CRMs is also marked unverified.

Before relying on the sync, connect a test account, push a few leads, and check the fields above in your CRM. If a sync fails, the CRM card shows the last error; a rejected sign-in marks the connection as needing to be reconnected.

## Related
- [CRM and integrations](16-crm-and-integrations.md)
- [Webhook events, signatures and Slack alerts](37-webhooks-and-slack-events.md)
- [Sequences and follow-ups](08-sequences-and-follow-ups.md)
- [Privacy, data retention and deletion](21-privacy-data-retention-deletion.md)
- [Known limitations](29-known-limitations.md)
