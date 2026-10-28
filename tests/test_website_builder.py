"""Website builder — API, SEO scorer and static export.

No Claude call is ever made here: the generation path is exercised through
`fake_claude`, and every endpoint test asserts on the QUEUED task rather than
on generated copy. What is pinned is the contract around the model — slug
validation, uniqueness, plan gates, publish rules, the deterministic scorer,
and what lands on disk.
"""

import uuid
from datetime import datetime, timezone

import pytest

from app.db import models as m
from app.services.website_builder import export_static_site, score_page_seo

# A page carrying every element SEO_RULES asks for. Used as the "good" end of
# the scorer's range, and edited down in the tests that remove one element.
GOOD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Outbound Pipeline for Agencies | Acme Agency</title>
<meta name="description" content="Your pipeline runs when you push it and stops when you stop. Here is how to build an outbound pipeline for agencies that does not need you.">
<meta property="og:title" content="Outbound Pipeline for Agencies">
<meta property="og:description" content="A pipeline that runs without the founder in it.">
<meta property="og:type" content="website">
<link rel="canonical" href="https://leadpilot.io/blog/test">
<script type="application/ld+json">{"@context":"https://schema.org","@type":"Article"}</script>
<style>body{font-family:sans-serif}</style>
</head>
<body>
<h1>The outbound pipeline for agencies that runs without you</h1>
<p>Most founders build an outbound pipeline for agencies that only moves when
they push it. It stops the week delivery gets heavy, and it stops for the same
structural reason every time.</p>
<h2>The problem</h2><p>Delivery and prospecting compete for one calendar.</p>
<h2>Why willpower does not fix it</h2><p>It is arithmetic, not discipline.</p>
<h2>What the system does instead</h2><p>Research, outreach, follow-up.</p>
<h3>The research layer</h3><p>Every lead is researched before contact.</p>
<h2>The first thirty days</h2><p>What to expect week by week.</p>
<img src="/img/pipeline.png" alt="A pipeline diagram showing three layers">
<p>Read <a href="/blog/cold-email-not-working-agency">why cold email stops
working</a> and <a href="/blog/apollo-clay-leadpilot-comparison">how the tools
compare</a>.</p>
<footer><p>&copy; 2026 Acme Agency.
<a href="/privacy">Privacy</a> · <a href="/terms">Terms</a></p></footer>
</body>
</html>"""

KEYWORD = "outbound pipeline for agencies"


def _body(**overrides) -> dict:
    body = {
        "page_type": "blog",
        "slug": "blog/test-page",
        "title": "A Test Page About Agency Pipelines",
        "meta_title": "A Test Page About Agency Pipelines | Acme Agency",
        "meta_description": "A test page used to exercise the website builder end to end.",
        "target_keyword": "agency pipeline",
        "secondary_keywords": ["agency outbound"],
        "brief": "A content brief that is comfortably longer than the fifty "
                 "character floor the API enforces on this field.",
        "auto_generate": True,
    }
    body.update(overrides)
    return body


OUR_WORKSPACE_SLUG = "acme-agency"
_OURS = object()   # _make_page default: the test user's workspace


@pytest.fixture(autouse=True)
def builder_plan(db_session, test_user):
    """The website builder is gated off on `free`, which is the fixture's
    default plan. Every test here is about the builder, not the gate -- the
    gate has its own test below. Pages belong to a workspace, so the test
    user owns one; the no-workspace cases have their own tests below."""
    test_user.plan = m.PlanTier.PRO
    db_session.add(m.Workspace(name="Acme Agency", slug=OUR_WORKSPACE_SLUG,
                               owner_user_id=test_user.id))
    db_session.commit()
    return test_user


def _our_workspace_id(db_session):
    return db_session.query(m.Workspace.id).filter(
        m.Workspace.slug == OUR_WORKSPACE_SLUG).scalar()


def _make_page(db_session, **overrides) -> m.SitePage:
    fields = {
        "workspace_id": _OURS,
        "page_type": m.PageType.BLOG,
        "slug": f"blog/{uuid.uuid4().hex[:8]}",
        "title": "Seeded page",
        "meta_title": "Seeded page | Acme Agency",
        "meta_description": "Seeded directly in the database.",
        "target_keyword": KEYWORD,
        "brief": "x" * 60,
        "secondary_keywords": [],
        "html_content": "",
        "status": m.PageStatus.DRAFT,
    }
    fields.update(overrides)
    if fields["workspace_id"] is _OURS:
        fields["workspace_id"] = _our_workspace_id(db_session)
    page = m.SitePage(**fields)
    db_session.add(page)
    db_session.commit()
    return page


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


class TestCreate:
    def test_create_page_success(self, client, db_session, queued_jobs):
        response = client.post("/site/pages", json=_body())

        assert response.status_code == 201
        payload = response.json()
        assert payload["slug"] == "blog/test-page"
        assert payload["status"] == "draft"
        assert payload["generation_status"] == "queued"
        page = db_session.query(m.SitePage).filter(
            m.SitePage.slug == "blog/test-page").one()
        assert page.brief.startswith("A content brief")
        assert queued_jobs["site_generate"] == [str(page.id)]

    def test_create_page_duplicate_slug(self, client, queued_jobs):
        assert client.post("/site/pages", json=_body()).status_code == 201
        second = client.post("/site/pages", json=_body())
        assert second.status_code == 409
        assert second.json()["detail"] == "slug already exists"

    def test_create_page_invalid_slug(self, client):
        assert client.post("/site/pages",
                           json=_body(slug="My Page!!")).status_code == 422

    def test_create_page_slug_with_uppercase(self, client):
        """Two casings of one URL is duplicate content, so uppercase is a 422
        rather than something quietly lowercased behind the user's back."""
        assert client.post("/site/pages",
                           json=_body(slug="Blog/Article")).status_code == 422

    def test_empty_slug_is_the_homepage_and_is_allowed(self, client, queued_jobs):
        response = client.post("/site/pages",
                               json=_body(slug="", page_type="landing"))
        assert response.status_code == 201
        assert response.json()["slug"] == ""

    def test_brief_under_fifty_characters_is_rejected(self, client):
        assert client.post("/site/pages", json=_body(brief="too short")).status_code == 422

    def test_auto_generate_false_queues_nothing(self, client, queued_jobs):
        response = client.post("/site/pages", json=_body(auto_generate=False))
        assert response.status_code == 201
        assert queued_jobs["site_generate"] == []

    def test_the_free_plan_is_gated_out(self, client, db_session, test_user):
        test_user.plan = m.PlanTier.FREE
        db_session.commit()
        response = client.post("/site/pages", json=_body())
        assert response.status_code == 402
        assert response.json()["detail"]["limit"] == "website_builder"


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


