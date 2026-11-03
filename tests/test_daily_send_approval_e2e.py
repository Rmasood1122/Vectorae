"""Daily send approval, end to end through the REAL send path, flag ON.

LEGAL/SAFETY GUARD (docs/features/daily-send-approval.md). Every automatic
path is driven through the production code that sends it -- the chokepoint,
the follow-up sweep, re-engagement, "not now" memory, the F01/F10 hold
releases and the Reply Copilot auto-send task -- and shown to be HELD without
today's approval, then SENT after it, with nothing copied or lost.

Fixtures for each channel are the ones its own test module uses (imported, so
the channel set-up here is exactly the one those suites prove sends).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.db import models as m
from app.services import daily_send_approval as dsa
from app.services import system_settings
from app.workers import outreach_tasks
from tests.conftest import NOW
# Channel fixtures and helpers, reused from the suites that prove each channel sends.
from tests.test_linkedin_channel import (  # noqa: F401
    RecordingChannel,
    _li_lead,
    _message as _li_message,
    li_sequence,
)
from tests.test_phone_calls import (  # noqa: F401
    _message as _phone_message,
    calling_on,
    phone_lead,
    phone_sequence,
    voice,
)
from tests.test_whatsapp_engine import _schedule_step

UTC = timezone.utc
_REAL_ENABLED = dsa.enabled


@pytest.fixture(autouse=True)
def approval_on(monkeypatch):
    """Undo conftest's `daily_send_approval_off`: the real flag, default ON."""
    monkeypatch.setattr(dsa, "enabled", _REAL_ENABLED)


def _approve(db, user, channels, now=NOW):
    return dsa.approve(db, user.id, actor_id=user.id, channels=channels,
                       linkedin_risk_acknowledged="linkedin" in channels,
                       linkedin_warning_version=(dsa.LINKEDIN_WARNING_VERSION
                                                 if "linkedin" in channels else None),
                       now=now)


def _assert_held(db, message, result, expected=dsa.SEND_RESULT_AWAITING):
    db.refresh(message)
    assert result == expected
    assert message.status is m.MessageStatus.SCHEDULED
    assert message.error.startswith(dsa.ERROR_PREFIX)


# --------------------------------------------------------------------------
# Every channel: held, approved, sent
# --------------------------------------------------------------------------


class TestChannels:
    # Gate change 2026-11-02 (docs/program/plans/gate-linkedin-unipile-removal.md):
    # LinkedIn "sent" now means handed to the user's L0 task queue; the hold
    # and the acknowledged approval it guards are unchanged. The second test
    # proves the real channel queues exactly one task only after approval.
    def test_linkedin_held_then_approved_with_acknowledgement_then_sent(
            self, db_session, verified_strategy, li_sequence, fake_claude, test_user):
        lead = _li_lead(db_session, verified_strategy, 1)
        msg = _li_message(db_session, li_sequence, lead)
        channel = RecordingChannel()
        _assert_held(db_session, msg, outreach_tasks.send_message_impl(
            db_session, msg.id, channel=channel, now=NOW))
        # Email-only approval leaves LinkedIn waiting.
        _approve(db_session, test_user, ["email"])
        _assert_held(db_session, msg, outreach_tasks.send_message_impl(
            db_session, msg.id, channel=channel, now=NOW), dsa.SEND_RESULT_CHANNEL)
        assert channel.sent == []
        out = _approve(db_session, test_user, ["linkedin"])
        assert out["released"] == 1
        assert outreach_tasks.send_message_impl(db_session, msg.id, channel=channel,
                                                now=NOW) == "sent"
        assert len(channel.sent) == 1

    def test_linkedin_real_channel_queues_a_task_only_after_approval(
            self, db_session, verified_strategy, li_sequence, fake_claude, test_user):
        lead = _li_lead(db_session, verified_strategy, 2)
        msg = _li_message(db_session, li_sequence, lead)
        _assert_held(db_session, msg, outreach_tasks.send_message_impl(
            db_session, msg.id, now=NOW))
        assert db_session.query(m.LinkedInTask).count() == 0
        _approve(db_session, test_user, ["linkedin"])
        assert outreach_tasks.send_message_impl(db_session, msg.id, now=NOW) == "sent"
        (task,) = db_session.query(m.LinkedInTask).all()
        assert task.status == "pending" and task.lead_id == lead.id   # the user still approves it

    def test_whatsapp_held_then_approved_then_sent(
            self, db_session, fake_claude, gmail_account, multichannel_sequence, wa_lead,
            approved_template, fake_channel, test_user):
        msg, _ = _schedule_step(db_session, multichannel_sequence, wa_lead, step_no=2)
        assert msg.channel is m.ChannelType.WHATSAPP
        _assert_held(db_session, msg, outreach_tasks.send_message_impl(
            db_session, msg.id, channel=fake_channel, now=NOW))
        assert fake_channel.sent == []
        _approve(db_session, test_user, ["whatsapp"])
        assert outreach_tasks.send_message_impl(db_session, msg.id, channel=fake_channel,
                                                now=NOW) == "sent"
        assert len(fake_channel.sent) == 1

    def test_phone_held_then_approved_then_called(
            self, db_session, phone_sequence, phone_lead, voice, calling_on, fake_claude,
            test_user):
        from app.integrations.phone_channel import PhoneChannel

        msg = _phone_message(db_session, phone_sequence, phone_lead)
        _assert_held(db_session, msg, outreach_tasks.send_message_impl(
            db_session, msg.id, channel=PhoneChannel(db_session, **voice), now=NOW))
        assert voice["vapi"].calls == []
        _approve(db_session, test_user, ["phone"])
        assert outreach_tasks.send_message_impl(
            db_session, msg.id, channel=PhoneChannel(db_session, **voice), now=NOW) == "sent"
        assert len(voice["vapi"].calls) == 1


