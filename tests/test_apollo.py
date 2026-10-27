"""Apollo adapter — ICP translation, capped pagination, mapping, caching.

Request shapes follow Apollo's docs as read 2026-10-27: people search is
POST /mixed_people/api_search with []-suffixed query parameters, the response
carries total_entries (no pagination object), people/match takes query
parameters, and /auth/health answers {"healthy", "is_logged_in"}."""

import json

import httpx

from app.integrations.apollo import ApolloAdapter, icp_to_apollo_params

ICP = {
    "titles": ["Owner", "Operations Manager"],
    "industries": ["fire protection services"],
    "locations": ["Texas, US"],
    "company_size_ranges": ["11,50"],
    "keywords": ["fire inspection", "NFPA"],
}


def q(request) -> dict:
    """A request's query parameters, repeated keys as lists."""
    out: dict = {}
    for key, value in request.url.params.multi_items():
        out.setdefault(key, []).append(value)
    return {k: v if k.endswith("[]") else v[0] for k, v in out.items()}


def make_apollo(fake_redis, handler) -> ApolloAdapter:
    return ApolloAdapter(
        api_key="test-apollo-key",
        redis=fake_redis,
        http=httpx.Client(base_url="https://api.apollo.io/api/v1",
                          transport=httpx.MockTransport(handler)),
        sleep=lambda s: None,
    )


def person(i: int, email: str | None = None) -> dict:
    return {
        "id": f"ap_{i}",
        "name": f"Person {i}",
        "first_name": "Person",
        "last_name": str(i),
        "title": "Owner",
        "email": email,
        "organization": {"name": f"Acme {i}", "primary_domain": f"acme{i}.com"},
        "phone_numbers": [{"sanitized_number": f"+1555000{i:04d}"}],
    }


class TestIcpTranslation:
    def test_full_mapping(self):
        params = icp_to_apollo_params(ICP)
        assert params["person_titles[]"] == ["Owner", "Operations Manager"]
        assert params["person_locations[]"] == ["Texas, US"]
        assert params["organization_num_employees_ranges[]"] == ["11,50"]
        assert params["organization_industries[]"] == ["fire protection services"]
        assert params["q_keywords"] == "fire inspection NFPA"
        assert "q_organization_keyword_tags" not in params  # not a documented filter

    def test_industries_are_lowercased_for_the_exact_filter(self):
        params = icp_to_apollo_params({"industries": ["Hospital & Health Care "]})
        assert params == {"organization_industries[]": ["hospital & health care"]}

    def test_industries_as_keywords(self):
        params = icp_to_apollo_params(ICP, industries_as_keywords=True)
        assert "organization_industries[]" not in params
        assert params["q_keywords"] == "fire inspection NFPA fire protection services"

    def test_missing_keys_are_omitted_not_invented(self):
        assert icp_to_apollo_params({}) == {}
        assert icp_to_apollo_params({"titles": ["CEO"]}) == {"person_titles[]": ["CEO"]}


class TestSearch:
    def test_pagination_respects_max_leads_cap(self, fake_redis):
        pages_served = []

        served = {"n": 0}

        def handler(request):
            assert request.method == "POST" and request.url.path.endswith("/mixed_people/api_search")
            params = q(request)
            page, per_page = int(params["page"]), int(params["per_page"])
            pages_served.append((page, per_page))
            start = served["n"]
            served["n"] += per_page
            return httpx.Response(200, json={
                "total_entries": 5000,
                "people": [person(start + i) for i in range(per_page)],
            })

        adapter = make_apollo(fake_redis, handler)
        leads = adapter.search(ICP, max_leads=60)  # page size settings default 25

        assert len(leads) == 60
        assert pages_served == [(1, 25), (2, 25), (3, 10)], \
            "last page requests only the remainder — never over-fetches paid results"
        assert len({l.external_id for l in leads}) == 60

    def test_stops_when_provider_runs_out(self, fake_redis):
        def handler(request):
            people = [person(i) for i in range(7)] if q(request)["page"] == "1" else []
            return httpx.Response(200, json={"people": people, "total_entries": 7})

        adapter = make_apollo(fake_redis, handler)
        assert len(adapter.search(ICP, max_leads=50)) == 7

    def test_auth_header_sent(self, fake_redis):
        seen = {}

        def handler(request):
            seen["key"] = request.headers.get("X-Api-Key")
            return httpx.Response(200, json={"people": [], "total_entries": 0})

        make_apollo(fake_redis, handler).search(ICP, max_leads=5)
        assert seen["key"] == "test-apollo-key"


