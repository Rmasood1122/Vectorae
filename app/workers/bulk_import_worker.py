"""Celery worker for bulk import lead creation."""

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
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
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(bind=True)
def process_bulk_import(self, job_id: str, db: Session | None = None) -> dict:
    """Process validated rows from a bulk import job and create leads.

    Fetches the job, iterates through validated rows, checks for duplicates,
    creates leads, and updates job/row status with comprehensive error tracking.

    Args:
        job_id: UUID string of the BulkImport job
        db: Optional database session (for testing); if None, creates new session

    Returns:
        dict with created/failed/total counts, error summary, and status
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
            logger.error(f"BulkImport job {job_id} not found")
            return {"error": f"BulkImport job {job_id} not found"}

        # Mark import as started
        job.status = BulkImportStatus.IMPORTING
        job.import_started_at = datetime.now(timezone.utc)
        db.add(job)
        db.flush()

        logger.info(f"Starting bulk import job {job_id} with strategy {job.strategy_id}")

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
        import_errors = []

        # Process each row
        for row in rows:
            try:
                email = row.raw_data_json.get("email", "").lower()
                if not email:
                    raise ValueError("Email is required")

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
                    logger.info(f"Row {row.row_number}: Skipped duplicate email {email}")
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
                logger.info(f"Row {row.row_number}: Created lead {email}")

            except IntegrityError as e:
                db.rollback()
                error_msg = f"Constraint violation: {str(e)[:100]}"
                row.status = BulkImportRowStatus.FAILED
                row.error_message = error_msg
                db.add(row)
                failed_count += 1
                import_errors.append({"row": row.row_number, "error": error_msg})
                logger.warning(f"Row {row.row_number}: {error_msg}")

            except ValueError as e:
                error_msg = str(e)
                row.status = BulkImportRowStatus.FAILED
                row.error_message = error_msg
                db.add(row)
                failed_count += 1
                import_errors.append({"row": row.row_number, "error": error_msg})
                logger.warning(f"Row {row.row_number}: Validation error: {error_msg}")

            except Exception as e:
                error_msg = f"Unexpected error: {str(e)[:100]}"
                row.status = BulkImportRowStatus.FAILED
                row.error_message = error_msg
                db.add(row)
                failed_count += 1
                import_errors.append({"row": row.row_number, "error": error_msg})
                logger.error(f"Row {row.row_number}: {error_msg}")

        # Determine final job status
        all_failed = created_count == 0 and skipped_count == 0 and failed_count == len(rows) and len(rows) > 0
        job.status = BulkImportStatus.FAILED if all_failed else BulkImportStatus.COMPLETED
        job.created_leads = created_count
        job.failed_rows = failed_count
        job.import_completed_at = datetime.now(timezone.utc)

        # Build error summary
        if import_errors:
            job.error_summary = {
                "import_errors": import_errors,
                "failed_rows_count": failed_count,
                "total_rows": len(rows),
            }

        db.add(job)
        db.commit()

        logger.info(
            f"Bulk import job {job_id} completed: "
            f"created={created_count}, skipped={skipped_count}, "
            f"failed={failed_count}, status={job.status.value}"
        )

        return {
            "created": created_count,
            "skipped": skipped_count,
            "failed": failed_count,
            "total": len(rows),
            "status": job.status.value,
        }

    except Exception as e:
        db.rollback()
        logger.exception(f"Critical error in bulk import job {job_id}: {str(e)}")
        return {"error": str(e)}
    finally:
        if should_close:
            db.close()
