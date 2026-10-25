"""SEALED guard tests moved unchanged from tests/test_website_builder.py.

Listed in evals/compliance_testset.yaml: a failing or missing test here
blocks the release gate. Moved by the gate PR in
docs/program/plans/phase5-seal-ai-and-tenancy-guards.md; the test bodies
are byte-identical to the source. Helpers they need are copied here so
the file stands alone.
"""

import uuid
from datetime import datetime, timezone
import pytest
from app.db import models as m
from app.services.website_builder import export_static_site


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


def _other_workspace(db_session, name="Theirs") -> m.Workspace:
    other = m.User(email=f"{uuid.uuid4().hex[:8]}@example.test", email_verified=True)
    db_session.add(other)
    db_session.flush()
    workspace = m.Workspace(name=name, slug=f"w{uuid.uuid4().hex[:8]}",
                            owner_user_id=other.id)
    db_session.add(workspace)
    db_session.commit()
    return workspace


def _published(db_session, **overrides) -> m.SitePage:
    return _make_page(db_session, html_content=GOOD_HTML,
                      status=m.PageStatus.PUBLISHED,
                      published_at=datetime.now(timezone.utc), **overrides)


@pytest.fixture()
def no_workspace(db_session):
    db_session.query(m.Workspace).filter(
        m.Workspace.slug == OUR_WORKSPACE_SLUG).delete()
    db_session.commit()


@pytest.fixture()
def admin(db_session, test_user):
    test_user.is_admin = True
    db_session.commit()
    return test_user


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


# ---------------------------------------------------------------------------
# Tenancy. A page with no workspace is ours (the marketing site): only an
# admin reaches it. A customer reaches only their own workspace's pages, and
# a non-admin with no workspace has nowhere to put one. The export, the
# public sitemap and the generation prompt's link menu never mix sites.
# ---------------------------------------------------------------------------


