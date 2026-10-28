# Engagement Hub

Status: **implemented, full test suite green (1,066 backend / 276 frontend),
static export builds.** Two integrations — Google Calendar/Meet and Zoom — are
written against their documented APIs but have **never been exercised against
the live services**, because no credentials exist yet. See
[Outstanding](#outstanding).

Four features that together close the loop between "we sent a message" and
"we had the call":

| # | Feature | Where it lives |
|---|---------|----------------|
| 1 | Automated follow-up | `app/workers/outreach_tasks.py`, migration `0020` |
| 2 | Self-built calendar | `app/api/calendar.py`, `app/services/calendar_service.py`, migration `0021` |
| 3 | Meetings, notes, transcripts, AI summary | `app/api/meetings.py`, `app/services/meeting_ai.py`, migration `0022` |
| 4 | Calendar + meetings in the CRM | `app/services/crm_events.py`, `app/services/sequence_engine.py`, `app/workers/calendar_tasks.py` |

---

## Feature 1 — Automated follow-up

### What it does

Every 30 minutes, `check_followup_due` sweeps for `SequenceEnrollment` rows
that are ACTIVE, whose last SENT message is older than that step's
`followup_delay_hours`, where the lead has not replied since — and sends the
next touch. If the sequence has no next step, the follow-up brief is generated
by Claude from the lead's own message and outcome history plus the product
brief, and sent on the same channel.

### The one decision worth reading

**The sweep skips any enrollment that already has a pending message**, and that
guard is the whole design.

`schedule_next_step` already queues step N+1 the moment step N sends. A sweep
that also queued one would send the same prospect the same message twice —
the kind of failure nobody sees in staging and every customer sees in their
recipient's inbox. So what the sweep actually picks up is the population the
engine currently drops on the floor:

* a send that failed permanently (`FAILED`) — the enrollment sits ACTIVE with
  nothing queued behind it, forever;
* a WhatsApp step that fell to `NEEDS_TEMPLATE` because the 24-hour window
  closed or a template lost approval — documented, expected, and currently
  terminal for that lead;
* a step deleted or renumbered after its message was scheduled.

In each case a real prospect was contacted, said nothing, and the system
quietly stopped. That is what this fixes.

**COMPLETED enrollments are deliberately out of scope.** Sweeping them would
auto-DM every lead who ever finished a sequence, on every sweep, with no upper
bound on how many follow-ups one lead receives. That is a mass-send this
system has no user-facing control for; if it is wanted it needs its own cap,
its own opt-in and its own row in the compliance story.

### Compliance

An automatic send goes through `send_message_impl` like any other: suppression
re-checked, campaign state, enrollment state, send window, daily cap, WhatsApp
opt-in and template approval. There is no bypass flag, here or anywhere.

The resulting `OutcomeEvent.SENT` carries `meta_json.source = "auto_followup"`.
An ordinary scheduled send's outcome meta is byte-identical to what M3 wrote,
so the M8 learning loop's existing aggregates are untouched.

### Idempotency is two layers

`followup:{enrollment_id}:{step_no}` in Redis (2h TTL) stops two workers acting
on the same overdue enrollment. The message row's `SCHEDULED -> SENDING` claim
stops a double transmit if the lock fails open. **The lock fails OPEN** when
Redis is unreachable: failing closed would silently stop every follow-up in the
product during an outage with nothing surfacing that it had, and the row claim
is the actual guarantee.

### Configuration

`BEAT_SWEEP_MINUTES` (30; the sweep runs in every worker burst), `FOLLOWUP_LOCK_TTL_SECONDS` (7200),
and per step `followup_enabled` / `followup_delay_hours` via
`POST /sequences/{id}/steps/{n}/followup-settings`.

### `followup_status` in the CRM grid

Computed server-side in `crm_service.followup_status_for_leads` — four batched
queries per page, never one per row — because the answer depends on the lead's
messages, outcomes and the step's delay, none of which the grid row carries.
`due` is computed the same way the sweep computes it, so the badge and the
automation cannot disagree about who is overdue.

---

## Feature 2 — The self-built calendar

`app/integrations/calendly.py` is untouched and keeps working. The difference
is where a booking **lands**. A Calendly booking arrives as one webhook for the
whole deployment, carrying the invitee's email and no account identity — which
is why `calendly.py` has to smuggle the tenant id through a `utm_content`
parameter and treat "we cannot tell" as "do not match". A booking made against
a page in this schema already knows its page, and the page knows its owner, so
ownership is a foreign key rather than an inference.

### Double-booking is prevented by a constraint, not a query

`calendar_bookings.slot_key` holds the slot's UTC start while a booking is
live and is `NULL` once it is cancelled. `UNIQUE (booking_page_id, slot_key)`
therefore blocks a second live booking of the same slot, and lets a cancelled
slot be re-booked — NULLs do not collide on PostgreSQL or SQLite.

The obvious alternative, a partial unique index (`WHERE status <> 'cancelled'`),
is PostgreSQL-only, which would mean the test suite exercising a constraint
production does not have and vice versa.

The endpoint still checks `slot_is_offered` first, but that check is for
turning "you booked a time I never offered" into a 409 — it is not the race
guard for the same page.

**Across a host's pages** (send-path-gaps fix, 2026-10-11) the UNIQUE cannot
help: a 30-minute intro at 10:30 and a 60-minute deep dive at 10:00 have
different pages and different starts. Each booking write now takes a row lock
on the host's `users` row (`calendar_service.lock_host_calendar`, `SELECT ...
FOR NO KEY UPDATE` -- serialises booking writes for one host without
blocking foreign-key inserts that reference the user) and only then runs the overlap check, so concurrent bookings for
one host are serialised and the second sees the first. Reactivating a
cancelled booking runs the same lock + `host_conflict` check. Residual window:
hand-made meetings do not take the lock. An exclusion constraint would close
it but is PostgreSQL-only. See the section comment in calendar_service.py.

### Slots are computed, never stored

A materialised `calendar_slots` table loses twice over: every availability edit
would have to regenerate a month of rows and reconcile them against existing
bookings, and the same Tuesday morning is four 15-minute slots on one page and
one 60-minute slot on another. There is no single set of slots to store.

A slot is unofferable when it is in the past or inside
`CALENDAR_MIN_NOTICE_MINUTES`, when it overlaps anything on the host's whole
calendar (every live booking on **any** of their pages, plus every
non-cancelled meeting — a host with two booking pages is still one person), or
when the page has hit its per-day cap for that date.

### Time zones

Availability rows store **wall-clock** times plus the IANA zone they were
written in. "I take calls at 9am" is a fact about the user's morning; stored as
UTC it would drift by an hour twice a year and start offering 8am or 10am on
its own. Conversion happens per offered day, so DST resolves against the date
being booked. There is a test that pins this across the October 2026 change.

### The public endpoints

Two, and only two, unauthenticated routes:
`GET /calendar/booking-pages/{slug}/slots` and
`POST /calendar/booking-pages/{slug}/book` (plus `/public` for the page's own
title and questions). The booking response deliberately does **not** echo
`lead_id`: doing so would turn the endpoint into an oracle for "is this address
in your CRM?".

`RATE_LIMIT_PUBLIC_BOOKING` (30/IP/hour) caps the writes. The slots endpoint is
deliberately not limited — it is a read, and the page calls it on every date
click.

---

## Feature 3 — Meetings

A meeting can come from a booking or be created by hand (`booking_id` is
nullable — the prospect proposed a time over email, or the call predates this
feature). It carries its own `lead_id` so the CRM's Meetings tab is one indexed
lookup rather than a LEFT JOIN that resolves only the booked half of the rows.

`start_at`/`end_at` are what was **scheduled**; `actual_start_at`/`actual_end_at`
are when Start and End were pressed. The summary prompt uses the actual
duration, because "booked 60 minutes, ran 12" is a signal about the call.

### The transcript endpoint is the security-sensitive one

`POST /meetings/{id}/transcript` is authenticated by HMAC-SHA256 over the raw
body (`MEETING_RECORDING_WEBHOOK_SECRET`), not by JWT: the caller is a browser
extension or a recording provider, neither of which holds a user token.

**With no secret configured it answers 503, not 200.** A transcript is the
verbatim content of a private sales call, and an unauthenticated write path to
it that appears by default the moment the feature ships is not a trade-off
worth making. It is also capped at 250,000 characters per meeting.

### The AI summary never touches the human's notes

`raw_notes` is what the user typed. `ai_notes`, `summary`, `key_points`,
`next_steps`, `action_items` and `sentiment` are the model's. Regenerating a
summary merges action items rather than replacing them, so a box somebody has
already ticked stays ticked.

`generate_meeting_summary` never raises — it returns the five-key shape with
empty values and an `error`. Raising would mean a model hiccup at the end of a
call both loses the summary *and* leaves the meeting stuck `in_progress`,
because the endpoint that ends a meeting is the one that queues the summary.

### Platform creation fails soft

`POST /meetings` with `platform=google_meet` asks Google for a Calendar event
(the only way to mint a Meet link). If Google declines, the meeting is still
created with a `platform_error` in the 201 response. Refusing to record a
meeting the user has actually scheduled, because a third party was
unavailable, leaves nowhere to attach their notes when the call happens anyway.

### The join panel is not an iframe

Google Meet, Zoom and Teams all send `X-Frame-Options: DENY` on their join
pages, so an embed renders a blank box — worse than no embed, because it looks
broken rather than absent. `PlatformLauncher` gives every platform the same two
affordances: open in the tool, and copy the raw link (shown in full, for the
moment on a call when the prospect cannot get in).

---

## Feature 4 — In the CRM

* A booking writes **one** `OutcomeEvent.BOOKED` (the learning loop computes
  rates from row counts) and **one** `CrmActivity` (the UI's audit trail).
  Those tables stay separate for the reason migration `0019` documents.
* A booking **PAUSES** the enrollment; it does not stop it. The M3 rule is that
  a booked meeting is a hard stop, and that is right for Calendly, where
  nothing reliably tells us a meeting fell through. We own this row, so a
  cancellation puts the lead back where it was instead of stranding a warm lead
  in a state the engine defines as irreversible. `resume_after_meeting_cancelled`
  only resumes pauses **this** feature made, so a cancelled meeting cannot
  un-pause an out-of-office hold.
* A completed meeting files its summary as a CRM **note**, because the note
  panel is where a user looks before their next touch on a lead.
* Only **our** action items become tasks. An item the client owns is
  information; turning it into a to-do is how a task list becomes noise.
* There is no `crm_tasks` table and this did not add one. The CRM's task
  surface is `crm_lead_meta.next_action_at` — the grid's "Next action" column,
  which is what people filter and sort on. It is only ever moved **earlier**,
  so a summary generated after a call cannot push out a reminder set for
  tomorrow morning.

---

## Deviations from the brief

Three, all forced by things already true about this codebase. Each is
implemented the closest way that works.

### 1. Table names are plural

The brief spells them `calendar_booking_page`, `calendar_booking`, `meeting`,
`meeting_participant`. Every other table in this schema is plural, and a schema
that is plural except in one corner is a trap for anyone writing a raw query.
The **model** names match the brief exactly (`CalendarBookingPage`,
`CalendarBooking`, `Meeting`, `MeetingParticipant`) and those are what
application code touches.

### 2. `/book/[slug]` is a static route that reads the slug from the URL

The brief specifies `app/book/[slug]/page.tsx` and
`app/(app)/meetings/[id]/page.tsx`. `next.config.js` sets `output: 'export'` —
it is what Capacitor bundles into the APK — and a static export can only emit
routes it can enumerate at build time. A dynamic segment needs
`generateStaticParams`, and booking slugs and meeting ids are created by users
after the build. **There are no `[param]` routes anywhere in this app** for
exactly this reason; `/strategies/detail?id=` is the established pattern.

So:

* `/book/` reads the slug from `?slug=` **or** from the path, and
  `frontend/vercel.json` ships a `/book/:slug -> /book/` rewrite so the
  shareable link stays pretty on the deployed site. Without the rewrite the
  query form still works, so another host degrades rather than breaking.
* `/meetings/detail?id=` and `/meetings/transcript?id=` follow
  `/strategies/detail`. These are internal links, never shared.

### 3. Extra columns beyond the brief's list

* `calendar_bookings.slot_key` — the double-booking guard, explained above.
* `calendar_bookings.invitee_timezone` — so the confirmation email renders the
  time in the zone the invitee chose it in.
* `meetings.key_points`, `next_steps`, `sentiment` — the remaining three fields
  of `generate_meeting_summary`'s contract. The brief's UI requires a bullet
  list and a sentiment chip; re-parsing those out of prose on every render is
  how a summary panel starts showing something the model did not say.
* `meetings.lead_id`, `title`, `external_event_id`, `actual_start_at`,
  `actual_end_at` — see Feature 3 above.
* `sequence_steps.followup_enabled` — the brief's UI asks for an
  enable/disable toggle; there was nothing to store it in.

---

## Configuration

Everything is documented inline in `.env.example`. In brief:

```
BEAT_SWEEP_MINUTES=30
FOLLOWUP_LOCK_TTL_SECONDS=7200
CALENDAR_SLOT_DAYS_AHEAD=30
CALENDAR_MIN_NOTICE_MINUTES=60
RATE_LIMIT_PUBLIC_BOOKING=30      # app/config.py
RATE_LIMIT_CALENDAR_WRITE=300     # app/config.py
GOOGLE_OAUTH_CLIENT_ID=
GOOGLE_OAUTH_CLIENT_SECRET=
ZOOM_OAUTH_CLIENT_ID=
ZOOM_OAUTH_CLIENT_SECRET=
ZOOM_ACCOUNT_ID=
MEETING_RECORDING_WEBHOOK_SECRET= # empty = transcript ingestion CLOSED
MEETING_AI_MAX_TOKENS=4096
```

New Celery beat entries: `check-followup-due` (30 min, `outreach` queue) and
`close-stale-meetings` (hourly, `default` queue). No new queues; no compose
change needed.

---

## Outstanding

1. **Google Calendar and Zoom have still never been called for real (OPEN).**
   Feature 7 re-checked both adapters against current docs, fixed the Google
   health check, added delete for cleanup and moved Zoom credentials to Admin ›
   Integrations. A live check is ready for whoever holds credentials:
   `scripts/verify_meeting_platforms.py` and
   `tests/test_meeting_platforms_live.py`. Until one passes, `platform=custom`
   is the only verified path, and a platform failure still returns a created
   meeting with `platform_error` (plus `platform_action`).

2. **The Google `calendar.events` scope and the reconnect flow.** The stored
   grant is checked before any call. Accounts connected before the scope
   shipped see "Reconnect for Calendar" in Settings and get
   `platform_action: reconnect_google` when they pick Meet. The auth URL sends
   `include_granted_scopes=true`, so reconnecting keeps their Gmail access.

3. **`/book/:slug` needs the hosting rewrite.** `frontend/vercel.json` ships it
   for the documented target (Vercel). On any other host, links must use
   `/book/?slug=…` until an equivalent rewrite is added.

4. **Transcript ingestion now has a client: Recall.ai** (see
   [`meeting-transcripts.md`](meeting-transcripts.md)). It has not been
   exercised against the live service: no Recall credentials exist yet. The
   HMAC chunk endpoint above is unchanged and remains the generic path for any
   other provider.

5. **Meetings are hosted by one Zoom account.** Zoom server-to-server OAuth
   means the Vectorae operator authorises once and every meeting is created
   under that account. Correct for the current audience (solo founders,
   boutique agency owners — the operator *is* the host); wrong for a future
   team plan, and `app/integrations/zoom.py` is the module that would change.

6. **The mobile tab bar now scrolls.** Calendar and Meetings took the bottom
   nav from seven items to nine, which at 360px is below the 44px touch target.
   The bar is horizontally scrollable rather than hiding two destinations
   behind a "more" menu — worth revisiting if a tenth item is ever added.
