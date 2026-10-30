"""Admins can see who signed up and from where (founder request 2026-10-30).

Founder choices: "Show IP + country", "Tell me on each sign-up", "Sign-up
log page"; sign-up stays open (P0-D-ENF, 2026-10-11).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.config import settings
from app.db import models as m
from app.services import signup_watch
from tests.conftest import auth_headers

PASSWORD = "Sup3rSecret!"


@pytest.fixture()
def admin(db_session):
    user = m.User(email="admin@vectorae.test", is_admin=True, email_verified=True)
    db_session.add(user)
    db_session.commit()
    return user


def _signup(anon_client, email="fresh@example.com"):
    resp = anon_client.post("/auth/signup", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 201, resp.text
    return resp


def _events(db_session, event=signup_watch.EVENT_SIGNUP):
    return db_session.execute(select(m.AccountSecurityEvent).where(
        m.AccountSecurityEvent.event == event)).scalars().all()


class TestSignupIsRecorded:
    def test_a_signup_writes_one_row_with_its_address(self, anon_client, db_session):
        _signup(anon_client)
        (row,) = _events(db_session)
        user = db_session.execute(select(m.User).where(
            m.User.email == "fresh@example.com")).scalar_one()
        assert row.user_id == user.id and row.email == "fresh@example.com"
        assert row.ip == user.signup_ip == "testclient"

    def test_the_admin_alert_is_queued_after_the_commit(self, anon_client, db_session,
                                                        queued_jobs):
        _signup(anon_client)
        (row,) = _events(db_session)
        assert queued_jobs["signup_alerts"] == [str(row.id)]

    def test_a_refused_duplicate_records_nothing(self, anon_client, db_session, queued_jobs):
        _signup(anon_client)
        resp = anon_client.post("/auth/signup",
                                json={"email": "fresh@example.com", "password": PASSWORD})
        assert resp.status_code == 409
        assert len(_events(db_session)) == 1 and len(queued_jobs["signup_alerts"]) == 1


class TestAdminAlert:
    @pytest.fixture()
    def sent(self, monkeypatch):
        mails, pushes = [], []
        monkeypatch.setattr("app.services.email_sender.send_email",
                            lambda **kw: mails.append(kw) or "id")

        async def _push(title, body, data=None):
            pushes.append((title, body, data))
            return 1

        monkeypatch.setattr(
            "app.services.notification_service.NotificationService.send_to_admins", _push)
        return mails, pushes

    @pytest.fixture()
    def country(self, monkeypatch):
        looked_up = []

        def _lookup(ip):
            looked_up.append(ip)
            return "PK"

        monkeypatch.setattr("app.integrations.geolocation.lookup_country", _lookup)
        return looked_up

    def _row(self, db_session, ip="198.51.100.7"):
        user = m.User(email="new@example.com")
        db_session.add(user)
        db_session.flush()
        row = signup_watch.record_signup(db_session, user, ip)
        db_session.commit()
        return row

    def test_every_admin_is_emailed_and_pushed(self, db_session, admin, sent, country):
        mails, pushes = sent
        other = m.User(email="second-admin@vectorae.test", is_admin=True)
        db_session.add(other)
        db_session.commit()
        result = signup_watch.alert_admins(db_session, self._row(db_session).id)
        assert sorted(m_["to"] for m_ in mails) == ["admin@vectorae.test",
                                                   "second-admin@vectorae.test"]
        assert "new@example.com" in mails[0]["subject"]
        assert "198.51.100.7 (PK)" in mails[0]["text"]
        assert pushes and "PK" in pushes[0][1]
        assert result["emailed"] == 2 and result["country"] == "PK"

    def test_the_country_is_stored_on_the_row(self, db_session, admin, sent, country):
        row = self._row(db_session)
        signup_watch.alert_admins(db_session, row.id)
        db_session.refresh(row)
        assert row.country == "PK" and country == ["198.51.100.7"]

    def test_non_admins_are_never_messaged(self, db_session, test_user, admin, sent, country):
        mails, _ = sent
        signup_watch.alert_admins(db_session, self._row(db_session).id)
        assert test_user.email not in [m_["to"] for m_ in mails]

    def test_switched_off_logs_but_messages_nobody(self, db_session, admin, sent, country,
                                                   monkeypatch):
        monkeypatch.setattr(settings, "signup_alerts_enabled", False)
        mails, pushes = sent
        row = self._row(db_session)
        result = signup_watch.alert_admins(db_session, row.id)
        assert mails == [] and pushes == []
        db_session.refresh(row)
        assert row.country == "PK" and "skipped" in result

    def test_a_failing_mail_does_not_stop_the_other_admins(self, db_session, admin, country,
                                                           monkeypatch):
        db_session.add(m.User(email="second-admin@vectorae.test", is_admin=True))
        db_session.commit()
        delivered = []

        def _send(**kw):
            if kw["to"] == "admin@vectorae.test":
                raise RuntimeError("mail down")
            delivered.append(kw["to"])
            return "id"

        monkeypatch.setattr("app.services.email_sender.send_email", _send)
        result = signup_watch.alert_admins(db_session, self._row(db_session).id)
        assert delivered == ["second-admin@vectorae.test"] and result["emailed"] == 1

    def test_the_email_escapes_what_the_user_typed(self, db_session, admin, sent, country):
        mails, _ = sent
        user = m.User(email="<b>x</b>@example.com")
        db_session.add(user)
        db_session.flush()
        row = signup_watch.record_signup(db_session, user, "198.51.100.7")
        db_session.commit()
        signup_watch.alert_admins(db_session, row.id)
        assert "<b>x</b>" not in mails[0]["html"] and "&lt;b&gt;" in mails[0]["html"]


class TestAdminPages:
    def test_signups_are_listed_newest_first_with_refusals(self, anon_client, client,
                                                          db_session, admin):
        from datetime import datetime, timedelta, timezone
        db_session.add(m.AccountSecurityEvent(
            event="signup_blocked_vpn", email="vpn@example.com", ip="203.0.113.9",
            ts=datetime.now(timezone.utc) - timedelta(hours=1)))
        db_session.commit()
        _signup(anon_client)
        rows = client.get("/admin/signups", headers=auth_headers(admin)).json()
        assert [r["email"] for r in rows] == ["fresh@example.com", "vpn@example.com"]
        assert rows[0]["outcome"] == "created"
        assert rows[1]["outcome"].startswith("refused")

    def test_only_admins_can_list_signups(self, client):
        assert client.get("/admin/signups").status_code == 403

    def test_users_show_where_each_account_came_from(self, anon_client, client, db_session,
                                                     admin):
        _signup(anon_client)
        user = db_session.execute(select(m.User).where(
            m.User.email == "fresh@example.com")).scalar_one()
        user.personal_country, user.company_name = "PK", "Acme"
        (row,) = _events(db_session)
        row.country = "AE"
        db_session.commit()
        users = client.get("/admin/users", headers=auth_headers(admin)).json()
        fresh = next(u for u in users if u["email"] == "fresh@example.com")
        assert fresh["signup_ip"] == "testclient" and fresh["signup_country"] == "AE"
        assert fresh["declared_country"] == "PK" and fresh["company_name"] == "Acme"
        detail = client.get(f"/admin/users/{user.id}", headers=auth_headers(admin)).json()
        assert detail["signup_country"] == "AE"

    def test_an_older_account_falls_back_to_the_identity_lookup(self, client, db_session,
                                                                admin, test_user):
        test_user.signup_ip, test_user.geo_detected_country = "203.0.113.4", "GB"
        db_session.commit()
        users = client.get("/admin/users", headers=auth_headers(admin)).json()
        row = next(u for u in users if u["email"] == test_user.email)
        assert (row["signup_ip"], row["signup_country"]) == ("203.0.113.4", "GB")


def test_erasing_the_account_clears_its_signup_row(db_session):
    from app.services import account_erasure as ae

    user = m.User(email="leaving@example.com", password_hash="x")
    db_session.add(user)
    db_session.flush()
    row = signup_watch.record_signup(db_session, user, "198.51.100.7")
    row.country = "PK"
    db_session.commit()
    ae.erase_account(db_session, user)
    db_session.expire_all()
    kept = db_session.get(m.AccountSecurityEvent, row.id)
    assert kept is None or (kept.email, kept.ip, kept.country) == (None, None, None)
