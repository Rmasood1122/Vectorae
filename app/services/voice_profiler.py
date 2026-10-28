"""Feature 3 — founder voice cloning.

Reads up to 20 of the Vectorae client's own LinkedIn posts and extracts HOW
they write, as a small set of closed-vocabulary dimensions. Every outgoing
message for that product is then written in their voice instead of a template
voice.

RELATIONSHIP TO app/services/style_profile.py. That module profiles a USER
from writing samples they paste, and is applied to every channel across their
whole account. This one profiles a PRODUCT from LinkedIn posts, and is applied
to that product's outreach. They compose rather than compete: style_profile
goes in the SYSTEM prompt ("write like this person"), the voice profile is
appended to the step BRIEF ("and specifically, here is their voice"). A user
with both gets both, which is the same instruction twice, not two conflicting
ones.

WHY CLOSED VOCABULARIES. Every dimension below has a fixed set of legal
values. A free-text "describe their style" field produces a different
description every run, which then produces differently-worded outreach every
run for no reason the founder asked for. Closed values make the profile
stable, diffable, and something a UI can render as a form the user can
correct.

THE POSTS THEMSELVES NEVER REACH AN OUTREACH PROMPT. Only the extracted
dimensions do. A profile built from a post about the founder's divorce must
not be able to put that post in front of a prospect.
"""

import logging
import re
import uuid as uuid_module
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Product, VoiceProfile
from app.services import anthropic_client, prompt_safety

logger = logging.getLogger(__name__)

MAX_POSTS = 20
MIN_POST_CHARS = 20
MAX_POST_CHARS = 4000
MAX_PHRASES = 5
MAX_TOKENS = 1000

# Every style dimension, its legal values, and the value used when the model
# returns something outside them. The first value is NOT automatically the
# default -- the default is the least opinionated answer, so an unparseable
# response produces neutral instructions rather than confidently wrong ones.
DIMENSIONS = {
    "avg_sentence_length": (("short", "medium", "long"), "medium"),
    "punctuation_style": (("minimal", "standard", "heavy"), "standard"),
    "opens_with": (("question", "statement", "observation", "story"), "statement"),
    "emoji_usage": (("none", "rare", "frequent"), "none"),
    "paragraph_length": (("single-line", "short", "long"), "short"),
    "vocabulary_level": (("conversational", "professional", "technical"),
                         "conversational"),
}

SYSTEM = (
    "You are Vectorae's voice analyst. You read one person's LinkedIn posts "
    "and describe HOW they write, so another writer can sound like them. "
    "Respond with ONLY a JSON object, no prose and no markdown fences:\n"
    '{"avg_sentence_length": "short|medium|long", '
    '"punctuation_style": "minimal|standard|heavy", '
    '"opens_with": "question|statement|observation|story", '
    '"uses_numbers": true|false, '
    '"emoji_usage": "none|rare|frequent", '
    '"paragraph_length": "single-line|short|long", '
    '"vocabulary_level": "conversational|professional|technical", '
    f'"signature_phrases": ["up to {MAX_PHRASES} short phrases they repeat"]}}\n'
    "DEFINITIONS. avg_sentence_length: short is under 10 words, medium 10-20, "
    "long over 20. punctuation_style: heavy means frequent commas, dashes and "
    "ellipses. opens_with: how their posts typically START. uses_numbers: "
    "true only if they reach for specific figures regularly. "
    "signature_phrases: phrases THEY actually repeat, copied exactly, each "
    "under eight words. Never include a client name, a person's name or a "
    "private detail in signature_phrases -- describe habits, not content."
)

# Output contract (Phase 3 item 2): every dimension is the closed vocabulary
# the prompt names and clean_dimensions maps.
VOICE_SCHEMA = {
    "type": "object",
    "properties": {
        **{name: {"type": "string", "enum": list(allowed)}
           for name, (allowed, _default) in DIMENSIONS.items()},
        "uses_numbers": {"type": "boolean"},
        "signature_phrases": {"type": "array", "items": {"type": "string"}},
    },
    "required": [],
    "additionalProperties": False,
}

_PROMPT_HEADER = (
    "These are one person's recent LinkedIn posts. Extract their writing "
    "style.\n\n"
)


class InvalidPosts(ValueError):
    """The posts given are not something a voice can be extracted from."""


def clean_posts(posts) -> list[str]:
    """Normalise the caller's posts into analysable text.

    WHAT IT RETURNS. Up to MAX_POSTS stripped, length-capped strings.

    RAISES InvalidPosts when nothing usable is left -- an empty list, or
    posts too short to show a style. This is the one function here that DOES
    raise: it is input validation, and the API turns it into a 422 so the
    user finds out their paste did not work instead of getting a profile
    built from three words.
    """
    cleaned = [str(post).strip()[:MAX_POST_CHARS] for post in (posts or [])
               if isinstance(post, str) and str(post).strip()]
    cleaned = [post for post in cleaned if len(post) >= MIN_POST_CHARS]
    if not cleaned:
        raise InvalidPosts(
            f"give at least one post of {MIN_POST_CHARS} characters or more")
    if len(cleaned) > MAX_POSTS:
        raise InvalidPosts(f"at most {MAX_POSTS} posts")
    return cleaned


