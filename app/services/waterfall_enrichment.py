"""F01 -- the email waterfall: an ordered finder chain, one independent verifier,
stop at the first address the verifier confirms, and a ledger row per attempt.

WHY A WATERFALL AND NOT "TRUST THE SOURCE"
The competitor complaint F01 answers is that "verified" data from a single
vendor bounces at 15-35%. So the source's own label (Apollo's email_status)
is RECORDED -- as `claimed_status` in the ledger, so the gap can be measured --
but never used as a verdict. Every candidate address goes through the
independent verifier (Hunter's email-verifier), whoever found it.

    for step in waterfall_provider_order (skipping steps with no credentials):
        candidate = step.find(lead)
        suppressed?          -> the PERSON is dropped (one opted-out address
                                means they opted out), nothing is verified
        already checked?     -> next step (never pay twice for one address)
        verify(candidate)    -> valid: STOP, first verified result wins
    else keep the best non-valid: catch_all > unknown > invalid

WHAT IT REUSES (nothing here is a second copy of existing plumbing)
- Adapters: app/integrations/base.py EmailVerifier (find_email / verify).
- The 30-day cache: plumbing.ResponseCache via CACHE_TTL_ENRICHMENT_SECONDS.
  A cache hit is recorded with cached=True, cost_units=0 (plumbing exposes it
  as adapter.last_call). A verdict already on the lead for the same address
  and younger than the cache TTL is reused with no call at all.

The verdict lives on the lead (enrichment_json.email_verdict), bound to the
address by SHA-256 so an edited email silently stops counting as verified.
GDPR delete replaces enrichment_json wholesale, so it goes with the person.
The ledger (EnrichmentAttempt) never stores an address or a name.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import EnrichmentAttempt, Lead, LeadBatch, LeadStatus, Strategy
from app.integrations.base import EmailVerificationStatus, EmailVerifier, VerificationResult
from app.services.spend_guard import MonthlySpendCapReached

logger = logging.getLogger(__name__)

FLAG = "waterfall_enrichment_enabled"
ORDER_SETTING = "waterfall_provider_order"

# Verdicts (the only values email_verdict.verdict ever holds).
VALID = "valid"
CATCH_ALL = "catch_all"
INVALID = "invalid"
UNKNOWN = "unknown"
# Outcomes of a whole run that never reach a verdict record.
NOT_FOUND = "not_found"
SUPPRESSED = "suppressed"

VERDICTS = (VALID, CATCH_ALL, INVALID, UNKNOWN)

# Same meaning the lead statuses always had: deliverable -> verified, risky ->
# flagged for a human, undeliverable -> dropped.
LEAD_STATUS_FOR = {
    VALID: LeadStatus.VERIFIED,
    CATCH_ALL: LeadStatus.FLAGGED,
    UNKNOWN: LeadStatus.FLAGGED,
    INVALID: LeadStatus.DROPPED,
}

# What app/services/provenance.record_email_verification understands.
PROVENANCE_STATUS_FOR = {
    VALID: "deliverable",
    CATCH_ALL: "accept_all",
    UNKNOWN: "risky",
    INVALID: "undeliverable",
}

# Best fallback first when no candidate verified valid.
_FALLBACK_RANK = {CATCH_ALL: 0, UNKNOWN: 1, INVALID: 2}

# Provider units a call consumes when the adapter cannot tell us (test fakes,
# adapters without plumbing). UNVERIFIED against vendor billing -- see spec.
DEFAULT_COST_UNITS = 1


def _now() -> datetime:
    return datetime.now(timezone.utc)


def enabled(db: Session) -> bool:
    from app.services import system_settings  # noqa: PLC0415

    return bool(system_settings.get(db, FLAG))


def email_hash(email: str | None) -> str | None:
    value = (email or "").strip().lower()
    return hashlib.sha256(value.encode()).hexdigest() if value else None


def email_domain(email: str | None) -> str | None:
    value = (email or "").strip().lower()
    return value.rsplit("@", 1)[1][:255] if "@" in value else None


def _parse(ts) -> datetime | None:
    if not ts:
        return None
    try:
        value = datetime.fromisoformat(str(ts))
    except ValueError:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------
# Classification -- one verifier answer -> one verdict
# --------------------------------------------------------------------------


def classify(result: VerificationResult) -> str:
    """Hunter's verifier answer -> valid | catch_all | invalid | unknown.

    Hunter has reported both a `status` field (valid / invalid / accept_all /
    webmail / disposable / unknown) and an older `result` field (deliverable /
    undeliverable / risky); which one the live API returns is UNVERIFIED, so
    `status` wins when it is one we know and `result` (already mapped onto
    EmailVerificationStatus by the adapter) is the fallback.

    A "valid" on a domain flagged accept_all is downgraded: an accept-all
    server says yes to every address, so it cannot prove this one exists.
    """
    raw = result.raw if isinstance(result.raw, dict) else {}
    status = str(raw.get("status") or "").strip().lower()
    accept_all = raw.get("accept_all") is True

    if status == "valid":
        verdict = VALID
    elif status in ("invalid", "disposable"):
        verdict = INVALID
    elif status == "accept_all":
        verdict = CATCH_ALL
    elif status in ("unknown", "webmail"):
        # webmail: no SMTP proof, an arbitrary score -- risky, flagged.
        verdict = UNKNOWN
    elif result.status is EmailVerificationStatus.UNDELIVERABLE:
        verdict = INVALID
    elif accept_all:
        verdict = CATCH_ALL
    elif result.status is EmailVerificationStatus.DELIVERABLE:
        verdict = VALID
    else:
        verdict = UNKNOWN

    if verdict == VALID and accept_all:
        verdict = CATCH_ALL
    return verdict


# --------------------------------------------------------------------------
# Finder steps -- the adapter interface for the chain
# --------------------------------------------------------------------------


@dataclass
class FinderResult:
    email: str | None
    called: bool = False            # did this step make a provider request
    cached: bool = False
    cost_units: int = 0
    claimed_status: str | None = None
    provider: str = ""


class FinderStep:
    """One link of the chain. Subclass, set `key`, register in FINDER_STEPS.

    `available()` is False when the provider has no credentials: the step is
    then skipped and reported as disabled, never called with an empty key."""

    key = "override-me"

    def available(self) -> bool:
        return True

    def find(self, lead: Lead) -> FinderResult:
        raise NotImplementedError


def _claimed_status(lead: Lead) -> str | None:
    """Apollo's own label for the address it returned. Field name UNVERIFIED
    (person.email_status); recorded for measurement only, never trusted."""
    stored = lead.enrichment_json if isinstance(lead.enrichment_json, dict) else {}
    for payload in (stored.get("enrichment"), stored.get("raw")):
        if not isinstance(payload, dict):
            continue
        person = payload.get("person") if isinstance(payload.get("person"), dict) else payload
        value = person.get("email_status")
        if value:
            return str(value)[:40]
    return None


class SourceEmailStep(FinderStep):
    """The address the lead source (Apollo) already returned at search or
    enrichment. No request: the enrich stage paid for it, so cost 0."""

    key = "apollo"

    def find(self, lead: Lead) -> FinderResult:
        return FinderResult(email=(lead.email or "").strip() or None, called=False,
                            claimed_status=_claimed_status(lead),
                            provider=lead.source or "apollo")


class HunterFinderStep(FinderStep):
    """Hunter's email finder for name + company domain (through the batch's
    EmailVerifier adapter, so the existing 30-day cache applies)."""

    key = "hunter"

    def __init__(self, verifier: EmailVerifier):
        self.verifier = verifier

    def available(self) -> bool:
        # The real adapter carries api_key from settings; an injected adapter
        # without one (a test fake) is treated as configured.
        return bool(getattr(self.verifier, "api_key", True))

    def find(self, lead: Lead) -> FinderResult:
        provider = getattr(self.verifier, "provider", "hunter")
        domain = (lead.enrichment_json or {}).get("company_domain")
        if not lead.full_name or not domain:
            return FinderResult(email=None, called=False, provider=provider)
        _reset_last_call(self.verifier)
        email = self.verifier.find_email(lead.full_name, domain)
        cached, units = _call_cost(self.verifier)
        return FinderResult(email=(email or "").strip() or None, called=True,
                            cached=cached, cost_units=units, provider=provider)


# key -> factory(verifier). Further providers (Dropcontact, Findymail, ...) are
# added here once they have credentials AND an adapter checked against the
# vendor's current docs; until then they do not exist rather than exist empty.
FINDER_STEPS = {
    "apollo": lambda verifier: SourceEmailStep(),
    "hunter": lambda verifier: HunterFinderStep(verifier),
}


def configured_steps(db: Session, verifier: EmailVerifier) -> tuple[list[FinderStep], list[dict]]:
    """(enabled steps in order, [{key, reason}] for the ones skipped)."""
    from app.services import system_settings  # noqa: PLC0415

    order = [k.strip().lower() for k in str(system_settings.get(db, ORDER_SETTING)).split(",")]
    steps: list[FinderStep] = []
    disabled: list[dict] = []
    seen: set[str] = set()
    for key in order:
        if not key or key in seen:
            continue
        seen.add(key)
        factory = FINDER_STEPS.get(key)
        if factory is None:
            disabled.append({"key": key, "reason": "unknown provider (no adapter)"})
            continue
        step = factory(verifier)
        if not step.available():
            disabled.append({"key": key, "reason": "no credentials"})
            continue
        steps.append(step)
    return steps, disabled


def provider_status(db: Session, verifier: EmailVerifier) -> list[dict]:
    steps, disabled = configured_steps(db, verifier)
    return ([{"key": s.key, "enabled": True, "reason": None} for s in steps]
            + [{"key": d["key"], "enabled": False, "reason": d["reason"]} for d in disabled])


def _reset_last_call(adapter) -> None:
    if hasattr(adapter, "last_call"):
        try:
            adapter.last_call = None
        except AttributeError:
            pass


def _call_cost(adapter) -> tuple[bool, int]:
    """(cached, cost_units) of the adapter's most recent call."""
    info = getattr(adapter, "last_call", None)
    if isinstance(info, dict):
        cached = bool(info.get("cached"))
        return cached, 0 if cached else int(info.get("cost_units") or DEFAULT_COST_UNITS)
    return False, DEFAULT_COST_UNITS


