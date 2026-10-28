"""SDK unit tests — resource methods mocked via respx.

Covers:
- Correct HTTP paths and payloads for every resource method
- Auth header is always present
- 401 → one automatic refresh attempt → retry
- 429 / 5xx backoff triggers retry
- Error shapes map to the correct typed exceptions
"""
from __future__ import annotations

import pytest
import httpx
import respx

from clienthunter import ClientHunter, AuthError, NotFoundError, ValidationError, ComplianceError, RateLimitError, APIError
from clienthunter.config import Config
from clienthunter.models import Product, Strategy, StrategyStatus, LeadList, CampaignOverview

from tests.conftest import (
    PRODUCT_RESP, STRATEGY_RESP, STRATEGY_STATUS_RESP,
    LEAD_LIST_RESP, CAMPAIGN_RESP, ANALYTICS_RESP,
    FAKE_PRODUCT_ID, FAKE_STRATEGY_ID, FAKE_LEAD_ID,
    TOKEN_RESP, COMPLIANCE_ERROR_RESP,
)

BASE = "http://test.local"


def _ch(access_token: str = "test_tok", refresh_token: str = "ref_tok") -> ClientHunter:
    """Build a Vectorae backed by a real httpx.Client (respx intercepts it)."""
    cfg = Config(api_url=BASE, access_token=access_token, refresh_token=refresh_token)
    return ClientHunter(_config=cfg, max_retries=2)


# ---------------------------------------------------------------------------
# Auth header is always injected
# ---------------------------------------------------------------------------


@respx.mock
def test_auth_header_bearer():
    route = respx.get(f"{BASE}/health").mock(return_value=httpx.Response(200, json={}))
    _ch().ping()
    assert route.called
    assert "Authorization" in route.calls[0].request.headers
    assert route.calls[0].request.headers["Authorization"] == "Bearer test_tok"


@respx.mock
def test_auth_header_api_key():
    cfg = Config(api_url=BASE, api_key="sk-mykey")
    ch = ClientHunter(_config=cfg)
    route = respx.get(f"{BASE}/health").mock(return_value=httpx.Response(200, json={}))
    ch.ping()
    assert route.calls[0].request.headers.get("X-API-Key") == "sk-mykey"


# ---------------------------------------------------------------------------
# Products resource
# ---------------------------------------------------------------------------


@respx.mock
def test_products_create():
    respx.post(f"{BASE}/products").mock(
        return_value=httpx.Response(201, json=PRODUCT_RESP)
    )
    ch = _ch()
    product = ch.products.create(
        name="Test SaaS", description="A test", type="product", user_email="a@b.com"
    )
    assert isinstance(product, Product)
    assert product.id == FAKE_PRODUCT_ID
    assert product.name == "Test SaaS"


@respx.mock
def test_products_get():
    respx.get(f"{BASE}/products/{FAKE_PRODUCT_ID}").mock(
        return_value=httpx.Response(200, json=PRODUCT_RESP)
    )
    product = _ch().products.get(FAKE_PRODUCT_ID)
    assert product.id == FAKE_PRODUCT_ID


@respx.mock
def test_products_add_past_clients():
    respx.post(f"{BASE}/products/{FAKE_PRODUCT_ID}/past-clients").mock(
        return_value=httpx.Response(201, json=[
            {"id": "abc", "details": "Acme", "acquisition_story": "LinkedIn",
             "extracted_patterns_json": None}
        ])
    )
    result = _ch().products.add_past_clients(
        FAKE_PRODUCT_ID,
        [{"details": "Acme", "acquisition_story": "LinkedIn"}],
    )
    assert len(result) == 1
    assert result[0].details == "Acme"


# ---------------------------------------------------------------------------
# Strategies resource
# ---------------------------------------------------------------------------


@respx.mock
def test_strategies_create():
    respx.post(f"{BASE}/products/{FAKE_PRODUCT_ID}/strategies").mock(
        return_value=httpx.Response(202, json=STRATEGY_RESP)
    )
    s = _ch().strategies.create(product_id=FAKE_PRODUCT_ID)
    assert isinstance(s, Strategy)
    assert s.status == "pending"


@respx.mock
def test_strategies_get():
    respx.get(f"{BASE}/strategies/{FAKE_STRATEGY_ID}").mock(
        return_value=httpx.Response(200, json=STRATEGY_STATUS_RESP)
    )
    s = _ch().strategies.get(FAKE_STRATEGY_ID)
    assert isinstance(s, StrategyStatus)
    assert len(s.progress) == 1
    assert s.progress[0].pipeline == "strategy"


@respx.mock
def test_strategies_progress_is_alias_for_get():
    respx.get(f"{BASE}/strategies/{FAKE_STRATEGY_ID}").mock(
        return_value=httpx.Response(200, json=STRATEGY_STATUS_RESP)
    )
    s = _ch().strategies.progress(FAKE_STRATEGY_ID)
    assert isinstance(s, StrategyStatus)


