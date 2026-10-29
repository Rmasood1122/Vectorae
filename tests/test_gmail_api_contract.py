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


import httpx

from app.integrations import gmail


def _channel(db_session, gmail_account, fake_redis, handler, max_retries=3):
    channel = gmail.GmailChannel(
        account=gmail_account, session=db_session, redis=fake_redis,
        http=httpx.Client(base_url=gmail.GMAIL_API_BASE,
                          transport=httpx.MockTransport(handler)),
        max_retries=max_retries, sleep=lambda s: None)
    # Token refresh is OAuth's job, tested elsewhere; never touch the network.
    channel._auth = lambda: {"headers": {"Authorization": "Bearer at"}}
    return channel


# -- plumbing: a non-idempotent call ---------------------------------------


# -- the Gmail send ----------------------------------------------------------


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
