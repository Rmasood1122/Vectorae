"""F05 Guardrails Studio -- per-account exclusion lists and the per-product
approved-claims / forbidden-phrases library.

THE PROBLEM. Opt-outs were already handled (suppression_list, deployment-wide,
always on). What was missing is the ACCOUNT's own knowledge: "Acme is our
customer", "we have an open deal with Globex", "Initech churned last year --
do not pitch them". Pitching those is brand damage, and it is the complaint F05
answers. Claims about US ("we helped 40 agencies grow 3x") were also unchecked:
the A1 claim engine verifies claims about the prospect only.

EXCLUSIONS (guardrail_exclusions, owned by user_id)
  domain   acme.com      matches the lead's email domain or company domain,
                         including subdomains (eu.acme.com)
  company  acme          normalised company name, exact after normalisation
  contact  jane@acme.com / +15551234567 / linkedin:jane-doe
Sources: manual, CSV, CRM sync (local deals + HubSpot + Salesforce). CRM sync
only ADDS or refreshes; it never removes -- failing toward "do not pitch".

ENFORCEMENT (system setting guardrails_enabled, default off)
  sourcing     lead_tasks: an excluded raw lead is never created; a lead whose
               enrichment reveals an excluded identity is DROPPED
  enrollment   sequence_engine.enroll_leads skips excluded leads
  SEND         outreach_tasks.send_message_impl -- right after suppression.
               This is a SAFETY GUARD: an exclusion added after scheduling
               still blocks the send; a lookup error DEFERS (fails closed).
  manual call  POST /leads/{id}/call refuses an excluded lead

LIBRARY (guardrail_library; product_id NULL = every product of the account)
  forbidden_phrase  BLOCK finding in the pre-launch review (adversarial_review)
                    and a hard block at send over the FINAL words (after any
                    reviewer edit)
  approved_claim    when a product has at least one, claim_verification strips
                    every proof claim about us that no approved claim backs

TENANCY. Every query in this module filters by the owning account's user_id.
There is no "all exclusions" query; matching resolves the owner of the lead
first (strategy -> product -> user_id) and looks at that owner's rows only.

The public functions at the bottom of each section are the service API F04
(Reply Copilot) consumes -- see docs/features/f05-guardrails-studio.md.
"""

from __future__ import annotations

import csv
import io
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timezone
from urllib.parse import urlparse

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.db.models import (
    Deal,
    DealStage,
    GuardrailExclusion,
    GuardrailLibraryEntry,
    Lead,
    LeadStatus,
    Product,
    Strategy,
)

logger = logging.getLogger(__name__)

SETTING = "guardrails_enabled"

DOMAIN, COMPANY, CONTACT = "domain", "company", "contact"
KINDS = (DOMAIN, COMPANY, CONTACT)
REASONS = ("customer", "open_deal", "churned", "partner", "competitor",
           "do_not_contact", "other")
SOURCES = ("manual", "csv", "crm_local", "crm_hubspot", "crm_salesforce")
APPROVED_CLAIM, FORBIDDEN_PHRASE = "approved_claim", "forbidden_phrase"
LIBRARY_KINDS = (APPROVED_CLAIM, FORBIDDEN_PHRASE)

MAX_CSV_BYTES = 2_000_000
MAX_CSV_ROWS = 10_000
MAX_REPORTED_ERRORS = 100
MAX_LIBRARY_TEXT = 2000
CRM_MAX_PAGES = 50

# Security-audit actions (security_audit_events.action, String(60)).
AUDIT_ADDED = "guardrails.exclusion_added"
AUDIT_REMOVED = "guardrails.exclusion_removed"
AUDIT_IMPORTED = "guardrails.exclusions_imported"
AUDIT_CRM_SYNCED = "guardrails.crm_synced"
AUDIT_LIBRARY_ADDED = "guardrails.library_added"
AUDIT_LIBRARY_REMOVED = "guardrails.library_removed"

#: Shared mailbox providers. A DOMAIN exclusion of one of these would exclude
#: every prospect who uses it, so it is refused (exclude the contact instead),
#: and CRM sync never derives one.
CONSUMER_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "hotmail.co.uk",
    "live.com", "msn.com", "yahoo.com", "yahoo.co.uk", "ymail.com", "icloud.com",
    "me.com", "mac.com", "aol.com", "proton.me", "protonmail.com", "gmx.com",
    "gmx.de", "gmx.net", "mail.com", "yandex.com", "yandex.ru", "zoho.com",
    "fastmail.com", "hey.com", "qq.com", "163.com", "web.de",
})

#: Trailing legal-form words dropped when normalising a company name.
_LEGAL_SUFFIXES = frozenset({
    "inc", "incorporated", "llc", "ltd", "limited", "corp", "corporation", "co",
    "company", "gmbh", "plc", "sa", "ag", "bv", "pty", "llp", "lp",
})

