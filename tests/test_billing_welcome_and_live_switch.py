"""Post-payment welcome email, and test-mode leftovers after switching to live keys.

WELCOME (founder ask 2026-11-05). After an account's first completed checkout,
email a welcome with the sign-in link and the onboarding-call link. Once per
account (billing_subscriptions.welcome_email_sent_at, migration 0098). Never a
password or other credential. A mail failure never fails the webhook.

LIVE SWITCH (production 2026-11-05). Accounts that bought with TEST keys keep a
test-mode customer and subscription id. Under LIVE keys Stripe answers
"No such customer" / "No such subscription" (code resource_missing):
  * checkout makes a fresh customer once and retries instead of a 502;
  * cancel ends a subscription Stripe does not know locally (nothing can bill
    it) instead of a 502 that leaves the plan running forever.
"""

import pytest

from app.config import settings
from app.db import models as m
from app.integrations import stripe_billing
from app.services import billing
from tests.test_billing import (  # noqa: F401 -- fixtures
    CONSENT,
    _event,
    _post_webhook,
    live_stripe,
    webhook_secret,
)


def _complete(client, user, *, subscription="sub_1", tier="growth", model="monthly"):
    meta = {"user_id": str(user.id), "billing_model": model, "tier": tier}
    obj = {"mode": "subscription" if model != "pay_per_meeting" else "setup",
           "customer": "cus_123", "metadata": meta}
    if model != "pay_per_meeting":
        obj["subscription"] = subscription
    return _post_webhook(client, _event("checkout.session.completed", obj))


def _welcomes(mailbox):
    return [msg for msg in mailbox if msg["subject"].startswith("Welcome to Vectorae")]


class TestWelcomeEmail:
    def test_first_checkout_sends_one_welcome(self, client, test_user, live_stripe,
                                              webhook_secret, mailbox, monkeypatch):
        monkeypatch.setattr(settings, "frontend_url", "https://www.example.com")
        monkeypatch.setattr(settings, "onboarding_call_url", "https://calendly.com/x/30min")
        assert _complete(client, test_user).json()["result"] == "activated"
        sent = _welcomes(mailbox)
        assert len(sent) == 1
        msg = sent[0]
        assert msg["to"] == test_user.email
        assert "Growth plan is active" in msg["text"]
        assert "https://www.example.com/login" in msg["text"]
        assert "https://calendly.com/x/30min" in msg["text"]
        assert 'href="https://www.example.com/login"' in msg["html"]

    def test_never_contains_a_password(self, client, test_user, live_stripe,
                                       webhook_secret, mailbox):
        _complete(client, test_user)
        body = _welcomes(mailbox)[0]["text"].lower()
        assert "password you chose" in body          # tells them to use their own
        assert "your password is" not in body and "temporary password" not in body

    def test_redelivered_event_and_plan_change_send_nothing_more(
            self, client, db_session, test_user, live_stripe, webhook_secret, mailbox):
        _complete(client, test_user, subscription="sub_1")
        _complete(client, test_user, subscription="sub_1")          # Stripe retry
        _complete(client, test_user, subscription="sub_2", tier="scale")  # plan change
        assert len(_welcomes(mailbox)) == 1
        sub = billing.get_subscription(db_session, test_user.id)
        db_session.refresh(sub)
        assert sub.welcome_email_sent_at is not None

    def test_pay_per_meeting_names_the_plan(self, client, test_user, live_stripe,
                                            webhook_secret, mailbox):
        _complete(client, test_user, model="pay_per_meeting", tier="pay_per_meeting")
        assert "Pay per meeting plan is active" in _welcomes(mailbox)[0]["text"]

    def test_no_booking_line_when_the_link_is_unset(self, client, test_user, live_stripe,
                                                    webhook_secret, mailbox, monkeypatch):
        monkeypatch.setattr(settings, "onboarding_call_url", "")
        _complete(client, test_user)
        assert "onboarding call" not in _welcomes(mailbox)[0]["text"]

    def test_mail_failure_does_not_fail_the_webhook_and_retries_later(
            self, client, db_session, test_user, live_stripe, webhook_secret, mailbox,
            monkeypatch):
        from app.services import email_sender

        real = email_sender.send_email

        def broken(**_kw):
            raise email_sender.EmailSendError("smtp down")

        monkeypatch.setattr(email_sender, "send_email", broken)
        resp = _complete(client, test_user, subscription="sub_1")
        assert resp.status_code == 200 and resp.json()["result"] == "activated"
        sub = billing.get_subscription(db_session, test_user.id)
        db_session.refresh(sub)
        assert sub.welcome_email_sent_at is None

        monkeypatch.setattr(email_sender, "send_email", real)
        _complete(client, test_user, subscription="sub_2", tier="scale")
        assert len(_welcomes(mailbox)) == 1


def _missing(param):
    return stripe_billing.StripeError("stripe HTTP 400: No such thing",
                                      code="resource_missing", param=param, status=400)


