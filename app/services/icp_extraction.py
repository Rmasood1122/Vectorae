"""ICP extraction — turns the M1 pipeline's ICP research (phase 2) and
market prioritization (phase 3) into the structured criteria dict that
lead-source adapters translate into provider queries.

Output schema (consumed by app/integrations/apollo.py and stored on
lead_batches.icp_criteria_json):
    titles: list[str]
    industries: list[str]
    locations: list[str]
    company_size_ranges: list[str]   # "min,max" strings
    keywords: list[str]
"""

import hashlib
import json
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import PipelineKind, ResearchStep, Strategy
from app.services.anthropic_client import get_client

logger = logging.getLogger(__name__)

CRITERIA_KEYS = ["titles", "industries", "locations", "company_size_ranges", "keywords"]

# --- Tactic profile (the "/tactic" half of Strategy.pattern_key) -----------
#
# Strategy.pattern_key is documented as "SHA-256 of sorted canonical ICP/tactic
# JSON", and pattern_key is what links a strategy to its playbook_scores rows.
# Both halves therefore have to be LOW CARDINALITY: the whole point of the
# playbook is aggregating outcomes ACROSS strategies that share a pattern, and
# a key that is unique per strategy buckets nothing and learns nothing.
#
# That rules out hashing the Phase 5/8 research directly - those steps are
# prompted for "clear markdown, 300-600 words", so every strategy's tactic
# prose is unique. The tactic half is instead a small CLOSED vocabulary,
# extracted once and filtered to the allowed values, so an unexpected answer
# from the model degrades to "unknown" (dropped) rather than minting a new
# bucket.
TACTIC_CHANNELS = ["email", "whatsapp", "linkedin", "phone", "referral", "inbound"]
TACTIC_MOTIONS = ["cold_outbound", "warm_intro", "inbound_led", "product_led", "partner_led"]
TACTIC_CADENCES = ["light", "standard", "aggressive"]

_TACTIC_SYSTEM = (
    "You are Vectorae's go-to-market classifier. You read an execution plan "
    "and label it with a small fixed vocabulary. Respond with ONLY a single "
    "JSON object — no prose, no markdown fences."
)

_TACTIC_PROMPT = """Classify the outreach TACTICS described below. Use ONLY
values from the given vocabularies; omit anything the plan does not state.

Required JSON keys:
- channels: list of the outreach channels the plan actually uses.
  Allowed: {channels}
- sales_motion: single value describing how deals are initiated.
  Allowed: {motions}
- cadence: single value for follow-up intensity — "light" (<= 3 touches),
  "standard" (4-6), "aggressive" (7+). Allowed: {cadences}

EXECUTION PLAN AND CHANNEL RESEARCH:
{research}
"""

# Output contract (Phase 3 item 2). The labels are plain strings, not enums:
# _one() below normalises case, spaces and hyphens ("Cold Outbound" counts),
# so the parsing and its filter to the allowed vocabulary stay the contract.
TACTIC_SCHEMA = {
    "type": "object",
    "properties": {
        "channels": {"type": "array", "items": {"type": "string"}},
        "sales_motion": {"type": ["string", "null"]},
        "cadence": {"type": ["string", "null"]},
    },
    "required": ["channels"],
    "additionalProperties": False,
}

_SYSTEM = (
    "You are Vectorae's ICP extraction engine. From strategy research you "
    "produce structured lead-search criteria. Respond with ONLY a single "
    "JSON object — no prose, no markdown fences."
)

_PROMPT = """From the ICP and market research below, produce search criteria
for a B2B people-search tool. Required JSON keys (each a list of strings,
empty list when unknown):

- titles: decision-maker job titles to target
- industries: industry/vertical names
- locations: geographies as "Region, Country" or "Country"
- company_size_ranges: employee ranges as "min,max" strings (e.g. "11,50")
- keywords: 3-8 short qualifier keywords

RESEARCH:
{research}
"""

# Output contract (Phase 3 item 2): every criteria key, each a list of strings.
CRITERIA_SCHEMA = {
    "type": "object",
    "properties": {key: {"type": "array", "items": {"type": "string"}}
                   for key in CRITERIA_KEYS},
    "required": list(CRITERIA_KEYS),
    "additionalProperties": False,
}


