"""Bulk import models and table schema verification."""

import uuid
from datetime import datetime

import pytest
import sqlalchemy as sa

from app.db import models as m  # noqa: F401
from app.db.base import Base


def test_bulk_import_status_enum_exists():
    """Verify BulkImportStatus enum exists with all required values."""
    assert hasattr(m, 'BulkImportStatus')
    assert hasattr(m.BulkImportStatus, 'PENDING')
    assert hasattr(m.BulkImportStatus, 'VALIDATING')
    assert hasattr(m.BulkImportStatus, 'READY_FOR_PREVIEW')
    assert hasattr(m.BulkImportStatus, 'IMPORTING')
    assert hasattr(m.BulkImportStatus, 'COMPLETED')
    assert hasattr(m.BulkImportStatus, 'FAILED')


def test_bulk_import_row_status_enum_exists():
    """Verify BulkImportRowStatus enum exists with all required values."""
    assert hasattr(m, 'BulkImportRowStatus')
    assert hasattr(m.BulkImportRowStatus, 'PENDING')
    assert hasattr(m.BulkImportRowStatus, 'VALIDATED')
    assert hasattr(m.BulkImportRowStatus, 'IMPORTED')
    assert hasattr(m.BulkImportRowStatus, 'FAILED')
    assert hasattr(m.BulkImportRowStatus, 'SKIPPED')


def test_bulk_import_table_exists():
    """Verify BulkImport table exists in models."""
    assert "bulk_imports" in Base.metadata.tables
    assert hasattr(m, 'BulkImport')


def test_bulk_import_table_schema():
    """Verify BulkImport has all required columns."""
    inspector = sa.inspect(Base.metadata.tables['bulk_imports'])
    columns = {c.name for c in inspector.columns}

    required = {
        'id', 'user_id', 'strategy_id', 'status', 'filename', 'total_rows',
        'valid_rows', 'failed_rows', 'created_leads', 'preview_json',
        'error_summary', 'validation_started_at', 'validation_completed_at',
        'import_started_at', 'import_completed_at', 'created_at', 'updated_at'
    }

    missing = required - columns
    assert not missing, f"Missing columns: {missing}. Have: {columns}"


def test_bulk_import_row_table_exists():
    """Verify BulkImportRow table exists in models."""
    assert "bulk_import_rows" in Base.metadata.tables
    assert hasattr(m, 'BulkImportRow')


def test_bulk_import_row_table_schema():
    """Verify BulkImportRow has all required columns."""
    inspector = sa.inspect(Base.metadata.tables['bulk_import_rows'])
    columns = {c.name for c in inspector.columns}

    required = {
        'id', 'bulk_import_id', 'lead_id', 'row_number',
        'raw_data_json', 'status', 'error_message', 'created_at', 'updated_at'
    }

    missing = required - columns
    assert not missing, f"Missing columns: {missing}. Have: {columns}"
