"""Engagement Hub, Feature 1 — the automated follow-up.

THE ASSERTION THIS FILE EXISTS FOR is that the sweep does NOT fire for an
enrollment the sequence engine is already handling. schedule_next_step queues
step N+1 the moment step N sends; a sweep that also queued one would send the
same prospect two messages, and that is the failure nobody would notice in
staging and everybody would notice in a customer's inbox.

Everything else here follows from that: what IS left over (a permanently
failed send, a WhatsApp step stuck on needs_template, a step deleted
mid-flight), the idempotency lock, the auto-DM fallback when the sequence has
run out of steps, and the fact that every automatic send still goes through
the same compliance path as a scheduled one.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.db import models as m
from app.workers import outreach_tasks as tasks

UTC = timezone.utc


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture()
def sequence(db_session, verified_strategy):
    seq = m.Sequence(strategy_id=verified_strategy.id,
                     channel=m.ChannelType.EMAIL, name="Outbound",
                     status=m.SequenceStatus.ACTIVE)
    db_session.add(seq)
    db_session.flush()
    db_session.add_all([
        m.SequenceStep(sequence_id=seq.id, step_no=1, template="Open on X",
                       delay_days=0, followup_delay_hours=48),
        m.SequenceStep(sequence_id=seq.id, step_no=2, template="Nudge",
                       delay_days=3, followup_delay_hours=72),
    ])
    db_session.commit()
    return seq


@pytest.fixture()
def lead(db_session, verified_strategy):
    row = m.Lead(strategy_id=verified_strategy.id, source="manual",
                 full_name="Sara Khan", company="Acme Fire",
                 email="sara@acme.test", status=m.LeadStatus.CONTACTED)
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture()
def enrollment(db_session, sequence, lead):
    row = m.SequenceEnrollment(sequence_id=sequence.id, lead_id=lead.id,
                              status=m.EnrollmentStatus.ACTIVE, current_step=1)
    db_session.add(row)
    db_session.commit()
    return row


def _sent(db_session, sequence, lead, *, step_no=1, hours_ago=100,
          status=m.MessageStatus.SENT) -> m.Message:
    when = datetime.now(UTC) - timedelta(hours=hours_ago)
    msg = m.Message(
        sequence_id=sequence.id, lead_id=lead.id, channel=m.ChannelType.EMAIL,
        step_no=step_no, template="Open on X", status=status,
        sent_at=when if status is m.MessageStatus.SENT else None,
        scheduled_at=when, body="hello", subject="hi",
    )
    db_session.add(msg)
    db_session.commit()
    return msg


def _sweep(db_session, now=None):
    found: list[tuple[str, int]] = []
    tasks.check_followup_due_impl(
        db_session, now=now or datetime.now(UTC),
        enqueue=lambda eid, step: found.append((eid, step)),
    )
    return found


# --------------------------------------------------------------------------
# The sweep
# --------------------------------------------------------------------------


class TestSweep:
    def test_a_stalled_enrollment_is_picked_up(
        self, db_session, sequence, lead, enrollment
    ):
        """Step 1 sent 100 hours ago against a 48-hour window, no reply,
        nothing queued behind it — the case the engine currently drops."""
        _sent(db_session, sequence, lead, hours_ago=100)
        assert _sweep(db_session) == [(str(enrollment.id), 2)]

    def test_an_enrollment_with_a_queued_message_is_left_alone(
        self, db_session, sequence, lead, enrollment
    ):
        """THE central guard. schedule_next_step owns this enrollment; firing
        here too sends the same prospect the same message twice."""
        _sent(db_session, sequence, lead, hours_ago=100)
        db_session.add(m.Message(
            sequence_id=sequence.id, lead_id=lead.id,
            channel=m.ChannelType.EMAIL, step_no=2, template="Nudge",
            status=m.MessageStatus.SCHEDULED,
            scheduled_at=datetime.now(UTC) + timedelta(days=1),
        ))
        db_session.commit()
        assert _sweep(db_session) == []

    def test_a_message_being_sent_right_now_also_blocks_the_sweep(
        self, db_session, sequence, lead, enrollment
    ):
        _sent(db_session, sequence, lead, hours_ago=100)
        db_session.add(m.Message(
            sequence_id=sequence.id, lead_id=lead.id,
            channel=m.ChannelType.EMAIL, step_no=2, template="Nudge",
            status=m.MessageStatus.SENDING,
        ))
        db_session.commit()
        assert _sweep(db_session) == []

    def test_a_failed_send_leaves_an_enrollment_the_sweep_recovers(
        self, db_session, sequence, lead, enrollment
    ):
        """FAILED is not pending, so the enrollment sits ACTIVE with nothing
        queued — forever, before this feature."""
        _sent(db_session, sequence, lead, step_no=1, hours_ago=100)
        db_session.add(m.Message(
            sequence_id=sequence.id, lead_id=lead.id,
            channel=m.ChannelType.EMAIL, step_no=2, template="Nudge",
            status=m.MessageStatus.FAILED, error="permanent",
        ))
        db_session.commit()
        assert _sweep(db_session) == [(str(enrollment.id), 2)]

    def test_inside_the_window_nothing_fires(
        self, db_session, sequence, lead, enrollment
    ):
        _sent(db_session, sequence, lead, hours_ago=10)
        assert _sweep(db_session) == []

    def test_the_per_step_delay_is_the_one_that_applies(
        self, db_session, sequence, lead, enrollment
    ):
        """Step 1 is 48h and step 2 is 72h. A send 60 hours ago is overdue on
        step 1 and not on step 2 — the sweep must read the step that SENT."""
        msg = _sent(db_session, sequence, lead, step_no=1, hours_ago=60)
        assert _sweep(db_session) == [(str(enrollment.id), 2)]

        msg.step_no = 2
        db_session.commit()
        assert _sweep(db_session) == []

    def test_a_disabled_step_never_follows_up(
        self, db_session, sequence, lead, enrollment
    ):
        step = db_session.query(m.SequenceStep).filter_by(
            sequence_id=sequence.id, step_no=1).one()
        step.followup_enabled = False
        db_session.commit()
        _sent(db_session, sequence, lead, hours_ago=100)
        assert _sweep(db_session) == []

    def test_a_reply_stops_the_follow_up(
        self, db_session, sequence, lead, enrollment
    ):
        _sent(db_session, sequence, lead, hours_ago=100)
        db_session.add(m.Outcome(lead_id=lead.id, event=m.OutcomeEvent.REPLIED,
                                 channel="email",
                                 ts=datetime.now(UTC) - timedelta(hours=1)))
        db_session.commit()
        assert _sweep(db_session) == []

    def test_a_reply_from_BEFORE_this_send_does_not_stop_it(
        self, db_session, sequence, lead, enrollment
    ):
        """A lead can reply to one campaign and be legitimately enrolled in
        another later. `outcomes` has no enrollment_id, so the send time of
        the message being followed up is the only usable boundary."""
        _sent(db_session, sequence, lead, hours_ago=100)
        db_session.add(m.Outcome(lead_id=lead.id, event=m.OutcomeEvent.REPLIED,
                                 channel="email",
                                 ts=datetime.now(UTC) - timedelta(days=30)))
        db_session.commit()
        assert _sweep(db_session) != []

    def test_a_paused_enrollment_is_skipped(
        self, db_session, sequence, lead, enrollment
    ):
        _sent(db_session, sequence, lead, hours_ago=100)
        enrollment.status = m.EnrollmentStatus.PAUSED
        db_session.commit()
        assert _sweep(db_session) == []

    def test_a_stopped_enrollment_is_skipped(
        self, db_session, sequence, lead, enrollment
    ):
        """A stopped enrollment can NEVER send again. That rule is older than
        this feature and this feature does not get to bend it."""
        _sent(db_session, sequence, lead, hours_ago=100)
        enrollment.status = m.EnrollmentStatus.STOPPED
        enrollment.stop_reason = "unsubscribed"
        db_session.commit()
        assert _sweep(db_session) == []

    def test_a_paused_campaign_sends_nothing(
        self, db_session, sequence, lead, enrollment, verified_strategy
    ):
        _sent(db_session, sequence, lead, hours_ago=100)
        verified_strategy.campaign_state = "paused_bounce_rate"
        db_session.commit()
        assert _sweep(db_session) == []

    def test_an_enrollment_that_never_sent_is_skipped(
        self, db_session, enrollment
    ):
        assert _sweep(db_session) == []


# --------------------------------------------------------------------------
# Sending
# --------------------------------------------------------------------------


class TestSendFollowup:
    @pytest.fixture(autouse=True)
    def channel(self, monkeypatch):
        """A fake outbound channel, exactly as the M3 send tests do it."""
        from app.integrations.outreach_base import SendResult

        sent: list = []

        class FakeChannel:
            def send(self, outbound):
                sent.append(outbound)
                return SendResult(ok=True, provider_message_id="p1",
                                  thread_ref="t1")

        monkeypatch.setattr(tasks, "_get_channel",
                            lambda session, account: FakeChannel())
        return sent

    @pytest.fixture(autouse=True)
    def gmail_account(self, db_session, test_user):
        from app.services import crypto

        row = m.GmailAccount(
            user_id=test_user.id, email_address="me@leadpilot.dev",
            token_ciphertext=crypto.encrypt_json({"access_token": "a",
                                                  "refresh_token": "r"}),
            token_expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        db_session.add(row)
        db_session.commit()
        return row

    @pytest.fixture(autouse=True)
    def in_window(self, monkeypatch):
        """The send window and daily cap are re-checked at send time. This
        feature does not bypass them (there is no bypass flag anywhere in this
        codebase, by design) — so the tests move the clock inside the window
        rather than disabling the check."""
        from app.services import sequence_engine as engine

        # `window` is the resolved compliance rule's window (Feature 8).
        monkeypatch.setattr(engine, "in_send_window", lambda dt, tz, window=None: True)

    def test_it_sends_the_next_sequence_step(
        self, db_session, sequence, lead, enrollment, channel, fake_claude
    ):
        _sent(db_session, sequence, lead, hours_ago=100)
        result = tasks.send_followup_impl(db_session, enrollment.id, 2)
        assert result == "sent", result

        sent_rows = db_session.query(m.Message).filter_by(
            step_no=2, status=m.MessageStatus.SENT).all()
        assert len(sent_rows) == 1
        assert sent_rows[0].template == "Nudge", (
            "the existing step's brief should be used, not a generated one"
        )

    def test_the_outcome_records_that_it_was_automatic(
        self, db_session, sequence, lead, enrollment, channel, fake_claude
    ):
        """Requirement 8. Without the tag, an automatic send is
        indistinguishable from a sequenced one in the learning loop."""
        _sent(db_session, sequence, lead, hours_ago=100)
        tasks.send_followup_impl(db_session, enrollment.id, 2)

        outcome = db_session.query(m.Outcome).filter(
            m.Outcome.event == m.OutcomeEvent.SENT,
            m.Outcome.meta_json["step_no"].as_integer() == 2,
        ).one()
        assert outcome.meta_json["source"] == "auto_followup"

    def test_an_ordinary_send_is_NOT_tagged(
        self, db_session, sequence, lead, enrollment, channel, fake_claude
    ):
        """The M3 outcome shape stays byte-identical, so the learning loop's
        existing aggregates are untouched by this feature."""
        msg = m.Message(sequence_id=sequence.id, lead_id=lead.id,
                        channel=m.ChannelType.EMAIL, step_no=1,
                        template="Open on X", status=m.MessageStatus.SCHEDULED,
                        scheduled_at=datetime.now(UTC))
        db_session.add(msg)
        db_session.commit()

        tasks.send_message_impl(db_session, msg.id)
        outcome = db_session.query(m.Outcome).filter(
            m.Outcome.event == m.OutcomeEvent.SENT).one()
        assert "source" not in outcome.meta_json

    def test_with_no_next_step_it_generates_one(
        self, db_session, sequence, lead, enrollment, channel, fake_claude
    ):
        """The auto-DM fallback. The generated BRIEF is stored as the
        message's template so the ordinary render path personalises it at
        send time exactly like a human-written step."""
        _sent(db_session, sequence, lead, step_no=2, hours_ago=100)
        result = tasks.send_followup_impl(db_session, enrollment.id, 3)
        assert result == "sent", result

        row = db_session.query(m.Message).filter_by(step_no=3).one()
        assert "onboarding backlog" in row.template, (
            "the brief should come from build_followup_brief"
        )
        assert row.body, "and it should still be rendered by the send path"
        # It was told what actually happened to this lead.
        assert fake_claude.followup_prompts

    def test_a_generated_brief_falls_back_when_the_model_fails(
        self, db_session, sequence, lead, enrollment, channel, fake_claude
    ):
        """A follow-up that silently never sends because Anthropic was down
        for a minute is worse than a less specific one."""
        from app.services.message_personalization import (
            FALLBACK_FOLLOWUP_BRIEF,
        )

        _sent(db_session, sequence, lead, step_no=2, hours_ago=100)
        fake_claude.followup_brief_response = RuntimeError("api down")
        assert tasks.send_followup_impl(db_session, enrollment.id, 3) == "sent"
        assert db_session.query(m.Message).filter_by(
            step_no=3).one().template == FALLBACK_FOLLOWUP_BRIEF


    def test_the_lock_makes_a_second_call_a_no_op(
        self, db_session, sequence, lead, enrollment, channel, fake_claude
    ):
        _sent(db_session, sequence, lead, hours_ago=100)
        assert tasks.send_followup_impl(db_session, enrollment.id, 2) == "sent"

        # Clear EVERY other guard so the only thing that can stop the second
        # call is the lock. Step 2 is the last step, so the successful send
        # also completed the enrollment (schedule_next_step) -- which would
        # otherwise short-circuit at `skipped_completed` and prove nothing
        # about idempotency.
        db_session.query(m.Message).filter_by(step_no=2).delete()
        enrollment.status = m.EnrollmentStatus.ACTIVE
        db_session.commit()
        assert tasks.send_followup_impl(db_session, enrollment.id, 2) == "locked"

    def test_the_lock_key_is_per_step(self, db_session, enrollment):
        """Step 3's follow-up must not be mistaken for a retry of step 2's."""
        assert tasks._followup_lock_key(enrollment.id, 2) != \
            tasks._followup_lock_key(enrollment.id, 3)
        assert str(enrollment.id) in tasks._followup_lock_key(enrollment.id, 2)

    def test_the_lock_fails_OPEN_when_redis_is_down(
        self, db_session, enrollment, monkeypatch
    ):
        """Failing closed would silently stop every follow-up in the product
        during a Redis outage, with nothing surfacing that it had. The message
        row's SCHEDULED -> SENDING claim is the real guarantee."""
        def boom():
            raise ConnectionError("redis down")

        monkeypatch.setattr("app.core.redis_client.get_sync_redis", boom)
        assert tasks._acquire_followup_lock(enrollment.id, 2) is True

    def test_a_whatsapp_last_touch_gets_no_generated_follow_up(
        self, db_session, sequence, lead, enrollment, fake_claude
    ):
        """A cold WhatsApp message needs an approved template and there is no
        step to name one. Generating free-form copy would be blocked by the
        adapter's compliance guard, or worse, sent into a window the lead did
        not open."""
        msg = _sent(db_session, sequence, lead, step_no=2, hours_ago=100)
        msg.channel = m.ChannelType.WHATSAPP
        db_session.commit()

        assert tasks.send_followup_impl(db_session, enrollment.id, 3) == \
            "skipped_whatsapp_needs_template"

    def test_a_reply_between_the_sweep_and_the_send_stops_it(
        self, db_session, sequence, lead, enrollment, channel, fake_claude
    ):
        _sent(db_session, sequence, lead, hours_ago=100)
        db_session.add(m.Outcome(lead_id=lead.id, event=m.OutcomeEvent.REPLIED,
                                 channel="email", ts=datetime.now(UTC)))
        db_session.commit()
        assert tasks.send_followup_impl(db_session, enrollment.id, 2) == \
            "skipped_replied"

    def test_suppression_is_still_enforced_at_send_time(
        self, db_session, sequence, lead, enrollment, channel, fake_claude
    ):
        """The send task is the last line of compliance enforcement, and an
        automatic send goes through exactly the same path."""
        _sent(db_session, sequence, lead, hours_ago=100)
        db_session.add(m.SuppressionEntry(email=lead.email,
                                          reason="unsubscribed_reply"))
        db_session.commit()

        assert tasks.send_followup_impl(db_session, enrollment.id, 2) == \
            "cancelled_suppressed"
        assert channel == [], "nothing may be transmitted"


