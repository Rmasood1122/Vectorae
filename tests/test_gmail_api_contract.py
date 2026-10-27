"""The Gmail channel matches Google's current Gmail API docs, and never sends
an email twice.

Checked against developers.google.com/workspace/gmail/api on 2026-10-27:
  * guides/handle-errors: rateLimitExceeded / userRateLimitExceeded (403)
    and 429 are retried later; "You can't assume that a 200 response means
    the email was successfully sent", and a timeout or 5xx proves nothing
    either way.
  * users.messages.list returns ids only, paged by nextPageToken.

Founder choice (2026-10-27, "Never auto-retry a send"): a send that may have
reached Gmail is never repeated automatically; it is failed with a reason
that says to check the Sent folder.
"""

import json

import httpx
import pytest

from app.integrations import gmail
from app.integrations.outreach_base import OutboundMessage
from app.integrations.plumbing import BaseHttpAdapter, ExternalAPIError, OutcomeUnknown


def _out():
    return OutboundMessage(message_id="m1", lead_id="l1", to_address="lead@acme.test",
                           subject="Hi", body="Body", headers={})


def _channel(db_session, gmail_account, fake_redis, handler, max_retries=3):
    channel = gmail.GmailChannel(
        account=gmail_account, session=db_session, redis=fake_redis,
        http=httpx.Client(base_url=gmail.GMAIL_API_BASE,
                          transport=httpx.MockTransport(handler)),
        max_retries=max_retries, sleep=lambda s: None)
    # Token refresh is OAuth's job, tested elsewhere; never touch the network.
    channel._auth = lambda: {"headers": {"Authorization": "Bearer at"}}
    return channel


def _google_error(status, reason):
    return httpx.Response(status, json={"error": {
        "code": status, "errors": [{"reason": reason}], "message": reason}})


class _Adapter(BaseHttpAdapter):
    provider = "test"
    base_url = "https://provider.test"


def _adapter(fake_redis, handler):
    return _Adapter(redis=fake_redis, sleep=lambda s: None, max_retries=3,
                    http=httpx.Client(base_url="https://provider.test",
                                      transport=httpx.MockTransport(handler)))


# -- plumbing: a non-idempotent call ---------------------------------------

class TestNonIdempotentCall:
    def test_a_timeout_after_sending_is_not_retried(self, fake_redis):
        calls = []

        def handler(request):
            calls.append(request)
            raise httpx.ReadTimeout("no answer", request=request)

        with pytest.raises(OutcomeUnknown):
            _adapter(fake_redis, handler).call("POST", "/send", json_body={}, idempotent=False)
        assert len(calls) == 1

    def test_a_5xx_is_not_retried(self, fake_redis):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(503, text="busy")

        with pytest.raises(OutcomeUnknown) as exc:
            _adapter(fake_redis, handler).call("POST", "/send", json_body={}, idempotent=False)
        assert exc.value.status == 503 and len(calls) == 1

    def test_a_connection_never_opened_is_retried(self, fake_redis):
        calls = []

        def handler(request):
            calls.append(request)
            if len(calls) == 1:
                raise httpx.ConnectError("refused", request=request)
            return httpx.Response(200, json={"id": "ok"})

        data = _adapter(fake_redis, handler).call("POST", "/send", json_body={},
                                                  idempotent=False)
        assert data == {"id": "ok"} and len(calls) == 2

    def test_a_429_is_retried(self, fake_redis):
        calls = []

        def handler(request):
            calls.append(request)
            return (httpx.Response(429, headers={"Retry-After": "0"}) if len(calls) == 1
                    else httpx.Response(200, json={"id": "ok"}))

        _adapter(fake_redis, handler).call("POST", "/send", json_body={}, idempotent=False)
        assert len(calls) == 2

    def test_an_idempotent_call_still_retries_a_5xx(self, fake_redis):
        calls = []

        def handler(request):
            calls.append(request)
            return (httpx.Response(503) if len(calls) == 1
                    else httpx.Response(200, json={"ok": True}))

        _adapter(fake_redis, handler).call("GET", "/read")
        assert len(calls) == 2

    def test_the_error_keeps_the_providers_reason(self, fake_redis):
        def handler(request):
            return _google_error(403, "userRateLimitExceeded")

        with pytest.raises(ExternalAPIError) as exc:
            _adapter(fake_redis, handler).call("POST", "/send", json_body={})
        assert exc.value.provider_reason() == "userRateLimitExceeded"


