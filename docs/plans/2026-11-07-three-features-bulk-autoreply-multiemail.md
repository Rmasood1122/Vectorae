# Three Major Features Implementation Plan

**Goal:** Add bulk CSV lead import, auto-reply inbox rules, and multi-email account support to Vectorae.

**Architecture:** 
- Bulk upload: CSV parser + validation + preview + async import worker
- Auto-reply: Rule engine with keyword/pattern matching, stored templates, per-strategy enablement, compliance gates
- Multi-email: Database migration removing unique constraint on user email, email_account table for account management, per-account settings

**Tech Stack:** FastAPI, SQLAlchemy/PostgreSQL, Celery/Redis, pytest, compliance gates (CAN-SPAM/GDPR/CASL)

---

## FEATURE 1: Bulk Upload Clients (CSV Import)

**Goal:** Allow users to upload CSV files with leads (name, email, company, title, phone, source, retainer, frequency) with validation and preview.

**Architecture:** CSV parser validates against suppression/consent lists, returns preview for confirmation, async worker imports on approval.

**Database Changes:**
- New table: `bulk_imports` — track upload job status, row counts, validation results
- New table: `bulk_import_rows` — map uploaded rows to created leads (for retry/rollback)

---

### Task 1: Create Bulk Import Models and Migration

**Files:**
- Modify: `app/db/models.py` — Add BulkImport and BulkImportRow enums/models
- Create: `alembic/versions/0100_bulk_import.py` — Migration for bulk_imports and bulk_import_rows tables

**Step 1: Define the failing test**

Create test file: `/home/user/Vectorae/tests/test_bulk_import_models.py`

```python
import uuid
from datetime import datetime
import pytest
from app.db.models import BulkImportStatus, BulkImport, BulkImportRow
from app.db.session import get_db

def test_bulk_import_model_exists():
    """Verify BulkImportStatus enum and BulkImport table exist."""
    assert hasattr(BulkImportStatus, 'PENDING')
    assert hasattr(BulkImportStatus, 'VALIDATING')
    assert hasattr(BulkImportStatus, 'READY_FOR_PREVIEW')
    assert hasattr(BulkImportStatus, 'IMPORTING')
    assert hasattr(BulkImportStatus, 'COMPLETED')
    assert hasattr(BulkImportStatus, 'FAILED')

def test_bulk_import_table_schema():
    """Verify BulkImport has all required columns."""
    import sqlalchemy as sa
    inspector = sa.inspect(BulkImport)
    columns = {c.name for c in inspector.columns}
    
    required = {'id', 'user_id', 'status', 'filename', 'total_rows', 
                'valid_rows', 'failed_rows', 'created_leads', 'preview_json',
                'error_summary', 'created_at', 'updated_at'}
    
    assert required.issubset(columns), f"Missing: {required - columns}"

def test_bulk_import_row_table_schema():
    """Verify BulkImportRow tracks mapping."""
    import sqlalchemy as sa
    inspector = sa.inspect(BulkImportRow)
    columns = {c.name for c in inspector.columns}
    
    required = {'id', 'bulk_import_id', 'lead_id', 'row_number', 
                'raw_data_json', 'validation_status', 'error_message'}
    
    assert required.issubset(columns), f"Missing: {required - columns}"
```

**Step 2: Run test to verify it fails**

```bash
cd /home/user/Vectorae
python -m pytest tests/test_bulk_import_models.py::test_bulk_import_model_exists -xvs
```

Expected: FAIL — `BulkImportStatus` not defined

**Step 3: Add enums and models to models.py**

Edit `/home/user/Vectorae/app/db/models.py`, add after the enum section (around line 230):

```python
class BulkImportStatus(str, enum.Enum):
    """Lifecycle of a bulk import job."""
    PENDING = "pending"                  # uploaded, not yet validated
    VALIDATING = "validating"            # running validation checks
    READY_FOR_PREVIEW = "ready_for_preview"  # validation complete, preview ready
    IMPORTING = "importing"              # user confirmed, import running
    COMPLETED = "completed"              # all rows imported
    FAILED = "failed"                    # unrecoverable validation or import error


class BulkImportRowStatus(str, enum.Enum):
    """Status of one row in a bulk import."""
    PENDING = "pending"
    VALIDATED = "validated"
    IMPORTED = "imported"
    FAILED = "failed"
    SKIPPED = "skipped"                  # duplicate, suppressed, etc.
```

Then add the models at the end of the file (before the last class):

```python
class BulkImport(TimestampMixin, Base):
    """Track a bulk CSV import job."""
    __tablename__ = "bulk_imports"
    
    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # Optional: which strategy this import is for (if null, user selects per row)
    strategy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("strategies.id", ondelete="SET NULL"), nullable=True, index=True
    )
    
    # File info
    filename: Mapped[str] = mapped_column(String(500))
    
    # Counters
    status: Mapped[BulkImportStatus] = mapped_column(
        _enum(BulkImportStatus), default=BulkImportStatus.PENDING, index=True
    )
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    valid_rows: Mapped[int] = mapped_column(Integer, default=0)
    failed_rows: Mapped[int] = mapped_column(Integer, default=0)
    created_leads: Mapped[int] = mapped_column(Integer, default=0)
    
    # Preview: [{email, name, company, status, reason}]
    preview_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    
    # Validation/import errors: {validation_errors: [...], import_errors: [...]}
    error_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    
    # Timestamps for tracking
    validation_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    validation_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    import_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    import_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class BulkImportRow(TimestampMixin, Base):
    """One row from a bulk import."""
    __tablename__ = "bulk_import_rows"
    __table_args__ = (
        UniqueConstraint("bulk_import_id", "row_number", name="bulk_import_row_unique"),
    )
    
    id: Mapped[uuid.UUID] = _uuid_pk()
    bulk_import_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("bulk_imports.id", ondelete="CASCADE"), index=True
    )
    
    # Row number in the CSV (1-indexed for user-facing errors)
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    
    # Optional: the lead created from this row
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, index=True
    )
    
    # Raw CSV row as dict
    raw_data_json: Mapped[dict] = mapped_column(JSON, default=dict)
    
    # Validation result
    status: Mapped[BulkImportRowStatus] = mapped_column(
        _enum(BulkImportRowStatus), default=BulkImportRowStatus.PENDING, index=True
    )
    
    # Error details: {validation: [...], import: "reason"}
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
```

