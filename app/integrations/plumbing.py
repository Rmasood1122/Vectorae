"""Shared plumbing every adapter gets for free.

- Rate-limit handling: HTTP 429/5xx retried with exponential backoff,
  honoring Retry-After when the provider sends it.
- Response caching: Redis, per-call configurable TTL, deterministic keys —
  we never pay twice for the same search/enrichment.
- Structured logging: every external call logs one JSON record with
  provider, endpoint, status, latency_ms, cost_units, cached, attempt.
- Circuit breaker: per provider, state shared across all workers via
  Redis; after N consecutive failures the provider is paused for a
  cooldown and calls fail fast with CircuitOpenError.

Adapters subclass BaseHttpAdapter and get all of the above via `call()`.
"""

import hashlib
import json
import logging
import time

import httpx
import redis as redis_lib

from app.config import settings

logger = logging.getLogger("leadpilot.external")

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------


class ExternalAPIError(Exception):
    """A provider call failed after all retries."""

    def __init__(self, provider: str, endpoint: str, detail: str, status: int | None = None,
                 body: str | None = None):
        self.provider, self.endpoint, self.status = provider, endpoint, status
        # The provider's error body (truncated), for callers that decide by
        # the provider's own reason code rather than the status alone.
        self.body = body
        super().__init__(f"[{provider}] {endpoint}: {detail}")

    def provider_reason(self) -> str | None:
        """errors[0].reason from a Google-style JSON error body, else None."""
        try:
            error = json.loads(self.body or "").get("error") or {}
            return (error.get("errors") or [{}])[0].get("reason") or error.get("status")
        except (ValueError, AttributeError, IndexError):
            return None


class RateLimitExceeded(ExternalAPIError):
    """429s persisted through every retry."""


class OutcomeUnknown(ExternalAPIError):
    """A non-idempotent request (for example sending an email) may have taken
    effect: it timed out after being sent, or the provider answered 5xx.
    Never retried automatically -- retrying could do it twice."""


class CircuitOpenError(Exception):
    """Provider is paused by its circuit breaker — fail fast, retry later."""

    def __init__(self, provider: str, retry_in_seconds: int):
        self.provider, self.retry_in_seconds = provider, retry_in_seconds
        super().__init__(f"[{provider}] circuit open — retry in ~{retry_in_seconds}s")


# --------------------------------------------------------------------------
# Redis handle
# --------------------------------------------------------------------------

_redis: redis_lib.Redis | None = None


def get_redis() -> redis_lib.Redis:
    """Lazy shared Redis client (tests inject their own, e.g. fakeredis)."""
    global _redis
    if _redis is None:
        _redis = redis_lib.Redis.from_url(settings.redis_url, decode_responses=True)
    return _redis


# --------------------------------------------------------------------------
# Circuit breaker (state in Redis — shared by every worker/process)
# --------------------------------------------------------------------------


class CircuitBreaker:
    def __init__(
        self,
        provider: str,
        redis: redis_lib.Redis | None = None,
        failure_threshold: int | None = None,
        cooldown_seconds: int | None = None,
    ):
        self.provider = provider
        self.redis = redis or get_redis()
        self.failure_threshold = failure_threshold or settings.circuit_failure_threshold
        self.cooldown_seconds = cooldown_seconds or settings.circuit_cooldown_seconds
        self._fail_key = f"cb:{provider}:failures"
        self._open_key = f"cb:{provider}:open"

    def check(self) -> None:
        """Raise CircuitOpenError if the provider is currently paused."""
        ttl = self.redis.ttl(self._open_key)
        if ttl and ttl > 0:
            raise CircuitOpenError(self.provider, ttl)

    def record_success(self) -> None:
        self.redis.delete(self._fail_key)

    def record_failure(self) -> None:
        failures = self.redis.incr(self._fail_key)
        # Failure counter itself decays, so old failures don't linger.
        self.redis.expire(self._fail_key, max(self.cooldown_seconds, 60))
        if int(failures) >= self.failure_threshold:
            self.redis.setex(self._open_key, self.cooldown_seconds, "1")
            self.redis.delete(self._fail_key)
            logger.warning(
                json.dumps({
                    "event": "circuit_opened",
                    "provider": self.provider,
                    "cooldown_seconds": self.cooldown_seconds,
                })
            )

    def is_open(self) -> bool:
        ttl = self.redis.ttl(self._open_key)
        return bool(ttl and ttl > 0)


