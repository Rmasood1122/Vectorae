"""Feature Group 5 — the LinkedIn channel's decisions and copy.

The send path (outreach_tasks.send_message_impl) owns WHEN a LinkedIn message
may go (campaign state, suppression, send window, daily caps). This module
answers the LinkedIn-specific questions it asks along the way:

  refresh_profile   who is this person on LinkedIn right now -- Unipile id,
                    Premium, already connected, request already pending
  resolve_action    connect, message or InMail for THIS step and THIS lead
  render            the copy, within LinkedIn's limits, in the sender's voice
  after_send        record the relationship the send created

RESOLVING `auto` (the default for LinkedIn steps)
  already connected                          -> message
  connection request still pending           -> WAIT (the step is skipped;
                                                the sequence continues)
  Premium lead (Open Profile)                -> InMail
  otherwise                                  -> connection request
An explicit `connect` on a connected lead becomes `message`; an explicit
`message` to someone not connected waits. InMail with no InMail-capable
account available falls back to a connection request in the send path.

NO UNSUBSCRIBE FOOTER on LinkedIn: a link-bearing footer in a connection note
is spam by LinkedIn's standards and would get the account restricted. The
opt-out path is the reply itself -- a reply classified as an unsubscribe
request suppresses the profile, the email and the phone, exactly as it does on
the other channels.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Lead, LinkedInAccount, Strategy
from app.integrations.linkedin_posts import public_identifier
from app.integrations.unipile_linkedin import CONNECTION_NOTE_MAX, UnipileClient
from app.services import anthropic_client, prompt_safety

logger = logging.getLogger(__name__)

WAIT = "wait_for_connection"
ACTIONS = ("auto", "connect", "message", "inmail")
MESSAGE_MAX = 1500

SYSTEM = (
    "You are Victorae's LinkedIn message writer. You write ONE LinkedIn "
    "touch for one prospect, following the step brief and the messaging "
    "playbook. Respond with ONLY a JSON object: {\"text\": \"...\", "
    "\"subject\": \"...\"}. RULES. Plain text, no links unless the brief "
    "asks for one, no hashtags, no emojis, no placeholders. A CONNECTION "
    "NOTE is at most 280 characters and never pitches -- it gives one "
    "genuine reason to connect. A MESSAGE is under 120 words and ends with "
    "one easy question. An INMAIL has a short subject (under 8 words) and a "
    "body under 150 words. Never invent facts about the prospect."
)

# Phase 3 item 2: only an InMail uses the subject, so it is optional.
LINKEDIN_MESSAGE_SCHEMA = {
    "type": "object",
    "properties": {"text": {"type": "string"}, "subject": {"type": "string"}},
    "required": ["text"],
    "additionalProperties": False,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_profile(url_or_slug: str | None) -> str | None:
    if not url_or_slug:
        return None
    slug = public_identifier(url_or_slug) or url_or_slug
    return slug.strip().strip("/").lower()[:200] or None


def client_for(session: Session, user_id) -> UnipileClient | None:
    from app.integrations import unipile_linkedin  # noqa: PLC0415

    return unipile_linkedin.get_client(session, user_id)


def active_accounts(session: Session, user_id) -> list[LinkedInAccount]:
    return list(session.execute(
        select(LinkedInAccount).where(LinkedInAccount.user_id == user_id,
                                      LinkedInAccount.is_active.is_(True))
    ).scalars().all())


def relationship_account(session: Session, lead: Lead) -> LinkedInAccount | None:
    return session.get(LinkedInAccount, lead.linkedin_account_id) \
        if lead.linkedin_account_id else None


def refresh_profile(session: Session, client: UnipileClient, lead: Lead, user_id) -> None:
    """Look the lead up through the relationship account (or any active one).
    Raises on a provider error: the send cannot proceed without a provider id,
    and the task's retry is the right response to a transient failure."""
    owner_account = relationship_account(session, lead)
    accounts = [owner_account] if owner_account else active_accounts(session, user_id)
    if not accounts:
        return
    identifier = lead.linkedin_provider_id or public_identifier(lead.linkedin_url)
    if not identifier:
        raise ValueError("lead has no usable LinkedIn profile URL")
    profile = client.profile(identifier, accounts[0].unipile_account_id)
    lead.linkedin_provider_id = profile["provider_id"]
    lead.linkedin_is_premium = profile["is_premium"]
    if profile["is_connected"]:
        if lead.linkedin_connection_status != "connected":
            lead.linkedin_connection_status = "connected"
            lead.linkedin_connected_at = lead.linkedin_connected_at or _now()
    elif profile["invitation_pending"] and lead.linkedin_connection_status is None:
        lead.linkedin_connection_status = "pending"
    session.commit()


