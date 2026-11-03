"""Admin panel: system-scope integration credentials and system settings.

Two surfaces the feature expansion needed and app/api/admin.py did not have:

  /admin/integrations      the deployment-wide API keys and OAuth app
                           credentials (OpenAI, NewsAPI, Vapi, Slack
                           app, HubSpot app ...). Stored ONLY through
                           TokenStore (Fernet-encrypted); never returned --
                           GET reports which keys are set, not their values.
  /admin/system-settings   feature switches and limits (consensus on/off,
                           LinkedIn daily cap, mutation idle days ...).

A separate router rather than more routes in admin.py because admin.py is
already 750 lines, and none of its paths collide with these (checked when this
was added -- see the note in app/main.py).
"""

# NOTE: deliberately NO `from __future__ import annotations` (FastAPI).

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.core.logging import get_logger
from app.db.base import get_db
from app.db.models import User
from app.services import credentials, system_settings

logger = get_logger("api.admin_integrations")

router = APIRouter(prefix="/admin", tags=["admin"])


class CredentialsIn(BaseModel):
    # key -> value. An empty string DELETES that key, so the form can clear a
    # single field without a second endpoint.
    values: dict[str, str] = Field(default_factory=dict)


class SettingIn(BaseModel):
    value: Any


@router.get("/integrations")
def list_system_integrations(db: Session = Depends(get_db),
                             _admin: User = Depends(require_admin)) -> list[dict]:
    return credentials.system_status(db)


@router.put("/integrations/{provider}")
def set_system_integration(provider: str, body: CredentialsIn,
                           db: Session = Depends(get_db),
                           admin: User = Depends(require_admin)) -> dict:
    spec = credentials.spec_for(provider)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"unknown provider {provider!r}")
    if spec.scope == "user":
        raise HTTPException(status_code=422,
                            detail=f"{provider} is connected per user, not by an admin")
    unknown = sorted(set(body.values) - set(spec.keys) - set(spec.optional))
    if unknown:
        raise HTTPException(status_code=422,
                            detail=f"unknown keys for {provider}: {unknown}")
    for key, value in body.values.items():
        if value.strip():
            credentials.set_system_secret(db, provider, key, value.strip())
        else:
            credentials.delete_system_secret(db, provider, key)
    # Log WHICH keys changed and who changed them. Never the values.
    logger.info("admin.system_credentials_updated", provider=provider,
                keys=sorted(body.values), admin_id=str(admin.id))
    return next(p for p in credentials.system_status(db) if p["provider"] == provider)


@router.delete("/integrations/{provider}")
def clear_system_integration(provider: str, db: Session = Depends(get_db),
                             admin: User = Depends(require_admin)) -> dict:
    if credentials.spec_for(provider) is None:
        raise HTTPException(status_code=404, detail=f"unknown provider {provider!r}")
    removed = credentials.delete_system_secret(db, provider)
    logger.info("admin.system_credentials_cleared", provider=provider,
                removed=removed, admin_id=str(admin.id))
    return {"provider": provider, "removed": removed}


@router.get("/system-settings")
def list_system_settings(db: Session = Depends(get_db),
                         _admin: User = Depends(require_admin)) -> list[dict]:
    return system_settings.all_settings(db)


@router.put("/system-settings/{key}")
def update_system_setting(key: str, body: SettingIn, db: Session = Depends(get_db),
                          admin: User = Depends(require_admin)) -> dict:
    try:
        value = system_settings.set(db, key, body.value, actor_user_id=admin.id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown setting {key!r}") from None
    except system_settings.SettingTypeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    logger.info("admin.system_setting_updated", key=key, admin_id=str(admin.id))
    return {"key": key, "value": value}
