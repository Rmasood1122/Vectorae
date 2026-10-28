"""Fit / Problem / Timing / Access scoring (Part 1, Feature 2).

WHY FOUR NUMBERS AND NOT ONE
`ai_booking_likelihood` (FG1) answers "will they book?" in a single 0-100.
That number is useful and unchanged, but it erases the difference between the
four ways a prospect can be wrong, and those four want opposite actions:

  FIT      Are they the kind of company and person this offer is for?
           Wrong fit -> do not contact. No amount of timing rescues it.
  PROBLEM  Is there evidence they HAVE the problem we solve?
           No evidence -> the message has no hook; go find one first.
  TIMING   Is something happening right now that makes this the moment?
           No timing -> contact them, but expect a "not now" (and Feature 7
           will schedule the return trip).
  ACCESS   Can we actually reach this person, on a channel they answer?
           No access -> the other three do not matter yet; fix the route.

TWO LAYERS, same shape as lead_scoring.py:
  1. A deterministic BASELINE per dimension, computed from data we hold, with
     the evidence recorded as a list of short signal strings. This is what
     makes a score explainable (Feature 12 renders exactly these) and what
     survives a model outage.
  2. ONE Claude call per batch that writes a human sentence per dimension and
     may move each baseline by at most +/-MAX_ADJUSTMENT points, grounded in
     the data it was given. The clamp is enforced HERE, not asked for in the
     prompt.

NOTHING IS INVENTED. A dimension with no evidence scores its documented
neutral baseline and says so ("no visible problem signal"), rather than
borrowing confidence from another dimension.
"""

from __future__ import annotations

import json
import logging
import re
import uuid as uuid_module
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Lead, LeadStatus, Message, MessageStatus, Product, Strategy
from app.services import anthropic_client, lead_scoring, prompt_safety
from app.services.icp_extraction import stored_icp

logger = logging.getLogger(__name__)

FIT, PROBLEM, TIMING, ACCESS = "fit", "problem", "timing", "access"
DIMENSIONS = (FIT, PROBLEM, TIMING, ACCESS)

#: Fit and problem carry the most weight because they decide whether the
#: prospect should be contacted at all; timing and access decide when and how.
WEIGHTS = {FIT: 0.30, PROBLEM: 0.30, TIMING: 0.20, ACCESS: 0.20}

BATCH_SIZE = 10
MAX_ADJUSTMENT = 15
MAX_SIGNALS = 5

#: A dimension with nothing to go on lands here -- deliberately below the
#: middle, so "we know nothing" never reads as "this is fine".
NEUTRAL = 0.4

RECENT_DAYS = 90
FUNDING_DAYS = 548

_PAIN_WORDS = re.compile(
    r"\b(struggl\w*|bottleneck|backlog|manual\w*|spreadsheet|behind|overwhelm\w*|"
    r"drowning|churn\w*|bleed\w*|losing|lost|slow(er|ing)?|broken|painful|"
    r"headache|nightmare|can'?t keep up|firefight\w*|short[- ]staffed|"
    r"under[- ]resourced|no bandwidth|hiring|understaffed|turnover)\b", re.I)

_GROWTH_WORDS = re.compile(
    r"\b(hiring|we'?re growing|expand\w*|new office|scal\w*|raised|funding|"
    r"series [a-d]|acquisition|acquired|launch\w*|new (role|hire|head of)|"
    r"just joined|promoted|announcement)\b", re.I)

SYSTEM = (
    "You are Vectorae's F-P-T-A prospect analyst. For each prospect you are "
    "given four deterministic BASELINE sub-scores (0-100) and the exact "
    "evidence each was computed from:\n"
    "  fit      - are they the company and person this offer is for?\n"
    "  problem  - is there evidence they HAVE the problem it solves?\n"
    "  timing   - is something happening now that makes this the moment?\n"
    "  access   - can we reach this person on a channel they answer?\n"
    "Write ONE short sentence per dimension explaining the score to a "
    "salesperson, and optionally adjust each score. You may move a score at "
    "most 15 points from its baseline, and ONLY for a reason grounded in the "
    "evidence given.\n"
    "Respond with ONLY a JSON object:\n"
    '{"prospects": [{"lead_id": "...", "fit": {"score": 0-100, "reason": '
    '"..."}, "problem": {...}, "timing": {...}, "access": {...}}]}\n'
    "NEVER invent a fact about a prospect. If a dimension has no evidence, "
    "say so plainly -- 'no visible problem signal' is a useful answer and a "
    "fabricated one is not."
)

