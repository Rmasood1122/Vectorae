"""Messaging a LinkedIn connection from the Network page. The USER sends.

Since 2026-11-02 (evals/vectorae, option a) there is one way: the user saves
the text, turns it into an L0 task (tests/test_linkedin_tasks.py) or records
that they sent it themselves ("copy"). Vectorae never sends: the one-click
and bulk routes are gone.

Rules: Growth+ plan, the user's OWN connection and product, the text the user
saved (a draft never becomes a task), suppression.
"""

from __future__ import annotations

import pytest

from app.db import models as m


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


@pytest.fixture()
def claude(monkeypatch):
    stub = StubClaude()
    monkeypatch.setattr("app.services.anthropic_client.get_client", lambda: stub)
    return stub


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
    return {"user": test_user, "product": product, "conn": conn}


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
            self, client, world, claude, db_session):
        first = _draft(client, world).json()["id"]
        assert _draft(client, world).json()["id"] == first
        mid = _approved(client, world)
        assert client.post(f"/linkedin-network/messages/{mid}/mark-sent").status_code == 200
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


class TestVectoraeNeverSends:
    def test_the_send_and_bulk_routes_are_gone(self, client, world, claude):
        mid = _approved(client, world)
        assert client.post(f"/linkedin-network/messages/{mid}/send").status_code in (404, 405)
        assert client.post("/linkedin-network/messages/bulk-send",
                           json={"ids": [mid]}).status_code in (404, 405)

    def test_suppressed_profiles_get_no_draft(self, client, world, claude, db_session):
        db_session.add(m.LinkedInSuppression(profile="ana-lopez", reason="optout"))
        db_session.commit()
        assert _draft(client, world).status_code == 409


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
