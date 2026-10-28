"""Website builder — AI-generated, SEO-optimised marketing pages.

Three functions, three very different kinds of work:

  generate_page_html   ONE Claude call that writes a complete HTML5 document
  score_page_seo       pure Python, zero model calls, fully deterministic
  export_static_site   writes published pages out as deployable static files

WHY THE SCORER HAS NO MODEL CALL. An SEO score that a model produces is a
different number every run for the same page, which makes "did my edit help?"
unanswerable and makes the score useless as a gate. Every rule in
score_page_seo is a structural fact about the document -- how many h1 tags,
is there a canonical link -- so it is counted, not judged. It uses stdlib
html.parser and nothing else: BeautifulSoup and lxml are deliberately not
dependencies of this system.

WHY THE GENERATOR RETURNS A WHOLE DOCUMENT. These pages are exported as flat
files and served without an application in front of them. There is no layout
to inherit from and no template engine at request time, so the model produces
the entire document including its inline CSS. That is also why the prompt
bans external stylesheets and CDN calls: a marketing page that depends on a
third-party host is a marketing page that breaks when that host does.
"""

import logging
import os
import re
from datetime import datetime, timezone
from html.parser import HTMLParser

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import PageStatus, SitePage
from app.services.anthropic_client import get_client

logger = logging.getLogger(__name__)

SITE_ORIGIN = "https://leadpilot.io"

# The feature specification named max_tokens=1000. That ceiling cannot
# produce what the same specification asks for: a complete HTML5 document
# carrying a 1,300-1,800 word article plus inline CSS is roughly 3,000-6,000
# output tokens, and 1,800 words of prose alone is already ~2,400. The
# Anthropic gateway raises TruncatedResponseError when a response stops at
# the ceiling (app/services/anthropic_client.py explains why a cut-off answer
# must never be stored), so 1000 would fail EVERY call rather than returning
# a shorter page. 8000 fits the longest brief in scripts/seed_seo_pages.py
# with headroom and stays under the gateway's non-streaming limit.
MAX_TOKENS = 8000

PAGE_TYPES = ("landing", "blog", "case_study", "comparison", "location")

SYSTEM_PROMPT = (
    "You are an expert SEO content engineer and conversion copywriter. "
    "You write HTML pages that rank on Google and turn readers into leads "
    "for the business described under BRAND CONTEXT, in that business's own "
    "voice — direct, specific, no buzzwords. "
    "Banned words: solution, platform, synergy, game-changer, leverage, "
    "excited, seamless, disruptive, revolutionary, innovative. "
    "Respond ONLY with a JSON object. No prose outside JSON. "
    "No markdown fences."
)

_USER_PROMPT = """PAGE TYPE: {page_type}
PRIMARY KEYWORD: {target_keyword}
SECONDARY KEYWORDS: {secondary_keywords}
PAGE TITLE: {title}
CONTENT BRIEF: {brief}

EXISTING PAGES TO LINK TO:
{existing_pages}

BRAND CONTEXT:
{brand_context}

HTML STRUCTURE REQUIREMENTS:
Complete HTML5 document with DOCTYPE html
head must contain:
  meta charset UTF-8
  meta viewport width=device-width initial-scale=1.0
  title tag: {meta_title} | {brand_name}
  meta name description content {meta_description}
  meta property og:title content {meta_title}
  meta property og:description content {meta_description}
  meta property og:type content website
  link rel canonical href {origin}/{slug}
  script type application/ld+json with schema markup
  Inline CSS only — no external stylesheets, no CDN calls
body must contain:
  Exactly one h1 containing the primary keyword naturally
  Minimum 4 h2 tags for major sections
  h3 tags for subsections where appropriate
  Primary keyword in first 100 words of body text
  Primary keyword density 1-2 percent
  Alt text on every img tag
  Internal links to at least 2 existing pages
  footer with copyright and links to /privacy and /terms

SCHEMA MARKUP BY PAGE TYPE:
blog:        Article schema with author {author}, datePublished {today}
case_study:  Article plus Review schema
comparison:  Article schema
landing:     Organization plus WebPage schema
location:    LocalBusiness schema with address region

OUTPUT — respond with ONLY this JSON object:
{{
  "html_content": "complete HTML string",
  "word_count": integer,
  "reading_time_mins": integer,
  "internal_links": ["slug1", "slug2"],
  "schema_markup": {{ JSON-LD object }}
}}"""

