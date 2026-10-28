"""Outreach channel foundation — the common interface for every channel.

Gmail implements this in M3; WhatsApp implements the SAME interface in M4
(project knowledge section E: adapter pattern — send/receive/status behind
one interface). Nothing in this file may be channel-specific: `subject`
is optional (WhatsApp has none), addresses are plain strings (email or
phone), and provider payloads live in `raw`/`metadata` dicts.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import ClassVar


# --------------------------------------------------------------------------
# Channel-agnostic message types
# --------------------------------------------------------------------------


@dataclass
class OutboundMessage:
    """A fully rendered message ready to send on some channel."""

    message_id: str                 # our messages.id (uuid str) — idempotency key
    lead_id: str
    to_address: str                 # email address or phone number
    body: str                       # final rendered body (persisted before send)
    subject: str | None = None      # email-only; None for chat channels
    headers: dict = field(default_factory=dict)   # e.g. List-Unsubscribe (email)
    thread_ref: str | None = None   # provider thread id for follow-ups
    metadata: dict = field(default_factory=dict)


@dataclass
class SendResult:
    ok: bool
    provider_message_id: str | None = None
    thread_ref: str | None = None
    error: str | None = None
    permanent_failure: bool = False  # True => do not retry (e.g. invalid address)
    # Email only: the sent message's own Message-ID / References (RFC 5322
    # 3.6.4), so the next email in the conversation can reply to it.
    rfc_message_id: str | None = None
    rfc_references: str | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class InboundMessage:
    """A message received on a channel (reply, bounce notice, etc.)."""

    provider_message_id: str
    thread_ref: str | None
    from_address: str
    to_address: str | None
    body: str
    subject: str | None = None
    received_at: datetime | None = None
    raw: dict = field(default_factory=dict)
    # Email only (RFC 5322 section 3.6.4): the message's own Message-ID and
    # its References chain, already sanitised (angle-bracketed ids only, no
    # CR/LF -- see email_threading.py). A reply sets In-Reply-To and
    # References from these so it threads in every mail client, not only in
    # Gmail (whose threadId means nothing to anyone else).
    rfc_message_id: str | None = None
    rfc_references: str | None = None


# --------------------------------------------------------------------------
# The interface
# --------------------------------------------------------------------------


class OutreachChannel(ABC):
    """One outreach channel (gmail, whatsapp, ...).

    Implementations MUST NOT enforce business rules (suppression, caps,
    stop conditions) — those live in the sequence engine so no channel can
    accidentally bypass them. Channels only transport messages.
    """

    channel: ClassVar[str]   # logical channel: "email", "whatsapp", ...
    provider: ClassVar[str]  # concrete provider key: "gmail", "whatsapp_cloud", ...

    @abstractmethod
    def send(self, message: OutboundMessage) -> SendResult:
        """Send one message. Must be safe to call in a Celery task."""

    @abstractmethod
    def fetch_replies(self, since: datetime | None = None) -> list[InboundMessage]:
        """Fetch new inbound messages for the connected account."""

    @abstractmethod
    def status(self, provider_message_id: str) -> dict:
        """Provider-side delivery status of a sent message."""

    @abstractmethod
    def health_check(self) -> bool:
        """Cheap call proving auth + connectivity."""


# --------------------------------------------------------------------------
# Registry — channels plug in without touching core logic
# --------------------------------------------------------------------------

_CHANNELS: dict[str, type[OutreachChannel]] = {}


def register_channel(cls: type[OutreachChannel]) -> type[OutreachChannel]:
    key = getattr(cls, "provider", None)
    if not key:
        raise ValueError(f"{cls.__name__} must define a `provider` key")
    if key in _CHANNELS:
        raise ValueError(f"outreach channel '{key}' registered twice")
    _CHANNELS[key] = cls
    return cls


def get_channel(provider: str, **kwargs) -> OutreachChannel:
    try:
        return _CHANNELS[provider](**kwargs)
    except KeyError:
        raise KeyError(
            f"unknown outreach channel '{provider}' — registered: {sorted(_CHANNELS)}"
        ) from None


def registered_channels() -> list[str]:
    return sorted(_CHANNELS)
