# Learn Vectorae — Tutorial Section (Feature 2)

Status: **implemented and verified locally.** All nine videos are
**placeholders** — the platform is complete, the content is not. See
[Adding the real videos](#adding-the-real-videos).

**No new environment variables.** Nothing to configure, nothing to paste into
Render.

---

## What it does

A `/learn` tab in the dashboard with the nine tutorials, grouped Beginner /
Intermediate / Advanced, each tracking progress per user.

- **Video player** — YouTube embed (`youtube-nocookie.com`) driven by the
  IFrame API so playback position records itself.
- **Progress per user per video** — resume point, percent watched, completion.
- **Completion badges** — one per level plus "Vectorae Certified" for all nine.
- **Search** — case-insensitive, over titles *and* descriptions.
- **Level sections** — with per-level counts and an overall progress header.

Progress is **private per user**. Admins get aggregate counts only
(`GET /admin/tutorials/completions`) — there is no endpoint anywhere that
reports which videos a named person watched.

---

## Design decisions worth knowing

| Decision | Why |
|---|---|
| **The catalogue is code, not a database table** (`app/services/tutorials.py`) | The videos are editorial content, not user data. Replacing a placeholder id becomes a reviewed one-line diff instead of hand-written SQL against production, the catalogue is identical in every environment, and this feature needs one migration instead of two tables. **Cost:** adding a video needs a deploy, not a form. |
| Only **progress** is in the database | Per-user, high write volume — the opposite kind of data. |
| `youtube_id = None`, not a fake id | A made-up id renders YouTube's own "Video unavailable" error, which looks exactly like a bug in this app. `None` makes `is_placeholder` true and the UI shows an honest "not published yet" panel. |
| **Slugs are permanent** | `tutorial_progress.tutorial_slug` points at them with no foreign key (there is no table to point at). Renaming a slug orphans every progress row for that video. Titles are safe to change; slugs are not. |
| Completion at **90%**, not 100% | Almost nobody reaches the final frame — end cards, credits, clicking away. A 100% rule strands users at "8 of 9" and teaches them to scrub to the end rather than watch. |
| `position_seconds` takes the **latest** value; `percent` takes the **maximum** | They answer different questions. Position is "where do I resume" — scrubbing back should resume back. Percent is "how much have I seen" — scrubbing back must not erase watched progress or undo a completion. One field cannot express both. |
| **Badges are derived, never stored** | A stored badge row can disagree with the progress meant to justify it, and then there is no honest answer to "why does this user have this badge?". Derived, a badge is exactly as true as its data — which is also why resetting a video correctly revokes one. |
| The summary **ignores** the search filter | It answers "how far through the course am I". It must not move while someone types in the search box. |
| Search and filtering happen **server-side** | Nine items would filter fine in the browser, but two implementations of one filter diverge the moment the catalogue grows. |
| A missing progress row is **not an error** | It serialises as a zeroed "not started" shape, so the client never branches on "has this user ever touched this video" and no row is created just to draw a 0% bar. |
| The player **polls** the IFrame API rather than listening to state events | A state event fires on play/pause but not while playback simply continues, so position would only ever be recorded at the moment someone paused. |
| Progress writes are **throttled** (`src/lib/tutorial-progress.ts`) | Heartbeat every 10 s, immediate on a seek or on crossing the completion threshold, never more often than every 3 s. Scrubbing a timeline fires a continuous stream of position changes and every one of them looks like a seek. |

---

## Files

**New**

| File | Purpose |
|---|---|
| `app/services/tutorials.py` | The catalogue — 9 tutorials, 3 levels, 4 badges, search |
| `app/api/tutorials.py` | `/tutorials` routes + progress/badge logic |
| `alembic/versions/0016_tutorial_progress.py` | The `tutorial_progress` table |
| `frontend/src/app/(app)/learn/page.tsx` | The Learn page |
| `frontend/src/components/tutorials/VideoPlayer.tsx` | YouTube embed + IFrame API tracking |
| `frontend/src/components/tutorials/ProgressBar.tsx` | Accessible progress bar |
| `frontend/src/components/tutorials/BadgeShelf.tsx` | Badge grid |
| `frontend/src/lib/api/tutorials.ts` | Typed API client |
| `frontend/src/lib/tutorial-progress.ts` | Pure reporting/formatting logic |
| `tests/test_tutorials.py` | 50 backend tests |
| `frontend/src/tests/tutorial-progress.test.ts` | 29 unit tests |
| `frontend/e2e/tutorials.spec.ts` | 18 e2e tests × 2 browsers |

**Modified**

| File | Change |
|---|---|
| `app/db/models.py` | `TutorialProgress` model + `User.tutorial_progress` |
| `app/main.py` | Registers the tutorials router |
| `app/api/admin.py` | `GET /admin/tutorials/completions` (aggregate only) |
| `frontend/src/components/shell/nav.ts` | The `Learn` nav entry |
| `frontend/src/lib/api/types.ts` | Tutorial types |

---

## API

Full reference: [`docs/api/endpoints.md`](../api/endpoints.md).

| Endpoint | Purpose |
|---|---|
| `GET /tutorials?q=&level=` | Catalogue + this user's progress, summary, badges |
| `GET /tutorials/{slug}` | One tutorial + progress |
| `PUT /tutorials/{slug}/progress` | Report a playback position |
| `POST /tutorials/{slug}/complete` | Mark finished without watching |
| `DELETE /tutorials/{slug}/progress` | Reset to not-started |
| `GET /admin/tutorials/completions` | Aggregate counts — admin, numbers only |

All of them are behind `get_current_user`, so Feature 1's
`EMAIL_NOT_VERIFIED` gate applies here too — for free, which is the point of
having put that check in one dependency rather than per router.

---

## Adding the real videos

1. Open `app/services/tutorials.py`.
2. For each entry, set `youtube_id` to the 11-character id from the URL:
   ```
   https://www.youtube.com/watch?v=AbCdEfGhIjK
                                   ^^^^^^^^^^^
   ```
   and set `duration_seconds` if you know it.
   ```python
   Tutorial(
       slug="getting-started-with-leadpilot",   # <- DO NOT CHANGE
       title="Getting Started with Vectorae",
       ...
       youtube_id="AbCdEfGhIjK",                # <- was None
       duration_seconds=424,                    # <- optional
   ),
   ```
3. **Never change a `slug`.** It is what progress rows point at; renaming one
   orphans every user's progress for that video.
4. Deploy. No migration, no SQL, no admin step.

Unlisted videos work — the embed does not require a public video. It must not
be *Private*, which blocks embedding entirely.

---

## How to test it locally

```bash
python -m app.db.migrate          # applies 0016
python -m uvicorn app.main:app --port 8000
cd frontend && npm run dev        # http://localhost:3000/learn
```

Sign in, open **Learn**, and check:

- Three sections with three tutorials each; overall progress reads `0 / 9`.
- Typing `apollo` narrows to one card; `deliverability` (a description-only
  word) finds *Scaling Your Pipeline*; `zzz` shows the empty state.
- The level buttons filter; the overall progress **does not move** while
  filtering.
- **Watch** on any tutorial shows the "not published yet" panel (all videos are
  placeholders) — not a broken YouTube frame.
- **Mark complete** moves progress to `1 / 9` and advances the Beginner badge.
- Completing all three Beginner videos earns *Beginner Complete* and nothing
  else.
- **Reset progress** takes the count back down and revokes the badge.

```bash
curl -s localhost:8000/tutorials -H "Authorization: Bearer <token>" | python -m json.tool | head -40
curl -s -X POST localhost:8000/tutorials/understanding-your-icp/complete \
     -H "Authorization: Bearer <token>"
```

### The test suite

```bash
python -m pytest tests/test_tutorials.py -v            # 50
python -m pytest tests/ --ignore=tests/integration     # 559
cd frontend && npx vitest run                          # 185
cd frontend && NEXT_PUBLIC_API_URL=https://api.example.com npx next build \
  && npx playwright test e2e/tutorials.spec.ts         # 36 (18 × 2 browsers)
```

---

## How to test it in production

1. Deploy, then confirm the migration applied:
   ```sql
   SELECT version_num FROM alembic_version;   -- 0016_tutorial_progress
   SELECT count(*) FROM tutorial_progress;    -- 0 on first deploy
   ```
2. Sign in and open **Learn**. The nine tutorials must render.
3. Mark one complete, reload the page, confirm it is still complete
   (proves the write reached the database, not just React state).
4. Sign in as a **second** user and confirm they see `0 / 9` — progress is
   per user.
5. As an admin, `GET /admin/tutorials/completions` and confirm the counts move
   and that the response contains **no** user identifiers.
6. Reset the test progress when done.

---

## Common errors and fixes

| Symptom | Cause | Fix |
|---|---|---|
| `/learn` 404s | Frontend not rebuilt — this is a static export, so a new route requires `next build` | Rebuild and redeploy the frontend |
| Learn tab missing | Same | Same |
| Every tutorial says "not published yet" | Expected — all `youtube_id` are `None` | [Add the real videos](#adding-the-real-videos) |
| Tutorials return 403 `EMAIL_NOT_VERIFIED` | Feature 1's gate; the account is unverified | Verify the email, or see the Feature 1 kill switch |
| `404 unknown tutorial` | A slug in the URL is not in the catalogue — usually a slug that was renamed | Restore the slug; renaming orphans progress |
| A user's progress vanished after a deploy | A slug was renamed | Restore the old slug. Rows are orphaned, not deleted, so the progress returns |
| Video shows YouTube's "Video unavailable" | The id is wrong, or the video is **Private** (Unlisted is fine) | Fix the id, or set the video to Unlisted |
| Player renders but progress never advances | The IFrame API script is blocked (ad blocker / CSP) | The UI falls back to a plain iframe and says progress tracking is unavailable; "Mark complete" still works |
| Progress bar shows 100% for an unwatched video | Would indicate a NaN width | Covered by tests — report it, it is a bug |
| Admin endpoint 403s | Not an admin | `python -m app.cli.create_admin`, or set `ADMIN_EMAIL` |

---

## Rollback procedure

### Level 1 — hide the tab (no deploy of the backend)

Remove the `Learn` entry from `frontend/src/components/shell/nav.ts` and
rebuild the frontend. The API stays up and progress is preserved; the section
is simply unreachable from the UI.

### Level 2 — revert the code, keep the schema

```bash
git reset --hard f307b68      # BACKUP: Pre-tutorial-section anchor
```
File-level copies of the 5 modified files are at
`C:\tmp\leadpilot-backups\20260829_183822_pre-tutorial-section\`.

Leaving migration 0016 applied is safe: an unused table affects nothing.

### Level 3 — drop the table

```bash
alembic downgrade 0015_email_verification
```
**Lossy:** this discards every user's watch progress, and therefore every
earned badge (badges are derived from progress, so there is nothing else to
lose). It touches nothing outside this feature. Take a `pg_dump` first — see
[`docs/backup/backup-procedure.md`](../backup/backup-procedure.md).

---

## Outstanding

| # | Item | Severity |
|---|---|---|
| 1 | **All nine videos are placeholders.** The section renders, tracks progress and awards badges, but there is nothing to watch. | HIGH — content, not code |
| 2 | **Real playback tracking is UNVERIFIED.** With no real `youtube_id`, the IFrame API branch of `VideoPlayer.tsx` has never run against a real video. The reporting *decision* logic is unit-tested (29 tests) and the embed URL construction is asserted, but "a real video plays and its position is recorded" cannot be proven until step 1 is done. | MEDIUM |
| 3 | Progress is not shown in onboarding or anywhere outside `/learn`. | LOW |
| 4 | No admin UI for the completion counts — the endpoint exists, no page consumes it. | LOW |
