"""Local business mode + consent-based customer win-back.

Spec: docs/features/local-business-mode.md. Two parts, two flags:

PART A -- LOCAL BUSINESS MODE (flag `local_business_mode_enabled`, default ON).
A product can be a local business (restaurant, clinic, shop, local service).
Its strategies can carry a service area and an offer. That context is added to
prompts that ALREADY run (pipeline steps, ICP extraction, personalization) and
narrows lead sourcing to the service area. Targets are BUSINESS buyers only --
offices that order catering, employers that buy screenings, property managers
that buy in bulk. Vectorae has no consumer prospecting and this module adds
none.

PART B -- CUSTOMER WIN-BACK (flag `customer_winback_enabled`, default OFF).
A local business imports its OWN customers, each with a recorded opt-in
(date + how), and sends simple owner-written campaigns through the existing
send chokepoint. Every channel needs explicit consent, email included; SMS and
calls are not offered; healthcare businesses are refused outright (a clinic's
customer list is a patient list -- HIPAA). `customer_send_block` is the send
path's guard (SAFETY GUARD).

NONE OF THIS IS LEGAL ADVICE. The readings it relies on are listed, with the
rule each comes from, in the spec's compliance review -- every one marked
"needs founder/legal confirmation".
"""

from __future__ import annotations

import csv
import io
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db.models import (
    ChannelType,
    ConsentEvent,
    CustomerImport,
    Lead,
    LeadStatus,
    Message,
    OptInSource,
    Product,
    Sequence,
    Strategy,
    User,
)

logger = logging.getLogger(__name__)

FLAG = "local_business_mode_enabled"
WINBACK_FLAG = "customer_winback_enabled"

#: Lead.source of a customer imported for win-back. Every consumer record in
#: the system carries it; everything B2B-only filters it out.
CUSTOMER_SOURCE = "customer_import"

AUDIENCE_PROSPECTS = "prospects"
AUDIENCE_CUSTOMERS = "customers"
AUDIENCES = (AUDIENCE_PROSPECTS, AUDIENCE_CUSTOMERS)

RESTAURANT, HEALTHCARE, RETAIL, LOCAL_SERVICE, OTHER = (
    "restaurant", "healthcare", "retail", "local_service", "other")

WINBACK_DISABLED = "CUSTOMER_WINBACK_DISABLED"
LOCAL_MODE_DISABLED = "local business mode is switched off on this deployment"

# Send-path vocabulary (outreach_tasks.send_message_impl).
AUDIT_BLOCKED = "blocked_winback"
STOP_REASON = "suppressed_winback"       # sequence_completion: SUPPRESSED
SEND_RESULT = "cancelled_winback"
ERROR_PREFIX = "win-back: "


# --------------------------------------------------------------------------
# Business types -- the local B2B plays
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Play:
    buyer: str                      # who, in words
    why: str                        # what they buy from a business like this
    titles: tuple[str, ...]         # decision-maker titles to search for
    industries: tuple[str, ...]     # industries / organisation kinds


@dataclass(frozen=True)
class BusinessType:
    key: str
    label: str
    plays: tuple[Play, ...]
    guidance: str
    # Recommended windows in the BUSINESS's local time, [start_hour, end_hour).
    # When the owner is serving customers and cannot answer a reply. Opinion,
    # not measured -- see the spec. Read by the send-time scheduler (another
    # branch); nothing in this module schedules anything.
    avoid_hours: tuple[tuple[int, int], ...] = ()
    prefer_hours: tuple[tuple[int, int], ...] = ()
    extra_limits: tuple[str, ...] = field(default=())


BUSINESS_TYPES: dict[str, BusinessType] = {
    RESTAURANT: BusinessType(
        key=RESTAURANT,
        label="Restaurant, café or caterer",
        plays=(
            Play("Offices and companies within the service area",
                 "catering, team lunches, client meetings and corporate events",
                 ("Office Manager", "Executive Assistant", "HR Manager",
                  "Operations Manager", "Facilities Manager"),
                 ("professional services", "technology", "financial services",
                  "real estate", "law practice")),
            Play("Event planners and event venues",
                 "catering partners for corporate events and private functions",
                 ("Event Manager", "Event Planner", "Venue Manager"),
                 ("events services", "hospitality")),
            Play("Hotels and serviced apartments",
                 "guest dining partnerships and overflow catering",
                 ("General Manager", "Guest Services Manager", "Food and Beverage Manager"),
                 ("hospitality", "hotels")),
        ),
        guidance=("Lead with a concrete catering or group-dining package and how "
                  "ordering works (lead time, minimum, delivery). Offer a tasting or "
                  "a first-order trial. Short, friendly, no menus pasted in."),
        avoid_hours=((11, 14), (17, 21)),
        prefer_hours=((9, 11), (14, 17)),
    ),
    HEALTHCARE: BusinessType(
        key=HEALTHCARE,
        label="Clinic, hospital or healthcare practice",
        plays=(
            Play("Employers in the service area",
                 "occupational health, pre-employment checks, flu shots, screenings "
                 "and corporate wellness programmes for their staff",
                 ("HR Director", "HR Manager", "Benefits Manager",
                  "Health and Safety Manager", "Operations Director"),
                 ("manufacturing", "logistics", "construction", "retail", "hospitality")),
            Play("Schools, colleges and sports clubs",
                 "sports physicals, injury care and health-day partnerships",
                 ("Principal", "Athletic Director", "Club Secretary", "Operations Manager"),
                 ("education", "sports")),
            Play("Referring practices",
                 "a B2B referral relationship between practices",
                 ("Practice Manager", "Medical Director"),
                 ("medical practice", "hospital and health care")),
            Play("Insurers and employee-benefits brokers",
                 "network or wellness-programme partnerships",
                 ("Benefits Consultant", "Account Executive", "Partnerships Manager"),
                 ("insurance",)),
        ),
        guidance=("Talk about services for the ORGANISATION (its staff, its pupils, "
                  "its members) -- scheduling, on-site options, turnaround. Plain, "
                  "factual, no outcome promises."),
        avoid_hours=((8, 10),),
        prefer_hours=((10, 12), (14, 16)),
        extra_limits=(
            "Never use, mention or imply any patient information, condition, "
            "treatment, test result or other health data about anyone. Outreach is "
            "to organisations about services for the organisation, never to "
            "patients (HIPAA).",
            "Never claim clinical outcomes or make medical promises.",
        ),
    ),
    RETAIL: BusinessType(
        key=RETAIL,
        label="Retail or local shop",
        plays=(
            Play("Offices in the service area",
                 "office supplies, client and staff gifting, seasonal orders",
                 ("Office Manager", "Executive Assistant", "HR Manager"),
                 ("professional services", "technology", "financial services")),
            Play("Schools and colleges",
                 "supplies, events and bulk orders",
                 ("Business Manager", "Procurement Officer", "Principal"),
                 ("education",)),
            Play("Property and facilities managers",
                 "bulk supply, resident welcome packs and building events",
                 ("Property Manager", "Facilities Manager", "Community Manager"),
                 ("real estate", "facilities services")),
            Play("Hotels, restaurants and venues",
                 "supply, gifting and guest amenities",
                 ("General Manager", "Purchasing Manager"),
                 ("hospitality",)),
        ),
        guidance=("Lead with what you can supply in bulk or as gifts, local "
                  "pickup/delivery, and account terms. Keep it practical."),
        avoid_hours=((12, 14),),
        prefer_hours=((9, 11), (14, 17)),
    ),
    LOCAL_SERVICE: BusinessType(
        key=LOCAL_SERVICE,
        label="Salon, fitness or other local service",
        plays=(
            Play("Employers in the service area",
                 "staff wellness perks and corporate memberships or packages",
                 ("HR Manager", "People and Culture Manager", "Office Manager"),
                 ("professional services", "technology", "financial services")),
            Play("Hotels and event venues",
                 "partner services for guests and events",
                 ("General Manager", "Event Manager", "Guest Services Manager"),
                 ("hospitality", "events services")),
            Play("Property managers",
                 "resident perks and building partnerships",
                 ("Property Manager", "Community Manager"),
                 ("real estate",)),
        ),
        guidance=("Lead with a simple corporate package (price per person or per "
                  "month), how booking works, and a trial for the team."),
        avoid_hours=((17, 20),),
        prefer_hours=((9, 12), (13, 16)),
    ),
    OTHER: BusinessType(
        key=OTHER,
        label="Other local business",
        plays=(
            Play("Offices and organisations in the service area",
                 "the business's products or services for their teams",
                 ("Office Manager", "Operations Manager", "Owner"),
                 ()),
            Play("Neighbouring businesses",
                 "partnerships and cross-referrals",
                 ("Owner", "General Manager"),
                 ()),
        ),
        guidance="Keep it short, concrete and local.",
        prefer_hours=((9, 17),),
    ),
}

