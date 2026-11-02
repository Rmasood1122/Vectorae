Server temporarily unreachableWe can't reach Vectorae's server right now, so we can't confirm whether scheduled sends are going out. We'll reconnect automatically. [Check service status](https://www.calendarharvest.com/status/)

[Vectorae](https://www.calendarharvest.com/)

- [How it Works](https://www.calendarharvest.com/how-it-works/)
- [Compare](https://www.calendarharvest.com/compare/)
- [Help](https://www.calendarharvest.com/help/)
- [About](https://www.calendarharvest.com/about/)
- [Pricing](https://www.calendarharvest.com/pricing/)
- [Contact](https://www.calendarharvest.com/contact/)

Help center

# What is Vectorae?

Vectorae is an outbound sales system. You describe a product or a service you sell, and Vectorae researches the market, writes a go-to-market strategy, checks that strategy, finds and checks contacts, and then sends personalized outreach by email (and, where set up, WhatsApp) automatically, within compliance limits you can see.

Not live yet: LinkedIn steps; Stripe checkout (paying for a plan online). Where this article describes them, they are built and tested only against simulated services. Free trials and online plan changes described here start through online checkout, which is not live yet; to start, [book a call](https://www.calendarharvest.com/contact/). Features marked off by default must be switched on by an administrator. See [known limitations](https://www.calendarharvest.com/help/known-limitations/).Last checked: 2026-10-10.

## What does Vectorae do, start to finish?

The work happens in a fixed order. Each stage has its own help article.

1. **Intake.** You give Vectorae a name and a plain-language description of your product or service. Optionally, you describe past clients and how you won them.
2. **Research.** Vectorae runs a research pipeline for that strategy: 72 steps if you described past clients, or 144 steps if you did not. It runs once per strategy, not once per prospect.
3. **Verification.** The finished strategy is checked against 10 criteria, including a legal and compliance check. Leads cannot be sourced and outreach cannot start until the strategy is verified.
4. **Lead sourcing.** Vectorae searches for people who match the strategy, adds details about them, finds and checks their email address, and removes anyone on the suppression list.
5. **Writing and sending.** Each email is written for one specific person at the moment it is sent (a WhatsApp template's wording is fixed; only its variables are filled per person). Messages are then sent automatically from your connected Gmail account (or the deployment's WhatsApp Business number), inside send windows and daily limits.
6. **Follow-up.** Later steps go out after the delay you set. When someone replies, their sequence stops.
7. **Booking and meetings.** Vectorae has its own booking pages and a meeting room for notes and an AI summary.
8. **Learning.** Results are rolled up every night and fed into the starting point for new strategies.

## Do I have to send the messages myself?

No. Once you launch a sequence, Vectorae sends the messages automatically. Before every single send it re-checks the suppression list, the campaign and contact state, the send window, and the daily allowance. Some messages can be held for a person to approve first (see the send-review queue article). Vectorae does not reply to people on your behalf: when a prospect replies, the reply is classified and routed to you, and you write the answer.

## Does the research run for every prospect?

No. The research pipeline runs once for each strategy you create. Messages to individual prospects are written separately, at send time, from that strategy's messaging research plus what Vectorae has stored about the person.

## How does Vectorae avoid making things up?

There are several guards in the code:

- The research instructions tell the model never to invent statistics and to label estimates as assumptions. Verification check #1 then tests for this.
- Every AI-written message is checked before it is sent. Any claim about the prospect or their company (for example funding, headcount, hiring, news, or "your recent post") must be backed by data Vectorae actually stored for that lead. Unsupported claims are removed or replaced with a neutral line.
- The legal and compliance verification check can never be passed manually by a person. It has to pass on its own.

## What compliance protections are built in?

Every outreach email carries a footer with the sender identity and an unsubscribe link, plus one-click unsubscribe headers. An unsubscribe, or a reply asking to stop, suppresses the person and stops all of their sequences immediately. WhatsApp messages only go to contacts with a recorded opt-in, and a first WhatsApp touch must use a Meta-approved template. If a campaign's bounce rate goes above the threshold (3% by default, after at least 10 sends), the whole campaign pauses until a person resumes it. Vectorae gives no legal advice; you remain responsible for having a lawful basis to contact each person.

## What do the plans limit?

Each plan sets how many strategies you can have, how many leads one strategy can hold, how many steps a sequence can have, which channels you can use, and a daily AI spending limit. When the daily AI limit is used up, new strategies and resumes are refused until it resets at 00:00 UTC. See the plans article for the numbers.

## Where does Vectorae run?

Vectorae runs on a server, so sequences keep sending and follow-ups keep firing when your computer is off. Each deployment (database, workers and app) is intended to serve one customer organization. Public sign-up is open to any email address by default, so a deployment stays limited to one organization only if its administrator closes sign-up or limits it to the organization's email domains. You use it through the web app; there is also an Android app.

## Which parts are not yet verified against live services?

Vectorae connects to outside services such as Apollo, Hunter, Gmail, Meta's WhatsApp Cloud API, Calendly, Stripe and Recall.ai. These integrations were built and tested against simulated responses. They have not yet been verified end to end against the live services.

## Related

- [Who Vectorae is for, and who it is not for](https://www.calendarharvest.com/help/who-its-for-and-not-for/)
- [Getting started](https://www.calendarharvest.com/help/getting-started/)
- [The research pipeline](https://www.calendarharvest.com/help/research-pipeline/)
- [Strategy verification](https://www.calendarharvest.com/help/strategy-verification/)
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