def extract_icp_criteria(session: Session, strategy: Strategy) -> dict:
    """Extract structured criteria from the strategy's phase 2 + 3 research."""
    rows = session.execute(
        select(ResearchStep)
        .where(
            ResearchStep.strategy_id == strategy.id,
            ResearchStep.pipeline == PipelineKind.STRATEGY,
            ResearchStep.phase.in_([2, 3]),
        )
        .order_by(ResearchStep.step_no)
    ).scalars().all()

    research = "\n\n".join(
        f"[Phase {r.phase} / Step {r.step_no}: {r.name}]\n{r.output}" for r in rows
    ) or "(no ICP research found — derive nothing, return empty lists)"
    # Local business mode: the buyers are LOCAL businesses -- the extraction
    # sees the same context block the research did, and the result is then
    # narrowed to the service area (apply_to_criteria). No extra model call.
    from app.services import local_business  # noqa: PLC0415

    local = local_business.block_for_strategy(session, strategy)
    if local:
        research = f"{local}\n\n{research}"

    criteria = local_business.apply_to_criteria(
        session, strategy, criteria_from_research(research))
    logger.info("ICP criteria extracted for strategy %s: %s", strategy.id, criteria)
    return criteria


def criteria_from_research(research: str) -> dict:
    """The model call + normalisation behind extract_icp_criteria, for any
    research text.

    F06 (Quickstart) calls this with the user's one-line offer and the text
    of their website instead of pipeline research, so both paths share one
    prompt, one output schema and one normalisation. Untrusted text inside
    `research` must already be delimited by the caller (prompt_safety)."""
    data = get_client().complete_json(system=_SYSTEM, prompt=_PROMPT.format(research=research),
                                       schema=CRITERIA_SCHEMA)
    criteria = {}
    for key in CRITERIA_KEYS:
        value = data.get(key)
        criteria[key] = ([item for item in (_criterion(v) for v in value) if item is not None]
                         if isinstance(value, list) else [])
    return criteria


def _criterion(value) -> str | None:
    """One list item of the model's criteria, as the string the lead search
    gets -- or None to drop it. Fail closed: a nested object used to be
    str()-ed into a Python repr ("{'name': ...}") and sent to the paid lead
    search as a title or industry. Strings pass; numbers become strings (a
    size range of [11, 50]); an object passes only its own name or label
    string; anything else (booleans, lists, other objects, null) is dropped."""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, dict):
        for field in ("name", "label"):
            if isinstance(value.get(field), str) and value[field].strip():
                return value[field]
    return None


def extract_tactic_profile(session: Session, strategy: Strategy) -> dict:
    """Classify the strategy's Phase 5 (channels) and Phase 8 (execution plan)
    research into the closed tactic vocabulary above.

    Never raises: a strategy with no usable tactic signal simply hashes with an
    empty tactic profile, which is a valid bucket of its own.
    """
    rows = session.execute(
        select(ResearchStep)
        .where(
            ResearchStep.strategy_id == strategy.id,
            ResearchStep.pipeline == PipelineKind.STRATEGY,
            ResearchStep.phase.in_([5, 8]),
        )
        .order_by(ResearchStep.step_no)
    ).scalars().all()

    research = "\n\n".join(
        f"[Phase {r.phase} / Step {r.step_no}: {r.name}]\n{r.output}" for r in rows
    )
    if not research:
        return {"channels": [], "sales_motion": None, "cadence": None}

    try:
        data = get_client().complete_json(
            system=_TACTIC_SYSTEM,
            prompt=_TACTIC_PROMPT.format(
                channels=", ".join(TACTIC_CHANNELS),
                motions=", ".join(TACTIC_MOTIONS),
                cadences=", ".join(TACTIC_CADENCES),
                research=research,
            ),
            schema=TACTIC_SCHEMA,
        )
    except Exception as exc:  # noqa: BLE001 - classification must not fail a pipeline
        logger.warning("tactic classification failed for strategy %s: %s",
                       strategy.id, exc)
        return {"channels": [], "sales_motion": None, "cadence": None}

    def _one(value, allowed):
        v = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
        return v if v in allowed else None

    raw_channels = data.get("channels")
    channels = [
        c for c in (
            _one(v, TACTIC_CHANNELS) for v in (raw_channels or [])
        ) if c
    ] if isinstance(raw_channels, list) else []

    profile = {
        "channels": channels,
        "sales_motion": _one(data.get("sales_motion"), TACTIC_MOTIONS),
        "cadence": _one(data.get("cadence"), TACTIC_CADENCES),
    }
    logger.info("tactic profile for strategy %s: %s", strategy.id, profile)
    return profile


