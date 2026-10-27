"""The LinkedIn channel matches Unipile's current docs.

Checked against developer.unipile.com on 2026-10-27 (no account or key used):
  * errors are {title, detail, type: "errors/...", status}; a LinkedIn limit
    on invites is 422 errors/cannot_resend_yet ("a temporary provider limit.
    Please try again later."); a disconnected or restricted account is
    errors/disconnected_account (401) or errors/account_restricted (403);
  * provider-limits: ~200 invites a week with Premium, ~150 without, and
    Unipile enforces none of it;
  * GET /users/{id}: network_distance FIRST_DEGREE..OUT_OF_NETWORK;
    invitation {type: SENT|RECEIVED, status};
  * GET /linkedin/inmail_balance: {premium, recruiter, sales_navigator};
  * webhooks: our own messages arrive as message_received too (compare
    account_info.user_id with sender.attendee_provider_id); a status change
    is {"AccountStatus": {account_id, account_type, message}}.

Founder choice (2026-10-27, "Retry later + our own caps").
"""

import json
from datetime import datetime, timezone

import httpx
import pytest

from app.db import models as m
from app.integrations import unipile_linkedin as up
from app.integrations.linkedin_channel import LinkedInChannel
from app.integrations.outreach_base import OutboundMessage
from app.services import credentials, linkedin_limits, system_settings
from tests.test_linkedin_channel import FakeUnipile, _signed, li_accounts  # noqa: F401

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)


def _client(monkeypatch, status, body):
    monkeypatch.setattr(up, "_http", lambda: httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(status, json=body))))
    return up.UnipileClient(api_key="k", dsn="api8.unipile.com:1")


def _error(status, error_type):
    return {"title": "x", "detail": "x", "type": error_type, "status": status}


# -- error classification ------------------------------------------------------

class TestErrors:
    @pytest.mark.parametrize("status, error_type", [
        (422, "errors/cannot_resend_yet"), (422, "errors/already_invited_recently"),
        (422, "errors/limit_exceeded"), (429, None)])
    def test_a_provider_limit_is_retried_later(self, monkeypatch, status, error_type):
        with pytest.raises(up.UnipileError) as exc:
            _client(monkeypatch, status, _error(status, error_type)).invite("p", "a", None)
        assert exc.value.retry_later and not exc.value.permanent
        assert exc.value.error_type == error_type

    @pytest.mark.parametrize("status, error_type", [
        (401, "errors/disconnected_account"), (403, "errors/account_restricted"),
        (401, "errors/invalid_credentials")])
    def test_an_account_problem_is_not_the_leads(self, monkeypatch, status, error_type):
        with pytest.raises(up.UnipileError) as exc:
            _client(monkeypatch, status, _error(status, error_type)).invite("p", "a", None)
        assert exc.value.account_fault and not exc.value.permanent

    def test_a_bad_request_is_permanent(self, monkeypatch):
        with pytest.raises(up.UnipileError) as exc:
            _client(monkeypatch, 400, _error(400, "errors/invalid_parameters")).invite("p", "a", None)
        assert exc.value.permanent


# -- the channel ---------------------------------------------------------------

def _invite_message():
    return OutboundMessage(message_id="m1", lead_id=None, to_address="li", body="Hi",
                           metadata={"action": "connect", "account_id": "acc-0",
                                     "provider_id": "pid-1"})


class TestChannel:
    def test_the_weekly_limit_does_not_fail_the_lead(self, db_session, li_accounts):  # noqa: F811
        fake = FakeUnipile()
        fake.fail_with = up.UnipileError("limit", status=422,
                                         error_type="errors/cannot_resend_yet")
        result = LinkedInChannel(db_session, fake).send(_invite_message())
        assert not result.ok and not result.permanent_failure
        assert result.raw["type"] == "errors/cannot_resend_yet"

    def test_a_disconnected_account_leaves_rotation(self, db_session, li_accounts):  # noqa: F811
        fake = FakeUnipile()
        fake.fail_with = up.UnipileError("gone", status=401,
                                         error_type="errors/disconnected_account")
        result = LinkedInChannel(db_session, fake).send(_invite_message())
        assert not result.permanent_failure
        db_session.refresh(li_accounts[0])
        assert li_accounts[0].status == "credentials_needed"

    def test_a_restricted_account_is_marked_restricted(self, db_session, li_accounts):  # noqa: F811
        fake = FakeUnipile()
        fake.fail_with = up.UnipileError("restricted", status=403,
                                         error_type="errors/account_restricted")
        LinkedInChannel(db_session, fake).send(_invite_message())
        db_session.refresh(li_accounts[0])
        assert li_accounts[0].status == "restricted"


# -- response parsing ------------------------------------------------------------