class TestRead:
    def test_list_pages_empty(self, client):
        response = client.get("/site/pages")
        assert response.status_code == 200
        assert response.json() == []

    def test_list_pages_with_type_filter(self, client, db_session):
        _make_page(db_session, page_type=m.PageType.BLOG, slug="blog/a")
        _make_page(db_session, page_type=m.PageType.BLOG, slug="blog/b")
        _make_page(db_session, page_type=m.PageType.LANDING, slug="pricing")

        blogs = client.get("/site/pages", params={"page_type": "blog"}).json()

        assert len(blogs) == 2
        assert {p["slug"] for p in blogs} == {"blog/a", "blog/b"}

    def test_get_page_not_found(self, client):
        assert client.get(f"/site/pages/{uuid.uuid4()}").status_code == 404

    def test_get_page_detail(self, client, db_session):
        page = _make_page(db_session, html_content=GOOD_HTML, word_count=420)

        body = client.get(f"/site/pages/{page.id}").json()

        assert body["html_content"] == GOOD_HTML
        assert body["word_count"] == 420
        assert body["brief"]
        assert body["generation_status"] == "complete"
        for field in ("secondary_keywords", "internal_links_json", "seo_issues_json"):
            assert isinstance(body[field], list)

    def test_another_users_page_is_404(self, client, db_session):
        other = m.User(email="stranger-site@example.test", email_verified=True)
        db_session.add(other)
        db_session.flush()
        workspace = m.Workspace(name="Theirs", slug=f"w{uuid.uuid4().hex[:8]}",
                                owner_user_id=other.id)
        db_session.add(workspace)
        db_session.flush()
        page = _make_page(db_session, workspace_id=workspace.id, slug="blog/theirs")

        assert client.get(f"/site/pages/{page.id}").status_code == 404


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_publish_page(self, client, db_session, queued_jobs):
        page = _make_page(db_session, html_content=GOOD_HTML)

        body = client.patch(f"/site/pages/{page.id}/publish").json()

        assert body["status"] == "published"
        assert body["published_at"] is not None
        # A customer's page is not part of our static site.
        assert not queued_jobs["site_export"]

    def test_publishing_one_of_our_pages_re_exports(self, client, db_session,
                                                    test_user, queued_jobs):
        test_user.is_admin = True
        db_session.commit()
        page = _make_page(db_session, workspace_id=None, html_content=GOOD_HTML)

        client.patch(f"/site/pages/{page.id}/publish")

        assert queued_jobs["site_export"], "publishing must trigger a re-export"

    def test_publishing_an_ungenerated_page_is_refused(self, client, db_session):
        """An empty document on a public URL and in the sitemap is worse than
        an unpublished page."""
        page = _make_page(db_session, html_content="")
        response = client.patch(f"/site/pages/{page.id}/publish")
        assert response.status_code == 409

    def test_unpublish_page(self, client, db_session):
        page = _make_page(db_session, html_content=GOOD_HTML)
        client.patch(f"/site/pages/{page.id}/publish")

        body = client.patch(f"/site/pages/{page.id}/unpublish").json()

        assert body["status"] == "archived"

    def test_regenerate_bumps_the_version_and_queues(self, client, db_session,
                                                     queued_jobs):
        page = _make_page(db_session, html_content=GOOD_HTML)

        body = client.post(f"/site/pages/{page.id}/regenerate").json()

        assert body == {"status": "queued", "generation_version": 2}
        assert queued_jobs["site_generate"] == [str(page.id)]

    def test_export_endpoint_queues_the_task(self, client, db_session, test_user,
                                             queued_jobs):
        test_user.is_admin = True
        db_session.commit()
        body = client.post("/site/export").json()
        assert body["status"] == "queued"
        assert queued_jobs["site_export"]


