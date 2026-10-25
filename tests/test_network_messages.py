"""Messaging a LinkedIn connection from the Network page.

Three ways to send, all behind the same rules:
  copy      user copies the text and sends it on LinkedIn themselves
  unipile   one click, one message, through the user's connected account
  bulk      many approved messages, queued and spaced, capped per day

Rules every path obeys: Growth+ plan, the user's OWN connection and product,
the text the user saved (a draft is never sent), suppression, the daily cap,
and 1st-degree connections only (never a stranger).
"""

from __future__ import annotations

import fakeredis
import pytest

from app.db import models as m
from app.integrations.unipile_linkedin import UnipileError
from app.services import linkedin_limits


class StubClaude:
    def __init__(self):
        self.system = ""
        self.prompt = ""
        self.reply = {"text": "Hi Ana, saw you run Acme Fire Protection. Quick question?"}

    def complete_json(self, *, system, prompt, max_tokens=1024, **_kw):
        self.system, self.prompt = system, prompt
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


class FakeUnipile:
    def __init__(self):
        self.calls = []
        self.connected = True
        self.fail_with = None

    def profile(self, identifier, account_id):
        self.calls.append(("profile", identifier))
        return {"provider_id": "prov-" + identifier, "is_connected": self.connected}

    def start_chat(self, provider_id, account_id, text, *, inmail=False, subject=None):
        self.calls.append(("start_chat", provider_id, account_id, text))
        if self.fail_with:
            raise self.fail_with
        return {"chat_id": "chat-1", "message_id": "msg-1"}


@pytest.fixture()
def claude(monkeypatch):
    stub = StubClaude()
    monkeypatch.setattr("app.services.anthropic_client.get_client", lambda: stub)
    return stub


@pytest.fixture()
def unipile(monkeypatch):
    fake = FakeUnipile()
    monkeypatch.setattr("app.integrations.unipile_linkedin.get_client",
                        lambda db, user_id=None: fake)
    return fake


@pytest.fixture(autouse=True)
def redis(monkeypatch):
    r = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(linkedin_limits, "_redis", lambda: r)
    return r


@pytest.fixture()
def queued(monkeypatch):
    calls = []
    from app.workers import network_tasks

    monkeypatch.setattr(network_tasks.send_queued, "apply_async",
                        lambda args=None, countdown=0, **kw: calls.append((args[0], countdown)))
    return calls


class _KeepOpen:
    """Stand-in for SessionLocal(): hands the test session to the task and
    does not close it on exit."""

    def __init__(self, session):
        self.session = session

    def __call__(self):
        return self

    def __enter__(self):
        return self.session

    def __exit__(self, *exc):
        return False


@pytest.fixture(autouse=True)
def task_session(monkeypatch, db_session):
    monkeypatch.setattr("app.db.base.SessionLocal", _KeepOpen(db_session))


@pytest.fixture()
def world(db_session, test_user):
    test_user.plan = m.PlanTier.GROWTH
    db_session.commit()
    product = m.Product(user=test_user, name="Fire SEO", type=m.ProductType.SKILL,
                        description="Local SEO for fire protection companies")
    db_session.add(product)
    conn = m.LinkedInConnection(user_id=test_user.id, dedupe_key="k1", full_name="Ana Lopez",
                                company="Acme Fire Protection", position="Owner",
                                profile_url="https://www.linkedin.com/in/ana-lopez/")
    db_session.add(conn)
    db_session.commit()
    account = m.LinkedInAccount(user_id=test_user.id, unipile_account_id="acc-1",
                                display_name="Me", is_active=True)
    db_session.add(account)
    db_session.commit()
    return {"user": test_user, "product": product, "conn": conn, "account": account}


def _draft(client, w):
    return client.post("/linkedin-network/messages/draft",
                       json={"connection_id": str(w["conn"].id),
                             "product_id": str(w["product"].id)})


def _approved(client, w, text="Hi Ana, a quick idea for Acme. Open to a chat?"):
    mid = _draft(client, w).json()["id"]
    assert client.put(f"/linkedin-network/messages/{mid}", json={"text": text}).status_code == 200
    return mid


