Server temporarily unreachableWe can't reach Vectorae's server right now, so we can't confirm whether scheduled sends are going out. We'll reconnect automatically. [Check service status](https://www.calendarharvest.com/status/)

[Vectorae](https://www.calendarharvest.com/)

- [How it Works](https://www.calendarharvest.com/how-it-works/)
- [Compare](https://www.calendarharvest.com/compare/)
- [Help](https://www.calendarharvest.com/help/)
- [About](https://www.calendarharvest.com/about/)
- [Pricing](https://www.calendarharvest.com/pricing/)
- [Contact](https://www.calendarharvest.com/contact/)

Help center

# Getting started with Vectorae

This article walks you through your first session: creating an account, verifying your email and phone, choosing a plan, and launching your first strategy. Email verification is required before you can use anything; the phone step and the plan choice can be put off, with the limits described below.

Not live yet: LinkedIn steps; Stripe checkout (paying for a plan online). Where this article describes them, they are built and tested only against simulated services. Free trials and online plan changes described here start through online checkout, which is not live yet; to start, [book a call](https://www.calendarharvest.com/contact/). Features marked off by default must be switched on by an administrator. See [known limitations](https://www.calendarharvest.com/help/known-limitations/).Last checked: 2026-10-18.

## How do I create an account?

Open the sign-up page, enter your email address and a password of at least 8 characters, and press **Create account**.

A few things can stop a sign-up:

- **Sign-up is closed or limited on your deployment.** Each Vectorae deployment can turn public sign-up off, or allow only certain email domains. By default it is open to any address. If it is closed you will see "Sign-up is closed on this Vectorae deployment. Ask your administrator for an account." If your domain is not allowed, you will see a similar message about approved email domains.
- **You are on a VPN, proxy, Tor or hosting network.** On production deployments, Vectorae refuses sign-up and sign-in from these networks and asks you to try again from your normal network.
- **Too many attempts.** Sign-up and other account requests are limited to 10 per 15 minutes from one network address.
- **The address already has an account.** Sign in instead.

## How do I verify my email address?

Right after sign-up, Vectorae emails you a verification link and shows a **Check your email** screen. Click the link in the email to activate your account. The link expires after 24 hours.

Until you click it, your account exists but cannot use any features. If you sign in before verifying, you are sent back to the **Check your email** screen.

If nothing arrives:

1. Check your spam folder.
2. On the **Check your email** screen, press **Resend verification email**. A new link replaces the old one.
3. If the screen says the email could not be sent, your account was still created. Use the resend button.

For your privacy, the resend screen always answers the same way ("If that address has an unverified account, a new link is on its way"), whether or not the address has an account. Vectorae's system emails come from noreply@calendarharvest.com by default.

## Why does Vectorae ask where I am and for my phone number?

New accounts go through a short, two-step verification screen after the email link.

**Step 1: Where are you based?** You choose the country you are personally in, and whether you are signing up as an individual or a company. A company also gives the country it is registered or operating in, and optionally its name. Vectorae compares your stated country with where your connection appears to come from. A mismatch never blocks you; it only flags your account for an administrator to review.

**Step 2: Verify your phone.** Enter your mobile number with its country code (for example +44 20 7946 0958) and press **Send code**. Vectorae texts you a 6-digit code.

- The code expires after 10 minutes.
- After 5 wrong attempts the code is locked. Ask for a new one.
- You can request a new code after 60 seconds, and up to 5 codes per hour.

You can press **Do this later**. Until your phone is verified you can explore and research, including running strategies, but you cannot launch outreach, choose a paid plan or share dashboards.

The text-message service behind this step (Twilio) has not yet been verified against the live service. If a production deployment has deliberately no text-message service, Vectorae cannot send codes, so it does not ask for one: the phone step is skipped and nothing is blocked until a phone is verified. If a text-message service is set up but misconfigured, the check stays on and codes do not arrive; contact your administrator or support. In both cases administrators see a warning at the top of the admin panel explaining what to fix.

## Do I need a plan?

Not to start. An account without a paid plan uses the Free plan's limits (for example 3 strategies, a $10 daily AI limit, and email only). Right after you sign in, if your account has no paid plan, Vectorae shows you the **Pricing** page ("Choose a plan to start using Vectorae"); it does not send you back there later. Each monthly tier starts with a 14-day free trial. Choosing a plan requires a verified phone number. The payment integration (Stripe) has not yet been verified against the live service; if checkout is not available on your deployment, contact support. Members of a workspace whose owner has a plan do not need their own. See the plans article for what each plan includes.

## How do I connect Gmail?

Go to **Settings**, open the **Integrations** tab, and press **Connect** on the Gmail card. Google asks you to approve access. Email outreach cannot be sent until a Gmail account is connected. The Gmail article covers this in detail.

## How do I create my first product and strategy?

Go to **Strategies** and press **New strategy**. The form has four short steps:

1. **What do you want clients for?** Give a name, describe the product or skill (what it does, who it is for, what it costs), and choose **A product** or **A skill / service**.
2. **Do you have any past clients for this?** Choose **Yes — build on proven patterns** or **No — research the market from scratch**.
3. **Past clients** (only if you said yes). For each client, describe who they were and how you won them. You can add more than one.
4. **Ready to launch.** Review the flow and press **Launch pipeline**.

If you added past clients, Vectorae runs a 72-step research pipeline anchored on them. If not, it runs 144 steps (72 strategy plus 72 go-to-market). Each time you complete this form, Vectorae creates a new product and a new strategy for it.

## What happens after I press Launch pipeline?

You land on the strategy's page. The research runs on Vectorae's servers, so you can close the tab; the strategy page shows research progress as steps done out of the total for each pipeline. When research finishes, the 10 verification checks run.

A strategy may be refused at launch if:

- you have reached your plan's number of strategies (for example, 3 on the Free plan);
- your plan's daily AI limit is used up (it resets at 00:00 UTC);
- you have already started 2 strategies in the past hour.

## What do I do once the strategy is verified?

On the verified strategy's page, use **1\. Find leads** to source and check contacts, then **2\. Write the email and launch** to start outreach. Launching needs a verified phone number, and emails only go out once a Gmail account is connected.

## What should I do in my first week?

Vectorae sets no timetable: research time depends on the AI service, and replies depend on your prospects. This is the order that takes you from sign-up to a first booked meeting, with where each step lives:

01. **Verify your email** with the link from the **Check your email** screen.
02. **Verify your phone** when asked, or press **Do this later**. You need it before launching outreach.
03. **Choose a plan** on **Pricing**, or stay within the Free plan's limits.
04. **Connect Gmail**: **Settings**, **Integrations** tab.
05. **Check your sending domain**: **Settings**, **Deliverability** tab, **Email health** card. Fix SPF, DKIM and DMARC problems before sending.
06. **Set up booking**: in **Calendar**, set your **Weekly availability**, then create a page under **Booking pages**.
07. **Start a strategy**: **Strategies**, **New strategy**. A clear product description gives better research.
08. **Wait for \`verified\`.** If the strategy ends as `needs_human_review`, use **Re-verify** or pass the failed checks manually.
09. **Find leads** in the **1\. Find leads** card on the strategy page. Start small.
10. **Launch** from the **2\. Write the email and launch** card.
11. **Approve sending each day** on **Today's automatic sending** when asked, and clear any held messages in **Campaigns**, **Review** tab.
12. **Answer replies** in **Inbox** ( **Needs reply**) and send interested prospects your booking page link. When they book through your booking page, the lead moves to the meeting-booked stage ( **Booked** on the **Pipeline** page).

## Related

- [The research pipeline](https://www.calendarharvest.com/help/research-pipeline/)
- [Strategy verification](https://www.calendarharvest.com/help/strategy-verification/)
- [Lead sourcing and email verification](https://www.calendarharvest.com/help/lead-sourcing-and-email-verification/)
- [Sending email through Gmail](https://www.calendarharvest.com/help/email-sending-gmail/)
- [Booking meetings and your calendar](https://www.calendarharvest.com/help/booking-and-calendar/)
- [Deliverability and mailbox health](https://www.calendarharvest.com/help/deliverability-and-mailbox-health/)
- [Your product profile and ideal customer profile](https://www.calendarharvest.com/help/product-profile-and-icp/)
- [Plans, limits and billing](https://www.calendarharvest.com/help/plans-limits-billing/)
- [Account security](https://www.calendarharvest.com/help/account-security/)

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