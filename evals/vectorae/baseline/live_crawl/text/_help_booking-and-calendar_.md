Server temporarily unreachableWe can't reach Vectorae's server right now, so we can't confirm whether scheduled sends are going out. We'll reconnect automatically. [Check service status](https://www.calendarharvest.com/status/)

[Vectorae](https://www.calendarharvest.com/)

- [How it Works](https://www.calendarharvest.com/how-it-works/)
- [Compare](https://www.calendarharvest.com/compare/)
- [Help](https://www.calendarharvest.com/help/)
- [About](https://www.calendarharvest.com/about/)
- [Pricing](https://www.calendarharvest.com/pricing/)
- [Contact](https://www.calendarharvest.com/contact/)

Help center

# Booking meetings and your calendar

Prospects can book a meeting with you in two ways: through a booking page on Vectorae's own calendar, or through a Calendly link. This article explains how each works, how Vectorae stops double bookings, how time zones are handled, and what happens to a prospect's sequence when they book or cancel.

Not live yet: LinkedIn steps; Stripe checkout (paying for a plan online). Where this article describes them, they are built and tested only against simulated services. Free trials and online plan changes described here start through online checkout, which is not live yet; to start, [book a call](https://www.calendarharvest.com/contact/). Features marked off by default must be switched on by an administrator. See [known limitations](https://www.calendarharvest.com/help/known-limitations/).Last checked: 2026-10-28.

## What is the difference between Vectorae's calendar and Calendly?

- **Vectorae's own calendar:** you set your weekly availability and create booking pages inside Vectorae. Each booking is tied to your page, so Vectorae always knows whose booking it is. When a booking is cancelled, the prospect goes back into their sequence.
- **Calendly:** a scheduling link can be attached to a sequence, and Vectorae asks the writer to include it in the call to action of the emails it writes, tagged with your account. Calendly reports bookings back to Vectorae through a webhook. A Calendly booking stops the prospect's sequences for good. In the current app, the sequence form does not have a field for this link; it can be set when a sequence is created through the Vectorae API.

The Calendly connection uses one API token for the whole deployment, set by whoever runs your Vectorae. In **Settings**, on the **Integrations** tab, the Calendly card shows either "API token configured" or "Not configured". The Calendly code was checked against Calendly's public documentation in October 2026, without a Calendly account; it has not been tested against the live service.

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

- [Sequences and follow-ups](https://www.calendarharvest.com/help/sequences-and-follow-ups/)
- [Replies and the unified inbox](https://www.calendarharvest.com/help/replies-and-unified-inbox/)
- [Meetings, prep briefs, the meeting room and transcripts](https://www.calendarharvest.com/help/meetings-prep-room-transcripts/)
- [CRM and integrations](https://www.calendarharvest.com/help/crm-and-integrations/)
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