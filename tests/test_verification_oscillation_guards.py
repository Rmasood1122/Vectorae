"""SEALED guard tests moved unchanged from tests/test_verification_oscillation.py.

Listed in evals/compliance_testset.yaml: a failing or missing test here
blocks the release gate. Moved by the gate PR in
docs/program/plans/phase5-seal-ai-and-tenancy-guards.md; the test bodies
are byte-identical to the source. Helpers they need are copied here so
the file stands alone.
"""

import pytest
from app.db import models as m
from app.verification.loop import (
    run_verification_loop,
)


DOC_EARLY = (
    "# Strategy\n\n## Pricing\n"
    "Victorae is priced at $2,500-$7,500/month versus $10K-$13K/month "
    "for a fully-loaded SDR.\n\n## Channels\nReferral-led outreach."
)


def _prepare(db, product_with_strategy, document=DOC_EARLY):
    _, strategy = product_with_strategy(m.FlowType.WITH_CLIENTS)
    strategy.strategy_document = document
    db.commit()
    return strategy


class _LaterFixClaude:
    """Judge passes everything except pass `fail_pass` on its first attempt;
    the fixer returns `rewrite`. Counts every model call."""

    def __init__(self, fail_pass: int, rewrite: str):
        self.fail_pass, self.rewrite = fail_pass, rewrite
        self.judge_calls: list[int] = []
        self.fix_calls = 0

    def complete_json(self, system, prompt, cache_prefix=None, **_kw) -> dict:
        import re

        pass_no = int(re.search(r"criterion #(\d+)", prompt, re.I).group(1))
        self.judge_calls.append(pass_no)
        if pass_no == self.fail_pass and self.judge_calls.count(pass_no) == 1:
            # The finding names the figures the rewrite then uses, as a real
            # research-backed finding does; an unsourced figure would trip the
            # factual floor (T31), which is not what these tests are about.
            return {"result": "FAIL",
                    "fix_description": "Add a warm-up ramp of 10 to 40 a day."}
        return {"result": "PASS"}

    def complete(self, system, prompt, cache_prefix=None, **_kw) -> str:
        self.fix_calls += 1
        return self.rewrite


_COMPLIANT_DOC = (
    "# Plan\nEmail with the sender's postal address and a working unsubscribe "
    "link. WhatsApp: approved template only, to opted-in numbers."
)


class TestShipBlockingPassRechecked:
    """A later pass's full-document rewrite must not silently undo what the
    ship-blocking pass #4 (legal & compliance) approved."""

    @pytest.mark.parametrize("fail_pass, rewrite, dropped", [
        (5, "# Plan\nEmail signed by the founder. Warm-up 10 to 40 a day. "
            "WhatsApp: approved template only, to opted-in numbers.",
         "postal address, unsubscribe"),
        (9, "# Plan\nEmail with the sender's postal address and a working "
            "unsubscribe link. WhatsApp: personalised free-form message to "
            "every number found.",
         "WhatsApp approved template, WhatsApp opt-in"),
    ])
    def test_rewrite_dropping_approved_element_fails_closed(
        self, db_session, monkeypatch, product_with_strategy, fail_pass, rewrite, dropped
    ):
        fake = _LaterFixClaude(fail_pass, rewrite)
        monkeypatch.setattr("app.verification.loop.get_client", lambda: fake)
        strategy = _prepare(db_session, product_with_strategy, _COMPLIANT_DOC)

        final = run_verification_loop(db_session, strategy, max_attempts=2)

        assert final is m.StrategyStatus.NEEDS_HUMAN_REVIEW
        p4 = [e for e in strategy.verified_passes_json if e["pass_no"] == 4]
        assert [e["result"] for e in p4] == ["PASS", "FAIL"]
        assert p4[-1]["outcome"] == "compliance_regressed"
        assert p4[-1]["attempt"] == 2 and p4[-1]["fix_applied"] is False
        assert dropped in p4[-1]["fix_description"]
        # Deterministic re-check: no extra model call (11 judges, 1 fix).
        assert len(fake.judge_calls) == 11 and fake.fix_calls == 1

    def test_rewrite_keeping_approved_elements_still_verifies(
        self, db_session, monkeypatch, product_with_strategy
    ):
        fake = _LaterFixClaude(5, _COMPLIANT_DOC + "\nWarm-up 10 to 40 a day.")
        monkeypatch.setattr("app.verification.loop.get_client", lambda: fake)
        strategy = _prepare(db_session, product_with_strategy, _COMPLIANT_DOC)

        assert run_verification_loop(db_session, strategy, max_attempts=2) \
            is m.StrategyStatus.VERIFIED
        assert not any(e.get("outcome") == "compliance_regressed"
                       for e in strategy.verified_passes_json)


class TestFactualPassRechecked:
    """T31: the factual pass (#1) is judged once, first. A later rewrite that
    adds a figure or a customer name nobody sourced must not ship VERIFIED."""

    @pytest.mark.parametrize("addition", [
        "\nTarget: 4.7% reply rate, the median of 12,000 agency campaigns.",
        "\nTrusted by Kestrel Freight and Harbourline Studio.",
    ])
    def test_a_later_rewrite_adding_unsourced_facts_needs_review(
        self, db_session, monkeypatch, product_with_strategy, addition
    ):
        fake = _LaterFixClaude(5, _COMPLIANT_DOC + addition)
        monkeypatch.setattr("app.verification.loop.get_client", lambda: fake)
        strategy = _prepare(db_session, product_with_strategy, _COMPLIANT_DOC)

        assert run_verification_loop(db_session, strategy, max_attempts=2) \
            is m.StrategyStatus.NEEDS_HUMAN_REVIEW
        regressed = [e for e in strategy.verified_passes_json
                     if e.get("outcome") == "facts_regressed"]
        assert len(regressed) == 1 and regressed[0]["key"] == "factual_accuracy"

    def test_list_markers_and_sourced_figures_are_not_facts(self):
        from app.verification.loop import _unsupported_facts

        assert _unsupported_facts("1. Email first\n2. Warm up 10 to 40 a day.",
                                  ["research: warm-up from 10 to 40 a day"]) == []
