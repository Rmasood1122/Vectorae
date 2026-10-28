"""'Why isn't this sending?' -- every condition currently stopping a
strategy's outreach, per channel, in plain words with the fix.

Spec: docs/features/autopilot-launch.md section 3.3.

SAME CHECKS AS THE SEND PATH, NOT A COPY OF IT. Each reason reads the
function or the data its gate reads: plans.check_send_channel, the owner's
Gmail account (and the token failures the send path recorded on it),
linkedin_outreach.client_for / active_accounts, daily_send_approval's own
`_channel_hold`, compliance_rules.resolve for the next due lead (empty
window, consent required, the resolved send window and daily cap -- never the
baseline when a rule applies), sequence_engine.in_send_window /
allowance_left / whatsapp_allowance_left, mailbox_health.gate, and the
markers the holding gates write (send_holds prefixes, AWAITING_REVIEW, the
Autopilot approval hold). It is a summary for the strategy, evaluated for the
NEXT due message's lead where a gate is per lead -- so a rule that affects
only some leads can be under-reported. tests/test_send_diagnostics.py pins
every reason code it can emit to its gate by also driving send_message_impl
(or the launch check / channel factory) into the matching result; a
meta-test fails if a new code has no pinned test.

`channel_readiness` is also what Autopilot uses to decide which channels it
may launch (app/services/autopilot.py), so "Autopilot is waiting for Gmail"
and the panel's "Gmail is not connected" are the same check.

READ-ONLY. Nothing here writes, sends, or calls a model or a vendor.
"""

from __future__ import annotations

import re

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    ChannelType,
    EnrollmentStatus,
    GmailAccount,
    Lead,
    LeadStatus,
    Message,
    MessageStatus,
    Sequence,
    SequenceEnrollment,
    Strategy,
    StrategyStatus,
    User,
    WhatsAppTemplate,
    WhatsAppTemplateCategory,
    WhatsAppTemplateStatus,
)

CHANNELS: tuple[str, ...] = ("email", "linkedin", "whatsapp", "phone")
CHANNEL_LABELS = {"email": "Email", "linkedin": "LinkedIn", "whatsapp": "WhatsApp",
                  "phone": "AI phone calls"}

INTEGRATIONS_LINK = "/settings?tab=integrations"
DELIVERABILITY_LINK = "/settings?tab=deliverability"
PRICING_LINK = "/pricing"
APPROVAL_LINK = "/send-approval"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _reason(code: str, title: str, fix: str | None = None, link: str | None = None,
            *, count: int | None = None, blocking: bool = True, detail: str | None = None
            ) -> dict:
    return {"code": code, "title": title, "detail": detail, "fix": fix, "link": link,
            "count": count, "blocking": blocking}


def strategy_link(strategy_id) -> str:
    return f"/strategies/detail?id={strategy_id}"


def campaign_link(strategy_id, tab: str = "overview") -> str:
    return f"/campaigns?strategy={strategy_id}&tab={tab}"


# --------------------------------------------------------------------------
# Channel prerequisites (shared with Autopilot)
# --------------------------------------------------------------------------


def _plan_reason(owner: User | None, channel: str) -> dict | None:
    from app.core.plans import PlanLimitExceeded, check_send_channel  # noqa: PLC0415

    if owner is None:
        return _reason("owner_missing", "The account that owns this strategy was not found.")
    try:
        check_send_channel(owner, channel)
    except PlanLimitExceeded as exc:
        return _reason("channel_not_in_plan",
                       f"Your plan does not include {CHANNEL_LABELS[channel]}.",
                       f"Upgrade to a plan with {CHANNEL_LABELS[channel]} "
                       f"({exc.upgrade_to or 'a higher plan'}).", PRICING_LINK)
    return None


def whatsapp_template_ready(session: Session) -> WhatsAppTemplate | None:
    """An APPROVED marketing template that tells the recipient how to opt out
    -- what a cold WhatsApp step must reference (the send path re-checks)."""
    from app.services import whatsapp_templates as tmpl_rules  # noqa: PLC0415

    rows = session.execute(
        select(WhatsAppTemplate)
        .where(WhatsAppTemplate.status == WhatsAppTemplateStatus.APPROVED,
               WhatsAppTemplate.category == WhatsAppTemplateCategory.MARKETING)
        .order_by(WhatsAppTemplate.name, WhatsAppTemplate.version.desc())
    ).scalars().all()
    for tmpl in rows:
        try:
            tmpl_rules.validate_opt_out(tmpl.body, tmpl.category)
        except tmpl_rules.TemplateError:
            continue
        return tmpl
    return None


