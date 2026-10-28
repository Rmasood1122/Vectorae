"""Fabrication-proof claim engine for outreach content (Feature A1).

Every AI-written message is checked BEFORE it is queued to send: each factual
claim it makes about the prospect or their company -- funding, headcount,
hiring, a job change, news, metrics, expansion, "your recent post" -- must be
supported by data this system actually fetched and stored for that lead. A claim
that is not is STRIPPED (the sentence is removed) or, where removing it would
gut the message (the opening hook, the subject line, a template variable),
REWRITTEN to a generic line that asserts nothing. Every decision, verified or
not, is written to `claim_verification_log`.

THE EVIDENCE LEDGER (build_evidence) is only what is on the lead row:
  identity      full_name / title / company         (the sourcing provider)
  enrichment    every scalar in enrichment_json      (Apollo person + org record,
                                                      Hunter verification, and any
                                                      tool research stored there)
  news          company_news_json                    (NewsAPI)
  post          linkedin_posts_json                  (the lead's own posts)
Nothing is fetched here. A fact the pipeline did not store is, by definition,
not a fact the message may state.
With F02 Signal Radar on (signal_radar_enabled), the ledger also holds the
lead's stored, unexpired market-intel headlines (lead_signals rows naming the
lead's company), as `news` evidence keyed `market_intel:<url>`. Off, the
ledger is exactly the four lead columns above.

HOW A CLAIM IS FOUND
  1. rules -- per-category patterns (CATEGORY_PATTERNS), applied only to
     sentences ABOUT THE PROSPECT (second person, their name or their company).
     "We helped 40 agencies grow 3x" is a claim about us, not them, and is not
     this engine's business.
  2. the model (optional, system setting claim_model_extraction_enabled) --
     one extraction call that catches what the patterns miss. Its failure
     degrades to rules only.

HOW A CLAIM IS SUPPORTED
  * category support: some evidence item of an allowed kind mentions the
    category (funding claims need funding evidence, "your post" needs a post);
  * token support: every specific token in the sentence -- numbers and money
    (unit-normalised, so "$20M" matches "20 million"), a "Series B", quoted
    phrases, proper nouns -- appears in the evidence. Proper nouns that name
    the lead, their company, or the SENDER's own product are exempt.
Both must hold. Anything less is unsupported.

FAILS CLOSED. If verification itself errors, every rule-detected claim is
stripped (empty evidence) rather than letting unverified content through.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ClaimVerificationLog, Lead, Message, Product, Strategy

logger = logging.getLogger(__name__)

VERIFIED, STRIPPED, REWRITTEN = "verified", "stripped", "rewritten"
GENERIC_SUBJECT = "Quick question"
GENERIC_VALUE = "your team"

CATEGORY_PATTERNS: dict[str, re.Pattern] = {
    "funding": re.compile(
        r"\b(rais(?:e|ed|es|ing)\b|funding|funded\b|series [a-h]\b|seed round|pre-seed|"
        r"investment round|investors?\b|backed by|valuation|went public|\bipo\b)", re.I),
    "headcount": re.compile(
        r"(\b\d[\d,]*\+?\s*(?:employees|people|staff|team members|engineers|salespeople|"
        r"reps|consultants|strong)\b|\bteam of \d|\bheadcount\b|"
        r"\b(?:grew|growing|doubled|tripled) (?:your|the|their) team\b)", re.I),
    "hiring": re.compile(
        r"\b(hiring|job (?:posting|opening|listing|ad)s?|open (?:roles?|positions?)|"
        r"recruiting|you'?re looking for an? )", re.I),
    "job_change": re.compile(
        r"\b(new (?:role|position|job)|recently (?:joined|started|promoted|took over)|"
        r"just (?:joined|started)|promot(?:ed|ion)|stepp(?:ed|ing) into|appointed|"
        r"took over as)\b", re.I),
    "news": re.compile(
        r"\b(saw (?:the|your) (?:news|announcement)|in the news|announc(?:ed|ement)|"
        r"just launched|launch(?:ed|ing) (?:a|an|your|the)|acqui(?:red|sition)|merg(?:ed|er)|"
        r"partnership with|featured (?:in|on)|press release|award|named (?:to|as|one of))\b",
        re.I),
    "metrics": re.compile(
        r"(\b\d+(?:\.\d+)?\s?%|\$\s?\d[\d,.]*\s?(?:k|m|mm|bn?|million|billion)?\b|\b\d+x\b|"
        r"\b(?:revenue|arr|mrr|record (?:year|quarter)|profitab\w*)\b)", re.I),
    "expansion": re.compile(
        r"\b(expand(?:ed|ing)? (?:into|to|across)|new (?:office|location|market)s?|"
        r"opened (?:an?|your|a new) (?:office|location|branch))\b", re.I),
    "their_content": re.compile(
        r"\b(your (?:recent )?(?:post|article|podcast|talk|webinar|comment|newsletter|video)|"
        r"you (?:recently )?(?:wrote|posted|shared|said|mentioned|talked about))\b", re.I),
}

# Which evidence kinds may support a category, and the words that evidence
# must contain. `None` words = any evidence item of an allowed kind will do.
_SUPPORT: dict[str, tuple[set[str], re.Pattern | None]] = {
    "funding": ({"enrichment", "news", "post"}, re.compile(
        r"fund|rais|series|seed|invest|valuation|round|ipo", re.I)),
    "headcount": ({"enrichment", "news", "post"}, re.compile(
        r"employee|headcount|staff|team|people", re.I)),
    "hiring": ({"enrichment", "news", "post"}, re.compile(
        r"hiring|job|opening|recruit|career|position|role", re.I)),
    "job_change": ({"enrichment", "news", "post"}, re.compile(
        r"joined|promot|new role|started|appoint|employment_history|took over", re.I)),
    "news": ({"news", "post", "enrichment"}, re.compile(
        r"announc|launch|acqui|merg|partner|award|featured|named|news|press", re.I)),
    "metrics": ({"enrichment", "news", "post"}, None),
    "expansion": ({"enrichment", "news", "post"}, re.compile(
        r"expan|office|location|opened|branch|market", re.I)),
    "their_content": ({"post"}, None),
    "other": ({"enrichment", "news", "post", "identity"}, None),
}

_ABOUT_THEM = re.compile(r"\b(you|your|you'?re|you'?ve|yours|congrat\w*)\b", re.I)
_GREETING = re.compile(r"^\s*(hi|hey|hello|dear|good (morning|afternoon|evening))\b", re.I)
_NUMBER = re.compile(r"\$?\s?(\d[\d,]*(?:\.\d+)?)\s?(%|k\b|m\b|mm\b|bn?\b|million\b|billion\b|x\b)?",
                     re.I)
_SERIES = re.compile(r"\bseries ([a-h])\b", re.I)
_QUOTED = re.compile(r"[\"“”]([^\"“”]{4,120})[\"“”]")
_PROPER = re.compile(r"(?<![\w'])([A-Z][a-zA-Z0-9&'’-]+(?:\s+[A-Z][a-zA-Z0-9&'’-]+)*)")
_POSSESSIVE = re.compile(r"['’]s$")
_STOP = {
    "I", "I'm", "I've", "I'd", "I'll", "Hi", "Hey", "Hello", "Dear", "Thanks", "Thank", "Congrats",
    "Congratulations", "Best", "Cheers", "Regards", "Monday", "Tuesday", "Wednesday", "Thursday",
    "Friday", "Saturday", "Sunday", "January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December", "Q1", "Q2", "Q3", "Q4", "CEO", "CTO",
    "COO", "CFO", "CMO", "VP", "SVP", "EVP", "Head", "Director", "Founder", "Co-founder", "Owner",
    "Manager", "Series", "LinkedIn", "Google", "Zoom", "Email", "AI", "B2B", "SaaS", "CRM", "SDR",
    "ROI", "US", "USA", "UK", "EU", "OK", "PS", "P.S", "Would", "Could", "Can", "Are", "Is", "Do",
    "Does", "Did", "Have", "Has", "Just", "Quick", "Saw", "Noticed", "Loved", "Read", "Great",
    "Your", "You", "We", "Our", "My", "The", "A", "An", "If", "When", "Since", "As", "And", "But",
    "So", "Also", "Happy", "Hope", "Worth", "Open",
}
_MULTIPLIERS = {"k": 1e3, "m": 1e6, "mm": 1e6, "million": 1e6, "b": 1e9, "bn": 1e9,
                "billion": 1e9}


@dataclass
class Evidence:
    kind: str       # identity | enrichment | news | post
    source: str     # e.g. "apollo:enrichment.person.organization.estimated_num_employees"
    text: str


@dataclass
class Decision:
    field: str
    category: str
    claim_text: str
    verdict: str
    extractor: str = "rules"
    evidence: Evidence | None = None
    unsupported: list[str] = field(default_factory=list)
    replacement: str | None = None


# --------------------------------------------------------------------------
# Evidence
# --------------------------------------------------------------------------


def _source_label(lead: Lead, path: str) -> str:
    lowered = path.lower()
    if "verification" in lowered:
        return "hunter"
    if "signalforge" in lowered:
        return "signalforge"
    return (lead.source or "enrichment").lower()


def _walk(value, path: str, out: list[tuple[str, str]], depth: int = 0) -> None:
    if len(out) >= 600 or depth > 7:
        return
    if isinstance(value, dict):
        for key, child in value.items():
            _walk(child, f"{path}.{key}", out, depth + 1)
    elif isinstance(value, list):
        for index, child in enumerate(value[:50]):
            _walk(child, f"{path}[{index}]", out, depth + 1)
    elif value is not None and value != "":
        out.append((path, str(value)[:600]))


def build_evidence(lead: Lead) -> list[Evidence]:
    items: list[Evidence] = []
    identity = " ".join(filter(None, [lead.full_name, lead.title, lead.company]))
    if identity:
        items.append(Evidence("identity", f"{(lead.source or 'lead').lower()}:profile", identity))
    leaves: list[tuple[str, str]] = []
    _walk(lead.enrichment_json or {}, "enrichment", leaves)
    for path, value in leaves:
        key = path.rsplit(".", 1)[-1]
        items.append(Evidence("enrichment", f"{_source_label(lead, path)}:{path}",
                              f"{key}: {value}"))
    for index, post in enumerate(lead.linkedin_posts_json or []):
        if isinstance(post, dict) and (post.get("text") or "").strip():
            items.append(Evidence("post", f"linkedin_post:{post.get('url') or index}",
                                  str(post["text"])[:2000]))
    for item in lead.company_news_json or []:
        if isinstance(item, dict) and item.get("headline"):
            items.append(Evidence("news", f"newsapi:{item.get('url') or item['headline'][:80]}",
                                  f"{item['headline']} {item.get('summary') or ''}"))
    return items


def _sender_vocabulary(session: Session, strategy: Strategy | None) -> str:
    from app.config import settings  # noqa: PLC0415

    parts = ["Vectorae", settings.sender_identity or ""]
    if strategy is not None:
        product = session.get(Product, strategy.product_id)
        if product is not None:
            parts += [product.name or "", product.description or ""]
        # Local business mode: the sender's own area and offer are facts about
        # the SENDER, never claims about the prospect. "" for other products.
        from app.services import local_business  # noqa: PLC0415

        local = local_business.sender_vocabulary(session, strategy)
        if local:
            parts.append(local)
    return " ".join(parts).lower()


# --------------------------------------------------------------------------
# Tokens
# --------------------------------------------------------------------------


def _numbers(text: str) -> set[float]:
    values: set[float] = set()
    for match in _NUMBER.finditer(text or ""):
        raw = match.group(1).replace(",", "")
        try:
            number = float(raw)
        except ValueError:
            continue
        unit = (match.group(2) or "").lower()
        values.add(number * _MULTIPLIERS.get(unit, 1.0))
    return values


def _claim_tokens(sentence: str, lead: Lead, sender_vocab: str) -> dict[str, list]:
    exempt_words = set(" ".join(filter(None, [lead.full_name, lead.company])).lower().split())
    proper = []
    for match in _PROPER.finditer(sentence):
        at_start = match.start() == len(sentence) - len(sentence.lstrip())
        # "Acme Digital's" names "Acme Digital": the possessive is grammar.
        raw_words = [_POSSESSIVE.sub("", w).strip("’'-") for w in match.group(1).split()]
        words = [w for w in raw_words if w and w not in _STOP]
        if at_start and words and raw_words[0] == words[0]:
            words = words[1:]   # a sentence-initial capital is grammar, not a name
        phrase = " ".join(words)
        if len(phrase) < 2 or phrase.lower() in sender_vocab:
            continue
        if all(part.lower() in exempt_words for part in words):
            continue
        proper.append(phrase)
    return {
        "numbers": sorted(_numbers(sentence)),
        "series": [s.lower() for s in _SERIES.findall(sentence)],
        "quotes": [q.strip().lower() for q in _QUOTED.findall(sentence)],
        "proper": proper,
    }


def _supported(category: str, sentence: str, evidence: list[Evidence], lead: Lead,
               sender_vocab: str) -> tuple[bool, Evidence | None, list[str]]:
    kinds, words = _SUPPORT.get(category, _SUPPORT["other"])
    pool = [e for e in evidence if e.kind in kinds]
    backing = [e for e in pool if words is None or words.search(e.text)]
    tokens = _claim_tokens(sentence, lead, sender_vocab)
    corpus = " ".join(e.text for e in evidence).lower()
    corpus_numbers = _numbers(corpus)

    missing: list[str] = []
    for number in tokens["numbers"]:
        if not any(abs(number - known) <= max(1e-6, abs(known) * 0.005)
                   for known in corpus_numbers):
            missing.append(f"number:{number:g}")
    missing += [f"series:{s}" for s in tokens["series"] if f"series {s}" not in corpus]
    missing += [f"quote:{q}" for q in tokens["quotes"] if q not in corpus]
    missing += [f"name:{p}" for p in tokens["proper"] if p.lower() not in corpus]

    if category == "other" and not any(tokens.values()):
        return False, None, ["no verifiable detail"]
    if not backing:
        return False, None, missing or [f"no {category} evidence on record"]
    if missing:
        return False, None, missing
    # The most specific backing item: the one containing the most tokens.
    def _score(item: Evidence) -> int:
        text = item.text.lower()
        return sum(1 for p in tokens["proper"] if p.lower() in text) + \
            sum(1 for s in tokens["series"] if f"series {s}" in text)
    return True, max(backing, key=_score), []


# --------------------------------------------------------------------------
# Verification
# --------------------------------------------------------------------------


# A sentence ends at . ! ? or a line break, but not at a decimal point
# ("$2.5M", "4.7%"): splitting there strips the "$2." half and sends the rest.
_SENTENCE = re.compile(r"(?:[^.!?\n]|(?<=\d)\.(?=\d))+(?:[.!?]+|$)\s*")


def _hook_line(lead: Lead) -> str:
    company = (lead.company or "").strip()
    return (f"I wanted to reach out to you at {company} directly." if company
            else "I wanted to reach out to you directly.")


def _about_them(sentence: str, lead: Lead) -> bool:
    if _ABOUT_THEM.search(sentence):
        return True
    names = [n for n in [lead.company, *(lead.full_name or "").split()] if n and len(n) > 2]
    return any(re.search(rf"\b{re.escape(n)}\b", sentence, re.I) for n in names)


def verify_text(text: str | None, *, field_name: str, lead: Lead, evidence: list[Evidence],
                sender_vocab: str, model_claims: dict[str, str] | None = None,
                replace_empty_with: str | None = None) -> tuple[str | None, list[Decision]]:
    """(the text with unsupported claims removed/rewritten, the decisions)."""
    if not text:
        return text, []
    decisions: list[Decision] = []
    model_claims = model_claims or {}
    out_paragraphs: list[str] = []
    hook_pending = True

    for paragraph in text.split("\n"):
        if not paragraph.strip():
            out_paragraphs.append(paragraph)
            continue
        # A bare greeting line ("Hi Sara,") is not the hook. A short paragraph
        # that STARTS with a greeting but carries a claim ("Hi, this is an AI
        # assistant. Congrats on your award.") is checked sentence by sentence.
        if (hook_pending and _GREETING.match(paragraph) and len(paragraph) < 60
                and not any(p.search(paragraph) for p in CATEGORY_PATTERNS.values())):
            out_paragraphs.append(paragraph)
            continue
        kept: list[str] = []
        for sentence in _SENTENCE.findall(paragraph):
            stripped = sentence.strip()
            if not stripped:
                continue
            is_hook, hook_pending = hook_pending, False
            categories = [c for c, p in CATEGORY_PATTERNS.items() if p.search(stripped)]
            extractor = "rules"
            if not categories:
                model_category = next((cat for claim, cat in model_claims.items()
                                       if claim and claim.lower() in stripped.lower()), None)
                if model_category:
                    categories, extractor = [model_category if model_category in _SUPPORT
                                             else "other"], "model"
            if not categories or not _about_them(stripped, lead):
                kept.append(sentence)
                continue

            failures, backing = [], None
            for category in categories:
                ok, item, missing = _supported(category, stripped, evidence, lead, sender_vocab)
                if ok:
                    backing = backing or item
                else:
                    failures.append((category, missing))
            if not failures:
                decisions.append(Decision(field_name, categories[0], stripped, VERIFIED,
                                          extractor, backing))
                kept.append(sentence)
                continue

            category, missing = failures[0]
            if is_hook and replace_empty_with is None:
                replacement = _hook_line(lead)
                kept.append(replacement + (" " if sentence.endswith(" ") else ""))
                decisions.append(Decision(field_name, category, stripped, REWRITTEN, extractor,
                                          None, [m for _, ms in failures for m in ms],
                                          replacement))
            else:
                decisions.append(Decision(field_name, category, stripped, STRIPPED, extractor,
                                          None, [m for _, ms in failures for m in ms]))
        out_paragraphs.append("".join(kept).rstrip())

    result = re.sub(r"\n{3,}", "\n\n", "\n".join(out_paragraphs)).strip()
    if not result and decisions:
        result = replace_empty_with if replace_empty_with is not None else _hook_line(lead)
        last = decisions[-1]
        if last.verdict == STRIPPED:
            last.verdict, last.replacement = REWRITTEN, result
    return result, decisions


EXTRACTOR_SYSTEM = (
    "You are Vectorae's claim extractor. You read one outreach message and list "
    "every sentence that asserts a FACT about the recipient or their company (funding, "
    "headcount, hiring, a job change, news, metrics, expansion, something they posted, "
    "or any other specific factual statement about them). Opinions, questions and "
    "statements about the sender are not claims. Respond with ONLY JSON: "
    '{"claims": [{"text": "<the sentence, copied exactly>", "category": '
    '"funding|headcount|hiring|job_change|news|metrics|expansion|their_content|other"}]}. '
    'If there are none, respond {"claims": []}.'
)

EXTRACTOR_SCHEMA = {
    "type": "object",
    "properties": {"claims": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "category": {"type": "string", "enum": [
                "funding", "headcount", "hiring", "job_change", "news", "metrics",
                "expansion", "their_content", "other"]},
        },
        "required": ["text", "category"],
        "additionalProperties": False,
    }}},
    "required": ["claims"],
    "additionalProperties": False,
}


def _model_claims(session: Session, texts: list[str]) -> dict[str, str]:
    from app.services import anthropic_client, prompt_safety, system_settings  # noqa: PLC0415

    if not system_settings.get(session, "claim_model_extraction_enabled"):
        return {}
    joined = "\n\n".join(t for t in texts if t)
    if not joined.strip():
        return {}
    try:
        data = anthropic_client.get_client().complete_json(
            # The draft is model text written from the prospect's own data:
            # it is the thing under review, delimited as data.
            system=EXTRACTOR_SYSTEM,
            prompt=f"{prompt_safety.NOTICE}\n\nMESSAGE:\n{prompt_safety.block(joined[:6000])}",
            max_tokens=800,
            temperature=anthropic_client.JUDGE_TEMPERATURE,
            schema=EXTRACTOR_SCHEMA)
    except Exception as exc:  # noqa: BLE001 -- rules still run
        logger.info("claim extraction model unavailable (%s); rules only", type(exc).__name__)
        return {}
    claims: dict[str, str] = {}
    for item in (data or {}).get("claims") or []:
        if isinstance(item, dict) and str(item.get("text") or "").strip():
            claims[str(item["text"]).strip()] = str(item.get("category") or "other").strip()
    return claims


def _record(session: Session, decisions: list[Decision], *, lead: Lead, message: Message | None,
            channel: str, user_id) -> None:
    for d in decisions:
        session.add(ClaimVerificationLog(
            user_id=user_id, lead_id=lead.id, message_id=message.id if message else None,
            channel=channel[:20], field=d.field[:30], category=d.category[:30],
            claim_text=d.claim_text[:4000], verdict=d.verdict, extractor=d.extractor,
            evidence_source=d.evidence.source[:300] if d.evidence else None,
            evidence_excerpt=d.evidence.text[:1000] if d.evidence else None,
            unsupported_json=d.unsupported or None, replacement_text=d.replacement,
        ))


def _owner(session: Session, strategy: Strategy | None):
    if strategy is None:
        return None
    try:
        from app.services import notifications  # noqa: PLC0415

        return notifications.owner_of_strategy(session, strategy)
    except Exception:  # noqa: BLE001
        return None


def _enabled(session: Session) -> bool:
    from app.services import system_settings  # noqa: PLC0415

    return bool(system_settings.get(session, "claim_verification_enabled"))


def _signal_evidence(session: Session, lead: Lead, strategy: Strategy | None) -> list[Evidence]:
    """F02 Signal Radar: the lead's stored market-intel headlines, as `news`
    evidence. Empty unless signal_radar_enabled (so the ledger is unchanged
    with the flag off). Every other signal source is already in the ledger."""
    from app.services import signal_radar  # noqa: PLC0415

    if not signal_radar.enabled(session):
        return []
    return signal_radar.extra_evidence(session, lead, _owner(session, strategy))


def enforce(session: Session, *, strategy: Strategy | None, lead: Lead, message: Message | None,
            channel: str, fields: dict[str, str | None],
            decisions_out: list | None = None) -> dict[str, str | None]:
    """Verify every named field ("subject", "body", "first_message", ...) and
    return them with unsupported claims removed. Adds log rows (no commit).

    Field kinds, by name:
      "subject"          an unsupported claim rewrites it to GENERIC_SUBJECT
      "variable:<n>"     a template slot; rewritten to GENERIC_VALUE
      "item:<name>"      a list entry (a call talking point); an unsupported
                         claim DROPS it -- the returned value is ""
      anything else      prose: unsupported sentences stripped, the opening
                         hook rewritten

    `decisions_out`, when given, receives every Decision made (F02 Signal
    Radar ties a cited signal to its stored source through these)."""
    if not _enabled(session):
        return dict(fields)
    try:
        evidence = build_evidence(lead) + _signal_evidence(session, lead, strategy)
        sender_vocab = _sender_vocabulary(session, strategy)
        model_claims = _model_claims(session, [v for v in fields.values() if v])
        fail_closed = False
    except Exception:  # noqa: BLE001 -- see FAILS CLOSED in the module docstring
        logger.exception("claim verification setup failed for lead %s; failing closed", lead.id)
        evidence, sender_vocab, model_claims, fail_closed = [], "", {}, True

    # F05 Guardrails Studio: proof claims about US ("we helped 40 agencies
    # grow 3x") must be backed by the product's approved-claims library. None
    # = not applicable (feature off, or the product has no approved claims),
    # and then nothing below changes. See app/services/guardrails.py.
    from app.services import guardrails  # noqa: PLC0415

    sender = guardrails.sender_claim_context(session, strategy)

    out: dict[str, str | None] = {}
    decisions: list[Decision] = []
    for name, value in fields.items():
        is_item = name.startswith("item:")
        short = name == "subject" or name.startswith("variable:") or is_item
        empty = (GENERIC_SUBJECT if name == "subject" else
                 "" if is_item else GENERIC_VALUE if short else None)
        new_value, field_decisions = verify_text(
            value, field_name=name[:30], lead=lead, evidence=evidence,
            sender_vocab=sender_vocab, model_claims=model_claims, replace_empty_with=empty)
        if short and any(d.verdict != VERIFIED for d in field_decisions):
            new_value = empty
            for d in field_decisions:
                if d.verdict != VERIFIED:
                    if is_item:
                        d.verdict, d.replacement = STRIPPED, None
                    else:
                        d.verdict, d.replacement = REWRITTEN, empty
        if sender is not None and new_value:
            verified = frozenset(d.claim_text for d in field_decisions if d.verdict == VERIFIED)
            new_value, sender_decisions = guardrails.verify_sender_claims(
                new_value, sender.claims, field_name=name[:30], lead=lead,
                product_name=sender.product_name, short=short, empty=empty,
                verified_texts=verified)
            field_decisions += sender_decisions
        out[name] = new_value
        decisions += field_decisions
    if fail_closed:
        for d in decisions:
            d.unsupported = (d.unsupported or []) + ["verifier_error_fail_closed"]
    _record(session, decisions, lead=lead, message=message, channel=channel,
            user_id=_owner(session, strategy))
    if decisions_out is not None:
        decisions_out.extend(decisions)
    changed = [d for d in decisions if d.verdict != VERIFIED]
    if changed:
        logger.info("claim engine: %d unsupported claim(s) removed from message %s (lead %s)",
                    len(changed), getattr(message, "id", None), lead.id)
    return out


def lead_log(session: Session, lead_id, limit: int = 100) -> list[dict]:
    rows = session.execute(
        select(ClaimVerificationLog).where(ClaimVerificationLog.lead_id == lead_id)
        .order_by(ClaimVerificationLog.created_at.desc()).limit(limit)
    ).scalars().all()
    return [log_out(r) for r in rows]


def log_out(row: ClaimVerificationLog) -> dict:
    return {
        "id": str(row.id), "lead_id": str(row.lead_id) if row.lead_id else None,
        "message_id": str(row.message_id) if row.message_id else None,
        "channel": row.channel, "field": row.field, "category": row.category,
        "claim_text": row.claim_text, "verdict": row.verdict, "extractor": row.extractor,
        "evidence_source": row.evidence_source, "evidence_excerpt": row.evidence_excerpt,
        "unsupported": row.unsupported_json or [], "replacement_text": row.replacement_text,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
