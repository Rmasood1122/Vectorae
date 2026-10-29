"""The reply poll reads only threads Vectorae sent mail in (privacy policy §3).

The policy says "We do not read, store or use other email in your mailbox for
any other purpose". Before this guard, GmailChannel.fetch_replies downloaded
every inbox message not sent by the user (format=full) and
outreach_tasks._route_inbound stored each one as an InboundReply and sent its
text to the classifier, even when it matched no lead. Now the Gmail list
call's threadId decides first: a message is fetched only when its thread holds
a Message this account's owner sent. Other mail is never downloaded, so it can
never be stored or classified.
"""

import httpx
from sqlalchemy import select

from app.db import models as m
from app.integrations import gmail
from app.workers.outreach_tasks import poll_account_replies_impl
from tests.conftest import NOW


def _channel(db_session, gmail_account, fake_redis, handler):
    channel = gmail.GmailChannel(
        account=gmail_account, session=db_session, redis=fake_redis,
        http=httpx.Client(base_url=gmail.GMAIL_API_BASE,
                          transport=httpx.MockTransport(handler)),
        max_retries=1, sleep=lambda s: None)
    channel._auth = lambda: {"headers": {"Authorization": "Bearer at"}}
    return channel


def _first_message(db, lead):
    return db.execute(select(m.Message).where(m.Message.lead_id == lead.id)).scalar_one()


def _inbox(listing, fetched):
    """users.messages.list returns {id, threadId}; .get returns the message."""
    by_id = {s["id"]: s for s in listing}

    def handler(request):
        path = request.url.path
        if path.endswith("/users/me/messages"):
            return httpx.Response(200, json={"messages": listing})
        msg_id = path.rsplit("/", 1)[-1]
        fetched.append(msg_id)
        sender = "lead0@co0.com" if msg_id == "reply" else "friend@elsewhere.com"
        return httpx.Response(200, json={
            "id": msg_id, "threadId": by_id[msg_id]["threadId"],
            "payload": {"mimeType": "text/plain",
                        "headers": [{"name": "From", "value": sender},
                                    {"name": "Subject", "value": "Re: hello"}]},
            "snippet": "sounds great"})
    return handler


class TestOnlyVectoraeThreadsAreRead:
    def test_an_unrelated_inbox_email_is_not_downloaded_or_stored(
            self, db_session, enrolled, verified_leads, gmail_account,
            fake_claude, fake_redis):
        sent = _first_message(db_session, verified_leads[0])
        sent.thread_ref = "t-ours"
        sent.sender_ref = str(gmail_account.id)
        db_session.commit()
        fake_claude.reply_verdicts["sounds great"] = "interested"
        fetched: list[str] = []
        channel = _channel(db_session, gmail_account, fake_redis, _inbox(
            [{"id": "reply", "threadId": "t-ours"},
             {"id": "private", "threadId": "t-family"}], fetched))

        handled = poll_account_replies_impl(db_session, gmail_account,
                                            channel=channel, now=NOW)

        assert fetched == ["reply"], "the unrelated message must never be downloaded"
        assert handled == 1
        stored = db_session.execute(select(m.InboundReply)).scalars().all()
        assert [r.thread_ref for r in stored] == ["t-ours"]
        assert all(r.from_address != "friend@elsewhere.com" for r in stored)

    def test_another_accounts_thread_does_not_count_as_ours(
            self, db_session, enrolled, verified_leads, gmail_account, fake_redis):
        other_user = m.User(email="other@tenant.test", email_verified=True)
        db_session.add(other_user)
        db_session.flush()
        other_product = m.Product(user=other_user, name="Other", description="x",
                                  type=m.ProductType.SKILL)
        db_session.add(other_product)
        db_session.flush()
        other_strategy = m.Strategy(product_id=other_product.id,
                                    flow_type=m.FlowType.WITH_CLIENTS)
        db_session.add(other_strategy)
        db_session.flush()
        # A lead that now belongs to another account, with a sent message.
        moved = verified_leads[1]
        moved.strategy_id = other_strategy.id
        _first_message(db_session, moved).thread_ref = "t-theirs"
        db_session.commit()
        fetched: list[str] = []
        channel = _channel(db_session, gmail_account, fake_redis, _inbox(
            [{"id": "private", "threadId": "t-theirs"}], fetched))

        assert channel.fetch_replies() == []
        assert fetched == []

    def test_a_listing_without_thread_ids_reads_nothing(
            self, db_session, enrolled, gmail_account, fake_redis):
        fetched: list[str] = []
        handler = _inbox([{"id": "a", "threadId": None}], fetched)
        assert _channel(db_session, gmail_account, fake_redis, handler).fetch_replies() == []
        assert fetched == []


def test_the_one_off_counter_finds_stored_unrelated_email_and_deletes_nothing(
        db_session, enrolled, verified_leads, gmail_account):
    from scripts.count_unrelated_inbox_replies import count_unrelated

    _first_message(db_session, verified_leads[0]).thread_ref = "t-ours"
    for thread in ("t-ours", "t-family", None):
        db_session.add(m.InboundReply(account_ref=str(gmail_account.id), channel="email",
                                      thread_ref=thread, from_address="a@b.test",
                                      body="x", classification="question"))
    db_session.add(m.InboundReply(account_ref="not-a-uuid", channel="email",
                                  thread_ref="t-ours", from_address="a@b.test", body="x"))
    db_session.commit()

    counts = count_unrelated(db_session)

    assert counts[gmail_account.email_address] == 2
    assert sum(counts.values()) == 3
    assert len(db_session.execute(select(m.InboundReply)).scalars().all()) == 4
