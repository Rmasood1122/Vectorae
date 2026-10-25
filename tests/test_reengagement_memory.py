"""Part 1 Feature 7 — re-engagement memory ("not now" is not "never").

The properties that matter:
  * a date the PROSPECT named always beats any default,
  * the reason is quoted, never invented,
  * one reply can never produce two plans,
  * appropriateness is re-checked when the plan comes DUE, not when it was
    made — 90 days is long enough for everything to have changed,
  * a model outage costs nuance, never the plan.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.db import models as m
from app.services import reengagement_memory as memory
from tests.conftest import auth_headers

NOW = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)
TODAY = NOW.date()


@pytest.fixture()
def lead(db_session, verified_strategy):
    row = m.Lead(strategy_id=verified_strategy.id, source="apollo", external_id="RM1",
                 full_name="Sara Khan", title="Operations Manager",
                 company="Blaze Safety", email="sara@blaze.test",
                 status=m.LeadStatus.CONTACTED)
    db_session.add(row)
    db_session.commit()
    return row


def _reply(db_session, lead, **kwargs) -> m.InboundReply:
    reply = m.InboundReply(
        lead_id=lead.id, channel="email", from_address=lead.email,
        subject=kwargs.pop("subject", "Re: quick question"),
        body=kwargs.pop("body", "No budget until the new fiscal year — try us then."),
        received_at=NOW, intent_label=kwargs.pop("intent_label", "not_now"), **kwargs)
    db_session.add(reply)
    db_session.commit()
    return reply


# --------------------------------------------------------------------------
# Reading the reply
# --------------------------------------------------------------------------


class TestExtract:
    def test_the_model_reads_the_reason(self, db_session, fake_claude, lead):
        result = memory.extract(db_session, _reply(db_session, lead), today=TODAY)
        assert result["reason_kind"] == memory.BUDGET
        assert "fiscal year" in result["reason_text"]

    def test_todays_date_is_given_so_a_relative_date_can_resolve(
            self, db_session, fake_claude, lead):
        memory.extract(db_session, _reply(db_session, lead), today=TODAY)
        assert TODAY.isoformat() in fake_claude.not_now_prompts[-1]

    def test_a_named_future_date_is_kept(self, db_session, fake_claude, lead):
        fake_claude.not_now_response = {"reason_kind": "contract",
                                        "reason_text": "mid-contract until March",
                                        "return_on": "2027-03-01"}
        result = memory.extract(db_session, _reply(db_session, lead), today=TODAY)
        assert result["return_on"] == date(2027, 3, 1)

    def test_a_date_in_the_past_is_a_mis_resolution_and_is_dropped(
            self, db_session, fake_claude, lead):
        fake_claude.not_now_response = {"reason_kind": "timing", "reason_text": "busy",
                                        "return_on": "2020-01-01"}
        assert memory.extract(db_session, _reply(db_session, lead),
                              today=TODAY)["return_on"] is None

    def test_an_unknown_kind_falls_back_to_the_rules_not_to_a_guess(
            self, db_session, fake_claude, lead):
        fake_claude.not_now_response = {"reason_kind": "vibes", "reason_text": "",
                                        "return_on": None}
        result = memory.extract(db_session, _reply(db_session, lead), today=TODAY)
        assert result["reason_kind"] == memory.BUDGET      # "budget" is in the body
        assert result["reason_text"] is None


    def test_their_own_words_are_kept(self, db_session, fake_claude, lead):
        fake_claude.not_now_response = {"reason_kind": "budget",
                                        "reason_text": "Spend is frozen until April",
                                        "return_on": None}
        result = memory.extract(
            db_session, _reply(db_session, lead, body="Thanks. Spend is frozen until April."),
            today=TODAY)
        assert result["reason_text"] == "Spend is frozen until April"

    def test_a_model_outage_costs_nuance_not_the_plan(self, db_session, fake_claude, lead):
        fake_claude.not_now_response = RuntimeError("anthropic down")
        result = memory.extract(db_session, _reply(db_session, lead), today=TODAY)
        assert result["reason_kind"] == memory.BUDGET
        assert result["reason_text"] is None

    @pytest.mark.parametrize("body,kind", [
        ("We're locked into a contract until next year.", memory.CONTRACT),
        ("We're mid-way through a migration.", memory.PROJECT),
        ("Hiring a new head of ops first.", memory.HEADCOUNT),
        ("It's on the roadmap but not a priority.", memory.PRIORITY),
        ("Bad timing, swamped right now.", memory.TIMING),
        ("No thanks for now.", memory.UNSPECIFIED),
    ])
    def test_the_rule_fallback_recognises_the_common_shapes(
            self, db_session, fake_claude, lead, body, kind):
        fake_claude.not_now_response = RuntimeError("down")
        result = memory.extract(db_session, _reply(db_session, lead, body=body),
                                today=TODAY)
        assert result["reason_kind"] == kind


class TestDueDate:
    def test_a_date_the_prospect_named_beats_every_default(self):
        due, interval = memory.due_date_for(
            {"reason_kind": memory.BUDGET, "return_on": date(2026, 11, 2)},
            fallback_days=90, today=TODAY)
        assert due == date(2026, 11, 2)
        assert interval == (date(2026, 11, 2) - TODAY).days

    def test_each_reason_gets_its_own_interval(self):
        """One interval for every objection is what makes re-engagement feel
        like spam: a contract renews on a different clock from a busy month."""
        contract, _ = memory.due_date_for({"reason_kind": memory.CONTRACT,
                                           "return_on": None},
                                          fallback_days=90, today=TODAY)
        timing, _ = memory.due_date_for({"reason_kind": memory.TIMING, "return_on": None},
                                        fallback_days=90, today=TODAY)
        assert contract > timing

    def test_an_unspecified_reason_uses_the_configured_default(self):
        due, interval = memory.due_date_for({"reason_kind": memory.UNSPECIFIED,
                                             "return_on": None},
                                            fallback_days=45, today=TODAY)
        assert interval == 45
        assert due == TODAY + timedelta(days=45)

    def test_the_configured_interval_is_clamped_to_something_sane(self, db_session):
        from app.services import system_settings

        system_settings.set(db_session, "reengagement_memory_days", 2)
        assert memory.default_interval(db_session) == memory.MIN_INTERVAL_DAYS
        system_settings.set(db_session, "reengagement_memory_days", 9999)
        assert memory.default_interval(db_session) == memory.MAX_INTERVAL_DAYS


# --------------------------------------------------------------------------
# Making the plan
# --------------------------------------------------------------------------


class TestPlanForReply:
    def test_a_not_now_becomes_a_dated_return_visit(
            self, db_session, fake_claude, lead, product_with_strategy):
        plan = memory.plan_for_reply(db_session, _reply(db_session, lead), now=NOW)
        assert plan is not None
        assert plan.status == memory.SCHEDULED
        assert plan.reason_kind == memory.BUDGET
        assert plan.due_at.date() == TODAY + timedelta(days=90)

    def test_any_other_intent_produces_nothing(self, db_session, fake_claude, lead):
        for label in ("interested", "objection", "unsubscribe", "neutral", None):
            reply = _reply(db_session, lead, intent_label=label)
            assert memory.plan_for_reply(db_session, reply, now=NOW) is None

    def test_one_reply_can_never_produce_two_plans(
            self, db_session, fake_claude, lead, product_with_strategy):
        reply = _reply(db_session, lead)
        first = memory.plan_for_reply(db_session, reply, now=NOW)
        second = memory.plan_for_reply(db_session, reply, now=NOW)
        assert first.id == second.id
        assert db_session.query(m.ReengagementPlan).count() == 1

    def test_the_feature_can_be_turned_off(self, db_session, fake_claude, lead):
        from app.services import system_settings

        system_settings.set(db_session, "reengagement_memory_enabled", False)
        assert memory.plan_for_reply(db_session, _reply(db_session, lead), now=NOW) is None

    def test_the_date_is_mirrored_onto_the_crm_row(
            self, db_session, fake_claude, lead, product_with_strategy):
        """So the existing follow-up machinery, the lead list and the kanban
        show it without knowing this feature exists."""
        memory.plan_for_reply(db_session, _reply(db_session, lead), now=NOW)
        meta = db_session.query(m.CrmLeadMeta).filter_by(lead_id=lead.id).one()
        assert meta.next_action_at is not None

    def test_a_failure_never_raises_into_the_reply_path(
            self, db_session, fake_claude, lead, monkeypatch):
        monkeypatch.setattr(memory, "extract",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        assert memory.plan_for_reply(db_session, _reply(db_session, lead), now=NOW) is None

    def test_the_reply_task_creates_the_plan(
            self, db_session, fake_claude, lead, product_with_strategy):
        from app.workers.reply_tasks import process_inbound_reply_impl

        reply = _reply(db_session, lead, intent_label=None)
        fake_claude.reply_intent_response = {"label": "not_now", "confidence": 0.9,
                                             "reason": "no budget this year"}
        result = process_inbound_reply_impl(db_session, reply.id)
        assert result["reengagement_plan_id"] is not None


# --------------------------------------------------------------------------
# Coming back
# --------------------------------------------------------------------------


class TestStillAppropriate:
    def _plan(self, db_session, lead, **kwargs):
        plan = m.ReengagementPlan(lead_id=lead.id, strategy_id=lead.strategy_id,
                                  due_at=NOW - timedelta(days=1),
                                  reason_kind=memory.BUDGET, status=memory.SCHEDULED,
                                  **kwargs)
        db_session.add(plan)
        db_session.commit()
        return plan

    def test_an_ordinary_prospect_is_still_worth_a_visit(self, db_session, lead):
        assert memory.still_appropriate(db_session, self._plan(db_session, lead)) is None

    @pytest.mark.parametrize("status", [
        m.LeadStatus.DROPPED, m.LeadStatus.DISQUALIFIED,
        m.LeadStatus.CLOSED_LOST, m.LeadStatus.CLOSED_WON,
    ])
    def test_a_finished_prospect_is_not(self, db_session, lead, status):
        plan = self._plan(db_session, lead)
        lead.status = status
        db_session.commit()
        assert memory.still_appropriate(db_session, plan) is not None

    def test_a_prospect_who_moved_on_without_this_is_not(self, db_session, lead):
        plan = self._plan(db_session, lead)
        lead.status = m.LeadStatus.MEETING_BOOKED
        db_session.commit()
        assert "meeting_booked" in memory.still_appropriate(db_session, plan)

    def test_a_suppressed_prospect_is_not(self, db_session, lead):
        plan = self._plan(db_session, lead)
        db_session.add(m.SuppressionEntry(email=lead.email, reason="unsubscribed"))
        db_session.commit()
        assert "suppressed" in memory.still_appropriate(db_session, plan)

    def test_a_killed_prospect_is_not(self, db_session, lead):
        plan = self._plan(db_session, lead)
        lead.kill_signal = "bounced"
        db_session.commit()
        assert "bounced" in memory.still_appropriate(db_session, plan)


class TestSweep:
    def _due_plan(self, db_session, lead, test_user):
        plan = m.ReengagementPlan(lead_id=lead.id, user_id=test_user.id,
                                  strategy_id=lead.strategy_id,
                                  due_at=NOW - timedelta(days=1),
                                  reason_kind=memory.BUDGET,
                                  reason_text="no budget until the new fiscal year",
                                  status=memory.SCHEDULED)
        db_session.add(plan)
        db_session.commit()
        return plan

    def test_a_due_plan_becomes_due(self, db_session, fake_claude, lead, test_user):
        plan = self._due_plan(db_session, lead, test_user)
        counts = memory.run_due(db_session, now=NOW)
        db_session.refresh(plan)
        assert counts["due"] == 1
        assert plan.status == memory.DUE

    def test_a_plan_that_stopped_being_appropriate_is_cancelled_with_its_reason(
            self, db_session, fake_claude, lead, test_user):
        plan = self._due_plan(db_session, lead, test_user)
        lead.status = m.LeadStatus.CLOSED_WON
        db_session.commit()
        memory.run_due(db_session, now=NOW)
        db_session.refresh(plan)
        assert plan.status == memory.CANCELLED
        assert "closed_won" in plan.cancelled_reason

    def test_a_plan_that_is_not_due_yet_is_untouched(
            self, db_session, fake_claude, lead, test_user):
        plan = self._due_plan(db_session, lead, test_user)
        plan.due_at = NOW + timedelta(days=30)
        db_session.commit()
        assert memory.run_due(db_session, now=NOW)["checked"] == 0

    def test_auto_send_is_off_by_default(self, db_session, fake_claude, lead, test_user,
                                         email_sequence):
        """Most people want to read a nine-month-old promise before acting."""
        plan = self._due_plan(db_session, lead, test_user)
        memory.run_due(db_session, now=NOW)
        db_session.refresh(plan)
        assert plan.status == memory.DUE
        assert plan.message_id is None

    def test_with_auto_send_on_a_real_message_is_scheduled(
            self, db_session, fake_claude, lead, test_user, email_sequence):
        from app.services import system_settings

        system_settings.set(db_session, "reengagement_memory_auto_send", True)
        db_session.add(m.Message(sequence_id=email_sequence.id, lead_id=lead.id,
                                 channel=m.ChannelType.EMAIL, step_no=2, template="t",
                                 status=m.MessageStatus.SENT, sent_at=NOW,
                                 body="earlier"))
        db_session.commit()
        plan = self._due_plan(db_session, lead, test_user)
        memory.run_due(db_session, now=NOW)
        db_session.refresh(plan)
        assert plan.status == memory.SENT
        message = db_session.get(m.Message, plan.message_id)
        assert message.status is m.MessageStatus.SCHEDULED
        assert message.origin == memory.ORIGIN
        assert message.step_no == 3

    def test_with_nothing_to_send_from_the_plan_still_becomes_due(
            self, db_session, fake_claude, lead, test_user):
        from app.services import system_settings

        system_settings.set(db_session, "reengagement_memory_auto_send", True)
        plan = self._due_plan(db_session, lead, test_user)
        memory.run_due(db_session, now=NOW)
        db_session.refresh(plan)
        assert plan.status == memory.DUE

    def test_one_broken_plan_never_stops_the_others(
            self, db_session, fake_claude, lead, test_user, monkeypatch):
        self._due_plan(db_session, lead, test_user)
        monkeypatch.setattr(memory, "still_appropriate",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        assert memory.run_due(db_session, now=NOW)["failed"] == 1


class TestBrief:
    def test_the_brief_quotes_the_prospect(self, db_session, lead):
        plan = m.ReengagementPlan(lead_id=lead.id, due_at=NOW,
                                  reason_kind=memory.CONTRACT,
                                  reason_text="mid-contract until March",
                                  stated_return_on=date(2027, 3, 1))
        brief = memory.brief_for(plan, lead)
        assert "mid-contract until March" in brief
        assert "2027-03-01" in brief
        assert "Sara Khan" in brief

    def test_the_brief_is_still_usable_without_a_quote(self, db_session, lead):
        plan = m.ReengagementPlan(lead_id=lead.id, due_at=NOW,
                                  reason_kind=memory.UNSPECIFIED)
        brief = memory.brief_for(plan, lead)
        assert "no reason beyond bad timing" in brief

    def test_the_brief_forbids_pretending_this_is_a_first_contact(self, db_session, lead):
        plan = m.ReengagementPlan(lead_id=lead.id, due_at=NOW, reason_kind=memory.BUDGET)
        assert "first contact" in memory.brief_for(plan, lead)


class TestOutput:
    def test_a_prospect_named_date_is_distinguished_from_a_guessed_one(
            self, db_session, lead):
        guessed = m.ReengagementPlan(lead_id=lead.id, due_at=NOW,
                                     reason_kind=memory.BUDGET)
        named = m.ReengagementPlan(lead_id=lead.id, due_at=NOW,
                                   reason_kind=memory.CONTRACT,
                                   stated_return_on=date(2027, 3, 1))
        assert memory.plan_out(db_session, guessed)["date_from_prospect"] is False
        assert memory.plan_out(db_session, named)["date_from_prospect"] is True

    def test_every_reason_kind_has_a_label(self):
        assert set(memory.REASON_LABELS) == set(memory.REASON_KINDS)
        assert set(memory.INTERVAL_BY_KIND) == set(memory.REASON_KINDS)


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------


class TestApi:
    @pytest.fixture()
    def plan(self, db_session, lead, test_user):
        row = m.ReengagementPlan(lead_id=lead.id, user_id=test_user.id,
                                 strategy_id=lead.strategy_id,
                                 due_at=NOW + timedelta(days=90),
                                 reason_kind=memory.BUDGET,
                                 reason_text="no budget until the new fiscal year",
                                 status=memory.SCHEDULED)
        db_session.add(row)
        db_session.commit()
        return row

    def test_the_list_endpoint(self, client, db_session, plan, test_user):
        response = client.get("/reengagement/plans", headers=auth_headers(test_user))
        assert response.status_code == 200
        item = response.json()["items"][0]
        assert item["reason_label"] == "No budget right now"
        assert item["lead"]["full_name"] == "Sara Khan"

    def test_the_per_lead_endpoint(self, client, db_session, plan, lead, test_user):
        response = client.get(f"/leads/{lead.id}/reengagement-plans",
                              headers=auth_headers(test_user))
        assert response.status_code == 200
        assert len(response.json()) == 1

    def test_rescheduling(self, client, db_session, plan, test_user):
        response = client.post(f"/reengagement/plans/{plan.id}/reschedule",
                               json={"due_on": "2027-01-15"},
                               headers=auth_headers(test_user))
        assert response.status_code == 200
        assert response.json()["due_at"].startswith("2027-01-15")

    def test_rescheduling_into_the_past_is_refused(self, client, db_session, plan,
                                                   test_user):
        assert client.post(f"/reengagement/plans/{plan.id}/reschedule",
                           json={"due_on": "2020-01-01"},
                           headers=auth_headers(test_user)).status_code == 422

    def test_cancelling_needs_a_reason(self, client, db_session, plan, test_user):
        assert client.post(f"/reengagement/plans/{plan.id}/cancel", json={"reason": "x"},
                           headers=auth_headers(test_user)).status_code == 422

    def test_cancelling(self, client, db_session, plan, test_user):
        response = client.post(f"/reengagement/plans/{plan.id}/cancel",
                               json={"reason": "They went with a competitor"},
                               headers=auth_headers(test_user))
        assert response.json()["status"] == memory.CANCELLED

    def test_deciding_twice_is_a_conflict(self, client, db_session, plan, test_user):
        client.post(f"/reengagement/plans/{plan.id}/cancel", json={"reason": "no longer"},
                    headers=auth_headers(test_user))
        assert client.post(f"/reengagement/plans/{plan.id}/reschedule",
                           json={"due_on": "2027-01-15"},
                           headers=auth_headers(test_user)).status_code == 409

    def test_another_account_cannot_see_or_move_a_plan(self, client, db_session, plan):
        other = m.User(email="nosy-plan@test.dev", email_verified=True)
        db_session.add(other)
        db_session.commit()
        assert client.get("/reengagement/plans",
                          headers=auth_headers(other)).json()["total"] == 0
        assert client.post(f"/reengagement/plans/{plan.id}/cancel",
                           json={"reason": "not mine"},
                           headers=auth_headers(other)).status_code == 404
