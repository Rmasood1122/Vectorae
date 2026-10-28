"""Lead pipeline — the sourcing chain as Celery tasks.

Chain per batch:  source_leads -> enrich_leads -> find_missing_emails
                  -> verify_emails -> finalize_lead_batch

Guarantees (mirroring the M1 engine's resumability rule):
- Idempotent: every stage selects work by lead status, and dedupe is
  enforced both in code and by the DB's unique constraints — re-running a
  stage never duplicates a lead.
- Resumable: each lead commits individually as it advances; a crashed
  stage re-runs and continues with whatever leads are still behind.
- Suppression list is checked at sourcing time AND again whenever a new
  email/phone is discovered. No exceptions — compliance rule.
- F05 Guardrails Studio (system setting guardrails_enabled): the owning
  account's exclusion lists are checked at the same three points.

Each task body is a plain `_impl` function taking a Session, so the test
suite exercises the real logic with SQLite + fake adapters and zero
broker/API usage.
"""

import logging
import uuid

from celery import chain
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.db.base import SessionLocal
from app.db.models import BatchStage, Lead, LeadBatch, LeadStatus, SuppressionEntry
from app.integrations.base import (
    EmailVerifier,
    LeadSource,
    RawLead,
    get_email_verifier,
    get_lead_source,
)

# Importing the adapter modules registers them.
import app.integrations.apollo  # noqa: F401
import app.integrations.hunter  # noqa: F401
from app.integrations.hunter import DataSubjectOptedOut

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Provider factories (tests monkeypatch these two functions)
# --------------------------------------------------------------------------


def _get_source(batch: LeadBatch) -> LeadSource:
    return get_lead_source(batch.source_provider or settings.lead_source_provider)


def _get_verifier(batch: LeadBatch) -> EmailVerifier:
    return get_email_verifier(batch.verifier_provider or settings.email_verifier_provider)


# --------------------------------------------------------------------------
# Suppression — always checked, no exceptions
# --------------------------------------------------------------------------


def suppression_phone(phone: str | None) -> str | None:
    """The ONE form a phone is written to suppression_list in: '+<digits>'.

    A value with no digits at all is kept as given (stripped) -- there is
    nothing to normalise, and the row must still be writable.
    """
    from app.integrations.whatsapp import normalize_phone  # noqa: PLC0415

    raw = (phone or "").strip()
    if not raw:
        return None
    digits = normalize_phone(raw)
    return f"+{digits}" if digits else raw


def _phone_suppressed(session: Session, phone: str) -> bool:
    """Is this number suppressed, in WHATEVER format either side is written?

    NEW-04: this was an exact string comparison, so a STOP recorded as
    '+923001112233' did not block a lead stored as '+92 300-111 2233'.

    Rows written since then are canonical (suppression_phone), so the indexed
    lookup below finds them. Rows written before may be in any format, and
    there is no normalised column to index, so a miss falls back to
    normalising every remaining stored phone. That is a table scan per
    not-suppressed number; it goes away with a normalised column.
    """
    from app.integrations.whatsapp import normalize_phone  # noqa: PLC0415

    raw = phone.strip()
    digits = normalize_phone(raw)
    known_forms = {raw, digits, f"+{digits}"} if digits else {raw}
    if session.execute(
        select(SuppressionEntry.id)
        .where(SuppressionEntry.phone.in_(known_forms)).limit(1)
    ).scalar_one_or_none() is not None:
        return True
    if not digits:
        return False
    stored_phones = session.execute(
        select(SuppressionEntry.phone)
        .where(SuppressionEntry.phone.isnot(None),
               SuppressionEntry.phone.notin_(known_forms))
    ).scalars()
    return any(normalize_phone(stored) == digits for stored in stored_phones)


