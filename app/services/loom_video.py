"""Feature Group 2 — personalised Loom video for high-likelihood leads.

WHAT THIS CAN AND CANNOT DO, STATED PLAINLY
Loom has no API that records a video on someone's behalf -- a person has to
record it. So "auto-generate a video" means what it honestly can:

  1. When a lead's ai_booking_likelihood rises above `loom_score_threshold`
     (default 75), a personalised RECORDING SCRIPT is written for the user
     (Claude, from the lead's profile, posts and company news) and the lead is
     marked `suggested`. The user gets one notification per scoring batch, not
     one per lead.
  2. The user records it in Loom and pastes the share link on the lead page.
     The lead is marked `recorded`.
  3. Sequence step 2 for that lead then includes a call to action linking to
     a Vectorae-hosted page -- /v?t=<token> -- pre-populated with the lead's
     first name and company, which embeds the video.

A step 2 that renders before a video exists gets NO video CTA. Sending a link
to a page with nothing on it would be worse than sending no link.

The page token is Fernet-encrypted {lead_id, purpose} (same scheme as the
unsubscribe token), so it cannot be forged or enumerated, and the public
endpoint behind it returns only a first name, a company name and the embed id.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import Lead, Strategy
from app.services import anthropic_client, crypto, prompt_safety

logger = logging.getLogger(__name__)

PURPOSE = "loom_video"
_LOOM_ID = re.compile(r"loom\.com/(?:share|embed)/([a-f0-9]{32})", re.I)

SYSTEM = (
    "You are Vectorae's video script writer. Write a script the seller will "
    "read while recording a 60-90 second personal Loom video for ONE "
    "prospect. Respond with ONLY a JSON object: {\"title\": \"...\", "
    "\"script\": \"...\", \"on_screen\": \"what to show while talking\"}. "
    "RULES: first person, conversational, under 180 words, open with the "
    "prospect's name and one specific detail from the material given, one "
    "clear ask at the end. Never invent facts about the prospect."
)

# Phase 3 item 2: on_screen is optional (the code stores "" without it).
LOOM_SCRIPT_SCHEMA = {
    "type": "object",
    "properties": {"title": {"type": "string"}, "script": {"type": "string"},
                   "on_screen": {"type": "string"}},
    "required": ["title", "script"],
    "additionalProperties": False,
}


DEFAULT_TITLE = "A quick video for you"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def loom_embed_id(share_url: str | None) -> str | None:
    match = _LOOM_ID.search(share_url or "")
    return match.group(1).lower() if match else None


def make_token(lead_id: uuid.UUID) -> str:
    return crypto.encrypt_json({"lead_id": str(lead_id), "purpose": PURPOSE})


def parse_token(token: str) -> uuid.UUID:
    data = crypto.decrypt_json(token)
    if data.get("purpose") != PURPOSE:
        raise ValueError("not a video page token")
    return uuid.UUID(data["lead_id"])


def page_url(lead: Lead) -> str:
    return f"{settings.frontend_url.rstrip('/')}/v?t={make_token(lead.id)}"


def state(lead: Lead) -> dict:
    return dict(lead.loom_video_json or {})


def is_recorded(lead: Lead) -> bool:
    s = state(lead)
    return s.get("status") == "recorded" and bool(s.get("embed_id"))


def write_script(session: Session, lead: Lead) -> dict:
    from app.services import claim_verification, meeting_prep  # noqa: PLC0415

    profile = meeting_prep.lead_profile(lead)
    posts = meeting_prep._linkedin_posts(lead)
    news = meeting_prep._company_news(lead)
    # F-AI-02: the profile is scraped, the posts were written by the prospect
    # and the news by third parties -- each is delimited and escaped.
    prompt = (f"{prompt_safety.NOTICE}\n\n"
              f"PROSPECT:\n{prompt_safety.block(str(profile))}\n"
              f"RECENT LINKEDIN POSTS:\n{prompt_safety.block(str(posts))}\n"
              f"RECENT COMPANY NEWS:\n{prompt_safety.block(str(news))}\n\n"
              "Return the JSON object now.")
    data = anthropic_client.get_client().complete_json(system=SYSTEM, prompt=prompt,
                                                       max_tokens=1200,
                                                       schema=LOOM_SCRIPT_SCHEMA)
    script = str((data or {}).get("script") or "").strip()
    if not script:
        raise ValueError("empty video script")
    title = str((data or {}).get("title") or "").strip()[:200]
    # T24: the seller reads the script on camera and the title is what the
    # prospect sees. Every claim about the prospect is checked against the
    # stored evidence, as on LinkedIn and email copy: an unsupported sentence
    # is stripped, an unsupported title replaced.
    strategy = session.get(Strategy, lead.strategy_id) if lead.strategy_id else None
    checked = claim_verification.enforce(session, strategy=strategy, lead=lead, message=None,
                                         channel="loom",
                                         fields={"subject": title, "script": script})
    script = (checked.get("script") or "").strip()
    title = (checked.get("subject") or "").strip()
    if title == claim_verification.GENERIC_SUBJECT:
        title = ""
    if not script:
        raise ValueError("empty video script")
    return {"title": title or DEFAULT_TITLE,
            "script": script[:3000],
            "on_screen": str(data.get("on_screen") or "").strip()[:500]}


def suggest(session: Session, lead: Lead, now: datetime | None = None) -> bool:
    """Mark a qualifying lead `suggested` with a script. Idempotent: a lead
    already suggested, recorded or skipped is left alone. Never raises."""
    from app.services import system_settings  # noqa: PLC0415

    try:
        threshold = system_settings.get(session, "loom_score_threshold")
        if lead.ai_booking_likelihood is None or lead.ai_booking_likelihood <= threshold:
            return False
        if state(lead).get("status") in ("suggested", "recorded", "skipped"):
            return False
        script = write_script(session, lead)
        lead.loom_video_json = {"status": "suggested", **script,
                                "suggested_at": (now or _now()).isoformat()}
        session.commit()
        return True
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        logger.warning("loom suggestion for lead %s failed: %s", lead.id, exc)
        return False


def record(session: Session, lead: Lead, share_url: str,
           now: datetime | None = None) -> dict:
    embed_id = loom_embed_id(share_url)
    if not embed_id:
        raise ValueError("that is not a Loom share link (https://www.loom.com/share/...)")
    lead.loom_video_json = {**state(lead), "status": "recorded",
                            "share_url": share_url.strip()[:500], "embed_id": embed_id,
                            "recorded_at": (now or _now()).isoformat()}
    session.commit()
    return lead.loom_video_json


def skip(session: Session, lead: Lead) -> dict:
    lead.loom_video_json = {**state(lead), "status": "skipped"}
    session.commit()
    return lead.loom_video_json


def suggest_for_leads(session: Session, leads: list[Lead]) -> int:
    """After a scoring batch: suggest videos and send ONE notification."""
    from app.services import event_bus, notifications  # noqa: PLC0415

    suggested = [lead for lead in leads if suggest(session, lead)]
    if suggested:
        owner = notifications.owner_of_lead(session, suggested[0])
        event_bus.emit(
            session, owner, "loom_requested",
            title=f"{len(suggested)} high-intent lead{'s' if len(suggested) != 1 else ''} "
                  f"ready for a personal video",
            body="Record a short Loom for each — the script is written for you.",
            deep_link=f"/leads/detail?id={suggested[0].id}&tab=overview",
            data={"count": str(len(suggested))},
        )
    return len(suggested)


def cta_for_step(lead: Lead, step_no: int) -> str | None:
    """The prompt line that puts the video into sequence step 2, or None."""
    if step_no != 2 or not is_recorded(lead):
        return None
    return (f"PERSONAL VIDEO: the seller recorded a short video for this lead. "
            f"Make it the call to action, naturally, in one sentence, with this "
            f"exact link: {page_url(lead)}")
