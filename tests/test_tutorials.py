"""Feature 2 (+ Task 3) — Learn Vectorae tutorial section.

The catalogue now lives in the `tutorial_catalogue` TABLE, not in code, so
every test here seeds it through the `catalogue` fixture. That matters more
than it sounds: with an empty table most of these assertions would pass
vacuously, which is why the first test checks the fixture actually inserted
something.

The tests that matter most are still the ones about state that is easy to get
subtly wrong and impossible to notice: progress that silently goes backwards
when a user scrubs, a summary that moves while someone types in the search
box, badges that disagree with the progress behind them, publish state leaking
drafts to users, and — the one with real consequences — one user being able to
see another's watch history.
"""

import uuid

import pytest
from sqlalchemy import select

from app.api.tutorials import COMPLETION_THRESHOLD_PERCENT
from app.db import models as m
from app.services import tutorials as catalogue
from tests.conftest import auth_headers, seed_catalogue

ALL_SLUGS = [t.slug for t in catalogue.SEED_CATALOGUE]
FIRST = ALL_SLUGS[0]


def _levels_of(payload):
    return [t["level"] for t in payload["tutorials"]]


# Seed slugs grouped by level. Module-level so tests can use them without
# shadowing the `catalogue` FIXTURE with the `catalogue` MODULE.
_SEED_BY_LEVEL = {
    lv: [t.slug for t in catalogue.SEED_CATALOGUE if t.level == lv]
    for lv in catalogue.LEVELS
}


