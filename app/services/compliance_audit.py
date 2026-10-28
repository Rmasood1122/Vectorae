"""Region-aware compliance, recorded per send (Feature Group 9).

For every outreach send -- and every send the engine refused on compliance
grounds -- one compliance_audit_log row records the recipient's region (from
app/services/compliance_region.py), the regime that applies, the checks that
were made and the decision. It is the answer to "on what basis did you
email this person?", written at the moment the decision was made.

Regimes and what Vectorae does about each:
  US  CAN-SPAM   unsubscribe link + sender identity in every email; honoured
                 immediately (the law allows 10 business days)
  CA  CASL       B2B cold email relies on IMPLIED consent (a published
                 business address and a message relevant to the role).
                 Vectorae cannot verify that; the audit records
                 "implied_consent_assumed" and the footer states the sender
                 and the unsubscribe route
  EU  GDPR       legitimate interest for B2B outreach; every email carries a
                 notice of the right to object
  UK  UK GDPR /  as EU; corporate subscribers may be emailed under PECR
      PECR
  SG  PDPA       email with an unsubscribe route; phone and SMS additionally
                 need the national Do-Not-Call check, which Vectorae does NOT
                 perform (recorded)
  AU/NZ          inferred consent + identification + unsubscribe
Unknown region: the strictest common baseline (identification, unsubscribe)
is applied and the row says the region was unknown.

None of this is legal advice; it is a record of what was checked.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import ComplianceAuditLog, Lead, Message
from app.services import compliance_region

LEGAL_BASIS = {
    "us": "opt-out regime (CAN-SPAM)",
    "ca": "implied_consent_assumed (CASL: published business contact, role-relevant)",
    "eu": "legitimate interest, B2B (GDPR Art. 6(1)(f)); right to object stated",
    "uk": "legitimate interest, B2B (UK GDPR); corporate subscriber (PECR)",
    "sg": "business contact information (PDPA); DNC registry not checked",
    "au": "inferred consent (Spam Act 2003)",
    "nz": "inferred consent (UEMA 2007)",
}

_NOTICES = {
    "eu": ("You are receiving this because your professional role matches what we offer "
           "(legitimate interest). You can object at any time: reply \"stop\" or use the "
           "link below."),
    "uk": ("You are receiving this because your professional role matches what we offer "
           "(legitimate interest). You can object at any time: reply \"stop\" or use the "
           "link below."),
    "ca": ("Sent by {sender}. You can unsubscribe at any time with the link below or by "
           "replying \"stop\"."),
}


def region_notice(lead: Lead) -> str | None:
    """An extra footer line for the regimes that ask for one. An
    email-network lead of unknown region gets the strictest one (the GDPR
    right-to-object notice) -- review round H2."""
    region = compliance_region.lead_region(lead)
    if region is None and compliance_region.strictest_when_unknown(lead):
        region = "eu"
    text = _NOTICES.get(region or "")
    return text.format(sender=settings.sender_identity) if text else None


def checks_for(lead: Lead, message: Message | None, channel: str) -> dict:
    region = compliance_region.lead_region(lead)
    checks: dict = {
        "region_source": "country" if compliance_region.lead_country(lead) else
                         ("timezone" if region else "unknown"),
        "legal_basis": LEGAL_BASIS.get(region or "", "unknown region: baseline "
                                                     "identification + unsubscribe applied"),
        "suppression_checked": True,
        "sender_identity": bool((settings.sender_identity or "").strip()),
    }
    if channel == "email" and message is not None:
        checks["unsubscribe_link"] = bool(message.unsubscribe_token)
        checks["region_notice"] = region_notice(lead) is not None
        checks["open_tracking"] = compliance_region.open_tracking_allowed(lead)
    if channel == "phone":
        checks["phone_consent"] = lead.phone_consent_at is not None
        checks["dnc_registry_checked"] = False
    if channel == "whatsapp":
        checks["whatsapp_opt_in"] = True   # the engine refuses to send without it
    return checks


def record(db: Session, *, lead: Lead, message: Message | None, channel: str, decision: str,
           user_id=None, extra: dict | None = None) -> ComplianceAuditLog:
    """Add (not commit) one audit row -- it rides the caller's transaction,
    so a send and its audit row are committed together or not at all."""
    region = compliance_region.lead_region(lead)
    row = ComplianceAuditLog(
        user_id=user_id, lead_id=lead.id, message_id=message.id if message else None,
        channel=channel, region=region, regime=compliance_region.REGIMES.get(region or ""),
        decision=decision, checks_json={**checks_for(lead, message, channel), **(extra or {})},
    )
    db.add(row)
    return row


def lead_summary(db: Session, lead: Lead, limit: int = 20) -> dict:
    from sqlalchemy import select  # noqa: PLC0415

    region = compliance_region.lead_region(lead)
    rows = db.execute(select(ComplianceAuditLog).where(ComplianceAuditLog.lead_id == lead.id)
                      .order_by(ComplianceAuditLog.ts.desc()).limit(limit)).scalars().all()
    return {
        "region": region, "regime": compliance_region.REGIMES.get(region or ""),
        "legal_basis": LEGAL_BASIS.get(region or ""),
        "open_tracking": compliance_region.open_tracking_allowed(lead),
        "audit": [audit_out(r) for r in rows],
    }


def audit_out(row: ComplianceAuditLog) -> dict:
    return {"id": str(row.id), "lead_id": str(row.lead_id) if row.lead_id else None,
            "message_id": str(row.message_id) if row.message_id else None,
            "channel": row.channel, "region": row.region, "regime": row.regime,
            "decision": row.decision, "checks": row.checks_json,
            "ts": row.ts.isoformat() if row.ts else None}
