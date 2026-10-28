"""Tutorial catalogue access + badge definitions.

WHERE THE CATALOGUE LIVES
-------------------------
In the DATABASE, table `tutorial_catalogue` (migration 0018). It used to live
in this file as a constant; that was reversed on purpose so a video can be
added through the admin UI with no deploy.

What remains here:

  SEED_CATALOGUE   the original nine entries. Migration 0018 inserts them and
                   NOTHING READS THEM AT RUNTIME. They stay so a fresh database
                   seeds identically and the editorial text is reviewable in
                   git rather than only in production rows.

  LEVELS / BADGES  structural, not editorial. Levels are a closed set the UI
                   lays out around, and badges are derived rules, not content
                   -- neither is something an admin should be able to invent
                   from a form.

  query helpers    list_tutorials / get_tutorial / slugs_for_level, all taking
                   a Session.

PUBLISHED VS NOT
Every user-facing query filters is_published. Admin queries do not. That
single distinction is why the helpers take an explicit `include_unpublished`
flag rather than inferring it: a default that silently leaked drafts to users
would be invisible until someone noticed an unfinished tutorial in the Learn
tab.

SLUGS ARE STILL PERMANENT
tutorial_progress rows reference the catalogue by slug with no foreign key
(see migration 0018 for why). Renaming a slug orphans progress, so the admin
API refuses to change one.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "SeedTutorial",
    "LEVELS",
    "LEVEL_LABELS",
    "SEED_CATALOGUE",
    "BADGES",
    "Badge",
    "badge_definitions",
    "list_tutorials",
    "get_tutorial",
    "slugs_for_level",
    "tutorial_out",
    "is_valid_level",
]

# Ordered easiest-first. The API and the UI both rely on this order, so it is
# defined once here rather than being re-sorted at each call site.
LEVELS = ("beginner", "intermediate", "advanced")

LEVEL_LABELS = {
    "beginner": "Beginner",
    "intermediate": "Intermediate",
    "advanced": "Advanced",
}


@dataclass(frozen=True)
class SeedTutorial:
    """One row of the INITIAL catalogue, used only by migration 0018.

    This is not the runtime type any more -- runtime reads
    db.models.TutorialCatalogue. It is kept as a plain frozen dataclass so the
    seed data is readable and diffable without a database.
    """

    slug: str
    title: str
    description: str
    level: str
    order: int
    # None until the real video exists -- see the module docstring.
    youtube_id: str | None = None
    duration_seconds: int | None = None



# ---------------------------------------------------------------------------
# The catalogue
# ---------------------------------------------------------------------------
# `order` is within a level, not global. Slugs are permanent: they are the
# foreign key tutorial_progress rows point at, so RENAMING A SLUG ORPHANS every
# user's progress for that video. Change titles freely; change slugs never.

SEED_CATALOGUE: tuple[SeedTutorial, ...] = (
    # -- Beginner -----------------------------------------------------------
    SeedTutorial(
        slug="getting-started-with-leadpilot",
        title="Getting Started with Vectorae",
        description=(
            "A tour of the platform end to end: what Vectorae automates, how a "
            "strategy becomes a booked meeting, and what to set up first."
        ),
        level="beginner",
        order=1,
    ),
    SeedTutorial(
        slug="setting-up-your-first-campaign",
        title="Setting Up Your First Campaign",
        description=(
            "Create a product, run the intake wizard, and launch your first "
            "outreach sequence without sending anything you did not mean to."
        ),
        level="beginner",
        order=2,
    ),
    SeedTutorial(
        slug="understanding-your-icp",
        title="Understanding Your ICP",
        description=(
            "What an Ideal Customer Profile is, how Vectorae extracts one from "
            "your past clients, and how to correct it when it gets you wrong."
        ),
        level="beginner",
        order=3,
    ),
    # -- Intermediate -------------------------------------------------------
    SeedTutorial(
        slug="advanced-lead-sourcing-with-apollo",
        title="Advanced Lead Sourcing with Apollo",
        description=(
            "Build precise Apollo searches, use enrichment without paying twice "
            "for the same contact, and keep your lead quality high at volume."
        ),
        level="intermediate",
        order=1,
    ),
    SeedTutorial(
        slug="writing-high-converting-dms",
        title="Writing High-Converting DMs",
        description=(
            "The anatomy of a message that gets replies: personalisation that is "
            "real, openers that are not templates, and asks people can say yes to."
        ),
        level="intermediate",
        order=2,
    ),
    SeedTutorial(
        slug="using-the-leadpilot-dashboard",
        title="Using the Vectorae Dashboard",
        description=(
            "Pipeline, campaigns, strategies and settings -- what each view is "
            "for and the fastest path through your daily workflow."
        ),
        level="intermediate",
        order=3,
    ),
    # -- Advanced -----------------------------------------------------------
    SeedTutorial(
        slug="multi-channel-outreach-strategy",
        title="Multi-Channel Outreach Strategy",
        description=(
            "Sequencing email and WhatsApp together: when each channel earns its "
            "place, opt-in rules that keep you compliant, and timing that works."
        ),
        level="advanced",
        order=1,
    ),
    SeedTutorial(
        slug="reading-your-analytics",
        title="Reading Your Analytics",
        description=(
            "Which numbers actually predict booked meetings, which are noise at "
            "your volume, and how to tell a real lift from a small sample."
        ),
        level="advanced",
        order=2,
    ),
    SeedTutorial(
        slug="scaling-your-pipeline",
        title="Scaling Your Pipeline",
        description=(
            "Growing send volume without wrecking deliverability: warm-up, "
            "bounce thresholds, and what to fix first when reply rates fall."
        ),
        level="advanced",
        order=3,
    ),
)


# ---------------------------------------------------------------------------
# Badges
# ---------------------------------------------------------------------------
# Earned state is DERIVED from progress, never stored. A stored badge row can
# disagree with the progress that is supposed to justify it -- and then the
# only honest answer to "why does this user have this badge?" is "we don't
# know". Deriving it means the badge is always exactly as true as the data.


@dataclass(frozen=True)
class Badge:
    slug: str
    label: str
    description: str
    # None = "every tutorial in the catalogue"; otherwise a level name.
    level: str | None


BADGES: tuple[Badge, ...] = (
    Badge("beginner-complete", "Beginner Complete",
          "Finished every Beginner tutorial.", "beginner"),
    Badge("intermediate-complete", "Intermediate Complete",
          "Finished every Intermediate tutorial.", "intermediate"),
    Badge("advanced-complete", "Advanced Complete",
          "Finished every Advanced tutorial.", "advanced"),
    Badge("leadpilot-certified", "Vectorae Certified",
          "Finished every tutorial in the library.", None),
)


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

_BY_SEED_SLUG = {entry.slug: entry for entry in SEED_CATALOGUE}
assert len(_BY_SEED_SLUG) == len(SEED_CATALOGUE), "duplicate slug in SEED_CATALOGUE"
assert all(e.level in LEVELS for e in SEED_CATALOGUE), "unknown level in SEED_CATALOGUE"


def is_valid_level(level: str | None) -> bool:
    return bool(level) and level.strip().lower() in LEVELS


def _order_by():
    """Level order, then sort_order, then title.

    Level is a string column, so ORDER BY level would give
    advanced/beginner/intermediate -- alphabetical, and wrong. The CASE keeps
    the pedagogical order the UI depends on without a second round trip.
    """
    from sqlalchemy import case

    from app.db.models import TutorialCatalogue as T

    level_rank = case(
        {name: index for index, name in enumerate(LEVELS)},
        value=T.level,
        else_=len(LEVELS),
    )
    return (level_rank, T.sort_order, T.title)


def list_tutorials(db, query: str | None = None, level: str | None = None,
                   include_unpublished: bool = False) -> list:
    """Catalogue rows, filtered and ordered.

    `include_unpublished` is explicit and defaults to False. A default that
    leaked drafts would be invisible until an admin noticed an unfinished
    tutorial sitting in a user's Learn tab.

    Search is a case-insensitive LIKE over title AND description, done in SQL
    rather than in Python: the same filter has to behave identically for the
    admin list and the user list, and two implementations of one filter
    diverge. Searching descriptions matters -- "deliverability" appears in no
    title.
    """
    from sqlalchemy import func, or_, select

    from app.db.models import TutorialCatalogue as T

    stmt = select(T)
    if not include_unpublished:
        stmt = stmt.where(T.is_published.is_(True))
    if level:
        stmt = stmt.where(T.level == level.strip().lower())
    needle = (query or "").strip().lower()
    if needle:
        pattern = f"%{needle}%"
        stmt = stmt.where(or_(func.lower(T.title).like(pattern),
                              func.lower(T.description).like(pattern)))
    return list(db.execute(stmt.order_by(*_order_by())).scalars().all())


def get_tutorial(db, slug: str, include_unpublished: bool = False):
    """One row by slug, or None."""
    from sqlalchemy import select

    from app.db.models import TutorialCatalogue as T

    stmt = select(T).where(T.slug == slug)
    if not include_unpublished:
        stmt = stmt.where(T.is_published.is_(True))
    return db.execute(stmt).scalars().first()


def slugs_for_level(db, level: str, include_unpublished: bool = False) -> list[str]:
    return [t.slug for t in list_tutorials(db, level=level,
                                           include_unpublished=include_unpublished)]


def tutorial_out(row) -> dict:
    """Serialise a catalogue row for the API.

    is_placeholder is DERIVED, never stored: it is simply "no youtube_id yet",
    and a second column could disagree with the first.
    """
    return {
        "slug": row.slug,
        "title": row.title,
        "description": row.description,
        "level": row.level,
        "order": row.sort_order,
        "youtube_id": row.youtube_id,
        "duration_seconds": row.duration_seconds,
        "is_placeholder": not row.youtube_id,
        "is_published": row.is_published,
    }


def badge_definitions() -> tuple["Badge", ...]:
    return BADGES
