"""Feature Group 5 API — LinkedIn accounts, Unipile callbacks, lead state.

  GET    /integrations/linkedin/accounts            accounts + today's usage
  POST   /integrations/linkedin/connect             Unipile hosted-auth URL
  POST   /integrations/linkedin/accounts            link an existing Unipile account id
  PATCH  /integrations/linkedin/accounts/{id}       activate / deactivate
  POST   /integrations/linkedin/accounts/{id}/refresh   re-read Premium + InMail credits
  DELETE /integrations/linkedin/accounts/{id}
  GET    /leads/{id}/linkedin                       connection state
  POST   /integrations/linkedin/unipile/notify      PUBLIC: hosted-auth callback
  POST   /webhooks/unipile                          PUBLIC: replies + new connections

THE TWO PUBLIC ENDPOINTS AND HOW THEY ARE AUTHENTICATED
  notify    Unipile calls the notify_url WE generated, which carries a
            Fernet-encrypted {user_id, purpose, expiry} state. Fernet is
            AES + HMAC-SHA256, so the state cannot be forged or altered; an
            expired or foreign state is refused.
  webhook   Verified with the `unipile.webhook_secret` system credential, as
            EITHER X-LeadPilot-Signature: sha256=HMAC(secret, raw body) (a
            relay that signs bodies) OR Unipile-Auth: <secret> (the custom
            header Unipile adds to each delivery; Unipile itself does not sign
            bodies). Constant-time compare; an unset secret rejects every
            delivery -- fail closed, like the WhatsApp webhook.
"""

# NOTE: deliberately NO `from __future__ import annotations` (FastAPI).

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.leads import _owned_lead
from app.config import settings
from app.core.logging import get_logger
from app.db.base import get_db
from app.db.models import (
    InboundReply,
    Lead,
    LinkedInAccount,
    ProcessedWebhook,
    Product,
    Strategy,
    User,
)
from app.services import credentials, crypto, linkedin_limits, linkedin_outreach

logger = get_logger("api.linkedin")

router = APIRouter(tags=["linkedin"])

STATE_PURPOSE = "unipile_link"
STATE_TTL = timedelta(hours=2)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class AccountOut(BaseModel):
    id: uuid.UUID
    unipile_account_id: str
    display_name: str | None
    profile_url: str | None
    has_premium: bool
    inmail_credits: int | None
    inmail_sent_total: int
    is_active: bool
    status: str
    last_used_at: datetime | None
    usage_today: dict
    limits: dict


class LinkAccountIn(BaseModel):
    unipile_account_id: str = Field(min_length=3, max_length=120)


class AccountPatch(BaseModel):
    is_active: bool


def _out(db: Session, account: LinkedInAccount) -> AccountOut:
    return AccountOut(
        id=account.id, unipile_account_id=account.unipile_account_id,
        display_name=account.display_name, profile_url=account.profile_url,
        has_premium=account.has_premium, inmail_credits=account.inmail_credits,
        inmail_sent_total=account.inmail_sent_total, is_active=account.is_active,
        status=account.status, last_used_at=account.last_used_at,
        usage_today=linkedin_limits.usage(account.id),
        limits=linkedin_limits.limits(db),
    )


def _owned_account(db: Session, account_id: uuid.UUID, user: User) -> LinkedInAccount:
    account = db.get(LinkedInAccount, account_id)
    if account is None or account.user_id != user.id:
        raise HTTPException(status_code=404, detail="LinkedIn account not found")
    return account


def _client(db: Session, user: User):
    client = linkedin_outreach.client_for(db, user.id)
    if client is None:
        raise HTTPException(status_code=503,
                            detail="LinkedIn is not configured on this deployment "
                                   "(Admin > Integrations > Unipile)")
    return client