class TestMapping:
    def test_locked_email_placeholder_becomes_none(self, fake_redis):
        def handler(request):
            return httpx.Response(200, json={
                "people": [person(1, email="email_not_unlocked@domain.com"),
                           person(2, email="real@acme2.com")],
                "total_entries": 2,
            })

        leads = make_apollo(fake_redis, handler).search(ICP, max_leads=5)
        assert leads[0].email is None, "locked placeholder must not be treated as an email"
        assert leads[1].email == "real@acme2.com"
        assert leads[0].company_domain == "acme1.com"
        assert leads[0].phone == "+15550000001"


class TestEnrichment:
    def test_enrichment_cached_never_pay_twice(self, fake_redis):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(200, json={"person": person(1, email="p1@acme1.com")})

        adapter = make_apollo(fake_redis, handler)
        raw = adapter._to_raw_lead(person(1))

        first = adapter.enrich(raw)
        second = adapter.enrich(raw)
        assert calls["n"] == 1, "second enrichment must be a cache hit"
        assert first.email == second.email == "p1@acme1.com"
        assert first.enrichment  # full raw response stored

    def test_enrich_never_loses_known_fields(self, fake_redis):
        def handler(request):
            return httpx.Response(200, json={"person": {}})  # provider knows nothing

        adapter = make_apollo(fake_redis, handler)
        raw = adapter._to_raw_lead(person(3, email="keep@acme3.com"))
        enriched = adapter.enrich(raw)
        assert enriched.email == "keep@acme3.com"
        assert enriched.external_id == "ap_3"
        assert enriched.company == "Acme 3"


class TestDocumentedContract:
    def test_filters_are_sent_as_query_parameters(self, fake_redis):
        seen = {}

        def handler(request):
            seen.update(q(request))
            seen["body"] = request.content
            return httpx.Response(200, json={"people": [], "total_entries": 0})

        make_apollo(fake_redis, handler).search({"titles": ["Owner", "COO"]}, max_leads=5)
        assert seen["person_titles[]"] == ["Owner", "COO"]
        assert seen["body"] == b""

    def test_per_page_never_exceeds_100(self, fake_redis, monkeypatch):
        from app.config import settings
        monkeypatch.setattr(settings, "apollo_page_size", 250)
        monkeypatch.setattr(settings, "leads_max_per_run", 1000)
        sizes = []

        def handler(request):
            sizes.append(int(q(request)["per_page"]))
            return httpx.Response(200, json={"people": [], "total_entries": 0})

        make_apollo(fake_redis, handler).search({"titles": ["Owner"]}, max_leads=300)
        assert sizes == [100]

    def test_no_exact_industry_match_retries_with_keywords(self, fake_redis):
        calls = []

        def handler(request):
            params = q(request)
            calls.append(params)
            found = "organization_industries[]" not in params
            return httpx.Response(200, json={
                "people": [person(1)] if found else [], "total_entries": int(found)})

        leads = make_apollo(fake_redis, handler).search(ICP, max_leads=5)
        assert [l.external_id for l in leads] == ["ap_1"]
        assert calls[0]["organization_industries[]"] == ["fire protection services"]
        assert calls[1]["q_keywords"] == "fire inspection NFPA fire protection services"
        assert len(calls) == 2

    def test_exact_industry_match_needs_no_retry(self, fake_redis):
        calls = []

        def handler(request):
            calls.append(q(request))
            return httpx.Response(200, json={"people": [person(1)], "total_entries": 1})

        assert len(make_apollo(fake_redis, handler).search(ICP, max_leads=5)) == 1
        assert len(calls) == 1

    def test_no_industries_means_no_retry(self, fake_redis):
        calls = []

        def handler(request):
            calls.append(q(request))
            return httpx.Response(200, json={"people": [], "total_entries": 0})

        assert make_apollo(fake_redis, handler).search({"titles": ["Owner"]}, 5) == []
        assert len(calls) == 1

    def test_enrich_sends_the_person_id_as_a_query_parameter(self, fake_redis):
        seen = {}

        def handler(request):
            seen.update(q(request), path=request.url.path, body=request.content)
            return httpx.Response(200, json={"person": {"id": "ap_9", "contact": {
                "phone_numbers": [{"sanitized_number": "+15550009999"}]}}})

        adapter = make_apollo(fake_redis, handler)
        enriched = adapter.enrich(adapter._to_raw_lead({"id": "ap_9"}))
        assert seen["path"].endswith("/people/match") and seen["id"] == "ap_9"
        assert seen["body"] == b""
        assert enriched.phone == "+15550009999"  # read from person.contact

    def test_health_needs_both_flags(self, fake_redis):
        def answer(body):
            return make_apollo(fake_redis, lambda r: httpx.Response(200, json=body))

        assert answer({"healthy": True, "is_logged_in": True}).health_check() is True
        assert answer({"healthy": True, "is_logged_in": False}).health_check() is False
        assert answer({}).health_check() is False