# -- the Gmail send ----------------------------------------------------------

class TestGmailSend:
    def test_a_5xx_fails_the_message_once_and_says_why(self, db_session, gmail_account,
                                                       fake_redis):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(500, text="backend error")

        result = _channel(db_session, gmail_account, fake_redis, handler).send(_out())
        assert not result.ok and result.permanent_failure
        assert result.raw["outcome_unknown"] is True
        assert "Sent folder" in result.error
        assert len(calls) == 1

    def test_a_timeout_fails_the_message_once(self, db_session, gmail_account, fake_redis):
        calls = []

        def handler(request):
            calls.append(request)
            raise httpx.ReadTimeout("no answer", request=request)

        result = _channel(db_session, gmail_account, fake_redis, handler).send(_out())
        assert result.permanent_failure and result.raw["outcome_unknown"]
        assert len(calls) == 1

    @pytest.mark.parametrize("reason", ["rateLimitExceeded", "userRateLimitExceeded",
                                        "dailyLimitExceeded"])
    def test_a_quota_403_is_retried_later(self, db_session, gmail_account, fake_redis,
                                          reason):
        result = _channel(db_session, gmail_account, fake_redis,
                          lambda r: _google_error(403, reason)).send(_out())
        assert not result.ok and not result.permanent_failure
        assert result.raw["reason"] == reason

    def test_a_permission_403_is_permanent(self, db_session, gmail_account, fake_redis):
        result = _channel(db_session, gmail_account, fake_redis,
                          lambda r: _google_error(403, "insufficientPermissions")).send(_out())
        assert result.permanent_failure

    def test_a_400_is_permanent(self, db_session, gmail_account, fake_redis):
        result = _channel(db_session, gmail_account, fake_redis,
                          lambda r: _google_error(400, "invalidArgument")).send(_out())
        assert result.permanent_failure

    def test_a_429_through_every_retry_is_retried_later(self, db_session, gmail_account,
                                                        fake_redis):
        result = _channel(db_session, gmail_account, fake_redis,
                          lambda r: httpx.Response(429, headers={"Retry-After": "0"})
                          ).send(_out())
        assert not result.ok and not result.permanent_failure

    def test_success_still_reports_the_ids(self, db_session, gmail_account, fake_redis):
        result = _channel(db_session, gmail_account, fake_redis,
                          lambda r: httpx.Response(200, json={"id": "g1", "threadId": "t1"})
                          ).send(_out())
        assert result.ok and (result.provider_message_id, result.thread_ref) == ("g1", "t1")


# -- the inbox poll ------------------------------------------------------------

class TestInboxPaging:
    def test_every_page_is_read(self, db_session, gmail_account, fake_redis):
        listed = []

        def handler(request):
            path = request.url.path
            if path.endswith("/users/me/messages"):
                token = request.url.params.get("pageToken")
                listed.append(token)
                if token is None:
                    return httpx.Response(200, json={"messages": [{"id": "a"}, {"id": "b"}],
                                                     "nextPageToken": "P2"})
                return httpx.Response(200, json={"messages": [{"id": "c"}]})
            msg_id = path.rsplit("/", 1)[-1]
            return httpx.Response(200, json={"id": msg_id, "threadId": "t",
                                             "payload": {"headers": []}, "snippet": "hi"})

        found = _channel(db_session, gmail_account, fake_redis, handler).fetch_replies()
        assert [m.provider_message_id for m in found] == ["a", "b", "c"]
        assert listed == [None, "P2"]

    def test_the_cut_off_is_exact(self, db_session, gmail_account, fake_redis):
        from datetime import datetime, timezone

        seen = {}

        def handler(request):
            seen["q"] = request.url.params.get("q")
            return httpx.Response(200, json={})

        since = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)
        _channel(db_session, gmail_account, fake_redis, handler).fetch_replies(since=since)
        assert seen["q"] == f"in:inbox -from:me after:{int(since.timestamp())}"
