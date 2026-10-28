"""Feature 4 — competitor displacement alerts.

Watches the LinkedIn posts of every lead already in the CRM, at any status, for
the moment they say in public that their current approach is not working. When
one does, it writes an alert carrying the evidence and a DM that references
that specific post.

WHY THIS BEATS COLD OUTREACH. A lead who has just posted "cold email is not
working for us" has pre-qualified themselves and told you the opening line. The
whole feature exists to make the gap between that post and a relevant message
minutes instead of never.

THREE TRIGGER GROUPS, ONE MATCH IS ENOUGH (see TRIGGER_GROUPS). Competitor
tool names are matched on word boundaries so "Clay" does not fire on
"declaim"; the pain phrases are matched as substrings because they are already
specific enough that a false positive is nearly impossible.

DEDUPLICATION AND EXPIRY. One alert per lead per DEDUP_DAYS (14), so a founder
who posts about the same frustration three times in a week is not three alerts.
Each alert is stale after EXPIRY_DAYS (7): a "saw your post" DM sent two weeks
later reads worse than no DM at all.

THE DM IS A DRAFT. Nothing here sends. The alert lands in a list a human reads.
"""

import logging
import re
import uuid as uuid_module
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import DisplacementAlert, Lead, LeadStatus, Strategy
from app.services import prompt_safety
from app.services.anthropic_client import get_client
from app.services.reply_intelligence import BANNED_WORDS, find_banned_words

logger = logging.getLogger(__name__)

TRIGGER_GROUPS = {
    # Named products. Word-boundary matched: "Clay" must not fire on
    # "declaim", and "Apollo" must not fire on "Apollonian".
    "competitor_tools": [
        "Apollo", "Clay", "Instantly", "Smartlead", "Outreach.io",
        "Salesloft", "Lemlist",
    ],
    # Phrases about the pipeline itself. Substring matched -- each is already
    # specific enough that a false positive is close to impossible.
    "pipeline_pain": [
        "pipeline dried up", "no new clients", "cold email not working",
        "outreach not converting", "prospecting is killing me",
    ],
    # Phrases about the people doing the prospecting.
    "people_pain": [
        "fired my SDR", "VA not performing", "outsourced sales",
        "can't find clients",
    ],
}

# Groups whose entries are product names and must match whole words.
_WORD_BOUNDARY_GROUPS = ("competitor_tools",)

EXCERPT_CHARS = 200
DEDUP_DAYS = 14
EXPIRY_DAYS = 7
MAX_TOKENS = 1000

# Day 0 DM rules, enforced on the generated draft (see validate_dm).
MAX_SENTENCES = 4
MAX_QUESTIONS = 1
# The product must not name itself in a first touch. A Day 0 DM that pitches
# is a Day 0 DM that gets ignored. Every name the product has had is listed:
# the current one (Vectorae) and the earlier ones a model may still produce.
FORBIDDEN_MENTIONS = ("victorae", "vectorae", "leadpilot", "lead pilot", "clienthunter",
                      "client hunter")

_DM_SYSTEM = (
    "You are Vectorae's displacement DM writer. A prospect has just posted "
    "publicly about a problem you can solve. You write the FIRST message to "
    "them -- a Day 0 cold DM. Respond with ONLY a JSON object, no prose and "
    'no markdown fences: {"dm": "the message"}\n'
    f"RULES, all of them hard. At most {MAX_SENTENCES} sentences. Exactly one "
    "question, at the end. Reference something SPECIFIC they wrote -- quote "
    "their own words where you can; a message that would work for any "
    "prospect is a failure. Never name a product, a company or a service, "
    "including your own. Never pitch, never offer a demo, never mention "
    "pricing. Never invent a statistic, a mutual connection or a case study. "
    "No greeting line and no sign-off, just the message.\n"
    "NEVER use any of these words: " + ", ".join(BANNED_WORDS) + "."
)

# F-AI-02: the post is scraped from LinkedIn and the name, title and company
# come from enrichment -- outside text, delimited and escaped.
_DM_PROMPT = """{notice}

WHAT THEY POSTED:
{post_excerpt}

WHAT FIRED THE ALERT (the words that matched): {matched}

WHO THEY ARE:
- Name: {full_name}
- Title: {title}
- Company: {company}

Write the Day 0 DM now."""

DM_SCHEMA = {
    "type": "object",
    "properties": {"dm": {"type": "string"}},
    "required": ["dm"],
    "additionalProperties": False,
}


