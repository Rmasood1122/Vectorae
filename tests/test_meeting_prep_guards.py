"""SEALED guard tests moved unchanged from tests/test_meeting_prep.py.

Listed in evals/compliance_testset.yaml: a failing or missing test here
blocks the release gate. Moved by the gate PR in
docs/program/plans/phase5-seal-ai-and-tenancy-guards.md; the test bodies
are byte-identical to the source. Helpers they need are copied here so
the file stands alone.
"""

from __future__ import annotations
import uuid
import pytest
from app.config import settings
from app.db import models as m
from app.services import meeting_prep


@pytest.fixture()
def lead(db_session, verified_strategy):
    row = m.Lead(
        strategy_id=verified_strategy.id, source="apollo", external_id="MP1",
        full_name="Sara Khan", title="Owner", company="Acme Fire",
        email="sara@acme.test", status=m.LeadStatus.REPLIED,
        enrichment_json={"person": {
            "linkedin_url": "https://linkedin.com/in/sarakhan",
            "city": "Austin", "state": "Texas", "country": "United States",
            "organization": {"name": "Acme Fire", "industry": "fire protection",
                             "estimated_num_employees": 30,
                             "latest_funding_stage": "Seed"},
        }},
    )
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture()
def emitted(monkeypatch):
    """Capture event_bus.emit instead of fanning out."""
    from app.services import event_bus

    calls: list[dict] = []

    def _emit(db, user_id, event, **kwargs):
        calls.append({"user_id": user_id, "event": event, **kwargs})
        return {"event": event, "push": 0, "slack": False, "webhooks": 0}

    monkeypatch.setattr(event_bus, "emit", _emit)
    return calls


def _brief(db_session, lead, user, *, start=None, source="manual", ref=None):
    brief = meeting_prep.upsert_brief(
        db_session, lead, user_id=user.id, source=source,
        external_ref=ref or f"manual:{uuid.uuid4()}", meeting_start_at=start,
    )
    db_session.commit()
    return brief


@pytest.fixture(autouse=True)
def _calendly_signing_key(monkeypatch):
    monkeypatch.setattr(settings, "calendly_webhook_signing_key", "fg7-signing-key")


class TestGeneration:

    def test_an_invented_price_and_quote_are_removed(self, db_session, test_user, lead,
                                                     fake_claude, emitted):
        """T17: the seller repeats the brief on the call. A price the records
        never named and a quote the prospect never wrote are removed; the
        grounded rest is kept."""
        db_session.add(m.InboundReply(lead_id=lead.id, from_address=lead.email,
                                      body="Sure, Thursday works.",
                                      classification="interested"))
        db_session.commit()
        fake_claude.meeting_prep_response = {
            "why_they_booked": 'They said "we are switching providers this quarter". '
                               "They replied that Thursday works.",
            "deal_structure": "Offer $4,500 for a year up front.",
            "talking_points": ["Inspections backlog", "Mention the 73% saving"],
            "opening_60_seconds": "Thanks for booking.",
        }
        brief = _brief(db_session, lead, test_user)
        assert meeting_prep.generate_brief(db_session, brief.id) == "ready"
        db_session.refresh(brief)
        sections = brief.sections_json
        assert sections["why_they_booked"] == "They replied that Thursday works."
        assert sections["deal_structure"] == ""
        assert sections["talking_points"] == ["Inspections backlog"]
        assert "4,500" not in brief.content_md and "switching providers" not in brief.content_md


class TestMeetingOutcome:

    def test_an_invented_price_date_or_promise_is_removed_from_the_draft(
            self, client, db_session, lead, fake_claude):
        """T18: the draft reads as the seller's promise. A price, date, free
        extra or guarantee nobody stated is removed; what the notes support
        stays."""
        fake_claude.followup_email_response = {
            "subject": "Next steps",
            "body": "Hi Sara,\n\nThanks for today. I'll send the 2-page proposal by Friday."
                    " The Studio plan is $2,400 a year.\n\nOnboarding is free onboarding and "
                    "we offer a money-back guarantee.\n\nMaren"}
        resp = client.post(f"/leads/{lead.id}/meeting-outcome", json={
            "outcome": "interested", "notes": "Agreed: I send the 2-page proposal by Friday."})
        assert resp.status_code == 201, resp.text
        assert resp.json()["followup_body"] == (
            "Hi Sara,\n\nThanks for today. I'll send the 2-page proposal by Friday.\n\nMaren")
