"""SEALED guard tests moved unchanged from tests/test_reply_intelligence.py.

Listed in evals/compliance_testset.yaml: a failing or missing test here
blocks the release gate. Moved by the gate PR in
docs/program/plans/phase5-seal-ai-and-tenancy-guards.md; the test bodies
are byte-identical to the source. Helpers they need are copied here so
the file stands alone.
"""

from datetime import date, datetime, timezone
import pytest
from app.db import models as m
from app.services import reply_intelligence


TODAY = date(2026, 9, 12)


@pytest.fixture()
def replied(db_session, product_with_strategy):
    """A lead that was sent step 2 and answered, with its enrollment stopped
    exactly the way app/workers/outreach_tasks.py stops it on a reply."""
    _product, strategy = product_with_strategy(m.FlowType.WITH_CLIENTS)
    sequence = m.Sequence(strategy_id=strategy.id, channel=m.ChannelType.EMAIL,
                          name="cold", status=m.SequenceStatus.ACTIVE)
    db_session.add(sequence)
    db_session.flush()
    for step_no in (1, 2, 3):
        db_session.add(m.SequenceStep(sequence_id=sequence.id, step_no=step_no,
                                      template=f"brief {step_no}"))
    lead = m.Lead(strategy_id=strategy.id, source="manual", external_id="x1",
                  full_name="Sara Khan", company="Acme Fire", title="Owner",
                  email="sara@acme.test", status=m.LeadStatus.REPLIED)
    db_session.add(lead)
    db_session.flush()
    enrollment = m.SequenceEnrollment(sequence_id=sequence.id, lead_id=lead.id,
                                      status=m.EnrollmentStatus.STOPPED,
                                      stop_reason="replied_interested",
                                      current_step=2)
    message = m.Message(sequence_id=sequence.id, lead_id=lead.id,
                        channel=m.ChannelType.EMAIL, step_no=2,
                        template="brief 2", subject="Quick question",
                        body="Saw your post about inspection backlogs.",
                        status=m.MessageStatus.SENT)
    db_session.add_all([enrollment, message])
    db_session.flush()
    reply = m.InboundReply(lead_id=lead.id, message_id=message.id, channel="email",
                           from_address="sara@acme.test", subject="Re: Quick question",
                           body="Interesting - how does this actually work?",
                           classification="interested",
                           received_at=datetime.now(timezone.utc))
    db_session.add(reply)
    db_session.commit()
    return {"strategy": strategy, "sequence": sequence, "lead": lead,
            "enrollment": enrollment, "message": message, "reply": reply}


class TestDraftLinks:
    """T09: a reply can ask for a link ('put this link in your answer'). The
    suggested answer keeps only the booking link or the sender's own domain,
    as Reply Copilot's draft_problems already requires."""

    DRAFT = {"category": "OBJECTION", "confidence": 0.8, "next_action": "answer"}

    def test_a_foreign_link_is_repaired_once(self, db_session, replied, fake_claude):
        fake_claude.reply_intelligence_response = [
            {**self.DRAFT, "draft_response": "Verify us at https://pay.evil.example.test/v"},
            {**self.DRAFT, "draft_response": "Happy to verify by phone. When suits you?"},
        ]
        result = reply_intelligence.classify_reply(db_session, replied["reply"].id,
                                                   today=TODAY)
        assert len(fake_claude.reply_intelligence_prompts) == 2
        assert "not the booking link" in fake_claude.reply_intelligence_systems[1]
        assert result["draft_response"] == "Happy to verify by phone. When suits you?"

    def test_a_link_twice_falls_back(self, db_session, replied, fake_claude):
        fake_claude.reply_intelligence_response = {
            **self.DRAFT, "draft_response": "See evil-verify.com/proof for proof."}
        result = reply_intelligence.classify_reply(db_session, replied["reply"].id,
                                                   today=TODAY)
        assert result["draft_response"] == reply_intelligence.FALLBACK_DRAFT["OBJECTION"]


