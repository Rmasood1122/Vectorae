"""Feature 3 — founder voice cloning.

Pins the closed vocabularies, the one-profile-per-product rule, what the voice
instructions actually say, the send-path integration, and the three routes.
"""

import uuid

import pytest

from app.db import models as m
from app.services import message_personalization, voice_profiler
from app.workers import voice_tasks

POSTS = [
    "Most agency owners never ask why their pipeline dried up. They just buy "
    "more leads. That is backwards.",
    "Here is the thing about outbound. It is not a volume problem. It is a "
    "relevance problem, and no amount of sending fixes that.",
]


@pytest.fixture()
def product(db_session, test_user):
    product = m.Product(user_id=test_user.id, name="Victorae",
                        description="Outbound for boutique agencies",
                        type=m.ProductType.SKILL)
    db_session.add(product)
    db_session.commit()
    return product


class TestExtraction:
    def test_stores_dimensions_phrases_and_posts(self, db_session, product,
                                                 fake_claude):
        profile = voice_profiler.extract_voice_profile(db_session, product.id, POSTS)

        assert profile.product_id == product.id
        assert profile.post_count == 2
        assert profile.raw_posts_json == POSTS
        assert profile.sample_phrases == ["here is the thing", "no fluff"]
        assert profile.style_dimensions_json["avg_sentence_length"] == "short"
        assert profile.style_dimensions_json["uses_numbers"] is True
        assert profile.last_analyzed_at is not None

    def test_every_dimension_is_forced_into_its_closed_vocabulary(self, db_session,
                                                                  product, fake_claude):
        """A creative or malformed answer must never put free text into an
        outreach prompt."""
        fake_claude.voice_profile_response = {
            "avg_sentence_length": "extremely punchy",
            "punctuation_style": "!!!",
            "opens_with": "a haiku",
            "uses_numbers": "yes please",
            "emoji_usage": "lots",
            "paragraph_length": "epic",
            "vocabulary_level": "galaxy-brained",
            "signature_phrases": "not a list",
        }

        profile = voice_profiler.extract_voice_profile(db_session, product.id, POSTS)

        dimensions = profile.style_dimensions_json
        for name, (allowed, _default) in voice_profiler.DIMENSIONS.items():
            assert dimensions[name] in allowed, name
        assert dimensions["uses_numbers"] is True          # coerced to bool
        assert profile.sample_phrases == []

    def test_caps_signature_phrases_at_five(self, db_session, product, fake_claude):
        fake_claude.voice_profile_response = {
            "avg_sentence_length": "short",
            "signature_phrases": [f"phrase {i}" for i in range(12)]}
        profile = voice_profiler.extract_voice_profile(db_session, product.id, POSTS)
        assert len(profile.sample_phrases) == voice_profiler.MAX_PHRASES

    def test_re_analysing_updates_the_same_row(self, db_session, product, fake_claude):
        """One profile per product, enforced by the schema -- a second analysis
        must not create a second answer to 'which voice does this send in?'."""
        first = voice_profiler.extract_voice_profile(db_session, product.id, POSTS)
        first_id = first.id

        second = voice_profiler.extract_voice_profile(db_session, product.id,
                                                      POSTS + [POSTS[0]])

        assert second.id == first_id
        assert second.post_count == 3
        assert db_session.query(m.VoiceProfile).count() == 1

    def test_rejects_unusable_posts(self, db_session, product, fake_claude):
        with pytest.raises(voice_profiler.InvalidPosts):
            voice_profiler.extract_voice_profile(db_session, product.id, ["hi", "  "])
        with pytest.raises(voice_profiler.InvalidPosts):
            voice_profiler.extract_voice_profile(db_session, product.id, [])

    def test_rejects_more_than_twenty_posts(self, db_session, product, fake_claude):
        with pytest.raises(voice_profiler.InvalidPosts):
            voice_profiler.clean_posts([POSTS[0]] * 21)

    def test_unknown_product_raises_lookup_error(self, db_session, fake_claude):
        with pytest.raises(LookupError):
            voice_profiler.extract_voice_profile(db_session, uuid.uuid4(), POSTS)


