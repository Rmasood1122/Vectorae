"""Positive reply classification (Part 1, Feature 1).

There are now four questions asked of every inbound reply, and they are
deliberately separate columns because each drives something different:

  `classification`      how does the ENGINE route this? (stop, pause, suppress)
  `reply_category`      what does the HUMAN do next? (draft + next action)
  `authenticity_kind`   is a real person on the other end?
  `intent_label`        WAS THIS REPLY POSITIVE?   <- this module

The fifth question nobody could answer before: "of the replies this campaign
got, how many were actually good?" A raw reply rate counts "unsubscribe me"
and "wrong person" as wins. The positive reply rate does not, and it sits
BESIDE the raw rate rather than replacing it -- a campaign with a high reply
rate and a low positive rate is a different problem from one with neither.

LABELS
  interested    wants to talk, asks to book, asks for pricing/details
  neutral       acknowledges, forwards, asks an unrelated question, or is a
                machine (bounce / out-of-office / auto-responder)
  objection     pushes back -- price, timing-as-excuse, incumbent, "not for us"
  not_now       genuinely interested LATER ("circle back in Q2", "after the
                reorg"); feeds the re-engagement memory (Feature 7)
  unsubscribe   asks to stop being contacted, in any wording

HOW. A rules pass first, a model call only when the rules are not certain:
  * `classification == "unsubscribe_request"` -> unsubscribe, confidence 1.0.
    The routing classifier already suppressed the contact; disagreeing with
    it here would report a suppression as a neutral reply.
  * bounce / out_of_office / automated_response -> neutral, confidence 1.0,
    and `machine_reply()` is True so the metric can exclude them.
  * anything else -> one structured Claude call returning
    {label, confidence, reason}.
A failed or unparseable model call leaves the reply UNCLASSIFIED rather than
guessing, because a guessed label would silently move a headline metric.
"""

from __future__ import annotations

import logging
import uuid as uuid_module
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import InboundReply, Lead, Message, MessageStatus
from app.services import prompt_safety
from app.services.anthropic_client import JUDGE_TEMPERATURE, get_client

logger = logging.getLogger(__name__)

INTERESTED = "interested"
NEUTRAL = "neutral"
OBJECTION = "objection"
NOT_NOW = "not_now"
UNSUBSCRIBE = "unsubscribe"

LABELS = (INTERESTED, NEUTRAL, OBJECTION, NOT_NOW, UNSUBSCRIBE)

#: The labels that count towards the positive reply rate. Deliberately only
#: one: "not_now" is a real future opportunity (Feature 7 schedules it) but it
#: is not a positive reply TO THIS CAMPAIGN, and counting it would make the
#: metric flattering rather than useful.
POSITIVE_LABELS = (INTERESTED,)

#: Routing classes that mean a machine sent this, not a person. Their replies
#: are labelled `neutral` deterministically and excluded from the rate.
MACHINE_CLASSES = ("bounce", "out_of_office", "automated_response")

MAX_TOKENS = 400
MAX_BODY = 4000

_SYSTEM = (
    "You are Vectorae's reply-quality classifier. You read ONE inbound reply "
    "to a cold outreach message and decide whether it was a positive reply.\n"
    "Answer with ONLY a JSON object:\n"
    '{"label": "<label>", "confidence": <0..1>, "reason": "<max 25 words>"}\n'
    "where <label> is exactly one of: " + ", ".join(LABELS) + ".\n"
    "Definitions:\n"
    "  interested  - wants to talk now: asks to book, asks for pricing, asks "
    "for details, says yes, or forwards to a decision maker who should be "
    "contacted now.\n"
    "  objection   - pushes back on the offer: too expensive, already have a "
    "vendor, not a fit, not convinced, annoyed at being contacted (but does "
    "NOT ask to be removed).\n"
    "  not_now     - open to it but LATER: a named future time, a budget "
    "cycle, a reorg, 'circle back', 'after Q2'.\n"
    "  unsubscribe - asks to stop being contacted, in any wording.\n"
    "  neutral     - anything else: an acknowledgement, an unrelated "
    "question, a referral to someone with no timeline, an automated message.\n"
    "`confidence` is your own certainty, not how positive the reply is. "
    "`reason` quotes or paraphrases the reply -- never invent detail."
)

