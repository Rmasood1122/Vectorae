---
id: daily-send-approval-and-notifications
title: Daily send approval and notifications
audience: customer
last_verified: 2026-10-18
owner: help-center
sources:
  - app/services/daily_send_approval.py
  - app/api/send_approval.py
  - app/workers/outreach_tasks.py
  - app/workers/network_tasks.py
  - app/workers/notification_tasks.py
  - app/services/event_bus.py
  - app/services/notifications.py
  - app/services/system_settings.py
  - frontend/src/app/(app)/send-approval/page.tsx
  - frontend/src/components/campaigns/SendApprovalBanner.tsx
  - frontend/src/lib/sendApproval.ts
  - frontend/src/lib/deeplinks.ts
  - docs/features/daily-send-approval.md
  - docs/features/autopilot-launch.md
  - docs/program/DECISIONS.md
---

# Daily send approval and notifications

Before Vectorae sends anything on its own on a given day, a person has to say yes for that channel. This article explains why, what counts as automatic, what the LinkedIn warning means, what happens when nobody decides, and how Vectorae tells you a decision is waiting. A short summary is also in [The send-review queue and approvals](12-send-review-queue.md).

## Why does Vectorae need a yes from me every day?

The founders decided that nothing Vectorae sends automatically should go out on a day when nobody chose to let it. Daily approval is a safety guard built into the point every message passes before it is sent, and it is on by default. Approval is given per channel for one day (email, LinkedIn, WhatsApp or AI phone calls), and one decision can approve several channels at once.

Automatic means anything Vectorae sends on its own schedule:

- sequence steps and automatic follow-ups;
- re-engagement messages and "not now" return messages;
- Reply Copilot answers sent by auto-send;
- LinkedIn network messages you queued with a bulk send, because they are sent later without you.

Not held, because a person already decided on that exact message:

- a message approved in the send-review queue;
- a Reply Copilot answer you approved yourself;
- a single LinkedIn network message you send now, an AI call you start with "Call now", and a meeting follow-up email you send yourself.

Anything Vectorae cannot positively identify as a person's decision is treated as automatic.

## How do I give or refuse today's approval?

Open the **Today's automatic sending** page. You reach it from the banner's **Review and decide** button, the link in the notification email, or by tapping the push notification; it has no entry in the main menu. The page shows the date, which time zone your day is counted in, and how many messages are queued today on each channel.

Tick the channels you approve and click **Approve selected channels for today**. Messages already waiting on those channels are released straight away and the page tells you how many. You can add another channel later the same day; earlier approvals stay. Or click **Decline for today**: nothing automatic is sent today, and you can still approve later the same day.

Only a workspace owner or manager can decide, and only when signed in as a person: a request made with a personal API key is refused. Other members see the state and the note "Only a workspace owner or manager can approve or decline automatic sending." Every decision is recorded with who made it and when.

## What is the LinkedIn warning I have to accept?

LinkedIn is a separate, explicit choice. Its checkbox is never ticked for you, and the page shows this warning:

"LinkedIn's User Agreement does not allow automated tools to send messages or connection requests. LinkedIn may restrict or suspend LinkedIn accounts that use automation, including Vectorae's automatic LinkedIn sending through your connected account. Do you want to proceed with automatic LinkedIn sending today?"

When you tick LinkedIn, a second checkbox appears: "I understand LinkedIn may restrict or suspend my LinkedIn account, and I want to proceed with automatic LinkedIn sending today." You cannot approve LinkedIn without it. The warning has a version number, and the version and text you saw are stored with your decision; if the warning changed after you opened the page, the approval is refused and you are asked to reload the page and read it again. Declining clears the acknowledgement, so approving LinkedIn again later that day asks again.

The warning is a risk disclosure, not legal advice. LinkedIn sending itself has not yet been checked against the live LinkedIn provider; see [LinkedIn outreach and your LinkedIn network](11-linkedin.md).

## What happens to my messages if nobody approves?

They wait. Nothing is failed, cancelled or deleted:

- Without a decision, or after a decline, each automatic message stays scheduled and moves to the next midnight in your account's time zone. Next day, Vectorae asks again.
- If you approved some channels but not this one, the same happens for that channel only.
- If Vectorae cannot check the approval (for example a database error), it holds the message and tries again in 1 hour. It never sends when in doubt.
- A held LinkedIn network message stays queued and nothing reaches LinkedIn.

While nobody approves, waiting messages build up, so the queued counts can grow from day to day. When you approve, released messages go through every normal check again: send window, daily cap, mailbox health, the send-review queue and suppression. Messages over the daily cap wait for the next day.

The approval is only asked for when a message could actually go out now. A message that is still outside the prospect's sending hours does not trigger the request. Autopilot needs daily approval to stay on; see [Sequences and follow-ups](08-sequences-and-follow-ups.md).

## How will I find out that approval is waiting?

The first time on a given day that an automatic message is due and nobody has decided, Vectorae notifies the account owner, once that day:

- **Email** to the account owner's address, subject "Approve today's automatic sending in Vectorae", with the queued counts per channel and, when LinkedIn is included, the LinkedIn warning. It is sent from Vectorae's own mail service, never from your Gmail.
- **Push notification** to the owner's phones where the Vectorae Android app is signed in and allowed to notify, titled "Approve today's automatic sending". Tapping it opens the approval page.
- **Slack**, if your account has Slack connected: the same message is also posted to Slack.
- **Banner** at the top of every app page, in the browser and in the Android app, while messages are waiting. It refreshes every 5 minutes.

Workspace managers can decide, but the email and push go to the account owner only. There are no browser or desktop notifications and no notification bell: on a computer, the email and the banner are how you find out.

Two limits: push only works if your deployment has Firebase push set up, and neither push to a real phone nor delivery of this email from the production mail service has been verified yet. Do not rely on one channel alone; the banner is always there while something waits. See [The Android app](26-mobile-app.md).

## Can daily approval be switched off or limited?

Not by individual users. These are settings for your whole Vectorae deployment, and your administrator controls them in System Settings: `daily_send_approval_enabled` (on by default) and `daily_send_approval_channels`, which lists the gated channels and by default is `email,linkedin,whatsapp,phone`. If that list contains no valid channel name, every channel is gated. When approval is off, the page shows "Not required (turned off by an admin)" and messages are not held for approval. Autopilot is the exception: it cannot be turned on without daily approval, and if approval is switched off later, Autopilot pauses and its messages wait until approval is back.

## Related
- [The send-review queue and approvals](12-send-review-queue.md)
- [Sequences and follow-ups](08-sequences-and-follow-ups.md)
- [LinkedIn outreach and your LinkedIn network](11-linkedin.md)
- [Reply classification and Reply Copilot](38-reply-copilot-and-classification.md)
- [The Android app](26-mobile-app.md)
- [Webhooks and Slack events](37-webhooks-and-slack-events.md)
- [Pausing and stopping outreach](25-pause-stop-kill-switch.md)
