---
id: annual-billing-and-plan-changes
title: Annual billing and changing your plan
audience: customer
last_verified: 2026-10-18
owner: help-center
sources:
  - app/core/billing_catalog.py
  - app/services/billing.py
  - app/services/system_settings.py
  - app/services/plan_holds.py
  - app/core/plans.py
  - app/api/sequences.py
  - app/api/strategies.py
  - app/api/leads.py
  - app/workers/celery_app.py
  - docs/features/annual-billing.md
  - docs/program/DECISIONS.md
  - frontend/src/app/pricing/page.tsx
  - frontend/src/app/pricing/PricingClient.tsx
  - frontend/src/lib/productFacts.ts
  - frontend/src/content/product-facts.json
  - frontend/src/lib/pricing.ts
  - frontend/src/components/billing/PlanCard.tsx
---

# Annual billing and changing your plan

This article explains the yearly way to pay for Victorae, what changes when you move to a higher or lower plan, and what happens to money you have already paid and to campaigns that are already running. The limits of each plan are in [Plans, limits and billing](22-plans-limits-billing.md); they are not repeated here.

## Can I pay once a year, and does it cost less?

Yes, where your deployment offers it. The annual option is switched off by default (the System Settings flag `annual_billing_enabled` starts off), and an administrator turns it on. When it is off, the **Pricing** page shows no **Monthly** / **Annual** toggle and an annual checkout is refused with "Annual billing is not offered right now."

An annual plan costs 10 times the monthly price, billed once a year, so you pay for 10 months and get 12. The toggle says **Annual · 2 months free**. Compared with paying monthly for 12 months, you save:

| Tier | Saving per year |
|---|---|
| Starter | $298 |
| Growth | $698 |
| Scale | $1,398 |
| Enterprise | $2,998 |

An annual plan has exactly the same limits as the monthly plan of the same tier, gets the same once-per-account 14-day free trial, and renews automatically every year until you cancel. There is no annual version of Pay per meeting or Free. Victorae never moves you to annual by itself; only you can choose it, at checkout. Before an annual plan renews, the account owner gets an email 30 days ahead with the amount and the date.

## Can I buy or change a plan online right now?

Not yet. Online payment through Stripe is marked "simulated only": it has been built and tested against a simulated payment service, not against the live one. While that is the case, the **Pricing** page shows the prices but, instead of a checkout button, it says "Online checkout opens soon. To start now, book a call." with a **Book a call** button.

So today, starting a paid plan, switching between monthly and annual, or moving to another tier goes through a conversation with the Victorae team rather than a checkout page. The rules in the rest of this article describe what the product does once online checkout is open, and they are what the billing code applies to any subscription that exists.

On a production deployment where payments are not set up at all, choosing a plan fails with a message saying payments are not configured.

## When does an upgrade or a downgrade take effect?

Moving up and moving down work the same way: you start a checkout for the new plan, and the change happens as soon as that checkout is completed. There is no option to schedule a change for the end of your current period.

- Your account gets the new plan's limits straight away.
- The subscription being replaced is cancelled at once, so you are never billed for two plans.
- If you leave the checkout page without finishing, nothing changes and you keep your current plan.
- The free trial is once per account. If you are in a trial and switch, the trial of the old plan ends when the new checkout is completed, and the new plan does not get a second 14-day trial.
- If you choose the plan you are already on, you see "You are already on this plan."

If you would rather go down a level only when the period you paid for ends, cancel instead (**Profile**, **Plan** card, **Cancel plan**). You keep the current plan until that date, then the account returns to Free, and you can choose a new plan from there.

## What happens to the money I already paid if I change plans part-way through?

Victorae does not refund or credit the unused part of a period when you switch. The Pricing page warns you of this before checkout: "The unused part of the period you already paid for is not refunded or credited (prorated changes are not available online yet)." No refunds are issued by the product itself.

For an annual plan, this matters more, so a paid year is protected:

- Once an annual year has been paid, you cannot start a checkout for a different plan online. You see: "Your annual plan is paid until (date). Changing it before then needs a prorated refund, which is not available online yet - contact support, or cancel it and choose a new plan when the year ends."
- During the free trial of an annual plan nothing has been paid, so you can switch like a monthly customer.
- If a checkout you opened earlier is completed after the year was paid, both are kept: your annual plan stays, the new subscription will not renew, and the **Plan** card says the team will contact you about a refund or a change.
- Cancelling a paid year stops the next renewal, and you keep the plan until the year ends. A year already paid is not refunded.

If you need a mid-year change, [contact support](30-contact-support-and-status.md).

## What happens to my strategies, sequences and scheduled sends after a downgrade?

Nothing you built is deleted or cancelled. The lower plan's limits apply from the moment the plan changes, at the points where they are checked:

- **Strategies:** all your existing strategies stay. Creating a new one is refused while you have as many as the new plan allows or more.
- **Leads:** leads you already have stay. Sourcing more for a strategy that is at or over the new limit is refused.
- **Sequences:** a sequence is not stopped, but one that has more steps or uses a channel the new plan does not include cannot be launched again.
- **Scheduled messages:** each one is checked again when it comes due. A message on a channel your plan no longer includes, or a step past your plan's step limit, is held, not sent and not deleted. Its reason starts with "plan_limit:". Steps your plan still covers keep sending.
- **After an upgrade:** a background check runs every 30 minutes and releases held messages the plan covers again. They then go through all the usual checks before sending.

Messages in sequences enrolled before 12 October 2026 are not held this way. Your daily AI limit also changes to the new plan's. Details are in [Plans, limits and billing](22-plans-limits-billing.md) and [Sequences and follow-ups](08-sequences-and-follow-ups.md).

## Related
- [Plans, limits and billing](22-plans-limits-billing.md)
- [Sequences and follow-ups](08-sequences-and-follow-ups.md)
- [Pause, stop and the kill switch](25-pause-stop-kill-switch.md)
- [Known limitations](29-known-limitations.md)
- [Contact support and status](30-contact-support-and-status.md)
