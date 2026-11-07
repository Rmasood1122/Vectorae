"""Bulk import orchestration service."""

import io
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import (
    BulkImport,
    BulkImportRow,
    BulkImportStatus,
    BulkImportRowStatus,
)
from app.services.bulk_import_parser import (
    parse_csv_file,
    validate_all_rows,
    ParseError,
)


class BulkImportService:
    """Service for bulk import operations."""

    def __init__(self, db: Session):
        self.db = db

    def create_import_job(
        self,
        user_id: uuid.UUID,
        filename: str,
        csv_content: bytes,
        strategy_id: uuid.UUID | None = None,
    ) -> BulkImport:
        """Create and validate a bulk import job.

        Returns BulkImport with status READY_FOR_PREVIEW if valid,
        FAILED with error_summary if parsing/validation fails.
        """
        # Create job record
        job = BulkImport(
            id=uuid.uuid4(),
            user_id=user_id,
            strategy_id=strategy_id,
            filename=filename,
            status=BulkImportStatus.VALIDATING,
            validation_started_at=datetime.now(timezone.utc),
        )

        try:
            # Parse CSV
            csv_file = io.StringIO(csv_content.decode("utf-8"))
            result = parse_csv_file(csv_file)
            headers, rows = result.headers, result.rows

            job.total_rows = len(rows)

            # Validate all rows
            valid_rows, errors_by_row = validate_all_rows(rows)
            job.valid_rows = len(valid_rows)
            job.failed_rows = len(errors_by_row)

            # Create BulkImportRow records
            for i, row in enumerate(rows, start=1):
                error_row_numbers = {err[0] for err in errors_by_row}
                import_row = BulkImportRow(
                    id=uuid.uuid4(),
                    bulk_import_id=job.id,
                    row_number=i,
                    raw_data_json=row,
                    status=(
                        BulkImportRowStatus.VALIDATED
                        if i not in error_row_numbers
                        else BulkImportRowStatus.FAILED
                    ),
                )

                # Add error message if validation failed
                errors = next((e[1] for e in errors_by_row if e[0] == i), None)
                if errors:
                    import_row.error_message = "; ".join(errors)

                self.db.add(import_row)

            # Build preview
            preview = [
                {
                    "row_number": row_num,
                    "email": row.get("email", ""),
                    "client_name": row.get("client_name", ""),
                    "company": row.get("company", ""),
                    "status": (
                        "valid"
                        if row_num not in {e[0] for e in errors_by_row}
                        else "error"
                    ),
                    "error": "; ".join(
                        next((e[1] for e in errors_by_row if e[0] == row_num), [])
                    ),
                }
                for row_num, row in enumerate(rows, start=1)
            ]

            job.preview_json = preview
            job.status = BulkImportStatus.READY_FOR_PREVIEW
            job.validation_completed_at = datetime.now(timezone.utc)

            if job.failed_rows > 0:
                job.error_summary = {
                    "validation_errors": [e[1] for e in errors_by_row],
                }

        except ParseError as e:
            job.status = BulkImportStatus.FAILED
            job.error_summary = {"parse_error": str(e)}
            job.validation_completed_at = datetime.now(timezone.utc)
        except Exception as e:
            job.status = BulkImportStatus.FAILED
            job.error_summary = {"error": str(e)}
            job.validation_completed_at = datetime.now(timezone.utc)

        self.db.add(job)
        self.db.commit()
        self.db.refresh(job)

        return job

    def get_preview(self, job_id: uuid.UUID, user_id: uuid.UUID) -> dict[str, Any]:
        """Get preview for a bulk import job."""
        job = self.db.query(BulkImport).filter(
            BulkImport.id == job_id,
            BulkImport.user_id == user_id,
        ).first()

        if not job:
            raise ValueError("Import job not found")

        return {
            "import_id": str(job.id),
            "filename": job.filename,
            "status": job.status.value,
            "total_count": job.total_rows,
            "valid_count": job.valid_rows,
            "error_count": job.failed_rows,
            "preview": job.preview_json or [],
        }

    def confirm_import(self, job_id: uuid.UUID, user_id: uuid.UUID) -> BulkImport:
        """Mark import job as confirmed and ready to process."""
        job = self.db.query(BulkImport).filter(
            BulkImport.id == job_id,
            BulkImport.user_id == user_id,
        ).first()

        if not job:
            raise ValueError("Import job not found")

        if job.status != BulkImportStatus.READY_FOR_PREVIEW:
            raise ValueError("Can only confirm jobs that are ready for preview")

        job.status = BulkImportStatus.IMPORTING
        job.import_started_at = datetime.now(timezone.utc)

        self.db.commit()
        self.db.refresh(job)

        return job
