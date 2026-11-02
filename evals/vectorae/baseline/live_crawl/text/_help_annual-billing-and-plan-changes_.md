Server temporarily unreachableWe can't reach Vectorae's server right now, so we can't confirm whether scheduled sends are going out. We'll reconnect automatically. [Check service status](https://www.calendarharvest.com/status/)

[Vectorae](https://www.calendarharvest.com/)

- [How it Works](https://www.calendarharvest.com/how-it-works/)
- [Compare](https://www.calendarharvest.com/compare/)
- [Help](https://www.calendarharvest.com/help/)
- [About](https://www.calendarharvest.com/about/)
- [Pricing](https://www.calendarharvest.com/pricing/)
- [Contact](https://www.calendarharvest.com/contact/)

Help center

# Annual billing and changing your plan

This article explains the yearly way to pay for Vectorae, what changes when you move to a higher or lower plan, and what happens to money you have already paid and to campaigns that are already running. The limits of each plan are in [Plans, limits and billing](https://www.calendarharvest.com/help/plans-limits-billing/); they are not repeated here.

Not live yet: LinkedIn steps; Stripe checkout (paying for a plan online). Where this article describes them, they are built and tested only against simulated services. Free trials and online plan changes described here start through online checkout, which is not live yet; to start, [book a call](https://www.calendarharvest.com/contact/). Features marked off by default must be switched on by an administrator. See [known limitations](https://www.calendarharvest.com/help/known-limitations/).Last checked: 2026-10-18.

## Can I pay once a year, and does it cost less?

Yes, where your deployment offers it. The annual option is switched off by default (the System Settings flag `annual_billing_enabled` starts off), and an administrator turns it on. When it is off, the **Pricing** page shows no **Monthly** / **Annual** toggle and an annual checkout is refused with "Annual billing is not offered right now."

An annual plan costs 10 times the monthly price, billed once a year, so you pay for 10 months and get 12. The toggle says **Annual · 2 months free**. Compared with paying monthly for 12 months, you save:

| Tier | Saving per year |
| --- | --- |
| Starter | $298 |
| Growth | $698 |
| Scale | $1,398 |
| Enterprise | $2,998 |

An annual plan has exactly the same limits as the monthly plan of the same tier, gets the same once-per-account 14-day free trial, and renews automatically every year until you cancel. There is no annual version of Pay per meeting or Free. Vectorae never moves you to annual by itself; only you can choose it, at checkout. Before an annual plan renews, the account owner gets an email 30 days ahead with the amount and the date.

## Can I buy or change a plan online right now?

Not yet. Online payment through Stripe is marked "simulated only": it has been built and tested against a simulated payment service, not against the live one. While that is the case, the **Pricing** page shows the prices but, instead of a checkout button, it says "Online checkout opens soon. To start now, book a call." with a **Book a call** button.

So today, starting a paid plan, switching between monthly and annual, or moving to another tier goes through a conversation with the Vectorae team rather than a checkout page. The rules in the rest of this article describe what the product does once online checkout is open, and they are what the billing code applies to any subscription that exists.

On a production deployment where payments are not set up at all, choosing a plan fails with a message saying payments are not configured.

## When does an upgrade or a downgrade take effect?

Moving up and moving down work the same way: you start a checkout for the new plan, and the change happens as soon as that checkout is completed. There is no option to schedule a change for the end of your current period.

- Your account gets the new plan's limits straight away.
- The subscription being replaced is cancelled at once, so you are never billed for two plans.
- If you leave the checkout page without finishing, nothing changes and you keep your current plan.
- The free trial is once per account. If you are in a trial and switch, the trial of the old plan ends when the new checkout is completed, and the new plan does not get a second 14-day trial.
- If you choose the plan you are already on, you see "You are already on this plan."

If you would rather go down a level only when the period you paid for ends, cancel instead ( **Profile**, **Plan** card, **Cancel plan**). You keep the current plan until that date, then the account returns to Free, and you can choose a new plan from there.

## What happens to the money I already paid if I change plans part-way through?

Vectorae does not refund or credit the unused part of a period when you switch. The Pricing page warns you of this before checkout: "The unused part of the period you already paid for is not refunded or credited (prorated changes are not available online yet)." No refunds are issued by the product itself.

For an annual plan, this matters more, so a paid year is protected:

- Once an annual year has been paid, you cannot start a checkout for a different plan online. You see: "Your annual plan is paid until (date). Changing it before then needs a prorated refund, which is not available online yet - contact support, or cancel it and choose a new plan when the year ends."
- During the free trial of an annual plan nothing has been paid, so you can switch like a monthly customer.
- If a checkout you opened earlier is completed after the year was paid, both are kept: your annual plan stays, the new subscription will not renew, and the **Plan** card says the team will contact you about a refund or a change.
- Cancelling a paid year stops the next renewal, and you keep the plan until the year ends. A year already paid is not refunded.

If you need a mid-year change, [contact support](https://www.calendarharvest.com/help/contact-support-and-status/).

## What happens to my strategies, sequences and scheduled sends after a downgrade?

Nothing you built is deleted or cancelled. The lower plan's limits apply from the moment the plan changes, at the points where they are checked:

- **Strategies:** all your existing strategies stay. Creating a new one is refused while you have as many as the new plan allows or more.
- **Leads:** leads you already have stay. Sourcing more for a strategy that is at or over the new limit is refused.
- **Sequences:** a sequence is not stopped, but one that has more steps or uses a channel the new plan does not include cannot be launched again.
- **Scheduled messages:** each one is checked again when it comes due. A message on a channel your plan no longer includes, or a step past your plan's step limit, is held, not sent and not deleted. Its reason starts with "plan\_limit:". Steps your plan still covers keep sending.
- **After an upgrade:** a background check runs every 30 minutes and releases held messages the plan covers again. They then go through all the usual checks before sending.

Messages in sequences enrolled before 12 October 2026 are not held this way. Your daily AI limit also changes to the new plan's. Details are in [Plans, limits and billing](https://www.calendarharvest.com/help/plans-limits-billing/) and [Sequences and follow-ups](https://www.calendarharvest.com/help/sequences-and-follow-ups/).

## Related

- [Plans, limits and billing](https://www.calendarharvest.com/help/plans-limits-billing/)
- [Sequences and follow-ups](https://www.calendarharvest.com/help/sequences-and-follow-ups/)
- [Pause, stop and the kill switch](https://www.calendarharvest.com/help/pause-stop-kill-switch/)
- [Known limitations](https://www.calendarharvest.com/help/known-limitations/)
- [Contact support and status](https://www.calendarharvest.com/help/contact-support-and-status/)

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