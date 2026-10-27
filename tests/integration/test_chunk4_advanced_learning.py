"""
Chunk 4 — Advanced Learning Engine tests (20 tests)

Coverage:
  Send-time optimizer  (4)
  Score decay          (3)
  Multi-variate        (5)
  Subject intelligence (4)
  Personalization      (4)
"""
from __future__ import annotations

import datetime
import json
import math
import re
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_redis():
    store: dict = {}
    redis = MagicMock()
    redis.get.side_effect = lambda k: store.get(k)
    redis.set.side_effect = lambda k, v, ex=None: store.__setitem__(k, v)
    redis.delete.side_effect = lambda *keys: [store.pop(k, None) for k in keys]
    redis.keys.side_effect = lambda pat: [k.encode() for k in store if _match_pattern(pat, k)]
    redis.ttl.side_effect = lambda k: 1800 if k in store else -2
    redis.pipeline.return_value.__enter__ = lambda s: s
    redis.pipeline.return_value.__exit__ = MagicMock()
    redis.pipeline.return_value.incr = MagicMock(return_value=None)
    redis.pipeline.return_value.ttl = MagicMock(return_value=None)
    redis.pipeline.return_value.execute = MagicMock(return_value=[1, 3600])
    return redis


def _match_pattern(pattern: str, key: str) -> bool:
    import fnmatch
    return fnmatch.fnmatch(key, pattern)


@pytest.fixture
def mock_db():
    return MagicMock()


# ===========================================================================
# Send-time optimizer (4 tests)
# ===========================================================================

# Slots are cached per account (send_time_optimizer._cache_key).
OWNER = "00000000-0000-0000-0000-0000000000aa"


class TestSendTimeOptimizer:

    def test_fallback_when_no_cache(self, mock_redis):
        with patch("app.core.redis_client.get_sync_redis", return_value=mock_redis):
            from app.services.send_time_optimizer import get_send_time_recommendation
            rec = get_send_time_recommendation("gmail", "SaaS", "SMB", owner_id=OWNER)
            assert rec.fallback_used is True
            assert rec.confidence == "no_data"
            assert len(rec.recommended_slots) > 0

    def test_cached_result_returned(self, mock_redis):
        cached = {
            "slots": [
                {"day_of_week": 1, "hour_utc": 9, "expected_reply_rate": 0.15, "sample_size": 50, "is_reliable": True}
            ]
        }
        # The mock_redis fixture gives `get` a side_effect backed by `store`,
        # and side_effect takes precedence over return_value -- so setting
        # `mock_redis.get.return_value` here did nothing and the service read
        # a miss. Seed the store through the same key the service computes.
        from app.services.send_time_optimizer import _cache_key

        mock_redis.set(_cache_key("gmail", "SaaS", "SMB", owner_id=OWNER),
                       json.dumps(cached).encode())
        with patch("app.core.redis_client.get_sync_redis", return_value=mock_redis):
            from app.services.send_time_optimizer import get_send_time_recommendation
            rec = get_send_time_recommendation("gmail", "SaaS", "SMB", owner_id=OWNER)
            assert rec.fallback_used is False
            # Confidence is a function of TOTAL sample size, not slot count:
            # _confidence_from_n(50) is "high" (>= 50). The old expectation of
            # "low" contradicted the same file's test_confidence_levels.
            assert rec.confidence == "high"
            assert rec.recommended_slots[0]["expected_reply_rate"] == 0.15

    def test_confidence_levels(self, mock_redis):
        from app.services.send_time_optimizer import _confidence_from_n
        assert _confidence_from_n(0) == "no_data"
        assert _confidence_from_n(10) == "low"
        assert _confidence_from_n(25) == "medium"
        assert _confidence_from_n(100) == "high"

    def test_pick_next_send_datetime_reliable(self, mock_redis):
        from app.services.send_time_optimizer import SendTimeRecommendation, pick_next_send_datetime
        rec = SendTimeRecommendation(
            channel="gmail",
            icp_industry="Tech",
            icp_company_size="Mid-Market",
            recommended_slots=[
                {"day_of_week": 1, "hour_utc": 9, "expected_reply_rate": 0.15, "sample_size": 50, "is_reliable": True}
            ],
            confidence="high",
            fallback_used=False,
        )
        # Monday 2026-01-05 00:00 UTC; next Mon 09:00 should be within 48h
        from_dt = datetime.datetime(2026, 1, 5, 0, 0, 0)  # Monday
        result = pick_next_send_datetime(rec, from_dt)
        # Should find Monday 09:00
        assert result is not None
        assert result.hour == 9
        assert result.weekday() == 0  # Monday


# ===========================================================================
# Score decay (3 tests)
# ===========================================================================

class TestScoreDecay:

    def test_recent_events_weighted_higher(self):
        from app.services.score_decay import compute_decayed_score
        now = datetime.datetime.utcnow()
        outcomes = [
            {"ts": now - datetime.timedelta(days=1), "is_reply": 1},    # recent success
            {"ts": now - datetime.timedelta(days=1), "is_reply": 1},    # recent success
            {"ts": now - datetime.timedelta(days=300), "is_reply": 0},  # old failure
            {"ts": now - datetime.timedelta(days=300), "is_reply": 0},  # old failure
        ]
        score, eff_n = compute_decayed_score(outcomes, "is_reply", half_life_days=90)
        # Recent successes should dominate — score > 0.5
        assert score > 0.5
        assert eff_n > 0

    def test_empty_outcomes_returns_zero(self):
        from app.services.score_decay import compute_decayed_score
        score, eff_n = compute_decayed_score([], "is_reply")
        assert score == 0.0
        assert eff_n == 0.0

    def test_trend_detection(self):
        from app.services.score_decay import determine_trend
        assert determine_trend(0.15, 0.10) == "rising"
        assert determine_trend(0.10, 0.15) == "falling"
        assert determine_trend(0.10, 0.10) == "stable"
        assert determine_trend(0.10, None) == "new"


