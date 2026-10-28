"""Apollo.io adapter — lead sourcing & enrichment.

Auth via APOLLO_API_KEY (env). All calls inherit the shared plumbing:
retry/backoff, Redis caching, structured logging, circuit breaker.

Checked against Apollo's public API docs on 2026-10-27 (docs.apollo.io:
reference/people-api-search, reference/people-enrichment, docs/test-api-key).
People search is POST /mixed_people/api_search: it returns no emails or
phone numbers (contact data comes from people/match, which the pipeline runs
for every sourced lead) and its filters are query parameters named with [].
The old /mixed_people/search is gone from the docs (deprecated for API
callers). tests/test_apollo.py pins each point.
"""

import logging
import math

from app.config import settings
from app.integrations.base import EnrichedLead, LeadSource, RawLead, register_lead_source
from app.integrations.plumbing import BaseHttpAdapter

logger = logging.getLogger(__name__)

# Apollo returns a placeholder instead of null for emails you haven't paid
# to unlock; treat those as "no email" so Hunter can find one instead.
_LOCKED_EMAIL_MARKERS = ("email_not_unlocked", "not_unlocked")


# people-api-search: at most 100 per page and 500 pages (50,000 records).
_SEARCH_PER_PAGE_MAX = 100
_SEARCH_PAGES_MAX = 500


def icp_to_apollo_params(icp_criteria: dict, *, industries_as_keywords: bool = False) -> dict:
    """Translate Victorae's structured ICP criteria into Apollo people
    search query parameters (array filters carry a [] suffix).

    Industries go to organization_industries[], which matches Apollo's own
    industry names exactly (lowercase, e.g. "hospital & health care"). Our
    industries are free text, so with industries_as_keywords they are added
    to q_keywords instead (the search's fallback when the exact filter finds
    nobody).

    Our ICP criteria schema (produced by app/services/icp_extraction.py or
    supplied explicitly by the API caller):
        titles: list[str]                e.g. ["Owner", "Operations Manager"]
        industries: list[str]            e.g. ["fire protection services"]
        locations: list[str]             e.g. ["Texas, US", "Georgia, US"]
        company_size_ranges: list[str]   e.g. ["11,50", "51,200"]
        keywords: list[str]              free-text qualifiers
    """
    params: dict = {}
    if icp_criteria.get("titles"):
        params["person_titles[]"] = list(icp_criteria["titles"])
    if icp_criteria.get("locations"):
        params["person_locations[]"] = list(icp_criteria["locations"])
    if icp_criteria.get("company_size_ranges"):
        # "min,max" strings, e.g. "11,50".
        params["organization_num_employees_ranges[]"] = list(icp_criteria["company_size_ranges"])
    keywords = list(icp_criteria.get("keywords") or [])
    if icp_criteria.get("industries"):
        if industries_as_keywords:
            keywords += list(icp_criteria["industries"])
        else:
            params["organization_industries[]"] = [
                str(i).strip().lower() for i in icp_criteria["industries"]]
    if keywords:
        params["q_keywords"] = " ".join(keywords)
    return params