class TestApplyVoiceToBrief:
    def _profile(self, **dimensions):
        base = {"avg_sentence_length": "medium", "punctuation_style": "standard",
                "opens_with": "statement", "uses_numbers": False,
                "emoji_usage": "none", "paragraph_length": "short",
                "vocabulary_level": "conversational"}
        base.update(dimensions)
        return m.VoiceProfile(product_id=uuid.uuid4(), style_dimensions_json=base,
                              sample_phrases=[])

    def test_no_profile_returns_the_brief_unchanged(self):
        assert voice_profiler.apply_voice_to_brief("open on their post", None) == \
            "open on their post"

    def test_short_sentences_become_an_instruction(self):
        out = voice_profiler.apply_voice_to_brief(
            "brief", self._profile(avg_sentence_length="short"))
        assert "under 10 words" in out

    def test_opens_with_question_becomes_an_instruction(self):
        out = voice_profiler.apply_voice_to_brief(
            "brief", self._profile(opens_with="question"))
        assert "Open the message with a question." in out

    def test_uses_numbers_asks_for_one_real_stat_and_forbids_inventing(self):
        out = voice_profiler.apply_voice_to_brief(
            "brief", self._profile(uses_numbers=True))
        assert "ONE concrete number" in out
        assert "never invent one" in out

    def test_uses_numbers_false_forbids_statistics(self):
        out = voice_profiler.apply_voice_to_brief(
            "brief", self._profile(uses_numbers=False))
        assert "Do not add any." in out

    def test_emoji_none_forbids_emojis(self):
        out = voice_profiler.apply_voice_to_brief(
            "brief", self._profile(emoji_usage="none"))
        assert "Never use an emoji." in out

    def test_the_original_brief_is_always_preserved(self):
        out = voice_profiler.apply_voice_to_brief("open on their hiring post",
                                                   self._profile())
        assert out.startswith("open on their hiring post")

    def test_a_broken_profile_degrades_to_the_plain_brief(self):
        """A missing voice must never block a send."""
        broken = m.VoiceProfile(product_id=uuid.uuid4(), style_dimensions_json=None,
                                sample_phrases=None)
        assert voice_profiler.apply_voice_to_brief("brief", broken) == "brief"


class TestSendPathIntegration:
    def test_render_message_sends_the_voice_enriched_brief(self, db_session, product,
                                                           fake_claude):
        strategy = m.Strategy(product_id=product.id, flow_type=m.FlowType.WITH_CLIENTS)
        db_session.add(strategy)
        db_session.flush()
        sequence = m.Sequence(strategy_id=strategy.id, channel=m.ChannelType.EMAIL,
                              name="cold")
        db_session.add(sequence)
        db_session.flush()
        step = m.SequenceStep(sequence_id=sequence.id, step_no=1,
                              template="open on their inspection backlog")
        lead = m.Lead(strategy_id=strategy.id, source="manual", external_id="v1",
                      full_name="Sara Khan", company="Acme", email="s@acme.test")
        db_session.add_all([step, lead])
        db_session.commit()
        voice_profiler.extract_voice_profile(db_session, product.id, POSTS)

        inputs: dict = {}
        message_personalization.render_message(db_session, strategy, lead, step,
                                               inputs_out=inputs)

        _system, prompt = fake_claude.personalization_calls[-1]
        assert "open on their inspection backlog" in prompt      # brief preserved
        assert "Open the message with a question." in prompt     # voice applied
        assert "under 10 words" in prompt
        assert inputs["voice_profile"] is True

    def test_no_profile_leaves_the_send_path_exactly_as_it_was(self, db_session,
                                                               product, fake_claude):
        strategy = m.Strategy(product_id=product.id, flow_type=m.FlowType.WITH_CLIENTS)
        db_session.add(strategy)
        db_session.flush()
        sequence = m.Sequence(strategy_id=strategy.id, channel=m.ChannelType.EMAIL,
                              name="cold")
        db_session.add(sequence)
        db_session.flush()
        step = m.SequenceStep(sequence_id=sequence.id, step_no=1, template="a brief")
        lead = m.Lead(strategy_id=strategy.id, source="manual", external_id="v2",
                      full_name="Sara", company="Acme", email="s2@acme.test")
        db_session.add_all([step, lead])
        db_session.commit()

        inputs: dict = {}
        message_personalization.render_message(db_session, strategy, lead, step,
                                               inputs_out=inputs)

        _system, prompt = fake_claude.personalization_calls[-1]
        assert "VOICE —" not in prompt
        assert inputs["voice_profile"] is False


