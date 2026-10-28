"""Seed the nine Learn Vectorae tutorials into `tutorial_catalogue`.

    python scripts/seed_tutorials.py

Writes to whatever DATABASE_URL points at. There is no separate "local"
database behind this -- check .env before running it.

IDEMPOTENT. A tutorial whose title OR slug already exists is skipped, never
rewritten: once Ahmad has added a real youtube_id through the admin UI,
re-running this must not blank it again.

HOW THE BRIEF MAPS ONTO THE REAL SCHEMA (app/db/models.py TutorialCatalogue)
  duration_minutes -> duration_seconds (x 60)
  order            -> sort_order. The API orders by level first, so a global
                      1-9 still sorts correctly inside each level.
  video_url        -> youtube_id = None. The column holds the 11-character id,
                      not an embed URL (String(32) -- an embed URL does not
                      fit), and a fake id makes VideoPlayer render YouTube's
                      own "Video unavailable" error. NULL renders the honest
                      "not published yet" panel instead. See
                      scripts/TUTORIAL_VIDEO_URLS.md for adding real ids.
  slug             -> fixed below, not derived from the title. Slugs are
                      permanent: tutorial_progress points at them by string, so
                      renaming one orphans every user's progress.

is_published = True. Migration 0018 seeds drafts on purpose, but these nine
exist to fill the Learn tab now, with videos to follow. The rows migration
0018 created are left untouched and stay unpublished.
"""

import sys
from pathlib import Path

# Run as `python scripts/seed_tutorials.py` and sys.path[0] is scripts/, not
# the repo root, so `from app...` would raise ModuleNotFoundError.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import or_  # noqa: E402

from app.db.base import SessionLocal  # noqa: E402
from app.db.models import TutorialCatalogue  # noqa: E402
from app.services.tutorials import LEVELS  # noqa: E402

TUTORIALS = [
    # -- Beginner -------------------------------------------------------------
    {
        "slug": "welcome-to-leadpilot",
        "title": "Welcome to Vectorae — Your First 10 Minutes",
        "level": "beginner",
        "duration_minutes": 10,
        "order": 1,
        "description": (
            "Start here. This tutorial walks you through the Vectorae dashboard, "
            "explains what each section does, and shows you the order to complete "
            "your setup."),
    },
    {
        "slug": "setting-up-your-icp",
        "title": "Setting Up Your ICP — Who You Are Targeting",
        "level": "beginner",
        "duration_minutes": 12,
        "order": 2,
        "description": (
            "Your Ideal Customer Profile is the foundation of every campaign. This "
            "tutorial shows you how to define your ICP inside Vectorae, what fields "
            "matter most, and how to use the DSS scoring system to qualify prospects."),
    },
    {
        "slug": "connecting-your-linkedin-account",
        "title": "Connecting Your LinkedIn Account",
        "level": "beginner",
        "duration_minutes": 8,
        "order": 3,
        "description": (
            "Vectorae uses a human sender model — your LinkedIn account sends the "
            "messages. This tutorial walks you through the connection process, what "
            "permissions are needed, and what LinkedIn compliance limits apply."),
    },
    # -- Intermediate ---------------------------------------------------------
    {
        "slug": "reading-your-pipeline-health-score",
        "title": "Reading Your Pipeline Health Score",
        "level": "intermediate",
        "duration_minutes": 15,
        "order": 4,
        "description": (
            "Your Pipeline Health Score updates every 6 hours. This tutorial explains "
            "what each component means — reply rate, sequence completion, lead "
            "freshness, active conversations — and what actions to take in each "
            "health band."),
    },
    {
        "slug": "understanding-reply-intelligence",
        "title": "Understanding Reply Intelligence",
        "level": "intermediate",
        "duration_minutes": 18,
        "order": 5,
        "description": (
            "When a prospect replies, Vectorae classifies it automatically: Buying "
            "Signal, Objection, Not Now, or Wrong Person. This tutorial shows you how "
            "to read each classification, review the AI-drafted response, and decide "
            "whether to send or edit."),
    },
    {
        "slug": "running-your-first-campaign",
        "title": "Running Your First Campaign",
        "level": "intermediate",
        "duration_minutes": 20,
        "order": 6,
        "description": (
            "End-to-end walkthrough of launching a campaign. Building the prospect "
            "list, reviewing research, approving the sequence, setting send limits, "
            "and reading the first results."),
    },
    # -- Advanced -------------------------------------------------------------
    {
        "slug": "founder-voice-cloning",
        "title": "Founder Voice Cloning — Personalizing at Scale",
        "level": "advanced",
        "duration_minutes": 22,
        "order": 7,
        "description": (
            "Vectorae can write messages in your voice — not a template voice. This "
            "tutorial shows you how to submit your LinkedIn posts for analysis, review "
            "your voice profile, and understand how it affects outgoing message "
            "quality."),
    },
    {
        "slug": "competitor-displacement-alerts",
        "title": "Competitor Displacement Alerts",
        "level": "advanced",
        "duration_minutes": 16,
        "order": 8,
        "description": (
            "When a prospect posts about frustration with Apollo, Clay, Instantly, or "
            "any competitor — Vectorae alerts you within 12 hours with a pre-written "
            "DM. This tutorial shows you how to review alerts, act on them, and "
            "dismiss false positives."),
    },
    {
        "slug": "reading-your-roi-dashboard",
        "title": "Reading Your ROI Dashboard and Sharing Proof Cards",
        "level": "advanced",
        "duration_minutes": 14,
        "order": 9,
        "description": (
            "Your ROI dashboard tracks meetings booked, pipeline value, messages sent, "
            "time saved, and revenue attributed. This tutorial shows you how to read "
            "each metric, set date ranges, and download your shareable proof card for "
            "LinkedIn or client reports."),
    },
]

assert len({t["slug"] for t in TUTORIALS}) == len(TUTORIALS), "duplicate slug"
assert all(t["level"] in LEVELS for t in TUTORIALS), "unknown level"


def seed() -> dict:
    """Insert every missing tutorial in ONE transaction.

    All-or-nothing: a failure part-way through leaves the catalogue exactly as
    it was, rather than a Learn tab showing four of nine.
    """
    session = SessionLocal()
    seeded: list[str] = []
    skipped: list[str] = []
    try:
        for spec in TUTORIALS:
            existing = session.query(TutorialCatalogue).filter(or_(
                TutorialCatalogue.title == spec["title"],
                TutorialCatalogue.slug == spec["slug"],
            )).first()
            if existing is not None:
                print(f"Skipped: {spec['title']}")
                skipped.append(spec["slug"])
                continue

            session.add(TutorialCatalogue(
                slug=spec["slug"],
                title=spec["title"],
                description=spec["description"],
                level=spec["level"],
                youtube_id=None,
                duration_seconds=spec["duration_minutes"] * 60,
                sort_order=spec["order"],
                is_published=True,
            ))
            print(f"Seeded: {spec['title']}")
            seeded.append(spec["slug"])
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    print(f"Done. {len(seeded)} seeded, {len(skipped)} skipped.")
    return {"seeded": seeded, "skipped": skipped}


if __name__ == "__main__":
    seed()