# Phase 3 item 2. The API needs every object closed, so schema_markup cannot
# be a free-form object: it lists the JSON-LD properties the five page types
# above call for (Article, Review, Organization, WebPage, LocalBusiness). The
# code keeps any dict, so none of them is required.
_LD_THING = {
    "type": "object",
    "properties": {"@type": {"type": "string"}, "name": {"type": "string"},
                   "url": {"type": "string"}},
    "additionalProperties": False,
}
_LD_ADDRESS = {
    "type": "object",
    "properties": {"@type": {"type": "string"}, "streetAddress": {"type": "string"},
                   "addressLocality": {"type": "string"},
                   "addressRegion": {"type": "string"},
                   "postalCode": {"type": "string"},
                   "addressCountry": {"type": "string"}},
    "additionalProperties": False,
}
_LD_REVIEW = {
    "type": "object",
    "properties": {
        "@type": {"type": "string"},
        "name": {"type": "string"},
        "reviewBody": {"type": "string"},
        "author": _LD_THING,
        "itemReviewed": _LD_THING,
        "reviewRating": {
            "type": "object",
            "properties": {"@type": {"type": "string"},
                           "ratingValue": {"type": ["number", "string"]},
                           "bestRating": {"type": ["number", "string"]}},
            "additionalProperties": False,
        },
    },
    "additionalProperties": False,
}
PAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "html_content": {"type": "string"},
        "word_count": {"type": "integer"},
        "reading_time_mins": {"type": "integer"},
        "internal_links": {"type": "array", "items": {"type": "string"}},
        "schema_markup": {
            "type": "object",
            "properties": {
                "@context": {"type": "string"},
                "@type": {"type": ["string", "array"], "items": {"type": "string"}},
                "name": {"type": "string"},
                "headline": {"type": "string"},
                "description": {"type": "string"},
                "url": {"type": "string"},
                "inLanguage": {"type": "string"},
                "datePublished": {"type": "string"},
                "dateModified": {"type": "string"},
                "author": _LD_THING,
                "publisher": _LD_THING,
                "mainEntityOfPage": {"type": "string"},
                "areaServed": {"type": "string"},
                "telephone": {"type": "string"},
                "address": _LD_ADDRESS,
                "review": _LD_REVIEW,
                "itemReviewed": _LD_THING,
            },
            "additionalProperties": False,
        },
    },
    "required": ["html_content", "word_count", "reading_time_mins", "internal_links",
                 "schema_markup"],
    "additionalProperties": False,
}


# BR-D1 (docs/program/brand/rename-inventory.md §4). Pages belong either to
# our own marketing site (workspace_id NULL) or to a customer's workspace.
# Only our own pages may carry our brand, founder and ICP; a customer's pages
# are written for the customer's business, named by the caller, and must
# never name us as their author or provider.
OWN_SITE_BRAND = "Vectorae"
OWN_SITE_AUTHOR = "Rehan Masood"
_OWN_SITE_CONTEXT = """Product: Vectorae, built by Rehan Masood and Ahmad Shahid
Founder: Rehan Masood
ICP: Boutique agency founders, 3-30 staff
Markets: US, UK, Canada, Australia, New Zealand, Ireland, Singapore
Core pain: Their pipeline runs when they push it. It stops when they stop.
Voice: Founder to founder. Direct. Specific. No hype. No buzzwords."""


def brand_context(brand_name: str | None) -> tuple[str, str, str]:
    """(context block, title-tag brand, schema author) for one page.

    None means a page of our own marketing site. A name means a customer's
    page: the block names only that business and tells the model the
    audience and offer come from the brief, so nothing of ours (brand,
    founder, ICP) reaches a customer's site.
    """
    if brand_name is None:
        return _OWN_SITE_CONTEXT, OWN_SITE_BRAND, OWN_SITE_AUTHOR
    name = " ".join(str(brand_name).split())[:100] or "this business"
    context = (f"Business: {name}\n"
               "Audience, offer and pain points: take them from the CONTENT BRIEF only.\n"
               f"Write as {name}. Do not name any other company or product as the "
               "author, owner or provider of this page.")
    return context, name, name


def _format_existing_pages(existing_pages: list[dict]) -> str:
    """The internal-linking menu, one page per line.

    Formatted rather than dumped as JSON because the model is choosing from
    this list, not parsing it, and a readable list produces better anchors.
    """
    lines = []
    for page in existing_pages or []:
        if not isinstance(page, dict):
            continue
        slug = str(page.get("slug", "")).strip()
        title = str(page.get("title") or page.get("target_keyword") or "").strip()
        lines.append(f"- /{slug}  ({title})" if title else f"- /{slug}")
    return "\n".join(lines) or "(none yet — this is the first page)"


