---
id: sequences-and-follow-ups
title: Sequences and follow-ups
audience: customer
last_verified: 2026-10-11
owner: help-center
sources:
  - app/services/send_holds.py
  - app/services/plan_holds.py
  - app/api/sequences.py
  - app/api/sequence_reviews.py
  - app/services/sequence_engine.py
  - app/services/adversarial_review.py
  - app/services/conversion_probability.py
  - app/services/send_windows.py
  - app/services/sequence_completion.py
  - app/services/system_settings.py
  - app/workers/outreach_tasks.py
  - app/workers/calendar_tasks.py
  - app/api/webhooks.py
  - app/core/plans.py
  - app/config.py
  - app/db/models.py
  - docs/features/sequence-completion.md
  - docs/features/reengagement.md
  - frontend/src/components/strategy/OutreachPanel.tsx
  - frontend/src/lib/leadOutreach.ts
  - frontend/src/app/(app)/campaigns/page.tsx
  - app/integrations/calendly.py
  - docs/VERIFICATION_REPORT.md
  - app/services/autopilot.py
  - app/services/send_time.py
  - app/services/send_diagnostics.py
  - app/api/autopilot.py
  - app/services/launch_consent.py
  - app/services/lead_timezone.py
  - docs/features/autopilot-launch.md
  - frontend/src/components/strategy/AutopilotCard.tsx
  - frontend/src/components/strategy/BestTimeCard.tsx
---

# Sequences and follow-ups

A sequence is the series of messages Victorae sends to each lead in a strategy. This article covers how sequences are built and launched, when each step goes out, what stops or pauses a sequence, how automatic follow-ups work, and the step limits on each plan.

## What is a sequence?

A sequence belongs to one strategy and has numbered steps. Each step holds:

- a **brief**: what the message should say. It is not the final text; Victorae writes the final message for each lead at send time.
- a **delay**: how many days to wait after the previous step is sent (0 to 60).
- a **channel**: email by default. Steps can also use WhatsApp, LinkedIn or phone, if your plan includes them. A step without its own channel uses the sequence's channel.
- optionally a **variant** label, for testing different versions of a step.

## How do I create and launch a sequence in the app?

On a verified strategy's page, after finding leads, use the **2. Write the email and launch** card:

1. Enter a **Sequence name**.
2. Under **First email: what should it say?**, describe the first email: who you are, why you are writing, one clear ask.
3. Optionally add a **Follow-up email** and the **Days to wait before the follow-up** (the form starts at 3).
4. Tick the box confirming you may lawfully contact each person on the list. Victorae records who confirmed, when, and the exact wording; the launch is refused without it.
5. Press **Launch to N lead(s)**.

In the app today, this creates an email-only sequence with one or two steps. Longer sequences, and steps on other channels, are created through the Victorae API, which accepts up to 20 steps numbered 1, 2, 3 and so on with no gaps. A launch through the API must also confirm the lawful basis: send `lawful_basis_confirmed: true` and the current `lawful_basis_text_version` (the wording and its version are at `GET /launch/lawful-basis`). Without them the launch is refused with a 422 that says so.

## How many steps can I put in a sequence?

| Plan | Steps per sequence | Channels |
|---|---|---|
| Free | 5 | Gmail |
| Starter | 10 | Gmail, WhatsApp |
| Pay per meeting | 10 | Gmail, WhatsApp, LinkedIn |
| Growth | 15 | Gmail, WhatsApp, LinkedIn |
| Scale | No plan limit | Gmail, WhatsApp, LinkedIn, phone |
| Pro, Enterprise | No plan limit | Gmail, WhatsApp, LinkedIn, phone |

These are checked when the sequence is created and again at every launch, so a sequence built on a higher plan cannot be launched after a downgrade. A sequence over the limit is refused with an upgrade message. For sequences enrolled from 12 October 2026 on, messages are also checked when they come due: after a downgrade, a step on a channel your current plan does not include, or a step past your plan's step limit, is held instead of sent. The Campaigns page says how many messages are on hold and why, and the lead's message shows the reason (it starts with "plan_limit:"). Steps your plan still covers keep sending, and the sequence is not stopped. When your plan covers a held step again, for example after an upgrade, it is released automatically within about 30 minutes and goes out through the usual checks. Enrollments from before 12 October 2026, administrator accounts and Reply Copilot answers a person approved are not held this way.

## What does Victorae check when I launch?

