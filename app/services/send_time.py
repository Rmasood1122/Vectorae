"""Best time to send: place each message at a good slot in the PROSPECT's
local time, inside the permitted sending hours.

Spec: docs/features/autopilot-launch.md section 3.2. Flag
`best_time_scheduling_enabled` (default on); off = the previous scheduling,
exactly.

WHERE A SLOT'S SCORE COMES FROM (strongest first)
  1. the person's override window (strategies.send_time_override_json) -- a
     HARD window: only slots inside it are used;
  2. (outside this module) the campaign's measured smart windows,
     app/services/send_windows.py, when that campaign switched them on;
  3. the account's own reply rates by local weekday x hour, once it has
     `best_time_min_sends` sends on the channel, blended toward 4./5. with
     Bayesian smoothing (K pseudo-sends);
  4. the strategy's send-time plan (strategies.send_time_plan_json), made when
     its research pipeline completed (ensure_plan);
  5. the channel / industry PRIORS below.

PRIORS, NOT PROMISES. The windows below are starting assumptions from widely
published industry send-time studies (general knowledge, not re-verified for
this change; LinkedIn's are measured for posts, not DMs). They only decide
WHERE INSIDE the permitted sending hours a message lands, and the account's
own results outweigh them once there is enough data.

PLACEMENT. Every quarter hour from `earliest` to earliest + the horizon
(`send_time_max_delay_hours`, the existing smart-send knob) that is inside the
permitted window in the lead's timezone is a candidate; the message goes to
the EARLIEST candidate scoring at least 90% of the best score in the horizon,
plus a deterministic per-lead spread of 0-29 minutes kept inside the window.
No candidate -> `earliest`. Candidates are UTC instants converted to local
time, so a DST gap or overlap can never produce a non-existent local time.

NOTHING HERE SENDS. Every message still goes through send_message_impl and
every gate in it (send window, daily approval, caps, review, ...).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time as _time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    ChannelType,
    Lead,
    Message,
    MessageStatus,
    Outcome,
    OutcomeEvent,
    Product,
    Sequence,
    Strategy,
)

logger = logging.getLogger(__name__)

FLAG = "best_time_scheduling_enabled"
MIN_SENDS_SETTING = "best_time_min_sends"
HORIZON_SETTING = "send_time_max_delay_hours"

CHANNELS: tuple[str, ...] = ("email", "linkedin", "whatsapp", "phone")
DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
WEEKDAYS = (0, 1, 2, 3, 4)

STEP = timedelta(minutes=15)
#: A slot inside a preferred window weighs this much more than one outside.
LIFT_IN, LIFT_OUT = 1.25, 0.85
#: Bayesian smoothing strength: the prior counts as this many sends per slot.
PSEUDO_SENDS = 20
#: The earliest candidate within this share of the best score wins.
GOOD_ENOUGH = 0.9
SPREAD_MINUTES = 30
#: An override is a hard window; look this far ahead to find its next slot.
OVERRIDE_HORIZON = timedelta(days=8)
LEARNING_LOOKBACK = timedelta(days=180)
LEARNING_MAX_SENDS = 5000
_CACHE_SECONDS = 600
_CACHE_KEY = "send_time_cache"
_SETTINGS_KEY = "send_time_settings"
_SETTINGS_SECONDS = 60
#: Learned rates live in Redis, written by the periodic refresh
#: (leadpilot.send_time.refresh_learning, every 6 h) -- never computed in the
#: send path. A miss means "no learning yet": the plan / priors decide.
LEARNED_KEY = "send_time:learned:{owner}:{channel}"
LEARNED_TTL_SECONDS = 7 * 3600
LEARNING_MAX_REPLIES = 20000

PLAN_VERSION = 1
PRIOR_SOURCE = ("Priors, not promises: widely published industry send-time studies "
                "(B2B email: HubSpot / Mailchimp / GetResponse and cold-email analyses; "
                "LinkedIn: social-media timing studies, measured for posts not DMs), "
                "general knowledge, not re-verified.")


# --------------------------------------------------------------------------
# Windows
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Window:
    """Local weekday set + [start, end) in minutes after local midnight."""
    days: tuple[int, ...]
    start: int
    end: int

    def contains(self, local: datetime) -> bool:
        minutes = local.hour * 60 + local.minute
        return local.weekday() in self.days and self.start <= minutes < self.end

    def to_json(self) -> dict:
        return {"days": list(self.days), "start": _hhmm(self.start), "end": _hhmm(self.end)}


def _hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _w(days, start: str, end: str) -> Window:
    return Window(tuple(days), _minutes(start), _minutes(end))


def _minutes(value: str) -> int:
    match = re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)", str(value or "").strip())
    if not match:
        raise ValueError(f"time must be HH:MM, got {value!r}")
    return int(match.group(1)) * 60 + int(match.group(2))


PRIORS: dict[str, dict] = {
    "email": {
        "windows": (_w((1, 2, 3), "09:00", "11:00"), _w((0, 4), "10:00", "11:00")),
        "reason": "B2B email gets the most replies mid-week, mid-morning "
                  "(Tue-Thu 9-11am); Monday and Friday mornings are a weaker second.",
    },
    "linkedin": {
        "windows": (_w((1, 2, 3), "08:00", "10:00"), _w((1, 2, 3), "12:00", "13:00")),
        "reason": "People check LinkedIn on weekday mornings and over lunch, "
                  "mid-week most (measured for posts, not messages).",
    },
    "whatsapp": {
        "windows": (_w(WEEKDAYS, "10:00", "12:00"), _w(WEEKDAYS, "15:00", "17:00")),
        "reason": "A business WhatsApp is best read late morning or mid-afternoon "
                  "on a weekday, never early morning or evening.",
    },
    "phone": {
        "windows": (_w((1, 2, 3), "10:00", "12:00"), _w((1, 2, 3), "14:00", "16:00")),
        "reason": "Calls connect best late morning and mid-afternoon, mid-week.",
    },
}

#: (label, keywords, windows for every channel, reason). First match wins.
INDUSTRY_PRIORS: tuple[tuple[str, tuple[str, ...], tuple[Window, ...], str], ...] = (
    ("restaurants", ("restaurant", "cafe", "café", "coffee shop", "hospitality",
                     "food service", "foodservice", "catering", "bakery", "pizzeria"),
     (_w(WEEKDAYS, "14:30", "16:30"),),
     "Restaurants and cafés are busy over lunch and dinner service; mid-afternoon "
     "(2:30-4:30pm) is when an owner or manager can read a message."),
    ("healthcare", ("clinic", "hospital", "dental", "dentist", "healthcare",
                    "health care", "medical", "physician", "physiotherap", "veterinar",
                    "pharmacy"),
     (_w(WEEKDAYS, "09:00", "11:00"),),
     "Clinic and hospital admin staff deal with messages early, before the "
     "day's patient peak (9-11am)."),
    ("retail", ("retail", "shop", "boutique", "store"),
     (_w(WEEKDAYS, "09:00", "09:30"), _w(WEEKDAYS, "14:00", "16:00")),
     "Shops are best reached just before opening or in the mid-afternoon lull."),
    ("real estate", ("real estate", "realtor", "realty", "property management",
                     "estate agent"),
     (_w(WEEKDAYS, "09:00", "10:30"),),
     "Agents plan their day first thing; mid-morning messages get read before "
     "viewings start."),
)


def _matches(keyword: str, text: str) -> bool:
    return re.search(rf"(?<![a-z]){re.escape(keyword)}", text) is not None


def industry_prior(industries) -> tuple[str, tuple[Window, ...], str] | None:
    text = " | ".join(str(i).lower() for i in (industries or []) if i)
    if not text:
        return None
    for label, keywords, windows, reason in INDUSTRY_PRIORS:
        if any(_matches(k, text) for k in keywords):
            return label, windows, reason
    return None


def strategy_icp(strategy: Strategy | None) -> dict:
    inputs = getattr(strategy, "pattern_inputs_json", None)
    icp = inputs.get("icp") if isinstance(inputs, dict) else None
    return icp if isinstance(icp, dict) else {}


def prior_plan(industries) -> dict:
    """{channel: {"windows": [Window], "reason", "basis"}} from the priors."""
    industry = industry_prior(industries)
    out = {}
    for channel, prior in PRIORS.items():
        if industry is not None:
            label, windows, reason = industry
            out[channel] = {"windows": list(windows), "reason": reason,
                            "basis": f"industry:{label}"}
        else:
            out[channel] = {"windows": list(prior["windows"]), "reason": prior["reason"],
                            "basis": "prior"}
    return out


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------


def enabled(db: Session) -> bool:
    from app.services import system_settings  # noqa: PLC0415

    return bool(system_settings.get(db, FLAG))


def _setting(db: Session, key: str):
    """A best-time setting other than the on/off flag, cached on the session
    for a minute (the send path places a message per call; reading the
    horizon from the database each time is a statement per send)."""
    from app.services import system_settings  # noqa: PLC0415

    cache = db.info.setdefault(_SETTINGS_KEY, {})
    hit = cache.get(key)
    if hit is not None and _time.monotonic() - hit[0] < _SETTINGS_SECONDS:
        return hit[1]
    value = system_settings.get(db, key)
    cache[key] = (_time.monotonic(), value)
    return value


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _channel(value) -> str:
    return str(getattr(value, "value", value) or "email")


def permitted_window() -> tuple[int, int, bool]:
    from app.config import settings  # noqa: PLC0415

    return (settings.send_window_start_hour, settings.send_window_end_hour,
            settings.send_window_skip_weekends)


# --------------------------------------------------------------------------
# Parsing / validating windows (model output, override input, stored JSON)
# --------------------------------------------------------------------------


def _day(value) -> int:
    if isinstance(value, bool):
        raise ValueError("day must be a weekday name or 0-6")
    if isinstance(value, int) and 0 <= value <= 6:
        return value
    text = str(value).strip()[:3].title()
    if text in DAY_NAMES:
        return DAY_NAMES.index(text)
    raise ValueError(f"unknown day {value!r}")


def parse_window(raw: dict, *, quarter_hours: bool = True) -> Window:
    """A stored / submitted {"days", "start", "end"} -> Window. Raises
    ValueError when it is not a usable window."""
    if not isinstance(raw, dict):
        raise ValueError("a window is an object with days, start and end")
    days = raw.get("days")
    if not isinstance(days, (list, tuple)) or not days:
        raise ValueError("days must be a non-empty list")
    parsed = tuple(sorted({_day(d) for d in days}))
    start, end = _minutes(raw.get("start")), _minutes(raw.get("end"))
    if quarter_hours and (start % 15 or end % 15):
        raise ValueError("times must be on the quarter hour (:00, :15, :30, :45)")
    if end - start < 30:
        raise ValueError("a window must be at least 30 minutes long and end after it starts")
    return Window(parsed, start, end)


def _windows_from_json(entry) -> list[Window]:
    if not isinstance(entry, dict):
        return []
    out = []
    for raw in entry.get("windows") or []:
        try:
            out.append(parse_window(raw, quarter_hours=False))
        except ValueError:
            continue
    return out


def overlaps_permitted(windows: list[Window], window: tuple[int, int, bool] | None = None
                       ) -> bool:
    start_h, end_h, skip_weekends = window or permitted_window()
    for w in windows:
        days = [d for d in w.days if not (skip_weekends and d >= 5)]
        if days and max(w.start, start_h * 60) < min(w.end, end_h * 60):
            return True
    return False


def override_windows(strategy: Strategy | None) -> list[Window]:
    data = getattr(strategy, "send_time_override_json", None)
    if not isinstance(data, dict):
        return []
    try:
        return [parse_window(data, quarter_hours=False)]
    except ValueError:
        return []


def validate_override(raw: dict) -> dict:
    """The override as stored, or ValueError (-> 422) with a plain reason."""
    window = parse_window(raw)
    if not overlaps_permitted([window]):
        start_h, end_h, skip = permitted_window()
        raise ValueError(
            f"That window is outside the permitted sending hours "
            f"({start_h:02d}:00-{end_h:02d}:00{', weekdays' if skip else ''}, in each "
            "prospect's local time), so nothing could ever be sent in it.")
    return window.to_json()


# --------------------------------------------------------------------------
# The send-time plan (made when the pipeline completes)
# --------------------------------------------------------------------------

_PLAN_SYSTEM = (
    "You are Vectorae's send-time planner. From an ideal customer profile you "
    "choose, per outreach channel, when in the PROSPECT'S OWN local time a "
    "first-touch message is most likely to be read and answered. Respect the "
    "prospect's working rhythm (service rushes, clinic hours, shift patterns). "
    'Respond with ONLY JSON: {"channels": {"email": {"windows": [{"days": '
    '["Tue","Wed"], "start": "09:00", "end": "11:00"}], "reason": "one '
    'sentence"}, "linkedin": {...}, "whatsapp": {...}}}. Times on the quarter '
    "hour, at most 4 windows per channel, weekdays unless the ICP clearly "
    "works weekends. No prose, no markdown fences."
)

_PLAN_CHANNEL_SCHEMA = {
    "type": "object",
    "properties": {
        "windows": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"days": {"type": "array", "items": {"type": "string"}},
                               "start": {"type": "string"},
                               "end": {"type": "string"}},
                "required": ["days", "start", "end"],
                "additionalProperties": False,
            },
        },
        "reason": {"type": "string"},
    },
    "required": ["windows", "reason"],
    "additionalProperties": False,
}

# Output contract (Phase 3 item 2). Each channel is optional (the priors fill
# the rest); the window count, quarter-hour and permitted-hours rules stay in
# _validated_model_channels (the API rejects minItems/maxItems).
PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "channels": {
            "type": "object",
            "properties": {ch: _PLAN_CHANNEL_SCHEMA for ch in ("email", "linkedin", "whatsapp")},
            "additionalProperties": False,
        },
    },
    "required": ["channels"],
    "additionalProperties": False,
}


def _plan_prompt(icp: dict, priors: dict) -> str:
    from app.services import prompt_safety  # noqa: PLC0415

    def _list(key):
        values = icp.get(key) or []
        return prompt_safety.inline(", ".join(str(v) for v in values)[:600]) or "(unknown)"

    lines = [prompt_safety.NOTICE, "",
             "IDEAL CUSTOMER PROFILE (from this strategy's research):",
             f"- Industries: {_list('industries')}",
             f"- Roles: {_list('titles')}",
             f"- Regions: {_list('locations')}",
             f"- Company sizes: {_list('company_size_ranges')}",
             "", "STARTING PRIORS (keep them unless the ICP gives a reason not to):"]
    for channel in ("email", "linkedin", "whatsapp"):
        entry = priors[channel]
        windows = "; ".join(describe_window(w) for w in entry["windows"])
        lines.append(f"- {channel}: {windows} -- {entry['reason']}")
    lines.append("")
    lines.append("Return the JSON now.")
    return "\n".join(lines)


def _validated_model_channels(data) -> dict:
    out = {}
    channels = data.get("channels") if isinstance(data, dict) else None
    if not isinstance(channels, dict):
        return out
    for channel in ("email", "linkedin", "whatsapp"):
        entry = channels.get(channel)
        if not isinstance(entry, dict):
            continue
        raw_windows = entry.get("windows")
        if not isinstance(raw_windows, list) or not 1 <= len(raw_windows) <= 4:
            continue
        try:
            windows = [parse_window(w) for w in raw_windows]
        except ValueError:
            continue
        if not overlaps_permitted(windows):
            continue
        reason = " ".join(str(entry.get("reason") or "").split())[:300]
        out[channel] = {"windows": windows,
                        "reason": reason or "Chosen for this strategy's customer profile.",
                        "basis": "model"}
    return out


def _plan_json(channels: dict, *, source: str, industries, now: datetime) -> dict:
    return {
        "version": PLAN_VERSION,
        "source": source,
        "generated_at": now.isoformat(),
        "industries": [str(i) for i in (industries or [])][:10],
        "channels": {ch: {"windows": [w.to_json() for w in entry["windows"]],
                          "reason": entry["reason"], "basis": entry["basis"]}
                     for ch, entry in channels.items()},
    }


def ensure_plan(session: Session, strategy: Strategy, *, now: datetime | None = None,
                use_model: bool = True) -> dict:
    """Make (once) and store the strategy's send-time plan. Never raises: any
    failure keeps the priors, and a stored plan is never re-bought."""
    existing = getattr(strategy, "send_time_plan_json", None)
    if isinstance(existing, dict) and existing.get("channels"):
        return existing
    now = _aware(now or datetime.now(timezone.utc))
    icp = strategy_icp(strategy)
    industries = icp.get("industries") or []
    channels = prior_plan(industries)
    source = "priors"
    if use_model:
        try:
            from app.services import usage_meter  # noqa: PLC0415
            from app.services.anthropic_client import get_client  # noqa: PLC0415

            with usage_meter.owner_scope(session, strategy, "send_time"):
                data = get_client().complete_json(system=_PLAN_SYSTEM,
                                                  prompt=_plan_prompt(icp, channels),
                                                  schema=PLAN_SCHEMA)
                usage_meter.flush_active(session)
            refined = _validated_model_channels(data)
            if refined:
                channels.update(refined)
                source = "model"
        except Exception as exc:  # noqa: BLE001 -- the priors are the fallback
            logger.warning("send-time plan for strategy %s: model refinement "
                           "failed (%s) -- using priors", strategy.id, exc)
            try:
                session.rollback()
            except Exception:  # noqa: BLE001
                pass
    plan = _plan_json(channels, source=source, industries=industries, now=now)
    try:
        strategy.send_time_plan_json = plan
        session.commit()
    except Exception:  # noqa: BLE001
        logger.exception("send-time plan for strategy %s could not be stored", strategy.id)
        session.rollback()
    return plan


def plan_for(strategy: Strategy | None, channel: str) -> dict:
    """{"windows": [Window], "reason", "basis"} for one channel: the stored
    plan's entry when usable, else the priors for the strategy's ICP."""
    channel = channel if channel in PRIORS else "email"
    plan = getattr(strategy, "send_time_plan_json", None)
    if isinstance(plan, dict):
        entry = (plan.get("channels") or {}).get(channel)
        windows = _windows_from_json(entry)
        if windows:
            return {"windows": windows, "reason": str(entry.get("reason") or ""),
                    "basis": str(entry.get("basis") or plan.get("source") or "plan")}
    return prior_plan(strategy_icp(strategy).get("industries"))[channel]


# --------------------------------------------------------------------------
# Learning: the account's own reply rates by local weekday x hour
# --------------------------------------------------------------------------


@dataclass
class Learned:
    sends: int
    replies: int
    slots: dict  # {(weekday, hour): [sends, replies]}

    @property
    def base_rate(self) -> float:
        return (self.replies + 1) / (self.sends + 2)


def learned_rates(session: Session, owner_id, channel: str,
                  now: datetime | None = None) -> Learned | None:
    """None below `best_time_min_sends` sends. Owner-filtered (lead ->
    strategy -> product.user_id); portable ORM, no PostgreSQL-only SQL."""
    from app.services import lead_timezone as lead_tz  # noqa: PLC0415

    if owner_id is None:
        return None
    now = _aware(now or datetime.now(timezone.utc))
    since = now - LEARNING_LOOKBACK
    owned = (select(Lead.id).join(Strategy, Strategy.id == Lead.strategy_id)
             .join(Product, Product.id == Strategy.product_id)
             .where(Product.user_id == owner_id))
    sends = session.execute(
        select(Message.lead_id, Message.sent_at, Lead.enrichment_json, Lead.strategy_id)
        .join(Lead, Lead.id == Message.lead_id)
        .where(Message.lead_id.in_(owned),
               Message.channel == ChannelType(channel),
               Message.status == MessageStatus.SENT,
               Message.sent_at.is_not(None),
               Message.sent_at >= since)
        .order_by(Message.sent_at.desc())
        .limit(LEARNING_MAX_SENDS)
    ).all()
    if len(sends) < int(_setting(session, MIN_SENDS_SETTING)):
        return None
    replies = session.execute(
        select(Outcome.lead_id, Outcome.ts)
        .where(Outcome.lead_id.in_(owned), Outcome.event == OutcomeEvent.REPLIED,
               Outcome.ts >= since)
        .order_by(Outcome.ts.desc())
        .limit(LEARNING_MAX_REPLIES)
    ).all()
    by_lead: dict = {}
    for lead_id, sent_at, enrichment, strategy_id in sends:
        by_lead.setdefault(lead_id, []).append((_aware(sent_at), enrichment, strategy_id))
    for rows in by_lead.values():
        rows.sort(key=lambda r: r[0])
    credited: set = set()
    replied_sends: set = set()
    for lead_id, ts in replies:
        rows = by_lead.get(lead_id)
        if not rows or ts is None:
            continue
        ts = _aware(ts)
        before = [r for r in rows if r[0] <= ts]
        if not before:
            continue
        key = (lead_id, before[-1][0])
        if key not in credited:
            credited.add(key)
            replied_sends.add(key)
    slots: dict = {}
    zones: dict = {}
    for lead_id, rows in by_lead.items():
        for sent_at, enrichment, strategy_id in rows:
            if lead_id not in zones:
                stub = _LeadTz(enrichment, strategy_id)
                zones[lead_id] = ZoneInfo(lead_tz.resolve(stub, session)[0])
            tz = zones[lead_id]
            local = sent_at.astimezone(tz)
            cell = slots.setdefault((local.weekday(), local.hour), [0, 0])
            cell[0] += 1
            if (lead_id, sent_at) in replied_sends:
                cell[1] += 1
    return Learned(sends=len(sends), replies=len(replied_sends), slots=slots)


class _LeadTz:
    """Just enough of a Lead for lead_timezone.resolve."""

    def __init__(self, enrichment, strategy_id=None):
        self.enrichment_json = enrichment if isinstance(enrichment, dict) else {}
        self.strategy_id = strategy_id


def clear_cache(session: Session) -> None:
    session.info.pop(_CACHE_KEY, None)
    session.info.pop(_SETTINGS_KEY, None)


def _redis():
    from app.core.redis_client import get_sync_redis  # noqa: PLC0415

    return get_sync_redis()


def _encode(learned: Learned | None) -> str:
    if learned is None:
        return json.dumps({"none": True})
    return json.dumps({"sends": learned.sends, "replies": learned.replies,
                       "slots": [[d, h, c[0], c[1]] for (d, h), c in learned.slots.items()]})


def _decode(raw) -> Learned | None:
    data = json.loads(raw)
    if data.get("none"):
        return None
    return Learned(sends=int(data["sends"]), replies=int(data["replies"]),
                   slots={(int(d), int(h)): [int(n), int(r)] for d, h, n, r in data["slots"]})


def cached_learned(owner_id, channel: str) -> Learned | None:
    """The account's learned rates from Redis (written by refresh_learning);
    None on a miss, a "below threshold" marker, or any Redis error. No SQL."""
    try:
        raw = _redis().get(LEARNED_KEY.format(owner=owner_id, channel=channel))
        return _decode(raw) if raw else None
    except Exception:  # noqa: BLE001 -- learning is an improvement, never a blocker
        logger.warning("send-time learned-rate cache unreadable for %s/%s", owner_id, channel)
        return None


def refresh_learning(session: Session, now: datetime | None = None,
                     owner_ids=None) -> dict:
    """Recompute every account's learned rates (accounts with a SENT message
    in the lookback) and store them in Redis for LEARNED_TTL_SECONDS. The
    periodic task runs this every 6 hours; nothing in the send path does."""
    now = _aware(now or datetime.now(timezone.utc))
    if owner_ids is None:
        owner_ids = session.execute(
            select(Product.user_id).distinct()
            .join(Strategy, Strategy.product_id == Product.id)
            .join(Lead, Lead.strategy_id == Strategy.id)
            .join(Message, Message.lead_id == Lead.id)
            .where(Message.status == MessageStatus.SENT,
                   Message.sent_at >= now - LEARNING_LOOKBACK)
        ).scalars().all()
    written = 0
    redis = _redis()
    for owner_id in owner_ids:
        for channel in CHANNELS:
            learned = learned_rates(session, owner_id, channel, now)
            redis.set(LEARNED_KEY.format(owner=owner_id, channel=channel), _encode(learned),
                      ex=LEARNED_TTL_SECONDS)
            written += 1
    return {"owners": len(owner_ids), "keys": written}


# --------------------------------------------------------------------------
# Scoring and placement
# --------------------------------------------------------------------------


class Scorer:
    def __init__(self, windows: list[Window], learned: Learned | None = None):
        self.windows = windows
        self.learned = learned

    def preferred(self, local: datetime) -> bool:
        return any(w.contains(local) for w in self.windows)

    def score(self, local: datetime) -> float:
        lift = LIFT_IN if self.preferred(local) else LIFT_OUT
        if self.learned is None:
            return lift
        prior_mean = self.learned.base_rate * lift
        sends, replies = self.learned.slots.get((local.weekday(), local.hour), (0, 0))
        return (replies + PSEUDO_SENDS * prior_mean) / (sends + PSEUDO_SENDS)


def _owner(session: Session, strategy: Strategy | None):
    """The strategy's owner, cached on the session (it never changes)."""
    if strategy is None:
        return None
    from app.services import notifications  # noqa: PLC0415

    owners = session.info.setdefault("send_time_owners", {})
    if strategy.id in owners:
        return owners[strategy.id]
    try:
        owner = notifications.owner_of_strategy(session, strategy)
    except Exception:  # noqa: BLE001
        return None
    owners[strategy.id] = owner
    return owner


