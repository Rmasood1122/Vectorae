"""Seed the five foundational SEO pages for leadpilot.io.

    python scripts/seed_seo_pages.py

Creates the homepage plus four articles as DRAFT SitePage rows and queues a
generation task for each. It writes nothing to the public site: every page
lands in `draft`, and a human publishes it (PATCH /site/pages/{id}/publish)
after reading what the generator produced. The whole point of the feature is
that marketing copy is reviewed before it ranks.

These five are global pages -- workspace_id is NULL -- because leadpilot.io
is the product's own marketing site and belongs to no customer workspace.

IDEMPOTENT. A slug that already exists is skipped, not rewritten: re-running
this after adding a sixth page must not silently regenerate the five that are
already live. Use POST /site/pages/{id}/regenerate to rewrite one on purpose.
"""

import sys
from pathlib import Path

# Run as `python scripts/seed_seo_pages.py` and sys.path[0] is scripts/, not
# the repo root, so `from app...` would raise ModuleNotFoundError. Same line
# as scripts/activate_with_keys.py.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.base import SessionLocal            # noqa: E402
from app.db.models import PageStatus, PageType, SitePage  # noqa: E402

PAGES = [
    {
        "page_type": PageType.LANDING,
        "slug": "",
        "title": "Vectorae — Outbound Pipeline for Agency Founders",
        "meta_title": "Vectorae — Outbound Pipeline for Agency Founders",
        "meta_description": (
            "Vectorae removes you from your own pipeline. Automated outreach "
            "system for boutique agency founders. US, UK, CA, AU. Book a call."),
        "target_keyword": "outbound pipeline for agencies",
        "secondary_keywords": ["client acquisition for agency founders",
                               "b2b outreach automation", "agency pipeline system"],
        "brief": (
            "Homepage for Vectorae. Hero: the founder is trapped "
            "delivering and cannot prospect at the same time. The pipeline runs "
            "when they push it. It stops when they stop. Three sections: the "
            "problem, how Vectorae works (72-step research pipeline, human "
            "senders, learning loop), who it is for (boutique agency founders "
            "3-30 staff). One CTA: book a call. Tone: founder to founder, "
            "direct, no hype, no buzzwords."),
    },
    {
        "page_type": PageType.BLOG,
        "slug": "blog/agency-founder-prospecting-delivery-problem",
        "title": "Why Agency Founders Cannot Prospect and Deliver at the Same Time",
        "meta_title": "Why Agency Founders Can't Prospect and Deliver | Vectorae",
        "meta_description": (
            "If your pipeline stops when delivery gets heavy, you do not have a "
            "prospecting problem. You have a structural problem. Here is why "
            "and the fix."),
        "target_keyword": "agency founder pipeline problem",
        "secondary_keywords": ["feast famine agency", "how to get clients agency",
                               "founder led sales problem"],
        "brief": (
            "1400 word article. Open with the month the pipeline dried up while "
            "the founder was heads-down on delivery. Explain why this is "
            "structural not behavioural. The mathematical impossibility of doing "
            "both at the same time. What the top 1 percent of agency founders do "
            "differently. End with the only permanent fix. No product pitch in "
            "body. One CTA at end only."),
    },
    {
        "page_type": PageType.COMPARISON,
        "slug": "blog/apollo-clay-leadpilot-comparison",
        "title": "Apollo vs Clay vs Vectorae — Which One Runs Without You?",
        "meta_title": "Apollo vs Clay vs Vectorae Comparison 2026 | Vectorae",
        "meta_description": (
            "Apollo gives you data. Clay gives you enrichment. Neither gives you "
            "a pipeline that runs without you. Honest comparison for agency "
            "founders."),
        "target_keyword": "apollo clay alternative",
        "secondary_keywords": ["apollo alternative agency", "clay alternative",
                               "instantly alternative", "smartlead alternative"],
        "brief": (
            "1800 word honest comparison. Section 1: what Apollo does well and "
            "where it stops — no strategy layer, no LinkedIn compliance, no human "
            "sender. Section 2: what Clay does well and why it requires a "
            "full-time operator to use. Section 3: what Instantly and Smartlead "
            "do and why spray-and-pray kills deliverability. Section 4: the gap "
            "none of them fill — a pipeline that runs without the founder in it. "
            "Section 5: comparison table with 5 criteria rows and 4 tool columns. "
            "Be fair to competitors — no dishonest claims."),
    },
    {
        "page_type": PageType.BLOG,
        "slug": "blog/cold-email-not-working-agency",
        "title": "Cold Email Is Not Dead — You Are Just Doing It Wrong",
        "meta_title": "Why Cold Email Is Not Working for Your Agency | Vectorae",
        "meta_description": (
            "One founder sent 1000 cold emails and got zero responses. He changed "
            "his entire strategy. Here is what actually works for boutique agency "
            "outbound."),
        "target_keyword": "cold outreach not working agency",
        "secondary_keywords": ["cold email agency founders", "b2b outreach tips",
                               "linkedin outreach agency"],
        "brief": (
            "1300 word article. Open with a founder who sent 1000 cold emails and "
            "got zero replies then changed strategy to executive dinners. Explain "
            "what generic outreach signals to a busy prospect. The research-first "
            "approach that changes reply rates. What a 4-sentence LinkedIn DM that "
            "actually gets replies looks like. The difference between outreach "
            "activity and a real pipeline. End with one CTA only."),
    },
    {
        "page_type": PageType.BLOG,
        "slug": "blog/client-pipeline-works-during-delivery",
        "title": "How to Build a Client Pipeline That Works During Delivery",
        "meta_title": "Build a Pipeline That Works During Delivery | Vectorae",
        "meta_description": (
            "The best agency founders do not prospect harder. They build systems "
            "that prospect for them. Here is the architecture that works during "
            "your busiest months."),
        "target_keyword": "client acquisition system for agency founders",
        "secondary_keywords": ["automated client acquisition", "agency outbound system",
                               "pipeline that runs without founder"],
        "brief": (
            "1600 word how-to article. Why willpower cannot fix a structural "
            "system problem. The 3 components of a self-running pipeline: research "
            "layer, outreach layer, follow-up layer. What each component does in "
            "practice. How the system compounds over time. What the first 30 days "
            "look like for a new client. End with CTA. No product pitch in body "
            "text."),
    },
]


