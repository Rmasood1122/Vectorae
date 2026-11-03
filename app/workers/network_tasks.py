"""Weekly refresh of the private LinkedIn network scores (Growth plan and up).

Re-scores connections that are ALREADY stored against each account's own
products. It calls no model and no external service. It cannot fetch new
connections: LinkedIn has no API for that, so new people only arrive when the
member uploads a fresh export.
"""

from __future__ import annotations

import logging

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

__all__ = ["weekly_refresh", "email_weekly_refresh",
           "gmail_scan_chunk", "enqueue_gmail_chunk", "enqueue_verification"]


@celery_app.task(name="leadpilot.network.weekly_refresh")
def weekly_refresh() -> dict:
    from app.db.base import SessionLocal
    from app.services import linkedin_network

    with SessionLocal() as session:
        result = linkedin_network.refresh_all(session)
    logger.info("linkedin network weekly refresh: %s", result)
    return result


@celery_app.task(name="leadpilot.network.email_weekly_refresh")
def email_weekly_refresh() -> dict:
    """Re-filter (suppression / exclusions change) and re-score every eligible
    account's stored email contacts. Flag off = nothing runs."""
    from app.db.base import SessionLocal  # noqa: PLC0415
    from app.services import email_network  # noqa: PLC0415

    with SessionLocal() as session:
        result = email_network.refresh_all(session)
    logger.info("email network weekly refresh: %s", result)
    return result


@celery_app.task(name="leadpilot.network.gmail_scan_chunk", bind=True, max_retries=5)
def gmail_scan_chunk(self, scan_id: str, seq: int) -> str:
    """One page of a "Scan my connected Gmail" run (headers only). Queues the
    next page itself, spaced to stay under Gmail's per-user quota."""
    import uuid  # noqa: PLC0415

    from app.db.base import SessionLocal  # noqa: PLC0415
    from app.services import email_network_gmail as eng  # noqa: PLC0415

    sid = uuid.UUID(str(scan_id))
    with SessionLocal() as session:
        try:
            outcome = eng.run_chunk(session, sid, int(seq))
        except eng.Transient as exc:
            session.rollback()
            if self.request.retries >= self.max_retries:
                eng.fail(session, sid, "Gmail kept refusing the scan; try again later.")
                return "failed"
            raise self.retry(exc=exc, countdown=60 * (self.request.retries + 1))
        except Exception:  # noqa: BLE001 -- recorded, never silently lost
            logger.exception("gmail scan %s failed", scan_id)
            session.rollback()
            eng.fail(session, sid, "The scan failed. Try again later.")
            return "failed"
    if outcome == "continue":
        enqueue_gmail_chunk(sid, int(seq) + 1, countdown=eng.CHUNK_SPACING_SECONDS)
    return outcome


def enqueue_gmail_chunk(scan_id, seq: int, countdown: int = 0) -> bool:
    """Publish one scan chunk. Never raises: False when the broker refused."""
    try:
        gmail_scan_chunk.apply_async(args=[str(scan_id), int(seq)], countdown=countdown,
                                     retry=False)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not queue gmail scan %s chunk %s: %s", scan_id, seq, exc)
        return False


def enqueue_verification(batch_id) -> bool:
    """The normal F01 verification stage + finalize, for a lead batch made by
    'Add to campaign'. Never raises: False when the broker refused (the batch
    then waits in `verifying`; nothing is enrollable before it is verified)."""
    try:
        from celery import chain  # noqa: PLC0415

        from app.workers import lead_tasks  # noqa: PLC0415

        chain(lead_tasks.verify_emails.s(batch_id=str(batch_id)),
              lead_tasks.finalize_lead_batch.s(batch_id=str(batch_id))).apply_async()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not queue verification for batch %s: %s", batch_id, exc)
        return False