# --------------------------------------------------------------------------
# Response cache
# --------------------------------------------------------------------------


class ResponseCache:
    def __init__(self, provider: str, redis: redis_lib.Redis | None = None):
        self.provider = provider
        self.redis = redis or get_redis()

    def key(self, endpoint: str, payload: dict | None) -> str:
        digest = hashlib.sha256(
            json.dumps(payload or {}, sort_keys=True, default=str).encode()
        ).hexdigest()[:32]
        return f"cache:{self.provider}:{endpoint}:{digest}"

    def get(self, endpoint: str, payload: dict | None) -> dict | None:
        value = self.redis.get(self.key(endpoint, payload))
        return json.loads(value) if value else None

    def set(self, endpoint: str, payload: dict | None, data: dict, ttl: int) -> None:
        self.redis.setex(self.key(endpoint, payload), ttl, json.dumps(data, default=str))


# --------------------------------------------------------------------------
# HTTP adapter base — retry, cache, breaker and logging in one place
# --------------------------------------------------------------------------


class BaseHttpAdapter:
    """Subclass per provider. Override `provider`, `base_url` and `_auth()`.

    All outbound calls go through `call()`, which applies (in order):
    circuit-breaker check -> cache lookup -> retried HTTP request with
    backoff + Retry-After -> breaker bookkeeping -> cache write ->
    structured log.
    """

    provider: str = "override-me"
    base_url: str = "https://override-me.invalid"

    def __init__(
        self,
        redis: redis_lib.Redis | None = None,
        http: httpx.Client | None = None,
        max_retries: int | None = None,
        sleep=time.sleep,
    ):
        self.redis = redis or get_redis()
        self.http = http or httpx.Client(base_url=self.base_url, timeout=30.0)
        self.max_retries = max_retries or settings.external_max_retries
        self.cache = ResponseCache(self.provider, self.redis)
        self.breaker = CircuitBreaker(self.provider, self.redis)
        self._sleep = sleep  # injectable so tests never actually sleep
        # F01: what the most recent successful call() cost -- {"cached": bool,
        # "cost_units": int} -- so a caller that keeps a cost ledger (the
        # email waterfall) can tell a paid call from a cache hit. None until
        # a call succeeds; never read by the adapter itself.
        self.last_call: dict | None = None

    # -- override in subclasses ------------------------------------------
    def _auth(self) -> dict:
        """Return {'headers': {...}} and/or {'params': {...}} merged into
        every request. Secrets come from settings (env), never hard-coded."""
        return {}

    # -- the one entry point ----------------------------------------------
    def call(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict | None = None,
        json_body: dict | None = None,
        cache_ttl: int | None = None,
        cost_units: int = 1,
        idempotent: bool = True,
    ) -> dict:
        """idempotent=False (sending a message): a request that may have
        reached the provider is never repeated. A connection that was never
        opened, or a 429 (the provider refused it), is still retried; a
        timeout after sending or a 5xx raises OutcomeUnknown at once."""
        self.breaker.check()

        cache_payload = {"m": method, "p": params, "j": json_body}
        if cache_ttl:
            cached = self.cache.get(endpoint, cache_payload)
            if cached is not None:
                self._log(endpoint, method, status=200, latency_ms=0,
                          cost_units=0, cached=True, attempt=0)
                self.last_call = {"cached": True, "cost_units": 0}
                return cached

        # F07 Spend Guard: a paid call (cache miss) is checked against the
        # account's monthly cap BEFORE the request. No-op outside a guarded run.
        from app.services import spend_guard  # noqa: PLC0415

        spend_guard.before_data_call(self.provider, cost_units)

        auth = self._auth()
        headers = auth.get("headers", {})
        merged_params = {**(auth.get("params") or {}), **(params or {})}

        last_error: str = "unknown"
        last_status: int | None = None
        for attempt in range(1, self.max_retries + 1):
            start = time.monotonic()
            try:
                resp = self.http.request(
                    method, endpoint, params=merged_params or None,
                    json=json_body, headers=headers,
                )
            except httpx.HTTPError as exc:  # connect/timeout errors
                last_error, last_status = f"{type(exc).__name__}: {exc}", None
                if not idempotent and not isinstance(exc, httpx.ConnectError):
                    # ConnectError (incl. ConnectTimeout): nothing was sent.
                    self.breaker.record_failure()
                    raise OutcomeUnknown(self.provider, endpoint,
                                         f"outcome unknown ({last_error})") from exc
                self._backoff(attempt, retry_after=None)
                continue

            latency_ms = int((time.monotonic() - start) * 1000)

            if resp.status_code in _RETRYABLE_STATUS:
                last_error = f"HTTP {resp.status_code}"
                last_status = resp.status_code
                self._log(endpoint, method, resp.status_code, latency_ms,
                          cost_units, cached=False, attempt=attempt)
                if not idempotent and resp.status_code != 429:
                    self.breaker.record_failure()
                    raise OutcomeUnknown(self.provider, endpoint,
                                         f"outcome unknown (HTTP {resp.status_code})",
                                         status=resp.status_code, body=resp.text[:2000])
                retry_after = _parse_retry_after(resp)
                self._backoff(attempt, retry_after)
                continue

            if resp.status_code >= 400:
                # Non-retryable client error: real request problem.
                self.breaker.record_failure()
                self._log(endpoint, method, resp.status_code, latency_ms,
                          cost_units, cached=False, attempt=attempt)
                raise ExternalAPIError(
                    self.provider, endpoint,
                    f"HTTP {resp.status_code}: {resp.text[:300]}",
                    status=resp.status_code, body=resp.text[:2000],
                )

            # Success
            self.breaker.record_success()
            self._log(endpoint, method, resp.status_code, latency_ms,
                      cost_units, cached=False, attempt=attempt)
            # 204 No Content (e.g. a DELETE on Google Calendar or Zoom) has no
            # body to parse; treating it as a JSON error would turn a
            # successful delete into a retried, then failed, call. Decided by
            # status code, not by inspecting the body: callers and test fakes
            # are only guaranteed status_code and json().
            data = {} if resp.status_code == 204 else resp.json()
            if cache_ttl:
                self.cache.set(endpoint, cache_payload, data, cache_ttl)
            self.last_call = {"cached": False, "cost_units": cost_units}
            spend_guard.after_data_call(self.provider, endpoint, cost_units)  # F07 meter
            return data

        # Retries exhausted
        self.breaker.record_failure()
        error_cls = RateLimitExceeded if last_status == 429 else ExternalAPIError
        raise error_cls(
            self.provider, endpoint,
            f"failed after {self.max_retries} attempts ({last_error})",
            status=last_status,
        )

    # -- helpers ------------------------------------------------------------
    def _backoff(self, attempt: int, retry_after: float | None) -> None:
        if attempt >= self.max_retries:
            return  # no sleep after the final attempt
        delay = retry_after if retry_after is not None else (
            settings.external_backoff_base_seconds * (2 ** (attempt - 1))
        )
        self._sleep(min(delay, 120.0))

    def _log(self, endpoint: str, method: str, status: int | None,
             latency_ms: int, cost_units: int, cached: bool, attempt: int) -> None:
        logger.info(json.dumps({
            "event": "external_call",
            "provider": self.provider,
            "endpoint": endpoint,
            "method": method,
            "status": status,
            "latency_ms": latency_ms,
            "cost_units": cost_units,
            "cached": cached,
            "attempt": attempt,
        }))

    def health_check(self) -> bool:  # sensible default; adapters may override
        return not self.breaker.is_open()


def _parse_retry_after(resp: httpx.Response) -> float | None:
    value = resp.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None  # HTTP-date form: fall back to exponential backoff
