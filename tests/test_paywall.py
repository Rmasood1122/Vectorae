"""Paywall: no paid plan -> the product refuses to work (402 SUBSCRIPTION_REQUIRED).

Founder decision 2026-11-05: "everyone can use Vectorae without purchasing ...
the app will not load until the user purchases a subscription". Enforced at
get_current_user (the one dependency every signed-in route shares) and at the
connector principal, so no route added later can forget it.

What must hold:
  * gate on + free account -> product routes are 402 with SUBSCRIPTION_REQUIRED;
  * gate on + paid plan / granting subscription / admin / member of a paying
    owner -> product routes work;
  * gate on + free account -> sign-in, billing, onboarding verification, team
    invite acceptance, support tickets and the GDPR routes still work;
  * default: on in production, off elsewhere; REQUIRE_ACTIVE_PLAN=false turns it
    off in production;
  * the exempt list matches whole path segments ("/authx" is not "/auth/").
"""

import uuid

import pytest

from app.config import settings
from app.db import models as m
from app.services import auth as auth_svc

PRODUCT_ROUTE = "/strategies"


@pytest.fixture()
def paywall_on(monkeypatch):
    monkeypatch.setattr(settings, "require_active_plan", True)


def _refused(resp) -> bool:
    return (resp.status_code == 402
            and isinstance(resp.json().get("detail"), dict)
            and resp.json()["detail"].get("error") == "SUBSCRIPTION_REQUIRED")


class TestSwitch:
    def test_off_outside_production_by_default(self, monkeypatch):
        monkeypatch.setattr(settings, "require_active_plan", None)
        monkeypatch.setattr(settings, "app_env", "test")
        assert settings.active_plan_required is False

    def test_on_in_production_by_default(self, monkeypatch):
        monkeypatch.setattr(settings, "require_active_plan", None)
        monkeypatch.setattr(settings, "app_env", "production")
        assert settings.active_plan_required is True

    def test_kill_switch_turns_it_off_in_production(self, monkeypatch):
        monkeypatch.setattr(settings, "require_active_plan", False)
        monkeypatch.setattr(settings, "app_env", "production")
        assert settings.active_plan_required is False


class TestFreeAccountIsRefused:
    def test_product_route_is_402(self, client, paywall_on):
        assert _refused(client.get(PRODUCT_ROUTE))

    @pytest.mark.parametrize("path", ["/crm/dashboard/pipeline", "/inbox", "/me/api-keys", "/meetings",
                                      "/me/style-profile", "/analytics/revenue"])
    def test_other_product_routes_are_402(self, client, paywall_on, path):
        assert _refused(client.get(path))

    def test_gate_off_leaves_free_accounts_alone(self, client, monkeypatch):
        monkeypatch.setattr(settings, "require_active_plan", False)
        assert client.get(PRODUCT_ROUTE).status_code == 200


class TestFreeAccountStillReaches:
    def test_auth_me_reports_no_plan(self, client, paywall_on):
        resp = client.get("/auth/me")
        assert resp.status_code == 200
        assert resp.json()["has_active_plan"] is False

    def test_billing_overview(self, client, paywall_on):
        assert not _refused(client.get("/billing"))
        assert client.get("/billing").status_code == 200

    def test_checkout_terms(self, client, paywall_on):
        assert client.get("/billing/checkout-terms").status_code == 200

    def test_onboarding_verification(self, client, paywall_on):
        assert client.get("/onboarding/verification").status_code == 200

    def test_gdpr_export_and_delete_are_not_paywalled(self, client, paywall_on):
        # Wrong password -> refused for THAT reason, never for not paying.
        export = client.post("/me/data-export", json={"password": "wrong"})
        delete = client.request("DELETE", "/me", json={"password": "wrong", "confirm": "DELETE"})
        assert not _refused(export) and export.status_code != 402
        assert not _refused(delete) and delete.status_code != 402

    def test_deleting_own_data_is_not_paywalled(self, client, paywall_on):
        assert not _refused(client.request("DELETE", "/me/style-profile"))