# ---------------------------------------------------------------------------
# Leads resource
# ---------------------------------------------------------------------------


@respx.mock
def test_leads_list_no_filter():
    respx.get(f"{BASE}/strategies/{FAKE_STRATEGY_ID}/leads").mock(
        return_value=httpx.Response(200, json=LEAD_LIST_RESP)
    )
    result = _ch().leads.list(FAKE_STRATEGY_ID)
    assert isinstance(result, LeadList)
    assert result.total == 1
    assert result.items[0].email == "jane@acme.com"


@respx.mock
def test_leads_list_with_status_filter():
    route = respx.get(f"{BASE}/strategies/{FAKE_STRATEGY_ID}/leads").mock(
        return_value=httpx.Response(200, json=LEAD_LIST_RESP)
    )
    _ch().leads.list(FAKE_STRATEGY_ID, status="verified")
    assert route.calls[0].request.url.params["status"] == "verified"


# ---------------------------------------------------------------------------
# Campaigns resource
# ---------------------------------------------------------------------------


@respx.mock
def test_campaigns_stats():
    respx.get(f"{BASE}/strategies/{FAKE_STRATEGY_ID}/campaign").mock(
        return_value=httpx.Response(200, json=CAMPAIGN_RESP)
    )
    c = _ch().campaigns.stats(FAKE_STRATEGY_ID)
    assert isinstance(c, CampaignOverview)
    assert c.meetings_booked == 2
    assert c.reply_rate == pytest.approx(0.1)


@respx.mock
def test_campaigns_pause():
    respx.post(f"{BASE}/strategies/{FAKE_STRATEGY_ID}/campaign/pause").mock(
        return_value=httpx.Response(200, json={"campaign_state": "paused_manual",
                                               "campaign_pause_reason": "paused by user"})
    )
    result = _ch().campaigns.pause(FAKE_STRATEGY_ID)
    assert result.campaign_state == "paused_manual"


@respx.mock
def test_campaigns_resume():
    respx.post(f"{BASE}/strategies/{FAKE_STRATEGY_ID}/campaign/resume").mock(
        return_value=httpx.Response(200, json={"campaign_state": "active",
                                               "campaign_pause_reason": None})
    )
    result = _ch().campaigns.resume(FAKE_STRATEGY_ID)
    assert result.campaign_state == "active"


# ---------------------------------------------------------------------------
# Auth resource
# ---------------------------------------------------------------------------


@respx.mock
def test_auth_login_stores_tokens(tmp_path, monkeypatch):
    monkeypatch.setattr("clienthunter.config._CONFIG_DIR", tmp_path / ".clienthunter")
    monkeypatch.setattr("clienthunter.config._CONFIG_FILE",
                        tmp_path / ".clienthunter" / "config.toml")
    respx.post(f"{BASE}/auth/login").mock(
        return_value=httpx.Response(200, json=TOKEN_RESP)
    )
    ch = _ch(access_token="", refresh_token="")
    pair = ch.auth.login("me@example.com", "s3cret")
    assert pair.access_token == "new_access_tok"
    assert ch.config.access_token == "new_access_tok"


@respx.mock
def test_auth_refresh():
    respx.post(f"{BASE}/auth/refresh").mock(
        return_value=httpx.Response(200, json=TOKEN_RESP)
    )
    pair = _ch().auth.refresh("my_refresh_tok")
    assert pair.refresh_token == "new_refresh_tok"


# ---------------------------------------------------------------------------
# 401 → one automatic refresh attempt → retry
# ---------------------------------------------------------------------------


@respx.mock
def test_auto_refresh_on_401(tmp_path, monkeypatch):
    """On a 401, the client refreshes the token once and retries."""
    monkeypatch.setattr("clienthunter.config._CONFIG_DIR", tmp_path / ".clienthunter")
    monkeypatch.setattr("clienthunter.config._CONFIG_FILE",
                        tmp_path / ".clienthunter" / "config.toml")

    # First products call → 401
    # Then refresh call → 200 with new token
    # Retry products call → 200

    call_count = {"n": 0}

    def product_side_effect(request):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return httpx.Response(401, json={"detail": "token expired"})
        return httpx.Response(200, json=PRODUCT_RESP)

    respx.get(f"{BASE}/products/{FAKE_PRODUCT_ID}").mock(side_effect=product_side_effect)
    respx.post(f"{BASE}/auth/refresh").mock(return_value=httpx.Response(200, json=TOKEN_RESP))

    product = _ch().products.get(FAKE_PRODUCT_ID)
    assert product.id == FAKE_PRODUCT_ID
    assert call_count["n"] == 2  # called twice (once failed, once retried)