def is_suppressed(session: Session, email: str | None = None, phone: str | None = None,
                  linkedin: str | None = None) -> bool:
    """True if ANY of the contact's identifiers is suppressed.

    Feature Group 5 added `linkedin` (a profile URL or slug), checked against
    linkedin_suppressions. Every send path passes all three, so a person who
    opted out on one channel is never reached on another.

    Phones are compared normalised (see _phone_suppressed); emails exactly,
    lower-cased, as before.
    """
    if email:
        row = session.execute(
            select(SuppressionEntry.id)
            .where(SuppressionEntry.email == email.lower().strip()).limit(1)
        ).scalar_one_or_none()
        if row is not None:
            return True
    if phone and phone.strip() and _phone_suppressed(session, phone):
        return True
    if linkedin:
        from app.db.models import LinkedInSuppression  # noqa: PLC0415
        from app.services.linkedin_outreach import normalize_profile  # noqa: PLC0415

        profile = normalize_profile(linkedin)
        if profile and session.execute(
            select(LinkedInSuppression.id).where(LinkedInSuppression.profile == profile)
        ).scalar_one_or_none() is not None:
            return True
    return False


# --------------------------------------------------------------------------
# Stage implementations
# --------------------------------------------------------------------------


def _set_stage(session: Session, batch: LeadBatch, stage: BatchStage) -> None:
    batch.stage = stage
    session.commit()


def _lead_exists(session: Session, batch: LeadBatch, raw: RawLead) -> bool:
    conditions = []
    if raw.email:
        conditions.append(Lead.email == raw.email)
    if raw.external_id:
        conditions.append((Lead.source == raw.source) & (Lead.external_id == raw.external_id))
    if not conditions:
        return False
    row = session.execute(
        select(Lead.id)
        .where(Lead.strategy_id == batch.strategy_id, or_(*conditions))
        .limit(1)
    ).scalar_one_or_none()
    return row is not None


def _linkedin_url(payload: dict | None) -> str | None:
    """Feature Group 2/5: the profile URL Apollo reports (top level on search
    rows, person.linkedin_url on enrichment)."""
    from app.services.personalization_context import linkedin_url_from  # noqa: PLC0415

    return linkedin_url_from(payload)


def strategy_lead_count(session: Session, strategy_id: uuid.UUID) -> int:
    """Leads that count toward max_leads_per_strategy: every lead of the
    strategy except DROPPED ones (suppressed, GDPR-erased, rejected)."""
    return int(session.execute(
        select(func.count(Lead.id)).where(Lead.strategy_id == strategy_id,
                                          Lead.status != LeadStatus.DROPPED)
    ).scalar_one() or 0)


def strategy_lead_room(session: Session, strategy_id: uuid.UUID) -> int | None:
    """How many more leads the strategy's owner's plan allows, None = unlimited."""
    from app.core.plans import plan_limit_remaining  # noqa: PLC0415
    from app.db.models import Product, Strategy, User  # noqa: PLC0415

    owner = session.execute(
        select(User).join(Product, Product.user_id == User.id)
        .join(Strategy, Strategy.product_id == Product.id)
        .where(Strategy.id == strategy_id)
    ).scalar_one_or_none()
    if owner is None:
        return None
    return plan_limit_remaining(owner, "max_leads_per_strategy",
                                strategy_lead_count(session, strategy_id))


