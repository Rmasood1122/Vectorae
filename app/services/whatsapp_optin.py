"""WhatsApp opt-in management — Milestone 4, Chunk 2.

Opt-in is what legally and policy-wise separates outreach from spam on
WhatsApp (project knowledge sections E and I), so it is FIRST-CLASS,
APPEND-ONLY data:

  - every consent event is a new WhatsAppOptIn row (never updated);
  - the lead's current status is simply the LATEST row;
  - Lead.whatsapp_opted_in / _at / _source (from Chunk 1) are a
    denormalized cache of that latest row, maintained ONLY here, so the
    adapter's send-time guard stays a cheap single-row read;
  - revocation propagates: opted_out row + cache off + phone on the
    suppression list + hard stop of every WhatsApp sequence enrollment.

Nothing in this module can fabricate consent: manual_import REQUIRES
evidence, and there is no code path that flips the cache to True without
writing an audit row first.
"""

import logging
import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    ChannelType,
    EnrollmentStatus,
    Lead,
    OptInSource,
    OptInStatus,
    Outcome,
    OutcomeEvent,
    Sequence,
    SequenceEnrollment,
    WhatsAppOptIn,
)
from app.services import crypto
from app.services import sequence_engine as engine
from app.workers.lead_tasks import is_suppressed

logger = logging.getLogger(__name__)


class OptInError(ValueError):
    """Invalid opt-in operation (bad phone, missing evidence, ...)."""


# --------------------------------------------------------------------------
# Phone normalization — E.164
# --------------------------------------------------------------------------

# E.164: up to 15 digits, no leading zero on the country code (ITU-T).
_E164_DIGITS = re.compile(r"^[1-9]\d{7,14}$")


def normalize_e164(phone: str) -> str:
    """Return the canonical '+<digits>' form or raise OptInError.

    Accepts common human formats ('+92 300-123 4567', '0092...', digits).
    A leading '00' international prefix is converted to '+'. We do NOT
    guess country codes for bare national numbers — consent records must
    be unambiguous."""
    raw = (phone or "").strip()
    if raw.startswith("00"):
        raw = raw[2:]
    digits = re.sub(r"[^\d]", "", raw)
    if not _E164_DIGITS.match(digits):
        raise OptInError(
            f"phone {phone!r} is not a valid E.164 number — expected an "
            "international number with country code, 8-15 digits "
            "(e.g. +923001234567)"
        )
    return f"+{digits}"


# --------------------------------------------------------------------------
# Reads
# --------------------------------------------------------------------------


def latest_row(session: Session, lead_id: uuid.UUID) -> WhatsAppOptIn | None:
    return session.execute(
        select(WhatsAppOptIn)
        .where(WhatsAppOptIn.lead_id == lead_id)
        .order_by(WhatsAppOptIn.ts.desc(), WhatsAppOptIn.id.desc())
        .limit(1)
    ).scalars().first()


def current_status(session: Session, lead_id: uuid.UUID) -> OptInStatus:
    row = latest_row(session, lead_id)
    return row.status if row is not None else OptInStatus.UNKNOWN


def history(session: Session, lead_id: uuid.UUID) -> list[WhatsAppOptIn]:
    return list(session.execute(
        select(WhatsAppOptIn)
        .where(WhatsAppOptIn.lead_id == lead_id)
        .order_by(WhatsAppOptIn.ts.asc(), WhatsAppOptIn.id.asc())
    ).scalars().all())


# --------------------------------------------------------------------------
# Writes (append-only)
# --------------------------------------------------------------------------


def _sync_lead_cache(lead: Lead, row: WhatsAppOptIn) -> None:
    opted_in = row.status is OptInStatus.OPTED_IN
    lead.whatsapp_opted_in = opted_in
    lead.whatsapp_opt_in_at = row.ts if opted_in else None
    lead.whatsapp_opt_in_source = row.source.value if opted_in else None


def record_opt_in(
    session: Session,
    lead: Lead,
    *,
    phone: str | None = None,
    source: OptInSource,
    evidence: str | None = None,
    consent_text: str | None = None,
    commit: bool = True,
) -> WhatsAppOptIn:
    """Append an opted_in row and sync the lead cache.

    manual_import REQUIRES evidence — a consent record with no evidence
    is worthless in a dispute and violates WhatsApp policy expectations.
    There is deliberately no way to relax this.

    commit=False adds everything to the caller's transaction instead (the
    local-business customer import commits once for the whole file)."""
    if source is OptInSource.MANUAL_IMPORT and not (evidence or "").strip():
        raise OptInError(
            "manual_import opt-ins require evidence (where/when/how this "
            "person consented, e.g. a form URL or signed-up-at note). "
            "Importing numbers without real consent violates WhatsApp "
            "policy and can get the business account banned."
        )
    normalized = normalize_e164(phone or lead.phone or "")
    if is_suppressed(session, phone=normalized) or (
        lead.phone and is_suppressed(session, phone=lead.phone.strip())
    ):
        raise OptInError(
            "this phone is on the suppression list (a prior opt-out or "
            "unsubscribe). Vectorae will not message a suppressed contact "
            "again, and the suppression cannot be lifted from the app, so "
            "no opt-in can be recorded for it."
        )
    row = WhatsAppOptIn(
        lead_id=lead.id,
        phone=normalized,
        status=OptInStatus.OPTED_IN,
        source=source,
        evidence=(evidence or "").strip() or None,
        consent_text=(consent_text or "").strip() or None,
        ts=datetime.now(timezone.utc),
    )
    session.add(row)
    # Keep the canonical number on the lead so send/suppression matching
    # uses one format.
    lead.phone = normalized
    _sync_lead_cache(lead, row)
    # Part 1 Feature 9: both halves of consent go in the ledger. A ledger that
    # only holds withdrawals cannot show that the contact was lawful to begin
    # with, which is the half a dispute usually turns on.
    from app.services import consent  # noqa: PLC0415

    consent.record_grant(session, lead, channel=consent.WHATSAPP,
                         source=f"whatsapp_{source.value}",
                         detail=(evidence or consent_text or "")[:300] or None,
                         commit=commit)
    if commit:
        session.commit()
    logger.info("whatsapp opt-in recorded lead=%s source=%s", lead.id, source.value)
    return row


