"""Celery worker for bulk import lead creation."""

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.base import SessionLocal
from app.db.models import (
    BulkImport,
    BulkImportRow,
    BulkImportStatus,
    BulkImportRowStatus,
    Lead,
    LeadStatus,
)


def process_bulk_import(job_id: str, db: Session | None = None) -> dict:
    """Process validated rows from a bulk import job and create leads.

    Fetches the job, iterates through validated rows, checks for duplicates,
    creates leads, and updates job/row status.

    Args:
        job_id: UUID string of the BulkImport job
        db: Optional database session (for testing); if None, creates new session

    Returns:
        dict with created/failed/total counts and any error message
    """
    should_close = False
    if db is None:
        db = SessionLocal()
        should_close = True

    try:
        # Fetch the job
        job_uuid = uuid.UUID(job_id)
        job = db.query(BulkImport).filter(BulkImport.id == job_uuid).first()

        if not job:
            return {"error": f"BulkImport job {job_id} not found"}

        # Fetch all validated rows for this job
        rows = (
            db.query(BulkImportRow)
            .filter(
                BulkImportRow.bulk_import_id == job.id,
                BulkImportRow.status == BulkImportRowStatus.VALIDATED,
            )
            .all()
        )

        created_count = 0
        skipped_count = 0
        failed_count = 0

        # Process each row
        for row in rows:
            try:
                email = row.raw_data_json.get("email", "").lower()

                # Check for duplicate email in strategy
                existing_lead = (
                    db.query(Lead)
                    .filter(
                        Lead.strategy_id == job.strategy_id,
                        Lead.email == email,
                    )
                    .first()
                )

                if existing_lead:
                    # Mark as skipped
                    row.status = BulkImportRowStatus.SKIPPED
                    row.error_message = f"Duplicate email {email} in strategy"
                    db.add(row)
                    skipped_count += 1
                    continue

                # Create new lead
                lead = Lead(
                    id=uuid.uuid4(),
                    strategy_id=job.strategy_id,
                    batch_id=None,
                    source="bulk_import",
                    external_id=None,
                    full_name=row.raw_data_json.get("client_name"),
                    title=row.raw_data_json.get("title"),
                    email=email,
                    phone=row.raw_data_json.get("phone"),
                    company=row.raw_data_json.get("company"),
                    status=LeadStatus.SOURCED,
                )
                db.add(lead)
                db.flush()  # Flush to get the lead ID

                # Mark row as imported
                row.status = BulkImportRowStatus.IMPORTED
                row.lead_id = lead.id
                db.add(row)
                created_count += 1

            except Exception as e:
                # Mark row as failed
                row.status = BulkImportRowStatus.FAILED
                row.error_message = str(e)
                db.add(row)
                failed_count += 1

        # Update job status
        job.status = BulkImportStatus.COMPLETED
        job.created_leads = created_count
        job.import_completed_at = datetime.now(timezone.utc)
        db.add(job)

        # Commit all changes
        db.commit()

        return {
            "created": created_count,
            "skipped": skipped_count,
            "failed": failed_count,
            "total": len(rows),
        }

    except Exception as e:
        db.rollback()
        return {"error": str(e)}
    finally:
        if should_close:
            db.close()
