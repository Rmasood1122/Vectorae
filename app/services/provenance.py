"""Data provenance tags (Part 1, Feature 10).

WHY. Every field on a prospect arrives from somewhere, and those somewheres are
not equally trustworthy. A title typed by a human is not a title Apollo guessed
from a job posting. An email Hunter marked deliverable is not one a pattern
generator built from a first name and a domain. A tech stack scraped from a
careers page is not one inferred from a logo.

Today all of them render identically, and a person deciding whether to write
"I saw you're hiring three more inspectors" has no way to know whether that
came from the company's own job board or from a guess. This records, per field,
WHERE IT CAME FROM, HOW CONFIDENT that source is, and WHEN it was observed.

THE CONFIDENCE IS THE SOURCE'S, NOT A MODEL'S. Each source has a documented
default confidence based on what that source actually is -- a verified email is
0.95 because a verification provider said the mailbox accepts mail, an inferred
field is 0.35 because nobody observed it. A caller may override it with
something they know better, but nothing here invents a number.

AGE MATTERS AS MUCH AS SOURCE. A headcount from Apollo is good data; a headcount
from Apollo eighteen months ago is a different claim. `staleness` is reported
beside confidence, and the two are deliberately kept separate rather than
multiplied into one score that hides which is the problem.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.db.models import Lead

logger = logging.getLogger(__name__)

# --- sources, and what each is actually worth ----------------------------
MANUAL = "manual"
APOLLO = "apollo"
HUNTER_VERIFIED = "hunter_verified"
HUNTER_RISKY = "hunter_risky"
HUNTER_PATTERN = "hunter_pattern"
LINKEDIN = "linkedin"
COMPANY_NEWS = "company_news"
CRM_SYNC = "crm_sync"
REPLY = "reply"
INFERRED = "inferred"
IMPORT = "import"

#: The default confidence for each source, and the one-line justification the
#: UI shows on hover. Every number here is a claim about the SOURCE, not about
#: the value -- which is why they are constants and not model output.
SOURCES: dict[str, tuple[float, str]] = {
    MANUAL: (1.0, "Typed or corrected by a person in Vectorae."),
    REPLY: (0.98, "The prospect said it themselves in a reply."),
    HUNTER_VERIFIED: (0.95, "A verification provider confirmed the mailbox accepts mail."),
    CRM_SYNC: (0.9, "Synced from your CRM, where a person maintains it."),
    LINKEDIN: (0.8, "Read from the prospect's own LinkedIn profile or posts."),
    APOLLO: (0.75, "From the Apollo data provider — good coverage, not verified."),
    COMPANY_NEWS: (0.7, "From a published article about the company."),
    IMPORT: (0.6, "From a file you uploaded; only as good as its source."),
    HUNTER_RISKY: (0.5, "A verification provider flagged this address as risky."),
    HUNTER_PATTERN: (0.45, "Constructed from a name and a domain, never verified."),
    INFERRED: (0.35, "Inferred from other fields — nobody observed it."),
}

SOURCE_LABELS = {
    MANUAL: "Entered by hand",
    REPLY: "Said by the prospect",
    HUNTER_VERIFIED: "Verified",
    CRM_SYNC: "From your CRM",
    LINKEDIN: "LinkedIn",
    APOLLO: "Apollo",
    COMPANY_NEWS: "Press",
    IMPORT: "Imported",
    HUNTER_RISKY: "Verified — risky",
    HUNTER_PATTERN: "Guessed pattern",
    INFERRED: "Inferred",
}

# --- the fields worth tagging --------------------------------------------
FIELD_LABELS = {
    "full_name": "Name",
    "title": "Title",
    "email": "Email",
    "phone": "Phone",
    "company": "Company",
    "linkedin_url": "LinkedIn profile",
    "company_size": "Company size",
    "industry": "Industry",
    "company_domain": "Company domain",
    "location": "Location",
    "tech_stack": "Tech stack",
    "intent_signal": "Intent signal",
    "funding": "Funding",
    "revenue": "Revenue",
}
FIELDS = tuple(FIELD_LABELS)

#: Past this, a field is old enough that it is a different claim. Ninety days
#: is roughly a quarter: long enough that a headcount, a title or a tech stack
#: can all have changed without anyone lying.
STALE_DAYS = 90
VERY_STALE_DAYS = 365


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _parse(value) -> datetime | None:
    if isinstance(value, datetime):
        return _aware(value)
    try:
        return _aware(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except (TypeError, ValueError):
        return None


def source_confidence(source: str) -> float:
    """The documented confidence of a source. An unknown source is treated as
    INFERRED rather than as certain -- the safe direction when something
    wrote a field without saying what it was."""
    return SOURCES.get(source, SOURCES[INFERRED])[0]


def source_note(source: str) -> str:
    return SOURCES.get(source, SOURCES[INFERRED])[1]


def source_label(source: str) -> str:
    return SOURCE_LABELS.get(source, source.replace("_", " ").title())


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------


def record(db: Session, lead: Lead, field: str, source: str, *,
           confidence: float | None = None, detail: str | None = None,
           value=None, observed_at: datetime | None = None,
           commit: bool = True) -> dict:
    """Tag one field with where it came from.

    `confidence` overrides the source default only when the caller genuinely
    knows better (a verification score, say). Out-of-range values are clamped
    rather than stored, because a confidence above 1 is a bug that would
    otherwise render as "140% sure".
    """
    observed = _aware(observed_at) or _now()
    if confidence is None:
        confidence = source_confidence(source)
    confidence = max(0.0, min(1.0, float(confidence)))

    current = dict(lead.provenance_json or {})
    current[field] = {
        "source": source,
        "confidence": round(confidence, 3),
        "observed_at": observed.isoformat(),
        "detail": (detail or "")[:300] or None,
        # A snapshot of the value as recorded. Kept so "this title came from
        # Apollo" can be checked against a title someone has since edited by
        # hand -- the mismatch is exactly when provenance matters.
        "value": None if value is None else str(value)[:300],
    }
    lead.provenance_json = current
    lead.provenance_updated_at = observed
    if commit:
        db.commit()
    return current[field]


def record_many(db: Session, lead: Lead, entries: dict, *,
                observed_at: datetime | None = None, commit: bool = True) -> dict:
    """Tag several fields in one write.

    `entries` maps a field to a source string, or to a dict of the keyword
    arguments `record` takes. One commit, because a sourcing run tags a dozen
    fields per lead and a dozen commits per lead is a sourcing run that takes
    all afternoon.
    """
    for field, spec in entries.items():
        if isinstance(spec, str):
            record(db, lead, field, spec, observed_at=observed_at, commit=False)
        elif isinstance(spec, dict):
            record(db, lead, field, spec.pop("source", INFERRED),
                   observed_at=observed_at, commit=False, **spec)
    if commit:
        db.commit()
    return lead.provenance_json or {}


def record_enrichment(db: Session, lead: Lead, source: str, *,
                      observed_at: datetime | None = None,
                      commit: bool = True) -> dict:
    """Tag every field the enrichment stage actually filled, and only those.

    Never raises: enrichment has already succeeded by the time this runs, and
    losing a provenance tag must not lose the lead.
    """
    try:
        org = _org(lead)
        present = {
            "full_name": lead.full_name,
            "title": lead.title,
            "company": lead.company,
            "phone": lead.phone,
            "linkedin_url": lead.linkedin_url,
            "company_domain": (lead.enrichment_json or {}).get("company_domain"),
            "company_size": org.get("estimated_num_employees"),
            "industry": org.get("industry"),
            "location": org.get("city") or org.get("country"),
            "funding": org.get("latest_funding_round_date"),
            "revenue": org.get("annual_revenue"),
        }
        return record_many(
            db, lead,
            {field: {"source": source, "value": value}
             for field, value in present.items() if value},
            observed_at=observed_at, commit=commit)
    except Exception:  # noqa: BLE001 -- see the docstring
        logger.exception("could not record provenance for lead %s", lead.id)
        return lead.provenance_json or {}


def record_email_verification(db: Session, lead: Lead, status: str,
                              score: float | None = None, *,
                              observed_at: datetime | None = None,
                              commit: bool = True) -> dict | None:
    """Tag the email with what the verifier actually said.

    The three verdicts are three different sources, not one source with three
    scores, because they mean different things: deliverable is a checked fact,
    risky is a warning, and a pattern-built address was never checked at all.
    """
    try:
        source = {"valid": HUNTER_VERIFIED, "deliverable": HUNTER_VERIFIED,
                  "risky": HUNTER_RISKY, "accept_all": HUNTER_RISKY,
                  "unknown": HUNTER_PATTERN}.get((status or "").lower(), HUNTER_RISKY)
        return record(db, lead, "email", source,
                      confidence=(score / 100 if score and score > 1 else score),
                      detail=f"verifier said {status}", value=lead.email,
                      observed_at=observed_at, commit=commit)
    except Exception:  # noqa: BLE001
        logger.exception("could not record email provenance for lead %s", lead.id)
        return None


def _org(lead: Lead) -> dict:
    enrichment = lead.enrichment_json if isinstance(lead.enrichment_json, dict) else {}
    person = enrichment.get("person") if isinstance(enrichment.get("person"), dict) else {}
    org = person.get("organization") or enrichment.get("organization") or {}
    return org if isinstance(org, dict) else {}


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------


def staleness(observed_at: datetime | None, now: datetime | None = None) -> dict:
    """How old a fact is, kept SEPARATE from how good its source is.

    Multiplying the two into one number would hide which of them is the
    problem, and they need different fixes: a weak source needs a better
    source, an old fact needs a refresh.
    """
    now = now or _now()
    if observed_at is None:
        return {"days": None, "band": "unknown"}
    days = max(0, (now - observed_at).days)
    if days >= VERY_STALE_DAYS:
        band = "very_stale"
    elif days >= STALE_DAYS:
        band = "stale"
    else:
        band = "fresh"
    return {"days": days, "band": band}


def entry_out(field: str, entry: dict, now: datetime | None = None) -> dict:
    source = str(entry.get("source") or INFERRED)
    observed = _parse(entry.get("observed_at"))
    return {
        "field": field,
        "label": FIELD_LABELS.get(field, field.replace("_", " ").title()),
        "source": source,
        "source_label": source_label(source),
        "source_note": source_note(source),
        "confidence": entry.get("confidence"),
        "observed_at": observed.isoformat() if observed else None,
        "staleness": staleness(observed, now),
        "detail": entry.get("detail"),
        "value": entry.get("value"),
    }


def for_lead(lead: Lead, now: datetime | None = None) -> dict:
    """Every tagged field, weakest first.

    Weakest first because the list exists to answer "what here should I not
    rely on?" -- and a list sorted alphabetically buries that under Company
    and Email.
    """
    now = now or _now()
    raw = lead.provenance_json if isinstance(lead.provenance_json, dict) else {}
    items = [entry_out(field, entry, now) for field, entry in raw.items()
             if isinstance(entry, dict)]
    items.sort(key=lambda item: ((item["confidence"] if item["confidence"] is not None
                                  else 0.0), item["label"]))
    return {
        "lead_id": str(lead.id),
        # NULL provenance is "never recorded", which is NOT "unknown source".
        # An empty list plus this flag lets the UI say which.
        "tracked": bool(raw),
        "updated_at": (_aware(lead.provenance_updated_at).isoformat()
                       if lead.provenance_updated_at else None),
        "items": items,
        "weakest": items[0] if items else None,
        "stale_count": sum(1 for i in items if i["staleness"]["band"] in
                           ("stale", "very_stale")),
    }


def field_out(lead: Lead, field: str, now: datetime | None = None) -> dict | None:
    """One field's tag — what a hover renders. None when untracked."""
    raw = lead.provenance_json if isinstance(lead.provenance_json, dict) else {}
    entry = raw.get(field)
    return entry_out(field, entry, now or _now()) if isinstance(entry, dict) else None