BUSINESS_TYPE_KEYS = tuple(BUSINESS_TYPES)
BusinessTypeKey = Literal["restaurant", "healthcare", "retail", "local_service", "other"]


def enabled(db: Session) -> bool:
    from app.services import system_settings  # noqa: PLC0415

    return bool(system_settings.get(db, FLAG))


def winback_enabled(db: Session) -> bool:
    from app.services import system_settings  # noqa: PLC0415

    return bool(system_settings.get(db, WINBACK_FLAG))


def send_time_profile(business_type: str | None) -> dict | None:
    """Recommended local send windows for a business type, for the send-time
    scheduler (built on another branch). Hours are the BUSINESS's local time,
    [start, end). The compliance send window always applies on top and wins.
    None for an unknown / unset type (= no local recommendation)."""
    bt = BUSINESS_TYPES.get(business_type or "")
    if bt is None:
        return None
    return {
        "business_type": bt.key,
        "avoid_local_hours": [list(w) for w in bt.avoid_hours],
        "prefer_local_hours": [list(w) for w in bt.prefer_hours],
        "basis": ("recommendation: when the owner is serving customers and cannot "
                  "answer a reply; not measured"),
    }


def types_out() -> list[dict]:
    return [{
        "key": bt.key,
        "label": bt.label,
        "plays": [{"buyer": p.buyer, "why": p.why, "titles": list(p.titles)}
                  for p in bt.plays],
        "send_time": send_time_profile(bt.key),
        "winback_allowed": bt.key != HEALTHCARE,
    } for bt in BUSINESS_TYPES.values()]


# --------------------------------------------------------------------------
# Service area
# --------------------------------------------------------------------------

#: Local mode is local: the most a service area may span.
MAX_RADIUS = {"mi": 50.0, "km": 80.0}
MIN_RADIUS = {"mi": 0.5, "km": 1.0}
_POSTAL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 \-]{0,10}[A-Za-z0-9]$")