_TOKEN_FAILURE = re.compile(
    r"invalid_grant|oauth2\.googleapis\.com/token|GmailNotConnected|"
    r"Token has been expired or revoked|unauthorized_client", re.I)


def gmail_token_failed(session: Session, account_id) -> bool:
    """The token-health signal that exists: the send path records why a Gmail
    send failed on the message it claimed for this mailbox (sender_ref). If
    the mailbox's MOST RECENT email attempt failed on Google's token endpoint
    (a revoked or expired grant), the mailbox is not ready until it is
    reconnected and a send succeeds again."""
    latest = session.execute(
        select(Message.status, Message.error)
        .where(Message.sender_ref == str(account_id), Message.channel == ChannelType.EMAIL)
        .order_by(Message.updated_at.desc(), Message.id.desc()).limit(1)
    ).first()
    return bool(latest and latest[0] is not MessageStatus.SENT and latest[1]
                and _TOKEN_FAILURE.search(latest[1]))


def channel_readiness(session: Session, owner_id, channel: str) -> list[dict]:
    """The prerequisites `channel` is missing for this account; [] = ready.
    Account-level only (no lead, no strategy)."""
    from app.config import settings  # noqa: PLC0415

    owner = session.get(User, owner_id) if owner_id is not None else None
    missing: list[dict] = []
    plan = _plan_reason(owner, channel)
    if plan is not None:
        missing.append(plan)
    if channel == "email":
        account = session.execute(
            select(GmailAccount.id).where(GmailAccount.user_id == owner_id).limit(1)
        ).first()
        if account is None:
            missing.append(_reason("gmail_not_connected", "Gmail is not connected.",
                                   "Connect the Gmail account Vectorae should send from.",
                                   INTEGRATIONS_LINK))
        elif gmail_token_failed(session, account[0]):
            missing.append(_reason("gmail_token_invalid",
                                   "Google refused your Gmail connection (revoked or expired).",
                                   "Reconnect Gmail.", INTEGRATIONS_LINK))
    elif channel == "linkedin":
        from app.services import linkedin_outreach  # noqa: PLC0415

        if linkedin_outreach.client_for(session, owner_id) is None:
            missing.append(_reason("linkedin_not_configured",
                                   "LinkedIn sending is not set up on this deployment.",
                                   "An admin adds the Unipile key under Admin > Integrations.",
                                   INTEGRATIONS_LINK))
        elif not linkedin_outreach.active_accounts(session, owner_id):
            missing.append(_reason("linkedin_no_account", "No LinkedIn account is connected.",
                                   "Connect your LinkedIn account.", INTEGRATIONS_LINK))
    elif channel == "whatsapp":
        if not (settings.whatsapp_access_token and settings.whatsapp_phone_number_id):
            missing.append(_reason("whatsapp_not_configured",
                                   "WhatsApp Business is not set up on this deployment.",
                                   "An admin adds the WhatsApp Business credentials.",
                                   INTEGRATIONS_LINK))
        elif whatsapp_template_ready(session) is None:
            missing.append(_reason("whatsapp_no_template",
                                   "No approved WhatsApp template with an opt-out line.",
                                   "Create a marketing template (with \"Reply STOP to opt "
                                   "out.\") and get it approved by Meta.",
                                   "/campaigns?tab=templates"))
    elif channel == "phone":
        from app.services import system_settings  # noqa: PLC0415

        if not system_settings.get(session, "phone_calling_enabled"):
            missing.append(_reason("phone_calling_off", "AI phone calling is switched off.",
                                   "An admin can switch it on (phone_calling_enabled)."))
    return missing


# --------------------------------------------------------------------------
# Per-strategy diagnosis
# --------------------------------------------------------------------------


def _strategy_messages(strategy_id):
    return (select(Message).join(Sequence, Sequence.id == Message.sequence_id)
            .where(Sequence.strategy_id == strategy_id))


def _count(session: Session, query) -> int:
    return int(session.execute(select(func.count()).select_from(query.subquery()))
               .scalar_one() or 0)