_DIMENSION_SCHEMA = {
    "type": "object",
    "properties": {"score": {"type": ["number", "string"]}, "reason": {"type": "string"}},
    "required": ["score", "reason"],
    "additionalProperties": False,
}

# Output contract (Phase 3 item 2). The +/-MAX_ADJUSTMENT clamp is enforced in
# _resolve, never by the schema (the API rejects minimum/maximum). The score
# may come as a string: _resolve coerces it with float().
FPTA_SCHEMA = {
    "type": "object",
    "properties": {
        "prospects": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"lead_id": {"type": "string"},
                               **{name: _DIMENSION_SCHEMA for name in DIMENSIONS}},
                "required": ["lead_id", *DIMENSIONS],
                "additionalProperties": False,
            },
        },
    },
    "required": ["prospects"],
    "additionalProperties": False,
}


# --------------------------------------------------------------------------
# Helpers over the enrichment blob
# --------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _parse_date(value) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _texts(lead: Lead) -> list[str]:
    """Everything the prospect or the press has said, as plain strings."""
    out: list[str] = []
    for post in (lead.linkedin_posts_json or []):
        if isinstance(post, dict) and post.get("text"):
            out.append(str(post["text"]))
    for item in (lead.company_news_json or []):
        if isinstance(item, dict):
            out.append(" ".join(str(item.get(k) or "") for k in ("headline", "summary")))
    org = lead_scoring._org(lead)
    for key in ("short_description", "description"):
        if org.get(key):
            out.append(str(org[key]))
    return [t for t in out if t.strip()]


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _pct(value: float) -> int:
    return max(0, min(100, int(round(_clamp(value) * 100))))


# --------------------------------------------------------------------------
# The four deterministic baselines
# --------------------------------------------------------------------------


def fit_signals(lead: Lead, icp: dict) -> tuple[float, list[str]]:
    """Right company, right person."""
    signals: list[str] = []
    seniority = lead_scoring.seniority(lead.title)
    if lead.title:
        signals.append(f"Title: {lead.title}")
    else:
        signals.append("No title on file")

    industry = lead_scoring.industry_match(lead, icp)
    org = lead_scoring._org(lead)
    if org.get("industry"):
        signals.append(f"Industry: {org['industry']}")
    if industry >= 1.0:
        signals.append("Industry matches the ICP exactly")
    elif industry <= 0.15:
        signals.append("Industry is outside the ICP")

    size = org.get("estimated_num_employees")
    in_range = lead_scoring._size_in_ranges(size, icp.get("company_size_ranges"))
    size_score = 0.5
    if in_range is True:
        size_score = 1.0
        signals.append(f"Headcount {size} is inside the ICP range")
    elif in_range is False:
        size_score = 0.15
        signals.append(f"Headcount {size} is outside the ICP range")
    elif size:
        signals.append(f"Headcount {size} (no ICP range set)")

    score = 0.40 * seniority + 0.40 * industry + 0.20 * size_score
    return _clamp(score), signals


def problem_signals(lead: Lead, icp: dict) -> tuple[float, list[str]]:
    """Evidence they HAVE the problem -- in their own words where possible.

    Quotes the prospect rather than paraphrasing: a hook built on their
    sentence survives being read back to them, and an inferred one does not.
    """
    signals: list[str] = []
    score = NEUTRAL
    keywords = [str(k).lower() for k in (icp.get("keywords") or []) if str(k).strip()]

    for text in _texts(lead):
        hit = _PAIN_WORDS.search(text)
        if hit:
            score += 0.18
            signals.append(f'Said: "{_excerpt(text, hit.start())}"')
        matched = [k for k in keywords if k in text.lower()]
        if matched:
            score += 0.12
            signals.append(f"Mentions {', '.join(matched[:3])}")
        if len(signals) >= MAX_SIGNALS:
            break

    if not signals:
        signals.append("No visible problem signal")
        score = 0.25
    return _clamp(score), signals[:MAX_SIGNALS]