def resolve_action(configured: str | None, lead: Lead) -> str:
    status = lead.linkedin_connection_status
    configured = (configured or "auto").lower()
    if configured == "inmail":
        return "inmail"
    if configured == "message":
        return "message" if status == "connected" else WAIT
    if configured == "connect":
        if status == "connected":
            return "message"
        return WAIT if status == "pending" else "connect"
    # auto
    if status == "connected":
        return "message"
    if status == "pending":
        return WAIT
    if lead.linkedin_is_premium:
        return "inmail"
    return "connect"


def _trim(text: str, limit: int) -> str:
    text = " ".join(text.split()) if limit <= CONNECTION_NOTE_MAX else text.strip()
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit(" ", 1)[0]
    return cut.rstrip(",;:") + "…"


def render(session: Session, strategy: Strategy, lead: Lead, step, action: str) -> dict:
    """{"text", "subject"?} for one LinkedIn touch."""
    from app.services import personalization_context, style_profile  # noqa: PLC0415
    from app.services.message_personalization import (  # noqa: PLC0415
        _host_and_path,
        _own_domains,
        _playbook,
        links_in,
        strip_foreign_links,
    )

    context, _ = personalization_context.prompt_block(lead)
    # prompt_block leads with the NOTICE when it has content; this prompt
    # states it once at the top, so drop the repeat.
    context = context.replace(f"{prompt_safety.NOTICE}\n\n", "", 1)
    kind = {"connect": "CONNECTION NOTE", "message": "MESSAGE", "inmail": "INMAIL"}[action]
    # F-AI-02: the prospect's name, title and company are scraped from a
    # profile they control, so the whole PROSPECT line is one delimited,
    # escaped value folded onto a single line. The step brief and playbook
    # are OURS and stay unwrapped; `context` is already delimited by
    # personalization_context.prompt_block.
    prompt = "\n".join([
        prompt_safety.NOTICE,
        f"WRITE A LINKEDIN {kind}.",
        f"STEP BRIEF: {step.template}",
        f"MESSAGING PLAYBOOK:\n{_playbook(session, strategy)[:6000]}",
        prompt_safety.inline(
            f"PROSPECT: {lead.full_name or 'unknown'} — {lead.title or ''}"
            f" at {lead.company or ''}"),
        context,
        "Return the JSON object now.",
    ])
    data = anthropic_client.get_client().complete_json(
        system=SYSTEM + style_profile.suffix_for_strategy(session, strategy),
        prompt=prompt, max_tokens=1024, schema=LINKEDIN_MESSAGE_SCHEMA)
    text = str((data or {}).get("text") or "").strip()
    # The prompt's "no links" rule is not a control. Keep only links to the
    # sender's own domains or ones the step brief (ours) names; a link an
    # injected profile or post asked for is removed, as in email bodies.
    allowed = _own_domains(session, strategy) | {
        _host_and_path(u)[0] for u in links_in(step.template or "")}
    text = strip_foreign_links(text, None, {h for h in allowed if h})
    if not text:
        raise ValueError("LinkedIn writer returned an empty message")
    limit = CONNECTION_NOTE_MAX if action == "connect" else MESSAGE_MAX
    out = {"text": _trim(text, limit)}
    if action == "inmail":
        out["subject"] = str((data or {}).get("subject") or "Quick question").strip()[:200]
    return out


def after_send(session: Session, lead: Lead, account: LinkedInAccount, action: str,
               result, now: datetime | None = None) -> None:
    """Record the relationship this send created. Caller commits."""
    now = now or _now()
    lead.linkedin_account_id = account.id
    account.last_used_at = now
    if action == "connect":
        lead.linkedin_connection_status = "pending"
        lead.linkedin_invited_at = now
    elif result.thread_ref:
        lead.linkedin_chat_id = result.thread_ref
    if action == "inmail":
        account.inmail_sent_total = (account.inmail_sent_total or 0) + 1
        if account.inmail_credits is not None:
            account.inmail_credits = max(0, account.inmail_credits - 1)


def lead_state(lead: Lead) -> dict:
    return {
        "linkedin_url": lead.linkedin_url,
        "provider_id": lead.linkedin_provider_id,
        "is_premium": lead.linkedin_is_premium,
        "connection_status": lead.linkedin_connection_status,
        "invited_at": lead.linkedin_invited_at.isoformat() if lead.linkedin_invited_at else None,
        "connected_at": (lead.linkedin_connected_at.isoformat()
                         if lead.linkedin_connected_at else None),
        "account_id": str(lead.linkedin_account_id) if lead.linkedin_account_id else None,
        "has_conversation": bool(lead.linkedin_chat_id),
    }