def _choice(value, dimension: str) -> str:
    allowed, default = DIMENSIONS[dimension]
    candidate = str(value or "").strip().lower().replace("_", "-")
    return candidate if candidate in allowed else default


def clean_dimensions(data: dict) -> tuple[dict, list[str]]:
    """(style dimensions, signature phrases) from a raw model response.

    Every dimension is forced into its closed vocabulary and every phrase is
    length-capped, so a malformed or creative response can never put free text
    into an outreach prompt. Returns neutral defaults rather than raising --
    the caller has already decided a profile is wanted.
    """
    data = data if isinstance(data, dict) else {}
    dimensions = {name: _choice(data.get(name), name) for name in DIMENSIONS}
    dimensions["uses_numbers"] = bool(data.get("uses_numbers"))
    # `or []` is not enough: a model that answers with a bare STRING would
    # otherwise be iterated character by character and produce
    # ["n", "o", "t", ...] as the founder's signature phrases.
    raw_phrases = data.get("signature_phrases")
    raw_phrases = raw_phrases if isinstance(raw_phrases, (list, tuple)) else []
    phrases = [
        re.sub(r"\s+", " ", phrase.strip())[:80]
        for phrase in raw_phrases
        if isinstance(phrase, str) and phrase.strip()
    ]
    # T26: a phrase is pasted into every step brief, so one carrying a link or
    # an instruction is dropped -- the closed vocabulary above is not enough.
    phrases = [p for p in phrases if prompt_safety.guidance_problem(p) is None][:MAX_PHRASES]
    return dimensions, phrases


def get_profile(db: Session, product_id) -> VoiceProfile | None:
    """The voice profile for a product, or None. Never raises."""
    try:
        if not isinstance(product_id, uuid_module.UUID):
            product_id = uuid_module.UUID(str(product_id))
        return db.execute(
            select(VoiceProfile).where(VoiceProfile.product_id == product_id)
        ).scalars().first()
    except Exception:  # noqa: BLE001 -- a missing voice must never block a send
        logger.exception("voice profile lookup failed for product %s", product_id)
        return None


def extract_voice_profile(db: Session, product_id, linkedin_posts: list[str]) -> VoiceProfile:
    """Analyse `linkedin_posts` and store the product's voice profile.

    WHAT IT DOES. Cleans the posts, asks Claude for the style dimensions and
    signature phrases, coerces the answer into the closed vocabularies, then
    UPSERTS the single voice_profiles row for this product and commits.

    WHAT IT RETURNS. The persisted VoiceProfile.

    WHAT IT NEVER RAISES. Nothing is swallowed here -- this is the deliberate
    exception in this feature. It runs from a Celery task the user explicitly
    asked for, and a silent failure would leave them staring at a spinner
    that never resolves. It raises InvalidPosts for unusable input, LookupError
    for an unknown product, and whatever the Anthropic gateway raises when the
    model is unreachable; app/workers/voice_tasks.py is where those become a
    logged, retryable failure. The SEND path never calls this -- it calls
    get_profile, which does swallow.
    """
    if not isinstance(product_id, uuid_module.UUID):
        product_id = uuid_module.UUID(str(product_id))
    if db.get(Product, product_id) is None:
        raise LookupError(f"product {product_id} not found")

    posts = clean_posts(linkedin_posts)
    prompt = _PROMPT_HEADER + "\n\n".join(
        f"POST {index + 1}:\n{post}" for index, post in enumerate(posts)
    ) + "\n\nReturn the JSON object now."
    data = anthropic_client.get_client().complete_json(
        system=SYSTEM, prompt=prompt, max_tokens=MAX_TOKENS, schema=VOICE_SCHEMA)
    dimensions, phrases = clean_dimensions(data)

    profile = get_profile(db, product_id)
    if profile is None:
        profile = VoiceProfile(product_id=product_id)
        db.add(profile)
    profile.raw_posts_json = posts
    profile.style_dimensions_json = dimensions
    profile.sample_phrases = phrases
    profile.post_count = len(posts)
    profile.last_analyzed_at = datetime.now(timezone.utc)
    db.commit()
    return profile


def clear_profile(db: Session, product_id) -> bool:
    """Delete a product's voice profile, reverting it to the default voice.

    WHAT IT RETURNS. True when a profile was deleted, False when there was
    none. Never raises on a missing profile -- deleting nothing is a
    successful no-op, which is what makes the DELETE route idempotent.
    """
    profile = get_profile(db, product_id)
    if profile is None:
        return False
    db.delete(profile)
    db.commit()
    return True


