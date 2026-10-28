"""Sitemap and robots.txt for the Vectorae marketing site.

Serves the LIVE versions of the two files Google reads first. The static
export (app/services/website_builder.py::export_static_site) writes its own
copies alongside the exported pages; these two functions are what
GET /site/sitemap.xml and GET /site/robots.txt return, so a page published a
minute ago is discoverable before anyone runs an export.

THE THREE STATIC URLS are the pages that exist on the marketing site
independently of this database — the front door, how-it-works and pricing.
They are listed first and unconditionally: a sitemap that omits the homepage
because nobody has created a SitePage row for it is worse than no sitemap.
A published page whose slug collides with one of them is not listed twice.
"""

import logging
from xml.sax.saxutils import escape

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import PageStatus, SitePage

logger = logging.getLogger(__name__)

SITE_ORIGIN = "https://leadpilot.io"

# (loc, priority, changefreq). Always present, always first.
STATIC_URLS = (
    (f"{SITE_ORIGIN}/", "1.0", "weekly"),
    (f"{SITE_ORIGIN}/how-it-works/", "0.9", "monthly"),
    (f"{SITE_ORIGIN}/pricing/", "0.9", "monthly"),
)

ROBOTS_TXT = ("User-agent: *\n"
              "Allow: /\n"
              f"Sitemap: {SITE_ORIGIN}/sitemap.xml\n")


def _url_block(loc: str, lastmod: str | None, changefreq: str, priority: str) -> str:
    # escape() because a slug reaches this from user input and an unescaped
    # "&" makes the whole document malformed XML, which Search Console
    # rejects outright rather than partially.
    parts = [f"    <loc>{escape(loc)}</loc>"]
    if lastmod:
        parts.append(f"    <lastmod>{lastmod}</lastmod>")
    parts.append(f"    <changefreq>{changefreq}</changefreq>")
    parts.append(f"    <priority>{priority}</priority>")
    return "  <url>\n" + "\n".join(parts) + "\n  </url>\n"


def generate_sitemap_xml(db: Session) -> str:
    """The sitemap for the three static URLs plus every published page.

    WHAT IT RETURNS. A sitemaps.org-conforming XML string, declaration and
    namespace included, ready to serve as application/xml.

    WHAT IT NEVER RAISES. Anything. If the database cannot be read, the three
    static URLs are still returned — Google getting a short sitemap is a far
    better failure than Google getting a 500 and backing off the whole site.
    """
    entries = [_url_block(loc, None, changefreq, priority)
               for loc, priority, changefreq in STATIC_URLS]
    seen = {loc for loc, _p, _c in STATIC_URLS}

    try:
        pages = db.execute(
            select(SitePage)
            .where(SitePage.status == PageStatus.PUBLISHED,
                   SitePage.workspace_id.is_(None))  # our pages, not customers'
            .order_by(SitePage.slug)
        ).scalars().all()
    except Exception:  # noqa: BLE001 -- see the docstring
        logger.exception("sitemap: could not read published pages")
        pages = []

    for page in pages:
        slug = (page.slug or "").strip().strip("/")
        loc = f"{SITE_ORIGIN}/{slug}" if slug else f"{SITE_ORIGIN}/"
        if loc in seen:
            continue
        seen.add(loc)
        lastmod = None
        if page.published_at is not None:
            try:
                lastmod = page.published_at.date().isoformat()
            except Exception:  # noqa: BLE001 -- a bad timestamp is not a bad page
                lastmod = None
        entries.append(_url_block(loc, lastmod, "weekly", "0.8"))

    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + "".join(entries)
            + "</urlset>\n")


def generate_robots_txt() -> str:
    """robots.txt: allow everything, and point at the sitemap.

    WHAT IT RETURNS. The file contents as a string.

    WHAT IT NEVER RAISES. Anything — it is a constant.
    """
    return ROBOTS_TXT
