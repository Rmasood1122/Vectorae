"""Vectorae sync HTTP client.

Architecture:
    ``ClientHunter`` is the entry point.  It owns an ``httpx.Client`` and
    exposes resource objects (``ch.products``, ``ch.strategies``, etc.) that
    each hold typed methods mapping to a group of backend endpoints.

    The client stays sync only for now — an async variant can be added
    (``AsyncClientHunter``) without changing this module; Chunk 2's CLI is
    sync and the most common scripting use-case is sync.

Auth:
    Priority: API key > JWT token pair > nothing (unauthenticated calls).
    When a JWT access token expires, the client retries the request once
    after transparently refreshing it via POST /auth/refresh.
    A second 401 raises ``AuthError`` — the refresh token itself has expired
    or been revoked; the user must ``init``/``login`` again.

Retry:
    Transient errors (429, 502, 503, 504, 5xx) are retried up to
    ``max_retries`` times (default 3) with exponential back-off:
        wait = min(base * 2 ** attempt, max_wait)
    A ``Retry-After`` header on 429 responses is honoured when present.
    Network errors (``httpx.TransportError``) also trigger retries.

Error mapping (see ``exceptions.py`` for full docstrings):
    401, 403        → AuthError
    404             → NotFoundError
    422 compliance  → ComplianceError
    422 other       → ValidationError
    429             → RateLimitError  (if all retries exhausted)
    5xx             → APIError        (if all retries exhausted)
"""

from __future__ import annotations

import time
import logging
from typing import Any, TYPE_CHECKING
from uuid import UUID

import httpx

