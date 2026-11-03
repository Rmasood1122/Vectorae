"""The compliance and consent layer (Part 1, Feature 9).

WHAT WAS ALREADY THERE, and is unchanged:
  * `compliance_region.py` decides which regime a recipient falls under, from
    their country and failing that their timezone;
  * `compliance_audit.py` writes one row per send saying what was checked and
    on what legal basis;
  * `sequence_engine.compliance_footer` puts the sender identity and the
    unsubscribe link in every email;
  * `lead_tasks.is_suppressed` checks email, phone AND LinkedIn on every send.

WHAT THIS MODULE ADDS, and why each piece exists:

1. ONE SUPPRESSION ACT THAT COVERS EVERY CHANNEL, INCLUDING WHATSAPP.
   `unsubscribe_lead` suppressed email, phone and LinkedIn but left
   `whatsapp_opted_in` alone -- so someone who unsubscribed by email could
   still be messaged on WhatsApp. "Stop contacting me" is a single
   instruction about a person, not a per-channel preference, and it is now
   applied as one.

2. A CONSENT LEDGER. `compliance_audit_log` answers "on what basis did you
   send this?". Nothing answered "when did they tell you to stop, and what
   did you do about it?" -- which is the question a regulator, and a prospect
   writing an angry second email, actually asks.

3. REQUIREMENTS PER REGION AND CHANNEL, stated once. What each regime demands
   was previously spread between a footer builder, a region module and a
   comment. `requirements_for()` puts it in one place that the send path, the
   UI and the tests all read, so they cannot drift.

NONE OF THIS IS LEGAL ADVICE. It is a record of what was checked and what was
done, which is a different and more useful thing.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    ConsentEvent,
    Lead,
    LinkedInSuppression,
    OptInStatus,
    Product,
    Strategy,
    SuppressionEntry,
    User,
    WhatsAppOptIn,
)
from app.services import compliance_audit, compliance_region

logger = logging.getLogger(__name__)

GRANTED, WITHDRAWN, SUPPRESSED, ERASED = "granted", "withdrawn", "suppressed", "erased"
KINDS = (GRANTED, WITHDRAWN, SUPPRESSED, ERASED)

EMAIL, PHONE, LINKEDIN, WHATSAPP, ALL = "email", "phone", "linkedin", "whatsapp", "all"
CHANNELS = (EMAIL, PHONE, LINKEDIN, WHATSAPP)

#: The regime name per region, for the ledger. The legal BASIS text lives in
#: compliance_audit.LEGAL_BASIS and is reused rather than duplicated -- two
#: descriptions of the same rule are two things that can disagree.
REGIMES = {
    "us": "CAN-SPAM",
    "ca": "CASL",
    "eu": "GDPR / ePrivacy",
    "uk": "UK GDPR / PECR",
    "sg": "PDPA",
    "au": "Spam Act 2003",
    "nz": "UEMA 2007",
}
UNKNOWN_REGIME = "unknown region — strictest baseline"


def regime_for(region: str | None) -> str:
    return REGIMES.get(region or "", UNKNOWN_REGIME)


# --------------------------------------------------------------------------
# What each regime demands
# --------------------------------------------------------------------------


def requirements_for(region: str | None, channel: str) -> dict:
    """What has to be true before contacting this person on this channel.

    Stated once, here, because it was previously spread across a footer
    builder, a region module and a comment -- three places that can drift.

    RETURNS
      unsubscribe_required   a working opt-out route must be in the message
      sender_identity        the sender's real identity must be stated
      prior_consent          contact is unlawful WITHOUT recorded opt-in
      tracking_allowed       an open pixel may be used
      notes                  what a person should know, in words
    """
    region = (region or "").lower()
    strict = region in ("eu", "uk")
    notes: list[str] = []

    # WhatsApp is the one channel where prior opt-in is required everywhere,
    # not because of a statute but because Meta's own Business Policy says so
    # -- and losing the number is a harder problem than a regulator's letter.
    if channel == WHATSAPP:
        notes.append("Meta's Business Policy requires recorded opt-in before any "
                     "business-initiated WhatsApp message, in every region.")
        return {"unsubscribe_required": True, "sender_identity": True,
                "prior_consent": True, "tracking_allowed": False, "notes": notes}

    if channel == PHONE:
        notes.append("An AI voice is an 'artificial voice' under the US TCPA "
                     "(FCC, February 2024), so a US number needs prior express "
                     "consent.")
        return {"unsubscribe_required": False, "sender_identity": True,
                "prior_consent": region == "us", "tracking_allowed": False,
                "notes": notes}

    if strict:
        notes.append("B2B outreach relies on legitimate interest; every message "
                     "states the right to object, and tracking pixels need prior "
                     "consent so they are not used.")
    elif region == "ca":
        notes.append("CASL: B2B cold email relies on IMPLIED consent (a published "
                     "business address and a role-relevant message). That cannot be "
                     "verified here, so it is recorded as assumed.")
    elif region == "us":
        notes.append("CAN-SPAM is an opt-out regime: no prior consent needed, but "
                     "the opt-out must work and be honoured promptly.")
    elif region == "sg":
        notes.append("PDPA: phone and SMS additionally need the national Do-Not-Call "
                     "check, which Vectorae does NOT perform.")
    elif not region:
        notes.append("Region unknown, so the strictest common baseline is applied: "
                     "identification and a working opt-out.")

    return {
        "unsubscribe_required": True,
        "sender_identity": True,
        "prior_consent": False,
        "tracking_allowed": not strict,
        "notes": notes,
    }


def lead_requirements(lead: Lead) -> dict:
    """Every channel's requirements for one prospect, with their region."""
    region = compliance_region.lead_region(lead)
    return {
        "region": region,
        "regime": regime_for(region),
        "legal_basis": compliance_audit.LEGAL_BASIS.get(
            region or "", "unknown region: baseline identification + unsubscribe"),
        "channels": {channel: requirements_for(region, channel) for channel in CHANNELS},
    }


