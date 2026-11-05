"""The bytes Vectorae actually sends to Stripe.

Production 2026-11-05: every live checkout failed with HTTP 500
("TypeError: expected a bytes-like object, tuple found") because
stripe_billing._request handed httpx a LIST of form pairs as `data=`;
httpx accepts only a dict there and streamed the tuples as raw body bytes.
Every other billing test replaces _request wholesale, so the encoding
never ran under test. These run the real _request through httpx with a
MockTransport standing in for api.stripe.com: no network.
"""

from urllib.parse import parse_qsl

import httpx
import pytest

from app.config import settings
from app.integrations import stripe_billing


@pytest.fixture()
def stripe_server(monkeypatch):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        request.read()
        seen.append(request)
        if request.url.path == "/v1/customers":
            return httpx.Response(200, json={"id": "cus_test_1"})
        if request.url.path == "/v1/checkout/sessions":
            return httpx.Response(200, json={"id": "cs_test_1", "url": "https://checkout.example/x"})
        return httpx.Response(404, json={"error": {"message": "no such route"}})

    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_fake_for_wire_test")
    monkeypatch.setattr(stripe_billing, "_http", lambda: httpx.Client(
        base_url="https://api.stripe.test", transport=httpx.MockTransport(handler)))
    return seen


def test_create_customer_sends_a_form_body(stripe_server):
    out = stripe_billing.create_customer(email="buyer@example.com", user_id="u-1")
    assert out["id"] == "cus_test_1"
    req = stripe_server[0]
    assert req.method == "POST"
    assert req.headers["content-type"] == "application/x-www-form-urlencoded"
    assert dict(parse_qsl(req.content.decode())) == {
        "email": "buyer@example.com", "metadata[user_id]": "u-1"}


def test_nested_and_list_keys_survive_encoding(stripe_server):
    tier = {"id": "growth", "name": "Growth", "price_cents": 34_900}
    stripe_billing.create_subscription_checkout(
        customer_id="cus_test_1", user_id="u-1", tier=tier, trial_days=14,
        success_url="https://www.example.com/settings?billing=success",
        cancel_url="https://www.example.com/pricing?checkout=cancelled")
    pairs = parse_qsl(stripe_server[0].content.decode())
    body = dict(pairs)
    assert body["mode"] == "subscription"
    assert body["line_items[0][price_data][unit_amount]"] == "34900"
    assert body["line_items[0][price_data][recurring][interval]"] == "month"
    assert body["success_url"] == "https://www.example.com/settings?billing=success"


def test_a_stripe_error_is_a_stripe_error_not_a_crash(stripe_server, monkeypatch):
    with pytest.raises(stripe_billing.StripeError, match="404"):
        stripe_billing._request("POST", "/v1/nope", {"a": 1})


def test_a_bodyless_call_sends_no_body(stripe_server):
    stripe_billing._request("POST", "/v1/customers")
    assert stripe_server[0].content == b""
    assert "content-type" not in stripe_server[0].headers
