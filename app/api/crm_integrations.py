"""HubSpot and Salesforce: connect, sync, settings, and their signed webhooks
(Feature Group 4). See app/services/crm_sync.py for what syncs and how.

    GET    /integrations/crm                        both providers' status
    GET    /integrations/{hubspot|salesforce}/auth-url
    GET    /integrations/{hubspot|salesforce}/callback   (public; OAuth redirect)
    POST   /integrations/{hubspot|salesforce}/sync       "Sync now" -> 202
    PUT    /integrations/{hubspot|salesforce}/settings
    DELETE /integrations/{hubspot|salesforce}            disconnect -> 204
    POST   /webhooks/hubspot       (public; X-HubSpot-Signature-v3)
    POST   /webhooks/salesforce    (public; X-LeadPilot-Signature over "<ts>.<body>")

Routes are registered per provider rather than with a {provider} path
parameter: /integrations/{provider} would also match /integrations/slack and
/integrations/gmail and answer them with a 422.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.config import settings
from app.core.rate_limiting import enforce_rate_limit
from app.db.base import get_db
from app.db.models import User
from app.integrations import hubspot, salesforce
from app.integrations.hubspot import HubSpotError
from app.integrations.salesforce import SalesforceError
from app.integrations.token_store import TokenStore
from app.services import crm_sync, oauth_state

logger = logging.getLogger(__name__)
router = APIRouter(tags=["crm-integrations"])

SIGNATURE_TOLERANCE_SECONDS = 300


def redirect_uri(provider: str) -> str:
    return f"{settings.public_base_url.rstrip('/')}/integrations/{provider}/callback"


def frontend_redirect(provider: str, status: str, reason: str | None = None) -> RedirectResponse:
    query = {provider: status, **({"reason": reason[:200]} if reason else {})}
    return RedirectResponse(
        f"{settings.frontend_url.rstrip('/')}/settings?tab=integrations&{urlencode(query)}",
        status_code=303)


def _app_credentials(db: Session, provider: str) -> tuple[str, str]:
    client_id = TokenStore.get(db, None, f"{provider}_app", "client_id")
    client_secret = TokenStore.get(db, None, f"{provider}_app", "client_secret")
    if not client_id or not client_secret:
        raise HTTPException(status_code=503, detail=(
            f"The {provider.title()} app is not configured -- an admin must add its "
            "client id and secret under Admin > Integrations."))
    return client_id, client_secret


@router.get("/integrations/crm")
def crm_status(db: Session = Depends(get_db),
               current_user: User = Depends(get_current_user)) -> list[dict]:
    return [crm_sync.status_out(db, crm_sync.get_connection(db, current_user.id, p), p)
            for p in crm_sync.PROVIDERS]


# --------------------------------------------------------------------------
# OAuth
# --------------------------------------------------------------------------


@router.get("/integrations/hubspot/auth-url")
def hubspot_auth_url(db: Session = Depends(get_db),
                     current_user: User = Depends(get_current_user)) -> dict:
    client_id, _ = _app_credentials(db, "hubspot")
    state = oauth_state.make(current_user.id, "hubspot")
    return {"auth_url": hubspot.build_auth_url(client_id, redirect_uri("hubspot"), state)}


def _gone(user: User | None) -> bool:
    """A signed state outlives the account that asked for it: an erased
    account (GDPR tombstone) must not be given a CRM connection back."""
    from app.services.account_erasure import is_tombstone  # noqa: PLC0415

    return user is None or is_tombstone(user)


@router.get("/integrations/hubspot/callback", include_in_schema=False)
def hubspot_callback(code: str | None = None, state: str | None = None,
                     error: str | None = None, db: Session = Depends(get_db)):
    """Public on purpose: a browser redirect carries no bearer token. The
    signed, purpose-bound, expiring `state` identifies the user."""
    if error or not code:
        return frontend_redirect("hubspot", "error", error or "missing code")
    try:
        user_id = oauth_state.parse(state, "hubspot")
    except oauth_state.InvalidState as exc:
        return frontend_redirect("hubspot", "error", str(exc))
    if _gone(db.get(User, user_id)):
        return frontend_redirect("hubspot", "error", "unknown user")
    try:
        client_id, client_secret = _app_credentials(db, "hubspot")
        tokens = hubspot.exchange_code(client_id, client_secret, code, redirect_uri("hubspot"))
        crm_sync.store_hubspot_tokens(db, user_id, tokens)
        info = hubspot.token_info(tokens["access_token"])
    except (HubSpotError, HTTPException, KeyError) as exc:
        logger.warning("hubspot connect failed for %s: %s", user_id, exc)
        return frontend_redirect("hubspot", "error", "HubSpot rejected the connection")
    conn = crm_sync.upsert_connection(db, user_id, "hubspot",
                                      account_id=str(info.get("hub_id") or ""),
                                      account_name=info.get("hub_domain"))
    from app.workers import crm_tasks  # noqa: PLC0415

    crm_tasks.enqueue_sync(conn.id, full=True)
    return frontend_redirect("hubspot", "connected")


@router.get("/integrations/salesforce/auth-url")
def salesforce_auth_url(db: Session = Depends(get_db),
                        current_user: User = Depends(get_current_user)) -> dict:
    client_id, _ = _app_credentials(db, "salesforce")
    state = oauth_state.make(current_user.id, "salesforce")
    return {"auth_url": salesforce.build_auth_url(client_id, redirect_uri("salesforce"), state)}


@router.get("/integrations/salesforce/callback", include_in_schema=False)
def salesforce_callback(code: str | None = None, state: str | None = None,
                        error: str | None = None, db: Session = Depends(get_db)):
    if error or not code:
        return frontend_redirect("salesforce", "error", error or "missing code")
    try:
        user_id = oauth_state.parse(state, "salesforce")
    except oauth_state.InvalidState as exc:
        return frontend_redirect("salesforce", "error", str(exc))
    if _gone(db.get(User, user_id)):
        return frontend_redirect("salesforce", "error", "unknown user")
    try:
        client_id, client_secret = _app_credentials(db, "salesforce")
        tokens = salesforce.exchange_code(client_id, client_secret, code,
                                          redirect_uri("salesforce"))
        crm_sync.store_salesforce_tokens(db, user_id, tokens)
    except (SalesforceError, HTTPException, KeyError) as exc:
        logger.warning("salesforce connect failed for %s: %s", user_id, exc)
        return frontend_redirect("salesforce", "error", "Salesforce rejected the connection")
    conn = crm_sync.upsert_connection(
        db, user_id, "salesforce",
        account_id=salesforce.org_id_from_identity(tokens.get("id")),
        account_name=tokens.get("instance_url"), instance_url=tokens.get("instance_url"))
    from app.workers import crm_tasks  # noqa: PLC0415

    crm_tasks.enqueue_sync(conn.id, full=True)
    return frontend_redirect("salesforce", "connected")


# --------------------------------------------------------------------------
# Sync, settings, disconnect (one route per provider)
# --------------------------------------------------------------------------


class CrmSettingsIn(BaseModel):
    sync_new_leads: bool | None = None
    lead_status_map: dict[str, str] | None = Field(default=None, max_length=20)
    deal_stage_map: dict[str, str] | None = Field(default=None, max_length=5)
    pipeline: str | None = Field(default=None, max_length=100)


_LOCAL_STATUSES = crm_sync.ENGAGED | crm_sync.EARLY
_LOCAL_STAGES = {"open", "won", "lost"}


def _connected(db: Session, user: User, provider: str):
    conn = crm_sync.get_connection(db, user.id, provider)
    if conn is None:
        raise HTTPException(status_code=404, detail=f"{provider} is not connected")
    return conn


def _register_provider_routes(provider: str) -> None:
    def sync_now(db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)) -> dict:
        enforce_rate_limit(str(current_user.id), f"crm_sync_{provider}", "RATE_LIMIT_AI_ACTION")
        conn = _connected(db, current_user, provider)
        from app.workers import crm_tasks  # noqa: PLC0415

        return {"queued": crm_tasks.enqueue_sync(conn.id, full=True)}

    def update_settings(body: CrmSettingsIn, db: Session = Depends(get_db),
                        current_user: User = Depends(get_current_user)) -> dict:
        conn = _connected(db, current_user, provider)
        current = dict(conn.settings_json or {})
        changes = body.model_dump(exclude_unset=True)
        for key, allowed in (("lead_status_map", _LOCAL_STATUSES),
                             ("deal_stage_map", _LOCAL_STAGES)):
            bad = set((changes.get(key) or {})) - allowed
            if bad:
                raise HTTPException(status_code=422,
                                    detail=f"{key}: unknown Vectorae values {sorted(bad)}")
        for key, value in changes.items():
            if value is None:
                current.pop(key, None)
            else:
                current[key] = value
        conn.settings_json = current
        db.commit()
        return crm_sync.status_out(db, conn, provider)

    def disconnect(db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)) -> Response:
        if not crm_sync.disconnect(db, current_user.id, provider):
            raise HTTPException(status_code=404, detail=f"{provider} is not connected")
        return Response(status_code=204)

    router.add_api_route(f"/integrations/{provider}/sync", sync_now, methods=["POST"],
                         status_code=202, name=f"{provider}_sync")
    router.add_api_route(f"/integrations/{provider}/settings", update_settings,
                         methods=["PUT"], name=f"{provider}_settings")
    router.add_api_route(f"/integrations/{provider}", disconnect, methods=["DELETE"],
                         status_code=204, name=f"{provider}_disconnect")


for _provider in crm_sync.PROVIDERS:
    _register_provider_routes(_provider)


# --------------------------------------------------------------------------
# Inbound webhooks (public, signed, fail closed)
# --------------------------------------------------------------------------


def _public_uri(request: Request) -> str:
    query = f"?{request.url.query}" if request.url.query else ""
    return f"{settings.public_base_url.rstrip('/')}{request.url.path}{query}"


@router.post("/webhooks/hubspot", include_in_schema=False)
async def hubspot_webhook(request: Request, db: Session = Depends(get_db)) -> dict:
    body = await request.body()
    secret = TokenStore.get(db, None, "hubspot_app", "client_secret")
    signature = request.headers.get("X-HubSpot-Signature-v3")
    timestamp = request.headers.get("X-HubSpot-Request-Timestamp")
    # HubSpot signs the URL it called; behind a proxy that is the public URL,
    # not the one uvicorn sees, so both are accepted.
    if not secret or not any(
            hubspot.verify_signature_v3(secret, "POST", uri, body, timestamp, signature)
            for uri in {str(request.url), _public_uri(request)}):
        raise HTTPException(status_code=401, detail="invalid signature")
    try:
        events = json.loads(body or b"[]")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid JSON") from exc
    return {"ok": True, **crm_sync.handle_hubspot_events(db, events)}


def verify_relay_signature(secret: str, body: bytes, timestamp: str | None,
                           signature: str | None, now: float | None = None) -> bool:
    """X-LeadPilot-Timestamp + X-LeadPilot-Signature: sha256=<hex HMAC-SHA256(
    secret, "<timestamp>.<body>")>, timestamp within five minutes."""
    if not (secret and timestamp and signature):
        return False
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    if abs((time.time() if now is None else now) - ts) > SIGNATURE_TOLERANCE_SECONDS:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), f"{ts}.".encode() + body,
                                    hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


@router.post("/webhooks/salesforce", include_in_schema=False)
async def salesforce_webhook(request: Request, db: Session = Depends(get_db)) -> dict:
    body = await request.body()
    secret = TokenStore.get(db, None, "salesforce_app", "webhook_secret")
    if not verify_relay_signature(secret or "", body,
                                  request.headers.get("X-LeadPilot-Timestamp"),
                                  request.headers.get("X-LeadPilot-Signature")):
        raise HTTPException(status_code=401, detail="invalid signature")
    try:
        payload = json.loads(body or b"{}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="expected a JSON object")
    return {"ok": True, **crm_sync.handle_salesforce_payload(db, payload)}