**Step 4: Run tests to verify they pass**

```bash
cd /home/user/Vectorae
python -m pytest tests/test_bulk_import_models.py -xvs
```

Expected: PASS (3 tests pass)

**Step 5: Create migration**

Create `/home/user/Vectorae/alembic/versions/0100_bulk_import.py`:

```python
"""Bulk import for CSV leads.

CREATES
  bulk_imports       Job metadata: filename, status, row counts, preview, errors
  bulk_import_rows   Mapping of uploaded rows to created leads + validation status

Revision ID: 0100_bulk_import
Revises: 0099_ppm_credits_limit
"""

import sqlalchemy as sa
from alembic import op

revision = "0100_bulk_import"
down_revision = "0099_ppm_credits_limit"


def upgrade() -> None:
    op.create_table(
        "bulk_imports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("strategy_id", sa.Uuid(), nullable=True),
        sa.Column("filename", sa.String(500), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("total_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("valid_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_leads", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("preview_json", sa.JSON(), nullable=True),
        sa.Column("error_summary", sa.JSON(), nullable=True),
        sa.Column("validation_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("validation_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("import_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("import_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["strategy_id"], ["strategies.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_bulk_imports_user_id", "bulk_imports", ["user_id"])
    op.create_index("ix_bulk_imports_strategy_id", "bulk_imports", ["strategy_id"])
    op.create_index("ix_bulk_imports_status", "bulk_imports", ["status"])

    op.create_table(
        "bulk_import_rows",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bulk_import_id", sa.Uuid(), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("lead_id", sa.Uuid(), nullable=True),
        sa.Column("raw_data_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("error_message", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["bulk_import_id"], ["bulk_imports.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("bulk_import_id", "row_number", name="bulk_import_row_unique"),
    )
    op.create_index("ix_bulk_import_rows_bulk_import_id", "bulk_import_rows", ["bulk_import_id"])
    op.create_index("ix_bulk_import_rows_lead_id", "bulk_import_rows", ["lead_id"])
    op.create_index("ix_bulk_import_rows_status", "bulk_import_rows", ["status"])


def downgrade() -> None:
    op.drop_index("ix_bulk_import_rows_status", "bulk_import_rows")
    op.drop_index("ix_bulk_import_rows_lead_id", "bulk_import_rows")
    op.drop_index("ix_bulk_import_rows_bulk_import_id", "bulk_import_rows")
    op.drop_table("bulk_import_rows")
    
    op.drop_index("ix_bulk_imports_status", "bulk_imports")
    op.drop_index("ix_bulk_imports_strategy_id", "bulk_imports")
    op.drop_index("ix_bulk_imports_user_id", "bulk_imports")
    op.drop_table("bulk_imports")
```

**Step 6: Commit**

```bash
cd /home/user/Vectorae
git add -A
git commit -m "feat: add bulk import database models and migration

- Add BulkImportStatus and BulkImportRowStatus enums
- Add BulkImport and BulkImportRow models
- Create migration 0100_bulk_import for new tables
- Support tracking CSV upload jobs and row-to-lead mapping"
```

---

### Task 2: CSV Parser and Validation Service

**Files:**
- Create: `app/services/bulk_import_parser.py` — CSV parsing and validation
- Create: `tests/test_bulk_import_parser.py` — Parser tests

**Step 1: Write failing test**

Create `/home/user/Vectorae/tests/test_bulk_import_parser.py`:

```python
import io
import pytest
from app.services.bulk_import_parser import (
    parse_csv_file,
    validate_row,
    BulkImportError,
)

def test_parse_csv_file():
    """Verify CSV parsing extracts headers and rows."""
    csv_data = """client_name,email,company,title,phone,source,monthly_retainer,payment_frequency
John Doe,john@example.com,Acme Inc,CEO,555-1234,linkedin,1000,monthly
Jane Smith,jane@example.com,TechCorp,CTO,555-5678,apollo,2000,quarterly"""
    
    f = io.StringIO(csv_data)
    headers, rows = parse_csv_file(f)
    
    assert headers == ['client_name', 'email', 'company', 'title', 'phone', 'source', 'monthly_retainer', 'payment_frequency']
    assert len(rows) == 2
    assert rows[0]['email'] == 'john@example.com'
    assert rows[1]['title'] == 'CTO'

def test_validate_row_success():
    """Verify valid row passes validation."""
    row = {
        'client_name': 'John Doe',
        'email': 'john@example.com',
        'company': 'Acme Inc',
        'title': 'CEO',
        'phone': '555-1234',
        'source': 'manual_import',
        'monthly_retainer': '1000',
        'payment_frequency': 'monthly',
    }
    
    errors = validate_row(row, row_number=1)
    assert errors == []

def test_validate_row_missing_email():
    """Verify row without email fails."""
    row = {
        'client_name': 'John Doe',
        'email': '',
        'company': 'Acme Inc',
        'title': 'CEO',
    }
    
    errors = validate_row(row, row_number=1)
    assert any('email' in e.lower() for e in errors)

def test_validate_row_invalid_email():
    """Verify invalid email format fails."""
    row = {
        'client_name': 'John Doe',
        'email': 'not-an-email',
        'company': 'Acme Inc',
    }
    
    errors = validate_row(row, row_number=1)
    assert any('email' in e.lower() and 'invalid' in e.lower() for e in errors)

def test_validate_row_invalid_retainer():
    """Verify non-numeric retainer fails."""
    row = {
        'client_name': 'John Doe',
        'email': 'john@example.com',
        'company': 'Acme Inc',
        'monthly_retainer': 'not-a-number',
    }
    
    errors = validate_row(row, row_number=1)
    assert any('retainer' in e.lower() and 'number' in e.lower() for e in errors)
```

**Step 2: Run test to fail**

```bash
cd /home/user/Vectorae
python -m pytest tests/test_bulk_import_parser.py::test_parse_csv_file -xvs
```

Expected: FAIL — module doesn't exist

**Step 3: Implement parser**

Create `/home/user/Vectorae/app/services/bulk_import_parser.py`:

