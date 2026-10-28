"""Configurable compliance rules, resolved per send (Feature 8).

WHAT MOVED FROM CODE TO DATA
Stage 6 used to read its compliance parameters from deployment-wide settings:
the 09:00-17:00 send window, weekend skipping, the Gmail and WhatsApp daily
caps, and the 3% bounce-pause threshold. They are now overridable per
workspace, per recipient region (app/services/compliance_region.py) and per
channel, from /admin/compliance-rules -- the same "data, not code" stance as
app/pipeline/registry.py and system_settings.

THE BASELINE IS THE CONTRACT
The hardcoded baseline is today's settings, unchanged. An empty table -- a
fresh deployment -- therefore behaves exactly as before, and that is pinned by
a test. Rules can move a send window within a hard band, and can only TIGHTEN
caps and the bounce threshold; they can never raise either past the baseline.

RESOLUTION (most specific valid row that sets a field wins)
  specificity: workspace over global, then a named region, then a named channel.
  A field no matching row sets comes from the baseline.

IT FAILS CLOSED, NEVER OPEN
  * the rules table cannot be read (DB error)  -> baseline, logged;
  * a MATCHING row is invalid (start >= end, hours outside the hard band, a
    negative cap, a threshold outside (0, 1]) -- which the API refuses, so it
    can only arrive by raw SQL -- -> that send gets the INTERSECTION of the
    baseline and every valid matching row: latest start, earliest end,
    weekends skipped if any says so, the lowest cap and threshold, consent
    required if any says so. A broken rule can never widen what is allowed;
  * an intersection with no hours left -> no send window at all; the send
    path defers the message and transmits nothing.

CONSENT REQUIRED
Checked against real evidence: the WhatsApp opt-in record, the phone call
consent record. Email and LinkedIn have NO consent record in Vectorae, so a
consent rule on those channels skips the send -- the restrictive reading, and
documented as such.

None of this is legal advice. It is the operator's policy, enforced.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import ChannelType, ComplianceRule, Lead, Product, Strategy, Workspace
from app.services import compliance_region

logger = logging.getLogger(__name__)

GLOBAL = "global"
ANY = "*"
REGIONS = ("us", "ca", "eu", "uk", "sg", "au", "nz", "other", "unknown", ANY)
CHANNELS = tuple(c.value for c in ChannelType) + (ANY,)
RULE_FIELDS = ("send_start_hour", "send_end_hour", "skip_weekends", "daily_cap",
               "consent_required", "bounce_pause_threshold")

# The band a configured send window must stay inside, in the recipient's local
# time. Not a setting: widening it is exactly the change this module exists to
# stop a misconfiguration from making.
HOUR_MIN = 7
HOUR_MAX = 20


@dataclass(frozen=True)
class Effective:
    send_start_hour: int
    send_end_hour: int
    skip_weekends: bool
    daily_cap: int | None           # None = the channel has no cap from this layer
    consent_required: bool
    bounce_pause_threshold: float
    rule_ids: tuple[str, ...] = field(default=())
    failed_closed: bool = False      # an invalid row, or the table was unreadable

    @property
    def window(self) -> tuple[int, int, bool]:
        return (self.send_start_hour, self.send_end_hour, self.skip_weekends)

    @property
    def window_empty(self) -> bool:
        return self.send_start_hour >= self.send_end_hour


def baseline_cap(channel: str) -> int | None:
    if channel == ChannelType.EMAIL.value:
        return settings.gmail_daily_cap
    if channel == ChannelType.WHATSAPP.value:
        return settings.whatsapp_daily_cap
    return None


def baseline(channel: str = ANY) -> Effective:
    return Effective(
        send_start_hour=settings.send_window_start_hour,
        send_end_hour=settings.send_window_end_hour,
        skip_weekends=settings.send_window_skip_weekends,
        daily_cap=baseline_cap(channel),
        consent_required=False,
        bounce_pause_threshold=settings.bounce_rate_pause_threshold,
    )


# --------------------------------------------------------------------------
# Validation (shared by the API and the resolver)
# --------------------------------------------------------------------------


def errors(values: dict, channel: str = ANY) -> list[str]:
    """What is wrong with a rule's values. Empty = valid."""
    out: list[str] = []
    start, end = values.get("send_start_hour"), values.get("send_end_hour")
    for name, hour in (("send_start_hour", start), ("send_end_hour", end)):
        if hour is not None and (isinstance(hour, bool) or not isinstance(hour, int)
                                 or not HOUR_MIN <= hour <= HOUR_MAX):
            out.append(f"{name} must be a whole hour between {HOUR_MIN} and {HOUR_MAX}")
    if isinstance(start, int) and isinstance(end, int) and start >= end:
        out.append("send_start_hour must be before send_end_hour")
    cap = values.get("daily_cap")
    if cap is not None and (isinstance(cap, bool) or not isinstance(cap, int) or cap < 0):
        out.append("daily_cap must be a whole number, 0 or more")
    elif cap is not None and (limit := baseline_cap(channel)) is not None and cap > limit:
        out.append(f"daily_cap may not exceed the deployment cap of {limit} for {channel}")
    threshold = values.get("bounce_pause_threshold")
    if threshold is not None and (isinstance(threshold, bool)
                                  or not isinstance(threshold, (int, float))
                                  or not 0 < threshold <= settings.bounce_rate_pause_threshold):
        out.append("bounce_pause_threshold must be above 0 and at most the baseline "
                   f"{settings.bounce_rate_pause_threshold}")
    for flag in ("skip_weekends", "consent_required"):
        if values.get(flag) is not None and not isinstance(values.get(flag), bool):
            out.append(f"{flag} must be true or false")
    return out


