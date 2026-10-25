"""Feature 2 — reply intelligence engine.

Pins the four categories and their side effects, the banned-word repair path,
the "human is never bypassed" guarantee, task idempotency, the dispatch from
every channel that creates an InboundReply, and the two API routes.
"""

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from app.db import models as m
from app.services import reply_intelligence
from app.workers import reply_tasks

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


class TestPrompt:
    def test_prompt_carries_everything_the_spec_requires(self, db_session, replied,
                                                         fake_claude):
        reply_intelligence.classify_reply(db_session, replied["reply"].id, today=TODAY)

        prompt = fake_claude.reply_intelligence_prompts[0]
        assert "Interesting - how does this actually work?" in prompt   # reply text
        assert "Sara Khan" in prompt                                    # lead name
        assert "Acme Fire" in prompt                                    # company
        assert "sequence step 2" in prompt                              # step number
        assert "Saw your post about inspection backlogs." in prompt     # original

    def test_system_prompt_names_every_banned_word(self, db_session, replied,
                                                   fake_claude):
        reply_intelligence.classify_reply(db_session, replied["reply"].id, today=TODAY)
        system = fake_claude.reply_intelligence_systems[0]
        for word in reply_intelligence.BANNED_WORDS:
            assert word in system


class TestCategories:
    def test_buying_signal_lines_the_enrollment_up_at_step_three(
            self, db_session, replied, fake_claude):
        fake_claude.reply_intelligence_response = {
            "category": "BUYING_SIGNAL", "confidence": 0.9,
            "next_action": "send_step_3", "draft_response": "Happy to show you."}

        result = reply_intelligence.classify_reply(db_session, replied["reply"].id,
                                                   today=TODAY)

        assert result["category"] == "BUYING_SIGNAL"
        # current_step records the last step SENT, so "next step is 3" is 2.
        db_session.refresh(replied["enrollment"])
        assert replied["enrollment"].current_step == 2

    def test_buying_signal_never_restarts_a_stopped_sequence(
            self, db_session, replied, fake_claude):
        """The guarantee this feature is built around: the human sends, not us."""
        fake_claude.reply_intelligence_response = {
            "category": "BUYING_SIGNAL", "confidence": 0.9,
            "next_action": "send_step_3", "draft_response": "Happy to show you."}

        reply_intelligence.classify_reply(db_session, replied["reply"].id, today=TODAY)

        db_session.refresh(replied["enrollment"])
        assert replied["enrollment"].status is m.EnrollmentStatus.STOPPED
        assert replied["enrollment"].stop_reason == "replied_interested"
        # and nothing new was scheduled
        assert db_session.query(m.Message).filter(
            m.Message.status == m.MessageStatus.SCHEDULED).count() == 0

    def test_not_now_schedules_thirty_days_out(self, db_session, replied, fake_claude):
        fake_claude.reply_intelligence_response = {
            "category": "NOT_NOW", "confidence": 0.7, "next_action": "reschedule",
            "draft_response": "Understood, I will come back in the autumn."}

        result = reply_intelligence.classify_reply(db_session, replied["reply"].id,
                                                   today=TODAY)

        assert result["reschedule_date"] == TODAY + timedelta(days=30)

    @pytest.mark.parametrize("category", ["OBJECTION", "WRONG_PERSON", "BUYING_SIGNAL"])
    def test_only_not_now_gets_a_reschedule_date(self, db_session, replied,
                                                 fake_claude, category):
        fake_claude.reply_intelligence_response = {
            "category": category, "confidence": 0.7, "next_action": "x",
            "draft_response": "A short reply."}
        result = reply_intelligence.classify_reply(db_session, replied["reply"].id,
                                                   today=TODAY)
        assert result["reschedule_date"] is None

    def test_an_unknown_category_degrades_to_objection(self, db_session, replied,
                                                       fake_claude):
        """A category we do not recognise must not become a null row: OBJECTION
        is the safe landing -- it puts the reply in front of a human."""
        fake_claude.reply_intelligence_response = {
            "category": "MAYBE_LATER?", "confidence": 2.5, "next_action": "",
            "draft_response": "A short reply."}

        result = reply_intelligence.classify_reply(db_session, replied["reply"].id,
                                                   today=TODAY)

        assert result["category"] == "OBJECTION"
        assert result["confidence"] == 1.0            # clamped, not rejected
        assert result["next_action"] == "send_objection_response"   # defaulted