def scorer_for(session: Session, strategy: Strategy | None, channel: str,
               now: datetime | None = None) -> tuple[Scorer, bool, dict]:
    """(scorer, hard, info). `hard` = the override window is in force."""
    override = override_windows(strategy)
    if override:
        return Scorer(override), True, {"basis": "override", "windows": override,
                                        "reason": "Your own sending window."}
    entry = plan_for(strategy, channel)
    owner = _owner(session, strategy)
    learned = cached_learned(owner, channel) if owner is not None else None
    info = {"basis": "learned" if learned is not None else entry["basis"],
            "windows": entry["windows"], "reason": entry["reason"], "learned": learned}
    return Scorer(entry["windows"], learned), False, info


def _spread(key) -> int:
    if key is None:
        return 0
    return int(hashlib.sha256(str(key).encode()).hexdigest(), 16) % SPREAD_MINUTES


def _ceil_quarter(dt: datetime) -> datetime:
    floor = dt.replace(minute=dt.minute - dt.minute % 15, second=0, microsecond=0)
    return floor if floor == dt else floor + STEP


def best_slot(session: Session, scorer: Scorer, hard: bool, earliest: datetime,
              tz_name: str, *, window: tuple[int, int, bool] | None = None,
              spread_key=None, horizon: timedelta | None = None) -> datetime:
    """The placement rule (module docstring). Pure apart from settings."""
    from app.services import sequence_engine as engine  # noqa: PLC0415

    earliest = _aware(earliest)
    zone = ZoneInfo(tz_name)
    if horizon is None:
        horizon = timedelta(hours=int(_setting(session, HORIZON_SETTING)))
    if hard:
        horizon = max(horizon, OVERRIDE_HORIZON)
    limit = earliest + horizon
    candidates: list[tuple[datetime, float]] = []
    current = _ceil_quarter(earliest)
    while current <= limit:
        if engine.in_send_window(current, tz_name, window):
            local = current.astimezone(zone)
            if not hard or scorer.preferred(local):
                candidates.append((current, scorer.score(local)))
        current += STEP
    if not candidates:
        # A person's own window is HARD: never fall back to a slot outside it.
        # The far horizon is re-checked (and re-placed) by the send path; the
        # API refuses a window that cannot overlap the permitted hours.
        return limit if hard else earliest
    best = max(score for _, score in candidates)
    threshold = best * GOOD_ENOUGH
    chosen = next(c for c, score in candidates if score >= threshold)
    offset = _spread(spread_key)
    if offset:
        shifted = chosen + timedelta(minutes=offset)
        local = shifted.astimezone(zone)
        if (shifted <= limit and engine.in_send_window(shifted, tz_name, window)
                and scorer.score(local) >= threshold
                and (not hard or scorer.preferred(local))):
            chosen = shifted
    return chosen


