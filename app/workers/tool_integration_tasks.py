"""Celery tasks for tool integrations (routed to the `default` queue).

Task 1: audit_draft_with_aegisaudit
  Audits an AI-generated draft before sending. Returns a pass/flag/block
  decision. Fails OPEN — an optional quality tool must never stop outreach.

Task 2: signalforge_research_lead
  On demand — run research for a prospect that ALREADY exists in the user's
  SIGNALFORGE instance (research also writes a REVIEW_REQUIRED draft), and
  return the ICP score and that draft. It never creates the prospect (that
  would need the WRITE scope, which Vectorae's key does not hold) and never
  generates a second draft: research already made one.

There is no PostIQ task: PostIQ exposes no API to read or trigger its drafts
(see app/integrations/postiq.py).

Arguments arrive JSON-serialised, so UUIDs come in as strings.
"""
from __future__ import annotations

import logging

from app.core.database import SessionLocal
from app.integrations import aegisaudit, signalforge
from app.services import tool_integrations as svc
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

# AegisAudit decision -> what the sender does with the draft.
_DECISIONS = {
    "not_ready": "block",
    "human_review": "flag",
    "review": "flag",
    "ready_for_human_review": "pass",
}


@celery_app.task(bind=True, max_retries=2, default_retry_delay=30,
                 name="tool_integrations.audit_draft")
def audit_draft_with_aegisaudit(self, *, prompt: str, answer: str) -> dict:
    """Audit an AI-generated draft with AegisAudit.

    Returns {"decision": "pass"|"flag"|"block", "gate": str,
    "score": float | None (0-100, AegisAudit's own scale), "findings": list}.
    Callers must treat "block" as a hard stop; "flag" means human review is
    recommended but the email CAN be sent. An unrecognised AegisAudit decision
    is flagged, never silently passed.
    """
    with SessionLocal() as db:
        cfg = svc.get_aegisaudit_config(db)

    if not cfg["enabled"]:
        return {"decision": "pass", "gate": "skipped_disabled", "score": None, "findings": []}

    try:
        result = aegisaudit.audit_response(
            base_url=cfg["base_url"], prompt=prompt, answer=answer, mode=cfg["default_mode"])
    except aegisaudit.AegisAuditError as exc:
        logger.warning("AegisAudit audit failed: %s — allowing draft through", exc)
        return {"decision": "pass", "gate": "skipped_error", "score": None, "findings": []}

    gate = (result.get("decision") or {}).get("decision", "unknown")
    return {
        "decision": _DECISIONS.get(gate, "flag"),
        "gate": gate,
        "score": (result.get("scores") or {}).get("overall"),
        "findings": result.get("findings", []),
    }


@celery_app.task(bind=True, max_retries=2, default_retry_delay=60,
                 # start_research alone may take 300s -- past the app-wide
                 # soft limit -- so this task carries its own.
                 soft_time_limit=420, time_limit=480,
                 name="tool_integrations.signalforge_research")
def signalforge_research_lead(self, *, lead_id: str, prospect_id: int | None = None,
                              tone: str = "PROFESSIONAL") -> dict:
    """Research an existing SIGNALFORGE prospect on behalf of a Vectorae lead.

    Returns {"lead_id", "prospect_id", "research_job_id", "final_status",
    "icp_score", "icp_breakdown", "draft_id", "draft_subject", "draft_body",
    "draft_status", "warnings"}. The draft sits at REVIEW_REQUIRED in
    SIGNALFORGE; nothing is approved or sent.

    Without a prospect_id it returns {"skipped": True, "reason":
    "no_prospect_id_provided", "message": ...}. SIGNALFORGE allows one
    active research job per prospect, so a retry never duplicates work.
    """
    if prospect_id in (None, ""):
        return {"skipped": True, "reason": "no_prospect_id_provided", "message": (
            "No SIGNALFORGE prospect is linked to this lead. Create the prospect in "
            "SIGNALFORGE first, then run research with its prospect_id.")}

    with SessionLocal() as db:
        cfg = svc.get_signalforge_config(db)

    if not cfg:
        return {"skipped": True, "reason": "signalforge_not_configured"}

    conn = {"base_url": cfg["base_url"], "operator_key": cfg["operator_key"]}
    prospect_id = int(prospect_id)
    try:
        research = signalforge.start_research(**conn, prospect_id=prospect_id, tone=tone)
    except signalforge.SignalForgeError as exc:
        logger.error("SIGNALFORGE research failed for lead %s: %s", lead_id, exc)
        if exc.error_code == "NOT_FOUND":
            return {"skipped": True, "reason": "prospect_not_found", "message": (
                f"SIGNALFORGE has no prospect {prospect_id}. Create the prospect in "
                "SIGNALFORGE first, then run research with its prospect_id.")}
        if exc.permanent:
            return {"skipped": True, "reason": str(exc)}
        raise self.retry(exc=exc)

    # The score and draft are best-effort reads: research has already
    # succeeded, so a failure here leaves them None rather than failing it.
    icp_score, breakdown = research.get("icp_score"), None
    try:
        score = signalforge.get_icp_score(**conn, prospect_id=prospect_id)
        icp_score = score.get("overall_score", icp_score)
        breakdown = score.get("breakdown")
    except signalforge.SignalForgeError as exc:
        logger.warning("SIGNALFORGE ICP score failed for lead %s: %s", lead_id, exc)

    draft: dict = {}
    if research.get("draft_id"):
        try:
            draft = signalforge.get_prospect(
                **conn, prospect_id=prospect_id).get("latest_draft") or {}
        except signalforge.SignalForgeError as exc:
            logger.warning("SIGNALFORGE draft read failed for lead %s: %s", lead_id, exc)

    return {
        "lead_id": str(lead_id),
        "prospect_id": prospect_id,
        "research_job_id": research.get("research_job_id"),
        "final_status": research.get("final_status"),
        "icp_score": icp_score,
        "icp_breakdown": breakdown,
        "draft_id": draft.get("id", research.get("draft_id")),
        "draft_subject": draft.get("subject"),
        "draft_body": draft.get("body"),
        "draft_status": draft.get("status"),
        "warnings": research.get("warnings") or [],
    }
