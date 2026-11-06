"""The one-time onboarding email sent after an address is verified.

What matters most is the negative space: a second copy, a copy to an
unverified or suspended account, an account marked "sent" when nothing went
out, or a link to a route the frontend does not have. Each looks fine until a
real user hits it.

The email is read from the in-process `mailbox` fixture (EMAIL_PROVIDER=memory),
so assertions are on the message a person would actually receive.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

import pytest
from sqlalchemy import select

from app.config import settings
from app.db import models as m
from app.services import onboarding_email as ob
from app.services.email_sender import EmailSendError
from app.workers import notification_tasks
from tests.conftest import verification_link

# Captured at import, before the autouse `queued_jobs` fixture replaces it.
_REAL_ENQUEUE = notification_tasks.enqueue_onboarding_email

PASSWORD = "hunter22!"
FRONTEND_APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "frontend", "src", "app")


def _user(db, email="owner@x.com", **fields):
    values = {"email": email, "password_hash": "x", "email_verified": True}
    values.update(fields)
    user = m.User(**values)
    db.add(user)
    db.commit()
    return user


def _signup_and_click(client, mailbox, email="fresh@x.com"):
    r = client.post("/auth/signup", json={"email": email, "password": PASSWORD})
    assert r.status_code == 201, r.text
    link = urlparse(verification_link(mailbox))
    return client.get(f"{link.path}?{link.query}", follow_redirects=False)


# --------------------------------------------------------------------------
# Trigger: /auth/verify
# --------------------------------------------------------------------------


class TestVerificationTriggersIt:
    def test_a_successful_verification_queues_it_for_that_account(
            self, client, db_session, mailbox, queued_jobs):
        resp = _signup_and_click(client, mailbox)
        assert resp.status_code == 302
        assert resp.headers["location"].endswith("/login?verified=true")

        user = db_session.execute(
            select(m.User).where(m.User.email == "fresh@x.com")).scalars().one()
        assert queued_jobs["onboarding_emails"] == [str(user.id)]

    def test_signup_alone_does_not_queue_it(self, client, queued_jobs):
        r = client.post("/auth/signup", json={"email": "nope@x.com", "password": PASSWORD})
        assert r.status_code == 201
        assert queued_jobs["onboarding_emails"] == []

    def test_a_reused_link_does_not_queue_it_again(self, client, mailbox, queued_jobs):
        _signup_and_click(client, mailbox)
        link = urlparse(verification_link(mailbox))
        again = client.get(f"{link.path}?{link.query}", follow_redirects=False)
        assert again.headers["location"].endswith("/login?verified=already")
        assert len(queued_jobs["onboarding_emails"]) == 1

    def test_an_invalid_link_does_not_queue_it(self, anon_client, queued_jobs):
        resp = anon_client.get("/auth/verify?token=not-a-real-token",
                               follow_redirects=False)
        assert resp.headers["location"].endswith("/login?error=invalid")
        assert queued_jobs["onboarding_emails"] == []


# --------------------------------------------------------------------------
# send_once
# --------------------------------------------------------------------------


class TestSendOnce:
    def test_sends_one_email_and_stamps_the_account(self, db_session, mailbox):
        user = _user(db_session)

        assert ob.send_once(db_session, user.id) == ob.SENT

        assert len(mailbox) == 1
        assert mailbox[0]["to"] == "owner@x.com"
        assert "what to do next" in mailbox[0]["subject"]
        db_session.refresh(user)
        assert user.onboarding_email_sent_at is not None

    def test_never_sends_a_second_copy(self, db_session, mailbox):
        user = _user(db_session)
        ob.send_once(db_session, user.id)

        assert ob.send_once(db_session, user.id) == ob.ALREADY_SENT
        assert ob.send_once(db_session, str(user.id)) == ob.ALREADY_SENT
        assert len(mailbox) == 1

    @pytest.mark.parametrize("fields", [
        {"email_verified": False},
        {"is_suspended": True},
        {"password_hash": None},
    ], ids=["unverified", "suspended", "unclaimed-pre-auth-row"])
    def test_ineligible_accounts_get_nothing(self, db_session, mailbox, fields):
        user = _user(db_session, **fields)

        assert ob.send_once(db_session, user.id) == ob.NOT_ELIGIBLE
        assert mailbox == []
        db_session.refresh(user)
        assert user.onboarding_email_sent_at is None

    def test_unknown_account(self, db_session, mailbox):
        import uuid

        assert ob.send_once(db_session, uuid.uuid4()) == ob.NO_SUCH_USER
        assert mailbox == []

    def test_a_failed_send_releases_the_claim_so_a_retry_sends(
            self, db_session, mailbox, monkeypatch):
        user = _user(db_session)

        def down(**_kw):
            raise EmailSendError("relay refused connection")

        monkeypatch.setattr(ob, "send_email", down)
        with pytest.raises(EmailSendError):
            ob.send_once(db_session, user.id)
        db_session.refresh(user)
        assert user.onboarding_email_sent_at is None, "marked sent but nothing went out"

        monkeypatch.undo()
        assert ob.send_once(db_session, user.id) == ob.SENT
        assert len(mailbox) == 1

    def test_a_lost_race_sends_nothing(self, db_session, mailbox, monkeypatch):
        """Another worker claimed the slot between our read and our UPDATE."""
        user = _user(db_session)
        real_claim = ob._claim

        def other_worker_won(db, uid):
            assert real_claim(db, uid) is True  # the other worker's claim
            return real_claim(db, uid)          # ours now changes no row

        monkeypatch.setattr(ob, "_claim", other_worker_won)
        assert ob.send_once(db_session, user.id) == ob.ALREADY_SENT
        assert mailbox == []


# --------------------------------------------------------------------------
# Content
# --------------------------------------------------------------------------


def _paths(db, user):
    return [path for _label, path in ob.steps_for(db, user)]


class TestContent:
    def test_links_are_absolute_on_the_frontend(self, db_session, mailbox):
        user = _user(db_session)
        ob.send_once(db_session, user.id)
        base = settings.frontend_url.rstrip("/")
        text = mailbox[0]["text"]
        assert f"{base}/login" in text
        assert f"{base}/strategies/new" in text
        assert f"{base}/settings?tab=integrations" in text

    def test_account_held_by_identity_checks_is_sent_there_first(self, db_session):
        user = _user(db_session, identity_required=True)
        assert _paths(db_session, user)[0] == "/onboarding/verify"

    def test_account_that_finished_its_checks_is_not_told_to_redo_them(
            self, db_session, monkeypatch):
        from app.services import identity

        monkeypatch.setattr(identity, "phone_verification_required", lambda: True)
        user = _user(db_session, identity_required=True, personal_country="US",
                     account_type="individual", phone_verified=True)
        assert "/onboarding/verify" not in _paths(db_session, user)

    def test_legacy_account_never_held_by_the_checks_skips_them(self, db_session):
        user = _user(db_session, identity_required=False)
        assert "/onboarding/verify" not in _paths(db_session, user)

    def test_plan_step_only_when_the_deployment_requires_one(
            self, db_session, monkeypatch):
        user = _user(db_session)
        monkeypatch.setattr(type(settings), "active_plan_required",
                            property(lambda self: False))
        assert "/pricing" not in _paths(db_session, user)

        monkeypatch.setattr(type(settings), "active_plan_required",
                            property(lambda self: True))
        assert "/pricing" in _paths(db_session, user)

        user.plan = m.PlanTier.PRO
        db_session.commit()
        assert "/pricing" not in _paths(db_session, user)

    def test_name_is_escaped_in_html(self, db_session):
        user = _user(db_session, full_name="<script>x</script>")
        _subject, html, text = ob.render(user, ob.steps_for(db_session, user))
        assert "<script>" not in html
        assert "&lt;script&gt;" in html
        assert "Hi <script>x</script>," in text  # plain text is not HTML

    def test_it_is_a_service_message_not_marketing(self, db_session):
        user = _user(db_session)
        _subject, html, text = ob.render(user, ob.steps_for(db_session, user))
        assert "not marketing" in text
        assert "owner@x.com" in text  # says which account it is about
        assert "<img" not in html     # no tracking pixel


class TestLinksPointAtRealPages:
    @staticmethod
    def _route_exists(path: str) -> bool:
        route = urlparse(path).path.strip("/")
        for group in ("", "(app)", "(auth)"):
            if os.path.isfile(os.path.join(FRONTEND_APP, group, route, "page.tsx")):
                return True
        return False

    def test_every_link_has_a_frontend_page(self, db_session):
        user = _user(db_session, identity_required=True)
        paths = _paths(db_session, user) + ["/pricing", "/login", "/help"]
        missing = [p for p in paths if not self._route_exists(p)]
        assert missing == []

    def test_product_steps_use_the_onboarding_checklist_routes(self):
        from app.api.onboarding import STEP_ACTIONS

        checklist = {action["href"] for action in STEP_ACTIONS.values()}
        assert {path for _label, path in ob.PRODUCT_STEPS} <= checklist


# --------------------------------------------------------------------------
# Queueing
# --------------------------------------------------------------------------


def test_enqueue_never_raises_when_the_broker_is_down(monkeypatch):
    def down(*_a, **_kw):
        raise ConnectionError("broker unreachable")

    monkeypatch.setattr(notification_tasks.onboarding_email, "apply_async", down)
    assert _REAL_ENQUEUE("abc") is False