class TestTenancy:
    def test_a_customer_cannot_reach_our_marketing_pages(self, client, db_session,
                                                         queued_jobs):
        ours = _make_page(db_session, workspace_id=None, slug="blog/ours",
                          html_content=GOOD_HTML)

        assert client.get("/site/pages").json() == []
        for method, path in (("get", ""), ("post", "/regenerate"),
                             ("patch", "/publish"), ("patch", "/unpublish"),
                             ("get", "/seo"), ("get", "/download")):
            response = getattr(client, method)(f"/site/pages/{ours.id}{path}")
            assert response.status_code == 404, (method, path, response.status_code)
        db_session.refresh(ours)
        assert ours.status == m.PageStatus.DRAFT
        assert ours.generation_version == 1
        assert queued_jobs["site_generate"] == []

    def test_a_customer_sees_only_their_own_workspace(self, client, db_session):
        mine = _make_page(db_session, slug="blog/mine")
        _make_page(db_session, workspace_id=_other_workspace(db_session).id,
                   slug="blog/theirs")
        _make_page(db_session, workspace_id=None, slug="blog/ours")

        assert [p["slug"] for p in client.get("/site/pages").json()] == [mine.slug]

    def test_no_workspace_and_not_admin_cannot_create(self, client, db_session,
                                                      no_workspace, queued_jobs):
        for path, payload in (("/site/pages", _body()),
                              ("/site/pages/bulk", {"pages": [_body()]})):
            response = client.post(path, json=payload)
            assert response.status_code == 409, path
            assert response.json()["detail"]["error"] == "workspace_required"
        assert db_session.query(m.SitePage).count() == 0
        assert queued_jobs["site_generate"] == []

    def test_no_workspace_and_not_admin_sees_nothing(self, client, db_session,
                                                     no_workspace):
        """Before: every workspaceless user shared the global bucket."""
        someone_elses = _make_page(db_session, workspace_id=None, slug="blog/x")

        assert client.get("/site/pages").json() == []
        assert client.get(f"/site/pages/{someone_elses.id}").status_code == 404

    def test_an_admin_without_a_workspace_writes_our_pages(self, client, db_session,
                                                           no_workspace, admin,
                                                           queued_jobs):
        response = client.post("/site/pages", json=_body(slug="blog/ours-new"))

        assert response.status_code == 201
        page = db_session.query(m.SitePage).filter(
            m.SitePage.slug == "blog/ours-new").one()
        assert page.workspace_id is None
        assert [p["slug"] for p in client.get("/site/pages").json()] == ["blog/ours-new"]

    def test_an_admin_with_a_workspace_sees_it_and_ours(self, client, db_session,
                                                        admin):
        _make_page(db_session, slug="blog/mine")
        _make_page(db_session, workspace_id=None, slug="blog/ours")
        _make_page(db_session, workspace_id=_other_workspace(db_session).id,
                   slug="blog/theirs")

        slugs = {p["slug"] for p in client.get("/site/pages").json()}

        assert slugs == {"blog/mine", "blog/ours"}

    def test_the_page_limit_counts_only_the_bucket_written_to(self, client,
                                                              db_session, admin,
                                                              queued_jobs):
        """An admin's own workspace is not charged for the marketing site."""
        from app.core.plans import get_plan

        admin.plan = m.PlanTier.STARTER
        db_session.commit()
        limit = get_plan("starter")["max_site_pages"]
        for i in range(limit):
            _make_page(db_session, workspace_id=None, slug=f"blog/ours-{i}")

        assert client.post("/site/pages", json=_body()).status_code == 201

    def test_only_an_admin_may_export(self, client, db_session, queued_jobs):
        assert client.post("/site/export").status_code == 403
        assert not queued_jobs["site_export"]

    def test_the_export_holds_only_our_pages(self, db_session, tmp_path):
        _published(db_session, workspace_id=None, slug="blog/ours")
        _published(db_session, slug="blog/a-customers")

        result = export_static_site(db_session, str(tmp_path))

        assert result["pages_exported"] == 1
        assert (tmp_path / "blog" / "ours" / "index.html").exists()
        assert not (tmp_path / "blog" / "a-customers").exists()
        assert "a-customers" not in (tmp_path / "sitemap.xml").read_text(encoding="utf-8")

    def test_the_public_sitemap_lists_only_our_pages(self, anon_client, db_session):
        _published(db_session, workspace_id=None, slug="blog/ours")
        _published(db_session, slug="blog/a-customers")

        text = anon_client.get("/site/sitemap.xml").text

        assert "https://leadpilot.io/blog/ours" in text
        assert "a-customers" not in text

    def test_the_link_menu_holds_only_the_same_sites_pages(self, db_session,
                                                           test_user,
                                                           recording_claude):
        from app.workers.site_tasks import generate_page_impl

        _published(db_session, slug="blog/my-other-page", title="My other page")
        _published(db_session, workspace_id=_other_workspace(db_session).id,
                   slug="blog/their-page", title="Their private launch plan")
        _published(db_session, workspace_id=None, slug="blog/our-page",
                   title="Our marketing page")
        page = _make_page(db_session, slug="blog/being-written")

        generate_page_impl(db_session, page.id, user_id=test_user.id)

        prompt = recording_claude.calls[0]["prompt"]
        assert "blog/my-other-page" in prompt
        assert "their-page" not in prompt and "Their private launch plan" not in prompt
        assert "our-page" not in prompt and "Our marketing page" not in prompt

    def test_our_pages_link_menu_holds_no_customer_page(self, db_session,
                                                        recording_claude):
        from app.workers.site_tasks import generate_page_impl

        _published(db_session, workspace_id=None, slug="blog/our-other-page")
        _published(db_session, slug="blog/a-customers")
        page = _make_page(db_session, workspace_id=None, slug="blog/ours-being-written")

        generate_page_impl(db_session, page.id)

        prompt = recording_claude.calls[0]["prompt"]
        assert "blog/our-other-page" in prompt
        assert "a-customers" not in prompt
