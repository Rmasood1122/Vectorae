Server temporarily unreachableWe can't reach Vectorae's server right now, so we can't confirm whether scheduled sends are going out. We'll reconnect automatically. [Check service status](https://www.calendarharvest.com/status/)

[Vectorae](https://www.calendarharvest.com/)

- [How it Works](https://www.calendarharvest.com/how-it-works/)
- [Compare](https://www.calendarharvest.com/compare/)
- [Help](https://www.calendarharvest.com/help/)
- [About](https://www.calendarharvest.com/about/)
- [Pricing](https://www.calendarharvest.com/pricing/)
- [Contact](https://www.calendarharvest.com/contact/)

Help center

# Strategy verification (the 10 checks)

After the research pipeline finishes, Vectorae checks the strategy document against 10 separate criteria before anything can reach a real person. This article explains the checks, what happens when one fails, and the two ways forward from `needs_human_review`: re-verifying, or passing a few checks yourself.

Not live yet: LinkedIn steps; Stripe checkout (paying for a plan online). Where this article describes them, they are built and tested only against simulated services. Free trials and online plan changes described here start through online checkout, which is not live yet; to start, [book a call](https://www.calendarharvest.com/contact/). Features marked off by default must be switched on by an administrator. See [known limitations](https://www.calendarharvest.com/help/known-limitations/).Last checked: 2026-10-10.

## How does verification work?

Each check is judged on its own, one criterion at a time. An AI reviewer answers PASS, or FAIL with a concrete instruction for what to change. On a FAIL, Vectorae rewrites the strategy document to resolve that one finding and runs the same check again.

- By default each check gets up to 3 attempts. A strategy that was shortened to fit a small budget gets at most 2.
- Vectorae runs all 10 checks even if an earlier one fails.
- If every check ends in PASS, the strategy becomes `verified`.
- If any check still fails after its attempts, the strategy becomes `needs_human_review`.

Until a strategy is `verified`, Vectorae will not source leads for it or launch a sequence from it.

## What are the 10 checks?

| # | Check | What it requires |
| --- | --- | --- |
| 1 | Factual accuracy | Every factual claim is supported by the research, and estimates are labelled as assumptions. No invented statistics. |
| 2 | ICP fit | The targets and personas match the ideal customer profile from the research, with no drift toward excluded audiences. |
| 3 | Channel–message fit | Each message suits its channel (length, formality, format), and the channels are where the ICP actually is. |
| 4 | Legal & compliance | Sender identity and a working unsubscribe on all email (CAN-SPAM), a lawful basis for EU targets (GDPR), cold WhatsApp only through approved templates to opted-in numbers, and no scraping that breaks a platform's terms. |
| 5 | Deliverability risk | Email volumes include a warm-up ramp, realistic daily caps, and a bounce-rate pause threshold. No day-one mass sending. |
| 6 | Competitive realism | Claims about beating competitors are realistic given the competitor research. |
| 7 | Resource feasibility | The plan can be carried out with your stated (or a solo founder's) time and budget. |
| 8 | KPI realism | Reply, meeting and win-rate targets are within credible ranges for cold outreach. |
| 9 | Personalization quality | Message plans use real per-lead personalization, not generic copy with a first-name token. |
| 10 | Consistency | With past clients: the strategy follows your past-client patterns, or explains why it departs from them. Without past clients: the strategy agrees with the GTM plan. |

When research phases give different values for the same fact (for example a price range in phase 1 and a final price in phase 2), the reviewer is told that the later phase is authoritative. The earlier figure is treated as a superseded estimate, not a contradiction.

## Where do I see the results?

On the strategy page, the **10x verification loop** card lists each check once, with its latest result (PASS or FAIL). A check that needed more than one attempt shows the count, for example "PASS x2". Under the check's name the card shows the fix that was applied ("fix applied: ..."), or, for a check that still fails, the reviewer's reason ("why it failed: ..."). A check passed by a person shows "PASS (manual)" with who passed it and their reason. After a re-verify, the card shows the latest run of each check.

A check can also stop early, before using all its attempts, for two reasons:

- **Conflicting sources.** The proposed fix would put the document back the way it was on an earlier attempt, which means two sources disagree and editing cannot satisfy both. Vectorae stops that check and leaves it for a person.
- **Rewrite too long.** The rewritten document came back cut off even after one retry with a larger limit. Vectorae leaves the document exactly as it was, rather than saving a truncated version, and fails that check.

## What does "needs\_human\_review" mean?

At least one check still failed. The strategy page shows a **Needs your review** notice. You have three options:

1. **Re-verify** to run all 10 checks again on the finished research.
2. **Pass the failed checks manually**, if no more than 3 failed, the Legal & compliance check is not one of them, and you have checked them yourself.
3. **Start a new strategy** with adjusted inputs.

Resume is not available from this status, because the research is already complete.

## How do I re-verify a strategy?

Press **Re-verify** on the strategy page (it appears only for `needs_human_review`). You can set:

- **Attempts per check**, from 1 to 5. The form starts at 4.
- An optional **New budget $**, which must be more than the strategy has already spent.

Press **Confirm re-verify**. The finished research is reused, not repeated, but the checks cost AI spending again and count toward your daily AI limit. Every check is judged from scratch, and the strategy becomes `verified` only if all 10 pass. Otherwise it returns to `needs_human_review`.

## How do I pass a check manually?

Press **Review and pass manually**. It is offered when the strategy is `needs_human_review` or `budget_exhausted`, and also while it shows `verifying`. For a `verifying` strategy, Vectorae refuses the sign-off until nothing has been logged for 15 minutes, because a run that is still working would overwrite your decision. If the Legal & compliance check is among the failures, the button is replaced by a note saying it cannot be passed manually.

The form lists each failed check and why it failed. To submit:

1. Write why the failure is acceptable, in at least 20 characters. This reason is kept on record.
2. Tick the box confirming you have checked these points yourself, and that the document is not independently verified.
3. Press **Pass manually**.

The rules are fixed:

- It is based on the latest verification run that reached all 10 checks. A later run that stopped partway is ignored.
- You must pass exactly the checks that failed, all at once.
- No more than 3 checks may have failed. With more, re-verify or fix the strategy.
- **The Legal & compliance check can never be passed manually.** It has to pass verification.

The strategy then becomes `verified`. The original FAIL results stay in the log. Each manually passed check shows **PASS (manual)** with "passed manually by" your email address and your reason, and the decision is also written to the security audit trail.

## How long does verification take?

It varies with how many checks fail and need a rewrite. Vectorae's own notes estimate one check-and-fix cycle at about 3 to 4 minutes. Each verification run has the same 2-hour time limit and automatic retries as the research pipeline.

## Related

- [The research pipeline](https://www.calendarharvest.com/help/research-pipeline/)
- [Lead sourcing and email verification](https://www.calendarharvest.com/help/lead-sourcing-and-email-verification/)
- [Compliance](https://www.calendarharvest.com/help/compliance/)
- [Troubleshooting](https://www.calendarharvest.com/help/troubleshooting/)
- [Glossary](https://www.calendarharvest.com/help/glossary/)

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