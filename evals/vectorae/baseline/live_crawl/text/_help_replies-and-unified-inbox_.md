Server temporarily unreachableWe can't reach Vectorae's server right now, so we can't confirm whether scheduled sends are going out. We'll reconnect automatically. [Check service status](https://www.calendarharvest.com/status/)

[Vectorae](https://www.calendarharvest.com/)

- [How it Works](https://www.calendarharvest.com/how-it-works/)
- [Compare](https://www.calendarharvest.com/compare/)
- [Help](https://www.calendarharvest.com/help/)
- [About](https://www.calendarharvest.com/about/)
- [Pricing](https://www.calendarharvest.com/pricing/)
- [Contact](https://www.calendarharvest.com/contact/)

Help center

# Replies and the unified inbox

When a prospect replies by email, WhatsApp or LinkedIn, Vectorae stores the reply, classifies it, and changes what happens next in their sequence. By default Vectorae does not reply to a person automatically; the optional Reply Copilot is the exception (see below). The **Inbox** gathers every reply into one conversation per prospect so you can see who is waiting for an answer.

Not live yet: LinkedIn steps; Stripe checkout (paying for a plan online). Where this article describes them, they are built and tested only against simulated services. Free trials and online plan changes described here start through online checkout, which is not live yet; to start, [book a call](https://www.calendarharvest.com/contact/). Features marked off by default must be switched on by an administrator. See [known limitations](https://www.calendarharvest.com/help/known-limitations/).Last checked: 2026-10-11.

## How does Vectorae pick up replies?

- **Email:** Vectorae checks your connected Gmail accounts for new replies on a schedule. The default is every 30 minutes; your deployment can change this.
- **WhatsApp and LinkedIn:** replies arrive through the providers' webhooks.

An email reply is matched to a prospect first by the email thread, then by the sender's address. Matching only looks at prospects that belong to the account whose Gmail received the reply, so another customer's reply can never change your leads.

## What happens to the sequence when someone replies?

Each reply is classified by AI into one of these routing classes, and the sequence reacts:

- **Interested, question, objection or not interested:** the prospect's sequence stops, on every channel, and the lead is marked **Replied**. The lead's owner is notified.
- **Unsubscribe request** (for example "remove me" or "stop emailing me"): treated exactly like clicking the unsubscribe link. The prospect is suppressed straight away and every sequence they are in stops.
- **Out of office:** the sequence is paused, not stopped, and picks up again after 7 days by default (configurable by your deployment).
- **Bounce** (a delivery-failure notice): recorded as a bounce, the sequence stops, the lead is dropped, and the campaign's bounce rate is checked. On LinkedIn a bounce has no meaning and is only recorded.
- **Automated response** (receipts, helpdesk tickets, no-reply bots): stored, but treated as not a reply. The sequence neither stops nor moves on.

If the AI's answer cannot be read, the reply is treated as a **question**. That stops the sequence and leaves it for you to look at, which is the safe direction.

A reply on one channel stops the whole sequence, including steps on other channels, because stopping applies to the prospect's enrollment, not to one channel.

## Does Vectorae answer replies for me?

Not by default. Replies are classified and routed, and a person writes the answer. The exception is Reply Copilot, which is off unless your administrator turns it on: it drafts an answer that a person approves before it is sent, and only if automatic sending is also allowed can an answer go out without a person. See [Reply Copilot and reply classification](https://www.calendarharvest.com/help/reply-copilot-and-classification/).

What it does do is suggest. A second AI pass reads each reply and decides what you should do next, in one of four categories:

- **Buying signal** – interest, a question about how it works, or a request for a call or pricing.
- **Objection** – a specific concern such as price or an existing supplier.
- **Not now** – a timing issue, not a rejection.
- **Wrong person** – they are not the buyer or name someone else.

It writes a suggested draft of under 150 words onto the reply. The draft is never sent by Vectorae; you read it, edit it, and send your answer yourself. For a buying signal, the sequence is lined up so that the next step would be step 3, but its status is not changed, so nothing sends until a person acts.

## What is the positive reply label?

Every reply also gets a label for reporting: **interested**, **objection**, **not now**, **unsubscribe** or **neutral**, with a confidence score and a one-line reason. Unsubscribe requests and machine mail (bounces, out of office, automated responses) are labelled by fixed rules. Everything else is labelled by AI, reading the reply together with the message it answers.

If the AI call fails, the reply is left **unclassified** instead of being guessed. This label feeds the positive reply rate in your campaign analytics. The ordinary reply rate is still shown next to it.

## What happens when someone says "not now"?

A reply labelled **not now** creates a re-engagement plan that records their reason in their own words and a return date. If they named a date, that date is used. Otherwise the wait depends on the reason: 60 days for timing or a project, 90 for budget or headcount, 120 for priority, 180 for a contract, and the configured default (90 days) when no reason is given.

When the date arrives, Vectorae first re-checks that coming back is still appropriate. If the lead has since been dropped (for example after a bounce), disqualified, closed as won or lost, moved on to a meeting, opportunity or proposal, or put on the suppression list, the plan is cancelled with the reason recorded. Otherwise you are notified. Sending a message automatically at that point is off by default. If your administrator turns it on, a return message is scheduled on the prospect's earlier sequence and goes through every normal send check (suppression, exclusions, send window, daily cap and the review queue). It is allowed past the stop that the "not now" reply caused, and only that stop. It is cancelled instead if the prospect has written again since, if the plan was cancelled, if automatic sending was switched off in the meantime, or if coming back is no longer appropriate. Until the message goes out, you can still cancel the plan ( **Never** on the lead's page); that cancels the message too. Sending it does not restart the old sequence.

## How does the Inbox work?

Open **Inbox** in the main menu. Each row is **one prospect**, not one channel: an email reply and a LinkedIn reply from the same person appear in one conversation. Opening a row shows the full timeline of messages sent and received on every channel, the same timeline as on the lead's page.

There are three filters:

- **Needs reply** – threads with at least one reply from a person that has not been marked handled. These are listed **oldest first**, so the longest-waiting replies are at the top.
- **All** – every thread, newest first.
- **Cleared** – threads you have marked handled, newest first.

Machine mail such as bounces, out-of-office replies and no-reply bots is filed in the conversation but never shows as needing a reply. A thread shows an **Overdue** badge when its reply has waited more than two days, and a "waiting" count when a prospect sent more than one unanswered message. The Inbox menu item shows how many people are waiting on a reply (shown as "99+" above 99), refreshed every minute. The **Needs reply** list shows each waiting person once, however many messages they sent.

## What does "Mark handled" do?

**Mark handled** clears a thread from "Needs reply". It sends nothing. It only records that someone has dealt with the conversation, including when you answered from Gmail directly or by phone, or decided no answer was needed. If you cleared a thread by mistake, open it under **Cleared** and click **Put back**.

Sending the next sequence step does not clear a thread. Only a person marking it handled does. If a prospect replies again after you cleared their thread, the new reply needs a reply of its own.

## Related

- [Sequences and follow-ups](https://www.calendarharvest.com/help/sequences-and-follow-ups/)
- [WhatsApp outreach](https://www.calendarharvest.com/help/whatsapp-outreach/)
- [LinkedIn outreach and your LinkedIn network](https://www.calendarharvest.com/help/linkedin/)
- [The send-review queue and approvals](https://www.calendarharvest.com/help/send-review-queue/)
- [Analytics, health score and ROI](https://www.calendarharvest.com/help/analytics-health-score-roi/)
- [Compliance](https://www.calendarharvest.com/help/compliance/)

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