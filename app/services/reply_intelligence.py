"""Feature 2 — reply intelligence engine.

Every inbound reply gets a second read by Claude that answers a question the
M3 classifier (app/services/reply_classification.py) deliberately does not:
not "how does the system route this?" but "what does the HUMAN do next?".

    BUYING_SIGNAL   interested, ready for the product conversation
                    -> line the enrollment up at step 3
    OBJECTION       a specific concern was raised
                    -> draft an answer to THAT concern
    NOT_NOW         timing, not rejection
                    -> come back on a date (30 days by default, editable)
    WRONG_PERSON    referred on, or confirmed not the buyer
                    -> draft a referral request

THE HUMAN IS NEVER BYPASSED. This module writes a DRAFT onto the reply row.
Nothing here sends anything, and nothing here restarts a stopped sequence: a
BUYING_SIGNAL moves the enrollment's `current_step` so the next step to render
is step 3, but leaves its STATUS exactly as the reply handler set it. A person
still has to act. That is the whole design -- an outbound system that answers
humans by itself is one bad classification away from an apology.

BANNED WORDS. The eight words in BANNED_WORDS are the ones that make a
prospect close the tab. A draft containing any of them is regenerated once
with the offenders named; if the second attempt still uses them, the draft
degrades to the category's neutral fallback rather than shipping copy that
reads like every other cold email in the inbox.
"""

import logging
import re
import uuid as uuid_module
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    InboundReply,
    Lead,
    Message,
    Sequence,
    SequenceEnrollment,
)
from app.services import prompt_safety
from app.services.anthropic_client import JUDGE_TEMPERATURE, get_client

logger = logging.getLogger(__name__)

CATEGORIES = ("BUYING_SIGNAL", "OBJECTION", "NOT_NOW", "WRONG_PERSON")

# Words that are never allowed in a generated draft.
BANNED_WORDS = ("solution", "platform", "synergy", "game-changer", "leverage",
                "excited", "seamless", "disruptive")

MAX_DRAFT_WORDS = 150
MAX_TOKENS = 1000

# NOT_NOW comes back in a month by default. The user can move it to 60 or 90
# through PATCH /crm/replies/{id}/intelligence -- which is why this is a
# starting point stored on a column, not a constant the UI has to mirror.
RESCHEDULE_DAYS = 30

# CONSENT GUARD (I1). A reply that asks to stop contact or to erase data is
# NEVER handed a sales draft and NEVER moves a sequence, whatever the model
# would say. Decided by rules before any model call (so it cannot be argued
# out of by the reply text, and costs nothing):
#   * routing class `unsubscribe_request` (reply_classification folds GDPR
#     erasure requests into it -- there is no separate erasure class);
#   * Feature 1 intent label `unsubscribe` (reply_intent);
#   * opt-out / erasure wording in the prospect's NEW text, the same detector
#     Reply Copilot uses (reply_copilot.opt_out_language), which covers a
#     reply whose routing class is not stored yet or was misread.
# Fail closed: any one of the three is enough.
OPT_OUT_ROUTING = frozenset({"unsubscribe_request"})
OPT_OUT_INTENTS = frozenset({"unsubscribe"})
# Stored as ai_next_action on an opted-out reply, so the CRM can tell "the
# prospect opted out, deliberately no draft" from "never classified" (both
# have category NULL).
OPT_OUT_NEXT_ACTION = "honor_opt_out"

# The step a BUYING_SIGNAL prepares. `current_step` records the last step
# SENT (app/services/sequence_engine.py::schedule_next_step), so lining the
# enrollment up at step 3 means setting it to 2.
BUYING_SIGNAL_STEP = 3

# The action recorded when the model does not name one itself.
DEFAULT_NEXT_ACTION = {
    "BUYING_SIGNAL": "send_step_3_product_intro",
    "OBJECTION": "send_objection_response",
    "NOT_NOW": "reschedule_followup",
    "WRONG_PERSON": "request_referral",
}