# Output contract (Phase 3 item 2). `label` is not an enum: _clean normalises
# variants ("Not-Now") and deliberately leaves an unknown label UNCLASSIFIED
# rather than forcing a guess. `confidence` may come back as a string; _clean
# coerces it.
INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "label": {"type": "string"},
        "confidence": {"type": ["number", "string"]},
        "reason": {"type": "string"},
    },
    "required": ["label", "confidence", "reason"],
    "additionalProperties": False,
}

# F-AI-02: the reply (sender, subject, body) is written by the prospect and
# goes in as delimited, escaped data. What they were replying to is OUR
# message, but a model wrote it from the prospect's own data (posts, news,
# profile), so it is delimited too: context to read, never instructions.
_PROMPT = """{notice}

THE REPLY
From: {from_address}
Subject: {subject}

{body}

WHAT THEY WERE REPLYING TO (step {step_no})
{original}

Classify the reply."""


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------


def machine_reply(reply: InboundReply) -> bool:
    """True when a machine, not a person, produced this reply.

    Reads the routing classifier first and the authenticity score second, so
    a reply stored before either feature still answers honestly (False)."""
    if (reply.classification or "") in MACHINE_CLASSES:
        return True
    return (reply.authenticity_kind or "") in ("bounce", "out_of_office",
                                               "auto_responder", "bot")


def _rules(reply: InboundReply) -> dict | None:
    """The certain cases, decided without a model call. None = ask the model."""
    routing = (reply.classification or "").strip().lower()
    if routing == "unsubscribe_request":
        return {"label": UNSUBSCRIBE, "confidence": 1.0, "source": "rules",
                "reason": "The routing classifier read this as a request to stop contact."}
    if routing in MACHINE_CLASSES:
        return {"label": NEUTRAL, "confidence": 1.0, "source": "rules",
                "reason": f"Automated message ({routing.replace('_', ' ')}), not a human reply."}
    return None


def _original(db: Session, reply: InboundReply) -> tuple[str, int | None]:
    """The outbound message this reply answers, for context."""
    message = db.get(Message, reply.message_id) if reply.message_id else None
    if message is None and reply.lead_id:
        message = db.execute(
            select(Message)
            .where(Message.lead_id == reply.lead_id,
                   Message.status == MessageStatus.SENT)
            .order_by(Message.step_no.desc())
        ).scalars().first()
    if message is None:
        return "(not on file)", None
    text = f"{(message.subject or '').strip()}\n{(message.body or '').strip()}".strip()
    return (text or "(empty)")[:2000], message.step_no


def _clean(data: object) -> dict | None:
    """Coerce a model answer into {label, confidence, reason}; None if unusable."""
    data = data if isinstance(data, dict) else {}
    label = str(data.get("label") or "").strip().lower().replace("-", "_").replace(" ", "_")
    if label not in LABELS:
        logger.warning("reply intent: unknown label %r -- leaving unclassified", label)
        return None
    try:
        confidence = max(0.0, min(1.0, float(data.get("confidence"))))
    except (TypeError, ValueError):
        confidence = 0.0
    reason = " ".join(str(data.get("reason") or "").split())[:300]
    return {"label": label, "confidence": round(confidence, 4),
            "reason": reason or None, "source": "model"}


def classify(db: Session, reply_id) -> dict:
    """Classify one reply's intent.

    WHAT IT RETURNS. {"label", "confidence", "reason", "source", "at"} or, on
    any failure, the same keys with label=None plus "error" -- so the caller
    can tell "not classified" from "classified as neutral".

    WHAT IT NEVER RAISES. Anything. This runs from the reply task on every
    inbound message; a model outage must leave the reply stored and routed
    exactly as it already was, with these fields simply unset.
    """
    try:
        if not isinstance(reply_id, uuid_module.UUID):
            reply_id = uuid_module.UUID(str(reply_id))
        reply = db.get(InboundReply, reply_id)
        if reply is None:
            raise LookupError(f"inbound reply {reply_id} not found")

        result = _rules(reply)
        if result is None:
            original, step_no = _original(db, reply)
            answer = get_client().complete_json(
                temperature=JUDGE_TEMPERATURE,
                system=_SYSTEM,
                prompt=_PROMPT.format(
                    notice=prompt_safety.NOTICE,
                    from_address=prompt_safety.inline(reply.from_address or "(unknown)"),
                    subject=prompt_safety.inline(reply.subject or "(none)"),
                    body=prompt_safety.block((reply.body or "")[:MAX_BODY]),
                    step_no=step_no if step_no is not None else "unknown",
                    original=prompt_safety.block(original),
                ),
                max_tokens=MAX_TOKENS,
                schema=INTENT_SCHEMA,
            )
            result = _clean(answer)
            if result is None:
                raise ValueError("the model did not return one of the five labels")
        result["at"] = datetime.now(timezone.utc)
        return result
    except Exception as exc:  # noqa: BLE001 -- see the docstring
        logger.warning("reply intent failed for reply %s: %s: %s",
                       reply_id, type(exc).__name__, exc)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return {"label": None, "confidence": None, "reason": None, "source": None,
                "at": None, "error": f"{type(exc).__name__}: {exc}"[:200]}


