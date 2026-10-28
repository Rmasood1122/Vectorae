---
id: who-its-for-and-not-for
title: Who Vectorae is for, and who it is not for
audience: prospect
last_verified: 2026-10-11
owner: help-center
sources:
  - app/services/local_business.py
  - app/api/local_business.py
  - docs/features/local-business-mode.md
  - docs/ICP.md
  - docs/program/DECISIONS.md
  - app/core/plans.py
  - app/config.py
  - app/services/sequence_engine.py
  - app/workers/outreach_tasks.py
  - app/workers/lead_tasks.py
  - app/api/leads.py
  - app/api/integrations.py
  - app/integrations/gmail.py
  - app/api/whatsapp_templates.py
  - app/services/reply_classification.py
  - app/services/compliance_region.py
  - app/services/compliance_audit.py
  - app/services/open_tracking.py
  - docs/features/compliance-rules.md
  - frontend/src/components/strategy/OutreachPanel.tsx
  - frontend/src/app/(app)/strategies/new/page.tsx
  - docs/program/BASELINE.md
  - app/api/auth.py
  - app/api/linkedin_network.py
  - app/services/linkedin_network.py
  - app/services/network_messages.py
  - frontend/src/app/(app)/network/page.tsx
---

# Who Vectorae is for, and who it is not for

This article helps you decide whether Vectorae fits your business before you sign up. The first part comes from Vectorae's own customer research (its ideal customer profile). The second part comes from what the product actually does today, which sets hard limits no matter who you are.

## Who is Vectorae designed for?

Vectorae's own research describes its target customer as a business-to-business company in the United States with roughly 5 to 50 employees, and a sweet spot of about 8 to 25. At that size there is usually one named person who decides and who is responsible for keeping the sales pipeline full, such as the owner, founder, general manager or head of sales, but no dedicated sales team or sales infrastructure yet.

The research ranks these kinds of businesses as the closest fit, in this order:

1. Small marketing, web development and design agencies.
2. Commercial cleaning and janitorial companies that have grown past a one- or two-person operation.
3. Small accounting, immigration and law consultancies (the research marks this one as an assumption that has not been checked yet).
4. Business-to-business staffing and recruiting agencies.
5. Commercial HVAC and facilities maintenance companies (the lowest-confidence rank).

These are targets, not requirements. You can run Vectorae for any product or service you sell to other businesses.

## When is Vectorae not a good fit?

The same research lists clear non-fits:

- **Companies that already run Salesforce or HubSpot and have a dedicated sales or business development hire.** The problem Vectorae addresses is the absence of that setup.
- **Owner-operators with fewer than 5 employees and no budget for software.**
- **Businesses that sell only to consumers or homeowners.** Vectorae finds and contacts people at companies. It has no consumer prospecting.

## What do I need before I can use it?

- **A product or service you can describe in writing.** The new-strategy form asks for a name and a description, and optionally details of past clients and how you won them.
- **A Google (Gmail) account to send from.** Email outreach is sent through the Gmail account you connect in Settings. Vectorae stores one Gmail connection per user account.
- **A lawful basis to contact each person.** Before you launch, Vectorae asks you to confirm that you may lawfully contact each person on the list, and records your confirmation. It applies compliance checks at send time, but it does not give legal advice.
- **For WhatsApp:** a plan that includes WhatsApp, a WhatsApp Business number configured on your Vectorae deployment, Meta-approved message templates, and a recorded opt-in for each contact.

## Can I upload my own list of contacts?

Not as leads. Leads for email and WhatsApp sequences come only from Vectorae's own sourcing step, which searches for people matching your verified strategy and then checks their email addresses. There is no way to import your own contact list as leads. If your business depends on emailing or messaging a list you already have, Vectorae does not currently support that. The one narrow exception, switched off by default, is a local business importing its own customers who opted in to its marketing (see "Can a local business message its own customers?" below).

Two exceptions are on the **Network** page, on the Growth, Scale, Pro and Enterprise plans. You can upload your LinkedIn `Connections.csv` export and send saved messages to people who are still your 1st-degree connections; those connections do not become leads. And, once the email network is switched on for your server (it is off by default), you can bring in your own business email contacts (contacts files, a mailbox export or your connected Gmail) and add the ones that fit your products to a campaign; they become leads and go through the same email verification and send checks as any other lead. See the LinkedIn article.