# Used when the model cannot produce a clean draft in two attempts. Short,
# specific to the category, and free of every banned word -- a human still
# edits it before it goes anywhere.
FALLBACK_DRAFT = {
    "BUYING_SIGNAL": (
        "Thanks for coming back to me. Happy to walk you through how this "
        "works for a team your size. Would a short call this week suit, or "
        "would you rather I put it in writing first?"
    ),
    "OBJECTION": (
        "That is a fair point and worth answering properly rather than in "
        "one line. Can I send you the short version of how we handle it, "
        "and you tell me if it holds up?"
    ),
    "NOT_NOW": (
        "Understood, and thanks for being straight about the timing. I will "
        "come back to you later in the year. Is there a month that tends to "
        "work better on your side?"
    ),
    "WRONG_PERSON": (
        "Thanks for telling me rather than leaving me guessing. Who owns this "
        "on your side? Happy to go to them directly and leave you out of it."
    ),
}

_SYSTEM = (
    "You are Vectorae's reply intelligence engine. You read ONE inbound "
    "reply to a cold outreach message and decide what the human sender does "
    "next. Respond with ONLY a JSON object, no prose and no markdown fences:\n"
    '{"category": "BUYING_SIGNAL|OBJECTION|NOT_NOW|WRONG_PERSON", '
    '"confidence": 0.0-1.0, "next_action": "short snake_case action", '
    '"draft_response": "the reply the sender will send"}\n'
    "CATEGORIES. BUYING_SIGNAL: interest, a question about how it works, a "
    "request for a call or pricing. OBJECTION: a specific concern -- price, "
    "timing of contract, existing supplier, scepticism. NOT_NOW: timing, not "
    "rejection ('ask me in Q2', 'we are mid-migration'). WRONG_PERSON: they "
    "are not the buyer, or they name someone else.\n"
    f"THE DRAFT. Under {MAX_DRAFT_WORDS} words. Plain text, no subject line, "
    "no signature, no placeholders in brackets. Answer what THEY actually "
    "said, quoting their own framing where it helps. One question at most. "
    "Never invent a fact, a statistic, a case study or a deadline.\n"
    "NEVER use any of these words: " + ", ".join(BANNED_WORDS) + "."
)

# Output contract (Phase 3 item 2): the four fields _clean reads; `category`
# is the closed set the prompt names and _clean maps.
REPLY_INTELLIGENCE_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": list(CATEGORIES)},
        "confidence": {"type": ["number", "string"]},
        "next_action": {"type": "string"},
        "draft_response": {"type": "string"},
    },
    "required": ["category", "confidence", "next_action", "draft_response"],
    "additionalProperties": False,
}

# F-AI-02: the reply is written by the prospect, and the lead's name, company
# and title come from scraped enrichment -- all outside text, so all delimited
# and escaped. The message WE sent is delimited too: a model wrote it from the
# prospect's own data, so it is context to read, never instructions.
_PROMPT = """{notice}

THE REPLY WE RECEIVED:
{reply_body}

WHO SENT IT:
- Name: {lead_name}
- Company: {company}
- Title: {title}

WHAT WE SENT THEM (sequence step {step_no}):
{original_message}

Classify this reply and write the sender's next message now."""


def find_banned_words(text: str) -> list[str]:
    """Every banned word present in `text`, lowercased, in BANNED_WORDS order.

    Matching is on word boundaries, so "platform" is caught but "platforms"
    inside a company name the prospect used is not silently reworded -- and
    "leverage" is caught without also flagging "lever".

    Returns a list (empty when the text is clean). Never raises.
    """
    lowered = (text or "").lower()
    return [word for word in BANNED_WORDS
            if re.search(rf"\b{re.escape(word)}\b", lowered)]


def _trim_words(text: str, limit: int = MAX_DRAFT_WORDS) -> str:
    words = (text or "").split()
    return text if len(words) <= limit else " ".join(words[:limit]).rstrip(",;:") + "."


