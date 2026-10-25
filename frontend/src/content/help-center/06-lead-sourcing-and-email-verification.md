---
id: lead-sourcing-and-email-verification
title: Lead sourcing and email verification
audience: customer
last_verified: 2026-10-18
owner: help-center
sources:
  - docs/program/ai-visibility/REPORT.md
  - frontend/src/content/product-facts.json
  - app/api/leads.py
  - app/api/schemas.py
  - app/workers/lead_tasks.py
  - app/integrations/apollo.py
  - app/integrations/hunter.py
  - app/integrations/base.py
  - app/integrations/plumbing.py
  - app/services/icp_extraction.py
  - app/services/lead_scoring.py
  - app/services/provenance.py
  - app/api/ui_support.py
  - frontend/src/app/(app)/pipeline/page.tsx
  - app/services/consent.py
  - app/services/sequence_engine.py
  - app/workers/outreach_tasks.py
  - app/db/models.py
  - app/core/plans.py
  - app/config.py
  - frontend/src/components/strategy/OutreachPanel.tsx
  - frontend/src/lib/leadOutreach.ts
  - frontend/src/lib/settings-tabs.ts
  - docs/VERIFICATION_REPORT.md
  - docs/program/BASELINE.md
  - app/api/linkedin_network.py
  - app/services/network_messages.py
  - frontend/src/app/(app)/network/page.tsx
  - app/api/sequences.py
  - app/services/local_business.py
  - app/services/spend_guard.py
  - app/services/waterfall_enrichment.py
  - app/services/system_settings.py
  - frontend/src/app/(app)/settings/page.tsx
  - frontend/src/components/analytics/SpendGuardPanel.tsx
  - frontend/src/app/(app)/analytics/page.tsx
---

# Lead sourcing and email verification

Once a strategy is verified, Victorae can find people who match it, look up their email addresses and check that those addresses accept mail. This article explains each stage, what "verified" means for a lead, how the suppression list protects people who opted out, and the limits your plan sets.

## How do I find leads?

Open the verified strategy's page. In the **1. Find leads** card, enter **How many leads** (1 to 25 per click; the form starts at 5) and press **Find leads**. Victorae shows "Finding leads. This takes a few minutes." and the list fills in as leads move through the stages.

Sourcing is refused for a strategy whose status is not `verified`.

Victorae builds the search from the strategy's research: job titles, industries, locations, company size ranges and keywords from the ideal customer profile. For a local business with a service area, the location is your city or postcode instead (the radius is not sent to the lead provider). Through the API a request can ask for up to 1,000 leads, but one sourcing run is capped at 200 by default.

By default you can start 5 sourcing requests per hour. Each lead uses paid Apollo and Hunter credits, so the screen suggests starting small.

## Whose Apollo and Hunter credits does sourcing use?

Those of the Apollo and Hunter accounts whose API keys your administrator set on the server; you do not enter keys in the app. In **Settings**, on the **Integrations** tab, the **Apollo.io** and **Hunter.io** cards show the key masked, or "Key not set"; **Test** checks the connection.

Victorae counts as paid: Apollo searches (one credit per person requested), each Apollo enrichment, and each Hunter email search and email check. Search results are cached for 1 day, and enrichment, email-finding and email-check results for 30 days; a cached repeat costs nothing (a repeated email check returns the earlier result). If your administrator turned on Spend Guard, the **Usage & spend** page (from **Analytics**) shows these as **Data credits**, in dollars only once an administrator sets a price per credit. There an owner or manager can set a **Monthly spend cap**: a run that does not fit is refused before it starts, and one that reaches it stops before its next paid call.

## What happens to each lead?

Each sourcing request runs five stages in order:

1. **Sourcing.** Apollo is searched for people matching the profile.
2. **Enriching.** Apollo adds name, job title, company, company domain, email, phone and LinkedIn profile where it has them, keeping details already found. An email Apollo has not unlocked counts as missing.
3. **Finding emails.** If a person has no email address yet, Hunter tries to find one from their name and company domain. If none is found, the lead is dropped.
4. **Verifying.** Hunter checks every email address.
5. **Finalizing.** Counts are recorded, and the surviving leads are given an AI booking-likelihood score.

Each lead is saved as it finishes each stage, so a stage that is interrupted can resume without creating duplicates.

A strategy never holds the same person twice (same email address, or same record from the same provider); the database enforces this too. The same person can appear in two different strategies.

## What do the lead statuses mean?

| Status | Meaning |
|---|---|
| `sourced` | Found by the search. |
| `enriched` | Details added. |
| `email_found` | Has an email address that has not been checked yet. |
| `verified` | Hunter reported the address as deliverable. |
| `flagged` | Hunter reported the address as risky, or could not tell. |
| `dropped` | Undeliverable, suppressed, no email found, a duplicate, removed by you, or erased. |
| `contacted` | At least one message has been sent. |
| `replied` | The person replied. |
| `meeting_booked` | The person booked a meeting. |

