"""SEALED guard tests moved unchanged from tests/test_voice_twin.py.

Listed in evals/compliance_testset.yaml: a failing or missing test here
blocks the release gate. Moved by the gate PR in
docs/program/plans/phase5-seal-ai-and-tenancy-guards.md; the test bodies
are byte-identical to the source. Helpers they need are copied here so
the file stands alone.
"""

from __future__ import annotations
import pytest


class TestHumanClaimWithAName:
    """'I'm Priya, a real person': a name in apposition must not hide the
    claim. Kept out of the sealed guard file; a false positive pauses the
    whole account, so an honest line with a comma is checked too."""

    @pytest.mark.parametrize("line", [
        "I'm Priya, a real person from the team.", "I am Priya Shah, a real human.",
    ])
    def test_the_claim_is_caught(self, line):
        from app.services import voice_twin

        assert "claims_real_person" in voice_twin.human_claim_labels(line, ("Rehan",)), line

    def test_an_apology_is_not_a_claim(self):
        from app.services import voice_twin

        line = "I'm sorry, a real person will call you back about that."
        assert voice_twin.human_claim_labels(line, ("Rehan",)) == []
