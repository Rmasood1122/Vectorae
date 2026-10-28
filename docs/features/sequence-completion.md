# Sequence Completion Guarantee + Metric (Part 1, Feature 3)

**Migration** `0055_sequence_completion` ·
**Service** `app/services/sequence_completion.py` ·
**UI** `frontend/src/lib/sequenceCompletion.ts`,
`components/analytics/CompletionPanel.tsx`

## The promise

A sequence stops early **only** for a reason a person would recognise as a
decision. Everything else means a prospect quietly stopped being contacted
halfway through a campaign that was still running — the failure this feature
exists to make impossible to hide.

## How the guarantee is enforced

1. **Every stop already goes through `sequence_engine.stop_enrollment`.** That
   was true before this feature; what is new is that the function now records
   *when* it stopped and *what kind* of stop it was.
2. **`categorize()` maps the free-text reason onto a fixed vocabulary** at
   **write** time. An unrecognised reason becomes `other` **and is logged at
   warning level**, so a new call site that invents a reason shows up in the
   logs and in the `dropped_unauthorised` bucket instead of vanishing.
   Deriving the category at read time would have let a new reason quietly
   widen a bucket nobody looks at.
3. **`AUTHORISED` holds the legitimate early stops.** Everything else is
   reported separately, however sensible it looked at the time — including the
   automated kill signal, which is a product decision whose volume is worth
   seeing.

| Category | Authorised? | Comes from |
|----------|-------------|-----------|
| `replied` | ✅ | `replied_interested` / `_question` / `_objection` |
| `unsubscribed` | ✅ | an unsubscribe click or `replied_unsubscribe_request` |
| `bounced` | ✅ | `bounced`, `replied_bounce` |
| `meeting_booked` | ✅ | the Calendly webhook |
| `suppressed` | ✅ | the send-time suppression check |
| `opted_out` | ✅ | WhatsApp STOP |
| `crm_closed` | ✅ | a HubSpot/Salesforce close (`crm_<provider>`), or a lead moved to closed won / closed lost / disqualified inside Vectorae (`crm_<status>`, `sequence_engine.stop_for_status`; added 2026-10-11, gaps-sweep-2 #3) |
| `call_outcome` | ✅ | an AI call that ended the conversation |
| `meeting_outcome` | ✅ | "Log Meeting Outcome" (`meeting_outcome_<kind>`; was counted as `other` until 2026-10-11, gaps-sweep-2 #4) |
| `system_kill` | ❌ | `kill_signal:*` (the conversion-probability sweep) |
| `all_steps_skipped` | ❌ | every step skipped, e.g. WhatsApp with no opt-in |
| `other` | ❌ | **an unrecognised reason — always a bug or a new call site** |

`replied_unsubscribe_request` is deliberately matched **before** `replied`
(longest prefix wins), so an unsubscribe is never counted as a plain reply.

## The three columns that make the metric truthful

- **`planned_steps`** — the number of steps the prospect was enrolled *into*,
  frozen at enrollment. Counting the sequence's steps at read time would mean
  adding a step 4 next month retroactively turns every completed enrollment
  into an incomplete one. A test asserts exactly this.
- **`steps_sent`** — incremented once, on the send that *advances* the
  sequence. A skipped step advances but was never received, so it does not
  count; a transient failure that re-sends the same step does not count twice.
- **`stop_category`** — the fixed vocabulary, decided at write time.

## The metric

`GET /strategies/{id}/completion`, `GET /sequences/{id}/completion`, and a
`completion` block on `GET /strategies/{id}/analytics`:

```json
{
  "enrolled": 120, "running": 30, "completed": 54, "dropped": 34, "unknown": 2,
  "finished": 88, "completion_rate": 0.6136, "dropped_unauthorised": 3,
  "reasons": [
    {"category": "replied", "label": "Prospect replied", "count": 21, "authorised": true},
    {"category": "system_kill", "label": "Stopped by an automated kill signal",
     "count": 3,  "authorised": false}
  ],
  "by_sequence": [ ... ]
}
```

Design notes:

- **Running enrollments are in neither the numerator nor the denominator.** A
  campaign on step 2 of 5 has not *failed* to complete; it has not finished.
- **`completion_rate` is `null`, never `0.0`, when nothing has finished.**
- **`unknown` is enrollments created before this migration** — no
  `planned_steps` snapshot, so completion is unanswerable. Reported honestly;
  never counted as a failure.
- **`dropped_unauthorised` is the number that matters** for the guarantee. A
  60% completion rate is *fine* if the other 40% replied, and *alarming* if the
  system dropped them, which is why the UI raises a warning on this number and
  not on the rate.
- **`by_sequence`** exists so a single bad sequence is visible rather than
  averaged into the campaign.

`GET /strategies/{id}/completion/dropped?unauthorised_only=true` is the work
list behind the number.

## Tests

- `tests/test_sequence_completion.py` (49) — every stop reason the codebase
  actually passes today is recognised; an invented reason is `other` *and* is
  logged; a meeting pause is never a stop; a skipped step is never a received
  one; a re-sent step is not double-counted; editing a sequence does not
  un-complete finished enrollments.
- `tests/test_sequence_completion_migration.py` (6) — including that
  `steps_sent` backfills to 0 rather than NULL.
- `frontend/src/tests/sequenceCompletion.test.ts` (15).
