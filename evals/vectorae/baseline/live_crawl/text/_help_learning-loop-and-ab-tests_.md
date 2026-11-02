Server temporarily unreachableWe can't reach Vectorae's server right now, so we can't confirm whether scheduled sends are going out. We'll reconnect automatically. [Check service status](https://www.calendarharvest.com/status/)

[Vectorae](https://www.calendarharvest.com/)

- [How it Works](https://www.calendarharvest.com/how-it-works/)
- [Compare](https://www.calendarharvest.com/compare/)
- [Help](https://www.calendarharvest.com/help/)
- [About](https://www.calendarharvest.com/about/)
- [Pricing](https://www.calendarharvest.com/pricing/)
- [Contact](https://www.calendarharvest.com/contact/)

Help center

# The learning loop and A/B tests

Vectorae records what happens to every message it sends (sent, replied, booked and so on) and uses those records to inform future work. This article explains what the learning loop looks at, how A/B variants are compared and when a winner is "promoted", and the limits you should know about. Nothing in the learning loop rewrites a live campaign on its own.

Not live yet: LinkedIn steps; Stripe checkout (paying for a plan online). Where this article describes them, they are built and tested only against simulated services. Free trials and online plan changes described here start through online checkout, which is not live yet; to start, [book a call](https://www.calendarharvest.com/contact/). Features marked off by default must be switched on by an administrator. See [known limitations](https://www.calendarharvest.com/help/known-limitations/).Last checked: 2026-10-10.

## What does Vectorae learn from?

Every send, reply, booking and similar event is stored as an outcome record, tagged with its strategy and, when the event is linked to a specific message, with that message's variant. An event that is not linked to a message (for example a reply matched only by the sender's address) counts for the strategy but not for any variant. Every night (by default at 02:05 UTC, configurable by your deployment), Vectorae rolls these records up into scores for each strategy pattern: reply rate and booking rate, per variant.

Older results count for less. A result from 90 days ago counts half as much as one from today (the default half-life, configurable). Vectorae also counts how much recent evidence there is, so a pattern with many old results can still count as "not enough data".

## How is what it learns used in new strategies?

When the research pipeline builds a new strategy, the messaging step and the execution-plan step are given up to 8 of your account's best-scoring patterns that have enough data to be considered reliable. Only patterns from your own account's strategies are used.

On a new account there is no data yet. In that case the pipeline is told plainly that no learned data exists and works from research alone.

## How do A/B variants work?

Each sequence step carries a variant label (the default is "A"). Within one sequence each step number appears only once, so a step has exactly one variant. To compare variants you run sequences whose steps carry different labels within the same strategy. The sequence form in the app does not set variant labels, so steps created there are all variant "A"; labels can be set when a sequence is created through the Vectorae API.

The **A/B variants** table on the CRM campaigns dashboard shows each variant's sends, reply rate and booking rate, counted from messages actually sent. A variant with under 30 sends is flagged as below the sample size the promotion check needs, so read it as an early signal only.

## When is a winning variant promoted?

Automatic promotion is **off by default**; an administrator turns it on with the `ab_auto_promotion_enabled` system setting. It is off because a promotion is permanent (at most once per strategy) and variants are labels on different sequences, not randomly assigned groups, so a "winner" can reflect which leads were enrolled where rather than the message itself. When it is on, a nightly sweep (30 minutes after the nightly roll-up, at 02:35 UTC by default) checks strategies that have at least two variants and at least 30 sends in total. The measure is **meetings booked per send**.

For exactly two variants, a winner is only promoted if **all four** checks pass:

1. **Sample size:** each variant has at least 30 sends.
2. **Significance:** the difference is statistically significant at p below 0.05.
3. **Minimum lift:** the winner is at least 10% better, relative to the other.
4. **Harm limit:** the winner's rate of bounces, unsubscribes and opt-outs is at most 5% of its sends.

These four numbers are deployment settings; the values above are the defaults.

For three or more variants, a different test is used: the variants must differ significantly overall (p below 0.05), the best must beat the second-best after a correction for comparing many pairs, the best must be at least the minimum lift better than the second-best, and its unsubscribe rate must be at most 5%. The code notes that results with fewer than 5 sends per variant should be treated as inconclusive.

If any check fails, nothing is promoted. Each strategy is promoted at most once. Messages that have already been sent are never changed.

**Important limitation:** promotion records the winning variant on the strategy, and the **A/B variants** table marks it "promoted winner" when the CRM campaigns dashboard is filtered to that strategy. A promotion does not by itself change what your campaign sends. To act on a result, change your sequences yourself.

## What happens when a campaign gets no replies?

If a campaign has been sending for 7 days (the default, set by your administrator) with **zero** replies, counted from its first sent message, Vectorae reviews it. AI looks at the strategy, the target customer, the step briefs and the numbers, and proposes a diagnosis and three changes: a new messaging angle, a channel recommendation, and target refinements, plus rewritten messaging.

This is saved as a **proposed** new version of the strategy and you are notified. It is never applied automatically; applying it is your decision. At most one automatic proposal is made per idle period.

## What is smart send time?

**Smart send time** is a per-campaign switch. Vectorae scores each day-and-hour slot in the lead's own time zone by opens plus three times replies. Once a campaign has 50 opens (the default), it works out its top windows, using only hours inside your allowed send window.

With it on, a step is moved to the next of the top 3 windows at or after its normal time, never earlier and never more than 72 hours later (the default). If no window fits, it sends at its normal time. Turning it on also moves steps already scheduled but not yet sent; turning it off leaves them where they are.

## Which plans include these features?

The learning-loop insights pages (send-time recommendations, subject-line patterns and playbook scores) need a plan with **playbook access**, included in Starter, Growth, Scale, Pay per meeting, Pro and Enterprise, and not in Free. Multi-variant results (three or more variants compared at once) are shown on the **Analytics** page, in the **Multi-variate test results** card under the variant comparison, and are also available through the Vectorae API. They need a plan with **multi-variate testing**: Growth, Scale, Pro and Enterprise. On other plans the card says "Multi-variate test results are included from the Growth plan." (the API answers HTTP 402).

## Related

- [The research pipeline](https://www.calendarharvest.com/help/research-pipeline/)
- [Sequences and follow-ups](https://www.calendarharvest.com/help/sequences-and-follow-ups/)
- [Analytics, health score and ROI](https://www.calendarharvest.com/help/analytics-health-score-roi/)
- [Plans, limits and billing](https://www.calendarharvest.com/help/plans-limits-billing/)
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