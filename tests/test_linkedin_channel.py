"""Feature Group 5 — LinkedIn sequence steps as L0 tasks (user sends).

Since 2026-11-02 (evals/vectorae, option a) Vectorae never acts on LinkedIn.
What must hold:
  * a due LinkedIn step renders claim-checked text and queues ONE task for
    the strategy's owner (source "sequence"); nothing is transmitted;
  * the connect / message decision follows the documented rules from what the
    USER reported on earlier tasks; a message to someone not yet connected is
    skipped (never forced);
  * no profile link -> the step fails clearly; a full waiting queue defers to
    tomorrow (never dropped);
  * suppression covers LinkedIn profiles, from every opt-out path, at send
    time and at enrol time; a "replied" task stops the lead's sequences;
  * the removed Unipile routes are gone.
"""

from __future__ import annotations

from datetime import timezone

import pytest
from sqlalchemy import select

from app.db import models as m
from app.integrations.linkedin_channel import LinkedInChannel
from app.services import linkedin_outreach, linkedin_tasks
from app.workers import outreach_tasks

from .conftest import NOW, entitle_owner

UTC = timezone.utc


# --------------------------------------------------------------------------
# Fixtures (also imported by the sealed tests/test_daily_send_approval_e2e.py)
# --------------------------------------------------------------------------


@pytest.fixture()
def li_sequence(db_session, verified_strategy):
    # Growth is the cheapest plan with LinkedIn; the send path re-checks the
    # plan (app/services/plan_holds.py). This file is about LinkedIn, not plans.
    entitle_owner(db_session, verified_strategy, m.PlanTier.GROWTH)
    seq = m.Sequence(strategy_id=verified_strategy.id, channel=m.ChannelType.LINKEDIN,
                     name="LinkedIn", status=m.SequenceStatus.ACTIVE)
    db_session.add(seq)
    db_session.flush()
    db_session.add(m.SequenceStep(sequence_id=seq.id, step_no=1, template="connect brief",
                                  delay_days=0, linkedin_action="auto"))
    db_session.add(m.SequenceStep(sequence_id=seq.id, step_no=2, template="follow up",
                                  delay_days=0, linkedin_action="message"))
    db_session.commit()
    return seq


def _li_lead(db_session, strategy, i, **kw):
    lead = m.Lead(strategy_id=strategy.id, source="apollo", external_id=f"LI{i}",
                  full_name=f"Li Lead {i}", company="Acme", status=m.LeadStatus.VERIFIED,
                  linkedin_url=f"https://www.linkedin.com/in/li-lead-{i}/", **kw)
    db_session.add(lead)
    db_session.commit()
    return lead


def _message(db_session, seq, lead, step_no=1):
    from app.services import sequence_engine as engine

    enrollment = m.SequenceEnrollment(sequence_id=seq.id, lead_id=lead.id)
    db_session.add(enrollment)
    db_session.flush()
    step = next(s for s in seq.steps if s.step_no == step_no)
    return engine._schedule_step_message(db_session, seq, enrollment, lead, step, base_time=NOW)


def _send(db_session, message, channel=None):
    return outreach_tasks.send_message_impl(db_session, message.id, channel=channel, now=NOW)


class RecordingChannel:
    """Stands in for the assisted channel: records OutboundMessages."""

    def __init__(self):
        self.sent = []

    def send(self, outbound):
        from app.integrations.outreach_base import SendResult
        self.sent.append(outbound)
        return SendResult(ok=True, provider_message_id=f"p{len(self.sent)}")


def _tasks(db):
    return list(db.execute(select(m.LinkedInTask).order_by(m.LinkedInTask.created_at)).scalars())


# --------------------------------------------------------------------------
# Action rules (now from what the user reported)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("configured_action,status,premium,expected", [
    ("auto", None, False, "connect"),
    ("auto", None, True, "inmail"),
    ("auto", "pending", True, linkedin_outreach.WAIT),
    ("auto", "connected", False, "message"),
    ("connect", "connected", False, "message"),
    ("connect", "pending", False, linkedin_outreach.WAIT),
    ("message", None, False, linkedin_outreach.WAIT),
    ("message", "connected", False, "message"),
    ("inmail", None, False, "inmail"),
    (None, None, False, "connect"),
])
def test_resolve_action(configured_action, status, premium, expected):
    lead = m.Lead(linkedin_connection_status=status, linkedin_is_premium=premium)
    assert linkedin_outreach.resolve_action(configured_action, lead) == expected


