"""Phase 2 help center -- the golden set (STAR_PROMPT A.4, 2.5).

Offline and deterministic: BM25 over the repository's own articles, and
FakeClaude for the live path. What these prove, and what they do not:

  - recall@6       the retriever puts an answering chunk in front of the
                   model for >= 95% of real questions. Every miss is listed.
  - must-refuse    every must-refuse question ends on the refusal constant or
                   the ticket message through the EXISTING guards, even when
                   the (fake) model writes an answer anyway.
  - mock precision mock mode answers none of the must-refuse questions.

Whether a REAL model refuses those questions is not provable offline; that
is scripts/help_center/live_eval.py (opt-in, real key, never in CI).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.services import help_center, support_chat, support_kb
from scripts.help_center import measure_golden

GOLDEN_PATH = Path(__file__).with_name("golden_questions.yaml")
GOLDEN = yaml.safe_load(GOLDEN_PATH.read_text(encoding="utf-8"))
ANSWERABLE = GOLDEN["answerable"]
MUST_REFUSE = GOLDEN["must_refuse"]

RECALL_TARGET = 0.95


class TestGoldenSetShape:
    def test_size_and_unique_ids(self):
        assert len(ANSWERABLE) >= 60
        assert len(MUST_REFUSE) >= 20
        ids = [i["id"] for i in ANSWERABLE + MUST_REFUSE]
        assert len(ids) == len(set(ids))

    def test_answerable_items_cover_the_required_phrasings(self):
        styles = {i["style"] for i in ANSWERABLE}
        assert {"paraphrase", "typo", "short", "non_native"} <= styles, styles
        for style in ("typo", "short", "non_native"):
            assert sum(1 for i in ANSWERABLE if i["style"] == style) >= 7, style

    def test_must_refuse_items_cover_the_required_categories(self):
        categories = {i["category"] for i in MUST_REFUSE}
        assert {"off_topic", "jailbreak", "other_vendor", "legal_financial",
                "unsupported_promise"} <= categories, categories
        assert {i["expected_path"] for i in MUST_REFUSE} == {"refusal", "ticket"}

    def test_every_expected_chunk_id_exists(self):
        """A heading rename changes a chunk id. This fails with the stale id
        instead of quietly turning into a recall miss."""
        corpus = help_center.get_corpus()
        stale = sorted({cid for i in ANSWERABLE for cid in i["expected"]
                        if cid not in corpus.by_id})
        assert not stale, f"golden_questions.yaml names chunks that no longer exist: {stale}"


class TestRetrievalRecall:
    def test_recall_at_6_meets_the_target_and_every_miss_is_reported(self):
        recall, misses = measure_golden.recall_at_k(GOLDEN, help_center.TOP_K)
        report = "\n".join(
            f"  {m['id']} {m['question']!r}\n    expected {m['expected']}\n    got {m['got']}"
            for m in misses)
        print(f"\nrecall@{help_center.TOP_K} = {recall:.3f} "
              f"({len(ANSWERABLE) - len(misses)}/{len(ANSWERABLE)}); misses:\n{report or '  none'}")
        assert recall >= RECALL_TARGET, (
            f"recall@{help_center.TOP_K} {recall:.3f} < {RECALL_TARGET}; misses:\n{report}")

    def test_retrieve_returns_at_most_six_scored_hits_best_first(self):
        hits = help_center.retrieve("how do I connect gmail")
        assert 0 < len(hits) <= 6
        scores = [h.score for h in hits]
        assert scores == sorted(scores, reverse=True) and scores[-1] > 0

    def test_retrieval_is_deterministic(self):
        first = [(h.chunk.id, h.score) for h in help_center.retrieve("pricing plans")]
        help_center.reset_cache()
        assert [(h.chunk.id, h.score) for h in help_center.retrieve("pricing plans")] == first

    @pytest.mark.parametrize("question", ["", "   ", "?!", "the a of", "\x00"])
    def test_nothing_to_match_returns_nothing(self, question):
        assert help_center.retrieve(question) == []


# --------------------------------------------------------------------------
# Must-refuse through the live path (FakeClaude) and through mock mode
# --------------------------------------------------------------------------


@pytest.fixture
def live(monkeypatch, fake_claude):
    from app.config import settings
    from app.services import anthropic_client
    monkeypatch.setattr(settings, "ai_mode", "live")
    monkeypatch.setattr(anthropic_client, "get_client", lambda: fake_claude)
    return fake_claude


@pytest.fixture
def mock(monkeypatch):
    from app.config import settings
    from app.services import anthropic_client

    def _explode():
        raise AssertionError("mock mode reached the Anthropic client")

    monkeypatch.setattr(settings, "ai_mode", "mock")
    monkeypatch.setattr(anthropic_client, "get_client", _explode)


def _hostile_response(item: dict) -> dict:
    """What a model that classified correctly would return -- but having
    written an answer anyway, which the guards must throw away."""
    leak = f"Sure! Here is a confident answer to: {item['question']} -- $99, guaranteed."
    if item["expected_path"] == "refusal":
        return {"on_topic": False, "confidence": 0.99, "answer": leak, "faq_ids": []}
    return {"on_topic": True, "confidence": 0.2, "answer": leak,
            "faq_ids": ["plans-limits-billing#how-much-do-the-plans-cost"]}


class TestMustRefuseLivePath:
    @pytest.mark.parametrize("item", MUST_REFUSE, ids=[i["id"] for i in MUST_REFUSE])
    def test_ends_in_the_refusal_or_ticket_path(self, live, item):
        live.support_response = _hostile_response(item)
        result = support_chat.answer_question(item["question"])
        if item["expected_path"] == "refusal":
            assert result.text == support_kb.REFUSAL_MESSAGE
            assert result.reason == "off_topic" and result.suggest_ticket is False
        else:
            assert result.text == support_kb.TICKET_SUGGESTION
            assert result.reason == "low_confidence" and result.suggest_ticket is True
        assert result.faq_ids == ()
        assert "$99" not in result.text
        # The question reached the model only inside <question>, escaped.
        prompt = live.support_prompts[-1]
        assert prompt.rstrip().endswith("</question>")
        assert prompt.count("</question>") == 1
        assert prompt.index("<documents>") < prompt.index("<question>")


class TestMustRefuseMockMode:
    def test_mock_mode_answers_none_of_them(self, mock):
        """Precision 100% on must-refuse: every item gets the ticket message,
        never a borrowed paragraph."""
        answered = []
        for item in MUST_REFUSE:
            result = support_chat.answer_question(item["question"])
            if result.reason != "mock_no_match":
                answered.append(f"{item['id']} {item['question']!r} -> {result.faq_ids}")
            else:
                assert result.text == support_kb.MOCK_NO_MATCH_MESSAGE
                assert result.suggest_ticket is True
        assert not answered, "mock mode answered must-refuse items:\n" + "\n".join(answered)


class TestMockModeMeasurement:
    """The table behind help_center.MOCK_MIN_SCORE / MOCK_MIN_HEADING_COVERAGE.

    Recorded as a test so the thresholds cannot be nudged later without the
    consequence showing up here rather than as quietly worse answers.
    Floors, not exact pins: a better corpus may answer more and better.
    """

    def _shipped(self):
        return measure_golden.tally(GOLDEN, lambda q: measure_golden.new_outcome(
            q, help_center.MOCK_MIN_SCORE, help_center.MOCK_MIN_HEADING_COVERAGE))

    def test_shipped_thresholds_reproduce_the_documented_numbers(self):
        result = self._shipped()
        assert result["wrong_refuse"] == 0
        # Re-measured 2026-10-18 on the 151/31 golden set (help_center.py).
        assert result["correct"] >= 57, result
        assert result["wrong_answerable"] <= 5, result

    def test_the_new_corpus_beats_the_old_faq(self):
        """The before/after claim in docs/program/help-center-eval.md."""
        old = measure_golden.tally(GOLDEN, measure_golden.old_outcome)
        new = self._shipped()
        assert new["correct"] > old["correct"]
        assert new["wrong_answer_rate"] < old["wrong_answer_rate"]
        assert old["wrong_refuse"] > 0 == new["wrong_refuse"]

    def test_lowering_the_coverage_floor_would_answer_a_must_refuse_item(self):
        """Why the floor is where it is: lower it and a must-refuse item gets
        a borrowed paragraph. Coverage is IDF-weighted, so it moves with the
        corpus: the privacy-policy question sat at 0.454 when the first floor
        was chosen; since the 2026-10-18 articles the nearest is "How much
        will Vectorae cost next year?" at 0.552. Probed at 0.40, well under
        the shipped 0.60."""
        loose = measure_golden.tally(GOLDEN, lambda q: measure_golden.new_outcome(
            q, help_center.MOCK_MIN_SCORE, 0.40))
        assert loose["wrong_refuse"] >= 1


class TestLiveEvalIsOptIn:
    """scripts/help_center/live_eval.py spends real money: it must refuse to
    run in CI and without a key, before touching the model client."""

    @pytest.fixture(autouse=True)
    def _no_model(self, monkeypatch):
        from app.services import anthropic_client

        def _explode():
            raise AssertionError("live_eval reached the Anthropic client")

        monkeypatch.setattr(anthropic_client, "get_client", _explode)
        # Never read a developer's real .env from a test.
        import dotenv
        monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)

    def test_skips_in_ci(self, monkeypatch):
        from scripts.help_center import live_eval
        monkeypatch.setenv("CI", "true")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-real-looking")
        assert live_eval.main([]) == 0

    def test_exits_2_without_a_key(self, monkeypatch):
        from scripts.help_center import live_eval
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert live_eval.main([]) == 2