def _row_values(row: ComplianceRule) -> dict:
    return {name: getattr(row, name) for name in RULE_FIELDS}


def _row_errors(row: ComplianceRule) -> list[str]:
    problems = errors(_row_values(row), row.channel)
    if row.region not in REGIONS:
        problems.append(f"unknown region {row.region!r}")
    if row.channel not in CHANNELS:
        problems.append(f"unknown channel {row.channel!r}")
    return problems


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------


def workspace_id_for_strategy(session: Session, strategy: Strategy | None) -> uuid.UUID | None:
    if strategy is None:
        return None
    return session.execute(
        select(Workspace.id)
        .join(Product, Product.user_id == Workspace.owner_user_id)
        .where(Product.id == strategy.product_id)
    ).scalar_one_or_none()


def _load(session: Session, workspace_id: uuid.UUID | None) -> list[ComplianceRule]:
    scopes = [GLOBAL] + ([str(workspace_id)] if workspace_id else [])
    return list(session.execute(
        select(ComplianceRule).where(ComplianceRule.scope.in_(scopes))
    ).scalars())


def _specificity(row: ComplianceRule) -> tuple[int, int, int]:
    return (int(row.scope != GLOBAL), int(row.region != ANY), int(row.channel != ANY))


def resolve_for(session: Session, workspace_id: uuid.UUID | None, region: str,
                channel: str) -> Effective:
    base = baseline(channel)
    try:
        rows = _load(session, workspace_id)
    except SQLAlchemyError:
        session.rollback()
        logger.exception("compliance rules unreadable -- applying the baseline")
        return Effective(**{**base.__dict__, "failed_closed": True})

    matching = [r for r in rows
                if r.region in (region, ANY) and r.channel in (channel, ANY)]
    valid = [r for r in matching if not _row_errors(r)]
    invalid = [r for r in matching if r not in valid]
    ids = tuple(str(r.id) for r in matching)

    if invalid:
        logger.error("invalid compliance rule(s) %s for region=%s channel=%s -- failing closed",
                     [str(r.id) for r in invalid], region, channel)
        values = [base.__dict__] + [
            {k: v for k, v in _row_values(r).items() if v is not None} for r in valid]

        def pick(name, combine, default):
            present = [v[name] for v in values if v.get(name) is not None]
            return combine(present) if present else default

        caps = [v["daily_cap"] for v in values if v.get("daily_cap") is not None]
        return Effective(
            send_start_hour=pick("send_start_hour", max, base.send_start_hour),
            send_end_hour=pick("send_end_hour", min, base.send_end_hour),
            skip_weekends=pick("skip_weekends", any, base.skip_weekends),
            daily_cap=min(caps) if caps else base.daily_cap,
            consent_required=pick("consent_required", any, False),
            bounce_pause_threshold=pick("bounce_pause_threshold", min,
                                        base.bounce_pause_threshold),
            rule_ids=ids, failed_closed=True,
        )

    resolved = dict(base.__dict__)
    for name in RULE_FIELDS:
        for row in sorted(valid, key=_specificity, reverse=True):
            value = getattr(row, name)
            if value is not None:
                resolved[name] = value
                break
    # Tighten-only fields, whatever a valid row said.
    if base.daily_cap is not None and resolved["daily_cap"] is not None:
        resolved["daily_cap"] = min(resolved["daily_cap"], base.daily_cap)
    resolved["bounce_pause_threshold"] = min(resolved["bounce_pause_threshold"],
                                             base.bounce_pause_threshold)
    resolved["rule_ids"] = ids
    resolved["failed_closed"] = False
    # A valid rule may set only one end of the window; paired with the other end
    # from a different layer the window could be empty -- and empty is closed.
    return Effective(**resolved)