# --------------------------------------------------------------------------
# The send path: a step becomes a task
# --------------------------------------------------------------------------


class TestStepBecomesATask:
    def test_first_touch_queues_a_connection_note_task(self, db_session, verified_strategy,
                                                        li_sequence, fake_claude, test_user):
        lead = _li_lead(db_session, verified_strategy, 1)
        msg = _message(db_session, li_sequence, lead)
        assert _send(db_session, msg) == "sent"
        db_session.refresh(msg)
        (task,) = _tasks(db_session)
        assert (task.user_id, task.source, task.kind, task.lead_id) == (
            test_user.id, "sequence", "connection_note", lead.id)
        assert task.profile_url == "https://www.linkedin.com/in/li-lead-1/"
        assert task.text == msg.body and len(task.text) <= 300
        assert task.status == "pending"                      # the user approves it
        assert msg.sender_ref == "linkedin:assisted"
        assert msg.provider_message_id == str(task.id)
        assert msg.linkedin_action == "connect" and msg.channel is m.ChannelType.LINKEDIN

    def test_inmail_becomes_a_message_task(self, db_session, verified_strategy, li_sequence,
                                           fake_claude):
        lead = _li_lead(db_session, verified_strategy, 2, linkedin_is_premium=True)
        assert _send(db_session, _message(db_session, li_sequence, lead)) == "sent"
        assert _tasks(db_session)[0].kind == "message"

    def test_message_step_waits_for_the_connection(self, db_session, verified_strategy,
                                                   li_sequence, fake_claude):
        lead = _li_lead(db_session, verified_strategy, 4)
        msg = _message(db_session, li_sequence, lead, step_no=2)
        assert _send(db_session, msg) == "skipped_linkedin_not_connected"
        db_session.refresh(msg)
        assert msg.status is m.MessageStatus.SKIPPED and _tasks(db_session) == []

    def test_user_reports_drive_the_next_step(self, db_session, verified_strategy, li_sequence,
                                              fake_claude, test_user):
        lead = _li_lead(db_session, verified_strategy, 5)
        _send(db_session, _message(db_session, li_sequence, lead))
        note = _tasks(db_session)[0]
        linkedin_tasks.approve(db_session, test_user, note.id, now=NOW)
        linkedin_tasks.open_task(db_session, test_user, note.id, now=NOW)
        linkedin_tasks.complete(db_session, test_user, note.id, "done", now=NOW)
        db_session.commit()
        db_session.refresh(lead)
        assert lead.linkedin_connection_status == "pending"
        # Still pending: a message step waits.
        lead2_msg = _message(db_session, li_sequence, _li_lead(db_session, verified_strategy, 6),
                             step_no=2)
        assert _send(db_session, lead2_msg) == "skipped_linkedin_not_connected"

    def test_replied_marks_connected_and_stops_the_sequence(
            self, db_session, verified_strategy, li_sequence, fake_claude, test_user):
        lead = _li_lead(db_session, verified_strategy, 7)
        _send(db_session, _message(db_session, li_sequence, lead))
        task = _tasks(db_session)[0]
        linkedin_tasks.approve(db_session, test_user, task.id, now=NOW)
        linkedin_tasks.open_task(db_session, test_user, task.id, now=NOW)
        linkedin_tasks.complete(db_session, test_user, task.id, "replied", now=NOW)
        db_session.commit()
        db_session.refresh(lead)
        assert lead.linkedin_connection_status == "connected"
        enrollment = db_session.execute(select(m.SequenceEnrollment).where(
            m.SequenceEnrollment.lead_id == lead.id)).scalar_one()
        assert enrollment.status is m.EnrollmentStatus.STOPPED

    def test_no_profile_link_fails_clearly(self, db_session, verified_strategy, li_sequence,
                                           fake_claude):
        lead = _li_lead(db_session, verified_strategy, 8)
        lead.linkedin_url = "https://www.linkedin.com/company/acme/"
        db_session.commit()
        msg = _message(db_session, li_sequence, lead)
        assert _send(db_session, msg) == "failed_no_linkedin_profile"
        assert _tasks(db_session) == []

    def test_full_queue_defers_never_drops(self, db_session, verified_strategy, li_sequence,
                                           fake_claude, test_user, monkeypatch):
        monkeypatch.setattr(linkedin_tasks, "_cap", lambda db, cls: 1)
        _send(db_session, _message(db_session, li_sequence,
                                   _li_lead(db_session, verified_strategy, 11)))
        msg = _message(db_session, li_sequence, _li_lead(db_session, verified_strategy, 12))
        assert _send(db_session, msg) == "deferred_cap"
        db_session.refresh(msg)
        assert msg.status is m.MessageStatus.SCHEDULED
        assert msg.scheduled_at.replace(tzinfo=UTC) > NOW
        assert len(_tasks(db_session)) == 1

    def test_suppressed_profile_is_never_contacted(self, db_session, verified_strategy,
                                                   li_sequence, fake_claude):
        lead = _li_lead(db_session, verified_strategy, 10)
        db_session.add(m.LinkedInSuppression(profile="li-lead-10", reason="test"))
        db_session.commit()
        channel = RecordingChannel()
        assert _send(db_session, _message(db_session, li_sequence, lead),
                     channel) == "cancelled_suppressed"
        assert channel.sent == [] and _tasks(db_session) == []


