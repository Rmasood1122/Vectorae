"""Feature Group 1 — the multi-model consensus engine.

WHAT RUNS TWICE, AND WHY ONLY THAT
The strategy document is assembled from each phase's SYNTHESIS step (step 9 of
every phase -- see pipeline/engine.py::_assemble). Those eight outputs per
pipeline are the document; the other 64 are research that feeds them. So
consensus runs on the synthesis steps only: GPT-4o answers the identical system
prompt and research brief in parallel with Claude, and every uncertain zone
maps onto a section a reader actually sees. Running both models on all 144
steps would triple the cost of a strategy to flag disagreements in text nobody
reads.

CLAUDE STAYS CANONICAL
Claude's answer is written to research_steps and assembled into the document
exactly as before; GPT-4o's is stored beside it (strategy_model_outputs). The
consensus layer adds information -- it never replaces a section with the other
model's text, because "the second model said something different" is a reason
for a human to look, not evidence the first one was wrong.

HOW A DISAGREEMENT IS DECIDED
A Claude "consensus judge" compares the two answers with the models ANONYMISED
as Strategist A and Strategist B (the judge is Claude; telling it which answer
was its own invites it to side with itself). It is told to ignore wording,
ordering, emphasis and depth, and to report only different recommendations,
contradictory facts or figures, or a different target segment, channel or
price. Only medium/high severity becomes an uncertain zone. The TF-IDF cosine
of the two outputs is stored alongside as a second, model-free signal.

FAILURE NEVER BLOCKS A STRATEGY
If OpenAI errors, times out or is not configured, the step completes on
Claude's answer alone and the failure is recorded; the strategy's
consensus_status becomes "partial". If the judge fails, no zones are written
for that step. Claude failing still raises, exactly as it did without
consensus, so the pipeline's retry/resume semantics are unchanged.
"""

from __future__ import annotations

import contextvars
import logging
import time
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import (
    Strategy,
    StrategyModelOutput,
    StrategyUncertainZone,
)
from app.services import similarity

logger = logging.getLogger(__name__)

JUDGE_SYSTEM = (
    "You are Vectorae's consensus judge. Two independent go-to-market "
    "strategists, A and B, answered the SAME research brief. Compare them and "
    "report only MEANINGFUL disagreements: a different recommendation, a "
    "contradictory fact or figure, or a different target segment, channel, "
    "price or sequence. Ignore wording, ordering, emphasis, formatting and "
    "level of detail -- one strategist covering a point the other omits is "
    "NOT a disagreement unless they conflict. Respond with ONLY a JSON "
    'object: {"disagreements": [{"topic": "...", "a_position": "...", '
    '"b_position": "...", "severity": "low"|"medium"|"high"}], '
    '"agreement_summary": "..."}. At most 6 disagreements; positions are one '
    "or two sentences each, quoting the strategist where you can. severity "
    "high = acting on the wrong one would change what the campaign does."
)

_KEEP = {"medium", "high"}

# Output contract (Phase 3 item 2). severity is a string, not an enum: judge()
# lower-cases it and keeps only medium/high. The 6-item cap is applied in
# judge(), never by the schema (the API rejects maxItems).
JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "disagreements": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"topic": {"type": "string"},
                               "a_position": {"type": "string"},
                               "b_position": {"type": "string"},
                               "severity": {"type": "string"}},
                "required": ["topic", "a_position", "b_position", "severity"],
                "additionalProperties": False,
            },
        },
        "agreement_summary": {"type": "string"},
    },
    "required": ["disagreements"],
    "additionalProperties": False,
}


def is_synthesis_step(spec) -> bool:
    return spec.step_no % 9 == 0


def applies(session: Session, strategy: Strategy, spec) -> bool:
    """Consensus runs on a synthesis step when the admin enabled it AND an
    OpenAI key is configured."""
    from app.services import credentials, system_settings  # noqa: PLC0415

    if not is_synthesis_step(spec):
        return False
    try:
        return bool(system_settings.get(session, "consensus_enabled")
                    and credentials.get_secret(session, "openai", "api_key"))
    except Exception:  # noqa: BLE001 -- a settings read must not fail a step
        return False


def text_similarity(a: str, b: str) -> float:
    """TF-IDF cosine of two texts, 0..1, using the M8 similarity helpers."""
    ta, tb = similarity.tokenize(a or ""), similarity.tokenize(b or "")
    if not ta or not tb:
        return 0.0
    idf = similarity._idf([ta, tb])
    return round(similarity.cosine_similarity(similarity._tfidf_vector(ta, idf),
                                              similarity._tfidf_vector(tb, idf)), 4)


def _timed(fn, *args, **kwargs):
    start = time.monotonic()
    try:
        return fn(*args, **kwargs), None, int((time.monotonic() - start) * 1000)
    except Exception as exc:  # noqa: BLE001 -- recorded, never raised
        return None, f"{type(exc).__name__}: {exc}", int((time.monotonic() - start) * 1000)


