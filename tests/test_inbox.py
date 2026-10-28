"""Part 1 Feature 6 — the unified cross-channel inbox.

The properties that matter:
  * threads are per PROSPECT, and channels merge into one of them,
  * machine mail is filed, never chased,
  * "handled" is explicit and per reply, so a reply answered outside Vectorae
    can be cleared and a second reply cannot be cleared by the first,
  * the needs-reply list is worked oldest first,
  * the badge counts people, not messages.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.db import models as m
from app.services import inbox
from tests.conftest import auth_headers

NOW = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)


def _lead(db_session, strategy, ext="I1", **kwargs) -> m.Lead:
    lead = m.Lead(strategy_id=strategy.id, source="apollo", external_id=ext,
                  full_name=kwargs.pop("full_name", "Sara Khan"),
                  title=kwargs.pop("title", "Operations Manager"),
                  company=kwargs.pop("company", "Blaze Safety"),
                  email=kwargs.pop("email", f"{ext.lower()}@blaze.test"),
                  status=m.LeadStatus.VERIFIED, **kwargs)
    db_session.add(lead)
    db_session.commit()
    return lead


def _reply(db_session, lead, **kwargs) -> m.InboundReply:
    reply = m.InboundReply(
        lead_id=lead.id,
        channel=kwargs.pop("channel", "email"),
        from_address=kwargs.pop("from_address", lead.email),
        subject=kwargs.pop("subject", "Re: quick question"),
        body=kwargs.pop("body", "Sounds interesting — can we talk?"),
        received_at=kwargs.pop("received_at", NOW), **kwargs)
    db_session.add(reply)
    db_session.commit()
    return reply


# --------------------------------------------------------------------------
# Who counts as a person
# --------------------------------------------------------------------------


class TestIsFromAPerson:
    def test_an_ordinary_reply_is(self, db_session, verified_strategy):
        assert inbox.is_from_a_person(_reply(db_session, _lead(db_session,
                                                               verified_strategy))) is True

    @pytest.mark.parametrize("routing", ["bounce", "out_of_office", "automated_response"])
    def test_machine_mail_is_not(self, db_session, verified_strategy, routing):
        lead = _lead(db_session, verified_strategy)
        assert inbox.is_from_a_person(
            _reply(db_session, lead, classification=routing)) is False

    def test_the_authenticity_verdict_also_counts(self, db_session, verified_strategy):
        lead = _lead(db_session, verified_strategy)
        assert inbox.is_from_a_person(
            _reply(db_session, lead, authenticity_kind="bot")) is False


# --------------------------------------------------------------------------
# Threading
# --------------------------------------------------------------------------


class TestThreads:
    def test_channels_merge_into_ONE_thread_per_prospect(
            self, db_session, verified_strategy, test_user):
        """The whole feature: a prospect who emailed and then replied on
        LinkedIn is one conversation, not two inboxes."""
        lead = _lead(db_session, verified_strategy)
        _reply(db_session, lead, channel="email", received_at=NOW - timedelta(days=2))
        _reply(db_session, lead, channel="linkedin", received_at=NOW)
        result = inbox.threads(db_session, test_user.id, filter="all")
        assert result["total"] == 1
        thread = result["items"][0]
        assert {c["channel"] for c in thread["channels"]} == {"email", "linkedin"}
        assert thread["reply_count"] == 2

    def test_the_channel_the_prospect_actually_uses_leads(
            self, db_session, verified_strategy, test_user):
        lead = _lead(db_session, verified_strategy)
        for _ in range(3):
            _reply(db_session, lead, channel="linkedin")
        _reply(db_session, lead, channel="email")
        thread = inbox.threads(db_session, test_user.id, filter="all")["items"][0]
        assert thread["channels"][0]["channel"] == "linkedin"

    def test_the_latest_reply_is_the_preview(self, db_session, verified_strategy,
                                             test_user):
        lead = _lead(db_session, verified_strategy)
        _reply(db_session, lead, body="older", received_at=NOW - timedelta(days=1))
        _reply(db_session, lead, body="newest", received_at=NOW)
        thread = inbox.threads(db_session, test_user.id, filter="all")["items"][0]
        assert thread["latest"]["preview"] == "newest"

    def test_a_prospect_with_nothing_inbound_is_not_in_the_inbox(
            self, db_session, verified_strategy, test_user):
        _lead(db_session, verified_strategy)
        assert inbox.threads(db_session, test_user.id, filter="all")["total"] == 0

    def test_another_accounts_prospects_are_invisible(
            self, db_session, verified_strategy, test_user):
        lead = _lead(db_session, verified_strategy)
        _reply(db_session, lead)
        other = m.User(email="stranger-inbox@test.dev", email_verified=True)
        db_session.add(other)
        db_session.commit()
        assert inbox.threads(db_session, other.id, filter="all")["total"] == 0

    def test_the_last_outbound_is_reported_per_thread(
            self, db_session, verified_strategy, email_sequence, test_user):
        lead = _lead(db_session, verified_strategy)
        _reply(db_session, lead)
        db_session.add(m.Message(sequence_id=email_sequence.id, lead_id=lead.id,
                                 channel=m.ChannelType.EMAIL, step_no=1, template="t",
                                 status=m.MessageStatus.SENT, sent_at=NOW))
        db_session.commit()
        thread = inbox.threads(db_session, test_user.id, filter="all")["items"][0]
        assert thread["last_outbound_at"] is not None


class TestNeedsReply:
    def test_an_unhandled_human_reply_needs_a_reply(
            self, db_session, verified_strategy, test_user):
        lead = _lead(db_session, verified_strategy)
        _reply(db_session, lead)
        result = inbox.threads(db_session, test_user.id)
        assert result["total"] == 1
        assert result["items"][0]["needs_reply"] is True

    def test_machine_mail_never_makes_a_thread_need_a_reply(
            self, db_session, verified_strategy, test_user):
        """A bounce is worth seeing and is never worth chasing."""
        lead = _lead(db_session, verified_strategy)
        _reply(db_session, lead, classification="bounce")
        assert inbox.threads(db_session, test_user.id)["total"] == 0
        assert inbox.threads(db_session, test_user.id, filter="all")["total"] == 1

    def test_handling_clears_the_thread(self, db_session, verified_strategy, test_user):
        lead = _lead(db_session, verified_strategy)
        reply = _reply(db_session, lead)
        inbox.mark_handled(db_session, [reply], actor=test_user, now=NOW)
        assert inbox.threads(db_session, test_user.id)["total"] == 0
        assert inbox.threads(db_session, test_user.id, filter="handled")["total"] == 1

    def test_a_second_reply_is_not_cleared_by_a_decision_about_the_first(
            self, db_session, verified_strategy, test_user):
        """Per-reply handling is the whole reason this is not a per-lead flag."""
        lead = _lead(db_session, verified_strategy)
        first = _reply(db_session, lead, body="first", received_at=NOW - timedelta(days=1))
        _reply(db_session, lead, body="second", received_at=NOW)
        inbox.mark_handled(db_session, [first], actor=test_user, now=NOW)
        thread = inbox.threads(db_session, test_user.id)["items"][0]
        assert thread["unhandled_count"] == 1

    def test_handling_is_reversible(self, db_session, verified_strategy, test_user):
        lead = _lead(db_session, verified_strategy)
        reply = _reply(db_session, lead)
        inbox.mark_handled(db_session, [reply], actor=test_user, now=NOW)
        assert inbox.mark_handled(db_session, [reply], handled=False) == 1
        assert inbox.threads(db_session, test_user.id)["total"] == 1

    def test_marking_an_already_handled_reply_changes_nothing(
            self, db_session, verified_strategy, test_user):
        lead = _lead(db_session, verified_strategy)
        reply = _reply(db_session, lead)
        inbox.mark_handled(db_session, [reply], actor=test_user, now=NOW)
        assert inbox.mark_handled(db_session, [reply], actor=test_user, now=NOW) == 0

    def test_an_outbound_message_does_NOT_clear_the_thread_on_its_own(
            self, db_session, verified_strategy, email_sequence, test_user):
        """Handling is explicit. Inferring it from a later send would clear a
        thread the moment the next sequence step went out."""
        lead = _lead(db_session, verified_strategy)
        _reply(db_session, lead, received_at=NOW - timedelta(days=1))
        db_session.add(m.Message(sequence_id=email_sequence.id, lead_id=lead.id,
                                 channel=m.ChannelType.EMAIL, step_no=2, template="t",
                                 status=m.MessageStatus.SENT, sent_at=NOW))
        db_session.commit()
        assert inbox.threads(db_session, test_user.id)["total"] == 1


class TestOrdering:
    def _three(self, db_session, strategy):
        for index, days in enumerate((5, 1, 3)):
            lead = _lead(db_session, strategy, ext=f"O{index}",
                         full_name=f"Lead {index}")
            _reply(db_session, lead, received_at=NOW - timedelta(days=days))

    def test_the_needs_reply_list_is_worked_oldest_first(
            self, db_session, verified_strategy, test_user):
        """An inbox worked newest-first leaves the longest-waiting replies at
        the bottom, and those are where a late answer costs the deal."""
        self._three(db_session, verified_strategy)
        names = [t["lead"]["full_name"]
                 for t in inbox.threads(db_session, test_user.id)["items"]]
        assert names == ["Lead 0", "Lead 2", "Lead 1"]

    def test_browsing_lists_are_newest_first(self, db_session, verified_strategy,
                                             test_user):
        self._three(db_session, verified_strategy)
        names = [t["lead"]["full_name"]
                 for t in inbox.threads(db_session, test_user.id, filter="all")["items"]]
        assert names == ["Lead 1", "Lead 2", "Lead 0"]

    def test_the_needs_reply_total_is_reported_whatever_the_filter(
            self, db_session, verified_strategy, test_user):
        self._three(db_session, verified_strategy)
        assert inbox.threads(db_session, test_user.id,
                             filter="all")["needs_reply_total"] == 3

    def test_paging(self, db_session, verified_strategy, test_user):
        self._three(db_session, verified_strategy)
        page = inbox.threads(db_session, test_user.id, limit=1, offset=1)
        assert len(page["items"]) == 1
        assert page["total"] == 3

    def test_filtering_by_channel(self, db_session, verified_strategy, test_user):
        lead = _lead(db_session, verified_strategy, ext="C1")
        _reply(db_session, lead, channel="linkedin")
        other = _lead(db_session, verified_strategy, ext="C2")
        _reply(db_session, other, channel="email")
        result = inbox.threads(db_session, test_user.id, channel="linkedin")
        assert result["total"] == 1
        assert result["items"][0]["lead"]["id"] == str(lead.id)


class TestBadge:
    def test_the_badge_counts_people_not_messages(
            self, db_session, verified_strategy, test_user):
        lead = _lead(db_session, verified_strategy)
        for _ in range(3):
            _reply(db_session, lead)
        assert inbox.unread_count(db_session, test_user.id) == 1

    def test_the_badge_ignores_machines_and_handled_threads(
            self, db_session, verified_strategy, test_user):
        lead = _lead(db_session, verified_strategy, ext="B1")
        _reply(db_session, lead, classification="bounce")
        second = _lead(db_session, verified_strategy, ext="B2")
        reply = _reply(db_session, second)
        inbox.mark_handled(db_session, [reply], actor=test_user, now=NOW)
        assert inbox.unread_count(db_session, test_user.id) == 0


class TestThreadDetail:
    def test_the_detail_reuses_the_lead_pages_timeline(
            self, db_session, verified_strategy, email_sequence, test_user):
        """Two implementations of 'merge these tables in time order' would
        eventually disagree about one prospect."""
        from app.services import conversation_thread

        lead = _lead(db_session, verified_strategy)
        _reply(db_session, lead)
        detail = inbox.thread_detail(db_session, lead)
        assert detail["items"] == conversation_thread.build_thread(db_session, lead)["items"]
        assert detail["inbox"]["needs_reply"] is True


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------


class TestApi:
    def test_the_inbox_lists_threads(self, client, db_session, verified_strategy,
                                     test_user):
        lead = _lead(db_session, verified_strategy)
        _reply(db_session, lead)
        response = client.get("/inbox", headers=auth_headers(test_user))
        assert response.status_code == 200
        assert response.json()["items"][0]["lead"]["full_name"] == "Sara Khan"

    def test_the_count_endpoint(self, client, db_session, verified_strategy, test_user):
        lead = _lead(db_session, verified_strategy)
        _reply(db_session, lead)
        assert client.get("/inbox/count",
                          headers=auth_headers(test_user)).json()["needs_reply"] == 1

    def test_an_unknown_filter_is_rejected(self, client, db_session, test_user):
        assert client.get("/inbox?filter=whatever",
                          headers=auth_headers(test_user)).status_code == 422

    def test_the_thread_endpoint_returns_the_whole_conversation(
            self, client, db_session, verified_strategy, test_user):
        lead = _lead(db_session, verified_strategy)
        _reply(db_session, lead)
        response = client.get(f"/inbox/{lead.id}", headers=auth_headers(test_user))
        assert response.status_code == 200
        assert response.json()["items"]
        assert response.json()["inbox"]["needs_reply"] is True

    def test_clearing_a_thread(self, client, db_session, verified_strategy, test_user):
        lead = _lead(db_session, verified_strategy)
        _reply(db_session, lead)
        response = client.post(f"/inbox/{lead.id}/handled", json={"handled": True},
                               headers=auth_headers(test_user))
        assert response.status_code == 200
        assert response.json()["needs_reply"] is False

    def test_un_clearing_a_thread(self, client, db_session, verified_strategy, test_user):
        lead = _lead(db_session, verified_strategy)
        _reply(db_session, lead)
        client.post(f"/inbox/{lead.id}/handled", json={"handled": True},
                    headers=auth_headers(test_user))
        response = client.post(f"/inbox/{lead.id}/handled", json={"handled": False},
                               headers=auth_headers(test_user))
        assert response.json()["needs_reply"] is True

    def test_clearing_one_reply(self, client, db_session, verified_strategy, test_user):
        lead = _lead(db_session, verified_strategy)
        reply = _reply(db_session, lead)
        response = client.post(f"/inbox/replies/{reply.id}/handled", json={},
                               headers=auth_headers(test_user))
        assert response.status_code == 200
        assert response.json()["handled_at"] is not None

    def test_another_account_cannot_read_or_clear_a_thread(
            self, client, db_session, verified_strategy):
        lead = _lead(db_session, verified_strategy)
        reply = _reply(db_session, lead)
        other = m.User(email="nosy-inbox@test.dev", email_verified=True)
        db_session.add(other)
        db_session.commit()
        assert client.get(f"/inbox/{lead.id}",
                          headers=auth_headers(other)).status_code == 404
        assert client.post(f"/inbox/{lead.id}/handled", json={},
                           headers=auth_headers(other)).status_code == 404
        assert client.post(f"/inbox/replies/{reply.id}/handled", json={},
                           headers=auth_headers(other)).status_code == 404