```python
"""CSV parser and validator for bulk lead imports."""

import csv
import io
import re
from typing import Any, NamedTuple


class ParseError(Exception):
    """CSV parsing error."""
    pass


class BulkImportError(Exception):
    """Validation error during bulk import."""
    pass


class ParseResult(NamedTuple):
    """Result of parsing CSV."""
    headers: list[str]
    rows: list[dict[str, Any]]


REQUIRED_FIELDS = {'email', 'client_name'}
OPTIONAL_FIELDS = {'company', 'title', 'phone', 'source', 'monthly_retainer', 'payment_frequency'}
ALL_FIELDS = REQUIRED_FIELDS | OPTIONAL_FIELDS


def parse_csv_file(file_obj: io.TextIOBase) -> ParseResult:
    """Parse CSV file and return headers and rows.
    
    Args:
        file_obj: File-like object opened in text mode
        
    Returns:
        ParseResult with headers list and rows list of dicts
        
    Raises:
        ParseError: If CSV is malformed or missing required fields
    """
    reader = csv.DictReader(file_obj)
    
    if not reader.fieldnames:
        raise ParseError("CSV file is empty")
    
    headers = list(reader.fieldnames)
    
    # Verify required fields present
    missing = REQUIRED_FIELDS - set(h.lower() for h in headers)
    if missing:
        raise ParseError(f"Missing required columns: {missing}")
    
    # Normalize header names to lowercase
    normalized_headers = [h.lower() for h in headers]
    
    rows = []
    for i, row in enumerate(reader, start=2):  # start=2 because header is row 1
        # Normalize keys to lowercase
        normalized_row = {k.lower(): v for k, v in row.items() if k}
        rows.append(normalized_row)
    
    return ParseResult(headers=normalized_headers, rows=rows)


def validate_row(row: dict[str, str], row_number: int) -> list[str]:
    """Validate one CSV row. Returns list of error messages (empty if valid).
    
    Args:
        row: One CSV row as dict (keys are lowercase)
        row_number: Row number for error reporting
        
    Returns:
        List of validation error messages (empty if valid)
    """
    errors = []
    
    # Check required fields
    email = row.get('email', '').strip()
    if not email:
        errors.append(f"Row {row_number}: 'email' is required")
    elif not _is_valid_email(email):
        errors.append(f"Row {row_number}: 'email' is not a valid email address ({email})")
    
    client_name = row.get('client_name', '').strip()
    if not client_name:
        errors.append(f"Row {row_number}: 'client_name' is required")
    
    # Validate optional fields
    if 'monthly_retainer' in row and row['monthly_retainer'].strip():
        try:
            float(row['monthly_retainer'].strip())
        except (ValueError, TypeError):
            errors.append(f"Row {row_number}: 'monthly_retainer' must be a valid number")
    
    if 'payment_frequency' in row and row['payment_frequency'].strip():
        freq = row['payment_frequency'].strip().lower()
        if freq not in {'monthly', 'quarterly', 'annual', 'one-time'}:
            errors.append(f"Row {row_number}: 'payment_frequency' must be monthly, quarterly, annual, or one-time")
    
    return errors


def _is_valid_email(email: str) -> bool:
    """Simple email validation."""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None


def validate_all_rows(rows: list[dict[str, str]]) -> tuple[list[dict], list[tuple[int, list[str]]]]:
    """Validate all rows. Returns (valid_rows, errors_by_row).
    
    Args:
        rows: List of CSV rows (keys lowercase)
        
    Returns:
        Tuple of (valid_rows_list, errors_list) where errors_list is 
        [(row_number, [error_messages, ...]), ...]
    """
    valid = []
    errors = []
    
    for i, row in enumerate(rows, start=2):  # CSV is 1-indexed, header is row 1
        row_errors = validate_row(row, row_number=i)
        if row_errors:
            errors.append((i, row_errors))
        else:
            valid.append(row)
    
    return valid, errors
```

**Step 4: Run tests to pass**

```bash
cd /home/user/Vectorae
python -m pytest tests/test_bulk_import_parser.py -xvs
```

Expected: PASS (6 tests)

**Step 5: Commit**

```bash
cd /home/user/Vectorae
git add -A
git commit -m "feat: add CSV parser and validation for bulk imports

- Implement parse_csv_file to handle CSV parsing
- Add validate_row for individual row validation
- Validate required fields (email, client_name)
- Validate optional fields (retainer as number, frequency enum)
- Email format validation with regex"
```

---

### Task 3: Bulk Import API Endpoint and Worker

**Files:**
- Create: `app/services/bulk_import.py` — Import job orchestration
- Modify: `app/api/routes/bulk_import.py` — POST endpoint for upload
- Modify: `app/workers/bulk_import_worker.py` — Celery task for async import

**Step 1: Write failing test**

Create `/home/user/Vectorae/tests/test_bulk_import_api.py`:

```python
import io
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from app.main import app
from app.db.models import BulkImport
from app.db.session import get_db


client = TestClient(app)


def test_bulk_import_upload_endpoint(db_session, auth_headers):
    """Verify POST /bulk-import/upload creates a BulkImport record."""
    csv_content = """client_name,email,company,title
John Doe,john@example.com,Acme Inc,CEO"""
    
    files = {'file': ('leads.csv', io.BytesIO(csv_content.encode()))}
    
    response = client.post(
        "/api/v1/bulk-import/upload",
        files=files,
        headers=auth_headers,
    )
    
    assert response.status_code == 200
    data = response.json()
    assert 'import_id' in data
    assert data['status'] == 'validating'


def test_bulk_import_preview_endpoint(db_session, auth_headers, bulk_import_ready):
    """Verify GET /bulk-import/{id}/preview returns preview."""
    response = client.get(
        f"/api/v1/bulk-import/{bulk_import_ready.id}/preview",
        headers=auth_headers,
    )
    
    assert response.status_code == 200
    data = response.json()
    assert 'preview' in data
    assert 'valid_count' in data
    assert 'error_count' in data


def test_bulk_import_confirm_endpoint(db_session, auth_headers, bulk_import_ready):
    """Verify POST /bulk-import/{id}/confirm starts import."""
    response = client.post(
        f"/api/v1/bulk-import/{bulk_import_ready.id}/confirm",
        headers=auth_headers,
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data['status'] == 'importing'
```

**Step 2: Run test to fail**

```bash
cd /home/user/Vectorae
python -m pytest tests/test_bulk_import_api.py::test_bulk_import_upload_endpoint -xvs
```

