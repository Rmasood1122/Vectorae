"""Follow-ups and meeting drafts reply to the email we sent before.

Gmail threads a message only with threadId AND In-Reply-To / References AND
a matching Subject (developers.google.com/workspace/gmail/api, users.messages
/ "Managing threads"); other mail clients thread on In-Reply-To / References
alone (RFC 5322 section 3.6.4). Before migration 0096 nothing stored the
Message-ID of an email we sent, so a sequence follow-up or a meeting
follow-up draft carried a threadId only and opened as a new email in the
prospect's client.

The Message-ID is read back from Gmail after the send (users.messages.get,
format=metadata), not set by us: nothing reviewed confirms that Gmail keeps
a Message-ID the sender supplies.
"""

from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from app.db import models as m
from app.integrations import gmail
from app.integrations.outreach_base import OutboundMessage
from app.services import meeting_followup
from app.workers import outreach_tasks
from tests.conftest import NOW

PARENT = "<CAparent123@mail.gmail.com>"
GRANDPARENT = "<CAfirst001@mail.gmail.com>"


def _out():
    return OutboundMessage(message_id="m1", lead_id="l1", to_address="lead@acme.test",
                           subject="Hi", body="Body", headers={})


def _channel(db_session, gmail_account, fake_redis, handler):
    channel = gmail.GmailChannel(
        account=gmail_account, session=db_session, redis=fake_redis,
        http=httpx.Client(base_url=gmail.GMAIL_API_BASE,
                          transport=httpx.MockTransport(handler)),
        max_retries=1, sleep=lambda s: None)
    channel._auth = lambda: {"headers": {"Authorization": "Bearer at"}}
    return channel


class TestSendReadsTheMessageIdBack:
    def test_the_sent_message_id_is_recorded(self, db_session, gmail_account, fake_redis):
        seen = []

        def handler(request):
            seen.append(request)
            if request.method == "POST":
                return httpx.Response(200, json={"id": "g1", "threadId": "t1"})
            return httpx.Response(200, json={"id": "g1", "payload": {"headers": [
                {"name": "Message-Id", "value": PARENT},
                {"name": "References", "value": GRANDPARENT}]}})

        result = _channel(db_session, gmail_account, fake_redis, handler).send(_out())
        assert result.ok and result.rfc_message_id == PARENT
        assert result.rfc_references == GRANDPARENT
        get = seen[1]
        assert get.url.path.endswith("/users/me/messages/g1")
        assert get.url.params.get("format") == "metadata"
        assert get.url.params.get_list("metadataHeaders") == ["Message-ID", "References"]

    def test_a_failed_read_back_never_fails_the_send(self, db_session, gmail_account,
                                                     fake_redis):
        posts = []

        def handler(request):
            if request.method == "POST":
                posts.append(request)
                return httpx.Response(200, json={"id": "g1", "threadId": "t1"})
            return httpx.Response(500, text="backend error")

        result = _channel(db_session, gmail_account, fake_redis, handler).send(_out())
        assert result.ok and result.provider_message_id == "g1"
        assert result.rfc_message_id is None
        assert len(posts) == 1  # the send itself is never repeated


def _sent(db_session, seq, lead, step, *, rfc_id=None, refs=None, thread="t1", days=0):
    msg = m.Message(sequence_id=seq.id, lead_id=lead.id, step_no=step,
                    channel=m.ChannelType.EMAIL, template=f"Step {step}", subject="Hi",
                    body="b", status=m.MessageStatus.SENT,
                    sent_at=NOW - timedelta(days=days), thread_ref=thread,
                    rfc_message_id=rfc_id, rfc_references=refs)
    db_session.add(msg)
    db_session.commit()
    return msg


