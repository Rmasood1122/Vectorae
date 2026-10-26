"""SEALED guard tests moved unchanged from tests/test_meeting_recording.py.

Listed in evals/compliance_testset.yaml: a failing or missing test here
blocks the release gate. Moved by the gate PR in
docs/program/plans/phase5-seal-ai-and-tenancy-guards.md; the test bodies
are byte-identical to the source. Helpers they need are copied here so
the file stands alone.
"""

from __future__ import annotations
import pytest


@pytest.fixture(autouse=True)
def queued(monkeypatch):
    """No broker in the root suite: record what would have been queued."""
    from app.workers import calendar_tasks

    jobs = {"summary": [], "transcript": []}
    monkeypatch.setattr(calendar_tasks.generate_meeting_summary, "delay",
                        lambda mid: jobs["summary"].append(mid), raising=False)
    monkeypatch.setattr(calendar_tasks.fetch_meeting_transcript, "delay",
                        lambda mid, tid=None: jobs["transcript"].append((mid, tid)),
                        raising=False)
    return jobs


class TestActionItemsGrounded:
    """T19: our action items become CRM tasks. An item stating a figure
    nobody said is dropped; a due date nobody gave is cleared."""

    def _summarise(self, monkeypatch, transcript, items):
        from app.services import meeting_ai

        class _Stub:
            def complete_json(self, **kw):
                return {"summary": "Call.", "action_items": items}

        monkeypatch.setattr(meeting_ai, "get_client", lambda: _Stub())
        return meeting_ai.generate_meeting_summary(transcript, None)["action_items"]

    def test_an_invented_amount_drops_the_item(self, monkeypatch):
        items = self._summarise(monkeypatch, "Dana: Send me something to read.\nSeller: Will do.",
                                [{"text": "Send pricing proposal for $3,000", "owner": "us"},
                                 {"text": "Send reading material", "owner": "us"}])
        assert [i["text"] for i in items] == ["Send reading material"]

    def test_an_invented_due_date_is_cleared_and_a_stated_one_kept(self, monkeypatch):
        items = self._summarise(
            monkeypatch, "Seller: I'll send the contract, and the quote by 2026-10-23.",
            [{"text": "Send the contract", "owner": "us", "due": "2026-11-30"},
             {"text": "Send the quote", "owner": "us", "due": "2026-10-23"}])
        assert [(i["text"], i["due"]) for i in items] == [
            ("Send the contract", None), ("Send the quote", "2026-10-23")]

    def test_a_due_date_on_the_weekday_they_named_is_kept(self, monkeypatch):
        """'by Friday' resolved to a Friday's date is a stated deadline."""
        items = self._summarise(
            monkeypatch, "Seller: I'll send the contract by Friday.",
            [{"text": "Send the contract", "owner": "us", "due": "2026-10-23"},
             {"text": "Call back", "owner": "us", "due": "2026-10-26"}])
        assert [(i["text"], i["due"]) for i in items] == [
            ("Send the contract", "2026-10-23"), ("Call back", None)]
