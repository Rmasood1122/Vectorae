# AI Customer Support Chat (Feature 3)

Status: **implemented and verified locally**, with one significant gap:
**the `ANTHROPIC_API_KEY` in `.env` is invalid (HTTP 401)**, so the assistant's
actual refusal behaviour has never been measured against a real model. See
[Outstanding](#outstanding).

---

## What it does

A chat widget on every dashboard page. Users ask about Vectorae and get
answers grounded in a human-written FAQ. Anything else is refused. When the
assistant cannot answer confidently, it offers a support ticket.

- **Grounded answers** — from `app/services/support_kb.py`, nine curated
  entries written by the product owner.
- **Refusal** — off-topic questions get a fixed message, never a model-written
  one.
- **Ticket fallback** — stored in the database, surfaced in an admin queue.
- **Chat history** — per user, private, deleted after 30 days.
- **Cost control** — 30 messages per user per day.

---

## The three guards

An instruction in a prompt is a request, not a constraint. A model told to
"only answer about Vectorae" will still, given a plausible enough question,
answer about something else. So the refusal is enforced three times:

| # | Where | What it does |
|---|---|---|
| 1 | The prompt | The model is told the rule and asked to classify the question itself (`on_topic`). |
| 2 | **The code** | When the model says off-topic, **the answer it wrote is discarded** and a constant is returned. The model never phrases its own refusal — given the chance to write a sentence about a topic, it writes a sentence about that topic. |
| 3 | The fallback | Any malformed response, API failure, or missing field degrades to "submit a ticket" — never to an ungrounded answer. |

Guard 2 is the one that matters. `tests/test_support_chat.py` proves it by
having the stub return an off-topic verdict **together with** a fully written
off-topic answer, then asserting the user receives the constant and never sees
the model's text.

---

## Design decisions worth knowing

| Decision | Why |
|---|---|
| **A curated FAQ, not RAG and not the model's own knowledge** | A support bot answering from model knowledge invents features — it will confidently describe LinkedIn automation Vectorae does not have. That is worse than no chat: it *creates* support load and makes promises the product cannot keep. |
| The keyword search **never drops entries** | The model cannot cite what it was not shown. Nine short entries cost almost nothing, so all nine go into every prompt and the ranking only affects ordering. A filtering retrieval step would turn a ranking miss into "sorry, I don't know" for a question the FAQ demonstrably answers. |
| Confidence below **0.5** discards the answer | A hedged wrong answer still reads as an answer. An unnecessary ticket is an annoyance; a confident wrong answer is the failure this feature must not produce. |
| **NaN confidence is coerced to 0** | Every comparison against NaN is False, so a naive `confidence < threshold` check would let it straight through. |
| `on_topic is not True`, not `== False` | Fail closed. A malformed response cannot smuggle an ungrounded answer through by omitting the key. |
| Hallucinated `faq_ids` are **stripped** | A fabricated citation must never be stored as a real one — it is the clearest signal the model is inventing rather than citing. |
| Off-topic questions get **no ticket offer** | Inviting someone to file a ticket about the weather makes work for a human and teaches users the bot is a routing layer to a person. |
| Tickets are **not** blocked by the chat budget | Someone who has run out of messages is exactly the person who most needs a human. |
| Tickets are **stored, not emailed** | Email delivery is deferred with Feature 1's transport work — and a ticket that is only emailed is lost whenever the relay is down, so storage is the durable half regardless. |
| `chat_messages.seq`, not `created_at`, orders a transcript | Both turns are written in one request and can share a timestamp, leaving a **random UUID** as the tiebreak. It failed intermittently — a transcript could show the answer before the question. |
| Ticket → session FK is **SET NULL**, not CASCADE | The 30-day purge would otherwise silently delete a month-old open ticket the moment its conversation aged out. |
| The purge keys on `last_message_at` | A conversation started six weeks ago but used yesterday is not stale. |
| The widget is mounted **once in `Shell.tsx`** | Eight pages each remembering to render it is eight chances to forget. |
| The ticket form is reachable **without chatting first** | A user who already knows the bot cannot help them should not have to perform a conversation. |
| `get_client` is reached **through the module** | `anthropic_client.get_client()`, not a `from` import — a module-level `from` import binds the real function before the test monkeypatch runs, so every test would hit the live API. |

---

## Files

**New**

| File | Purpose |
|---|---|
| `app/services/support_kb.py` | The 9 curated FAQ entries + refusal/ticket constants |
| `app/services/support_chat.py` | Prompt, guards, confidence floor, fallbacks |
| `app/api/support.py` | `/support/*` routes |
| `app/workers/support_tasks.py` | 30-day retention purge |
| `alembic/versions/0017_ai_support_chat.py` | 3 tables |
| `frontend/src/components/support/ChatWidget.tsx` | The widget |
| `frontend/src/lib/api/support.ts` | Typed client |
| `frontend/src/app/(admin)/admin/support-tickets/page.tsx` | Admin queue |
| `frontend/src/app/(admin)/admin/tutorials/page.tsx` | Feature 2 FLAG 24 |
| `scripts/support_chat_adversarial.py` | LIVE adversarial check (not a test) |
| `tests/test_support_chat.py` | 67 tests |
| `frontend/e2e/support-chat.spec.ts` | 14 e2e × 2 browsers |

**Modified**: `app/db/models.py`, `app/config.py`, `app/core/config.py`,
`app/api/admin.py`, `app/main.py`, `app/workers/celery_app.py`,
`tests/conftest.py`, `tests/test_celery_routing.py`,
`frontend/src/components/shell/Shell.tsx`, `frontend/src/lib/api/types.ts`,
`frontend/src/lib/api/admin.ts`, `frontend/src/app/(admin)/admin/layout.tsx`.

---

## Environment variables

| Variable | Default | Notes |
|---|---|---|
| `SUPPORT_CHAT_ENABLED` | `true` | **Kill switch.** `false` → the endpoint returns 503 and the widget offers tickets only. |
| `SUPPORT_CHAT_MIN_CONFIDENCE` | `0.5` | Below this the answer is discarded and a ticket offered. |
| `SUPPORT_CHAT_MAX_TOKENS` | `1024` | Output ceiling per reply. |
| `SUPPORT_CHAT_HISTORY_TURNS` | `6` | Prior turns replayed. A direct multiplier on input cost. |
| `SUPPORT_CHAT_RETENTION_DAYS` | `30` | Chat history lifetime. `0` disables the purge. |
| `AI_MODE` | *(empty — auto)* | `console` \| `mock` \| `live`. Empty resolves to `live` when `ANTHROPIC_API_KEY` is set and `mock` when it is not. See **AI_MODE** below. |
| `RATE_LIMIT_SUPPORT_CHAT` | `20` | Messages per user **per day**. Lives on `app/config.py` (moved there from `app/core/config.py`, F-OPS-03), the settings object `enforce_rate_limit` reads. |
| `ANTHROPIC_API_KEY` | *(existing)* | **Currently invalid — see Outstanding.** |

**20, lowered from 30 on 2026-08-30** at the product owner's instruction. The
case for 30 was that a user troubleshooting a real problem sends 15–20 messages
in one sitting, and being cut off mid-thread pushes them into a ticket — the
exact outcome the chat exists to avoid. At 20 that user is cut off right at the
edge of a normal session, so expect some tickets a higher cap would have
absorbed. Raise it if ticket volume from exhausted sessions shows up.

The cap applies in **every** mode, including mock, where a message costs
nothing. Deliberate: a user must not build a habit against a limit that
tightens the day a real key is added.

---

## AI_MODE

`answer_question` dispatches on `AI_MODE`. Every mode returns the same shape,
so persistence, the daily cap and the ticket flow are identical in all three
— the mode only decides where the answer *text* comes from.

| Mode | Anthropic call | Answer text | Use it for |
|---|---|---|---|
| `live` | yes | model, grounded in the FAQ, three guards | production, once the key is real |
| `mock` | **never** | a curated FAQ answer, **verbatim** | before the key arrives; demos; capping spend |
| `console` | **never** | same as `mock` | debugging *why* mock matched what it did |
| *(empty)* | — | resolves to `live` with a key, `mock` without | the default, and the recommended setting |

**Why mock exists.** With an absent or invalid key the widget used to show an
error to every user who opened it. A product that has not launched yet is
exactly when the key is missing, so the pre-launch state was the broken one.
Auto-resolution means the same build runs mock today and live the moment a
real key lands, with nothing to remember to change.

**Mock's guarantee is stronger than live's, not weaker.** Live lets the model
rephrase FAQ content, which is the one remaining place an invented fact can
enter. Mock returns the human-written answer byte for byte, so hallucination
is not reduced — it is structurally impossible.

**What mock gives up is comprehension.** It matches keywords, so it cannot
combine two entries, cannot follow "what about the second one?", and cannot
recognise a question phrased in words the FAQ does not use. When it cannot
match it offers a ticket rather than guessing.

**The match threshold is 4** (`support_kb.MIN_MOCK_MATCH_SCORE`), chosen by
measurement against 20 on-topic and 15 off-topic questions:

| Threshold | On-topic answered | Off-topic **wrongly** answered |
|---|---|---|
| 2 | 18/20 | **2/15** |
| 3 | 18/20 | 0/15 |
| **4** | **18/20** | **0/15** |
| 5 | 16/20 | 0/15 |

4 is the highest threshold that costs no recall, and it lands on a meaningful
boundary: exactly one keyword hit. The battery is
`tests/test_support_chat.py::TestMockMatching`, so the threshold cannot be
nudged later without a failing test.

Two on-topic questions are not answered. `"pricing plans"` is correct — there
is no pricing entry, so a ticket is the right outcome. `"who is it for"` is a
genuine miss: it is in the FAQ but consists entirely of stopwords once `who`
and `for` are removed, and admitting `who` would match "who won the world cup"
against that entry. The miss costs one unnecessary ticket; the alternative
costs a confidently wrong answer.

**A dead key in `live` mode falls back to the FAQ, not to a ticket.**
Auto-resolution picks `live` whenever a key is *present*, and it cannot know
the key is dead without spending a call to find out. The `.env` on this machine
holds an invalid key, so it resolves to `live` and every message hits the
model's failure path. A **permanent** failure there — 401 invalid/revoked key,
403, an exhausted balance, an unknown model — is a configuration problem that
retrying and waiting cannot fix, so the answer degrades to the mock answerer
and the user gets the curated FAQ text. It is logged at ERROR naming
`ANTHROPIC_API_KEY`, because serving the FAQ is the right behaviour *and* the
key is still broken.

A **transient** failure — an overloaded API, a timeout — still degrades to a
ticket. Quietly serving keyword-matched answers through a five-minute blip
would hide a real incident behind a slightly worse product.

**`reason` on every stored message records which path produced it** —
`mock_faq_match`, `mock_no_match`, `answered`, `off_topic`, `low_confidence`,
`model_error` — so history is never ambiguous about whether a model was
involved.

**No FAQ match pre-fills the ticket subject** with
`Question not in FAQ: <the question>`, truncated to the 200 characters the API
accepts (`frontend/src/lib/support/ticketSubject.ts`).

---

## API

Full reference: [`docs/api/endpoints.md`](../api/endpoints.md).

| Endpoint | Purpose |
|---|---|
| `GET /support/faq` | The KB + `chat_enabled` |
| `POST /support/chat` | Ask a question |
| `GET /support/chat/sessions` | Your conversations |
| `GET /support/chat/sessions/{id}` | One transcript |
| `POST /support/chat/sessions` | Start a new thread |
| `DELETE /support/chat/sessions/{id}` | Delete a thread |
| `POST /support/tickets` | Escalate |
| `GET /support/tickets` | Your tickets |
| `GET /admin/support/tickets` | Admin queue (carries requester email) |
| `POST /admin/support/tickets/{id}/resolve` | Close a ticket |

---

## Editing the FAQ

Edit `app/services/support_kb.py` and deploy. No migration, no SQL.

**Never change an `id`** — stored chat messages reference them in `faq_ids`
for auditing which answer was used. Change the text freely.

Add `keywords` for words a user might type that appear in neither the question
nor the answer. Matching is bidirectional, so `"tutorial"` matches a user
typing `"tutorials"`.

---

## How to test it locally

```bash
python -m app.db.migrate            # applies 0017
python -m uvicorn app.main:app --port 8000
cd frontend && npm run dev
```

Open any dashboard page, click the life-ring button, bottom right.

```bash
python -m pytest tests/test_support_chat.py -v          # 67
python -m pytest tests/ --ignore=tests/integration      # 626
cd frontend && npx vitest run                           # 185
cd frontend && NEXT_PUBLIC_API_URL=https://api.example.com npx next build \
  && npx playwright test e2e/support-chat.spec.ts       # 28
```

### The adversarial check (costs money, needs a WORKING key)

```bash
python -m scripts.support_chat_adversarial
```

32 cases: 10 on-topic, 10 off-topic, 7 prompt-injection, 5
on-topic-but-not-in-the-FAQ. Exit code 0 only if all pass.

It **refuses to grade anything** when the model is unreachable. That guard
exists because the first run scored "5/32 passed" against a dead key — the
five were the unknown-question cases, whose expected outcome is a ticket
fallback, which is also what a model error produces. A broken key looked like
partially working behaviour.

---

## How to test it in production

1. Confirm the migration: `SELECT version_num FROM alembic_version;` →
   `0017_ai_support_chat`.
2. **Run the adversarial script against production config** before telling any
   user the chat exists. It is the only thing that measures the refusal.
3. Ask an on-topic question in the widget; confirm a grounded answer.
4. Ask "what is the capital of France?"; confirm the refusal and **no** ticket
   button.
5. Ask "how much does it cost?" (not in the FAQ); confirm a ticket offer rather
   than an invented price.
6. Submit a ticket; confirm it appears at `/admin/support-tickets`.
7. Confirm a second user sees none of the first user's conversations.
8. Check the cost in the Anthropic console after a day of real use.

---

## Common errors and fixes

| Symptom | Cause | Fix |
|---|---|---|
| Every answer is "I'm not confident enough…" with `reason=model_error` | The API key is invalid, revoked, or out of credit | Fix `ANTHROPIC_API_KEY`. Check the log line `support chat model call failed:` |
| Chat returns 503 | `SUPPORT_CHAT_ENABLED=false` | Set it true and restart |
| Chat returns 429 | Daily budget spent | Expected. Raise `RATE_LIMIT_SUPPORT_CHAT` or wait |
| Chat returns 403 `EMAIL_NOT_VERIFIED` | Feature 1's gate | Verify the address |
| The AI answers something not in the FAQ | A real grounding failure | Capture the question, run the adversarial script, tighten the prompt or add the FAQ entry. **Report it** — this is the failure mode the feature exists to prevent |
| The AI refuses a legitimate question | Not covered by the FAQ | Add an entry. Refusing is the designed behaviour, not a bug |
| Widget missing | Frontend not rebuilt (static export) | Rebuild and redeploy |
| Chat history vanished after 30 days | Retention working as designed | Raise `SUPPORT_CHAT_RETENTION_DAYS` |
| Tickets pile up unanswered | Nothing emails them — the queue is polled by a human | Watch `/admin/support-tickets`. See Outstanding #3 |

---

## Rollback procedure

### Level 1 — kill the chat, keep tickets (no deploy)

```
SUPPORT_CHAT_ENABLED=false
```
Restart. The endpoint returns 503, the widget disables its input and offers
the ticket form. Nothing else changes. **This is the emergency lever** if the
assistant ever says something it should not.

### Level 2 — revert the code, keep the schema

```bash
git reset --hard 5e5777a      # BACKUP: Pre-ai-support-chat anchor
```
File-level copies of the 12 modified files:
`C:\tmp\leadpilot-backups\20260829_212808_pre-ai-support-chat\`.

Leaving migration 0017 applied is safe — three unused tables affect nothing.

### Level 3 — drop the tables

```bash
alembic downgrade 0016_tutorial_progress
```
**Lossy:** discards all chat history **and every support ticket, including
open ones**. Take a `pg_dump` first — see
[`docs/backup/backup-procedure.md`](../backup/backup-procedure.md).

---

## Outstanding

| # | Item | Severity |
|---|---|---|
| 1 | **`ANTHROPIC_API_KEY` in `.env` is INVALID** — the live API returns `401 authentication_error: API key is invalid`. **Mitigated for the chat as of 2026-08-30:** with no valid key `AI_MODE` auto-resolves to `mock`, so the widget answers from the FAQ instead of failing on every question. Still HIGH because the strategy pipeline uses the same key and has no mock mode, and because the model's refusal behaviour remains unmeasured. | **HIGH** |
| 2 | **Refusal behaviour is UNVERIFIED end to end.** The code-level guards have 67 tests, and a live run confirmed that with a dead key all 32 adversarial inputs degraded to the ticket fallback with zero ungrounded content. But "the model classifies off-topic correctly" is unmeasured. Run `scripts/support_chat_adversarial.py` once the key works. | **HIGH** |
| 3 | **Nobody is notified of a new ticket.** They are stored and visible at `/admin/support-tickets`, but nothing emails or pushes. The widget promises a response "within 24 hours" — that promise currently depends on someone remembering to look. | **MEDIUM** |
| 4 | Prompt-injection resistance is **untested against a real model** for the same reason as #2. The 7 injection cases are in the script, ready to run. | MEDIUM |
| 5 | Cost is uncapped in aggregate — 20/user/day is per user, so N users cost N × 20. There is no global daily ceiling. In `mock` mode the aggregate cost is zero. | LOW |
| 6 | The FAQ has no entry for pricing, refunds, or integrations, so those questions correctly produce tickets. Add entries if they become common. | LOW |
| 7 | **Mock mode's matcher is keyword-based and misses `"who is it for"`** — an FAQ question that is entirely stopwords. It produces an unnecessary ticket rather than a wrong answer, which is the safe direction, but it is a real recall gap that disappears the moment `AI_MODE` is `live`. | LOW |