# --------------------------------------------------------------------------
# Ledger
# --------------------------------------------------------------------------


@dataclass
class Context:
    user_id: uuid.UUID
    strategy_id: uuid.UUID | None = None
    batch_id: uuid.UUID | None = None


def context_for_lead(db: Session, lead: Lead, batch: LeadBatch | None = None) -> Context | None:
    from app.services import notifications  # noqa: PLC0415

    strategy = db.get(Strategy, lead.strategy_id) if lead.strategy_id else None
    owner = notifications.owner_of_strategy(db, strategy)
    if owner is None:
        return None
    return Context(user_id=owner, strategy_id=lead.strategy_id,
                   batch_id=batch.id if batch is not None else lead.batch_id)


def _ledger(db: Session, ctx: Context | None, lead: Lead, *, step: str, provider: str,
            operation: str, outcome: str, cached: bool = False, cost_units: int = 0,
            claimed_status: str | None = None, domain: str | None = None) -> None:
    """Write one attempt and COMMIT it now: a crash later in the same lead
    must not erase the record of a call that was already paid for."""
    if ctx is None:
        logger.warning("waterfall: lead %s has no resolvable owner -- attempt not ledgered",
                       lead.id)
        return
    db.add(EnrichmentAttempt(
        user_id=ctx.user_id, strategy_id=ctx.strategy_id, lead_id=lead.id,
        batch_id=ctx.batch_id, step=step[:32], provider=(provider or "unknown")[:50],
        operation=operation, outcome=outcome, cached=cached, cost_units=cost_units,
        claimed_status=claimed_status, email_domain=domain,
    ))
    db.commit()