class TestDraft:
    def test_drafts_in_the_users_voice(self, client, world, claude, db_session):
        world["user"].style_profile_json = {"tone": "warm and direct", "formality": 2}
        db_session.commit()
        r = _draft(client, world)
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "draft" and body["text"].startswith("Hi Ana")
        assert "warm and direct" in claude.system
        assert "Owner" in claude.prompt and "Acme Fire Protection" in claude.prompt

    def test_free_plan_gets_402(self, client, world, claude, db_session):
        world["user"].plan = m.PlanTier.FREE
        db_session.commit()
        r = _draft(client, world)
        assert r.status_code == 402 and r.json()["detail"]["upgrade_to"] == "growth"

    def test_someone_elses_connection_is_404(self, client, world, claude, db_session):
        other = m.User(email="o@x.dev", email_verified=True, plan=m.PlanTier.GROWTH)
        db_session.add(other)
        db_session.commit()
        c2 = m.LinkedInConnection(user_id=other.id, dedupe_key="z", full_name="Zed")
        db_session.add(c2)
        db_session.commit()
        r = client.post("/linkedin-network/messages/draft",
                        json={"connection_id": str(c2.id), "product_id": str(world["product"].id)})
        assert r.status_code == 404

    def test_someone_elses_product_is_404(self, client, world, claude, db_session):
        other = m.User(email="o@x.dev", email_verified=True, plan=m.PlanTier.GROWTH)
        db_session.add(other)
        db_session.commit()
        p2 = m.Product(user=other, name="P", type=m.ProductType.SKILL, description="d")
        db_session.add(p2)
        db_session.commit()
        r = client.post("/linkedin-network/messages/draft",
                        json={"connection_id": str(world["conn"].id), "product_id": str(p2.id)})
        assert r.status_code == 404


    def test_a_draft_that_was_only_invention_is_502(self, client, world, claude):
        claude.reply = {"text": "As we discussed, congrats on the Series B."}
        assert _draft(client, world).status_code == 502

    def test_empty_model_reply_is_502(self, client, world, claude):
        claude.reply = {"text": "  "}
        assert _draft(client, world).status_code == 502

    def test_redrafting_replaces_an_unsent_draft_but_not_a_sent_one(
            self, client, world, claude, unipile, db_session):
        first = _draft(client, world).json()["id"]
        assert _draft(client, world).json()["id"] == first
        mid = _approved(client, world)
        assert client.post(f"/linkedin-network/messages/{mid}/send").status_code == 200
        assert _draft(client, world).status_code == 409


class TestEdit:
    def test_save_approves_the_text(self, client, world, claude):
        mid = _approved(client, world, "My own words.")
        got = client.get("/linkedin-network/messages").json()["messages"]
        assert got[0]["text"] == "My own words." and got[0]["status"] == "approved"
        assert mid == got[0]["id"]

    @pytest.mark.parametrize("text", ["", "   ", "x" * 1501])
    def test_bad_text_is_422(self, client, world, claude, text):
        mid = _draft(client, world).json()["id"]
        assert client.put(f"/linkedin-network/messages/{mid}", json={"text": text}).status_code == 422

    def test_someone_elses_message_is_404(self, client, world, claude, db_session):
        other = m.User(email="o@x.dev", email_verified=True, plan=m.PlanTier.GROWTH)
        db_session.add(other)
        db_session.commit()
        msg = m.LinkedInNetworkMessage(user_id=other.id, connection_id=world["conn"].id,
                                       product_id=world["product"].id, text="x", status="draft")
        db_session.add(msg)
        db_session.commit()
        assert client.put(f"/linkedin-network/messages/{msg.id}",
                          json={"text": "mine now"}).status_code == 404
        assert client.get("/linkedin-network/messages").json()["messages"] == []


class TestCopyMode:
    def test_mark_sent_requires_an_approved_text(self, client, world, claude):
        mid = _draft(client, world).json()["id"]
        assert client.post(f"/linkedin-network/messages/{mid}/mark-sent").status_code == 409
        client.put(f"/linkedin-network/messages/{mid}", json={"text": "hello"})
        r = client.post(f"/linkedin-network/messages/{mid}/mark-sent")
        assert r.status_code == 200 and r.json()["status"] == "sent"
        assert r.json()["channel"] == "copy"