# --------------------------------------------------------------------------
# The ledger
# --------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _owner_id(db: Session, lead: Lead | None):
    if lead is None:
        return None
    strategy = db.get(Strategy, lead.strategy_id)
    product = db.get(Product, strategy.product_id) if strategy else None
    return product.user_id if product else None


def record(db: Session, *, lead: Lead | None, kind: str, channel: str, source: str,
           identifier: str | None = None, detail: str | None = None,
           actor: User | None = None, meta: dict | None = None,
           user_id=None, commit: bool = True) -> ConsentEvent:
    """Append one consent event.

    Never raises for a missing region or a deleted strategy: the ledger has to
    be writable in exactly the situations where things have gone wrong.
    """
    region = None
    try:
        region = compliance_region.lead_region(lead) if lead is not None else None
    except Exception:  # noqa: BLE001
        logger.exception("consent ledger: could not resolve a region")

    event = ConsentEvent(
        lead_id=lead.id if lead is not None else None,
        user_id=user_id if user_id is not None else _owner_id(db, lead),
        kind=kind,
        channel=channel,
        identifier=(identifier or "").strip().lower()[:320] or None,
        region=region,
        regime=regime_for(region),
        basis=compliance_audit.LEGAL_BASIS.get(region or "")[:200] if region else None,
        source=source,
        actor_user_id=actor.id if actor is not None else None,
        detail=(detail or "")[:300] or None,
        meta_json=meta,
        ts=_now(),
    )
    db.add(event)
    if commit:
        db.commit()
    return event


# --------------------------------------------------------------------------
# One instruction, every channel
# --------------------------------------------------------------------------