class TestAdminRoutesKeepTheirOwnAnswer:
    def test_non_admin_gets_403_not_402(self, client, paywall_on):
        assert client.get("/admin/config-warnings").status_code == 403


class TestPayingAccountsPass:
    @pytest.mark.parametrize("status", ["trialing", "active", "past_due"])
    def test_granting_subscription(self, client, db_session, test_user, paywall_on, status):
        db_session.add(m.BillingSubscription(user_id=test_user.id, billing_model="monthly",
                                             tier="growth", status=status))
        db_session.commit()
        assert client.get(PRODUCT_ROUTE).status_code == 200

    @pytest.mark.parametrize("status", ["incomplete", "canceled"])
    def test_non_granting_subscription_is_refused(self, client, db_session, test_user,
                                                  paywall_on, status):
        db_session.add(m.BillingSubscription(user_id=test_user.id, billing_model="monthly",
                                             tier="growth", status=status))
        db_session.commit()
        assert _refused(client.get(PRODUCT_ROUTE))

    def test_paid_plan(self, client, db_session, test_user, paywall_on):
        test_user.plan = m.PlanTier.GROWTH
        db_session.commit()
        assert client.get(PRODUCT_ROUTE).status_code == 200

    def test_admin(self, client, db_session, test_user, paywall_on):
        test_user.is_admin = True
        db_session.commit()
        assert client.get(PRODUCT_ROUTE).status_code == 200


class TestExemptList:
    @pytest.mark.parametrize("method,path", [
        ("GET", "/auth/me"), ("POST", "/auth/logout"), ("GET", "/billing"),
        ("POST", "/billing/checkout"), ("GET", "/onboarding/verification"),
        ("POST", "/workspaces/invitations/accept"), ("POST", "/support/tickets"),
        ("POST", "/me/data-export"), ("DELETE", "/me"), ("DELETE", "/me/voice-clone"),
        ("GET", "/me/theme"), ("GET", "/billing/"), ("GET", "/admin/config-warnings"),
    ])
    def test_exempt(self, method, path):
        assert auth_svc.plan_exempt(method, path) is True

    @pytest.mark.parametrize("method,path", [
        ("GET", "/strategies"), ("GET", "/leads"), ("GET", "/authx"),
        ("GET", "/billingx"), ("GET", "/me/voice-clone"), ("POST", "/support/chat"),
        ("GET", "/workspaces/current"), ("GET", "/adminx"),
        ("POST", "/onboardingx"), ("GET", "/"),
    ])
    def test_not_exempt(self, method, path):
        assert auth_svc.plan_exempt(method, path) is False


class TestConnector:
    def test_connector_refuses_a_free_account(self, db_session, test_user, paywall_on, monkeypatch):
        from app.mcp import auth as mcp_auth

        monkeypatch.setattr(mcp_auth, "connector_session_live", lambda *a, **k: True)
        claims = {"sub": str(test_user.id), "sid": str(uuid.uuid4()),
                  "fam": str(uuid.uuid4()), "iat": 9_999_999_999}
        with pytest.raises(mcp_auth.PrincipalRejected, match="SUBSCRIPTION_REQUIRED"):
            mcp_auth.load_principal(db_session, claims)

    def test_connector_accepts_a_paying_account(self, db_session, test_user, paywall_on,
                                                monkeypatch):
        from app.mcp import auth as mcp_auth

        test_user.plan = m.PlanTier.STARTER
        db_session.commit()
        monkeypatch.setattr(mcp_auth, "connector_session_live", lambda *a, **k: True)
        claims = {"sub": str(test_user.id), "sid": str(uuid.uuid4()),
                  "fam": str(uuid.uuid4()), "iat": 9_999_999_999}
        assert mcp_auth.load_principal(db_session, claims).id == test_user.id