def generate_page_html(
    page_type: str,
    title: str,
    target_keyword: str,
    secondary_keywords: list[str],
    brief: str,
    existing_pages: list[dict],
    *,
    slug: str = "",
    meta_title: str = "",
    meta_description: str = "",
    brand_name: str | None = None,
) -> dict:
    """Write one complete, SEO-structured HTML page with Claude.

    `brand_name` is the business the page is for: None for a page of our own
    marketing site, the workspace's name for a customer's page
    (brand_context explains why the two never mix).

    WHAT IT DOES. Builds the brand-and-structure prompt, makes ONE call
    through app/services/anthropic_client.py, and coerces the reply into the
    five fields the caller stores.

    WHAT IT RETURNS. {"html_content": str, "word_count": int,
    "reading_time_mins": int, "internal_links": list[str],
    "schema_markup": dict}. `word_count` and `reading_time_mins` are
    recomputed from the delivered HTML when the model's own numbers are
    missing or nonsense, because they are displayed to the user and a
    self-reported word count is not evidence of anything.

    WHAT IT RAISES. ValueError when the reply is not JSON or carries no
    usable html_content -- the caller (app/workers/site_tasks.py) turns that
    into a logged failure that leaves the existing page untouched. It also
    propagates whatever the Anthropic gateway raises, including
    TruncatedResponseError: a half-written page must never be stored.
    """
    context, title_brand, author = brand_context(brand_name)
    data = get_client().complete_json(
        system=SYSTEM_PROMPT,
        prompt=_USER_PROMPT.format(
            brand_context=context,
            brand_name=title_brand,
            author=author,
            page_type=page_type,
            target_keyword=target_keyword,
            secondary_keywords=", ".join(secondary_keywords or []) or "(none)",
            title=title,
            brief=brief,
            existing_pages=_format_existing_pages(existing_pages),
            meta_title=meta_title or title,
            meta_description=meta_description or "",
            origin=SITE_ORIGIN,
            slug=slug,
            today=datetime.now(timezone.utc).date().isoformat(),
        ),
        max_tokens=MAX_TOKENS,
        schema=PAGE_SCHEMA,
    )

    html_content = str((data or {}).get("html_content") or "").strip()
    if not html_content:
        raise ValueError("the generator returned no html_content")

    words = len(visible_text(html_content).split())
    try:
        word_count = int(data.get("word_count"))
        if word_count <= 0:
            raise ValueError
    except (TypeError, ValueError):
        word_count = words
    try:
        reading_time = int(data.get("reading_time_mins"))
        if reading_time <= 0:
            raise ValueError
    except (TypeError, ValueError):
        # 225 wpm is the usual figure for adult silent reading of prose.
        reading_time = max(1, round((word_count or words) / 225))

    raw_links = data.get("internal_links")
    internal_links = [
        str(link).strip().lstrip("/")
        for link in (raw_links if isinstance(raw_links, (list, tuple)) else [])
        if isinstance(link, str) and str(link).strip()
    ]
    schema_markup = data.get("schema_markup")
    if not isinstance(schema_markup, dict):
        schema_markup = {}

    return {
        "html_content": html_content,
        "word_count": word_count,
        "reading_time_mins": reading_time,
        "internal_links": internal_links,
        "schema_markup": schema_markup,
    }


# --------------------------------------------------------------------------
# SEO scoring — deterministic, stdlib only
# --------------------------------------------------------------------------

# (points, key, failure message). The weights total exactly 100.
SEO_RULES = (
    (15, "single_h1", "No <h1>, or more than one — a page needs exactly one."),
    (10, "h1_keyword", "The <h1> does not contain the target keyword."),
    (10, "title", "No <title>, or it is 70 characters or longer (Google truncates)."),
    (10, "meta_description",
     'No <meta name="description">, or it is 165 characters or longer.'),
    (10, "keyword_early",
     "The target keyword does not appear in the first 100 words of body text."),
    (10, "canonical", 'No <link rel="canonical"> — duplicate URLs will compete.'),
    (10, "json_ld", 'No <script type="application/ld+json"> schema markup.'),
    (10, "internal_links", "Fewer than 2 internal links (<a href=\"/...\">)."),
    (5, "og_title", 'No <meta property="og:title"> — shares will look bare.'),
    (5, "og_description", 'No <meta property="og:description">.'),
    (5, "h2_count", "Fewer than 4 <h2> section headings."),
)

_TITLE_MAX = 70
_DESCRIPTION_MAX = 165
_EARLY_WORDS = 100
_MIN_INTERNAL_LINKS = 2
_MIN_H2 = 4