def resolved_window(session: Session, lead: Lead, channel,
                    strategy: Strategy | None = None) -> tuple[int, int, bool] | None:
    """The compliance-rule window the send path will apply to this lead and
    channel (compliance_rules.resolve), or None for the deployment baseline
    when it cannot be resolved or is empty (the send path then defers)."""
    from app.services import compliance_rules  # noqa: PLC0415

    try:
        rules = compliance_rules.resolve(session, lead, _channel(channel), strategy)
    except Exception:  # noqa: BLE001
        return None
    return None if rules.window_empty else rules.window


def override_conflicts(session: Session, strategy: Strategy, override: dict) -> list[str]:
    """The (region, channel) windows a proposed override can never overlap:
    the deployment baseline and every compliance-rule window resolved for the
    strategy's leads on the channels it uses. Empty = acceptable."""
    from app.services import compliance_rules, compliance_region  # noqa: PLC0415
    from app.services.send_diagnostics import _channels_in_use  # noqa: PLC0415

    window = parse_window(override)
    regions = {compliance_region.lead_region(lead) or "unknown" for lead in session.execute(
        select(Lead).where(Lead.strategy_id == strategy.id)).scalars()} or {"unknown"}
    workspace = compliance_rules.workspace_id_for_strategy(session, strategy)
    conflicts = []
    for channel in (_channels_in_use(session, strategy) or ["email"]):
        for region in sorted(regions):
            rules = compliance_rules.resolve_for(session, workspace, region, channel)
            if rules.window_empty or not overlaps_permitted([window], rules.window):
                start, end, _skip = rules.window
                conflicts.append(f"{channel} / {region}: {start:02d}:00-{end:02d}:00")
    return conflicts