def _list(client, **params):
    r = client.get("/tutorials", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _put(client, slug, position, duration=None):
    body = {"position_seconds": position}
    if duration is not None:
        body["duration_seconds"] = duration
    return client.put(f"/tutorials/{slug}/progress", json=body)


def _progress_of(payload, slug):
    for t in payload["tutorials"]:
        if t["slug"] == slug:
            return t["progress"]
    raise AssertionError(f"{slug} not in payload")


# --------------------------------------------------------------------------
# Catalogue
# --------------------------------------------------------------------------


class TestCatalogue:
    def test_nine_tutorials_three_per_level(self, catalogue, client):
        payload = _list(client)
        assert len(payload["tutorials"]) == 9
        for level in ("beginner", "intermediate", "advanced"):
            assert len(_list(client, level=level)["tutorials"]) == 3

    def test_ordered_beginner_first(self, catalogue, client):
        """Level is a STRING column, so ORDER BY level would give
        advanced/beginner/intermediate. The query ranks levels explicitly."""
        assert _levels_of(_list(client)) == (
            ["beginner"] * 3 + ["intermediate"] * 3 + ["advanced"] * 3)

    def test_the_fixture_actually_seeded_the_table(self, db_session, catalogue):
        """Guard against every other test in this file passing vacuously.

        With the catalogue in a table instead of in code, an empty table makes
        "no tutorials matched" indistinguishable from "the feature works".
        """
        rows = db_session.execute(select(m.TutorialCatalogue)).scalars().all()
        assert len(rows) == 9
        assert all(r.is_published for r in rows)

    def test_slugs_are_unique(self):
        """A duplicate slug would merge two videos' progress into one row."""
        assert len(set(ALL_SLUGS)) == len(ALL_SLUGS)

    def test_placeholders_are_flagged_not_faked(self, catalogue, client):
        """A fake YouTube id would render a broken player and look like a bug.

        None -> is_placeholder -> the UI shows "coming soon" instead.
        """
        for t in _list(client)["tutorials"]:
            if t["youtube_id"] is None:
                assert t["is_placeholder"] is True

    def test_every_tutorial_has_a_description(self, catalogue, client):
        for t in _list(client)["tutorials"]:
            assert t["description"].strip()


# --------------------------------------------------------------------------
# Search + filter
# --------------------------------------------------------------------------


class TestSearchAndFilter:
    @pytest.mark.parametrize("query,expected", [
        ("apollo", "advanced-lead-sourcing-with-apollo"),
        ("ICP", "understanding-your-icp"),
        ("analytics", "reading-your-analytics"),
    ])
    def test_search_finds_by_title(self, catalogue, client, query, expected):
        found = [t["slug"] for t in _list(client, q=query)["tutorials"]]
        assert expected in found

    def test_search_is_case_insensitive(self, catalogue, client):
        assert (_list(client, q="APOLLO")["tutorials"]
                == _list(client, q="apollo")["tutorials"])

    def test_search_also_matches_descriptions(self, catalogue, client):
        """"deliverability" appears in no title. A user typing it should still
        find the video that covers it."""
        found = [t["slug"] for t in _list(client, q="deliverability")["tutorials"]]
        assert found == ["scaling-your-pipeline"]

    def test_search_with_no_matches_returns_empty_not_error(self, catalogue, client):
        assert _list(client, q="zzzznothing")["tutorials"] == []

    @pytest.mark.parametrize("level", ["beginner", "intermediate", "advanced"])
    def test_level_filter(self, catalogue, client, level):
        found = _list(client, level=level)["tutorials"]
        assert len(found) == 3
        assert {t["level"] for t in found} == {level}

    def test_search_and_level_combine(self, catalogue, client):
        found = _list(client, q="vectorae", level="beginner")["tutorials"]
        assert all(t["level"] == "beginner" for t in found)

    def test_unknown_level_is_empty_not_422(self, catalogue, client):
        """It is a browse filter, not an API contract — a typo in a shared URL
        should show nothing, not an error page."""
        r = client.get("/tutorials", params={"level": "expert"})
        assert r.status_code == 200
        assert r.json()["tutorials"] == []


# --------------------------------------------------------------------------
# Progress
# --------------------------------------------------------------------------


class TestProgress:
    def test_untouched_tutorial_reports_zero_not_null(self, catalogue, client):
        """No row is created just so the UI can draw a 0% bar."""
        p = _progress_of(_list(client), FIRST)
        assert p == {
            "position_seconds": 0, "duration_seconds": None, "percent": 0.0,
            "completed": False, "completed_at": None, "last_watched_at": None,
            "started": False,
        }

    def test_update_records_position_and_percent(self, catalogue, client):
        r = _put(client, FIRST, position=30, duration=120)
        assert r.status_code == 200
        p = r.json()["progress"]
        assert p["position_seconds"] == 30
        assert p["percent"] == 25.0
        assert p["completed"] is False
        assert p["started"] is True

    def test_position_takes_the_LATEST_value(self, catalogue, client):
        """position answers "where do I resume" — scrubbing back means
        resuming back."""
        _put(client, FIRST, position=90, duration=120)
        r = _put(client, FIRST, position=10, duration=120)
        assert r.json()["progress"]["position_seconds"] == 10

    def test_percent_takes_the_MAXIMUM_and_never_regresses(self, catalogue, client):
        """percent answers "how much have I seen" — scrubbing back must not
        erase watched progress. This is the bug that would silently undo a
        user's completion every time they rewatched an intro."""
        _put(client, FIRST, position=60, duration=120)   # 50%
        r = _put(client, FIRST, position=10, duration=120)  # back to the start
        assert r.json()["progress"]["percent"] == 50.0

    def test_percent_is_clamped_at_100(self, catalogue, client):
        """Players can report a position a shade past their own duration."""
        r = _put(client, FIRST, position=130, duration=120)
        assert r.json()["progress"]["percent"] == 100.0

    def test_repeated_updates_do_not_create_duplicate_rows(self, catalogue, client, db_session):
        """A player fires these constantly; UNIQUE(user_id, slug) is what keeps
        "have I finished this?" to a single answer."""
        for pos in (5, 10, 15, 20, 25):
            _put(client, FIRST, position=pos, duration=120)
        rows = db_session.execute(
            select(m.TutorialProgress).where(
                m.TutorialProgress.tutorial_slug == FIRST)
        ).scalars().all()
        assert len(rows) == 1

    def test_duration_may_be_omitted(self, catalogue, client):
        """The player has not loaded metadata on the first report."""
        r = _put(client, FIRST, position=5)
        assert r.status_code == 200
        assert r.json()["progress"]["percent"] == 0.0

    def test_unknown_slug_is_404(self, catalogue, client):
        assert _put(client, "no-such-video", position=1).status_code == 404
        assert client.get("/tutorials/no-such-video").status_code == 404
        assert client.post("/tutorials/no-such-video/complete").status_code == 404

    def test_negative_position_rejected(self, catalogue, client):
        assert _put(client, FIRST, position=-1).status_code == 422


# --------------------------------------------------------------------------
# Completion
# --------------------------------------------------------------------------


class TestCompletion:
    def test_crossing_the_threshold_completes(self, catalogue, client):
        r = _put(client, FIRST, position=90, duration=100)   # 90%
        p = r.json()["progress"]
        assert p["percent"] >= COMPLETION_THRESHOLD_PERCENT
        assert p["completed"] is True
        assert p["completed_at"] is not None

    def test_just_below_the_threshold_does_not_complete(self, catalogue, client):
        assert _put(client, FIRST, position=89, duration=100
                    ).json()["progress"]["completed"] is False

    def test_threshold_is_90_not_100(self, catalogue, client):
        """Almost nobody reaches the final frame — end cards, credits, clicking
        away. A 100% rule strands users at "8 of 9" and teaches them to scrub."""
        assert COMPLETION_THRESHOLD_PERCENT == 90.0

    def test_explicit_complete_works_without_watching(self, catalogue, client):
        """Required, not convenience: a placeholder video cannot be watched at
        all, and a user who knows the material must still be able to finish."""
        p = client.post(f"/tutorials/{FIRST}/complete").json()["progress"]
        assert p["completed"] is True
        assert p["percent"] == 100.0

    def test_completed_at_keeps_the_FIRST_completion_time(self, catalogue, client):
        first = client.post(f"/tutorials/{FIRST}/complete").json()["progress"]["completed_at"]
        _put(client, FIRST, position=5, duration=120)      # rewatch
        again = client.post(f"/tutorials/{FIRST}/complete").json()["progress"]["completed_at"]
        assert again == first

    def test_rewatching_does_not_uncomplete(self, catalogue, client):
        client.post(f"/tutorials/{FIRST}/complete")
        p = _put(client, FIRST, position=1, duration=120).json()["progress"]
        assert p["completed"] is True

    def test_reset_clears_everything(self, catalogue, client, db_session):
        client.post(f"/tutorials/{FIRST}/complete")
        r = client.delete(f"/tutorials/{FIRST}/progress")
        assert r.status_code == 200
        p = r.json()["progress"]
        assert p["completed"] is False and p["percent"] == 0.0
        assert p["started"] is False
        # Deleted, not zeroed — "not started" has exactly one representation.
        rows = db_session.execute(
            select(m.TutorialProgress).where(
                m.TutorialProgress.tutorial_slug == FIRST)
        ).scalars().all()
        assert rows == []

    def test_reset_on_an_untouched_tutorial_is_not_an_error(self, catalogue, client):
        assert client.delete(f"/tutorials/{FIRST}/progress").status_code == 200


# --------------------------------------------------------------------------
# Summary
# --------------------------------------------------------------------------


class TestSummary:
    def test_starts_at_zero(self, catalogue, client):
        s = _list(client)["summary"]
        assert s["total"] == 9 and s["completed"] == 0 and s["percent"] == 0.0

    def test_counts_completions(self, catalogue, client):
        for slug in _SEED_BY_LEVEL['beginner']:
            client.post(f"/tutorials/{slug}/complete")
        s = _list(client)["summary"]
        assert s["completed"] == 3
        assert s["percent"] == round(100 * 3 / 9, 1)
        assert s["by_level"]["beginner"] == {"label": "Beginner", "total": 3,
                                             "completed": 3}
        assert s["by_level"]["advanced"]["completed"] == 0

    def test_summary_IGNORES_the_search_filter(self, catalogue, client):
        """The summary answers "how far through the course am I". It must not
        move while the user types in the search box."""
        client.post(f"/tutorials/{FIRST}/complete")
        unfiltered = _list(client)["summary"]
        filtered = _list(client, q="apollo")["summary"]
        assert len(_list(client, q="apollo")["tutorials"]) == 1  # filter DID apply
        assert filtered == unfiltered

    def test_summary_ignores_the_level_filter_too(self, catalogue, client):
        client.post(f"/tutorials/{FIRST}/complete")
        assert _list(client, level="advanced")["summary"] == _list(client)["summary"]


# --------------------------------------------------------------------------
# Badges
# --------------------------------------------------------------------------


class TestBadges:
    def test_none_earned_initially(self, catalogue, client):
        assert all(b["earned"] is False for b in _list(client)["badges"])

    def test_level_badge_needs_ALL_of_its_level(self, catalogue, client):
        slugs = _SEED_BY_LEVEL['beginner']
        for slug in slugs[:2]:
            client.post(f"/tutorials/{slug}/complete")
        badge = next(b for b in _list(client)["badges"]
                     if b["slug"] == "beginner-complete")
        assert badge["earned"] is False
        assert badge["required_completed"] == 2 and badge["required_total"] == 3

        client.post(f"/tutorials/{slugs[2]}/complete")
        badge = next(b for b in _list(client)["badges"]
                     if b["slug"] == "beginner-complete")
        assert badge["earned"] is True
        assert badge["earned_at"] is not None

    def test_finishing_one_level_does_not_earn_another(self, catalogue, client):
        for slug in _SEED_BY_LEVEL['beginner']:
            client.post(f"/tutorials/{slug}/complete")
        earned = {b["slug"] for b in _list(client)["badges"] if b["earned"]}
        assert earned == {"beginner-complete"}

    def test_certified_badge_needs_the_whole_catalogue(self, catalogue, client):
        for slug in ALL_SLUGS[:-1]:
            client.post(f"/tutorials/{slug}/complete")
        assert not next(b for b in _list(client)["badges"]
                        if b["slug"] == "leadpilot-certified")["earned"]

        client.post(f"/tutorials/{ALL_SLUGS[-1]}/complete")
        badges = {b["slug"]: b for b in _list(client)["badges"]}
        assert badges["leadpilot-certified"]["earned"] is True
        assert all(b["earned"] for b in badges.values())

    def test_resetting_a_video_REVOKES_the_badge(self, catalogue, client):
        """Badges are derived, never stored — so they cannot drift out of
        agreement with the progress that justifies them."""
        for slug in _SEED_BY_LEVEL['beginner']:
            client.post(f"/tutorials/{slug}/complete")
        assert next(b for b in _list(client)["badges"]
                    if b["slug"] == "beginner-complete")["earned"] is True

        client.delete(f"/tutorials/{_SEED_BY_LEVEL['beginner'][0]}/progress")
        assert next(b for b in _list(client)["badges"]
                    if b["slug"] == "beginner-complete")["earned"] is False


# --------------------------------------------------------------------------
# Access control
# --------------------------------------------------------------------------


class TestAccessControl:
    def test_requires_authentication(self, catalogue, anon_client):
        assert anon_client.get("/tutorials").status_code == 401
        assert anon_client.put(f"/tutorials/{FIRST}/progress",
                               json={"position_seconds": 1}).status_code == 401

    def test_unverified_users_are_blocked_by_the_feature_1_gate(self, catalogue, client, db_session):
        """Feature 2 inherits Feature 1's chokepoint for free — the point of
        putting that check in get_current_user rather than per-router."""
        r = client.post("/auth/signup", json={"email": "learner@x.com",
                                              "password": "hunter22!"})
        tokens = r.json()
        resp = client.get("/tutorials", headers={
            "Authorization": f"Bearer {tokens['access_token']}"})
        assert resp.status_code == 403
        assert resp.json()["detail"] == "EMAIL_NOT_VERIFIED"

    def test_progress_is_PRIVATE_between_users(self, catalogue, client, db_session):
        """The one failure here with real consequences."""
        other = m.User(email=f"other_{uuid.uuid4().hex[:6]}@x.com",
                       email_verified=True)
        db_session.add(other)
        db_session.commit()

        # The fixture user completes everything.
        for slug in ALL_SLUGS:
            client.post(f"/tutorials/{slug}/complete")

        # The other user sees a clean slate, not that progress.
        payload = client.get("/tutorials", headers=auth_headers(other)).json()
        assert payload["summary"]["completed"] == 0
        assert all(t["progress"]["started"] is False for t in payload["tutorials"])
        assert all(b["earned"] is False for b in payload["badges"])

    def test_one_users_reset_does_not_touch_another(self, catalogue, client, db_session):
        other = m.User(email=f"other2_{uuid.uuid4().hex[:6]}@x.com",
                       email_verified=True)
        db_session.add(other)
        db_session.commit()

        client.post(f"/tutorials/{FIRST}/complete")
        client.post(f"/tutorials/{FIRST}/complete", headers=auth_headers(other))

        client.delete(f"/tutorials/{FIRST}/progress", headers=auth_headers(other))

        mine = client.get(f"/tutorials/{FIRST}").json()["progress"]
        assert mine["completed"] is True


# --------------------------------------------------------------------------
# Admin aggregates
# --------------------------------------------------------------------------


class TestAdminCompletions:
    def _make_admin(self, db_session):
        admin = m.User(email=f"admin_{uuid.uuid4().hex[:6]}@x.com",
                       is_admin=True, email_verified=True)
        db_session.add(admin)
        db_session.commit()
        return admin

    def test_non_admin_is_refused(self, catalogue, client):
        assert client.get("/admin/tutorials/completions").status_code == 403

    def test_counts_are_aggregate(self, catalogue, client, db_session):
        admin = self._make_admin(db_session)
        client.post(f"/tutorials/{FIRST}/complete")
        _put(client, ALL_SLUGS[1], position=10, duration=120)  # started only

        data = client.get("/admin/tutorials/completions",
                          headers=auth_headers(admin)).json()
        rows = {r["slug"]: r for r in data["tutorials"]}
        assert rows[FIRST]["completed_count"] == 1
        assert rows[FIRST]["in_progress_count"] == 0
        assert rows[ALL_SLUGS[1]]["started_count"] == 1
        assert rows[ALL_SLUGS[1]]["completed_count"] == 0
        assert rows[ALL_SLUGS[1]]["in_progress_count"] == 1
        assert data["active_learners"] == 1
        assert data["total_completions"] == 1

    def test_response_carries_NO_user_identifiers(self, catalogue, client, db_session):
        """The product decision was: admins see completion counts, never which
        videos a named person watched. Enforced by the payload containing no
        user field at all, not by a UI that declines to render one."""
        admin = self._make_admin(db_session)
        client.post(f"/tutorials/{FIRST}/complete")

        raw = client.get("/admin/tutorials/completions",
                         headers=auth_headers(admin)).text
        assert "user_id" not in raw
        assert "email" not in raw
        assert "test@leadpilot.dev" not in raw

    def test_every_catalogue_entry_is_listed_even_at_zero(self, catalogue, client, db_session):
        admin = self._make_admin(db_session)
        data = client.get("/admin/tutorials/completions",
                          headers=auth_headers(admin)).json()
        assert [r["slug"] for r in data["tutorials"]] == ALL_SLUGS
        assert all(r["completed_count"] == 0 for r in data["tutorials"])


# --------------------------------------------------------------------------
# Task 3 — the catalogue is now editable from the admin API
# --------------------------------------------------------------------------


def _admin(db_session):
    admin = m.User(email=f"tadmin_{uuid.uuid4().hex[:6]}@x.com",
                   is_admin=True, email_verified=True)
    db_session.add(admin)
    db_session.commit()
    return admin


class TestPublishVisibility:
    """The single most important behaviour Task 3 introduces: unpublished
    tutorials must be invisible to users, everywhere, by default."""

    def test_unpublished_tutorials_are_hidden_from_users(self, db_session, client):
        seed_catalogue(db_session, published=False)
        payload = _list(client)
        assert payload["tutorials"] == []
        assert payload["summary"]["total"] == 0

    def test_an_unpublished_slug_is_a_404_not_a_403(self, db_session, client):
        """Indistinguishable from non-existent on purpose — otherwise the
        status code tells anyone with a shared URL that a draft exists."""
        seed_catalogue(db_session, published=False)
        assert client.get(f"/tutorials/{FIRST}").status_code == 404
        assert client.post(f"/tutorials/{FIRST}/complete").status_code == 404
        assert _put(client, FIRST, position=5).status_code == 404

    def test_an_empty_catalogue_does_NOT_grant_every_badge(self, db_session, client):
        """all() over an empty list is True. Without the bool(required) guard a
        brand-new user on a fresh install would be handed 'Vectorae
        Certified' for doing nothing."""
        seed_catalogue(db_session, published=False)
        payload = _list(client)
        assert payload["badges"], "badges should still be listed"
        assert all(b["earned"] is False for b in payload["badges"])

    def test_publishing_one_tutorial_shows_exactly_that_one(self, db_session,
                                                            client):
        rows = seed_catalogue(db_session, published=False)
        rows[0].youtube_id = "abc12345678"
        rows[0].is_published = True
        db_session.commit()

        payload = _list(client)
        assert [t["slug"] for t in payload["tutorials"]] == [rows[0].slug]
        assert payload["summary"]["total"] == 1

    def test_summary_counts_only_PUBLISHED_tutorials(self, db_session, client):
        """Counting a hidden tutorial in the denominator would show '1 / 9' on
        a page displaying one — arithmetic the user cannot check."""
        rows = seed_catalogue(db_session, published=True)
        client.post(f"/tutorials/{rows[0].slug}/complete")
        assert _list(client)["summary"] == {
            **_list(client)["summary"], "total": 9, "completed": 1}

        for row in rows[1:]:
            row.is_published = False
        db_session.commit()

        summary = _list(client)["summary"]
        assert summary["total"] == 1
        assert summary["completed"] == 1
        assert summary["percent"] == 100.0

    def test_progress_SURVIVES_unpublishing_and_returns_on_republish(
        self, db_session, client
    ):
        """Hiding a tutorial must not destroy watch history — an admin fixing a
        typo should not wipe what users have done."""
        rows = seed_catalogue(db_session, published=True)
        client.post(f"/tutorials/{FIRST}/complete")

        row = next(r for r in rows if r.slug == FIRST)
        row.is_published = False
        db_session.commit()
        assert _list(client)["summary"]["completed"] == 0   # hidden, not lost

        row.is_published = True
        db_session.commit()
        assert _progress_of(_list(client), FIRST)["completed"] is True


class TestAdminCatalogueCrud:
    def test_non_admin_is_refused_everywhere(self, catalogue, client):
        assert client.get("/admin/tutorials").status_code == 403
        assert client.post("/admin/tutorials", json={}).status_code == 403
        assert client.put(f"/admin/tutorials/{FIRST}", json={}).status_code == 403
        assert client.delete(f"/admin/tutorials/{FIRST}").status_code == 403
        assert client.post(f"/admin/tutorials/{FIRST}/publish").status_code == 403

    def test_admin_list_includes_unpublished(self, db_session, client):
        seed_catalogue(db_session, published=False)
        data = client.get("/admin/tutorials",
                          headers=auth_headers(_admin(db_session))).json()
        assert data["total_count"] == 9
        assert data["published_count"] == 0
        assert all(t["is_published"] is False for t in data["tutorials"])

    def test_admin_adds_a_youtube_id_and_publishes(self, db_session, client):
        """The whole point of Task 3: a video goes live with no deploy."""
        seed_catalogue(db_session, published=False)
        headers = auth_headers(_admin(db_session))

        assert _list(client)["tutorials"] == []          # nothing visible yet

        updated = client.put(f"/admin/tutorials/{FIRST}",
                             json={"youtube_id": "abc12345678",
                                   "duration_seconds": 424},
                             headers=headers)
        assert updated.status_code == 200
        assert updated.json()["youtube_id"] == "abc12345678"
        assert updated.json()["is_placeholder"] is False

        published = client.post(f"/admin/tutorials/{FIRST}/publish",
                                headers=headers)
        assert published.status_code == 200

        visible = _list(client)["tutorials"]
        assert [t["slug"] for t in visible] == [FIRST]
        assert visible[0]["youtube_id"] == "abc12345678"
        assert visible[0]["duration_seconds"] == 424

    def test_publish_REFUSES_without_a_youtube_id(self, db_session, client):
        """Publishing a placeholder puts a permanent 'coming soon' card in the
        Learn tab, which reads as broken rather than incomplete."""
        seed_catalogue(db_session, published=False)
        resp = client.post(f"/admin/tutorials/{FIRST}/publish",
                           headers=auth_headers(_admin(db_session)))
        assert resp.status_code == 409
        assert "youtube_id" in resp.json()["detail"]
        assert _list(client)["tutorials"] == []

    def test_unpublish_hides_it_again(self, db_session, client):
        seed_catalogue(db_session, published=True)
        headers = auth_headers(_admin(db_session))
        assert len(_list(client)["tutorials"]) == 9
        client.post(f"/admin/tutorials/{FIRST}/unpublish", headers=headers)
        assert FIRST not in [t["slug"] for t in _list(client)["tutorials"]]

    def test_create_a_brand_new_tutorial(self, db_session, client):
        headers = auth_headers(_admin(db_session))
        resp = client.post("/admin/tutorials", json={
            "slug": "brand-new-lesson",
            "title": "Brand New Lesson",
            "description": "Something we recorded this morning.",
            "level": "beginner",
            "youtube_id": "xyz98765432",
            "duration_seconds": 300,
            "sort_order": 9,
            "is_published": True,
        }, headers=headers)
        assert resp.status_code == 201
        assert [t["slug"] for t in _list(client)["tutorials"]] == ["brand-new-lesson"]

    def test_duplicate_slug_is_409(self, db_session, client):
        seed_catalogue(db_session)
        resp = client.post("/admin/tutorials", json={
            "slug": FIRST, "title": "Clash", "description": "A clashing slug.",
            "level": "beginner",
        }, headers=auth_headers(_admin(db_session)))
        assert resp.status_code == 409

    @pytest.mark.parametrize("bad", [
        {"slug": "Has Spaces", "title": "x" * 5, "description": "y" * 20,
         "level": "beginner"},
        {"slug": "ok-slug", "title": "ab", "description": "y" * 20,
         "level": "beginner"},
        {"slug": "ok-slug", "title": "fine", "description": "short",
         "level": "beginner"},
    ])
    def test_create_validation(self, db_session, client, bad):
        assert client.post("/admin/tutorials", json=bad,
                           headers=auth_headers(_admin(db_session))
                           ).status_code == 422

    def test_unknown_level_is_rejected(self, db_session, client):
        resp = client.post("/admin/tutorials", json={
            "slug": "ok-slug", "title": "Fine title",
            "description": "A long enough description.", "level": "expert",
        }, headers=auth_headers(_admin(db_session)))
        assert resp.status_code == 422

    def test_the_slug_CANNOT_be_renamed(self, db_session, client):
        """Renaming orphans every progress row pointing at it, and there is no
        FK to stop it. The field is absent from the patch model entirely, so
        the API cannot do it rather than merely declining to."""
        seed_catalogue(db_session)
        headers = auth_headers(_admin(db_session))
        resp = client.put(f"/admin/tutorials/{FIRST}",
                          json={"slug": "renamed", "title": "New Title"},
                          headers=headers)
        assert resp.status_code == 200
        assert resp.json()["slug"] == FIRST          # unchanged
        assert resp.json()["title"] == "New Title"   # the real edit applied
        assert client.get(f"/tutorials/{FIRST}").status_code == 200

    def test_omitted_fields_are_left_alone(self, db_session, client):
        """exclude_unset: a PUT that sets only the title must not blank the
        description."""
        seed_catalogue(db_session)
        headers = auth_headers(_admin(db_session))
        before = client.get(f"/tutorials/{FIRST}").json()
        client.put(f"/admin/tutorials/{FIRST}", json={"title": "Renamed"},
                   headers=headers)
        after = client.get(f"/tutorials/{FIRST}").json()
        assert after["title"] == "Renamed"
        assert after["description"] == before["description"]
        assert after["level"] == before["level"]

    def test_youtube_id_can_be_CLEARED(self, db_session, client):
        """Explicit null must be distinguishable from an omitted field, or a
        wrong video id can never be removed."""
        seed_catalogue(db_session, published=True, with_video=True)
        headers = auth_headers(_admin(db_session))
        assert client.get(f"/tutorials/{FIRST}").json()["youtube_id"]

        resp = client.put(f"/admin/tutorials/{FIRST}", json={"youtube_id": None},
                          headers=headers)
        assert resp.json()["youtube_id"] is None
        assert resp.json()["is_placeholder"] is True

    def test_delete_KEEPS_user_progress(self, db_session, client):
        """Progress outliving a deleted tutorial is the forgiving behaviour:
        re-creating the slug restores every user's history. A cascade would
        make an accidental delete silent and unrecoverable."""
        seed_catalogue(db_session, published=True)
        headers = auth_headers(_admin(db_session))
        client.post(f"/tutorials/{FIRST}/complete")

        resp = client.delete(f"/admin/tutorials/{FIRST}", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["progress_rows_kept"] == 1

        rows = db_session.execute(
            select(m.TutorialProgress).where(
                m.TutorialProgress.tutorial_slug == FIRST)
        ).scalars().all()
        assert len(rows) == 1 and rows[0].completed is True
        assert client.get(f"/tutorials/{FIRST}").status_code == 404

    def test_recreating_a_deleted_slug_restores_progress(self, db_session, client):
        seed_catalogue(db_session, published=True)
        headers = auth_headers(_admin(db_session))
        client.post(f"/tutorials/{FIRST}/complete")
        client.delete(f"/admin/tutorials/{FIRST}", headers=headers)

        client.post("/admin/tutorials", json={
            "slug": FIRST, "title": "Restored", "description": "Back again now.",
            "level": "beginner", "youtube_id": "abc12345678",
            "is_published": True,
        }, headers=headers)

        assert _progress_of(_list(client), FIRST)["completed"] is True

    def test_unknown_slug_is_404(self, db_session, client):
        headers = auth_headers(_admin(db_session))
        assert client.put("/admin/tutorials/nope", json={"title": "New title"},
                          headers=headers).status_code == 404
        assert client.delete("/admin/tutorials/nope",
                             headers=headers).status_code == 404
        assert client.post("/admin/tutorials/nope/publish",
                           headers=headers).status_code == 404

    def test_completions_endpoint_still_reports_unpublished(self, db_session,
                                                            client):
        """An admin needs counts for a tutorial they just unpublished —
        otherwise the numbers vanish exactly when someone asks why engagement
        dropped."""
        seed_catalogue(db_session, published=True)
        headers = auth_headers(_admin(db_session))
        client.post(f"/tutorials/{FIRST}/complete")
        client.post(f"/admin/tutorials/{FIRST}/unpublish", headers=headers)

        data = client.get("/admin/tutorials/completions", headers=headers).json()
        row = next(r for r in data["tutorials"] if r["slug"] == FIRST)
        assert row["completed_count"] == 1
        assert row["is_published"] is False
