"""WhatsApp Business Cloud API adapter — Milestone 4, Chunk 1.

Implements the SAME OutreachChannel interface as Gmail (project knowledge
section E: adapter pattern). Reuses the M2 plumbing (retry/backoff,
structured logging, circuit breaker) via BaseHttpAdapter.

COMPLIANCE DESIGN NOTE (why this adapter has guards while outreach_base.py
says "channels only transport messages"):
    The sequence engine remains the owner of *business* rules (caps, stop
    conditions, suppression at enroll time). But WhatsApp carries
    *channel-inherent Meta platform policy* that no caller may ever relax:
    cold contact is ONLY allowed via Meta-approved templates to opted-in
    numbers, and free-form text is ONLY allowed inside an open 24-hour
    customer-service window (project knowledge sections E and I). Those
    rules are enforced HERE, at the last possible moment before the API
    call, as defense-in-depth on top of the engine — so no future code
    path (a new task, a script, a REPL session) can structurally bypass
    them. There is deliberately NO bypass parameter. The suppression
    re-check is likewise repeated here for WhatsApp because suppression is
    a legal obligation, not a scheduling preference.

Every endpoint path, version, payload field and status value below was
checked against Meta's public WhatsApp Cloud API and Business Management API
docs on 2026-10-27 (developers.facebook.com/documentation/business-messaging/
whatsapp/...); tests/test_whatsapp_cloud_contract.py pins what was checked.
Never invent Meta API behavior — extend only after checking the docs.

Message kinds supported by send() — selected via OutboundMessage.metadata:
    metadata["kind"] == "template":
        metadata["template_name"]: str   (Meta-approved template name)
        metadata["language"]: str        (e.g. "en_US")
        metadata["components"]: list     (Meta components array, optional)
    metadata["kind"] == "text" (free-form):
        body is sent as-is — ONLY inside an open 24h service window.
Anything else raises ComplianceError.
"""

import logging
import re
from datetime import datetime

from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import Lead, Message, MessageStatus
from app.integrations.outreach_base import (
    InboundMessage,
    OutboundMessage,
    OutreachChannel,
    SendResult,
    register_channel,
)
from app.integrations.plumbing import BaseHttpAdapter
from app.workers.lead_tasks import is_suppressed

logger = logging.getLogger(__name__)

# The version comes from settings.whatsapp_api_version.
GRAPH_API_BASE = "https://graph.facebook.com"

# text.body limit (docs: messages/text-messages).
TEXT_BODY_MAX_CHARS = 4096
# Template list pages followed before giving up on a name lookup.
_TEMPLATE_PAGES_MAX = 20

# The canonical compliance exception. This module used to declare its OWN
# `class ComplianceError(Exception)`, which app.core.errors does not recognise:
# a WhatsApp compliance block therefore surfaced as a 500 instead of the
# documented 422 everywhere the adapter is reached through the real API. Each
# raise below now carries the machine-readable `compliance_code` the global
# handler exposes to clients, so no caller has to re-derive it from the prose.
from app.core.exceptions import ComplianceError  # noqa: E402


class WhatsAppNotConfigured(Exception):
    """Required WHATSAPP_* env vars are missing."""


# --------------------------------------------------------------------------
# Meta message status -> our MessageStatus
# --------------------------------------------------------------------------

# Webhook status values (docs: webhooks/reference/messages/status): sent,
# delivered, read, failed, played (first play of a voice message).
# "delivered", "read" and "played" have no dedicated MessageStatus (that enum tracks
# our send lifecycle); they are recorded as Outcome events by the webhook
# and returned verbatim in status() detail.
META_STATUS_MAP: dict[str, MessageStatus] = {
    "sent": MessageStatus.SENT,
    "delivered": MessageStatus.SENT,
    "read": MessageStatus.SENT,
    "played": MessageStatus.SENT,
    "failed": MessageStatus.FAILED,
}


def normalize_phone(phone: str) -> str:
    """Digits only. The form webhook `from`/`wa_id` values arrive in, so it is
    what inbound matching and suppression compare. Never send it: see to_e164."""
    return re.sub(r"[^\d]", "", phone or "")


