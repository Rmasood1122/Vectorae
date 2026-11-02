Server temporarily unreachableWe can't reach Vectorae's server right now, so we can't confirm whether scheduled sends are going out. We'll reconnect automatically. [Check service status](https://www.calendarharvest.com/status/)

[Vectorae](https://www.calendarharvest.com/)

- [How it Works](https://www.calendarharvest.com/how-it-works/)
- [Compare](https://www.calendarharvest.com/compare/)
- [Help](https://www.calendarharvest.com/help/)
- [About](https://www.calendarharvest.com/about/)
- [Pricing](https://www.calendarharvest.com/pricing/)
- [Contact](https://www.calendarharvest.com/contact/)

Help center

# Workspaces, team roles and agency mode

A workspace lets other people work on your Vectorae account with a role that limits what they can do. Agency mode (the **Clients** page) is a separate feature that lets an agency keep each client's campaigns, reporting, sending domains and invoice apart. This article explains both.

Not live yet: LinkedIn steps; Stripe checkout (paying for a plan online). Where this article describes them, they are built and tested only against simulated services. Free trials and online plan changes described here start through online checkout, which is not live yet; to start, [book a call](https://www.calendarharvest.com/contact/). Features marked off by default must be switched on by an administrator. See [known limitations](https://www.calendarharvest.com/help/known-limitations/).Last checked: 2026-10-20.

## What is a workspace?

A workspace is its owner's account. Your products, strategies, leads, integrations and deals stay yours; the people you invite work on your records. Every user owns exactly one workspace, created automatically the first time they use the app, and can also be a member of other people's workspaces.

When you are a member of someone else's workspace, pick it with the **Workspace** switcher in the top bar. From then on you see and act on that owner's data, within your role. Personal things always stay your own whatever workspace is selected: your sign-in and password, your profile and theme, your API keys, your support chats, tutorials, onboarding and your own billing.

A member works under the owner's plan and is not asked to buy a plan of their own, as long as the owner has a paid plan.

## What can each role do?

There are four roles:

- **Owner** – everything, including workspace settings, white label and managing managers.
- **Manager** – everything an owner can do with the data: work leads, sequences, deals and meetings, launch campaigns, approve or decline SDR launches, manage integrations, webhooks and costs, delete things, and invite or manage SDRs and viewers. Managers cannot invite or manage other managers, and cannot change workspace settings or white label.
- **SDR** – can read everything and work leads, sequences, deals and meetings. An SDR cannot change integrations, webhooks or costs, and cannot delete anything ("Deleting needs a manager in this workspace."). Launching a campaign may need a manager's approval (see below).
- **Viewer** – read-only ("Viewers have read-only access to this workspace.").

These rules are checked by the server on every request, not just hidden in the screens.

## How do I invite someone?

Open **Team**, go to the **Members** tab, and use the **Invite** card: enter the **Email**, choose a **Role**, and press **Send invite**.

- Owners can invite any role. Managers can invite SDRs and viewers only.
- The invitation is emailed, and the link is also shown to you as **Invitation link (also emailed)**, so you can send it yourself.
- The link expires after 7 days and works once. It only works for a signed-in person using the invited email address.
- Inviting the same address again replaces the earlier link. You can also **Revoke** a pending invitation.

## How do campaign approvals work?

By default, when an SDR launches a sequence for the first time, nothing is sent. The launch is held as "pending approval" and the owner and managers are notified. They see it in **Team** › **Approvals**.

- **Approve** carries out the SDR's launch exactly as they asked.
- **Decline** lets you write "What should change? (sent to the SDR)" and press **Send decline**. The sequence goes back to draft and the SDR sees your note.
- Either way, the SDR is told the decision.
- Once a sequence has been approved, later launches of that same sequence do not need approval again.
- Approval is refused while that sequence's pre-launch review is blocked. The review findings are shown right in the request.

The owner can switch this off in **Team** › **Settings** by unticking **SDR campaign launches need a manager's approval**. Owners and managers never need approval.

## What is white label?

The owner can brand the app under **Team** › **White label**: a brand name, a logo (PNG, JPEG or WebP, up to 1 MB; SVG is refused), a primary colour, a support email and a custom domain. A custom domain must be verified with a DNS record shown on that page, and the deployment's operator also has to add the domain to the hosting. The deployment's administrator can turn white label off for everyone.

Branding changes what people see in the app and the brand name used in invitation emails. Outreach still goes out from the owner's connected Gmail account.

## Who sends the outreach when a teammate works a lead?

The workspace owner's connected accounts. Inside a workspace, a lead can be assigned to a member (the **Owner** column in the CRM grid), but that only records who works the lead. Messages are still sent from the owner's Gmail and LinkedIn accounts, written in the owner's voice profile. The person a lead is assigned to is not notified, and the grid cannot filter by assignee yet.

## What is agency mode (Clients)?

Agency mode is for an agency running outreach for several clients from one account. Open **Clients** and press **Add a client**, with a **Client name** and a **Monthly retainer**. Each client then gets:

- **Separate reporting** – sent messages, reply rate, meetings and revenue are counted only over that client's campaigns.
- **Separate sending domains** – with **Reserve a sending domain**, you list the email domains that client's campaigns may send from. Vectorae checks this at the moment of sending. A message that would go out from a domain not on the client's list is not sent; it is marked failed with a reason.
- **A billing view** – **Show this month's invoice** shows the retainer, the per-meeting fee, the number of meetings this month and the lines that add up to the total.

If a client has no sending domain reserved, there is no restriction: its campaigns can send from any connected mailbox, and the page warns you about this.

A campaign that is not filed under any client is treated as the agency's own work. The page warns that such work "is not on any invoice". Deleting a client does not delete the outreach done for it.

Creating clients, changing their prices and changing their domain lists needs a manager or the owner. Any member can read them.

## Can a client sign in and see only their own campaigns?

No. A client on the **Clients** page is a record in your account, not a login, and there is no role that limits someone to one client's campaigns. You have two options:

- **Invite the client into your workspace as a Viewer** ( **Team** › **Members** › **Invite**). They can then read everything in your workspace, including your other clients and your own work, but change nothing. Only do this if that is acceptable.
- **Let the client have a workspace of their own.** The client creates their own Vectorae account (their workspace is created automatically) and invites you, for example as a **Manager**. You then pick their workspace with the **Workspace** switcher. Their leads, campaigns, connected Gmail and plan are theirs, and they see only their own data. The **Clients** page features (reserved domains, retainer and invoice) belong to your own workspace and do not follow you into theirs.

## Do clients get their own plan, limits and report?

There are no per-client plans or limits. Every client runs on the account owner's plan, and the plan's limits (number of strategies, daily AI limit and the rest) are counted over the whole account: your own work and every client together. See [Plans, limits and billing](https://www.calendarharvest.com/help/plans-limits-billing/). The retainer and per-meeting fee on a client are what you charge that client; Vectorae does not bill them.

Each client's row on **Clients** is its report: **Sent**, **Reply rate**, **Meetings** and **Revenue** (won deals), counted over all time for that client's campaigns, with no date filter. A reply rate shows "—" until something has been sent. The invoice view counts meetings from the first day of the current month. There is no export or shareable link for a client report in the web app. For a single campaign's detail, choose it on **Analytics**.

The web form sets only the name and the monthly retainer. A client's per-meeting fee, currency (USD unless changed), contact and billing details, and pausing or archiving the client are set through the Vectorae API. Marking a client paused does not pause its campaigns; pause them with **Pause campaign**.

## What are the limits of agency mode?

- Filing a strategy under a client is currently done through the Vectorae API; the web app has no button for it yet.
- An account can connect only one Gmail mailbox. So a client's domain list can block sends from the wrong domain, but it cannot route sends between several mailboxes.
- WhatsApp uses one sender number for the whole deployment, so every client's prospects see the same WhatsApp number. Domain isolation applies to email only.
- The learning loop's playbook scores are kept per account, not per client: all of an agency account's strategies with the same pattern key feed one score, so outcomes from different clients can feed the same score. Other accounts' outcomes never do.

## Related

- [Plans, limits and billing](https://www.calendarharvest.com/help/plans-limits-billing/)
- [Send review queue](https://www.calendarharvest.com/help/send-review-queue/)
- [WhatsApp outreach](https://www.calendarharvest.com/help/whatsapp-outreach/)
- [Learning loop and A/B tests](https://www.calendarharvest.com/help/learning-loop-and-ab-tests/)
- [Account security](https://www.calendarharvest.com/help/account-security/)
- [Known limitations](https://www.calendarharvest.com/help/known-limitations/)
- [Working as an SDR in someone else's workspace](https://www.calendarharvest.com/help/guide-sdr-team-member/)

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