In order, a launch is refused if:

- your phone number is not verified;
- the strategy is not `verified`, or its campaign is paused;
- the sequence is outside your plan's steps or channels;
- the sequence's pre-launch content review found a blocking problem.

The **pre-launch review** scans the sequence for spam and pressure phrases, compliance problems (for example a deceptive subject line, or a WhatsApp step without an approved template), and briefs that ask the writer to state facts about the prospect. Only blocking findings stop the launch. A workspace owner or manager can override them with a written reason of at least 10 characters. Changing the content after an override brings the review back. You can see the review under **Campaigns**, on the **Sequences** tab.

If you are an SDR in a workspace that requires approval, your first launch is sent to a manager instead: "Sent to a manager for approval. Nothing is sent until they approve."

On launch, Victorae enrolls the strategy's `verified` leads that have the address the first step needs: an email address for an email first step, a LinkedIn profile for a LinkedIn first step, and a phone number for a WhatsApp (or AI call) first step. It skips anyone suppressed or already enrolled, and schedules step 1. Only `verified` leads can be enrolled. If your sending domain has deliverability problems, the launch still goes ahead but shows a warning.

## When does each step go out?

- **Step 1** is scheduled for the best time to send at or after the moment you launch, inside the send window.
- **Each later step** is scheduled when the previous step is actually sent: its delay in days is added, then the time is moved to the best time to send inside the send window.
- The send window is business hours in the lead's own time zone, 09:00 to 17:00 by default, with weekends skipped. Victorae works out each lead's time zone from the location found for them: the country, and for the United States, Canada, Australia and Brazil the state or province. A lead with no known location uses the time zone of the strategy's target region, then your account's time zone (from your calendar settings), then the deployment's fallback zone (UTC by default). The **Why isn't this sending?** panel says how many leads use a fallback.
- **Best time to send** picks a slot within the next 72 hours (by default) in the prospect's local time. It starts from published industry patterns, which are starting assumptions, not promises: business email Tuesday to Thursday 09:00 to 11:00; LinkedIn weekday mornings and lunchtime; WhatsApp late morning or mid-afternoon. Some industries get their own window, for example restaurants and cafés 14:30 to 16:30 (between lunch and dinner service) and clinics 09:00 to 11:00. When a strategy's research finishes, Victorae adjusts these windows to that strategy's ideal customer. Once your account has sent 30 messages on a channel, your own reply rates by day and hour are blended in.
- The strategy page shows **Best time to send**: "Messages go out …" for each channel, and why. Press **Choose my own window** to set your own days and hours (only the account owner or a workspace manager can, signed in). Messages are then only ever placed inside your window, never outside it; messages already scheduled are moved into it in the background over the next few minutes. A window that never overlaps the permitted sending hours for the strategy's leads (for example a compliance rule's hours) is refused, because those messages could never be sent.
- If smart send time is turned on for the campaign, a step may be held for the campaign's best engagement window, for up to 72 hours by default. Your own window wins over it.

## Can Victorae find leads and start sending automatically when the research finishes?

Yes, if you turn on **Autopilot** for the strategy: "When research finishes, find leads, write the sequence and start outreach automatically". You can tick it on the last step of **New strategy** or later in the **Autopilot** card on the strategy page. It is never on unless you turn it on, and turning it on needs you to confirm that you have a lawful basis to contact the people it finds. Only the account owner or a workspace manager can turn it on.

When the strategy is verified, Autopilot:

