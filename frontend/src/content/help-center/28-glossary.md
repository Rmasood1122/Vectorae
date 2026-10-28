---
id: glossary
title: Glossary of Vectorae terms
audience: customer
last_verified: 2026-10-11
owner: help-center
sources:
  - FEATURES.md
  - app/db/models.py
  - app/api/strategies.py
  - app/core/plans.py
  - app/services/usage_meter.py
  - app/services/fpta_scoring.py
  - app/services/conversion_probability.py
  - app/services/system_settings.py
  - app/services/sequence_engine.py
  - app/config.py
  - app/services/reply_intent.py
  - app/services/reengagement_memory.py
  - app/services/playbook_service.py
  - app/services/attribution.py
  - app/services/pipeline_health.py
  - app/services/consent.py
  - app/services/rbac.py
  - app/services/client_workspaces.py
  - app/services/api_keys.py
  - app/services/mailbox_health.py
  - docs/features/fpta-scoring.md
  - docs/features/attribution-ledger.md
  - docs/features/send-review-queue.md
  - docs/features/workspaces.md
  - docs/program/BASELINE.md
  - app/api/unsubscribe.py
  - app/api/leads.py
  - app/api/account_data.py
  - app/pipeline/engine.py
  - app/api/admin.py
---

# Glossary of Vectorae terms

This page explains the words Vectorae uses in the app and in this help center. Terms are grouped by the part of the product they belong to. Each group links to the article with the full details.

## What do the setup terms mean?

- **Product** – what you sell, described in plain language. Every strategy belongs to a product.
- **Past client** – details of a client you already won, and the story of how you won them. Vectorae pulls structured patterns from it: industry, company size, buyer role, deal size, acquisition channel, trigger event and sales-cycle length.
- **Flow 1 (with clients)** – used when the product has at least one past client. The research is anchored on that proven pattern.
- **Flow 2 (no clients)** – used when there is no past client yet. Vectorae builds the strategy and then a go-to-market plan (144 steps in all).
- **Strategy** – the research-backed plan for selling one product: who to target, why, on which channels and with what message. On the Pricing page, strategies are counted as "campaigns".
- **ICP (ideal customer profile)** – the kind of company and person your offer is for, including the people it is not for. It is settled in phase 2 of the research.
- **GTM (go-to-market)** – positioning, pricing, launch order, channel budget and similar groundwork. Built after the strategy, in Flow 2 only.

## What do the research and verification terms mean?

- **Research pipeline** – the research Vectorae runs once for each strategy: 72 steps (8 phases of 9 steps) in Flow 1, or 144 steps (72 strategy plus 72 GTM) in Flow 2. It runs once per strategy, not once per prospect.
- **Verification passes** – 10 independent checks the finished strategy must pass, covering topics such as legal compliance, targets and ICP fit. Nothing is sourced or sent until all 10 pass.
- **Needs human review** – a check kept failing after its retries. A person must re-verify or decide.
- **Strategy budget** – the most a single strategy's research and verification may spend on AI. The default is $15; it can be set between $0.50 and $50.00.
- **Budget exhausted** – the strategy stopped because the next AI step could have gone over its budget or the plan's daily AI limit. It can be resumed.
- **Daily AI limit** – the most AI spend your whole account may use per UTC day, set by your plan. It resets at 00:00 UTC.

## What do the lead terms mean?

- **Lead** – one prospect (a person at a company) found for a strategy.
- **Lead statuses** – sourced, enriched, email found, then verified (email deliverable), flagged (risky) or dropped (undeliverable or suppressed). After outreach: contacted, replied, meeting booked, proposal sent, opportunity, closed won, closed lost or disqualified.
- **Suppression list** – the do-not-contact list of email addresses, phone numbers and LinkedIn profiles. Checked before every send and before sourcing. Entries can't be removed by customers; only a platform administrator can remove one.
- **F-P-T-A** – four scores from 0 to 100 for each lead: **Fit** (right company and person?), **Problem** (evidence they have the problem you solve?), **Timing** (is now a good moment?) and **Access** (can you reach them on a channel they answer?). The overall score weights them 30% fit, 30% problem, 20% timing and 20% access.
- **Cooling / archived** – before each send Vectorae estimates a lead's chance of converting. A cooling lead's sequence is paused; an archived lead's sequence is stopped. See the pausing article for the default thresholds.

## What do the sending terms mean?

