# Unified Cross-Channel Inbox (Part 1, Feature 6)

**Migration** `0058_inbox_handling` · **Service** `app/services/inbox.py` ·
**API** `app/api/inbox.py` ·
**UI** `frontend/src/lib/inbox.ts`, `app/(app)/inbox/page.tsx`

## Threaded by prospect, not by channel

That is the whole feature. A prospect who answered an email on Tuesday, was
messaged on LinkedIn on Wednesday and replied there on Thursday is **one**
conversation. Every tool that files those as three inboxes makes a person
reconstruct the relationship in their head before they can answer it, and the
usual result is a reply that contradicts something said on another channel two
days earlier.

`conversation_thread.build_thread` (Feature A4) already merges one prospect's
channels into one timeline. This feature is the **list** above it: every
prospect with anything inbound, with enough per-thread summary to decide where
to start without opening anything.

`thread_detail()` calls `build_thread` rather than re-merging the tables — two
implementations of "merge these in time order" would eventually disagree about
one prospect, and the lead page and the inbox must show the same conversation.

## What "needs a reply" means

- the newest inbound on any channel is **unhandled**, and
- it came from **a person** — machine mail (bounces, out-of-office,
  auto-responders, no-reply bots) is filed, never chased.

### Why "handled" is a stored flag and not derived

"The latest inbound is newer than the latest outbound" is what an unhandled
reply usually looks like, but it cannot represent the two cases that matter
most:

- a reply answered **outside Vectorae** — someone picked up the phone, or
  replied from Gmail directly;
- a reply that **needs no answer**.

Both are done and neither produces an outbound row here. Without the flag the
inbox would show them forever, and an inbox that lies about what is outstanding
stops being opened.

Inferring it from a later outbound would be worse still: the thread would clear
itself the moment the next sequence step went out.

### Why it is per reply, not per prospect

A prospect who replies twice in a week has **one thread and two things to
answer**. A per-prospect flag would let the second be cleared by a decision
made about the first.

## Ordering

`needs_reply` is **oldest first**. An inbox worked newest-first leaves the
replies that have waited longest at the bottom, and those are exactly the ones
where a late answer costs the deal. `all` and `handled` are newest-first —
those are browsed, not worked.

## The badge

`GET /inbox/count` counts **threads, not replies**. A prospect who sent three
messages is one thing to do, and a badge reading 3 makes the inbox look worse
than it is.

## API

| Method | Path | Notes |
|--------|------|-------|
| `GET` | `/inbox?filter=needs_reply\|all\|handled&channel=&limit=&offset=` | Threads, by prospect |
| `GET` | `/inbox/count` | Threads waiting |
| `GET` | `/inbox/{lead_id}` | The whole conversation + its inbox state |
| `POST` | `/inbox/{lead_id}/handled` | `{handled: bool}` — clears or restores the thread |
| `POST` | `/inbox/replies/{id}/handled` | One reply |

`/inbox` is a new prefix. It does not collide with `/crm/replies` (Feature A3),
which lists replies **by reply** with their authenticity — a different question.

**Marking handled sends nothing.** It records that a person has dealt with the
conversation, wherever they dealt with it. Both directions are supported:
"done" is a judgement, and clearing a thread by mistake must be undoable
without hunting for a reply the list no longer shows.

## UI

`/inbox?lead=<id>` — a query parameter rather than `/inbox/[id]`, because
`next.config.js` sets `output: 'export'` and a dynamic segment cannot serve an
id that did not exist at build time. Left: the thread list with each person's
channels, the latest preview, an intent badge and an **Overdue** flag after two
days. Right: the same `ConversationThread` component the lead page uses.

Added to the main nav **second, next to Pipeline** — those are the two screens
opened every morning: one is "who should I contact", the other is "who is
waiting on me". The mobile bar scrolls horizontally, so an eleventh entry costs
a swipe rather than a hidden destination.

## Tests

`tests/test_inbox.py` (34) — channels merging into one thread, machine mail
filed but not chased, per-reply handling, an outbound **not** clearing a
thread, oldest-first ordering, the badge counting people, cross-account
isolation. `tests/test_inbox_migration.py` (5) — including that
`handled_by_user_id` is `ON DELETE SET NULL`, so the fact that a reply *was*
handled outlives the account of whoever handled it.
`frontend/src/tests/inbox.test.ts` (18).

## F04 Reply Copilot

With `reply_copilot_enabled` on, the thread view shows a **Reply Copilot**
card above the conversation: drafted answers to approve / edit / discard
(LinkedIn: copy-only). Approving or copying marks the reply handled through
`inbox.mark_handled`; discarding does not. See
`docs/features/f04-reply-copilot.md`.
