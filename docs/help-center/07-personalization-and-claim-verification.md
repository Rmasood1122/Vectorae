---
id: personalization-and-claim-verification
title: Personalization and claim verification
audience: customer
last_verified: 2026-10-10
owner: help-center
sources:
  - app/services/message_personalization.py
  - app/services/personalization_context.py
  - app/services/claim_verification.py
  - app/services/style_profile.py
  - app/services/system_settings.py
  - app/services/fpta_scoring.py
  - app/services/sequence_engine.py
  - app/workers/outreach_tasks.py
  - app/integrations/linkedin_posts.py
  - app/integrations/newsapi.py
  - app/api/claims.py
  - app/api/personalization.py
  - docs/features/personalization.md
  - docs/features/fpta-scoring.md
  - docs/features/why-this-prospect.md
  - docs/VERIFICATION_REPORT.md
  - frontend/src/app/(app)/leads/detail/page.tsx
  - frontend/src/components/leads/ClaimChecksPanel.tsx
  - frontend/src/components/leads/WhyThisProspectPanel.tsx
  - frontend/src/components/leads/PersonalizationPanel.tsx
  - frontend/src/lib/fpta.ts
  - frontend/src/app/(app)/settings/page.tsx
---

# Personalization and claim verification

Vectorae writes each outreach message for one person, using what it actually knows about them. Before a message is sent, every factual claim it makes about that person or their company is checked against stored data, and anything unsupported is taken out. This article explains what Vectorae uses, what it removes, and where you can see both.

## How is each message written?

Messages are written at the moment they are sent, not when the sequence is created. For an email, Vectorae gives the AI:

- the step's **brief** (what you asked the message to say);
- the strategy's **messaging research** (the summary of research phase 6);
- the lead's **name, title, company** and enrichment details;
- any recent **LinkedIn posts** and **company news** it has stored for the lead;
- your **voice profile**, if you set one up;
- the sequence's booking link, if it has one.

The AI is asked for a plain-text email under 140 words, with no placeholders such as [Name]. It does not write the unsubscribe footer; Vectorae adds the compliant footer itself. The exact subject and body are saved on the message before it is sent, so you can always see what went out.

## What information about a prospect does Vectorae use?

- **Enrichment** gathered during lead sourcing (from Apollo).
- **Recent LinkedIn posts.** Right before writing an email, Vectorae fetches the lead's three most recent posts if its stored copy is more than 7 days old. It reads them through Unipile, which needs a connected LinkedIn account, or through a RapidAPI scraper as a fallback. When posts are available, the email's first line is asked to reference one specific detail from them.
- **Company news.** News from NewsAPI from the last 30 days, refreshed if the stored copy is more than 3 days old. An article is kept only if the company name appears in its title or description, and the AI is told to use it only if it plausibly concerns this company.
- **Your voice.** In **Settings**, on the **Voice** tab, you can paste up to 5 writing samples. Vectorae extracts a style profile from them (tone, formality, vocabulary and similar). Only the profile is used when writing; your samples are stored for editing but never go into an outreach prompt.

If a provider is not set up or fails, Vectorae writes the email without that input. It never holds back a send because of it.

The LinkedIn post and news integrations have not yet been verified against the live services. Several request details for the LinkedIn post providers are still marked in the code as needing confirmation.

You can see what Vectorae has stored on a lead's detail page, on the **Personalization** tab (**Company news** and **Recent LinkedIn posts**).

## What happens when a message makes a claim Vectorae can't back up?

Every AI-written message goes through a claim check after it is written and before it is sent. The check looks for claims about the prospect or their company in these categories: funding, headcount, hiring, a job change, news, metrics, expansion, and references to their own content (such as "your recent post").

A claim counts as supported only if both are true:

- stored data of the right kind mentions the topic (for example, a funding claim needs funding evidence); and
- every specific detail in the sentence, such as a number, an amount, a "Series B", a quoted phrase or a proper name, appears in that stored data.

The only evidence that counts is what Vectorae has stored on the lead: their name, title and company, enrichment data, stored company news and their own stored posts. Nothing new is looked up during the check.

When a claim is not supported:

- the sentence is **removed**; or
- where removing it would break the message (the opening line, the subject line, or a WhatsApp template variable), it is **rewritten** to a neutral line that asserts nothing. A replaced subject line becomes "Quick question".

If the check itself fails with an error, every claim it detected by its rules is removed rather than sent unchecked.

Besides its fixed rules, Vectorae also asks the AI to find claims the rules missed. This is on by default; an administrator can turn it off, and it costs one extra AI call per message.

The check runs on email, on WhatsApp free-form replies and template variables (the approved template text itself is fixed and is not changed), and on LinkedIn messages.

## Does the claim check cover what I say about my own company?

No. The check only looks at sentences about the prospect: second person ("you", "your"), their name, or their company. A sentence such as "We helped 40 agencies grow" is a claim about you, and it is your responsibility to make sure it is true.

## Where can I see what was checked or removed?

Open the lead's detail page and choose the **Claim checks** tab. For each message you see either "All claims verified" or how many were removed. For each claim you see:

- the verdict (verified, stripped or rewritten) and the category;
- for a verified claim, the stored data that backed it;
- for an unsupported claim, the original text struck through, what it was replaced with, and what could not be found in stored data.

## How are WhatsApp template variables filled?

A variable mapped to a lead field (full name, first name, title, company or email) is filled straight from the lead record, with no AI call. Any other variable is filled by the AI from the lead's data and the messaging research, and it is told never to invent facts, names or statistics. If a variable cannot be filled, the message is not sent with an empty slot.

## What is the "Why this prospect" panel?

On a lead's detail page, the **Overview** tab includes **Why this prospect**. It explains the lead's F-P-T-A score, which rates four things from 0 to 100:

- **Fit:** are they the company and person this offer is for?
- **Problem:** is there evidence they have the problem you solve?
- **Timing:** is something happening now that makes this the moment?
- **Access:** can you reach them on a channel they answer?

The overall score weights Fit and Problem at 30% each and Timing and Access at 20% each. Leads are scored when they are enrolled in a sequence. Each dimension starts from a baseline computed from stored data, and one AI call per batch of 10 leads writes an explanation and may move each baseline by at most 15 points.

The panel names the strongest and weakest dimension, suggests a next step when the weakest is below 60, and shows the evidence behind every dimension. A lead that has not been scored says "Not scored yet", and a score made without the AI step is labelled as such.

## Related
- [Lead sourcing and email verification](06-lead-sourcing-and-email-verification.md)
- [Sequences and follow-ups](08-sequences-and-follow-ups.md)
- [WhatsApp outreach](10-whatsapp-outreach.md)
- [LinkedIn](11-linkedin.md)
- [Compliance](20-compliance.md)
