---
id: booking-and-calendar
title: Booking meetings and your calendar
audience: customer
last_verified: 2026-10-28
owner: help-center
sources:
  - app/api/calendar.py
  - app/services/calendar_service.py
  - app/db/models.py
  - app/workers/calendar_tasks.py
  - app/services/sequence_engine.py
  - app/config.py
  - app/integrations/calendly.py
  - app/api/webhooks.py
  - app/workers/outreach_tasks.py
  - app/api/sequences.py
  - app/services/message_personalization.py
  - frontend/src/lib/api/outreach.ts
  - frontend/src/components/strategy/OutreachPanel.tsx
  - frontend/src/app/(app)/calendar/page.tsx
  - frontend/src/app/(app)/calendar/booking-pages/page.tsx
  - frontend/src/app/book/page.tsx
  - frontend/src/app/(app)/settings/page.tsx
  - docs/features/engagement-hub.md
---

# Booking meetings and your calendar

Prospects can book a meeting with you in two ways: through a booking page on Vectorae's own calendar, or through a Calendly link. This article explains how each works, how Vectorae stops double bookings, how time zones are handled, and what happens to a prospect's sequence when they book or cancel.

## What is the difference between Vectorae's calendar and Calendly?

- **Vectorae's own calendar:** you set your weekly availability and create booking pages inside Vectorae. Each booking is tied to your page, so Vectorae always knows whose booking it is. When a booking is cancelled, the prospect goes back into their sequence.
- **Calendly:** a scheduling link can be attached to a sequence, and Vectorae asks the writer to include it in the call to action of the emails it writes, tagged with your account. Calendly reports bookings back to Vectorae through a webhook. A Calendly booking stops the prospect's sequences for good. In the current app, the sequence form does not have a field for this link; it can be set when a sequence is created through the Vectorae API.

claude/bold-heisenberg-58xvzj
claude/epic-fermi-rmnw4c
The Calendly connection uses one API token for the whole deployment, set by whoever runs your Vectorae. In **Settings**, on the **Integrations** tab, the Calendly card shows either "API token configured" or "Not configured". Several parts of the Calendly code are marked as not yet checked against Calendly's current documentation (the sign-in method, the webhook signature header and the booking data it reads), so treat the Calendly integration as not yet verified against the live service.
The Calendly connection uses one API token for the whole deployment, set by whoever runs your Vectorae. In **Settings**, on the **Integrations** tab, the Calendly card shows either "API token configured" or "Not configured". The Calendly code was checked against Calendly's public documentation in October 2026, without a Calendly account; it has not been tested against the live service.
master

The Calendly connection uses one API token for the whole deployment, set by whoever runs your Vectorae. In **Settings**, on the **Integrations** tab, the Calendly card shows either "API token configured" or "Not configured". The Calendly code was checked against Calendly's public documentation in October 2026, without a Calendly account; it has not been tested against the live service.
master

## How do I set my availability?

Open **Calendar** in the main menu and set your **Weekly availability**. Each block is a day of the week, a start time, an end time and a time zone. The end time must be after the start time; overnight blocks are not supported. If you have no availability, your booking pages offer no times. Your Calendar page warns you: "No availability set — nobody can book you yet."

Your availability is shared by all your booking pages, because you are one person with one diary.

## How do I create a booking page?

Go to **Calendar**, then **Booking pages**, and click **Create booking page**. You set:

- **Title** (for example "Intro call"),
- **Link** – the part after `/book/`, using lowercase letters, digits and single hyphens,
- **Length** – 15, 30, 45 or 60 minutes,
- **Description** – optional,
- **Questions** – up to 10 custom questions, each optional or required.

You can pause and resume a page, and copy its link to share it. Booking pages also support a gap between meetings (0 to 120 minutes) and a maximum number of bookings per day (1 to 50). These two settings are not in the booking-page form; they can be set through the Vectorae API.

## What does a prospect see when they book?

The public page shows the page title, description, length and your questions. The prospect picks a time zone (their browser's zone is pre-selected), picks a date and time, and enters **Your name**, **Email** and optionally **Phone**, then clicks **Confirm booking**.

Times are offered from your availability for the next 30 days, and no sooner than 60 minutes from now. Both numbers are deployment settings, so your deployment may differ. The page does not reveal anything about whether the prospect is already in your CRM.

Public bookings are limited to 30 per IP address per hour.

## How does Vectorae prevent double booking?

A time is not offered if it overlaps anything already on your calendar: a live booking on **any** of your booking pages, or any meeting that is not cancelled. Times in the past, inside the minimum notice period, or on a day that has reached the page's daily cap are also hidden.

If two people try to book overlapping times at the same moment, even on two different booking pages of yours, only the first is accepted; the second gets an error asking them to pick another time. Bookings for the same person are checked one at a time against everything already on the calendar. A cancelled booking frees its time so it can be booked again; turning a cancelled booking back to confirmed is refused if that time has since been taken. One gap remains: a meeting you add by hand at the very same moment as a booking is not checked against it.

Vectorae's calendar does **not** read your Google Calendar or any other external calendar. Busy times there are not blocked unless they are also in Vectorae.

## How are time zones handled?

Your availability is stored as your local clock time plus its time zone, not converted to UTC. That means "9am" stays 9am in your zone through daylight-saving changes. Each day is converted on its own date.

The prospect sees and books times in the zone they choose. Confirmation emails show the time in the invitee's zone to the invitee and in your zone to you.

## What happens after someone books?

When a booking is made on your Vectorae page:

1. The booking appears on your calendar immediately.
2. If the invitee's email matches one of your leads, that lead is marked **Meeting booked**, a "booked" outcome is recorded for reporting, and the booking is noted in the CRM.
3. The lead's sequence is **paused**, not stopped, until one day after the meeting ends.
4. You are notified, and a meeting prep brief is requested for that lead.
5. Confirmation emails are sent to the invitee and to you.

If sending the emails fails, the booking still exists and stays on your calendar.

When a booking comes in through Calendly, the matching lead is marked **Meeting booked**, a "booked" outcome is recorded, and all the lead's sequences are **stopped**. If the Calendly link did not carry your account's tag (for example an old link, or one someone found on their own), Vectorae does not guess which account it belongs to and matches no lead.

## What happens if a booking is cancelled?

On Vectorae's calendar you can cancel a booking from the calendar ("Cancel booking"). The time is freed, a cancellation email goes to the invitee and to you, any meeting prep reminders for it are cancelled, a lead marked **Meeting booked** goes back to **Replied**, and the sequence pause made by the booking is lifted. Cancelling a meeting never lifts a different pause, such as an out-of-office hold.

When a Calendly booking is cancelled outright, the lead goes back to **Replied**. Its sequences were already stopped and are not restarted.

If the prospect **reschedules** through Calendly instead, the lead stays **Meeting booked**: Calendly reports a reschedule as a cancellation of the old time plus a new booking, and Vectorae recognises it. The reminders for the old time stop, and the new time gets its own prep brief (when meeting prep is turned on).

## Related
- [Sequences and follow-ups](08-sequences-and-follow-ups.md)
- [Replies and the unified inbox](13-replies-and-unified-inbox.md)
- [Meetings, prep briefs, the meeting room and transcripts](15-meetings-prep-room-transcripts.md)
- [CRM and integrations](16-crm-and-integrations.md)
- [Known limitations](29-known-limitations.md)
