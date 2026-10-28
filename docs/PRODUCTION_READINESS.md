# VECTORAE PRODUCTION READINESS

**Last updated:** 2026-08-30
**Local environment:** ✅ READY
**Production environment:** ⏳ PENDING KEYS

---

## QUICK START — WHEN KEYS ARRIVE

**Step 1: Add to `.env`:**

```bash
RESEND_API_KEY=your_key
RESEND_FROM_EMAIL=noreply@calendarharvest.com
ANTHROPIC_API_KEY=your_key
AI_MODE=live
```

**Step 2: Run one command:**

```bash
python scripts/activate_with_keys.py
```

**Step 3: Open admin dashboard:**

`/admin/tutorials` → add YouTube IDs → publish

**Step 4: Done. All 3 features live.**

> `activate_with_keys.py` stops at the first failure and prints the exact fix.
> It is safe to re-run; the only destructive step (the live migration) asks
> before it touches anything. Run it with `--dry-run` first if you want to see
> the check list without making a single network call.

---

## What "verified" means in this document

Every ✅ below was re-run on **2026-08-30** and its output read. Nothing is
marked verified because it was verified once in the past, and nothing is marked
verified because it *should* work.

Anything that depends on a credential this machine does not have is ⏳, never
✅ — including things that are almost certainly fine.

**Measured today:**

| Check | Result |
|---|---|
| Backend suite (`pytest tests/ --ignore=tests/integration`) | **754 passed, 5 skipped, 0 failed** |
| Frontend unit (`vitest run`) | **191 passed**, 12 files |
| E2E (`playwright test`) | **106 passed, 0 failed — 3 consecutive runs** |
| Type check (`tsc --noEmit`) | 1 error, pre-existing (`statusTone.test.ts` → `Strategy`). 0 new |
| Production build (`next build`) | Compiled, **27 routes** |
| Migration chain on PostgreSQL 16 | `0001 → 0018` clean, rollback verified |

---

## FEATURE 1 — EMAIL VERIFICATION

- ✅ Code implemented and tested
- ✅ Migration 0015 written and verified locally
- ✅ **28 backend tests pass** (`tests/test_email_verification.py`)
- ✅ **Playwright e2e spec passes (3 consecutive)** — `email-verification.spec.ts`, 14 tests × 2 browser projects
- ✅ Rollback procedure documented — `docs/backup/backup-procedure.md`
- ✅ Email provider abstracted: `console` / `memory` / `resend` / `smtp`
- ✅ Currently running `EMAIL_PROVIDER=console` — no mail leaves the machine
- ⏳ NEEDS KEY: `RESEND_API_KEY`
- ⏳ NEEDS ACTION: Verify `calendarharvest.com` in Resend
- ⏳ NEEDS ACTION: Run live Neon migration
- ⏳ NEEDS PROOF: Real email received in inbox

**How to activate:**

1. Add `RESEND_API_KEY` to `.env`
2. Add `RESEND_FROM_EMAIL=noreply@calendarharvest.com`
3. Verify domain in Resend dashboard
4. Run: `python scripts/activate_with_keys.py`

**Emergency lever:** `REQUIRE_EMAIL_VERIFICATION=false` in the Render dashboard
+ restart. Seconds, zero data change. Use this before any rollback if
verification ever locks real users out.

---

## FEATURE 2 — TUTORIAL SECTION

- ✅ Code implemented and tested
- ✅ Migration 0016 written and verified locally
- ✅ Migration 0018 moves the catalogue into the database — verified on PostgreSQL 16
- ✅ **75 backend tests pass** (`tests/test_tutorials.py`)
- ✅ **Playwright e2e spec passes (3 consecutive)** — `tutorials.spec.ts`, 18 tests × 2 browser projects
- ✅ Progress tracking proven (postMessage working, duration 214s)
- ✅ YouTube player bug fixed (Error 153 resolved, nocookie host)
- ✅ Search, badges, categories all working
- ✅ Admin UI at `/admin/tutorials` — add videos without a deploy
- ⏳ NEEDS ACTION: Add real YouTube video IDs
- ⏳ NEEDS ACTION: Publish tutorials via admin UI

**How to activate:**

1. Open `/admin/tutorials` in dashboard
2. Add YouTube video ID for each tutorial
3. Click Publish for each tutorial
4. Users can now watch and track progress

### ⚠️ `/learn` IS EMPTY RIGHT NOW — THIS IS INTENDED

Migration 0018 seeds nine tutorials with `is_published = false` and
`youtube_id = NULL`. Published, they would render as nine permanent "coming
soon" cards, which reads as a **broken** feature rather than an empty one.
Unpublished, `/learn` is honestly empty until somebody adds a real video and
presses Publish.

Publish **refuses** a tutorial that has no `youtube_id`, so this cannot be
short-circuited by accident.

---

## FEATURE 3 — AI CUSTOMER SUPPORT

