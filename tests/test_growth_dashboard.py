"""Admin > Growth: live sign-ups and purchases, for a fixed list of viewers.

Founder request 2026-11-03: only devsahmad37@gmail.com and four
calendarharvest addresses may see it. The gate is admin AND on the list.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.config import settings
from app.db import models as m
from app.services import growth_dashboard, signup_watch
from tests.conftest import auth_headers

PASSWORD = "Sup3rSecret!"
VIEWER = "rehanmasood@calendarharvest.com"


def _user(db, email, **kw):
    kw.setdefault("email_verified", True)
    user = m.User(email=email, password_hash="x", **kw)
    db.add(user)
    db.commit()
    return user


@pytest.fixture()
def viewer(db_session):
    return _user(db_session, VIEWER, is_admin=True)


class TestWhoCanSeeIt:
    def test_the_founder_list_is_the_default(self):
        assert settings.growth_dashboard_viewer_emails == {
            "devsahmad37@gmail.com", "vectorae@calendarharvest.com",
            "ahmadshahid@calendaharvest.com", "rehanmasood@calendarharvest.com",
            "shahidussain@calendarharvest.com"}

    def test_a_listed_admin_sees_it(self, client, viewer):
        resp = client.get("/admin/growth", headers=auth_headers(viewer))
        assert resp.status_code == 200, resp.text
        assert client.get("/admin/growth/access",
                          headers=auth_headers(viewer)).json() == {"allowed": True}

    def test_the_list_match_ignores_case(self, client, db_session):
        user = _user(db_session, "DevsAhmad37@Gmail.com", is_admin=True)
        assert client.get("/admin/growth", headers=auth_headers(user)).status_code == 200

    def test_an_admin_not_on_the_list_is_refused(self, client, db_session):
        other = _user(db_session, "ops@calendarharvest.com", is_admin=True)
        for path in ("/admin/growth", "/admin/growth/access"):
            resp = client.get(path, headers=auth_headers(other))
            assert resp.status_code == 403
            assert resp.json()["detail"] == "growth dashboard access required"

    def test_a_listed_address_without_the_admin_flag_is_refused(self, client, db_session):
        listed = _user(db_session, "vectorae@calendarharvest.com", is_admin=False)
        resp = client.get("/admin/growth", headers=auth_headers(listed))
        assert resp.status_code == 403
        assert resp.json()["detail"] == "growth dashboard access required"

    def test_an_ordinary_user_is_refused(self, client):
        assert client.get("/admin/growth").status_code == 403

    def test_anonymous_is_refused(self, anon_client):
        assert anon_client.get("/admin/growth").status_code == 401

    def test_a_suspended_listed_admin_is_refused(self, client, db_session):
        user = _user(db_session, "shahidussain@calendarharvest.com", is_admin=True,
                     is_suspended=True)
        assert client.get("/admin/growth", headers=auth_headers(user)).status_code == 403

    def test_an_empty_list_closes_it_to_everyone(self, client, viewer, monkeypatch):
        monkeypatch.setattr(settings, "growth_dashboard_viewers", "")
        assert client.get("/admin/growth", headers=auth_headers(viewer)).status_code == 403

    def test_an_api_key_never_reaches_it(self, client, viewer, monkeypatch):
        # require_admin refuses API-key auth; the growth gate builds on it.
        from app.api import deps
        from types import SimpleNamespace

        request = SimpleNamespace(state=SimpleNamespace(auth_via="api_key", actor=None))
        with pytest.raises(deps.HTTPException) as exc:
            deps.require_growth_viewer(request, viewer)
        assert exc.value.status_code == 403


class TestSignupSource:
    def test_utm_referrer_host_and_landing_path_are_kept(self, anon_client, db_session):
        resp = anon_client.post("/auth/signup", json={
            "email": "lead@example.com", "password": PASSWORD,
            "source": {"utm_source": "linkedin", "utm_medium": "social",
                       "utm_campaign": "nov-launch",
                       "referrer": "https://www.google.com/search?q=private+terms",
                       "landing_path": "/pricing?token=secret#x",
                       "unexpected": "dropped"}})
        assert resp.status_code == 201, resp.text
        (row,) = db_session.execute(select(m.AccountSecurityEvent).where(
            m.AccountSecurityEvent.event == "signup")).scalars().all()
        assert row.details_json == {"source": {
            "utm_source": "linkedin", "utm_medium": "social",
            "utm_campaign": "nov-launch", "referrer_host": "www.google.com",
            "landing_path": "/pricing"}}

    def test_a_signup_without_a_source_still_works_and_stores_none(self, anon_client,
                                                                   db_session):
        resp = anon_client.post("/auth/signup",
                                json={"email": "plain@example.com", "password": PASSWORD})
        assert resp.status_code == 201
        (row,) = db_session.execute(select(m.AccountSecurityEvent).where(
            m.AccountSecurityEvent.event == "signup")).scalars().all()
        assert row.details_json is None

    def test_oversized_or_junk_values_never_refuse_a_signup(self, anon_client, db_session):
        resp = anon_client.post("/auth/signup", json={
            "email": "junk@example.com", "password": PASSWORD,
            "source": {"utm_source": "x" * 5000 + "\x00", "referrer": "not a url",
                       "landing_path": "javascript:alert(1)"}})
        assert resp.status_code == 201, resp.text
        (row,) = db_session.execute(select(m.AccountSecurityEvent).where(
            m.AccountSecurityEvent.event == "signup")).scalars().all()
        source = row.details_json["source"]
        assert len(source["utm_source"]) == 120 and "\x00" not in source["utm_source"]
        assert "landing_path" not in source

    @pytest.mark.parametrize("raw,label", [
        (None, "direct"), ({}, "direct"),
        ({"referrer_host": "news.ycombinator.com"}, "news.ycombinator.com"),
        ({"utm_source": "linkedin", "referrer_host": "t.co"}, "linkedin"),
    ])
    def test_source_label(self, raw, label):
        assert signup_watch.source_label(raw) == label


NOW = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)


def _signup_event(db, user, *, ts, country="US", source=None, ip="198.51.100.7"):
    row = m.AccountSecurityEvent(user_id=user.id, email=user.email, event="signup",
                                 ip=ip, country=country, ts=ts,
                                 details_json={"source": source} if source else None)
    db.add(row)
    db.commit()
    return row


def _sub(db, user, **kw):
    kw.setdefault("billing_model", "monthly")
    kw.setdefault("tier", "growth")
    kw.setdefault("status", "active")
    sub = m.BillingSubscription(user_id=user.id, **kw)
    db.add(sub)
    db.commit()
    return sub


class TestDashboardContent:
    def test_signups_and_purchases_with_where_and_when(self, db_session):
        buyer = _user(db_session, "buyer@acme.test", personal_country="GB",
                      account_type="company", company_name="Acme", company_country="GB",
                      phone_verified=True)
        _signup_event(db_session, buyer, ts=NOW - timedelta(days=3), country="GB",
                      source={"utm_source": "linkedin", "utm_campaign": "nov"})
        _sub(db_session, buyer, renewal_consent_at=NOW - timedelta(days=1),
             price_unit_amount_cents=9900, price_interval="month", price_currency="usd",
             stripe_customer_id="cus_123")
        browser = _user(db_session, "browser@example.com", email_verified=False)
        _signup_event(db_session, browser, ts=NOW - timedelta(hours=2), country="PK")

        data = growth_dashboard.build(db_session, days=30, now=NOW)

        assert [r["email"] for r in data["signups"]] == ["browser@example.com",
                                                         "buyer@acme.test"]
        row = data["signups"][1]
        assert (row["ip_country"], row["declared_country"], row["source"],
                row["utm_campaign"], row["company_name"], row["subscription_status"]) == \
            ("GB", "GB", "linkedin", "nov", "Acme", "active")
        (purchase,) = data["purchases"]
        assert purchase["email"] == "buyer@acme.test"
        assert purchase["purchased_at"] == (NOW - timedelta(days=1)).isoformat()
        assert (purchase["amount_cents"], purchase["currency"], purchase["signup_country"],
                purchase["signup_source"], purchase["days_signup_to_purchase"],
                purchase["stripe_customer_id"]) == (9900, "USD", "GB", "linkedin", 2,
                                                    "cus_123")
        totals = data["totals"]
        assert totals["signups_24h"] == 1 and totals["signups_window"] == 2
        assert totals["verified_in_window"] == 1 and totals["converted_in_window"] == 1
        assert totals["paying_subscriptions"] == 1 and totals["purchases_window"] == 1
        assert totals["mrr_cents_by_currency"] == {"USD": 9900}
        assert dict(data["top_countries"]) == {"GB": 1, "PK": 1}
        assert data["daily"][-1] == {"date": "2026-11-03", "signups": 1, "purchases": 0}

    def test_stub_subscriptions_are_listed_but_never_revenue(self, db_session):
        user = _user(db_session, "stub@example.com")
        _sub(db_session, user, is_stub=True, price_unit_amount_cents=9900,
             price_currency="usd", renewal_consent_at=NOW - timedelta(days=1))
        data = growth_dashboard.build(db_session, days=30, now=NOW)
        assert data["purchases"][0]["is_stub"] is True
        assert data["purchases"][0]["counts_as_revenue"] is False
        assert data["totals"]["mrr_cents_by_currency"] == {}
        assert data["totals"]["paying_subscriptions"] == 0
        assert data["totals"]["purchases_window"] == 0

    def test_annual_is_spread_per_month_and_currencies_never_mix(self, db_session):
        a = _user(db_session, "annual@example.com")
        b = _user(db_session, "euro@example.com")
        _sub(db_session, a, billing_model="annual", price_unit_amount_cents=120000,
             price_interval="year", price_currency="usd")
        _sub(db_session, b, price_unit_amount_cents=5000, price_interval="month",
             price_currency="eur", status="past_due")
        totals = growth_dashboard.build(db_session, days=30, now=NOW)["totals"]
        assert totals["mrr_cents_by_currency"] == {"EUR": 5000, "USD": 10000}

    def test_trialing_and_canceled_do_not_count_as_paying(self, db_session):
        t = _user(db_session, "trial@example.com")
        c = _user(db_session, "gone@example.com")
        _sub(db_session, t, status="trialing", price_unit_amount_cents=9900,
             price_currency="usd")
        _sub(db_session, c, status="canceled", price_unit_amount_cents=9900,
             price_currency="usd", canceled_at=NOW - timedelta(days=2))
        totals = growth_dashboard.build(db_session, days=30, now=NOW)["totals"]
        assert totals["paying_subscriptions"] == 0
        assert totals["trialing_subscriptions"] == 1 and totals["canceled_window"] == 1
        assert totals["mrr_cents_by_currency"] == {}

    def test_the_window_excludes_older_rows_and_is_clamped(self, db_session):
        old = _user(db_session, "old@example.com")
        _signup_event(db_session, old, ts=NOW - timedelta(days=40))
        data = growth_dashboard.build(db_session, days=30, now=NOW)
        assert data["signups"] == [] and data["totals"]["signups_window"] == 0
        assert growth_dashboard.build(db_session, days=9999, now=NOW)["window_days"] == 365

    def test_an_erased_account_shows_as_deleted_not_a_crash(self, db_session):
        db_session.add(m.AccountSecurityEvent(user_id=None, email=None, event="signup",
                                              ts=NOW - timedelta(hours=1)))
        db_session.commit()
        (row,) = growth_dashboard.build(db_session, days=1, now=NOW)["signups"]
        assert row["account_deleted"] is True and row["email"] is None
