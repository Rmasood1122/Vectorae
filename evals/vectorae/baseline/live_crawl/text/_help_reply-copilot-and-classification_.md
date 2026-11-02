Server temporarily unreachableWe can't reach Vectorae's server right now, so we can't confirm whether scheduled sends are going out. We'll reconnect automatically. [Check service status](https://www.calendarharvest.com/status/)

[Vectorae](https://www.calendarharvest.com/)

- [How it Works](https://www.calendarharvest.com/how-it-works/)
- [Compare](https://www.calendarharvest.com/compare/)
- [Help](https://www.calendarharvest.com/help/)
- [About](https://www.calendarharvest.com/about/)
- [Pricing](https://www.calendarharvest.com/pricing/)
- [Contact](https://www.calendarharvest.com/contact/)

Help center

# Reply classification and Reply Copilot

This article explains how Vectorae decides what kind of reply it received, which consequences follow automatically and which are only suggestions, and how Reply Copilot drafts answers for you to approve. What each class does to a sequence, and how the **Inbox** works, are covered in [Replies and the unified inbox](https://www.calendarharvest.com/help/replies-and-unified-inbox/).

Not live yet: LinkedIn steps; Stripe checkout (paying for a plan online). Where this article describes them, they are built and tested only against simulated services. Free trials and online plan changes described here start through online checkout, which is not live yet; to start, [book a call](https://www.calendarharvest.com/contact/). Features marked off by default must be switched on by an administrator. See [known limitations](https://www.calendarharvest.com/help/known-limitations/).Last checked: 2026-10-18.

## How does Vectorae decide what a reply means?

Every reply is read by AI, which must pick exactly one routing class: `interested`, `question`, `objection`, `not_interested`, `unsubscribe_request`, `out_of_office`, `bounce` or `automated_response`. The AI sees the sender's address, the subject and the first 4000 characters of the body. The reply text is passed to it as untrusted data, so instructions written inside a reply are not followed. The AI is told that any request to stop contacting is `unsubscribe_request`, that delivery-failure notices are `bounce`, and that absence auto-replies are `out_of_office`.

Two safety rules sit around the AI:

- If its answer is unreadable or not one of the classes, or the call fails, the reply becomes `question`. That stops the sequence and leaves the reply for a person, the safe direction.
- When the class is `interested`, `question`, `objection` or `not_interested`, a second check asks whether a machine wrote it (a no-reply sender, an auto-reply subject, ticket or "we received your message" wording). A reply judged automated is stored as `automated_response`: it stays visible, but it does not count as a reply. Your administrator can switch this check off (`reply_fraud_detection_enabled`, on by default).

On WhatsApp, a STOP message is handled by a fixed rule, not by AI: it is stored as `unsubscribe_request` and the person is suppressed.

Separately, each reply also gets a positive-reply label for reporting and a suggested next step with a draft (see article 13). Those are two further AI readings; they do not change the routing class.

## Which reply actions are automatic and which are only suggestions?

**Automatic**, as soon as the routing class is stored:

- the sequence stops, pauses or carries on, according to the class;
- an `unsubscribe_request` suppresses the person on every channel;
- a `bounce` is recorded and the campaign's bounce rate is checked;
- the positive-reply label is set.

**Only suggested**, never sent by itself:

- the suggested next step and draft that every reply gets. Approving that draft adds a note with the text and an entry on the lead's timeline; it does not send anything;
- Reply Copilot drafts (below). A person approves each one, with one narrow exception: auto-send for "send me a time" email replies, which is off unless both your administrator and you switch it on.

## What is Reply Copilot?

Reply Copilot writes a draft answer to a reply, using only your campaign's objection research, the approved claims in your library and your booking link. It is off by default: your administrator turns it on in System Settings (`reply_copilot_enabled`).

A draft is written only when all of these are true:

- the reply came by email or LinkedIn (WhatsApp and phone replies never get one);
- it is matched to one of your leads and was written by a person;
- its class is `interested`, `question` or `objection`;
- it contains no opt-out wording such as "unsubscribe", "remove me" or "please stop". This is checked with fixed rules on the prospect's new text, whatever the AI decided;
- the lead is not suppressed or excluded, and its status is still Replied, Contacted, Verified or Flagged. A lead that booked a meeting, was closed or disqualified gets no draft.

Each reply gets at most one draft, of at most 140 words. When the same lead writes again, any older draft still waiting is marked superseded. If the AI writes a draft containing a link other than your booking link or your own sending domain, a placeholder such as `[Name]`, or a forbidden phrase, Vectorae asks once more; if the second try fails, no draft is made and you answer by hand. Claims Vectorae cannot back are removed and listed on the draft.

## How do I approve, edit or discard a draft?

Open the prospect's conversation in **Inbox**. Waiting drafts appear in the **Reply Copilot** card above the conversation. You can change the text in the box, then:

- **Approve & send** (email), or **Send edited** once you changed it;
- **Copy** or **Copy edited** (LinkedIn): the text goes to your clipboard and you send it from LinkedIn yourself. Vectorae never sends LinkedIn drafts;
- **Discard**: the reply stays in "Needs reply";
- **Undo edits** puts the original draft back.

Before anything is queued, Vectorae checks the final words again. An answer longer than 5000 characters, one containing a forbidden phrase, or one with a claim it cannot back is refused, with the reason shown. An approved email is handed to the normal send path straight away: unsubscribe footer and headers, send window, daily cap and the send-review queue all apply, so it can wait. It is cancelled if, before it goes out, the prospect writes again, opts out, books a meeting, or the lead is closed. The card shows what happened, for example "sent", "queued (waits for the send window and daily cap)" or "held in the send-review queue".

## Can Reply Copilot answer replies on its own?

Only in one case. The card always shows the option **Auto-send answers to "send me a time" email replies**, but it can only be turned on when your administrator allows it (`reply_copilot_auto_send_allowed`, off by default); until then it is disabled with "An administrator has not allowed auto-send on this deployment." You also have to turn it on yourself. A draft is then sent without you only if it is an email, the prospect asked for a time, the draft contains your booking link, claim checking changed nothing, and every approval check passes. Anything else stays waiting for you. Auto-sent answers are automatic sends, so they also wait for that day's send approval (see [Daily send approval and notifications](https://www.calendarharvest.com/help/daily-send-approval-and-notifications/)).

## What does the "prospect wrote twice" option do?

When a prospect writes twice, their first reply is what stopped the sequence. By default, an answer you approve to their second reply is still sent. The founders made this a per-account choice: the option **When a prospect wrote twice, still send an approved answer to the later reply** in the **Reply Copilot** card. Turn it off and only answers to the reply that stopped the sequence are sent; an answer to the later reply is cancelled and the reply returns to "Needs reply". The off setting only ever cancels more. The card, and so this option, appears only in a conversation that has at least one draft.

## What can I do if a reply was classified wrongly?

There is no control to change a reply's routing class by hand, and correcting a label does not restart a stopped sequence. What you can do:

- **Answer the person yourself** from Gmail or LinkedIn, then use **Mark handled** in the **Inbox**. If the class was wrong, Reply Copilot may not have drafted anything, so do not wait for a draft.
- **Re-run the positive-reply label.** On the **Replies** tab of the CRM, a reply without a label shows a **Classify** button that asks the AI again. This updates the reporting label only, not the routing class.
- **A reply wrongly treated as an unsubscribe** puts the person on the suppression list. Only an administrator can remove an entry, on the **Suppression List** page of the admin area. Do this only if you are sure the person did not ask to stop.
- **Real replies filed as automated**: tell your administrator. If it keeps happening, they can switch off the automated-reply check described above.

## Related

- [Replies and the unified inbox](https://www.calendarharvest.com/help/replies-and-unified-inbox/)
- [The send-review queue and approvals](https://www.calendarharvest.com/help/send-review-queue/)
- [Daily send approval and notifications](https://www.calendarharvest.com/help/daily-send-approval-and-notifications/)
- [Personalization and claim verification](https://www.calendarharvest.com/help/personalization-and-claim-verification/)
- [Compliance (CAN-SPAM, GDPR, CASL, PDPA)](https://www.calendarharvest.com/help/compliance/)
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