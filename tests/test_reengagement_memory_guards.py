"""SEALED guard tests moved unchanged from tests/test_reengagement_memory.py.

Listed in evals/compliance_testset.yaml: a failing or missing test here
blocks the release gate. Moved by the gate PR in
docs/program/plans/phase5-seal-ai-and-tenancy-guards.md; the test bodies
are byte-identical to the source. Helpers they need are copied here so
the file stands alone.
"""

from __future__ import annotations
from datetime import datetime, timezone
import pytest
from app.db import models as m
from app.services import reengagement_memory as memory


NOW = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)


TODAY = NOW.date()


@pytest.fixture()
def lead(db_session, verified_strategy):
    row = m.Lead(strategy_id=verified_strategy.id, source="apollo", external_id="RM1",
                 full_name="Sara Khan", title="Operations Manager",
                 company="Blaze Safety", email="sara@blaze.test",
                 status=m.LeadStatus.CONTACTED)
    db_session.add(row)
    db_session.commit()
    return row


def _reply(db_session, lead, **kwargs) -> m.InboundReply:
    reply = m.InboundReply(
        lead_id=lead.id, channel="email", from_address=lead.email,
        subject=kwargs.pop("subject", "Re: quick question"),
        body=kwargs.pop("body", "No budget until the new fiscal year — try us then."),
        received_at=NOW, intent_label=kwargs.pop("intent_label", "not_now"), **kwargs)
    db_session.add(reply)
    db_session.commit()
    return reply


class TestExtract:

    def test_a_reason_the_reply_never_gave_is_not_kept(self, db_session, fake_claude, lead):
        """T10: the brief quotes reason_text as their words. A reason that is
        not in the reply is dropped; the plan (kind, date) stands."""
        fake_claude.not_now_response = {"reason_kind": "budget",
                                        "reason_text": "budget frozen until the merger closes",
                                        "return_on": None}
        result = memory.extract(db_session, _reply(db_session, lead, body="Not now."),
                                today=TODAY)
        assert result["reason_kind"] == memory.BUDGET
        assert result["reason_text"] is None
