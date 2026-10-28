"""Feature Group 1 — predictive lead scoring (`ai_booking_likelihood`, 0-100).

TWO LAYERS, AND WHY
1. FACTORS, computed deterministically from data we hold: role seniority,
   industry match against the ICP, email verification status, company
   signals (recent funding, size in the ICP's range), and the playbook's
   observed booking rate for this strategy's pattern. Weighted into a
   heuristic baseline.
2. CLAUDE reads the factors and the lead, in batches, and may move the
   baseline by at most +/-20 points with a one-sentence reason grounded in
   the data. The clamp is enforced HERE, not just asked for in the prompt.

The model adds judgement the weights cannot ("Head of Ops at a 30-person
contractor is the actual buyer for this offer"); the baseline keeps a score
explainable and stops a model hiccup from turning a strong lead into a 5. If
the model call fails, every lead in the batch keeps its heuristic score and is
marked `method: heuristic` -- scoring degrades, it never blocks sourcing.

WHERE IT RUNS
After a sourcing batch finalizes (app/workers/lead_tasks.py), on the leads that
survived verification, and on demand from the API (Rescore).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Lead, LeadStatus, PlaybookScore, Product, Strategy
from app.services import anthropic_client, prompt_safety
from app.services.icp_extraction import stored_icp

logger = logging.getLogger(__name__)

BATCH_SIZE = 15
MAX_ADJUSTMENT = 20

WEIGHTS = {"seniority": 0.25, "industry_match": 0.25, "verification": 0.20,
           "company_signals": 0.15, "playbook": 0.15}

_SENIORITY = [
    (re.compile(r"\b(founder|co-?founder|owner|ceo|chief|president|partner|"
                r"principal|managing director)\b", re.I), 1.0),
    (re.compile(r"\b(vp|vice president|head of|director|general manager|gm)\b", re.I), 0.8),
    (re.compile(r"\b(manager|lead|senior)\b", re.I), 0.55),
]

SYSTEM = (
    "You are Vectorae's lead scoring analyst. For each lead, estimate the "
    "probability (0-100) that they book a sales meeting from cold outreach "
    "for the product described. Each lead comes with a HEURISTIC baseline "
    "computed from factual factors. You may move a lead's score at most 20 "
    "points from its baseline, and only for a reason grounded in the data "
    "given (their title, company, industry, the ICP). Respond with ONLY a "
    'JSON object: {"scores": [{"lead_id": "...", "score": 0-100, "reason": '
    '"one sentence"}]}. Never invent facts about a lead.'
)

# Output contract (Phase 3 item 2). The +/-20 clamp is enforced in
# score_leads, never by the schema (the API rejects minimum/maximum); the
# score may come as a string, which score_leads coerces with float().
SCORES_SCHEMA = {
    "type": "object",
    "properties": {
        "scores": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"lead_id": {"type": "string"},
                               "score": {"type": ["number", "string"]},
                               "reason": {"type": "string"}},
                "required": ["lead_id", "score", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["scores"],
    "additionalProperties": False,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _org(lead: Lead) -> dict:
    enrichment = lead.enrichment_json if isinstance(lead.enrichment_json, dict) else {}
    person = enrichment.get("person") if isinstance(enrichment.get("person"), dict) else {}
    org = person.get("organization") or enrichment.get("organization") or {}
    return org if isinstance(org, dict) else {}


def seniority(title: str | None) -> float:
    for pattern, value in _SENIORITY:
        if pattern.search(title or ""):
            return value
    return 0.3 if title else 0.4   # unknown title: slightly above a known junior one


def industry_match(lead: Lead, icp: dict) -> float:
    from app.services.similarity import tokenize  # noqa: PLC0415

    targets = set(tokenize(" ".join(str(x) for x in (icp.get("industries") or []))))
    keywords = set(tokenize(" ".join(str(x) for x in (icp.get("keywords") or []))))
    if not targets and not keywords:
        return 0.5
    org = _org(lead)
    industry_tokens = set(tokenize(str(org.get("industry") or "")))
    company_tokens = set(tokenize(" ".join([
        str(lead.company or ""), " ".join(str(k) for k in (org.get("keywords") or [])),
        str(org.get("short_description") or ""),
    ])))
    if targets & industry_tokens:
        return 1.0
    if (targets | keywords) & company_tokens:
        return 0.75
    if industry_tokens:
        return 0.15          # we know their industry, and it is not the ICP's
    return 0.5               # nothing to go on


def verification(lead: Lead) -> float:
    if lead.status is LeadStatus.VERIFIED:
        return 1.0
    if lead.status is LeadStatus.FLAGGED:
        return 0.5
    return 0.2 if lead.email else 0.0


def _size_in_ranges(size, ranges) -> bool | None:
    try:
        size = int(size)
    except (TypeError, ValueError):
        return None
    for r in ranges or []:
        try:
            low, high = (int(x) for x in str(r).split(",", 1))
        except ValueError:
            continue
        if low <= size <= high:
            return True
    return False if ranges else None


def company_signals(lead: Lead, icp: dict, now: datetime) -> float:
    org = _org(lead)
    score = 0.5
    funded = org.get("latest_funding_round_date")
    if funded:
        try:
            when = datetime.fromisoformat(str(funded).replace("Z", "+00:00"))
            when = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
            if when >= now - timedelta(days=548):
                score += 0.3
        except ValueError:
            pass
    in_range = _size_in_ranges(org.get("estimated_num_employees"),
                               icp.get("company_size_ranges"))
    if in_range is True:
        score += 0.2
    elif in_range is False:
        score -= 0.3
    return max(0.0, min(1.0, score))


def playbook_rate(session: Session, strategy: Strategy) -> float | None:
    """Best reliable booking rate for this strategy's pattern, else None.

    F-DB-01: only the strategy owner's own rows count, never another
    account's results for the same pattern_key.
    """
    if not strategy.pattern_key:
        return None
    owner_id = select(Product.user_id).where(Product.id == strategy.product_id)
    rate = session.execute(
        select(func.max(PlaybookScore.booking_rate)).where(
            PlaybookScore.user_id == owner_id.scalar_subquery(),
            PlaybookScore.pattern_key == strategy.pattern_key,
            PlaybookScore.is_reliable.is_(True),
        )
    ).scalar_one_or_none()
    return float(rate) if rate is not None else None


def factors_for(lead: Lead, icp: dict, booking_rate: float | None,
                now: datetime) -> dict:
    playbook = 0.5 if booking_rate is None else min(1.0, booking_rate / 0.05)
    factors = {
        "seniority": round(seniority(lead.title), 3),
        "industry_match": round(industry_match(lead, icp), 3),
        "verification": round(verification(lead), 3),
        "company_signals": round(company_signals(lead, icp, now), 3),
        "playbook": round(playbook, 3),
        "playbook_booking_rate": booking_rate,
    }
    factors["heuristic"] = heuristic_score(factors)
    return factors


def heuristic_score(factors: dict) -> int:
    total = sum(WEIGHTS[k] * float(factors[k]) for k in WEIGHTS)
    return max(0, min(100, int(round(total * 100))))


def _lead_line(lead: Lead, factors: dict) -> dict:
    org = _org(lead)
    return {"lead_id": str(lead.id), "title": lead.title, "company": lead.company,
            "industry": org.get("industry"), "employees": org.get("estimated_num_employees"),
            "status": lead.status.value, "factors": factors,
            "baseline": factors["heuristic"]}


def _model_scores(batch: list[tuple[Lead, dict]], strategy: Strategy,
                  product_brief: str, icp: dict) -> dict[str, dict]:
    # Lead title, company and enrichment fields were written outside this
    # system: the lead list goes in one untrusted block (F-AI-02).
    leads = json.dumps([_lead_line(l, f) for l, f in batch], default=str)
    prompt = (f"PRODUCT: {product_brief}\nICP: {json.dumps(icp, default=str)}\n\n"
              f"{prompt_safety.NOTICE}\n\n"
              f"LEADS:\n{prompt_safety.block(leads)}\n\n"
              "Return the JSON object now.")
    data = anthropic_client.get_client().complete_json(system=SYSTEM, prompt=prompt,
                                                       max_tokens=2048, schema=SCORES_SCHEMA)
    out = {}
    for row in (data or {}).get("scores") or []:
        if isinstance(row, dict) and row.get("lead_id"):
            out[str(row["lead_id"])] = row
    return out


def score_leads(session: Session, strategy: Strategy, leads: list[Lead],
                now: datetime | None = None, use_model: bool = True) -> int:
    """Score `leads` in place and commit. Returns how many were scored."""
    from app.db.models import Product  # noqa: PLC0415

    now = now or _now()
    # Local business mode: imported customers (consumers) are never scored.
    from app.services.local_business import is_customer  # noqa: PLC0415

    leads = [lead for lead in leads if not is_customer(lead)]
    if not leads:
        return 0
    icp = stored_icp(strategy)
    rate = playbook_rate(session, strategy)
    product = session.get(Product, strategy.product_id)
    brief = f"{product.name} — {product.description}" if product else "(unknown)"

    scored = 0
    for start in range(0, len(leads), BATCH_SIZE):
        batch = [(lead, factors_for(lead, icp, rate, now))
                 for lead in leads[start:start + BATCH_SIZE]]
        model: dict[str, dict] = {}
        if use_model:
            try:
                model = _model_scores(batch, strategy, brief, icp)
            except Exception as exc:  # noqa: BLE001 -- heuristic fallback
                logger.warning("lead scoring model call failed for strategy %s: %s",
                               strategy.id, exc)
        for lead, factors in batch:
            baseline = factors["heuristic"]
            row = model.get(str(lead.id))
            score, reason, method = baseline, None, "heuristic"
            if row is not None:
                try:
                    proposed = int(round(float(row.get("score"))))
                    score = max(baseline - MAX_ADJUSTMENT,
                                min(baseline + MAX_ADJUSTMENT, proposed))
                    score = max(0, min(100, score))
                    reason = str(row.get("reason") or "").strip()[:500] or None
                    method = "model"
                except (TypeError, ValueError):
                    pass
            lead.ai_booking_likelihood = score
            lead.ai_score_reason = reason or _heuristic_reason(factors)
            lead.ai_score_factors = {**factors, "method": method}
            lead.ai_scored_at = now
            scored += 1
        session.commit()
    return scored


def _heuristic_reason(f: dict) -> str:
    strongest = max(WEIGHTS, key=lambda k: WEIGHTS[k] * float(f[k]))
    weakest = min(WEIGHTS, key=lambda k: float(f[k]))
    label = {"seniority": "role seniority", "industry_match": "industry fit",
             "verification": "email verification", "company_signals": "company signals",
             "playbook": "past campaign results"}
    return (f"Baseline from data: strongest factor is {label[strongest]}, "
            f"weakest is {label[weakest]}.")


SCORABLE = (LeadStatus.VERIFIED, LeadStatus.FLAGGED, LeadStatus.CONTACTED,
            LeadStatus.REPLIED)


def score_batch(session: Session, batch_id, now: datetime | None = None) -> int:
    """Hook for the sourcing chain: score the batch's surviving leads.
    Never raises -- sourcing has already succeeded by the time this runs."""
    from app.db.models import LeadBatch  # noqa: PLC0415
    from app.services import system_settings  # noqa: PLC0415

    try:
        if not system_settings.get(session, "lead_scoring_enabled"):
            return 0
        batch = session.get(LeadBatch, batch_id)
        strategy = session.get(Strategy, batch.strategy_id) if batch else None
        if strategy is None:
            return 0
        leads = session.execute(
            select(Lead).where(Lead.batch_id == batch_id, Lead.status.in_(SCORABLE))
        ).scalars().all()
        scored = score_leads(session, strategy, list(leads), now=now)
        # Feature Group 2: leads above loom_score_threshold get a personal
        # video script and the owner ONE notification for the batch.
        from app.services import loom_video  # noqa: PLC0415

        loom_video.suggest_for_leads(session, list(leads))
        return scored
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        logger.warning("lead scoring for batch %s failed: %s", batch_id, exc)
        return 0