def scan_post_for_triggers(post_text: str) -> list[str]:
    """Every trigger keyword or phrase present in one post.

    WHAT IT RETURNS. The matched keywords in their canonical (configured)
    spelling, deduplicated, in TRIGGER_GROUPS order -- empty when the post is
    not interesting. The caller treats a non-empty list as "alert".

    WHAT IT NEVER RAISES. Anything; None or a non-string is simply no match.
    """
    text = str(post_text or "")
    if not text.strip():
        return []
    lowered = text.lower()
    matched: list[str] = []
    for group, keywords in TRIGGER_GROUPS.items():
        boundary = group in _WORD_BOUNDARY_GROUPS
        for keyword in keywords:
            needle = keyword.lower()
            hit = (re.search(rf"\b{re.escape(needle)}\b", lowered) if boundary
                   else needle in lowered)
            if hit and keyword not in matched:
                matched.append(keyword)
    return matched


def excerpt_of(post_text: str, limit: int = EXCERPT_CHARS) -> str:
    """The first `limit` characters of a post, whitespace-normalised."""
    return re.sub(r"\s+", " ", str(post_text or "").strip())[:limit]


def validate_dm(dm: str) -> list[str]:
    """Every Day 0 rule `dm` breaks.

    WHAT IT RETURNS. A list of human-readable rule violations, empty when the
    draft is compliant. Kept separate from generation so the same rules can be
    asserted in tests and reused if another surface ever writes a Day 0 DM.

    WHAT IT NEVER RAISES. Anything; empty input is reported as a violation,
    not an exception.
    """
    text = (dm or "").strip()
    problems: list[str] = []
    if not text:
        return ["the draft is empty"]

    sentences = [part for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]
    if len(sentences) > MAX_SENTENCES:
        problems.append(f"it is {len(sentences)} sentences, the limit is {MAX_SENTENCES}")
    questions = text.count("?")
    if questions > MAX_QUESTIONS:
        problems.append(f"it asks {questions} questions, the limit is {MAX_QUESTIONS}")
    if questions == 0:
        problems.append("it asks no question")
    lowered = text.lower()
    named = [name for name in FORBIDDEN_MENTIONS if name in lowered]
    if named:
        problems.append(f"it names the product ({named[0]})")
    banned = find_banned_words(text)
    if banned:
        problems.append(f"it uses banned words: {', '.join(banned)}")
    return problems


def generate_displacement_dm(lead: Lead, post_excerpt: str,
                             matched_keywords: list[str]) -> str | None:
    """Write a Day 0 DM that references this specific post.

    WHAT IT DOES. Asks Claude for a compliant Day 0 message, validates it
    against every rule in validate_dm, and makes ONE repair attempt naming the
    violations before giving up.

    WHAT IT RETURNS. The DM, or None when no compliant draft could be
    produced. None is deliberate: an alert with no DM still shows the founder
    the post and the matched words, which is the valuable half, whereas a
    non-compliant DM would be a rule-breaking message one click from being
    sent.

    WHAT IT NEVER RAISES. Anything. A model outage returns None and the sweep
    keeps going -- one un-drafted alert must not cost the other forty.
    """
    prompt = _DM_PROMPT.format(
        notice=prompt_safety.NOTICE,
        post_excerpt=prompt_safety.block(post_excerpt),
        matched=", ".join(matched_keywords) or "(none)",
        full_name=prompt_safety.inline(getattr(lead, "full_name", None) or "there"),
        title=prompt_safety.inline(getattr(lead, "title", None) or "unknown"),
        company=prompt_safety.inline(getattr(lead, "company", None) or "their company"),
    )
    system = _DM_SYSTEM
    try:
        for attempt in (1, 2):
            data = get_client().complete_json(system=system, prompt=prompt,
                                              max_tokens=MAX_TOKENS, schema=DM_SCHEMA)
            dm = str((data or {}).get("dm") or "").strip()
            problems = validate_dm(dm)
            if not problems:
                return dm
            if attempt == 2:
                break
            system = (_DM_SYSTEM + "\nYOUR PREVIOUS ANSWER WAS REJECTED because "
                      + "; ".join(problems) + ". Rewrite it, same angle.")
        logger.warning("displacement DM for lead %s rejected twice (%s)",
                       getattr(lead, "id", None), "; ".join(problems))
    except Exception:  # noqa: BLE001 -- see the docstring
        logger.exception("displacement DM generation failed for lead %s",
                         getattr(lead, "id", None))
    return None


def recently_alerted(db: Session, lead_id, now: datetime,
                     days: int = DEDUP_DAYS) -> bool:
    """True when this lead already has an alert inside the dedup window."""
    since = now - timedelta(days=days)
    return db.execute(
        select(DisplacementAlert.id)
        .where(DisplacementAlert.lead_id == lead_id,
               DisplacementAlert.created_at >= since)
        .order_by(DisplacementAlert.created_at.desc())
        .limit(1)
    ).scalars().first() is not None


