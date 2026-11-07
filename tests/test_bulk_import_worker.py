"""Tests for bulk import Celery worker."""

import uuid
from datetime import datetime, timezone

from app.db.models import (
    BulkImport,
    BulkImportRow,
    BulkImportStatus,
    BulkImportRowStatus,
    Lead,
)
from app.workers.bulk_import_worker import process_bulk_import


def test_bulk_import_worker_creates_leads(db_session, test_user, verified_strategy):
    """Verify worker creates leads from valid rows."""
    # Create bulk import job
    job = BulkImport(
        id=uuid.uuid4(),
        user_id=test_user.id,
        strategy_id=verified_strategy.id,
        filename="test.csv",
        status=BulkImportStatus.IMPORTING,
        total_rows=2,
        valid_rows=2,
        failed_rows=0,
    )
    db_session.add(job)
    db_session.flush()

    # Add two validated rows
    row1 = BulkImportRow(
        id=uuid.uuid4(),
        bulk_import_id=job.id,
        row_number=1,
        raw_data_json={
            "client_name": "John Doe",
            "email": "john@example.com",
            "company": "Acme Inc",
            "title": "CEO",
            "phone": "555-1234",
            "monthly_retainer": "5000",
            "payment_frequency": "monthly",
        },
        status=BulkImportRowStatus.VALIDATED,
    )
    row2 = BulkImportRow(
        id=uuid.uuid4(),
        bulk_import_id=job.id,
        row_number=2,
        raw_data_json={
            "client_name": "Jane Smith",
            "email": "jane@example.com",
            "company": "TechCorp",
            "title": "CTO",
            "phone": "555-5678",
            "monthly_retainer": "7000",
            "payment_frequency": "quarterly",
        },
        status=BulkImportRowStatus.VALIDATED,
    )
    db_session.add(row1)
    db_session.add(row2)
    db_session.commit()

    # Process the import
    result = process_bulk_import(str(job.id), db_session)

    # Verify job is marked completed
    db_session.refresh(job)
    assert job.status == BulkImportStatus.COMPLETED
    assert job.created_leads == 2
    assert job.failed_rows == 0
    assert job.import_started_at is not None
    assert job.import_completed_at is not None

    # Verify leads were created
    leads = (
        db_session.query(Lead)
        .filter(Lead.strategy_id == verified_strategy.id)
        .all()
    )
    assert len(leads) == 2
    assert leads[0].source == "bulk_import"
    assert leads[0].email == "john@example.com"
    assert leads[1].email == "jane@example.com"

    # Verify rows are marked imported
    db_session.refresh(row1)
    db_session.refresh(row2)
    assert row1.status == BulkImportRowStatus.IMPORTED
    assert row1.lead_id is not None
    assert row2.status == BulkImportRowStatus.IMPORTED
    assert row2.lead_id is not None

    # Return value
    assert result["created"] == 2
    assert result["status"] == "completed"


def test_bulk_import_worker_handles_duplicates(
    db_session, test_user, verified_strategy
):
    """Verify worker skips duplicate emails."""
    # Create an existing lead
    existing_lead = Lead(
        id=uuid.uuid4(),
        strategy_id=verified_strategy.id,
        batch_id=None,
        email="john@example.com",
        full_name="Existing John",
        source="manual",
        status="sourced",
    )
    db_session.add(existing_lead)
    db_session.flush()

    # Create bulk import with row that duplicates existing email
    job = BulkImport(
        id=uuid.uuid4(),
        user_id=test_user.id,
        strategy_id=verified_strategy.id,
        filename="test.csv",
        status=BulkImportStatus.IMPORTING,
        total_rows=1,
        valid_rows=1,
        failed_rows=0,
    )
    db_session.add(job)
    db_session.flush()

    row = BulkImportRow(
        id=uuid.uuid4(),
        bulk_import_id=job.id,
        row_number=1,
        raw_data_json={
            "client_name": "John Doe",
            "email": "john@example.com",
            "company": "Acme Inc",
            "title": "CEO",
        },
        status=BulkImportRowStatus.VALIDATED,
    )
    db_session.add(row)
    db_session.commit()

    # Process the import
    result = process_bulk_import(str(job.id), db_session)

    # Verify job is completed but no leads created
    db_session.refresh(job)
    assert job.status == BulkImportStatus.COMPLETED
    assert job.created_leads == 0

    # Verify row is marked skipped
    db_session.refresh(row)
    assert row.status == BulkImportRowStatus.SKIPPED
    assert "Duplicate" in row.error_message

    assert result["created"] == 0
    assert result["skipped"] == 1


def test_bulk_import_worker_handles_missing_email(
    db_session, test_user, verified_strategy
):
    """Verify worker fails rows with missing email."""
    job = BulkImport(
        id=uuid.uuid4(),
        user_id=test_user.id,
        strategy_id=verified_strategy.id,
        filename="test.csv",
        status=BulkImportStatus.IMPORTING,
        total_rows=1,
        valid_rows=1,
        failed_rows=0,
    )
    db_session.add(job)
    db_session.flush()

    row = BulkImportRow(
        id=uuid.uuid4(),
        bulk_import_id=job.id,
        row_number=1,
        raw_data_json={
            "client_name": "John Doe",
            "email": "",  # Missing email
            "company": "Acme Inc",
        },
        status=BulkImportRowStatus.VALIDATED,
    )
    db_session.add(row)
    db_session.commit()

    result = process_bulk_import(str(job.id), db_session)

    db_session.refresh(job)
    db_session.refresh(row)
    assert job.status == BulkImportStatus.FAILED  # All rows failed
    assert row.status == BulkImportRowStatus.FAILED
    assert "Email is required" in row.error_message
    assert result["failed"] == 1
    assert job.error_summary is not None
    assert "import_errors" in job.error_summary


def test_bulk_import_worker_job_not_found(db_session):
    """Verify worker handles missing job gracefully."""
    fake_id = str(uuid.uuid4())
    result = process_bulk_import(fake_id)
    assert "error" in result