Later stages, after a meeting, are covered in the meetings and CRM articles.

## What does "verified" mean for a lead?

Hunter's check returned "deliverable" for the address. It is the provider's verdict at the time of the check, not a guarantee that a future email will arrive. Apollo's own label on an email is never the verdict: every address goes through Hunter, whoever found it.

- **Risky** addresses, and addresses Hunter could not judge, become `flagged` for you to decide on. Victorae stores Hunter's verdict and confidence score, not its reason, so it cannot tell you why an address is risky. With the email waterfall turned on by your administrator, an address on a server that accepts every address (catch-all) is also `flagged`, even if Hunter called it valid. If you have checked a flagged address yourself, move the lead to `verified` (or back to `flagged`) on the **Pipeline** board.
- **Undeliverable** addresses become `dropped` and are never enrolled. Only `verified` leads can be enrolled, from the app or the API; an API request naming any other status (for example `flagged`) is refused.
- An address Hunter built from a name and company domain is recorded as found that way, separately from one a provider confirmed.

Launching a sequence enrolls only `verified` leads with an email address; the launch form says how many.

## How does the suppression list protect people?

The suppression list holds people who must not be contacted. A person is added when they unsubscribe, reply asking to stop, send STOP on WhatsApp, or when you erase their lead. It covers their email address, phone number and LinkedIn profile, so opting out on one channel stops all of them.

Victorae checks the list when a lead is first found, whenever a new email or phone number is discovered for them, at enrollment, and immediately before every message is sent. A suppressed lead is skipped or dropped, never contacted. The list applies across your whole Victorae deployment. Administrators can view it in **Settings** under the **Suppression** tab; other users do not see that tab.

## How many leads can a strategy hold?

Your plan limits the number of leads per strategy. Dropped leads do not count.

| Plan | Leads per strategy |
|---|---|
| Free | 100 |
| Starter | 500 |
| Growth | 2,500 |
| Pay per meeting | 1,000 |
| Scale | 10,000 |
| Pro, Enterprise | No limit |

When a strategy is full, a new sourcing request is refused with an upgrade message. When it is nearly full, Victorae asks for no more leads than the room left, and checks again for each lead it adds.

## Can I remove a lead or erase their data?

- **Remove** (next to a verified or flagged lead in the **1. Find leads** list) marks the lead `dropped`, so it is not included when you launch. It also stops any sequence the lead is in straight away: queued messages are cancelled, and a due message is checked again at sending, so a removed lead is never sent to. A dropped lead cannot be moved back to another status.
- **Delete (GDPR erasure)** (open a lead on the **Pipeline** page) permanently erases the lead's personal data, the messages written to them and the replies they sent, and adds their email address, phone number and LinkedIn profile to the suppression list, so they are never contacted or sourced again. A record that the erasure happened is kept. You confirm with **Delete**.

## What if a search finds nobody or a stage fails?

- **Apollo returns no one.** The search stops at the first empty page and the run finishes with no new leads. No message says so; the list simply does not grow. The search uses all of the profile's titles, industries, locations, company sizes and keywords, so a narrow profile can return few or no people.
- **People are skipped** when they are already in the strategy, suppressed, on one of your exclusion lists (if your administrator turned those on), or beyond your plan's lead limit.
- **A stage errors** (for example a provider is down or a key is wrong): it is retried up to 3 times, 30 seconds apart, and resumes where it stopped. The error is recorded but not shown in the app.

## Can I upload my own contact list?

Not in the app: leads come from the sourcing process above. (Through the API, a local business with customer win-back switched on, which is off by default, can import its own opted-in customers into a strategy.) On the Growth, Scale, Pro and Enterprise plans you can upload your LinkedIn connections on the **Network** page and message them there, but they do not become leads (see the LinkedIn article).

## Are Apollo and Hunter verified against the live services?

The founders confirmed finding and verifying contacts as working end to end in production on 2026-10-13. Both integrations were built from the providers' documentation and are tested automatically against simulated responses, and several request details are still marked in the code as needing confirmation against the providers' current documentation. If sourcing returns no leads or errors, contact support.

## Related
- [Strategy verification](05-strategy-verification.md)
- [Personalization and claim verification](07-personalization-and-claim-verification.md)
- [Sequences and follow-ups](08-sequences-and-follow-ups.md)
- [Compliance](20-compliance.md)
- [Privacy, data retention and deletion](21-privacy-data-retention-deletion.md)
- [Plans, limits and billing](22-plans-limits-billing.md)