- **Sequence** – the series of messages (steps) sent to leads, with a delay between steps. A sequence can be draft, active, paused, completed or pending approval.
- **Step** – one message in a sequence, on one channel.
- **Enrollment** – one lead's place in one sequence. It can be active, paused (resumes later), stopped (can never send again to that lead) or completed (all steps sent).
- **Campaign** – a strategy's outreach while it is running. It can be paused and resumed as a whole.
- **Channel** – where a message goes: email through your connected Gmail (called "gmail" in plans), WhatsApp, LinkedIn, or an AI phone call ("phone").
- **Send window** – the hours messages may go out. By default 09:00 to 17:00 in the lead's time zone, weekdays only.
- **Daily cap and warm-up** – the most emails a mailbox sends per day (100 by default). A new mailbox starts lower (10) and rises by 10 each day.
- **Bounce auto-pause** – a campaign pauses itself when more than 3% of its emails bounce, once at least 10 have been sent.
- **Mailbox health** – a 0 to 100 score per mailbox. Below 70 sending slows down (throttled); below 40 the mailbox is paused.
- **Send-review queue** – where high-risk messages wait for a person to approve them before sending.
- **Opt-in** – a recorded permission from a prospect to receive WhatsApp messages. Cold WhatsApp needs an opt-in and an approved template.
- **24-hour window** – WhatsApp's rule that free-form messages are allowed only within 24 hours of the prospect's last message. Outside it, only approved templates may be sent.

## What do the reply and meeting terms mean?

- **Reply classification** – each human reply is labelled interested, neutral, objection, not now or unsubscribe. Machine messages such as bounces and out-of-office replies are recognised separately.
- **Re-engagement memory** – a "not now" reply is turned into a reminder to come back on the date the prospect gave, or after 90 days by default.
- **Booking page** – Vectorae's own scheduling page where prospects book a meeting with you. Calendly is supported as an alternative.

## What do the learning and reporting terms mean?

- **Playbook** – what Vectorae has learned from real outcomes about which approaches work. New strategies consult it during the messaging and execution phases. On a new account it is empty and says so.
- **A/B test** – sending different versions (variants) of a step to compare results.
- **Attribution** – which message earned a reply or meeting. Vectorae always records how it decided: **direct reply** (the reply is linked to that message; confidence 1.0), **thread match** (same email thread; 0.85), **last touch** (the most recent message before the outcome; a guess whose confidence falls as time passes), or **none** (nothing was sent before the outcome).
- **Pipeline Health Score** – one number from 0 to 100 for a campaign, made of reply rate (35%), sequence completion (25%), how recently new leads were added (20%) and open conversations (20%).

## What do the compliance terms mean?

- **Consent ledger** – the record of opt-ins, opt-outs and erasures for each prospect: when they told you, and what Vectorae did.
- **Unsubscribe** – the link in every email. It works without signing in, takes effect immediately and stops contact on every channel.
- **Erasure (GDPR delete)** – clearing a lead's personal details and adding them to the suppression list.

## What do the account and team terms mean?

- **Plan** – what your account may do: limits on strategies, leads, sequence steps, channels and daily AI spend.
- **Deployment** – one installation of Vectorae (its own server and database). Many settings in this help center are "deployment settings" chosen by whoever runs it.
- **Administrator** – a person who runs the deployment, with access to the admin panel.
- **Workspace** – your account as seen by teammates you invite. **Roles** are owner, manager, SDR and viewer.
- **SDR** – sales development representative: a team member who works leads but whose campaign launches may need approval.
- **Client (agency mode)** – a separate book of business inside an agency account, with its own reporting, sending domains and invoice view.
- **White label** – showing the app under your own brand name, logo and domain.
- **Account deletion** – erasing your own account from **Profile**; billing, consent and suppression records are kept, without your name or email.
- **API key** – a secret starting with `lpk_` that lets tools such as Zapier act as you through the API.
- **Webhook** – a message Vectorae sends to another tool when something happens, for example a reply or a booked meeting.

## Related
- [What is Vectorae](01-what-is-leadpilot.md)
- [Research pipeline](04-research-pipeline.md)
- [Strategy verification](05-strategy-verification.md)
- [Sequences and follow-ups](08-sequences-and-follow-ups.md)
- [Learning loop and A/B tests](17-learning-loop-and-ab-tests.md)
- [Analytics, health score and ROI](18-analytics-health-score-roi.md)
- [Compliance](20-compliance.md)