def suppress_everywhere(db: Session, lead: Lead, *, source: str, reason: str,
                        actor: User | None = None, detail: str | None = None) -> dict:
    """"Stop contacting me", applied to the whole person at once.

    A person asking to be left alone is making ONE statement about themselves,
    not setting four per-channel preferences. Before this, an unsubscribe by
    email suppressed email, phone and LinkedIn but left `whatsapp_opted_in`
    True -- so the next WhatsApp step went out anyway.

    RETURNS {channel: "suppressed" | "already" | "no_identifier"} plus the
    events written. Idempotent: suppressing twice adds no duplicate rows.
    """
    from app.workers.lead_tasks import is_suppressed, suppression_phone  # noqa: PLC0415

    result: dict = {}
    events: list[ConsentEvent] = []

    # --- email -----------------------------------------------------------
    if lead.email:
        if is_suppressed(db, email=lead.email):
            result[EMAIL] = "already"
        else:
            db.add(SuppressionEntry(email=lead.email.lower().strip(), reason=reason))
            events.append(record(db, lead=lead, kind=SUPPRESSED, channel=EMAIL,
                                 source=source, identifier=lead.email, detail=detail,
                                 actor=actor, commit=False))
            result[EMAIL] = "suppressed"
    else:
        result[EMAIL] = "no_identifier"

    # --- phone -----------------------------------------------------------
    if lead.phone:
        if is_suppressed(db, phone=lead.phone):
            result[PHONE] = "already"
        else:
            db.add(SuppressionEntry(phone=suppression_phone(lead.phone), reason=reason))
            events.append(record(db, lead=lead, kind=SUPPRESSED, channel=PHONE,
                                 source=source, identifier=lead.phone, detail=detail,
                                 actor=actor, commit=False))
            result[PHONE] = "suppressed"
    else:
        result[PHONE] = "no_identifier"

    # --- linkedin --------------------------------------------------------
    if lead.linkedin_url:
        from app.services.linkedin_outreach import normalize_profile  # noqa: PLC0415

        profile = normalize_profile(lead.linkedin_url)
        if profile:
            # L0: open LinkedIn tasks for this person end now, in every
            # account (evals/vectorae Phase 5B step 3.5).
            from app.services.linkedin_tasks import cancel_for_profile  # noqa: PLC0415

            cancel_for_profile(db, profile)
        if not profile:
            result[LINKEDIN] = "no_identifier"
        elif is_suppressed(db, linkedin=lead.linkedin_url):
            result[LINKEDIN] = "already"
        else:
            db.add(LinkedInSuppression(profile=profile, reason=reason))
            events.append(record(db, lead=lead, kind=SUPPRESSED, channel=LINKEDIN,
                                 source=source, identifier=profile, detail=detail,
                                 actor=actor, commit=False))
            result[LINKEDIN] = "suppressed"
    else:
        result[LINKEDIN] = "no_identifier"

    # --- whatsapp: THE GAP THIS FEATURE CLOSES ---------------------------
    # Revoking the flag is what actually stops the send (the adapter refuses
    # without it); the opt-in row is updated so the audit trail agrees.
    if lead.whatsapp_opted_in:
        lead.whatsapp_opted_in = False
        row = db.execute(
            select(WhatsAppOptIn)
            .where(WhatsAppOptIn.lead_id == lead.id)
            .order_by(WhatsAppOptIn.ts.desc())).scalars().first()
        if row is not None and row.status is not OptInStatus.OPTED_OUT:
            row.status = OptInStatus.OPTED_OUT
        events.append(record(db, lead=lead, kind=SUPPRESSED, channel=WHATSAPP,
                             source=source, identifier=lead.phone, detail=detail,
                             actor=actor, commit=False))
        result[WHATSAPP] = "suppressed"
    else:
        result[WHATSAPP] = "already" if lead.phone else "no_identifier"

    # One "withdrawn" event for the instruction itself, so the ledger reads as
    # a decision followed by its consequences rather than four unrelated rows.
    events.append(record(db, lead=lead, kind=WITHDRAWN, channel=ALL, source=source,
                         identifier=lead.email or lead.phone, detail=detail,
                         actor=actor, meta={"applied": result}, commit=False))
    db.commit()
    logger.info("consent withdrawn for lead %s via %s: %s", lead.id, source, result)
    return {"applied": result, "events": [str(e.id) for e in events]}


def record_grant(db: Session, lead: Lead, *, channel: str, source: str,
                 detail: str | None = None, actor: User | None = None,
                 commit: bool = True) -> ConsentEvent:
    """Consent was GIVEN — a WhatsApp opt-in, a form submission, a phone
    consent. Recorded so the ledger shows both halves; a ledger that only
    holds withdrawals cannot show that contact was lawful to begin with."""
    return record(db, lead=lead, kind=GRANTED, channel=channel, source=source,
                  identifier=(lead.phone if channel in (PHONE, WHATSAPP) else lead.email),
                  detail=detail, actor=actor, commit=commit)


