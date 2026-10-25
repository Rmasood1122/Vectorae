"""SEALED guard tests moved unchanged from tests/test_phone_calls.py.

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
from app.services import phone_calls
from .conftest import entitle_owner


UTC = timezone.utc


@pytest.fixture()
def phone_lead(db_session, verified_strategy):
    lead = m.Lead(strategy_id=verified_strategy.id, source="apollo", external_id="PH1",
                  full_name="Sara Khan", company="Acme Fire", phone="+1 (555) 010-2000",
                  status=m.LeadStatus.VERIFIED, phone_consent_at=datetime.now(UTC),
                  phone_consent_source="web form")
    db_session.add(lead)
    db_session.commit()
    return lead


@pytest.fixture()
def phone_sequence(db_session, verified_strategy):
    # Scale is the cheapest plan with the phone channel; the send path re-checks
    # the plan (app/services/plan_holds.py). This file is about calls, not plans.
    entitle_owner(db_session, verified_strategy, m.PlanTier.SCALE)
    seq = m.Sequence(strategy_id=verified_strategy.id, channel=m.ChannelType.PHONE,
                     name="Calls", status=m.SequenceStatus.ACTIVE)
    db_session.add(seq)
    db_session.flush()
    db_session.add(m.SequenceStep(sequence_id=seq.id, step_no=1, template="intro call",
                                  delay_days=0))
    db_session.add(m.SequenceStep(sequence_id=seq.id, step_no=2, template="second call",
                                  delay_days=2))
    db_session.commit()
    return seq


@pytest.fixture()
def placed_call(db_session, phone_lead, phone_sequence, test_user):
    enrollment = m.SequenceEnrollment(sequence_id=phone_sequence.id, lead_id=phone_lead.id)
    db_session.add(enrollment)
    call = m.Call(lead_id=phone_lead.id, strategy_id=phone_lead.strategy_id, user_id=test_user.id,
                  provider="vapi", provider_call_id="vc-1", to_number="+15550102000",
                  status="ringing", voicemail_text="Hi Sara, AI assistant here.")
    db_session.add(call)
    db_session.commit()
    return call


class TestAnalysis:

    def test_outbound_spoken_stop_is_recorded_once_without_the_model(self, db_session,
                                                                     placed_call, fake_claude):
        """I1 / T15: on an OUTBOUND call a spoken "stop calling me" is recorded
        by the deterministic check, from the callee's turns only, when the
        model is down -- through the webhook's post-call check and every
        Celery retry -- and the unsubscribe is written exactly once."""
        from app.services import voice_twin
        from app.workers.lead_tasks import is_suppressed

        fake_claude.call_analysis_response = RuntimeError("simulated gateway outage")
        placed_call.transcript = ("AI: Hi Sara, this is an AI assistant.\n"
                                  "User: Don't call me again.")
        db_session.commit()
        assert voice_twin.post_call_check(db_session, placed_call)["stop"] is True
        for _attempt in range(3):  # analyze_call: max_retries=2, then it gives up
            with pytest.raises(RuntimeError):
                phone_calls.analyze(db_session, placed_call)
            db_session.rollback()
        assert is_suppressed(db_session, phone="+1 (555) 010-2000")
        lead = db_session.get(m.Lead, placed_call.lead_id)
        assert db_session.query(m.Outcome).filter_by(
            lead_id=lead.id, event=m.OutcomeEvent.UNSUBSCRIBED).count() == 1
        enrollment = db_session.query(m.SequenceEnrollment).filter_by(lead_id=lead.id).one()
        assert enrollment.status is m.EnrollmentStatus.STOPPED

    def test_stop_seen_by_check_and_model_is_unsubscribed_once(self, db_session, placed_call,
                                                               fake_claude):
        from app.services import voice_twin

        fake_claude.call_analysis_response = {"outcome": "not_interested", "stop_request": True}
        placed_call.transcript = "AI: Hi Sara.\nUser: Please stop calling me."
        db_session.commit()
        voice_twin.post_call_check(db_session, placed_call)
        phone_calls.analyze(db_session, placed_call)
        phone_calls.analyze(db_session, placed_call)  # a redelivered task
        assert db_session.query(m.Outcome).filter_by(
            lead_id=placed_call.lead_id, event=m.OutcomeEvent.UNSUBSCRIBED).count() == 1
        assert db_session.query(m.ConsentEvent).filter_by(
            lead_id=placed_call.lead_id, kind="withdrawn").count() == 1

    @pytest.mark.parametrize("said", [
        "Do not contact me again.", "Stop contacting me.", "Never call here again.",
        "I want to opt out.", "Not interested, don't call back.", "Delete my data.",
        "Leave me alone.", "Please don't phone me.", "Don’t ring me again."])
    def test_common_stop_wordings_are_recorded_when_the_model_is_down(
            self, db_session, placed_call, fake_claude, said):
        """The pre-model check is the only stop handling when analysis fails.
        It used to know only "stop calling" / "don't call me" style phrases,
        so "Do not contact me again" left the number callable."""
        from app.services import voice_twin
        from app.workers.lead_tasks import is_suppressed

        fake_claude.call_analysis_response = RuntimeError("simulated gateway outage")
        placed_call.transcript = f"AI: Hi Sara, this is an AI assistant.\nUser: {said}"
        db_session.commit()
        assert voice_twin.post_call_check(db_session, placed_call)["stop"] is True
        assert is_suppressed(db_session, phone="+1 (555) 010-2000")

    @pytest.mark.parametrize("said", [
        "Sounds good, call me Tuesday.", "I can't stop thinking about month-end.",
        "Don't worry, call me later this week."])
    def test_ordinary_replies_are_not_a_stop(self, said):
        from app.services import voice_twin

        assert voice_twin.caller_asked_to_stop([said]) is False

    def test_outbound_stop_the_model_missed_is_still_recorded(self, db_session, placed_call,
                                                              fake_claude):
        from app.workers.lead_tasks import is_suppressed

        fake_claude.call_analysis_response = {"outcome": "not_interested",
                                              "stop_request": False}
        placed_call.transcript = "AI: Hi Sara.\nUser: Stop calling me. Remove my number."
        db_session.commit()
        assert phone_calls.analyze(db_session, placed_call)["stop_request"] is True
        assert is_suppressed(db_session, phone="+1 (555) 010-2000")

    def test_assistant_saying_it_wont_call_again_is_not_a_stop(self, db_session, placed_call,
                                                               fake_claude):
        from app.services import voice_twin
        from app.workers.lead_tasks import is_suppressed

        fake_claude.call_analysis_response = {"outcome": "answered", "stop_request": False}
        placed_call.transcript = ("AI: No problem, I won't call again unless you ask. "
                                  "Don't call me back is fine too.\n"
                                  "User: Actually, call me next week.")
        db_session.commit()
        assert voice_twin.post_call_check(db_session, placed_call)["stop"] is False
        phone_calls.analyze(db_session, placed_call)
        assert not is_suppressed(db_session, phone="+1 (555) 010-2000")