class TestBannedWords:
    def test_detects_on_word_boundaries(self):
        assert reply_intelligence.find_banned_words(
            "Our platform is seamless") == ["platform", "seamless"]
        assert reply_intelligence.find_banned_words("pull the lever") == []

    def test_a_banned_draft_is_regenerated_once(self, db_session, replied, fake_claude):
        fake_claude.reply_intelligence_response = [
            {"category": "OBJECTION", "confidence": 0.8, "next_action": "answer",
             "draft_response": "Our platform makes this seamless."},
            {"category": "OBJECTION", "confidence": 0.8, "next_action": "answer",
             "draft_response": "Fair point. Can I send you the short version?"},
        ]

        result = reply_intelligence.classify_reply(db_session, replied["reply"].id,
                                                   today=TODAY)

        assert len(fake_claude.reply_intelligence_prompts) == 2
        assert result["draft_response"] == "Fair point. Can I send you the short version?"
        assert reply_intelligence.find_banned_words(result["draft_response"]) == []

    def test_two_bad_attempts_fall_back_instead_of_shipping_banned_copy(
            self, db_session, replied, fake_claude):
        fake_claude.reply_intelligence_response = {
            "category": "OBJECTION", "confidence": 0.8, "next_action": "answer",
            "draft_response": "Our platform is a game-changer."}

        result = reply_intelligence.classify_reply(db_session, replied["reply"].id,
                                                   today=TODAY)

        assert len(fake_claude.reply_intelligence_prompts) == 2   # one repair, no more
        assert result["draft_response"] == reply_intelligence.FALLBACK_DRAFT["OBJECTION"]
        assert reply_intelligence.find_banned_words(result["draft_response"]) == []

    def test_no_fallback_draft_contains_a_banned_word(self):
        for category, draft in reply_intelligence.FALLBACK_DRAFT.items():
            assert reply_intelligence.find_banned_words(draft) == [], category

    def test_a_long_draft_is_trimmed_to_the_word_limit(self, db_session, replied,
                                                       fake_claude):
        fake_claude.reply_intelligence_response = {
            "category": "OBJECTION", "confidence": 0.8, "next_action": "answer",
            "draft_response": " ".join(["word"] * 400)}

        result = reply_intelligence.classify_reply(db_session, replied["reply"].id,
                                                   today=TODAY)

        assert len(result["draft_response"].split()) <= reply_intelligence.MAX_DRAFT_WORDS


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


class TestNeverRaises:
    def test_a_model_outage_leaves_the_reply_untouched(self, db_session, replied,
                                                       fake_claude):
        fake_claude.reply_intelligence_response = RuntimeError("anthropic is down")

        result = reply_intelligence.classify_reply(db_session, replied["reply"].id,
                                                   today=TODAY)

        assert result["category"] is None
        assert "error" in result
        db_session.refresh(replied["reply"])
        assert replied["reply"].reply_category is None
        # The M3 routing that already happened is untouched.
        assert replied["reply"].classification == "interested"

    def test_unknown_reply_id_degrades(self, db_session):
        result = reply_intelligence.classify_reply(db_session, uuid.uuid4(), today=TODAY)
        assert result["category"] is None and "error" in result

    def test_apply_refuses_to_write_a_failed_result(self, db_session, replied):
        reply = replied["reply"]
        reply.reply_category = "BUYING_SIGNAL"
        db_session.commit()

        reply_intelligence.apply(db_session, reply, {"error": "boom"})

        assert reply.reply_category == "BUYING_SIGNAL"


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