# ---------------------------------------------------------------------------
# SEO + download
# ---------------------------------------------------------------------------


class TestSeoEndpoint:
    def test_seo_score_endpoint(self, client, db_session):
        page = _make_page(db_session, html_content=GOOD_HTML,
                          target_keyword=KEYWORD, word_count=300)

        body = client.get(f"/site/pages/{page.id}/seo").json()

        assert isinstance(body["score"], int)
        assert isinstance(body["issues"], list)
        assert body["target_keyword"] == KEYWORD
        assert body["word_count"] == 300
        db_session.refresh(page)
        assert page.seo_score == body["score"]

    def test_download_page(self, client, db_session):
        page = _make_page(db_session, html_content=GOOD_HTML, slug="blog/downloadable")
        client.patch(f"/site/pages/{page.id}/publish")

        response = client.get(f"/site/pages/{page.id}/download")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "attachment" in response.headers["content-disposition"]
        # The slug is a path; the filename must not be.
        assert 'filename="blog-downloadable.html"' in response.headers["content-disposition"]
        assert response.text == GOOD_HTML


# ---------------------------------------------------------------------------
# Public routes
# ---------------------------------------------------------------------------


class TestPublicRoutes:
    def test_sitemap_xml_public(self, anon_client, db_session):
        _make_page(db_session, workspace_id=None, slug="blog/published-one",
                   html_content=GOOD_HTML,
                   status=m.PageStatus.PUBLISHED,
                   published_at=datetime.now(timezone.utc))

        response = anon_client.get("/site/sitemap.xml")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/xml")
        assert "urlset" in response.text
        assert "https://leadpilot.io/blog/published-one" in response.text
        # The three static URLs are always present.
        assert "https://leadpilot.io/pricing/" in response.text

    def test_sitemap_excludes_drafts(self, anon_client, db_session):
        _make_page(db_session, workspace_id=None, slug="blog/still-a-draft",
                   html_content=GOOD_HTML)
        assert "blog/still-a-draft" not in anon_client.get("/site/sitemap.xml").text

    def test_robots_txt_public(self, anon_client):
        response = anon_client.get("/site/robots.txt")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/plain")
        assert "User-agent: *" in response.text
        assert "Sitemap: https://leadpilot.io/sitemap.xml" in response.text