def source_leads_impl(session: Session, batch_id: uuid.UUID, source: LeadSource | None = None) -> dict:
    batch = session.get(LeadBatch, batch_id)
    _set_stage(session, batch, BatchStage.SOURCING)
    source = source or _get_source(batch)

    raw_leads = source.search(batch.icp_criteria_json or {}, batch.requested_leads)

    # Plan limit (max_leads_per_strategy), enforced HERE as well as at the API:
    # the API caps the request, but two batches for one strategy can run, and
    # an account can be downgraded between the request and this task.
    remaining = strategy_lead_room(session, batch.strategy_id)

    # F05 Guardrails Studio: the account's own exclusion lists (customers, open
    # deals, churned, do-not-contact). None when the feature is off, and then
    # nothing below changes -- not even the shape of the result.
    from app.services import guardrails  # noqa: PLC0415

    guard_owner = guardrails.active_owner(session, batch.strategy_id)

    added = skipped_duplicate = skipped_suppressed = skipped_plan_limit = skipped_excluded = 0
    for raw in raw_leads:
        if remaining is not None and remaining <= 0:
            skipped_plan_limit += 1
            continue
        # All three identifiers, like every send path: after a GDPR erasure
        # the person's LinkedIn profile is suppressed too, and a LinkedIn-only
        # row of the same person must not be sourced again.
        if is_suppressed(session, raw.email, raw.phone, linkedin=_linkedin_url(raw.raw)):
            skipped_suppressed += 1
            continue
        if guard_owner is not None and guardrails.find_match(
                session, guard_owner, email=raw.email, phone=raw.phone,
                linkedin=_linkedin_url(raw.raw), company=raw.company,
                domains=[raw.company_domain]) is not None:
            skipped_excluded += 1
            continue
        if _lead_exists(session, batch, raw):
            skipped_duplicate += 1
            continue
        session.add(Lead(
            strategy_id=batch.strategy_id,
            batch_id=batch.id,
            source=raw.source,
            external_id=raw.external_id,
            full_name=raw.full_name,
            title=raw.title,
            company=raw.company,
            email=raw.email,
            phone=raw.phone,
            enrichment_json={"raw": raw.raw, "company_domain": raw.company_domain},
            status=LeadStatus.SOURCED,
            linkedin_url=_linkedin_url(raw.raw),
        ))
        try:
            session.commit()  # per-lead commit: crash-safe, resume-safe
            added += 1
            if remaining is not None:
                remaining -= 1
        except IntegrityError:
            session.rollback()  # racing worker inserted it — schema-level dedupe
            skipped_duplicate += 1

    counts = {"added": added, "duplicates": skipped_duplicate, "suppressed": skipped_suppressed,
              "plan_limit": skipped_plan_limit}
    if guard_owner is not None:
        counts["excluded"] = skipped_excluded
    logger.info("batch %s sourced: %s", batch_id, counts)
    return counts


def _opted_out_at_provider(session: Session, lead: Lead) -> None:
    """The email provider (Hunter, HTTP 451) says this person asked it not to
    process their personal data: drop the lead and suppress the person on
    every channel, so they are never sourced or contacted (founder choice,
    2026-10-27)."""
    from app.services import consent  # noqa: PLC0415

    lead.status = LeadStatus.DROPPED
    lead.enrichment_json = {**(lead.enrichment_json or {}),
                            "dropped_reason": "opted_out_at_data_provider"}
    consent.suppress_everywhere(session, lead, source="data_provider",
                                reason="opted_out_at_data_provider",
                                detail="the email provider reported a data-processing objection (HTTP 451)")
    session.commit()


def enrich_leads_impl(session: Session, batch_id: uuid.UUID, source: LeadSource | None = None) -> int:
    batch = session.get(LeadBatch, batch_id)
    _set_stage(session, batch, BatchStage.ENRICHING)
    source = source or _get_source(batch)

    leads = session.execute(
        select(Lead).where(Lead.batch_id == batch_id, Lead.status == LeadStatus.SOURCED)
    ).scalars().all()

    from app.services import guardrails  # noqa: PLC0415

    guard_owner = guardrails.active_owner(session, batch.strategy_id)

    processed = 0
    for lead in leads:
        stored = lead.enrichment_json or {}
        raw = RawLead(
            source=lead.source,
            external_id=lead.external_id,
            full_name=lead.full_name,
            title=lead.title,
            company=lead.company,
            company_domain=stored.get("company_domain"),
            email=lead.email,
            phone=lead.phone,
            raw=stored.get("raw") or {},
        )
        enriched = source.enrich(raw)

        new_email = lead.email or enriched.email
        new_phone = lead.phone or enriched.phone
        # F05: enrichment can reveal who this really is (the company domain,
        # an email) -- an excluded identity is dropped here, before any
        # sequence can be built on it.
        excluded = None if guard_owner is None else guardrails.find_match(
            session, guard_owner, email=new_email, phone=new_phone,
            linkedin=lead.linkedin_url or _linkedin_url(enriched.enrichment),
            company=lead.company or enriched.company,
            domains=[enriched.company_domain, stored.get("company_domain")])
        if is_suppressed(session, new_email, new_phone,
                         linkedin=lead.linkedin_url or _linkedin_url(enriched.enrichment)):
            lead.status = LeadStatus.DROPPED
            lead.enrichment_json = {**stored, "dropped_reason": "suppressed"}
        elif excluded is not None:
            lead.status = LeadStatus.DROPPED
            lead.enrichment_json = {**stored, "dropped_reason": "excluded",
                                    "excluded_by": str(excluded.id)}
        else:
            lead.email = new_email
            lead.phone = new_phone
            lead.full_name = lead.full_name or enriched.full_name
            lead.title = lead.title or enriched.title
            lead.company = lead.company or enriched.company
            lead.linkedin_url = lead.linkedin_url or _linkedin_url(enriched.enrichment)
            lead.enrichment_json = {
                **stored,
                "company_domain": enriched.company_domain or stored.get("company_domain"),
                "enrichment": enriched.enrichment,
            }
            lead.status = LeadStatus.ENRICHED
            # The lead's own timezone, from its location (country / state /
            # provider time_zone), where the send window reads it. Never a
            # fallback zone: see app/services/lead_timezone.py.
            from app.services import lead_timezone as lead_tz  # noqa: PLC0415

            lead_tz.record(lead)
            # Part 1 Feature 10: tag every field this stage actually filled,
            # with the provider that filled it. Never raises -- enrichment has
            # already succeeded and losing a tag must not lose the lead.
            from app.services import provenance  # noqa: PLC0415

            provenance.record_enrichment(session, lead, lead.source or provenance.APOLLO,
                                         commit=False)
        try:
            session.commit()  # per-lead: resume continues from here
        except IntegrityError:
            # enrichment surfaced an email another lead already owns
            session.rollback()
            lead.email = None
            lead.status = LeadStatus.ENRICHED
            session.commit()
        processed += 1
    return processed


