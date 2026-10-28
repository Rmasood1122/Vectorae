# Feature Group 4 — CRM & ecosystem

## Slack
Settings › Integrations › **Slack**: connect (OAuth v2, bot scopes only), pick a
channel, send a test message. Vectorae posts:

| Event | When |
|---|---|
| Meeting booked | Calendly or the Vectorae calendar |
| Interested reply | A reply classified *interested* (email, WhatsApp, LinkedIn, call) |
| Campaign auto-paused | Bounce rate over the limit (manual pauses are not posted) |
| Strategy mutation | An idle campaign's strategy was rewritten |
| Also | Meeting prep ready, meeting reminders, objection spikes, deals won |

Public channels work straight away (`chat:write.public`); invite the bot to a
private channel before choosing it. Admin setup: **Admin › Integrations ›
Slack app** (`client_id`, `client_secret`), with the redirect URL
`<PUBLIC_BASE_URL>/integrations/slack/callback`.

## HubSpot and Salesforce (two-way)
Connect from Settings › Integrations. Admin setup: **HubSpot app**
(`client_id`, `client_secret`; redirect `<PUBLIC_BASE_URL>/integrations/hubspot/callback`)
and **Salesforce connected app** (`client_id`, `client_secret`, `webhook_secret`;
callback `<PUBLIC_BASE_URL>/integrations/salesforce/callback`, scopes `api refresh_token`).

**Vectorae → CRM.** Leads that engaged (replied, meeting booked, opportunity,
closed, disqualified) become HubSpot **contacts** / Salesforce **leads**, matched
by email; turn on *Sync new leads* to push every verified lead as well. Deals
become HubSpot **deals** (associated with the contact) / Salesforce
**opportunities**. Pushes happen immediately on key events (meeting booked,
reply, call, deal won, kanban move, deal edit) and in a 30-minute sweep (each worker burst).

**CRM → Vectorae.** Only records Vectorae linked, and only forward:

| CRM change | Vectorae effect |
|---|---|
| HubSpot `hs_lead_status` = UNQUALIFIED | lead → disqualified |
| HubSpot `hs_lead_status` = OPEN_DEAL, lifecycle = opportunity | lead → opportunity |
| HubSpot lifecycle = customer | lead → closed won |
| Deal/opportunity closed won / lost | deal won / lost, lead closed won / lost |
| Deal amount or close date | copied to the local deal |
| Salesforce lead converted | lead → opportunity |
| Salesforce lead "Closed - Not Converted" | lead → disqualified |
| HubSpot deal created on a linked contact | imported as a Vectorae deal |

A CRM change never moves a lead backwards and never reopens a closed lead —
which also stops Vectorae and the CRM from overwriting each other. Closing a
lead stops its sequences.

Changes arrive two ways: the 30-minute sweep reads every linked record back
(no CRM configuration needed), and webhooks make it immediate:
- **HubSpot:** in the app's Webhooks settings, target
  `<PUBLIC_BASE_URL>/webhooks/hubspot`, subscribe to `contact.propertyChange`
  (`hs_lead_status`, `lifecyclestage`), `deal.propertyChange` (`dealstage`,
  `amount`, `closedate`), `deal.creation`, `contact.deletion`, `deal.deletion`.
  Verified with signature v3 (the app's client secret), 5-minute window.
- **Salesforce:** a Flow or Apex callout on Lead/Opportunity change posting
  `{"org_id": "00D…", "records": [{"sobject": "Lead", "id": "00Q…",
  "fields": {"Status": "…", "IsConverted": false}}]}` to
  `<PUBLIC_BASE_URL>/webhooks/salesforce` with headers
  `X-LeadPilot-Timestamp: <unix seconds>` and
  `X-LeadPilot-Signature: sha256=<hex HMAC-SHA256(webhook_secret, "<timestamp>.<body>")>`.

Custom picklists: `PUT /integrations/{hubspot|salesforce}/settings` accepts
`lead_status_map`, `deal_stage_map` and `pipeline` overrides.

## Zapier, Make and your own endpoints
Outbound webhooks for `meeting_booked`, `reply_received`, `lead_sourced` and
`campaign_paused` (plus `reply_interested`, `deal_won`, `call_completed`,
`strategy_mutated`, `objection_spike`). Register with
`POST /webhooks/outbound/register`; the signing secret is returned once.
Every delivery is signed:
`X-LeadPilot-Signature: t=<unix ts>,v1=<hex HMAC-SHA256(secret, "<ts>.<body>")>`.
Retries back off 30s → 2h: the first attempt plus 5 retries, 6 attempts in all (it stopped after 5 before the send-path-gaps fix, so the 2-hour retry never ran); a `410 Gone` unsubscribes (the
Zapier REST-hook convention). Targets must be public HTTPS URLs; Vectorae
refuses private addresses and does not follow redirects.

Zapier and Make authenticate with a **personal API key** (Settings ›
Integrations › API keys): `Authorization: Bearer lpk_…` or `X-API-Key: lpk_…`.
Keys are shown once, stored hashed, and cannot reach admin routes.

## Repaired along the way
The M8-C5 outbound webhook module never delivered: its raw SQL used a Postgres
array operator on a JSON column, wrote columns that did not exist, and its
task imported modules at paths that do not exist. It is rebuilt on the ORM
(`app/services/webhook_delivery.py`), keeping the `/webhooks/targets` routes.