# --------------------------------------------------------------------------
# The follow-up sweep never copies a held step
# --------------------------------------------------------------------------


class TestFollowupSweep:
    def test_a_held_step_is_pending_so_the_sweep_makes_no_copy(
            self, db_session, enrolled, verified_leads, fake_channel, test_user, monkeypatch):
        monkeypatch.setattr(outreach_tasks, "_get_channel", lambda s, a: fake_channel)
        _approve(db_session, test_user, ["email"])
        first = (db_session.query(m.Message).filter_by(status=m.MessageStatus.SCHEDULED)
                 .order_by(m.Message.created_at, m.Message.id).first())
        assert outreach_tasks.send_message_impl(db_session, first.id, now=NOW) == "sent"
        later = NOW + timedelta(days=10)       # a new day: no approval
        step2 = db_session.query(m.Message).filter_by(
            lead_id=first.lead_id, status=m.MessageStatus.SCHEDULED).one()
        step2.scheduled_at = later - timedelta(minutes=1)
        db_session.commit()
        _assert_held(db_session, step2,
                     outreach_tasks.send_message_impl(db_session, step2.id, now=later))
        before = db_session.query(m.Message).filter_by(lead_id=first.lead_id).count()

        enrollment = db_session.query(m.SequenceEnrollment).filter_by(
            lead_id=first.lead_id).one()
        due = outreach_tasks.check_followup_due_impl(
            db_session, now=later + timedelta(days=5), enqueue=lambda e, s: None)
        assert all(e != str(enrollment.id) for e, _ in due)
        assert outreach_tasks.send_followup_impl(
            db_session, enrollment.id, step2.step_no + 1,
            now=later + timedelta(days=5)) == "skipped_already_pending"
        assert db_session.query(m.Message).filter_by(lead_id=first.lead_id).count() == before


# --------------------------------------------------------------------------
# Re-engagement and "not now" memory
# --------------------------------------------------------------------------


@pytest.fixture()
def in_window(monkeypatch):
    from app.services import sequence_engine as engine

    monkeypatch.setattr(engine, "in_send_window", lambda dt, tz, window=None: True)