class TestSendNow:
    def test_sends_the_saved_text_through_the_account(self, client, world, claude, unipile):
        mid = _approved(client, world, "Exactly this text.")
        r = client.post(f"/linkedin-network/messages/{mid}/send")
        assert r.status_code == 200 and r.json()["status"] == "sent"
        assert ("start_chat", "prov-ana-lopez", "acc-1", "Exactly this text.") in unipile.calls

    def test_a_draft_is_never_sent(self, client, world, claude, unipile):
        mid = _draft(client, world).json()["id"]
        assert client.post(f"/linkedin-network/messages/{mid}/send").status_code == 409
        assert not [c for c in unipile.calls if c[0] == "start_chat"]

    def test_no_connected_account_is_409(self, client, world, claude, unipile, db_session):
        world["account"].is_active = False
        db_session.commit()
        mid = _approved(client, world)
        r = client.post(f"/linkedin-network/messages/{mid}/send")
        assert r.status_code == 409 and "LinkedIn account" in r.json()["detail"]

    def test_a_stranger_is_never_messaged(self, client, world, claude, unipile):
        unipile.connected = False
        mid = _approved(client, world)
        r = client.post(f"/linkedin-network/messages/{mid}/send")
        assert r.status_code == 409
        assert not [c for c in unipile.calls if c[0] == "start_chat"]

    def test_suppressed_profiles_are_never_messaged(self, client, world, claude, unipile, db_session):
        db_session.add(m.LinkedInSuppression(profile="ana-lopez", reason="optout"))
        db_session.commit()
        mid = _approved(client, world)
        assert client.post(f"/linkedin-network/messages/{mid}/send").status_code == 409
        assert not [c for c in unipile.calls if c[0] == "start_chat"]

    def test_daily_cap_blocks_and_does_not_send(self, client, world, claude, unipile, redis):
        key = linkedin_limits._key(world["account"].id, "message", None)
        redis.set(key, 10_000)
        mid = _approved(client, world)
        r = client.post(f"/linkedin-network/messages/{mid}/send")
        assert r.status_code == 429
        assert not [c for c in unipile.calls if c[0] == "start_chat"]

    def test_provider_error_marks_failed_and_releases_the_slot(
            self, client, world, claude, unipile, redis):
        unipile.fail_with = UnipileError("boom", status=500)
        mid = _approved(client, world)
        r = client.post(f"/linkedin-network/messages/{mid}/send")
        assert r.status_code == 502
        got = client.get("/linkedin-network/messages").json()["messages"][0]
        assert got["status"] == "failed" and got["error"]
        assert int(redis.get(linkedin_limits._key(world["account"].id, "message", None)) or 0) == 0

    def test_downgraded_plan_cannot_send(self, client, world, claude, unipile, db_session):
        mid = _approved(client, world)
        world["user"].plan = m.PlanTier.FREE
        db_session.commit()
        assert client.post(f"/linkedin-network/messages/{mid}/send").status_code == 402

    def test_cannot_send_twice(self, client, world, claude, unipile):
        mid = _approved(client, world)
        assert client.post(f"/linkedin-network/messages/{mid}/send").status_code == 200
        assert client.post(f"/linkedin-network/messages/{mid}/send").status_code == 409