_DOMAIN_RE = re.compile(
    r"^(?=.{3,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_CHARS = re.compile(r"^[\d\s()+.\-]+$")


class GuardrailValueError(ValueError):
    """A value that cannot be an exclusion / library entry (API: 422)."""


class ForbiddenPhraseBlocked(Exception):
    """Raised inside the send path when the final copy contains a forbidden
    phrase. outreach_tasks catches it and marks the message FAILED."""

    def __init__(self, phrases: list[str]):
        super().__init__(", ".join(phrases))
        self.phrases = phrases


def _now() -> datetime:
    return datetime.now(UTC)


def enabled(session: Session) -> bool:
    """Is the feature switched on for this deployment? Raises on a database
    error -- callers on the send path treat that as "cannot tell" and fail
    closed, see outreach_tasks."""
    from app.services import system_settings  # noqa: PLC0415

    return bool(system_settings.get(session, SETTING))


# --------------------------------------------------------------------------
# Normalisation -- ONE function per kind, used for storing AND for matching,
# so the two sides can never disagree about what "the same domain" means.
# --------------------------------------------------------------------------


def normalize_domain(value: str | None) -> str | None:
    """'https://www.Acme.com/about' / '@acme.com' / 'jane@acme.com' -> 'acme.com'."""
    raw = (value or "").strip().lower()
    if not raw:
        return None
    if "@" in raw and "://" not in raw:
        raw = raw.rsplit("@", 1)[1]
    if "://" in raw:
        raw = urlparse(raw).hostname or ""
    else:
        raw = re.split(r"[/?#]", raw, maxsplit=1)[0]
    raw = raw.split(":", 1)[0].strip().strip(".")
    raw = raw.removeprefix("www.")
    if not _DOMAIN_RE.match(raw):
        return None
    if not re.search(r"[a-z]", raw.rsplit(".", 1)[-1]):
        return None  # an IP address, not a domain
    return raw


def normalize_company(value: str | None) -> str | None:
    """'Acme, Inc.' / 'ACME Inc' / 'Acme' -> 'acme'. Exact matching only."""
    text = (value or "").lower().replace("&", " and ")
    text = re.sub(r"[^\w\s]", " ", text).replace("_", " ")
    words = text.split()
    while len(words) > 1 and words[-1] in _LEGAL_SUFFIXES:
        words.pop()
    result = " ".join(words)[:320]
    return result if len(result) >= 2 else None


def _phone_key(value: str | None) -> str | None:
    raw = (value or "").strip()
    if not raw or not _PHONE_CHARS.match(raw):
        return None
    digits = re.sub(r"\D", "", raw)
    return f"+{digits}" if 7 <= len(digits) <= 15 else None


def _linkedin_key(value: str | None) -> str | None:
    from app.services.linkedin_outreach import normalize_profile  # noqa: PLC0415

    raw = (value or "").strip()
    if raw.lower().startswith("linkedin:"):
        raw = raw[len("linkedin:"):]
    slug = normalize_profile(raw) if raw else None
    return f"linkedin:{slug}" if slug else None


def _email_key(value: str | None) -> str | None:
    raw = (value or "").strip().lower()
    return raw[:320] if _EMAIL_RE.match(raw) else None


def normalize_contact(value: str | None) -> str | None:
    """An email, a phone number or a LinkedIn profile, in the one form each is
    stored and matched in: 'jane@acme.com', '+15551234567', 'linkedin:jane-doe'."""
    raw = (value or "").strip()
    if not raw:
        return None
    low = raw.lower()
    if "linkedin.com/" in low or low.startswith("linkedin:"):
        return _linkedin_key(raw)
    if "@" in raw:
        return _email_key(raw)
    return _phone_key(raw)


def normalize(kind: str, value: str | None) -> str:
    """The stored form of `value`, or GuardrailValueError."""
    if kind not in KINDS:
        raise GuardrailValueError(f"kind must be one of {', '.join(KINDS)}")
    if kind == DOMAIN:
        result = normalize_domain(value)
        if result in CONSUMER_DOMAINS:
            raise GuardrailValueError(
                f"{result} is a shared mailbox provider -- excluding it would exclude "
                "everyone who uses it. Exclude the contact instead.")
    elif kind == COMPANY:
        result = normalize_company(value)
    else:
        result = normalize_contact(value)
    if not result:
        raise GuardrailValueError(f"not a valid {kind}: {str(value or '')[:80]!r}")
    return result


def detect_kind(value: str | None) -> str:
    """Best guess for a CSV row with no `type` column."""
    raw = (value or "").strip()
    low = raw.lower()
    if "@" in raw and "://" not in raw and _EMAIL_RE.match(low):
        return CONTACT
    if "linkedin.com/" in low or low.startswith("linkedin:"):
        return CONTACT
    if _phone_key(raw):
        return CONTACT
    if " " not in raw and normalize_domain(raw):
        return DOMAIN
    return COMPANY


# --------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Match:
    id: uuid.UUID
    kind: str
    display_value: str
    reason: str
    source: str

    def audit(self) -> dict:
        """What the audit rows carry: ids and categories, never the value
        (a contact exclusion's value is a third party's email)."""
        return {"id": str(self.id), "kind": self.kind, "reason": self.reason,
                "source": self.source}

    def out(self) -> dict:
        return {**self.audit(), "value": self.display_value}


def _domain_candidates(*domains: str | None) -> set[str]:
    """Every domain a lead lives under: eu.acme.com -> {eu.acme.com, acme.com}."""
    out: set[str] = set()
    for domain in domains:
        normalized = normalize_domain(domain)
        if not normalized:
            continue
        parts = normalized.split(".")
        for i in range(len(parts) - 1):
            out.add(".".join(parts[i:]))
    return out


def find_match(session: Session, user_id, *, email: str | None = None,
               phone: str | None = None, linkedin: str | None = None,
               company: str | None = None, domains=()) -> Match | None:
    """The active exclusion of THIS account covering these identifiers, if
    any. Does not look at the flag -- see send_block / active_owner.

    ONE row (the first by kind, then age). Right for every caller that blocks
    on ANY exclusion. A caller that exempts some reasons must look at all of
    them -- find_matches -- or one exempt row hides a do_not_contact row."""
    matches = find_matches(session, user_id, email=email, phone=phone, linkedin=linkedin,
                           company=company, domains=domains, limit=1)
    return matches[0] if matches else None


def find_matches(session: Session, user_id, *, email: str | None = None,
                 phone: str | None = None, linkedin: str | None = None,
                 company: str | None = None, domains=(), limit: int | None = None) -> list[Match]:
    """EVERY active exclusion of this account covering these identifiers,
    first by kind then age (the order find_match has always used)."""
    if user_id is None:
        return []
    email_domain = email.rsplit("@", 1)[1] if email and "@" in email else None
    domain_keys = _domain_candidates(email_domain, *domains)
    contact_keys = {k for k in (_email_key(email), _phone_key(phone), _linkedin_key(linkedin))
                    if k}
    company_key = normalize_company(company)
    clauses = []
    if domain_keys:
        clauses.append(and_(GuardrailExclusion.kind == DOMAIN,
                            GuardrailExclusion.value.in_(sorted(domain_keys))))
    if contact_keys:
        clauses.append(and_(GuardrailExclusion.kind == CONTACT,
                            GuardrailExclusion.value.in_(sorted(contact_keys))))
    if company_key:
        clauses.append(and_(GuardrailExclusion.kind == COMPANY,
                            GuardrailExclusion.value == company_key))
    if not clauses:
        return []
    query = (select(GuardrailExclusion)
             .where(GuardrailExclusion.user_id == user_id,
                    GuardrailExclusion.removed_at.is_(None),
                    or_(*clauses))
             .order_by(GuardrailExclusion.kind, GuardrailExclusion.created_at))
    if limit is not None:
        query = query.limit(limit)
    return [Match(id=row.id, kind=row.kind, display_value=row.display_value,
                  reason=row.reason, source=row.source)
            for row in session.execute(query).scalars()]


def _lead_identifiers(lead: Lead) -> dict:
    enrichment = lead.enrichment_json or {}
    return {"email": lead.email, "phone": lead.phone, "linkedin": lead.linkedin_url,
            "company": lead.company, "domains": [enrichment.get("company_domain")]}


def _resolve_owner(session: Session, lead: Lead, user_id):
    if user_id is None:
        from app.services import notifications  # noqa: PLC0415

        user_id = notifications.owner_of_lead(session, lead)
    return user_id


def exclusion_for_lead(session: Session, lead: Lead, *, user_id=None) -> Match | None:
    """The exclusion covering this lead, for its owning account."""
    return find_match(session, _resolve_owner(session, lead, user_id),
                      **_lead_identifiers(lead))


def exclusions_for_lead(session: Session, lead: Lead, *, user_id=None) -> list[Match]:
    """EVERY exclusion covering this lead, for its owning account."""
    return find_matches(session, _resolve_owner(session, lead, user_id),
                        **_lead_identifiers(lead))


def active_owner(session: Session, strategy_id) -> uuid.UUID | None:
    """The account whose exclusions apply to this strategy's leads, or None
    when the feature is off (callers then skip every check)."""
    if not enabled(session):
        return None
    from app.services import notifications  # noqa: PLC0415

    return notifications.owner_of_strategy(session, session.get(Strategy, strategy_id))


def send_block(session: Session, lead: Lead, strategy: Strategy | None) -> Match | None:
    """The send-time check. None = not excluded (or the feature is off).
    RAISES on a database error: the send path defers (fails closed)."""
    if not enabled(session):
        return None
    from app.services import notifications  # noqa: PLC0415

    owner = notifications.owner_of_strategy(session, strategy)
    return exclusion_for_lead(session, lead, user_id=owner) if owner else None


def send_blocks(session: Session, lead: Lead, strategy: Strategy | None) -> list[Match]:
    """send_block, but EVERY matching exclusion (empty = not excluded, or the
    feature is off). For a caller that exempts some reasons
    (recipient_guards.person_block): it must see a do_not_contact row even
    when an exempt row sorts first. RAISES on a database error."""
    if not enabled(session):
        return []
    from app.services import notifications  # noqa: PLC0415

    owner = notifications.owner_of_strategy(session, strategy)
    return exclusions_for_lead(session, lead, user_id=owner) if owner else []


# --------------------------------------------------------------------------
# Exclusion CRUD (every function takes the owning user_id)
# --------------------------------------------------------------------------


def _audit(session: Session, action: str, *, actor_user_id, owner_user_id,
           target_id=None, details: dict | None = None) -> None:
    from app.services import security_audit  # noqa: PLC0415

    security_audit.record(session, action=action, user_id=actor_user_id,
                          owner_user_id=owner_user_id, target_type="guardrails",
                          target_id=target_id, details=details)


def _check_reason(reason: str | None) -> str:
    reason = (reason or "other").strip().lower()
    if reason not in REASONS:
        raise GuardrailValueError(f"reason must be one of {', '.join(REASONS)}")
    return reason


def upsert_exclusion(session: Session, user_id, kind: str, value: str | None, *,
                     reason: str = "other", source: str = "manual", note: str | None = None,
                     external_ref: str | None = None, actor_user_id=None,
                     now: datetime | None = None) -> tuple[GuardrailExclusion, str]:
    """Add or reactivate one exclusion. Flushes, never commits.

    RETURNS (row, status) with status
      added        a new row
      reactivated  a removed row brought back (active_since moves to now)
      updated      already active, and this write OUTRANKS it (below): the
                   reason and source are replaced
      exists       already active -- left as it is, only last_seen_at is
                   refreshed

    PRECEDENCE on an active row (first match wins):
      1. do_not_contact is sticky: a row whose reason is do_not_contact is
         never downgraded by any later write. (To lift it, a person removes
         the exclusion.)
      2. A write whose reason is do_not_contact upgrades the row, whatever
         wrote it first -- "never contact this person" outranks "they are a
         customer / have an open deal".
      3. A MANUAL write replaces a row last written by an import (CSV or a
         CRM sync): a person's explicit decision beats a sync. An import
         never overrides a manual row.
      4. Otherwise the first writer's reason and source stand (a CRM sync
         re-asserting a row does not flip it back and forth).
    The send-time F05 block is unaffected (any active exclusion blocks); the
    precedence matters to callers that exempt some reasons
    (recipient_guards.person_block) and to what the settings page shows.
    """
    now = now or _now()
    kind = (kind or "").strip().lower()
    normalized = normalize(kind, value)
    reason = _check_reason(reason)
    if source not in SOURCES:
        raise GuardrailValueError(f"source must be one of {', '.join(SOURCES)}")
    row = session.execute(
        select(GuardrailExclusion).where(GuardrailExclusion.user_id == user_id,
                                         GuardrailExclusion.kind == kind,
                                         GuardrailExclusion.value == normalized)
    ).scalar_one_or_none()
    display = (str(value or "").strip() or normalized)[:320]
    if row is None:
        row = GuardrailExclusion(
            user_id=user_id, kind=kind, value=normalized, display_value=display,
            reason=reason, source=source, note=(note or "").strip()[:300] or None,
            external_ref=(external_ref or None) and str(external_ref)[:100],
            created_by_user_id=actor_user_id, active_since=now, last_seen_at=now)
        session.add(row)
        session.flush()
        return row, "added"
    row.last_seen_at = now
    if row.removed_at is not None:
        row.removed_at = None
        row.active_since = now
        row.reason, row.source, row.display_value = reason, source, display
        row.note = (note or "").strip()[:300] or None
        row.external_ref = (external_ref or None) and str(external_ref)[:100]
        session.flush()
        return row, "reactivated"
    if _outranks(reason, source, row):
        row.reason, row.source = reason, source
        if note:
            row.note = note.strip()[:300] or row.note
        if external_ref:
            row.external_ref = str(external_ref)[:100]
        session.flush()
        return row, "updated"
    session.flush()
    return row, "exists"


DO_NOT_CONTACT = "do_not_contact"


def _outranks(reason: str, source: str, row: GuardrailExclusion) -> bool:
    """Does a write of (reason, source) replace this ACTIVE row's reason and
    source? See upsert_exclusion's PRECEDENCE."""
    if row.reason == DO_NOT_CONTACT:
        return False
    if reason == DO_NOT_CONTACT:
        return True
    return source == "manual" and row.source != "manual"


def add_exclusion(session: Session, user_id, kind: str, value: str, *, reason: str = "other",
                  note: str | None = None, actor_user_id=None) -> tuple[GuardrailExclusion, str]:
    """Manual entry: upsert + security-audit row, committed together."""
    row, status = upsert_exclusion(session, user_id, kind, value, reason=reason,
                                   source="manual", note=note, actor_user_id=actor_user_id)
    if status != "exists":
        _audit(session, AUDIT_ADDED, actor_user_id=actor_user_id, owner_user_id=user_id,
               target_id=row.id, details={"kind": row.kind, "reason": row.reason,
                                          "source": row.source, "status": status})
    session.commit()
    return row, status


def owned_exclusion(session: Session, user_id, exclusion_id) -> GuardrailExclusion | None:
    row = session.get(GuardrailExclusion, exclusion_id)
    return row if row is not None and row.user_id == user_id else None


def remove_exclusion(session: Session, user_id, exclusion_id, *,
                     actor_user_id=None, now: datetime | None = None) -> GuardrailExclusion | None:
    """Soft delete. None when the row is not this account's (API: 404)."""
    row = owned_exclusion(session, user_id, exclusion_id)
    if row is None:
        return None
    if row.removed_at is None:
        row.removed_at = now or _now()
        _audit(session, AUDIT_REMOVED, actor_user_id=actor_user_id, owner_user_id=user_id,
               target_id=row.id, details={"kind": row.kind, "reason": row.reason,
                                          "source": row.source})
        session.commit()
    return row


def exclusion_out(row: GuardrailExclusion) -> dict:
    return {
        "id": str(row.id), "kind": row.kind, "value": row.value,
        "display_value": row.display_value, "reason": row.reason, "source": row.source,
        "external_ref": row.external_ref, "note": row.note,
        "active": row.removed_at is None,
        "active_since": row.active_since.isoformat() if row.active_since else None,
        "removed_at": row.removed_at.isoformat() if row.removed_at else None,
        "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def list_exclusions(session: Session, user_id, *, kind: str | None = None,
                    source: str | None = None, q: str | None = None,
                    include_removed: bool = False, limit: int = 100,
                    offset: int = 0) -> dict:
    base = select(GuardrailExclusion).where(GuardrailExclusion.user_id == user_id)
    if kind:
        base = base.where(GuardrailExclusion.kind == kind)
    if source:
        base = base.where(GuardrailExclusion.source == source)
    if not include_removed:
        base = base.where(GuardrailExclusion.removed_at.is_(None))
    if q:
        needle = f"%{q.strip().lower()[:100]}%"
        base = base.where(or_(GuardrailExclusion.value.like(needle),
                              func.lower(GuardrailExclusion.display_value).like(needle)))
    total = session.execute(select(func.count()).select_from(base.subquery())).scalar_one()
    rows = session.execute(base.order_by(GuardrailExclusion.created_at.desc(),
                                         GuardrailExclusion.id)
                           .offset(offset).limit(limit)).scalars().all()
    return {"total": int(total), "limit": limit, "offset": offset,
            "items": [exclusion_out(r) for r in rows]}


def counts(session: Session, user_id) -> dict:
    rows = session.execute(
        select(GuardrailExclusion.kind, func.count())
        .where(GuardrailExclusion.user_id == user_id, GuardrailExclusion.removed_at.is_(None))
        .group_by(GuardrailExclusion.kind)).all()
    library = session.execute(
        select(GuardrailLibraryEntry.kind, func.count())
        .where(GuardrailLibraryEntry.user_id == user_id)
        .group_by(GuardrailLibraryEntry.kind)).all()
    return {"exclusions": {k: 0 for k in KINDS} | {k: int(n) for k, n in rows},
            "library": {k: 0 for k in LIBRARY_KINDS} | {k: int(n) for k, n in library}}


# --------------------------------------------------------------------------
# CSV import
# --------------------------------------------------------------------------

#: Column-per-kind layout: every non-empty cell becomes one exclusion.
_CSV_COLUMNS = {"domain": DOMAIN, "website": DOMAIN, "company": COMPANY,
                "company_name": COMPANY, "email": CONTACT, "phone": CONTACT,
                "linkedin": CONTACT, "linkedin_url": CONTACT}


def import_csv(session: Session, user_id, text: str, *, default_reason: str = "other",
               actor_user_id=None, now: datetime | None = None) -> dict:
    """Import a CSV. Header row required: `type,value[,reason,note]`, or any of
    domain/company/email/phone/linkedin columns. Invalid rows are reported by
    row number and skipped; the valid ones are imported in one commit."""
    now = now or _now()
    text = text or ""
    if len(text.encode("utf-8")) > MAX_CSV_BYTES:
        raise GuardrailValueError("the CSV is larger than 2 MB -- split it")
    default_reason = _check_reason(default_reason)
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    headers = {(h or "").strip().lower() for h in (reader.fieldnames or [])}
    typed = "value" in headers
    if not typed and not headers & set(_CSV_COLUMNS):
        raise GuardrailValueError(
            "the first row must be a header: 'type,value' (plus optional reason, note) "
            "or columns named domain, company, email, phone, linkedin")
    tally = {"added": 0, "reactivated": 0, "exists": 0}
    errors: list[dict] = []
    rows_read = 0
    for line_no, raw_row in enumerate(reader, start=2):
        rows_read += 1
        if rows_read > MAX_CSV_ROWS:
            errors.append({"row": line_no, "error": f"more than {MAX_CSV_ROWS} rows -- the rest "
                                                    "were not imported"})
            break
        row = {(k or "").strip().lower(): (v or "").strip() if isinstance(v, str) else ""
               for k, v in raw_row.items() if k is not None}
        try:
            reason = _check_reason(row.get("reason") or default_reason)
        except GuardrailValueError as exc:
            errors.append({"row": line_no, "error": str(exc)})
            continue
        note = row.get("note") or None
        items: list[tuple[str, str]] = []
        if typed:
            value = row.get("value") or ""
            if value:
                kind = (row.get("type") or row.get("kind") or "").lower() or detect_kind(value)
                items.append((kind, value))
        else:
            items = [(kind, row[col]) for col, kind in _CSV_COLUMNS.items() if row.get(col)]
        for kind, value in items:
            try:
                _, status = upsert_exclusion(session, user_id, kind, value, reason=reason,
                                             source="csv", note=note,
                                             actor_user_id=actor_user_id, now=now)
            except GuardrailValueError as exc:
                errors.append({"row": line_no, "error": str(exc)})
                continue
            tally[status] = tally.get(status, 0) + 1
    result = {**tally, "rows": min(rows_read, MAX_CSV_ROWS), "error_count": len(errors),
              "errors": errors[:MAX_REPORTED_ERRORS]}
    _audit(session, AUDIT_IMPORTED, actor_user_id=actor_user_id, owner_user_id=user_id,
           details={k: result[k] for k in ("added", "reactivated", "updated", "exists",
                                           "rows", "error_count") if k in result})
    session.commit()
    return result


# --------------------------------------------------------------------------
# CRM sync: customers, open deals, churned -> exclusions. Adds, never removes.
# --------------------------------------------------------------------------

HUBSPOT_CUSTOMER_STAGES = ("customer", "evangelist")
SALESFORCE_CUSTOMER_TYPES = ("Customer - Direct", "Customer - Channel", "Customer")
SALESFORCE_CHURNED_TYPES = ("Former Customer",)


@dataclass
class _Entry:
    kind: str
    value: str
    reason: str
    external_ref: str | None = None


def _lead_entries(lead: Lead, reason: str, ref: str) -> list[_Entry]:
    out: list[_Entry] = []
    enrichment = lead.enrichment_json or {}
    for domain in (enrichment.get("company_domain"),
                   lead.email.rsplit("@", 1)[1] if lead.email and "@" in lead.email else None):
        normalized = normalize_domain(domain)
        if normalized and normalized not in CONSUMER_DOMAINS:
            out.append(_Entry(DOMAIN, normalized, reason, ref))
            break
    if _email_key(lead.email):
        out.append(_Entry(CONTACT, lead.email, reason, ref))
    return out


def _local_entries(session: Session, user_id) -> list[_Entry]:
    """From Vectorae's own CRM: won leads, and leads with an open or won deal.
    Ownership: the lead -> strategy -> product chain, filtered by user_id."""
    owned = (select(Lead).join(Strategy, Strategy.id == Lead.strategy_id)
             .join(Product, Product.id == Strategy.product_id)
             .where(Product.user_id == user_id))
    entries: list[_Entry] = []
    for lead in session.execute(owned.where(Lead.status == LeadStatus.CLOSED_WON)).scalars():
        entries += _lead_entries(lead, "customer", f"lead:{lead.id}")
    deal_rows = session.execute(
        select(Deal, Lead).join(Lead, Lead.id == Deal.lead_id)
        .join(Strategy, Strategy.id == Lead.strategy_id)
        .join(Product, Product.id == Strategy.product_id)
        .where(Deal.user_id == user_id, Product.user_id == user_id,
               Deal.stage.in_([DealStage.OPEN, DealStage.WON]))).all()
    for deal, lead in deal_rows:
        reason = "open_deal" if deal.stage is DealStage.OPEN else "customer"
        entries += _lead_entries(lead, reason, f"deal:{deal.id}")
    return entries


def _company_entries(name: str | None, domain: str | None, reason: str,
                     ref: str) -> list[_Entry]:
    out = []
    normalized = normalize_domain(domain)
    if normalized and normalized not in CONSUMER_DOMAINS:
        out.append(_Entry(DOMAIN, normalized, reason, ref))
    if normalize_company(name):
        out.append(_Entry(COMPANY, name, reason, ref))
    return out


def _settings_list(conn, key: str, default: tuple) -> list[str]:
    value = (conn.settings_json or {}).get(key)
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return [v for v in value if v.strip()]
    return list(default)


def _hubspot_entries(session: Session, conn) -> list[_Entry]:
    """Companies by lifecycle stage and open-deal count.
    UNVERIFIED against a live portal -- see the spec."""
    from app.services import crm_sync  # noqa: PLC0415

    client = crm_sync.hubspot_client(session, conn)
    props = ["name", "domain", "lifecyclestage", "hs_num_open_deals"]
    queries = [("customer", [{"propertyName": "lifecyclestage", "operator": "IN",
                              "values": _settings_list(conn, "customer_lifecycle_stages",
                                                       HUBSPOT_CUSTOMER_STAGES)}]),
               ("open_deal", [{"propertyName": "hs_num_open_deals", "operator": "GT",
                               "value": "0"}])]
    churned = _settings_list(conn, "churned_lifecycle_stages", ())
    if churned:
        queries.append(("churned", [{"propertyName": "lifecyclestage", "operator": "IN",
                                     "values": churned}]))
    entries: list[_Entry] = []
    for reason, filters in queries:
        if filters[0].get("values") == []:
            continue
        for company in client.search("companies", [{"filters": filters}], props,
                                     max_pages=CRM_MAX_PAGES):
            p = company.get("properties") or {}
            entries += _company_entries(p.get("name"), p.get("domain"), reason,
                                        f"hubspot:company:{company.get('id')}")
    return entries


def _salesforce_entries(session: Session, conn) -> list[_Entry]:
    """Accounts by Type and open Opportunities. UNVERIFIED against a live org."""
    from app.integrations.salesforce import soql_quote  # noqa: PLC0415
    from app.services import crm_sync  # noqa: PLC0415

    client = crm_sync.salesforce_client(session, conn)
    entries: list[_Entry] = []
    for reason, key, default in (
            ("customer", "customer_account_types", SALESFORCE_CUSTOMER_TYPES),
            ("churned", "churned_account_types", SALESFORCE_CHURNED_TYPES)):
        types = _settings_list(conn, key, default)
        if not types:
            continue
        soql = ("SELECT Id, Name, Website, Type FROM Account WHERE Type IN ("
                + ", ".join(soql_quote(t) for t in types) + ")")
        for account in client.query_all(soql, max_pages=CRM_MAX_PAGES):
            entries += _company_entries(account.get("Name"), account.get("Website"), reason,
                                        f"salesforce:account:{account.get('Id')}")
    soql = ("SELECT Id, AccountId, Account.Name, Account.Website FROM Opportunity "
            "WHERE IsClosed = false AND AccountId != null")
    for opp in client.query_all(soql, max_pages=CRM_MAX_PAGES):
        account = opp.get("Account") or {}
        entries += _company_entries(account.get("Name"), account.get("Website"), "open_deal",
                                    f"salesforce:account:{opp.get('AccountId')}")
    return entries


def _apply(session: Session, user_id, entries: list[_Entry], source: str,
           now: datetime) -> dict:
    tally = {"added": 0, "reactivated": 0, "exists": 0, "invalid": 0}
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        try:
            key = (entry.kind, normalize(entry.kind, entry.value))
        except GuardrailValueError:
            tally["invalid"] += 1
            continue
        if key in seen:
            continue
        seen.add(key)
        _, status = upsert_exclusion(session, user_id, entry.kind, entry.value,
                                     reason=entry.reason, source=source,
                                     external_ref=entry.external_ref, now=now)
        # "updated" only appears when it happened (the result shape is
        # otherwise unchanged for existing API consumers).
        tally[status] = tally.get(status, 0) + 1
    return tally


def sync_from_crm(session: Session, user_id, *, actor_user_id=None,
                  now: datetime | None = None) -> dict:
    """Pull customers / open deals / churned into this account's exclusions.

    Local records always; each connected CRM independently -- one vendor's
    failure is reported and does not undo what the others added. Never
    removes an exclusion."""
    from app.services import crm_sync  # noqa: PLC0415

    now = now or _now()
    result: dict = {"local": {"status": "ok",
                              **_apply(session, user_id, _local_entries(session, user_id),
                                       "crm_local", now)}}
    session.commit()
    for conn in crm_sync.connections(session, user_id):
        fetch = {"hubspot": _hubspot_entries, "salesforce": _salesforce_entries}.get(conn.provider)
        if fetch is None:
            continue
        try:
            entries = fetch(session, conn)
            result[conn.provider] = {"status": "ok",
                                     **_apply(session, user_id, entries,
                                              f"crm_{conn.provider}", now)}
            session.commit()
        except Exception as exc:  # noqa: BLE001 -- reported per provider
            session.rollback()
            logger.warning("guardrails CRM sync (%s) failed for %s: %s",
                           conn.provider, user_id, exc)
            result[conn.provider] = {"status": "error", "error": str(exc)[:300]}
    _audit(session, AUDIT_CRM_SYNCED, actor_user_id=actor_user_id, owner_user_id=user_id,
           details={source: {k: v for k, v in res.items() if k != "error"}
                    for source, res in result.items()})
    session.commit()
    return result


# --------------------------------------------------------------------------
# Library: approved claims + forbidden phrases
# --------------------------------------------------------------------------


def _library_rows(session: Session, user_id, product_id, kind: str | None = None):
    query = select(GuardrailLibraryEntry).where(
        GuardrailLibraryEntry.user_id == user_id,
        or_(GuardrailLibraryEntry.product_id.is_(None),
            GuardrailLibraryEntry.product_id == product_id) if product_id is not None
        else GuardrailLibraryEntry.product_id.is_(None))
    if kind:
        query = query.where(GuardrailLibraryEntry.kind == kind)
    return session.execute(query.order_by(GuardrailLibraryEntry.created_at,
                                          GuardrailLibraryEntry.id)).scalars().all()


def library_out(row: GuardrailLibraryEntry) -> dict:
    return {"id": str(row.id), "kind": row.kind, "text": row.text,
            "product_id": str(row.product_id) if row.product_id else None,
            "source_url": row.source_url,
            "created_at": row.created_at.isoformat() if row.created_at else None}


def approved_claims(session: Session, user_id, product_id) -> list[dict]:
    """Approved claims for this product (plus account-wide ones). F04 API."""
    return [library_out(r) for r in _library_rows(session, user_id, product_id, APPROVED_CLAIM)]


def forbidden_phrases(session: Session, user_id, product_id) -> list[dict]:
    """Forbidden phrases for this product (plus account-wide ones). F04 API."""
    return [library_out(r) for r in _library_rows(session, user_id, product_id,
                                                   FORBIDDEN_PHRASE)]


def list_library(session: Session, user_id, product_id=None) -> list[dict]:
    return [library_out(r) for r in _library_rows(session, user_id, product_id)]


def add_library_entry(session: Session, user_id, kind: str, text: str, *,
                      product_id=None, source_url: str | None = None,
                      actor_user_id=None) -> GuardrailLibraryEntry:
    """`product_id` must already be verified as owned by user_id (the API does)."""
    kind = (kind or "").strip().lower()
    if kind not in LIBRARY_KINDS:
        raise GuardrailValueError(f"kind must be one of {', '.join(LIBRARY_KINDS)}")
    text = " ".join((text or "").split())
    if len(text) < 2:
        raise GuardrailValueError("text is empty")
    if len(text) > MAX_LIBRARY_TEXT:
        raise GuardrailValueError(f"text is longer than {MAX_LIBRARY_TEXT} characters")
    if source_url and not re.match(r"^https?://", source_url.strip(), re.I):
        raise GuardrailValueError("source_url must start with http:// or https://")
    row = GuardrailLibraryEntry(user_id=user_id, product_id=product_id, kind=kind, text=text,
                                source_url=(source_url or "").strip()[:500] or None,
                                created_by_user_id=actor_user_id)
    session.add(row)
    session.flush()
    _audit(session, AUDIT_LIBRARY_ADDED, actor_user_id=actor_user_id, owner_user_id=user_id,
           target_id=row.id, details={"kind": kind,
                                      "product_id": str(product_id) if product_id else None})
    session.commit()
    return row


def remove_library_entry(session: Session, user_id, entry_id, *, actor_user_id=None) -> bool:
    row = session.get(GuardrailLibraryEntry, entry_id)
    if row is None or row.user_id != user_id:
        return False
    _audit(session, AUDIT_LIBRARY_REMOVED, actor_user_id=actor_user_id, owner_user_id=user_id,
           target_id=row.id, details={"kind": row.kind,
                                      "product_id": str(row.product_id) if row.product_id
                                      else None})
    session.delete(row)
    session.commit()
    return True


# ---- forbidden phrases ------------------------------------------------------


def _phrase_pattern(phrase: str) -> re.Pattern:
    body = r"\s+".join(re.escape(word) for word in phrase.split())
    return re.compile(rf"(?<!\w){body}(?!\w)", re.I)


def forbidden_hits(texts, phrases) -> list[str]:
    """Which phrases occur in any of `texts` (case-insensitive, whitespace-
    normalised, on word boundaries). Pure. F04 API."""
    joined = "\n".join(t for t in texts if t)
    if not joined:
        return []
    hits: list[str] = []
    for phrase in phrases:
        phrase = " ".join(str(phrase or "").split())
        if phrase and phrase not in hits and _phrase_pattern(phrase).search(joined):
            hits.append(phrase)
    return hits


def check_copy(session: Session, user_id, product_id, subject: str | None,
               body: str | None) -> list[str]:
    """Forbidden phrases present in this copy, for this account + product. F04 API."""
    phrases = [r["text"] for r in forbidden_phrases(session, user_id, product_id)]
    return forbidden_hits([subject, body], phrases) if phrases else []


def send_copy_block(session: Session, strategy: Strategy | None, subject: str | None,
                    body: str | None) -> list[str]:
    """The send-time phrase check over the FINAL words. [] when off."""
    if strategy is None or not enabled(session):
        return []
    product = session.get(Product, strategy.product_id)
    if product is None:
        return []
    return check_copy(session, product.user_id, product.id, subject, body)


def sequence_phrase_hits(session: Session, sequence) -> list[tuple[int, str]]:
    """(step_no, phrase) for every forbidden phrase in a sequence's step briefs.
    [] when off. Feeds adversarial_review.rule_findings (BLOCK)."""
    if not enabled(session):
        return []
    strategy = session.get(Strategy, sequence.strategy_id)
    product = session.get(Product, strategy.product_id) if strategy else None
    if product is None:
        return []
    phrases = [r["text"] for r in forbidden_phrases(session, product.user_id, product.id)]
    if not phrases:
        return []
    out: list[tuple[int, str]] = []
    for step in sorted(sequence.steps, key=lambda s: s.step_no):
        out += [(step.step_no, hit) for hit in forbidden_hits([step.template], phrases)]
    return out


# ---- approved claims -> claim verification -----------------------------------

_ABOUT_US = re.compile(r"\b(we|we'?re|we'?ve|we'?ll|we'?d|our|ours|us)\b", re.I)
_PROOF = re.compile(
    r"(?<!\w)(trusted by|used by|relied (?:up)?on by|customers like|clients like|"
    r"customers include|clients include|award[- ]winning|awards?|certified|number one|"
    r"#1|leading|best[- ]in[- ]class|guarantee[sd]?|proven|top[- ]rated|rated|ranked)(?!\w)",
    re.I)
# Up to two words may sit between the number and the noun: "300 UK agencies",
# "40 happy customers" are counts as much as "40 agencies" (T11).
_COUNT = re.compile(
    r"\b\d[\d,]*\+?\s*(?:[a-z][\w'-]*\s+){0,2}(?:customers|clients|companies|teams|users|"
    r"businesses|brands|agencies|founders|years|countries|partners)\b", re.I)


@dataclass
class SenderClaims:
    claims: list[dict] = field(default_factory=list)   # [{"id", "text"}]
    product_name: str = ""
    fail_closed: bool = False


def sender_claim_context(session: Session, strategy: Strategy | None) -> SenderClaims | None:
    """The approved claims that govern this strategy's copy, or None when the
    check does not apply (feature off, or the product has no approved claims
    -- opt-in by existence). An error FAILS CLOSED: an empty library, so every
    proof claim about us is stripped."""
    if strategy is None:
        return None
    try:
        if not enabled(session):
            return None
        product = session.get(Product, strategy.product_id)
        if product is None:
            return None
        claims = [{"id": r["id"], "text": r["text"]}
                  for r in approved_claims(session, product.user_id, product.id)]
        if not claims:
            return None
        return SenderClaims(claims=claims, product_name=product.name or "")
    except Exception:  # noqa: BLE001 -- see the docstring
        logger.exception("approved-claims lookup failed for strategy %s; failing closed",
                         getattr(strategy, "id", None))
        return SenderClaims(claims=[], product_name="", fail_closed=True)


def is_sender_claim(sentence: str, product_name: str = "") -> bool:
    """A sentence about US that makes a proof claim (metric, count, proof phrase)."""
    from app.services.claim_verification import CATEGORY_PATTERNS  # noqa: PLC0415

    about_us = bool(_ABOUT_US.search(sentence)) or (
        len(product_name.strip()) > 2
        and re.search(rf"(?<!\w){re.escape(product_name.strip())}(?!\w)", sentence, re.I))
    if not about_us:
        return False
    return bool(_PROOF.search(sentence) or _COUNT.search(sentence)
                or CATEGORY_PATTERNS["metrics"].search(sentence))


def _required_phrases(sentence: str) -> list[str]:
    from app.services.claim_verification import CATEGORY_PATTERNS  # noqa: PLC0415

    phrases = [" ".join(m.group(0).lower().split()) for m in _PROOF.finditer(sentence)]
    phrases += [m.group(0).strip().lower() for m in CATEGORY_PATTERNS["metrics"].finditer(sentence)
                if not re.search(r"\d", m.group(0))]
    return list(dict.fromkeys(phrases))


def _claim_support(sentence: str, claims: list[dict], lead: Lead,
                   product_name: str) -> tuple[dict | None, list[str]]:
    """(the approved claim that backs the sentence, []) or (None, missing)."""
    from app.services import claim_verification as cv  # noqa: PLC0415

    tokens = cv._claim_tokens(sentence, lead, product_name.lower())
    phrases = _required_phrases(sentence)
    best_missing: list[str] | None = None
    for claim in claims:
        text = (claim.get("text") or "").lower()
        known = cv._numbers(text)
        missing = [f"number:{n:g}" for n in tokens["numbers"]
                   if not any(abs(n - k) <= max(1e-6, abs(k) * 0.005) for k in known)]
        missing += [f"series:{s}" for s in tokens["series"] if f"series {s}" not in text]
        missing += [f"quote:{q}" for q in tokens["quotes"] if q not in text]
        missing += [f"name:{p}" for p in tokens["proper"] if p.lower() not in text]
        missing += [f"phrase:{p}" for p in phrases if p not in " ".join(text.split())]
        if not missing:
            return claim, []
        if best_missing is None or len(missing) < len(best_missing):
            best_missing = missing
    return None, best_missing if best_missing is not None else ["no approved claim on record"]


def verify_sender_claims(text: str | None, claims: list[dict], *, field_name: str, lead: Lead,
                         product_name: str = "", short: bool = False,
                         empty: str | None = None,
                         verified_texts=frozenset()) -> tuple[str | None, list]:
    """Strip every proof claim about us that no approved claim backs.

    Pure. Returns (text, [claim_verification.Decision]) with category
    "sender_claim" and extractor "library". `short` fields (subject, template
    slot, list item) are replaced by `empty` when anything in them is
    unsupported, exactly as the prospect-claim engine treats them. A sentence
    the prospect engine already VERIFIED about the prospect is left alone.
    F04 API."""
    from app.services import claim_verification as cv  # noqa: PLC0415

    if not text:
        return text, []
    decisions: list = []
    out_paragraphs: list[str] = []
    for paragraph in text.split("\n"):
        if not paragraph.strip():
            out_paragraphs.append(paragraph)
            continue
        kept: list[str] = []
        for sentence in cv._SENTENCE.findall(paragraph):
            stripped = sentence.strip()
            if not stripped:
                continue
            if not is_sender_claim(stripped, product_name) or (
                    stripped in verified_texts and cv._about_them(stripped, lead)):
                kept.append(sentence)
                continue
            claim, missing = _claim_support(stripped, claims, lead, product_name)
            if claim is not None:
                decisions.append(cv.Decision(
                    field_name, "sender_claim", stripped, cv.VERIFIED, "library",
                    cv.Evidence("approved_claim", f"approved_claim:{claim['id']}",
                                claim["text"])))
                kept.append(sentence)
            else:
                decisions.append(cv.Decision(field_name, "sender_claim", stripped, cv.STRIPPED,
                                             "library", None, missing))
        out_paragraphs.append("".join(kept).rstrip())

    unsupported = [d for d in decisions if d.verdict != cv.VERIFIED]
    if short:
        if not unsupported:
            return text, decisions
        for d in unsupported:
            if empty == "":
                d.verdict, d.replacement = cv.STRIPPED, None
            else:
                d.verdict, d.replacement = cv.REWRITTEN, empty
        return empty, decisions
    if not unsupported:
        return text, decisions
    result = re.sub(r"\n{3,}", "\n\n", "\n".join(out_paragraphs)).strip()
    if not result:
        result = cv._hook_line(lead)
        unsupported[-1].verdict, unsupported[-1].replacement = cv.REWRITTEN, result
    return result, decisions