# --------------------------------------------------------------------------
# Verdict record on the lead
# --------------------------------------------------------------------------


def verdict_record(lead: Lead) -> dict | None:
    """The stored record, whatever address it was for (None if none)."""
    stored = lead.enrichment_json if isinstance(lead.enrichment_json, dict) else {}
    record = stored.get("email_verdict")
    return record if isinstance(record, dict) and record.get("verdict") in VERDICTS else None


def verdict_for(lead: Lead) -> dict | None:
    """The stored record only if it is about the lead's CURRENT address."""
    record = verdict_record(lead)
    if record is None or not lead.email:
        return None
    return record if record.get("email_sha256") == email_hash(lead.email) else None


def store_verdict(lead: Lead, *, verdict: str, email: str, provider: str | None,
                  verifier: str | None, score: int | None, legacy_status: str | None,
                  claimed_status: str | None = None, checked_at: datetime | None = None) -> dict:
    record = {
        "verdict": verdict,
        "email_sha256": email_hash(email),
        "provider": provider,
        "verifier": verifier,
        "score": score,
        "legacy_status": legacy_status,
        "claimed_status": claimed_status,
        "checked_at": (checked_at or _now()).isoformat(),
    }
    # Reassign (not mutate) so the JSON column is seen as changed.
    lead.enrichment_json = {**(lead.enrichment_json or {}), "email_verdict": record}
    return record


def _reusable(lead: Lead, email: str, now: datetime) -> dict | None:
    record = verdict_record(lead)
    if record is None or record.get("email_sha256") != email_hash(email):
        return None
    checked = _parse(record.get("checked_at"))
    if checked is None or now - checked > timedelta(seconds=settings.cache_ttl_enrichment_seconds):
        return None
    return record


# --------------------------------------------------------------------------
# Verify one address / run the whole chain for one lead
# --------------------------------------------------------------------------


