"""Stripe over its REST API (no SDK dependency) — Section E.

The repo had no payments code, so this follows the house integration style
(httpx, a monkeypatchable `_http()` factory, one error type) rather than
adding the `stripe` package. Stripe's API is form-encoded; `_form()` flattens
nested dicts into `a[b][c]=v` keys the way the official SDKs do.

STUB MODE. With no STRIPE_SECRET_KEY every call raises StripeNotConfigured,
and app/services/billing.py takes its explicit stub path instead (activating
plans locally with is_stub=True). Nothing here pretends to have charged.
    # TODO: connect live Stripe keys (STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET)

WEBHOOKS are verified with Stripe's scheme: header
`Stripe-Signature: t=<unix>,v1=<hex hmac_sha256(secret, "<t>.<raw body>")>`,
constant-time compared, with a timestamp tolerance against replay.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from typing import Any
from urllib.parse import urlencode

import httpx

from app.config import settings
from app.core.exceptions import ClientHunterError

logger = logging.getLogger(__name__)


class StripeError(ClientHunterError):
    """Stripe refused or could not be reached. The message can carry Stripe's
    own response text, so it is never put in an API response as is."""

    user_safe = False


class StripeNotConfigured(StripeError):
    """No STRIPE_SECRET_KEY: the caller must take its stub path."""


def _http() -> httpx.Client:
    """Factory tests monkeypatch."""
    return httpx.Client(base_url=settings.stripe_api_base,
                        timeout=settings.stripe_timeout_seconds)


def _form(data: dict[str, Any], prefix: str = "") -> list[tuple[str, str]]:
    """{"a": {"b": 1}, "c": [{"d": 2}]} -> [("a[b]", "1"), ("c[0][d]", "2")]."""
    out: list[tuple[str, str]] = []
    for key, value in data.items():
        name = f"{prefix}[{key}]" if prefix else str(key)
        if value is None:
            continue
        if isinstance(value, dict):
            out.extend(_form(value, name))
        elif isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                item_name = f"{name}[{index}]"
                if isinstance(item, dict):
                    out.extend(_form(item, item_name))
                else:
                    out.append((item_name, _scalar(item)))
        else:
            out.append((name, _scalar(value)))
    return out


def _scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _request(method: str, path: str, data: dict | None = None,
             idempotency_key: str | None = None) -> dict:
    if settings.billing_stub_mode:
        raise StripeNotConfigured("STRIPE_SECRET_KEY is not set")
    headers = {"Stripe-Version": "2024-06-20"}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    # Stripe takes form-encoded bodies with bracketed keys (a[b]=1, c[0]=x),
    # which a dict cannot express when a key repeats. Encode the pairs here:
    # httpx's `data=` accepts only a dict, and given this list it streamed
    # the tuples as raw bytes and every live call died with
    # "TypeError: expected a bytes-like object, tuple found" (prod, 2026-11-05).
    pairs = _form(data or {})
    content = urlencode(pairs).encode() if pairs else None
    if content is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    try:
        with _http() as client:
            resp = client.request(method, path, content=content,
                                  headers=headers,
                                  auth=(settings.stripe_secret_key, ""))
    except httpx.HTTPError as exc:
        raise StripeError(f"stripe transport error: {exc}") from exc
    try:
        body = resp.json() or {}
    except ValueError:
        body = {}
    if resp.status_code >= 400:
        message = ((body.get("error") or {}).get("message")) or resp.text[:200]
        raise StripeError(f"stripe HTTP {resp.status_code}: {message}")
    return body


# --------------------------------------------------------------------------
# Objects
# --------------------------------------------------------------------------


def create_customer(*, email: str, user_id: str) -> dict:
    return _request("POST", "/v1/customers",
                    {"email": email, "metadata": {"user_id": user_id}},
                    idempotency_key=f"customer-{user_id}")


def create_subscription_checkout(*, customer_id: str, user_id: str, tier: dict,
                                 trial_days: int, success_url: str, cancel_url: str,
                                 price_id: str | None = None,
                                 billing_model: str = "monthly",
                                 interval: str = "month",
                                 unit_amount_cents: int | None = None,
                                 trial_end: int | None = None,
                                 session_metadata: dict | None = None) -> dict:
    """Checkout Session in subscription mode for one monthly or annual tier.

    `interval` is the Stripe recurring interval ("month" | "year") and
    `unit_amount_cents` the per-interval amount (defaults to the tier's
    monthly price). `trial_end` (unix seconds) -- set when an active pilot
    pushes the trial past its verdict -- replaces `trial_period_days`; Stripe
    accepts one or the other. With no trial (an account that already had
    one) NEITHER is sent: Stripe's trial_period_days minimum is 1.
    `session_metadata` (the consent record) goes on the Checkout Session
    only; the webhook reads it back when the checkout completes."""
    if interval not in ("month", "year"):
        raise ValueError(f"unsupported interval {interval!r}")
    amount = int(unit_amount_cents if unit_amount_cents is not None else tier["price_cents"])
    if price_id:
        line_item: dict[str, Any] = {"price": price_id, "quantity": 1}
    else:
        suffix = " (annual)" if interval == "year" else ""
        line_item = {"quantity": 1, "price_data": {
            "currency": "usd", "unit_amount": amount,
            "recurring": {"interval": interval},
            "product_data": {"name": f"Vectorae {tier['name']}{suffix}"},
        }}
    metadata = {"user_id": user_id, "billing_model": billing_model, "tier": tier["id"]}
    subscription_data: dict[str, Any] = {"metadata": metadata}
    if trial_end is not None:
        subscription_data["trial_end"] = int(trial_end)
    elif trial_days and int(trial_days) > 0:
        subscription_data["trial_period_days"] = int(trial_days)
    return _request("POST", "/v1/checkout/sessions", {
        "mode": "subscription", "customer": customer_id,
        "line_items": [line_item],
        "subscription_data": subscription_data,
        "metadata": {**(session_metadata or {}), **metadata},
        "client_reference_id": user_id,
        "success_url": success_url, "cancel_url": cancel_url,
    })


def retrieve_subscription(subscription_id: str) -> dict:
    """The subscription object itself (status, trial, period, price). The
    checkout-completed event does not carry it, and events can arrive in any
    order, so the handler reads Stripe's current state instead of guessing.
    Raises StripeError ("stripe HTTP 404: ...") when Stripe has no such
    subscription (app/services/account_erasure.py uses this to tell "already
    gone" from "could not cancel")."""
    return _request("GET", f"/v1/subscriptions/{subscription_id}")


def expire_checkout_session(session_id: str) -> dict:
    """Close an OPEN Checkout Session so it can no longer complete. Stripe
    refuses (400) a session that is already complete or expired; callers
    treat that as nothing to do."""
    return _request("POST", f"/v1/checkout/sessions/{session_id}/expire")


def void_open_invoices(subscription_id: str) -> list[str]:
    """Void every OPEN invoice of a subscription, so a cancelled past-due
    period is never collected later. Returns the voided invoice ids."""
    listing = _request("GET", f"/v1/invoices?subscription={subscription_id}&status=open")
    voided = []
    for invoice in listing.get("data") or []:
        if invoice.get("id") and invoice.get("status") == "open":
            _request("POST", f"/v1/invoices/{invoice['id']}/void",
                     idempotency_key=f"void-{invoice['id']}")
            voided.append(invoice["id"])
    return voided


def create_setup_checkout(*, customer_id: str, user_id: str, success_url: str,
                          cancel_url: str) -> dict:
    """Checkout Session in setup mode: save a card for per-meeting charges."""
    metadata = {"user_id": user_id, "billing_model": "pay_per_meeting",
                "tier": "pay_per_meeting"}
    return _request("POST", "/v1/checkout/sessions", {
        "mode": "setup", "customer": customer_id, "currency": "usd",
        "payment_method_types": ["card"], "metadata": metadata,
        "setup_intent_data": {"metadata": metadata},
        "client_reference_id": user_id,
        "success_url": success_url, "cancel_url": cancel_url,
    })


def create_one_time_checkout(*, customer_id: str, user_id: str, amount_cents: int,
                            currency: str, description: str, success_url: str,
                            cancel_url: str, session_metadata: dict | None = None) -> dict:
    """Checkout Session in payment mode: one-time purchase (e.g. credit top-up)."""
    metadata = {
        "user_id": user_id,
        "purchase_type": "ppm_credits",
        **(session_metadata or {})
    }
    return _request("POST", "/v1/checkout/sessions", {
        "mode": "payment",
        "customer": customer_id,
        "currency": currency,
        "line_items": [{
            "price_data": {
                "currency": currency,
                "product_data": {"name": description},
                "unit_amount": amount_cents,
            },
            "quantity": 1,
        }],
        "metadata": metadata,
        "client_reference_id": user_id,
        "success_url": success_url,
        "cancel_url": cancel_url,
    })


def cancel_subscription_at_period_end(subscription_id: str) -> dict:
    return _request("POST", f"/v1/subscriptions/{subscription_id}",
                    {"cancel_at_period_end": True})


def cancel_subscription_now(subscription_id: str) -> dict:
    return _request("DELETE", f"/v1/subscriptions/{subscription_id}")


def charge_meeting(*, customer_id: str, amount_cents: int, currency: str,
                   description: str, meeting_id: str) -> dict:
    """One invoice for one meeting: invoice item -> invoice -> finalize.

    Idempotency keys are derived from the meeting id, so a retried sweep can
    never create a second invoice for the same meeting.
    """
    metadata = {"billable_meeting_id": meeting_id}
    item = _request("POST", "/v1/invoiceitems", {
        "customer": customer_id, "amount": amount_cents, "currency": currency,
        "description": description, "metadata": metadata,
    }, idempotency_key=f"meeting-item-{meeting_id}")
    invoice = _request("POST", "/v1/invoices", {
        "customer": customer_id, "collection_method": "charge_automatically",
        "auto_advance": True, "pending_invoice_items_behavior": "include",
        "description": description, "metadata": metadata,
    }, idempotency_key=f"meeting-invoice-{meeting_id}")
    finalized = _request("POST", f"/v1/invoices/{invoice['id']}/finalize",
                         idempotency_key=f"meeting-finalize-{meeting_id}")
    return {"invoice_item_id": item.get("id"), "invoice_id": finalized.get("id"),
            "status": finalized.get("status")}


# --------------------------------------------------------------------------
# Webhooks
# --------------------------------------------------------------------------


def verify_webhook(raw_body: bytes, header: str | None, *, secret: str | None = None,
                   now: float | None = None) -> bool:
    secret = secret if secret is not None else settings.stripe_webhook_secret
    if not secret or not header:
        return False
    parts: dict[str, list[str]] = {}
    for piece in header.split(","):
        key, _, value = piece.strip().partition("=")
        parts.setdefault(key, []).append(value)
    try:
        timestamp = int((parts.get("t") or [""])[0])
    except ValueError:
        return False
    tolerance = max(int(settings.stripe_webhook_tolerance_seconds), 1)
    if abs((now if now is not None else time.time()) - timestamp) > tolerance:
        return False
    expected = hmac.new(secret.encode(), f"{timestamp}.".encode() + raw_body,
                        hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, candidate) for candidate in parts.get("v1", []))


def sign_webhook(raw_body: bytes, secret: str, timestamp: int | None = None) -> str:
    """The header Stripe would send. For tests and local webhook replay."""
    ts = int(timestamp if timestamp is not None else time.time())
    digest = hmac.new(secret.encode(), f"{ts}.".encode() + raw_body,
                      hashlib.sha256).hexdigest()
    return f"t={ts},v1={digest}"
