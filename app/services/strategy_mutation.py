"""Feature Group 1 — dynamic strategy mutation.

THE TRIGGER
A campaign has been sending for `strategy_mutation_idle_days` (default 7,
admin-controlled) with ZERO replies. Measured from its FIRST sent message, not
from when the strategy was created: a strategy that sat verified for a month
before the user connected Gmail has not been failing for a month. At most one
automatic mutation per idle window (strategies.last_mutation_at).

WHAT THE MODEL GETS
The strategy document, the ICP, the sequence briefs actually used, and the
numbers: sends per channel and per step, opens, bounces, replies (zero), days
active. It is asked for a diagnosis and three concrete changes -- a new
messaging angle, a channel recommendation, and ICP refinements -- plus a
rewritten messaging section.

WHY IT PROPOSES RATHER THAN APPLIES
The result is stored as a new StrategyVersion with status `proposed`, and the
user is notified. It is never applied on its own: the strategy document is what
every future email is personalised from (message_personalization), and
silently rewriting the pitch of a live campaign is not a decision software
should make for someone. Applying is one click (POST .../versions/{id}/apply).

The version document is the current document with the mutation prepended as a
clearly marked section, not a full model rewrite. A full rewrite of an
~8,000-token document is the exact job that truncated in the verification loop
(see config.verification_fixer_max_tokens); a prepended section cannot be
truncated into a corrupt document, and the reader sees exactly what changed.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    Lead,
    Message,
    MessageStatus,
    Outcome,
    OutcomeEvent,
    Sequence,
    SequenceStep,
    Strategy,
    StrategyStatus,
    StrategyVersion,
)
from app.services import anthropic_client

logger = logging.getLogger(__name__)

SYSTEM = (
    "You are Vectorae's strategy mutation strategist. A cold outreach "
    "campaign has run for days and received ZERO replies. Diagnose why, from "
    "the evidence given, and propose concrete changes. Respond with ONLY a "
    'JSON object: {"diagnosis": "...", "messaging_angle": {"current": "...", '
    '"proposed": "...", "rationale": "..."}, "channel": {"recommended": '
    '"email"|"whatsapp"|"linkedin"|"phone", "rationale": "..."}, '
    '"icp_refinement": {"changes": ["..."], "rationale": "..."}, '
    '"revised_messaging": "markdown, at most 400 words: the new hooks, '
    'subject-line directions and first-line openers"}. RULES. Base the '
    "diagnosis on the numbers given (e.g. low opens point at subject lines "
    "or deliverability, opens without replies point at the offer or the "
    "audience). Never invent results or statistics. Keep every field specific "
    "to this product and ICP."
)


def _strings(*keys: str) -> dict:
    return {"type": "object", "properties": {k: {"type": "string"} for k in keys},
            "required": list(keys), "additionalProperties": False}


# Output contract (Phase 3 item 2). channel.recommended is a string, not an
# enum: _clean_changes lower-cases it and blanks anything outside the four
# channels, so that filter stays the contract.
MUTATION_SCHEMA = {
    "type": "object",
    "properties": {
        "diagnosis": {"type": "string"},
        "messaging_angle": _strings("current", "proposed", "rationale"),
        "channel": _strings("recommended", "rationale"),
        "icp_refinement": {
            "type": "object",
            "properties": {"changes": {"type": "array", "items": {"type": "string"}},
                           "rationale": {"type": "string"}},
            "required": ["changes", "rationale"],
            "additionalProperties": False,
        },
        "revised_messaging": {"type": "string"},
    },
    "required": ["diagnosis", "messaging_angle", "channel", "icp_refinement",
                 "revised_messaging"],
    "additionalProperties": False,
}

ACTIVE_CAMPAIGN = "active"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


def outcome_snapshot(session: Session, strategy: Strategy,
                     now: datetime | None = None) -> dict:
    now = now or _now()
    lead_ids = select(Lead.id).where(Lead.strategy_id == strategy.id)
    sent_rows = session.execute(
        select(Message.channel, Message.step_no, func.count(Message.id),
               func.min(Message.sent_at))
        .where(Message.lead_id.in_(lead_ids), Message.status == MessageStatus.SENT)
        .group_by(Message.channel, Message.step_no)
    ).all()
    by_channel: dict[str, int] = {}
    by_step: dict[str, int] = {}
    first_sent = None
    for channel, step_no, count, first in sent_rows:
        by_channel[channel.value] = by_channel.get(channel.value, 0) + count
        by_step[str(step_no)] = by_step.get(str(step_no), 0) + count
        first = _aware(first)
        if first and (first_sent is None or first < first_sent):
            first_sent = first

    def _count(event: OutcomeEvent) -> int:
        return session.execute(
            select(func.count(Outcome.id)).where(Outcome.lead_id.in_(lead_ids),
                                                 Outcome.event == event)
        ).scalar_one()

    return {
        "sent_total": sum(by_channel.values()),
        "sent_by_channel": by_channel,
        "sent_by_step": by_step,
        "opened": _count(OutcomeEvent.OPENED),
        "replied": _count(OutcomeEvent.REPLIED),
        "bounced": _count(OutcomeEvent.BOUNCED),
        "unsubscribed": _count(OutcomeEvent.UNSUBSCRIBED),
        "booked": _count(OutcomeEvent.BOOKED),
        "first_sent_at": first_sent.isoformat() if first_sent else None,
        "days_active": (now - first_sent).days if first_sent else 0,
        "leads": session.execute(
            select(func.count(Lead.id)).where(Lead.strategy_id == strategy.id)
        ).scalar_one(),
    }


def is_idle(snapshot: dict, strategy: Strategy, idle_days: int,
            now: datetime | None = None) -> bool:
    now = now or _now()
    if strategy.campaign_state != ACTIVE_CAMPAIGN:
        return False
    if not snapshot["first_sent_at"] or snapshot["replied"] > 0:
        return False
    first = datetime.fromisoformat(snapshot["first_sent_at"])
    if first > now - timedelta(days=idle_days):
        return False
    last = _aware(strategy.last_mutation_at)
    return last is None or last <= now - timedelta(days=idle_days)


def find_idle_strategies(session: Session, idle_days: int,
                         now: datetime | None = None) -> list[tuple[Strategy, dict]]:
    now = now or _now()
    candidates = session.execute(
        select(Strategy).where(
            Strategy.campaign_state == ACTIVE_CAMPAIGN,
            Strategy.status.in_([StrategyStatus.VERIFIED, StrategyStatus.EXECUTING]),
        )
    ).scalars().all()
    out = []
    for strategy in candidates:
        snap = outcome_snapshot(session, strategy, now=now)
        if is_idle(snap, strategy, idle_days, now=now):
            out.append((strategy, snap))
    return out


# ---------------------------------------------------------------------------
# Mutation
# ---------------------------------------------------------------------------


def _sequence_briefs(session: Session, strategy: Strategy) -> list[dict]:
    rows = session.execute(
        select(Sequence.name, Sequence.channel, SequenceStep.step_no,
               SequenceStep.template, SequenceStep.channel)
        .join(SequenceStep, SequenceStep.sequence_id == Sequence.id)
        .where(Sequence.strategy_id == strategy.id)
        .order_by(Sequence.name, SequenceStep.step_no)
    ).all()
    return [{"sequence": name, "step": step_no,
             "channel": (step_channel or seq_channel).value,
             "brief": (template or "")[:600]}
            for name, seq_channel, step_no, template, step_channel in rows[:12]]


def ensure_original_version(session: Session, strategy: Strategy) -> StrategyVersion:
    existing = session.execute(
        select(StrategyVersion).where(StrategyVersion.strategy_id == strategy.id,
                                      StrategyVersion.version_no == 1)
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    original = StrategyVersion(
        strategy_id=strategy.id, version_no=1,
        document=strategy.strategy_document or "",
        change_summary="The strategy as the research pipeline produced it.",
        trigger="original", status="applied", applied_at=strategy.created_at,
    )
    session.add(original)
    session.flush()
    return original


def _render_version(changes: dict, version_no: int, base_document: str,
                    snapshot: dict) -> str:
    angle = changes.get("messaging_angle") or {}
    channel = changes.get("channel") or {}
    icp = changes.get("icp_refinement") or {}
    lines = [
        f"## Strategy mutation — version {version_no}",
        "",
        f"_Proposed after {snapshot.get('days_active', 0)} days, "
        f"{snapshot.get('sent_total', 0)} messages sent, "
        f"{snapshot.get('opened', 0)} opens and 0 replies._",
        "",
        f"**Diagnosis.** {changes.get('diagnosis') or '—'}",
        "",
        "**New messaging angle.**",
        f"- Was: {angle.get('current') or '—'}",
        f"- Now: {angle.get('proposed') or '—'}",
        f"- Why: {angle.get('rationale') or '—'}",
        "",
        f"**Channel.** {channel.get('recommended') or '—'} — {channel.get('rationale') or ''}",
        "",
        "**ICP refinements.**",
        *[f"- {c}" for c in (icp.get("changes") or [])],
        f"_{icp.get('rationale') or ''}_",
        "",
        "### Revised messaging",
        "",
        changes.get("revised_messaging") or "—",
        "",
        "---",
        "",
    ]
    return "\n".join(lines) + base_document


def _clean_changes(data: dict) -> dict:
    data = data if isinstance(data, dict) else {}

    def _s(v, n=1500):
        return str(v).strip()[:n] if isinstance(v, (str, int, float)) else ""

    def _d(v, keys):
        v = v if isinstance(v, dict) else {}
        return {k: _s(v.get(k)) for k in keys}

    icp = data.get("icp_refinement") if isinstance(data.get("icp_refinement"), dict) else {}
    channel = _d(data.get("channel"), ("recommended", "rationale"))
    if channel["recommended"].lower() not in ("email", "whatsapp", "linkedin", "phone"):
        channel["recommended"] = ""
    return {
        "diagnosis": _s(data.get("diagnosis"), 2000),
        "messaging_angle": _d(data.get("messaging_angle"), ("current", "proposed", "rationale")),
        "channel": channel,
        "icp_refinement": {
            "changes": [_s(c, 300) for c in (icp.get("changes") or []) if _s(c)][:6],
            "rationale": _s(icp.get("rationale")),
        },
        "revised_messaging": _s(data.get("revised_messaging"), 6000),
    }


def mutate(session: Session, strategy: Strategy, *, trigger: str,
           snapshot: dict | None = None, actor_user_id=None,
           now: datetime | None = None) -> StrategyVersion:
    """Create a PROPOSED version. Raises on a model failure (the caller
    decides: the sweep logs and moves on, the API returns 502)."""
    now = now or _now()
    snapshot = snapshot or outcome_snapshot(session, strategy, now=now)
    base = ensure_original_version(session, strategy)

    prompt = "\n\n".join([
        f"OUTCOME DATA:\n{snapshot}",
        f"IDEAL CUSTOMER PROFILE:\n{strategy.pattern_inputs_json or '(not extracted)'}",
        f"SEQUENCE BRIEFS IN USE:\n{_sequence_briefs(session, strategy)}",
        f"STRATEGY DOCUMENT (truncated):\n{(strategy.strategy_document or '')[:14_000]}",
        "Return the JSON object now.",
    ])
    data = anthropic_client.get_client().complete_json(system=SYSTEM, prompt=prompt,
                                                       max_tokens=4096, schema=MUTATION_SCHEMA)
    changes = _clean_changes(data)
    if not changes["diagnosis"] or not changes["messaging_angle"]["proposed"]:
        raise ValueError("the model returned an empty mutation")

    latest = session.execute(
        select(func.max(StrategyVersion.version_no))
        .where(StrategyVersion.strategy_id == strategy.id)
    ).scalar_one() or 1
    current = session.execute(
        select(StrategyVersion).where(StrategyVersion.strategy_id == strategy.id,
                                      StrategyVersion.status == "applied")
        .order_by(StrategyVersion.version_no.desc())
    ).scalars().first() or base
    version = StrategyVersion(
        strategy_id=strategy.id, version_no=latest + 1, parent_version_id=current.id,
        document=_render_version(changes, latest + 1,
                                 strategy.strategy_document or "", snapshot),
        change_summary=(f"{changes['messaging_angle']['proposed']} "
                        f"(channel: {changes['channel']['recommended'] or 'unchanged'})")[:2000],
        changes_json=changes, outcome_snapshot_json=snapshot, trigger=trigger,
        status="proposed", created_by_user_id=actor_user_id,
    )
    session.add(version)
    strategy.last_mutation_at = now
    session.commit()
    return version


def apply_version(session: Session, strategy: Strategy, version: StrategyVersion,
                  now: datetime | None = None) -> StrategyVersion:
    """Make `version` the live document. The previously applied version
    becomes `superseded`; nothing is deleted."""
    now = now or _now()
    ensure_original_version(session, strategy)
    for other in session.execute(
        select(StrategyVersion).where(StrategyVersion.strategy_id == strategy.id,
                                      StrategyVersion.status == "applied",
                                      StrategyVersion.id != version.id)
    ).scalars():
        other.status = "superseded"
    version.status = "applied"
    version.applied_at = now
    strategy.strategy_document = version.document
    session.commit()
    return version


def notify(session: Session, strategy: Strategy, version: StrategyVersion) -> None:
    from app.services import event_bus, notifications  # noqa: PLC0415

    owner = notifications.owner_of_strategy(session, strategy)
    snap = version.outcome_snapshot_json or {}
    event_bus.emit(
        session, owner, "strategy_mutated",
        title="Campaign strategy mutated",
        body=(f"{snap.get('days_active', 0)} days, {snap.get('sent_total', 0)} sends, "
              f"no replies. Review the proposed changes: "
              f"{(version.change_summary or '')[:200]}"),
        deep_link=f"/strategies/document?id={strategy.id}&tab=versions",
        data={"strategyId": str(strategy.id), "versionId": str(version.id)},
        webhook_payload={"strategy_id": str(strategy.id), "version_id": str(version.id),
                         "version_no": version.version_no, "trigger": version.trigger,
                         "changes": version.changes_json},
    )


def run_idle_sweep(session: Session, now: datetime | None = None) -> dict:
    from app.services import system_settings  # noqa: PLC0415

    now = now or _now()
    if not system_settings.get(session, "strategy_mutation_enabled"):
        return {"checked": 0, "mutated": 0, "failed": 0, "disabled": True}
    idle_days = system_settings.get(session, "strategy_mutation_idle_days")
    idle = find_idle_strategies(session, idle_days, now=now)
    mutated = failed = 0
    for strategy, snap in idle:
        try:
            from app.services import usage_meter  # noqa: PLC0415

            # P0-5b: metered to the owner, on its daily AI limit (an account
            # past its limit is simply retried by tomorrow's sweep).
            with usage_meter.owner_scope(session, strategy, "mutation"):
                version = mutate(session, strategy, trigger="idle_no_replies",
                                 snapshot=snap, now=now)
            notify(session, strategy, version)
            mutated += 1
        except Exception as exc:  # noqa: BLE001 -- one strategy never stops the sweep
            session.rollback()
            failed += 1
            logger.warning("mutation of strategy %s failed: %s", strategy.id, exc)
    return {"checked": len(idle), "mutated": mutated, "failed": failed}