class TestReengagement:
    def test_reengagement_send_is_held_then_sent_after_approval(
            self, db_session, verified_strategy, gmail_account, fake_channel, fake_claude,
            test_user, in_window, monkeypatch):
        monkeypatch.setattr(outreach_tasks, "_get_channel", lambda s, a: fake_channel)
        from app.services import reengagement

        verified_strategy.reengagement_enabled = True
        verified_strategy.reengagement_delay_days = 30
        seq = m.Sequence(strategy_id=verified_strategy.id, channel=m.ChannelType.EMAIL,
                         name="Outbound", status=m.SequenceStatus.ACTIVE)
        db_session.add(seq)
        db_session.flush()
        db_session.add(m.SequenceStep(sequence_id=seq.id, step_no=1, template="Open",
                                      delay_days=0))
        lead = m.Lead(strategy_id=verified_strategy.id, source="manual", external_id="re1",
                      full_name="Lead 1", company="Acme", email="lead1@acme.test",
                      status=m.LeadStatus.CONTACTED)
        db_session.add(lead)
        db_session.flush()
        when = datetime.now(UTC) - timedelta(days=40)
        enrollment = m.SequenceEnrollment(sequence_id=seq.id, lead_id=lead.id,
                                          status=m.EnrollmentStatus.COMPLETED, current_step=1)
        db_session.add_all([enrollment, m.Message(
            sequence_id=seq.id, lead_id=lead.id, channel=m.ChannelType.EMAIL, step_no=1,
            template="Open", status=m.MessageStatus.SENT, sent_at=when, scheduled_at=when,
            body="hello", subject="hi")])
        db_session.commit()

        result = outreach_tasks.send_reengagement_impl(db_session, enrollment.id)
        msg = db_session.query(m.Message).filter_by(origin=reengagement.ORIGIN).one()
        _assert_held(db_session, msg, result)
        assert fake_channel.sent == []
        _approve(db_session, test_user, ["email"], now=datetime.now(UTC))
        assert outreach_tasks.send_message_impl(db_session, msg.id) == "sent"
        assert len(fake_channel.sent) == 1
        assert db_session.query(m.Message).filter_by(origin=reengagement.ORIGIN).count() == 1

    def test_not_now_memory_touch_is_held_then_sent_after_approval(
            self, db_session, verified_strategy, email_sequence, gmail_account, fake_channel,
            fake_claude, test_user, in_window, monkeypatch):
        from app.services import reengagement_memory as memory

        monkeypatch.setattr(outreach_tasks, "_get_channel", lambda s, a: fake_channel)
        system_settings.set(db_session, "reengagement_memory_auto_send", True)
        lead = m.Lead(strategy_id=verified_strategy.id, source="apollo", external_id="RM1",
                      full_name="Sara Khan", title="Operations Manager", company="Blaze",
                      email="sara@blaze.test", status=m.LeadStatus.CONTACTED)
        db_session.add(lead)
        db_session.flush()
        db_session.add_all([
            m.SequenceEnrollment(sequence_id=email_sequence.id, lead_id=lead.id,
                                 status=m.EnrollmentStatus.ACTIVE, current_step=2),
            m.Message(sequence_id=email_sequence.id, lead_id=lead.id,
                      channel=m.ChannelType.EMAIL, step_no=2, template="t",
                      status=m.MessageStatus.SENT, sent_at=NOW - timedelta(days=90),
                      body="earlier")])
        plan = m.ReengagementPlan(lead_id=lead.id, user_id=test_user.id,
                                  strategy_id=lead.strategy_id, due_at=NOW - timedelta(days=1),
                                  reason_kind=memory.BUDGET, reason_text="no budget",
                                  status=memory.SCHEDULED)
        db_session.add(plan)
        db_session.commit()
        memory.run_due(db_session, now=NOW)
        db_session.refresh(plan)
        msg = db_session.get(m.Message, plan.message_id)
        assert msg.origin == memory.ORIGIN
        _assert_held(db_session, msg,
                     outreach_tasks.send_message_impl(db_session, msg.id, now=NOW))
        _approve(db_session, test_user, ["email"])
        assert outreach_tasks.send_message_impl(db_session, msg.id, now=NOW) == "sent"
        assert len(fake_channel.sent) == 1


# --------------------------------------------------------------------------
# F01 / F10 holds released INTO the gate
# --------------------------------------------------------------------------


