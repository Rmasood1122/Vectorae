"""SEALED guard tests moved unchanged from tests/test_network_messages.py.

Listed in evals/compliance_testset.yaml: a failing or missing test here
blocks the release gate. Moved by the gate PR in
docs/program/plans/phase5-seal-ai-and-tenancy-guards.md; the test bodies
are byte-identical to the source. Helpers they need are copied here so
the file stands alone.
"""

from __future__ import annotations
import fakeredis
import pytest
from app.db import models as m
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


@pytest.fixture()
def claude(monkeypatch):
    stub = StubClaude()
    monkeypatch.setattr("app.services.anthropic_client.get_client", lambda: stub)
    return stub


@pytest.fixture(autouse=True)
def redis(monkeypatch):
    r = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(linkedin_limits, "_redis", lambda: r)
    return r


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


class TestDraft:

    def test_an_invented_meeting_or_round_is_removed_from_the_draft(
            self, client, world, claude):
        """T25: a LinkedIn connection shows no meeting and no funding. The
        draft keeps only what the records can back."""
        claude.reply = {"text": "Hi Ana, great meeting you at the Leeds summit. "
                                "Congrats on the Series B. Quick question about inspections?"}
        r = _draft(client, world)
        assert r.status_code == 200
        assert r.json()["text"] == "Quick question about inspections?"