- ✅ Code implemented and tested
- ✅ Migration 0017 written and verified locally
- ✅ **131 backend tests pass** (`tests/test_support_chat.py`)
- ✅ **Playwright e2e spec passes (3 consecutive)** — `support-chat.spec.ts`, 14 tests × 2 browser projects
- ✅ **Mock mode working — FAQ answers without API**, no Anthropic call is ever made
- ✅ Ticket system working — saves to database
- ✅ Rate limiting working — **20 messages/day** (lowered from 30 on 2026-08-30)
- ✅ Chat history — 30 day retention
- ✅ Admin ticket queue — working, `/admin/support-tickets`
- ✅ Nine FAQ entries, human-written, returned verbatim in mock mode
- ⏳ NEEDS KEY: Valid `ANTHROPIC_API_KEY`
- ⏳ NEEDS PROOF: Adversarial test 28/32 minimum

**How to activate:**

1. Add valid `ANTHROPIC_API_KEY` to `.env`
2. Set `AI_MODE=live` in `.env`
3. Run: `python scripts/activate_with_keys.py`
4. Confirm adversarial test passes

### The chat works TODAY, without a key

`AI_MODE` decides where the answer text comes from. Everything around it —
persistence, the daily cap, ticket creation — is identical in every mode.

| Mode | Anthropic call | Answer text |
|---|---|---|
| `live` | yes | model, grounded in the FAQ, three refusal guards |
| `mock` | **never** | a curated FAQ answer, **verbatim** |
| `console` | **never** | same as `mock`, plus an INFO log of the match |
| *(empty)* | — | **auto:** `live` with a key, `mock` without |

The key in `.env` right now is **present but invalid** (401). Auto-resolution
picks `live` whenever a key is present and cannot know it is dead without
spending a call to find out — so a **permanent** model failure (401, 403,
exhausted balance, unknown model) degrades to the curated FAQ rather than to a
ticket, logged at ERROR naming `ANTHROPIC_API_KEY`. A **transient** failure
still degrades to a ticket, so a real outage is not hidden behind a slightly
worse product.

**Net effect: asking "what is Vectorae" today returns the real answer, not
"submit a ticket".**

---

## DATABASE

- ✅ Local SQLite — migrations `0001–0018` verified
- ✅ Throwaway PostgreSQL 16 — all migrations verified, including full chain `0001 → 0018`
- ✅ Rollback tested for each migration — table in `docs/backup/backup-procedure.md`
- ✅ Migration 0018 rollback verified: `downgrade 0017` drops `tutorial_catalogue`, `tutorial_progress` and `users` intact, re-upgrade re-seeds all 9 rows
- ⏳ Live Neon DB still at migration **0014**
- ⏳ Needs `pg_dump` backup before migrating

**How to migrate:**

1. Run: `pg_dump $DATABASE_URL > backup_pre_migrate.sql`
2. Verify dump file is not empty
3. Run: `python scripts/activate_with_keys.py`
4. Confirm `alembic head = 0018_tutorial_catalogue` in live DB

> **The target is 0018, not 0017.** `activate_with_keys.py` pins
> `TARGET_REVISION` and a test asserts it matches the real alembic head, so a
> new migration cannot silently leave this out of date. That guard has already
> fired once, on 0018 itself.

### ⚠️ 0018's rollback is lossy in a way the others are not

Downgrading 0018 brings the nine seeded rows back on re-upgrade, but **every
admin edit is gone**: YouTube IDs, retitled tutorials, publish state, and any
tutorial created after the migration exist nowhere except that table. Take a
dump first.

---

## WHAT IS NOT PROVEN

Listed plainly, because an unproven item hidden among proven ones is how a
launch goes wrong.

| # | Item | Why it is not proven | Severity |
|---|---|---|---|
| 1 | **The model's refusal behaviour** | Needs a valid `ANTHROPIC_API_KEY`. The code-level guards have tests; "the model classifies off-topic correctly" is a different claim and is unmeasured. Run `scripts/support_chat_adversarial.py` — threshold 28/32. | **HIGH** |
| 2 | **Prompt-injection resistance** | Same reason as #1. The 7 injection cases are written and ready to run. | **MEDIUM** |
| 3 | **Real email delivery** | Needs `RESEND_API_KEY` + a verified domain. `EMAIL_PROVIDER=console` has never put a message on the wire. | **HIGH** |
| 4 | **The live Neon database** | Deliberately untouched. It is recorded as being at 0014; that was **not** re-verified today, because verifying it means connecting to production. | **MEDIUM** |
| 5 | **Nobody is notified of a new ticket** | Tickets are stored and visible at `/admin/support-tickets`, but nothing emails or pushes. The widget promises a response "within 24 hours" — that promise depends on someone remembering to look. | **MEDIUM** |
| 6 | **Aggregate AI cost** | 20/user/day is *per user*, so N users cost N × 20. There is no global daily ceiling. Zero in mock mode. | LOW |
| 7 | **Mock mode misses `"who is it for"`** | An FAQ question that is entirely stopwords. Produces an unnecessary ticket rather than a wrong answer — the safe direction — and disappears the moment `AI_MODE=live`. | LOW |
| 8 | **Real YouTube video IDs** | None exist yet, which is why `/learn` is empty. | LOW |

---

## Reference

| What | Where |
|---|---|
| Activation script | `scripts/activate_with_keys.py` (`--dry-run` to preview) |
| Backup + rollback procedure | `docs/backup/backup-procedure.md` |
| Email verification | `docs/features/email-verification.md` |
| Tutorial section | `docs/features/tutorial-section.md` |
| AI support chat + `AI_MODE` | `docs/features/ai-support-chat.md` |
| Every environment variable | `.env.example` |