# ---------------------------------------------------------------------------
# Bulk
# ---------------------------------------------------------------------------


class TestBulk:
    def test_bulk_create_pages_success(self, client, db_session, queued_jobs):
        payload = {"pages": [_body(slug=f"blog/bulk-{i}") for i in range(3)]}

        response = client.post("/site/pages/bulk", json=payload)

        assert response.status_code == 201
        assert len(response.json()) == 3
        assert db_session.query(m.SitePage).count() == 3
        assert len(queued_jobs["site_generate"]) == 3

    def test_bulk_create_slug_conflict(self, client, db_session, queued_jobs):
        """All or nothing: a partial bulk create cannot be retried, because
        the same payload would then fail on the pages that succeeded."""
        _make_page(db_session, slug="blog/already-here")
        payload = {"pages": [_body(slug="blog/new-one"),
                             _body(slug="blog/already-here")]}

        response = client.post("/site/pages/bulk", json=payload)

        assert response.status_code == 409
        assert response.json()["detail"]["conflicting_slugs"] == ["blog/already-here"]
        assert db_session.query(m.SitePage).count() == 1   # only the pre-existing one
        assert queued_jobs["site_generate"] == []

    def test_bulk_rejects_slugs_that_repeat_within_the_request(self, client,
                                                               queued_jobs):
        payload = {"pages": [_body(slug="blog/same"), _body(slug="blog/same")]}
        response = client.post("/site/pages/bulk", json=payload)
        assert response.status_code == 409
        assert "blog/same" in response.json()["detail"]["conflicting_slugs"]

    def test_bulk_is_capped_at_ten(self, client):
        payload = {"pages": [_body(slug=f"blog/many-{i}") for i in range(11)]}
        assert client.post("/site/pages/bulk", json=payload).status_code == 422


# ---------------------------------------------------------------------------
# Unit: the scorer
# ---------------------------------------------------------------------------