def canonical_pattern_payload(criteria: dict, tactics: dict | None = None) -> dict:
    """The exact structure that gets hashed into Strategy.pattern_key.

    Kept as its own function so a test can pin the shape: adding, removing or
    reordering a field here silently re-buckets every playbook score, so it is
    an architecture tripwire, not an implementation detail.

    Canonicalization: every value lower-cased, stripped, de-duplicated and
    sorted, so key equality never depends on the order the model happened to
    emit its lists in. `flow_type` participates because a Flow 1 strategy
    (anchored on proven past clients) and a Flow 2 strategy are not the same
    play even against an identical ICP.
    """
    tactics = tactics or {}

    def _norm_list(values):
        return sorted({str(v).strip().lower() for v in (values or []) if str(v).strip()})

    def _norm_one(value):
        v = str(value or "").strip().lower()
        return v or None

    return {
        "icp": {key: _norm_list(criteria.get(key)) for key in CRITERIA_KEYS},
        "tactics": {
            "channels": _norm_list(tactics.get("channels")),
            "sales_motion": _norm_one(tactics.get("sales_motion")),
            "cadence": _norm_one(tactics.get("cadence")),
            "flow_type": _norm_one(tactics.get("flow_type")),
        },
    }


def stored_icp(strategy) -> dict:
    """The ICP criteria in strategy.pattern_inputs_json.

    ensure_pattern_key stores the canonical payload, which nests them under
    "icp" next to "tactics". Readers that took the column as a flat ICP saw
    no industries, sizes or keywords at all (I3/#30). A flat dict, as older
    rows or hand-set data may hold, is read as it is.
    """
    inputs = getattr(strategy, "pattern_inputs_json", None)
    if not isinstance(inputs, dict):
        return {}
    icp = inputs.get("icp")
    if isinstance(icp, dict):
        return icp
    return {} if "tactics" in inputs else inputs


def pattern_key_of(payload: dict) -> str:
    """SHA-256 of an already-canonical payload.

    The single place the hash is actually taken, so a stored
    pattern_inputs_json can be rehashed without going back through
    canonicalization and risking a different answer.
    """
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def compute_pattern_key(criteria: dict, tactics: dict | None = None) -> str:
    """SHA-256 of the sorted canonical ICP/tactic JSON.

    This is the key that links a strategy to its playbook_scores rows -- see
    Strategy.pattern_key, which documents exactly this. Two strategies aiming
    at the same buyer WITH THE SAME PLAY share a key, which is the point: the
    learning loop aggregates outcomes across them.

    The tactic half was missing until now: the key was ICP-only, so two
    strategies targeting the same buyer through completely different channels
    and cadences shared one playbook bucket and their outcomes were averaged
    together.
    """
    return pattern_key_of(canonical_pattern_payload(criteria, tactics))


def ensure_pattern_key(session: Session, strategy: Strategy, criteria: dict,
                       tactics: dict | None = None) -> str:
    """Set strategy.pattern_key from `criteria` if it is not already set.

    NOTHING in the app used to write this column, so it was NULL on every
    strategy -- and every consumer filters on it:
      * score_decay.compute_scores_with_decay() joins outcomes to strategies
        `WHERE s.pattern_key IS NOT NULL`, so it always returned {} and
        playbook_scores was never written;
      * learning_tasks.auto_promote_winners() had no scores to read, so
        default_variant was never set and A/B tests never concluded;
      * PlaybookService.get_insights() joins on it, so the Phase 6/8 playbook
        injection was permanently the "no data yet" block.
    In other words the entire self-learning loop was inert. This is the one
    place the key is derived; if the intended canonical form differs, change
    compute_pattern_key and backfill.
    """
    if strategy.pattern_key:
        return strategy.pattern_key
    if tactics is None:
        tactics = extract_tactic_profile(session, strategy)
    tactics = {**tactics,
               "flow_type": getattr(strategy.flow_type, "value", strategy.flow_type)}

    # Persist the payload as well as the hash (migration 0014). Without it the
    # inputs were discarded, so the bucket could not be audited and a backfill
    # had to re-ask the model - two calls per strategy, and non-deterministic,
    # so an unchanged strategy could legitimately land in a different bucket on
    # recompute.
    payload = canonical_pattern_payload(criteria, tactics)
    strategy.pattern_inputs_json = payload
    strategy.pattern_key = pattern_key_of(payload)
    session.commit()
    logger.info("pattern_key set for strategy %s: %s", strategy.id, strategy.pattern_key)
    return strategy.pattern_key