from clienthunter import config as _config_module
from clienthunter.config import Config, save as _save_config
from clienthunter.exceptions import (
    APIError,
    AuthError,
    ClientHunterError,
    ComplianceError,
    NotFoundError,
    RateLimitError,
    ValidationError,
)
from clienthunter.models import (
    Analytics,
    CampaignOverview,
    CampaignStateUpdate,
    GmailStatus,
    Lead,
    LeadBatch,
    LeadDetail,
    LeadList,
    PastClient,
    Product,
    Sequence,
    StrategyStatus,
    Strategy,
    TokenPair,
    UserOut,
    WhatsAppTemplate,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_RETRY_STATUSES = frozenset({429, 502, 503, 504})
_DEFAULT_TIMEOUT = 30.0  # seconds
_DEFAULT_MAX_RETRIES = 3
_BACKOFF_BASE = 1.0      # seconds
_BACKOFF_MAX = 60.0      # seconds

# Backend compliance error markers — used to distinguish ComplianceError from
# generic ValidationError on HTTP 422.
_COMPLIANCE_DETAIL_MARKERS = (
    "compliance",
    "gdpr",
    "can_spam",
    "whatsapp",
    "suppression_list",
    "bounce_rate",
    "opt_in",
    "template",
    "unsubscribe",
)


# ---------------------------------------------------------------------------
# Internal HTTP helpers
# ---------------------------------------------------------------------------


def _extract_detail(response: httpx.Response) -> str:
    """Extract a human-readable detail string from a backend error response.

    FastAPI returns ``{"detail": ...}`` for all errors.  The value can be a
    string (custom raises) or a list of dicts (Pydantic validation).  We
    stringify both without ever trusting user-controlled content to execute.
    Never include tokens or auth headers in the output.
    """
    try:
        body = response.json()
    except Exception:
        return response.text[:500] or f"HTTP {response.status_code}"

    detail = body.get("detail", body)
    if isinstance(detail, list):
        parts = []
        for item in detail:
            if isinstance(item, dict):
                loc = ".".join(str(x) for x in item.get("loc", []))
                msg = item.get("msg", "")
                parts.append(f"{loc}: {msg}" if loc else msg)
            else:
                parts.append(str(item))
        return "; ".join(parts) or str(body)
    return str(detail)[:1000]


def _is_compliance(detail_str: str, body: dict[str, Any]) -> tuple[bool, str]:
    """Decide whether a 422 response is a compliance block, and which rule."""
    lower = detail_str.lower()
    for marker in _COMPLIANCE_DETAIL_MARKERS:
        if marker in lower:
            # Try to pull a rule name from the body; fall back to the marker.
            rule = body.get("rule", body.get("error_code", marker))
            return True, str(rule)
    return False, ""


# ---------------------------------------------------------------------------
# Resource classes
# ---------------------------------------------------------------------------


class _AuthResource:
    """Endpoints: POST /auth/signup, POST /auth/login, POST /auth/refresh, GET /auth/me."""

    def __init__(self, client: "ClientHunter") -> None:
        self._c = client

    def signup(self, email: str, password: str) -> TokenPair:
        """Create an account and return a token pair.

        Stores the tokens in the config file automatically.

        Args:
            email: New account email address.
            password: Must be ≥ 8 characters (enforced by the backend).

        Returns:
            ``TokenPair`` with ``access_token`` and ``refresh_token``.
        """
        data = self._c._post("/auth/signup", json={"email": email, "password": password})
        pair = TokenPair.model_validate(data)
        self._c._store_token_pair(pair)
        return pair

    def login(self, email: str, password: str) -> TokenPair:
        """Authenticate and return a token pair.

        Stores the tokens in the config file automatically so subsequent
        SDK calls and CLI commands work without re-authenticating.

        Args:
            email: Account email address.
            password: Account password.

        Returns:
            ``TokenPair`` with ``access_token`` and ``refresh_token``.

        Raises:
            ``AuthError``: if the credentials are invalid.
        """
        data = self._c._post("/auth/login", json={"email": email, "password": password})
        pair = TokenPair.model_validate(data)
        self._c._store_token_pair(pair)
        return pair

    def refresh(self, refresh_token: str | None = None) -> TokenPair:
        """Exchange a refresh token for a new token pair.

        Usually called automatically by the client on 401 responses.  Call
        explicitly if you manage token rotation in your own code.

        Args:
            refresh_token: Defaults to the refresh token in the current config.

        Returns:
            New ``TokenPair``.

        Raises:
            ``AuthError``: if the refresh token is expired or revoked.
            ``ValueError``: if no refresh token is available.
        """
        token = refresh_token or self._c.config.refresh_token
        if not token:
            raise ValueError("no refresh token available; call login() first")
        data = self._c._post("/auth/refresh", json={"refresh_token": token},
                              _skip_refresh=True)
        pair = TokenPair.model_validate(data)
        self._c._store_token_pair(pair)
        return pair

    def me(self) -> UserOut:
        """Return the currently authenticated user.

        Returns:
            ``UserOut`` with ``id``, ``email``, and ``plan``.
        """
        return UserOut.model_validate(self._c._get("/auth/me"))


class _ProductsResource:
    """Endpoints: POST /products, GET /products/{id}, POST /products/{id}/past-clients."""

    def __init__(self, client: "ClientHunter") -> None:
        self._c = client

    def create(
        self,
        name: str,
        description: str,
        type: str,       # "product" | "skill"
        user_email: str,
    ) -> Product:
        """Create a new product or skill entry (intake Step 1).

        Args:
            name: Short product name (≤ 200 chars).
            description: Full description of what you sell or offer.
            type: ``"product"`` or ``"skill"``.
            user_email: Email of the user who owns this product.  Will be
                created automatically if it does not exist yet (pre-auth flow).

        Returns:
            ``Product`` with an assigned ``id`` and ``created_at``.
        """
        data = self._c._post(
            "/products",
            json={
                "name": name,
                "description": description,
                "type": type,
                "user_email": user_email,
            },
        )
        return Product.model_validate(data)

    def get(self, product_id: str | UUID) -> Product:
        """Fetch a product by ID.

        Args:
            product_id: UUID of the product.

        Returns:
            ``Product``.

        Raises:
            ``NotFoundError``: if no product with this ID exists.
        """
        return Product.model_validate(self._c._get(f"/products/{product_id}"))

    def add_past_clients(
        self,
        product_id: str | UUID,
        clients: list[dict[str, str]],
    ) -> list[PastClient]:
        """Submit past-client details for Flow 1 (intake Step 2, "yes" path).

        Claude analyses each client's text and extracts structured patterns
        (industry, company size, acquisition channel, trigger event, etc.).
        These patterns anchor the strategy that the pipeline will build.

        Args:
            product_id: UUID of the product.
            clients: List of dicts, each with ``"details"`` (who the client
                     was) and ``"acquisition_story"`` (how they were found).
                     Minimum 1 entry, maximum 50.

        Returns:
            List of ``PastClient`` objects including extracted pattern JSON.

        Raises:
            ``NotFoundError``: if the product does not exist.
            ``ValidationError``: if any client entry is malformed.
        """
        data = self._c._post(
            f"/products/{product_id}/past-clients",
            json={"clients": clients},
        )
        return [PastClient.model_validate(item) for item in data]


class _StrategiesResource:
    """Endpoints: POST /products/{id}/strategies, GET /strategies/{id},
    GET /strategies (requires backend addition — see note below).
    """

    def __init__(self, client: "ClientHunter") -> None:
        self._c = client

    def create(
        self,
        product_id: str | UUID,
        flow_type: str | None = None,
    ) -> Strategy:
        """Create a strategy and kick off the research pipeline (returns HTTP 202).

        The pipeline runs asynchronously in the cloud.  Poll
        ``strategies.progress(id)`` to watch it advance through phases.

        Args:
            product_id: UUID of the product to research.
            flow_type: ``"with_clients"`` or ``"no_clients"``.  Omit to let
                       the backend infer from whether past clients exist.

        Returns:
            ``Strategy`` with status ``"pending"`` — the pipeline starts
            immediately in the background.

        Raises:
            ``NotFoundError``: if the product does not exist.
            ``ValidationError``: if ``flow_type="with_clients"`` is explicit
                                 but no past clients have been added yet.
        """
        body: dict[str, Any] = {}
        if flow_type is not None:
            body["flow_type"] = flow_type
        data = self._c._post(f"/products/{product_id}/strategies", json=body)
        return Strategy.model_validate(data)

    def get(self, strategy_id: str | UUID) -> StrategyStatus:
        """Fetch full strategy status including pipeline progress and verification passes.

        Args:
            strategy_id: UUID of the strategy.

        Returns:
            ``StrategyStatus`` with per-phase step counts, verification pass
            results, and ready flags for the strategy and GTM documents.

        Raises:
            ``NotFoundError``: if the strategy does not exist.
        """
        return StrategyStatus.model_validate(self._c._get(f"/strategies/{strategy_id}"))

    # Alias preferred by the CLI
    def progress(self, strategy_id: str | UUID) -> StrategyStatus:
        """Alias for ``get()`` — preferred name in progress-display contexts."""
        return self.get(strategy_id)

    def list(self, product_id: str | UUID | None = None) -> list[StrategyStatus]:
        """List strategies, optionally filtered by product.

        NOTE: This method requires ``GET /strategies`` on the backend, which is
        not yet implemented (M1–M5 only expose ``GET /strategies/{id}``).
        The ``clienthunter status`` CLI command needs this endpoint.

        # TODO: Add GET /strategies?product_id=<uuid> to the FastAPI router
        #       (app/api/strategies.py).  The endpoint should JOIN products,
        #       filter by the authenticated user, and return a list of
        #       StrategyStatusOut (reusing the existing serialisation).
        #       Flag this to the backend team before releasing the CLI.

        Args:
            product_id: If given, return only strategies for this product.

        Returns:
            List of ``StrategyStatus`` objects.

        Raises:
            ``APIError``: until the backend endpoint is deployed (returns 404
                          on the unimplemented route).
        """
        params: dict[str, str] = {}
        if product_id is not None:
            params["product_id"] = str(product_id)
        data = self._c._get("/strategies", params=params)
        items = data if isinstance(data, list) else data.get("items", [])
        return [StrategyStatus.model_validate(item) for item in items]


class _LeadsResource:
    """Endpoints: GET /strategies/{id}/leads, GET /strategies/{id}/leads/{lead_id},
    POST /strategies/{id}/leads/source.
    """

    def __init__(self, client: "ClientHunter") -> None:
        self._c = client

    def list(
        self,
        strategy_id: str | UUID,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> LeadList:
        """Paginated list of leads for a strategy.

        Args:
            strategy_id: UUID of the strategy.
            status: Filter by lead status (e.g. ``"verified"``, ``"contacted"``,
                    ``"meeting_booked"``).  Omit for all statuses.
            limit: Max leads to return (1–1000, default 100).
            offset: Pagination offset (default 0).

        Returns:
            ``LeadList`` with ``total``, ``limit``, ``offset``, and ``items``.
        """
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if status is not None:
            params["status"] = status
        data = self._c._get(f"/strategies/{strategy_id}/leads", params=params)
        return LeadList.model_validate(data)

    def get(self, strategy_id: str | UUID, lead_id: str | UUID) -> LeadDetail:
        """Fetch full lead detail including enrichment JSON.

        Args:
            strategy_id: UUID of the strategy.
            lead_id: UUID of the lead.

        Returns:
            ``LeadDetail`` with enrichment payload and batch metadata.

        Raises:
            ``NotFoundError``: if the lead or strategy does not exist.
        """
        data = self._c._get(f"/strategies/{strategy_id}/leads/{lead_id}")
        return LeadDetail.model_validate(data)

    def source(
        self,
        strategy_id: str | UUID,
        max_leads: int | None = None,
        icp_criteria: dict[str, Any] | None = None,
    ) -> LeadBatch:
        """Kick off a new Apollo lead-sourcing batch for a strategy.

        The batch runs asynchronously.  Poll ``GET /strategies/{id}/leads``
        to see leads appear as the batch progresses through sourcing →
        enriching → finding emails → verifying.

        Args:
            strategy_id: UUID of a VERIFIED strategy (the pipeline must have
                         completed all 10 verification passes before sourcing).
            max_leads: Upper cap for this batch (max 1000).  Defaults to the
                       server-side ``LEADS_MAX_PER_RUN`` setting.
            icp_criteria: Explicit Apollo search criteria
                          (``titles``, ``industries``, ``locations``,
                          ``company_size_ranges``, ``keywords``).  Omit to
                          extract criteria automatically from Phase 2 research.

        Returns:
            ``LeadBatch`` with the batch ``id`` and initial ``stage``.
        """
        body: dict[str, Any] = {}
        if max_leads is not None:
            body["max_leads"] = max_leads
        if icp_criteria is not None:
            body["icp_criteria"] = icp_criteria
        data = self._c._post(f"/strategies/{strategy_id}/leads/source", json=body)
        return LeadBatch.model_validate(data)


class _CampaignsResource:
    """Endpoints: GET /strategies/{id}/campaign, GET /strategies/{id}/analytics,
    POST /strategies/{id}/campaign/pause, POST /strategies/{id}/campaign/resume.
    """

    def __init__(self, client: "ClientHunter") -> None:
        self._c = client

    def overview(self, strategy_id: str | UUID) -> CampaignOverview:
        """Real-time campaign snapshot: lead counts, sends, reply rate, etc.

        Args:
            strategy_id: UUID of the strategy.

        Returns:
            ``CampaignOverview`` with per-channel statistics.
        """
        data = self._c._get(f"/strategies/{strategy_id}/campaign")
        return CampaignOverview.model_validate(data)

    # Alias used by the CLI ``status`` command
    def stats(self, strategy_id: str | UUID) -> CampaignOverview:
        """Alias for ``overview()``."""
        return self.overview(strategy_id)

    def analytics(
        self,
        strategy_id: str | UUID,
        granularity: str = "day",
    ) -> Analytics:
        """Time-series analytics for a strategy's campaign.

        Args:
            strategy_id: UUID of the strategy.
            granularity: ``"day"``, ``"week"``, or ``"month"``.

        Returns:
            ``Analytics`` with a time series of outcome events and
            per-variant A/B aggregates.
        """
        data = self._c._get(
            f"/strategies/{strategy_id}/analytics",
            params={"granularity": granularity},
        )
        return Analytics.model_validate(data)

    def pause(self, strategy_id: str | UUID) -> CampaignStateUpdate:
        """Manually pause a campaign.

        The campaign will not send any more messages until resumed.  Use
        this when you want to review replies or update the strategy before
        continuing.

        Args:
            strategy_id: UUID of the strategy.

        Returns:
            ``CampaignStateUpdate`` confirming the new state.
        """
        data = self._c._post(f"/strategies/{strategy_id}/campaign/pause", json={})
        return CampaignStateUpdate.model_validate(data)

    def resume(self, strategy_id: str | UUID) -> CampaignStateUpdate:
        """Resume a manually or bounce-paused campaign.

        Resuming after a bounce-pause is a deliberate human decision.  The
        backend's 3% bounce monitor will re-pause if bounce rate stays high.

        Args:
            strategy_id: UUID of the strategy.

        Returns:
            ``CampaignStateUpdate`` confirming the new state.
        """
        data = self._c._post(f"/strategies/{strategy_id}/campaign/resume", json={})
        return CampaignStateUpdate.model_validate(data)


class _WhatsAppResource:
    """Endpoints under /whatsapp/templates."""

    def __init__(self, client: "ClientHunter") -> None:
        self._c = client

    def list_templates(self) -> list[WhatsAppTemplate]:
        """List all WhatsApp message templates (newest first).

        Returns:
            List of ``WhatsAppTemplate`` objects.
        """
        data = self._c._get("/whatsapp/templates")
        return [WhatsAppTemplate.model_validate(t) for t in data.get("templates", [])]

    def get_template(self, template_id: str | UUID) -> WhatsAppTemplate:
        """Fetch a single WhatsApp template.

        Args:
            template_id: UUID of the template.

        Returns:
            ``WhatsAppTemplate``.

        Raises:
            ``NotFoundError``: if the template does not exist.
        """
        return WhatsAppTemplate.model_validate(
            self._c._get(f"/whatsapp/templates/{template_id}")
        )

    def create_template(
        self,
        name: str,
        language: str,
        body: str,
        category: str = "marketing",
        variable_descriptions: dict[str, str] | None = None,
    ) -> WhatsAppTemplate:
        """Create a draft WhatsApp message template.

        The template is saved as DRAFT and must be submitted for Meta review
        via ``submit_template()`` before it can be sent.  Only APPROVED
        templates may ever be sent to cold contacts.

        Args:
            name: Lowercase snake_case identifier (e.g. ``"intro_offer_v1"``).
            language: BCP-47 language code (e.g. ``"en_US"``).
            body: Template text with ``{{1}}`` ``{{2}}`` placeholders.
            category: ``"marketing"`` (default) or ``"utility"``.
            variable_descriptions: Maps ``"1"`` → description of that variable.

        Returns:
            ``WhatsAppTemplate`` with status ``"draft"``.
        """
        payload: dict[str, Any] = {
            "name": name,
            "language": language,
            "body": body,
            "category": category,
            "variable_descriptions": variable_descriptions or {},
        }
        data = self._c._post("/whatsapp/templates", json=payload)
        return WhatsAppTemplate.model_validate(data)

    def submit_template(self, template_id: str | UUID) -> WhatsAppTemplate:
        """Submit a DRAFT template for Meta review.

        Only templates in ``"draft"`` or ``"rejected"`` status can be submitted.

        Args:
            template_id: UUID of the template.

        Returns:
            ``WhatsAppTemplate`` with updated status (usually ``"submitted"``).
        """
        data = self._c._post(f"/whatsapp/templates/{template_id}/submit", json={})
        return WhatsAppTemplate.model_validate(data)


class _IntegrationsResource:
    """Integration status/health endpoints."""

    def __init__(self, client: "ClientHunter") -> None:
        self._c = client

    def gmail_status(self, user_email: str) -> GmailStatus:
        """Check whether Gmail is connected for a user.

        Args:
            user_email: Email address of the user to check.

        Returns:
            ``GmailStatus`` with ``connected`` flag and account details.
        """
        data = self._c._get(
            "/integrations/gmail/status", params={"user_email": user_email}
        )
        return GmailStatus.model_validate(data)

    def gmail_auth_url(self, user_email: str) -> str:
        """Get the Gmail OAuth authorisation URL.

        Open this URL in a browser to grant Vectorae access to send email
        on behalf of the user.  The OAuth callback is handled by the backend.

        Args:
            user_email: Email address of the user to connect.

        Returns:
            OAuth URL string.
        """
        data = self._c._get(
            "/integrations/gmail/auth-url", params={"user_email": user_email}
        )
        return data["auth_url"]


# ---------------------------------------------------------------------------
# Main client
# ---------------------------------------------------------------------------


class ClientHunter:
    """Typed sync client for the Vectorae API.

    Usage (with stored config)::

        ch = ClientHunter()
        ch.auth.login("you@example.com", "secret")
        product = ch.products.create(name="My App", ...)

    Usage (with explicit config)::

        ch = ClientHunter(api_url="http://localhost:8000", api_key="sk-...")

    Usage (with env vars)::

        # Set CLIENTHUNTER_API_URL and CLIENTHUNTER_API_KEY in environment.
        ch = ClientHunter()

    All methods raise subclasses of ``ClientHunterError`` on failure —
    never bare ``httpx`` exceptions.  See ``exceptions.py`` for the full
    hierarchy and compliance-error remediation guidance.
    """

    def __init__(
        self,
        api_url: str | None = None,
        api_key: str | None = None,
        access_token: str | None = None,
        refresh_token: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        _config: Config | None = None,
    ) -> None:
        """
        Args:
            api_url: Override the backend URL.  Falls back to env var
                     ``CLIENTHUNTER_API_URL`` → config file → default.
            api_key: Static API key.  Falls back to env var
                     ``CLIENTHUNTER_API_KEY`` → config file.
            access_token: JWT access token (takes precedence over config file).
            refresh_token: JWT refresh token (takes precedence over config file).
            timeout: Request timeout in seconds (default 30).
            max_retries: How many times to retry transient errors (default 3).
            _config: Pre-built ``Config`` object (testing / advanced use).
        """
        if _config is not None:
            self.config = _config
        else:
            self.config = _config_module.load()

        # Explicit constructor args take precedence over loaded config
        if api_url is not None:
            self.config.api_url = api_url.rstrip("/")
        if api_key is not None:
            self.config.api_key = api_key
        if access_token is not None:
            self.config.access_token = access_token
        if refresh_token is not None:
            self.config.refresh_token = refresh_token

        self._timeout = timeout
        self._max_retries = max_retries
        self._http = httpx.Client(timeout=timeout)

        # Resource objects
        self.auth = _AuthResource(self)
        self.products = _ProductsResource(self)
        self.strategies = _StrategiesResource(self)
        self.leads = _LeadsResource(self)
        self.campaigns = _CampaignsResource(self)
        self.whatsapp = _WhatsAppResource(self)
        self.integrations = _IntegrationsResource(self)

    def __enter__(self) -> "ClientHunter":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying httpx connection pool."""
        self._http.close()

    # ------------------------------------------------------------------
    # Token management
    # ------------------------------------------------------------------

    def _store_token_pair(self, pair: TokenPair) -> None:
        """Update in-memory config and persist to disk.  Never logged."""
        self.config.access_token = pair.access_token
        self.config.refresh_token = pair.refresh_token
        try:
            _save_config(self.config)
        except Exception as exc:
            # Config persistence failure should not break the API call.
            logger.warning("could not persist token to config file: %s", type(exc).__name__)

    def _auth_headers(self) -> dict[str, str]:
        """Build auth headers from the current config.

        Priority: API key > access token > nothing.
        Secrets are never logged — this method must not be called from log
        statements.
        """
        if self.config.api_key:
            # TODO: verify the backend header name once API-key auth is added
            #       to app/api/auth.py.  Placeholder: X-API-Key.
            return {"X-API-Key": self.config.api_key}
        if self.config.access_token:
            return {"Authorization": f"Bearer {self.config.access_token}"}
        return {}

    # ------------------------------------------------------------------
    # Core request machinery
    # ------------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        _skip_refresh: bool = False,
    ) -> Any:
        """Execute an HTTP request with retry and error mapping.

        Args:
            method: HTTP method (``"GET"``, ``"POST"``, etc.).
            path: API path starting with ``/`` (e.g. ``"/products"``).
            params: Query string parameters.
            json: Request body (serialised automatically).
            _skip_refresh: Internal flag to prevent infinite refresh loops.

        Returns:
            Parsed JSON body (dict or list).

        Raises:
            ``AuthError``, ``NotFoundError``, ``ValidationError``,
            ``ComplianceError``, ``RateLimitError``, ``APIError``.
        """
        url = f"{self.config.api_url}{path}"
        last_exc: ClientHunterError | None = None

        for attempt in range(self._max_retries + 1):
            if attempt > 0:
                wait = min(_BACKOFF_BASE * (2 ** (attempt - 1)), _BACKOFF_MAX)
                logger.debug("retry %d/%d after %.1fs", attempt, self._max_retries, wait)
                time.sleep(wait)

            try:
                response = self._http.request(
                    method,
                    url,
                    params=params,
                    json=json,
                    headers=self._auth_headers(),
                )
            except httpx.TransportError as exc:
                last_exc = APIError(str(exc))
                continue

            status = response.status_code

            # --- Success ---
            if status < 400:
                if not response.content:
                    return {}
                try:
                    return response.json()
                except Exception:
                    return {}

            # --- 401: try one token refresh then retry ---
            if status == 401 and not _skip_refresh and self.config.refresh_token:
                logger.debug("access token expired, attempting refresh")
                try:
                    self.auth.refresh()
                    # Retry the original request (attempt does not consume a slot)
                    try:
                        response2 = self._http.request(
                            method,
                            url,
                            params=params,
                            json=json,
                            headers=self._auth_headers(),
                        )
                    except httpx.TransportError as exc:
                        raise APIError(str(exc)) from exc
                    if response2.status_code < 400:
                        return response2.json() if response2.content else {}
                    # Second 401 — refresh didn't help
                    raise AuthError(_extract_detail(response2))
                except (AuthError, ValueError):
                    raise
                except ClientHunterError:
                    raise AuthError("token refresh failed; please login again")

            # --- 401 / 403 ---
            if status in (401, 403):
                raise AuthError(_extract_detail(response))

            # --- 404 ---
            if status == 404:
                raise NotFoundError(detail=_extract_detail(response))

            # --- 422: compliance vs. validation ---
            if status == 422:
                detail_str = _extract_detail(response)
                try:
                    body = response.json()
                except Exception:
                    body = {}
                is_comp, rule = _is_compliance(detail_str, body)
                if is_comp:
                    raise ComplianceError(rule=rule, detail=detail_str)
                raise ValidationError(body.get("detail", detail_str))

            # --- 429: respect Retry-After ---
            if status == 429:
                retry_after_raw = response.headers.get("Retry-After")
                retry_after = float(retry_after_raw) if retry_after_raw else None
                last_exc = RateLimitError(retry_after)
                if retry_after and attempt < self._max_retries:
                    time.sleep(min(retry_after, _BACKOFF_MAX))
                continue

            # --- 5xx: retryable ---
            if status >= 500:
                last_exc = APIError(_extract_detail(response), status_code=status)
                continue

            # --- Other 4xx: not retryable ---
            raise APIError(_extract_detail(response), status_code=status)

        # All retries exhausted
        assert last_exc is not None
        raise last_exc

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._request("GET", path, params=params)

    def _post(
        self,
        path: str,
        json: Any = None,
        _skip_refresh: bool = False,
    ) -> Any:
        return self._request("POST", path, json=json, _skip_refresh=_skip_refresh)

    def _delete(self, path: str) -> Any:
        return self._request("DELETE", path)

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    def ping(self) -> bool:
        """Return True if the backend is reachable and healthy.

        Used by ``clienthunter init`` to verify connectivity after setup.
        Does not require authentication.

        Returns:
            ``True`` on success.

        Raises:
            ``APIError``: if the backend is unreachable or unhealthy.
        """
        try:
            self._get("/health")
            return True
        except (APIError, httpx.TransportError) as exc:
            raise APIError(f"health check failed: {exc}") from exc