def plan_wanted(session: Session, strategy: Strategy) -> bool:
    """Make the (metered) send-time plan only when it will be used: best-time
    on AND (Autopilot on for the strategy OR the strategy has an active
    sequence). Otherwise placement uses the priors with no model call."""
    from app.db.models import AutopilotLaunch, SequenceStatus  # noqa: PLC0415

    if not enabled(session):
        return False
    autopilot_on = session.execute(select(AutopilotLaunch.id).where(
        AutopilotLaunch.strategy_id == strategy.id,
        AutopilotLaunch.enabled.is_(True))).first() is not None
    if autopilot_on:
        return True
    return session.execute(select(Sequence.id).where(
        Sequence.strategy_id == strategy.id,
        Sequence.status == SequenceStatus.ACTIVE)).first() is not None


def place(session: Session, strategy: Strategy | None, lead: Lead, channel,
          earliest: datetime, *, window: tuple[int, int, bool] | None = None,
          spread_key=None, now: datetime | None = None) -> datetime:
    """Where a message due at `earliest` should be scheduled. `earliest`
    itself when the flag is off or nothing better is permitted."""
    earliest = _aware(earliest)
    from app.services.sequence_engine import lead_timezone  # noqa: PLC0415

    try:
        # Placement reads only: never flush the caller's pending writes from
        # here (the send path and enroll_leads commit them themselves).
        with session.no_autoflush:
            if not enabled(session):
                return earliest
            scorer, hard, _info = scorer_for(session, strategy, _channel(channel), now)
            return best_slot(session, scorer, hard, earliest, lead_timezone(lead),
                             window=window, spread_key=spread_key)
    except Exception:  # noqa: BLE001 -- never block scheduling on an optimisation
        logger.exception("best-time placement failed for lead %s -- using %s",
                         getattr(lead, "id", None), earliest)
        return earliest