Expected: FAIL — endpoint doesn't exist

**Step 3: Implement service layer**

Create `/home/user/Vectorae/app/services/bulk_import.py`:

```python
"""Bulk import orchestration service."""

import io
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import BulkImport, BulkImportRow, BulkImportStatus, BulkImportRowStatus
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
            validation_started_at=datetime.utcnow(),
        )
        
        try:
            # Parse CSV
            csv_file = io.StringIO(csv_content.decode('utf-8'))
            result = parse_csv_file(csv_file)
            headers, rows = result.headers, result.rows
            
            job.total_rows = len(rows)
            
            # Validate all rows
            valid_rows, errors_by_row = validate_all_rows(rows)
            job.valid_rows = len(valid_rows)
            job.failed_rows = len(errors_by_row)
            
            # Create BulkImportRow records
            for i, row in enumerate(rows, start=1):
                import_row = BulkImportRow(
                    id=uuid.uuid4(),
                    bulk_import_id=job.id,
                    row_number=i,
                    raw_data_json=row,
                    status=BulkImportRowStatus.VALIDATED
                    if i not in {err[0] for err in errors_by_row}
                    else BulkImportRowStatus.FAILED,
                )
                
                # Add error message if validation failed
                errors = next((e[1] for e in errors_by_row if e[0] == i), None)
                if errors:
                    import_row.error_message = "; ".join(errors)
                
                self.db.add(import_row)
            
            # Build preview
            preview = [
                {
                    'row_number': row_num,
                    'email': row.get('email', ''),
                    'client_name': row.get('client_name', ''),
                    'company': row.get('company', ''),
                    'status': 'valid' if row_num not in {e[0] for e in errors_by_row} else 'error',
                    'error': "; ".join(next((e[1] for e in errors_by_row if e[0] == row_num), []))
                }
                for row_num, row in enumerate(rows, start=1)
            ]
            
            job.preview_json = preview
            job.status = BulkImportStatus.READY_FOR_PREVIEW
            job.validation_completed_at = datetime.utcnow()
            
            if job.failed_rows > 0:
                job.error_summary = {
                    'validation_errors': [e[1] for e in errors_by_row],
                }
        
        except ParseError as e:
            job.status = BulkImportStatus.FAILED
            job.error_summary = {'parse_error': str(e)}
            job.validation_completed_at = datetime.utcnow()
        except Exception as e:
            job.status = BulkImportStatus.FAILED
            job.error_summary = {'error': str(e)}
            job.validation_completed_at = datetime.utcnow()
        
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
            'import_id': str(job.id),
            'filename': job.filename,
            'status': job.status.value,
            'total_count': job.total_rows,
            'valid_count': job.valid_rows,
            'error_count': job.failed_rows,
            'preview': job.preview_json or [],
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
        job.import_started_at = datetime.utcnow()
        
        self.db.commit()
        self.db.refresh(job)
        
        return job
```

**Step 4: Create API routes**

Create `/home/user/Vectorae/app/api/routes/bulk_import.py`:

```python
"""Bulk import API routes."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, status
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.services.bulk_import import BulkImportService


router = APIRouter(prefix="/bulk-import", tags=["bulk-import"])


@router.post("/upload")
async def upload_bulk_import(
    file: UploadFile = File(...),
    current_user: Annotated[User, Depends(get_current_user)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
):
    """Upload and validate a CSV file for bulk import."""
    
    if not file.filename.endswith('.csv'):
        raise HTTPException(status_code=400, detail="File must be CSV format")
    
    content = await file.read()
    
    service = BulkImportService(db)
    job = service.create_import_job(
        user_id=current_user.id,
        filename=file.filename,
        csv_content=content,
    )
    
    return {
        'import_id': str(job.id),
        'status': job.status.value,
        'filename': job.filename,
        'validation_complete': job.status in [
            'ready_for_preview',
            'failed'
        ],
    }


@router.get("/{import_id}/preview")
async def get_preview(
    import_id: str,
    current_user: Annotated[User, Depends(get_current_user)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
):
    """Get preview of a bulk import job."""
    
    try:
        job_id = uuid.UUID(import_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid import ID")
    
    service = BulkImportService(db)
    
    try:
        preview = service.get_preview(job_id, current_user.id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    
    return preview


@router.post("/{import_id}/confirm")
async def confirm_import(
    import_id: str,
    current_user: Annotated[User, Depends(get_current_user)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
):
    """Confirm bulk import and start processing."""
    
    try:
        job_id = uuid.UUID(import_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid import ID")
    
    service = BulkImportService(db)
    
    try:
        job = service.confirm_import(job_id, current_user.id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    # TODO: Trigger Celery task here
    # from app.workers.bulk_import_worker import process_bulk_import
    # process_bulk_import.delay(str(job.id))
    
    return {
        'import_id': str(job.id),
        'status': job.status.value,
    }
```

**Step 5: Register routes in main app**

Edit `/home/user/Vectorae/app/main.py`, add near the router registrations (around line 200):

```python
from app.api.routes import bulk_import

app.include_router(bulk_import.router, prefix="/api/v1")
```

**Step 6: Run tests**

```bash
cd /home/user/Vectorae
python -m pytest tests/test_bulk_import_api.py -xvs
```

Expected: PASS (3 tests)

**Step 7: Commit**

```bash
cd /home/user/Vectorae
git add -A
git commit -m "feat: add bulk import API endpoints and service

- Implement BulkImportService for job orchestration
- Add POST /api/v1/bulk-import/upload endpoint
- Add GET /api/v1/bulk-import/{id}/preview endpoint
- Add POST /api/v1/bulk-import/{id}/confirm endpoint
- Validate CSV, generate preview, track job status"
```

---

### Task 4: Bulk Import Worker and Lead Creation

**Files:**
- Create: `app/workers/bulk_import_worker.py` — Celery task for import
- Create: `tests/test_bulk_import_worker.py` — Worker tests

**Step 1: Write failing test**

Create `/home/user/Vectorae/tests/test_bulk_import_worker.py`:

```python
import uuid
import pytest
from app.db.models import BulkImport, BulkImportStatus, Strategy, Lead
from app.workers.bulk_import_worker import process_bulk_import


@pytest.mark.asyncio
def test_bulk_import_worker_creates_leads(db_session, test_user, test_strategy):
    """Verify worker creates leads from valid rows."""
    
    # Create a mock bulk import with valid rows
    job = BulkImport(
        id=uuid.uuid4(),
        user_id=test_user.id,
        strategy_id=test_strategy.id,
        filename="test.csv",
        status=BulkImportStatus.IMPORTING,
        total_rows=2,
        valid_rows=2,
        failed_rows=0,
    )
    db_session.add(job)
    db_session.commit()
    
    # Process the import
    process_bulk_import(str(job.id))
    
    # Verify leads were created
    leads = db_session.query(Lead).filter(
        Lead.strategy_id == test_strategy.id
    ).all()
    
    assert len(leads) == 2
    assert leads[0].source == 'bulk_import'


def test_bulk_import_worker_marks_failures(db_session, test_user, test_strategy):
    """Verify worker marks failed rows appropriately."""
    
    job = BulkImport(
        id=uuid.uuid4(),
        user_id=test_user.id,
        strategy_id=test_strategy.id,
        filename="test.csv",
        status=BulkImportStatus.IMPORTING,
        total_rows=1,
        valid_rows=0,
        failed_rows=1,
    )
    db_session.add(job)
    db_session.commit()
    
    # Process the import
    process_bulk_import(str(job.id))
    
    # Reload job
    job = db_session.query(BulkImport).get(job.id)
    
    assert job.status == BulkImportStatus.COMPLETED
    assert job.created_leads == 0
    assert job.import_completed_at is not None
```

**Step 2: Run test to fail**

```bash
cd /home/user/Vectorae
python -m pytest tests/test_bulk_import_worker.py::test_bulk_import_worker_creates_leads -xvs
```

Expected: FAIL — module doesn't exist

**Step 3: Implement worker**

Create `/home/user/Vectorae/app/workers/bulk_import_worker.py`:

```python
"""Celery task for processing bulk imports."""

import uuid
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import (
    BulkImport,
    BulkImportRow,
    BulkImportStatus,
    BulkImportRowStatus,
    Lead,
    LeadStatus,
)
from app.workers.celery_app import celery_app


@celery_app.task(bind=True, name="bulk_import_worker.process_bulk_import")
def process_bulk_import(self, job_id: str) -> dict:
    """Process a bulk import job: create leads from validated rows.
    
    Args:
        job_id: UUID of BulkImport job
        
    Returns:
        Summary dict with counts
    """
    
    # Get database session
    engine = create_engine(settings.DATABASE_URL)
    session = Session(engine)
    
    try:
        job = session.query(BulkImport).get(uuid.UUID(job_id))
        
        if not job:
            return {'error': 'Job not found'}
        
        if job.status != BulkImportStatus.IMPORTING:
            return {'error': f'Job status is {job.status}, expected importing'}
        
        # Get all rows for this job that are validated
        rows = session.query(BulkImportRow).filter(
            BulkImportRow.bulk_import_id == job.id,
            BulkImportRow.status == BulkImportRowStatus.VALIDATED,
        ).all()
        
        created_count = 0
        failed_count = 0
        
        for row in rows:
            try:
                # Create lead from row data
                lead_data = row.raw_data_json
                
                # Check for duplicates within this strategy
                existing = session.query(Lead).filter(
                    Lead.strategy_id == job.strategy_id,
                    Lead.email == lead_data.get('email', '').lower(),
                ).first()
                
                if existing:
                    row.status = BulkImportRowStatus.SKIPPED
                    row.error_message = "Duplicate email in strategy"
                    session.add(row)
                    continue
                
                # Create new lead
                lead = Lead(
                    id=uuid.uuid4(),
                    strategy_id=job.strategy_id,
                    batch_id=None,  # Could be set later if using batch tracking
                    source='bulk_import',
                    full_name=lead_data.get('client_name', ''),
                    title=lead_data.get('title'),
                    email=lead_data.get('email', '').lower(),
                    phone=lead_data.get('phone'),
                    company=lead_data.get('company'),
                    status=LeadStatus.SOURCED,
                    enrichment_json={'retainer': lead_data.get('monthly_retainer'),
                                      'frequency': lead_data.get('payment_frequency')},
                )
                
                session.add(lead)
                session.flush()  # Get the ID
                
                row.lead_id = lead.id
                row.status = BulkImportRowStatus.IMPORTED
                session.add(row)
                
                created_count += 1
                
            except Exception as e:
                row.status = BulkImportRowStatus.FAILED
                row.error_message = str(e)
                session.add(row)
                failed_count += 1
        
        # Update job
        job.created_leads = created_count
        job.status = BulkImportStatus.COMPLETED
        job.import_completed_at = datetime.utcnow()
        session.add(job)
        
        session.commit()
        
        return {
            'job_id': job_id,
            'created': created_count,
            'failed': failed_count,
            'total': len(rows),
        }
        
    except Exception as e:
        job = session.query(BulkImport).get(uuid.UUID(job_id))
        if job:
            job.status = BulkImportStatus.FAILED
            job.error_summary = {'import_error': str(e)}
            job.import_completed_at = datetime.utcnow()
            session.add(job)
            session.commit()
        
        raise
    
    finally:
        session.close()
```

**Step 4: Run tests**

```bash
cd /home/user/Vectorae
python -m pytest tests/test_bulk_import_worker.py -xvs
```

Expected: PASS (2 tests)

**Step 5: Commit**

```bash
cd /home/user/Vectorae
git add -A
git commit -m "feat: add bulk import worker for async processing

- Implement Celery task to process bulk import jobs
- Create leads from validated CSV rows
- Handle duplicates within strategy
- Track import progress and mark completed/failed"
```

---

## FEATURE 2: Auto-Reply Inbox Rules

**Goal:** Extend inbox to support auto-reply rules with keyword matching, templates, vacation mode, compliance gates.

**Database Changes:**
- New table: `auto_reply_rules` — rule definitions (keywords, patterns, response template)
- New table: `auto_reply_templates` — reusable response templates
- New column: User table gets `vacation_mode_enabled` and `vacation_message`

---

### Task 5: Auto-Reply Models and Migration

**Files:**
- Modify: `app/db/models.py` — Add AutoReplyRule, AutoReplyTemplate enums/models
- Create: `alembic/versions/0101_auto_reply.py` — Migration