class TestTask:
    def test_classifies_and_persists_every_field(self, db_session, replied, fake_claude):
        fake_claude.reply_intelligence_response = {
            "category": "NOT_NOW", "confidence": 0.66, "next_action": "Reschedule Me",
            "draft_response": "Understood on the timing."}

        result = reply_tasks.process_inbound_reply_impl(db_session, replied["reply"].id)

        # Part 1 Feature 1 added `intent` to the task's result; the FG1 half
        # of the contract is unchanged.
        assert result["status"] == "classified"
        assert result["category"] == "NOT_NOW"
        reply = replied["reply"]
        db_session.refresh(reply)
        assert reply.reply_category == "NOT_NOW"
        assert reply.category_confidence == 0.66
        assert reply.ai_next_action == "reschedule_me"        # normalised
        assert reply.ai_draft_response == "Understood on the timing."
        assert reply.classified_at is not None
        assert reply.reschedule_date is not None

    def test_is_idempotent_and_costs_nothing_on_redelivery(self, db_session, replied,
                                                          fake_claude):
        reply_tasks.process_inbound_reply_impl(db_session, replied["reply"].id)
        calls_after_first = len(fake_claude.reply_intelligence_prompts)

        second = reply_tasks.process_inbound_reply_impl(db_session, replied["reply"].id)

        assert second["status"] == "skipped"
        assert len(fake_claude.reply_intelligence_prompts) == calls_after_first

    def test_force_reclassifies(self, db_session, replied, fake_claude):
        reply_tasks.process_inbound_reply_impl(db_session, replied["reply"].id)
        result = reply_tasks.process_inbound_reply_impl(db_session, replied["reply"].id,
                                                        force=True)
        assert result["status"] == "classified"

    def test_a_missing_reply_is_a_status_not_an_exception(self, db_session):
        assert reply_tasks.process_inbound_reply_impl(
            db_session, uuid.uuid4())["status"] == "not_found"
        assert reply_tasks.process_inbound_reply_impl(
            db_session, "not-a-uuid")["status"] == "not_found"


class TestDispatch:
    def test_the_whatsapp_webhook_dispatches_classification(self, db_session,
                                                            queued_jobs, replied):
        """Every channel that creates an InboundReply must hand it off."""
        from app.api import webhooks_whatsapp

        source = webhooks_whatsapp.__file__
        # gaps-sweep-2 #7: the id is read before routing and the job is
        # published after it, in a `finally` (behaviour tested in
        # tests/test_gaps_sweep_2.py::TestWhatsAppInboundOrdering).
        with open(source, encoding="utf-8") as handle:
            assert "reply_tasks.enqueue(reply_id)" in handle.read()

    def test_the_email_poller_dispatches_classification(self):
        from app.workers import outreach_tasks

        with open(outreach_tasks.__file__, encoding="utf-8") as handle:
            assert "reply_tasks.enqueue(reply_row.id)" in handle.read()

    def test_the_linkedin_webhook_dispatches_classification(self):
        from app.api import linkedin

        # gaps-sweep-2 #7: published after routing, from an id read before it
        # (behaviour tested in tests/test_linkedin_channel.py::TestWebhook).
        with open(linkedin.__file__, encoding="utf-8") as handle:
            assert "reply_tasks.enqueue(reply_id)" in handle.read()


