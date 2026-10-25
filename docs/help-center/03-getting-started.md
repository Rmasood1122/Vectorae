---
id: getting-started
title: Getting started with Victorae
audience: customer
last_verified: 2026-10-18
owner: help-center
sources:
  - app/api/auth.py
  - app/services/auth.py
  - app/services/email_verification.py
  - app/services/network_guard.py
  - app/api/onboarding.py
  - app/services/identity.py
  - app/services/phone_verification.py
  - app/api/products.py
  - app/api/strategies.py
  - app/api/schemas.py
  - app/core/plans.py
  - app/core/billing_catalog.py
  - app/core/rate_limiting.py
  - app/services/billing.py
  - app/api/billing.py
  - app/api/sequences.py
  - app/workers/outreach_tasks.py
  - app/config.py
  - BUILD_BLOCKERS.md
  - frontend/src/app/(auth)/signup/page.tsx
  - frontend/src/app/(auth)/check-email/page.tsx
  - frontend/src/app/(app)/onboarding/verify/page.tsx
  - frontend/src/app/pricing/page.tsx
  - frontend/src/lib/post-login.ts
  - frontend/src/app/(app)/strategies/page.tsx
  - frontend/src/app/(app)/strategies/new/page.tsx
  - frontend/src/app/(app)/settings/page.tsx
  - docs/VERIFICATION_REPORT.md
  - frontend/src/components/shell/nav.ts
  - frontend/src/components/strategy/OutreachPanel.tsx
  - frontend/src/components/strategy/ReverifyStrategyButton.tsx
  - frontend/src/components/settings/DeliverabilityCard.tsx
  - frontend/src/lib/settings-tabs.ts
  - frontend/src/app/(app)/calendar/page.tsx
  - frontend/src/app/(app)/calendar/booking-pages/page.tsx
  - frontend/src/app/(app)/send-approval/page.tsx
  - frontend/src/app/(app)/campaigns/page.tsx
  - frontend/src/app/(app)/inbox/page.tsx
  - frontend/src/lib/conversation.ts
  - app/services/daily_send_approval.py
---

# Getting started with Victorae

This article walks you through your first session: creating an account, verifying your email and phone, choosing a plan, and launching your first strategy. Email verification is required before you can use anything; the phone step and the plan choice can be put off, with the limits described below.

## How do I create an account?

Open the sign-up page, enter your email address and a password of at least 8 characters, and press **Create account**.

A few things can stop a sign-up:

- **Sign-up is closed or limited on your deployment.** Each Victorae deployment can turn public sign-up off, or allow only certain email domains. By default it is open to any address. If it is closed you will see "Sign-up is closed on this Victorae deployment. Ask your administrator for an account." If your domain is not allowed, you will see a similar message about approved email domains.
- **You are on a VPN, proxy, Tor or hosting network.** On production deployments, Victorae refuses sign-up and sign-in from these networks and asks you to try again from your normal network.
- **Too many attempts.** Sign-up and other account requests are limited to 10 per 15 minutes from one network address.
- **The address already has an account.** Sign in instead.

## How do I verify my email address?

Right after sign-up, Victorae emails you a verification link and shows a **Check your email** screen. Click the link in the email to activate your account. The link expires after 24 hours.

Until you click it, your account exists but cannot use any features. If you sign in before verifying, you are sent back to the **Check your email** screen.

If nothing arrives:

1. Check your spam folder.
2. On the **Check your email** screen, press **Resend verification email**. A new link replaces the old one.
3. If the screen says the email could not be sent, your account was still created. Use the resend button.

For your privacy, the resend screen always answers the same way ("If that address has an unverified account, a new link is on its way"), whether or not the address has an account. Victorae's system emails come from noreply@calendarharvest.com by default.

## Why does Victorae ask where I am and for my phone number?

New accounts go through a short, two-step verification screen after the email link.

**Step 1: Where are you based?** You choose the country you are personally in, and whether you are signing up as an individual or a company. A company also gives the country it is registered or operating in, and optionally its name. Victorae compares your stated country with where your connection appears to come from. A mismatch never blocks you; it only flags your account for an administrator to review.

**Step 2: Verify your phone.** Enter your mobile number with its country code (for example +44 20 7946 0958) and press **Send code**. Victorae texts you a 6-digit code.

- The code expires after 10 minutes.
- After 5 wrong attempts the code is locked. Ask for a new one.
- You can request a new code after 60 seconds, and up to 5 codes per hour.

You can press **Do this later**. Until your phone is verified you can explore and research, including running strategies, but you cannot launch outreach, choose a paid plan or share dashboards.