# Tags whose text is markup, not prose, and must never count as body copy.
_NON_TEXT_TAGS = {"script", "style", "noscript", "template"}


class _SeoParser(HTMLParser):
    """Collects exactly the structural facts SEO_RULES asks about.

    Deliberately tolerant: convert_charrefs is on and unknown/broken markup is
    ignored rather than raising, because this runs over model-generated HTML
    and a scorer that crashes on a stray tag tells the user nothing.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.h1_texts: list[str] = []
        self.h2_count = 0
        self.title_text = ""
        self.meta_description = None
        self.og_title = False
        self.og_description = False
        self.canonical = False
        self.json_ld = False
        self.internal_links = 0
        self.body_text: list[str] = []
        self._capture: list[str] = []
        self._suppress = 0
        self._in_body = False

    # -- helpers ----------------------------------------------------------
    @staticmethod
    def _attr(attrs, name):
        for key, value in attrs:
            if key.lower() == name:
                return value or ""
        return None

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in _NON_TEXT_TAGS:
            self._suppress += 1
            if tag == "script":
                typ = (self._attr(attrs, "type") or "").strip().lower()
                if typ == "application/ld+json":
                    self.json_ld = True
            return
        if tag == "body":
            self._in_body = True
        elif tag == "h1":
            self.h1_texts.append("")
            self._capture.append("h1")
        elif tag == "h2":
            self.h2_count += 1
        elif tag == "title":
            self._capture.append("title")
        elif tag == "meta":
            name = (self._attr(attrs, "name") or "").strip().lower()
            prop = (self._attr(attrs, "property") or "").strip().lower()
            content = self._attr(attrs, "content")
            if name == "description" and content is not None:
                self.meta_description = content
            if prop == "og:title" and (content or "").strip():
                self.og_title = True
            if prop == "og:description" and (content or "").strip():
                self.og_description = True
        elif tag == "link":
            rel = (self._attr(attrs, "rel") or "").strip().lower()
            if "canonical" in rel.split() and (self._attr(attrs, "href") or "").strip():
                self.canonical = True
        elif tag == "a":
            href = (self._attr(attrs, "href") or "").strip()
            # A site-root-relative link. "//host" is protocol-relative and
            # points off-site, so it is not an internal link.
            if href.startswith("/") and not href.startswith("//"):
                self.internal_links += 1

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in _NON_TEXT_TAGS:
            self._suppress = max(0, self._suppress - 1)
            return
        if tag == "body":
            self._in_body = False
        elif tag in ("h1", "title") and self._capture and self._capture[-1] == tag:
            self._capture.pop()

    def handle_data(self, data):
        if self._suppress:
            return
        if self._capture:
            current = self._capture[-1]
            if current == "h1" and self.h1_texts:
                self.h1_texts[-1] += data
            elif current == "title":
                self.title_text += data
        if self._in_body:
            self.body_text.append(data)


def _parse(html_content: str) -> _SeoParser:
    parser = _SeoParser()
    try:
        parser.feed(html_content or "")
        parser.close()
    except Exception:  # noqa: BLE001 -- a scorer must never crash on bad markup
        logger.warning("SEO scorer hit malformed HTML; scoring what it parsed")
    return parser


def visible_text(html_content: str) -> str:
    """The page's body prose, with markup, scripts and styles removed.

    Used both by the scorer and by generate_page_html's word count. Falls
    back to the whole document when there is no <body> so a fragment is still
    measurable. Never raises.
    """
    parser = _parse(html_content)
    text = " ".join(parser.body_text)
    if not text.strip():
        text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html_content or "",
                      flags=re.I | re.S)
        text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def score_page_seo(html_content: str, target_keyword: str) -> dict:
    """Score one page against eleven on-page SEO rules.

    WHAT IT DOES. Parses the document with stdlib html.parser and counts
    structural facts. No model call, no network, no third-party parser — the
    same HTML always scores the same, which is what makes the number usable
    as a gate and comparable across edits.

    WHAT IT RETURNS. {"score": int 0-100, "issues": list[str]} — one
    plain-English issue per failed rule, in SEO_RULES order (highest-value
    problems first).

    WHAT IT NEVER RAISES. Anything. Empty, truncated or malformed HTML scores
    what it earns and reports the rest as issues; an unparseable page is a
    zero with eleven issues, not an exception.
    """
    keyword = (target_keyword or "").strip().lower()
    parser = _parse(html_content or "")

    body = visible_text(html_content or "")
    early = " ".join(body.split()[:_EARLY_WORDS]).lower()
    title = parser.title_text.strip()
    description = (parser.meta_description or "").strip()

    passed = {
        "single_h1": len(parser.h1_texts) == 1,
        "h1_keyword": bool(keyword) and any(
            keyword in text.lower() for text in parser.h1_texts),
        "title": bool(title) and len(title) < _TITLE_MAX,
        "meta_description": bool(description) and len(description) < _DESCRIPTION_MAX,
        "keyword_early": bool(keyword) and keyword in early,
        "canonical": parser.canonical,
        "json_ld": parser.json_ld,
        "internal_links": parser.internal_links >= _MIN_INTERNAL_LINKS,
        "og_title": parser.og_title,
        "og_description": parser.og_description,
        "h2_count": parser.h2_count >= _MIN_H2,
    }

    score = sum(points for points, key, _msg in SEO_RULES if passed.get(key))
    issues = [msg for _points, key, msg in SEO_RULES if not passed.get(key)]
    return {"score": max(0, min(100, score)), "issues": issues}


# --------------------------------------------------------------------------
# Static export
# --------------------------------------------------------------------------


def _sitemap_entry(slug: str, published_at) -> str:
    loc = f"{SITE_ORIGIN}/{slug}" if slug else f"{SITE_ORIGIN}/"
    lastmod = ""
    if published_at is not None:
        try:
            lastmod = f"    <lastmod>{published_at.date().isoformat()}</lastmod>\n"
        except Exception:  # noqa: BLE001 -- a bad timestamp is not a bad page
            lastmod = ""
    return ("  <url>\n"
            f"    <loc>{loc}</loc>\n"
            f"{lastmod}"
            "    <changefreq>weekly</changefreq>\n"
            "    <priority>0.8</priority>\n"
            "  </url>\n")


ROBOTS_TXT = ("User-agent: *\n"
              "Allow: /\n"
              f"Sitemap: {SITE_ORIGIN}/sitemap.xml\n")


def export_static_site(db: Session, output_dir: str) -> dict:
    """Write our marketing site's published pages out as static files.

    WHAT IT DOES. For each published SitePage with no workspace (ours; a
    customer's page is not part of our site), writes html_content to
    {output_dir}/{slug}/index.html (or {output_dir}/index.html for the
    homepage, whose slug is the empty string), then writes sitemap.xml and
    robots.txt beside them.

    WHAT IT RETURNS. {"pages_exported": int, "output_dir": str,
    "index_generated": bool} — `index_generated` says whether a homepage
    (empty slug) was among them, because a site export with no index.html is
    a site that 404s at its own front door and the caller should know.

    WHAT IT NEVER RAISES. Anything. A page that cannot be written is logged
    and skipped so one bad row cannot cost the other forty their export; a
    failure to write the sitemap leaves the pages on disk. Errors are counted
    but the export is always reported.
    """
    exported = 0
    index_generated = False
    slugs: list[tuple[str, object]] = []

    try:
        pages = db.execute(
            select(SitePage)
            .where(SitePage.status == PageStatus.PUBLISHED,
                   SitePage.workspace_id.is_(None))
            .order_by(SitePage.slug)
        ).scalars().all()
    except Exception:  # noqa: BLE001 -- see the docstring
        logger.exception("static export could not list published pages")
        return {"pages_exported": 0, "output_dir": output_dir,
                "index_generated": False}

    for page in pages:
        try:
            slug = (page.slug or "").strip().strip("/")
            path = (os.path.join(output_dir, "index.html") if not slug
                    else os.path.join(output_dir, *slug.split("/"), "index.html"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(page.html_content or "")
            exported += 1
            index_generated = index_generated or not slug
            slugs.append((slug, page.published_at))
        except Exception:  # noqa: BLE001 -- see the docstring
            logger.exception("static export failed for page %s (%s)",
                             getattr(page, "id", None), getattr(page, "slug", None))

    try:
        os.makedirs(output_dir, exist_ok=True)
        xml = ('<?xml version="1.0" encoding="UTF-8"?>\n'
               '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
               + "".join(_sitemap_entry(slug, when) for slug, when in slugs)
               + "</urlset>\n")
        with open(os.path.join(output_dir, "sitemap.xml"), "w", encoding="utf-8") as h:
            h.write(xml)
        with open(os.path.join(output_dir, "robots.txt"), "w", encoding="utf-8") as h:
            h.write(ROBOTS_TXT)
    except Exception:  # noqa: BLE001 -- see the docstring
        logger.exception("static export could not write sitemap.xml / robots.txt")

    logger.info("static export: %s pages -> %s (index=%s)",
                exported, output_dir, index_generated)
    return {"pages_exported": exported, "output_dir": output_dir,
            "index_generated": index_generated}
