"""Shared pytest fixtures for the Vectorae SDK test suite."""
from __future__ import annotations

import datetime
import uuid
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from clienthunter import ClientHunter
from clienthunter.config import Config


# ---------------------------------------------------------------------------
# Shared constants — canonical fake responses mirroring backend schemas
# ---------------------------------------------------------------------------

FAKE_PRODUCT_ID = str(uuid.uuid4())
FAKE_STRATEGY_ID = str(uuid.uuid4())
FAKE_LEAD_ID = str(uuid.uuid4())
NOW_ISO = datetime.datetime.utcnow().isoformat()

PRODUCT_RESP = {
    "id": FAKE_PRODUCT_ID,
    "name": "Test SaaS",
    "description": "A test product",
    "type": "product",
    "created_at": NOW_ISO,
}

STRATEGY_RESP = {
    "id": FAKE_STRATEGY_ID,
    "flow_type": "with_clients",
    "status": "pending",
    "created_at": NOW_ISO,
}

STRATEGY_STATUS_RESP = {
    "id": FAKE_STRATEGY_ID,
    "flow_type": "with_clients",
    "status": "researching",
    "progress": [
        {
            "pipeline": "strategy",
            "done": 9,
            "total": 72,
            "phases": [
                {"phase": 1, "title": "Product Decomposition", "done": 9, "total": 9},
                {"phase": 2, "title": "ICP Definition", "done": 0, "total": 9},
            ],
        }
    ],
    "verification": [],
    "strategy_document_ready": False,
    "gtm_document_ready": False,
    "error": None,
}

LEAD_RESP = {
    "id": FAKE_LEAD_ID,
    "status": "verified",
    "source": "apollo",
    "full_name": "Jane Smith",
    "title": "Head of Engineering",
    "company": "Acme Corp",
    "email": "jane@acme.com",
    "phone": None,
    "created_at": NOW_ISO,
}

LEAD_LIST_RESP = {
    "total": 1,
    "limit": 100,
    "offset": 0,
    "items": [LEAD_RESP],
}

CAMPAIGN_RESP = {
    "strategy_id": FAKE_STRATEGY_ID,
    "campaign_state": "active",
    "campaign_pause_reason": None,
    "leads_by_status": {"verified": 10, "contacted": 5},
    "sent_total": 50,
    "sends_today": 10,
    "daily_cap_today": 50,
    "channels": {
        "email": {"sent_total": 50, "sends_today": 10, "daily_cap_today": 50,
                  "replied": 5, "bounced": 1},
        "whatsapp": {"sent_total": 0, "sends_today": 0, "daily_cap_today": 0},
    },
    "reply_rate": 0.1,
    "bounce_rate": 0.02,
    "bounce_pause_threshold": 0.03,
    "meetings_booked": 2,
    "unsubscribed": 0,
}

ANALYTICS_RESP = {
    "strategy_id": FAKE_STRATEGY_ID,
    "granularity": "day",
    "series": [
        {"bucket": "2026-08-01", "channel": "email", "event": "sent", "count": 20},
    ],
    "variants": {"A": {"sent": 20, "replied": 2}},
    "learning_insights": None,
}

TOKEN_RESP = {
    "access_token": "new_access_tok",
    "refresh_token": "new_refresh_tok",
    "token_type": "bearer",
}

COMPLIANCE_ERROR_RESP = {
    "detail": "gdpr: EU target requires a lawful basis",
    "rule": "gdpr",
}


# ---------------------------------------------------------------------------
# Client fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def client_config() -> Config:
    return Config(
        api_url="http://test.local",
        access_token="test_access_tok",
        refresh_token="test_refresh_tok",
    )


@pytest.fixture
def ch(client_config: Config) -> ClientHunter:
    """A ClientHunter instance with a test config; no real HTTP calls."""
    return ClientHunter(_config=client_config)


# ---------------------------------------------------------------------------
# Mock client fixture for CLI tests
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_ch():
    """A MagicMock that mimics the Vectorae interface for CLI tests."""
    m = MagicMock()

    # Sensible defaults for common method returns
    from clienthunter.models import (
        Analytics, CampaignOverview, CampaignStateUpdate,
        Lead, LeadList, Product, Strategy, StrategyStatus, TokenPair
    )

    m.auth.login.return_value = TokenPair(**TOKEN_RESP)
    m.ping.return_value = True
    m.products.create.return_value = Product(**PRODUCT_RESP)
    m.products.get.return_value = Product(**PRODUCT_RESP)
    m.products.add_past_clients.return_value = []
    m.strategies.create.return_value = Strategy(**STRATEGY_RESP)
    m.strategies.progress.return_value = StrategyStatus(**STRATEGY_STATUS_RESP)
    m.strategies.list.return_value = [StrategyStatus(**STRATEGY_STATUS_RESP)]
    m.leads.list.return_value = LeadList(**LEAD_LIST_RESP)
    m.campaigns.overview.return_value = CampaignOverview(**CAMPAIGN_RESP)
    m.campaigns.stats.return_value = CampaignOverview(**CAMPAIGN_RESP)
    m.campaigns.pause.return_value = CampaignStateUpdate(
        campaign_state="paused_manual", campaign_pause_reason="paused by user"
    )
    m.campaigns.resume.return_value = CampaignStateUpdate(campaign_state="active")
    return m