def watchlist(db: Session, strategy_id) -> list[Lead]:
    """The leads in a strategy worth watching: any status, but they must have
    a LinkedIn profile to read and must not have been erased.

    DROPPED covers both the suppressed and the GDPR-deleted (whose
    linkedin_url is NULL anyway). Every other status is in scope on purpose --
    a lead who went quiet at `contacted` posting "cold email is not working"
    is precisely the person this feature exists for.
    """
    from app.services.local_business import CUSTOMER_SOURCE  # noqa: PLC0415

    return db.execute(
        select(Lead).where(Lead.strategy_id == strategy_id,
                           Lead.linkedin_url.isnot(None),
                           Lead.status != LeadStatus.DROPPED,
                           # Local business mode: consumers are never watched.
                           Lead.source != CUSTOMER_SOURCE)
    ).scalars().all()


def _posts_for(db: Session, lead: Lead, owner_id, now: datetime) -> list[dict]:
    """This lead's recent posts, refreshed through the normal cached path.

    Reuses app/services/personalization_context.py::ensure_fresh rather than
    calling Unipile directly, so the 7-day cache, the provider fallback and
    the admin kill switch are all shared with the personalization path -- a
    twelve-hourly sweep must not re-fetch every lead's posts twice a day.
    Never raises.
    """
    from app.services import personalization_context  # noqa: PLC0415

    try:
        personalization_context.ensure_fresh(db, lead, owner_id=owner_id, now=now)
    except Exception:  # noqa: BLE001 -- a provider outage is not a sweep failure
        logger.warning("displacement: could not refresh posts for lead %s", lead.id)
    posts = lead.linkedin_posts_json or []
    return [post for post in posts if isinstance(post, dict)]


def run_displacement_scan(db: Session, strategy_id, now: datetime | None = None) -> int:
    """Scan one strategy's watch list and create alerts for what fired.

    WHAT IT DOES. For each watched lead: refreshes their recent posts through
    the shared cached path, checks each post against TRIGGER_GROUPS, and (when
    the lead is outside the 14-day dedup window) writes ONE alert for the
    first post that fired, with a generated Day 0 DM and a 7-day expiry.

    WHAT IT RETURNS. The number of new alerts created.

    WHAT IT NEVER RAISES. Anything. A per-lead failure is logged and the sweep
    continues; an unknown strategy returns 0. This runs unattended twice a day
    across every strategy, and one lead with a malformed cached post must not
    stop the other thirty-nine from being scanned.
    """
    now = now or datetime.now(timezone.utc)
    try:
        if not isinstance(strategy_id, uuid_module.UUID):
            strategy_id = uuid_module.UUID(str(strategy_id))
        strategy = db.get(Strategy, strategy_id)
        if strategy is None:
            return 0

        from app.services.notifications import owner_of_strategy  # noqa: PLC0415

        owner_id = owner_of_strategy(db, strategy)
        leads = watchlist(db, strategy_id)
    except Exception:  # noqa: BLE001 -- see the docstring
        logger.exception("displacement scan could not start for strategy %s", strategy_id)
        return 0

    from app.services import usage_meter  # noqa: PLC0415

    # P0-5b: the DMs are model calls, metered to the owner and on its daily
    # AI limit (a lead skipped today is scanned again in 12 hours).
    with usage_meter.user_scope(db, owner_id, "displacement"):
        created = 0
        for lead in leads:
            try:
                if recently_alerted(db, lead.id, now):
                    continue
                for post in _posts_for(db, lead, owner_id, now):
                    matched = scan_post_for_triggers(post.get("text"))
                    if not matched:
                        continue
                    excerpt = excerpt_of(post.get("text"))
                    alert = DisplacementAlert(
                        lead_id=lead.id,
                        matched_keywords=matched,
                        post_excerpt=excerpt,
                        post_url=(str(post.get("url"))[:500] if post.get("url") else None),
                        suggested_dm=generate_displacement_dm(lead, excerpt, matched),
                        alert_status="pending",
                        created_at=now,
                        expires_at=now + timedelta(days=EXPIRY_DAYS),
                    )
                    db.add(alert)
                    db.commit()
                    created += 1
                    # One alert per lead per sweep: the dedup window is per lead,
                    # not per post, so a founder who made the same complaint three
                    # times this week is one alert.
                    break
            except Exception:  # noqa: BLE001 -- see the docstring
                logger.exception("displacement scan failed for lead %s", lead.id)
                try:
                    db.rollback()
                except Exception:  # noqa: BLE001
                    pass
    return created


def is_expired(alert: DisplacementAlert, now: datetime | None = None) -> bool:
    """True when a still-pending alert is past its expiry. Never raises."""
    now = now or datetime.now(timezone.utc)
    expires = alert.expires_at
    if expires is None:
        return False
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return alert.alert_status == "pending" and expires <= now


def effective_status(alert: DisplacementAlert, now: datetime | None = None) -> str:
    """The status to SHOW: "expired" for a stale pending alert, else stored.

    Expiry is derived rather than swept. A nightly job flipping rows to
    "expired" would be a second source of truth for something `expires_at`
    already states exactly, and it would make an alert's status depend on
    whether that job ran. Never raises.
    """
    return "expired" if is_expired(alert, now) else alert.alert_status
