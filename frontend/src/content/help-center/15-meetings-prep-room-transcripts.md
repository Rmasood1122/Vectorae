---
id: meetings-prep-room-transcripts
title: Meetings, prep briefs, the meeting room and transcripts
audience: customer
last_verified: 2026-10-11
owner: help-center
sources:
  - app/services/recipient_guards.py
  - app/workers/calendar_tasks.py
  - app/api/meetings.py
  - app/db/models.py
  - app/services/system_settings.py
  - app/services/meeting_practice.py
  - app/services/roleplay.py
  - app/integrations/recall.py
  - frontend/src/components/leads/MeetingPrepPanel.tsx
  - frontend/src/components/leads/CallScriptPanel.tsx
  - frontend/src/components/leads/RoleplayPanel.tsx
  - frontend/src/components/leads/LogOutcomeDialog.tsx
  - frontend/src/components/meetings/MeetingRoom.tsx
  - frontend/src/app/(app)/meetings/detail/page.tsx
  - frontend/src/components/meetings/PlatformLauncher.tsx
  - frontend/src/components/meetings/RecordingControl.tsx
  - docs/features/meeting-prep.md
  - docs/features/meeting-transcripts.md
  - docs/features/meeting-practice.md
  - docs/features/engagement-hub.md
  - FEATURES.md
---

# Meetings, prep briefs, the meeting room and transcripts

Once a prospect books, Victorae helps you prepare for the call, run it, and follow up. This article covers the prep brief, reminders, the call script and practice calls, the meeting room with live notes, recorded transcripts, the AI summary, and logging the outcome.

## What is a meeting prep brief?

When a meeting is booked (through Calendly or a Victorae booking page), Victorae writes a prep brief for that lead. You can also start one yourself from the lead's **Meeting Prep** tab with **Generate prep brief**. Writing one takes about a minute. When it is ready, the owner gets a notification, and a Slack message if Slack is connected.

The brief includes:

- **Your opening 60 seconds** – a short script for the start of the call.
- **Why they booked** – the message and reply that led to the booking, quoted.
- **Lead profile** – copied from your records, not written by AI.
- **Company overview**, **Company news** and **Recent LinkedIn activity** (their last three posts, when Victorae has them).
- **Likely pain points** – labelled as hypotheses to confirm on the call.
- **Likely objections**, **Talking points**, **Discovery questions**, **Competitive landscape**, **Recommended next steps** and **Deal structure**.

You can copy the brief as Markdown. Your Victorae administrator can turn automatic briefs off in System Settings; they are on by default.

## Will I get reminders before the call?

Yes, by default. Victorae sends a day-before reminder once the call is within 24 hours, and a 1-hour reminder whose text is your opening script. Reminders go out at the first check after that point, and checks run every 30 minutes by default, so the 1-hour reminder can arrive up to half an hour later than the hour mark. Each is sent at most once. Cancelling the booking stops both, and a reschedule sets them up again. Your administrator can switch either reminder off.

## What are the call script, checklist and practice calls?

Under the brief you get **Your call script**: an opening, discovery questions, objection answers, a close and notes. It is built from the brief and labelled **Suggested** until you edit it. Your edits are yours: regenerating the brief never overwrites the script.

The **Pre-meeting checklist** has four items: brief generated, script reviewed, at least 3 objections prepared, and practised. It is worked out fresh each time from what exists. Practice only blocks the checklist if you choose **Require a rehearsal before this call** for that meeting; it is off by default.

**Practice a call** starts a roleplay where the AI plays the prospect, using the pains from the brief and the objections they have actually raised. Choose how hard they should be (easy, realistic or hostile), click **Start the call**, and type your lines. A rehearsal is limited to 40 turns. Click **End and get feedback** to get five scores (overall, discovery, objection handling, tone, close), what worked, what to improve, and the objections you did not handle. A session with fewer than two of your own lines is not scored. The practice panel is available even when a lead has no prep brief yet, so you can rehearse before a meeting is booked.

## How does the meeting room work?

Open **Meetings** in the main menu to see upcoming and past meetings, then **Open room**. In the room you can:

- Click **Open in Google Meet**, **Open in Zoom** or **Open in Microsoft Teams** (or **Open in the meeting link** for any other link). It opens in the meeting tool; it is not embedded. You can also use the copy button next to the link.
- Click **Start meeting** to start the timer, and **End meeting** when you finish.
- Type **Live notes**, which save as you go. After you end the meeting, you can review and add action items under the AI summary.

A meeting can be on Google Meet, Zoom, Microsoft Teams, or any other link you paste. Victorae can create Google Meet and Zoom links for you, but **these two integrations have never been tested against the live Google and Zoom services**. If creating the link fails, the meeting is still created and you can paste a link yourself. Teams has no integration; you paste the link. Pasting your own link is the only verified path today.

A meeting nobody ended is closed automatically 6 hours after its scheduled end, without a summary.

## How is the AI summary made?

When you click **End meeting**, Victorae writes a summary from what exists, usually your notes: a summary, key points, next steps, action items and sentiment. Your own notes are never changed by the AI. If you regenerate the summary, action items you already ticked stay ticked.

When the meeting is completed, the summary is filed as a note on the lead in the CRM. Action items that are yours (not the client's) can move the lead's "next action" date earlier, never later.

## Can Victorae record and transcribe the call?

There is a **Record & transcribe** button in the meeting room, which sends a recording bot from a service called Recall.ai to the call. Please read these limits first:

- **Not verified against the live service.** No Recall.ai credentials exist in any Victorae environment, and parts of the integration (how it signs in and how the bot is created) are marked as not yet checked against Recall's current documentation.
- **Your administrator must set it up** under Admin › Integrations › Recall.ai. Until it is, sending a bot fails, and the transcript webhook is closed.
- The meeting needs a join link. Without one the button is disabled ("Add a join link to send a recording bot.").

When a transcript arrives, it replaces the notes-only summary with one built from the transcript and your notes. Victorae waits up to 60 minutes after the meeting for it by default (an administrator setting); a late transcript is still accepted. Transcripts are capped at 250,000 characters. You can search a transcript and export it as PDF or TXT.

## Do I need consent to record a call?

Victorae does **not** collect recording consent. The bot records everyone on the call. Some places require every participant to agree before a call is recorded, and data-protection rules such as GDPR may apply. The app reminds you: "Tell everyone on the call that it is being recorded." It is your responsibility to get any consent required where you and your participants are; Victorae does not enforce it. This is not legal advice.

## How do I log the outcome and follow up?

On the lead's page, use **Log meeting outcome**. Pick an outcome, add notes, and for a closed-won outcome optionally a deal value. Victorae records the outcome first: it updates the lead's status, adds a CRM activity and note, records the result for reporting, creates a won deal for closed-won, and stops every live sequence for that lead.

It then writes a follow-up email and saves it as a **Gmail draft**. It is never sent automatically. You edit it and send it yourself, from Victorae or from Gmail. Both when drafting and when sending from Victorae, the same contact checks as every outreach message apply: the lead must not have been removed, and the suppression list (email, phone and LinkedIn profile), your exclusion lists when Guardrails is on, a recorded job change (the person has left the company), and an opt-out on the person's earlier record are all checked. Customer and open-deal exclusion entries, which the meeting itself creates, do not block the follow-up, but every other entry for the person does, including a do-not-contact entry on their company, domain or address. A blocked follow-up shows the reason and cannot be sent. Once the cause is gone (for example you removed the exclusion), click **Check again**; if the checks pass, the draft is saved to Gmail. If the checks cannot run, nothing is sent and you can try again. Saving drafts needs Gmail's compose permission; if you connected Gmail before this feature existed, reconnect it. Until then the draft text is kept so you can copy it.

## Related
- [Booking meetings and your calendar](14-booking-and-calendar.md)
- [Replies and the unified inbox](13-replies-and-unified-inbox.md)
- [CRM and integrations](16-crm-and-integrations.md)
- [Email sending through Gmail](09-email-sending-gmail.md)
- [Privacy, data retention and deletion](21-privacy-data-retention-deletion.md)
- [Known limitations](29-known-limitations.md)
