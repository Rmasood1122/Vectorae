"""Hunter adapter (finder + verifier mapping) and adapter contract tests.

The contract classes run the SAME assertions against every implementation
of an interface — real adapters (HTTP mocked) and in-memory fakes alike —
which is what makes new providers plug-compatible."""

import httpx
import pytest

from app.db.models import LeadStatus
from app.integrations.apollo import ApolloAdapter
from app.integrations.base import (
    EmailVerificationStatus,
    EnrichedLead,
    LeadSource,
    RawLead,
    VerificationResult,
    get_email_verifier,
    get_lead_source,
    registered_email_verifiers,
    registered_lead_sources,
)
from app.integrations.hunter import HunterAdapter
from tests.conftest import FakeLeadSource, FakeVerifier, make_raw


def make_hunter(fake_redis, handler) -> HunterAdapter:
    return HunterAdapter(
        api_key="test-hunter-key",
        redis=fake_redis,
        http=httpx.Client(base_url="https://api.hunter.io/v2",
                          transport=httpx.MockTransport(handler)),
        sleep=lambda s: None,
    )


class TestHunterFinder:
    def test_find_email_returns_address_and_sends_api_key(self, fake_redis):
        seen = {}

        def handler(request):
            seen["api_key"] = request.url.params.get("api_key")
            seen["domain"] = request.url.params.get("domain")
            return httpx.Response(200, json={"data": {"email": "jane.doe@acme.com", "score": 92}})

        email = make_hunter(fake_redis, handler).find_email("Jane Doe", "acme.com")
        assert email == "jane.doe@acme.com"
        assert seen["api_key"] == "test-hunter-key"
        assert seen["domain"] == "acme.com"

    def test_find_email_none_when_not_found(self, fake_redis):
        handler = lambda r: httpx.Response(200, json={"data": {"email": None}})
        assert make_hunter(fake_redis, handler).find_email("Jane Doe", "acme.com") is None

    def test_find_email_requires_inputs(self, fake_redis):
        adapter = make_hunter(fake_redis, lambda r: httpx.Response(200, json={}))
        assert adapter.find_email("", "acme.com") is None
        assert adapter.find_email("Jane Doe", "") is None

    def test_finder_results_cached(self, fake_redis):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(200, json={"data": {"email": "j@a.com"}})

        adapter = make_hunter(fake_redis, handler)
        adapter.find_email("Jane Doe", "acme.com")
        adapter.find_email("Jane Doe", "acme.com")
        assert calls["n"] == 1


class TestHunterVerifierMapping:
    @pytest.mark.parametrize("provider_result,expected_status,expected_lead_status", [
        ("deliverable", EmailVerificationStatus.DELIVERABLE, LeadStatus.VERIFIED),
        ("risky", EmailVerificationStatus.RISKY, LeadStatus.FLAGGED),
        ("undeliverable", EmailVerificationStatus.UNDELIVERABLE, LeadStatus.DROPPED),
        ("something_new", EmailVerificationStatus.UNKNOWN, LeadStatus.FLAGGED),
    ])
    def test_status_mapping(self, fake_redis, provider_result, expected_status, expected_lead_status):
        def handler(request):
            return httpx.Response(200, json={"data": {"result": provider_result, "score": 71}})

        result = make_hunter(fake_redis, handler).verify("x@y.com")
        assert result.status is expected_status
        assert result.lead_status is expected_lead_status
        assert result.score == 71
        assert result.raw["result"] == provider_result


# --------------------------------------------------------------------------
# Contract tests — every implementation must satisfy these
# --------------------------------------------------------------------------

def _apollo_for_contract(fake_redis) -> ApolloAdapter:
    def handler(request):
        if "mixed_people/api_search" in str(request.url):
            per_page = int(request.url.params["per_page"])
            people = [{"id": f"c{i}", "name": f"C {i}", "title": "CEO",
                       "organization": {"name": f"Org {i}", "primary_domain": f"org{i}.com"}}
                      for i in range(per_page)]
            return httpx.Response(200, json={"people": people, "total_entries": per_page})
        return httpx.Response(200, json={"person": {"id": "c0", "name": "C 0", "email": "c0@org0.com",
                                                    "organization": {"name": "Org 0"}}})
    return ApolloAdapter(api_key="k", redis=fake_redis,
                         http=httpx.Client(base_url="https://api.apollo.io/api/v1",
                                           transport=httpx.MockTransport(handler)),
                         sleep=lambda s: None)


def _fake_source_for_contract() -> FakeLeadSource:
    return FakeLeadSource(feed=[make_raw(i) for i in range(10)])


@pytest.fixture(params=["apollo", "fakesource"])
def any_lead_source(request, fake_redis) -> LeadSource:
    return _apollo_for_contract(fake_redis) if request.param == "apollo" else _fake_source_for_contract()


class TestLeadSourceContract:
    def test_search_respects_max_leads(self, any_lead_source):
        leads = any_lead_source.search({"titles": ["CEO"]}, max_leads=3)
        assert len(leads) <= 3
        assert all(isinstance(l, RawLead) for l in leads)

    def test_leads_carry_source_and_ids(self, any_lead_source):
        leads = any_lead_source.search({}, max_leads=5)
        assert all(l.source for l in leads)
        ids = [l.external_id for l in leads]
        assert len(ids) == len(set(ids)), "external ids must be unique within a search"

    def test_enrich_returns_enriched_lead_preserving_identity(self, any_lead_source):
        lead = any_lead_source.search({}, max_leads=1)[0]
        enriched = any_lead_source.enrich(lead)
        assert isinstance(enriched, EnrichedLead)
        assert enriched.external_id == lead.external_id

    def test_health_check_returns_bool(self, any_lead_source):
        assert isinstance(any_lead_source.health_check(), bool)


@pytest.fixture(params=["hunter", "fakeverifier"])
def any_verifier(request, fake_redis):
    if request.param == "hunter":
        return make_hunter(fake_redis,
                           lambda r: httpx.Response(200, json={"data": {
                               "email": "found@acme.com", "result": "deliverable", "score": 90}}))
    return FakeVerifier()


class TestEmailVerifierContract:
    def test_verify_returns_verification_result_with_lead_status(self, any_verifier):
        result = any_verifier.verify("a@b.com")
        assert isinstance(result, VerificationResult)
        assert isinstance(result.status, EmailVerificationStatus)
        assert isinstance(result.lead_status, LeadStatus)

    def test_find_email_returns_str_or_none(self, any_verifier):
        out = any_verifier.find_email("Jane Doe", "acme.com")
        assert out is None or isinstance(out, str)


class TestRegistry:
    def test_real_adapters_are_registered(self):
        assert "apollo" in registered_lead_sources()
        assert "hunter" in registered_email_verifiers()

    def test_factories_instantiate(self):
        assert get_lead_source("apollo").provider == "apollo"
        assert get_email_verifier("hunter").provider == "hunter"

    def test_unknown_provider_error_lists_known_ones(self):
        with pytest.raises(KeyError, match="apollo"):
            get_lead_source("linkedin")