class TestScorePageSeoUnit:
    def test_score_page_seo_unit(self):
        result = score_page_seo(GOOD_HTML, KEYWORD)
        assert result["score"] >= 80, result["issues"]
        assert result["issues"] == []
        assert result["score"] <= 100

    def test_is_deterministic(self):
        """The reason it is not a model call: the same page must score the
        same every time, or 'did my edit help?' is unanswerable."""
        assert score_page_seo(GOOD_HTML, KEYWORD) == score_page_seo(GOOD_HTML, KEYWORD)

    def test_missing_h1_loses_points_and_names_the_problem(self):
        stripped = GOOD_HTML.replace("<h1>", "<p>").replace("</h1>", "</p>")
        result = score_page_seo(stripped, KEYWORD)
        assert result["score"] < score_page_seo(GOOD_HTML, KEYWORD)["score"]
        assert any("h1" in issue.lower() for issue in result["issues"])

    def test_two_h1_tags_is_also_a_failure(self):
        doubled = GOOD_HTML.replace("<body>", "<body><h1>Another heading</h1>")
        assert any("h1" in issue.lower()
                   for issue in score_page_seo(doubled, KEYWORD)["issues"])

    def test_missing_canonical_loses_points_and_names_the_problem(self):
        stripped = GOOD_HTML.replace(
            '<link rel="canonical" href="https://leadpilot.io/blog/test">', "")
        result = score_page_seo(stripped, KEYWORD)
        assert result["score"] < score_page_seo(GOOD_HTML, KEYWORD)["score"]
        assert any("canonical" in issue.lower() for issue in result["issues"])

    def test_an_over_long_title_fails_the_title_rule(self):
        long_title = "x" * 80
        page = GOOD_HTML.replace("Outbound Pipeline for Agencies | Acme Agency", long_title)
        assert any("70 characters" in issue
                   for issue in score_page_seo(page, KEYWORD)["issues"])

    def test_empty_html_scores_zero_with_every_issue(self):
        result = score_page_seo("", KEYWORD)
        assert result["score"] == 0
        assert len(result["issues"]) == 11

    def test_malformed_html_does_not_raise(self):
        result = score_page_seo("<h1>unclosed <div><<>", KEYWORD)
        assert 0 <= result["score"] <= 100

    def test_script_text_is_not_counted_as_body_copy(self):
        """A keyword buried in a <script> is not on the page."""
        page = ("<html><body><script>var k = 'outbound pipeline for agencies';"
                "</script><p>Nothing here.</p></body></html>")
        assert any("first 100 words" in issue
                   for issue in score_page_seo(page, KEYWORD)["issues"])

    def test_offsite_links_are_not_internal_links(self):
        """A protocol-relative "//host" href points off-site, and an absolute
        one obviously does. Neither counts toward the internal-link rule."""
        offsite = ('<html><body><h1>x</h1>'
                   '<a href="//evil.example.com/x">a</a>'
                   '<a href="https://example.com/y">b</a>'
                   '</body></html>')
        assert any("internal links" in issue.lower()
                   for issue in score_page_seo(offsite, KEYWORD)["issues"])

        internal = ('<html><body><h1>x</h1>'
                    '<a href="/privacy">a</a><a href="/terms">b</a>'
                    '</body></html>')
        assert not any("internal links" in issue.lower()
                       for issue in score_page_seo(internal, KEYWORD)["issues"])


# ---------------------------------------------------------------------------
# Unit: the static export
# ---------------------------------------------------------------------------


class TestExportStaticSiteUnit:
    def test_export_static_site_unit(self, db_session, tmp_path):
        now = datetime.now(timezone.utc)
        _make_page(db_session, workspace_id=None, slug="", page_type=m.PageType.LANDING,
                   html_content="<html>home</html>",
                   status=m.PageStatus.PUBLISHED, published_at=now)
        _make_page(db_session, workspace_id=None, slug="blog/first-post",
                   html_content="<html>post</html>",
                   status=m.PageStatus.PUBLISHED, published_at=now)
        _make_page(db_session, workspace_id=None, slug="blog/a-draft",
                   html_content="<html>draft</html>")

        result = export_static_site(db_session, str(tmp_path))

        assert result["pages_exported"] == 2
        assert result["index_generated"] is True
        assert (tmp_path / "index.html").read_text(encoding="utf-8") == "<html>home</html>"
        assert (tmp_path / "blog" / "first-post" / "index.html").read_text(
            encoding="utf-8") == "<html>post</html>"
        # The draft is not on disk.
        assert not (tmp_path / "blog" / "a-draft").exists()

        sitemap = (tmp_path / "sitemap.xml").read_text(encoding="utf-8")
        assert "https://leadpilot.io/blog/first-post" in sitemap
        assert f"<lastmod>{now.date().isoformat()}</lastmod>" in sitemap
        assert "blog/a-draft" not in sitemap

        robots = (tmp_path / "robots.txt").read_text(encoding="utf-8")
        assert "User-agent: *" in robots
        assert "Sitemap: https://leadpilot.io/sitemap.xml" in robots

    def test_export_with_nothing_published_still_writes_the_files(self, db_session,
                                                                  tmp_path):
        result = export_static_site(db_session, str(tmp_path))
        assert result == {"pages_exported": 0, "output_dir": str(tmp_path),
                          "index_generated": False}
        assert (tmp_path / "sitemap.xml").exists()
        assert (tmp_path / "robots.txt").exists()


# ---------------------------------------------------------------------------
# BR-D1: whose brand a generated page carries. A page of our own marketing
# site (no workspace) is written as Vectorae; a customer workspace's page is
# written for that customer's business and never names us, our founder or
# our ideal customer profile.
# ---------------------------------------------------------------------------

