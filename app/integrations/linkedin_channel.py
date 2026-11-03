"""AssistedLinkedInChannel: a LinkedIn sequence step becomes a task for the USER.

Founder decision 2026-11-02 (evals/vectorae, option a): Vectorae never acts
on LinkedIn. The sequence engine still decides WHEN a LinkedIn step is due
(campaign state, daily approval, suppression, send window) and renders the
claim-checked text; this channel then queues an L0 task
(app/services/linkedin_tasks.py) instead of transmitting anything. The user
approves the task, opens the prospect's profile, sends the text on LinkedIn
themselves and marks the task done, replied or skipped.

So a LinkedIn message in status "sent" means "handed to the user's LinkedIn
task queue" (sender_ref "linkedin:assisted", provider_message_id = task id),
not "delivered on LinkedIn". Nothing here makes a network call; the
linkedin-guard test (tests/test_linkedin_guard.py) fails if one appears.

OutboundMessage.metadata carries {"action": "connect" | "message" | "inmail"}.
InMail becomes a message task: the user can send it as an InMail if their own
account allows.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from app.core.exceptions import ComplianceError
from app.db.models import Lead, User
from app.integrations.outreach_base import (
    InboundMessage,
    OutboundMessage,
    OutreachChannel,
    SendResult,
    register_channel,
)

KIND_FOR_ACTION = {"connect": "connection_note", "message": "message", "inmail": "message"}


@register_channel
class LinkedInChannel(OutreachChannel):
    channel = "linkedin"
    provider = "assisted"

    def __init__(self, session: Session, owner: User | None):
        self.session = session
        self.owner = owner

    def _lead(self, message: OutboundMessage) -> Lead | None:
        # lead_id arrives as a STRING from the send path (see phone_channel.py).
        return (self.session.get(Lead, uuid.UUID(str(message.lead_id)))
                if message.lead_id else None)

    def send(self, message: OutboundMessage) -> SendResult:
        from app.services import linkedin_tasks  # noqa: PLC0415
        from app.workers.lead_tasks import is_suppressed  # noqa: PLC0415

        action = (message.metadata or {}).get("action")
        kind = KIND_FOR_ACTION.get(action or "")
        lead = self._lead(message)
        if kind is None or lead is None or self.owner is None:
            return SendResult(ok=False, permanent_failure=True,
                              error=f"incomplete LinkedIn task ({action})")
        # Suppression is a legal obligation: re-checked at the last moment
        # here as on every other channel, and again when the task is opened.
        if is_suppressed(self.session, lead.email, lead.phone, linkedin=lead.linkedin_url):
            raise ComplianceError("lead is suppressed", compliance_code="SUPPRESSED")
        try:
            task = linkedin_tasks.create(
                self.session, self.owner, source="sequence", kind=kind, text=message.body,
                profile_url=lead.linkedin_url or "", claim_checked=True, lead_id=lead.id)
        except linkedin_tasks.TaskError as exc:
            return SendResult(ok=False, permanent_failure=True, error=exc.detail)
        return SendResult(ok=True, provider_message_id=str(task.id),
                          raw={"linkedin_task_id": str(task.id), "kind": kind})

    def fetch_replies(self, since: datetime | None = None) -> list[InboundMessage]:
        # Replies happen on LinkedIn, in the user's own inbox. The user marks
        # a task "replied" (linkedin_tasks.complete), which stops the lead's
        # sequence like any reply.
        return []

    def status(self, provider_message_id: str) -> dict:
        return {"provider_message_id": provider_message_id, "provider": self.provider}

    def health_check(self) -> bool:
        return True