def _original_message(db: Session, reply: InboundReply) -> tuple[str, int | None]:
    """(body of the message being replied to, its step number).

    Prefers the message the reply was actually matched to; falls back to the
    most recent thing sent to that lead, because a reply that arrived on a
    new thread is still a reply to the last touch.
    """
    message = db.get(Message, reply.message_id) if reply.message_id else None
    if message is None and reply.lead_id:
        message = db.execute(
            select(Message)
            .where(Message.lead_id == reply.lead_id, Message.body.isnot(None))
            .order_by(Message.step_no.desc())
        ).scalars().first()
    if message is None:
        return "(the original message is not on file)", None
    subject = (message.subject or "").strip()
    body = (message.body or "").strip()
    return (f"{subject}\n{body}".strip() or "(empty)")[:4000], message.step_no


def _ask(system: str, prompt: str) -> dict:
    return get_client().complete_json(system=system, prompt=prompt,
                                      max_tokens=MAX_TOKENS,
                                      temperature=JUDGE_TEMPERATURE,
                                      schema=REPLY_INTELLIGENCE_SCHEMA)


def _clean(data: dict, fallback_category: str = "OBJECTION") -> dict:
    """Coerce a model response into the four fields, whatever it returned."""
    data = data if isinstance(data, dict) else {}
    category = str(data.get("category") or "").strip().upper()
    if category not in CATEGORIES:
        logger.warning("reply intelligence: unknown category %r -- using %s",
                       category, fallback_category)
        category = fallback_category
    try:
        confidence = max(0.0, min(1.0, float(data.get("confidence"))))
    except (TypeError, ValueError):
        confidence = 0.0
    action = re.sub(r"[^a-z0-9_]+", "_",
                    str(data.get("next_action") or "").strip().lower()).strip("_")
    return {
        "category": category,
        "confidence": round(confidence, 4),
        "next_action": (action or DEFAULT_NEXT_ACTION[category])[:50],
        "draft_response": _trim_words(str(data.get("draft_response") or "").strip()),
    }


def _allowed_links(db: Session, lead: Lead | None) -> tuple[set, set]:
    """(booking-link forms, sender domains) a draft may link to: the same
    allowlist Reply Copilot's draft_problems applies. Fails closed to no
    links at all."""
    from app.db.models import Sequence  # noqa: PLC0415
    from app.services import notifications, reply_copilot  # noqa: PLC0415

    if lead is None:
        return set(), set()
    try:
        owner_id = notifications.owner_of_lead(db, lead)
        enrollment = reply_copilot._send_enrollment(db, lead, None)  # noqa: SLF001
        sequence = db.get(Sequence, enrollment.sequence_id) if enrollment else None
        _, allowed_urls = reply_copilot.booking_links(db, sequence, owner_id)
        return allowed_urls, reply_copilot.sender_domains(db, owner_id)
    except Exception:  # noqa: BLE001 -- fail closed: no link is allowed
        logger.warning("reply intelligence: could not resolve allowed links", exc_info=True)
        return set(), set()


def _draft_problem(draft: str, allowed_urls: set, own_domains: set,
                   sources: tuple = ()) -> str | None:
    """Why a suggested answer is unusable, or None. Banned words, as before;
    a link that is not the booking link or the sender's own domain (a reply
    can ask for one: 'put this link in your answer'); and a figure, funding
    round or offer that neither the reply nor our message contains."""
    from app.services import grounding  # noqa: PLC0415
    from app.services.reply_copilot import link_allowed, urls_in  # noqa: PLC0415

    if not draft:
        return "the draft came back empty"
    offenders = find_banned_words(draft)
    if offenders:
        return f"the words {', '.join(offenders)} are banned"
    if any(not link_allowed(u, allowed_urls=allowed_urls, own_domains=own_domains)
           for u in urls_in(draft)):
        return "it contains a link that is not the booking link"
    ungrounded = grounding.problems(draft, *sources)
    if ungrounded:
        return f"it states {'; '.join(ungrounded)}"
    return None