def _channels_in_use(session: Session, strategy: Strategy) -> list[str]:
    from app.db.models import SequenceStep  # noqa: PLC0415

    used: set[str] = set()
    for seq in session.execute(
            select(Sequence).where(Sequence.strategy_id == strategy.id)).scalars():
        used.add(seq.channel.value)
        for step in session.execute(
                select(SequenceStep).where(SequenceStep.sequence_id == seq.id)).scalars():
            used.add(step.effective_channel(seq).value)
    # A scheduled message can be on another channel than its step (the channel
    # stagnation switch moves it), so the messages count too.
    for channel in session.execute(
            select(Message.channel).join(Sequence, Sequence.id == Message.sequence_id)
            .where(Sequence.strategy_id == strategy.id).distinct()).scalars():
        used.add(channel.value)
    from app.services import autopilot  # noqa: PLC0415

    row = autopilot.row_for(session, strategy.id)
    if row is not None and row.enabled:
        used.update((row.channels_json or {}).keys())
    return [c for c in CHANNELS if c in used]


def _channel_messages(strategy_id, channel: str):
    return _strategy_messages(strategy_id).where(Message.channel == ChannelType(channel))


def _diagnose_channel(session: Session, strategy: Strategy, owner_id, channel: str,
                      now: datetime) -> dict:
    from app.services import daily_send_approval as dsa  # noqa: PLC0415
    from app.services import send_holds  # noqa: PLC0415
    from app.services import sequence_engine as engine  # noqa: PLC0415

    reasons: list[dict] = list(channel_readiness(session, owner_id, channel))
    base = _channel_messages(strategy.id, channel)

    enrolled = _count(session, select(SequenceEnrollment.id)
                      .join(Sequence, Sequence.id == SequenceEnrollment.sequence_id)
                      .join(Message, (Message.sequence_id == Sequence.id)
                            & (Message.lead_id == SequenceEnrollment.lead_id))
                      .where(Sequence.strategy_id == strategy.id,
                             Message.channel == ChannelType(channel))
                      .distinct())
    active = _count(session, select(SequenceEnrollment.id)
                    .join(Sequence, Sequence.id == SequenceEnrollment.sequence_id)
                    .join(Message, (Message.sequence_id == Sequence.id)
                          & (Message.lead_id == SequenceEnrollment.lead_id))
                    .where(Sequence.strategy_id == strategy.id,
                           Message.channel == ChannelType(channel),
                           SequenceEnrollment.status == EnrollmentStatus.ACTIVE)
                    .distinct())
    scheduled = session.execute(
        base.where(Message.status == MessageStatus.SCHEDULED)
        .order_by(Message.scheduled_at)
    ).scalars().all()
    if enrolled == 0:
        reasons.append(_reason("not_launched", f"No {CHANNEL_LABELS[channel]} outreach has "
                               "been launched for this strategy.",
                               "Launch a sequence from the strategy page, or turn on "
                               "Autopilot.", strategy_link(strategy.id)))
    elif active == 0 and not scheduled:
        reasons.append(_reason("sequence_finished", "Every enrolled lead has finished or "
                               "stopped this sequence.", "Find more leads to contact.",
                               strategy_link(strategy.id), blocking=False))

    # Held markers written by the real gates.
    approval_held = [m for m in scheduled
                     if (m.error or "").startswith(dsa.ERROR_PREFIX)]
    due = [m for m in scheduled if _aware(m.scheduled_at) <= now]
    upcoming = [m for m in scheduled if not (m.error or "").startswith(dsa.ERROR_PREFIX)]

    # Daily send approval: the gate's own decision for this owner + channel.
    if (owner_id is not None and dsa.enabled(session)
            and channel in dsa.gated_channels(session)):
        hold = dsa._channel_hold(session, owner_id, channel, now)
        if hold is not None:
            reasons.append(_reason(
                "send_approval_missing",
                f"Today's automatic {CHANNEL_LABELS[channel]} sending is not approved.",
                "Approve today's automatic sending.", APPROVAL_LINK,
                count=len(approval_held), detail=hold.error.removeprefix(dsa.ERROR_PREFIX)))

    # The compliance rules the send path resolves for the NEXT due lead
    # (compliance_rules.resolve): an empty window, consent required, the
    # resolved window and daily cap.
    from app.services import compliance_rules  # noqa: PLC0415

    head = (due or upcoming or scheduled or [None])[0]
    rules = None
    if head is not None:
        lead = session.get(Lead, head.lead_id)
        tz = engine.lead_timezone(lead)
        rules = compliance_rules.resolve(session, lead, channel, strategy)
        if rules.window_empty:
            reasons.append(_reason("no_send_window",
                                   "The compliance rules leave no permitted sending hour for "
                                   "this prospect's region on this channel.",
                                   "An admin must fix the conflicting compliance rules."))
        elif not engine.in_send_window(now, tz, rules.window):
            nxt = engine.next_window_slot(now, tz, rules.window)
            reasons.append(_reason("outside_sending_hours",
                                   "It is outside sending hours for the next prospect.",
                                   f"Sending resumes at {nxt.isoformat()} ({tz}).",
                                   blocking=False))
        lacking = 0
        considered = {}
        for msg in scheduled[:200]:
            if msg.lead_id in considered:
                continue
            msg_lead = lead if msg.lead_id == head.lead_id else session.get(Lead, msg.lead_id)
            msg_rules = compliance_rules.resolve(session, msg_lead, channel, strategy)
            considered[msg.lead_id] = (msg_rules.consent_required and not
                                       compliance_rules.has_consent(session, msg_lead,
                                                                    ChannelType(channel)))
            lacking += int(considered[msg.lead_id])
        if lacking:
            reasons.append(_reason(
                "consent_required",
                f"{lacking} prospect(s) need recorded consent on this channel under the "
                "compliance rules; their messages are skipped.",
                "Record consent, or use another channel for them.",
                blocking=lacking == len(considered), count=lacking))
    held_autopilot = [m for m in scheduled if (m.error or "").startswith("autopilot: ")]
    if held_autopilot:
        reasons.append(_reason(
            "autopilot_approval_hold",
            f"{len(held_autopilot)} Autopilot message(s) wait because daily send approval "
            "does not cover this channel any more.",
            "An admin must turn daily send approval back on for this channel.",
            count=len(held_autopilot)))
    rule_cap = rules.daily_cap if rules is not None else None

    # Daily caps (email: the mailbox's allowance with health cap + F03 ramp;
    # WhatsApp: the number's allowance) -- the very functions the send path uses.
    if channel == "email" and owner_id is not None:
        from app.services import deliverability_autopilot, mailbox_health  # noqa: PLC0415

        account = session.execute(
            select(GmailAccount).where(GmailAccount.user_id == owner_id)).scalars().first()
        if account is not None:
            health = mailbox_health.gate(session, account.user_id, str(account.id))
            if health["state"] == mailbox_health.PAUSED:
                reasons.append(_reason("mailbox_paused",
                                       "Your mailbox is paused to protect its reputation.",
                                       health.get("reason") or "Check mailbox health and "
                                       "resume it when it is safe.", DELIVERABILITY_LINK))
            elif engine.allowance_left(
                    session, account, now,
                    min(c for c in (health["cap"], rule_cap) if c is not None)
                    if (health["cap"], rule_cap) != (None, None) else None,
                    autopilot=deliverability_autopilot.enabled(session)) <= 0:
                reasons.append(_reason("daily_cap_reached",
                                       "Today's email sending limit for your mailbox is used up.",
                                       "Nothing to do: sending continues tomorrow (warm-up "
                                       "protects your mailbox).", blocking=False))
    if channel == "whatsapp" and engine.whatsapp_allowance_left(session, now, rule_cap) <= 0:
        reasons.append(_reason("daily_cap_reached", "Today's WhatsApp sending limit is used up.",
                               "Sending continues tomorrow.", blocking=False))

    # Review queue and guard holds (the markers the gates write).
    review = _count(session, base.where(Message.status == MessageStatus.AWAITING_REVIEW))
    if review:
        reasons.append(_reason("held_in_review", f"{review} message(s) are waiting for "
                               "a person to approve them in the review queue.",
                               "Review them.", campaign_link(strategy.id, "review"),
                               count=review))
    for code, prefix, title, fix, link in (
            ("plan_holds", send_holds.PLAN_LIMIT_PREFIX,
             "message(s) are held because your plan no longer covers them.",
             "Upgrade your plan; they are released automatically.", PRICING_LINK),
            ("bounce_shield_holds", send_holds.BOUNCE_SHIELD_PREFIX,
             "email(s) are held by Bounce Shield (address not verified as valid).",
             "Re-verify the address or override it on the lead.", None),
            ("champion_holds", send_holds.CHAMPION_TRACKER_PREFIX,
             "message(s) are held until the prospect's role is re-checked.",
             "Nothing to do: the role check releases them.", None)):
        held = _count(session, base.where(send_holds.held_clause((prefix,))))
        if held:
            reasons.append(_reason(code, f"{held} {title}", fix, link, count=held))

    suppressed = _count(session, base.where(Message.status == MessageStatus.CANCELLED,
                                            Message.error.like("suppressed at send time%")))
    if suppressed:
        reasons.append(_reason("suppressed", f"{suppressed} message(s) were not sent "
                               "because the person is on the suppression list.",
                               blocking=False, count=suppressed))

    next_send = _aware(upcoming[0].scheduled_at) if upcoming else None
    return {
        "channel": channel,
        "label": CHANNEL_LABELS[channel],
        "sending": not any(r["blocking"] for r in reasons),
        "scheduled": len(scheduled),
        "next_send_at": next_send.isoformat() if next_send else None,
        "reasons": reasons,
    }


