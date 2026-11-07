"""Tests for bulk import API endpoints."""

import io
import uuid

from app.db.models import BulkImport, BulkImportStatus
from tests.conftest import auth_headers


def test_bulk_import_upload_endpoint(client, test_user):
    """Verify POST /bulk-import/upload creates a BulkImport record."""
    csv_content = b"""client_name,email,company,title
John Doe,john@example.com,Acme Inc,CEO"""

    files = {'file': ('leads.csv', io.BytesIO(csv_content))}

    response = client.post(
        "/bulk-import/upload",
        files=files,
        headers=auth_headers(test_user),
    )

    assert response.status_code == 200
    data = response.json()
    assert 'import_id' in data
    assert data['status'] in ['validating', 'ready_for_preview', 'failed']


def test_bulk_import_preview_endpoint(client, test_user, db_session):
    """Verify GET /bulk-import/{id}/preview returns preview."""
    # Create a bulk import job
    job = BulkImport(
        id=uuid.uuid4(),
        user_id=test_user.id,
        filename="test.csv",
        status=BulkImportStatus.READY_FOR_PREVIEW,
        total_rows=2,
        valid_rows=2,
        failed_rows=0,
        preview_json=[
            {
                'row_number': 1,
                'email': 'john@example.com',
                'client_name': 'John Doe',
                'company': 'Acme Inc',
                'status': 'valid',
                'error': ''
            },
            {
                'row_number': 2,
                'email': 'jane@example.com',
                'client_name': 'Jane Smith',
                'company': 'TechCorp',
                'status': 'valid',
                'error': ''
            }
        ]
    )
    db_session.add(job)
    db_session.commit()

    response = client.get(
        f"/bulk-import/{job.id}/preview",
        headers=auth_headers(test_user),
    )

    assert response.status_code == 200
    data = response.json()
    assert 'preview' in data
    assert 'valid_count' in data
    assert 'error_count' in data


def test_bulk_import_confirm_endpoint(client, test_user, db_session):
    """Verify POST /bulk-import/{id}/confirm starts import."""
    # Create a bulk import job
    job = BulkImport(
        id=uuid.uuid4(),
        user_id=test_user.id,
        filename="test.csv",
        status=BulkImportStatus.READY_FOR_PREVIEW,
        total_rows=1,
        valid_rows=1,
        failed_rows=0,
        preview_json=[
            {
                'row_number': 1,
                'email': 'john@example.com',
                'client_name': 'John Doe',
                'company': 'Acme Inc',
                'status': 'valid',
                'error': ''
            }
        ]
    )
    db_session.add(job)
    db_session.commit()

    response = client.post(
        f"/bulk-import/{job.id}/confirm",
        headers=auth_headers(test_user),
    )

    assert response.status_code == 200
    data = response.json()
    assert data['status'] == 'importing'


def test_bulk_import_upload_non_csv_file(client, test_user):
    """Verify non-CSV file upload is rejected."""
    files = {'file': ('data.txt', io.BytesIO(b'not csv'))}

    response = client.post(
        "/bulk-import/upload",
        files=files,
        headers=auth_headers(test_user),
    )

    assert response.status_code == 400
    assert 'CSV' in response.json()['detail']


def test_bulk_import_preview_not_found(client, test_user):
    """Verify preview endpoint returns 404 for missing job."""
    fake_id = uuid.uuid4()

    response = client.get(
        f"/bulk-import/{fake_id}/preview",
        headers=auth_headers(test_user),
    )

    assert response.status_code == 404