def _upsert_account(db: Session, user_id, unipile_account_id: str, client) -> LinkedInAccount:
    info = client.account(unipile_account_id)
    account = db.execute(
        select(LinkedInAccount).where(LinkedInAccount.unipile_account_id == unipile_account_id)
    ).scalar_one_or_none()
    if account is not None and account.user_id != user_id:
        raise HTTPException(status_code=409,
                            detail="this LinkedIn account is connected to another user")
    if account is None:
        account = LinkedInAccount(user_id=user_id, unipile_account_id=unipile_account_id)
        db.add(account)
    account.display_name = info.get("name") or account.display_name
    account.profile_url = info.get("profile_url") or account.profile_url
    account.has_premium = bool(info.get("has_premium"))
    account.inmail_credits = client.inmail_balance(unipile_account_id)
    account.is_active = True
    account.status = "ok"
    db.commit()
    return account


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------


@router.get("/integrations/linkedin/accounts", response_model=list[AccountOut])
def list_accounts(db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)) -> list[AccountOut]:
    rows = db.execute(
        select(LinkedInAccount).where(LinkedInAccount.user_id == current_user.id)
        .order_by(LinkedInAccount.created_at)
    ).scalars().all()
    return [_out(db, a) for a in rows]


@router.post("/integrations/linkedin/connect")
def connect_url(db: Session = Depends(get_db),
                current_user: User = Depends(get_current_user)) -> dict:
    client = _client(db, current_user)
    expires = datetime.now(timezone.utc) + STATE_TTL
    state = crypto.encrypt_json({"user_id": str(current_user.id), "purpose": STATE_PURPOSE,
                                 "exp": expires.isoformat()})
    notify = (f"{settings.public_base_url.rstrip('/')}/integrations/linkedin/unipile/notify"
              f"?state={state}")
    url = client.hosted_link(
        notify_url=notify, name=str(current_user.id),
        expires_on=expires.isoformat().replace("+00:00", "Z"),
        success_url=f"{settings.frontend_url.rstrip('/')}/settings?tab=integrations",
    )
    return {"url": url}


@router.post("/integrations/linkedin/accounts", response_model=AccountOut, status_code=201)
def link_account(body: LinkAccountIn, db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)) -> AccountOut:
    client = _client(db, current_user)
    try:
        account = _upsert_account(db, current_user.id, body.unipile_account_id.strip(), client)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        from app.core.errors import public_detail  # noqa: PLC0415

        why = public_detail(exc, "check the account id in Unipile",
                            event="api.linkedin_account_lookup_failed")
        raise HTTPException(status_code=422,
                            detail=f"Unipile does not recognise that account: {why}") from exc
    return _out(db, account)


@router.patch("/integrations/linkedin/accounts/{account_id}", response_model=AccountOut)
def patch_account(account_id: uuid.UUID, body: AccountPatch, db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)) -> AccountOut:
    account = _owned_account(db, account_id, current_user)
    account.is_active = body.is_active
    db.commit()
    return _out(db, account)


@router.post("/integrations/linkedin/accounts/{account_id}/refresh", response_model=AccountOut)
def refresh_account(account_id: uuid.UUID, db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_user)) -> AccountOut:
    account = _owned_account(db, account_id, current_user)
    client = _client(db, current_user)
    return _out(db, _upsert_account(db, current_user.id, account.unipile_account_id, client))


@router.delete("/integrations/linkedin/accounts/{account_id}", status_code=204)
def delete_account(account_id: uuid.UUID, db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)) -> None:
    db.delete(_owned_account(db, account_id, current_user))
    db.commit()


@router.get("/leads/{lead_id}/linkedin")
def lead_linkedin(lead_id: uuid.UUID, db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)) -> dict:
    return linkedin_outreach.lead_state(_owned_lead(db, lead_id, current_user))


# ---------------------------------------------------------------------------
# Public: hosted-auth callback
# ---------------------------------------------------------------------------