def apply(db: Session, reply: InboundReply, result: dict) -> InboundReply:
    """Write a classify() result onto the reply row and commit.

    A result carrying "error" is not written at all, so a retry can never
    blank a good classification from an earlier attempt."""
    if result.get("error") or not result.get("label"):
        return reply
    try:
        reply.intent_label = result["label"]
        reply.intent_confidence = result["confidence"]
        reply.intent_reason = result["reason"]
        reply.intent_source = result["source"]
        reply.intent_at = result["at"] or datetime.now(timezone.utc)
        db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("could not persist reply intent for reply %s",
                         getattr(reply, "id", None))
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
    return reply


def classify_and_apply(db: Session, reply: InboundReply, *, force: bool = False) -> dict:
    """The one call the reply task makes. Idempotent unless `force`."""
    if reply.intent_at is not None and not force:
        return {"status": "skipped", "label": reply.intent_label}
    result = classify(db, reply.id)
    if result.get("error"):
        return {"status": "failed", "label": None}
    apply(db, reply, result)
    return {"status": "classified", "label": reply.intent_label}


def intent_out(reply: InboundReply) -> dict:
    """The reply-intent block every API response uses."""
    return {
        "label": reply.intent_label,
        "confidence": reply.intent_confidence,
        "reason": reply.intent_reason,
        "source": reply.intent_source,
        "at": reply.intent_at.isoformat() if reply.intent_at else None,
        "is_positive": reply.intent_label in POSITIVE_LABELS,
    }


# --------------------------------------------------------------------------
# The metric
# --------------------------------------------------------------------------


def _rate(numerator: int, denominator: int) -> float | None:
    """None, never 0.0, when there is nothing to divide by -- an empty
    campaign has no reply rate, and charting it as 0% is a lie."""
    return round(numerator / denominator, 4) if denominator else None


def strategy_metrics(db: Session, strategy_id) -> dict:
    """Reply quality for one strategy.

    RETURNS
      sent                   messages actually sent (the rate denominator)
      replies                every inbound reply, machines included
      human_replies          replies a person wrote
      classified             human replies carrying an intent label
      unclassified           human replies with no label yet (model outage,
                             or stored before this feature) -- shown so a
                             low positive rate is never read as "bad replies"
                             when it is really "not classified yet"
      reply_rate             replies / sent          (unchanged, raw)
      positive_reply_rate    interested / sent       (the headline)
      positive_share         interested / classified (of the replies we
                             understood, how many were good)
      breakdown              {label: count} over human replies
    """
    sent = db.execute(
        select(func.count(Message.id))
        .join(Lead, Lead.id == Message.lead_id)
        .where(Lead.strategy_id == strategy_id, Message.status == MessageStatus.SENT)
    ).scalar_one()

    replies = db.execute(
        select(InboundReply)
        .join(Lead, Lead.id == InboundReply.lead_id)
        .where(Lead.strategy_id == strategy_id)
    ).scalars().all()

    human = [r for r in replies if not machine_reply(r)]
    breakdown = {label: 0 for label in LABELS}
    for reply in human:
        if reply.intent_label in breakdown:
            breakdown[reply.intent_label] += 1
    classified = sum(breakdown.values())
    positive = sum(breakdown[label] for label in POSITIVE_LABELS)

    return {
        "sent": sent,
        "replies": len(replies),
        "human_replies": len(human),
        "classified": classified,
        "unclassified": len(human) - classified,
        "reply_rate": _rate(len(replies), sent),
        "positive_reply_rate": _rate(positive, sent),
        "positive_share": _rate(positive, classified),
        "breakdown": breakdown,
    }
