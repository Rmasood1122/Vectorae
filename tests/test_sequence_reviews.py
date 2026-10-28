"""Feature A7 — the pre-send adversarial review.

Pins: clean content launches; each blocking rule (spam, deceptive subject,
dropping the unsubscribe, unverifiable claims, placeholder sender identity in
production) stops enrollment AND manager approval with 409; warnings never
block; model findings are warnings only and a model failure never blocks; an
override needs a reason and a manager, is logged, and stops applying when the
content changes.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db import models as m
from app.services import adversarial_review as ar
from app.services import system_settings


def _sequence(db_session, strategy, *briefs, channel=m.ChannelType.EMAIL):
    seq = m.Sequence(strategy_id=strategy.id, channel=channel, name="Review me",
                     status=m.SequenceStatus.DRAFT)
    db_session.add(seq)
    db_session.flush()
    for i, brief in enumerate(briefs, start=1):
        db_session.add(m.SequenceStep(sequence_id=seq.id, step_no=i, template=brief,
                                      delay_days=0 if i == 1 else 3))
    db_session.commit()
    db_session.refresh(seq)
    return seq


def _enroll(client, seq):
    return client.post(f"/sequences/{seq.id}/enroll", json={})


def _codes(review):
    return {f["code"] for f in review.findings_json}


class TestGate:
    def test_clean_content_launches(self, client, db_session, verified_strategy, verified_leads,
                                    gmail_account):
        seq = _sequence(db_session, verified_strategy,
                        "Open with one question about how they schedule inspections.",
                        "Share a two-line example of a similar team and ask for 15 minutes.")
        resp = _enroll(client, seq)
        assert resp.status_code == 202, resp.text
        review = ar.latest(db_session, seq)
        assert review.status == "passed" and review.blocking_count == 0

    @pytest.mark.parametrize("brief,code", [
        ("Tell them results are GUARANTEED and to act now.", "spam:guaranteed"),
        ("Subject: RE: our call last week. Then pitch the audit.", "compliance:deceptive_subject"),
        ("Keep it short and don't include the unsubscribe line.", "compliance:drop_unsubscribe"),
        ("Congratulate them on their Series B and pitch.", "claim:funding"),
    ])
    def test_blocking_findings_stop_enrollment(self, client, db_session, verified_strategy,
                                               verified_leads, brief, code):
        seq = _sequence(db_session, verified_strategy, brief)
        resp = _enroll(client, seq)
        assert resp.status_code == 409 and resp.json()["detail"] == ar.REVIEW_BLOCKED
        review = client.get(f"/sequences/{seq.id}/review").json()
        assert review["status"] == "blocked" and review["is_current"] is True
        finding = next(f for f in review["findings"] if f["code"] == code)
        assert finding["severity"] == "block" and finding["step_no"] == 1
        assert db_session.execute(select(m.SequenceEnrollment)).first() is None

    def test_warnings_do_not_block(self, client, db_session, verified_strategy, verified_leads,
                                   gmail_account):
        seq = _sequence(db_session, verified_strategy,
                        "Limited time: mention the free trial, keep it URGENT!!! VERY SHORT PLEASE")
        assert _enroll(client, seq).status_code == 202
        review = ar.latest(db_session, seq)
        assert review.status == "passed" and review.warning_count >= 3
        assert {"spam:limited time", "tone:exclamations", "tone:all_caps"} <= _codes(review)

    def test_approval_is_gated_too(self, client, db_session, verified_strategy, verified_leads):
        seq = _sequence(db_session, verified_strategy, "Act now — tell them it's risk-free.")
        seq.status = m.SequenceStatus.PENDING_APPROVAL
        seq.approval_payload_json = {"lead_statuses": ["verified"]}
        db_session.commit()
        assert client.post(f"/sequences/{seq.id}/approve").status_code == 409

    def test_the_gate_can_be_switched_off(self, client, db_session, verified_strategy,
                                          verified_leads, gmail_account):
        system_settings.set(db_session, "sequence_review_enabled", False)
        seq = _sequence(db_session, verified_strategy, "Guaranteed results, act now.")
        assert _enroll(client, seq).status_code == 202


class TestOverride:
    def test_a_manager_override_with_a_reason_unblocks_and_is_logged(
            self, client, db_session, verified_strategy, verified_leads, gmail_account, test_user):
        seq = _sequence(db_session, verified_strategy, "Congratulate them on their recent award.")
        assert _enroll(client, seq).status_code == 409
        too_short = client.post(f"/sequences/{seq.id}/review/override", json={"reason": "ok"})
        assert too_short.status_code == 422
        resp = client.post(f"/sequences/{seq.id}/review/override",
                           json={"reason": "The award is in their enrichment data (verified)."})
        assert resp.status_code == 200 and resp.json()["status"] == "overridden"
        assert _enroll(client, seq).status_code == 202
        event = db_session.execute(select(m.AccountSecurityEvent).where(
            m.AccountSecurityEvent.event == "sequence_review_override")).scalar_one()
        assert event.user_id == test_user.id
        assert "claim:news" in event.details_json["blocking_findings"]

    def test_editing_the_content_after_an_override_brings_the_gate_back(
            self, client, db_session, verified_strategy, verified_leads):
        seq = _sequence(db_session, verified_strategy, "Congratulate them on their recent award.")
        _enroll(client, seq)
        client.post(f"/sequences/{seq.id}/review/override",
                    json={"reason": "Verified against enrichment before launch."})
        step = seq.steps[0]
        step.template = "Congratulate them on their acquisition of a rival."
        db_session.commit()
        db_session.refresh(seq)
        assert _enroll(client, seq).status_code == 409
        assert client.get(f"/sequences/{seq.id}/review").json()["status"] == "blocked"

    def test_an_override_needs_a_current_blocked_review(self, client, db_session,
                                                        verified_strategy):
        seq = _sequence(db_session, verified_strategy, "A friendly question about scheduling.")
        client.post(f"/sequences/{seq.id}/review")
        resp = client.post(f"/sequences/{seq.id}/review/override",
                           json={"reason": "Nothing to override here at all."})
        assert resp.status_code == 409


class TestRules:
    def test_placeholder_sender_identity_blocks_only_in_production(self, db_session,
                                                                   verified_strategy, monkeypatch):
        from app.config import settings

        seq = _sequence(db_session, verified_strategy, "A friendly question.")
        monkeypatch.setattr(settings, "sender_identity", "Vectorae User, 123 Main St, City, Country")
        dev = next(f for f in ar.rule_findings(db_session, seq)
                   if f["code"] == "compliance:sender_identity")
        assert dev["severity"] == "warn"
        monkeypatch.setattr(settings, "app_env", "production")
        prod = next(f for f in ar.rule_findings(db_session, seq)
                    if f["code"] == "compliance:sender_identity")
        assert prod["severity"] == "block"

    def test_whatsapp_compliance(self, db_session, verified_strategy, wa_lead_no_optin,
                                 approved_template):
        seq = m.Sequence(strategy_id=verified_strategy.id, channel=m.ChannelType.WHATSAPP,
                         name="wa", status=m.SequenceStatus.DRAFT)
        db_session.add(seq)
        db_session.flush()
        db_session.add(m.SequenceStep(sequence_id=seq.id, step_no=1, template="wa intro",
                                      channel=m.ChannelType.WHATSAPP,
                                      whatsapp_kind=m.WhatsAppStepKind.TEMPLATE,
                                      whatsapp_template_id=approved_template.id))
        db_session.commit()
        db_session.refresh(seq)
        findings = {f["code"]: f for f in ar.rule_findings(db_session, seq)}
        assert findings["compliance:whatsapp_optin"]["severity"] == "warn"
        approved_template.status = m.WhatsAppTemplateStatus.SUBMITTED
        db_session.commit()
        findings = {f["code"]: f for f in ar.rule_findings(db_session, seq)}
        assert findings["compliance:whatsapp_template_unapproved"]["severity"] == "warn"

    def test_eu_leads_are_reported(self, db_session, verified_strategy, verified_leads):
        verified_leads[0].enrichment_json = {"country": "Germany"}
        db_session.commit()
        seq = _sequence(db_session, verified_strategy, "A friendly question.")
        codes = {f["code"] for f in ar.rule_findings(db_session, seq)}
        assert "compliance:gdpr_leads" in codes

    def test_acronyms_are_not_shouting(self, db_session, verified_strategy):
        seq = _sequence(db_session, verified_strategy, "Ask about NFPA and HVAC and GDPR audits.")
        assert "tone:all_caps" not in {f["code"] for f in ar.rule_findings(db_session, seq)}

    def test_the_hash_covers_what_the_lead_receives(self, db_session, verified_strategy):
        seq = _sequence(db_session, verified_strategy, "A friendly question.")
        before = ar.content_hash(seq)
        seq.booking_url = "https://cal.com/new"
        assert ar.content_hash(seq) != before


class TestModelPass:
    def test_model_findings_are_warnings_only(self, client, db_session, verified_strategy,
                                              verified_leads, gmail_account, fake_claude):
        fake_claude.red_team_response = {"findings": [
            {"step_no": 1, "category": "tone", "issue": "Too pushy for a first touch",
             "suggestion": "Lead with their problem"}]}
        seq = _sequence(db_session, verified_strategy, "A friendly question about scheduling.")
        assert _enroll(client, seq).status_code == 202
        review = ar.latest(db_session, seq)
        (finding,) = [f for f in review.findings_json if f["source"] == "model"]
        assert finding["severity"] == "warn" and "Lead with their problem" in finding["message"]
        assert fake_claude.red_team_prompts and "scheduling" in fake_claude.red_team_prompts[0]

    def test_a_model_failure_never_blocks(self, client, db_session, verified_strategy,
                                          verified_leads, gmail_account, fake_claude):
        fake_claude.red_team_response = RuntimeError("model down")
        seq = _sequence(db_session, verified_strategy, "A friendly question.")
        assert _enroll(client, seq).status_code == 202
        assert "system:model_unavailable" in _codes(ar.latest(db_session, seq))