class TestParsing:
    def test_only_first_degree_is_connected(self, monkeypatch):
        client = _client(monkeypatch, 200, {"provider_id": "p", "network_distance": "DISTANCE_1"})
        assert client.profile("x", "a")["is_connected"] is False
        client = _client(monkeypatch, 200, {"provider_id": "p", "network_distance": "FIRST_DEGREE"})
        assert client.profile("x", "a")["is_connected"] is True

    def test_only_a_pending_invitation_we_sent_blocks(self, monkeypatch):
        received = {"invitation": {"type": "RECEIVED", "status": "PENDING"}}
        sent = {"invitation": {"type": "SENT", "status": "PENDING"}}
        assert _client(monkeypatch, 200, received).profile("x", "a")["invitation_pending"] is False
        assert _client(monkeypatch, 200, sent).profile("x", "a")["invitation_pending"] is True

    def test_an_inmail_balance_of_zero_is_zero(self, monkeypatch):
        body = {"object": "LinkedinInmailBalance", "premium": 0, "recruiter": None,
                "sales_navigator": None}
        assert _client(monkeypatch, 200, body).inmail_balance("a") == 0

    def test_a_sales_navigator_balance_is_read(self, monkeypatch):
        body = {"premium": None, "recruiter": None, "sales_navigator": 12}
        assert _client(monkeypatch, 200, body).inmail_balance("a") == 12


# -- caps and rotation ------------------------------------------------------------

class TestWeeklyCap:
    def _reserve_n(self, db_session, account, n):
        return [linkedin_limits.reserve(db_session, account, "connect", now=NOW)
                for _ in range(n)]

    def test_premium_stops_at_the_weekly_limit(self, db_session, li_accounts):  # noqa: F811
        system_settings.set(db_session, "linkedin_daily_connection_limit", 1000)
        system_settings.set(db_session, "linkedin_weekly_connection_limit", 5)
        premium = li_accounts[1]
        assert self._reserve_n(db_session, premium, 6) == [True] * 5 + [False]

    def test_without_premium_the_week_is_at_most_150(self, db_session, li_accounts):  # noqa: F811
        system_settings.set(db_session, "linkedin_weekly_connection_limit", 180)
        assert linkedin_limits.weekly_connection_limit(db_session, li_accounts[0]) == 150
        assert linkedin_limits.weekly_connection_limit(db_session, li_accounts[1]) == 180

    def test_a_refused_week_slot_gives_back_the_day_slot(self, db_session, li_accounts):  # noqa: F811
        system_settings.set(db_session, "linkedin_daily_connection_limit", 1000)
        system_settings.set(db_session, "linkedin_weekly_connection_limit", 1)
        account = li_accounts[1]
        self._reserve_n(db_session, account, 3)
        assert linkedin_limits.usage(account.id, NOW)["connect"] == 1

    def test_release_frees_the_week_slot(self, db_session, li_accounts):  # noqa: F811
        system_settings.set(db_session, "linkedin_weekly_connection_limit", 1)
        account = li_accounts[1]
        assert self._reserve_n(db_session, account, 1) == [True]
        linkedin_limits.release(account, "connect", now=NOW)
        assert self._reserve_n(db_session, account, 1) == [True]

    def test_an_account_needing_attention_is_not_picked(self, db_session, test_user,
                                                        li_accounts):  # noqa: F811
        li_accounts[0].status = "credentials_needed"
        db_session.commit()
        picked = linkedin_limits.pick_account(db_session, test_user.id, "connect", now=NOW)
        assert picked is not None and picked.id == li_accounts[1].id
        li_accounts[1].status = "restricted"
        db_session.commit()
        assert linkedin_limits.pick_account(db_session, test_user.id, "connect", now=NOW) is None


# -- webhook ----------------------------------------------------------------------

class TestWebhook:
    @pytest.fixture()
    def secret(self, db_session):
        credentials.set_system_secret(db_session, "unipile", "webhook_secret", "whsec")

    def test_our_own_message_is_not_a_reply(self, client, db_session, secret,
                                            li_accounts):  # noqa: F811
        resp = _signed(client, {"event": "message_received", "account_id": "acc-0",
                                "message_id": "own-1", "chat_id": "c", "message": "hello",
                                "account_info": {"user_id": "me-1"},
                                "sender": {"attendee_provider_id": "me-1"}})
        assert resp.json()["reason"] == "own message"
        assert db_session.query(m.InboundReply).count() == 0

    def test_account_status_is_recorded(self, client, db_session, secret,
                                        li_accounts):  # noqa: F811
        body = {"AccountStatus": {"account_id": "acc-0", "account_type": "LINKEDIN",
                                  "message": "CREDENTIALS"}}
        assert _signed(client, body).json()["status"] == "credentials_needed"
        db_session.refresh(li_accounts[0])
        assert li_accounts[0].status == "credentials_needed"
        body["AccountStatus"]["message"] = "RECONNECTED"
        assert _signed(client, body).json()["status"] == "ok"

    def test_the_same_status_twice_is_applied_twice(self, client, db_session, secret,
                                                    li_accounts):  # noqa: F811
        bad = {"AccountStatus": {"account_id": "acc-0", "message": "CREDENTIALS"}}
        good = {"AccountStatus": {"account_id": "acc-0", "message": "OK"}}
        for body, expected in [(bad, "credentials_needed"), (good, "ok"),
                               (bad, "credentials_needed")]:
            assert _signed(client, body).json()["status"] == expected

    def test_connecting_leaves_the_status_alone(self, client, db_session, secret,
                                                li_accounts):  # noqa: F811
        body = {"AccountStatus": {"account_id": "acc-0", "message": "CONNECTING"}}
        assert _signed(client, body).json()["status"] == "ok"