def find_missing_emails_impl(
    session: Session, batch_id: uuid.UUID, verifier: EmailVerifier | None = None
) -> dict:
    batch = session.get(LeadBatch, batch_id)
    _set_stage(session, batch, BatchStage.FINDING_EMAILS)
    verifier = verifier or _get_verifier(batch)

    # F01: with the waterfall on, this stage walks the provider chain instead
    # (find AND verify, stop at the first verified address). Off = the code
    # below, unchanged.
    from app.services import waterfall_enrichment  # noqa: PLC0415

    if waterfall_enrichment.enabled(session):
        return _find_emails_waterfall(session, batch, verifier)

    leads = session.execute(
        select(Lead).where(Lead.batch_id == batch_id, Lead.status == LeadStatus.ENRICHED)
    ).scalars().all()

    from app.services import guardrails  # noqa: PLC0415

    guard_owner = guardrails.active_owner(session, batch.strategy_id)

    found = already_had = dropped = 0
    for lead in leads:
        if lead.email:
            lead.status = LeadStatus.EMAIL_FOUND
            session.commit()
            already_had += 1
            continue

        domain = (lead.enrichment_json or {}).get("company_domain")
        try:
            email = verifier.find_email(lead.full_name or "", domain or "") if domain else None
        except DataSubjectOptedOut:
            _opted_out_at_provider(session, lead)
            dropped += 1
            continue

        if not email or is_suppressed(session, email=email):
            lead.status = LeadStatus.DROPPED
            lead.enrichment_json = {
                **(lead.enrichment_json or {}),
                "dropped_reason": "no_email_found" if not email else "suppressed",
            }
            session.commit()
            dropped += 1
            continue
        excluded = None if guard_owner is None else guardrails.find_match(
            session, guard_owner, email=email)
        if excluded is not None:
            # F05: the found address is on this account's exclusion list.
            lead.status = LeadStatus.DROPPED
            lead.enrichment_json = {**(lead.enrichment_json or {}),
                                    "dropped_reason": "excluded",
                                    "excluded_by": str(excluded.id)}
            session.commit()
            dropped += 1
            continue

        lead.email = email
        lead.status = LeadStatus.EMAIL_FOUND
        # Part 1 Feature 10: an address BUILT from a name and a domain is not
        # the same claim as one a provider confirmed. Verification overwrites
        # this tag a stage later if the mailbox turns out to accept mail.
        from app.services import provenance  # noqa: PLC0415

        provenance.record(session, lead, "email", provenance.HUNTER_PATTERN,
                          detail=f"found from the domain {domain}", value=email,
                          commit=False)
        try:
            session.commit()
            found += 1
        except IntegrityError:  # duplicate email within the strategy
            session.rollback()
            lead.email = None
            lead.status = LeadStatus.DROPPED
            lead.enrichment_json = {**(lead.enrichment_json or {}), "dropped_reason": "duplicate_email"}
            session.commit()
            dropped += 1

    return {"found": found, "already_had": already_had, "dropped": dropped}


