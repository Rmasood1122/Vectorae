"""Hunter.io adapter — email finding + verification.

Every email in Vectorae MUST pass through `verify()` before any later
milestone may use it. Status mapping (project knowledge):
    deliverable -> verified | risky -> flagged | undeliverable -> dropped

Auth via HUNTER_API_KEY (env). Shares all plumbing (retry, cache,
breaker, logging). Checked against hunter.io/api-documentation/v2 on
2026-10-27: the verifier's `result` field is deprecated in favour of
`status`; 202 means "still in progress, call again", 222 an SMTP failure to
retry later, and 451 that the person asked not to have their data processed.
tests/test_hunter_api_contract.py pins each point.
"""

import logging

from app.config import settings
from app.integrations.base import (
    EmailVerificationStatus,
    EmailVerifier,
    VerificationResult,
    register_email_verifier,
)
from app.integrations.plumbing import BaseHttpAdapter, ExternalAPIError

logger = logging.getLogger(__name__)

# Hunter's email-verifier `status` (current) and `result` (deprecated). A
# webmail address cannot be checked over SMTP (Hunter gives it an arbitrary
# score), so it is risky -- flagged, never auto-verified (founder choice).
_STATUS_MAP = {
    "valid": EmailVerificationStatus.DELIVERABLE,
    "invalid": EmailVerificationStatus.UNDELIVERABLE,
    "disposable": EmailVerificationStatus.UNDELIVERABLE,
    "accept_all": EmailVerificationStatus.RISKY,
    "webmail": EmailVerificationStatus.RISKY,
    "unknown": EmailVerificationStatus.UNKNOWN,
}
_RESULT_MAP = {
    "deliverable": EmailVerificationStatus.DELIVERABLE,
    "risky": EmailVerificationStatus.RISKY,
    "undeliverable": EmailVerificationStatus.UNDELIVERABLE,
}


class DataSubjectOptedOut(Exception):
    """Hunter answered 451: the person asked Hunter not to process their
    personal data. The lead is dropped and the person suppressed; never
    retried."""


class VerificationPending(ExternalAPIError):
    """Hunter answered 202 (verification still running) or 222 (the remote
    mail server failed; retry later). Not an answer: raised so the stage
    retries, and never cached."""


@register_email_verifier
class HunterAdapter(BaseHttpAdapter, EmailVerifier):
    provider = "hunter"
    base_url = "https://api.hunter.io/v2"

    def __init__(self, api_key: str | None = None, **kwargs):
        super().__init__(**kwargs)
        self.api_key = api_key if api_key is not None else settings.hunter_api_key
        if not self.api_key:
            logger.warning("HUNTER_API_KEY is not set — Hunter calls will fail auth")

    def _auth(self) -> dict:
        # The X-API-KEY header is documented; unlike the api_key query
        # parameter it stays out of proxy and access logs.
        return {"headers": {"X-API-KEY": self.api_key}}

    def _get(self, endpoint: str, params: dict) -> dict:
        try:
            data = self.call("GET", endpoint, params=params,
                             cache_ttl=settings.cache_ttl_enrichment_seconds)
        except ExternalAPIError as exc:
            if exc.status == 451:
                raise DataSubjectOptedOut("the person objected to data processing at Hunter (HTTP 451)") from exc
            raise
        status = (self.last_call or {}).get("status")
        if status in (202, 222):
            raise VerificationPending(self.provider, endpoint,
                                      f"HTTP {status}: not a final answer", status=status)
        return data

    # ------------------------------------------------------------------
    # EmailVerifier interface
    # ------------------------------------------------------------------

    def find_email(self, full_name: str, domain: str) -> str | None:
        """Email finder for leads the source returned without an email.
        Cached — the same person+domain lookup is never paid for twice."""
        if not full_name or not domain:
            return None
        data = self._get("/email-finder", {"domain": domain, "full_name": full_name})
        return (data.get("data") or {}).get("email")

    def verify(self, email: str) -> VerificationResult:
        """Deliverability verification — the mandatory gate."""
        data = self._get("/email-verifier", {"email": email})
        payload = data.get("data") or {}
        status = (_STATUS_MAP.get(str(payload.get("status") or "").lower())
                  or _RESULT_MAP.get(str(payload.get("result") or "").lower(),
                                     EmailVerificationStatus.UNKNOWN))
        score = payload.get("score")
        return VerificationResult(
            email=email,
            status=status,
            score=int(score) if score is not None else None,
            raw=payload,
        )

    def health_check(self) -> bool:
        if self.breaker.is_open():
            return False
        try:
            self.call("GET", "/account")
            return True
        except Exception:
            return False