@dataclass
class Verified:
    verdict: str
    score: int | None = None
    legacy_status: str | None = None
    cached: bool = False
    reused: bool = False
    verifier: str | None = None
    checked_at: datetime | None = None   # set when reused: keep the ORIGINAL age


def verify_address(db: Session, lead: Lead, email: str, verifier: EmailVerifier,
                   ctx: Context | None, *, step: str, now: datetime | None = None,
                   reuse: bool = True) -> Verified:
    """One verifier answer for `email`, ledgered. A fresh verdict on the lead
    for the same address is reused (no call, no ledger row)."""
    now = now or _now()
    if reuse:
        record = _reusable(lead, email, now)
        if record is not None:
            return Verified(verdict=record["verdict"], score=record.get("score"),
                            legacy_status=record.get("legacy_status"), reused=True,
                            verifier=record.get("verifier"),
                            checked_at=_parse(record.get("checked_at")))
    provider = getattr(verifier, "provider", "verifier")
    _reset_last_call(verifier)
    try:
        result = verifier.verify(email)
    except MonthlySpendCapReached:
        raise       # F07 refused the call before it was made: not a provider attempt
    except Exception:
        _ledger(db, ctx, lead, step=step, provider=provider, operation="verify",
                outcome="error", domain=email_domain(email))
        raise
    cached, units = _call_cost(verifier)
    verdict = classify(result)
    _ledger(db, ctx, lead, step=step, provider=provider, operation="verify",
            outcome=verdict, cached=cached, cost_units=units, domain=email_domain(email))
    return Verified(verdict=verdict, score=result.score, legacy_status=result.status.value,
                    cached=cached, verifier=provider)


@dataclass
class ChainResult:
    verdict: str                     # valid | catch_all | unknown | invalid | not_found | suppressed
    email: str | None = None
    provider: str | None = None      # who FOUND the chosen address
    verified: Verified | None = None
    claimed_status: str | None = None
    checked: dict = field(default_factory=dict)   # lowercased email -> verdict


def run_for_lead(db: Session, lead: Lead, verifier: EmailVerifier, ctx: Context | None,
                 steps: list[FinderStep], *, now: datetime | None = None) -> ChainResult:
    """Walk the chain for one lead. Never modifies the lead; the caller
    decides what to store. Provider exceptions propagate (after an `error`
    ledger row) so the Celery stage retries, exactly as before F01."""
    from app.workers.lead_tasks import is_suppressed  # noqa: PLC0415

    now = now or _now()
    result = ChainResult(verdict=NOT_FOUND)
    best: ChainResult | None = None

    for step in steps:
        try:
            found = step.find(lead)
        except MonthlySpendCapReached:
            raise   # F07 refused the call before it was made: not a provider attempt
        except Exception:
            _ledger(db, ctx, lead, step=step.key, provider=step.key,
                    operation="find_email", outcome="error")
            raise
        email = found.email
        if found.called or email:
            outcome = "candidate" if email else "not_found"
            if email and is_suppressed(db, email=email):
                outcome = "suppressed"
            elif email and email.lower() in result.checked:
                outcome = "duplicate"
            _ledger(db, ctx, lead, step=step.key, provider=found.provider or step.key,
                    operation="find_email", outcome=outcome, cached=found.cached,
                    cost_units=found.cost_units, claimed_status=found.claimed_status,
                    domain=email_domain(email))
        if not email:
            continue
        key = email.lower()
        if is_suppressed(db, email=email):
            # One opted-out address is the person opting out. Never go looking
            # for another way to reach them.
            return ChainResult(verdict=SUPPRESSED, email=None, provider=found.provider,
                               checked=result.checked)
        if key in result.checked:
            continue

        verified = verify_address(db, lead, email, verifier, ctx, step=step.key, now=now)
        result.checked[key] = verified.verdict
        candidate = ChainResult(verdict=verified.verdict, email=email,
                                provider=found.provider or step.key, verified=verified,
                                claimed_status=found.claimed_status, checked=result.checked)
        if verified.verdict == VALID:
            return candidate                     # first verified result wins
        if best is None or _FALLBACK_RANK[verified.verdict] < _FALLBACK_RANK[best.verdict]:
            best = candidate

    return best if best is not None else result


def store_chain_result(lead: Lead, result: ChainResult, now: datetime | None = None) -> dict:
    verified = result.verified
    return store_verdict(
        lead, verdict=result.verdict, email=result.email or "", provider=result.provider,
        verifier=verified.verifier if verified else None,
        score=verified.score if verified else None,
        legacy_status=verified.legacy_status if verified else None,
        claimed_status=result.claimed_status,
        checked_at=(verified.checked_at if verified and verified.checked_at else now))