class ServiceArea(BaseModel):
    city: str | None = Field(default=None, max_length=120,
                             description="City or neighbourhood, e.g. 'Astoria, NY'")
    postal_code: str | None = Field(default=None, max_length=12)
    country: str = Field(min_length=2, max_length=2,
                         description="Two-letter country code, e.g. US")
    radius: float = Field(description="Distance from the location; 0.5-50 mi or 1-80 km")
    unit: Literal["mi", "km"] = "mi"
    timezone: str | None = Field(default=None, max_length=64,
                                 description="IANA time zone, e.g. America/New_York")

    @field_validator("city", "postal_code", "timezone")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        value = (value or "").strip()
        return value or None

    @field_validator("country")
    @classmethod
    def _country(cls, value: str) -> str:
        value = value.strip().upper()
        if not re.fullmatch(r"[A-Z]{2}", value):
            raise ValueError("country must be a two-letter code such as US or GB")
        return value

    @field_validator("postal_code")
    @classmethod
    def _postal(cls, value: str | None) -> str | None:
        if value is not None and not _POSTAL.match(value):
            raise ValueError("postal_code must be 2-12 letters, digits, spaces or hyphens")
        return value

    @field_validator("timezone")
    @classmethod
    def _tz(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(f"unknown time zone {value!r}") from None
        return value

    @model_validator(mode="after")
    def _area(self) -> "ServiceArea":
        if not self.city and not self.postal_code:
            raise ValueError("a service area needs a city or a postal code")
        low, high = MIN_RADIUS[self.unit], MAX_RADIUS[self.unit]
        if not (low <= self.radius <= high):
            raise ValueError(f"radius must be between {low:g} and {high:g} {self.unit} "
                             "(local business mode is for a local area)")
        return self


def location_string(area: dict) -> str:
    """The provider location filter for a service area: "<city>, <country>",
    else "<postal code>, <country>". Whether Apollo matches a postal code in
    person_locations is unverified against the live API (see the spec)."""
    place = area.get("city") or area.get("postal_code") or ""
    return f"{place}, {area.get('country')}".strip(", ")


def describe_area(area: dict) -> str:
    place = ", ".join(p for p in (area.get("city"), area.get("postal_code")) if p)
    radius = area.get("radius")
    radius_text = f"{radius:g}" if isinstance(radius, (int, float)) else str(radius)
    return f"within {radius_text} {area.get('unit', 'mi')} of {place}, {area.get('country')}"


# --------------------------------------------------------------------------
# Part A: context for prompts and sourcing
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class LocalContext:
    business_type: BusinessType
    service_area: dict | None
    offer: str | None


def context_for(db: Session, strategy: Strategy | None) -> LocalContext | None:
    """The strategy's local context, or None (not a local business, flag off,
    unknown type). Never raises: a prompt must not fail on this."""
    if strategy is None:
        return None
    try:
        product = db.get(Product, strategy.product_id)
        bt = BUSINESS_TYPES.get((product.business_type if product else None) or "")
        if bt is None or not enabled(db):
            return None
        area = strategy.service_area_json if isinstance(strategy.service_area_json, dict) \
            else None
        return LocalContext(bt, area, (strategy.local_offer or "").strip() or None)
    except Exception:  # noqa: BLE001
        logger.exception("local business context unavailable for strategy %s",
                         getattr(strategy, "id", None))
        return None


def prompt_block(ctx: LocalContext) -> str:
    """The LOCAL BUSINESS CONTEXT section for a prompt. All of it is the
    account owner's own data (not untrusted inbound text)."""
    bt = ctx.business_type
    lines = [
        "LOCAL BUSINESS CONTEXT (set by the account owner: this is a LOCAL business "
        "selling to OTHER BUSINESSES near it):",
        f"- Business type: {bt.label}",
    ]
    if ctx.service_area:
        lines.append(f"- Service area: {describe_area(ctx.service_area)}")
    if ctx.offer:
        lines.append(f"- Offer for business buyers: {ctx.offer}")
    lines.append("- Local B2B buyers to target:")
    for i, play in enumerate(bt.plays, 1):
        lines.append(f"  {i}. {play.buyer} -- {play.why} "
                     f"(typical titles: {', '.join(play.titles)})")
    lines.append(f"- Messaging guidance: {bt.guidance}")
    lines.append("- Hard limits (never break these):")
    limits = [
        "Business buyers only. Never target consumers, households, patients or "
        "people buying for themselves.",
        "Never state a distance or travel time to the prospect (e.g. 'a 20-minute "
        "walk away') unless it is in the lead data; you may say the business is "
        "based in or serves its area.",
        "Mention offers and prices only from the offer above or the product "
        "description.",
        *bt.extra_limits,
    ]
    lines += [f"  * {limit}" for limit in limits]
    return "\n".join(lines)


def block_for_strategy(db: Session, strategy: Strategy | None) -> str:
    ctx = context_for(db, strategy)
    return prompt_block(ctx) if ctx is not None else ""


def apply_to_criteria(db: Session, strategy: Strategy, criteria: dict,
                      *, explicit: bool = False) -> dict:
    """Narrow a lead-search criteria dict to the strategy's local context.

    - `locations` becomes the service area's location, unless the caller gave
      explicit criteria with their own locations;
    - empty `titles` / `industries` are filled from the business type's plays;
    - `local_business` records the type, the full service area and that the
      radius is NOT sent to the provider (no radius parameter is used by the
      Apollo adapter, and none is invented here).
    Unchanged (same dict) when there is no local context.
    """
    ctx = context_for(db, strategy)
    if ctx is None:
        return criteria
    out = dict(criteria)
    area = ctx.service_area
    if area and not (explicit and out.get("locations")):
        out["locations"] = [location_string(area)]
    bt = ctx.business_type
    if not out.get("titles"):
        out["titles"] = sorted({t for p in bt.plays for t in p.titles})
    if not out.get("industries"):
        industries = sorted({i for p in bt.plays for i in p.industries})
        if industries:
            out["industries"] = industries
    out["local_business"] = {
        "business_type": bt.key,
        "service_area": area,
        "radius_sent_to_provider": False,
    }
    return out


def sender_vocabulary(db: Session, strategy: Strategy | None) -> str:
    """Words that describe the SENDER (its area and offer), for the claim
    engine: naming the sender's own neighbourhood is not a claim about the
    prospect."""
    ctx = context_for(db, strategy)
    if ctx is None:
        return ""
    parts = [ctx.business_type.label, ctx.offer or ""]
    if ctx.service_area:
        parts += [ctx.service_area.get("city") or "", ctx.service_area.get("postal_code") or ""]
    return " ".join(p for p in parts if p)


# --------------------------------------------------------------------------
# Part B: consent-based customer win-back
# --------------------------------------------------------------------------

#: Version 2 (review 2026-10-12): adds the HIPAA-entity exclusion. Stored on
#: every customer_imports row so the wording a person confirmed is provable.
ATTESTATION_VERSION = "2"
ATTESTATION = ("I confirm every person in this file is my own customer and opted in "
               "to marketing from my business on the channels listed, on the date and "
               "in the way recorded in the file. This business is not a HIPAA covered "
               "entity or business associate, and this file contains no patients or "
               "patient information.")

HEALTHCARE_REFUSAL = (
    "Customer win-back is not available for healthcare businesses. A clinic's or "
    "hospital's customer list is a patient list, and using patient information for "
    "marketing needs each patient's written HIPAA authorization (45 CFR "
    "164.508(a)(3)), which Vectorae does not collect. Use local business mode to "
    "reach employers, schools, sports clubs, referring practices and insurers instead."
)
HEALTHCARE_LOCKED = (
    "This product was a healthcare business, so customer win-back stays unavailable "
    "for it permanently, whatever its business type is now (HIPAA: a former clinic's "
    "customer list is still a patient list)."
)

PHI_REFUSAL = (
    "This file has a column that looks like health information ({columns}). "
    "Vectorae never accepts health data for marketing. Remove the column(s) and "
    "upload again."
)
PHI_WORDING_REFUSAL = (
    "Line {line} describes the consent with patient or health wording ({words}). "
    "Vectorae never imports patients or health information for marketing, so the "
    "whole file was refused and nothing was saved."
)

#: Header words that mark a health-data column. Refused for EVERY business
#: type -- no marketing import may carry health information.
PHI_TOKENS = frozenset({
    "patient", "patients", "diagnosis", "diagnoses", "condition", "conditions",
    "treatment", "treatments", "prescription", "prescriptions", "medication",
    "medications", "mrn", "medical", "insurance", "insurer", "health", "dob",
    "appointment", "appointments", "allergy", "allergies", "symptom", "symptoms",
    "procedure", "procedures", "icd", "npi", "clinical", "rx", "medicine",
    "medicines", "dosage", "dose", "vaccine", "vaccination",
})
PHI_PHRASES = ("date_of_birth", "birth_date", "birthdate", "birthday")
#: Words in consent_source / consent_text that say the list came from a
#: clinical relationship. Same vocabulary plus care settings and roles.
PHI_WORDING_TOKENS = PHI_TOKENS | frozenset({
    "clinic", "clinics", "hospital", "hospitals", "doctor", "doctors", "dentist",
    "dental", "physician", "pharmacy", "pharmacist", "therapy", "therapist", "nurse",
    "surgery", "surgical", "prescribed", "diagnosed", "checkup", "hipaa", "phi",
})

CONSENT_COLUMNS = ("consent_channels", "consent_date", "consent_source")
#: Every column an import may have. Anything else is refused (422): nothing
#: unexpected -- notes, medications, order history -- is ever accepted, even
#: to be thrown away (data minimisation, and a disguised health column such
#: as "rx" or "notes" cannot slip through the header-word check).
ALLOWED_COLUMNS = ("name", "email", "phone", "linkedin", "consent_channels", "consent_date",
                   "consent_source", "consent_text", "country", "timezone")
CHANNELS_ALLOWED = ("email", "whatsapp")
CHANNELS_REFUSED = {
    "sms": "SMS is not offered: Vectorae has no SMS channel, and marketing texts "
           "to consumers need prior express written consent under the US TCPA",
    "text": "SMS is not offered: Vectorae has no SMS channel",
    "phone": "calls are not offered for customers (TCPA: prior express written "
             "consent for marketing calls with an artificial voice)",
    "call": "calls are not offered for customers",
}
MAX_IMPORT_BYTES = 2 * 1024 * 1024
MAX_IMPORT_ROWS = 5000
#: Column limits, checked per row BEFORE anything is written (the import is
#: one transaction; an over-long value is a refused row, never a DB error).
#: Sized to the columns they land in (leads.full_name 200, leads.email 320,
#: consent_events.detail 300 holds "opted in ... via <source>").
MAX_NAME = 200
MAX_EMAIL = 320
MAX_SOURCE = 200
MAX_CONSENT_TEXT = 1000
MAX_PHONE_RAW = 50
MAX_LINKEDIN = 500
_EMAIL = re.compile(r"^[^@\s,;<>=+@\-][^@\s,;<>]*@[^@\s,;<>]+\.[A-Za-z]{2,}$")
#: OWASP CSV-injection: a cell starting with one of these is a formula when
#: the owner exports the data to a spreadsheet later.
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")

#: "Stop contacting me" kinds in the consent ledger.
REVOKING_KINDS = ("withdrawn", "suppressed", "erased")


class ImportRefused(Exception):
    """The whole import is refused: (HTTP status, detail)."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def is_customer(lead: Lead | None) -> bool:
    return lead is not None and lead.source == CUSTOMER_SOURCE


# --------------------------------------------------------------------------
# One question for every entry point (SAFETY GUARD)
# --------------------------------------------------------------------------

#: The only channels an imported customer is ever reached on.
CUSTOMER_CHANNELS = (ChannelType.EMAIL, ChannelType.WHATSAPP)
CUSTOMER_REFUSAL = ("{action} is not available for an imported customer: a local "
                    "business's own customers are consumers, reached only by win-back "
                    "email or an approved WhatsApp template, and never researched, "
                    "scored, called or looked up at a provider.")


def channel_refusal(lead: Lead | None, channel) -> str | None:
    """Why `lead` may not be contacted on `channel`, or None. The single
    channel question every entry point asks (send path, AI calls, phone
    consent, opt-in APIs). None for every non-customer lead."""
    if not is_customer(lead):
        return None
    value = getattr(channel, "value", channel)
    if value in {c.value for c in CUSTOMER_CHANNELS}:
        return None
    return (f"customers are messaged by email or WhatsApp only, not {value} "
            "(no calls or texts to consumers -- TCPA)")


def refuse_channel(lead: Lead | None, channel) -> None:
    """409 when `channel` is not allowed for this lead (channel_refusal).
    The API form of the send path's channel question: AI calls and phone
    consent ask exactly what send_message_impl asks."""
    refusal = channel_refusal(lead, channel)
    if refusal:
        from fastapi import HTTPException  # noqa: PLC0415

        raise HTTPException(status_code=409, detail=refusal)


def refuse_if_customer(lead: Lead | None, action: str) -> None:
    """409 for an imported customer. Used by every API entry point that would
    send a person's data to a model or a provider, or open a channel that
    win-back does not allow."""
    if is_customer(lead):
        from fastapi import HTTPException  # noqa: PLC0415

        raise HTTPException(status_code=409, detail=CUSTOMER_REFUSAL.format(action=action))


def _header_token_set(name: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", name) if t}


def phi_columns(headers: list[str]) -> list[str]:
    found = []
    for header in headers:
        h = re.sub(r"[^a-z0-9]+", "_", header.lower()).strip("_")
        if _header_token_set(h) & PHI_TOKENS or any(p in h for p in PHI_PHRASES):
            found.append(header)
    return found


def phi_wording(text: str) -> list[str]:
    return sorted(_header_token_set((text or "").lower()) & PHI_WORDING_TOKENS)


def neutralise(value: str | None) -> str | None:
    """Prefix a spreadsheet-formula value with an apostrophe (OWASP)."""
    if value and value.startswith(_FORMULA_START):
        return "'" + value
    return value


# --------------------------------------------------------------------------
# Healthcare lock and business-type changes (audited)
# --------------------------------------------------------------------------


def has_customer_data(db: Session, product: Product) -> bool:
    """The product holds imported customers (any not removed/erased)."""
    return db.execute(
        select(Lead.id).join(Strategy, Strategy.id == Lead.strategy_id)
        .where(Strategy.product_id == product.id, Lead.source == CUSTOMER_SOURCE,
               Lead.status != LeadStatus.DROPPED).limit(1)
    ).first() is not None


def change_business_type(db: Session, product: Product, new: str | None, *,
                         actor: User, request=None) -> None:
    """Set the product's business type, under the healthcare rules, and audit
    who changed it from what to what. Does not commit. Raises ImportRefused.

    THE RULE (docs/features/local-business-mode.md, "Healthcare lock"):
      1. A product holding imported customers can neither BECOME healthcare
         nor LEAVE healthcare: remove (erase) the customers first. A
         healthcare product must never hold a marketing list, and a list must
         never be carried across the boundary.
      2. The first time a product is healthcare, healthcare_locked_at is set
         and never cleared: switching away later keeps B2B targeting working
         but win-back stays refused for that product forever.
    """
    old = product.business_type
    if old == new:
        return
    if (new == HEALTHCARE or old == HEALTHCARE) and has_customer_data(db, product):
        raise ImportRefused(409, "This product holds imported customers. Remove them "
                                 "before changing to or from a healthcare business type.")
    product.business_type = new
    # Leaving healthcare locks too: a type set to healthcare outside this
    # function (an admin edit, a row from before the lock existed) still
    # counts as "was healthcare".
    if HEALTHCARE in (old, new) and product.healthcare_locked_at is None:
        product.healthcare_locked_at = datetime.now(timezone.utc)
    from app.services import security_audit  # noqa: PLC0415

    security_audit.record(db, action=security_audit.PRODUCT_BUSINESS_TYPE_CHANGED,
                          request=request, user_id=actor.id, owner_user_id=product.user_id,
                          target_type="product", target_id=product.id,
                          details={"from": old, "to": new})


def refuse_unless_import_allowed(db: Session, product: Product) -> None:
    """Shared by the import and customer sequences: both flags, business type,
    healthcare (now or ever). Raises ImportRefused."""
    if not winback_enabled(db):
        raise ImportRefused(409, WINBACK_DISABLED)
    if not enabled(db):
        raise ImportRefused(409, LOCAL_MODE_DISABLED)
    if product.business_type == HEALTHCARE:
        raise ImportRefused(403, HEALTHCARE_REFUSAL)
    if product.healthcare_locked_at is not None:
        raise ImportRefused(403, HEALTHCARE_LOCKED)
    if product.business_type not in BUSINESS_TYPES:
        raise ImportRefused(409, "Set this product's local business type first "
                                 "(PUT /products/{id}/local-business).")


def _parse_channels(raw: str) -> list[str]:
    return [c for c in re.split(r"[\s,;|/+]+", raw.strip().lower()) if c]


def _parse_date(raw: str) -> date | None:
    try:
        return date.fromisoformat(raw.strip()[:10])
    except ValueError:
        return None


def revoked_identifiers(db: Session, owner_id, identifiers: list[str]) -> bool:
    """Any withdrawn / suppressed / erased event in this account's consent
    ledger for one of `identifiers` (email, phone or LinkedIn profile)."""
    keys = sorted({i.strip().lower() for i in identifiers if i and i.strip()})
    if not keys:
        return False
    return db.execute(
        select(ConsentEvent.id).where(ConsentEvent.user_id == owner_id,
                                      ConsentEvent.identifier.in_(keys),
                                      ConsentEvent.kind.in_(REVOKING_KINDS)).limit(1)
    ).first() is not None


def _lead_revoked(db: Session, lead: Lead) -> bool:
    return db.execute(
        select(ConsentEvent.id).where(ConsentEvent.lead_id == lead.id,
                                      ConsentEvent.kind.in_(REVOKING_KINDS)).limit(1)
    ).first() is not None


def _read_csv(raw: bytes) -> tuple[list[str], list[dict]]:
    """Headers and rows, or ImportRefused(422) for a file that is not a
    well-formed UTF-8 CSV within the limits."""
    if len(raw) > MAX_IMPORT_BYTES:
        raise ImportRefused(422, f"file too large (max {MAX_IMPORT_BYTES // (1024 * 1024)} MB)")
    if b"\x00" in raw:
        raise ImportRefused(422, "the file contains NUL bytes; export it again as a plain "
                                 "UTF-8 CSV")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ImportRefused(422, "the file is not UTF-8 text; export it again as a "
                                 "UTF-8 CSV") from None
    try:
        reader = csv.reader(io.StringIO(text))
        header = next(reader, None)
        if not header:
            raise ImportRefused(422, "the CSV needs a header row")
        headers = [(h or "").strip().lower() for h in header]
        named = [h for h in headers if h]
        dupes = sorted({h for h in named if named.count(h) > 1})
        if dupes:
            raise ImportRefused(422, f"duplicate column(s) in the header: {', '.join(dupes)}")
        rows: list[dict] = []
        for values in reader:
            if len(rows) >= MAX_IMPORT_ROWS:
                raise ImportRefused(422, f"too many rows (max {MAX_IMPORT_ROWS} per file)")
            if not any((v or "").strip() for v in values):
                rows.append({})
                continue
            rows.append({h: (values[i] if i < len(values) else "").strip()
                         for i, h in enumerate(headers) if h})
    except csv.Error as exc:
        # The parser's own wording stays in the log: no exception text in an
        # API response (gaps-sweep-2 #17, app/core/errors.public_detail).
        logger.info("customer import: CSV parse error: %s", exc)
        raise ImportRefused(422, "the CSV could not be read; check for a very long "
                                 "or broken field (for example an unclosed quote "
                                 "or a field over 128 KB)") from None
    return headers, rows


def import_customers(db: Session, user: User, strategy: Strategy, product: Product,
                     raw: bytes, attestation: str, *, today: date | None = None) -> dict:
    """Import a business's own opted-in customers into `strategy`.

    ONE TRANSACTION: every row is validated first, nothing inside commits, and
    the whole import commits once at the end -- or rolls back and raises
    ImportRefused(500, "... nothing was saved"). The caller has checked
    ownership. Whole-file refusals raise ImportRefused; per-row refusals are
    returned. See the spec, Part B."""
    from app.services import consent  # noqa: PLC0415

    refuse_unless_import_allowed(db, product)
    if (attestation or "").strip() != ATTESTATION:
        raise ImportRefused(422, "Confirm the attestation exactly as written: "
                                 f"\"{ATTESTATION}\"")
    headers, rows = _read_csv(raw)
    phi = phi_columns(headers)
    if phi:
        raise ImportRefused(422, PHI_REFUSAL.format(columns=", ".join(phi)))
    unknown = [h for h in headers if h and h not in ALLOWED_COLUMNS]
    if unknown:
        raise ImportRefused(422, f"unexpected column(s): {', '.join(unknown)}. Only these "
                                 f"columns are accepted: {', '.join(ALLOWED_COLUMNS)}. "
                                 "Remove the others and upload again.")
    missing = [c for c in CONSENT_COLUMNS if c not in headers]
    if missing:
        raise ImportRefused(
            422, "Every customer needs a recorded opt-in. The CSV must have the "
                 f"columns {', '.join(CONSENT_COLUMNS)} (missing: {', '.join(missing)}). "
                 "Customers without recorded consent are never imported.")
    if "email" not in headers and "phone" not in headers:
        raise ImportRefused(422, "the CSV needs an email or a phone column")
    # Health wording anywhere in the consent description means the list came
    # from a clinical relationship: the whole file is refused (HIPAA).
    for line_no, cells in enumerate(rows, start=2):
        words = phi_wording(f"{cells.get('consent_source', '')} {cells.get('consent_text', '')}")
        if words:
            raise ImportRefused(422, PHI_WORDING_REFUSAL.format(line=line_no,
                                                                 words=", ".join(words)))

    try:
        result = _import_rows(db, user, strategy, product, rows,
                              today or datetime.now(timezone.utc).date())
        db.commit()
    except ImportRefused:
        db.rollback()
        raise
    except Exception as exc:  # noqa: BLE001 -- one transaction: all or nothing
        db.rollback()
        logger.exception("customer import for strategy %s failed; rolled back", strategy.id)
        raise ImportRefused(500, "The import failed and nothing was saved "
                                 f"({type(exc).__name__}). Try again; if it keeps "
                                 "failing, contact support.") from exc
    logger.info("customer import %s for strategy %s: %s accepted, %s refused",
                result["import_id"], strategy.id, len(result["accepted"]),
                len(result["rejected"]))
    return result


def _validate_row(cells: dict, today: date) -> tuple[dict | None, str | None]:
    """(parsed row, None) or (None, refusal reason). No database access."""
    from app.services import whatsapp_optin  # noqa: PLC0415
    from app.services.whatsapp_optin import OptInError  # noqa: PLC0415

    channels = _parse_channels(cells.get("consent_channels", ""))
    if not channels:
        return None, ("no consent: consent_channels is empty -- customers without a "
                      "recorded opt-in are never imported")
    refused = [c for c in channels if c in CHANNELS_REFUSED]
    if refused:
        return None, CHANNELS_REFUSED[refused[0]]
    unknown = [c for c in channels if c not in CHANNELS_ALLOWED]
    if unknown:
        return None, f"unknown consent channel {unknown[0]!r} (allowed: email, whatsapp)"
    consented_on = _parse_date(cells.get("consent_date", ""))
    if consented_on is None:
        return None, "no consent: consent_date is missing or not a date (YYYY-MM-DD)"
    if consented_on > today:
        return None, "consent_date is in the future"
    via = cells.get("consent_source", "")
    if len(via) < 3:
        return None, ("no consent: consent_source must say how the person opted in "
                      "(e.g. 'loyalty sign-up form at the till')")
    if len(via) > MAX_SOURCE:
        return None, f"consent_source is too long (max {MAX_SOURCE} characters)"
    consent_text = cells.get("consent_text", "")
    if len(consent_text) > MAX_CONSENT_TEXT:
        return None, f"consent_text is too long (max {MAX_CONSENT_TEXT} characters)"
    name = cells.get("name", "")
    if len(name) > MAX_NAME:
        return None, f"name is too long (max {MAX_NAME} characters)"
    country = cells.get("country") or None
    if country and not re.fullmatch(r"[A-Za-z]{2}", country):
        return None, "country must be a two-letter code"
    tz = cells.get("timezone") or None
    if tz:
        try:
            if len(tz) > 64:
                raise ValueError(tz)
            ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError):
            return None, f"unknown time zone {tz[:64]!r}"
    raw_email = cells.get("email", "").lower()
    raw_phone = cells.get("phone", "")
    linkedin = cells.get("linkedin", "")
    if len(raw_phone) > MAX_PHONE_RAW or len(linkedin) > MAX_LINKEDIN:
        return None, "phone or linkedin value is too long"
    if raw_email and (len(raw_email) > MAX_EMAIL or not _EMAIL.match(raw_email)):
        if "email" in channels:
            return None, "email consent but no valid email address"
        raw_email = raw_email[:MAX_EMAIL]          # still checked against opt-outs
    if "email" in channels and not raw_email:
        return None, "email consent but no valid email address"
    phone = None
    if raw_phone:
        try:
            phone = whatsapp_optin.normalize_e164(raw_phone)
        except OptInError:
            phone = None
    if "whatsapp" in channels and phone is None:
        return None, ("WhatsApp consent but no valid international phone number "
                      "(E.164, e.g. +12125550123)")
    return {"channels": channels, "consented_on": consented_on, "via": via,
            "consent_text": consent_text, "name": name, "country": country, "tz": tz,
            "email": raw_email or None, "phone": phone, "raw_phone": raw_phone or None,
            "linkedin": linkedin or None}, None


def _import_rows(db: Session, user: User, strategy: Strategy, product: Product,
                 rows: list[dict], today: date) -> dict:
    from app.services import consent  # noqa: PLC0415
    from app.services import whatsapp_optin  # noqa: PLC0415
    from app.services.linkedin_outreach import normalize_profile  # noqa: PLC0415
    from app.workers.lead_tasks import is_suppressed, strategy_lead_room  # noqa: PLC0415

    record = CustomerImport(user_id=user.id, strategy_id=strategy.id,
                            business_type=product.business_type, attestation=ATTESTATION,
                            attestation_version=ATTESTATION_VERSION)
    db.add(record)
    db.flush()

    # EVERY lead of this strategy, prospects included and suppressed or not,
    # by email and by phone: one person is one record (H1). Scoped to ONE
    # strategy the caller owns -- never another account's rows.
    by_email: dict[str, Lead] = {}
    by_phone: dict[str, Lead] = {}
    for lead in db.execute(select(Lead).where(Lead.strategy_id == strategy.id)).scalars():
        if lead.email:
            by_email.setdefault(lead.email.strip().lower(), lead)
        if lead.phone:
            try:
                by_phone.setdefault(whatsapp_optin.normalize_e164(lead.phone), lead)
            except Exception:  # noqa: BLE001
                by_phone.setdefault(lead.phone.strip(), lead)
    room = strategy_lead_room(db, strategy.id)
    created = 0
    accepted: list[dict] = []
    rejected: list[dict] = []

    for line_no, cells in enumerate(rows, start=2):           # 1 = the header
        if not cells:
            continue
        row, reason = _validate_row(cells, today)
        if row is None:
            rejected.append({"line": line_no, "reason": reason})
            continue

        # H1 -- an opt-out is final. ANY identifier on the row (consented to
        # or not) that is suppressed, or that this account's consent ledger
        # records as withdrawn / suppressed / erased, refuses the WHOLE row,
        # whatever consent_date the file claims.
        profile = normalize_profile(row["linkedin"]) if row["linkedin"] else None
        identifiers = [row["email"], row["phone"], row["raw_phone"], profile]
        opted_out = (
            (row["email"] and is_suppressed(db, email=row["email"]))
            or (row["phone"] and is_suppressed(db, phone=row["phone"]))
            or (row["raw_phone"] and is_suppressed(db, phone=row["raw_phone"]))
            or (row["linkedin"] and is_suppressed(db, linkedin=row["linkedin"]))
            or revoked_identifiers(db, user.id, identifiers)
        )
        matches = {lead.id: lead for lead in (
            by_email.get(row["email"]) if row["email"] else None,
            by_phone.get(row["phone"]) if row["phone"] else None) if lead is not None}
        if len(matches) > 1:
            rejected.append({"line": line_no, "reason": "this email and this phone belong "
                                                        "to two different records"})
            continue
        lead = next(iter(matches.values()), None)
        if opted_out or (lead is not None and (_lead_revoked(db, lead)
                                               or lead.status is LeadStatus.DROPPED)):
            rejected.append({"line": line_no, "reason": (
                "opted out: this person unsubscribed, opted out or was erased before; "
                "an imported consent never overrides that, whatever its date")})
            continue
        if lead is not None and lead.source != CUSTOMER_SOURCE:
            rejected.append({"line": line_no, "reason": (
                "this address already belongs to a business prospect in this campaign; "
                "one person cannot be both")})
            continue

        # Only the identifiers the person consented to are kept (minimisation).
        email = row["email"] if "email" in row["channels"] else None
        phone = row["phone"] if "whatsapp" in row["channels"] else None
        if lead is not None and email and lead.email and lead.email.lower() != email:
            rejected.append({"line": line_no, "reason": "this record already has a "
                                                        "different email address"})
            continue
        is_new = lead is None
        if is_new:
            if room is not None and created >= room:
                rejected.append({"line": line_no,
                                 "reason": "your plan's lead limit for this campaign is "
                                           "reached"})
                continue
            extra = {"customer_import_id": str(record.id)}
            if row["country"]:
                extra["country"] = row["country"].upper()
            if row["tz"]:
                extra["timezone"] = row["tz"]
            lead = Lead(strategy_id=strategy.id, source=CUSTOMER_SOURCE,
                        full_name=neutralise(row["name"]) or None,
                        email=email, phone=phone, status=LeadStatus.SOURCED,
                        enrichment_json=extra)
            db.add(lead)
            db.flush()
            created += 1
        elif email and not lead.email:
            lead.email = email

        via = neutralise(row["via"])
        consent_text = neutralise(row["consent_text"]) or None
        evidence = f"opted in to marketing on {row['consented_on'].isoformat()} via {via}"
        meta = {"import_id": str(record.id), "consented_on": row["consented_on"].isoformat(),
                "via": via, "consent_text": consent_text}
        granted: list[str] = []
        if phone:
            # commit=False: part of this import's single transaction. Raises
            # OptInError only for a suppressed number, which H1 has already
            # refused above -- so an error here is unexpected and rolls back.
            whatsapp_optin.record_opt_in(
                db, lead, phone=phone, source=OptInSource.MANUAL_IMPORT,
                evidence=f"{evidence} (customer import {record.id})",
                consent_text=consent_text, commit=False)
            granted.append("whatsapp")
        if email:
            consent.record(db, lead=lead, kind=consent.GRANTED, channel=consent.EMAIL,
                           source=CUSTOMER_SOURCE, identifier=email, detail=evidence,
                           actor=user, meta=meta, user_id=user.id, commit=False)
            granted.append("email")
        if email:
            by_email[email] = lead
        if lead.phone:
            by_phone[lead.phone] = lead
        accepted.append({"line": line_no, "lead_id": str(lead.id), "channels": granted})

    record.accepted_count = len(accepted)
    record.rejected_count = len(rejected)
    record.rejected_json = rejected or None
    from app.services import security_audit  # noqa: PLC0415

    security_audit.record(db, action=security_audit.CUSTOMERS_IMPORTED, user_id=user.id,
                          owner_user_id=product.user_id, target_type="customer_import",
                          target_id=record.id,
                          details={"strategy_id": str(strategy.id),
                                   "business_type": product.business_type,
                                   "attestation_version": ATTESTATION_VERSION,
                                   "accepted": len(accepted), "refused": len(rejected)})
    return {
        "import_id": str(record.id),
        "accepted": accepted,
        "rejected": rejected,
        "summary": f"{len(accepted)} imported, {len(rejected)} refused",
        "policy_note": ("Only customers with a recorded opt-in were imported, and only "
                        "on the channels they opted in to. Rows without consent, and "
                        "anyone who opted out before, were refused and not stored."),
    }


# --------------------------------------------------------------------------
# Part B: customer sequences (shape) and copy (deterministic, no model)
# --------------------------------------------------------------------------

PLACEHOLDERS = ("first_name", "business_name")
_PLACEHOLDER = re.compile(r"\{([^{}]*)\}")
#: The only WhatsApp variable sources a customer step may use: deterministic,
#: and nothing a model writes.
CUSTOMER_WA_FIELDS = ("lead.first_name", "lead.full_name")


def customer_step_errors(step_no: int, channel: ChannelType, template: str,
                         whatsapp_kind, variable_mapping: dict | None,
                         expected_vars: dict | None = None) -> list[str]:
    errors: list[str] = []
    if channel not in (ChannelType.EMAIL, ChannelType.WHATSAPP):
        errors.append(f"step {step_no}: customer (win-back) sequences send by email "
                      "or WhatsApp only")
        return errors
    if channel is ChannelType.EMAIL:
        bad = sorted({p for p in _PLACEHOLDER.findall(template) if p not in PLACEHOLDERS})
        if bad:
            errors.append(f"step {step_no}: unknown placeholder(s) "
                          f"{', '.join('{' + b + '}' for b in bad)}; allowed: "
                          f"{', '.join('{' + p + '}' for p in PLACEHOLDERS)}")
        subject, body = split_subject(template)
        if not body.strip():
            errors.append(f"step {step_no}: the message body is empty")
        return errors
    from app.db.models import WhatsAppStepKind  # noqa: PLC0415

    if whatsapp_kind is not None and whatsapp_kind is not WhatsAppStepKind.TEMPLATE:
        errors.append(f"step {step_no}: customer WhatsApp steps must use an approved "
                      "template (business-initiated marketing)")
    mapping = variable_mapping or {}
    unmapped = sorted(str(n) for n in (expected_vars or {}) if str(n) not in mapping)
    if unmapped:
        errors.append(f"step {step_no}: map every template variable ({', '.join(unmapped)}) "
                      f"to one of {', '.join(CUSTOMER_WA_FIELDS)}")
    for slot, source in mapping.items():
        if source not in CUSTOMER_WA_FIELDS:
            errors.append(f"step {step_no}: variable {slot} must be one of "
                          f"{', '.join(CUSTOMER_WA_FIELDS)} -- nothing model-written "
                          "is sent to a customer")
    return errors


def split_subject(template: str) -> tuple[str | None, str]:
    text = (template or "").strip("\n")
    first, _, rest = text.partition("\n")
    if first.lower().startswith("subject:"):
        return first[len("subject:"):].strip() or None, rest.strip("\n")
    return None, text


def _first_name(lead: Lead) -> str:
    name = (lead.full_name or "").strip()
    return name.split()[0] if name else "there"


def render_customer_email(db: Session, strategy: Strategy, lead: Lead,
                          template: str) -> tuple[str, str]:
    """The owner's own copy with the two placeholders filled. No model call,
    no research about the person."""
    product = db.get(Product, strategy.product_id)
    business = (product.name if product else "") or "us"
    values = {"first_name": _first_name(lead), "business_name": business}
    subject, body = split_subject(template)

    def fill(text: str) -> str:
        return _PLACEHOLDER.sub(lambda m: values.get(m.group(1), m.group(0)), text)

    return fill(subject or f"News from {business}"), fill(body).strip()


def customer_whatsapp_values(lead: Lead, mapping: dict | None,
                             expected: dict | None) -> dict[str, str]:
    """Template variables for a customer: deterministic only. Raises
    ComplianceError for anything else (never sent)."""
    from app.core.exceptions import ComplianceError  # noqa: PLC0415

    mapping = mapping or {}
    values: dict[str, str] = {}
    for slot in (expected or mapping):
        source = mapping.get(str(slot))
        if source == "lead.first_name":
            values[str(slot)] = _first_name(lead)
        elif source == "lead.full_name":
            values[str(slot)] = (lead.full_name or "").strip() or "there"
        else:
            raise ComplianceError(f"customer WhatsApp variable {slot} is not a "
                                  "deterministic name field")
    return values


# --------------------------------------------------------------------------
# Part B: the send-time guard (SAFETY GUARD)
# --------------------------------------------------------------------------

_REVOKING = REVOKING_KINDS


def has_email_consent(db: Session, lead: Lead) -> bool:
    """A current email opt-in for this lead in the consent ledger: a `granted`
    email event with no `withdrawn` / `suppressed` / `erased` event (email or
    all channels) at or after it."""
    grant = db.execute(
        select(ConsentEvent.ts).where(ConsentEvent.lead_id == lead.id,
                                      ConsentEvent.kind == "granted",
                                      ConsentEvent.channel == "email")
        .order_by(ConsentEvent.ts.desc()).limit(1)
    ).scalar_one_or_none()
    if grant is None:
        return False
    revoked = db.execute(
        select(ConsentEvent.id).where(ConsentEvent.lead_id == lead.id,
                                      ConsentEvent.kind.in_(_REVOKING),
                                      or_(ConsentEvent.channel == "email",
                                          ConsentEvent.channel == "all"),
                                      ConsentEvent.ts >= grant)
        .limit(1)
    ).first()
    return revoked is None


def customer_send_block(db: Session, lead: Lead, sequence: Sequence | None,
                        message: Message, strategy: Strategy) -> str | None:
    """Why this message must NOT go out, or None. Raises on a lookup error
    (the caller fails closed).

    Applies to every message: a prospect in a customer sequence, or a
    customer in a prospect sequence, is cancelled whatever the flags say."""
    audience = getattr(sequence, "audience", None) or AUDIENCE_PROSPECTS
    customer = is_customer(lead)
    if customer and audience != AUDIENCE_CUSTOMERS:
        return "an imported customer is only messaged by a customer (win-back) sequence"
    if not customer and audience == AUDIENCE_CUSTOMERS:
        return "a customer (win-back) sequence only messages imported customers"
    if not customer:
        return None
    if not winback_enabled(db):
        return "customer win-back is switched off on this deployment"
    refusal = channel_refusal(lead, message.channel)
    if refusal:
        return refusal
    from app.services import reengagement, reengagement_memory  # noqa: PLC0415

    if message.origin in (reengagement.ORIGIN, reengagement_memory.ORIGIN):
        # Automatic, model-written re-engagement touches are B2B features; a
        # customer only ever receives the owner's own win-back copy (or a
        # reply a person approved).
        return "automatic re-engagement touches are never sent to customers"
    product = db.get(Product, strategy.product_id)
    business_type = product.business_type if product else None
    if business_type == HEALTHCARE or (product is not None
                                        and product.healthcare_locked_at is not None):
        return "healthcare businesses may not message customers (HIPAA)"
    if business_type not in BUSINESS_TYPES:
        return "the product is no longer a local business"
    if message.channel is ChannelType.EMAIL and not has_email_consent(db, lead):
        return "no current email opt-in recorded for this customer"
    # H1 defence in depth: an opt-out recorded for the PERSON (this record, any
    # channel-wide event, or any of their identifiers in the account's
    # ledger) stops every channel, whatever an opt-in row still says.
    channel = message.channel.value
    lead_revoked = db.execute(
        select(ConsentEvent.id).where(ConsentEvent.lead_id == lead.id,
                                      ConsentEvent.kind.in_(REVOKING_KINDS),
                                      ConsentEvent.channel.in_(("all", channel)))
        .limit(1)).first() is not None
    if lead_revoked or revoked_identifiers(db, product.user_id if product else None,
                                           [lead.email or "", lead.phone or ""]):
        return "this customer opted out"
    return None


def cancel_for_block(db: Session, message: Message, reason: str) -> None:
    from app.db.models import MessageStatus  # noqa: PLC0415

    message.status = MessageStatus.CANCELLED
    message.error = f"{ERROR_PREFIX}{reason}"[:1000]
