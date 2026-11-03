"""Old-name public URLs are renamed and 301-redirected (evals/vectorae GAP-BR-01/02).

The marketing site is a static export served by Vercel, so redirects live in
frontend/vercel.json (next.config redirects do not run in an export). Pins:
  * every old URL, with and without its trailing slash, has exactly ONE
    redirect, permanent (301), to a page the site actually builds (a path in
    site-pages.json, the sitemap/canonical source) or a file in public/;
  * no destination is itself redirected (single hop, no chains or loops);
  * the old slugs appear nowhere in the pages, content, help articles or
    public files -- only as redirect sources;
  * chat messages that stored a chunk id of the renamed help article, or the
    legacy FAQ id, still resolve to the current article.
Offline: reads repository files only.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
VERCEL = json.loads((FRONTEND / "vercel.json").read_text())
REDIRECTS = VERCEL.get("redirects", [])
SITE_PATHS = {p["path"] for p in json.loads(
    (FRONTEND / "src/content/site-pages.json").read_text())["pages"]}

COMPETITORS = ("apollo", "instantly", "smartlead", "clay", "artisan")
RENAMED_PAGES = {"/what-is-leadpilot/": "/what-is-vectorae/",
                 "/help/what-is-leadpilot/": "/help/what-is-vectorae/",
                 **{f"/compare/leadpilot-vs-{c}/": f"/compare/vectorae-vs-{c}/"
                    for c in COMPETITORS}}
RENAMED_MEDIA = {f"/media/leadpilot-walkthrough{s}": f"/media/vectorae-walkthrough{s}"
                 for s in ("-poster.jpg", ".mp4", ".webm", ".en.vtt")}
OLD = re.compile(r"what-is-leadpilot|leadpilot-vs-|leadpilot-walkthrough")


def _rules_for(source: str) -> list[dict]:
    return [r for r in REDIRECTS if r["source"] == source]


def _exists(dest: str) -> bool:
    if dest in SITE_PATHS:
        return True
    # Help articles are generated per article id from docs/help-center.
    m = re.fullmatch(r"/help/([\w-]+)/", dest)
    if m and list((ROOT / "docs/help-center").glob(f"*-{m.group(1)}.md")):
        return True
    return (FRONTEND / "public" / dest.lstrip("/")).is_file()


@pytest.mark.parametrize("old,new", sorted(RENAMED_PAGES.items()))
def test_each_old_page_has_one_permanent_redirect_in_both_forms(old, new):
    for source in (old, old.rstrip("/")):
        rules = _rules_for(source)
        assert len(rules) == 1, f"{source}: {len(rules)} redirect rules"
        assert rules[0]["destination"] == new
        assert rules[0].get("statusCode") == 301   # not Vercel's default 308
        assert "permanent" not in rules[0]          # statusCode alone decides


@pytest.mark.parametrize("old,new", sorted(RENAMED_MEDIA.items()))
def test_each_old_media_file_redirects_to_the_renamed_file(old, new):
    (rule,) = _rules_for(old)
    assert rule["destination"] == new and rule["statusCode"] == 301


def test_every_destination_exists_and_is_never_redirected_again():
    sources = {r["source"] for r in REDIRECTS}
    for rule in REDIRECTS:
        dest = rule["destination"]
        assert _exists(dest), f"{dest} is not a page or file the site serves"
        assert dest not in sources and dest.rstrip("/") not in sources, f"chain at {dest}"
        assert not OLD.search(dest)


def test_the_new_pages_are_in_the_sitemap_and_the_old_ones_are_not():
    assert {"/what-is-vectorae/", *(f"/compare/vectorae-vs-{c}/" for c in COMPETITORS)} \
        <= SITE_PATHS
    assert not [p for p in SITE_PATHS if OLD.search(p)]


def test_old_slugs_survive_only_as_redirect_sources():
    roots = [FRONTEND / "src", FRONTEND / "public", FRONTEND / "video-src",
             ROOT / "docs/help-center"]
    hits = []
    for root in roots:
        for path in root.rglob("*"):
            if path.is_file() and path.suffix in {".ts", ".tsx", ".json", ".md", ".txt",
                                                  ".html", ".mjs", ".vtt", ".xml", ".js"}:
                text = path.read_text(encoding="utf-8", errors="ignore")
                hits += [f"{path.relative_to(ROOT)}" for _ in OLD.finditer(text)]
            if OLD.search(path.name):
                hits.append(f"{path.relative_to(ROOT)} (file name)")
    assert hits == []


def test_stored_citations_of_the_renamed_article_still_resolve():
    from app.services import help_center

    new = help_center.chunk_by_id("what-is-vectorae#what-does-vectorae-do-start-to-finish")
    assert new is not None
    old_chunk = help_center.resolve_citation(
        "what-is-leadpilot#what-does-vectorae-do-start-to-finish")
    assert old_chunk is not None and old_chunk.id == new.id
    assert help_center.resolve_citation("what-is-leadpilot").id == new.id   # legacy FAQ id
    assert help_center.resolve_citation("what-is-leadpilot#no-such-heading") is None
