---
id: send-review-queue
title: The send-review queue and approvals
audience: customer
last_verified: 2026-10-11
owner: help-center
sources:
  - app/services/event_bus.py
  - app/services/send_review.py
  - app/api/send_reviews.py
  - app/workers/outreach_tasks.py
  - app/db/models.py
  - app/services/system_settings.py
  - app/api/analytics.py
  - app/services/adversarial_review.py
  - app/api/sequence_reviews.py
  - frontend/src/components/campaigns/ReviewQueuePanel.tsx
  - frontend/src/components/campaigns/SequenceReviewPanel.tsx
  - frontend/src/lib/sendReview.ts
  - frontend/src/app/(app)/campaigns/page.tsx
  - frontend/src/app/(app)/team/page.tsx
  - docs/features/send-review-queue.md
  - docs/features/workspaces.md
  - app/services/daily_send_approval.py
  - app/api/send_approval.py
  - frontend/src/app/(app)/send-approval/page.tsx
  - docs/features/daily-send-approval.md
---

# The send-review queue and approvals

Vectorae sends outreach automatically. The send-review queue is a safety net: certain messages are held, just before they would go out, until a person approves or rejects them. This article explains what gets held, how to approve or reject, and how the queue relates to pausing and to the other approval steps in Vectorae.

## Which messages are held for review?

A message is checked after its text has been written and right before it would be sent. It is held if any of these four rules applies:

- **Prospect objected before** – the prospect's most recent reply was classified as an objection.
- **Deal has stalled** – the prospect has an open deal whose expected close date has passed, or Vectorae has marked the prospect as "cooling".
- **Executive / VIP contact** – the prospect's job title matches a fixed list: founder, co-founder, owner, CEO, COO, CFO, CTO, CMO, CRO, "chief …", president, managing director, partner, VP or vice president, "head of", board member, or chair.
- **Tone or spam check flagged the copy** – the written message trips Vectorae's tone or spam rules (the same rules used in the pre-launch review).

The queue covers email, WhatsApp and LinkedIn messages. AI phone calls are not held, because a live call cannot be read and approved in advance.

## Do I approve automatic sending every day?

Yes. On top of the review queue, Vectorae asks the account owner (or a workspace owner or manager) to approve its **automatic** sending once per day, per channel: email, LinkedIn, WhatsApp and AI phone calls. Until today's approval exists, automatic messages are not sent. They wait in the queue, they are not failed or deleted, and they go out after approval through every normal check.

Automatic means anything Vectorae sends on its own schedule: sequence steps, automatic follow-ups, re-engagement and "not now" return messages, and Reply Copilot answers sent by auto-send. A message a person already approved one by one, in this review queue or as an approved Reply Copilot answer, is not held again.

When the first automatic message of the day is ready to go and nobody has decided yet, Vectorae emails the account owner and sends a push notification to the phone app, once per day. Every page of the app also shows a banner with today's queued counts. Open **Today's automatic sending** (`/send-approval`), tick the channels you approve and click **Approve selected channels for today**, or click **Decline for today**. You can still approve later the same day after declining.

An approval lasts until midnight in your account's time zone. That time zone comes from your calendar availability; if none is set, the day is counted in UTC and the page says so. Approving LinkedIn is a separate choice with its own warning: LinkedIn may restrict or suspend accounts that use automation, so you are asked to confirm you want to proceed. Every decision is recorded with who made it, when, which channels, the queued counts, and the version of the LinkedIn warning that was shown. Your administrator can turn this requirement off in System Settings (`daily_send_approval_enabled`, on by default).

## Can the queue or a rule be turned off?

Yes, but not by individual users. The queue as a whole and each of the four rules are switched on or off by your Vectorae administrator in System Settings. All five settings are **on** by default.

If your prospects are mostly founders or executives, the VIP rule would hold nearly every message. The administrator setting for that rule says to turn it off in that case.

If Vectorae cannot read these settings, it treats the queue as on. If the review check itself fails with an error, the message is sent rather than held, so a fault in the queue does not silently stop all your outreach. The queue sits on top of the normal compliance checks; it does not replace them.