class TestTestModeLeftovers:
    def test_checkout_replaces_a_customer_stripe_does_not_know(
            self, client, db_session, test_user, live_stripe):
        db_session.add(m.BillingSubscription(user_id=test_user.id, billing_model="monthly",
                                             tier="enterprise", status="canceled",
                                             stripe_customer_id="cus_TESTMODE"))
        db_session.commit()
        attempts = []

        def checkout(kwargs):
            attempts.append(kwargs["customer_id"])
            if kwargs["customer_id"] == "cus_TESTMODE":
                raise _missing("customer")
            return {"id": "cs_new", "url": "https://checkout.stripe.com/c/pay/cs_new"}

        live_stripe["install"]("create_subscription_checkout", checkout)
        resp = client.post("/billing/checkout", json={"billing_model": "monthly",
                                                      "tier": "starter", **CONSENT})
        assert resp.status_code == 200, resp.text
        assert resp.json()["checkout_url"].endswith("cs_new")
        assert attempts == ["cus_TESTMODE", "cus_123"]
        assert len(live_stripe["create_customer"]) == 1
        sub = billing.get_subscription(db_session, test_user.id)
        db_session.refresh(sub)
        assert sub.stripe_customer_id == "cus_123"

    def test_other_stripe_errors_are_still_a_502(self, client, db_session, test_user,
                                                 live_stripe):
        db_session.add(m.BillingSubscription(user_id=test_user.id, billing_model="monthly",
                                             tier="enterprise", status="canceled",
                                             stripe_customer_id="cus_REAL"))
        db_session.commit()
        live_stripe["install"]("create_subscription_checkout",
                               stripe_billing.StripeError("card network down"))
        resp = client.post("/billing/checkout", json={"billing_model": "monthly",
                                                      "tier": "starter", **CONSENT})
        assert resp.status_code == 502
        assert "create_customer" not in live_stripe   # no customer churn on a real outage

    def test_a_missing_price_is_not_mistaken_for_a_missing_customer(
            self, client, db_session, test_user, live_stripe):
        db_session.add(m.BillingSubscription(user_id=test_user.id, billing_model="monthly",
                                             tier="enterprise", status="canceled",
                                             stripe_customer_id="cus_REAL"))
        db_session.commit()
        live_stripe["install"]("create_subscription_checkout", _missing("line_items[0][price]"))
        resp = client.post("/billing/checkout", json={"billing_model": "monthly",
                                                      "tier": "starter", **CONSENT})
        assert resp.status_code == 502
        assert "create_customer" not in live_stripe

    def test_cancel_ends_a_subscription_stripe_does_not_know(
            self, client, db_session, test_user, live_stripe, monkeypatch):
        db_session.add(m.BillingSubscription(user_id=test_user.id, billing_model="monthly",
                                             tier="enterprise", status="trialing",
                                             stripe_subscription_id="sub_TESTMODE"))
        test_user.plan = m.PlanTier.ENTERPRISE
        db_session.commit()

        def gone(subscription_id):
            raise _missing("id")

        monkeypatch.setattr(stripe_billing, "cancel_subscription_at_period_end", gone)
        resp = client.post("/billing/cancel")
        assert resp.status_code == 200, resp.text
        sub = billing.get_subscription(db_session, test_user.id)
        db_session.refresh(sub)
        db_session.refresh(test_user)
        assert sub.status == "canceled"
        assert test_user.plan is m.PlanTier.FREE

    def test_cancel_still_502s_on_a_real_outage(self, client, db_session, test_user,
                                                live_stripe, monkeypatch):
        db_session.add(m.BillingSubscription(user_id=test_user.id, billing_model="monthly",
                                             tier="growth", status="active",
                                             stripe_subscription_id="sub_live"))
        db_session.commit()

        def down(subscription_id):
            raise stripe_billing.StripeError("timeout")

        monkeypatch.setattr(stripe_billing, "cancel_subscription_at_period_end", down)
        assert client.post("/billing/cancel").status_code == 502
        sub = billing.get_subscription(db_session, test_user.id)
        db_session.refresh(sub)
        assert sub.status == "active" and not sub.cancel_at_period_end


class TestErrorCodesFromTheWire:
    def test_request_carries_stripes_error_code(self, monkeypatch):
        import httpx

        def handler(request):
            return httpx.Response(400, json={"error": {
                "code": "resource_missing", "param": "customer",
                "message": "No such customer: 'cus_x'"}})

        monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_fake")
        monkeypatch.setattr(stripe_billing, "_http", lambda: httpx.Client(
            base_url="https://api.stripe.test", transport=httpx.MockTransport(handler)))
        with pytest.raises(stripe_billing.StripeError) as info:
            stripe_billing._request("POST", "/v1/checkout/sessions", {"customer": "cus_x"})
        assert info.value.missing and info.value.param == "customer"
        assert info.value.status == 400
