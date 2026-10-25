"""Product defect #28 -- LEGAL GUARD (opt-out instruction), plus a false error.

1. WhatsApp template validation did not require opt-out language: the editor
   only hinted "Include 'Reply STOP to opt out.'". A MARKETING template must now
   carry a real opt-out INSTRUCTION, not merely the word STOP:
       (reply|text|send|respond|message) ... STOP     (verb case-insensitive)
       STOP ... (opt out|opt-out|optout|unsubscribe)
   within 25 characters and the same sentence, with STOP in capitals (the
   keyword the inbound webhook acts on, app/api/webhooks_whatsapp.py). UTILITY
   templates are transactional and are not required to carry it.
   Checked on create, on edit, right before submission to Meta, and again at
   SEND time: an approved marketing template without the instruction (e.g. one
   approved before this rule) fails closed into NEEDS_TEMPLATE.
   Basis (needs founder/legal confirmation): WhatsApp Business Messaging
   Policy (honour opt-out requests); CASL s.6(2)(c) and s.11 (an unsubscribe
   mechanism in every commercial electronic message to Canadian recipients);
   GDPR Art. 21(2)-(3) (right to object to direct marketing).

2. record_opt_in told a suppressed contact's sender that "only the contact can
   re-initiate (e.g. by messaging you first)". No code path lifts a
   suppression, so that was false; the message now says so.
"""

import pytest

from app.db import models as m
from app.services import whatsapp_optin as optin_svc
from app.services import whatsapp_templates as svc
from app.workers.outreach_tasks import send_message_impl
from tests.conftest import NOW

MARKETING = m.WhatsAppTemplateCategory.MARKETING
UTILITY = m.WhatsAppTemplateCategory.UTILITY


def _draft(db, body, name="optout_t", category=MARKETING):
    return svc.create_draft(db, name=name, language="en_US", category=category,
                            body=body, variable_descriptions={"1": "first name"})


class TestOptOutInstructionRequired:
    @pytest.mark.parametrize("body", [
        "Hi {{1}}, Sam from LeadPilot about your audit.",
        "Hi {{1}}, don't stop now, Sam from LeadPilot.",          # lower-case stop
        "Hi {{1}}, Sam from LeadPilot. Reply NOSTOP.",
        "Hi {{1}}, Sam from LeadPilot has NON-STOP deals for you.",  # bare STOP
        "Hi {{1}}, Sam from LeadPilot, meet us at the BUS STOP.",
        "Hi {{1}}, Sam from LeadPilot: see https://leadpilot.example/STOP",
        "Hi {{1}}, Sam from LeadPilot. STOP.",
        "Hi {{1}}. Reply to Sam from LeadPilot about the audit and the price. STOP",
    ])
    def test_marketing_refuses_a_body_without_an_instruction(self, db_session, body):
        with pytest.raises(svc.TemplateError, match="opt out"):
            _draft(db_session, body)
        assert db_session.query(m.WhatsAppTemplate).count() == 0

    @pytest.mark.parametrize("body", [
        "Hi {{1}}, Sam from LeadPilot. Reply STOP to opt out.",
        "Hi {{1}}, Sam from LeadPilot. reply STOP to unsubscribe",
        "Hi {{1}}. Sam, LeadPilot. STOP to unsubscribe",
        "Hi {{1}}. Sam, LeadPilot. Text STOP anytime.",
        "Hi {{1}}. Sam, LeadPilot. Send STOP and we will not message you again",
    ])
    def test_marketing_accepts_a_real_instruction(self, db_session, body):
        assert _draft(db_session, body).status is m.WhatsAppTemplateStatus.DRAFT

    def test_utility_templates_do_not_need_it(self, db_session):
        t = _draft(db_session, "Hi {{1}}, your meeting with Sam is confirmed.",
                   category=UTILITY)
        assert t.status is m.WhatsAppTemplateStatus.DRAFT

    def test_edit_cannot_remove_the_instruction(self, db_session):
        t = _draft(db_session, "Hi {{1}}, Sam from LeadPilot. Reply STOP to opt out.")
        with pytest.raises(svc.TemplateError, match="opt out"):
            svc.update_template(db_session, t, body="Hi {{1}}, Sam from LeadPilot.")
        db_session.refresh(t)
        assert "STOP" in t.body

    def test_switching_a_utility_template_to_marketing_needs_it(self, db_session):
        t = _draft(db_session, "Hi {{1}}, your meeting with Sam is confirmed.",
                   category=UTILITY)
        with pytest.raises(svc.TemplateError, match="opt out"):
            svc.update_template(db_session, t, category=MARKETING)

    def test_a_legacy_row_without_it_cannot_be_submitted(self, db_session):
        row = m.WhatsAppTemplate(name="legacy_t", language="en_US", version=1,
                                 category=MARKETING,
                                 status=m.WhatsAppTemplateStatus.DRAFT,
                                 body="Hi {{1}}, Sam from LeadPilot.",
                                 variable_descriptions_json={"1": "first name"})
        db_session.add(row)
        db_session.commit()

        class _Adapter:
            submitted = False

            def submit_template(self, **kw):
                _Adapter.submitted = True
                return {"id": "x"}

        with pytest.raises(svc.TemplateError, match="opt out"):
            svc.submit_template(db_session, row, _Adapter())
        assert _Adapter.submitted is False
        assert row.status is m.WhatsAppTemplateStatus.DRAFT

    def test_api_returns_422_with_the_reason(self, client, db_session, test_user):
        # Templates are deployment-wide, so managing them is an admin task
        # (F-SEC-05); the caller here is an admin so the opt-out rule is what
        # answers.
        test_user.is_admin = True
        db_session.commit()
        r = client.post("/whatsapp/templates", json={
            "name": "api_t", "language": "en_US", "category": "marketing",
            "body": "Hi {{1}}, Sam from LeadPilot.",
            "variable_descriptions": {"1": "first name"}})
        assert r.status_code == 422
        assert "STOP" in r.json()["detail"]


