"""Feature Group 5 — the LinkedIn outreach channel.

What must hold:
  * no account ever exceeds its daily connection ceiling; sends rotate to the
    least-used account, and a lead's later touches always come from the
    account that owns the relationship;
  * the connect / message / InMail decision follows the documented rules,
    InMail falls back to a connection request when no account can send one,
    and a message to someone not yet connected is skipped (never forced);
  * suppression covers LinkedIn profiles, from every opt-out path, at send
    time and at enrol time;
  * the webhook is authenticated (fail closed) and a reply is routed through
    the same stop rules as every other channel.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.db import models as m
from app.integrations.linkedin_channel import LinkedInChannel
from app.integrations.unipile_linkedin import UnipileError
from app.services import credentials, linkedin_limits, linkedin_outreach, system_settings
from app.workers import outreach_tasks

from .conftest import NOW, auth_headers, entitle_owner

UTC = timezone.utc


# --------------------------------------------------------------------------
# Fakes + fixtures
# --------------------------------------------------------------------------


class FakeUnipile:
    def __init__(self):
        self.profiles: dict[str, dict] = {}
        self.calls: list[tuple] = []
        self.accounts: dict[str, dict] = {}
        self.fail_with: UnipileError | None = None

    def profile(self, identifier, account_id):
        self.calls.append(("profile", identifier, account_id))
        return {"provider_id": f"pid-{identifier}", "is_premium": False,
                "is_connected": False, "invitation_pending": False, "name": "",
                **self.profiles.get(identifier, {})}

    def invite(self, provider_id, account_id, note):
        self.calls.append(("invite", provider_id, account_id, note))
        if self.fail_with:
            raise self.fail_with
        return {"invitation_id": "inv-1"}

    def start_chat(self, provider_id, account_id, text, *, inmail=False, subject=None):
        self.calls.append(("start_chat", provider_id, account_id, inmail, subject))
        return {"chat_id": "chat-1", "message_id": "msg-1"}

    def send_in_chat(self, chat_id, text):
        self.calls.append(("send_in_chat", chat_id))
        return {"message_id": "msg-2"}

    def account(self, account_id):
        if account_id not in self.accounts:
            raise UnipileError("not found", status=404)
        return self.accounts[account_id]

    def inmail_balance(self, account_id):
        return 5

    def hosted_link(self, **kwargs):
        self.calls.append(("hosted_link", kwargs))
        return "https://account.unipile.com/link/abc"

    def health(self):
        return True


@pytest.fixture()
def unipile(monkeypatch):
    fake = FakeUnipile()
    monkeypatch.setattr("app.integrations.unipile_linkedin.get_client",
                        lambda db, user_id=None: fake)
    return fake


@pytest.fixture()
def li_accounts(db_session, test_user):
    rows = []
    for i, premium in enumerate((False, True)):
        row = m.LinkedInAccount(user_id=test_user.id, unipile_account_id=f"acc-{i}",
                                display_name=f"Account {i}", has_premium=premium,
                                inmail_credits=3 if premium else None)
        db_session.add(row)
        rows.append(row)
    db_session.commit()
    return rows


@pytest.fixture()
def li_sequence(db_session, verified_strategy):
    # Growth is the cheapest plan with LinkedIn; the send path re-checks the
    # plan (app/services/plan_holds.py). This file is about LinkedIn, not plans.
    entitle_owner(db_session, verified_strategy, m.PlanTier.GROWTH)
    seq = m.Sequence(strategy_id=verified_strategy.id, channel=m.ChannelType.LINKEDIN,
                     name="LinkedIn", status=m.SequenceStatus.ACTIVE)
    db_session.add(seq)
    db_session.flush()
    db_session.add(m.SequenceStep(sequence_id=seq.id, step_no=1, template="connect brief",
                                  delay_days=0, linkedin_action="auto"))
    db_session.add(m.SequenceStep(sequence_id=seq.id, step_no=2, template="follow up",
                                  delay_days=0, linkedin_action="message"))
    db_session.commit()
    return seq


def _li_lead(db_session, strategy, i, **kw):
    lead = m.Lead(strategy_id=strategy.id, source="apollo", external_id=f"LI{i}",
                  full_name=f"Li Lead {i}", company="Acme", status=m.LeadStatus.VERIFIED,
                  linkedin_url=f"https://www.linkedin.com/in/li-lead-{i}/", **kw)
    db_session.add(lead)
    db_session.commit()
    return lead


def _message(db_session, seq, lead, step_no=1):
    from app.services import sequence_engine as engine

    enrollment = m.SequenceEnrollment(sequence_id=seq.id, lead_id=lead.id)
    db_session.add(enrollment)
    db_session.flush()
    step = next(s for s in seq.steps if s.step_no == step_no)
    return engine._schedule_step_message(db_session, seq, enrollment, lead, step, base_time=NOW)


@pytest.fixture()
def configured(db_session):
    credentials.set_system_secret(db_session, "unipile", "api_key", "k")
    credentials.set_system_secret(db_session, "unipile", "dsn", "api8.unipile.com:1")


def _send(db_session, message, channel):
    return outreach_tasks.send_message_impl(db_session, message.id, channel=channel, now=NOW)


class RecordingChannel:
    """Stands in for LinkedInChannel: records OutboundMessages."""

    def __init__(self):
        self.sent = []

    def send(self, outbound):
        from app.integrations.outreach_base import SendResult
        self.sent.append(outbound)
        action = outbound.metadata["action"]
        return SendResult(ok=True, provider_message_id=f"p{len(self.sent)}",
                          thread_ref=None if action == "connect" else "chat-9")


# --------------------------------------------------------------------------
# Limits + rotation
# --------------------------------------------------------------------------


class TestLimits:
    def test_connection_ceiling_and_release(self, db_session, li_accounts):
        system_settings.set(db_session, "linkedin_daily_connection_limit", 2)
        acc = li_accounts[0]
        assert linkedin_limits.reserve(db_session, acc, "connect", now=NOW)
        assert linkedin_limits.reserve(db_session, acc, "connect", now=NOW)
        assert not linkedin_limits.reserve(db_session, acc, "connect", now=NOW)
        assert linkedin_limits.usage(acc.id, NOW)["connect"] == 2
        linkedin_limits.release(acc, "connect", now=NOW)
        assert linkedin_limits.usage(acc.id, NOW)["connect"] == 1
        # A new UTC day is a fresh counter.
        assert linkedin_limits.reserve(db_session, acc, "connect", now=NOW + timedelta(days=1))

    def test_rotation_picks_least_used_then_exhausts(self, db_session, li_accounts, test_user):
        system_settings.set(db_session, "linkedin_daily_connection_limit", 1)
        first = linkedin_limits.pick_account(db_session, test_user.id, "connect", now=NOW)
        second = linkedin_limits.pick_account(db_session, test_user.id, "connect", now=NOW)
        assert {first.id, second.id} == {a.id for a in li_accounts}
        assert linkedin_limits.pick_account(db_session, test_user.id, "connect", now=NOW) is None

    def test_relationship_pin_and_inmail_needs_premium(self, db_session, li_accounts, test_user):
        plain, premium = li_accounts
        got = linkedin_limits.pick_account(db_session, test_user.id, "message",
                                           required_account_id=plain.id, now=NOW)
        assert got.id == plain.id
        assert linkedin_limits.pick_account(db_session, test_user.id, "inmail",
                                            now=NOW).id == premium.id
        premium.inmail_credits = 0
        db_session.commit()
        assert linkedin_limits.pick_account(db_session, test_user.id, "inmail", now=NOW) is None

    def test_inmail_counts_against_the_message_ceiling(self, db_session, li_accounts):
        system_settings.set(db_session, "linkedin_daily_message_limit", 1)
        premium = li_accounts[1]
        assert linkedin_limits.reserve(db_session, premium, "inmail", now=NOW)
        assert not linkedin_limits.reserve(db_session, premium, "message", now=NOW)
        assert linkedin_limits.usage(premium.id, NOW) == {"connect": 0, "message": 1, "inmail": 1}


@pytest.mark.parametrize("configured_action,status,premium,expected", [
    ("auto", None, False, "connect"),
    ("auto", None, True, "inmail"),
    ("auto", "pending", True, linkedin_outreach.WAIT),
    ("auto", "connected", False, "message"),
    ("connect", "connected", False, "message"),
    ("connect", "pending", False, linkedin_outreach.WAIT),
    ("message", None, False, linkedin_outreach.WAIT),
    ("message", "connected", False, "message"),
    ("inmail", None, False, "inmail"),
    (None, None, False, "connect"),
])
def test_resolve_action(configured_action, status, premium, expected):
    lead = m.Lead(linkedin_connection_status=status, linkedin_is_premium=premium)
    assert linkedin_outreach.resolve_action(configured_action, lead) == expected


# --------------------------------------------------------------------------
# The send path
# --------------------------------------------------------------------------


class TestSendPath:
    def test_first_touch_is_a_connection_request(self, db_session, verified_strategy,
                                                 li_sequence, li_accounts, unipile,
                                                 configured, fake_claude):
        lead = _li_lead(db_session, verified_strategy, 1)
        msg = _message(db_session, li_sequence, lead)
        channel = RecordingChannel()
        assert _send(db_session, msg, channel) == "sent"
        out = channel.sent[0]
        assert out.metadata["action"] == "connect"
        assert out.metadata["provider_id"] == "pid-li-lead-1"
        assert len(out.body) <= 300
        db_session.refresh(lead)
        db_session.refresh(msg)
        assert lead.linkedin_connection_status == "pending"
        assert lead.linkedin_account_id == msg.linkedin_account_id
        assert msg.linkedin_action == "connect" and msg.channel is m.ChannelType.LINKEDIN
        assert linkedin_limits.usage(msg.linkedin_account_id, NOW)["connect"] == 1
        assert db_session.query(m.Outcome).filter_by(
            event=m.OutcomeEvent.SENT, channel="linkedin").count() == 1

    def test_premium_lead_gets_inmail_from_a_premium_account(
            self, db_session, verified_strategy, li_sequence, li_accounts, unipile,
            configured, fake_claude):
        lead = _li_lead(db_session, verified_strategy, 2)
        unipile.profiles["li-lead-2"] = {"is_premium": True}
        msg = _message(db_session, li_sequence, lead)
        channel = RecordingChannel()
        _send(db_session, msg, channel)
        assert channel.sent[0].metadata["action"] == "inmail"
        assert channel.sent[0].subject == "Quick question"
        premium = li_accounts[1]
        db_session.refresh(premium)
        assert premium.inmail_sent_total == 1 and premium.inmail_credits == 2
        db_session.refresh(lead)
        assert lead.linkedin_chat_id == "chat-9"

    def test_inmail_falls_back_to_connect_without_credits(
            self, db_session, verified_strategy, li_sequence, li_accounts, unipile,
            configured, fake_claude):
        li_accounts[1].inmail_credits = 0
        db_session.commit()
        lead = _li_lead(db_session, verified_strategy, 3)
        unipile.profiles["li-lead-3"] = {"is_premium": True}
        channel = RecordingChannel()
        _send(db_session, _message(db_session, li_sequence, lead), channel)
        assert channel.sent[0].metadata["action"] == "connect"

    def test_message_step_waits_for_the_connection(self, db_session, verified_strategy,
                                                   li_sequence, li_accounts, unipile,
                                                   configured, fake_claude):
        lead = _li_lead(db_session, verified_strategy, 4)
        msg = _message(db_session, li_sequence, lead, step_no=2)
        assert _send(db_session, msg, RecordingChannel()) == "skipped_linkedin_not_connected"
        db_session.refresh(msg)
        assert msg.status is m.MessageStatus.SKIPPED

    def test_connected_lead_gets_a_message_from_the_relationship_account(
            self, db_session, verified_strategy, li_sequence, li_accounts, unipile,
            configured, fake_claude):
        pinned = li_accounts[1]
        lead = _li_lead(db_session, verified_strategy, 5, linkedin_account_id=pinned.id,
                        linkedin_connection_status="connected")
        channel = RecordingChannel()
        _send(db_session, _message(db_session, li_sequence, lead, step_no=2), channel)
        assert channel.sent[0].metadata["action"] == "message"
        assert channel.sent[0].metadata["account_id"] == pinned.unipile_account_id

    def test_disconnected_relationship_account_fails_clearly(
            self, db_session, verified_strategy, li_sequence, li_accounts, unipile,
            configured, fake_claude):
        pinned = li_accounts[0]
        pinned.is_active = False
        lead = _li_lead(db_session, verified_strategy, 6, linkedin_account_id=pinned.id,
                        linkedin_connection_status="connected")
        msg = _message(db_session, li_sequence, lead, step_no=2)
        assert _send(db_session, msg, RecordingChannel()) == "failed_linkedin_account_gone"

    def test_ceiling_defers_never_drops(self, db_session, verified_strategy, li_sequence,
                                        li_accounts, unipile, configured, fake_claude):
        system_settings.set(db_session, "linkedin_daily_connection_limit", 0)
        lead = _li_lead(db_session, verified_strategy, 7)
        msg = _message(db_session, li_sequence, lead)
        assert _send(db_session, msg, RecordingChannel()) == "deferred_cap"
        db_session.refresh(msg)
        assert msg.status is m.MessageStatus.SCHEDULED
        scheduled = msg.scheduled_at if msg.scheduled_at.tzinfo else \
            msg.scheduled_at.replace(tzinfo=UTC)      # SQLite returns naive
        assert scheduled > NOW

    def test_not_configured_or_no_account(self, db_session, verified_strategy, li_sequence,
                                          unipile, fake_claude, monkeypatch):
        lead = _li_lead(db_session, verified_strategy, 8)
        monkeypatch.setattr("app.integrations.unipile_linkedin.get_client",
                            lambda db, user_id=None: None)
        assert _send(db_session, _message(db_session, li_sequence, lead),
                     RecordingChannel()) == "failed_linkedin_not_configured"
        monkeypatch.setattr("app.integrations.unipile_linkedin.get_client",
                            lambda db, user_id=None: unipile)
        lead2 = _li_lead(db_session, verified_strategy, 9)
        assert _send(db_session, _message(db_session, li_sequence, lead2),
                     RecordingChannel()) == "failed_no_linkedin_account"

    def test_suppressed_profile_is_never_contacted(self, db_session, verified_strategy,
                                                   li_sequence, li_accounts, unipile,
                                                   configured, fake_claude):
        lead = _li_lead(db_session, verified_strategy, 10)
        db_session.add(m.LinkedInSuppression(profile="li-lead-10", reason="test"))
        db_session.commit()
        channel = RecordingChannel()
        assert _send(db_session, _message(db_session, li_sequence, lead),
                     channel) == "cancelled_suppressed"
        assert channel.sent == []

    def test_failed_send_releases_the_slot(self, db_session, verified_strategy, li_sequence,
                                           li_accounts, unipile, configured, fake_claude):
        from app.integrations.outreach_base import SendResult

        class Refusing(RecordingChannel):
            def send(self, outbound):
                return SendResult(ok=False, permanent_failure=True, error="400")

        lead = _li_lead(db_session, verified_strategy, 11)
        msg = _message(db_session, li_sequence, lead)
        assert _send(db_session, msg, Refusing()) == "failed_permanent"
        assert all(linkedin_limits.usage(a.id, NOW)["connect"] == 0 for a in li_accounts)


class TestRenderLinks:
    """The prompt's 'no links' rule is not a control: a link an injected
    profile or post asked for must not reach a LinkedIn note."""

    def _render(self, monkeypatch, db_session, strategy, lead, text, template):
        from types import SimpleNamespace

        from app.services import anthropic_client

        class _Writer:
            def complete_json(self, **_kw):
                return {"text": text}

        monkeypatch.setattr(anthropic_client, "get_client", lambda: _Writer())
        return linkedin_outreach.render(db_session, strategy, lead,
                                        SimpleNamespace(template=template), "message")

    def test_a_foreign_link_is_removed(self, monkeypatch, db_session, verified_strategy):
        lead = _li_lead(db_session, verified_strategy, 41)
        out = self._render(monkeypatch, db_session, verified_strategy, lead,
                           "Hi Dana, details at https://evil.example.test/book. Worth a chat?",
                           "ask for a short call")
        assert "evil.example.test" not in out["text"]
        assert "Worth a chat?" in out["text"]

    @pytest.mark.parametrize("link", ["www.bait.example.com", "bait.example.com/book",
                                      "bit.ly/x9q"])
    def test_a_link_without_a_scheme_is_removed(self, monkeypatch, db_session,
                                                verified_strategy, link):
        lead = _li_lead(db_session, verified_strategy, 44)
        out = self._render(monkeypatch, db_session, verified_strategy, lead,
                           f"Hi Dana, details at {link}. Worth a chat?",
                           "ask for a short call")
        assert "bait" not in out["text"] and "bit.ly" not in out["text"]
        assert "Worth a chat?" in out["text"]

    def test_a_scheme_less_link_the_brief_names_is_kept(self, monkeypatch, db_session,
                                                        verified_strategy):
        lead = _li_lead(db_session, verified_strategy, 45)
        out = self._render(monkeypatch, db_session, verified_strategy, lead,
                           "Hi Dana, our guide: guides.ours.com/payroll. Useful?",
                           "share guides.ours.com/payroll")
        assert out["text"] == "Hi Dana, our guide: guides.ours.com/payroll. Useful?"

    def test_a_link_the_brief_names_is_kept(self, monkeypatch, db_session,
                                            verified_strategy):
        lead = _li_lead(db_session, verified_strategy, 42)
        out = self._render(monkeypatch, db_session, verified_strategy, lead,
                           "Hi Dana, our guide: https://guides.ours.example/payroll. Useful?",
                           "share https://guides.ours.example/payroll")
        assert "https://guides.ours.example/payroll" in out["text"]

    def test_a_note_that_was_only_a_link_is_refused(self, monkeypatch, db_session,
                                                    verified_strategy):
        lead = _li_lead(db_session, verified_strategy, 43)
        with pytest.raises(ValueError, match="empty"):
            self._render(monkeypatch, db_session, verified_strategy, lead,
                         "https://evil.example.test/book", "ask for a short call")


class TestChannelClass:
    def _outbound(self, lead, action, chat_id=None):
        from app.integrations.outreach_base import OutboundMessage
        # lead_id as a STRING, exactly as the send path builds it -- passing
        # the UUID object here once hid a guard that crashed on the real input.
        return OutboundMessage(message_id=str(uuid.uuid4()), lead_id=str(lead.id),
                               to_address="pid", body="hello", subject="S",
                               metadata={"action": action, "account_id": "acc-0",
                                         "provider_id": "pid", "chat_id": chat_id})

    def test_dispatch(self, db_session, verified_strategy):
        fake = FakeUnipile()
        lead = _li_lead(db_session, verified_strategy, 20)
        channel = LinkedInChannel(session=db_session, client=fake)
        assert channel.send(self._outbound(lead, "connect")).ok
        assert channel.send(self._outbound(lead, "message", chat_id="c1")).thread_ref == "c1"
        assert channel.send(self._outbound(lead, "inmail")).thread_ref == "chat-1"
        assert [c[0] for c in fake.calls] == ["invite", "send_in_chat", "start_chat"]
        assert fake.calls[2][3] is True        # inmail flag

    def test_provider_4xx_is_permanent(self, db_session, verified_strategy):
        fake = FakeUnipile()
        fake.fail_with = UnipileError("already invited", status=422)
        lead = _li_lead(db_session, verified_strategy, 21)
        result = LinkedInChannel(session=db_session, client=fake).send(
            self._outbound(lead, "connect"))
        assert not result.ok and result.permanent_failure

    def test_guard_refuses_a_suppressed_lead(self, db_session, verified_strategy):
        from app.core.exceptions import ComplianceError

        lead = _li_lead(db_session, verified_strategy, 22)
        db_session.add(m.LinkedInSuppression(profile="li-lead-22", reason="t"))
        db_session.commit()
        with pytest.raises(ComplianceError):
            LinkedInChannel(session=db_session, client=FakeUnipile()).send(
                self._outbound(lead, "connect"))


# --------------------------------------------------------------------------
# Enrolment, suppression paths, sequences API
# --------------------------------------------------------------------------


def test_linkedin_first_sequence_enrols_leads_without_email(db_session, verified_strategy,
                                                           li_sequence):
    from app.services.sequence_engine import enroll_leads

    _li_lead(db_session, verified_strategy, 30)                 # no email
    no_url = m.Lead(strategy_id=verified_strategy.id, source="apollo", external_id="X",
                    email="x@y.test", status=m.LeadStatus.VERIFIED)
    db_session.add(no_url)
    db_session.commit()
    assert enroll_leads(db_session, li_sequence, now=NOW) == 1


def test_unsubscribe_and_gdpr_suppress_the_profile(client, db_session, verified_strategy):
    from app.services import sequence_engine as engine
    from app.workers.lead_tasks import is_suppressed

    lead = _li_lead(db_session, verified_strategy, 31)
    engine.unsubscribe_lead(db_session, lead, source="test")
    assert is_suppressed(db_session, linkedin="https://linkedin.com/in/LI-LEAD-31")

    other = _li_lead(db_session, verified_strategy, 32)
    assert client.delete(f"/leads/{other.id}").status_code == 204
    assert is_suppressed(db_session, linkedin="li-lead-32")
    db_session.refresh(other)
    assert other.linkedin_url is None


def test_sequences_api_accepts_linkedin_steps(client, verified_strategy, db_session, test_user):
    # Plan enforcement: LinkedIn sequences need a plan that includes the
    # channel (Growth and up); this test is about the sequence API, not plans.
    test_user.plan = m.PlanTier.GROWTH
    db_session.commit()
    resp = client.post(f"/strategies/{verified_strategy.id}/sequences", json={
        "name": "LI", "channel": "linkedin",
        "steps": [{"step_no": 1, "template": "connect"},
                  {"step_no": 2, "template": "follow", "linkedin_action": "message"}]})
    assert resp.status_code == 201, resp.text
    assert [s["linkedin_action"] for s in resp.json()["steps"]] == ["auto", "message"]
    bad = client.post(f"/strategies/{verified_strategy.id}/sequences", json={
        "name": "E", "channel": "email",
        "steps": [{"step_no": 1, "template": "x", "linkedin_action": "connect"}]})
    assert bad.status_code == 422


# --------------------------------------------------------------------------
# Webhook + account API
# --------------------------------------------------------------------------


def _signed(client, payload, secret="whsec", header="hmac"):
    raw = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if header == "hmac":
        headers["X-LeadPilot-Signature"] = "sha256=" + hmac.new(
            secret.encode(), raw, hashlib.sha256).hexdigest()
    else:
        headers["Unipile-Auth"] = secret
    return client.post("/webhooks/unipile", content=raw, headers=headers)


class TestWebhook:
    def test_fail_closed_without_a_secret(self, client):
        assert _signed(client, {"event": "x"}).status_code == 401

    def test_bad_signature(self, client, db_session):
        credentials.set_system_secret(db_session, "unipile", "webhook_secret", "whsec")
        assert _signed(client, {"event": "x"}, secret="wrong").status_code == 401

    def test_reply_is_routed_and_new_relation_connects(self, client, db_session,
                                                       verified_strategy, li_sequence,
                                                       li_accounts, fake_claude):
        credentials.set_system_secret(db_session, "unipile", "webhook_secret", "whsec")
        lead = _li_lead(db_session, verified_strategy, 40, linkedin_provider_id="pid-40")
        enrollment = m.SequenceEnrollment(sequence_id=li_sequence.id, lead_id=lead.id)
        db_session.add(enrollment)
        db_session.commit()

        resp = _signed(client, {"event": "new_relation", "account_id": "acc-0",
                                "user_provider_id": "pid-40"}, header="static")
        assert resp.json()["action"] == "connected"
        db_session.refresh(lead)
        assert lead.linkedin_connection_status == "connected"

        resp = _signed(client, {"event": "message_received", "account_id": "acc-0",
                                "message_id": "m1", "chat_id": "chat-40",
                                "message": "Sounds interesting, tell me more",
                                "sender": {"attendee_provider_id": "pid-40"}})
        assert resp.json()["classification"] == "interested"
        db_session.refresh(lead)
        db_session.refresh(enrollment)
        assert lead.status is m.LeadStatus.REPLIED and lead.linkedin_chat_id == "chat-40"
        assert enrollment.status is m.EnrollmentStatus.STOPPED
        assert db_session.query(m.InboundReply).filter_by(channel="linkedin").count() == 1
        # Retried delivery: acknowledged, not re-processed.
        again = _signed(client, {"event": "message_received", "account_id": "acc-0",
                                 "message_id": "m1", "chat_id": "chat-40", "message": "x",
                                 "sender": {"attendee_provider_id": "pid-40"}})
        assert again.json().get("duplicate") is True

    def test_the_reply_job_is_published_after_classification(
            self, client, db_session, verified_strategy, li_accounts, fake_claude,
            monkeypatch):
        """gaps-sweep-2 #7: the reply-intelligence job used to be published
        before routing, so it could read classification NULL."""
        from app.workers import reply_tasks

        seen = []
        monkeypatch.setattr(reply_tasks, "enqueue", lambda reply_id: seen.append(
            db_session.get(m.InboundReply, reply_id).classification) or True)
        credentials.set_system_secret(db_session, "unipile", "webhook_secret", "whsec")
        _li_lead(db_session, verified_strategy, 43, linkedin_provider_id="pid-43")
        resp = _signed(client, {"event": "message_received", "account_id": "acc-0",
                                "message_id": "m43", "message": "Sounds interesting",
                                "sender": {"attendee_provider_id": "pid-43"}})
        assert resp.json()["classification"] == "interested"
        assert seen == ["interested"]

    def test_the_reply_job_is_published_even_if_routing_fails(
            self, client, db_session, verified_strategy, li_accounts, fake_claude,
            monkeypatch):
        from app.workers import outreach_tasks, reply_tasks

        seen = []
        monkeypatch.setattr(reply_tasks, "enqueue", lambda reply_id: seen.append(reply_id))

        def boom(*a, **k):
            raise RuntimeError("router down")

        monkeypatch.setattr(outreach_tasks, "route_linkedin_inbound_impl", boom)
        credentials.set_system_secret(db_session, "unipile", "webhook_secret", "whsec")
        _li_lead(db_session, verified_strategy, 44, linkedin_provider_id="pid-44")
        with pytest.raises(RuntimeError):
            _signed(client, {"event": "message_received", "account_id": "acc-0",
                             "message_id": "m44", "message": "hello",
                             "sender": {"attendee_provider_id": "pid-44"}})
        assert len(seen) == 1

    def test_unsubscribe_reply_suppresses_the_profile(self, client, db_session,
                                                      verified_strategy, li_accounts,
                                                      fake_claude):
        from app.workers.lead_tasks import is_suppressed

        credentials.set_system_secret(db_session, "unipile", "webhook_secret", "whsec")
        fake_claude.default_reply_class = "unsubscribe_request"
        lead = _li_lead(db_session, verified_strategy, 41)
        _signed(client, {"event": "message_received", "account_id": "acc-0",
                         "message_id": "m2", "message": "please stop",
                         "sender": {"attendee_profile_url": "https://linkedin.com/in/li-lead-41"}})
        assert is_suppressed(db_session, linkedin=lead.linkedin_url)

    def test_another_tenants_lead_is_never_matched(self, client, db_session,
                                                   verified_strategy, fake_claude):
        credentials.set_system_secret(db_session, "unipile", "webhook_secret", "whsec")
        other = m.User(email="li-other@x.test", email_verified=True)
        db_session.add(other)
        db_session.flush()
        db_session.add(m.LinkedInAccount(user_id=other.id, unipile_account_id="acc-other"))
        _li_lead(db_session, verified_strategy, 42, linkedin_provider_id="pid-42")
        db_session.commit()
        resp = _signed(client, {"event": "message_received", "account_id": "acc-other",
                                "message_id": "m3", "message": "hi",
                                "sender": {"attendee_provider_id": "pid-42"}})
        assert resp.json()["matched"] is False


class TestAccountApi:
    def test_link_list_patch_and_ownership(self, client, db_session, unipile, configured):
        unipile.accounts["acc-new"] = {"id": "acc-new", "name": "Rehan", "has_premium": True,
                                       "profile_url": None}
        resp = client.post("/integrations/linkedin/accounts",
                           json={"unipile_account_id": "acc-new"})
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["has_premium"] is True and body["inmail_credits"] == 5
        assert body["limits"] == {"connect": 20, "message": 50}
        assert client.post("/integrations/linkedin/accounts",
                           json={"unipile_account_id": "nope"}).status_code == 422
        account_id = body["id"]
        assert client.patch(f"/integrations/linkedin/accounts/{account_id}",
                            json={"is_active": False}).json()["is_active"] is False
        other = m.User(email="acc-o@x.test", email_verified=True)
        db_session.add(other)
        db_session.commit()
        assert client.delete(f"/integrations/linkedin/accounts/{account_id}",
                             headers=auth_headers(other)).status_code == 404

    def test_hosted_auth_round_trip(self, client, db_session, test_user, unipile, configured):
        url = client.post("/integrations/linkedin/connect").json()["url"]
        assert url.startswith("https://account.unipile.com")
        notify_url = unipile.calls[-1][1]["notify_url"]
        state = notify_url.split("state=", 1)[1]
        unipile.accounts["acc-hosted"] = {"id": "acc-hosted", "name": "N", "has_premium": False}
        client.headers.pop("Authorization", None)    # Unipile has no session
        resp = client.post(f"/integrations/linkedin/unipile/notify?state={state}",
                           json={"status": "CREATION_SUCCESS", "account_id": "acc-hosted"})
        assert resp.json()["linked"] is True
        assert db_session.query(m.LinkedInAccount).filter_by(
            unipile_account_id="acc-hosted").one().user_id == test_user.id
        assert client.post("/integrations/linkedin/unipile/notify?state=" + "x" * 40,
                           json={}).status_code == 400

    def test_not_configured_is_503(self, client, unipile, monkeypatch):
        monkeypatch.setattr("app.integrations.unipile_linkedin.get_client",
                            lambda db, user_id=None: None)
        assert client.post("/integrations/linkedin/connect").status_code == 503
