"""PhoneChannel — Feature Group 6's implementation of OutreachChannel.

send() places ONE AI call through Vapi, falling back to ElevenLabs'
Conversational AI when Vapi is not configured or refuses the call. "Sent"
means the call was placed; what happened on it arrives later on the
provider's webhook (app/api/calls.py).

It re-checks suppression at the last moment (like WhatsApp and LinkedIn),
because a number on the do-not-contact list must never ring.

OutboundMessage.metadata carries everything the provider needs:
    {"call_id", "system_prompt", "first_message", "voicemail_text",
     "customer_name", "server_url", "server_secret",
     voice twin: "disclosure", "voicemail_disclosure", "owner_name",
     "voice_id", "voice_clone", "transfer_number"}

VOICE TWIN, THE LAST-MOMENT GUARD (docs/features/voice-twin.md): whatever the
metadata says, the first message and the voicemail are re-checked here to OPEN
with the AI disclosure and recording notice, and the system prompt to START
with the identity rules. An invalid or missing disclosure in the metadata is
replaced by the generic one -- a call never goes out without it.

ElevenLabs takes those words only as per-call overrides, which its agent
must allow (Security tab; off by default). So before an ElevenLabs call the
agent's override settings are read (voice_providers.require_overrides) and
the call is refused, permanently and before anything is dialled, unless the
opening line and the script (and the voice, for a cloned voice) can be set.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from app.core.exceptions import ComplianceError
from app.db.models import Lead
from app.integrations import voice_providers
from app.integrations.outreach_base import (
    InboundMessage,
    OutboundMessage,
    OutreachChannel,
    SendResult,
    register_channel,
)
from app.integrations.voice_providers import VoiceProviderError


@register_channel
class PhoneChannel(OutreachChannel):
    channel = "phone"
    provider = "ai_voice"

    def __init__(self, session: Session, vapi=None, eleven=None):
        self.session = session
        self.vapi = vapi
        self.eleven = eleven

    def _guard(self, message: OutboundMessage) -> None:
        from app.workers.lead_tasks import is_suppressed  # noqa: PLC0415

        # OutboundMessage.lead_id is a STRING (the send path builds it with
        # str(lead.id)); the Uuid column needs a UUID on SQLite. PostgreSQL
        # accepts the string, which is how this class of bug hides -- see
        # app/workers/tasks.py::_pk.
        lead = self.session.get(Lead, uuid.UUID(str(message.lead_id))) \
            if message.lead_id else None
        if lead is not None and is_suppressed(self.session, lead.email, lead.phone,
                                              linkedin=lead.linkedin_url):
            raise ComplianceError("lead is suppressed", compliance_code="SUPPRESSED")

    def _owner_names(self, message: OutboundMessage, meta: dict) -> tuple[str, ...]:
        """Every name the account owner goes by (H1): from the metadata AND
        from the database, so a call whose metadata lost them still filters
        "this is <owner>" out of what is said."""
        from app.services import notifications, voice_twin  # noqa: PLC0415

        names = list(meta.get("owner_names") or []) + [str(meta.get("owner_name") or "")]
        try:
            lead = self.session.get(Lead, uuid.UUID(str(message.lead_id))) \
                if message.lead_id else None
            owner = notifications.owner_of_lead(self.session, lead) if lead is not None else None
            if owner is not None:
                names += list(voice_twin.owner_names(self.session, owner))
        except Exception:  # noqa: BLE001 -- the metadata names still apply
            pass
        return tuple(n for n in names if n)

    @staticmethod
    def _disclosed(meta: dict, body: str | None, names: tuple[str, ...] = ()) -> dict:
        """Guard 1 + 2, enforced on the words that actually go to the provider."""
        from app.services import voice_twin  # noqa: PLC0415

        opening = meta.get("disclosure")
        if not voice_twin.valid_opening(opening):
            opening = voice_twin.GENERIC_OPENING
        vm_opening = meta.get("voicemail_disclosure")
        if not voice_twin.valid_voicemail_opening(vm_opening):
            vm_opening = voice_twin.GENERIC_VOICEMAIL_OPENING
        voicemail = meta.get("voicemail_text")
        owner = voice_twin.clean_name(meta.get("owner_name")) or "the seller"
        clone = bool(meta.get("voice_clone") and meta.get("voice_id"))
        return {
            "first_message": voice_twin.enforce_opening(meta.get("first_message") or body,
                                                        opening, names),
            "voicemail_message": (voice_twin.enforce_opening(voicemail, vm_opening, names)
                                  if voicemail else None),
            "system_prompt": voice_twin.ensure_identity_rules(
                meta.get("system_prompt") or "", owner, clone=clone,
                can_transfer=bool(meta.get("transfer_number"))),
            "voice_id": meta.get("voice_id") if clone else None,
            "voice_is_clone": clone,
            "transfer_number": meta.get("transfer_number") or None,
        }

    def send(self, message: OutboundMessage) -> SendResult:
        meta = message.metadata or {}
        self._guard(message)
        kwargs = dict(to_number=message.to_address, customer_name=meta.get("customer_name"),
                      server_url=meta.get("server_url") or "",
                      server_secret=meta.get("server_secret"),
                      metadata={"call_id": meta.get("call_id"), "lead_id": message.lead_id,
                                "message_id": message.message_id},
                      **self._disclosed(meta, message.body, self._owner_names(message, meta)))
        errors = []
        for provider in (self.vapi, self.eleven):
            if provider is None or (provider is self.eleven and not getattr(provider, "can_call", False)):
                continue
            try:
                if isinstance(provider, voice_providers.ElevenLabsClient):
                    voice_providers.require_overrides(
                        provider, voice_providers.overrides_needed(kwargs["voice_id"]))
                data = provider.create_call(**kwargs)
                return SendResult(ok=True, provider_message_id=data.get("id"),
                                  raw={"provider": provider.provider, **data})
            except VoiceProviderError as exc:
                errors.append(f"{provider.provider}: {exc}")
                if provider is self.vapi and self.eleven is not None and \
                        getattr(self.eleven, "can_call", False):
                    continue
                return SendResult(ok=False, error="; ".join(errors),
                                  permanent_failure=exc.permanent)
        return SendResult(ok=False, permanent_failure=True,
                          error="; ".join(errors) or "no voice provider configured")

    def fetch_replies(self, since: datetime | None = None) -> list[InboundMessage]:
        return []   # results arrive on the provider webhook

    def status(self, provider_message_id: str) -> dict:
        return {"provider_call_id": provider_message_id}

    def health_check(self) -> bool:
        return self.vapi is not None or (self.eleven is not None and self.eleven.can_call)