class TestGuardReleases:
    def _first(self, db):
        return (db.query(m.Message).filter_by(status=m.MessageStatus.SCHEDULED)
                .order_by(m.Message.created_at, m.Message.id).first())

    def test_a_bounce_shield_release_still_needs_todays_approval(
            self, db_session, enrolled, verified_leads, fake_channel, test_user, monkeypatch):
        from app.services import bounce_shield

        monkeypatch.setattr(outreach_tasks, "_get_channel", lambda s, a: fake_channel)
        system_settings.set(db_session, "bounce_shield_enabled", True)
        msg = self._first(db_session)
        lead = db_session.get(m.Lead, msg.lead_id)
        assert outreach_tasks.send_message_impl(db_session, msg.id, now=NOW) == \
            bounce_shield.SEND_RESULT
        bounce_shield.set_override(db_session, lead, actor_id=test_user.id,
                                   owner_id=test_user.id, reason="checked by phone", now=NOW)
        assert bounce_shield.requeue_blocked(db_session, lead, now=NOW) == 1
        _assert_held(db_session, msg,
                     outreach_tasks.send_message_impl(db_session, msg.id, now=NOW))
        assert fake_channel.sent == []
        _approve(db_session, test_user, ["email"])
        assert outreach_tasks.send_message_impl(db_session, msg.id, now=NOW) == "sent"

    def test_a_champion_tracker_release_still_needs_todays_approval(
            self, db_session, enrolled, verified_leads, fake_channel, test_user, monkeypatch):
        from app.services import champion_tracker

        monkeypatch.setattr(outreach_tasks, "_get_channel", lambda s, a: fake_channel)
        system_settings.set(db_session, "champion_tracker_enabled", True)
        msg = self._first(db_session)
        lead = db_session.get(m.Lead, msg.lead_id)
        lead.created_at = NOW - timedelta(days=400)        # stale role data
        db_session.commit()
        assert outreach_tasks.send_message_impl(db_session, msg.id, now=NOW) == \
            champion_tracker.SEND_RESULT_HELD
        champion_tracker._set_role_check(lead, state=champion_tracker.CURRENT,
                                         observed_at=NOW)
        db_session.commit()
        assert champion_tracker.release_held(db_session, lead, now=NOW) == 1
        _assert_held(db_session, msg,
                     outreach_tasks.send_message_impl(db_session, msg.id, now=NOW))
        _approve(db_session, test_user, ["email"])
        assert outreach_tasks.send_message_impl(db_session, msg.id, now=NOW) == "sent"
        assert len(fake_channel.sent) == 1


# --------------------------------------------------------------------------
# Reply Copilot auto-send through its own task
# --------------------------------------------------------------------------


class TestReplyCopilotAutoSend:
    def test_an_auto_sent_answer_waits_for_todays_approval(
            self, db_session, test_user, fake_claude, monkeypatch):
        from app.integrations.outreach_base import SendResult
        from app.services import crypto, reply_copilot
        from app.services import sequence_engine as engine
        from app.workers import reply_tasks
        from tests.test_reply_copilot import BOOKING, MEETING_REPLY, _build, _row

        sent: list = []

        class _Channel:
            def send(self, outbound):
                sent.append(outbound)
                return SendResult(ok=True, provider_message_id="pm-1", thread_ref="th-1")

        monkeypatch.setattr(outreach_tasks, "_get_channel", lambda s, a: _Channel())
        monkeypatch.setattr(engine, "in_send_window", lambda dt, tz, window=None: True)
        monkeypatch.setattr(reply_tasks, "enqueue_send", lambda message_id: True)
        fake_claude.reply_copilot_response = {
            "body": f"Great - here is my calendar, pick any slot: {BOOKING}",
            "meeting_time_request": True}
        db_session.add(m.GmailAccount(
            user_id=test_user.id, email_address="me@leadpilot.dev",
            token_ciphertext=crypto.encrypt_json({"access_token": "a", "refresh_token": "r"}),
            token_expires_at=datetime.now(UTC) + timedelta(hours=1)))
        system_settings.set(db_session, "reply_copilot_enabled", True)
        system_settings.set(db_session, "reply_copilot_auto_send_allowed", True)
        reply_copilot.set_auto_send(db_session, test_user.id, True, actor_user_id=test_user.id)
        world = _build(db_session, test_user, reply_body=MEETING_REPLY)
        assert reply_copilot.draft_for_reply(db_session, world["reply"])["auto"] == "queued"
        draft = _row(db_session, world["reply"])
        assert draft.auto_sent is True

        result = reply_tasks.send_reply_draft_impl(db_session, draft.message_id)
        msg = db_session.get(m.Message, draft.message_id)
        _assert_held(db_session, msg, result)
        assert sent == []
        _approve(db_session, test_user, ["email"], now=datetime.now(UTC))
        assert reply_tasks.send_reply_draft_impl(db_session, draft.message_id) == "sent"
        assert len(sent) == 1