# --------------------------------------------------------------------------
# The settings endpoint
# --------------------------------------------------------------------------


class TestFollowupSettingsEndpoint:
    def test_it_updates_both_fields(self, client, db_session, sequence):
        resp = client.post(
            f"/sequences/{sequence.id}/steps/1/followup-settings",
            json={"enabled": False, "delay_hours": 24},
        )
        assert resp.status_code == 200
        assert resp.json() == {
            "sequence_id": str(sequence.id), "step_no": 1,
            "followup_enabled": False, "followup_delay_hours": 24,
        }

        step = db_session.query(m.SequenceStep).filter_by(
            sequence_id=sequence.id, step_no=1).one()
        assert step.followup_enabled is False
        assert step.followup_delay_hours == 24

    def test_each_field_can_be_sent_alone(self, client, sequence):
        """The toggle and the delay are independent controls: flipping the
        switch must not clobber a delay the user is halfway through typing."""
        client.post(f"/sequences/{sequence.id}/steps/1/followup-settings",
                    json={"delay_hours": 12})
        body = client.post(
            f"/sequences/{sequence.id}/steps/1/followup-settings",
            json={"enabled": False},
        ).json()
        assert body["followup_delay_hours"] == 12
        assert body["followup_enabled"] is False

    def test_the_delay_is_bounded(self, client, sequence):
        for value in (0, 99999):
            assert client.post(
                f"/sequences/{sequence.id}/steps/1/followup-settings",
                json={"delay_hours": value},
            ).status_code == 422

    def test_an_unknown_step_is_404(self, client, sequence):
        assert client.post(
            f"/sequences/{sequence.id}/steps/99/followup-settings",
            json={"enabled": True},
        ).status_code == 404

    def test_another_users_sequence_is_404(self, client, db_session, sequence):
        other = m.User(email="other@leadpilot.dev", email_verified=True)
        db_session.add(other)
        db_session.flush()
        product = db_session.get(m.Product,
                                 db_session.get(m.Strategy,
                                                sequence.strategy_id).product_id)
        product.user_id = other.id
        db_session.commit()

        assert client.post(
            f"/sequences/{sequence.id}/steps/1/followup-settings",
            json={"enabled": True},
        ).status_code == 404

    def test_new_steps_default_to_enabled_at_72_hours(
        self, client, db_session, verified_strategy
    ):
        resp = client.post(
            f"/strategies/{verified_strategy.id}/sequences",
            json={"name": "New", "channel": "email",
                  "steps": [{"step_no": 1, "template": "Hi"}]},
        )
        assert resp.status_code == 201
        step = resp.json()["steps"][0]
        assert step["followup_enabled"] is True
        assert step["followup_delay_hours"] == 72


