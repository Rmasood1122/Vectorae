---
id: strategy-patterns-and-playbooks
title: Strategy patterns and playbooks
audience: customer
last_verified: 2026-10-20
owner: help-center
sources:
  - app/services/icp_extraction.py
  - app/services/pattern_recognition.py
  - scripts/backfill_pattern_key.py
  - app/workers/tasks.py
  - app/api/leads.py
  - app/services/score_decay.py
  - app/workers/learning_tasks.py
  - app/services/playbook_service.py
  - app/pipeline/engine.py
  - app/api/playbook.py
  - app/api/admin.py
  - app/services/lead_scoring.py
  - app/services/subject_intelligence.py
  - app/services/ab_testing.py
  - app/services/crm_service.py
  - app/services/system_settings.py
  - app/services/claim_verification.py
  - app/services/personalization_context.py
  - app/config.py
  - app/db/models.py
  - frontend/src/app/(app)/analytics/page.tsx
  - frontend/src/app/(admin)/admin/playbook/page.tsx
  - frontend/src/lib/lead-score.ts
  - frontend/src/app/(app)/leads/detail/page.tsx
  - docs/program/DECISIONS.md
  - docs/program/fixes/product-defects.md
  - docs/program/help-center-discoveries.md
---

# Strategy patterns and playbooks

Victorae groups strategies that aim at the same kind of buyer with the same kind of outreach, and keeps a running score of how that combination performs. This article explains what a pattern key is, how to read a playbook score, what Victorae does with the scores, and what it never changes on its own. For the nightly schedule, A/B variants and the promotion checks, see the learning loop article.

## What is a pattern key?

A pattern key is a fingerprint of a strategy's "play": who it targets and how it reaches them. It is a 64-character code (a SHA-256 hash) built from two halves.

**The ICP half** is the five lead-search lists taken from the strategy's research: titles, industries, locations, company size ranges and keywords. Every value is lower-cased, de-duplicated and sorted, so the order the AI listed them in does not matter.

**The tactic half** is classified by AI from research phase 5 (channels) and phase 8 (execution plan), using only fixed values:

- **Channels:** email, whatsapp, linkedin, phone, referral, inbound.
- **Sales motion:** cold_outbound, warm_intro, inbound_led, product_led, partner_led.
- **Cadence:** light (3 touches or fewer), standard (4 to 6) or aggressive (7 or more).
- **Flow type:** whether the strategy was built on past clients or researched from scratch.

An answer outside these lists is dropped rather than creating a new value. If the classification fails, the channels, sales motion and cadence are left empty; the flow type is still recorded.

Two of your strategies with identical values share a pattern key, and their results are pooled into one score for your account. A strategy gets its key once, when research finishes, or at its first **Find leads** if that step failed. It never changes afterwards, even if later sourcing uses different criteria. The exact inputs are stored with the strategy, so the key can be audited.

## What is a playbook score?

A playbook score is one row per account, pattern key and A/B variant (sends with no variant are grouped as "control"). It is recalculated every night from the outcome records of your strategies that have that pattern key. It holds:

- **Reply rate:** the share of sends that went to a lead who replied.
- **Booking rate:** the share of sends that went to a lead who booked a meeting.
- **Sample size:** the number of sends counted.
- **Effective sample size:** the sends weighted by age (see below).
- **Reliable:** whether the effective sample size has reached 30.
- **Trend:** how the reply rate moved since the previous nightly run.

Both rates are weighted by age with a 90-day half-life: a send from 90 days ago counts half as much as one from today. Replies and bookings are matched to sends by lead, so if a lead received three emails and replied once, all three sends count as replied.

Scores are kept per account. Another account's strategies with the same pattern key never count towards your score, and yours never count towards theirs. Scores calculated before this change pooled every account on the deployment. They are no longer used, and you no longer see them; only administrators can still see them.

## How do I read a playbook score?

1. **Check Reliable first.** An unreliable score has too little recent evidence. Many old sends can still be unreliable, because their weight has decayed.
2. **Compare effective sample size with sample size.** A large gap means most of the evidence is old.
3. **Read the trend.** "new" means the first calculation. "rising" or "falling" means the reply rate moved more than half a percentage point (0.005) since the previous night; anything smaller is "stable".
4. **Prefer booking rate over reply rate.** Replies include objections and refusals.

Playbook scores are not shown on a customer screen yet. The **Analytics** page has a **Learning insights** card marked **Coming in M8**. On plans with playbook access, the Victorae API returns your scores at `GET /playbook/scores` (optionally for one `strategy_id`), and best-performing subject-line styles at `GET /playbook/subject-patterns`. Administrators see every score on the admin **Playbook Scores** page.

## What does Victorae do with the scores?

Only two things read them:

- **New research.** When a new strategy reaches research phases 6 (messaging and offer) and 8 (execution plan), the research is given up to 8 reliable scores from pattern keys your strategies use, best booking rate first. The research sees each pattern by its code with its rates, sample size and trend, not the ICP and tactics behind it.
- **AI booking likelihood.** The **Past campaign results** factor uses the best reliable booking rate for the strategy's pattern key. A booking rate of 5% or more gives the full factor; with no reliable score it is 50%. It is 15% of the score.

## Does Victorae switch to the better message on its own?

No. Nothing in the learning loop changes a running campaign.

- **A/B auto-promotion is off.** The `ab_auto_promotion_enabled` setting is off by default, and a founder decision on 2026-10-11 keeps it off: a promotion is permanent, and variants are labels on different sequences rather than randomly assigned groups, so a "winner" can reflect which leads were enrolled where.
- **Even when an administrator turns it on,** a promotion only records the winner on the strategy, and the CRM campaigns dashboard marks it "promoted winner". Your sequences keep sending exactly what they were set up with.
- **Other things it does not change:** step briefs, messages already written or sent, which variant a lead gets, and a strategy's pattern key.

To act on a result, change your sequences yourself.

## Why do my emails keep starting with the same line?

The learning loop does not choose or reuse opening lines. The usual causes are:

- **The claim check rewrote the opener.** If the first sentence makes a claim about the prospect that stored data cannot back up, it is replaced with "I wanted to reach out to you at" followed by their company and "directly." (or "I wanted to reach out to you directly." with no company). An unsupported subject line becomes "Quick question". Open the lead's **Claim checks** tab: a rewritten line has the verdict "rewritten".
- **There was nothing personal to use.** The first line is asked to reference one of the lead's recent LinkedIn posts only when posts are stored for them, and company news only when there is recent news. Check the **Personalization** tab.
- **Every email in a step uses the same brief.** A brief that dictates an opening produces it every time.

To fix it, rewrite the step's brief around a question or a problem rather than facts about the prospect.

## Related
- [The learning loop and A/B tests](17-learning-loop-and-ab-tests.md)
- [Your product profile and ideal customer profile](33-product-profile-and-icp.md)
- [Personalization and claim verification](07-personalization-and-claim-verification.md)
- [Sequences and follow-ups](08-sequences-and-follow-ups.md)
- [Analytics, health score and ROI](18-analytics-health-score-roi.md)
- [Plans, limits and billing](22-plans-limits-billing.md)
