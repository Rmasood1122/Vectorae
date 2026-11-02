Server temporarily unreachableWe can't reach Vectorae's server right now, so we can't confirm whether scheduled sends are going out. We'll reconnect automatically. [Check service status](https://www.calendarharvest.com/status/)

[Vectorae](https://www.calendarharvest.com/)

- [How it Works](https://www.calendarharvest.com/how-it-works/)
- [Compare](https://www.calendarharvest.com/compare/)
- [Help](https://www.calendarharvest.com/help/)
- [About](https://www.calendarharvest.com/about/)
- [Pricing](https://www.calendarharvest.com/pricing/)
- [Contact](https://www.calendarharvest.com/contact/)

Help center

# Analytics, health score and ROI

Vectorae reports on your campaigns in several places: the **Analytics** page, the **Revenue & ROI** page, a shareable ROI dashboard, and a Pipeline Health Score. This article explains how each number is calculated so you know what it does and does not tell you.

Not live yet: LinkedIn steps; Stripe checkout (paying for a plan online). Where this article describes them, they are built and tested only against simulated services. Free trials and online plan changes described here start through online checkout, which is not live yet; to start, [book a call](https://www.calendarharvest.com/contact/). Features marked off by default must be switched on by an administrator. See [known limitations](https://www.calendarharvest.com/help/known-limitations/).Last checked: 2026-10-10.

## What is the Pipeline Health Score?

A single number from 0 to 100 for one campaign, made from four parts. Each part is scored 0 to 100, then weighted:

- **Reply rate** (35%): replies divided by sends, as a percentage. A 10% reply rate scores 10 on this part.
- **Sequence completion** (25%): finished enrollments divided by all enrollments.
- **Lead freshness** (20%): full marks if your newest lead is up to 3 days old, zero at 30 days or older, sliding in between.
- **Open conversations** (20%): 10 points for each lead sitting at **Replied**, up to 100.

The total falls into a band with a short message:

- 0–40 **critical**: "Pipeline empty. Add leads immediately."
- 41–60 **low**: "Pipeline thinning. Add 10 leads this week."
- 61–80 **moderate**: "Pipeline healthy. Maintain your cadence."
- 81–100 **strong**: "Pipeline strong. Focus on open replies."

Because the reply-rate part uses the raw percentage, a campaign with a normal cold-outreach reply rate earns only a few points from it. Read the four parts, not just the total. If the calculation fails, the score shows 0 and "critical" with an error flag, so a failed measurement can be told apart from an empty pipeline.

The score is refreshed every six hours for verified and running campaigns, and recalculated live when requested. It is available on every plan. On a verified strategy's page, the **Pipeline health** card shows the score out of 100, its band and message, and the four parts. It is also available through the Vectorae API.

## What does the Analytics page show?

Choose a campaign on **Analytics** to see, among other panels: reply quality (the raw reply rate next to the positive reply rate), sequence completion, attribution ("What earned the outcomes"), and benchmarks ("How you compare"). The page also links to **Revenue & ROI**.

The positive reply rate counts only replies labelled **interested**. Replies that could not be classified are counted as unclassified, never guessed.

## How does attribution decide which message earned a meeting?

Every booked meeting, won deal and **interested** reply is credited to a message, and Vectorae always records how it decided and how sure it is:

- **Direct reply:** their reply is linked to a specific message. Confidence 1.0.
- **Thread match:** their reply is in the same email thread as one of your messages. Confidence 0.85.
- **Last touch:** nothing is linked, so the most recent message sent **before** the outcome gets the credit. This is a guess, and its confidence drops with time: 0.7 within a day, 0.55 within 3 days, 0.4 within a week, 0.25 within a month, 0.1 after that.
- **None:** nothing was sent before the outcome, so nothing is credited.

The panel groups results by step and by channel, and each group shows how many entries are confirmed. When fewer than half are confirmed, a caveat appears above the totals. Attribution is updated every 30 minutes, and **Recompute** runs it now. A message sent after the outcome is never credited.

## How are benchmarks calculated, and what are they not?

**How you compare** shows your reply rate, meeting-booked rate and bounce rate per channel next to the spread across **other accounts on this Vectorae deployment** over the last 90 days. It is not an industry statistic.

Rules that apply (defaults, set by your administrator):

- An account counts only with at least 30 sends in that group.
- A group is shown only if at least 10 accounts are in it. This can never be set below 5. Smaller groups are not stored at all.
- Only the 25th, 50th (median) and 75th percentiles are shown, rounded to half a percentage point. The number of accounts is shown as a band (10+, 25+, 100+).
- Suspended accounts are excluded.

If not enough accounts qualify, you will not see a comparison.

## How is ROI calculated on the Revenue & ROI page?

Open **Analytics**, then **Revenue & ROI**, and pick a period (30 days, 90 days, 12 months or year to date). Per campaign you see:

- **Revenue:** deals marked won, dated by close date.
- **Meetings:** distinct leads with a booked meeting in the period.
- **Cost:** costs you record with **Add cost** (data, tools, ads, or hours × rate), metered AI usage priced with your administrator's estimates, AI call minutes, and a share of account-wide costs spread by each campaign's sends.
- **Cost / meeting**, **Cost / deal**, and **ROI** = (revenue − cost) ÷ cost. ROI is blank when there is no cost.

The report uses your most common deal currency (USD if you have no deals). Amounts in other currencies are left out and counted in a notice, never converted. AI costs are in USD, so they are only included in a USD report.

## What is on the shareable ROI dashboard?

In **Settings** you can create a share link with a title, one campaign or **All campaigns**, and an expiry (default 30 days, up to 365). The link is shown once. Anyone with it can view a read-only page until it expires or you click **Revoke**. Creating a link requires a verified phone number when your deployment requires phone verification, which is the default.

It shows six numbers:

- **Meetings booked:** booked meetings from leads whose sequence reached step 3 or later. Meetings booked off steps 1 or 2 are not counted.
- **Pipeline value:** the estimated deal value of leads currently at Meeting booked, Proposal sent or Closed won (not limited to the date range). Leads at Opportunity are not included.
- **Messages sent**, and **reply rate** (unique leads who replied ÷ leads contacted).
- **Time saved:** an estimate of 15 minutes per message plus 90 minutes per meeting.
- **Revenue attributed:** value from leads that closed won.

These are based on lead statuses, so they can differ from the deal-based Revenue & ROI page. For money, the deal-based page is the more exact one.

## Related

- [Replies and the unified inbox](https://www.calendarharvest.com/help/replies-and-unified-inbox/)
- [The learning loop and A/B tests](https://www.calendarharvest.com/help/learning-loop-and-ab-tests/)
- [Deliverability and mailbox health](https://www.calendarharvest.com/help/deliverability-and-mailbox-health/)
- [Plans, limits and billing](https://www.calendarharvest.com/help/plans-limits-billing/)
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