@register_lead_source
class ApolloAdapter(BaseHttpAdapter, LeadSource):
    provider = "apollo"
    base_url = "https://api.apollo.io/api/v1"

    def __init__(self, api_key: str | None = None, **kwargs):
        super().__init__(**kwargs)
        self.api_key = api_key if api_key is not None else settings.apollo_api_key
        if not self.api_key:
            logger.warning("APOLLO_API_KEY is not set — Apollo calls will fail auth")

    def _auth(self) -> dict:
        # x-api-key header (header names are case-insensitive).
        return {"headers": {
            "X-Api-Key": self.api_key,
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
        }}

    # ------------------------------------------------------------------
    # LeadSource interface
    # ------------------------------------------------------------------

    def search(self, icp_criteria: dict, max_leads: int) -> list[RawLead]:
        """Paginated people search, hard-capped at max_leads.

        If the exact industry filter finds nobody on the first page, the
        search runs once more with the industries as free-text keywords
        (founder choice, 2026-10-27). Search costs Apollo 0 credits, so the
        retry only uses rate limit."""
        max_leads = min(max_leads, settings.leads_max_per_run)
        leads = self._search(icp_to_apollo_params(icp_criteria), max_leads)
        if not leads and icp_criteria.get("industries"):
            logger.info("apollo search: no match on exact industries %s; "
                        "retrying them as keywords", icp_criteria["industries"])
            leads = self._search(
                icp_to_apollo_params(icp_criteria, industries_as_keywords=True), max_leads)
        return leads

    def _search(self, base_params: dict, max_leads: int) -> list[RawLead]:
        leads: list[RawLead] = []
        page = 1
        while len(leads) < max_leads and page <= _SEARCH_PAGES_MAX:
            per_page = min(settings.apollo_page_size, _SEARCH_PER_PAGE_MAX,
                           max_leads - len(leads))
            params = {**base_params, "page": page, "per_page": per_page}
            data = self.call(
                "POST",
                "/mixed_people/api_search",
                params=params,
                cache_ttl=settings.cache_ttl_search_seconds,
                # Apollo bills this search 0 credits; it is still metered per
                # requested lead, as before, so a spend cap errs early.
                cost_units=per_page,
            )
            people = data.get("people") or []
            if not people:
                break
            leads.extend(self._to_raw_lead(p) for p in people)
            # The response has total_entries, not a pagination object.
            total_pages = math.ceil(int(data.get("total_entries") or 0) / per_page)
            if len(people) < per_page or page >= total_pages:
                break
            page += 1
        return leads[:max_leads]

    def enrich(self, lead: RawLead) -> EnrichedLead:
        """Enrich one person. Cached in Redis for
        CACHE_TTL_ENRICHMENT_SECONDS — the same person is never paid for
        twice (cache key is derived from the request payload)."""
        # people/match takes these as query parameters; an Apollo person id
        # from search is the most reliable key.
        if lead.external_id:
            params = {"id": lead.external_id}
        elif lead.email:
            params = {"email": lead.email}
        else:
            params = {
                "first_name": lead.first_name,
                "last_name": lead.last_name,
                "organization_name": lead.company,
            }
        data = self.call(
            "POST",
            "/people/match",
            params={k: v for k, v in params.items() if v},
            cache_ttl=settings.cache_ttl_enrichment_seconds,
        )
        person = data.get("person") or {}
        merged = self._to_raw_lead(person) if person else lead
        # Never lose information we already had.
        merged.source = "apollo"
        merged.external_id = merged.external_id or lead.external_id
        merged.email = merged.email or lead.email
        merged.phone = merged.phone or lead.phone
        merged.full_name = merged.full_name or lead.full_name
        merged.title = merged.title or lead.title
        merged.company = merged.company or lead.company
        merged.company_domain = merged.company_domain or lead.company_domain
        return EnrichedLead.from_raw(merged, enrichment=data)

    def health_check(self) -> bool:
        if self.breaker.is_open():
            return False
        try:
            data = self.call("GET", "/auth/health")
        except Exception:
            return False
        # {"healthy": true, "is_logged_in": true}; a bad key answers 200 too.
        return bool(data.get("healthy")) and bool(data.get("is_logged_in"))

    # ------------------------------------------------------------------
    # Response mapping
    # ------------------------------------------------------------------

    @staticmethod
    def _to_raw_lead(person: dict) -> RawLead:
        org = person.get("organization") or {}
        email = person.get("email")
        if email and any(marker in email for marker in _LOCKED_EMAIL_MARKERS):
            email = None
        phone = None
        # people/match puts numbers under person.contact.phone_numbers.
        numbers = (person.get("phone_numbers")
                   or (person.get("contact") or {}).get("phone_numbers") or [])
        if numbers and isinstance(numbers[0], dict):
            phone = numbers[0].get("sanitized_number") or numbers[0].get("raw_number")
        return RawLead(
            source="apollo",
            external_id=str(person["id"]) if person.get("id") is not None else None,
            full_name=person.get("name"),
            first_name=person.get("first_name"),
            last_name=person.get("last_name"),
            title=person.get("title"),
            company=org.get("name"),
            company_domain=org.get("primary_domain") or org.get("website_url"),
            email=email,
            phone=phone,
            raw=person,
        )