def override_hold(session: Session, strategy: Strategy | None, message: Message, lead: Lead,
                  now: datetime, *, window: tuple[int, int, bool] | None = None
                  ) -> datetime | None:
    """The person's own sending window is HARD at send time too. A message due
    now OUTSIDE it -- scheduled before the window was saved and not yet
    re-placed, or parked at the horizon because a compliance rule added later
    leaves no overlap -- is deferred to the next slot inside it (and inside
    `window`), never sent outside it. None = no override, best-time off, a
    human-approved message (review queue: a person chose to send it), or `now`
    is inside the window. Reads no SQL unless an override is set. Placement
    only; an internal error lets the message continue (the compliance window
    is enforced separately by the send path)."""
    override = override_windows(strategy)
    if not override:
        return None
    from app.services import daily_send_approval  # noqa: PLC0415
    from app.services.sequence_engine import lead_timezone  # noqa: PLC0415

    try:
        with session.no_autoflush:
            if not enabled(session) or not daily_send_approval.is_automatic(session, message):
                return None
            tz_name = lead_timezone(lead)
            scorer = Scorer(override)
            if scorer.preferred(_aware(now).astimezone(ZoneInfo(tz_name))):
                return None
            return best_slot(session, scorer, True, now, tz_name, window=window,
                             spread_key=lead.id)
    except Exception:  # noqa: BLE001 -- an optimisation never blocks the send path
        logger.exception("best-time override check failed for message %s",
                         getattr(message, "id", None))
        return None