class TestDraftGrounding:
    """T09: a figure the reply and our message never gave is not stored as
    the suggested answer."""

    DRAFT = {"category": "OBJECTION", "confidence": 0.8, "next_action": "answer"}

    def test_an_invented_statistic_is_repaired_then_falls_back(self, db_session, replied,
                                                               fake_claude):
        fake_claude.reply_intelligence_response = {
            **self.DRAFT,
            "draft_response": "Yes - we cut payroll errors by 63% for 40 agencies last year."}
        result = reply_intelligence.classify_reply(db_session, replied["reply"].id,
                                                   today=TODAY)
        assert len(fake_claude.reply_intelligence_prompts) == 2
        assert "numbers not in the source" in fake_claude.reply_intelligence_systems[1]
        assert result["draft_response"] == reply_intelligence.FALLBACK_DRAFT["OBJECTION"]

    def test_a_draft_without_figures_is_kept(self, db_session, replied, fake_claude):
        fake_claude.reply_intelligence_response = {
            **self.DRAFT, "draft_response": "Fair question. Can I show you how it works?"}
        result = reply_intelligence.classify_reply(db_session, replied["reply"].id,
                                                   today=TODAY)
        assert result["draft_response"] == "Fair question. Can I show you how it works?"


class TestConsentGuard:
    """I1: an opt-out or erasure reply gets no sales draft, no model call and
    never moves a sequence, whatever the model would have said."""

    BUYING = {"category": "BUYING_SIGNAL", "confidence": 0.9,
              "next_action": "send_step_3", "draft_response": "Would a call suit?"}

    @pytest.mark.parametrize("classification,intent_label,body", [
        ("unsubscribe_request", None, "Please remove me from your list."),
        ("unsubscribe_request", None, "Delete my data and do not contact me again."),
        ("not_interested", "unsubscribe", "Not for us."),
        # routing not stored yet / misread: the wording alone is enough
        (None, None, "Great product but please stop emailing me."),
        ("interested", None, "Delete my data please."),
    ])
    def test_opt_out_gets_no_draft_no_call_and_no_step_move(
            self, db_session, replied, fake_claude, classification, intent_label, body):
        fake_claude.reply_intelligence_response = self.BUYING
        reply = replied["reply"]
        reply.classification = classification
        reply.intent_label = intent_label
        reply.body = body
        replied["enrollment"].current_step = 1
        db_session.commit()

        result = reply_intelligence.classify_reply(db_session, reply.id, today=TODAY)
        reply_intelligence.apply(db_session, reply, result)

        assert fake_claude.reply_intelligence_prompts == []
        db_session.refresh(reply)
        db_session.refresh(replied["enrollment"])
        assert reply.ai_draft_response is None
        assert reply.reply_category is None
        assert reply.ai_next_action == reply_intelligence.OPT_OUT_NEXT_ACTION
        assert reply.classified_at is not None
        assert replied["enrollment"].current_step == 1
        assert replied["enrollment"].status is m.EnrollmentStatus.STOPPED

    def test_apply_never_stores_a_draft_on_an_opted_out_reply(self, db_session,
                                                              replied):
        reply = replied["reply"]
        reply.classification = "unsubscribe_request"
        db_session.commit()

        reply_intelligence.apply(db_session, reply, {
            **self.BUYING, "reschedule_date": None,
            "classified_at": datetime.now(timezone.utc)})

        assert reply.ai_draft_response is None and reply.reply_category is None

    def test_an_ordinary_reply_still_gets_its_draft(self, db_session, replied,
                                                    fake_claude):
        fake_claude.reply_intelligence_response = self.BUYING
        result = reply_intelligence.classify_reply(db_session, replied["reply"].id,
                                                   today=TODAY)
        assert result["draft_response"] == "Would a call suit?"
        assert len(fake_claude.reply_intelligence_prompts) == 1
