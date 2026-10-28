"""Turn a transcript and a human's scrappy notes into a usable meeting record.

Goes through app/services/anthropic_client.py::get_client() like every other
model call in this codebase. Not because a second client would be hard to
write, but because retries, model selection, the truncation guard and the
single monkeypatch point the test suite hangs off all live there -- a
`anthropic.Anthropic(...)` here would quietly opt this feature out of all four.

WHAT THIS RETURNS, AND WHY IT NEVER RAISES ON A BAD ANSWER
`generate_meeting_summary` always returns the five-key dict its callers store,
with empty values on failure, and reports what happened in `ok`/`error`. The
alternative -- raising -- would mean a model hiccup at the end of a call loses
the summary AND leaves the meeting stuck in `in_progress`, because the endpoint
that generates the summary is the same one that completes the meeting. The
transcript and the user's own notes are already saved; a missing summary is a
button to press again, not lost work.

WHAT IT WILL NOT DO
It does not invent action items. The system prompt says so explicitly and the
sanitiser below drops anything that is not a non-empty string, because an
action item is a thing a human is about to be told they committed to. The same
rule the outreach personalizer follows for pain signals applies here: if it is
not in the transcript or the notes, it does not go in the summary.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from typing import Any

from app.config import settings
from app.services import prompt_safety
from app.services.anthropic_client import get_client

logger = logging.getLogger(__name__)

__all__ = ["EMPTY_SUMMARY", "generate_meeting_summary"]

_SENTIMENTS = ("positive", "neutral", "negative", "mixed")

# The shape every caller can rely on, whatever happened.
EMPTY_SUMMARY: dict[str, Any] = {
    "summary": "",
    "key_points": [],
    "action_items": [],
    "next_steps": [],
    "sentiment": "unknown",
}

_SYSTEM = (
    "You are Vectorae's meeting analyst. You read the transcript of one "
    "sales call plus the seller's own live notes, and you return a factual "
    "record of what was said. Respond with ONLY a JSON object, no prose and "
    "no markdown fences:\n"
    '{"summary": "...", "key_points": ["..."], "action_items": '
    '[{"text": "...", "owner": "us"|"client", "due": "YYYY-MM-DD"|null}], '
    '"next_steps": ["..."], "sentiment": "positive"|"neutral"|"negative"|'
    '"mixed"}\n'
    "RULES. `summary` is one short paragraph, under 120 words, in the past "
    "tense. `key_points` are at most six statements the call actually "
    "established. `action_items` are ONLY commitments someone made out loud; "
    "if nobody committed to anything, return an empty list -- do not invent "
    "plausible next actions to fill it. `owner` is 'us' for the seller and "
    "'client' for the prospect. `due` is a date only if one was stated. "
    "`sentiment` is the PROSPECT's disposition, not the seller's. Never "
    "state a fact, name, number or date that is not in the material given."
)

# Output contract (Phase 3 item 2). `sentiment` and `owner` are closed sets
# the sanitiser maps exactly; `done` is read if present (the UI writes it).
SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "key_points": {"type": "array", "items": {"type": "string"}},
        "action_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "owner": {"type": "string", "enum": ["us", "client"]},
                    "due": {"type": ["string", "null"]},
                    "done": {"type": "boolean"},
                },
                "required": ["text"],
                "additionalProperties": False,
            },
        },
        "next_steps": {"type": "array", "items": {"type": "string"}},
        "sentiment": {"type": "string", "enum": list(_SENTIMENTS)},
    },
    "required": ["summary"],
    "additionalProperties": False,
}

# F-AI-02: the transcript is what the PROSPECT (and anyone else on the call)
# said, and the prospect's name, company and title come from enrichment --
# outside text, delimited and escaped. The seller's own notes and the product
# brief are ours and stay as plain context.
_PROMPT = """{notice}

MEETING
- Title: {title}
- Platform: {platform}
- Scheduled duration: {scheduled_minutes} minutes
- Actual duration: {actual_minutes}

PROSPECT
- Name: {lead_name}
- Company: {lead_company}
- Title: {lead_title}
- Current CRM status: {lead_status}

WHAT IS BEING SOLD
{product}

SELLER'S LIVE NOTES (typed during the call — may be fragmentary):
{raw_notes}

TRANSCRIPT:
{transcript}