def verify_emails_impl(
    session: Session, batch_id: uuid.UUID, verifier: EmailVerifier | None = None
) -> dict:
    batch = session.get(LeadBatch, batch_id)
    _set_stage(session, batch, BatchStage.VERIFYING)
    verifier = verifier or _get_verifier(batch)

    from app.services import waterfall_enrichment  # noqa: PLC0415

    if waterfall_enrichment.enabled(session):
        return _verify_emails_waterfall(session, batch, verifier)

    leads = session.execute(
        select(Lead).where(Lead.batch_id == batch_id, Lead.status == LeadStatus.EMAIL_FOUND)
    ).scalars().all()

    counts = {"verified": 0, "flagged": 0, "dropped": 0}
    for lead in leads:
        try:
            result = verifier.verify(lead.email)
        except DataSubjectOptedOut:
            _opted_out_at_provider(session, lead)
            counts["dropped"] += 1
            continue
        lead.status = result.lead_status
        lead.enrichment_json = {
            **(lead.enrichment_json or {}),
            "verification": {"status": result.status.value, "score": result.score},
        }
        # Part 1 Feature 10: the verifier's three verdicts are three different
        # SOURCES, not one source with three scores -- deliverable is a checked
        # fact, risky is a warning, unknown was never checked at all.
        from app.services import provenance  # noqa: PLC0415

        provenance.record_email_verification(session, lead, result.status.value,
                                             result.score, commit=False)
        session.commit()  # per-lead: resume-safe
        if lead.status is LeadStatus.VERIFIED:
            counts["verified"] += 1
        elif lead.status is LeadStatus.FLAGGED:
            counts["flagged"] += 1
        else:
            counts["dropped"] += 1
    return counts


def _find_emails_waterfall(session: Session, batch: LeadBatch,
                           verifier: EmailVerifier) -> dict:
    """F01 find stage: run the provider chain per ENRICHED lead.

    Same counts and the same statuses as the legacy stage: a lead with an
    address moves to EMAIL_FOUND carrying its waterfall verdict (the verify
    stage maps that verdict and never pays for it again); no address, or a
    suppressed one, drops the lead.
    """
    from app.services import provenance  # noqa: PLC0415
    from app.services import waterfall_enrichment as wf  # noqa: PLC0415

    steps, disabled = wf.configured_steps(session, verifier)
    if disabled:
        logger.info("batch %s: waterfall steps skipped: %s", batch.id, disabled)

    leads = session.execute(
        select(Lead).where(Lead.batch_id == batch.id, Lead.status == LeadStatus.ENRICHED)
    ).scalars().all()

    found = already_had = dropped = 0
    for lead in leads:
        ctx = wf.context_for_lead(session, lead, batch)
        original = (lead.email or "").strip().lower()
        try:
            result = wf.run_for_lead(session, lead, verifier, ctx, steps)
        except DataSubjectOptedOut:
            _opted_out_at_provider(session, lead)
            dropped += 1
            continue

        if result.verdict in (wf.NOT_FOUND, wf.SUPPRESSED):
            lead.status = LeadStatus.DROPPED
            lead.enrichment_json = {
                **(lead.enrichment_json or {}),
                "dropped_reason": ("suppressed" if result.verdict == wf.SUPPRESSED
                                   else "no_email_found"),
            }
            session.commit()
            dropped += 1
            continue

        lead.email = result.email
        wf.store_chain_result(lead, result)
        lead.status = LeadStatus.EMAIL_FOUND
        is_new = result.email.lower() != original
        if is_new:
            # Same Part 1 Feature 10 tag the legacy stage writes for a found
            # address; the verify stage overwrites it with the verdict.
            provenance.record(session, lead, "email", provenance.HUNTER_PATTERN,
                              detail=(f"found by {result.provider} from the domain "
                                      f"{(lead.enrichment_json or {}).get('company_domain')}"),
                              value=result.email, commit=False)
        try:
            session.commit()
            if is_new:
                found += 1
            else:
                already_had += 1
        except IntegrityError:  # duplicate email within the strategy
            session.rollback()
            lead.email = None
            lead.status = LeadStatus.DROPPED
            lead.enrichment_json = {**(lead.enrichment_json or {}),
                                    "dropped_reason": "duplicate_email"}
            session.commit()
            dropped += 1

    return {"found": found, "already_had": already_had, "dropped": dropped}