def _generate(db: Session, reply: InboundReply) -> dict:
    """One classification + draft, with a single repair attempt for banned words."""
    lead = db.get(Lead, reply.lead_id) if reply.lead_id else None
    original, step_no = _original_message(db, reply)
    prompt = _PROMPT.format(
        notice=prompt_safety.NOTICE,
        reply_body=prompt_safety.block((reply.body or "")[:6000]),
        lead_name=prompt_safety.inline(getattr(lead, "full_name", None) or "unknown"),
        company=prompt_safety.inline(getattr(lead, "company", None) or "unknown"),
        title=prompt_safety.inline(getattr(lead, "title", None) or "unknown"),
        step_no=step_no if step_no is not None else "unknown",
        original_message=prompt_safety.block(original),
    )

    allowed_urls, own_domains = _allowed_links(db, lead)
    result = _clean(_ask(_SYSTEM, prompt))
    sources = (reply.body or "", original)
    reason = _draft_problem(result["draft_response"], allowed_urls, own_domains, sources)
    if reason is None:
        return result

    # One repair attempt, naming the problem. A second failure is not worth
    # a third call: the fallback draft is already a usable message.
    repaired = _clean(
        _ask(_SYSTEM + f"\nYOUR PREVIOUS ANSWER WAS REJECTED because {reason}. "
                       "Rewrite the draft without that, same meaning.",
             prompt),
        fallback_category=result["category"],
    )
    if _draft_problem(repaired["draft_response"], allowed_urls, own_domains,
                      sources) is None:
        return repaired

    logger.warning("reply intelligence: draft for reply %s still unusable after a "
                   "repair attempt (%s) -- using the %s fallback",
                   reply.id, reason, result["category"])
    return {**result, "draft_response": FALLBACK_DRAFT[result["category"]]}


def opt_out_reason(reply: InboundReply) -> str | None:
    """Why this reply must get no draft and no sequence move, else None.

    Pure over the reply row; never raises (an error while checking the
    wording is treated as an opt-out -- fail closed)."""
    routing = (getattr(reply, "classification", None) or "").strip().lower()
    if routing in OPT_OUT_ROUTING:
        return f"routing_{routing}"
    intent = (getattr(reply, "intent_label", None) or "").strip().lower()
    if intent in OPT_OUT_INTENTS:
        return f"intent_{intent}"
    try:
        from app.services.reply_copilot import opt_out_language  # noqa: PLC0415

        if opt_out_language(reply.body) or opt_out_language(reply.subject):
            return "opt_out_language"
    except Exception:  # noqa: BLE001 -- fail closed
        logger.exception("reply intelligence: opt-out check failed for reply %s",
                         getattr(reply, "id", None))
        return "opt_out_check_failed"
    return None


def _opt_out_result(reason: str) -> dict:
    """The stored result for an opted-out reply: no category, no draft, no
    reschedule, an explicit next action, and classified_at set so the reply
    task treats it as done (and a forced re-run clears any older draft)."""
    return {"category": None, "confidence": None,
            "next_action": OPT_OUT_NEXT_ACTION, "draft_response": None,
            "reschedule_date": None,
            "classified_at": datetime.now(timezone.utc),
            "enrollments_advanced": 0, "opt_out": reason}


def prepare_step_three(db: Session, lead_id, step: int = BUYING_SIGNAL_STEP) -> int:
    """Line every enrollment for `lead_id` up so its next step is `step`.

    WHAT IT DOES. Sets `current_step` to step-1 on each of the lead's
    enrollments whose sequence actually HAS that step, so the next step the
    engine would render is the product introduction. It deliberately does NOT
    change enrollment status: the reply handler stopped these enrollments on
    purpose, and a human decides whether to resume. Nothing is sent here.

    WHAT IT RETURNS. The number of enrollments moved.

    WHAT IT NEVER RAISES. Anything -- a failure is logged and returns 0. A
    buying signal whose enrollment could not be repositioned is still a
    buying signal with a draft in front of a human.
    """
    moved = 0
    try:
        enrollments = db.execute(
            select(SequenceEnrollment).where(SequenceEnrollment.lead_id == lead_id)
        ).scalars().all()
        for enrollment in enrollments:
            sequence = db.get(Sequence, enrollment.sequence_id)
            if sequence is None or not any(s.step_no >= step for s in sequence.steps):
                continue
            if enrollment.current_step != step - 1:
                enrollment.current_step = step - 1
                moved += 1
        if moved:
            db.commit()
    except Exception:  # noqa: BLE001 -- see the docstring
        logger.exception("reply intelligence: could not prepare step %s for lead %s",
                         step, lead_id)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return 0
    return moved