## Where do I find messages waiting for approval?

Go to **Campaigns** and open the **Review** tab. The queue covers all your campaigns, oldest first. You can filter by **Pending**, **Approved** or **Rejected**. When nothing is waiting, you see "Nothing is waiting for approval."

When a message is held, the account owner is sent a notification titled "A message to [name] needs approval", linking to the Review tab.

Each item shows who it is for, their title and company, the step number and channel, how long it has been waiting, and a badge for each rule that applied. The most serious rule is also named in the item's headline. An item still pending after more than 3 days shows a **Going stale** badge, because the copy may no longer fit the timing.

## How do I approve a message?

Read the subject and message. You can edit both before approving. Then click **Approve & send** (or **Approve edit & send** if you changed anything). A note is optional.

What you approve is exactly what is sent. Vectorae saves the text at the moment the message is held and, after approval, sends that saved text (or your edit). It does not rewrite the message after you approve it.

Approval puts the message back in the normal send queue, and it goes out on the next send run ("Approved — it will send on the next tick").

## How do I reject a message, and what happens next?

Click **Reject**, type a note, then click **Confirm reject**. The note must be at least 5 characters, so the reason is recorded.

A rejected message is never sent. Only that one message is cancelled. The prospect stays enrolled and the sequence moves on: the next step is scheduled as if the rejected step had been skipped (it does not count as sent), or, if it was the last step, the sequence is marked completed. The rejected step is never tried again, including by automatic follow-ups. To stop contacting the person, stop or pause them separately.

While a message waits in the queue, Vectorae does not create another copy of that step, so approving it later sends it once.

A review can be decided only once. Trying to approve or reject an item that was already decided returns an error instead of overwriting the earlier decision.

If the prospect's sequence stops while a message is waiting (they reply, unsubscribe, bounce, are removed or closed, and so on), the waiting message is cancelled and its review closes by itself. It is listed under **Rejected**, marked **Closed by the system** (not a person's rejection), with the stop reason as the note (for example "closed by system: enrollment stopped: unsubscribed") and no reviewer named. It no longer waits under **Pending**.

## Does approval skip the other safety checks?

No. An approved message goes back through the full send process. At the moment it is actually sent, Vectorae checks again that:

- the campaign is still active (not paused),
- the prospect is still enrolled and not stopped or paused,
- the prospect is not on the suppression (do-not-contact) list,
- channel rules still allow it (for example WhatsApp opt-in and template approval),
- the current compliance rules and the send window allow it,
- the daily sending limits allow it.

If any of these fails, the approved message is not sent at that time, just like any other message.

## How does the queue work with pausing a campaign?

Pausing a campaign stops everything in it from sending, including messages you have already approved. The send run skips paused campaigns entirely, and the send step checks the campaign state again before sending. When you resume the campaign, approved messages are sent through the normal checks.

Held messages stay in the queue while a campaign is paused. Approving one while the campaign is paused does not send it until the campaign is active again. See [Pause, stop and the kill switch](25-pause-stop-kill-switch.md) for all the ways to pause.

## Is this the same as "Pre-send review" or Team approvals?

No. Vectorae has three separate checks with similar names:

- **Send-review queue** (this article): checks one message, at send time, for the four rules above.
- **Pre-send review** on a sequence: checks the sequence's content before it can be launched. It looks for spam and tone problems, compliance problems (for example removing the unsubscribe footer or a misleading "Re:" subject), and claims about prospects. "Block" findings stop the launch until the content is fixed or an owner or manager overrides it with a written reason of at least 10 characters. Changing the content after an override brings the check back.
- **Team approvals**: in a workspace that requires approval, an SDR's first launch of a sequence waits for an owner or manager to approve it under **Team**, **Approvals**.

## Related
- [Sequences and follow-ups](08-sequences-and-follow-ups.md)
- [Replies and the unified inbox](13-replies-and-unified-inbox.md)
- [Compliance](20-compliance.md)
- [Workspaces and agency mode](24-workspaces-and-agency-mode.md)
- [Pause, stop and the kill switch](25-pause-stop-kill-switch.md)
