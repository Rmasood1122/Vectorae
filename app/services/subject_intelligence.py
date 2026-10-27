"""
Subject line intelligence — extracts structural patterns from winning messages.

Wins on reply rate tell us WHICH variant won; this tells us WHY.

8 pattern types (regex/heuristic, no external NLP):
  question             Subject ends with "?"
  number               Subject contains a digit [0-9]
  personalized_company Subject contains [Company] placeholder or {company}
  pain_point_hook      Contains known pain-point trigger words
  curiosity_gap        Starts with "How" / "Why" / "What if" / "Did you"
  social_proof         Contains "customers", "clients", "case study", "results"
  direct_offer         Contains "free", "trial", "demo", "%", "discount"
  short_under_6_words  Word count ≤ 6
  long_over_10_words   Word count > 10

A subject can match multiple patterns (counted in each).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from app.core.logging import get_logger

logger = get_logger("services.subject_intelligence")


@dataclass
class SubjectLinePattern:
    # No example subject line: the table is one aggregate for the whole
    # install, so any stored subject would be another account's (a sent
    # subject is already filled in with the prospect's name or company).
    pattern_type: str
    avg_reply_rate: float
    avg_meeting_rate: float
    sample_size: int
    is_reliable: bool
    channel: str


# ---------------------------------------------------------------------------
# Pattern classifiers
# ---------------------------------------------------------------------------

_PAIN_POINT_WORDS = re.compile(
    r"\b(struggling|wasting|losing|problem|challenge|pain|issue|struggle|cost|slow|manual|inefficient)\b",
    re.IGNORECASE,
)
_CURIOSITY_WORDS = re.compile(
    r"^(how|why|what if|did you|have you|ever wonder|wondering)",
    re.IGNORECASE,
)
_SOCIAL_PROOF_WORDS = re.compile(
    r"\b(customer|client|case study|results|revenue|roi|success|testimonial)\b",
    re.IGNORECASE,
)
_DIRECT_OFFER_WORDS = re.compile(
    r"\b(free|trial|demo|%|discount|offer|limited|exclusive|save)\b",
    re.IGNORECASE,
)
_COMPANY_PLACEHOLDER = re.compile(
    r"\{company\}|\[company\]|\{first_name\}|\[first_name\]",
    re.IGNORECASE,
)
_DIGIT = re.compile(r"\d")


def classify_subject(subject: str) -> list[str]:
    """Return all matching pattern types for a subject line string."""
    if not subject:
        return []

    patterns = []
    words = subject.strip().split()
    word_count = len(words)

    if subject.strip().endswith("?"):
        patterns.append("question")

    if _DIGIT.search(subject):
        patterns.append("number")

    if _COMPANY_PLACEHOLDER.search(subject):
        patterns.append("personalized_company")

    if _PAIN_POINT_WORDS.search(subject):
        patterns.append("pain_point_hook")

    if _CURIOSITY_WORDS.match(subject.strip()):
        patterns.append("curiosity_gap")

    if _SOCIAL_PROOF_WORDS.search(subject):
        patterns.append("social_proof")

    if _DIRECT_OFFER_WORDS.search(subject):
        patterns.append("direct_offer")

    if word_count <= 6:
        patterns.append("short_under_6_words")

    if word_count > 10:
        patterns.append("long_over_10_words")

    return patterns


def anonymize_subject(subject: str) -> str:
    """Replace likely company/person names with placeholders for safe storage."""
    # Replace content already in {braces} or [brackets]
    s = re.sub(r"\{[^}]+\}", "[Company]", subject)
    s = re.sub(r"\[[^\]]+\]", "[Company]", s)
    return s


# ---------------------------------------------------------------------------
# Pattern extraction from outcomes
# ---------------------------------------------------------------------------

def extract_patterns_from_outcomes(
    db_session,
    strategy_id: Optional[str] = None,
) -> list[SubjectLinePattern]:
    """
    Read messages with subject lines that have linked outcomes.
    Group by (pattern_type, channel), compute rates.
    Returns list sorted by avg_meeting_rate descending.
    """
    from sqlalchemy import text
    from app.core.config import settings

    params: dict = {}
    where_extra = ""
    if strategy_id:
        where_extra = "AND seq.strategy_id = :strategy_id"
        params["strategy_id"] = strategy_id

    sql = text(f"""
        SELECT
            m.subject,
            m.channel,
            COUNT(*) FILTER (WHERE o.event = 'sent') AS sends,
            COUNT(*) FILTER (WHERE o.event = 'replied') AS replies,
            COUNT(*) FILTER (WHERE o.event = 'booked') AS bookings
        FROM messages m
        JOIN sequences seq ON seq.id = m.sequence_id
        LEFT JOIN outcomes o ON o.message_id = m.id
        WHERE m.subject IS NOT NULL
          AND m.subject <> ''
          {where_extra}
        GROUP BY m.subject, m.channel
        HAVING COUNT(*) FILTER (WHERE o.event = 'sent') > 0
    """)

    try:
        rows = db_session.execute(sql, params).mappings().all()
    except Exception as e:
        # REPAIR: rollback — see send_time_optimizer note.
        try:
            db_session.rollback()
        except Exception:
            pass
        logger.error("subject_intelligence.query_failed", error=str(e))
        return []

    # Accumulate stats per (pattern_type, channel)
    from collections import defaultdict
    accum: dict[tuple, dict] = defaultdict(
        lambda: {"sends": 0, "replies": 0, "bookings": 0}
    )

    for row in rows:
        subject = row["subject"] or ""
        channel = row["channel"] or "gmail"
        sends = row["sends"] or 0
        replies = row["replies"] or 0
        bookings = row["bookings"] or 0

        ptypes = classify_subject(subject)
        for ptype in ptypes:
            key = (ptype, channel)
            accum[key]["sends"] += sends
            accum[key]["replies"] += replies
            accum[key]["bookings"] += bookings

    min_sample = settings.PLAYBOOK_MIN_SAMPLE
    patterns: list[SubjectLinePattern] = []

    for (ptype, channel), data in accum.items():
        sends = max(data["sends"], 1)
        pattern = SubjectLinePattern(
            pattern_type=ptype,
            avg_reply_rate=round(data["replies"] / sends, 4),
            avg_meeting_rate=round(data["bookings"] / sends, 4),
            sample_size=data["sends"],
            is_reliable=data["sends"] >= min_sample,
            channel=channel,
        )
        patterns.append(pattern)

    patterns.sort(key=lambda p: p.avg_meeting_rate, reverse=True)
    logger.info("subject_intelligence.extracted", pattern_count=len(patterns))
    return patterns


def persist_patterns(db_session, patterns: list[SubjectLinePattern]) -> None:
    """Upsert subject line patterns into the subject_line_patterns table.

    `example` is written NULL, which also clears a subject line stored by
    the code before 2026-10-27.
    """
    import datetime
    import uuid
    from sqlalchemy import text

    now = datetime.datetime.utcnow()
    for p in patterns:
        db_session.execute(text("""
            INSERT INTO subject_line_patterns
                (id, pattern_type, channel, avg_reply_rate, avg_meeting_rate,
                 sample_size, is_reliable, example, last_updated)
            VALUES
                (:id, :ptype, :channel, :reply_rate, :meeting_rate,
                 :sample, :reliable, NULL, :now)
            ON CONFLICT (pattern_type, channel) DO UPDATE SET
                avg_reply_rate = EXCLUDED.avg_reply_rate,
                avg_meeting_rate = EXCLUDED.avg_meeting_rate,
                sample_size = EXCLUDED.sample_size,
                is_reliable = EXCLUDED.is_reliable,
                example = NULL,
                last_updated = EXCLUDED.last_updated
        """), {
            # Supplied here, not left to the column default, which SQLite lacks.
            "id": str(uuid.uuid4()),
            "ptype": p.pattern_type,
            "channel": p.channel,
            "reply_rate": p.avg_reply_rate,
            "meeting_rate": p.avg_meeting_rate,
            "sample": p.sample_size,
            "reliable": p.is_reliable,
            "now": now,
        })
    db_session.commit()


def get_top_patterns(
    db_session,
    channel: str = "gmail",
    top_n: int = 5,
) -> list[SubjectLinePattern]:
    """
    Read top patterns from the subject_line_patterns table.
    Returns sorted by avg_meeting_rate desc, reliable-only first.
    """
    from sqlalchemy import text

    try:
        rows = db_session.execute(text("""
            SELECT pattern_type, channel, avg_reply_rate, avg_meeting_rate,
                   sample_size, is_reliable
            FROM subject_line_patterns
            WHERE channel = :channel AND is_reliable = true
            ORDER BY avg_meeting_rate DESC
            LIMIT :n
        """), {"channel": channel, "n": top_n}).mappings().all()

        return [SubjectLinePattern(
            pattern_type=row["pattern_type"],
            avg_reply_rate=row["avg_reply_rate"],
            avg_meeting_rate=row["avg_meeting_rate"],
            sample_size=row["sample_size"],
            is_reliable=row["is_reliable"],
            channel=row["channel"],
        ) for row in rows]
    except Exception as e:
        try:
            db_session.rollback()
        except Exception:
            pass
        logger.error("subject_intelligence.get_top_failed", error=str(e))
        return []


def build_phase6_pattern_context(patterns: list[SubjectLinePattern]) -> str:
    """
    Build the prompt-injection text for the Phase 6 Claude system prompt.
    Returns empty string (not an error) if no reliable patterns exist.
    """
    reliable = [p for p in patterns if p.is_reliable]
    if not reliable:
        return ""

    top = reliable[:3]
    lines = [
        "Based on past campaign data, the following subject line patterns have shown "
        "the highest reply rates for this channel and ICP. Prefer these structural "
        "patterns in your subject line recommendations:"
    ]
    for p in top:
        lines.append(
            f"  - {p.pattern_type.replace('_', ' ').title()}: "
            f"{p.avg_reply_rate:.0%} reply rate, {p.avg_meeting_rate:.0%} meeting rate "
            f"(n={p.sample_size})."
        )
    return "\n".join(lines)
