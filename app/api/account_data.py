"""GDPR account data -- the account holder's rights of access (Art. 15 / 20)
and erasure (Art. 17).

POST /me/data-export   {"password": "..."} -> a JSON file of every row this
                       account owns (app/services/account_export.py).
DELETE /me             {"password": "...", "confirm": "DELETE"} -> the account
                       is erased (app/services/account_erasure.py; spec
                       docs/features/gdpr-erasure.md).

WHY POST WITH A PASSWORD. The file holds every prospect, message and meeting
note in the account. A stolen access token must not be enough to walk off with
all of it in one request, so the signed-in person re-types their password, the
same rule as deleting a strategy. A personal API key (Zapier, scripts) is
refused outright: a takeout of the whole account is a person's act.

WHOSE DATA. /me is a personal route (app/services/rbac.py), so the workspace
header is ignored and current_user is always the person who signed in: a
workspace member exports their own account, never the owner's.

AUDIT. Every export and every refused attempt writes a security_audit_events
row, committed before the response leaves.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.rate_limiting import AUTH_WINDOW_SECONDS, enforce_rate_limit
from app.db.base import get_db
from app.db.models import User
from app.services import account_erasure, account_export
from app.services import auth as auth_svc
from app.services import security_audit

logger = logging.getLogger(__name__)

router = APIRouter(tags=["account"])

INVALID_PASSWORD = "INVALID_PASSWORD"
API_KEY_REFUSED = ("A data export needs you to sign in; a personal API key "
                   "cannot download the whole account.")
# DELETE /me contracts the frontend matches on (lib/profile.ts).
CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"
DELETE_API_KEY_REFUSED = ("Deleting the account needs you to sign in; a personal "
                          "API key cannot delete it.")
ADMIN_REFUSED = ("An administrator account cannot be deleted from the app. Ask "
                 "another administrator to remove your admin rights first.")


class DataExportIn(BaseModel):
    password: str = Field(min_length=1, max_length=200)


class AccountDeleteIn(BaseModel):
    password: str = Field(min_length=1, max_length=200)
    # Typed by the person: must be exactly account_erasure.CONFIRMATION_PHRASE.
    confirm: str = Field(default="", max_length=50)


@router.post("/me/data-export")
def export_my_data(body: DataExportIn, request: Request,
                   db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)) -> Response:
    """Download everything this account owns as one JSON file.

    ORDER. API-key refusal, then the rate limit (its own bucket, shared limit
    and window with login, so mistyping here cannot lock anyone out of
    signing in), then the password, then the export.
    """
    if getattr(request.state, "auth_via", None) == "api_key":
        raise HTTPException(status_code=403, detail=API_KEY_REFUSED)
    enforce_rate_limit(f"acct:{current_user.id}", "password_confirm", "RATE_LIMIT_AUTH",
                       AUTH_WINDOW_SECONDS)

    if not auth_svc.verify_password(body.password, current_user.password_hash):
        security_audit.record(
            db, action=security_audit.ACCOUNT_EXPORT_DENIED, request=request,
            user_id=current_user.id, target_type="account", target_id=current_user.id,
            details={"reason": "invalid_password"})
        db.commit()
        raise HTTPException(status_code=403, detail=INVALID_PASSWORD)

    payload = account_export.build(db, current_user.id)
    security_audit.record(
        db, action=security_audit.ACCOUNT_EXPORTED, request=request,
        user_id=current_user.id, target_type="account", target_id=current_user.id,
        details={"format_version": payload["format_version"],
                 "rows": sum(payload["row_counts"].values())})
    db.commit()
    logger.info("account %s exported its data (%d rows)", current_user.id,
                sum(payload["row_counts"].values()))

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Response(
        content=json.dumps(payload, ensure_ascii=False, indent=1),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="leadpilot-account-export-{stamp}.json"',
            "Cache-Control": "no-store",
        },
    )


@router.delete("/me")
def delete_my_account(body: AccountDeleteIn, request: Request,
                      db: Session = Depends(get_db),
                      current_user: User = Depends(get_current_user)) -> Response:
    """Erase this account (GDPR Art. 17). Irreversible.

    WHOSE ACCOUNT. /me is personal (app/services/rbac.py): the workspace header
    is ignored and current_user is always the person who signed in. A
    workspace owner's account can therefore only ever be deleted by that
    owner; a member deleting their own account removes only their own data
    and memberships (decision E3, account_erasure.py).

    ORDER. API-key refusal, the rate limit (the password-confirmation bucket
    shared with data export and strategy deletion), the typed confirmation,
    the administrator refusal, the password (a refusal is audited and
    committed on its own), then billing: a subscription that exists in Stripe
    is cancelled FIRST, and if that cannot be confirmed (Stripe unreachable,
    or no Stripe key on this server) nothing is erased (502). Then the
    provider-side deletions run (disconnect_providers, best effort), and the
    erasure and its record commit together; the account's files are removed
    after.
    """
    if getattr(request.state, "auth_via", None) == "api_key":
        raise HTTPException(status_code=403, detail=DELETE_API_KEY_REFUSED)
    enforce_rate_limit(f"acct:{current_user.id}", "password_confirm", "RATE_LIMIT_AUTH",
                       AUTH_WINDOW_SECONDS)
    if body.confirm != account_erasure.CONFIRMATION_PHRASE:
        raise HTTPException(status_code=422, detail=CONFIRMATION_REQUIRED)
    if current_user.is_admin:
        # An operator account, not a customer's: deleting the last admin would
        # lock the deployment's operators out of the admin console.
        raise HTTPException(status_code=409, detail=ADMIN_REFUSED)

    if not auth_svc.verify_password(body.password, current_user.password_hash):
        security_audit.record(
            db, action=security_audit.ACCOUNT_DELETE_DENIED, request=request,
            user_id=current_user.id, target_type="account", target_id=current_user.id,
            details={"reason": "invalid_password"})
        db.commit()
        raise HTTPException(status_code=403, detail=INVALID_PASSWORD)

    user_id = current_user.id
    try:
        billing_result = account_erasure.cancel_billing(db, user_id)
    except account_erasure.BillingNotCancellable as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    # Committed on its own: Stripe has already stopped billing, and the local
    # mirror must say so even if the erasure below were to fail.
    db.commit()

    providers = account_erasure.disconnect_providers(db, user_id)
    files = account_erasure.collect_media_files(db, user_id)
    summary = account_erasure.erase_account(db, current_user, billing=billing_result,
                                            providers=providers)
    db.commit()
    removed = account_erasure.delete_files(files)
    logger.info("account %s deleted itself (%d files removed)", user_id, removed)

    response = JSONResponse(content={
        "deleted": True,
        "deleted_at": summary["deleted_at"],
        "kept": summary["kept"],
        "billing_retention_years": summary["billing_retention_years"],
    })
    from app.api.auth import _clear_refresh_cookie, _wants_cookie  # noqa: PLC0415

    if _wants_cookie(request):
        _clear_refresh_cookie(response)
    return response
