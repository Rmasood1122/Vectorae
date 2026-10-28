"""Feature 4 — competitor displacement alerts.

Pins the three trigger groups (including the word-boundary rule that stops
"Clay" firing on "declaim"), the Day 0 DM rules, deduplication and expiry, the
sweep's idempotency, and the two routes.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.db import models as m
from app.services import displacement_monitor as dm
from app.workers import displacement_tasks

NOW = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def watched(db_session, product_with_strategy, monkeypatch):
    """A strategy with one lead who has a LinkedIn profile and recent posts.

    The post refresh is stubbed out: this feature's job is to decide what to
    do with posts, and the fetching path has its own tests
    (tests/test_personalization.py).
    """
    _product, strategy = product_with_strategy(m.FlowType.WITH_CLIENTS)
    strategy.status = m.StrategyStatus.EXECUTING
    lead = m.Lead(strategy_id=strategy.id, source="manual", external_id="d1",
                  full_name="Sara Khan", company="Acme Fire", title="Founder",
                  email="sara@acmefire.test", status=m.LeadStatus.CONTACTED,
                  linkedin_url="https://www.linkedin.com/in/sara-khan/")
    db_session.add(lead)
    db_session.commit()

    monkeypatch.setattr(
        "app.services.personalization_context.ensure_fresh",
        lambda session, lead, **kwargs: {"posts": "stubbed"})
    return strategy, lead


def _posts(*texts, url="https://li/p/1"):
    return [{"text": text, "posted_at": NOW.isoformat(), "url": url}
            for text in texts]


class TestTriggers:
    @pytest.mark.parametrize("text,expected", [
        ("We moved off Apollo last month", ["Apollo"]),
        ("Clay is great but expensive", ["Clay"]),
        ("our pipeline dried up in August", ["pipeline dried up"]),
        ("honestly cold email not working anymore", ["cold email not working"]),
        ("I fired my SDR in June", ["fired my SDR"]),
        ("we outsourced sales and regretted it", ["outsourced sales"]),
    ])
    def test_each_group_fires(self, text, expected):
        assert dm.scan_post_for_triggers(text) == expected

    def test_tool_names_match_on_word_boundaries(self):
        """The bug this prevents: alerting the founder every time a prospect
        posts the word 'declaim' or 'claylike'."""
        assert dm.scan_post_for_triggers("he began to declaim loudly") == []
        assert dm.scan_post_for_triggers("claylike soil this year") == []
        assert dm.scan_post_for_triggers("Apollonian ideals") == []

    def test_matching_is_case_insensitive(self):
        assert dm.scan_post_for_triggers("APOLLO is fine") == ["Apollo"]
        assert dm.scan_post_for_triggers("Our Pipeline Dried Up") == ["pipeline dried up"]

    def test_multiple_matches_are_all_returned_without_duplicates(self):
        matched = dm.scan_post_for_triggers(
            "We left Apollo and Clay, and now our pipeline dried up. Apollo again.")
        assert matched == ["Apollo", "Clay", "pipeline dried up"]

    def test_an_ordinary_post_fires_nothing(self):
        assert dm.scan_post_for_triggers("Great conference in Austin today") == []
        assert dm.scan_post_for_triggers("") == []
        assert dm.scan_post_for_triggers(None) == []

    def test_excerpt_is_capped_and_whitespace_normalised(self):
        assert dm.excerpt_of("a\n\n  b") == "a b"
        assert len(dm.excerpt_of("x" * 500)) == dm.EXCERPT_CHARS


class TestDayZeroRules:
    def test_a_compliant_dm_has_no_problems(self):
        assert dm.validate_dm(
            "You wrote that your pipeline dried up. That is usually a list "
            "problem. What changed about who you were writing to?") == []

    def test_too_many_sentences_is_rejected(self):
        problems = dm.validate_dm("One. Two. Three. Four. Five? ")
        assert any("sentences" in p for p in problems)

    def test_more_than_one_question_is_rejected(self):
        problems = dm.validate_dm("Saw your post. What changed? And when?")
        assert any("questions" in p for p in problems)

    def test_no_question_is_rejected(self):
        assert any("no question" in p for p in dm.validate_dm("Saw your post."))

    def test_naming_the_product_is_rejected(self):
        problems = dm.validate_dm("Vectorae fixes this. Want a look?")
        assert any("names the product" in p for p in problems)

    @pytest.mark.parametrize("name", ["Vectorae", "VECTORAE", "vectorae",
                                      "Victorae", "VICTORAE", "victorae"])
    def test_every_product_name_is_rejected(self, name):
        problems = dm.validate_dm(f"{name} fixes this. Want a look?")
        assert any("names the product" in p for p in problems)

    def test_banned_words_are_rejected(self):
        problems = dm.validate_dm("Our platform fixes this. Worth a look?")
        assert any("banned words" in p for p in problems)

    def test_an_empty_draft_is_rejected(self):
        assert dm.validate_dm("") == ["the draft is empty"]


class TestDmGeneration:
    def test_the_prompt_carries_the_post_and_the_matched_words(self, db_session,
                                                               watched, fake_claude):
        _strategy, lead = watched
        dm.generate_displacement_dm(lead, "our pipeline dried up in August",
                                    ["pipeline dried up"])
        prompt = fake_claude.displacement_dm_prompts[0]
        assert "our pipeline dried up in August" in prompt
        assert "pipeline dried up" in prompt
        assert "Sara Khan" in prompt

    def test_a_rule_breaking_draft_is_regenerated_once(self, db_session, watched,
                                                       fake_claude):
        _strategy, lead = watched
        fake_claude.displacement_dm_response = [
            {"dm": "Vectorae fixes this. One. Two. Three. Want a look?"},
            {"dm": "You said the pipeline dried up. Usually that is the list. "
                   "What changed?"},
        ]

        result = dm.generate_displacement_dm(lead, "pipeline dried up",
                                             ["pipeline dried up"])

        assert len(fake_claude.displacement_dm_prompts) == 2
        assert dm.validate_dm(result) == []

    def test_two_bad_attempts_return_none_rather_than_a_bad_dm(self, db_session,
                                                               watched, fake_claude):
        """An alert with no DM still shows the evidence. A rule-breaking DM
        one click from being sent is strictly worse."""
        _strategy, lead = watched
        fake_claude.displacement_dm_response = {"dm": "Vectorae is a platform. Yes?"}

        assert dm.generate_displacement_dm(lead, "x", ["Apollo"]) is None
        assert len(fake_claude.displacement_dm_prompts) == 2

    def test_a_model_outage_returns_none_instead_of_raising(self, db_session,
                                                            watched, fake_claude):
        _strategy, lead = watched
        fake_claude.displacement_dm_response = RuntimeError("anthropic is down")
        assert dm.generate_displacement_dm(lead, "x", ["Apollo"]) is None


class TestScan:
    def test_creates_an_alert_with_evidence_and_a_dm(self, db_session, watched,
                                                     fake_claude):
        strategy, lead = watched
        lead.linkedin_posts_json = _posts("Honestly, cold email not working for us.")
        db_session.commit()

        created = dm.run_displacement_scan(db_session, strategy.id, now=NOW)

        assert created == 1
        alert = db_session.query(m.DisplacementAlert).one()
        assert alert.lead_id == lead.id
        assert alert.matched_keywords == ["cold email not working"]
        assert "cold email not working" in alert.post_excerpt
        assert alert.post_url == "https://li/p/1"
        assert alert.suggested_dm
        assert alert.alert_status == "pending"
        # SQLite hands timestamps back without tzinfo (which is exactly why
        # displacement_monitor.is_expired reads a naive value as UTC), so
        # compare the instant, not the tzinfo.
        expected = NOW + timedelta(days=dm.EXPIRY_DAYS)
        assert alert.expires_at.replace(tzinfo=timezone.utc) == expected

    def test_an_uninteresting_post_creates_nothing(self, db_session, watched,
                                                   fake_claude):
        strategy, lead = watched
        lead.linkedin_posts_json = _posts("Great conference in Austin today.")
        db_session.commit()

        assert dm.run_displacement_scan(db_session, strategy.id, now=NOW) == 0
        assert db_session.query(m.DisplacementAlert).count() == 0

    def test_one_alert_per_lead_even_with_several_matching_posts(self, db_session,
                                                                 watched, fake_claude):
        strategy, lead = watched
        lead.linkedin_posts_json = _posts("We left Apollo.", "Pipeline dried up.",
                                          "Fired my SDR.")
        db_session.commit()

        assert dm.run_displacement_scan(db_session, strategy.id, now=NOW) == 1

    def test_deduplicates_inside_the_fourteen_day_window(self, db_session, watched,
                                                         fake_claude):
        strategy, lead = watched
        lead.linkedin_posts_json = _posts("We left Apollo.")
        db_session.commit()
        dm.run_displacement_scan(db_session, strategy.id, now=NOW)

        later = NOW + timedelta(days=dm.DEDUP_DAYS - 1)
        assert dm.run_displacement_scan(db_session, strategy.id, now=later) == 0
        assert db_session.query(m.DisplacementAlert).count() == 1

    def test_alerts_again_once_the_window_has_passed(self, db_session, watched,
                                                     fake_claude):
        strategy, lead = watched
        lead.linkedin_posts_json = _posts("We left Apollo.")
        db_session.commit()
        dm.run_displacement_scan(db_session, strategy.id, now=NOW)

        later = NOW + timedelta(days=dm.DEDUP_DAYS + 1)
        assert dm.run_displacement_scan(db_session, strategy.id, now=later) == 1

    def test_a_lead_with_no_linkedin_url_is_not_watched(self, db_session, watched,
                                                        fake_claude):
        strategy, lead = watched
        lead.linkedin_url = None
        lead.linkedin_posts_json = _posts("We left Apollo.")
        db_session.commit()
        assert dm.run_displacement_scan(db_session, strategy.id, now=NOW) == 0

    def test_a_dropped_lead_is_not_watched(self, db_session, watched, fake_claude):
        strategy, lead = watched
        lead.status = m.LeadStatus.DROPPED
        lead.linkedin_posts_json = _posts("We left Apollo.")
        db_session.commit()
        assert dm.run_displacement_scan(db_session, strategy.id, now=NOW) == 0

    def test_leads_at_any_other_status_are_watched(self, db_session, watched,
                                                   fake_claude):
        strategy, lead = watched
        lead.status = m.LeadStatus.CLOSED_LOST
        lead.linkedin_posts_json = _posts("We left Apollo.")
        db_session.commit()
        assert dm.run_displacement_scan(db_session, strategy.id, now=NOW) == 1

    def test_an_unknown_strategy_returns_zero_rather_than_raising(self, db_session):
        assert dm.run_displacement_scan(db_session, uuid.uuid4(), now=NOW) == 0
        assert dm.run_displacement_scan(db_session, "not-a-uuid", now=NOW) == 0

    def test_a_malformed_cached_post_does_not_stop_the_scan(self, db_session, watched,
                                                            fake_claude):
        strategy, lead = watched
        lead.linkedin_posts_json = ["a bare string", None,
                                    {"text": "We left Apollo.", "url": None}]
        db_session.commit()
        assert dm.run_displacement_scan(db_session, strategy.id, now=NOW) == 1


class TestExpiry:
    def test_a_pending_alert_past_expiry_reads_as_expired(self, db_session, watched):
        _strategy, lead = watched
        alert = m.DisplacementAlert(lead_id=lead.id, post_excerpt="x",
                                    alert_status="pending", created_at=NOW,
                                    expires_at=NOW + timedelta(days=7))
        assert dm.effective_status(alert, NOW + timedelta(days=8)) == "expired"
        assert dm.effective_status(alert, NOW + timedelta(days=1)) == "pending"

    def test_an_acted_alert_never_reads_as_expired(self, db_session, watched):
        _strategy, lead = watched
        alert = m.DisplacementAlert(lead_id=lead.id, post_excerpt="x",
                                    alert_status="acted", created_at=NOW,
                                    expires_at=NOW + timedelta(days=7))
        assert dm.effective_status(alert, NOW + timedelta(days=30)) == "acted"


class TestTasks:
    def test_scan_all_covers_only_scannable_strategies(self, db_session, watched,
                                                       product_with_strategy,
                                                       fake_claude):
        strategy, lead = watched
        lead.linkedin_posts_json = _posts("We left Apollo.")
        product_with_strategy(m.FlowType.WITH_CLIENTS)   # PENDING, out of scope
        db_session.commit()

        result = displacement_tasks.scan_all_strategies_impl(db_session)

        assert result == {"strategies": 1, "alerts_created": 1, "failed": 0}

    def test_the_sweep_is_idempotent(self, db_session, watched, fake_claude):
        strategy, lead = watched
        lead.linkedin_posts_json = _posts("We left Apollo.")
        db_session.commit()

        displacement_tasks.scan_all_strategies_impl(db_session)
        second = displacement_tasks.scan_all_strategies_impl(db_session)

        assert second["alerts_created"] == 0
        assert db_session.query(m.DisplacementAlert).count() == 1


class TestEndpoints:
    @pytest.fixture()
    def alerted(self, client, db_session, watched, fake_claude):
        strategy, lead = watched
        lead.linkedin_posts_json = _posts("Honestly, cold email not working.")
        db_session.commit()
        dm.run_displacement_scan(db_session, strategy.id,
                                 now=datetime.now(timezone.utc))
        return strategy, lead, db_session.query(m.DisplacementAlert).one()

    def test_list_returns_the_alert_with_its_evidence(self, client, alerted):
        strategy, lead, alert = alerted

        response = client.get(f"/strategies/{strategy.id}/displacement-alerts")

        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]["id"] == str(alert.id)
        assert body[0]["lead_name"] == "Sara Khan"
        assert body[0]["matched_keywords"] == ["cold email not working"]
        assert body[0]["status"] == "pending"
        assert body[0]["suggested_dm"]

    def test_status_filter(self, client, db_session, alerted):
        strategy, _lead, alert = alerted
        assert len(client.get(
            f"/strategies/{strategy.id}/displacement-alerts?status=pending").json()) == 1
        assert client.get(
            f"/strategies/{strategy.id}/displacement-alerts?status=acted").json() == []

    def test_an_expired_alert_leaves_the_pending_queue(self, client, db_session,
                                                       alerted):
        strategy, _lead, alert = alerted
        alert.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        db_session.commit()

        pending = client.get(
            f"/strategies/{strategy.id}/displacement-alerts?status=pending").json()
        expired = client.get(
            f"/strategies/{strategy.id}/displacement-alerts?status=expired").json()

        assert pending == []
        assert len(expired) == 1 and expired[0]["status"] == "expired"

    def test_patch_acted_stamps_acted_at(self, client, db_session, alerted):
        _strategy, _lead, alert = alerted

        response = client.patch(f"/alerts/{alert.id}/displacement",
                                json={"status": "acted"})

        assert response.status_code == 200
        assert response.json()["status"] == "acted"
        assert response.json()["acted_at"] is not None

    def test_patch_dismissed_does_not_stamp_acted_at(self, client, alerted):
        _strategy, _lead, alert = alerted
        body = client.patch(f"/alerts/{alert.id}/displacement",
                            json={"status": "dismissed"}).json()
        assert body["status"] == "dismissed"
        assert body["acted_at"] is None

    def test_patch_rejects_an_unknown_status(self, client, alerted):
        _strategy, _lead, alert = alerted
        assert client.patch(f"/alerts/{alert.id}/displacement",
                            json={"status": "snoozed"}).status_code == 422

    def test_unknown_alert_is_404(self, client):
        assert client.patch(f"/alerts/{uuid.uuid4()}/displacement",
                            json={"status": "acted"}).status_code == 404

    def test_another_users_strategy_is_404(self, client, db_session):
        other = m.User(email="stranger4@example.com", plan=m.PlanTier.PRO)
        db_session.add(other)
        db_session.flush()
        product = m.Product(user_id=other.id, name="theirs", description="x",
                            type=m.ProductType.SKILL)
        db_session.add(product)
        db_session.flush()
        strategy = m.Strategy(product_id=product.id, flow_type=m.FlowType.WITH_CLIENTS)
        db_session.add(strategy)
        db_session.commit()

        assert client.get(
            f"/strategies/{strategy.id}/displacement-alerts").status_code == 404