def _verify_emails_waterfall(session: Session, batch: LeadBatch,
                             verifier: EmailVerifier) -> dict:
    """F01 verify stage: map the verdict the find stage stored. A lead with no
    verdict for its current address (the flag was switched on mid-batch, or
    the address was edited) is verified now -- once, ledgered."""
    from app.services import provenance  # noqa: PLC0415
    from app.services import waterfall_enrichment as wf  # noqa: PLC0415

    leads = session.execute(
        select(Lead).where(Lead.batch_id == batch.id, Lead.status == LeadStatus.EMAIL_FOUND)
    ).scalars().all()

    counts = {"verified": 0, "flagged": 0, "dropped": 0}
    for lead in leads:
        record = wf.verdict_for(lead)
        if record is None:
            if not lead.email or is_suppressed(session, email=lead.email):
                lead.status = LeadStatus.DROPPED
                lead.enrichment_json = {**(lead.enrichment_json or {}),
                                        "dropped_reason": ("suppressed" if lead.email
                                                           else "no_email_found")}
                session.commit()
                counts["dropped"] += 1
                continue
            ctx = wf.context_for_lead(session, lead, batch)
            try:
                verified = wf.verify_address(session, lead, lead.email, verifier, ctx,
                                             step="verify")
            except DataSubjectOptedOut:
                _opted_out_at_provider(session, lead)
                counts["dropped"] += 1
                continue
            record = wf.store_verdict(
                lead, verdict=verified.verdict, email=lead.email,
                provider=lead.source, verifier=verified.verifier, score=verified.score,
                legacy_status=verified.legacy_status, checked_at=verified.checked_at)

        verdict = record["verdict"]
        lead.status = wf.LEAD_STATUS_FOR[verdict]
        lead.enrichment_json = {
            **(lead.enrichment_json or {}),
            # The legacy key, still read by older consumers.
            "verification": {"status": record.get("legacy_status") or verdict,
                             "score": record.get("score")},
        }
        provenance.record_email_verification(session, lead, wf.PROVENANCE_STATUS_FOR[verdict],
                                             record.get("score"), commit=False)
        session.commit()
        if lead.status is LeadStatus.VERIFIED:
            counts["verified"] += 1
        elif lead.status is LeadStatus.FLAGGED:
            counts["flagged"] += 1
        else:
            counts["dropped"] += 1
    return counts


def finalize_lead_batch_impl(session: Session, batch_id: uuid.UUID) -> dict:
    batch = session.get(LeadBatch, batch_id)
    rows = session.execute(select(Lead.status).where(Lead.batch_id == batch_id)).scalars().all()
    summary = {status.value: 0 for status in LeadStatus}
    for s in rows:
        summary[s.value] += 1
    batch.summary_json = {"counts": summary, "total": len(rows)}
    batch.stage = BatchStage.FINALIZED
    session.commit()
    logger.info("batch %s finalized: %s", batch_id, batch.summary_json)

    # Feature Group 1: score the leads that survived verification. After the
    # FINALIZED commit on purpose -- sourcing has succeeded whatever happens
    # next, and score_batch never raises (a model failure falls back to the
    # heuristic score).
    from app.db.models import Strategy as _Strategy  # noqa: PLC0415
    from app.services import lead_scoring, usage_meter  # noqa: PLC0415

    strategy = session.get(_Strategy, batch.strategy_id) if batch else None
    with usage_meter.owner_scope(session, strategy, "lead_scoring"):
        scored = lead_scoring.score_batch(session, batch_id)
    if scored:
        logger.info("batch %s: scored %d leads", batch_id, scored)
    _emit_lead_sourced(session, batch)
    return batch.summary_json


