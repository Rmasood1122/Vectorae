"""The single gateway for every transactional email Vectorae sends.

Before Feature 1 this codebase had NO way to send a system email at all.
app/integrations/gmail.py sends on behalf of a *customer's* connected Gmail
account as part of outreach; it is not, and must not become, the transport for
"here is your verification link" — that mail has to go out before the user has
connected anything, and it must come from Vectorae's own domain.

So: one function, `send_email`, and four interchangeable transports selected by
the EMAIL_PROVIDER setting.

    resend   HTTPS POST to api.resend.com.        Production.
    smtp     Plain SMTP.                          Mailtrap sandbox locally,
                                                  any relay in production.
    console  Logs the whole message, sends none.  Local dev with no creds.
    memory   Appends to SENT_MESSAGES, sends none. The test suite asserts on it.

WHY THE PROVIDER IS EXPLICIT AND NOT INFERRED
A tempting design is "use Resend if RESEND_API_KEY is set, else log". That
fails silently in exactly the case that matters: a production deploy where the
key is missing or the variable name is misspelled keeps booting, keeps
answering health checks, and writes every user's verification link to a log
file nobody reads. Naming the transport means a misconfigured production
process raises at send time with a message that says which variable is wrong.

FAILURE POLICY
send_email raises EmailSendError on any transport failure. Callers decide
whether that is fatal. Signup deliberately does NOT let it be fatal — see
app/api/auth.py — because an account that was created and then 500'd because
the mail relay was briefly down is worse than an account that exists and needs
a resend click.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage

import httpx

from app.config import settings
from app.core.exceptions import ClientHunterError

logger = logging.getLogger(__name__)

__all__ = [
    "EmailSendError",
    "SENT_MESSAGES",
    "send_email",
    "reset_sent_messages",
]


class EmailSendError(ClientHunterError):
    """The message could not be handed to the transport. Can carry the
    provider's response text: never put in an API response as is."""

    user_safe = False


# In-process capture for EMAIL_PROVIDER=memory. Tests read this; nothing in
# production ever does. A module-level list is intentional — it must survive
# across the request boundary so a test can POST /auth/signup and then look.
SENT_MESSAGES: list[dict] = []


def reset_sent_messages() -> None:
    """Clear the memory transport. Call between tests."""
    SENT_MESSAGES.clear()


def _from_header() -> str:
    name = (settings.email_from_name or "").strip()
    addr = (settings.email_from or "").strip()
    if not addr:
        raise EmailSendError(
            "EMAIL_FROM is not set — there is no address to send from"
        )
    return f"{name} <{addr}>" if name else addr


# --------------------------------------------------------------------------
# Transports
# --------------------------------------------------------------------------


def _send_resend(to: str, subject: str, html: str, text: str) -> str:
    if not settings.resend_api_key:
        raise EmailSendError(
            "EMAIL_PROVIDER=resend but RESEND_API_KEY is empty. Set it in the "
            "environment (Render dashboard in production, .env locally)."
        )
    payload = {
        "from": _from_header(),
        "to": [to],
        "subject": subject,
        "html": html,
        "text": text,
    }
    try:
        resp = httpx.post(
            settings.resend_api_url,
            json=payload,
            headers={"Authorization": f"Bearer {settings.resend_api_key}"},
            timeout=settings.email_send_timeout_seconds,
        )
    except httpx.HTTPError as exc:
        raise EmailSendError(f"resend request failed: {exc}") from exc
    if resp.status_code >= 400:
        # The body carries Resend's own reason (unverified domain, bad key,
        # rate limit). Truncated because it goes into a log line, not a page.
        raise EmailSendError(
            f"resend rejected the message: HTTP {resp.status_code} "
            f"{resp.text[:300]}"
        )
    try:
        message_id = resp.json().get("id", "")
    except ValueError:
        message_id = ""
    return message_id


def _send_smtp(to: str, subject: str, html: str, text: str) -> str:
    if not settings.smtp_host:
        raise EmailSendError(
            "EMAIL_PROVIDER=smtp but SMTP_HOST is empty. For the Mailtrap "
            "sandbox set SMTP_HOST=sandbox.smtp.mailtrap.io and SMTP_PORT=2525."
        )
    from app.core.mime_policy import OUTBOUND_POLICY  # noqa: PLC0415

    message = EmailMessage(policy=OUTBOUND_POLICY)   # long headers go out raw
    message["From"] = _from_header()
    message["To"] = to
    message["Subject"] = subject
    message.set_content(text)
    message.add_alternative(html, subtype="html")
    try:
        with smtplib.SMTP(
            settings.smtp_host,
            settings.smtp_port,
            timeout=settings.email_send_timeout_seconds,
        ) as server:
            if settings.smtp_starttls:
                server.starttls(context=ssl.create_default_context())
            if settings.smtp_user:
                server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(message)
    except (smtplib.SMTPException, OSError) as exc:
        raise EmailSendError(f"smtp send failed: {exc}") from exc
    return ""


def _send_console(to: str, subject: str, html: str, text: str) -> str:
    logger.warning(
        "EMAIL NOT SENT (EMAIL_PROVIDER=console). to=%s subject=%s\n"
        "----- text body -----\n%s\n---------------------",
        to,
        subject,
        text,
    )
    return ""


def _send_memory(to: str, subject: str, html: str, text: str) -> str:
    SENT_MESSAGES.append(
        {"to": to, "subject": subject, "html": html, "text": text}
    )
    return f"memory-{len(SENT_MESSAGES)}"


_TRANSPORTS = {
    "resend": _send_resend,
    "smtp": _send_smtp,
    "console": _send_console,
    "memory": _send_memory,
}


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def send_email(*, to: str, subject: str, html: str, text: str) -> str:
    """Deliver one message. Returns the provider message id (may be empty).

    Raises EmailSendError if the configured transport rejects it, or if
    EMAIL_PROVIDER names a transport that does not exist — an unknown value is
    a configuration error, never a reason to quietly fall back to logging.
    """
    provider = (settings.email_provider or "").strip().lower()
    transport = _TRANSPORTS.get(provider)
    if transport is None:
        raise EmailSendError(
            f"EMAIL_PROVIDER={provider!r} is not a known transport. "
            f"Valid values: {', '.join(sorted(_TRANSPORTS))}."
        )
    message_id = transport(to, subject, html, text)
    logger.info(
        "email sent provider=%s to=%s subject=%s message_id=%s",
        provider,
        to,
        subject,
        message_id or "-",
    )
    return message_id