def _store_output(session: Session, strategy: Strategy, spec, *, provider: str,
                  model: str, output: str | None, error: str | None,
                  latency_ms: int | None) -> None:
    session.execute(delete(StrategyModelOutput).where(
        StrategyModelOutput.strategy_id == strategy.id,
        StrategyModelOutput.pipeline == spec.pipeline,
        StrategyModelOutput.step_no == spec.step_no,
        StrategyModelOutput.provider == provider,
    ))
    session.add(StrategyModelOutput(
        strategy_id=strategy.id, pipeline=spec.pipeline, phase=spec.phase,
        step_no=spec.step_no, provider=provider, model=model[:64],
        output=output, error=(error or None) and error[:2000],
        latency_ms=latency_ms,
    ))


def judge(claude, section_title: str, claude_text: str, gpt_text: str) -> list[dict]:
    """The judge's medium/high disagreements, mapped back to the models."""
    prompt = (f"SECTION: {section_title}\n\n"
              f"STRATEGIST A:\n{claude_text[:14_000]}\n\n"
              f"STRATEGIST B:\n{gpt_text[:14_000]}\n\n"
              "Return the JSON object now.")
    from app.services.anthropic_client import JUDGE_TEMPERATURE  # noqa: PLC0415

    data = claude.complete_json(system=JUDGE_SYSTEM, prompt=prompt, max_tokens=2048,
                                temperature=JUDGE_TEMPERATURE, schema=JUDGE_SCHEMA)
    zones = []
    for item in (data or {}).get("disagreements") or []:
        if not isinstance(item, dict):
            continue
        severity = str(item.get("severity") or "").strip().lower()
        topic = str(item.get("topic") or "").strip()
        if severity not in _KEEP or not topic:
            continue
        zones.append({
            "topic": topic[:300],
            "claude_position": str(item.get("a_position") or "").strip()[:2000],
            "gpt_position": str(item.get("b_position") or "").strip()[:2000],
            "severity": severity,
        })
    return zones[:6]


def run_step(session: Session, strategy: Strategy, spec, *, system: str,
             prompt: str, claude, cache_prefix: str | None = None) -> str:
    """Run one synthesis step on both models. Returns Claude's answer.

    Writes model outputs and zones to the session WITHOUT committing -- the
    engine commits them in the same transaction as the step's research row,
    so a step is either fully recorded or not recorded at all.
    """
    from app.integrations.openai_client import get_openai_client  # noqa: PLC0415

    openai = get_openai_client(session)
    if openai is None:
        return claude.complete(system=system, prompt=prompt, cache_prefix=cache_prefix)

    with ThreadPoolExecutor(max_workers=1) as pool:
        # A worker thread does not inherit context variables, so without
        # copy_context() the usage scope was None there and GPT-4o spend was
        # never recorded, even inside the pipeline scope (P0-5b). The copy
        # holds the SAME scope object, so the thread's notes land in it.
        future = pool.submit(contextvars.copy_context().run,
                             _timed, openai.complete, system,
                             # GPT-4o reads the same brief, in the same order.
                             (cache_prefix or "") + prompt)
        started = time.monotonic()
        claude_text = claude.complete(system=system, prompt=prompt,  # may raise: pipeline retries
                                      cache_prefix=cache_prefix)
        claude_ms = int((time.monotonic() - started) * 1000)
        gpt_text, gpt_error, gpt_ms = future.result()

    _store_output(session, strategy, spec, provider="anthropic",
                  model=settings.anthropic_model, output=claude_text, error=None,
                  latency_ms=claude_ms)
    _store_output(session, strategy, spec, provider="openai", model=openai.model,
                  output=gpt_text, error=gpt_error, latency_ms=gpt_ms)

    session.execute(delete(StrategyUncertainZone).where(
        StrategyUncertainZone.strategy_id == strategy.id,
        StrategyUncertainZone.pipeline == spec.pipeline,
        StrategyUncertainZone.step_no == spec.step_no,
    ))
    if gpt_text is None:
        logger.warning("consensus: OpenAI failed on %s step %s: %s",
                       strategy.id, spec.step_no, gpt_error)
        strategy.consensus_status = "partial"
        return claude_text

    sim = text_similarity(claude_text, gpt_text)
    try:
        zones = judge(claude, spec.name, claude_text, gpt_text)
    except Exception as exc:  # noqa: BLE001 -- no zones, never a failed step
        logger.warning("consensus judge failed on %s step %s: %s",
                       strategy.id, spec.step_no, exc)
        strategy.consensus_status = "partial"
        return claude_text

    for zone in zones:
        session.add(StrategyUncertainZone(
            strategy_id=strategy.id, pipeline=spec.pipeline, phase=spec.phase,
            step_no=spec.step_no, section_title=spec.name[:200],
            similarity=sim, **zone,
        ))
    if strategy.consensus_status != "partial":
        strategy.consensus_status = "complete"
    return claude_text


def zones_for(session: Session, strategy_id) -> list[StrategyUncertainZone]:
    return list(session.execute(
        select(StrategyUncertainZone)
        .where(StrategyUncertainZone.strategy_id == strategy_id)
        .order_by(StrategyUncertainZone.pipeline, StrategyUncertainZone.step_no,
                  StrategyUncertainZone.severity)
    ).scalars().all())