OUR_MARKERS = ("Vectorae", "Vectorae", "LeadPilot", "Calendarharvest", "Rehan Masood",
               "Boutique agency founders")


class _RecordingClaude:
    def __init__(self):
        self.calls = []

    def complete_json(self, *, system, prompt, max_tokens, schema=None):
        self.calls.append({"system": system, "prompt": prompt})
        return {"html_content": GOOD_HTML, "word_count": 1400, "reading_time_mins": 6,
                "internal_links": [], "schema_markup": {}}


@pytest.fixture()
def recording_claude(monkeypatch):
    from app.services import website_builder

    fake = _RecordingClaude()
    monkeypatch.setattr(website_builder, "get_client", lambda: fake)
    return fake


def _generate(**kwargs):
    from app.services.website_builder import generate_page_html

    return generate_page_html("blog", "Agency pipelines", "agency pipeline", [],
                              "A brief about keeping an agency's pipeline full.", [],
                              slug="agency-pipelines", **kwargs)


class TestBrandContext:
    def test_our_own_site_is_written_as_vectorae(self, recording_claude):
        _generate()
        prompt = recording_claude.calls[0]["prompt"]
        assert "Product: Vectorae, built by Rehan Masood and Ahmad Shahid" in prompt
        assert "title tag: Agency pipelines | Vectorae" in prompt
        assert "author Rehan Masood" in prompt

    def test_a_customer_page_names_only_the_customer(self, recording_claude):
        _generate(brand_name="Northwind Cleaning")
        call = recording_claude.calls[0]
        assert "Business: Northwind Cleaning" in call["prompt"]
        assert "title tag: Agency pipelines | Northwind Cleaning" in call["prompt"]
        assert "author Northwind Cleaning" in call["prompt"]
        for marker in OUR_MARKERS:
            assert marker not in call["prompt"], marker
            assert marker not in call["system"], marker

    def test_a_blank_customer_name_still_never_falls_back_to_ours(self, recording_claude):
        _generate(brand_name="   ")
        prompt = recording_claude.calls[0]["prompt"]
        assert "Business: this business" in prompt
        assert not any(marker in prompt for marker in OUR_MARKERS)

    def test_the_worker_passes_the_workspace_name(self, db_session, test_user,
                                                  recording_claude):
        from app.workers.site_tasks import generate_page_impl

        ws = db_session.get(m.Workspace, _our_workspace_id(db_session))
        ws.name = "Northwind Cleaning"
        db_session.flush()
        page = m.SitePage(workspace_id=ws.id, slug="nw-page", title="Office cleaning", meta_title="Office cleaning",
                          meta_description="Office cleaning in Leeds",
                          target_keyword="office cleaning", page_type=m.PageType.LANDING,
                          brief="Office cleaning for small firms in Leeds, weekly or daily.")
        db_session.add(page)
        db_session.commit()

        assert generate_page_impl(db_session, page.id, user_id=test_user.id)["status"] \
            == "generated"
        prompt = recording_claude.calls[0]["prompt"]
        assert "Business: Northwind Cleaning" in prompt
        assert not any(marker in prompt for marker in OUR_MARKERS)

    def test_the_worker_uses_the_white_label_brand_when_it_is_on(self, db_session,
                                                                 test_user,
                                                                 recording_claude):
        from app.workers.site_tasks import generate_page_impl

        ws = db_session.get(m.Workspace, _our_workspace_id(db_session))
        ws.name, ws.white_label_enabled, ws.brand_name = "Agency Ltd", True, "Bright Leads"
        db_session.flush()
        page = m.SitePage(workspace_id=ws.id, slug="bl-page", title="Lead generation", meta_title="Lead generation",
                          meta_description="Lead generation for accountants",
                          target_keyword="lead generation", page_type=m.PageType.LANDING,
                          brief="Lead generation for accountants, done for you each month.")
        db_session.add(page)
        db_session.commit()

        generate_page_impl(db_session, page.id, user_id=test_user.id)
        assert "Business: Bright Leads" in recording_claude.calls[0]["prompt"]