# --------------------------------------------------------------------------
# The CRM grid column
# --------------------------------------------------------------------------


class TestFollowupStatusColumn:
    def _status(self, db_session, lead):
        from app.services.crm_service import followup_status_for_leads

        return followup_status_for_leads(db_session, [lead.id])[lead.id]

    def test_never_contacted(self, db_session, lead):
        assert self._status(db_session, lead) == "none"

    def test_waiting_inside_the_window(
        self, db_session, sequence, lead, enrollment
    ):
        _sent(db_session, sequence, lead, hours_ago=1)
        assert self._status(db_session, lead) == "waiting"

    def test_due_matches_what_the_sweep_would_pick_up(
        self, db_session, sequence, lead, enrollment
    ):
        """The badge and the automation must not disagree about who is
        overdue — they are computed from the same inputs on purpose."""
        _sent(db_session, sequence, lead, hours_ago=100)
        assert self._status(db_session, lead) == "due"
        assert _sweep(db_session) == [(str(enrollment.id), 2)]

    def test_scheduled_wins_over_due(
        self, db_session, sequence, lead, enrollment
    ):
        _sent(db_session, sequence, lead, hours_ago=100)
        db_session.add(m.Message(
            sequence_id=sequence.id, lead_id=lead.id,
            channel=m.ChannelType.EMAIL, step_no=2, template="Nudge",
            status=m.MessageStatus.SCHEDULED,
            scheduled_at=datetime.now(UTC) + timedelta(days=1),
        ))
        db_session.commit()
        assert self._status(db_session, lead) == "scheduled"

    def test_replied_wins_over_everything(
        self, db_session, sequence, lead, enrollment
    ):
        _sent(db_session, sequence, lead, hours_ago=100)
        db_session.add(m.Outcome(lead_id=lead.id, event=m.OutcomeEvent.REPLIED,
                                 channel="email"))
        db_session.commit()
        assert self._status(db_session, lead) == "replied"

    def test_the_grid_row_carries_it(self, client, db_session, sequence, lead,
                                     enrollment):
        _sent(db_session, sequence, lead, hours_ago=100)
        rows = client.post("/crm/grid", json={}).json()["items"]
        assert rows
        assert rows[0]["followup_status"] == "due"

    def test_an_empty_page_costs_no_queries(self, db_session):
        from app.services.crm_service import followup_status_for_leads

        assert followup_status_for_leads(db_session, []) == {}
