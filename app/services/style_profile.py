"""Feature Group 2 — voice-matched messaging.

A user pastes up to five samples of their own writing (emails, LinkedIn
posts). Claude extracts a STYLE PROFILE -- tone, vocabulary level, sentence
length, formality, humour, habits to keep and to avoid -- stored as JSON on the
user. `system_suffix()` renders it for the system prompt of EVERY outreach
generator: cold email (message_personalization.render_message), WhatsApp
template variables, the automated follow-up brief, the post-meeting follow-up,
LinkedIn messages and call scripts.

It goes in the SYSTEM prompt, not the user prompt, because it is an
instruction about how to write, not material to write about -- and so it
cannot be mistaken for content to quote. The samples themselves never reach an
outreach prompt: only the extracted profile does, so a phrase from a private
email the user pasted can never end up in a stranger's inbox.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.models import User
from app.services import anthropic_client, prompt_safety

logger = logging.getLogger(__name__)

MAX_SAMPLES = 5
MIN_SAMPLE_CHARS = 40
MAX_SAMPLE_CHARS = 4000

SYSTEM = (
    "You are Vectorae's writing style analyst. From samples of ONE person's "
    "writing, describe how they write so another writer can match their "
    "voice. Respond with ONLY a JSON object: "
    '{"tone": "...", "formality": 1-5, "vocabulary_level": "plain"|"conversational"|'
    '"professional"|"technical", "sentence_length": "short"|"medium"|"long", '
    '"avg_words_per_sentence": number, "humor": "none"|"light"|"frequent", '
    '"greeting_style": "...", "signoff_style": "...", '
    '"signature_habits": ["..."], "do": ["..."], "dont": ["..."], '
    '"summary": "two sentences"}. formality: 1 = very casual, 5 = very formal. '
    "Describe patterns, never quote private details (names, numbers, "
    "clients) from the samples."
)

_VOCAB = {"plain", "conversational", "professional", "technical"}
_LENGTH = {"short", "medium", "long"}
_HUMOR = {"none", "light", "frequent"}


# Output contract (Phase 3 item 2): what _clean_profile reads. Numbers are
# coerced with float(), so a number sent as a string is accepted too.
STYLE_SCHEMA = {
    "type": "object",
    "properties": {
        "tone": {"type": "string"},
        "formality": {"type": ["number", "string"]},
        "vocabulary_level": {"type": "string", "enum": sorted(_VOCAB)},
        "sentence_length": {"type": "string", "enum": sorted(_LENGTH)},
        "avg_words_per_sentence": {"type": ["number", "string"]},
        "humor": {"type": "string", "enum": sorted(_HUMOR)},
        "greeting_style": {"type": "string"},
        "signoff_style": {"type": "string"},
        "signature_habits": {"type": "array", "items": {"type": "string"}},
        "do": {"type": "array", "items": {"type": "string"}},
        "dont": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
    },
    "required": ["tone", "summary"],
    "additionalProperties": False,
}


class InvalidSamples(ValueError):
    pass


def clean_samples(samples: list[str]) -> list[str]:
    cleaned = [s.strip()[:MAX_SAMPLE_CHARS] for s in samples or [] if isinstance(s, str)]
    cleaned = [s for s in cleaned if s]
    if not cleaned:
        raise InvalidSamples("paste at least one writing sample")
    if len(cleaned) > MAX_SAMPLES:
        raise InvalidSamples(f"at most {MAX_SAMPLES} samples")
    short = [i + 1 for i, s in enumerate(cleaned) if len(s) < MIN_SAMPLE_CHARS]
    if short:
        raise InvalidSamples(
            f"sample(s) {short} are too short to show a style "
            f"(at least {MIN_SAMPLE_CHARS} characters)")
    return cleaned


def _clean_profile(data: dict) -> dict:
    data = data if isinstance(data, dict) else {}

    def _s(v, n=300):
        text = str(v).strip()[:n] if isinstance(v, (str, int, float)) else ""
        # T26: free text here becomes part of every outbound writer's SYSTEM
        # prompt. A value with a link or an instruction in it is dropped.
        problem = prompt_safety.guidance_problem(text)
        if problem:
            logger.warning("style profile: dropped a value that %s", problem)
            return ""
        return text

    def _list(v, n=6):
        kept = [_s(x, 200) for x in (v or []) if isinstance(x, str) and x.strip()]
        return [x for x in kept if x][:n]

    try:
        formality = max(1, min(5, int(round(float(data.get("formality"))))))
    except (TypeError, ValueError):
        formality = 3
    try:
        words = max(3, min(60, int(round(float(data.get("avg_words_per_sentence"))))))
    except (TypeError, ValueError):
        words = None
    vocab = _s(data.get("vocabulary_level")).lower()
    length = _s(data.get("sentence_length")).lower()
    humor = _s(data.get("humor")).lower()
    return {
        "tone": _s(data.get("tone")),
        "formality": formality,
        "vocabulary_level": vocab if vocab in _VOCAB else "conversational",
        "sentence_length": length if length in _LENGTH else "medium",
        "avg_words_per_sentence": words,
        "humor": humor if humor in _HUMOR else "none",
        "greeting_style": _s(data.get("greeting_style"), 120),
        "signoff_style": _s(data.get("signoff_style"), 120),
        "signature_habits": _list(data.get("signature_habits")),
        "do": _list(data.get("do")),
        "dont": _list(data.get("dont")),
        "summary": _s(data.get("summary"), 600),
    }


def extract(db: Session, user: User, samples: list[str]) -> dict:
    """Analyse the samples, store samples + profile on the user, commit."""
    cleaned = clean_samples(samples)
    prompt = "\n\n".join(f"SAMPLE {i + 1}:\n{s}" for i, s in enumerate(cleaned))
    data = anthropic_client.get_client().complete_json(
        system=SYSTEM, prompt=prompt + "\n\nReturn the JSON object now.", max_tokens=1500,
        schema=STYLE_SCHEMA)
    profile = _clean_profile(data)
    if not profile["tone"] and not profile["summary"]:
        raise ValueError("the style analysis came back empty -- try longer samples")
    user.style_samples_json = cleaned
    user.style_profile_json = profile
    user.style_profile_updated_at = datetime.now(timezone.utc)
    db.commit()
    return profile


def clear(db: Session, user: User) -> None:
    user.style_samples_json = None
    user.style_profile_json = None
    user.style_profile_updated_at = None
    db.commit()


_FORMALITY = {1: "very casual", 2: "casual", 3: "neutral", 4: "formal", 5: "very formal"}


def system_suffix(profile: dict | None) -> str:
    """The style profile as a system-prompt addendum ('' when there is none)."""
    if not profile:
        return ""
    lines = [
        "",
        "WRITE IN THE SENDER'S OWN VOICE. This is their style, extracted from "
        "their real writing -- match it, never imitate it so closely that it "
        "reads as parody, and never let it override the rules above:",
        f"- Tone: {profile.get('tone') or 'natural'}",
        f"- Formality: {profile.get('formality', 3)}/5 "
        f"({_FORMALITY.get(profile.get('formality', 3), 'neutral')})",
        f"- Vocabulary: {profile.get('vocabulary_level')}",
        f"- Sentences: {profile.get('sentence_length')}"
        + (f" (~{profile['avg_words_per_sentence']} words)"
           if profile.get("avg_words_per_sentence") else ""),
        f"- Humour: {profile.get('humor')}",
    ]
    if profile.get("greeting_style"):
        lines.append(f"- Greeting: {profile['greeting_style']}")
    if profile.get("signoff_style"):
        lines.append(f"- Sign-off: {profile['signoff_style']}")
    if profile.get("do"):
        lines.append("- Do: " + "; ".join(profile["do"]))
    if profile.get("dont"):
        lines.append("- Don't: " + "; ".join(profile["dont"]))
    return "\n".join(lines)


def suffix_for_strategy(session: Session, strategy) -> str:
    """The suffix for whoever owns `strategy`. Never raises."""
    try:
        from app.services.notifications import owner_of_strategy  # noqa: PLC0415

        owner_id = owner_of_strategy(session, strategy)
        user = session.get(User, owner_id) if owner_id else None
        return system_suffix(getattr(user, "style_profile_json", None))
    except Exception:  # noqa: BLE001 -- a missing voice must never block a send
        return ""