@router.post("/integrations/linkedin/unipile/notify")
async def unipile_notify(request: Request, state: str = Query(min_length=20),
                         db: Session = Depends(get_db)) -> dict:
    try:
        data = crypto.decrypt_json(state)
        if data.get("purpose") != STATE_PURPOSE:
            raise ValueError("wrong purpose")
        if datetime.fromisoformat(data["exp"]) < datetime.now(timezone.utc):
            raise ValueError("expired")
        user_id = uuid.UUID(data["user_id"])
    except Exception:  # noqa: BLE001 -- any bad state is the same answer
        raise HTTPException(status_code=400, detail="invalid or expired state") from None
    from app.services.account_erasure import is_tombstone  # noqa: PLC0415

    user = db.get(User, user_id)
    if user is None or is_tombstone(user):   # an erased account stays erased
        raise HTTPException(status_code=400, detail="invalid or expired state")
    body = await request.json()
    status = str(body.get("status") or "").upper()
    account_id = body.get("account_id")
    if not account_id or status not in ("CREATION_SUCCESS", "RECONNECTED", "SUCCESS"):
        return {"ok": True, "linked": False, "status": status}
    client = linkedin_outreach.client_for(db, user.id)
    if client is None:
        return {"ok": True, "linked": False, "status": "not_configured"}
    _upsert_account(db, user.id, str(account_id), client)
    logger.info("linkedin.account_linked", user_id=str(user.id))
    return {"ok": True, "linked": True}


# ---------------------------------------------------------------------------
# Public: event webhook
# ---------------------------------------------------------------------------


def verify_unipile(raw: bytes, headers, secret: str | None) -> bool:
    if not secret:
        return False
    signed = headers.get("X-LeadPilot-Signature") or ""
    if signed.startswith("sha256="):
        expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        return hmac.compare_digest(signed.removeprefix("sha256="), expected)
    static = headers.get("Unipile-Auth") or ""
    return bool(static) and hmac.compare_digest(static, secret)


def _lead_for(db: Session, user_id, provider_id: str | None, profile_url: str | None) -> Lead | None:
    owned = (select(Lead.id).join(Strategy, Strategy.id == Lead.strategy_id)
             .join(Product, Product.id == Strategy.product_id)
             .where(Product.user_id == user_id))
    if provider_id:
        lead = db.execute(select(Lead).where(Lead.linkedin_provider_id == provider_id,
                                             Lead.id.in_(owned))).scalars().first()
        if lead is not None:
            return lead
    slug = linkedin_outreach.normalize_profile(profile_url)
    if not slug:
        return None
    for lead in db.execute(select(Lead).where(Lead.linkedin_url.isnot(None),
                                              Lead.id.in_(owned))).scalars():
        if linkedin_outreach.normalize_profile(lead.linkedin_url) == slug:
            return lead
    return None


