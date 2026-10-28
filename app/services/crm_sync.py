"""Two-way CRM sync — HubSpot and Salesforce (Feature Group 4).

WHAT SYNCS
  Vectorae -> CRM   engaged leads (replied and beyond; every sourced lead too
                     if the user turns on `sync_new_leads`) become HubSpot
                     contacts / Salesforce leads, with their status mapped;
                     deals become HubSpot deals / Salesforce opportunities.
  CRM -> Vectorae   status changes on records Vectorae linked: a contact
                     marked unqualified disqualifies the lead, lifecycle
                     "customer" closes it won, a deal moved to closed won/lost
                     settles the local deal and lead; a HubSpot deal created on
                     a linked contact is imported.
Records that exist only in the CRM are not imported -- Vectorae is where the
prospecting happens, not a second copy of the CRM.

HOW
  * Every link is an external_crm_links row, unique both ways, so a retried push
    updates the linked record instead of duplicating it.
  * Pushes happen immediately on key events (the event hub calls on_event)
    and in a 15-minute sweep that pushes whatever changed since the last one
    -- the sweep is what catches the status changes no event announces (a
    kanban drag, a meeting outcome). The same sweep pulls linked records back
    (HubSpot batch read / SOQL), so two-way sync works even where nobody set up
    CRM webhooks; the webhooks (/webhooks/hubspot, /webhooks/salesforce) just
    make it immediate.
  * Inbound changes only move a lead FORWARD in the funnel or into a closed
    state, and never reopen a closed one. That is also what stops ping-pong:
    Vectorae pushes hs_lead_status OPEN_DEAL for a won lead, reads it back as
    "opportunity", and ignores it because closed_won is already further on.

Status maps are defaults; a connection's settings_json can override them
(lead_status_map, deal_stage_map, pipeline) for CRMs with custom picklists.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    CrmConnection,
    CrmSyncLink,
    Deal,
    DealStage,
    Lead,
    LeadStatus,
    Product,
    Strategy,
)
from app.integrations import hubspot, salesforce
from app.integrations.hubspot import HubSpotError
from app.integrations.salesforce import SalesforceError
from app.integrations.token_store import TokenStore

logger = logging.getLogger(__name__)

PROVIDERS = ("hubspot", "salesforce")
REMOTE_LEAD_TYPE = {"hubspot": "contact", "salesforce": "lead"}
REMOTE_DEAL_TYPE = {"hubspot": "deal", "salesforce": "opportunity"}

ENGAGED = {"replied", "meeting_booked", "opportunity", "closed_won", "closed_lost",
           "disqualified"}
EARLY = {"verified", "contacted"}
TERMINAL = {"closed_won", "closed_lost", "disqualified"}
_RANK = {"sourced": 0, "enriched": 0, "email_found": 0, "verified": 0, "flagged": 0,
         "contacted": 1, "replied": 2, "meeting_booked": 3, "opportunity": 4}
PUSH_BATCH_LIMIT = 500
LEAD_EVENTS = frozenset({"meeting_booked", "reply_received", "reply_interested",
                         "call_completed", "deal_won"})

HUBSPOT_LEAD_STATUS = {
    "verified": "NEW", "contacted": "ATTEMPTED_TO_CONTACT", "replied": "CONNECTED",
    "meeting_booked": "IN_PROGRESS", "opportunity": "OPEN_DEAL", "closed_won": "OPEN_DEAL",
    "closed_lost": "BAD_TIMING", "disqualified": "UNQUALIFIED",
}
# Only ever pushed forward: HubSpot rejects moving a lifecycle stage backwards.
HUBSPOT_LIFECYCLE = {"opportunity": "opportunity", "closed_won": "customer"}
HUBSPOT_DEAL_STAGE = {"open": "appointmentscheduled", "won": "closedwon", "lost": "closedlost"}
SALESFORCE_LEAD_STATUS = {
    "verified": "Open - Not Contacted", "contacted": "Working - Contacted",
    "replied": "Working - Contacted", "meeting_booked": "Working - Contacted",
    "opportunity": "Working - Contacted", "closed_won": "Closed - Converted",
    "closed_lost": "Closed - Not Converted", "disqualified": "Closed - Not Converted",
}
SALESFORCE_STAGE = {"open": "Prospecting", "won": "Closed Won", "lost": "Closed Lost"}

_HS_IN_LEAD_STATUS = {"UNQUALIFIED": "disqualified", "OPEN_DEAL": "opportunity"}
_HS_IN_LIFECYCLE = {"opportunity": "opportunity", "customer": "closed_won"}
_HS_IN_DEAL_STAGE = {"closedwon": DealStage.WON, "closedlost": DealStage.LOST}
_SF_IN_LEAD_STATUS = {"Closed - Converted": "opportunity",
                      "Closed - Not Converted": "disqualified"}


class CrmNotConnected(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid(value) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _status(value) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _truthy(value) -> bool:
    return value is True or str(value).lower() == "true"


# --------------------------------------------------------------------------
# Connections
# --------------------------------------------------------------------------


def get_connection(db: Session, user_id, provider: str) -> CrmConnection | None:
    return db.execute(select(CrmConnection).where(CrmConnection.user_id == _uuid(user_id),
                                                  CrmConnection.provider == provider)
                      ).scalar_one_or_none()


def connections(db: Session, user_id, providers=None) -> list[CrmConnection]:
    rows = db.execute(select(CrmConnection).where(CrmConnection.user_id == _uuid(user_id),
                                                  CrmConnection.status != "revoked")
                      ).scalars().all()
    return [c for c in rows if not providers or c.provider in providers]


def has_connection(db: Session, user_id) -> bool:
    if user_id is None:
        return False
    return db.execute(select(func.count(CrmConnection.id)).where(
        CrmConnection.user_id == _uuid(user_id), CrmConnection.status != "revoked")
    ).scalar_one() > 0


def upsert_connection(db: Session, user_id, provider: str, *, account_id: str | None,
                      account_name: str | None = None,
                      instance_url: str | None = None) -> CrmConnection:
    conn = get_connection(db, user_id, provider)
    if conn is None:
        conn = CrmConnection(user_id=_uuid(user_id), provider=provider)
        db.add(conn)
    conn.account_id = account_id
    conn.account_name = account_name
    if instance_url:
        conn.instance_url = instance_url
    conn.status = "connected"
    conn.last_error = None
    db.commit()
    db.refresh(conn)
    return conn


def _settings(conn: CrmConnection) -> dict:
    return conn.settings_json if isinstance(conn.settings_json, dict) else {}


def _map(conn: CrmConnection, key: str, default: dict) -> dict:
    return {**default, **(_settings(conn).get(key) or {})}


def _ok(conn: CrmConnection) -> None:
    conn.status = "connected"
    conn.last_error = None


def _fail(conn: CrmConnection, exc: Exception) -> None:
    conn.last_error = str(exc)[:1000]
    status = getattr(exc, "status", None)
    if isinstance(exc, CrmNotConnected) or status in (401, 403):
        conn.status = "error"
    logger.warning("crm %s sync for user %s failed: %s", conn.provider, conn.user_id, exc)


def disconnect(db: Session, user_id, provider: str) -> bool:
    conn = get_connection(db, user_id, provider)
    TokenStore.delete(db, _uuid(user_id), provider)
    db.query(CrmSyncLink).filter(CrmSyncLink.user_id == _uuid(user_id),
                                 CrmSyncLink.provider == provider).delete(
        synchronize_session=False)
    if conn is not None:
        db.delete(conn)
    db.commit()
    return conn is not None


def forget_local(db: Session, local_type: str, local_id) -> int:
    """Drop links to a deleted lead/deal (GDPR erase)."""
    count = db.query(CrmSyncLink).filter(CrmSyncLink.local_type == local_type,
                                         CrmSyncLink.local_id == _uuid(local_id)).delete(
        synchronize_session=False)
    db.commit()
    return count


def status_out(db: Session, conn: CrmConnection | None, provider: str) -> dict:
    configured = TokenStore.get(db, None, f"{provider}_app", "client_id") is not None
    if conn is None:
        return {"provider": provider, "configured": configured, "connected": False}
    links = dict(db.execute(
        select(CrmSyncLink.local_type, func.count(CrmSyncLink.id))
        .where(CrmSyncLink.user_id == conn.user_id, CrmSyncLink.provider == provider)
        .group_by(CrmSyncLink.local_type)).all())
    return {
        "provider": provider, "configured": configured, "connected": True,
        "status": conn.status, "account_id": conn.account_id,
        "account_name": conn.account_name,
        "last_push_at": conn.last_push_at.isoformat() if conn.last_push_at else None,
        "last_pull_at": conn.last_pull_at.isoformat() if conn.last_pull_at else None,
        "last_error": conn.last_error,
        "linked_leads": links.get("lead", 0), "linked_deals": links.get("deal", 0),
        "settings": {"sync_new_leads": bool(_settings(conn).get("sync_new_leads")),
                     **{k: v for k, v in _settings(conn).items() if k != "sync_new_leads"}},
    }


# --------------------------------------------------------------------------
# Tokens and clients
# --------------------------------------------------------------------------


def _app_secret(db: Session, provider: str, key: str) -> str:
    value = TokenStore.get(db, None, provider, key)
    if not value:
        raise CrmNotConnected(f"{provider}.{key} is not configured (Admin > Integrations)")
    return value


def store_hubspot_tokens(db: Session, user_id, data: dict) -> None:
    expires = _now() + timedelta(seconds=max(60, int(data.get("expires_in") or 1800) - 60))
    TokenStore.set(db, _uuid(user_id), "hubspot", "access_token", data["access_token"],
                   expires_at=expires, commit=False)
    if data.get("refresh_token"):
        TokenStore.set(db, _uuid(user_id), "hubspot", "refresh_token", data["refresh_token"],
                       commit=False)
    db.commit()


def _hubspot_token(db: Session, conn: CrmConnection) -> str:
    token = TokenStore.get(db, conn.user_id, "hubspot", "access_token")
    if token:
        return token
    refresh_token = TokenStore.get(db, conn.user_id, "hubspot", "refresh_token")
    if not refresh_token:
        raise CrmNotConnected("HubSpot is not connected -- reconnect it in Settings")
    data = hubspot.refresh(_app_secret(db, "hubspot_app", "client_id"),
                           _app_secret(db, "hubspot_app", "client_secret"), refresh_token)
    store_hubspot_tokens(db, conn.user_id, data)
    return data["access_token"]


def hubspot_client(db: Session, conn: CrmConnection) -> hubspot.HubSpotClient:
    return hubspot.HubSpotClient(_hubspot_token(db, conn))


def store_salesforce_tokens(db: Session, user_id, data: dict) -> None:
    TokenStore.set(db, _uuid(user_id), "salesforce", "access_token", data["access_token"],
                   commit=False)
    if data.get("refresh_token"):
        TokenStore.set(db, _uuid(user_id), "salesforce", "refresh_token",
                       data["refresh_token"], commit=False)
    db.commit()


def _salesforce_refresh(db: Session, conn: CrmConnection) -> str:
    refresh_token = TokenStore.get(db, conn.user_id, "salesforce", "refresh_token")
    if not refresh_token:
        raise CrmNotConnected("Salesforce is not connected -- reconnect it in Settings")
    data = salesforce.refresh(_app_secret(db, "salesforce_app", "client_id"),
                              _app_secret(db, "salesforce_app", "client_secret"),
                              refresh_token)
    store_salesforce_tokens(db, conn.user_id, data)
    if data.get("instance_url"):
        conn.instance_url = data["instance_url"]
    return data["access_token"]


def salesforce_client(db: Session, conn: CrmConnection) -> salesforce.SalesforceClient:
    if not conn.instance_url:
        raise CrmNotConnected("Salesforce instance URL unknown -- reconnect")
    token = (TokenStore.get(db, conn.user_id, "salesforce", "access_token")
             or _salesforce_refresh(db, conn))
    return salesforce.SalesforceClient(conn.instance_url, token,
                                       on_unauthorized=lambda: _salesforce_refresh(db, conn))


# --------------------------------------------------------------------------
# Links
# --------------------------------------------------------------------------


def _link(db: Session, user_id, provider: str, local_type: str, local_id) -> CrmSyncLink | None:
    return db.execute(select(CrmSyncLink).where(
        CrmSyncLink.user_id == _uuid(user_id), CrmSyncLink.provider == provider,
        CrmSyncLink.local_type == local_type, CrmSyncLink.local_id == _uuid(local_id))
    ).scalar_one_or_none()


def _link_remote(db: Session, user_id, provider: str, remote_type: str,
                 remote_id: str) -> CrmSyncLink | None:
    return db.execute(select(CrmSyncLink).where(
        CrmSyncLink.user_id == _uuid(user_id), CrmSyncLink.provider == provider,
        CrmSyncLink.remote_type == remote_type, CrmSyncLink.remote_id == str(remote_id))
    ).scalar_one_or_none()


def _save_link(db: Session, conn: CrmConnection, local_type: str, local_id,
               remote_type: str, remote_id: str) -> CrmSyncLink:
    link = _link(db, conn.user_id, conn.provider, local_type, local_id)
    if link is not None:
        link.remote_type, link.remote_id = remote_type, str(remote_id)
        return link
    # The same person sourced into two campaigns is one CRM record; the first
    # lead keeps the link and later pushes simply update that record.
    existing = _link_remote(db, conn.user_id, conn.provider, remote_type, remote_id)
    if existing is not None:
        return existing
    link = CrmSyncLink(user_id=conn.user_id, provider=conn.provider, local_type=local_type,
                       local_id=_uuid(local_id), remote_type=remote_type,
                       remote_id=str(remote_id))
    db.add(link)
    db.flush()
    return link


# --------------------------------------------------------------------------
# Push: Vectorae -> CRM
# --------------------------------------------------------------------------


def _split_name(full_name: str | None) -> tuple[str | None, str | None]:
    parts = (full_name or "").strip().split()
    if not parts:
        return None, None
    if len(parts) == 1:
        return parts[0], None
    return " ".join(parts[:-1]), parts[-1]


def _hubspot_contact_props(conn: CrmConnection, lead: Lead) -> dict:
    first, last = _split_name(lead.full_name)
    status = _status(lead.status)
    props = {"email": lead.email, "firstname": first, "lastname": last,
             "company": lead.company, "jobtitle": lead.title, "phone": lead.phone,
             "hs_lead_status": _map(conn, "lead_status_map", HUBSPOT_LEAD_STATUS).get(status),
             "lifecyclestage": HUBSPOT_LIFECYCLE.get(status)}
    return {k: v for k, v in props.items() if v}


def _hubspot_write(fn, props: dict):
    """A lifecycle stage HubSpot refuses (a backwards move) must not block
    the rest of the update: retry once without it."""
    try:
        return fn(props)
    except HubSpotError as exc:
        if exc.status == 400 and "lifecyclestage" in props:
            return fn({k: v for k, v in props.items() if k != "lifecyclestage"})
        raise


def _push_lead_hubspot(db: Session, conn: CrmConnection, lead: Lead) -> str:
    client = hubspot_client(db, conn)
    props = _hubspot_contact_props(conn, lead)
    link = _link(db, conn.user_id, "hubspot", "lead", lead.id)
    remote_id = link.remote_id if link else client.find_contact_by_email(lead.email)
    if remote_id:
        try:
            _hubspot_write(lambda p: client.update_contact(remote_id, p), props)
        except HubSpotError as exc:
            if exc.status != 404:
                raise
            remote_id = None   # deleted in HubSpot: create it again
    if not remote_id:
        remote_id = _hubspot_write(client.create_contact, props)
    link = _save_link(db, conn, "lead", lead.id, "contact", remote_id)
    link.last_pushed_at = _now()
    return remote_id


def _salesforce_lead_fields(conn: CrmConnection, lead: Lead) -> dict:
    first, last = _split_name(lead.full_name)
    fields = {"Email": lead.email, "FirstName": first if last else None,
              "LastName": last or first or "Unknown", "Company": lead.company or "Unknown",
              "Title": lead.title, "Phone": lead.phone,
              "Status": _map(conn, "lead_status_map", SALESFORCE_LEAD_STATUS)
              .get(_status(lead.status))}
    return {k: v for k, v in fields.items() if v}


def _push_lead_salesforce(db: Session, conn: CrmConnection, lead: Lead) -> str:
    client = salesforce_client(db, conn)
    fields = _salesforce_lead_fields(conn, lead)
    link = _link(db, conn.user_id, "salesforce", "lead", lead.id)
    remote_id = link.remote_id if link else client.find_lead_by_email(lead.email)
    if remote_id:
        try:
            client.update("Lead", remote_id, fields)
        except SalesforceError as exc:
            if exc.status == 404:
                remote_id = None
            elif not (exc.status == 400 and "CONVERTED" in str(exc).upper()):
                raise
            # A converted lead is read-only in Salesforce; it now lives on as a
            # contact/opportunity the Salesforce user owns. Leave it.
    if not remote_id:
        remote_id = client.create("Lead", {**fields, "LeadSource": "Vectorae"})
    link = _save_link(db, conn, "lead", lead.id, "lead", remote_id)
    link.last_pushed_at = _now()
    return remote_id


def _deal_amount(deal: Deal) -> float:
    return round((deal.value_cents or 0) / 100, 2)


def _push_deal_hubspot(db: Session, conn: CrmConnection, deal: Deal) -> str:
    client = hubspot_client(db, conn)
    stage = _map(conn, "deal_stage_map", HUBSPOT_DEAL_STAGE)[_status(deal.stage)]
    props = {"dealname": deal.name, "amount": f"{_deal_amount(deal):.2f}", "dealstage": stage,
             "pipeline": _settings(conn).get("pipeline") or "default",
             "deal_currency_code": deal.currency}
    if deal.close_date:
        props["closedate"] = f"{deal.close_date.isoformat()}T00:00:00Z"
    link = _link(db, conn.user_id, "hubspot", "deal", deal.id)
    remote_id = None
    if link:
        try:
            client.update_deal(link.remote_id, props)
            remote_id = link.remote_id
        except HubSpotError as exc:
            if exc.status != 404:
                raise
    if not remote_id:
        remote_id = client.create_deal(props)
        lead = db.get(Lead, deal.lead_id) if deal.lead_id else None
        if lead is not None and lead.email:
            contact = _link(db, conn.user_id, "hubspot", "lead", lead.id)
            contact_id = contact.remote_id if contact else _push_lead_hubspot(db, conn, lead)
            try:
                client.associate_deal_contact(remote_id, contact_id)
            except HubSpotError as exc:
                logger.warning("hubspot: could not associate deal %s: %s", remote_id, exc)
    link = _save_link(db, conn, "deal", deal.id, "deal", remote_id)
    link.last_pushed_at = _now()
    return remote_id


def _push_deal_salesforce(db: Session, conn: CrmConnection, deal: Deal) -> str:
    client = salesforce_client(db, conn)
    lead = db.get(Lead, deal.lead_id) if deal.lead_id else None
    fields = {
        "Name": (deal.name or "Vectorae deal")[:120],
        "StageName": _map(conn, "deal_stage_map", SALESFORCE_STAGE)[_status(deal.stage)],
        # CloseDate is required on an Opportunity; an open deal gets a
        # placeholder a month out that the Salesforce user will adjust.
        "CloseDate": (deal.close_date or (date.today() + timedelta(days=30))).isoformat(),
        "Amount": _deal_amount(deal),
        "Description": f"Vectorae deal {deal.id}" + (f" for {lead.email}" if lead and lead.email
                                                     else ""),
    }
    link = _link(db, conn.user_id, "salesforce", "deal", deal.id)
    remote_id = None
    if link:
        try:
            client.update("Opportunity", link.remote_id, fields)
            remote_id = link.remote_id
        except SalesforceError as exc:
            if exc.status != 404:
                raise
    if not remote_id:
        remote_id = client.create("Opportunity", fields)
    link = _save_link(db, conn, "deal", deal.id, "opportunity", remote_id)
    link.last_pushed_at = _now()
    return remote_id


_PUSH_LEAD = {"hubspot": _push_lead_hubspot, "salesforce": _push_lead_salesforce}
_PUSH_DEAL = {"hubspot": _push_deal_hubspot, "salesforce": _push_deal_salesforce}
_ERRORS = (HubSpotError, SalesforceError, CrmNotConnected)


def _owner_of_lead(db: Session, lead: Lead):
    return db.execute(select(Product.user_id).join(Strategy, Strategy.product_id == Product.id)
                      .where(Strategy.id == lead.strategy_id)).scalar_one_or_none()


def push_lead(db: Session, user_id, lead_id, providers=None) -> dict:
    lead = db.get(Lead, _uuid(lead_id))
    if lead is None or not lead.email or _owner_of_lead(db, lead) != _uuid(user_id):
        return {}
    results = {}
    for conn in connections(db, user_id, providers):
        try:
            results[conn.provider] = _PUSH_LEAD[conn.provider](db, conn, lead)
            conn.last_push_at = _now()
            _ok(conn)
        except _ERRORS as exc:
            _fail(conn, exc)
            results[conn.provider] = None
    db.commit()
    return results


def push_deal(db: Session, user_id, deal_id, providers=None) -> dict:
    deal = db.get(Deal, _uuid(deal_id))
    if deal is None or deal.user_id != _uuid(user_id):
        return {}
    results = {}
    for conn in connections(db, user_id, providers):
        try:
            results[conn.provider] = _PUSH_DEAL[conn.provider](db, conn, deal)
            conn.last_push_at = _now()
            _ok(conn)
        except _ERRORS as exc:
            _fail(conn, exc)
            results[conn.provider] = None
    db.commit()
    return results


def push_changed(db: Session, conn: CrmConnection, *, full: bool = False) -> dict:
    """Push every syncable lead and deal changed since the last sweep (or all
    of them, `full`). Auth failures propagate; per-record failures count."""
    started = _now()
    statuses = ENGAGED | (EARLY if _settings(conn).get("sync_new_leads") else set())
    leads_q = (select(Lead).join(Strategy, Strategy.id == Lead.strategy_id)
               .join(Product, Product.id == Strategy.product_id)
               .where(Product.user_id == conn.user_id, Lead.email.isnot(None),
                      Lead.status.in_([LeadStatus(s) for s in statuses])))
    deals_q = select(Deal).where(Deal.user_id == conn.user_id)
    if not full and conn.last_push_at is not None:
        leads_q = leads_q.where(Lead.updated_at > conn.last_push_at)
        deals_q = deals_q.where(Deal.updated_at > conn.last_push_at)
    pushed = failed = 0
    for model_q, fn in ((leads_q.order_by(Lead.updated_at), _PUSH_LEAD[conn.provider]),
                        (deals_q.order_by(Deal.updated_at), _PUSH_DEAL[conn.provider])):
        for record in db.execute(model_q.limit(PUSH_BATCH_LIMIT)).scalars().all():
            try:
                fn(db, conn, record)
                pushed += 1
            except (HubSpotError, SalesforceError) as exc:
                if getattr(exc, "status", None) in (401, 403):
                    raise
                failed += 1
                logger.warning("crm %s: push of %s failed: %s", conn.provider, record.id, exc)
    conn.last_push_at = started
    db.commit()
    return {"pushed": pushed, "push_failed": failed}


# --------------------------------------------------------------------------
# Apply: CRM -> Vectorae
# --------------------------------------------------------------------------


def apply_lead_status(db: Session, lead: Lead, new_status: str, *, source: str) -> bool:
    """Forward-only; never reopens a closed lead. See the module docstring."""
    current = _status(lead.status)
    if new_status == current or current in TERMINAL:
        return False
    if new_status not in TERMINAL and _RANK.get(new_status, -1) <= _RANK.get(current, -1):
        return False
    lead.status = LeadStatus(new_status)
    if new_status in TERMINAL:
        # The shared rule (sequence_engine.CLOSED_STATUSES == TERMINAL,
        # asserted in tests/test_gaps_sweep_2.py), with the CRM's own reason.
        from app.services import sequence_engine as engine  # noqa: PLC0415

        engine.stop_for_status(db, lead, reason=f"crm_{source}")
    logger.info("crm %s moved lead %s %s -> %s", source, lead.id, current, new_status)
    return True


def apply_deal(db: Session, deal: Deal, *, stage: DealStage | None = None,
               amount_cents: int | None = None, close_date: date | None = None,
               source: str) -> bool:
    changed = False
    if stage is not None and deal.stage is not stage:
        deal.stage = stage
        changed = True
        if stage is DealStage.WON and deal.close_date is None:
            deal.close_date = date.today()
    if amount_cents is not None and amount_cents != deal.value_cents:
        deal.value_cents = amount_cents
        changed = True
    if close_date is not None and close_date != deal.close_date:
        deal.close_date = close_date
        changed = True
    if stage in (DealStage.WON, DealStage.LOST) and deal.lead_id:
        lead = db.get(Lead, deal.lead_id)
        if lead is not None:
            apply_lead_status(db, lead, "closed_won" if stage is DealStage.WON else "closed_lost",
                              source=source)
    return changed


def _cents(value) -> int | None:
    if value in (None, ""):
        return None
    try:
        return round(float(value) * 100)
    except (TypeError, ValueError):
        return None


def _parse_date(value) -> date | None:
    if value in (None, ""):
        return None
    text = str(value)
    try:
        if text.isdigit():
            return datetime.fromtimestamp(int(text) / 1000, tz=timezone.utc).date()
        return date.fromisoformat(text[:10])
    except (ValueError, OverflowError):
        return None


def _hubspot_stage_in(conn: CrmConnection, value) -> DealStage | None:
    inverse = {v: k for k, v in (_settings(conn).get("deal_stage_map") or {}).items()}
    if value in inverse:
        return DealStage(inverse[value])
    return _HS_IN_DEAL_STAGE.get(str(value or "").lower())


def _apply_hubspot_contact(db: Session, conn: CrmConnection, link: CrmSyncLink,
                           props: dict) -> bool:
    lead = db.get(Lead, link.local_id) if link.local_type == "lead" else None
    if lead is None:
        return False
    changed = False
    for target in (_HS_IN_LIFECYCLE.get(str(props.get("lifecyclestage") or "").lower()),
                   _HS_IN_LEAD_STATUS.get(str(props.get("hs_lead_status") or "").upper())):
        if target:
            changed = apply_lead_status(db, lead, target, source="hubspot") or changed
    link.last_pulled_at = _now()
    return changed


def _apply_hubspot_deal(db: Session, conn: CrmConnection, link: CrmSyncLink,
                        props: dict) -> bool:
    deal = db.get(Deal, link.local_id) if link.local_type == "deal" else None
    if deal is None:
        return False
    link.last_pulled_at = _now()
    return apply_deal(db, deal,
                      stage=_hubspot_stage_in(conn, props["dealstage"]) if "dealstage" in props
                      else None,
                      amount_cents=_cents(props.get("amount")),
                      close_date=_parse_date(props.get("closedate")), source="hubspot")


def _salesforce_lead_status_in(fields: dict) -> str | None:
    if _truthy(fields.get("IsConverted")):
        return "opportunity"
    return _SF_IN_LEAD_STATUS.get(str(fields.get("Status") or ""))


def _salesforce_stage_in(fields: dict) -> DealStage | None:
    if _truthy(fields.get("IsWon")) or fields.get("StageName") == "Closed Won":
        return DealStage.WON
    if _truthy(fields.get("IsClosed")) or fields.get("StageName") == "Closed Lost":
        return DealStage.LOST
    return None


def _apply_salesforce_lead(db: Session, conn: CrmConnection, link: CrmSyncLink,
                           fields: dict) -> bool:
    lead = db.get(Lead, link.local_id) if link.local_type == "lead" else None
    if lead is None:
        return False
    link.last_pulled_at = _now()
    target = _salesforce_lead_status_in(fields)
    return bool(target) and apply_lead_status(db, lead, target, source="salesforce")


def _apply_salesforce_opportunity(db: Session, conn: CrmConnection, link: CrmSyncLink,
                                  fields: dict) -> bool:
    deal = db.get(Deal, link.local_id) if link.local_type == "deal" else None
    if deal is None:
        return False
    link.last_pulled_at = _now()
    return apply_deal(db, deal, stage=_salesforce_stage_in(fields),
                      amount_cents=_cents(fields.get("Amount")),
                      close_date=_parse_date(fields.get("CloseDate")), source="salesforce")


def handle_hubspot_events(db: Session, events) -> dict:
    """Apply a (signature-verified) HubSpot webhook batch."""
    from app.workers import crm_tasks  # noqa: PLC0415

    applied = ignored = imported = 0
    cache: dict[str, list[CrmConnection]] = {}
    for event in events if isinstance(events, list) else []:
        if not isinstance(event, dict):
            continue
        portal = str(event.get("portalId") or "")
        if portal not in cache:
            cache[portal] = db.execute(select(CrmConnection).where(
                CrmConnection.provider == "hubspot", CrmConnection.account_id == portal,
                CrmConnection.status != "revoked")).scalars().all()
        kind = str(event.get("subscriptionType") or "")
        object_id = str(event.get("objectId") or "")
        prop, value = event.get("propertyName"), event.get("propertyValue")
        for conn in cache[portal]:
            done = False
            if kind == "contact.propertyChange" and prop in ("hs_lead_status", "lifecyclestage"):
                link = _link_remote(db, conn.user_id, "hubspot", "contact", object_id)
                done = bool(link) and _apply_hubspot_contact(db, conn, link, {prop: value})
            elif kind == "deal.propertyChange" and prop in ("dealstage", "amount", "closedate"):
                link = _link_remote(db, conn.user_id, "hubspot", "deal", object_id)
                done = bool(link) and _apply_hubspot_deal(db, conn, link, {prop: value})
            elif kind == "deal.creation":
                if _link_remote(db, conn.user_id, "hubspot", "deal", object_id) is None:
                    crm_tasks.enqueue_import(conn.user_id, object_id)
                    imported += 1
                continue
            elif kind in ("contact.deletion", "deal.deletion"):
                remote_type = "contact" if kind.startswith("contact") else "deal"
                link = _link_remote(db, conn.user_id, "hubspot", remote_type, object_id)
                if link is not None:
                    db.delete(link)
                    done = True
            applied += int(done)
            ignored += int(not done)
    db.commit()
    return {"applied": applied, "ignored": ignored, "imports_queued": imported}


def import_hubspot_deal(db: Session, user_id, remote_deal_id: str) -> str:
    """A deal created in HubSpot on a contact Vectorae linked becomes a local
    deal (source "hubspot"). A deal on a contact Vectorae never pushed is
    not ours to import."""
    conn = get_connection(db, user_id, "hubspot")
    if conn is None:
        return "not_connected"
    if _link_remote(db, user_id, "hubspot", "deal", remote_deal_id) is not None:
        return "exists"
    data = hubspot_client(db, conn).get_deal(remote_deal_id)
    props = data.get("properties") or {}
    contacts = (((data.get("associations") or {}).get("contacts") or {}).get("results") or [])
    lead = None
    for item in contacts:
        link = _link_remote(db, user_id, "hubspot", "contact", str(item.get("id")))
        if link is not None:
            lead = db.get(Lead, link.local_id)
            break
    if lead is None:
        return "no_linked_contact"
    stage = _hubspot_stage_in(conn, props.get("dealstage")) or DealStage.OPEN
    deal = Deal(user_id=_uuid(user_id), lead_id=lead.id, strategy_id=lead.strategy_id,
                name=(props.get("dealname") or lead.company or "HubSpot deal")[:200],
                value_cents=_cents(props.get("amount")) or 0,
                currency=(props.get("deal_currency_code") or "USD")[:3].upper(),
                stage=stage, close_date=_parse_date(props.get("closedate")),
                source="hubspot", external_ref=str(remote_deal_id))
    db.add(deal)
    db.flush()
    _save_link(db, conn, "deal", deal.id, "deal", str(remote_deal_id))
    if stage in (DealStage.WON, DealStage.LOST):
        apply_lead_status(db, lead, "closed_won" if stage is DealStage.WON else "closed_lost",
                          source="hubspot")
    db.commit()
    return "imported"


def handle_salesforce_payload(db: Session, payload: dict) -> dict:
    """Apply a (signature-verified) relay payload:
    {"org_id": "00D...", "records": [{"sobject": "Lead"|"Opportunity",
     "id": "...", "fields": {...}}]}"""
    org = str((payload or {}).get("org_id") or "")
    conns = [c for c in db.execute(select(CrmConnection).where(
        CrmConnection.provider == "salesforce", CrmConnection.status != "revoked")).scalars()
        if org and (c.account_id or "")[:15] == org[:15]]
    applied = ignored = 0
    for record in (payload or {}).get("records") or []:
        if not isinstance(record, dict):
            continue
        sobject, rid = record.get("sobject"), str(record.get("id") or "")
        fields = record.get("fields") if isinstance(record.get("fields"), dict) else {}
        for conn in conns:
            done = False
            if sobject == "Lead":
                link = _link_remote(db, conn.user_id, "salesforce", "lead", rid)
                done = bool(link) and _apply_salesforce_lead(db, conn, link, fields)
            elif sobject == "Opportunity":
                link = _link_remote(db, conn.user_id, "salesforce", "opportunity", rid)
                done = bool(link) and _apply_salesforce_opportunity(db, conn, link, fields)
            applied += int(done)
            ignored += int(not done)
    db.commit()
    return {"applied": applied, "ignored": ignored}


def pull(db: Session, conn: CrmConnection) -> dict:
    """Read every linked record back and apply what changed."""
    links = db.execute(select(CrmSyncLink).where(CrmSyncLink.user_id == conn.user_id,
                                                 CrmSyncLink.provider == conn.provider)
                       ).scalars().all()
    by_type: dict[str, dict[str, CrmSyncLink]] = {}
    for link in links:
        by_type.setdefault(link.remote_type, {})[link.remote_id] = link
    applied = 0
    if conn.provider == "hubspot" and by_type:
        client = hubspot_client(db, conn)
        for remote_type, object_type, props, fn in (
                ("contact", "contacts", ["hs_lead_status", "lifecyclestage"],
                 _apply_hubspot_contact),
                ("deal", "deals", ["dealstage", "amount", "closedate"], _apply_hubspot_deal)):
            ids = list(by_type.get(remote_type, {}))
            if ids:
                for row in client.batch_read(object_type, ids, props):
                    link = by_type[remote_type].get(str(row.get("id")))
                    if link is not None and fn(db, conn, link, row.get("properties") or {}):
                        applied += 1
    elif conn.provider == "salesforce" and by_type:
        client = salesforce_client(db, conn)
        for remote_type, sobject, fields, fn in (
                ("lead", "Lead", salesforce.LEAD_FIELDS, _apply_salesforce_lead),
                ("opportunity", "Opportunity", salesforce.OPPORTUNITY_FIELDS,
                 _apply_salesforce_opportunity)):
            ids = list(by_type.get(remote_type, {}))
            if ids:
                for row in client.get_many(sobject, ids, fields):
                    link = by_type[remote_type].get(str(row.get("Id")))
                    if link is not None and fn(db, conn, link, row):
                        applied += 1
    conn.last_pull_at = _now()
    db.commit()
    return {"pulled": sum(len(v) for v in by_type.values()), "applied": applied}


def sync_connection(db: Session, connection_id, *, full: bool = False) -> dict:
    conn = db.get(CrmConnection, _uuid(connection_id))
    if conn is None or conn.status == "revoked":
        return {"status": "skipped"}
    try:
        result = {**push_changed(db, conn, full=full), **pull(db, conn)}
        _ok(conn)
        db.commit()
        return {"status": "ok", **result}
    except _ERRORS as exc:
        db.rollback()
        conn = db.get(CrmConnection, _uuid(connection_id))
        _fail(conn, exc)
        db.commit()
        return {"status": "error", "error": str(exc)[:300]}


# --------------------------------------------------------------------------
# Event hub hook
# --------------------------------------------------------------------------


def on_event(db: Session, user_id, event: str, data: dict | None,
             payload: dict | None) -> int:
    """Called by event_bus.emit: queue an immediate push for the lead/deal the
    event is about. Returns how many pushes were queued."""
    from app.workers import crm_tasks  # noqa: PLC0415

    if event not in LEAD_EVENTS or not has_connection(db, user_id):
        return 0
    data, payload = data or {}, payload or {}
    lead = payload.get("lead") if isinstance(payload.get("lead"), dict) else {}
    lead_id = (data.get("leadId") or data.get("lead_id") or payload.get("lead_id")
               or lead.get("lead_id"))
    deal_id = data.get("dealId") or data.get("deal_id") or payload.get("deal_id")
    queued = 0
    if lead_id:
        queued += int(crm_tasks.enqueue_push("lead", user_id, lead_id))
    if deal_id:
        queued += int(crm_tasks.enqueue_push("deal", user_id, deal_id))
    return queued