def revoke_opt_in(
    session: Session,
    lead: Lead,
    *,
    source: OptInSource = OptInSource.API,
    evidence: str | None = None,
) -> WhatsAppOptIn:
    """Append an opted_out row, then PROPAGATE:
    cache off -> phone suppressed -> every WhatsApp sequence enrollment
    hard-stopped. Effective immediately, like the email unsubscribe."""
    now = datetime.now(timezone.utc)
    phone = None
    try:
        phone = normalize_e164(lead.phone or "")
    except OptInError:
        phone = (lead.phone or "").strip() or "unknown"

    row = WhatsAppOptIn(
        lead_id=lead.id,
        phone=phone,
        status=OptInStatus.OPTED_OUT,
        source=source,
        evidence=(evidence or "").strip() or None,
        ts=now,
        revoked_at=now,
    )
    session.add(row)
    _sync_lead_cache(lead, row)

    session.add(Outcome(lead_id=lead.id, event=OutcomeEvent.UNSUBSCRIBED,
                        channel="whatsapp",
                        meta_json={"channel": "whatsapp", "source": source.value}))
    # Part 1 Feature 9: a WhatsApp STOP is an instruction about the PERSON,
    # not about one channel. suppress_everywhere applies it to phone, email
    # and LinkedIn and writes the consent ledger.
    #
    # It is the ONLY writer of the phone suppression row. This function used
    # to add that row itself as well; SessionLocal is autoflush=False, so
    # neither guard's SELECT saw the other's pending row and the commit
    # violated the unique index on suppression_list.phone (NEW-01: a signed
    # STOP answered HTTP 500 and suppressed nothing).
    from app.services import consent  # noqa: PLC0415

    consent.suppress_everywhere(session, lead, source=f"whatsapp_{source.value}",
                                reason="whatsapp_optout",
                                detail="STOP received on WhatsApp")
    session.commit()

    stopped = 0
    enrollments = session.execute(
        select(SequenceEnrollment)
        .join(Sequence, Sequence.id == SequenceEnrollment.sequence_id)
        .where(SequenceEnrollment.lead_id == lead.id,
               Sequence.channel == ChannelType.WHATSAPP)
    ).scalars().all()
    for enrollment in enrollments:
        if enrollment.status is not EnrollmentStatus.STOPPED:
            engine.stop_enrollment(session, enrollment, reason="whatsapp_optout")
            stopped += 1
    session.commit()
    logger.info("whatsapp opt-in revoked lead=%s (stopped %s sequences)",
                lead.id, stopped)
    return row


def record_inbound_initiation(session: Session, lead: Lead,
                              wamid: str | None) -> WhatsAppOptIn | None:
    """The prospect messaged US first. If their status was unknown, that
    inbound message IS an opt-in event (they initiated contact) — record
    it with the message id as evidence. Called by the webhook receiver.
    Never upgrades an explicit opted_out: a suppressed/opted-out contact
    saying something other than STOP does not silently re-subscribe them —
    a human should review that conversation."""
    status = current_status(session, lead.id)
    if status is not OptInStatus.UNKNOWN:
        return None
    try:
        return record_opt_in(
            session, lead,
            source=OptInSource.INBOUND_MESSAGE,
            evidence=f"inbound whatsapp message {wamid or '(no id)'}",
            consent_text=None,
        )
    except OptInError as exc:
        logger.info("inbound initiation not recorded for lead %s: %s", lead.id, exc)
        return None


# --------------------------------------------------------------------------
# Hosted opt-in page tokens (same crypto pattern as unsubscribe tokens)
# --------------------------------------------------------------------------


def make_optin_token(lead_id: uuid.UUID) -> str:
    return crypto.encrypt_json({"lead_id": str(lead_id), "purpose": "whatsapp_optin"})


def parse_optin_token(token: str) -> uuid.UUID:
    data = crypto.decrypt_json(token)
    if data.get("purpose") != "whatsapp_optin":
        raise ValueError("not a whatsapp opt-in token")
    return uuid.UUID(data["lead_id"])