def to_e164(phone: str) -> str | None:
    """'+<digits>' for a number stored in international format, else None.

    Meta (messages/send-messages): "If the plus sign is omitted, your business
    phone number's country calling code is prepended to the customer's phone
    number. This can result in undelivered or misdelivered messages." So a
    send always carries the '+', and a number with no '+' or '00' prefix is
    refused: its country cannot be known, and guessing could reach a stranger.
    """
    raw = re.sub(r"[\s\-().]", "", phone or "")
    if raw.startswith("00"):
        raw = "+" + raw[2:]
    digits = normalize_phone(raw)
    if not raw.startswith("+") or not digits:
        return None
    return "+" + digits


# --------------------------------------------------------------------------
# The channel
# --------------------------------------------------------------------------


@register_channel
class WhatsAppChannel(BaseHttpAdapter, OutreachChannel):
    channel = "whatsapp"
    provider = "whatsapp_cloud"
    base_url = GRAPH_API_BASE

    def __init__(self, session: Session, **kwargs):
        super().__init__(**kwargs)
        if not (settings.whatsapp_access_token and settings.whatsapp_phone_number_id):
            raise WhatsAppNotConfigured(
                "WHATSAPP_ACCESS_TOKEN and WHATSAPP_PHONE_NUMBER_ID must be set"
            )
        self.session = session
        self.phone_number_id = settings.whatsapp_phone_number_id
        self.api_version = settings.whatsapp_api_version

    def _auth(self) -> dict:
        return {"headers": {"Authorization": f"Bearer {settings.whatsapp_access_token}"}}

    # ---- 24h customer-service window (real since Chunk 3) -----------------
    def is_service_window_open(self, lead: Lead, now: datetime | None = None) -> bool:
        """Whether the 24-hour customer-service window is open for this
        lead: open for 24h from their most recent INBOUND message (each
        new inbound resets it); never open if they never messaged us;
        template sends do NOT open it. Computed live from the persisted
        inbound timestamp — never cached — so it cannot go stale between
        scheduling and sending."""
        from app.services.whatsapp_window import window_state_for_lead
        return window_state_for_lead(lead, now).open

    # ---- the hard compliance guard ----------------------------------------
    def _compliance_guard(self, message: OutboundMessage, kind: str, lead: Lead | None) -> None:
        """Raise ComplianceError unless this send is provably allowed.
        Re-checked at send time, every time. No bypass exists."""
        # Local import: services import models/engine; the adapter importing
        # services at call time avoids a module-load cycle.
        from app.services import whatsapp_optin as optin
        from app.services.whatsapp_templates import sendable_template

        # (a) suppression — same rule as Gmail: re-check immediately before
        # send. Checked against BOTH the target address and the lead's
        # stored email/phone, because formatting can differ until Chunk 3
        # adds a normalized-phone column.
        lead_email = lead.email if lead is not None else None
        lead_phone = lead.phone if lead is not None else None
        if is_suppressed(self.session, email=lead_email, phone=message.to_address) or (
            lead_phone and is_suppressed(self.session, phone=lead_phone)
        ):
            raise ComplianceError(
                f"recipient {message.to_address} is on the suppression list",
                rule="whatsapp_suppressed",
                compliance_code="SUPPRESSED",
                remediation="Remove the number from the suppression list only "
                            "if it was added in error.",
            )
        # (b) recorded opt-in required for ANY outbound WhatsApp message —
        #     read from the append-only whatsapp_optins audit trail
        #     (Chunk 2), with the Lead boolean as a required-consistent
        #     cache: BOTH must agree, so a manually flipped flag with no
        #     audit row still blocks.
        if lead is None:
            raise ComplianceError(
                "message has no lead — WhatsApp sends require a lead with "
                "a recorded opt-in",
                rule="whatsapp_no_optin",
                compliance_code="WHATSAPP_NO_OPTIN",
                remediation="Attach the message to a lead that has a recorded "
                            "opt-in.",
            )
        from app.db.models import OptInStatus  # local: avoid import churn
        audit_status = optin.current_status(self.session, lead.id)
        if audit_status is not OptInStatus.OPTED_IN or not lead.whatsapp_opted_in:
            raise ComplianceError(
                "no recorded WhatsApp opt-in for this lead — cold WhatsApp "
                "contact requires opt-in + an approved template (Meta "
                "policy, project knowledge sections E and I). Compliant "
                "fix: send them the hosted opt-in page link "
                "(/optin/whatsapp/{token}, e.g. from an email footer) or "
                "record real consent evidence via the opt-in API."
                ,
                rule="whatsapp_no_optin",
                compliance_code="WHATSAPP_NO_OPTIN",
            )
        # (c) template sends require the template to be APPROVED in OUR
        #     db — a template name passed in manually cannot skip review.
        if kind == "template":
            meta = message.metadata or {}
            template_name = meta.get("template_name") or ""
            language = meta.get("language") or ""
            if sendable_template(self.session, template_name, language) is None:
                raise ComplianceError(
                    f"template {template_name!r} ({language}) is not "
                    "APPROVED in the template registry — create it via "
                    "POST /whatsapp/templates, submit it for Meta review, "
                    "and sync until approved. Unapproved templates are "
                    "never sendable.",
                    rule="whatsapp_template_not_approved",
                    compliance_code="WHATSAPP_TEMPLATE_NOT_APPROVED",
                )
        # (d) free-form text only inside an open 24h customer-service window.
        if kind == "text" and not self.is_service_window_open(lead):
            raise ComplianceError(
                "free-form WhatsApp text is only allowed inside an open "
                "24-hour customer-service window (opened only by the lead "
                "messaging you; it expires 24h after their last inbound "
                "message). Your only options: (a) send an approved template "
                "to this opted-in number, or (b) wait for the lead to "
                "message first."
                ,
                rule="whatsapp_window_closed",
                compliance_code="WHATSAPP_WINDOW_CLOSED",
            )

    # ---- sending -----------------------------------------------------------
    def send(self, message: OutboundMessage) -> SendResult:
        """Send one template or (window-gated) free-form text message.

        ComplianceError is raised (not returned) so callers can never
        mistake a blocked send for a transient provider failure.
        """
        kind = (message.metadata or {}).get("kind")
        if kind not in ("template", "text"):
            raise ComplianceError(
                f"unsupported WhatsApp message kind {kind!r} — "
                "must be 'template' or 'text'",
                rule="whatsapp_bad_kind",
                compliance_code="WHATSAPP_BLOCKED",
            )

        lead = self.session.get(Lead, message.lead_id) if message.lead_id else None
        self._compliance_guard(message, kind, lead)

        to = to_e164(message.to_address)
        if to is None:
            raise ComplianceError(
                f"recipient {message.to_address!r} is not in international "
                "format; without a country code Meta prepends our own "
                "country's code, which can reach the wrong person",
                rule="whatsapp_phone_not_international",
                compliance_code="WHATSAPP_BLOCKED",
                remediation="Store the number in international format, "
                            "e.g. +14155550123.",
            )
        if kind == "text" and len(message.body or "") > TEXT_BODY_MAX_CHARS:
            return SendResult(
                ok=False, permanent_failure=True,
                error=f"WhatsApp text body exceeds {TEXT_BODY_MAX_CHARS} characters",
            )
        payload: dict = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
        }
        if kind == "template":
            meta = message.metadata
            template_name = meta.get("template_name")
            language = meta.get("language")
            if not template_name or not language:
                raise ComplianceError(
                    "template sends require metadata.template_name and "
                    "metadata.language",
                    rule="whatsapp_bad_template_metadata",
                    compliance_code="WHATSAPP_BLOCKED",
                )
            payload["type"] = "template"
            payload["template"] = {
                "name": template_name,
                "language": {"code": language},
            }
            if meta.get("components"):
                # {type: header|body|button, parameters: [...]}; built by the caller.
                payload["template"]["components"] = meta["components"]
        else:  # kind == "text" — guard above already proved the window is open
            payload["type"] = "text"
            payload["text"] = {"body": message.body, "preview_url": False}

        endpoint = f"/{self.api_version}/{self.phone_number_id}/messages"
        try:
            data = self.call("POST", endpoint, json_body=payload)
        except ComplianceError:
            raise
        except Exception as exc:
            status_code = getattr(exc, "status", None)
            return SendResult(
                ok=False,
                error=str(exc),
                # 400 = malformed request / bad number; 403 = permission —
                # neither will succeed on retry.
                permanent_failure=status_code in (400, 403),
                raw={"status": status_code},
            )

        # {"messaging_product", "contacts": [...], "messages": [{"id": "wamid..."}]}
        provider_id = None
        messages = data.get("messages") or []
        if messages:
            provider_id = messages[0].get("id")
        return SendResult(
            ok=True,
            provider_message_id=provider_id,
            # WhatsApp "thread" == the recipient number, in the digits-only
            # form inbound webhooks carry.
            thread_ref=normalize_phone(to),
            raw=data,
        )

    # ---- inbound -------------------------------------------------------------
    def fetch_replies(self, since: datetime | None = None) -> list[InboundMessage]:
        """WhatsApp Cloud API delivers inbound messages ONLY via webhooks —
        there is no supported polling endpoint for message history.

        Inbound handling therefore lives in app/api/webhooks_whatsapp.py.
        This method exists to satisfy the interface and always returns [];
        the reply-polling beat task must simply never learn anything new
        from this channel (it is not an error).
        """
        return []

    # ---- status ---------------------------------------------------------------
    def status(self, provider_message_id: str) -> dict:
        """Meta pushes delivery status via webhooks; there is no documented
        GET-status-by-message-id endpoint to poll ("You receive delivery
        status via messages webhooks instead", messages/send-messages).

        So this returns the status we have PERSISTED from webhook events —
        which is the provider-side truth as last reported by Meta — rather
        than fabricating a Graph API call that does not exist.
        """
        row = (
            self.session.query(Message)
            .filter_by(provider_message_id=provider_message_id)
            .one_or_none()
        )
        if row is None:
            return {"known": False}
        return {
            "known": True,
            "status": row.status.value,
            "sent_at": row.sent_at.isoformat() if row.sent_at else None,
            "error": row.error,
        }

    # ---- Business Management API: template review (Chunk 2) ----------------
    def submit_template(self, *, name: str, language: str, category: str,
                        body: str, examples: list[str] | None = None) -> dict:
        """Submit a template for Meta review under our WABA:
        POST /{version}/{waba_id}/message_templates, which returns
        {id, status, category}. Meta requires an example value for each body
        parameter ("example": {"body_text": [[...]]}, templates/components);
        `examples` gives them in {{1}}..{{n}} order."""
        if not settings.whatsapp_business_account_id:
            raise WhatsAppNotConfigured(
                "WHATSAPP_BUSINESS_ACCOUNT_ID must be set to manage templates"
            )
        placeholders = len(set(re.findall(r"\{\{(\d+)\}\}", body)))
        examples = list(examples or [])
        if len(examples) != placeholders:
            raise ValueError(
                f"template body has {placeholders} placeholder(s) but "
                f"{len(examples)} example value(s); Meta needs one example each"
            )
        component: dict = {"type": "BODY", "text": body}
        if placeholders:
            component["example"] = {"body_text": [examples]}
        payload = {
            "name": name,
            "language": language,
            "category": category.upper(),  # MARKETING / UTILITY / AUTHENTICATION
            "components": [component],
        }
        endpoint = (
            f"/{self.api_version}/{settings.whatsapp_business_account_id}"
            "/message_templates"
        )
        return self.call("POST", endpoint, json_body=payload)

    def fetch_template_status(self, *, name: str, language: str,
                              meta_template_id: str | None = None) -> dict | None:
        """Current review status of one template from Meta.

        With its id: GET /{version}/{template_id}. Without: page through
        GET /{version}/{waba_id}/message_templates (data + paging.cursors
        .after) until the name and language match. `name` is sent as a
        filter hint; Meta's reference does not list it, so every row is
        still matched here."""
        if not settings.whatsapp_business_account_id:
            raise WhatsAppNotConfigured(
                "WHATSAPP_BUSINESS_ACCOUNT_ID must be set to manage templates"
            )
        fields = "id,name,language,status,rejected_reason"
        if meta_template_id:
            return self.call("GET", f"/{self.api_version}/{meta_template_id}",
                             params={"fields": fields}) or None
        endpoint = (
            f"/{self.api_version}/{settings.whatsapp_business_account_id}"
            "/message_templates"
        )
        params = {"name": name, "fields": fields}
        for _ in range(_TEMPLATE_PAGES_MAX):
            data = self.call("GET", endpoint, params=params)
            for row in data.get("data") or []:
                if row.get("name") == name and row.get("language") == language:
                    return row
            paging = data.get("paging") or {}
            after = (paging.get("cursors") or {}).get("after")
            if not paging.get("next") or not after:
                return None
            params = {**params, "after": after}
        return None

    # ---- health -----------------------------------------------------------------
    def health_check(self) -> bool:
        """Cheap auth + connectivity check: read our own phone number object
        (GET /{version}/{phone_number_id})."""
        if self.breaker.is_open():
            return False
        try:
            self.call("GET", f"/{self.api_version}/{self.phone_number_id}",
                      params={"fields": "id"})
            return True
        except Exception:
            logger.warning("whatsapp health check failed", exc_info=True)
            return False
