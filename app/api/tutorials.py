"""Learn Vectorae — the tutorial section (Feature 2).

GET    /tutorials                      catalogue + this user's progress, with
                                       optional ?q= search and ?level= filter
GET    /tutorials/{slug}               one tutorial + progress
PUT    /tutorials/{slug}/progress      report a playback position
POST   /tutorials/{slug}/complete      mark finished explicitly
DELETE /tutorials/{slug}/progress      reset one video to not-started

Every route is user-scoped through get_current_user, so progress is PRIVATE
per user by construction -- there is no route here that can read another
user's rows, not even for an admin. Admins get aggregate completion counts
from GET /admin/tutorials/completions, which returns numbers only and never
names a video a specific person watched.

The catalogue lives in the `tutorial_catalogue` table (migration 0018) and is
managed from /admin/tutorials. EVERY query on this router filters
is_published, so a draft tutorial is invisible here -- an unpublished slug is
a 404 exactly like a slug that does not exist, which is what stops an admin
accidentally previewing work-in-progress to users through a shared URL.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.base import get_db
from app.db.models import TutorialProgress, User
from app.services import tutorials as catalogue

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tutorials", tags=["tutorials"])

# A video is "watched" at 90%, not 100%.
#
# Almost nobody reaches the final frame: end cards, credits and the habit of
# clicking away once the useful part is over all stop playback short. A 100%
# rule would leave users who genuinely finished the course sitting at
# "8 of 9 complete" with no way to earn the badge except scrubbing to the very
# end -- which teaches them to scrub, not to watch.
COMPLETION_THRESHOLD_PERCENT = 90.0


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------


class ProgressIn(BaseModel):
    """A playback position report from the player."""

    position_seconds: int = Field(ge=0, le=86_400)
    # The player knows the real duration once metadata loads; the catalogue
    # may not know it at all. Optional so an early report is still accepted.
    duration_seconds: int | None = Field(default=None, ge=1, le=86_400)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    """Serialise a timestamp as an ISO string that is ALWAYS UTC-aware.

    SQLite has no timezone-aware storage: a DateTime(timezone=True) column is
    written aware and read back NAIVE. PostgreSQL returns it aware. So the same
    endpoint emitted "...T13:43:59.637443" for a value it had just written in
    memory and "...T13:43:59.637443+00:00" for the same value re-read a moment
    later -- a client comparing the two would see a completion time change on
    its own.

    Caught by test_completed_at_keeps_the_FIRST_completion_time, which is the
    only place the two paths are compared directly. Normalising here means the
    API's output does not depend on which database is underneath it.
    """
    if value is None:
        return None
    aware = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return aware.isoformat()


def _require_tutorial(db: Session, slug: str):
    """Resolve a slug to a PUBLISHED tutorial, or 404.

    Unpublished is indistinguishable from non-existent on purpose: an admin
    who shares a draft URL must not be able to expose it, and the status code
    must not become an oracle for "does this draft exist?".
    """
    tutorial = catalogue.get_tutorial(db, slug)
    if tutorial is None:
        raise HTTPException(status_code=404, detail="unknown tutorial")
    return tutorial


def _progress_map(db: Session, user: User) -> dict[str, TutorialProgress]:
    """Every progress row this user has, keyed by slug.

    One query for the whole page rather than one per tutorial -- the list
    endpoint would otherwise issue ten queries to render nine cards.
    """
    rows = db.execute(
        select(TutorialProgress).where(TutorialProgress.user_id == user.id)
    ).scalars().all()
    return {row.tutorial_slug: row for row in rows}


def _progress_out(row: TutorialProgress | None) -> dict:
    """Serialise progress. A MISSING ROW IS NOT AN ERROR -- it is 'not started'.

    Returning a zeroed shape rather than null means the client never has to
    branch on "has this user ever touched this video", and no row has to be
    created just so the UI can render a progress bar at 0%.
    """
    if row is None:
        return {
            "position_seconds": 0,
            "duration_seconds": None,
            "percent": 0.0,
            "completed": False,
            "completed_at": None,
            "last_watched_at": None,
            "started": False,
        }
    return {
        "position_seconds": row.position_seconds,
        "duration_seconds": row.duration_seconds,
        "percent": round(row.percent, 2),
        "completed": row.completed,
        "completed_at": _iso(row.completed_at),
        "last_watched_at": _iso(row.last_watched_at),
        "started": True,
    }


def _tutorial_out(tutorial, row: TutorialProgress | None) -> dict:
    return {**catalogue.tutorial_out(tutorial), "progress": _progress_out(row)}


def _completed_slugs(progress: dict[str, TutorialProgress]) -> set[str]:
    return {slug for slug, row in progress.items() if row.completed}


def _summary(db: Session, progress: dict[str, TutorialProgress]) -> dict:
    """Counts over every PUBLISHED tutorial, never over a filtered result set.

    Two separate rules, both load-bearing:

    * It ignores ?q= and ?level=. The summary answers "how far through the
      course am I", so it must not move while the user types in the search box.

    * It counts only PUBLISHED tutorials. Progress rows can outlive
      unpublishing, and counting a hidden tutorial in the denominator would
      show "3 / 9" on a page displaying six -- arithmetic the user cannot
      check. Completions of a now-hidden tutorial are excluded from the
      numerator for the same reason, and are NOT deleted: republish and they
      come back.
    """
    done = _completed_slugs(progress)
    published = catalogue.list_tutorials(db)
    published_slugs = {t.slug for t in published}
    total = len(published)

    by_level = {}
    for level in catalogue.LEVELS:
        slugs = [t.slug for t in published if t.level == level]
        by_level[level] = {
            "label": catalogue.LEVEL_LABELS[level],
            "total": len(slugs),
            "completed": sum(1 for s in slugs if s in done),
        }

    visible_done = done & published_slugs
    return {
        "total": total,
        "completed": len(visible_done),
        "percent": round(100.0 * len(visible_done) / total, 1) if total else 0.0,
        "by_level": by_level,
    }


def _badges(db: Session, progress: dict[str, TutorialProgress]) -> list[dict]:
    """Derive badge state from progress. Nothing about badges is stored.

    A stored badge row can disagree with the progress meant to justify it, and
    then there is no honest answer to "why does this user have this badge?".
    Derived, a badge is exactly as true as the data behind it.

    `earned_at` is the completion time of the LAST video that earned it, which
    is the moment the badge became true.
    """
    done = _completed_slugs(progress)
    published = catalogue.list_tutorials(db)
    out = []
    for badge in catalogue.badge_definitions():
        # Requirements are the PUBLISHED tutorials only. `bool(required)` below
        # is what stops an empty catalogue from making every badge vacuously
        # earned -- all() over an empty list is True, so with nothing published
        # a brand-new user would be handed "Vectorae Certified".
        required = [t.slug for t in published
                    if badge.level is None or t.level == badge.level]
        earned = bool(required) and all(s in done for s in required)
        earned_at = None
        if earned:
            times = [
                progress[s].completed_at for s in required
                if progress.get(s) and progress[s].completed_at
            ]
            if times:
                # _iso-normalised BEFORE max(): comparing a naive datetime to
                # an aware one raises TypeError, and a table can hold both if
                # rows were written under different databases.
                earned_at = max(
                    t if t.tzinfo else t.replace(tzinfo=timezone.utc)
                    for t in times
                ).isoformat()
        out.append({
            "slug": badge.slug,
            "label": badge.label,
            "description": badge.description,
            "level": badge.level,
            "earned": earned,
            "earned_at": earned_at,
            "required_total": len(required),
            "required_completed": sum(1 for s in required if s in done),
        })
    return out


def _get_or_create(db: Session, user: User, slug: str) -> TutorialProgress:
    """Fetch this user's row for `slug`, creating it if absent.

    The IntegrityError branch is not defensive padding: a video player fires
    progress updates every few seconds, and two in flight together both see
    "no row" and both insert. The UNIQUE (user_id, tutorial_slug) constraint
    turns that into an error instead of a duplicate, and the loser of the race
    simply re-reads the row the winner created.
    """
    row = db.execute(
        select(TutorialProgress).where(
            TutorialProgress.user_id == user.id,
            TutorialProgress.tutorial_slug == slug,
        )
    ).scalars().first()
    if row is not None:
        return row

    row = TutorialProgress(user_id=user.id, tutorial_slug=slug)
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        row = db.execute(
            select(TutorialProgress).where(
                TutorialProgress.user_id == user.id,
                TutorialProgress.tutorial_slug == slug,
            )
        ).scalars().one()
    return row


def _mark_complete(row: TutorialProgress, when: datetime) -> None:
    """Idempotent. completed_at keeps the FIRST completion time.

    Re-watching a finished video must not move the timestamp -- it is the date
    the user earned it, and badge `earned_at` is computed from these.
    """
    if not row.completed:
        row.completed = True
        row.completed_at = when
    row.percent = 100.0


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------


@router.get("")
def list_tutorials(
    q: str | None = Query(default=None, max_length=200,
                          description="Case-insensitive search over title and description"),
    level: str | None = Query(default=None,
                              description="beginner | intermediate | advanced"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """The catalogue with this user's progress merged in.

    `tutorials` respects ?q= and ?level=; `summary` and `badges` deliberately
    do NOT -- they describe the whole course, so they must not change while
    the user is typing in the search box.

    An unknown ?level= returns an empty list rather than a 422: it is a filter
    on a browse screen, and a validation error is a strange answer to a
    typo in a URL someone shared.
    """
    progress = _progress_map(db, user)
    found = catalogue.list_tutorials(db, query=q, level=level)
    return {
        "tutorials": [_tutorial_out(t, progress.get(t.slug)) for t in found],
        "levels": [
            {"level": lv, "label": catalogue.LEVEL_LABELS[lv]}
            for lv in catalogue.LEVELS
        ],
        "summary": _summary(db, progress),
        "badges": _badges(db, progress),
        "query": {"q": q, "level": level},
    }


@router.get("/{slug}")
def get_tutorial(slug: str, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)) -> dict:
    tutorial = _require_tutorial(db, slug)
    row = db.execute(
        select(TutorialProgress).where(
            TutorialProgress.user_id == user.id,
            TutorialProgress.tutorial_slug == slug,
        )
    ).scalars().first()
    return _tutorial_out(tutorial, row)


@router.put("/{slug}/progress")
def update_progress(slug: str, body: ProgressIn,
                    db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)) -> dict:
    """Report a playback position.

    Called repeatedly by the player, so it must be cheap and safe to repeat.

    position_seconds takes the LATEST value (it answers "where do I resume").
    percent takes the MAXIMUM (it answers "how much have I seen"), so scrubbing
    backwards cannot erase watched progress or undo a completion.

    Crossing COMPLETION_THRESHOLD_PERCENT marks the video complete. Completion
    is never revoked here -- only DELETE .../progress does that.
    """
    tutorial = _require_tutorial(db, slug)
    row = _get_or_create(db, user, slug)
    now = _now()

    row.position_seconds = body.position_seconds
    if body.duration_seconds:
        row.duration_seconds = body.duration_seconds

    duration = row.duration_seconds
    if duration:
        # Clamped: a player can report a position a shade past its own
        # duration, and 100.4% would be nonsense in the UI.
        pct = min(100.0, 100.0 * body.position_seconds / duration)
        row.percent = max(row.percent, pct)

    row.last_watched_at = now
    if row.percent >= COMPLETION_THRESHOLD_PERCENT:
        _mark_complete(row, now)

    db.commit()
    return _tutorial_out(tutorial, row)


@router.post("/{slug}/complete")
def complete_tutorial(slug: str, db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)) -> dict:
    """Mark a tutorial finished without watching it to the threshold.

    Needed for two real cases, not just convenience: a video that is still a
    placeholder cannot be watched at all, and a user who already knows the
    material should be able to clear it and reach the badge.
    """
    tutorial = _require_tutorial(db, slug)
    row = _get_or_create(db, user, slug)
    _mark_complete(row, _now())
    row.last_watched_at = _now()
    db.commit()
    return _tutorial_out(tutorial, row)


@router.delete("/{slug}/progress")
def reset_progress(slug: str, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)) -> dict:
    """Reset one tutorial to not-started. The only thing that clears a badge.

    Deletes the row rather than zeroing it, so "not started" has exactly one
    representation in the database instead of two that have to agree.

    204 would be the tidier status, but the client needs the reset shape back
    to update its cache without a second round trip, so this returns 200 with
    the tutorial.
    """
    tutorial = _require_tutorial(db, slug)
    row = db.execute(
        select(TutorialProgress).where(
            TutorialProgress.user_id == user.id,
            TutorialProgress.tutorial_slug == slug,
        )
    ).scalars().first()
    if row is not None:
        db.delete(row)
        db.commit()
    return _tutorial_out(tutorial, None)
