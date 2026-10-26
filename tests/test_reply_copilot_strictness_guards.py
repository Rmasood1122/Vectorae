"""SEALED guard tests moved unchanged from tests/test_reply_copilot_strictness.py.

Listed in evals/compliance_testset.yaml: a failing or missing test here
blocks the release gate. Moved by the gate PR in
docs/program/plans/phase5-seal-ai-and-tenancy-guards.md; the test bodies
are byte-identical to the source. Helpers they need are copied here so
the file stands alone.
"""

from __future__ import annotations
from .test_reply_copilot_review_fixes import (  # noqa: F401  (pytest fixtures)
    _later_reply,
    channel,
    flag_on,
    gmail,
    sends,
    world,
)


class TestClaimsAboutUs:
    """T11: a proof claim about us must be checked somewhere before it can be
    sent. With no approved-claims library the prompt says 'make no claims
    about us', and the draft check now holds it to that."""

    def test_a_count_with_words_before_the_noun_is_a_sender_claim(self):
        from app.services.guardrails import is_sender_claim

        assert is_sender_claim("We run payroll for 300 UK agencies.")
        assert is_sender_claim("We have 40 happy customers.")
        assert not is_sender_claim("Your 3 new studios look busy.")

    def test_without_a_library_a_claim_about_us_is_a_problem(self):
        from app.services.reply_copilot import draft_problems

        body = "We cut payroll errors by 63% for our clients. Grab a slot?"
        problems = draft_problems(body, allowed_urls=set(), forbidden=[],
                                  claims_on_file=False)
        assert any("claim about us" in p for p in problems)
        assert draft_problems(body, allowed_urls=set(), forbidden=[]) == []

    def test_a_plain_answer_is_fine_without_a_library(self):
        from app.services.reply_copilot import draft_problems

        assert draft_problems("Happy to. Does Tuesday at 10 suit?", allowed_urls=set(),
                              forbidden=[], claims_on_file=False) == []