def pending_reslot_count(session: Session, strategy: Strategy,
                         now: datetime | None = None) -> int:
    """How many scheduled messages a reslot would look at (not those due
    within 5 minutes) -- what PUT /best-time reports before the task runs."""
    from sqlalchemy import func  # noqa: PLC0415

    now = _aware(now or datetime.now(timezone.utc))
    return int(session.execute(
        select(func.count(Message.id)).join(Sequence, Sequence.id == Message.sequence_id)
        .where(Sequence.strategy_id == strategy.id,
               Message.status == MessageStatus.SCHEDULED,
               Message.scheduled_at > now + timedelta(minutes=5))).scalar_one() or 0)


def reslot(session: Session, strategy: Strategy, now: datetime | None = None) -> int:
    """Re-place this strategy's future SCHEDULED messages (never earlier than
    their current time; not those due within 5 minutes). Commits."""
    from app.services.sequence_engine import lead_timezone  # noqa: PLC0415

    now = _aware(now or datetime.now(timezone.utc))
    if not enabled(session):
        return 0
    rows = session.execute(
        select(Message).join(Sequence, Sequence.id == Message.sequence_id)
        .where(Sequence.strategy_id == strategy.id,
               Message.status == MessageStatus.SCHEDULED,
               Message.scheduled_at.is_not(None))
    ).scalars().all()
    moved = 0
    scorers: dict = {}
    for message in rows:
        current = _aware(message.scheduled_at)
        if current <= now + timedelta(minutes=5):
            continue
        lead = session.get(Lead, message.lead_id)
        if lead is None:
            continue
        channel = _channel(message.channel)
        if channel not in scorers:
            scorers[channel] = scorer_for(session, strategy, channel, now)
        scorer, hard, _ = scorers[channel]
        new = best_slot(session, scorer, hard, current, lead_timezone(lead),
                        window=resolved_window(session, lead, channel, strategy),
                        spread_key=lead.id)
        if new != current:
            message.scheduled_at = new
            moved += 1
    session.commit()
    return moved