# --------------------------------------------------------------------------
# Applying the voice
# --------------------------------------------------------------------------

_LENGTH_INSTRUCTION = {
    "short": "Keep sentences under 10 words. Short, clipped, one idea each.",
    "medium": "Keep sentences to 10-20 words.",
    "long": "Longer sentences are fine (20+ words); do not chop them up.",
}
_PUNCTUATION_INSTRUCTION = {
    "minimal": "Punctuate sparely -- full stops, almost no commas or dashes.",
    "standard": "Punctuate normally.",
    "heavy": "Use commas and dashes freely, the way they do.",
}
_OPENING_INSTRUCTION = {
    "question": "Open the message with a question.",
    "statement": "Open with a flat statement, not a question.",
    "observation": "Open with an observation about them or their market.",
    "story": "Open with one concrete moment or example, in a sentence.",
}
_PARAGRAPH_INSTRUCTION = {
    "single-line": "Write in single-line paragraphs with blank lines between.",
    "short": "Keep paragraphs to 2-3 lines.",
    "long": "Longer paragraphs are fine.",
}
_VOCABULARY_INSTRUCTION = {
    "conversational": "Plain, spoken vocabulary. Contractions are fine.",
    "professional": "Professional vocabulary, no slang, no jargon.",
    "technical": "Technical vocabulary is fine; they know the domain.",
}


def apply_voice_to_brief(brief: str, profile: VoiceProfile | None) -> str:
    """Append the founder's voice instructions to a sequence step's brief.

    WHAT IT DOES. Turns the stored dimensions into imperative instructions
    and appends them under a VOICE heading, so the message writer receives
    the step's intent AND how this particular founder writes. The brief is
    returned unchanged when there is no profile, so the default voice is
    simply "no extra instructions".

    WHAT IT RETURNS. The enriched brief (a string).

    WHAT IT NEVER RAISES. Anything. A malformed or half-written profile
    degrades to fewer instructions, never to a failed send -- the worst case
    this function is allowed to produce is a message in the default voice.
    """
    try:
        dimensions = (getattr(profile, "style_dimensions_json", None) or {}) if profile else {}
        phrases = (getattr(profile, "sample_phrases", None) or []) if profile else []
        if not dimensions and not phrases:
            return brief

        lines = [
            "",
            "VOICE — write this in the SENDER'S own voice, extracted from their "
            "own LinkedIn posts. These instructions describe HOW to write, not "
            "what to say, and never override the rules or the brief above:",
        ]
        for key, table in (("avg_sentence_length", _LENGTH_INSTRUCTION),
                           ("punctuation_style", _PUNCTUATION_INSTRUCTION),
                           ("opens_with", _OPENING_INSTRUCTION),
                           ("paragraph_length", _PARAGRAPH_INSTRUCTION),
                           ("vocabulary_level", _VOCABULARY_INSTRUCTION)):
            instruction = table.get(dimensions.get(key))
            if instruction:
                lines.append(f"- {instruction}")

        if dimensions.get("uses_numbers"):
            lines.append("- They use specific figures. Include ONE concrete number "
                         "if the lead data supports it -- never invent one.")
        else:
            lines.append("- They do not quote statistics. Do not add any.")

        emoji = dimensions.get("emoji_usage")
        if emoji == "none":
            lines.append("- Never use an emoji.")
        elif emoji == "rare":
            lines.append("- At most one emoji, and only if it is genuinely natural.")
        elif emoji == "frequent":
            lines.append("- An emoji or two is in character.")

        if phrases:
            lines.append("- Phrases they actually use: "
                         + "; ".join(f'"{phrase}"' for phrase in phrases)
                         + ". Borrow their cadence, do not paste all of them in.")
        return f"{brief}\n" + "\n".join(lines)
    except Exception:  # noqa: BLE001 -- see the docstring
        logger.exception("could not apply the voice profile to a brief")
        return brief


def brief_for_strategy(db: Session, strategy, brief: str) -> tuple[str, bool]:
    """(enriched brief, whether a voice profile was applied) for a strategy.

    The seam app/services/message_personalization.py::render_message uses:
    resolves the strategy's product, looks up its profile and applies it.

    WHAT IT NEVER RAISES. Anything -- a lookup failure returns the original
    brief and False. A missing voice must never block a send.
    """
    try:
        profile = get_profile(db, getattr(strategy, "product_id", None))
        if profile is None:
            return brief, False
        return apply_voice_to_brief(brief, profile), True
    except Exception:  # noqa: BLE001 -- see the docstring
        logger.exception("voice profile could not be applied for strategy %s",
                         getattr(strategy, "id", None))
        return brief, False