def diagnose(session: Session, strategy: Strategy, now: datetime | None = None) -> dict:
    """The full panel for one (already ownership-checked) strategy."""
    from app.services import autopilot, notifications  # noqa: PLC0415
    from app.services import daily_send_approval as dsa  # noqa: PLC0415
    from app.services import sequence_engine as engine  # noqa: PLC0415

    now = _aware(now or _now())
    owner_id = notifications.owner_of_strategy(session, strategy)
    general: list[dict] = []
    if strategy.status is StrategyStatus.NEEDS_HUMAN_REVIEW:
        general.append(_reason("verification_needs_review",
                               "Verification needs your review before anything can be sent.",
                               "Re-run verification or sign off the failed checks.",
                               strategy_link(strategy.id)))
    elif strategy.status not in (StrategyStatus.VERIFIED, StrategyStatus.EXECUTING):
        general.append(_reason("strategy_not_verified",
                               f"The strategy is {strategy.status.value.replace('_', ' ')}; "
                               "outreach starts once it is verified.",
                               None, strategy_link(strategy.id)))
    if strategy.campaign_state != engine.CAMPAIGN_ACTIVE:
        general.append(_reason("campaign_paused",
                               f"The campaign is paused ({strategy.campaign_pause_reason or strategy.campaign_state}).",
                               "Resume the campaign.", campaign_link(strategy.id)))
    verified_waiting = int(session.execute(
        select(func.count(Lead.id)).where(
            Lead.strategy_id == strategy.id, Lead.status == LeadStatus.VERIFIED,
            Lead.id.not_in(select(SequenceEnrollment.lead_id)
                           .join(Sequence, Sequence.id == SequenceEnrollment.sequence_id)
                           .where(Sequence.strategy_id == strategy.id)))
    ).scalar_one() or 0)
    enrolled_total = int(session.execute(
        select(func.count(SequenceEnrollment.id))
        .join(Sequence, Sequence.id == SequenceEnrollment.sequence_id)
        .where(Sequence.strategy_id == strategy.id)).scalar_one() or 0)
    if verified_waiting == 0 and enrolled_total == 0:
        general.append(_reason("no_verified_leads", "There are no verified leads to contact yet.",
                               "Find leads (or let Autopilot find them).",
                               strategy_link(strategy.id)))
    row = autopilot.row_for(session, strategy.id)
    if row is not None and row.enabled and not autopilot.available(session):
        general.append(_reason("autopilot_flag_off",
                               "Autopilot is on for this strategy but switched off on this "
                               "deployment.", "An admin can switch on autopilot_launch_enabled."))
    # Which zone the leads' local time comes from (app/services/lead_timezone.py).
    from app.services import lead_timezone as lead_tz  # noqa: PLC0415
    from app.services import send_time  # noqa: PLC0415

    coverage = send_time.timezone_coverage(session, strategy)
    guessed = coverage["leads"] - coverage["with_timezone"]
    if guessed and coverage["fallback"]:
        label = {lead_tz.SOURCE_STRATEGY: "the strategy's target region",
                 lead_tz.SOURCE_ACCOUNT: "your account's timezone",
                 lead_tz.SOURCE_DEFAULT: "the deployment default"}.get(
                     coverage["fallback"]["source"], "a fallback")
        general.append(_reason(
            "timezone_fallback",
            f"{guessed} lead(s) have no known location; their sending hours use "
            f"{label} ({coverage['fallback']['zone']}).",
            "Leads found by sourcing get their own timezone from their country and "
            "state; set your calendar timezone to change the fallback.",
            "/calendar", blocking=False, count=guessed))
    channels = _channels_in_use(session, strategy) or ["email"]
    per_channel = [_diagnose_channel(session, strategy, owner_id, ch, now) for ch in channels]
    blocking_general = any(r["blocking"] for r in general)
    return {
        "strategy_id": str(strategy.id),
        "checked_at": now.isoformat(),
        "sending": not blocking_general and any(c["sending"] for c in per_channel),
        "general": general,
        "verified_leads_not_enrolled": verified_waiting,
        "timezones": coverage["sources"],
        "channels": per_channel,
        "daily_send_approval_enabled": dsa.enabled(session),
    }