class TestChannelClass:
    def test_registered_as_the_assisted_provider(self, db_session):
        from app.integrations.outreach_base import get_channel, registered_channels

        assert "assisted" in registered_channels() and "unipile_linkedin" not in registered_channels()
        ch = get_channel("assisted", session=db_session, owner=None)
        assert isinstance(ch, LinkedInChannel) and ch.channel == "linkedin"

    def test_it_never_reads_replies_from_linkedin(self, db_session):
        assert LinkedInChannel(db_session, None).fetch_replies() == []

    def test_guard_refuses_a_suppressed_lead(self, db_session, verified_strategy, test_user):
        from app.core.exceptions import ComplianceError
        from app.integrations.outreach_base import OutboundMessage

        lead = _li_lead(db_session, verified_strategy, 20)
        db_session.add(m.LinkedInSuppression(profile="li-lead-20", reason="t"))
        db_session.commit()
        ch = LinkedInChannel(db_session, test_user)
        with pytest.raises(ComplianceError):
            ch.send(OutboundMessage(message_id="x", lead_id=str(lead.id), to_address="",
                                    body="hi", metadata={"action": "connect"}))
        assert _tasks(db_session) == []


# --------------------------------------------------------------------------
# Enrolment, suppression paths, sequences API, removed routes
# --------------------------------------------------------------------------


def test_linkedin_first_sequence_enrols_leads_without_email(db_session, verified_strategy,
                                                           li_sequence):
    from app.services.sequence_engine import enroll_leads

    _li_lead(db_session, verified_strategy, 30)                 # no email
    no_url = m.Lead(strategy_id=verified_strategy.id, source="apollo", external_id="X",
                    email="x@y.test", status=m.LeadStatus.VERIFIED)
    db_session.add(no_url)
    db_session.commit()
    assert enroll_leads(db_session, li_sequence, now=NOW) == 1


def test_unsubscribe_and_gdpr_suppress_the_profile(client, db_session, verified_strategy):
    from app.services import sequence_engine as engine
    from app.workers.lead_tasks import is_suppressed

    lead = _li_lead(db_session, verified_strategy, 31)
    engine.unsubscribe_lead(db_session, lead, source="test")
    assert is_suppressed(db_session, linkedin="https://linkedin.com/in/LI-LEAD-31")

    other = _li_lead(db_session, verified_strategy, 32)
    assert client.delete(f"/leads/{other.id}").status_code == 204
    assert is_suppressed(db_session, linkedin="li-lead-32")
    db_session.refresh(other)
    assert other.linkedin_url is None


def test_sequences_api_accepts_linkedin_steps(client, verified_strategy, db_session, test_user):
    test_user.plan = m.PlanTier.GROWTH
    db_session.commit()
    resp = client.post(f"/strategies/{verified_strategy.id}/sequences", json={
        "name": "LI", "channel": "linkedin",
        "steps": [{"step_no": 1, "template": "connect"},
                  {"step_no": 2, "template": "follow", "linkedin_action": "message"}]})
    assert resp.status_code == 201, resp.text
    assert [s["linkedin_action"] for s in resp.json()["steps"]] == ["auto", "message"]


def test_the_unipile_routes_are_gone():
    from app.main import app

    paths = {getattr(r, "path", "") for r in app.routes}
    assert not {p for p in paths if "unipile" in p or p.startswith("/integrations/linkedin")}
    assert "/linkedin-network/messages/{message_id}/send" not in paths
    assert "/linkedin-network/messages/bulk-send" not in paths
    assert "/leads/{lead_id}/linkedin" in paths