def classify_reply(db: Session, reply_id, today: date | None = None) -> dict:
    """Classify one inbound reply and draft the sender's next message.

    WHAT IT DOES. Reads the reply, the lead and the message it answers, asks
    Claude for a category, a confidence, a next action and a draft, rejects
    and regenerates a draft containing banned copy, and applies the two
    category side effects: NOT_NOW gets a reschedule_date of today + 30 days,
    BUYING_SIGNAL lines the lead's enrollments up at step 3 (without
    restarting them -- see prepare_step_three).

    WHAT IT RETURNS. {"category", "confidence", "next_action",
    "draft_response", "reschedule_date" (a date or None), "classified_at"}.
    On failure the same keys with category=None and an extra "error" key, so
    the caller can tell "not classified" from "classified as nothing".

    WHAT IT NEVER RAISES. Anything. This runs from a webhook-triggered task on
    every inbound reply; an Anthropic outage, a malformed response or a
    deleted lead must leave the reply stored and routed exactly as the M3
    path already stored and routed it, with the intelligence fields simply
    unset. Persisting is the caller's job (app/workers/reply_tasks.py) and is
    skipped when "error" is present.
    """
    today = today or datetime.now(timezone.utc).date()
    try:
        if not isinstance(reply_id, uuid_module.UUID):
            reply_id = uuid_module.UUID(str(reply_id))
        reply = db.get(InboundReply, reply_id)
        if reply is None:
            raise LookupError(f"inbound reply {reply_id} not found")

        # Consent guard BEFORE the model call: an opt-out / erasure reply gets
        # no draft, no category side effect (never prepare_step_three), and
        # costs no tokens. See OPT_OUT_ROUTING.
        reason = opt_out_reason(reply)
        if reason is not None:
            logger.info("reply intelligence: reply %s is an opt-out (%s) -- no draft",
                        reply.id, reason)
            return _opt_out_result(reason)

        result = _generate(db, reply)
        result["reschedule_date"] = (
            today + timedelta(days=RESCHEDULE_DAYS)
            if result["category"] == "NOT_NOW" else None
        )
        result["classified_at"] = datetime.now(timezone.utc)

        if result["category"] == "BUYING_SIGNAL" and reply.lead_id:
            result["enrollments_advanced"] = prepare_step_three(db, reply.lead_id)
        return result
    except Exception as exc:  # noqa: BLE001 -- see the docstring
        logger.warning("reply intelligence failed for reply %s: %s: %s",
                       reply_id, type(exc).__name__, exc)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return {"category": None, "confidence": None, "next_action": None,
                "draft_response": None, "reschedule_date": None,
                "classified_at": None,
                "error": f"{type(exc).__name__}: {exc}"[:200]}


def apply(db: Session, reply: InboundReply, result: dict) -> InboundReply:
    """Write a classify_reply() result onto the reply row and commit.

    WHAT IT RETURNS. The same InboundReply instance.

    WHAT IT NEVER RAISES. Anything. A result carrying "error" is not written
    at all -- a failed classification must not blank a good one from an
    earlier attempt, which is what makes the task safe to retry. A commit
    failure is rolled back and logged.
    """
    if result.get("error"):
        return reply
    # Defence in depth: whatever result a caller passes, an opted-out reply
    # never stores a sales draft or a category.
    reason = opt_out_reason(reply)
    if reason is not None:
        result = {**_opt_out_result(reason),
                  "classified_at": result.get("classified_at")}
    try:
        reply.reply_category = result["category"]
        reply.category_confidence = result["confidence"]
        reply.ai_next_action = result["next_action"]
        reply.ai_draft_response = result["draft_response"]
        reply.classified_at = result["classified_at"] or datetime.now(timezone.utc)
        reply.reschedule_date = result["reschedule_date"]
        db.commit()
    except Exception:  # noqa: BLE001 -- see the docstring
        logger.exception("could not persist reply intelligence for reply %s",
                         getattr(reply, "id", None))
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
    return reply