class TestEndpoints:
    def test_get_returns_the_classification(self, client, db_session, replied,
                                            fake_claude):
        reply_tasks.process_inbound_reply_impl(db_session, replied["reply"].id)

        response = client.get(f"/crm/replies/{replied['reply'].id}/intelligence")

        assert response.status_code == 200
        body = response.json()
        assert body["category"] == "BUYING_SIGNAL"
        assert body["draft_response"]
        assert body["reply_id"] == str(replied["reply"].id)

    def test_get_is_honest_about_an_unclassified_reply(self, client, replied):
        body = client.get(
            f"/crm/replies/{replied['reply'].id}/intelligence").json()
        assert body["category"] is None
        assert body["classified_at"] is None

    def test_patch_moves_the_reschedule_date(self, client, db_session, replied,
                                             fake_claude):
        fake_claude.reply_intelligence_response = {
            "category": "NOT_NOW", "confidence": 0.7, "next_action": "reschedule",
            "draft_response": "Understood."}
        reply_tasks.process_inbound_reply_impl(db_session, replied["reply"].id)
        ninety = (datetime.now(timezone.utc).date() + timedelta(days=90)).isoformat()

        response = client.patch(f"/crm/replies/{replied['reply'].id}/intelligence",
                                json={"reschedule_date": ninety})

        assert response.status_code == 200
        assert response.json()["reschedule_date"] == ninety

    def test_patch_rejects_a_date_in_the_past(self, client, replied):
        yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
        response = client.patch(f"/crm/replies/{replied['reply'].id}/intelligence",
                                json={"reschedule_date": yesterday})
        assert response.status_code == 422

    def test_patch_with_an_empty_body_is_422(self, client, replied):
        assert client.patch(f"/crm/replies/{replied['reply'].id}/intelligence",
                            json={}).status_code == 422

    def test_approving_a_draft_queues_it_for_a_human_and_sends_nothing(
            self, client, db_session, replied, fake_claude, queued_jobs):
        reply_tasks.process_inbound_reply_impl(db_session, replied["reply"].id)

        response = client.patch(f"/crm/replies/{replied['reply'].id}/intelligence",
                                json={"approved_draft": True})

        assert response.status_code == 200
        # A note holding the copy, an activity row, and a notification...
        notes = db_session.query(m.CrmNote).filter(
            m.CrmNote.lead_id == replied["lead"].id).all()
        assert len(notes) == 1 and "Approved reply draft" in notes[0].body
        kinds = [a.kind for a in db_session.query(m.CrmActivity).all()]
        assert m.CrmActivityKind.TASK_CREATED in kinds
        assert any(event == "reply_draft_approved"
                   for _user, event, _kwargs in queued_jobs["events"])
        # ...and nothing scheduled to send.
        assert db_session.query(m.Message).filter(
            m.Message.status == m.MessageStatus.SCHEDULED).count() == 0

    def test_approving_with_no_draft_is_409(self, client, replied):
        response = client.patch(f"/crm/replies/{replied['reply'].id}/intelligence",
                                json={"approved_draft": True})
        assert response.status_code == 409

    def test_another_users_reply_is_404(self, client, db_session):
        other = m.User(email="stranger2@example.com", plan=m.PlanTier.PRO)
        db_session.add(other)
        db_session.flush()
        product = m.Product(user_id=other.id, name="theirs", description="x",
                            type=m.ProductType.SKILL)
        db_session.add(product)
        db_session.flush()
        strategy = m.Strategy(product_id=product.id, flow_type=m.FlowType.WITH_CLIENTS)
        db_session.add(strategy)
        db_session.flush()
        lead = m.Lead(strategy_id=strategy.id, source="manual", external_id="y1",
                      email="theirs@example.com")
        db_session.add(lead)
        db_session.flush()
        reply = m.InboundReply(lead_id=lead.id, channel="email",
                               from_address="theirs@example.com", body="hi")
        db_session.add(reply)
        db_session.commit()

        assert client.get(
            f"/crm/replies/{reply.id}/intelligence").status_code == 404

    def test_an_unmatched_reply_belongs_to_nobody(self, client, db_session):
        reply = m.InboundReply(lead_id=None, channel="email",
                               from_address="stranger@nowhere.test", body="who dis")
        db_session.add(reply)
        db_session.commit()

        assert client.get(f"/crm/replies/{reply.id}/intelligence").status_code == 404


class TestClassifierOptOutBackstop:
    """Phase 5 review: the inbound classifier returned the model's label as
    is, and the lead is suppressed only on "unsubscribe_request". A plain
    opt-out the model mislabelled left the lead unsuppressed and the sequence
    running. The written-reply opt-out detector now overrides the label."""

    @pytest.mark.parametrize("body", [
        "Stop emailing me.", "Please remove me from your list.",
        "Unsubscribe", "Do not contact me again.", "Delete my data please."])
    def test_a_plain_opt_out_is_an_unsubscribe_whatever_the_model_said(self, fake_claude,
                                                                        body):
        from app.services.reply_classification import classify_reply

        fake_claude.default_reply_class = "question"
        assert classify_reply("m.okafor@example.test", "Re: hi", body) == "unsubscribe_request"

    def test_a_newsletter_footer_in_the_signature_is_not_an_opt_out(self, fake_claude):
        from app.services.reply_classification import classify_reply

        fake_claude.default_reply_class = "interested"
        body = "Yes, let's talk next week.\n\n--\nTo unsubscribe from our newsletter, click here."
        assert classify_reply("m.okafor@example.test", "Re: hi", body) == "interested"

    def test_a_bounce_that_quotes_our_footer_stays_a_bounce(self, fake_claude):
        from app.services.reply_classification import classify_reply

        fake_claude.default_reply_class = "bounce"
        body = ("Delivery has failed to these recipients.\n\nOriginal message:\n"
                "Hi Sara, worth a chat?\nUnsubscribe: https://app.example.test/u/abc")
        assert classify_reply("Mail Delivery System <MAILER-DAEMON@mx.example.test>",
                              "Undeliverable", body) == "bounce"

    def test_an_ordinary_reply_keeps_the_model_label(self, fake_claude):
        from app.services.reply_classification import classify_reply

        fake_claude.default_reply_class = "interested"
        assert classify_reply("m.okafor@example.test", "Re: hi",
                              "Sounds good, Tuesday works.") == "interested"