def seed(dispatch: bool = True) -> dict:
    """Create any missing seed page and queue its generation.

    WHAT IT RETURNS. {"seeded": [slugs], "skipped": [slugs]}.

    WHAT IT NEVER RAISES. A broker that refuses the generation job: the page
    row is still created and `POST /site/pages/{id}/regenerate` will pick it
    up. Database errors DO propagate — a seed script that cannot write should
    say so loudly rather than report success.
    """
    session = SessionLocal()
    seeded: list[str] = []
    skipped: list[str] = []
    try:
        for spec in PAGES:
            slug = spec["slug"]
            existing = session.query(SitePage).filter(SitePage.slug == slug).first()
            label = slug or "(homepage)"
            if existing is not None:
                print(f"Skipped: {label}")
                skipped.append(slug)
                continue

            page = SitePage(
                workspace_id=None,          # global: leadpilot.io itself
                page_type=spec["page_type"],
                slug=slug,
                title=spec["title"],
                meta_title=spec["meta_title"],
                meta_description=spec["meta_description"],
                target_keyword=spec["target_keyword"],
                secondary_keywords=list(spec["secondary_keywords"]),
                brief=spec["brief"],
                html_content="",
                status=PageStatus.DRAFT,
            )
            session.add(page)
            session.commit()

            if dispatch:
                from app.workers import site_tasks  # noqa: PLC0415

                site_tasks.enqueue_generation(page.id)
            print(f"Seeded: {label} — generation queued")
            seeded.append(slug)
    finally:
        session.close()

    print(f"Done. {len(seeded)} seeded, {len(skipped)} skipped.")
    return {"seeded": seeded, "skipped": skipped}


if __name__ == "__main__":
    # --no-dispatch creates the rows without queueing generation, for a
    # deployment that wants the pages present before the worker is running.
    seed(dispatch="--no-dispatch" not in sys.argv)