class TestBulk:
    def test_queues_approved_messages_spaced_apart(self, client, world, claude, queued, db_session):
        c2 = m.LinkedInConnection(user_id=world["user"].id, dedupe_key="k2", full_name="Ben Ng",
                                  profile_url="https://www.linkedin.com/in/ben-ng/")
        db_session.add(c2)
        db_session.commit()
        a = _approved(client, world)
        b = client.post("/linkedin-network/messages/draft",
                        json={"connection_id": str(c2.id), "product_id": str(world["product"].id)}).json()["id"]
        client.put(f"/linkedin-network/messages/{b}", json={"text": "hi Ben"})
        r = client.post("/linkedin-network/messages/bulk-send", json={"ids": [a, b]})
        assert r.status_code == 200 and r.json()["queued"] == 2
        assert [i for i, _ in queued] == [a, b]
        delays = [d for _, d in queued]
        assert delays == sorted(delays) and delays[1] - delays[0] >= 30

    def test_one_unapproved_message_rejects_the_whole_batch(self, client, world, claude, queued):
        a = _approved(client, world)
        r = client.post("/linkedin-network/messages/bulk-send", json={"ids": [a, "00000000-0000-0000-0000-000000000000"]})
        assert r.status_code in (404, 409) and queued == []

    def test_a_draft_cannot_be_bulk_sent(self, client, world, claude, queued):
        d = _draft(client, world).json()["id"]
        assert client.post("/linkedin-network/messages/bulk-send", json={"ids": [d]}).status_code == 409
        assert queued == []

    def test_batch_size_is_capped(self, client, world, claude, queued):
        ids = ["00000000-0000-0000-0000-0000000000%02d" % i for i in range(26)]
        assert client.post("/linkedin-network/messages/bulk-send", json={"ids": ids}).status_code == 422

    def test_free_plan_gets_402(self, client, world, claude, queued, db_session):
        world["user"].plan = m.PlanTier.FREE
        db_session.commit()
        assert client.post("/linkedin-network/messages/bulk-send", json={"ids": []}).status_code in (402, 422)

    def test_queued_text_cannot_be_edited(self, client, world, claude, queued):
        a = _approved(client, world)
        client.post("/linkedin-network/messages/bulk-send", json={"ids": [a]})
        assert client.put(f"/linkedin-network/messages/{a}", json={"text": "swap"}).status_code == 409

    def test_task_sends_a_queued_message(self, client, world, claude, queued, unipile, db_session):
        from app.workers import network_tasks

        a = _approved(client, world, "Queued text.")
        client.post("/linkedin-network/messages/bulk-send", json={"ids": [a]})
        out = network_tasks.send_queued.run(a)
        assert out["status"] == "sent"
        assert ("start_chat", "prov-ana-lopez", "acc-1", "Queued text.") in unipile.calls

    def test_task_skips_a_message_that_is_no_longer_queued(self, client, world, claude, unipile):
        from app.workers import network_tasks

        a = _approved(client, world)   # approved, never queued
        assert network_tasks.send_queued.run(a)["status"] == "skipped"
        assert not [c for c in unipile.calls if c[0] == "start_chat"]

    def test_task_rechecks_the_plan(self, client, world, claude, queued, unipile, db_session):
        from app.workers import network_tasks

        a = _approved(client, world)
        client.post("/linkedin-network/messages/bulk-send", json={"ids": [a]})
        world["user"].plan = m.PlanTier.FREE
        db_session.commit()
        assert network_tasks.send_queued.run(a)["status"] == "failed"
        assert not [c for c in unipile.calls if c[0] == "start_chat"]

    def test_task_cap_hit_returns_message_to_approved(self, client, world, claude, queued,
                                                      unipile, redis):
        from app.workers import network_tasks

        a = _approved(client, world)
        client.post("/linkedin-network/messages/bulk-send", json={"ids": [a]})
        redis.set(linkedin_limits._key(world["account"].id, "message", None), 10_000)
        assert network_tasks.send_queued.run(a)["status"] == "approved"


class TestPrivacy:
    def test_erasing_the_network_erases_the_messages(self, client, world, claude, db_session):
        _approved(client, world)
        assert client.delete("/linkedin-network").status_code == 200
        assert db_session.query(m.LinkedInNetworkMessage).count() == 0

    def test_prompt_never_contains_other_accounts_data(self, client, world, claude, db_session):
        other = m.User(email="o@x.dev", email_verified=True, plan=m.PlanTier.GROWTH)
        db_session.add(other)
        db_session.commit()
        db_session.add(m.LinkedInConnection(user_id=other.id, dedupe_key="s", full_name="Secret Person"))
        db_session.commit()
        _draft(client, world)
        assert "Secret Person" not in claude.prompt