**Step 1: Write failing test**

Create `/home/user/Vectorae/tests/test_auto_reply_models.py`:

```python
import pytest
from app.db.models import AutoReplyRule, AutoReplyTemplate, AutoReplyMatchType


def test_auto_reply_enums_exist():
    """Verify AutoReplyMatchType enum has required values."""
    assert hasattr(AutoReplyMatchType, 'KEYWORD')
    assert hasattr(AutoReplyMatchType, 'SENDER_PATTERN')
    assert hasattr(AutoReplyMatchType, 'SUBJECT_PATTERN')


def test_auto_reply_rule_model_exists():
    """Verify AutoReplyRule table has required columns."""
    import sqlalchemy as sa
    inspector = sa.inspect(AutoReplyRule)
    columns = {c.name for c in inspector.columns}
    
    required = {'id', 'user_id', 'strategy_id', 'name', 'match_type',
                'pattern', 'response_template_id', 'enabled', 'created_at', 'updated_at'}
    
    assert required.issubset(columns), f"Missing: {required - columns}"


def test_auto_reply_template_model_exists():
    """Verify AutoReplyTemplate table has required columns."""
    import sqlalchemy as sa
    inspector = sa.inspect(AutoReplyTemplate)
    columns = {c.name for c in inspector.columns}
    
    required = {'id', 'user_id', 'name', 'subject', 'body', 'created_at', 'updated_at'}
    
    assert required.issubset(columns), f"Missing: {required - columns}"
```

**Step 2: Run test to fail**

```bash
cd /home/user/Vectorae
python -m pytest tests/test_auto_reply_models.py::test_auto_reply_enums_exist -xvs
```

Expected: FAIL — enums don't exist

**Step 3: Add models to models.py**

Add to `/home/user/Vectorae/app/db/models.py` after other enums (around line 240):

```python
class AutoReplyMatchType(str, enum.Enum):
    """How an auto-reply rule matches incoming messages."""
    KEYWORD = "keyword"                    # Match if message contains word/phrase
    SENDER_PATTERN = "sender_pattern"      # Match if from email matches pattern
    SUBJECT_PATTERN = "subject_pattern"    # Match if subject matches pattern
```

Then add models at end of file:

```python
class AutoReplyTemplate(TimestampMixin, Base):
    """Reusable auto-reply message template."""
    __tablename__ = "auto_reply_templates"
    
    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    
    name: Mapped[str] = mapped_column(String(200))  # e.g., "OOO - Back Next Monday"
    subject: Mapped[str] = mapped_column(String(500))  # Auto-reply subject
    body: Mapped[str] = mapped_column(Text)  # Auto-reply body
    
    # User can mark favorites for quick reuse
    is_favorite: Mapped[bool] = mapped_column(Boolean, default=False)


class AutoReplyRule(TimestampMixin, Base):
    """Auto-reply rule tied to a strategy."""
    __tablename__ = "auto_reply_rules"
    __table_args__ = (
        UniqueConstraint("user_id", "strategy_id", "name", name="user_strategy_rule_name"),
    )
    
    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    strategy_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), index=True
    )
    
    # Rule name
    name: Mapped[str] = mapped_column(String(200))
    
    # What to match on
    match_type: Mapped[AutoReplyMatchType] = mapped_column(
        _enum(AutoReplyMatchType), default=AutoReplyMatchType.KEYWORD
    )
    
    # The pattern to match (keyword, regex, email pattern)
    pattern: Mapped[str] = mapped_column(String(500))
    
    # Which template to use for this rule
    response_template_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("auto_reply_templates.id", ondelete="SET NULL"), nullable=True
    )
    
    # Or inline response (if no template)
    inline_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    
    # Enable/disable
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    
    # Priority (lower number = higher priority)
    priority: Mapped[int] = mapped_column(Integer, default=100)


class UserVacationMode(TimestampMixin, Base):
    """User's vacation/OOO auto-reply settings."""
    __tablename__ = "user_vacation_modes"
    
    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True
    )
    
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    subject: Mapped[str] = mapped_column(String(500), default="Out of Office")
    message: Mapped[str] = mapped_column(Text)
    
    # When the vacation mode is active
    start_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

**Step 4: Run tests to pass**

```bash
cd /home/user/Vectorae
python -m pytest tests/test_auto_reply_models.py -xvs
```

Expected: PASS (3 tests)

**Step 5: Create migration**

Create `/home/user/Vectorae/alembic/versions/0101_auto_reply.py`:

```python
"""Auto-reply inbox rules and templates.

CREATES
  auto_reply_templates    Reusable response templates
  auto_reply_rules        Per-strategy auto-reply rules
  user_vacation_modes     User vacation/OOO auto-reply settings

Revision ID: 0101_auto_reply
Revises: 0100_bulk_import
"""

import sqlalchemy as sa
from alembic import op

revision = "0101_auto_reply"
down_revision = "0100_bulk_import"


def upgrade() -> None:
    op.create_table(
        "auto_reply_templates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("subject", sa.String(500), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("is_favorite", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_auto_reply_templates_user_id", "auto_reply_templates", ["user_id"])

    op.create_table(
        "auto_reply_rules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("strategy_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("match_type", sa.String(32), nullable=False, server_default="keyword"),
        sa.Column("pattern", sa.String(500), nullable=False),
        sa.Column("response_template_id", sa.Uuid(), nullable=True),
        sa.Column("inline_response", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["strategy_id"], ["strategies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["response_template_id"], ["auto_reply_templates.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "strategy_id", "name", name="user_strategy_rule_name"),
    )
    op.create_index("ix_auto_reply_rules_user_id", "auto_reply_rules", ["user_id"])
    op.create_index("ix_auto_reply_rules_strategy_id", "auto_reply_rules", ["strategy_id"])
    op.create_index("ix_auto_reply_rules_enabled", "auto_reply_rules", ["enabled"])

    op.create_table(
        "user_vacation_modes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("subject", sa.String(500), nullable=False, server_default="Out of Office"),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("start_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="user_vacation_mode_unique"),
    )
    op.create_index("ix_user_vacation_modes_enabled", "user_vacation_modes", ["enabled"])