The text-message service behind this step (Twilio) has not yet been verified against the live service. If a production deployment has deliberately no text-message service, Victorae cannot send codes, so it does not ask for one: the phone step is skipped and nothing is blocked until a phone is verified. If a text-message service is set up but misconfigured, the check stays on and codes do not arrive; contact your administrator or support. In both cases administrators see a warning at the top of the admin panel explaining what to fix.

## Do I need a plan?

Not to start. An account without a paid plan uses the Free plan's limits (for example 3 strategies, a $10 daily AI limit, and email only). Right after you sign in, if your account has no paid plan, Victorae shows you the **Pricing** page ("Choose a plan to start using Victorae"); it does not send you back there later. Each monthly tier starts with a 14-day free trial. Choosing a plan requires a verified phone number. The payment integration (Stripe) has not yet been verified against the live service; if checkout is not available on your deployment, contact support. Members of a workspace whose owner has a plan do not need their own. See the plans article for what each plan includes.

## How do I connect Gmail?

Go to **Settings**, open the **Integrations** tab, and press **Connect** on the Gmail card. Google asks you to approve access. Email outreach cannot be sent until a Gmail account is connected. The Gmail article covers this in detail.

## How do I create my first product and strategy?

Go to **Strategies** and press **New strategy**. The form has four short steps:

1. **What do you want clients for?** Give a name, describe the product or skill (what it does, who it is for, what it costs), and choose **A product** or **A skill / service**.
2. **Do you have any past clients for this?** Choose **Yes — build on proven patterns** or **No — research the market from scratch**.
3. **Past clients** (only if you said yes). For each client, describe who they were and how you won them. You can add more than one.
4. **Ready to launch.** Review the flow and press **Launch pipeline**.

If you added past clients, Victorae runs a 72-step research pipeline anchored on them. If not, it runs 144 steps (72 strategy plus 72 go-to-market). Each time you complete this form, Victorae creates a new product and a new strategy for it.

## What happens after I press Launch pipeline?

You land on the strategy's page. The research runs on Victorae's servers, so you can close the tab; the strategy page shows research progress as steps done out of the total for each pipeline. When research finishes, the 10 verification checks run.

A strategy may be refused at launch if:

- you have reached your plan's number of strategies (for example, 3 on the Free plan);
- your plan's daily AI limit is used up (it resets at 00:00 UTC);
- you have already started 2 strategies in the past hour.

## What do I do once the strategy is verified?

On the verified strategy's page, use **1. Find leads** to source and check contacts, then **2. Write the email and launch** to start outreach. Launching needs a verified phone number, and emails only go out once a Gmail account is connected.

## What should I do in my first week?

Victorae sets no timetable: research time depends on the AI service, and replies depend on your prospects. This is the order that takes you from sign-up to a first booked meeting, with where each step lives:

1. **Verify your email** with the link from the **Check your email** screen.
2. **Verify your phone** when asked, or press **Do this later**. You need it before launching outreach.
3. **Choose a plan** on **Pricing**, or stay within the Free plan's limits.
4. **Connect Gmail**: **Settings**, **Integrations** tab.
5. **Check your sending domain**: **Settings**, **Deliverability** tab, **Email health** card. Fix SPF, DKIM and DMARC problems before sending.
6. **Set up booking**: in **Calendar**, set your **Weekly availability**, then create a page under **Booking pages**.
7. **Start a strategy**: **Strategies**, **New strategy**. A clear product description gives better research.
8. **Wait for `verified`.** If the strategy ends as `needs_human_review`, use **Re-verify** or pass the failed checks manually.
9. **Find leads** in the **1. Find leads** card on the strategy page. Start small.
10. **Launch** from the **2. Write the email and launch** card.
11. **Approve sending each day** on **Today's automatic sending** when asked, and clear any held messages in **Campaigns**, **Review** tab.
12. **Answer replies** in **Inbox** (**Needs reply**) and send interested prospects your booking page link. When they book through your booking page, the lead moves to the meeting-booked stage (**Booked** on the **Pipeline** page).

## Related
- [The research pipeline](04-research-pipeline.md)
- [Strategy verification](05-strategy-verification.md)
- [Lead sourcing and email verification](06-lead-sourcing-and-email-verification.md)
- [Sending email through Gmail](09-email-sending-gmail.md)
- [Booking meetings and your calendar](14-booking-and-calendar.md)
- [Deliverability and mailbox health](19-deliverability-and-mailbox-health.md)
- [Your product profile and ideal customer profile](33-product-profile-and-icp.md)
- [Plans, limits and billing](22-plans-limits-billing.md)
- [Account security](23-account-security.md)