@respx.mock
def test_double_401_raises_auth_error(tmp_path, monkeypatch):
    """Two consecutive 401s (refresh also fails) → AuthError."""
    monkeypatch.setattr("clienthunter.config._CONFIG_DIR", tmp_path / ".clienthunter")
    monkeypatch.setattr("clienthunter.config._CONFIG_FILE",
                        tmp_path / ".clienthunter" / "config.toml")

    respx.get(f"{BASE}/products/{FAKE_PRODUCT_ID}").mock(
        return_value=httpx.Response(401, json={"detail": "token expired"})
    )
    respx.post(f"{BASE}/auth/refresh").mock(
        return_value=httpx.Response(401, json={"detail": "refresh expired"})
    )
    with pytest.raises(AuthError):
        _ch().products.get(FAKE_PRODUCT_ID)


# ---------------------------------------------------------------------------
# Retry on 429 and 5xx
# ---------------------------------------------------------------------------


@respx.mock
def test_retry_on_429(monkeypatch):
    """429 triggers retry with back-off; eventual success returns data."""
    monkeypatch.setattr("clienthunter.client.time.sleep", lambda _: None)
    call_count = {"n": 0}

    def side_effect(request):
        call_count["n"] += 1
        if call_count["n"] < 2:
            return httpx.Response(429, headers={"Retry-After": "1"},
                                  json={"detail": "rate limited"})
        return httpx.Response(200, json=PRODUCT_RESP)

    respx.get(f"{BASE}/products/{FAKE_PRODUCT_ID}").mock(side_effect=side_effect)
    product = _ch().products.get(FAKE_PRODUCT_ID)
    assert product.id == FAKE_PRODUCT_ID
    assert call_count["n"] == 2


@respx.mock
def test_rate_limit_exhausted_raises(monkeypatch):
    """All retries exhausted on 429 → RateLimitError."""
    monkeypatch.setattr("clienthunter.client.time.sleep", lambda _: None)
    respx.get(f"{BASE}/products/{FAKE_PRODUCT_ID}").mock(
        return_value=httpx.Response(429, json={"detail": "rate limited"})
    )
    with pytest.raises(RateLimitError):
        _ch(access_token="tok").products.get(FAKE_PRODUCT_ID)


@respx.mock
def test_retry_on_503(monkeypatch):
    """503 retries; eventual 200 succeeds."""
    monkeypatch.setattr("clienthunter.client.time.sleep", lambda _: None)
    call_count = {"n": 0}

    def side_effect(request):
        call_count["n"] += 1
        if call_count["n"] < 2:
            return httpx.Response(503, json={"detail": "unavailable"})
        return httpx.Response(200, json=PRODUCT_RESP)

    respx.get(f"{BASE}/products/{FAKE_PRODUCT_ID}").mock(side_effect=side_effect)
    product = _ch().products.get(FAKE_PRODUCT_ID)
    assert product.id == FAKE_PRODUCT_ID


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


@respx.mock
def test_404_raises_not_found():
    respx.get(f"{BASE}/products/{FAKE_PRODUCT_ID}").mock(
        return_value=httpx.Response(404, json={"detail": "product not found"})
    )
    with pytest.raises(NotFoundError):
        _ch().products.get(FAKE_PRODUCT_ID)


@respx.mock
def test_422_validation_error():
    respx.post(f"{BASE}/products").mock(
        return_value=httpx.Response(422, json={"detail": [
            {"loc": ["body", "name"], "msg": "field required", "type": "missing"}
        ]})
    )
    with pytest.raises(ValidationError) as exc_info:
        _ch().products.create(name="", description="", type="product", user_email="x@y.com")
    assert "name" in str(exc_info.value).lower() or "required" in str(exc_info.value).lower()


@respx.mock
def test_422_compliance_error():
    respx.post(f"{BASE}/products/{FAKE_PRODUCT_ID}/strategies").mock(
        return_value=httpx.Response(422, json=COMPLIANCE_ERROR_RESP)
    )
    with pytest.raises(ComplianceError) as exc_info:
        _ch().strategies.create(FAKE_PRODUCT_ID)
    e = exc_info.value
    assert e.rule == "gdpr"
    assert e.remediation is not None  # rule is known, remediation should be populated


@respx.mock
def test_403_raises_auth_error():
    respx.get(f"{BASE}/products/{FAKE_PRODUCT_ID}").mock(
        return_value=httpx.Response(403, json={"detail": "forbidden"})
    )
    with pytest.raises(AuthError):
        _ch().products.get(FAKE_PRODUCT_ID)


@respx.mock
def test_500_exhausted_raises_api_error(monkeypatch):
    monkeypatch.setattr("clienthunter.client.time.sleep", lambda _: None)
    respx.get(f"{BASE}/products/{FAKE_PRODUCT_ID}").mock(
        return_value=httpx.Response(500, json={"detail": "internal server error"})
    )
    with pytest.raises(APIError) as exc_info:
        _ch().products.get(FAKE_PRODUCT_ID)
    assert exc_info.value.status_code == 500