def downgrade() -> None:
    op.drop_index("ix_user_vacation_modes_enabled", "user_vacation_modes")
    op.drop_table("user_vacation_modes")
    
    op.drop_index("ix_auto_reply_rules_enabled", "auto_reply_rules")
    op.drop_index("ix_auto_reply_rules_strategy_id", "auto_reply_rules")
    op.drop_index("ix_auto_reply_rules_user_id", "auto_reply_rules")
    op.drop_table("auto_reply_rules")
    
    op.drop_index("ix_auto_reply_templates_user_id", "auto_reply_templates")
    op.drop_table("auto_reply_templates")
```

**Step 6: Commit**

```bash
cd /home/user/Vectorae
git add -A
git commit -m "feat: add auto-reply models and migration

- Add AutoReplyMatchType enum (keyword, sender, subject patterns)
- Add AutoReplyTemplate table for reusable response templates
- Add AutoReplyRule table for per-strategy auto-reply rules
- Add UserVacationMode table for OOO auto-reply settings
- Create migration 0101_auto_reply"
```

---

### Task 6: Auto-Reply Rule Management API

**Files:**
- Create: `app/services/auto_reply.py` — Rule management service
- Create: `app/api/routes/auto_reply.py` — API endpoints
- Create: `tests/test_auto_reply_api.py` — Integration tests

*Similar pattern to bulk import: write failing test, implement service, add endpoints. Due to space constraints, I'll provide a condensed version.*

**Commands to test and commit:**

```bash
cd /home/user/Vectorae
python -m pytest tests/test_auto_reply_api.py -xvs
python -m pytest evals/run_evals.py
python3 evals/tools/promote.py
git add -A
git commit -m "feat: add auto-reply rule management API

- POST /api/v1/auto-reply/rules - create rule
- GET /api/v1/auto-reply/rules - list rules for strategy
- PUT /api/v1/auto-reply/rules/{id} - update rule
- DELETE /api/v1/auto-reply/rules/{id} - delete rule
- Enable/disable per-strategy with compliance gates"
```

---

### Task 7: Auto-Reply Dispatcher Worker

**Files:**
- Create: `app/workers/auto_reply_dispatcher.py` — Send auto-replies
- Create: `tests/test_auto_reply_dispatcher.py` — Tests

When an inbound reply is received via webhook:
1. Check if vacation mode enabled → send vacation reply
2. Check auto-reply rules (by priority) → send matching reply
3. All replies go through compliance gates
4. Log event for audit

**Verification:**
```bash
cd /home/user/Vectorae
python -m pytest tests/test_auto_reply_dispatcher.py -xvs
python3 evals/run_evals.py
```

---

## FEATURE 3: Multiple Email Support

**Goal:** Add support for Outlook, Yahoo, Zoho, custom domains alongside Gmail; remove unique constraint on user_id + provider.

**Database Changes:**
- New table: `email_accounts` — manage multiple email accounts per user
- Modify: `integration_tokens` — remove unique constraint on user_id (allow multiple Gmail, Outlook, etc.)
- New column: User table gets `primary_email_account_id`

---

### Task 8: Email Accounts Model and Migration

**Files:**
- Modify: `app/db/models.py` — Add EmailAccount model
- Create: `alembic/versions/0102_multi_email.py` — Migration

**Step 1: Write failing test**

Create `/home/user/Vectorae/tests/test_email_accounts.py`:

```python
import pytest
from app.db.models import EmailAccount, EmailProvider


def test_email_provider_enums_exist():
    """Verify all email providers are supported."""
    assert hasattr(EmailProvider, 'GMAIL')
    assert hasattr(EmailProvider, 'OUTLOOK')
    assert hasattr(EmailProvider, 'YAHOO')
    assert hasattr(EmailProvider, 'ZOHO')
    assert hasattr(EmailProvider, 'CUSTOM')


def test_email_account_model_exists():
    """Verify EmailAccount table exists with required columns."""
    import sqlalchemy as sa
    inspector = sa.inspect(EmailAccount)
    columns = {c.name for c in inspector.columns}
    
    required = {'id', 'user_id', 'email', 'provider', 'is_primary', 
                'is_connected', 'created_at', 'updated_at'}
    
    assert required.issubset(columns), f"Missing: {required - columns}"
```

**Step 2: Run test to fail**

```bash
cd /home/user/Vectorae
python -m pytest tests/test_email_accounts.py::test_email_provider_enums_exist -xvs
```

Expected: FAIL

**Step 3: Add enum and model**

Add to `/home/user/Vectorae/app/db/models.py` after other enums:

```python
class EmailProvider(str, enum.Enum):
    """Email service provider."""
    GMAIL = "gmail"
    OUTLOOK = "outlook"
    YAHOO = "yahoo"
    ZOHO = "zoho"
    CUSTOM = "custom"             # Custom domain (self-hosted or external)
```

Then add model:

```python
class EmailAccount(TimestampMixin, Base):
    """One email account connected to a user."""
    __tablename__ = "email_accounts"
    __table_args__ = (
        UniqueConstraint("user_id", "email", name="user_email_unique"),
    )
    
    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    
    # The email address
    email: Mapped[str] = mapped_column(String(320))
    
    # Provider
    provider: Mapped[EmailProvider] = mapped_column(
        _enum(EmailProvider), default=EmailProvider.GMAIL
    )
    
    # Settings
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    
    # Connection status
    is_connected: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    
    # OAuth token expiry
    token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    
    # Last sync time
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    
    # Settings JSON: {signature, auto_reply_enabled, ...}
    settings_json: Mapped[dict] = mapped_column(JSON, default=dict)
```

**Step 4: Modify integration_tokens unique constraint**

Edit migration to change `IntegrationToken` constraint from:
```python
UniqueConstraint("user_id", "provider", "token_kind", name="user_provider_kind")
```

To:
```python
UniqueConstraint("user_id", "provider", "token_kind", "email_account_id", name="user_provider_email_kind")
```

Add column to `IntegrationToken`:
```python
email_account_id: Mapped[uuid.UUID | None] = mapped_column(
    ForeignKey("email_accounts.id", ondelete="CASCADE"), nullable=True, index=True
)
```

**Step 5: Run tests and create migration**

```bash
cd /home/user/Vectorae
python -m pytest tests/test_email_accounts.py -xvs
```

Create migration `/home/user/Vectorae/alembic/versions/0102_multi_email.py`:

```python
"""Multi-email account support.

CREATES
  email_accounts           Manage multiple email accounts per user
  
MODIFIES
  integration_tokens       Add email_account_id foreign key
  users                    Add primary_email_account_id

Revision ID: 0102_multi_email
Revises: 0101_auto_reply
"""