class TestTask:
    def test_builds_and_reports(self, db_session, product, fake_claude):
        result = voice_tasks.build_voice_profile_impl(db_session, product.id, POSTS)
        assert result["status"] == "built"
        assert result["post_count"] == 2

    def test_is_idempotent(self, db_session, product, fake_claude):
        voice_tasks.build_voice_profile_impl(db_session, product.id, POSTS)
        voice_tasks.build_voice_profile_impl(db_session, product.id, POSTS)
        assert db_session.query(m.VoiceProfile).count() == 1

    def test_a_model_outage_leaves_the_existing_profile_alone(self, db_session,
                                                              product, fake_claude):
        voice_tasks.build_voice_profile_impl(db_session, product.id, POSTS)
        fake_claude.voice_profile_response = RuntimeError("anthropic is down")

        result = voice_tasks.build_voice_profile_impl(db_session, product.id, POSTS)

        assert result["status"] == "failed"
        profile = voice_profiler.get_profile(db_session, product.id)
        assert profile is not None and profile.post_count == 2

    def test_bad_input_is_a_status_not_a_traceback(self, db_session, product,
                                                   fake_claude):
        assert voice_tasks.build_voice_profile_impl(
            db_session, product.id, [])["status"] == "invalid"
        assert voice_tasks.build_voice_profile_impl(
            db_session, uuid.uuid4(), POSTS)["status"] == "not_found"


class TestEndpoints:
    def test_analyze_queues_and_returns_a_task_id(self, client, product, queued_jobs):
        response = client.post(f"/products/{product.id}/voice-profile/analyze",
                               json={"posts": POSTS})

        assert response.status_code == 202
        assert response.json()["status"] == "queued"
        assert response.json()["task_id"]
        assert queued_jobs["voice"] == [(str(product.id), POSTS)]

    def test_analyze_validates_before_queueing_anything(self, client, product,
                                                        queued_jobs):
        response = client.post(f"/products/{product.id}/voice-profile/analyze",
                               json={"posts": ["hi"]})
        assert response.status_code == 422
        assert queued_jobs["voice"] == []

    def test_analyze_rejects_more_than_twenty_posts(self, client, product):
        response = client.post(f"/products/{product.id}/voice-profile/analyze",
                               json={"posts": [POSTS[0]] * 21})
        assert response.status_code == 422

    def test_get_returns_the_profile(self, client, db_session, product, fake_claude):
        voice_profiler.extract_voice_profile(db_session, product.id, POSTS)

        body = client.get(f"/products/{product.id}/voice-profile").json()

        assert body["post_count"] == 2
        assert body["style_dimensions"]["opens_with"] == "question"
        assert body["sample_phrases"] == ["here is the thing", "no fluff"]
        # The posts themselves are NOT exposed by the read endpoint.
        assert "raw_posts" not in body

    def test_get_is_404_when_none_has_been_analysed(self, client, product):
        assert client.get(f"/products/{product.id}/voice-profile").status_code == 404

    def test_delete_reverts_to_the_default_voice(self, client, db_session, product,
                                                 fake_claude):
        voice_profiler.extract_voice_profile(db_session, product.id, POSTS)

        assert client.delete(f"/products/{product.id}/voice-profile").status_code == 204

        assert voice_profiler.get_profile(db_session, product.id) is None

    def test_delete_is_idempotent(self, client, product):
        assert client.delete(f"/products/{product.id}/voice-profile").status_code == 204
        assert client.delete(f"/products/{product.id}/voice-profile").status_code == 204

    def test_another_users_product_is_404_on_every_route(self, client, db_session):
        other = m.User(email="stranger3@example.com", plan=m.PlanTier.PRO)
        db_session.add(other)
        db_session.flush()
        theirs = m.Product(user_id=other.id, name="theirs", description="x",
                           type=m.ProductType.SKILL)
        db_session.add(theirs)
        db_session.commit()

        assert client.get(f"/products/{theirs.id}/voice-profile").status_code == 404
        assert client.delete(f"/products/{theirs.id}/voice-profile").status_code == 404
        assert client.post(f"/products/{theirs.id}/voice-profile/analyze",
                           json={"posts": POSTS}).status_code == 404
