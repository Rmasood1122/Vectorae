"""F04 Reply Copilot -- the per-account `answer_earlier_replies` setting.

Founder decision 2026-10-11 ("User decide this"). When a prospect replied
twice, the FIRST reply stopped the sequence and the second is not re-stopped.

  * default (True, the behaviour before the setting existed): an answer to
    the second reply may pass the stop the first reply caused;
  * strict (False): an answer passes only the stop caused by ITS OWN reply;
    an earlier reply that could have caused the same stop means it was that
    reply's stop (ties fail closed);
  * strict never refuses what the setting does not cover (the reply that
    caused the stop is answered as before);
  * the setting is per account (tenancy), round-trips through the API on its
    own (no deployment switch needed), and an empty PUT is 422;
  * auto-send does not queue an answer the strict send path would cancel.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.db import models as m
from app.services import reply_copilot, system_settings
from app.workers import reply_tasks

from .test_reply_copilot import BOOKING, _draft, _other_user, _row
from .test_reply_copilot_review_fixes import (  # noqa: F401  (pytest fixtures)
    _later_reply,
    channel,
    flag_on,
    gmail,
    sends,
    world,
)

UTC = timezone.utc


def _strict(db, user, on=False):
    reply_copilot.set_answer_earlier_replies(db, user.id, on)


def _answer(db, user, reply) -> m.Message:
    _draft(db, reply)
    draft = _row(db, reply)
    reply_copilot.approve(db, draft, actor=user)
    return db.get(m.Message, draft.message_id)


class TestDefault:
    def test_no_row_means_answer_earlier_replies(self, db_session, test_user):
        assert reply_copilot.account_settings(db_session, test_user.id) is None
        assert reply_copilot.answer_earlier_replies(db_session, test_user.id) is True

    def test_a_row_created_for_auto_send_defaults_to_true(self, db_session, test_user):
        system_settings.set(db_session, "reply_copilot_auto_send_allowed", True)
        reply_copilot.set_auto_send(db_session, test_user.id, True)
        assert reply_copilot.answer_earlier_replies(db_session, test_user.id) is True

    def test_default_answers_the_second_reply(self, db_session, world, test_user, fake_claude,
                                              flag_on, channel, gmail):
        second = _later_reply(db_session, world, "And what does it cost?", minutes=1)
        msg = _answer(db_session, test_user, second)
        assert reply_tasks.send_reply_draft_impl(db_session, msg.id) == "sent"
        assert len(channel) == 1


class TestStrict:
    def test_strict_cancels_the_answer_to_the_second_reply(
            self, db_session, world, test_user, fake_claude, flag_on, channel, gmail):
        _strict(db_session, test_user)
        second = _later_reply(db_session, world, "And what does it cost?", minutes=1)
        msg = _answer(db_session, test_user, second)
        assert reply_tasks.send_reply_draft_impl(db_session, msg.id) == "cancelled_stopped"
        assert channel == []
        db_session.refresh(msg)
        assert msg.status is m.MessageStatus.CANCELLED

    def test_strict_cancels_even_when_both_replies_have_the_same_class(
            self, db_session, world, test_user, fake_claude, flag_on, channel, gmail):
        """The first "interested" reply wrote replied_interested; the second
        "interested" reply could not have (stops are written once)."""
        _strict(db_session, test_user)
        second = _later_reply(db_session, world, "Yes, keen - send details", minutes=1,
                              classification="interested")
        msg = _answer(db_session, test_user, second)
        assert reply_tasks.send_reply_draft_impl(db_session, msg.id) == "cancelled_stopped"
        assert channel == []

    def test_strict_still_answers_the_reply_that_caused_the_stop(
            self, db_session, world, test_user, fake_claude, flag_on, channel, gmail):
        _strict(db_session, test_user)
        msg = _answer(db_session, test_user, world["reply"])
        assert reply_tasks.send_reply_draft_impl(db_session, msg.id) == "sent"
        assert len(channel) == 1

    def test_strict_ignores_an_earlier_reply_that_did_not_stop(
            self, db_session, world, test_user, fake_claude, flag_on, channel, gmail):
        """An out-of-office reply pauses, it never writes replied_interested,
        so the later interested reply is the one that stopped the sequence."""
        _strict(db_session, test_user)
        world["reply"].classification = "out_of_office"
        db_session.commit()
        second = _later_reply(db_session, world, "Back now - how does this work?", minutes=1,
                              classification="interested")
        world["enrollment"].stopped_at = datetime.now(UTC) + timedelta(minutes=2)
        db_session.commit()
        msg = _answer(db_session, test_user, second)
        assert reply_tasks.send_reply_draft_impl(db_session, msg.id) == "sent"

    def test_stop_explained_by_both_modes(self, db_session, world):
        first = world["reply"]
        second = _later_reply(db_session, world, "And what does it cost?", minutes=1)
        enrollment = world["enrollment"]
        assert reply_copilot.stop_explained_by(db_session, first, enrollment, strict=True)
        assert reply_copilot.stop_explained_by(db_session, first, enrollment, strict=False)
        assert reply_copilot.stop_explained_by(db_session, second, enrollment, strict=False)
        assert not reply_copilot.stop_explained_by(db_session, second, enrollment, strict=True)

    def test_switching_back_restores_the_default(self, db_session, world, test_user,
                                                 fake_claude, flag_on, channel, gmail):
        _strict(db_session, test_user)
        _strict(db_session, test_user, on=True)
        second = _later_reply(db_session, world, "And what does it cost?", minutes=1)
        msg = _answer(db_session, test_user, second)
        assert reply_tasks.send_reply_draft_impl(db_session, msg.id) == "sent"


class TestAutoSend:
    def _ready(self, db, world, user, reply):
        system_settings.set(db, "reply_copilot_auto_send_allowed", True)
        reply_copilot.set_auto_send(db, user.id, True)
        _draft(db, reply)
        draft = _row(db, reply)
        draft.channel, draft.intent, draft.meeting_time_request = "email", "positive", True
        draft.draft_body = f"Happy to - pick a time: {BOOKING}"
        draft.claim_flags_json = None
        db.commit()
        return draft

    def test_strict_auto_send_does_not_queue_a_doomed_answer(
            self, db_session, world, test_user, fake_claude, flag_on):
        second = _later_reply(db_session, world, "Can you send me a time?", minutes=1)
        draft = self._ready(db_session, world, test_user, second)
        generated = {"allowed_urls": [BOOKING]}
        assert reply_copilot.auto_send_decision(db_session, draft, generated) is None
        _strict(db_session, test_user)
        assert (reply_copilot.auto_send_decision(db_session, draft, generated)
                == "stop_not_from_this_reply")


class TestApiAndTenancy:
    def test_round_trip_without_the_deployment_switch(self, client, db_session, test_user,
                                                      flag_on):
        assert client.get("/reply-copilot/settings").json()["answer_earlier_replies"] is True
        r = client.put("/reply-copilot/settings", json={"answer_earlier_replies": False})
        assert r.status_code == 200, r.text
        assert r.json()["answer_earlier_replies"] is False
        assert r.json()["auto_send_meeting_time"] is False
        assert client.get("/reply-copilot/settings").json()["answer_earlier_replies"] is False

    def test_an_empty_put_is_422_and_the_old_body_still_works(self, client, flag_on):
        assert client.put("/reply-copilot/settings", json={}).status_code == 422
        r = client.put("/reply-copilot/settings", json={"auto_send_meeting_time": False})
        assert r.status_code == 200 and r.json()["answer_earlier_replies"] is True

    def test_a_refused_auto_send_change_does_not_half_apply(self, client, db_session,
                                                            test_user, flag_on):
        r = client.put("/reply-copilot/settings",
                       json={"auto_send_meeting_time": True, "answer_earlier_replies": False})
        assert r.status_code == 409
        assert reply_copilot.answer_earlier_replies(db_session, test_user.id) is True

    def test_the_setting_is_per_account(self, client, db_session, test_user, flag_on):
        other = _other_user(db_session)
        _strict(db_session, other)
        assert client.get("/reply-copilot/settings").json()["answer_earlier_replies"] is True
        client.put("/reply-copilot/settings", json={"answer_earlier_replies": False})
        _strict(db_session, other, on=True)
        assert reply_copilot.answer_earlier_replies(db_session, test_user.id) is False
        assert reply_copilot.answer_earlier_replies(db_session, other.id) is True

    def test_flag_off_404(self, client):
        assert client.put("/reply-copilot/settings",
                          json={"answer_earlier_replies": False}).status_code == 404