Return the JSON object now."""


def _clean_strings(value: Any, limit: int) -> list[str]:
    """Non-empty strings only, capped. Anything else in the list is dropped.

    The model is asked for a list of strings; it occasionally returns a list
    of objects, or a single string, or nulls among the entries. Coercing those
    into something renderable is how a bullet list ends up showing
    "[object Object]" to a user.
    """
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    out = [item.strip() for item in value
           if isinstance(item, str) and item.strip()]
    return out[:limit]


def _clean_action_items(value: Any, limit: int = 20) -> list[dict]:
    """Action items normalised to {text, owner, due, done}.

    `done` is added here, defaulting False, because the checkbox list in the
    UI writes it back into this same JSON column. Adding it at generation time
    means the frontend never has to distinguish "not done" from "this item
    predates the checkbox".
    """
    if not isinstance(value, list):
        return []
    items: list[dict] = []
    for entry in value:
        if isinstance(entry, str):
            entry = {"text": entry}
        if not isinstance(entry, dict):
            continue
        text = str(entry.get("text") or "").strip()
        if not text:
            continue
        owner = str(entry.get("owner") or "us").strip().lower()
        if owner not in ("us", "client"):
            owner = "us"
        due = entry.get("due")
        items.append({
            "text": text[:500],
            "owner": owner,
            "due": str(due)[:10] if due else None,
            "done": bool(entry.get("done", False)),
        })
        if len(items) >= limit:
            break
    return items


def _weekday_stated(due: str, material: str) -> bool:
    """True when `due` is a date whose weekday the transcript or notes name
    ('by Friday' resolved to that Friday's date). The model was given the
    meeting date, so a weekday is a stated deadline, not an invented one."""
    try:
        weekday = date.fromisoformat(str(due)[:10]).strftime("%A").lower()
    except ValueError:
        return False
    return re.search(rf"\b{weekday}\b", material or "", re.I) is not None


def _ground_action_items(items: list[dict], material: str) -> list[dict]:
    """T19: action items checked against what was actually said.

    Our items become CRM tasks (calendar_tasks.py). An item whose text states
    a figure, round, offer or deadline the transcript and notes lack is
    dropped; a due date neither contains is cleared (the item stays, with no
    date). Deterministic; logged."""
    from app.services import grounding  # noqa: PLC0415

    kept = []
    for item in items:
        found = grounding.problems(item["text"], material)
        if found:
            logger.warning("meeting summary: dropped action item %r: %s",
                           item["text"][:80], "; ".join(found))
            continue
        if (item["due"] and grounding.unsupported_numbers(item["due"], material)
                and not _weekday_stated(item["due"], material)):
            logger.warning("meeting summary: cleared due date %s on %r: not in the "
                           "transcript or notes", item["due"], item["text"][:80])
            item = {**item, "due": None}
        kept.append(item)
    return kept


def _duration_minutes(start, end) -> int | None:
    if not start or not end:
        return None
    return max(0, int((end - start).total_seconds() // 60))


def generate_meeting_summary(
    transcript: str | None,
    raw_notes: str | None,
    lead_context: dict | None = None,
) -> dict:
    """{summary, key_points, action_items, next_steps, sentiment} + ok/error.

    `lead_context` carries what the brief requires in the prompt: the lead's
    name and company, the product being sold, and the meeting's duration. It
    is a plain dict rather than ORM objects so this function stays callable
    from a Celery task, an endpoint and a test without a Session.
    """
    context = dict(lead_context or {})
    material = f"{transcript or ''}\n{raw_notes or ''}".strip()
    if not material:
        # Nothing to summarise is not an error, and calling the model with an
        # empty transcript is how you get a confidently hallucinated meeting.
        return {**EMPTY_SUMMARY, "ok": False,
                "error": "no transcript or notes to summarise"}

    actual = context.get("actual_minutes")
    prompt = _PROMPT.format(
        # The title of a booked meeting carries the invitee's self-typed name.
        title=prompt_safety.inline(context.get("title") or "Sales call"),
        platform=context.get("platform") or "unknown",
        scheduled_minutes=context.get("scheduled_minutes") or "unknown",
        actual_minutes=(f"{actual} minutes" if actual is not None
                        else "not recorded"),
        notice=prompt_safety.NOTICE,
        lead_name=prompt_safety.inline(context.get("lead_name") or "unknown"),
        lead_company=prompt_safety.inline(context.get("lead_company") or "unknown"),
        lead_title=prompt_safety.inline(context.get("lead_title") or "unknown"),
        lead_status=context.get("lead_status") or "unknown",
        product=context.get("product") or "(no product brief on file)",
        raw_notes=(raw_notes or "(none)")[:8000],
        transcript=prompt_safety.block(
            (transcript or "(no transcript captured)")[:60_000]),
    )

    try:
        data = get_client().complete_json(
            system=_SYSTEM, prompt=prompt,
            max_tokens=settings.meeting_ai_max_tokens,
            schema=SUMMARY_SCHEMA,
        )
    except Exception as exc:  # noqa: BLE001 — see the module docstring
        logger.warning("meeting summary generation failed: %s: %s",
                       type(exc).__name__, exc)
        return {**EMPTY_SUMMARY, "ok": False, "error": f"{type(exc).__name__}: {exc}"}

    if not isinstance(data, dict):
        return {**EMPTY_SUMMARY, "ok": False,
                "error": "model did not return a JSON object"}

    sentiment = str(data.get("sentiment") or "").strip().lower()
    return {
        "summary": str(data.get("summary") or "").strip()[:4000],
        "key_points": _clean_strings(data.get("key_points"), limit=6),
        "action_items": _ground_action_items(_clean_action_items(data.get("action_items")),
                                             material),
        "next_steps": _clean_strings(data.get("next_steps"), limit=6),
        # An unrecognised sentiment becomes "unknown" rather than being passed
        # through: the UI colours a chip from this value, and an unmapped one
        # would render as an uncoloured word that looks like a bug.
        "sentiment": sentiment if sentiment in _SENTIMENTS else "unknown",
        "ok": True,
        "error": None,
    }


def context_for_meeting(db, meeting) -> dict:
    """Build `lead_context` from a Meeting row. Tolerant of missing links.

    Lives here rather than in the router so the Celery path and the HTTP path
    build the same prompt context; two builders would drift and the summary
    would silently differ depending on which one ran.
    """
    from app.db.models import Product, Strategy  # noqa: PLC0415

    lead = meeting.lead
    product_brief = None
    if lead is not None:
        strategy = db.get(Strategy, lead.strategy_id)
        product = db.get(Product, strategy.product_id) if strategy else None
        if product is not None:
            product_brief = f"{product.name} — {product.description or ''}".strip(" —")

    return {
        "title": meeting.title,
        "platform": meeting.platform.value if meeting.platform else None,
        "scheduled_minutes": _duration_minutes(meeting.start_at, meeting.end_at),
        "actual_minutes": _duration_minutes(meeting.actual_start_at,
                                            meeting.actual_end_at),
        "lead_name": lead.full_name if lead else None,
        "lead_company": lead.company if lead else None,
        "lead_title": lead.title if lead else None,
        "lead_status": (lead.status.value if lead and lead.status else None),
        "product": product_brief,
    }