def record_erasure(db: Session, lead: Lead, *, source: str = "gdpr_request",
                   actor: User | None = None, detail: str | None = None) -> ConsentEvent:
    """Personal data was erased. Written BEFORE the delete, with the user id
    and identifier denormalized onto the row, so the proof survives the
    deletion it is proof of."""
    return record(db, lead=lead, kind=ERASED, channel=ALL, source=source,
                  identifier=lead.email or lead.phone,
                  user_id=_owner_id(db, lead), actor=actor,
                  detail=detail or "lead record deleted on request")


# --------------------------------------------------------------------------
# Reading it back
# --------------------------------------------------------------------------


def event_out(event: ConsentEvent) -> dict:
    return {
        "id": str(event.id),
        "lead_id": str(event.lead_id) if event.lead_id else None,
        "kind": event.kind,
        "channel": event.channel,
        "identifier": event.identifier,
        "region": event.region,
        "regime": event.regime,
        "basis": event.basis,
        "source": event.source,
        "detail": event.detail,
        "meta": event.meta_json,
        "actor_user_id": str(event.actor_user_id) if event.actor_user_id else None,
        "ts": event.ts.isoformat() if event.ts else None,
    }


def ledger(db: Session, user_id, *, lead_id=None, identifier: str | None = None,
           kind: str | None = None, limit: int = 200, offset: int = 0) -> dict:
    query = select(ConsentEvent).where(ConsentEvent.user_id == user_id)
    if lead_id is not None:
        query = query.where(ConsentEvent.lead_id == lead_id)
    if identifier:
        query = query.where(ConsentEvent.identifier == identifier.strip().lower())
    if kind:
        query = query.where(ConsentEvent.kind == kind)
    rows = list(db.execute(
        query.order_by(ConsentEvent.ts.desc()).offset(offset).limit(limit)).scalars())
    total = len(list(db.execute(query.with_only_columns(ConsentEvent.id)).all()))
    return {"total": total, "limit": limit, "offset": offset,
            "items": [event_out(row) for row in rows]}


def lead_status(db: Session, lead: Lead) -> dict:
    """Per-channel: may we contact this person, and on what basis?

    The single screen that answers "why is this prospect not being messaged?"
    without reading four tables.
    """
    from app.workers.lead_tasks import is_suppressed  # noqa: PLC0415

    requirements = lead_requirements(lead)
    suppressed = {
        EMAIL: bool(lead.email) and is_suppressed(db, email=lead.email),
        PHONE: bool(lead.phone) and is_suppressed(db, phone=lead.phone),
        LINKEDIN: bool(lead.linkedin_url) and is_suppressed(db, linkedin=lead.linkedin_url),
        WHATSAPP: not lead.whatsapp_opted_in,
    }
    identifiers = {EMAIL: lead.email, PHONE: lead.phone,
                   LINKEDIN: lead.linkedin_url, WHATSAPP: lead.phone}

    channels = {}
    for channel in CHANNELS:
        rules = requirements["channels"][channel]
        has_identifier = bool(identifiers[channel])
        blocked = suppressed[channel]
        if channel == WHATSAPP and not lead.whatsapp_opted_in:
            reason = "no recorded opt-in"
        elif blocked:
            reason = "suppressed"
        elif not has_identifier:
            reason = "no address on file"
        elif rules["prior_consent"] and channel == PHONE and not lead.phone_consent_at:
            reason = "prior express consent required and not recorded"
        else:
            reason = None
        channels[channel] = {
            "contactable": reason is None,
            "reason": reason,
            "identifier": identifiers[channel],
            "requirements": rules,
        }

    return {
        "lead_id": str(lead.id),
        "region": requirements["region"],
        "regime": requirements["regime"],
        "legal_basis": requirements["legal_basis"],
        "channels": channels,
        "history": [event_out(e) for e in db.execute(
            select(ConsentEvent).where(ConsentEvent.lead_id == lead.id)
            .order_by(ConsentEvent.ts.desc()).limit(50)).scalars()],
    }
