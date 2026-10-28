# Human Review Queue for High-Risk Sends (Part 1, Feature 5)

**Migration** `0057_send_reviews` · **Service** `app/services/send_review.py` ·
**API** `app/api/send_reviews.py` ·
**UI** `frontend/src/lib/sendReview.ts`,
`components/campaigns/ReviewQueuePanel.tsx` (Campaigns → Review)

## Why this exists beside `adversarial_review.py`

Feature A7 reviews a **sequence's content**, once, before launch. This reviews
a **message**, at send time — because every trigger is a fact about the
prospect *at that moment* that no pre-launch review of a template could know.

| Trigger | Fires when | Why it is worth a person |
|---------|-----------|--------------------------|
| `prior_objection` | their **latest** reply was classified as an objection (by *either* `reply_category == OBJECTION` or Feature 1's `intent_label == objection`) | Sending the next scheduled step into an objection is how a recoverable "not convinced" becomes "stop emailing me" |
| `deal_stalled` | an **open** deal whose `close_date` has passed, or a lead the conversion sweep marked `cooling` | There is money on this relationship and it has gone quiet; a templated nudge is the wrong instrument |
| `vip_title` | founder / C-level / VP / MD / partner / board | One badly judged line costs the account |
| `tone_flag` | the **rendered** copy trips the shared tone/spam rules | The copy that was actually written, not the template it came from |

`tone_flag` calls `adversarial_review._text_findings` **verbatim** rather than
reimplementing it, so the pre-launch gate and this one can never disagree about
what reads badly. Only `tone` and `spam` findings hold a message — compliance
findings are the compliance layer's job and claim findings are stripped by the
claim engine.

There is **no stalled stage in `DealStage`** (open/won/lost). Inventing one
would mean migrating a column every consumer already reads; an open deal whose
expected close date has passed is the same fact, already recorded.

## Every trigger is separately switchable

`system_settings`: `send_review_enabled`, plus one key per trigger. This is not
decoration. **If the people you sell to *are* founders and owners — Vectorae's
own ICP is boutique agency owners — leaving `send_review_vip_titles` on puts
every message in the queue, and a queue nobody can finish is a queue nobody
reads.** Turn it off for that ICP.

Reading the settings fails **closed** (assume enabled): the safe direction for
a *review* gate is to review.

## What holding means

The message is rendered, persisted, and moved to
`MessageStatus.AWAITING_REVIEW` — a **VARCHAR-backed enum addition**, so no
column changed (the same technique `LeadStatus.PROPOSAL_SENT` used).

It is **queued, not lost**. Approving returns it to `SCHEDULED` with
`scheduled_at = now` and the next dispatch tick sends it. Nothing is deleted
and no step is skipped, so Feature 3's completion guarantee is unaffected.

Phone is excluded from the gate: a live call is not a message a reviewer can
read and approve after the fact.

## What the reviewer approves is what sends — literally

The exact subject and body are snapshotted when the message is held, and an
**approved review sends that text**, discarding whatever the next render
produced.

That is not a convenience, it is the only correct rule here. **Rendering is a
model call**: the same step, the same lead and the same brief produce different
words every pass. If the send path re-rendered after approval and compared, the
copy would differ every time and the message would loop in the queue forever,
never sending. Snapshot-is-authoritative also means a person can point at the
sent message and at the approval and see the same words.

A reviewer may **edit** the copy, and the edit is what transmits — the point of
a human gate is that the human can improve the message, not only veto it.

## Rejecting

`POST /send-reviews/{id}/reject` requires a note of at least 5 characters. A
rejected message is never sent; the next person to read the queue needs to know
why, and so does the campaign post-mortem.

The **message** is cancelled; the **enrollment keeps running**. Rejecting one
badly-timed email should not end the relationship. For a sequence step the
sequence advances to its next step exactly as a skipped step does (not counted
as sent), or completes when it was the last step; the follow-up sweep never
re-creates a rejected step, and a message waiting in the queue counts as the
step's pending message (send-path-gaps fix, 2026-10-11).

## Failure behaviour

`gate()` and `evaluate()` **never raise**, and on any internal failure they
return "send". A broken review system must not silently stop outreach: the
triggers are a safety net over an already-compliant pipeline, not the
compliance layer itself.

## API

| Method | Path | Notes |
|--------|------|-------|
| `GET` | `/send-reviews?status=pending\|approved\|rejected\|all` | Oldest first — a queue, not a feed |
| `GET` | `/send-reviews/count` | Cheap enough for a nav badge |
| `GET` | `/send-reviews/{id}` | |
| `POST` | `/send-reviews/{id}/approve` | Optional `subject`/`body` edit and `note` |
| `POST` | `/send-reviews/{id}/reject` | `note` required (≥ 5 chars) |

Deciding an already-decided review is a **409**, never a silent overwrite.

## A note on the test suite

`tests/conftest.py` has an autouse `send_review_off` fixture. The shared
`verified_leads` fixture gives every lead the title "Owner" — a VIP title — so
with the gate on, ~120 existing engine assertions would return
`held_for_review` instead of `sent` while testing something else entirely.
`tests/test_send_review.py` turns it back on with its own `review_on` fixture.

## Tests

`tests/test_send_review.py` (55) — each trigger on its own evidence, the
narrowness of the VIP list, the queue-not-lost property, approve-with-edit,
the approved-snapshot-is-authoritative rule, reject leaving the enrollment
running, fail-open, and the API including the 409 and the cross-account 404.
`tests/test_send_reviews_migration.py` (6) — including that the new message
status needs no column change. `frontend/src/tests/sendReview.test.ts` (18).