## Can a restaurant, clinic or local shop use Vectorae?

Yes, to win business customers near you. On the first step of a new strategy, answer **Is this a local business?** with your kind of business: restaurant, café or caterer; clinic, hospital or healthcare practice; retail or local shop; salon, fitness or other local service; or other local business. Then enter your service area (a city or postcode, a two-letter country code, and a radius in miles or kilometres) and, if you like, what you offer business buyers.

Vectorae then researches, searches and writes for business buyers in that area. For example: offices near a restaurant for catering, team lunches and corporate events; employers near a clinic for occupational health, screenings and staff wellness; schools, sports clubs, referring practices and insurers for a healthcare practice; offices, schools and property managers near a shop for supplies, gifting and bulk orders.

What to expect:

- **Business buyers only.** Vectorae still finds people at companies. It never finds or cold-messages members of the public.
- **The radius is not a search filter.** The lead search uses your city or postcode. The radius guides the research and the messages, but the lead provider does not apply it, so some leads can sit outside it.
- **No invented distances.** Messages may say your business is based in or serves your area. They never claim a walking time or distance to the prospect's office, because Vectorae does not know where it is.
- **Healthcare is business to business.** Outreach for a clinic or hospital is about services for an organisation and never uses or mentions patient information.

## Can a local business message its own customers?

Only through the customer win-back feature, and it is switched off by default on every Vectorae deployment until its legal review is complete. When an administrator turns it on, a local business can upload a CSV of its own customers and send them simple win-back or announcement messages. The rules are strict:

- Every customer needs a recorded opt-in for each channel: the date they opted in and how (for example "loyalty sign-up form at the till"). Rows without that are refused and not stored, and so is anyone who unsubscribed before.
- Email and approved WhatsApp templates only. No text messages (SMS) and no calls.
- The emails are your own words; no AI writes to your customers, and every email carries an unsubscribe link.
- Clinics, hospitals and other healthcare businesses cannot use it at all. Their customer list is a patient list, and using it for marketing needs each patient's written permission, which Vectorae does not collect.

## Is Vectorae built for high-volume sending?

No. It is built for careful, personalized outreach. By default, a newly connected Gmail account starts at 10 emails on its first day, adds 10 per day, and stops at a ceiling of 100 emails per day. These numbers are set per deployment, and compliance rules can only lower the cap, never raise it. Messages over the day's allowance wait for the next day; they are not dropped. The deployment's WhatsApp number follows the same pattern by default.

## Will Vectorae reply to prospects for me?

No. Vectorae does not auto-reply to people. When someone replies, Vectorae classifies the reply, stops their sequence (or pauses it for an out-of-office), and tells you. You write the answer. If you want an AI that holds conversations with prospects on your behalf, Vectorae is not that product.

## Does it work for agencies that run outreach for several clients?

Partly. A Vectorae deployment is intended to serve one customer organization (this holds only if the administrator closes public sign-up or limits it to the organization's email domains; by default sign-up is open). For email, client workspaces can restrict each client's outreach to that client's own sending domains. For WhatsApp, there is one sending number per deployment, so every client's prospects see the same WhatsApp number. If you need a separate WhatsApp number per client, Vectorae does not support that yet.

## Does it work outside the United States?

Vectorae works out which region a lead is in from their country or time zone and applies region-specific rules. For example, it never adds an open-tracking pixel to email sent to leads in the EU, EEA or UK, and it adds a regional notice to the email footer where that region's rules ask for one. Leads with neither a country nor a time zone are treated as "unknown". You are still responsible for knowing the outreach rules that apply to you and your prospects.

## Related
- [What is Vectorae?](01-what-is-leadpilot.md)
- [Getting started](03-getting-started.md)
- [Lead sourcing and email verification](06-lead-sourcing-and-email-verification.md)
- [Sending email through Gmail](09-email-sending-gmail.md)
- [WhatsApp outreach](10-whatsapp-outreach.md)
- [Plans, limits and billing](22-plans-limits-billing.md)