def resolve(session: Session, lead: Lead, channel: str,
            strategy: Strategy | None = None) -> Effective:
    strategy = strategy or session.get(Strategy, lead.strategy_id)
    return resolve_for(session, workspace_id_for_strategy(session, strategy),
                       compliance_region.lead_region(lead) or "unknown", channel)


def bounce_threshold(session: Session, strategy: Strategy) -> float:
    """The campaign's bounce-pause threshold: its workspace's rule, region and
    channel ANY (a campaign pauses as a whole), never above the baseline."""
    return resolve_for(session, workspace_id_for_strategy(session, strategy),
                       ANY, ANY).bounce_pause_threshold


def account_bounce_threshold(session: Session, user_id: uuid.UUID) -> float:
    """The bounce-pause threshold every campaign of this account pauses at:
    its workspace's rule (region and channel ANY), never above the baseline.
    Account-level reports (the CRM campaigns dashboard, the Bounce Shield
    report) show THIS, the number the auto-pause actually uses, not the 3%
    baseline -- one helper so they cannot disagree (gaps-sweep-2 #11). Equal
    to bounce_threshold() for every strategy the account owns (strategies
    resolve their workspace through their product's owner)."""
    uid = user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(str(user_id))
    workspace_id = session.execute(
        select(Workspace.id).where(Workspace.owner_user_id == uid)).scalar_one_or_none()
    return resolve_for(session, workspace_id, ANY, ANY).bounce_pause_threshold


def campaign_daily_cap(session: Session, strategy: Strategy, channel: str) -> int | None:
    """The daily cap a workspace rule sets for `channel` in EVERY region (rule
    region ANY), never above the baseline. What the campaign page shows as the
    cap (product defect #21); a region-specific rule can lower it further for
    the leads in that region, which the send path applies per lead."""
    return resolve_for(session, workspace_id_for_strategy(session, strategy),
                       ANY, channel).daily_cap


def has_consent(session: Session, lead: Lead, channel: ChannelType) -> bool:
    if channel is ChannelType.WHATSAPP:
        from app.db.models import OptInStatus  # noqa: PLC0415
        from app.services import whatsapp_optin  # noqa: PLC0415

        return whatsapp_optin.current_status(session, lead.id) is OptInStatus.OPTED_IN
    if channel is ChannelType.PHONE:
        from app.services import phone_calls  # noqa: PLC0415

        return bool(phone_calls.consent_ok(session, lead))
    # Email and LinkedIn: Vectorae records no consent for these channels.
    return False