def _emit_lead_sourced(session: Session, batch: LeadBatch) -> None:
    """Feature Group 4: `lead_sourced` to outbound webhooks (Zapier / Make),
    carrying the batch's verified leads (first 200). Webhook-only -- a push
    per sourcing batch would be noise. Never raises: sourcing has succeeded."""
    from app.db.models import Strategy as _Strategy  # noqa: PLC0415
    from app.services import event_bus, notifications  # noqa: PLC0415
    from app.workers import notification_tasks  # noqa: PLC0415

    try:
        strategy = session.get(_Strategy, batch.strategy_id)
        owner = notifications.owner_of_strategy(session, strategy) if strategy else None
        if owner is None:
            return
        leads = session.execute(
            select(Lead).where(Lead.batch_id == batch.id, Lead.status == LeadStatus.VERIFIED)
            .limit(200)
        ).scalars().all()
        if not leads:
            return
        notification_tasks.enqueue_event(
            owner, "lead_sourced", push=False, slack=False,
            title=f"{len(leads)} leads sourced", body="A sourcing batch finished.",
            deep_link="/pipeline",
            data={"batchId": str(batch.id), "strategyId": str(batch.strategy_id)},
            webhook_payload={"batch_id": str(batch.id), "strategy_id": str(batch.strategy_id),
                             "counts": (batch.summary_json or {}).get("counts", {}),
                             "leads": [event_bus.lead_payload(lead) for lead in leads]},
        )
    except Exception:
        logger.exception("batch %s: lead_sourced event failed", batch.id)


# --------------------------------------------------------------------------
# Celery task wrappers + the chain
# --------------------------------------------------------------------------


def _run_stage(impl, batch_id: str, task) -> dict | int:
    from app.services import spend_guard  # noqa: PLC0415  (F07)

    session = SessionLocal()
    try:
        # F07 Spend Guard: every paid provider call in a stage is checked and
        # metered; a batch the monthly cap stopped runs no further stage.
        with spend_guard.batch_scope(session, uuid.UUID(batch_id)) as guard:
            if guard is not None and guard.stopped:
                return {"skipped": spend_guard.STOP_PREFIX}
            return impl(session, uuid.UUID(batch_id))
    except spend_guard.MonthlySpendCapReached as exc:
        # A clean stop, not a failure: no retry (nothing was spent on the
        # refused call), and the later stages skip.
        spend_guard.stop_batch(session, uuid.UUID(batch_id), exc)
        return {"stopped": spend_guard.STOP_PREFIX}
    except Exception as exc:
        session.rollback()
        batch = session.get(LeadBatch, uuid.UUID(batch_id))
        if batch is not None:
            batch.error = f"{type(exc).__name__}: {exc}"
            session.commit()
        logger.exception("lead stage failed for batch %s — will retry/resume", batch_id)
        raise task.retry(exc=exc, countdown=30)
    finally:
        session.close()


@celery_app.task(name="leadpilot.leads.source", bind=True, max_retries=3)
def source_leads(self, batch_id: str):
    return _run_stage(source_leads_impl, batch_id, self)


@celery_app.task(name="leadpilot.leads.enrich", bind=True, max_retries=3)
def enrich_leads(self, _prev=None, *, batch_id: str):
    return _run_stage(enrich_leads_impl, batch_id, self)


@celery_app.task(name="leadpilot.leads.find_emails", bind=True, max_retries=3)
def find_missing_emails(self, _prev=None, *, batch_id: str):
    return _run_stage(find_missing_emails_impl, batch_id, self)


@celery_app.task(name="leadpilot.leads.verify", bind=True, max_retries=3)
def verify_emails(self, _prev=None, *, batch_id: str):
    return _run_stage(verify_emails_impl, batch_id, self)


@celery_app.task(name="leadpilot.leads.finalize", bind=True, max_retries=3)
def finalize_lead_batch(self, _prev=None, *, batch_id: str):
    return _run_stage(finalize_lead_batch_impl, batch_id, self)


def start_lead_chain(batch_id: str):
    """Enqueue the full sourcing chain for a batch."""
    return chain(
        source_leads.s(batch_id),
        enrich_leads.s(batch_id=batch_id),
        find_missing_emails.s(batch_id=batch_id),
        verify_emails.s(batch_id=batch_id),
        finalize_lead_batch.s(batch_id=batch_id),
    ).apply_async()
