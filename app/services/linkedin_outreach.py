"""Feature Group 5 — the LinkedIn channel's decisions and copy (L0: assisted).

Since 2026-11-02 (evals/vectorae, option a) Vectorae never acts on LinkedIn:
a LinkedIn step becomes a task the USER performs (app/integrations/
linkedin_channel.py -> app/services/linkedin_tasks.py). The send path
(outreach_tasks.send_message_impl) still owns WHEN a step is due. This module
answers:

  resolve_action    connect, message or InMail for THIS step and THIS lead,
                    from what the USER reported on earlier tasks
  render            the copy, within LinkedIn's limits, in the sender's voice
  after_task        record what the user reported (sent, replied)

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

from sqlalchemy.orm import Session

from app.db.models import Lead, Strategy
from app.services import anthropic_client, prompt_safety
from app.services.linkedin_profile import normalize_profile, public_identifier  # noqa: F401
from app.services.linkedin_tasks import CONNECTION_NOTE_MAX

logger = logging.getLogger(__name__)

WAIT = "wait_for_connection"
ACTIONS = ("auto", "connect", "message", "inmail")
MESSAGE_MAX = 1500

SYSTEM = (
    "You are Vectorae's LinkedIn message writer. You write ONE LinkedIn "
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


def after_task(session: Session, lead: Lead, kind: str, outcome: str,
               now: datetime | None = None) -> None:
    """Record what the user reported on a sequence task. Caller commits.

    connection_note done     -> request pending
    connection_note replied  -> connected (they accepted and wrote back)
    any task replied         -> the lead's live sequences stop, as on a reply
    """
    from app.services import sequence_engine  # noqa: PLC0415

    now = now or _now()
    if kind == "connection_note" and outcome == "done":
        if lead.linkedin_connection_status != "connected":
            lead.linkedin_connection_status = "pending"
            lead.linkedin_invited_at = now
    if outcome == "replied":
        lead.linkedin_connection_status = "connected"
        lead.linkedin_connected_at = lead.linkedin_connected_at or now
        sequence_engine.stop_live_enrollments(session, lead.id, "replied", now, commit=False)


def lead_state(lead: Lead) -> dict:
    return {
        "linkedin_url": lead.linkedin_url,
        "is_premium": lead.linkedin_is_premium,
        "connection_status": lead.linkedin_connection_status,
        "invited_at": lead.linkedin_invited_at.isoformat() if lead.linkedin_invited_at else None,
        "connected_at": (lead.linkedin_connected_at.isoformat()
                         if lead.linkedin_connected_at else None),
    }
