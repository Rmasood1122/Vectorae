Server temporarily unreachableWe can't reach Vectorae's server right now, so we can't confirm whether scheduled sends are going out. We'll reconnect automatically. [Check service status](https://www.calendarharvest.com/status/)

[Vectorae](https://www.calendarharvest.com/)

- [How it Works](https://www.calendarharvest.com/how-it-works/)
- [Compare](https://www.calendarharvest.com/compare/)
- [Help](https://www.calendarharvest.com/help/)
- [About](https://www.calendarharvest.com/about/)
- [Pricing](https://www.calendarharvest.com/pricing/)
- [Contact](https://www.calendarharvest.com/contact/)

Help center

# Strategy patterns and playbooks

Vectorae groups strategies that aim at the same kind of buyer with the same kind of outreach, and keeps a running score of how that combination performs. This article explains what a pattern key is, how to read a playbook score, what Vectorae does with the scores, and what it never changes on its own. For the nightly schedule, A/B variants and the promotion checks, see the learning loop article.

Not live yet: LinkedIn steps; Stripe checkout (paying for a plan online). Where this article describes them, they are built and tested only against simulated services. Free trials and online plan changes described here start through online checkout, which is not live yet; to start, [book a call](https://www.calendarharvest.com/contact/). Features marked off by default must be switched on by an administrator. See [known limitations](https://www.calendarharvest.com/help/known-limitations/).Last checked: 2026-10-20.

## What is a pattern key?

A pattern key is a fingerprint of a strategy's "play": who it targets and how it reaches them. It is a 64-character code (a SHA-256 hash) built from two halves.

**The ICP half** is the five lead-search lists taken from the strategy's research: titles, industries, locations, company size ranges and keywords. Every value is lower-cased, de-duplicated and sorted, so the order the AI listed them in does not matter.

**The tactic half** is classified by AI from research phase 5 (channels) and phase 8 (execution plan), using only fixed values:

- **Channels:** email, whatsapp, linkedin, phone, referral, inbound.
- **Sales motion:** cold\_outbound, warm\_intro, inbound\_led, product\_led, partner\_led.
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

Playbook scores are not shown on a customer screen yet. The **Analytics** page has a **Learning insights** card marked **Coming in M8**. On plans with playbook access, the Vectorae API returns your scores at `GET /playbook/scores` (optionally for one `strategy_id`), and the subject-line styles with the highest meeting rate at `GET /playbook/subject-patterns`. Subject-line rates are pooled across every account on this install, so that endpoint returns each style with its reply and meeting rates and sample size, never an example subject line. Administrators see every score on the admin **Playbook Scores** page.

## What does Vectorae do with the scores?

Only two things read them:

- **New research.** When a new strategy reaches research phases 6 (messaging and offer) and 8 (execution plan), the research is given up to 8 reliable scores from pattern keys your strategies use, best booking rate first. The research sees each pattern by its code with its rates, sample size and trend, not the ICP and tactics behind it.
- **AI booking likelihood.** The **Past campaign results** factor uses the best reliable booking rate for the strategy's pattern key. A booking rate of 5% or more gives the full factor; with no reliable score it is 50%. It is 15% of the score.

## Does Vectorae switch to the better message on its own?

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

- [The learning loop and A/B tests](https://www.calendarharvest.com/help/learning-loop-and-ab-tests/)
- [Your product profile and ideal customer profile](https://www.calendarharvest.com/help/product-profile-and-icp/)
- [Personalization and claim verification](https://www.calendarharvest.com/help/personalization-and-claim-verification/)
- [Sequences and follow-ups](https://www.calendarharvest.com/help/sequences-and-follow-ups/)
- [Analytics, health score and ROI](https://www.calendarharvest.com/help/analytics-health-score-roi/)
- [Plans, limits and billing](https://www.calendarharvest.com/help/plans-limits-billing/)

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