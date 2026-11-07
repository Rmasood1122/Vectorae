"""Bulk import API routes."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
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

    if not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="File must be CSV format")

    content = await file.read()

    service = BulkImportService(db)
    job = service.create_import_job(
        user_id=current_user.id,
        filename=file.filename,
        csv_content=content,
    )

    return {
        "import_id": str(job.id),
        "status": job.status.value,
        "filename": job.filename,
        "validation_complete": job.status.value in ["ready_for_preview", "failed"],
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
        "import_id": str(job.id),
        "status": job.status.value,
    }