1. runs its own lead search, the same way as **Find leads** (25 by default, within your plan's limits and your spend limits);
2. writes a short sequence for each channel from the strategy's own messaging research;
3. runs the same checks as **Launch** (plan, pre-launch review, sending-domain checks);
4. enrolls only the `verified` leads its own search found, each on one channel, and schedules step 1 at the best time to send.

Autopilot never contacts leads you added or verified by hand, leads from an earlier search, or people flagged by job-change tracking: those are only ever enrolled by you. Leads it found that can never be contacted (for example suppressed or excluded) are counted on the Autopilot card and are not reported as launched.

Autopilot does not skip anything. It can only be turned on while **daily send approval** is on for every channel it uses (email, LinkedIn and WhatsApp), and if daily send approval is turned off later, Autopilot pauses, tells you, and its messages wait until approval is back. Every message still waits for your daily send approval and passes every check before it is sent. If your spend limits refuse the lead search, Autopilot tells you and tries again the next day. If verification needs your review, Autopilot waits for you. If a channel is not ready (for example Gmail is not connected, no LinkedIn account is connected, or your plan does not include it), Autopilot starts the channels that are ready, tells you by email and notification what to connect, and starts the waiting channel by itself once it is ready. WhatsApp is only used for people who opted in, and only with the approved WhatsApp template you choose in the Autopilot card; until you choose one, the WhatsApp channel waits. You can turn Autopilot off at any time; to stop messages already scheduled, pause the campaign.

Every send also passes the daily cap, the suppression check and, where triggered, the send-review queue. Messages over the day's cap wait for the next day; they are not dropped.

## What stops or pauses a sequence?

| Event | Effect |
|---|---|
| The lead replies (interested, question, objection or not interested) | All of that lead's sequences stop, on every channel. |
| The lead unsubscribes or asks to stop | Suppressed everywhere; all sequences stop immediately. |
| An email bounces | The lead is dropped and their sequences stop. |
| An out-of-office reply | Sequences pause for 7 days by default, then continue. |
| A meeting is booked on Victorae's own calendar | Sequences pause. If the meeting is cancelled they resume; otherwise they resume a day after the meeting ends unless something else stopped them. |
| A meeting is booked through Calendly | Sequences stop. (The Calendly integration has not yet been verified against the live service.) |
| The lead is suppressed by the time a message is due | That message is cancelled and the sequence stops. |
| You remove the lead (**Remove**, which marks it `dropped`) | The sequence stops straight away; a removed lead is never sent to. |
| You move the lead to closed won, closed lost or disqualified (Pipeline board, CRM grid, bulk edit, a HubSpot or Salesforce deal stage) | All of that lead's sequences stop straight away, and any step still waiting (scheduled, in the send-review queue, or held by a safety check) is cancelled. A closed lead is never sent another sequence step. |
| You log a meeting outcome (**Log Meeting Outcome**) | All of that lead's sequences stop. |
| A step's channel has no address for the lead (for example a WhatsApp step and no phone number) | That message is cancelled and the sequence stops, with the reason "no address for the step's channel" (not counted as a compliance stop). |
| An automated reply (ticket receipts, no-reply bots) | Nothing changes; it is stored but not treated as a reply. |

Victorae also keeps a live estimate of each lead's chance of booking. After a lead has had 4 unanswered sends (the default), a lead judged to have gone cold may have its sequence paused ("cooling") or stopped ("archived").

A stopped sequence can never send to that lead again.

## What are automatic follow-ups?

Automatic follow-ups are separate from step delays. They catch a lead whose sequence is still active but has nothing queued, for example because a send failed permanently, a WhatsApp step needed a template, or a step was deleted.

Every 30 minutes, Victorae looks for active sequences where the last message has had no reply for longer than that step's follow-up delay (72 hours by default; you can set 1 to 2,160 hours). A sequence with a message still queued, waiting in the send-review queue, or held by a safety check is left alone. If the sequence has a next step, that step is sent. A step a reviewer rejected is never sent again; the follow-up moves on to the step after it. If it does not, Victorae writes a follow-up from the lead's own message history and your product description, on the same channel as the last message, and sends it through the normal checks. It does not write automatic follow-ups for WhatsApp, because a WhatsApp first contact needs an approved template.

No follow-up goes to a lead who has replied, or from a paused campaign. To change the setting, go to **Campaigns**, choose the strategy, open the **Sequences** tab, and use **Auto follow-up** and **after N hours of silence** under each step.

## When is a sequence complete?

When every step has been sent to a lead, their sequence is marked completed. Victorae does not keep following up after that. A separate re-engagement feature can send one more email to leads who finished without replying, but it is off for every campaign until an owner or manager turns it on.

Victorae also reports a completion rate, and lists each reason a sequence ended early. A sequence where every step was skipped (for example, an all-WhatsApp sequence to a lead without opt-in) is flagged for your attention. Skipped steps do not count as sent.

## Related
- [Personalization and claim verification](07-personalization-and-claim-verification.md)
- [Sending email through Gmail](09-email-sending-gmail.md)
- [WhatsApp outreach](10-whatsapp-outreach.md)
- [The send-review queue](12-send-review-queue.md)
- [Replies and the unified inbox](13-replies-and-unified-inbox.md)
- [Pausing, stopping and the kill switch](25-pause-stop-kill-switch.md)