class TestSequenceFollowUp:
    def test_a_follow_up_replies_to_the_previous_email(self, db_session, email_sequence,
                                                       verified_leads):
        lead = verified_leads[0]
        _sent(db_session, email_sequence, lead, 1, rfc_id=PARENT, refs=GRANDPARENT, days=3)
        step2 = _sent(db_session, email_sequence, lead, 2, thread=None)
        headers: dict = {}
        assert outreach_tasks._thread_ref_for_followup(db_session, step2, headers) == "t1"
        assert headers["In-Reply-To"] == PARENT
        assert headers["References"] == f"{GRANDPARENT} {PARENT}"

    def test_an_email_sent_before_0096_threads_as_before(self, db_session, email_sequence,
                                                         verified_leads):
        lead = verified_leads[1]
        _sent(db_session, email_sequence, lead, 1, days=3)
        step2 = _sent(db_session, email_sequence, lead, 2, thread=None)
        headers: dict = {}
        assert outreach_tasks._thread_ref_for_followup(db_session, step2, headers) == "t1"
        assert headers == {}

    def test_a_linkedin_step_in_between_is_skipped(self, db_session, email_sequence,
                                                  verified_leads):
        lead = verified_leads[0]
        _sent(db_session, email_sequence, lead, 1, rfc_id=PARENT, days=5)
        li = _sent(db_session, email_sequence, lead, 2, thread="li-chat-9", days=2)
        li.channel = m.ChannelType.LINKEDIN
        db_session.commit()
        step3 = _sent(db_session, email_sequence, lead, 3, thread=None)
        headers: dict = {}
        assert outreach_tasks._thread_ref_for_followup(db_session, step3, headers) == "t1"
        assert headers["In-Reply-To"] == PARENT

    def test_the_send_stores_what_the_channel_read_back(self, db_session, enrolled,
                                                        verified_leads, fake_channel):
        from app.integrations.outreach_base import SendResult

        lead = verified_leads[2]
        msg = db_session.execute(select(m.Message).where(
            m.Message.lead_id == lead.id)).scalar_one()
        fake_channel.result_queue.append(SendResult(
            ok=True, provider_message_id="g9", thread_ref="t9", rfc_message_id=PARENT,
            rfc_references=GRANDPARENT))
        outreach_tasks.send_message_impl(db_session, msg.id, channel=fake_channel, now=NOW)
        db_session.refresh(msg)
        assert (msg.rfc_message_id, msg.rfc_references) == (PARENT, GRANDPARENT)


class TestMeetingDraft:
    @pytest.fixture()
    def draft_calls(self, monkeypatch):
        calls = []

        def fake_create(db, account, **kwargs):
            calls.append(kwargs)
            return {"id": "d1", "message": {"id": "msg-1"}}

        monkeypatch.setattr(gmail, "create_draft", fake_create)
        return calls

    def _outcome(self, db_session, lead, user):
        row = m.MeetingOutcome(lead_id=lead.id, user_id=user.id,
                               outcome=m.MeetingOutcomeKind.INTERESTED,
                               followup_subject="Great speaking today",
                               followup_body="Thanks -- next step as agreed.")
        db_session.add(row)
        db_session.commit()
        return row

    def test_the_draft_replies_to_the_last_email(self, db_session, email_sequence,
                                                 verified_leads, test_user, gmail_account,
                                                 draft_calls):
        lead = verified_leads[0]
        _sent(db_session, email_sequence, lead, 1, rfc_id=PARENT, days=2)
        meeting_followup.save_gmail_draft(db_session, self._outcome(db_session, lead, test_user),
                                          user=test_user)
        assert draft_calls[0]["thread_ref"] == "t1"
        assert draft_calls[0]["reply_headers"] == {"In-Reply-To": PARENT,
                                                   "References": PARENT}

    def test_a_later_whatsapp_message_is_not_the_drafts_parent(
            self, db_session, email_sequence, verified_leads, test_user, gmail_account,
            draft_calls):
        lead = verified_leads[1]
        _sent(db_session, email_sequence, lead, 1, rfc_id=PARENT, days=3)
        wa = _sent(db_session, email_sequence, lead, 2, thread="15550100000", days=1)
        wa.channel = m.ChannelType.WHATSAPP
        db_session.commit()
        meeting_followup.save_gmail_draft(db_session, self._outcome(db_session, lead, test_user),
                                          user=test_user)
        assert draft_calls[0]["thread_ref"] == "t1"
        assert draft_calls[0]["reply_headers"]["In-Reply-To"] == PARENT

    def test_the_raw_draft_carries_the_headers(self):
        import base64

        raw = gmail._raw_message("me@x.test", "lead@acme.test", "Hi", "Body",
                                 {"In-Reply-To": PARENT, "References": PARENT})
        text = base64.urlsafe_b64decode(raw).decode()
        assert f"In-Reply-To: {PARENT}" in text and f"References: {PARENT}" in text
