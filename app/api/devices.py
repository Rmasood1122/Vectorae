"""
devices.py — Vectorae (M7 Chunk 3)

Device token registration API for FCM push notifications.

Endpoints:
  POST   /devices/register      — upsert FCM token for the current user
  DELETE /devices/{token}        — soft-delete (mark invalid) a token

Token rotation:
  When FCM rotates a device token, the client sends the new token to
  POST /devices/register. The upsert handles this automatically: if the
  new token already exists it updates last_seen_at; if not it inserts.
  The old (rotated) token is eventually marked invalid by FCM error responses.

Auth: JWT required for all endpoints (standard get_current_user dependency from M5).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.db.models import DeviceToken
from app.services.auth import get_current_user  # canonical M5 auth dependency
from app.db.models import User as UserRead  # devices.py annotates with the ORM user

log = logging.getLogger(__name__)
router = APIRouter()


# ── Request / response schemas ────────────────────────────────────────────────

class DeviceRegisterRequest(BaseModel):
    token:    str                               = Field(..., min_length=1, description="FCM device token")
    platform: Literal["android", "ios"]         = Field(..., description="Device platform")


class DeviceRegisterResponse(BaseModel):
    token:       str
    platform:    str
    registered:  bool       # True = new registration, False = updated existing


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post(
    "/register",
    response_model=DeviceRegisterResponse,
    status_code=status.HTTP_200_OK,
    summary="Register or refresh a device token for push notifications",
)
def register_device(
    body:         DeviceRegisterRequest,
    current_user: UserRead = Depends(get_current_user),
    db:           Session  = Depends(get_db),
) -> DeviceRegisterResponse:
    """
    Upsert a device token for the authenticated user.

    - If the token does not exist: insert a new record.
    - If the token already exists: update last_seen_at and ensure is_valid=True.
    - Tokens from OTHER users that match are updated to this user (token transfer
      after device wipe / new install).

    Called on app startup after login, and on token refresh events.
    """
    now = datetime.now(timezone.utc)

    # PostgreSQL upsert on the unique token column
    stmt = (
        pg_insert(DeviceToken)
        .values(
            user_id=current_user.id,
            token=body.token,
            platform=body.platform,
            created_at=now,
            last_seen_at=now,
            is_valid=True,
        )
        .on_conflict_do_update(
            index_elements=["token"],
            set_=dict(
                user_id=current_user.id,      # handle token transfer
                last_seen_at=now,
                is_valid=True,                 # re-validate if previously marked invalid
            ),
        )
        .returning(DeviceToken.created_at)
    )

    result = db.execute(stmt)
    db.commit()

    returned_created_at = result.scalar_one()
    is_new = abs((returned_created_at - now).total_seconds()) < 1

    log.info(
        "Device token %s for user %s (%s)",
        "registered" if is_new else "refreshed",
        current_user.id,
        body.platform,
    )

    return DeviceRegisterResponse(
        token=body.token,
        platform=body.platform,
        registered=is_new,
    )


@router.delete(
    "/{token}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Deregister a device token (soft delete)",
    # `from __future__ import annotations` makes FastAPI resolve `-> None`
    # to NoneType (truthy) and reject it for 204 — be explicit instead.
    response_model=None,
)
def deregister_device(
    token:        str,
    current_user: UserRead = Depends(get_current_user),
    db:           Session  = Depends(get_db),
) -> None:
    """
    Soft-delete a device token by marking it invalid.

    Call this on logout so the user stops receiving notifications on this device.
    The record is kept for audit; FCM errors will also mark it invalid automatically.
    """
    result = db.execute(
        update(DeviceToken)
        .where(
            DeviceToken.token   == token,
            DeviceToken.user_id == current_user.id,  # users can only deregister their own tokens
        )
        .values(is_valid=False)
        .returning(DeviceToken.id)
    )
    db.commit()

    if result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Token not found or does not belong to this account.",
        )

    log.info("Device token deregistered for user %s", current_user.id)