@router.post("/webhooks/unipile")
async def unipile_webhook(request: Request, db: Session = Depends(get_db)) -> dict:
    """Replies (`message_received`), accepted requests (`new_relation`) and
    account status changes. Shapes per Unipile's webhook docs (read
    2026-10-27): a message event carries account_info.user_id (our own
    LinkedIn id) and sender.attendee_provider_id, and Unipile sends our OWN
    messages through it too; a status event is {"AccountStatus": {account_id,
    account_type, message}} with no `event` key."""
    raw = await request.body()
    secret = credentials.get_secret(db, "unipile", "webhook_secret")
    if not verify_unipile(raw, request.headers, secret):
        raise HTTPException(status_code=401, detail="invalid webhook signature")
    payload = json.loads(raw or b"{}")
    if isinstance(payload.get("AccountStatus"), dict):
        return _account_status(db, payload["AccountStatus"])
    event = payload.get("event") or payload.get("type")
    event_id = str(payload.get("message_id") or payload.get("event_id")
                   or hashlib.sha256(raw).hexdigest())[:200]
    db.add(ProcessedWebhook(provider="unipile", event_id=f"{event}:{event_id}"[:200]))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return {"ok": True, "duplicate": True}

    account = db.execute(
        select(LinkedInAccount).where(LinkedInAccount.unipile_account_id == payload.get("account_id"))
    ).scalar_one_or_none()
    if account is None:
        return {"ok": True, "matched": False, "reason": "unknown account"}

    if event == "new_relation":
        lead = _lead_for(db, account.user_id, payload.get("user_provider_id"),
                         payload.get("user_profile_url") or payload.get("user_public_identifier"))
        if lead is None:
            return {"ok": True, "matched": False}
        lead.linkedin_connection_status = "connected"
        lead.linkedin_connected_at = lead.linkedin_connected_at or datetime.now(timezone.utc)
        lead.linkedin_account_id = lead.linkedin_account_id or account.id
        db.commit()
        return {"ok": True, "matched": True, "action": "connected"}

    if event == "message_received":
        own_id = (payload.get("account_info") or {}).get("user_id")
        if payload.get("is_sender") or (
                own_id and own_id == (payload.get("sender") or {}).get("attendee_provider_id")):
            return {"ok": True, "matched": False, "reason": "own message"}
        sender = payload.get("sender") or {}
        lead = _lead_for(db, account.user_id, sender.get("attendee_provider_id"),
                         sender.get("attendee_profile_url"))
        if lead is None:
            return {"ok": True, "matched": False}
        reply = InboundReply(
            lead_id=lead.id, account_ref=f"linkedin:{account.id}", channel="linkedin",
            thread_ref=payload.get("chat_id"),
            from_address=sender.get("attendee_profile_url") or sender.get("attendee_provider_id") or "linkedin",
            body=str(payload.get("message") or "")[:20_000],
            received_at=datetime.now(timezone.utc),
        )
        db.add(reply)
        lead.linkedin_chat_id = payload.get("chat_id") or lead.linkedin_chat_id
        if lead.linkedin_connection_status != "connected":
            lead.linkedin_connection_status = "connected"
            lead.linkedin_connected_at = datetime.now(timezone.utc)
        db.commit()
        from app.workers import reply_tasks  # noqa: PLC0415
        from app.workers.outreach_tasks import route_linkedin_inbound_impl  # noqa: PLC0415

        # Route FIRST (it sets and commits reply.classification and applies
        # the stop rules), THEN publish the reply-intelligence job -- the email
        # path's order. Publishing first let the job read the row while
        # classification was still NULL: reply_intent, reengagement_memory and
        # Reply Copilot all branch on it (gaps-sweep-2 #7). The job stays off
        # this thread (two model calls here would have Unipile retry on
        # timeout); `finally` so a routing error cannot leave the reply
        # unclassified for the human as well.
        reply_id = reply.id   # read now: a failed routing may leave the session unusable
        try:
            classification = route_linkedin_inbound_impl(db, lead, reply)
        finally:
            reply_tasks.enqueue(reply_id)
        return {"ok": True, "matched": True, "classification": classification}

    return {"ok": True, "action": "ignored", "event": event}


# AccountStatus.message (webhooks docs) -> linkedin_accounts.status. Only "ok"
# is used for sending (linkedin_limits.pick_account); CONNECTING is a step on
# the way and leaves the status as it is.
_ACCOUNT_STATUS = {
    "OK": "ok", "SYNC_SUCCESS": "ok", "RECONNECTED": "ok", "CREATION_SUCCESS": "ok",
    "CREDENTIALS": "credentials_needed", "ERROR": "error", "STOPPED": "stopped",
    "DELETED": "deleted",
}


def _account_status(db: Session, status_event: dict) -> dict:
    """Not de-duplicated: the same status can recur (disconnected again after a
    reconnect), and applying it twice is harmless."""
    account = db.execute(
        select(LinkedInAccount).where(
            LinkedInAccount.unipile_account_id == status_event.get("account_id"))
    ).scalar_one_or_none()
    if account is None:
        return {"ok": True, "matched": False, "reason": "unknown account"}
    status = _ACCOUNT_STATUS.get(str(status_event.get("message") or "").upper())
    if status is not None:
        account.status = status
        db.commit()
    return {"ok": True, "action": "account_status", "status": account.status}