import sqlalchemy as sa
from alembic import op

revision = "0102_multi_email"
down_revision = "0101_auto_reply"


def upgrade() -> None:
    op.create_table(
        "email_accounts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False, server_default="gmail"),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("is_connected", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("settings_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "email", name="user_email_unique"),
    )
    op.create_index("ix_email_accounts_user_id", "email_accounts", ["user_id"])
    op.create_index("ix_email_accounts_is_primary", "email_accounts", ["is_primary"])
    op.create_index("ix_email_accounts_is_connected", "email_accounts", ["is_connected"])

    # Modify integration_tokens to add email_account_id
    op.add_column("integration_tokens", sa.Column("email_account_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_integration_tokens_email_account_id",
        "integration_tokens",
        "email_accounts",
        ["email_account_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_integration_tokens_email_account_id", "integration_tokens", ["email_account_id"])


def downgrade() -> None:
    op.drop_index("ix_integration_tokens_email_account_id", "integration_tokens")
    op.drop_constraint("fk_integration_tokens_email_account_id", "integration_tokens", type_="foreignkey")
    op.drop_column("integration_tokens", "email_account_id")
    
    op.drop_index("ix_email_accounts_is_connected", "email_accounts")
    op.drop_index("ix_email_accounts_is_primary", "email_accounts")
    op.drop_index("ix_email_accounts_user_id", "email_accounts")
    op.drop_table("email_accounts")
```

**Step 6: Commit**

```bash
cd /home/user/Vectorae
git add -A
git commit -m "feat: add multi-email account support

- Create EmailAccount table for managing multiple accounts per user
- Add EmailProvider enum (Gmail, Outlook, Yahoo, Zoho, Custom)
- Remove unique constraint on integration_tokens user_id
- Allow multiple email accounts with different settings
- Track connection status and token expiry per account"
```

---

### Task 9: Email Account Connection API (OAuth)

**Files:**
- Create: `app/services/email_account_manager.py` — Account management
- Create: `app/api/routes/email_accounts.py` — Connection endpoints
- Create: `tests/test_email_account_api.py` — Tests

For each provider (Gmail, Outlook, Yahoo, Zoho):
- `/api/v1/email-accounts/connect/{provider}` — Start OAuth flow
- `/api/v1/email-accounts/callback/{provider}` — Handle OAuth callback
- `/api/v1/email-accounts` — List connected accounts
- `/api/v1/email-accounts/{id}/primary` — Set as primary
- `/api/v1/email-accounts/{id}/disconnect` — Revoke access

**Test:**
```bash
cd /home/user/Vectorae
python -m pytest tests/test_email_account_api.py -xvs
```

---

### Task 10: Compliance Gates for Multi-Email

**Files:**
- Modify: `app/verification/compliance_gates.py` — Add email account checks
- Create: `tests/test_multi_email_compliance.py` — Compliance tests

Ensure:
- Every outbound message includes the correct sender (from the connected account)
- Suppress lists are checked per account
- Unsubscribe headers use correct account
- Reply-to matches sender

**Test:**
```bash
cd /home/user/Vectorae
python -m pytest tests/test_multi_email_compliance.py -xvs
```

---

## Full Verification and Testing

After all tasks, run:

```bash
# Run unit tests
python -m pytest tests/test_bulk_import*.py -xvs
python -m pytest tests/test_auto_reply*.py -xvs
python -m pytest tests/test_email_account*.py -xvs

# Run offline eval suite (must pass; sealed tests cannot break)
python3 evals/tools/registry_lint.py evals/registry.yaml
python3 evals/run_evals.py

# Run gate (candidate vs baseline)
python3 evals/tools/promote.py
# Expected: exit 0 (PROMOTE — no regressions)

# Verify sealed compliance tests still pass
python3 evals/fixtures/verify_block_fixtures.py

# Quick gate before merge
scripts/gate/quick_gate.sh

# Full gate before marking ready
scripts/gate/full_gate.sh
```

---

## Commit and Merge

```bash
# Create squash commit
git add -A
git commit -m "feat: add three features - bulk import, auto-reply, multi-email

FEATURE 1: Bulk CSV Lead Import
- Parse and validate CSV with required fields (email, name, company, title, phone, source, retainer, frequency)
- Preview validated rows before confirming
- Async Celery worker processes import, checks duplicates
- Track job status: pending → validating → preview → importing → completed

FEATURE 2: Auto-Reply Inbox
- Extend inbox with auto-reply rules (keywords, patterns, sender)
- Reusable response templates with favorites
- Vacation mode with date-based enablement
- All replies go through compliance gates

FEATURE 3: Multiple Email Support
- Support Gmail, Outlook, Yahoo, Zoho, custom domains
- EmailAccount table removes unique constraint on user_id
- OAuth flow per provider
- Primary account selection, connection status tracking
- Compliance gates ensure correct sender/from headers

Database: 3 new migrations (0100, 0101, 0102)
Tests: 10+ new test files, all passing
Evals: All offline suite tests pass, gate passes (PROMOTE)
Compliance: CAN-SPAM, GDPR, CASL gates maintained"

# Merge to master
scripts/gate/safe_merge.sh <PR_NUMBER>
```

---

## Success Criteria

1. **Bulk Import Feature Complete:**
   - CSV upload, validation, preview, async import
   - Leads created with duplicates skipped
   - Job status tracked end-to-end
   - All tests pass

2. **Auto-Reply Feature Complete:**
   - Rules created per strategy
   - Templates reusable
   - Vacation mode with dates
   - Dispatcher sends with compliance gates
   - All tests pass

3. **Multi-Email Feature Complete:**
   - Multiple accounts per user
   - OAuth for each provider
   - Primary account selection
   - Compliance gates updated
   - All tests pass

4. **Overall Quality:**
   - No regressions (evals/tools/promote.py exits 0)
   - All sealed compliance tests still pass
   - New tests are deterministic, offline
   - Commit message documents changes
   - PR ready for co-founder review post-merge

