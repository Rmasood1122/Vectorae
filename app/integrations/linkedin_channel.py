"""LinkedInChannel — Feature Group 5's implementation of OutreachChannel.

Like WhatsAppChannel, it TRANSPORTS: every business rule (campaign state,
caps, send window, stop conditions) was decided by the sequence engine before
send() is called. Also like WhatsAppChannel, it re-checks suppression at the
last possible moment, because suppression is a legal obligation rather than a
scheduling preference and no future caller may be able to bypass it.

OutboundMessage.metadata carries the resolved action:
    {"action": "connect" | "message" | "inmail",
     "account_id": <unipile account id>, "provider_id": <lead's unipile id>,
     "chat_id": <existing conversation, if any>}

Replies do not come through fetch_replies in normal operation -- Unipile
pushes them to POST /webhooks/unipile. fetch_replies is implemented for
completeness (a manual poll of the conversations this user has open).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from app.core.exceptions import ComplianceError
from app.db.models import Lead
from app.integrations.outreach_base import (
    InboundMessage,
    OutboundMessage,
    OutreachChannel,
    SendResult,
    register_channel,
)
from app.integrations.unipile_linkedin import UnipileClient, UnipileError


@register_channel
class LinkedInChannel(OutreachChannel):
    channel = "linkedin"
    provider = "unipile_linkedin"

    def __init__(self, session: Session, client: UnipileClient):
        self.session = session
        self.client = client

    def _guard(self, message: OutboundMessage) -> None:
        from app.workers.lead_tasks import is_suppressed  # noqa: PLC0415

        # lead_id arrives as a STRING from the send path; see the same note in
        # phone_channel.py and app/workers/tasks.py::_pk.
        lead = self.session.get(Lead, uuid.UUID(str(message.lead_id))) \
            if message.lead_id else None
        if lead is not None and is_suppressed(self.session, lead.email, lead.phone,
                                              linkedin=lead.linkedin_url):
            raise ComplianceError("lead is suppressed", compliance_code="SUPPRESSED")

    def send(self, message: OutboundMessage) -> SendResult:
        meta = message.metadata or {}
        action = meta.get("action")
        account_id = meta.get("account_id")
        provider_id = meta.get("provider_id")
        if action not in ("connect", "message", "inmail") or not account_id or not provider_id:
            return SendResult(ok=False, permanent_failure=True,
                              error=f"incomplete LinkedIn send ({action}, {account_id}, {provider_id})")
        self._guard(message)
        try:
            if action == "connect":
                data = self.client.invite(provider_id, account_id, message.body)
                return SendResult(ok=True, raw=data,
                                  provider_message_id=str(data.get("invitation_id") or ""))
            if action == "message" and meta.get("chat_id"):
                data = self.client.send_in_chat(meta["chat_id"], message.body)
                return SendResult(ok=True, raw=data, thread_ref=meta["chat_id"],
                                  provider_message_id=str(data.get("message_id") or ""))
            data = self.client.start_chat(provider_id, account_id, message.body,
                                          inmail=action == "inmail",
                                          subject=message.subject)
            return SendResult(ok=True, raw=data, thread_ref=data.get("chat_id"),
                              provider_message_id=str(data.get("message_id") or ""))
        except UnipileError as exc:
            if exc.account_fault:
                self._mark_account(account_id, exc)
            # A LinkedIn limit or an account problem is retried later; only a
            # problem with this lead's request fails it for good.
            return SendResult(ok=False, error=str(exc), permanent_failure=exc.permanent,
                              raw={"status": exc.status, "type": exc.error_type})

    def _mark_account(self, unipile_account_id: str, exc: UnipileError) -> None:
        """Take a disconnected or restricted account out of rotation
        (linkedin_limits.pick_account uses only status "ok") until Unipile
        reports it OK again or the user reconnects it."""
        from sqlalchemy import select  # noqa: PLC0415

        from app.db.models import LinkedInAccount  # noqa: PLC0415

        account = self.session.execute(
            select(LinkedInAccount).where(LinkedInAccount.unipile_account_id == unipile_account_id)
        ).scalar_one_or_none()
        if account is not None:
            account.status = ("restricted" if exc.error_type == "errors/account_restricted"
                              else "credentials_needed")
            self.session.commit()

    def fetch_replies(self, since: datetime | None = None) -> list[InboundMessage]:
        from sqlalchemy import select  # noqa: PLC0415

        out: list[InboundMessage] = []
        chats = self.session.execute(
            select(Lead.linkedin_chat_id).where(Lead.linkedin_chat_id.isnot(None))
        ).scalars().all()
        for chat_id in chats:
            for item in self.client.chat_messages(chat_id):
                if item.get("is_sender"):
                    continue
                out.append(InboundMessage(
                    provider_message_id=str(item.get("id") or ""), thread_ref=chat_id,
                    from_address=str(item.get("sender_id") or ""), to_address=None,
                    body=str(item.get("text") or ""), raw=item))
        return out

    def status(self, provider_message_id: str) -> dict:
        return {"provider_message_id": provider_message_id}

    def health_check(self) -> bool:
        return self.client.health()
