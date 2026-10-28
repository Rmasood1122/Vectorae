# Feature Group 7 — Meeting preparation and follow-up

## What it does

1. **A prep brief the moment a meeting is booked.** A Calendly
   `invitee.created` delivery or a booking on the Vectorae calendar writes a
   `meeting_prep_briefs` row and queues generation on the `notifications`
   queue. The brief covers:
   - the lead profile (name, title, company, LinkedIn URL, email, …) — **copied
     from the records, never written by the model**;
   - company overview, industry, size, funding, recent news;
   - the lead's last three LinkedIn posts (when Feature Group 2 fetched them);
   - why they booked — the message and reply that converted them, quoted;
   - likely pain points (labelled as hypotheses) and objections with responses;
   - talking points and discovery questions for this prospect;
   - competitive landscape (from the pipeline's competitor research steps);
   - recommended next steps and deal structure;
   - an "opening 60 seconds" script.

   When it is ready the owner gets a push notification and, if Slack is
   connected, a Slack message with the "why they booked" and opening script
   inline and a link to the **Meeting Prep** tab of `/leads/detail`.

2. **Reminders.** A Beat sweep in every worker burst (`BEAT_SWEEP_MINUTES`, 30) sends a "coming up" reminder
   once within 24h of the call and a 1-hour reminder whose body is the opening
   script. Both are claimed with a conditional UPDATE, so they are
   at-most-once across workers. A cancellation (Calendly `invitee.canceled` or
   cancelling the Vectorae booking) stops both; a reschedule re-arms them.

3. **Log Meeting Outcome.** On the lead page. The outcome is committed first
   (status change, CRM activity + note, `won`/`lost` outcome for the learning
   loop, a won Deal for closed-won, every live sequence enrollment stopped),
   then Claude writes a follow-up email that is saved as a **Gmail draft**. The
   user edits it in place and presses Send, or sends from Gmail. It is never
   sent automatically, and the suppression list is checked both when drafting
   and when sending.

## Configuration

| Where | What |
|---|---|
| Admin › System Settings | `meeting_prep_enabled`, `meeting_reminder_24h_enabled`, `meeting_reminder_1h_enabled` |
| `MEETING_PREP_MAX_TOKENS` (4096) | Output ceiling for one brief |
| `MEETING_FOLLOWUP_MAX_TOKENS` (1500) | Output ceiling for one follow-up |
| `BEAT_SWEEP_MINUTES` (30) | Reminder sweep interval (every worker burst): the 1h reminder lands between 60 and 30 minutes before the meeting |
| `RATE_LIMIT_AI_ACTION` (60/h) | Regenerate / log-with-follow-up per user |

Saving drafts needs the `gmail.compose` scope, added to the Gmail OAuth
request by this feature. **Accounts connected before this change must
reconnect Gmail**; until they do, a follow-up lands as `reauth_required` with
its text kept and copyable.

## Workers

The `notifications` queue has its own worker in `docker-compose.prod.yml`
(`celery-worker-notifications`) and `railway/worker-notifications.json`; the
dev compose worker and the Render single worker consume it alongside the
other queues.

## Fixed along the way

- **Calendly cancellations were silently dropped.** Webhook dedupe keyed on the
  invitee URI, which Calendly sends on both `invitee.created` and
  `invitee.canceled`, so every cancellation was acknowledged as a duplicate.
  The key now includes the event type.
- **`TokenStore` had never worked** (wrong column name, naive/aware datetime
  comparison, string UUIDs). Repaired, and extended with a system scope.
- **Lead push-notification deep links opened a 404** on the static export
  (`/leads/<id>`); they now route to `/leads/detail?id=`.
- **Every `apiClient` read and write in the frontend was broken** (bodies
  JSON-encoded twice; reads of `.data` on an already-parsed body) — this
  affected the admin users/suppression/task-error pages, the plan card,
  onboarding and several analytics cards.