# --------------------------------------------------------------------------
# Explanation for the UI
# --------------------------------------------------------------------------


def _day_span(days) -> str:
    days = sorted(days)
    if not days:
        return ""
    runs, start, prev = [], days[0], days[0]
    for d in days[1:]:
        if d == prev + 1:
            prev = d
            continue
        runs.append((start, prev))
        start = prev = d
    runs.append((start, prev))
    parts = []
    for a, b in runs:
        if a == b:
            parts.append(DAY_NAMES[a])
        elif b == a + 1:
            parts.append(f"{DAY_NAMES[a]}, {DAY_NAMES[b]}")
        else:
            parts.append(f"{DAY_NAMES[a]}–{DAY_NAMES[b]}")
    return ", ".join(parts)


def describe_window(window: Window) -> str:
    return f"{_day_span(window.days)} {_hhmm(window.start)}–{_hhmm(window.end)}"


def explain(session: Session, strategy: Strategy, channel: str,
            now: datetime | None = None) -> dict:
    """What the strategy page shows: "Messages go out at ..." and why."""
    from app.config import settings  # noqa: PLC0415

    scorer, hard, info = scorer_for(session, strategy, channel, now)
    windows = info["windows"]
    when = " and ".join(describe_window(w) for w in windows)
    learned = info.get("learned")
    top = []
    if learned is not None:
        ranked = sorted(learned.slots.items(),
                        key=lambda kv: -scorer.score(_slot_local(kv[0])))
        top = [{"day": DAY_NAMES[d], "hour": h, "sends": c[0], "replies": c[1]}
               for (d, h), c in ranked[:3]]
    basis = info["basis"]
    if basis == "override":
        reason = "You set this window yourself. Messages are only placed inside it."
    elif basis == "learned":
        reason = (f"Based on your own results: {learned.replies} replies to "
                  f"{learned.sends} {channel} messages, blended with the starting "
                  f"windows ({info['reason']})")
    else:
        reason = info["reason"]
    start_h, end_h, skip = permitted_window()
    return {
        "channel": channel,
        "enabled": enabled(session),
        "basis": basis,
        "windows": [w.to_json() for w in windows],
        "summary": (f"Messages go out {when}, in each prospect's local time."
                    if windows else "Messages go out as soon as sending hours open."),
        "reason": reason,
        "learned_top_slots": top,
        "priors_note": None if basis in ("override", "learned") else PRIOR_SOURCE,
        "sending_hours": f"{start_h:02d}:00–{end_h:02d}:00{' on weekdays' if skip else ''}",
        "fallback_timezone": settings.send_window_timezone,
        "plan_source": ((strategy.send_time_plan_json or {}).get("source")
                        if isinstance(strategy.send_time_plan_json, dict) else None),
        "override": strategy.send_time_override_json if hard else None,
    }


def _slot_local(slot) -> datetime:
    """A representative local datetime for (weekday, hour), for scoring."""
    weekday, hour = slot
    base = datetime(2024, 1, 1, hour, 0)  # a Monday
    return base + timedelta(days=weekday)


def timezone_coverage(session: Session, strategy: Strategy) -> dict:
    """How many of the strategy's leads have a zone from their OWN location
    (recorded or resolved), and which fallback the rest use."""
    from app.services import lead_timezone as lead_tz  # noqa: PLC0415

    leads = session.execute(select(Lead).where(Lead.strategy_id == strategy.id)).scalars().all()
    sources: dict[str, int] = {}
    fallback = None
    for lead in leads:
        zone, source = lead_tz.resolve(lead, session)
        sources[source] = sources.get(source, 0) + 1
        if source not in (lead_tz.SOURCE_RECORDED, lead_tz.SOURCE_LOCATION):
            fallback = {"zone": zone, "source": source}
    own = sources.get(lead_tz.SOURCE_RECORDED, 0) + sources.get(lead_tz.SOURCE_LOCATION, 0)
    return {"leads": len(leads), "with_timezone": own, "sources": sources,
            "fallback": fallback}