# ===========================================================================
# Multi-variate testing (5 tests)
# ===========================================================================

class TestMultiVariate:

    def test_kruskal_wallis_identical_groups_not_significant(self):
        """Identical distributions → H≈0, p≈1.0"""
        from app.services.multi_variate import _kruskal_wallis_h
        groups = {
            "A": [1, 0, 1, 0, 1, 0, 1, 0, 1, 0],
            "B": [1, 0, 1, 0, 1, 0, 1, 0, 1, 0],
        }
        H, p = _kruskal_wallis_h(groups)
        assert p > 0.05

    def test_kruskal_wallis_clearly_different_groups(self):
        """One group all 1s, one all 0s → p should be very small."""
        from app.services.multi_variate import _kruskal_wallis_h
        groups = {
            "A": [1] * 30,
            "B": [0] * 30,
        }
        H, p = _kruskal_wallis_h(groups)
        assert p < 0.001

    def test_bonferroni_threshold_scales_with_pairs(self):
        """With k=4 variants there are 6 pairs; Bonferroni threshold = 0.05/6."""
        from app.services.multi_variate import compare_multi_variants
        # 4 variants with too little data → should return recommendation, not crash
        result = compare_multi_variants(
            strategy_id="test-sid",
            variants=["A", "B", "C", "D"],
            metric="meeting_rate",
            db_session=None,
        )
        assert len(result.pairwise_results) == 6  # C(4,2) = 6 pairs
        for pr in result.pairwise_results:
            assert abs(pr.corrected_threshold - 0.05 / 6) < 1e-6

    def test_winner_not_declared_with_harm_flag(self):
        """Even if statistically significant, don't promote if winner has high unsubscribes."""
        from app.services.multi_variate import compare_multi_variants, _fetch_variant_data
        with patch("app.services.multi_variate._fetch_variant_data") as mock_fetch:
            mock_fetch.return_value = {
                "A": {"sends": 100, "replies": 30, "bookings": 20, "unsubscribes": 0},
                "B": {"sends": 100, "replies": 30, "bookings": 10, "unsubscribes": 0},
                "C": {"sends": 100, "replies": 30, "bookings": 8,  "unsubscribes": 8},  # 8% unsub > 5%
            }
            result = compare_multi_variants(
                strategy_id="test-sid",
                variants=["A", "B", "C"],
                metric="meeting_rate",
                db_session=MagicMock(),
            )
            assert result.harm_flags.get("C") is True

    def test_insufficient_variants_returns_early(self):
        from app.services.multi_variate import compare_multi_variants
        result = compare_multi_variants(
            strategy_id="test-sid",
            variants=["A"],
            metric="meeting_rate",
            db_session=None,
        )
        assert "2 variants" in result.recommendation


# ===========================================================================
# Subject intelligence (4 tests)
# ===========================================================================

class TestSubjectIntelligence:

    def test_classify_question(self):
        from app.services.subject_intelligence import classify_subject
        assert "question" in classify_subject("Are you struggling with lead gen?")

    def test_classify_multiple_patterns(self):
        from app.services.subject_intelligence import classify_subject
        patterns = classify_subject("3 ways {company} can reduce churn?")
        assert "number" in patterns
        assert "personalized_company" in patterns
        assert "question" in patterns

    def test_classify_short(self):
        from app.services.subject_intelligence import classify_subject
        assert "short_under_6_words" in classify_subject("Quick question for you")

    def test_anonymize_removes_template_vars(self):
        from app.services.subject_intelligence import anonymize_subject
        result = anonymize_subject("{company} is falling behind on {topic}")
        assert "{company}" not in result
        assert "[Company]" in result


# ===========================================================================
# Personalization scorer (4 tests)
# ===========================================================================

class TestPersonalizationScorer:

    def test_score_zero_when_no_enrichment(self):
        from app.services.personalization_scorer import score_message
        ps = score_message("Hi there, I noticed you might benefit from our product.", None)
        assert ps.raw_score == 0.0

    def test_score_increases_with_field_usage(self):
        from app.services.personalization_scorer import score_message
        enrichment = {
            "company": "Acme Corp",
            "industry": "Manufacturing",
            "job_title": "Operations Manager",
        }
        body_poor = "Hi, I'd love to connect with you."
        body_good = "Hi, I noticed Acme Corp is in Manufacturing and that Operations Managers often face X."

        ps_poor = score_message(body_poor, enrichment)
        ps_good = score_message(body_good, enrichment)
        assert ps_good.raw_score > ps_poor.raw_score

    def test_pearson_correlation_positive_example(self):
        from app.services.personalization_scorer import _pearson
        xs = [0.1, 0.3, 0.5, 0.7, 0.9]
        ys = [0,   0,   1,   1,   1]
        r = _pearson(xs, ys)
        assert r is not None
        assert r > 0

    def test_build_injection_empty_when_low_corr(self):
        from app.services.personalization_scorer import build_personalization_prompt_injection
        result = build_personalization_prompt_injection(0.1, ["company", "industry"])
        assert result == ""

    def test_build_injection_present_when_high_corr(self):
        from app.services.personalization_scorer import build_personalization_prompt_injection
        result = build_personalization_prompt_injection(0.45, ["company", "industry", "job_title"])
        assert "company" in result
        assert "45%" in result