def _excerpt(text: str, at: int, width: int = 90) -> str:
    start = max(0, at - width // 3)
    return " ".join(text[start:start + width].split()) + ("…" if len(text) > start + width else "")


def timing_signals(lead: Lead, icp: dict, now: datetime) -> tuple[float, list[str]]:
    """Is something happening right now that makes this the moment?"""
    signals: list[str] = []
    score = NEUTRAL
    org = lead_scoring._org(lead)

    funded = _parse_date(org.get("latest_funding_round_date"))
    if funded and funded >= now - timedelta(days=FUNDING_DAYS):
        score += 0.25
        signals.append(f"Raised funding {funded.date().isoformat()}")

    for item in (lead.company_news_json or [])[:MAX_SIGNALS]:
        published = _parse_date(item.get("published_at")) if isinstance(item, dict) else None
        if published and published >= now - timedelta(days=RECENT_DAYS):
            score += 0.15
            signals.append(f"In the news: {str(item.get('headline') or '')[:90]}")

    for post in (lead.linkedin_posts_json or [])[:MAX_SIGNALS]:
        posted = _parse_date(post.get("posted_at")) if isinstance(post, dict) else None
        if posted and posted >= now - timedelta(days=30):
            score += 0.10
            signals.append("Posted on LinkedIn in the last 30 days")
            break

    for text in _texts(lead):
        hit = _GROWTH_WORDS.search(text)
        if hit:
            score += 0.12
            signals.append(f'Change signal: "{_excerpt(text, hit.start(), 70)}"')
            break

    if not signals:
        signals.append("No recent activity on file")
        score = 0.30
    return _clamp(score), signals[:MAX_SIGNALS]


def access_signals(lead: Lead) -> tuple[float, list[str]]:
    """Can we reach this person, on a channel they answer?

    The only dimension that can be checked rather than inferred, so it is
    scored from facts alone.
    """
    signals: list[str] = []
    score = 0.0
    if lead.status is LeadStatus.VERIFIED and lead.email:
        score += 0.45
        signals.append("Verified email address")
    elif lead.email:
        score += 0.20
        signals.append("Email address, not verified")
    else:
        signals.append("No email address")

    if lead.linkedin_connection_status == "connected":
        score += 0.30
        signals.append("Connected on LinkedIn")
    elif lead.linkedin_url:
        score += 0.15
        signals.append("LinkedIn profile on file")

    if lead.phone and lead.phone_consent_at:
        score += 0.15
        signals.append("Phone with recorded consent")
    elif lead.phone:
        score += 0.05
        signals.append("Phone number, no consent recorded")

    if lead.whatsapp_opted_in:
        score += 0.10
        signals.append("Opted in to WhatsApp")

    return _clamp(score), signals[:MAX_SIGNALS]


def baselines(lead: Lead, icp: dict, now: datetime | None = None) -> dict:
    """The four deterministic sub-scores and their evidence.

    RETURNS {dimension: {"baseline": 0-100, "signals": [...]}}.
    """
    now = now or _now()
    fit, fit_why = fit_signals(lead, icp)
    problem, problem_why = problem_signals(lead, icp)
    timing, timing_why = timing_signals(lead, icp, now)
    access, access_why = access_signals(lead)
    pairs = {FIT: (fit, fit_why), PROBLEM: (problem, problem_why),
             TIMING: (timing, timing_why), ACCESS: (access, access_why)}
    return {name: {"baseline": _pct(value), "signals": why}
            for name, (value, why) in pairs.items()}


def overall(scores: dict) -> int:
    """The weighted overall. Rounded once, at the end."""
    total = sum(WEIGHTS[name] * float(scores[name]) for name in DIMENSIONS)
    return max(0, min(100, int(round(total))))


def heuristic_reason(dimension: str, entry: dict) -> str:
    """The sentence used when the model is unavailable -- the evidence itself,
    which is the honest answer rather than a generated one."""
    signals = entry.get("signals") or []
    lead_in = {FIT: "Fit", PROBLEM: "Problem evidence", TIMING: "Timing",
               ACCESS: "Reachability"}[dimension]
    return f"{lead_in}: {'; '.join(signals[:3])}." if signals else f"{lead_in}: nothing on file."


# --------------------------------------------------------------------------
# The model pass
# --------------------------------------------------------------------------


def _prospect_line(lead: Lead, base: dict) -> dict:
    org = lead_scoring._org(lead)
    return {
        "lead_id": str(lead.id),
        "name": lead.full_name, "title": lead.title, "company": lead.company,
        "industry": org.get("industry"),
        "employees": org.get("estimated_num_employees"),
        "baselines": {name: base[name]["baseline"] for name in DIMENSIONS},
        "evidence": {name: base[name]["signals"] for name in DIMENSIONS},
    }


def _model_pass(batch: list[tuple[Lead, dict]], brief: str, icp: dict) -> dict[str, dict]:
    # The prospect rows carry the lead's name, title and company, enrichment
    # fields and scraped post excerpts (the evidence): all written outside
    # this system, so the whole list goes in one untrusted block (F-AI-02).
    prospects = json.dumps([_prospect_line(l, b) for l, b in batch], default=str)
    prompt = (f"PRODUCT: {brief}\nICP: {json.dumps(icp, default=str)[:2000]}\n\n"
              f"{prompt_safety.NOTICE}\n\n"
              f"PROSPECTS:\n{prompt_safety.block(prospects)}"
              "\n\nReturn the JSON object now.")
    data = anthropic_client.get_client().complete_json(system=SYSTEM, prompt=prompt,
                                                       max_tokens=3000, schema=FPTA_SCHEMA)
    out: dict[str, dict] = {}
    for row in (data or {}).get("prospects") or []:
        if isinstance(row, dict) and row.get("lead_id"):
            out[str(row["lead_id"])] = row
    return out


def _resolve(dimension: str, entry: dict, row: dict | None) -> tuple[int, str, str]:
    """Baseline + the model's opinion -> (score, reason, method), clamped."""
    baseline = int(entry["baseline"])
    proposed = row.get(dimension) if isinstance(row, dict) else None
    if not isinstance(proposed, dict):
        return baseline, heuristic_reason(dimension, entry), "heuristic"
    try:
        value = int(round(float(proposed.get("score"))))
        score = max(baseline - MAX_ADJUSTMENT, min(baseline + MAX_ADJUSTMENT, value))
        score = max(0, min(100, score))
    except (TypeError, ValueError):
        score = baseline
    reason = " ".join(str(proposed.get("reason") or "").split())[:300]
    if not reason:
        return score, heuristic_reason(dimension, entry), "heuristic"
    return score, reason, "model"


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------


def score_leads(session: Session, strategy: Strategy, leads: list[Lead],
                now: datetime | None = None, use_model: bool = True) -> int:
    """Score `leads` in place and commit. Returns how many were scored.

    Never raises for a model failure: every lead in a failed batch keeps its
    deterministic baseline and is marked `method: heuristic`. Scoring degrades,
    it never blocks an enrollment.
    """
    now = now or _now()
    # Local business mode: a business's imported customers are consumers and
    # are never scored (no model call about them, whoever calls this).
    from app.services.local_business import is_customer  # noqa: PLC0415

    leads = [lead for lead in leads if not is_customer(lead)]
    if not leads:
        return 0
    icp = stored_icp(strategy)
    product = session.get(Product, strategy.product_id)
    brief = f"{product.name} — {product.description}" if product else "(unknown)"

    scored = 0
    for start in range(0, len(leads), BATCH_SIZE):
        batch = [(lead, baselines(lead, icp, now)) for lead in leads[start:start + BATCH_SIZE]]
        model: dict[str, dict] = {}
        if use_model:
            try:
                model = _model_pass(batch, brief, icp)
            except Exception as exc:  # noqa: BLE001 -- heuristic fallback
                logger.warning("F-P-T-A model call failed for strategy %s: %s",
                               strategy.id, exc)
        for lead, base in batch:
            row = model.get(str(lead.id))
            scores: dict[str, int] = {}
            reasons: dict[str, dict] = {}
            methods: set[str] = set()
            for dimension in DIMENSIONS:
                value, reason, method = _resolve(dimension, base[dimension], row)
                scores[dimension] = value
                reasons[dimension] = {"score": value, "reason": reason,
                                      "signals": base[dimension]["signals"],
                                      "baseline": base[dimension]["baseline"]}
                methods.add(method)
            lead.fpta_fit = scores[FIT]
            lead.fpta_problem = scores[PROBLEM]
            lead.fpta_timing = scores[TIMING]
            lead.fpta_access = scores[ACCESS]
            lead.fpta_overall = overall(scores)
            lead.fpta_reasons_json = reasons
            lead.fpta_method = "model" if methods == {"model"} else (
                "heuristic" if methods == {"heuristic"} else "mixed")
            lead.fpta_scored_at = now
            scored += 1
        session.commit()
    return scored


def score_lead(session: Session, lead: Lead, now: datetime | None = None,
               use_model: bool = True) -> Lead:
    """Score one lead. The API's rescore path."""
    strategy = session.get(Strategy, lead.strategy_id)
    if strategy is not None:
        score_leads(session, strategy, [lead], now=now, use_model=use_model)
    return lead


SCORABLE = (LeadStatus.VERIFIED, LeadStatus.FLAGGED, LeadStatus.CONTACTED,
            LeadStatus.REPLIED)


def score_for_enrollment(session: Session, lead_ids: list, now: datetime | None = None) -> int:
    """The enrollment hook (app/services/sequence_engine.enroll_leads).

    Scores only leads that have never been scored -- re-enrolling a prospect
    must not silently overwrite a score a person has already read and acted
    on. Never raises: the enrollment has already been decided by the time
    this runs, and a scoring failure must not undo it.
    """
    try:
        ids = [i if isinstance(i, uuid_module.UUID) else uuid_module.UUID(str(i))
               for i in lead_ids]
        if not ids:
            return 0
        leads = session.execute(
            select(Lead).where(Lead.id.in_(ids), Lead.fpta_scored_at.is_(None))
        ).scalars().all()
        if not leads:
            return 0
        strategy = session.get(Strategy, leads[0].strategy_id)
        if strategy is None:
            return 0
        return score_leads(session, strategy, list(leads), now=now)
    except Exception as exc:  # noqa: BLE001 -- see the docstring
        logger.warning("F-P-T-A enrollment scoring failed: %s: %s", type(exc).__name__, exc)
        try:
            session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return 0


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------


def band(score: int | None) -> str:
    """strong | workable | weak | unscored -- the words the UI uses."""
    if score is None:
        return "unscored"
    if score >= 70:
        return "strong"
    if score >= 45:
        return "workable"
    return "weak"


def summary(lead: Lead) -> dict:
    """The compact block the lead LIST carries."""
    return {
        "overall": lead.fpta_overall,
        "band": band(lead.fpta_overall),
        "fit": lead.fpta_fit,
        "problem": lead.fpta_problem,
        "timing": lead.fpta_timing,
        "access": lead.fpta_access,
        "scored_at": lead.fpta_scored_at.isoformat() if lead.fpta_scored_at else None,
    }


def detail(lead: Lead) -> dict:
    """The full block the lead DETAIL and the explainability panel read."""
    reasons = lead.fpta_reasons_json if isinstance(lead.fpta_reasons_json, dict) else {}
    return {
        **summary(lead),
        "method": lead.fpta_method,
        "weights": dict(WEIGHTS),
        "dimensions": [
            {
                "key": name,
                "score": reasons.get(name, {}).get("score"),
                "reason": reasons.get(name, {}).get("reason"),
                "signals": reasons.get(name, {}).get("signals") or [],
                "baseline": reasons.get(name, {}).get("baseline"),
                "weight": WEIGHTS[name],
            }
            for name in DIMENSIONS
        ],
    }


def engagement_note(session: Session, lead: Lead) -> str | None:
    """One line of context the panel shows beside the score: what has actually
    happened with this prospect, which no sub-score covers."""
    sent = session.execute(
        select(Message).where(Message.lead_id == lead.id,
                              Message.status == MessageStatus.SENT)
    ).scalars().all()
    if not sent:
        return None
    opened = sum(1 for m in sent if m.opened_at)
    last = max((_aware(m.sent_at) for m in sent if m.sent_at), default=None)
    parts = [f"{len(sent)} message{'s' if len(sent) != 1 else ''} sent"]
    if opened:
        parts.append(f"{opened} opened")
    if last:
        parts.append(f"last on {last.date().isoformat()}")
    return ", ".join(parts) + "."
