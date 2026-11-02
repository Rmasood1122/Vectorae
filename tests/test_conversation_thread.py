"""Feature A4 — the unified cross-channel conversation and the stagnation step.

Pins: every channel's history merges into one chronological thread assembled
from the owning tables; stagnation is judged per channel against the last
GENUINE reply; the next channel is plan-, reachability- and consent-aware;
auto-switch moves the already-scheduled message instead of adding one; and the
API is owner-scoped.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.db import models as m
from app.pipeline import channel_orchestrator as orch
from app.services import conversation_thread, system_settings
from tests.conftest import auth_headers

NOW = datetime.now(timezone.utc)


def _sent(db_session, sequence, lead, days_ago, channel=m.ChannelType.EMAIL, step_no=1):
    msg = m.Message(sequence_id=sequence.id, lead_id=lead.id, channel=channel, step_no=step_no,
                    template="brief", subject="Hi", body=f"touch {step_no}",
                    status=m.MessageStatus.SENT, sent_at=NOW - timedelta(days=days_ago))
    db_session.add(msg)
    db_session.commit()
    return msg


@pytest.fixture()
def quiet_lead(db_session, test_user, email_sequence, verified_leads):
    """Three unanswered emails, the newest four days old, one step still scheduled."""
    lead = verified_leads[0]
    lead.linkedin_url = "https://www.linkedin.com/in/lead-zero"
    test_user.plan = m.PlanTier.GROWTH
    db_session.add(m.SequenceEnrollment(sequence_id=email_sequence.id, lead_id=lead.id,
                                        current_step=3))
    for step, days in ((1, 12), (2, 8), (3, 4)):
        _sent(db_session, email_sequence, lead, days, step_no=step)
    pending = m.Message(sequence_id=email_sequence.id, lead_id=lead.id,
                        channel=m.ChannelType.EMAIL, step_no=3, template="breakup brief",
                        status=m.MessageStatus.SCHEDULED, scheduled_at=NOW + timedelta(days=2))
    db_session.add(pending)
    db_session.commit()
    return lead, pending


class TestThread:
    def test_every_channel_in_one_chronological_thread(self, db_session, test_user,
                                                       email_sequence, verified_leads):
        lead = verified_leads[0]
        _sent(db_session, email_sequence, lead, 6)
        db_session.add(m.InboundReply(lead_id=lead.id, channel="whatsapp",
                                      from_address="+15550001", body="Interested!",
                                      classification="interested",
                                      received_at=NOW - timedelta(days=5),
                                      authenticity_kind="genuine", buyer_intent_score=0.9,
                                      authenticity_confidence=0.9))
        db_session.add(m.Call(lead_id=lead.id, provider="vapi", to_number="+15550001",
                              status="ended", outcome=m.CallOutcome.INTERESTED,
                              duration_seconds=240, started_at=NOW - timedelta(days=4),
                              analysis_json={"summary": "Agreed to a demo."}))
        db_session.add(m.Meeting(host_user_id=test_user.id, lead_id=lead.id, title="Demo",
                                 start_at=NOW - timedelta(days=2),
                                 end_at=NOW - timedelta(days=2) + timedelta(minutes=30),
                                 summary="Went well."))
        db_session.commit()

        thread = conversation_thread.build_thread(db_session, lead)
        kinds = [(i["kind"], i["channel"], i["direction"]) for i in thread["items"]]
        assert kinds == [("message", "email", "outbound"), ("reply", "whatsapp", "inbound"),
                         ("call", "phone", "outbound"), ("meeting", "meeting", "both")]
        reply = thread["items"][1]
        assert reply["authenticity"]["kind"] == "genuine"
        assert thread["items"][2]["body"] == "Agreed to a demo."
        summary = {c["channel"]: c for c in thread["summary"]["channels"]}
        assert summary["email"]["outbound"] == 1 and summary["whatsapp"]["inbound"] == 1

    def test_scheduled_messages_show_without_a_body(self, db_session, quiet_lead):
        lead, _pending = quiet_lead
        items = conversation_thread.build_thread(db_session, lead)["items"]
        scheduled = [i for i in items if i["status"] == "scheduled"]
        assert len(scheduled) == 1 and scheduled[0]["body"] is None

    def test_api_is_owner_scoped(self, client, db_session, quiet_lead):
        lead, _ = quiet_lead
        body = client.get(f"/leads/{lead.id}/conversation").json()
        assert len(body["items"]) == 4
        stranger = m.User(email="thread-stranger@example.com", email_verified=True)
        db_session.add(stranger)
        db_session.commit()
        assert client.get(f"/leads/{lead.id}/conversation",
                          headers=auth_headers(stranger)).status_code == 404


class TestStagnation:
    def test_three_unanswered_emails_suggest_linkedin(self, db_session, quiet_lead):
        lead, pending = quiet_lead
        result = orch.detect_stagnation(db_session)
        assert result["suggested"] == 1 and result["auto_switched"] == 0
        (s,) = db_session.execute(select(m.ChannelSuggestion)).scalars().all()
        assert (s.from_channel, s.to_channel, s.sends_without_reply) == ("email", "linkedin", 3)
        assert "LinkedIn profile" in s.reason
        db_session.refresh(pending)
        assert pending.channel is m.ChannelType.EMAIL, "suggest-only by default"

    def test_auto_switch_moves_the_scheduled_message(self, db_session, quiet_lead):
        lead, pending = quiet_lead
        system_settings.set(db_session, "stagnation_auto_switch_enabled", True)
        assert orch.detect_stagnation(db_session)["auto_switched"] == 1
        db_session.refresh(pending)
        assert pending.channel is m.ChannelType.LINKEDIN
        scheduled = db_session.execute(select(m.Message).where(
            m.Message.status == m.MessageStatus.SCHEDULED)).scalars().all()
        assert len(scheduled) == 1, "switched in place, never scheduled twice"
        (s,) = db_session.execute(select(m.ChannelSuggestion)).scalars().all()
        assert s.status == "auto_switched" and s.switched_message_id == pending.id

    def test_a_genuine_reply_resets_the_count(self, db_session, quiet_lead):
        lead, _ = quiet_lead
        db_session.add(m.InboundReply(lead_id=lead.id, channel="email", from_address=lead.email,
                                      body="Not now, try in Q4", classification="not_interested",
                                      received_at=NOW - timedelta(days=5),
                                      authenticity_kind="genuine"))
        db_session.commit()
        assert orch.detect_stagnation(db_session)["suggested"] == 0

    def test_an_out_of_office_does_not_reset_it(self, db_session, quiet_lead):
        lead, _ = quiet_lead
        db_session.add(m.InboundReply(lead_id=lead.id, channel="email", from_address=lead.email,
                                      body="I am away", classification="out_of_office",
                                      received_at=NOW - timedelta(days=5),
                                      authenticity_kind="out_of_office"))
        db_session.commit()
        assert orch.detect_stagnation(db_session)["suggested"] == 1

    def test_the_lead_gets_time_to_reply(self, db_session, quiet_lead, email_sequence):
        lead, _ = quiet_lead
        _sent(db_session, email_sequence, lead, 0.5, step_no=3)
        assert orch.detect_stagnation(db_session)["suggested"] == 0

    def test_no_repeat_suggestion(self, db_session, quiet_lead):
        orch.detect_stagnation(db_session)
        assert orch.detect_stagnation(db_session)["suggested"] == 0

    def test_the_plan_decides_which_channels_exist(self, db_session, test_user, quiet_lead):
        test_user.plan = m.PlanTier.FREE    # email only
        db_session.commit()
        assert orch.detect_stagnation(db_session)["suggested"] == 0

    def test_no_linkedin_profile_link_means_no_linkedin(self, db_session, quiet_lead):
        # L0 (2026-11-02): LinkedIn needs no connected account, only a public
        # profile link the user can open; a company page is not a person.
        lead, _ = quiet_lead
        lead.linkedin_url = "https://www.linkedin.com/company/acme/"
        db_session.commit()
        assert orch.detect_stagnation(db_session)["suggested"] == 0

    def test_whatsapp_is_suggested_but_never_auto_switched(self, db_session, quiet_lead):
        from app.services import whatsapp_optin as optin_svc

        lead, pending = quiet_lead
        lead.linkedin_url = None
        lead.phone = "+923001112233"
        db_session.commit()
        optin_svc.record_opt_in(db_session, lead, source=m.OptInSource.API, evidence="test")
        system_settings.set(db_session, "stagnation_auto_switch_enabled", True)
        result = orch.detect_stagnation(db_session)
        assert result == {"checked": 1, "suggested": 1, "auto_switched": 0}
        db_session.refresh(pending)
        assert pending.channel is m.ChannelType.EMAIL

    def test_the_kill_switch(self, db_session, quiet_lead):
        system_settings.set(db_session, "stagnation_detection_enabled", False)
        assert orch.detect_stagnation(db_session) == {"checked": 0, "suggested": 0,
                                                     "auto_switched": 0}

    def test_an_outcome_ranking_can_reorder_the_choice(self, db_session, quiet_lead):
        from app.services import phone_calls

        lead, _ = quiet_lead
        lead.phone = "+14155550123"
        lead.phone_consent_at = NOW
        test_user = db_session.get(m.User, db_session.get(m.Product, db_session.get(
            m.Strategy, lead.strategy_id).product_id).user_id)
        test_user.plan = m.PlanTier.SCALE     # the first plan that includes AI calls
        db_session.commit()
        system_settings.set(db_session, "phone_calling_enabled", True)
        orch.detect_stagnation(db_session, ranking_for=lambda s, l: ["phone", "linkedin"])
        (s,) = db_session.execute(select(m.ChannelSuggestion)).scalars().all()
        assert s.to_channel == "phone"
        assert phone_calls.consent_ok(db_session, lead)


class TestSuggestionApi:
    def test_accept_switches_and_is_final(self, client, db_session, quiet_lead):
        lead, pending = quiet_lead
        client.post("/channel-suggestions/detect")
        (s,) = client.get("/channel-suggestions").json()
        resp = client.post(f"/channel-suggestions/{s['id']}/accept")
        assert resp.status_code == 200 and resp.json()["switched_message_id"] == str(pending.id)
        db_session.refresh(pending)
        assert pending.channel is m.ChannelType.LINKEDIN
        assert client.post(f"/channel-suggestions/{s['id']}/accept").status_code == 409

    def test_dismiss(self, client, quiet_lead):
        lead, _ = quiet_lead
        client.post("/channel-suggestions/detect")
        (s,) = client.get(f"/leads/{lead.id}/channel-suggestions").json()
        assert client.post(f"/channel-suggestions/{s['id']}/dismiss").json()["status"] == "dismissed"
        assert client.get("/channel-suggestions").json() == []

    def test_other_accounts_cannot_act(self, client, db_session, quiet_lead):
        client.post("/channel-suggestions/detect")
        (s,) = client.get("/channel-suggestions").json()
        stranger = m.User(email="suggest-stranger@example.com", email_verified=True)
        db_session.add(stranger)
        db_session.commit()
        assert client.post(f"/channel-suggestions/{s['id']}/accept",
                           headers=auth_headers(stranger)).status_code == 404


def test_the_step_is_scheduled_on_the_outreach_queue():
    from app.workers import channel_tasks  # noqa: F401
    from app.workers.celery_app import celery_app

    entry = celery_app.conf.beat_schedule["detect-channel-stagnation"]
    assert entry["task"] == "app.workers.channel_tasks.detect_channel_stagnation"
    assert celery_app.amqp.router.route({}, entry["task"])["queue"].name == "outreach"