class TestCheckedAtSendTime:
    def _schedule(self, db, sequence, lead):
        from tests.test_whatsapp_engine import _schedule_step
        msg, _ = _schedule_step(db, sequence, lead, 2)
        return msg

    def test_legacy_approved_marketing_template_is_blocked_at_send(
            self, db_session, fake_claude, multichannel_sequence, wa_lead,
            approved_template, fake_channel):
        approved_template.body = "Hi {{1}}, Sam from LeadPilot about {{2}}."
        db_session.commit()
        msg = self._schedule(db_session, multichannel_sequence, wa_lead)
        result = send_message_impl(db_session, msg.id, channel=fake_channel, now=NOW)
        db_session.refresh(msg)
        assert result == "needs_template_no_opt_out"
        assert msg.status is m.MessageStatus.NEEDS_TEMPLATE
        assert "opt-out instruction" in msg.error
        assert fake_channel.sent == []

    def test_utility_template_without_it_still_sends(
            self, db_session, fake_claude, multichannel_sequence, wa_lead,
            approved_template, fake_channel):
        approved_template.body = "Hi {{1}}, Sam from LeadPilot about {{2}}."
        approved_template.category = UTILITY
        db_session.commit()
        msg = self._schedule(db_session, multichannel_sequence, wa_lead)
        result = send_message_impl(db_session, msg.id, channel=fake_channel, now=NOW)
        assert result != "needs_template_no_opt_out"


class TestGeneratedDrafts:
    def test_a_candidate_without_it_gets_the_standard_instruction(self, db_session,
                                                                verified_strategy,
                                                                fake_claude, monkeypatch):
        class _Client:
            def complete_json(self, **kw):
                assert "Reply STOP to opt out." in kw["system"]
                return {"templates": [{
                    "name": "gen_a", "language": "en_US", "category": "marketing",
                    "body": "Hi {{1}}, Sam from LeadPilot about {{2}}.",
                    "variable_descriptions": {"1": "first name", "2": "pain"}}]}

        monkeypatch.setattr(svc, "get_client", lambda: _Client())
        (row,) = svc.generate_drafts(db_session, verified_strategy)
        assert row.body.endswith("Reply STOP to opt out.")
        assert svc.has_opt_out_instruction(row.body)


def test_suppressed_contact_error_does_not_promise_a_re_opt_in(db_session, wa_lead_no_optin):
    lead = wa_lead_no_optin
    db_session.add(m.SuppressionEntry(phone=lead.phone, reason="opted_out"))
    db_session.commit()
    with pytest.raises(optin_svc.OptInError) as exc:
        optin_svc.record_opt_in(db_session, lead, source=m.OptInSource.API,
                                evidence="form")
    text = str(exc.value)
    assert "suppression list" in text
    assert "re-initiate" not in text and "messaging you first" not in text
    assert "cannot be lifted" in text
