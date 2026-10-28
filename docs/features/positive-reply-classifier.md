# Positive Reply Classifier (Part 1, Feature 1)

**Migration** `0053_reply_intent` · **Service** `app/services/reply_intent.py` ·
**UI** `frontend/src/lib/replyIntent.ts`, `components/analytics/ReplyQualityPanel.tsx`

## The problem

A reply rate counts "please remove me" as a win. Two campaigns with a 20% reply
rate can be opposite outcomes: one booked meetings, the other annoyed a list.
Nothing in Vectorae could tell them apart, so nothing could tell you which
sequence to keep running.

## What it does

Every inbound reply, on every channel, gets a fifth verdict: **was this reply
positive?** — one of

| Label | Meaning |
|-------|---------|
| `interested` | Wants to talk now: asks to book, asks for pricing, says yes, forwards to a buyer |
| `objection` | Pushes back — price, incumbent vendor, "not a fit" — but does not ask to be removed |
| `not_now` | Open to it *later*: a named future time, a budget cycle, a reorg |
| `unsubscribe` | Asks to stop being contacted, in any wording |
| `neutral` | Anything else, including machine mail |

…with the model's own **confidence** and a one-line **reason** quoting the reply.

## Why new columns and not `reply_category`

`InboundReply` now answers four separate questions, each in its own column,
because each drives something different:

| Column | Question | Drives |
|--------|----------|--------|
| `classification` | How does the engine route this? | Stop / pause / suppress |
| `reply_category` | What does the human do next? | The AI draft + next action |
| `authenticity_kind` | Is a real person on the other end? | Inbox noise filtering |
| **`intent_label`** | **Was this reply positive?** | **The positive reply rate** |

Widening `reply_category`'s four values would have silently changed the meaning
of the FG1 reply-intelligence UI and its drafts, which already read them.

## How it classifies

A rules pass first, a model call only when the rules are not certain:

- `classification == "unsubscribe_request"` → `unsubscribe`, confidence 1.0.
  The routing classifier already suppressed the contact; disagreeing here would
  report a suppression as a neutral reply.
- `bounce` / `out_of_office` / `automated_response` → `neutral`, confidence 1.0,
  and `machine_reply()` returns True so the metric can exclude them.
- Everything else → one structured Claude call returning
  `{label, confidence, reason}`, given the reply *and the outbound message it
  answers* (subject, body, step number) so "yes, that works" can be read in
  context.

**A failed or unparseable model call leaves the reply UNCLASSIFIED.** It never
falls back to `neutral`. A guessed label would silently move a headline metric,
and "we don't know" is a fact the UI can show honestly.

## The metric

`GET /strategies/{id}/reply-quality`, and a `reply_quality` block on
`GET /strategies/{id}/analytics`:

```json
{
  "sent": 420, "replies": 61, "human_replies": 48,
  "classified": 48, "unclassified": 0,
  "reply_rate": 0.1452,
  "positive_reply_rate": 0.0333,
  "positive_share": 0.2917,
  "breakdown": {"interested": 14, "neutral": 9, "objection": 15,
                "not_now": 8, "unsubscribe": 2}
}
```

Design notes worth keeping:

- **The raw reply rate is unchanged and still shown.** The positive rate sits
  beside it, never in place of it: a high raw rate with a low positive rate is a
  *different problem* from having neither.
- **Only `interested` counts as positive.** `not_now` is a real future
  opportunity (Feature 7 schedules a re-engagement touch from it) but it is not
  a positive reply *to this campaign*, and counting it would make the metric
  flattering rather than useful.
- **Machine mail is excluded from `human_replies`**, so bounces cannot dilute
  the share.
- **Rates are `null`, never `0.0`, when the denominator is zero.** A campaign
  that has sent nothing has no reply rate; charting it as 0% reads as failure.
- **`unclassified` is reported.** A low positive rate must be distinguishable
  from "the classifier hasn't caught up yet". The UI labels a rate built on
  fewer than five human replies, or on a mostly-unclassified set, as
  *provisional*.

## Where it runs

`app/workers/reply_tasks.process_inbound_reply` — the task already dispatched
the moment any channel commits an `InboundReply`. Intent has its **own**
idempotency marker (`intent_at`), separate from `classified_at`, so a reply
stored before this feature picks up a label on the next pass without
regenerating a draft the user may already be editing.

## API

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/crm/replies` | Each item carries an `intent` block (always present; `label: null` = not classified) |
| `GET` | `/crm/replies/{id}/intent` | One reply's verdict |
| `POST` | `/crm/replies/{id}/intent/reclassify` | Re-run it — for history, or when a person disagrees. 503 rather than a silent no-op if the model is unreachable |
| `GET` | `/strategies/{id}/reply-quality` | The metric on its own |
| `GET` | `/strategies/{id}/analytics` | `reply_quality` block |

## Tests

- `tests/test_reply_intent.py` — classification, the rules shortcuts, the
  no-guessing rule, idempotency, the metric's denominators, the API surface.
- `tests/test_reply_intent_migration.py` — the migration adds exactly the
  model's columns, all nullable, and round-trips.
- `frontend/src/tests/replyIntent.test.ts` — labels, the "—" vs "0%" rule,
  provisional detection, the breakdown.
