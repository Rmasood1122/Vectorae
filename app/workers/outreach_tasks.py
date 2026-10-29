"""Outreach Celery tasks — dispatcher (Beat), idempotent send, reply polling.

The send task is the LAST LINE of compliance enforcement: it re-checks the
suppression list, the campaign state, the enrollment state, the send
window and the daily allowance immediately before transmitting — no
matter what was true when the message was scheduled. There is no bypass
flag anywhere in this codebase, by design.

All `_impl` functions take a Session + explicit `now` so the test suite
drives them deterministically with fakes and SQLite.
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import settings
from app.db.base import SessionLocal
from app.db.models import (
    ChannelType,
    EnrollmentStatus,
    GmailAccount,
    InboundReply,
    Lead,
    LeadStatus,
    Message,
    MessageStatus,
    OptInSource,
    OptInStatus,
    Outcome,
    OutcomeEvent,
    Product,
    Sequence,
    SequenceEnrollment,
    Strategy,
    User,
    WhatsAppStepKind,
    WhatsAppTemplate,
    WhatsAppTemplateStatus,
)
from app.integrations.outreach_base import OutboundMessage, OutreachChannel
from app.services import sequence_engine as engine
from app.services import message_personalization as personalization
from app.services.message_personalization import (
    preview_whatsapp_body,
    render_message,
    render_whatsapp_variables,
)
from app.services.reply_classification import classify_reply
from app.services import compliance_rules, reengagement
from app.services import whatsapp_optin as optin_svc
from app.services.whatsapp_window import window_state_for_lead
from app.workers.celery_app import celery_app
from app.workers.lead_tasks import is_suppressed

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------
# Channel/account resolution (tests monkeypatch _get_channel)
# --------------------------------------------------------------------------


def _account_for_strategy(session: Session, strategy: Strategy) -> GmailAccount:
    product = session.get(Product, strategy.product_id)
    account = session.execute(
        select(GmailAccount).where(GmailAccount.user_id == product.user_id)
    ).scalar_one_or_none()
    if account is None:
        raise RuntimeError(
            f"no connected Gmail account for user of strategy {strategy.id} — "
            "connect one via /integrations/gmail/auth-url first"
        )
    return account


def _get_channel(session: Session, account: GmailAccount) -> OutreachChannel:
    from app.integrations.gmail import GmailChannel
    return GmailChannel(account=account, session=session)


def _get_whatsapp_channel(session: Session) -> OutreachChannel:
    """Tests monkeypatch THIS function (like _get_channel for Gmail)."""
    from app.integrations.whatsapp import WhatsAppChannel
    return WhatsAppChannel(session=session)


# --------------------------------------------------------------------------
# Dispatcher (Celery Beat): find due messages, enqueue sends
# --------------------------------------------------------------------------


def dispatch_due_messages_impl(session: Session, now: datetime | None = None,
                               enqueue=None) -> list[str]:
    """Resume due paused enrollments, then enqueue every due SCHEDULED
    message whose campaign is active. Returns the enqueued message ids."""
    now = now or _now()
    enqueue = enqueue or (lambda mid: send_message.delay(mid))

    engine.resume_due_enrollments(session, now)

    rows = session.execute(
        select(Message, Strategy)
        .join(Lead, Lead.id == Message.lead_id)
        .join(Strategy, Strategy.id == Lead.strategy_id)
        .where(Message.status == MessageStatus.SCHEDULED,
               Message.scheduled_at <= now)
        .order_by(Message.scheduled_at)
    ).all()

    enqueued: list[str] = []
    for message, strategy in rows:
        if strategy.campaign_state != engine.CAMPAIGN_ACTIVE:
            continue  # paused campaigns send nothing
        enqueue(str(message.id))
        enqueued.append(str(message.id))
    return enqueued


# --------------------------------------------------------------------------
# The send task (idempotent, compliance-enforcing)
# --------------------------------------------------------------------------


def send_message_impl(session: Session, message_id: uuid.UUID,
                      channel: OutreachChannel | None = None,
                      now: datetime | None = None,
                      outcome_source: str | None = None) -> str:
    """Send one message, re-checking every compliance gate at send time.

    `outcome_source` tags the OutcomeEvent.SENT row this writes with WHY the
    send happened. It is None for the ordinary scheduled path (whose outcome
    meta stays byte-identical to what M3 wrote, so the learning loop's
    existing aggregates are untouched) and "auto_followup" when Feature 1's
    sweep drove it -- which is what makes an automatic send distinguishable
    from a sequenced one in the audit trail and in the learning loop.
    """
    now = now or _now()
    message = session.get(Message, message_id)
    if message is None:
        return "missing"

    # ---- idempotency guards: a retry can NEVER double-send ----------------
    if message.status is MessageStatus.SENT:
        return "already_sent"
    if message.status is MessageStatus.SENDING:
        # A previous attempt claimed it and may have transmitted before
        # crashing. Never resend automatically.
        logger.warning("message %s stuck in 'sending' — flagged, not resent", message_id)
        return "in_flight_needs_review"
    if message.status is not MessageStatus.SCHEDULED:
        return f"skipped_{message.status.value}"

    lead = session.get(Lead, message.lead_id)
    sequence = session.get(Sequence, message.sequence_id)
    strategy = session.get(Strategy, lead.strategy_id)
    enrollment = session.execute(
        select(SequenceEnrollment).where(
            SequenceEnrollment.sequence_id == message.sequence_id,
            SequenceEnrollment.lead_id == message.lead_id,
        )
    ).scalar_one_or_none()

    # ---- F04 Reply Copilot: an approved answer to an inbound reply --------
    # SAFETY GUARD. Such a row passes the stopped-enrollment gate below ONLY
    # when the stop was the reply it answers (reply_copilot.answers_the_stop);
    # every other gate in this function applies to it unchanged. Its own
    # extra checks (flag still on, draft still there, no later decline or
    # opt-out) cancel it here. Ordinary messages never enter this branch.
    from app.services import reply_copilot  # noqa: PLC0415

    is_reply_copilot = message.origin == reply_copilot.ORIGIN
    if is_reply_copilot:
        held = reply_copilot.send_time_hold(session, message, lead)
        if held is not None:
            return held
        if outcome_source is None:
            outcome_source = reply_copilot.OUTCOME_SOURCE

    # ---- Part 1 Feature 7: a "not now" re-engagement touch ----------------
    # Like a Reply Copilot answer it continues the conversation whose reply
    # stopped the sequence: it passes the stopped-enrollment gate below ONLY
    # when reengagement_memory.answers_the_stop ties the stop to that reply.
    # Its own checks (switch off, plan cancelled, prospect no longer
    # appropriate, a newer reply) cancel it here; every other gate applies.
    from app.services import reengagement_memory  # noqa: PLC0415

    is_notnow_touch = message.origin == reengagement_memory.ORIGIN
    if is_notnow_touch:
        held = reengagement_memory.send_time_hold(session, message, lead, now=now)
        if held is not None:
            return held

    # ---- hard gates (checked at SEND time, always) -------------------------
    if strategy.campaign_state != engine.CAMPAIGN_ACTIVE:
        return "campaign_paused"
    if enrollment is None or (enrollment.status is EnrollmentStatus.STOPPED
                              and not reply_copilot.answers_the_stop(session, message, enrollment)
                              and not reengagement_memory.answers_the_stop(
                                  session, message, enrollment)):
        message.status = MessageStatus.CANCELLED
        message.error = "enrollment stopped"
        session.commit()
        return "cancelled_stopped"
    if enrollment.status is EnrollmentStatus.PAUSED:
        return "enrollment_paused"
    # ---- a lead the account REMOVED is never sent to -----------------------
    # "Remove" sets the lead to DROPPED (app/api/ui_support.py patch_lead,
    # which also stops its enrollments); a GDPR erasure and the sourcing
    # chain's suppression drop set it too. Checked here, at send time, so a
    # message scheduled before the removal -- or a status set by any other
    # path -- can never go out.
    if lead.status is LeadStatus.DROPPED:
        message.status = MessageStatus.CANCELLED
        message.error = "lead removed (status dropped)"
        _audit(session, strategy, lead, message, "blocked_lead_removed")
        session.commit()
        if enrollment.status is not EnrollmentStatus.STOPPED:
            engine.stop_enrollment(session, enrollment, reason=engine.STOP_LEAD_REMOVED)
        return "cancelled_lead_removed"
    # ---- a CLOSED lead gets no more sequence steps -------------------------
    # Closed won / lost / disqualified end the sequence (sequence_engine.
    # stop_for_status, called by every status writer). Checked here too so an
    # enrollment a writer did not stop -- or one closed before that rule
    # existed -- cannot send its next step. Sequence steps only (origin NULL).
    # Other origins have their own lead-status gates: a Reply Copilot answer
    # to a closed lead is cancelled by reply_copilot.send_time_hold
    # (cancelled_lead_status -- won / lost / disqualified are not answerable),
    # and the post-meeting follow-up is not a Message at all.
    if lead.status in engine.CLOSED_STATUSES and message.origin is None:
        message.status = MessageStatus.CANCELLED
        message.error = f"lead closed (status {lead.status.value})"
        _audit(session, strategy, lead, message, "blocked_lead_closed")
        session.commit()
        if enrollment.status in (EnrollmentStatus.ACTIVE, EnrollmentStatus.PAUSED):
            engine.stop_enrollment(session, enrollment,
                                   reason=engine.status_stop_reason(lead.status))
        return "cancelled_lead_closed"
    is_whatsapp = message.channel is ChannelType.WHATSAPP
    # Feature Group 5: the LinkedIn channel addresses the lead's profile.
    is_linkedin = message.channel is ChannelType.LINKEDIN
    # Feature Group 6: the phone channel dials the lead's number.
    is_phone = message.channel is ChannelType.PHONE
    target = (lead.phone if (is_whatsapp or is_phone)
              else lead.linkedin_url if is_linkedin else lead.email)
    if not target or is_suppressed(session, lead.email, lead.phone,
                                   linkedin=lead.linkedin_url):
        message.status = MessageStatus.CANCELLED
        message.error = "suppressed at send time" if target else "no address for channel"
        _audit(session, strategy, lead, message,
               "blocked_suppressed" if target else "blocked_no_address")
        session.commit()
        if enrollment.status is not EnrollmentStatus.STOPPED:
            # The honest reason: a lead with no address for this channel was
            # NOT suppressed, and must not be counted as a compliance stop.
            engine.stop_enrollment(session, enrollment,
                                   reason="suppressed" if target else engine.STOP_NO_ADDRESS)
        return "cancelled_suppressed" if target else "cancelled_no_address"

    # ---- Local business mode, Part B: customer win-back (SAFETY GUARD) -----
    # Every message: a prospect in a customer sequence, or an imported
    # customer in a prospect sequence, is cancelled. A customer message also
    # needs the win-back flag, email or WhatsApp, a non-healthcare local
    # business (HIPAA) and -- for email -- a current opt-in in the consent
    # ledger (WhatsApp opt-in is the existing gate below). Lookup error fails
    # CLOSED: deferred an hour. docs/features/local-business-mode.md.
    from app.services import local_business  # noqa: PLC0415

    try:
        winback_block = local_business.customer_send_block(session, lead, sequence,
                                                           message, strategy)
    except Exception:
        logger.exception("win-back check failed for message %s -- deferred", message_id)
        session.rollback()
        message = session.get(Message, message_id)
        message.scheduled_at = now + timedelta(hours=1)
        message.error = "win-back consent could not be checked -- deferred (fails closed)"
        session.commit()
        return "deferred_winback_unavailable"
    if winback_block is not None:
        local_business.cancel_for_block(session, message, winback_block)
        _audit(session, strategy, lead, message, local_business.AUDIT_BLOCKED,
               extra={"reason": winback_block})
        session.commit()
        if enrollment.status is not EnrollmentStatus.STOPPED:
            engine.stop_enrollment(session, enrollment, reason=local_business.STOP_REASON)
        return local_business.SEND_RESULT

    # ---- F05 Guardrails Studio: the account's exclusion lists -------------
    # SAFETY GUARD. Customers, open deals, churned accounts and do-not-contact
    # entries of THIS account (domain / company / contact), checked NOW -- an
    # exclusion added after the message was scheduled still stops it. Off
    # (system setting guardrails_enabled) = one settings read, no list
    # lookup, and nothing else changes. A lookup error
    # fails CLOSED: the message waits an hour and is checked again; it is
    # never sent unchecked.
    from app.services import guardrails  # noqa: PLC0415

    try:
        excluded = guardrails.send_block(session, lead, strategy)
    except Exception:
        logger.exception("exclusion check failed for message %s -- deferred", message_id)
        session.rollback()
        message = session.get(Message, message_id)
        message.scheduled_at = now + timedelta(hours=1)
        message.error = "exclusion list could not be checked -- deferred (fails closed)"
        session.commit()
        return "deferred_guardrails_unavailable"
    if excluded is not None:
        message.status = MessageStatus.CANCELLED
        message.error = f"excluded by guardrails: {excluded.kind} ({excluded.reason})"
        _audit(session, strategy, lead, message, "blocked_excluded",
               extra={"exclusion": excluded.audit()})
        session.commit()
        if enrollment.status is not EnrollmentStatus.STOPPED:
            engine.stop_enrollment(session, enrollment, reason="suppressed_excluded")
        return "cancelled_excluded"
    # ---- F10 Champion Tracker (SAFETY GUARD, every channel) ----------------
    # 1. Departure block. A person confirmed to have LEFT the company this lead
    #    was sourced at is never messaged at the old lead or the old address
    #    again. A recorded departure is a fact, so this is NOT behind the flag
    #    (no rows = no change). Lookup error fails CLOSED: deferred an hour.
    # 2. Staleness hold (flag champion_tracker_enabled). Role data older than
    #    the limit holds the message (FAILED + prefix: kept, visible,
    #    releasable) until the re-verify sweep confirms the role. No provider
    #    call here; nothing rendered; the enrollment lives.
    from app.services import champion_tracker  # noqa: PLC0415

    try:
        departed = champion_tracker.departure_block(session, lead, strategy)
        # 1b. A lead created FROM a move (a champion's new company) inherits
        #     the person's opt-outs recorded on any EARLIER record, now --
        #     suppression is keyed by address and would not follow the person.
        opted_out = (None if departed is not None else champion_tracker.derived_opt_out(
            session, lead, _owner_or_raise(session, strategy)))
    except Exception:
        logger.exception("departure check failed for message %s -- deferred", message_id)
        session.rollback()
        message = session.get(Message, message_id)
        message.scheduled_at = now + timedelta(hours=1)
        message.error = "departure check could not be run -- deferred (fails closed)"
        session.commit()
        return champion_tracker.SEND_RESULT_DEFERRED
    if departed is not None:
        champion_tracker.cancel_for_departure(message, departed)
        _audit(session, strategy, lead, message, champion_tracker.AUDIT_LEFT,
               extra={"move_id": str(departed.id)})
        session.commit()
        if enrollment.status is not EnrollmentStatus.STOPPED:
            engine.stop_enrollment(session, enrollment, reason=champion_tracker.STOP_REASON)
        return champion_tracker.SEND_RESULT_LEFT
    if opted_out is not None:
        message.status = MessageStatus.CANCELLED
        message.error = f"suppressed at send time: {opted_out}"[:1000]
        _audit(session, strategy, lead, message, "blocked_suppressed",
               extra={"earlier_record": opted_out})
        session.commit()
        if enrollment.status is not EnrollmentStatus.STOPPED:
            engine.stop_enrollment(session, enrollment, reason="suppressed")
        return "cancelled_suppressed"
    if champion_tracker.enabled(session):
        stale = champion_tracker.staleness_hold(session, lead, now)
        if stale is not None:
            champion_tracker.hold_message(message, stale)
            _audit(session, strategy, lead, message, champion_tracker.AUDIT_HELD,
                   extra={"reason": stale})
            session.commit()
            return champion_tracker.SEND_RESULT_HELD
    # ---- Plan entitlement, re-checked NOW (app/services/plan_holds.py) -----
    # Launch checks the plan; a downgrade after launch must not keep sending
    # what the account no longer pays for. A channel the owner's CURRENT plan
    # lacks, or a sequence step past its max_sequence_steps, is HELD (FAILED
    # + 'plan_limit: ', kept and visible, enrollment not stopped; released by
    # the plan-hold sweep once the plan covers it again). Grandfathered:
    # enrollments created before plan_holds.PLAN_HOLD_EFFECTIVE_AT, admin
    # owners, and person-approved Reply Copilot answers. Placed
    # AFTER every gate that cancels (suppression, exclusions, departures,
    # derived opt-outs): a message that must never go out is cancelled, not
    # merely held. An owner that cannot be resolved fails CLOSED: deferred an
    # hour, never sent unchecked.
    from app.services import plan_holds  # noqa: PLC0415

    try:
        plan_owner = session.get(User, _owner_or_raise(session, strategy))
        if plan_owner is None:
            raise LookupError(f"owner row missing for strategy {strategy.id}")
        over_plan = plan_holds.check(session, plan_owner, message, enrollment)
    except Exception:
        logger.exception("plan check failed for message %s -- deferred", message_id)
        session.rollback()
        message = session.get(Message, message_id)
        message.scheduled_at = now + timedelta(hours=1)
        message.error = "plan entitlement could not be checked -- deferred (fails closed)"
        session.commit()
        return "deferred_plan_check_unavailable"
    if over_plan is not None:
        plan_holds.hold_message(message, over_plan)
        _audit(session, strategy, lead, message, plan_holds.AUDIT_HELD,
               extra={"plan": str(getattr(plan_owner.plan, "value", plan_owner.plan)),
                      "reason": over_plan})
        session.commit()
        return plan_holds.SEND_RESULT

    # ---- F01 Bounce Shield (SAFETY GUARD, email only, flag default off) ----
    # An email goes only to an address verified valid (fresh), catch-all under
    # an 'allow' policy, or one a manager overrode for this lead. Blocked =
    # FAILED with the shield prefix (kept, releasable by override/re-verify),
    # audited; the enrollment is NOT stopped. Flag off: not even read.
    email_verdict_tag = None
    if not (is_whatsapp or is_linkedin or is_phone):
        from app.services import bounce_shield  # noqa: PLC0415

        if bounce_shield.enabled(session):
            shield = bounce_shield.decide(session, lead, now)
            if not shield.allowed:
                bounce_shield.block_message(session, message, shield)
                _audit(session, strategy, lead, message, bounce_shield.AUDIT_BLOCKED)
                session.commit()
                return bounce_shield.SEND_RESULT
            email_verdict_tag = shield.tag

    # ---- Feature 5: opt-in re-engagement's own switch and cap -------------
    # IN ADDITION to every gate here, never instead of one. A campaign whose
    # re-engagement was switched off since scheduling cancels; an exhausted
    # re-engagement cap defers to tomorrow. The channel daily cap below still
    # applies on top.
    if message.origin == reengagement.ORIGIN:
        held = reengagement.send_time_hold(session, strategy, lead, message, now)
        if held is not None:
            return held

    # ---- Feature A5: live conversion probability / kill signals -----------
    # A lead that has gone cold is not sent to: the gate pauses (cooling) or
    # stops (archived) its sequence and the send ends here. Probability alone
    # only judges after conversion_min_unanswered_sends unanswered sends.
    # Not for a "not now" re-engagement touch (Part 1 Feature 7): the gate's
    # kill signal for that lead IS the "not now" reply the plan answers (its
    # routing class is typically not_interested, a hard kill here), so the
    # gate would archive every such touch on the day it is due. A reply AFTER
    # that one already cancelled the touch (reengagement_memory.send_time_hold),
    # bounces / unsubscribes are enforced by suppression above and by
    # still_appropriate (statuses, lead.kill_signal), and an admin switched
    # the auto-send on deliberately.
    from app.services import conversion_probability  # noqa: PLC0415

    held = (None if is_notnow_touch
            else conversion_probability.send_gate(session, lead, enrollment, now=now))
    if held is not None:
        return held

    # ---- WhatsApp engine rules (defense in depth — the adapter's own
    # guard re-checks all of this; the ENGINE checks it too so a lead
    # without opt-in is SKIPPED and the sequence continues, rather than
    # the whole sequence dying on a ComplianceError) --------------------------
    if is_whatsapp:
        if optin_svc.current_status(session, lead.id) is not OptInStatus.OPTED_IN:
            _audit(session, strategy, lead, message, "skipped_no_consent")
            engine.skip_message(session, message, reason="skipped_no_optin", now=now)
            return "skipped_no_optin"

        if message.whatsapp_kind is WhatsAppStepKind.TEXT:
            # Free-form: re-check the 24h window at ACTUAL send time. A
            # message queued while the window was open but expired by now
            # fails CLOSED into needs_template — never silently sent.
            state = window_state_for_lead(lead, now)
            if not state.open:
                message.status = MessageStatus.NEEDS_TEMPLATE
                message.error = (
                    "24h customer-service window closed before send — "
                    "convert this step to an approved template or wait "
                    "for the lead to message again"
                )
                session.commit()
                return "needs_template_window_closed"
        else:
            # Template step: the referenced template must STILL be
            # approved at send time (it may have been rejected or
            # superseded since scheduling).
            tmpl = (session.get(WhatsAppTemplate, message.whatsapp_template_id)
                    if message.whatsapp_template_id else None)
            if tmpl is None or tmpl.status is not WhatsAppTemplateStatus.APPROVED:
                message.status = MessageStatus.NEEDS_TEMPLATE
                message.error = (
                    "referenced template is missing or no longer approved "
                    "— re-submit or pick an approved template"
                )
                session.commit()
                return "needs_template_not_approved"
            # LEGAL GUARD (product defect #28): a marketing template approved
            # before the opt-out rule (or edited outside the app) must still
            # tell the recipient how to opt out. Fails CLOSED, never sent.
            from app.services import whatsapp_templates as tmpl_rules  # noqa: PLC0415

            try:
                tmpl_rules.validate_opt_out(tmpl.body, tmpl.category)
            except tmpl_rules.TemplateError:
                message.status = MessageStatus.NEEDS_TEMPLATE
                message.error = (
                    f"approved marketing template '{tmpl.name}' has no opt-out "
                    f"instruction (e.g. \"{tmpl_rules.STANDARD_OPT_OUT}\") -- add "
                    "it, re-submit the template to Meta and use the new version"
                )
                _audit(session, strategy, lead, message, "blocked_no_opt_out")
                session.commit()
                return "needs_template_no_opt_out"

    # ---- Feature 8: the resolved compliance rule for THIS lead + channel ----
    # Workspace / region / channel overrides of the baseline, resolved now.
    # Fails closed -- see app/services/compliance_rules.py.
    rules = compliance_rules.resolve(session, lead, message.channel.value, strategy)
    if rules.consent_required and not compliance_rules.has_consent(
            session, lead, message.channel):
        # Skipped, not failed: the sequence continues on its other steps, the
        # same way a WhatsApp step without opt-in is skipped.
        engine.skip_message(session, message, reason="skipped_consent_required", now=now)
        return "skipped_consent_required"

    # ---- Email network: CASL two-year rule, re-checked NOW (review M3) ------
    # A lead added from the user's own email contacts with a Canadian (.ca)
    # address may only be emailed while the last TWO-WAY exchange is under
    # two years old (CASL s.10(10) implied consent -- needs legal
    # confirmation). Every other lead: one attribute read, nothing else.
    # Skipped like a missing consent; a lookup error defers an hour.
    if lead.source == "email_network" and not (is_whatsapp or is_linkedin or is_phone):
        from app.services import email_network_messages  # noqa: PLC0415

        try:
            casl = email_network_messages.casl_send_block(
                session, lead, notifications_owner(session, strategy), now=now)
        except Exception:
            logger.exception("CASL check failed for message %s -- deferred", message_id)
            session.rollback()
            message = session.get(Message, message_id)
            message.scheduled_at = now + timedelta(hours=1)
            message.error = "CASL check could not be run -- deferred (fails closed)"
            session.commit()
            return "deferred_casl_check_unavailable"
        if casl is not None:
            _audit(session, strategy, lead, message, "skipped_casl_window",
                   extra={"reason": casl})
            engine.skip_message(session, message, reason="skipped_casl_window", now=now)
            return "skipped_casl_window"

    # ---- send window --------------------------------------------------------
    tz = engine.lead_timezone(lead)
    if rules.window_empty:
        # Conflicting (or invalid) rules leave no permitted hour. Closed:
        # nothing transmits, and the message is looked at again tomorrow.
        message.scheduled_at = now + timedelta(days=1)
        message.error = "no permitted send window under the current compliance rules"
        session.commit()
        return "deferred_no_send_window"
    if not engine.in_send_window(now, tz, rules.window):
        message.scheduled_at = engine.next_window_slot(now, tz, rules.window)
        # Best time to send (docs/features/autopilot-launch.md): a message
        # deferred to the next window (e.g. after a daily-approval hold to
        # local midnight) goes to that day's best slot inside the SAME
        # resolved window, not to its first minute. Only ever later; flag off
        # = unchanged. Placement only -- nothing here sends.
        from app.services import send_time  # noqa: PLC0415

        message.scheduled_at = send_time.place(
            session, strategy, lead, message.channel, message.scheduled_at,
            window=rules.window, spread_key=lead.id, now=now)
        session.commit()
        return "deferred_window"
    # The person's OWN sending window (PUT /best-time) is hard at send time
    # too: inside the permitted window but outside theirs -> placed at the
    # next slot inside both, never sent. No override = no SQL, unchanged.
    from app.services import send_time  # noqa: PLC0415

    override_slot = send_time.override_hold(session, strategy, message, lead, now,
                                            window=rules.window)
    if override_slot is not None:
        message.scheduled_at = override_slot
        session.commit()
        return "deferred_override_window"

    # ---- Daily send approval (SAFETY GUARD, every channel, flag default ON) --
    # Founder decision 2026-10-11: an AUTOMATIC send goes out only on a day the
    # account owner (or a workspace owner/manager) approved automatic sending
    # for this channel; LinkedIn needs an explicit acknowledgement of
    # LinkedIn's restriction risk. Placed after every cancel/skip gate and the
    # send window (so "due" means it could go out now) and before caps, the
    # LinkedIn slot, the claim and the render (so a held message spends
    # nothing). Held = still SCHEDULED, moved to the next local midnight,
    # error prefixed -- never failed, never dropped; approval releases it
    # through every gate again. Human-approved messages (send review, Reply
    # Copilot approvals) are not gated. Lookup error fails CLOSED: deferred an
    # hour. Flag off: one settings read. See docs/features/daily-send-approval.md.
    from app.services import daily_send_approval  # noqa: PLC0415

    try:
        approval_hold = daily_send_approval.send_hold(session, message, strategy, now)
    except Exception:
        logger.exception("daily send approval check failed for message %s -- deferred",
                         message_id)
        session.rollback()
        message = session.get(Message, message_id)
        message.scheduled_at = now + daily_send_approval.UNAVAILABLE_RETRY
        message.error = (f"{daily_send_approval.ERROR_PREFIX}approval could not be "
                         "checked -- deferred (fails closed)")
        session.commit()
        return daily_send_approval.SEND_RESULT_UNAVAILABLE
    if approval_hold is not None:
        before = (message.scheduled_at, message.error)
        message.scheduled_at = approval_hold.until
        message.error = approval_hold.error
        session.flush()
        # Race guard: an approval may have committed (and run its release)
        # after the hold was computed but before this tag is committed. Re-read
        # today's decision under its row lock (approve/decline take the same
        # lock); if this channel is approved now, do not park the message.
        try:
            still_held = daily_send_approval.confirm_hold(
                session, approval_hold, message.channel.value, now)
        except Exception:
            logger.exception("daily send approval re-check failed for message %s -- held",
                             message_id)
            session.rollback()
            message = session.get(Message, message_id)
            message.scheduled_at = approval_hold.until
            message.error = approval_hold.error
            still_held = True
        if still_held:
            session.commit()
            daily_send_approval.notify_if_needed(session, approval_hold, now)
            return approval_hold.result
        message.scheduled_at, message.error = before
        session.commit()      # releases the row lock before the slow part
    if (message.error or "").startswith(daily_send_approval.ERROR_PREFIX):
        message.error = None   # an earlier hold's reason no longer applies

    # ---- Autopilot's promise (docs/features/autopilot-launch.md 3.1) --------
    # The person who turned Autopilot on confirmed "nothing it schedules is
    # sent on a day I have not approved". If daily approval is switched off,
    # or no longer covers this channel, a message of an Autopilot sequence
    # waits (deferred an hour, never failed, never dropped). Normal config:
    # two settings reads, nothing else. Lookup error fails CLOSED like the
    # gate above.
    from app.services import autopilot as autopilot_svc  # noqa: PLC0415

    try:
        ap_hold = autopilot_svc.send_hold(session, message, strategy, now)
    except Exception:
        logger.exception("autopilot hold check failed for message %s -- deferred", message_id)
        session.rollback()
        message = session.get(Message, message_id)
        ap_hold = (now + autopilot_svc.HOLD_RETRY,
                   f"{autopilot_svc.HOLD_PREFIX}check could not run -- deferred (fails closed)")
    if ap_hold is not None:
        message.scheduled_at, message.error = ap_hold
        session.commit()
        return "deferred_autopilot_needs_daily_approval"
    if (message.error or "").startswith(autopilot_svc.HOLD_PREFIX):
        message.error = None

    # ---- daily cap + warm-up (per channel; deferred, never dropped) ---------
    li_account = li_action = None
    autopilot = False          # F03, set in the email branch below
    if is_linkedin:
        # Profile lookup, connect/message/InMail decision, account rotation
        # and the per-account daily ceiling -- see _prepare_linkedin.
        account = None
        prepared = _prepare_linkedin(session, strategy, lead, message, now, tz)
        if isinstance(prepared, str):
            return prepared
        li_account, li_action = prepared
    elif is_phone:
        # Admin switch, consent, dialable number, provider, daily call cap --
        # see _prepare_phone. The send window above already bounds calling
        # hours to the lead's local business day.
        account = None
        stopped = _prepare_phone(session, strategy, lead, message, now, tz)
        if stopped is not None:
            return stopped
    elif is_whatsapp:
        account = None
        if engine.whatsapp_allowance_left(session, now, rules.daily_cap) <= 0:
            tomorrow = now.astimezone(timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0
            ) + timedelta(days=1)
            message.scheduled_at = engine.next_window_slot(tomorrow, tz, rules.window)
            session.commit()
            return "deferred_cap"
    else:
        account = _account_for_strategy(session, strategy)
        # Part 1 Feature 4: per-mailbox deliverability health. A PAUSED mailbox
        # defers the message to tomorrow's window -- deferred, never dropped,
        # the same rule the daily cap has always followed -- and a THROTTLED
        # one lowers the ceiling for today. Neither can ever RAISE the cap: the
        # throttle is combined with the compliance rule's cap by taking the
        # smaller of the two.
        from app.services import mailbox_health  # noqa: PLC0415

        # Part 1 Feature 11: a client's prospects must never see another
        # client's sending domain. Checked HERE, in the send path, not only
        # rendered in a settings page -- an isolation rule that lives in a
        # form is an isolation rule that leaks. An empty pool means no
        # restriction, so nothing changes for an account without clients.
        from app.services import client_workspaces  # noqa: PLC0415

        wrong_pool = client_workspaces.domain_allowed(session, strategy,
                                                      account.email_address)
        if wrong_pool is not None:
            message.status = MessageStatus.FAILED
            message.error = f"sending domain not in the client's pool: {wrong_pool}"
            session.commit()
            return "blocked_client_domain"

        health = mailbox_health.gate(session, account.user_id, str(account.id))
        effective_cap = rules.daily_cap
        if health["cap"] is not None:
            effective_cap = (health["cap"] if effective_cap is None
                             else min(effective_cap, health["cap"]))
        # F03: with the Deliverability Autopilot on, the warm-up ramp also
        # applies the stepped new-mailbox schedule (sequence_engine.RAMP_SCHEDULE)
        # counted from this mailbox's first send. min() only: never a raise.
        from app.services import deliverability_autopilot  # noqa: PLC0415

        autopilot = deliverability_autopilot.enabled(session)
        if (health["state"] == mailbox_health.PAUSED
                or engine.allowance_left(session, account, now, effective_cap,
                                         autopilot=autopilot) <= 0):
            tomorrow = now.astimezone(timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0
            ) + timedelta(days=1)
            message.scheduled_at = engine.next_window_slot(tomorrow, tz, rules.window)
            if health["state"] == mailbox_health.PAUSED:
                # The reason travels with the message, so the lead timeline
                # can say WHY this step has not gone out.
                message.error = f"mailbox paused: {health['reason'] or 'deliverability health'}"
                session.commit()
                return "deferred_mailbox_paused"
            session.commit()
            return "deferred_cap"

    # ---- claim (scheduled -> sending), render, persist BEFORE transmit ----
    # The claim is a CONDITIONAL UPDATE, never an ORM attribute write: the
    # status guards at the top of this function are reads, not locks, so two
    # workers can both hold this message as SCHEDULED in memory and both
    # reach this line. The WHERE clause makes the database arbitrate — only
    # the worker whose UPDATE actually moved the row out of SCHEDULED may
    # transmit; the other matches 0 rows and walks away. Same at-most-once
    # pattern as meeting_prep_tasks._claim.
    claimed = session.execute(
        update(Message)
        .where(Message.id == message.id,
               Message.status == MessageStatus.SCHEDULED)
        .values(status=MessageStatus.SENDING)
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        session.rollback()
        # Give back what THIS worker reserved in its prepare step. The
        # message row itself belongs to the winner now — nothing on it may
        # be touched here (which is why this is not _phone_send_failed).
        if li_account is not None:
            from app.services import linkedin_limits  # noqa: PLC0415

            linkedin_limits.release(li_account, li_action, now=now)
        if is_phone:
            from app.services import notifications, phone_calls  # noqa: PLC0415

            phone_calls.release_call_slot(
                notifications.owner_of_strategy(session, strategy), now=now)
        logger.info("message %s already claimed by another worker", message_id)
        return "lost_claim_race"
    # Keep the in-memory row honest (expire_on_commit=False + the Core
    # UPDATE above bypassing the identity map would otherwise leave it
    # saying SCHEDULED for the rest of this function).
    message.status = MessageStatus.SENDING
    if is_linkedin:
        message.sender_ref = f"linkedin:{li_account.id}"
        message.linkedin_account_id = li_account.id
        message.linkedin_action = li_action
    elif is_phone:
        message.sender_ref = "phone:ai_voice"
    else:
        message.sender_ref = (
            f"whatsapp:{settings.whatsapp_phone_number_id}" if is_whatsapp
            else str(account.id)
        )
    session.commit()

    try:
        if is_linkedin:
            outbound = _render_linkedin_outbound(session, strategy, lead, message,
                                                 li_account, li_action)
        elif is_phone:
            outbound = _render_phone_outbound(session, strategy, lead, message)
        elif is_whatsapp:
            outbound = _render_whatsapp_outbound(session, strategy, lead,
                                                 message, now)
        else:
            if is_reply_copilot:
                # F04: the approved words, never a re-render -- with the same
                # claim check and compliant footer + headers as every email.
                outbound = _render_reply_copilot_outbound(session, strategy, lead, message)
            elif local_business.is_customer(lead):
                # Local business mode, Part B: the owner's own win-back copy.
                outbound = _render_customer_email_outbound(session, strategy, lead, message)
            else:
                # Email network (docs/features/email-network.md): a lead's
                # step-1 email may carry the first email its owner wrote and
                # SAVED for this person. Those words are rendered here -- with
                # the claim check and build_compliant_email -- and then pass
                # every gate below (review queue, forbidden phrases, the
                # erasure re-claim) exactly like a written step. Every gate
                # above already ran. None = the step is written as usual.
                from app.services import email_network_messages  # noqa: PLC0415

                opener = email_network_messages.saved_opener(session, message, lead,
                                                             strategy)
                if opener is not None:
                    outbound = _render_saved_opener_outbound(session, strategy, lead,
                                                             message, opener)
                    if outcome_source is None:
                        outcome_source = email_network_messages.OPENER_SOURCE
                else:
                    outbound = _render_email_outbound(session, strategy, lead,
                                                      sequence, message, now=now)
            # F03: an email without both RFC 8058 one-click unsubscribe
            # headers never transmits (ComplianceError -> FAILED below).
            if autopilot:
                deliverability_autopilot.require_one_click_headers(outbound.headers)
        session.commit()  # rendered content persisted before the send

        # ---- Part 1 Feature 5: the human review queue ---------------------
        # AFTER rendering, because three of the four triggers are facts about
        # the prospect right now and the fourth is about the copy that was
        # actually written. A held message is QUEUED, not lost: it sits in
        # AWAITING_REVIEW until a person approves it, and approving returns it
        # to SCHEDULED. Phone is excluded -- a live call is not a message a
        # reviewer can read and approve after the fact.
        if not is_phone:
            from app.services import send_review  # noqa: PLC0415

            # Email network: a lead whose attached saved first email was
            # deleted gets this step-1 email HELD for a person instead of
            # AI words sent silently in its place (review round LOW).
            if lead.source == "email_network" and not (is_whatsapp or is_linkedin):
                from app.services import email_network_messages  # noqa: PLC0415

                if email_network_messages.hold_if_opener_deleted(
                        session, lead, message, outbound.subject, outbound.body,
                        notifications_owner(session, strategy)):
                    message.status = MessageStatus.AWAITING_REVIEW
                    message.error = None
                    session.commit()
                    return "held_for_review"
            decision = send_review.gate(session, lead, message,
                                        outbound.subject, outbound.body, now=now)
            if decision["action"] == "hold":
                message.status = MessageStatus.AWAITING_REVIEW
                message.error = None
                session.commit()
                return "held_for_review"
            if decision["action"] == "cancel":
                message.status = MessageStatus.CANCELLED
                message.error = f"{send_review.REJECTED_ERROR_PREFIX}{decision['reason']}"
                session.commit()
                # Same rule as send_review.reject(): a rejected sequence step
                # advances the sequence (not counted as sent), so it is never
                # left stalled for the follow-up sweep to re-create.
                if message.origin is None:
                    send_review.advance_after_reject(session, message, now)
                return "rejected_in_review"
            # A reviewer may have EDITED the copy. Their words are what sends,
            # and they are persisted so the sent row matches what went out.
            if (decision["subject"], decision["body"]) != (outbound.subject, outbound.body):
                outbound.subject = decision["subject"]
                outbound.body = decision["body"]
                message.subject = decision["subject"]
                message.body = decision["body"]
                session.commit()

        # ---- F05: forbidden phrases, over the words that will ACTUALLY go
        # out -- after any reviewer edit, so an approved edit cannot bring a
        # banned phrase back. Raised, so the handler below releases whatever
        # the prepare step reserved (LinkedIn slot, call slot).
        from app.services import guardrails  # noqa: PLC0415

        banned = guardrails.send_copy_block(session, strategy, outbound.subject, outbound.body)
        if banned:
            raise guardrails.ForbiddenPhraseBlocked(banned)
        # Email network (review M5): the words that will actually go out to a
        # person from the user's own contacts -- written, saved or edited by
        # a reviewer -- may not claim a meeting, a call or a conversation the
        # email history does not show. Same handling as a forbidden phrase.
        if lead.source == "email_network" and not (is_whatsapp or is_linkedin or is_phone):
            from app.services import email_network_messages  # noqa: PLC0415

            overclaim = email_network_messages.send_time_wording_problems(
                session, lead, notifications_owner(session, strategy),
                outbound.subject, outbound.body, now=now)
            if overclaim:
                raise guardrails.ForbiddenPhraseBlocked(
                    ["relationship claim not supported by your email history"])

        if channel is None:
            if is_linkedin:
                channel = _get_linkedin_channel(session, strategy)
            elif is_phone:
                channel = _get_phone_channel(session, strategy)
            else:
                channel = (_get_whatsapp_channel(session) if is_whatsapp
                           else _get_channel(session, account))
        # GDPR (docs/features/gdpr-erasure.md): a lead or account erasure that
        # ran after the claim cancels (or deletes) this row. Re-assert the
        # claim the same way it was taken -- a conditional UPDATE -- and fail
        # closed if it no longer holds.
        from app.services import account_erasure  # noqa: PLC0415

        if not account_erasure.confirm_send_claim(session, message.id):
            raise account_erasure.SendClaimLost(str(message.id))
        result = channel.send(outbound)
    except Exception as exc:
        if li_account is not None:
            # A request LinkedIn never received is not usage.
            from app.services import linkedin_limits  # noqa: PLC0415

            linkedin_limits.release(li_account, li_action, now=now)
        from app.services import account_erasure  # noqa: PLC0415

        if isinstance(exc, account_erasure.SendClaimLost):
            # Nothing was transmitted. The row is no longer ours: never write
            # status or content to it. If the prospect was erased while this
            # task rendered, scrub again what the render re-persisted.
            lead_id = message.lead_id      # read before the rollback expires it
            session.rollback()
            if is_phone:
                from app.services import notifications, phone_calls  # noqa: PLC0415

                try:
                    phone_calls.release_call_slot(
                        notifications.owner_of_strategy(session, strategy), now=now)
                except Exception:  # noqa: BLE001 -- the account may be gone
                    logger.warning("call slot not released for message %s", message_id)
            if account_erasure.lead_is_erased(session, lead_id):
                account_erasure.erase_lead_correspondence(session, lead_id)
                session.commit()
                logger.info("message %s not sent: the lead was erased mid-send", message_id)
                return "cancelled_lead_erased"
            logger.info("message %s not sent: claim lost before transmit", message_id)
            return "lost_claim_race"
        if is_phone:
            # The call was never placed: mark it failed and give the daily
            # call slot back.
            _phone_send_failed(session, strategy, message,
                               f"{type(exc).__name__}: {exc}", now)
        # Import from the canonical module, not from the adapter. whatsapp.py
        # used to declare its own ComplianceError and this caught THAT class;
        # now there is only one, which also means this branch would catch a
        # compliance block raised on the EMAIL render path, so the resulting
        # status has to depend on the channel: NEEDS_TEMPLATE is a WhatsApp
        # state and would be nonsense on an email row.
        from app.services.guardrails import ForbiddenPhraseBlocked  # noqa: PLC0415

        if isinstance(exc, ForbiddenPhraseBlocked):
            # Never retried: the same words would be refused again. Surfaced
            # on the message for the owner to fix the copy or the library.
            message.status = MessageStatus.FAILED
            message.error = f"forbidden phrase: {', '.join(exc.phrases)}"[:2000]
            _audit(session, strategy, lead, message, "blocked_forbidden_phrase",
                   extra={"forbidden_phrases": exc.phrases[:20]})
            session.commit()
            return "blocked_forbidden_phrase"
        from app.core.exceptions import ComplianceError
        if isinstance(exc, ComplianceError):
            # A compliance guard fired (suppression/opt-in/window/template).
            # Never retried — surfaced for attention.
            message.status = (MessageStatus.NEEDS_TEMPLATE if is_whatsapp
                              else MessageStatus.FAILED)
            message.error = f"ComplianceError: {exc}"
            session.commit()
            return "blocked_compliance"
        message.status = MessageStatus.SCHEDULED  # transient: retry later
        message.error = f"{type(exc).__name__}: {exc}"
        session.commit()
        raise

    if not result.ok:
        if li_account is not None:
            from app.services import linkedin_limits  # noqa: PLC0415

            linkedin_limits.release(li_account, li_action, now=now)
        if is_phone:
            _phone_send_failed(session, strategy, message, result.error or "call failed", now)
        if result.permanent_failure:
            message.status = MessageStatus.FAILED
            message.error = result.error
            session.commit()
            return "failed_permanent"
        message.status = MessageStatus.SCHEDULED
        message.error = result.error
        session.commit()
        return "failed_transient"

    message.status = MessageStatus.SENT
    message.sent_at = now
    message.provider_message_id = result.provider_message_id
    message.thread_ref = result.thread_ref
    message.rfc_message_id = result.rfc_message_id
    message.rfc_references = result.rfc_references
    if li_account is not None:
        from app.services import linkedin_outreach  # noqa: PLC0415

        linkedin_outreach.after_send(session, lead, li_account, li_action, result, now)
    if is_phone:
        # "Sent" = the call was placed. What happened on it arrives on the
        # provider's webhook (app/api/calls.py).
        from app.services import phone_calls  # noqa: PLC0415

        call = phone_calls.call_for_message(session, message)
        if call is not None:
            call.provider_call_id = result.provider_message_id
            call.provider = (result.raw or {}).get("provider") or call.provider
            call.status = "ringing"
            # Voice twin: the live-control link for "Take over" (never shown).
            call.control_url = ((result.raw or {}).get("control_url") or None)
    sent_meta = {"step_no": message.step_no, "variant": message.variant}
    if outcome_source:
        sent_meta["source"] = outcome_source
    if email_verdict_tag:
        # F01: what the shield let this send through on -- the success metric
        # (bounces on verified sends) is measured on this tag. Absent when the
        # shield is off, so the meta stays byte-identical to before.
        sent_meta["email_verdict"] = email_verdict_tag
    session.add(Outcome(lead_id=lead.id, message_id=message.id,
                        event=OutcomeEvent.SENT,
                        channel=message.channel.value,
                        meta_json=sent_meta))
    if lead.status in (LeadStatus.VERIFIED, LeadStatus.FLAGGED):
        lead.status = LeadStatus.CONTACTED
    # Feature Group 9: the region, regime and checks behind this send, in the
    # same commit as the send itself.
    _audit(session, strategy, lead, message, "sent")
    session.commit()

    # F04: an answer to a reply is not a sequence step -- the stopped
    # sequence is neither advanced nor completed by it. Nor is a "not now"
    # re-engagement touch (Part 1 Feature 7).
    if not (is_reply_copilot or is_notnow_touch):
        engine.schedule_next_step(session, message, now=now)
    return "sent"


def notifications_owner(session: Session, strategy: Strategy):
    """The account owning `strategy`, or None."""
    from app.services import notifications  # noqa: PLC0415

    return notifications.owner_of_strategy(session, strategy)


def _owner_or_raise(session: Session, strategy: Strategy):
    """The account owning `strategy`. Raises when it cannot be resolved, so a
    guard that needs it fails closed (the caller defers the send)."""
    from app.services import champion_tracker, notifications  # noqa: PLC0415

    owner = notifications.owner_of_strategy(session, strategy)
    if owner is None:
        raise champion_tracker.OwnerUnresolved(f"no owner for strategy {strategy.id}")
    return owner


def _audit(session: Session, strategy: Strategy, lead: Lead, message: Message,
           decision: str, extra: dict | None = None) -> None:
    """Add a compliance_audit_log row (committed by the caller). Never raises:
    an audit failure must not turn into a send that silently did not happen."""
    try:
        from app.services import compliance_audit, notifications  # noqa: PLC0415

        compliance_audit.record(session, lead=lead, message=message,
                                channel=message.channel.value, decision=decision,
                                user_id=notifications.owner_of_strategy(session, strategy),
                                extra=extra)
    except Exception:
        logger.exception("compliance audit for message %s failed", message.id)


def _render_email_outbound(session: Session, strategy: Strategy, lead: Lead,
                           sequence: Sequence, message: Message,
                           now: datetime | None = None) -> OutboundMessage:
    # Tag the scheduling link with the owning tenant so the Calendly webhook
    # can resolve the booking back to THIS account instead of guessing by
    # email across every tenant. This is the only place a booking link reaches
    # a lead (the WhatsApp text path passes booking_url=None, and templates
    # carry no scheduling link), so tagging here covers all outbound links.
    from app.integrations.calendly import tag_booking_url  # noqa: PLC0415
    from app.services import notifications  # noqa: PLC0415

    from app.services import personalization_context  # noqa: PLC0415

    owner_id = notifications.owner_of_strategy(session, strategy)
    # Feature Group 2: refresh the lead's recent posts / company news if stale,
    # right before writing. Never raises -- a failed fetch means an email
    # without that hook, never a send that did not happen.
    personalization_context.ensure_fresh(session, lead, owner_id=owner_id)
    # F02 Signal Radar (flag, default off): re-derive the lead's dated signals
    # from what ensure_fresh just stored -- no fetch of its own -- and pick the
    # one citable signal to offer. Never raises; an empty pick changes nothing.
    from app.services import signal_radar  # noqa: PLC0415

    pick = signal_radar.prepare(session, lead, owner_id=owner_id, strategy=strategy, now=now)
    booking_url = tag_booking_url(sequence.booking_url, owner_id)
    used: dict = {}
    render_kwargs = {"why_now": pick.block} if pick.block else {}
    subject, body = render_message(session, strategy, lead,
                                   _step_of(session, message), booking_url,
                                   inputs_out=used, **render_kwargs)
    # Feature A1: every claim about the prospect must be backed by stored data.
    # BEFORE the compliance footer is appended, so the footer is never parsed
    # for claims and never altered.
    from app.services import claim_verification  # noqa: PLC0415

    decisions: list = []
    checked = claim_verification.enforce(session, strategy=strategy, lead=lead, message=message,
                                         channel="email",
                                         fields={"subject": subject, "body": body},
                                         decisions_out=decisions)
    subject, body = checked["subject"], checked["body"]
    # F02: a signal counts as cited ONLY through a verified claim on its own
    # stored source. None when nothing was offered (flag off included).
    citation = signal_radar.resolve_citation(pick, decisions)
    if citation is not None:
        used["signal_radar"] = citation
    subject, body, headers, token = engine.build_compliant_email(lead, subject, body)
    message.subject = subject
    message.body = body
    message.unsubscribe_token = token
    message.personalization_json = used or None
    # Feature Group 3: an HTML twin of the same text carrying the open pixel,
    # unless tracking is off or the lead is in the EU/EEA/UK.
    from app.services import open_tracking  # noqa: PLC0415

    metadata = {}
    if open_tracking.tracking_enabled(session, lead):
        # Feature A2: links in the HTML twin go through the click redirect when
        # click tracking is on (off by default -- link rewriting is a spam signal).
        click = ((lambda url: open_tracking.click_url(message.id, url))
                 if open_tracking.click_tracking_enabled(session, lead) else None)
        metadata["html_body"] = open_tracking.html_body(
            body, open_tracking.pixel_url(message.id), click_url=click)
    return OutboundMessage(
        message_id=str(message.id),
        lead_id=str(lead.id),
        to_address=lead.email,
        subject=subject,
        body=body,
        headers=headers,
        thread_ref=_thread_ref_for_followup(session, message, headers),
        metadata=metadata,
    )


def _render_reply_copilot_outbound(session: Session, strategy: Strategy, lead: Lead,
                                   message: Message) -> OutboundMessage:
    """F04: the words a person approved (reply_copilot.approved_copy -- from
    the draft, so a retry never footers twice), re-checked by the claim engine
    and wrapped by build_compliant_email (unsubscribe footer, region notice,
    List-Unsubscribe + List-Unsubscribe-Post), in the prospect's reply
    thread. No model call, no open/click tracking."""
    from app.services import claim_verification, reply_copilot  # noqa: PLC0415

    subject, body, thread_ref = reply_copilot.approved_copy(session, message)
    checked = claim_verification.enforce(session, strategy=strategy, lead=lead,
                                         message=message, channel="email",
                                         fields={"subject": subject, "body": body})
    subject, body = checked["subject"], checked["body"]
    subject, body, headers, token = engine.build_compliant_email(lead, subject, body)
    # gaps-sweep-2 #6: In-Reply-To / References from the prospect's reply, so
    # the answer threads in their client too (Gmail's threadId only threads it
    # in the sender's mailbox). The compliance headers above are kept as built.
    headers = {**headers, **reply_copilot.thread_headers(session, message)}
    message.subject = subject
    message.body = body
    message.unsubscribe_token = token
    return OutboundMessage(
        message_id=str(message.id),
        lead_id=str(lead.id),
        to_address=lead.email,
        subject=subject,
        body=body,
        headers=headers,
        thread_ref=thread_ref,
    )


def _render_saved_opener_outbound(session: Session, strategy: Strategy, lead: Lead,
                                  message: Message, opener: tuple[str, str]) -> OutboundMessage:
    """Email network: the first email the account owner wrote and SAVED for
    this person (email_network_messages.saved_opener). No model call. Like
    the Reply Copilot render: the claim engine checks the words, and
    build_compliant_email adds the unsubscribe link, the sender's postal
    address, the region notice and the one-click List-Unsubscribe headers. No
    open/click tracking on a personal first email."""
    from app.services import claim_verification  # noqa: PLC0415

    subject, body = opener
    checked = claim_verification.enforce(session, strategy=strategy, lead=lead,
                                         message=message, channel="email",
                                         fields={"subject": subject, "body": body})
    subject, body = checked["subject"], checked["body"]
    subject, body, headers, token = engine.build_compliant_email(lead, subject, body)
    message.subject = subject
    message.body = body
    message.unsubscribe_token = token
    message.personalization_json = {"email_network_saved_opener": True}
    return OutboundMessage(
        message_id=str(message.id),
        lead_id=str(lead.id),
        to_address=lead.email,
        subject=subject,
        body=body,
        headers=headers,
        thread_ref=None,
    )


def _render_customer_email_outbound(session: Session, strategy: Strategy, lead: Lead,
                                    message: Message) -> OutboundMessage:
    """Local business mode, Part B: a win-back email to the business's own
    opted-in customer. The owner's copy as written ({first_name} and
    {business_name} filled) -- no model writes it and nothing is researched
    about the person (no posts, news or signals). The claim check still runs,
    then build_compliant_email adds the unsubscribe footer, the region notice
    and the RFC 8058 one-click headers, as for every email. No open or click
    tracking for consumers."""
    from app.services import claim_verification, local_business  # noqa: PLC0415

    subject, body = local_business.render_customer_email(
        session, strategy, lead, _step_of(session, message).template)
    checked = claim_verification.enforce(session, strategy=strategy, lead=lead,
                                         message=message, channel="email",
                                         fields={"subject": subject, "body": body})
    subject, body = checked["subject"], checked["body"]
    subject, body, headers, token = engine.build_compliant_email(lead, subject, body)
    message.subject = subject
    message.body = body
    message.unsubscribe_token = token
    message.personalization_json = {"winback": True, "model_written": False}
    return OutboundMessage(
        message_id=str(message.id),
        lead_id=str(lead.id),
        to_address=lead.email,
        subject=subject,
        body=body,
        headers=headers,
        thread_ref=_thread_ref_for_followup(session, message, headers),
    )


def _render_whatsapp_outbound(session: Session, strategy: Strategy, lead: Lead,
                              message: Message, now: datetime) -> OutboundMessage:
    """Build the WhatsApp OutboundMessage: template variables filled via
    the same personalization path as email, rendered preview persisted on
    the row BEFORE transmit."""
    from app.services import claim_verification  # noqa: PLC0415

    if message.whatsapp_kind is WhatsAppStepKind.TEXT:
        # Reply-handling step inside an open window: the step brief is
        # rendered by the same email personalizer minus subject.
        _, body = render_message(session, strategy, lead,
                                 _step_of(session, message), None)
        body = claim_verification.enforce(session, strategy=strategy, lead=lead,
                                          message=message, channel="whatsapp",
                                          fields={"body": body})["body"]
        message.body = body
        return OutboundMessage(
            message_id=str(message.id),
            lead_id=str(lead.id),
            to_address=lead.phone,
            body=body,
            metadata={"kind": "text"},
        )

    tmpl = session.get(WhatsAppTemplate, message.whatsapp_template_id)
    step = _step_of(session, message)
    from app.services import local_business  # noqa: PLC0415

    if local_business.is_customer(lead):
        # Local business mode, Part B: a customer's template variables are
        # deterministic name fields only -- never model-written.
        values = local_business.customer_whatsapp_values(
            lead, getattr(step, "variable_mapping_json", None),
            tmpl.variable_descriptions_json or {})
    else:
        values = render_whatsapp_variables(
            session, strategy, lead,
            template_body=tmpl.body or "",
            variable_descriptions=tmpl.variable_descriptions_json or {},
            variable_mapping=getattr(step, "variable_mapping_json", None),
        )
    # Feature A1: the template is Meta-approved and fixed; only the variables
    # are model-written, so they are what gets checked. An unsupported claim in
    # a slot is replaced with a neutral value rather than leaving it empty.
    if values:
        checked = claim_verification.enforce(
            session, strategy=strategy, lead=lead, message=message, channel="whatsapp",
            fields={f"variable:{n}": v for n, v in values.items()})
        values = {n: checked[f"variable:{n}"] for n in values}
    message.body = preview_whatsapp_body(tmpl.body or "", values)
    components = []
    if values:
        components = [{
            "type": "body",
            # TODO: verify against current WhatsApp Cloud API docs
            # (parameters array: [{"type": "text", "text": ...}, ...]
            # ordered by variable number)
            "parameters": [
                {"type": "text", "text": values[n]}
                for n in sorted(values, key=lambda x: int(x))
            ],
        }]
    return OutboundMessage(
        message_id=str(message.id),
        lead_id=str(lead.id),
        to_address=lead.phone,
        body=message.body,
        metadata={
            "kind": "template",
            "template_name": tmpl.name,
            "language": tmpl.language,
            "components": components,
        },
    )


def _step_of(session: Session, message: Message):
    from app.db.models import SequenceStep
    step = session.execute(
        select(SequenceStep).where(SequenceStep.sequence_id == message.sequence_id,
                                   SequenceStep.step_no == message.step_no)
    ).scalar_one_or_none()
    if step is not None:
        return step
    # Step edited/removed after scheduling: fall back to the snapshot.
    return SequenceStep(sequence_id=message.sequence_id, step_no=message.step_no,
                        template=message.template, variant=message.variant, delay_days=0)


# --------------------------------------------------------------------------
# Feature Group 5 — LinkedIn
# --------------------------------------------------------------------------


def _get_linkedin_channel(session: Session, strategy: Strategy) -> OutreachChannel:
    """Tests monkeypatch THIS function (like _get_channel for Gmail)."""
    from app.integrations.linkedin_channel import LinkedInChannel  # noqa: PLC0415
    from app.services import linkedin_outreach, notifications  # noqa: PLC0415

    owner = notifications.owner_of_strategy(session, strategy)
    return LinkedInChannel(session=session,
                           client=linkedin_outreach.client_for(session, owner))


def _prepare_linkedin(session: Session, strategy: Strategy, lead: Lead,
                      message: Message, now: datetime, tz: str):
    """Everything a LinkedIn send decides before it may claim the message.

    Returns (account, action) with the account's daily slot RESERVED, or a
    status string when the send ends here:
      failed_linkedin_not_configured   no Unipile credentials (permanent)
      failed_no_linkedin_account       the owner has no active account
      failed_linkedin_account_gone     the account that owns this lead's
                                       conversation was disconnected
      skipped_linkedin_not_connected   a message step, request still pending
                                       (the sequence continues, like a
                                       WhatsApp step for a lead without opt-in)
      deferred_cap                     every eligible account is at its ceiling
    """
    from app.services import linkedin_limits, notifications  # noqa: PLC0415
    from app.services import linkedin_outreach as li  # noqa: PLC0415

    def _fail(code: str, reason: str) -> str:
        message.status = MessageStatus.FAILED
        message.error = reason
        session.commit()
        return code

    owner = notifications.owner_of_strategy(session, strategy)
    client = li.client_for(session, owner)
    if client is None:
        return _fail("failed_linkedin_not_configured",
                     "LinkedIn is not configured (Admin > Integrations > Unipile)")
    if not li.active_accounts(session, owner):
        return _fail("failed_no_linkedin_account",
                     "no active LinkedIn account connected (Settings > Integrations)")
    pinned = li.relationship_account(session, lead)
    if lead.linkedin_account_id and (pinned is None or not pinned.is_active):
        return _fail("failed_linkedin_account_gone",
                     "the LinkedIn account that owns this conversation is "
                     "disconnected -- reconnect it to continue")

    li.refresh_profile(session, client, lead, owner)   # raises -> task retry
    action = li.resolve_action(_step_of(session, message).linkedin_action, lead)
    if action == li.WAIT:
        engine.skip_message(session, message, reason="skipped_linkedin_not_connected", now=now)
        return "skipped_linkedin_not_connected"

    account = linkedin_limits.pick_account(session, owner, action,
                                           required_account_id=lead.linkedin_account_id,
                                           now=now)
    if account is None and action == "inmail" and lead.linkedin_connection_status is None:
        # No InMail-capable account with credits: a connection request is the
        # next best first touch.
        action = "connect"
        account = linkedin_limits.pick_account(session, owner, action,
                                               required_account_id=lead.linkedin_account_id,
                                               now=now)
    if account is None:
        tomorrow = now.astimezone(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        message.scheduled_at = engine.next_window_slot(tomorrow, tz)
        session.commit()
        return "deferred_cap"
    return account, action


def _render_linkedin_outbound(session: Session, strategy: Strategy, lead: Lead,
                              message: Message, account, action: str) -> OutboundMessage:
    from app.services import linkedin_outreach  # noqa: PLC0415

    from app.services import claim_verification  # noqa: PLC0415

    rendered = linkedin_outreach.render(session, strategy, lead,
                                        _step_of(session, message), action)
    # Feature A1. A LinkedIn subject (InMail) is short-form like an email's.
    fields = {"body": rendered["text"]}
    if rendered.get("subject"):
        fields["subject"] = rendered["subject"]
    checked = claim_verification.enforce(session, strategy=strategy, lead=lead, message=message,
                                         channel="linkedin", fields=fields)
    rendered = {**rendered, "text": checked["body"], "subject": checked.get("subject")}
    message.body = rendered["text"]
    message.subject = rendered.get("subject")
    return OutboundMessage(
        message_id=str(message.id),
        lead_id=str(lead.id),
        to_address=lead.linkedin_provider_id or lead.linkedin_url,
        body=rendered["text"],
        subject=rendered.get("subject"),
        thread_ref=lead.linkedin_chat_id,
        metadata={"action": action, "account_id": account.unipile_account_id,
                  "provider_id": lead.linkedin_provider_id,
                  "chat_id": lead.linkedin_chat_id},
    )


# --------------------------------------------------------------------------
# Feature Group 6 — AI phone calls
# --------------------------------------------------------------------------


def _get_phone_channel(session: Session, strategy: Strategy) -> OutreachChannel:
    """Tests monkeypatch THIS function."""
    from app.integrations import voice_providers  # noqa: PLC0415
    from app.integrations.phone_channel import PhoneChannel  # noqa: PLC0415
    from app.services import notifications  # noqa: PLC0415

    vapi, eleven = voice_providers.get_clients(
        session, notifications.owner_of_strategy(session, strategy))
    return PhoneChannel(session=session, vapi=vapi, eleven=eleven)


def _prepare_phone(session: Session, strategy: Strategy, lead: Lead, message: Message,
                   now: datetime, tz: str) -> str | None:
    """The phone channel's gates. None = proceed (a call slot is reserved);
    a status string = the send ends here:
      failed_calling_disabled         the admin has not enabled AI calling
      failed_bad_number               the number cannot be dialled
      skipped_no_call_consent         no recorded consent (sequence continues)
      failed_calling_not_configured   no Vapi / ElevenLabs credentials
      deferred_cap                    the owner's daily call limit is reached
    See app/services/phone_calls.py for why consent is required.
    """
    from app.integrations import voice_providers  # noqa: PLC0415
    from app.services import notifications, phone_calls, system_settings  # noqa: PLC0415

    def _fail(code: str, reason: str) -> str:
        message.status = MessageStatus.FAILED
        message.error = reason
        session.commit()
        return code

    if not system_settings.get(session, "phone_calling_enabled"):
        return _fail("failed_calling_disabled", "AI calling is disabled (Admin > System Settings)")
    # Voice twin guard 3: an AI call of this account claimed to be human, so
    # every AI call waits until a person has read the transcript and resumed
    # them. Deferred, never dropped (like a plan hold).
    from app.services import voice_twin  # noqa: PLC0415

    if voice_twin.calls_paused(session, notifications.owner_of_strategy(session, strategy)):
        tomorrow = now.astimezone(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        message.scheduled_at = engine.next_window_slot(tomorrow, tz)
        message.error = "AI calls paused after a disclosure incident (Settings > Voice)"
        session.commit()
        return "deferred_ai_calls_paused"
    if not phone_calls.e164(lead.phone):
        return _fail("failed_bad_number", "the lead's phone number cannot be dialled")
    if not phone_calls.consent_ok(session, lead):
        engine.skip_message(session, message, reason="skipped_no_call_consent", now=now)
        return "skipped_no_call_consent"
    owner = notifications.owner_of_strategy(session, strategy)
    vapi, eleven = voice_providers.get_clients(session, owner)
    if vapi is None and not (eleven is not None and eleven.can_call):
        return _fail("failed_calling_not_configured",
                     "no AI voice provider configured (Admin > Integrations > Vapi / ElevenLabs)")
    if not phone_calls.reserve_call_slot(session, owner, now=now):
        tomorrow = now.astimezone(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        message.scheduled_at = engine.next_window_slot(tomorrow, tz)
        session.commit()
        return "deferred_cap"
    return None


def _render_phone_outbound(session: Session, strategy: Strategy, lead: Lead,
                           message: Message) -> OutboundMessage:
    from app.services import notifications, phone_calls  # noqa: PLC0415

    owner = notifications.owner_of_strategy(session, strategy)
    call, meta = phone_calls.prepare_call(session, strategy, lead,
                                          _step_of(session, message).template,
                                          owner_id=owner, message=message)
    # Feature A1: the call script is spoken verbatim, so its claims are checked
    # like an email's. Talking points with an unsupported claim are dropped; the
    # system prompt is rebuilt from the checked script.
    from app.services import claim_verification  # noqa: PLC0415

    script = dict(call.script_json or {})
    points = [p for p in (script.get("talking_points") or []) if isinstance(p, str)]
    fields = {"first_message": script.get("first_message"), "voicemail": script.get("voicemail"),
              "close": script.get("close")}
    fields.update({f"item:talking_point:{i}": p for i, p in enumerate(points)})
    checked = claim_verification.enforce(session, strategy=strategy, lead=lead, message=message,
                                         channel="phone", fields=fields)
    # Voice twin guard 1: whatever the claim check changed, the disclosure and
    # the recording notice still open the call (and the voicemail).
    from app.services import voice_twin  # noqa: PLC0415

    opening = meta.get("disclosure")
    if not voice_twin.valid_opening(opening):
        opening = voice_twin.GENERIC_OPENING
    vm_opening = meta.get("voicemail_disclosure")
    if not voice_twin.valid_voicemail_opening(vm_opening):
        vm_opening = voice_twin.GENERIC_VOICEMAIL_OPENING
    names = tuple(meta.get("owner_names") or ()) + (str(meta.get("owner_name") or ""),)
    script["first_message"] = voice_twin.enforce_opening(
        checked["first_message"] or script.get("first_message"), opening, names)
    if checked["voicemail"]:
        checked["voicemail"] = voice_twin.enforce_opening(checked["voicemail"], vm_opening,
                                                          names)
    script["close"] = checked["close"]
    script["talking_points"] = [checked[f"item:talking_point:{i}"] for i in range(len(points))
                                if checked[f"item:talking_point:{i}"]]
    if checked["voicemail"] != script.get("voicemail"):
        script["voicemail"] = checked["voicemail"]
        call.voicemail_text = checked["voicemail"] or None
        # The pre-rendered audio says the unchecked text; never play it.
        call.voicemail_audio_url = None
    call.script_json = script
    meta = {**meta, "first_message": script["first_message"],
            "voicemail_text": call.voicemail_text,
            "system_prompt": phone_calls.system_prompt(script, lead, meta.get("identity"))}
    message.body = call.script_json["first_message"]
    message.subject = None
    return OutboundMessage(message_id=str(message.id), lead_id=str(lead.id),
                           to_address=call.to_number, body=message.body, metadata=meta)


def _phone_send_failed(session: Session, strategy: Strategy, message: Message,
                       error: str, now: datetime) -> None:
    from app.services import notifications, phone_calls  # noqa: PLC0415

    phone_calls.mark_send_failed(session, phone_calls.call_for_message(session, message),
                                 error, notifications.owner_of_strategy(session, strategy), now)


def _thread_ref_for_followup(session: Session, message: Message,
                             headers: dict | None = None) -> str | None:
    """Gmail threadId of the previous email in this sequence, and -- into
    `headers` -- In-Reply-To / References to that email's own Message-ID
    (migration 0096). Gmail threads on threadId AND those headers (and a
    matching Subject); other clients thread on the headers alone. A previous
    email sent before 0096 has no stored Message-ID: threadId only, as before."""
    if message.step_no <= 1:
        return None
    prev = session.execute(
        select(Message).where(Message.sequence_id == message.sequence_id,
                              Message.lead_id == message.lead_id,
                              Message.step_no < message.step_no,
                              # Only an email: a LinkedIn chat id or a WhatsApp
                              # number is not a Gmail threadId.
                              Message.channel == ChannelType.EMAIL,
                              Message.thread_ref.isnot(None))
        .order_by(Message.step_no.desc())
    ).scalars().first()
    if prev is None:
        return None
    if headers is not None:
        from app.services import email_threading  # noqa: PLC0415

        headers.update(email_threading.reply_headers(prev.rfc_message_id,
                                                     prev.rfc_references))
    return prev.thread_ref


# --------------------------------------------------------------------------
# Reply polling + routing (chunk 4)
# --------------------------------------------------------------------------


def poll_account_replies_impl(session: Session, account: GmailAccount,
                              channel: OutreachChannel | None = None,
                              now: datetime | None = None) -> int:
    now = now or _now()
    channel = channel or _get_channel(session, account)
    since = account.last_poll_at or (now - timedelta(days=2))
    inbound = channel.fetch_replies(since=since)
    handled = 0
    for msg in inbound:
        route_inbound_impl(session, msg, account)
        handled += 1
    account.last_poll_at = now
    session.commit()
    return handled


def _notify_new_reply(session: Session, lead: Lead, classification: str | None = None,
                      channel: str = "email") -> None:
    """Push "new reply" to the lead's owner. Shared by the email, WhatsApp and
    LinkedIn inbound routers so all resolve the recipient the same way."""
    from app.services import notifications  # noqa: PLC0415

    owner = notifications.owner_of_lead(session, lead)
    if owner is None:
        logger.warning("lead %s has no resolvable owner - no reply notification",
                       lead.id)
        return
    notifications.dispatch(
        notifications.notify_new_reply(owner, lead.id, company=lead.company)
    )
    # Feature Group 4: outbound webhooks for every reply; Slack only for an
    # interested one (the spec's "interested reply received"). push=False on
    # both -- the M7 push above already told the phone.
    from app.services import event_bus  # noqa: PLC0415
    from app.workers import notification_tasks  # noqa: PLC0415

    who = lead.full_name or lead.email or "A lead"
    payload = {"lead": event_bus.lead_payload(lead), "channel": channel,
               "classification": classification}
    link = f"/leads/detail?id={lead.id}"
    notification_tasks.enqueue_event(
        owner, "reply_received", push=False, slack=False, title="A lead replied",
        body=f"{who} replied on {channel} ({classification or 'unclassified'}).",
        deep_link=link, data={"leadId": str(lead.id)}, webhook_payload=payload)
    if classification == "interested":
        notification_tasks.enqueue_event(
            owner, "reply_interested", push=False, title="Interested reply",
            body=f"{who}{f' ({lead.company})' if lead.company else ''} replied with "
                 f"interest on {channel}.",
            deep_link=link, data={"leadId": str(lead.id)}, webhook_payload=payload)


def _score_authenticity(session: Session, reply: InboundReply, lead: Lead,
                        classification: str, channel: ChannelType) -> None:
    """Feature A3 for the chat channels: score against the most recent send
    on the same channel (a reply seconds after it is almost always a machine).
    Never raises -- scoring is advisory, routing is not."""
    from app.services import reply_authenticity  # noqa: PLC0415

    try:
        sent_at = session.execute(
            select(Message.sent_at)
            .where(Message.lead_id == lead.id, Message.channel == channel,
                   Message.sent_at.isnot(None))
            .order_by(Message.sent_at.desc()).limit(1)
        ).scalar_one_or_none()
    except Exception:  # noqa: BLE001
        sent_at = None
    reply_authenticity.safe_apply(session, reply, classification=classification,
                                  sent_at=sent_at)


def route_inbound_impl(session: Session, inbound, account: GmailAccount) -> str:
    """Match an inbound message to a lead and apply the routing rules.
    Never auto-replies to humans.

    P0-5b: the classifier calls are metered to the account whose inbox
    received the message -- counted, never capped (STOP / bounce / unsubscribe
    handling must not be starved by the daily AI limit)."""
    from app.services import usage_meter  # noqa: PLC0415

    with usage_meter.user_scope(session, getattr(account, "user_id", None),
                                "reply_handling"):
        return _route_inbound(session, inbound, account)


def _route_inbound(session: Session, inbound, account: GmailAccount) -> str:
    # Every lookup below is scoped to the leads owned by the user whose Gmail
    # account received this message. Both used to be global: an inbound reply
    # was matched to the FIRST lead anywhere in the database with that address
    # (`select(Lead).where(Lead.email == ...)`, no tenancy filter). If two
    # customers prospected the same person - routine in B2B - a reply landing
    # in one account's inbox flipped the other customer's lead to REPLIED,
    # stopped their sequences, wrote a REPLIED outcome into their learning
    # loop, and on an "unsubscribe_request" suppressed their lead. This is the
    # same cross-tenant matching class Phase A fixed in whatsapp_optin.py.
    owned_lead_ids = (
        select(Lead.id)
        .join(Strategy, Strategy.id == Lead.strategy_id)
        .join(Product, Product.id == Strategy.product_id)
        .where(Product.user_id == account.user_id)
    )

    matched_message: Message | None = None
    if inbound.thread_ref:
        matched_message = session.execute(
            select(Message)
            .where(Message.thread_ref == inbound.thread_ref,
                   Message.lead_id.in_(owned_lead_ids))
            .order_by(Message.step_no.desc())
        ).scalars().first()

    lead: Lead | None = None
    if matched_message is not None:
        lead = session.get(Lead, matched_message.lead_id)
    elif inbound.from_address:
        addr = inbound.from_address.strip()
        lead = session.execute(
            select(Lead).where(Lead.email == addr.lower(),
                               Lead.id.in_(owned_lead_ids))
        ).scalars().first() or session.execute(
            select(Lead).where(Lead.email == inbound.from_address,
                               Lead.id.in_(owned_lead_ids))
        ).scalars().first()

    classification = classify_reply(inbound.from_address, inbound.subject, inbound.body,
                                    session=session)

    reply_row = InboundReply(
        lead_id=lead.id if lead else None,
        message_id=matched_message.id if matched_message else None,
        account_ref=str(account.id),
        channel="email",
        thread_ref=inbound.thread_ref,
        from_address=inbound.from_address,
        subject=inbound.subject,
        body=inbound.body,
        classification=classification,
        received_at=inbound.received_at,
        # gaps-sweep-2 #6: what an answer needs for In-Reply-To / References.
        rfc_message_id=getattr(inbound, "rfc_message_id", None),
        rfc_references=getattr(inbound, "rfc_references", None),
    )
    # Feature A3: scored in the same commit the reply is stored in, so the
    # inbox never shows a reply without its authenticity. Never raises.
    from app.services import reply_authenticity  # noqa: PLC0415

    reply_authenticity.safe_apply(
        session, reply_row, classification=classification,
        headers=reply_authenticity.headers_from_raw(getattr(inbound, "raw", None)),
        sent_at=matched_message.sent_at if matched_message else None)
    session.add(reply_row)
    session.commit()

    # Feature 2: classify what the HUMAN should do next. Dispatched AFTER the
    # commit so the task can never look up a row that is not there yet, and
    # before the routing below so a slow model call cannot delay the hard
    # stop / suppression that routing performs. enqueue() never raises.
    from app.workers import reply_tasks  # noqa: PLC0415

    reply_tasks.enqueue(reply_row.id)

    if lead is None:
        return f"unmatched_{classification}"
    # Feature Group 9: a machine's "reply" is stored (above) but is not a
    # reply -- the sequence neither stops nor advances, nothing is counted.
    if classification == "automated_response":
        return classification

    enrollments = [
        e for e in session.execute(
            select(SequenceEnrollment).where(SequenceEnrollment.lead_id == lead.id)
        ).scalars()
    ]

    if classification == "unsubscribe_request":
        # Treated EXACTLY like an unsubscribe click — immediately.
        engine.unsubscribe_lead(session, lead, source="reply",
                                message_id=matched_message.id if matched_message else None)
        return classification

    if classification == "bounce":
        engine.record_bounce(session, lead, matched_message,
                             meta={"from": inbound.from_address})
        return classification

    if classification == "out_of_office":
        until = (inbound.received_at or datetime.now(timezone.utc)) + timedelta(
            days=settings.ooo_reschedule_days
        )
        for e in enrollments:
            if e.status is EnrollmentStatus.ACTIVE:
                engine.pause_enrollment(session, e, until=until)
        return classification

    # interested / question / objection / not_interested → stop + record
    for e in enrollments:
        if e.status is not EnrollmentStatus.STOPPED:
            engine.stop_enrollment(session, e, reason=f"replied_{classification}")
    lead.status = LeadStatus.REPLIED
    session.add(Outcome(lead_id=lead.id,
                        message_id=matched_message.id if matched_message else None,
                        event=OutcomeEvent.REPLIED,
                        channel="email",
                        meta_json={"classification": classification}))
    session.commit()
    # A human replied - tell the lead's owner. dispatch() never raises, so a
    # push failure cannot undo the stop/outcome writes above.
    _notify_new_reply(session, lead, classification, "email")

    return classification


def route_whatsapp_inbound_impl(session: Session, lead: Lead,
                                reply: InboundReply) -> str:
    """Metered wrapper (P0-5b): counted to the lead's owner, never capped."""
    with _reply_scope(session, lead):
        return _route_whatsapp_inbound(session, lead, reply)


def _reply_scope(session: Session, lead: Lead):
    from app.services import notifications, usage_meter  # noqa: PLC0415

    try:
        owner = notifications.owner_of_lead(session, lead)
    except Exception:  # noqa: BLE001 -- attribution must never block routing
        owner = None
    return usage_meter.user_scope(session, owner, "reply_handling")


def _route_whatsapp_inbound(session: Session, lead: Lead,
                            reply: InboundReply) -> str:
    """Classify a non-STOP inbound WhatsApp message with the SAME Claude
    classifier as email and apply the SAME unified stop rules (M4 Chunk 3):
    a WhatsApp reply stops the WHOLE sequence — including pending cold
    email steps — because engine.stop_enrollment stops enrollments, not
    channels. WhatsApp delivery failures never arrive here (Meta reports
    them via status webhooks, already mapped to the bounce path), but the
    'bounce' class is still handled for robustness.
    Never auto-replies to humans."""
    classification = classify_reply(reply.from_address, None, reply.body, session=session)
    reply.classification = classification
    _score_authenticity(session, reply, lead, classification, ChannelType.WHATSAPP)
    session.commit()
    if classification == "automated_response":   # Feature Group 9
        return classification

    enrollments = [
        e for e in session.execute(
            select(SequenceEnrollment).where(SequenceEnrollment.lead_id == lead.id)
        ).scalars()
    ]

    if classification == "unsubscribe_request":
        # A phrased opt-out the keyword matcher missed — same effect as
        # STOP: audit row + suppression on both identifiers + full stop.
        optin_svc.revoke_opt_in(session, lead, source=OptInSource.INBOUND_MESSAGE,
                                evidence=f"classified unsubscribe_request "
                                         f"(inbound reply {reply.id})")
        engine.unsubscribe_lead(session, lead, source="whatsapp_reply",
                                channel="whatsapp")
        return classification

    if classification == "bounce":
        engine.record_bounce(session, lead, None,
                             meta={"from": reply.from_address},
                             channel="whatsapp")
        return classification

    if classification == "out_of_office":
        until = (reply.received_at or datetime.now(timezone.utc)) + timedelta(
            days=settings.ooo_reschedule_days
        )
        for e in enrollments:
            if e.status is EnrollmentStatus.ACTIVE:
                engine.pause_enrollment(session, e, until=until)
        return classification

    # interested / question / objection / not_interested -> stop EVERYTHING
    for e in enrollments:
        if e.status is not EnrollmentStatus.STOPPED:
            engine.stop_enrollment(session, e, reason=f"replied_{classification}")
    lead.status = LeadStatus.REPLIED
    session.add(Outcome(lead_id=lead.id, event=OutcomeEvent.REPLIED,
                        channel="whatsapp",
                        meta_json={"classification": classification}))
    session.commit()
    # A human replied - tell the lead's owner. dispatch() never raises, so a
    # push failure cannot undo the stop/outcome writes above.
    _notify_new_reply(session, lead, classification, "whatsapp")

    return classification


# --------------------------------------------------------------------------
# Feature Group 5: LinkedIn inbound routing
# (the section header below is kept for the Celery wrappers)
# --------------------------------------------------------------------------


def route_linkedin_inbound_impl(session: Session, lead: Lead,
                                reply: InboundReply) -> str:
    """Metered wrapper (P0-5b): counted to the lead's owner, never capped."""
    with _reply_scope(session, lead):
        return _route_linkedin_inbound(session, lead, reply)


def _route_linkedin_inbound(session: Session, lead: Lead,
                            reply: InboundReply) -> str:
    """Classify a LinkedIn reply with the SAME classifier and apply the SAME
    unified stop rules as email and WhatsApp. An unsubscribe request
    suppresses the email, the phone AND the LinkedIn profile. Never
    auto-replies to humans."""
    classification = classify_reply(reply.from_address, None, reply.body, session=session)
    reply.classification = classification
    _score_authenticity(session, reply, lead, classification, ChannelType.LINKEDIN)
    session.commit()
    if classification == "automated_response":   # Feature Group 9
        return classification

    enrollments = list(session.execute(
        select(SequenceEnrollment).where(SequenceEnrollment.lead_id == lead.id)
    ).scalars())

    if classification == "unsubscribe_request":
        engine.unsubscribe_lead(session, lead, source="linkedin_reply", channel="linkedin")
        return classification

    if classification == "out_of_office":
        until = (reply.received_at or datetime.now(timezone.utc)) + timedelta(
            days=settings.ooo_reschedule_days)
        for e in enrollments:
            if e.status is EnrollmentStatus.ACTIVE:
                engine.pause_enrollment(session, e, until=until)
        return classification

    if classification == "bounce":
        return classification   # not meaningful on LinkedIn; recorded above

    for e in enrollments:
        if e.status is not EnrollmentStatus.STOPPED:
            engine.stop_enrollment(session, e, reason=f"replied_{classification}")
    lead.status = LeadStatus.REPLIED
    session.add(Outcome(lead_id=lead.id, event=OutcomeEvent.REPLIED, channel="linkedin",
                        meta_json={"classification": classification}))
    session.commit()
    _notify_new_reply(session, lead, classification, "linkedin")
    return classification


# --------------------------------------------------------------------------
# Celery wrappers + Beat entry points
# --------------------------------------------------------------------------


@celery_app.task(name="leadpilot.outreach.dispatch")
def dispatch_due_messages() -> int:
    session = SessionLocal()
    try:
        return len(dispatch_due_messages_impl(session))
    finally:
        session.close()


@celery_app.task(name="leadpilot.outreach.send", bind=True, max_retries=3)
def send_message(self, message_id: str) -> str:
    session = SessionLocal()
    try:
        # Feature Group 3: every model call made while rendering this send is
        # attributed to its campaign (usage_meter).
        from app.services import usage_meter  # noqa: PLC0415

        message = session.get(Message, uuid.UUID(message_id))
        sequence = session.get(Sequence, message.sequence_id) if message else None
        strategy = session.get(Strategy, sequence.strategy_id) if sequence else None
        with usage_meter.owner_scope(session, strategy, "outreach"):
            result = send_message_impl(session, uuid.UUID(message_id))
        # F04: a Reply Copilot answer sent (or held / cancelled) by the beat
        # dispatcher -- e.g. after a send-review approval or a deferral --
        # records its result and keeps the reply's inbox marker true. A no-op
        # for every other message.
        if message is not None and message.origin == "reply_copilot":
            from app.services import reply_copilot  # noqa: PLC0415

            reply_copilot.after_send_attempt(session, uuid.UUID(message_id), result)
        return result
    except Exception as exc:
        session.rollback()
        logger.exception("send failed for message %s — retrying", message_id)
        raise self.retry(exc=exc, countdown=60)
    finally:
        session.close()


@celery_app.task(name="leadpilot.outreach.poll_replies")
def poll_replies() -> int:
    session = SessionLocal()
    try:
        total = 0
        for account in session.execute(select(GmailAccount)).scalars().all():
            try:
                total += poll_account_replies_impl(session, account)
            except Exception:
                logger.exception("reply polling failed for account %s", account.id)
        return total
    finally:
        session.close()


# --------------------------------------------------------------------------
# Engagement Hub, Feature 1 — automated follow-up
# --------------------------------------------------------------------------
#
# WHAT THIS DOES THAT THE SEQUENCE ENGINE DOES NOT
# schedule_next_step already queues step N+1 the moment step N sends, on the
# step's `delay_days` timer. That covers the happy path completely, and this
# sweep must not fire for it -- two systems scheduling the same follow-up is
# how a prospect receives the same message twice.
#
# So the sweep's central guard is: SKIP ANY ENROLLMENT THAT ALREADY HAS A
# PENDING MESSAGE. What is left is the population the engine currently drops
# on the floor:
#
#   * a send that failed permanently (status FAILED) -- the enrollment stays
#     ACTIVE with nothing queued behind it, forever;
#   * a WhatsApp step that fell to NEEDS_TEMPLATE because the 24h window
#     closed or a template lost approval -- documented, expected, and
#     currently terminal for that lead;
#   * a step deleted or renumbered after its message was scheduled.
#
# In each case a real prospect was contacted, said nothing, and the system
# quietly stopped. That is what this fixes.
#
# ENROLLMENT STATUS: ACTIVE ONLY.
# COMPLETED enrollments (every step sent, no reply) are deliberately out of
# scope. Sweeping them would auto-DM every lead who ever finished a sequence,
# on every sweep, with no upper bound on how many follow-ups one lead
# receives -- a mass-send this system has no user-facing control for. If that
# behaviour is wanted it needs its own cap, its own opt-in and its own row in
# the compliance story.
#
# IDEMPOTENCY IS TWO LAYERS, NOT ONE.
# The Redis lock (followup:{enrollment_id}:{step_no}, 2h TTL) stops two
# workers acting on the same overdue enrollment. The message row's own
# SCHEDULED -> SENDING claim in send_message_impl stops a double transmit if
# the lock ever fails open (Redis down). The lock is an optimisation for the
# common case; the claim is the guarantee.

FOLLOWUP_LOCK_PREFIX = "followup:"

# How far into the future an auto follow-up message is dated when it is
# created. It is NOT a delay: send_followup_impl transmits it immediately in
# the same task, and send_message_impl does not look at scheduled_at.
#
# It exists so the 60-second beat dispatcher cannot claim the row in the
# window between INSERT and send. If it did, the message would still send
# exactly once (the SENDING claim guarantees that) but it would be sent by the
# generic path, and the OutcomeEvent would be written without
# source="auto_followup" -- so the audit trail would lose the one fact that
# says this send was automatic.
_FOLLOWUP_DISPATCH_GUARD = timedelta(minutes=5)


def _followup_lock_key(enrollment_id, step_no: int) -> str:
    return f"{FOLLOWUP_LOCK_PREFIX}{enrollment_id}:{step_no}"


def _acquire_followup_lock(enrollment_id, step_no: int) -> bool:
    """SET NX EX. True when this caller owns the follow-up.

    FAILS OPEN. If Redis is unreachable this returns True and the send goes
    ahead protected only by the message row's SENDING claim. The alternative
    -- failing closed -- means a Redis outage silently stops every follow-up
    in the product with nothing surfacing that it has, which is a worse
    failure than the one it would be preventing.
    """
    try:
        from app.core.redis_client import get_sync_redis  # noqa: PLC0415

        return bool(get_sync_redis().set(
            _followup_lock_key(enrollment_id, step_no), "1",
            nx=True, ex=settings.followup_lock_ttl_seconds,
        ))
    except Exception as exc:  # noqa: BLE001
        logger.warning("follow-up lock unavailable (%s) — relying on the "
                       "message SENDING claim for enrollment %s step %s",
                       exc, enrollment_id, step_no)
        return True


def _aware(value: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes for timestamptz columns; PostgreSQL does
    not. Every value in those columns is UTC, so this restates that rather
    than converting -- without it the arithmetic below raises on SQLite only,
    i.e. in the test suite and nowhere else."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _last_sent_message(session: Session, enrollment: SequenceEnrollment) -> Message | None:
    return session.execute(
        select(Message)
        .where(Message.sequence_id == enrollment.sequence_id,
               Message.lead_id == enrollment.lead_id,
               Message.status == MessageStatus.SENT)
        .order_by(Message.step_no.desc(), Message.sent_at.desc())
    ).scalars().first()


def _has_pending_message(session: Session, enrollment: SequenceEnrollment) -> bool:
    # A message a send-time guard HOLDS (F01 Bounce Shield, F10 Champion
    # Tracker: FAILED + the guard's prefix) is still pending for its step.
    # Without this the follow-up sweep made a new copy of a held step every
    # time its lock expired, and every copy sent when the hold released.
    from app.services import send_holds  # noqa: PLC0415

    return session.execute(
        select(Message.id).where(
            Message.sequence_id == enrollment.sequence_id,
            Message.lead_id == enrollment.lead_id,
            send_holds.pending_clause(),
        ).limit(1)
    ).scalars().first() is not None


def _rejected_steps(session: Session, enrollment: SequenceEnrollment) -> set[int]:
    """Step numbers of this enrollment that a reviewer REJECTED in the send
    review queue (CANCELLED + send_review.REJECTED_ERROR_PREFIX). A rejected
    step is decided: the sweep must never create it again."""
    from app.services.send_review import REJECTED_ERROR_PREFIX  # noqa: PLC0415

    return set(session.execute(
        select(Message.step_no).where(
            Message.sequence_id == enrollment.sequence_id,
            Message.lead_id == enrollment.lead_id,
            Message.status == MessageStatus.CANCELLED,
            Message.error.like(f"{REJECTED_ERROR_PREFIX}%"),
        )
    ).scalars())


def _next_followup_step(session: Session, enrollment: SequenceEnrollment,
                        last: Message) -> int | None:
    """The step the follow-up should send: last sent + 1, moved past every
    step a reviewer rejected. None when that walks past the sequence's last
    step -- no generated auto-DM stands in for a step a human just rejected.
    (send_review.reject now advances the sequence itself; this is the guard
    for enrollments rejected before that, and for a paused enrollment that was
    not advanced.)"""
    rejected = _rejected_steps(session, enrollment)
    step_no = last.step_no + 1
    if step_no not in rejected:
        return step_no
    while step_no in rejected:
        step_no += 1
    return step_no if _step_for(session, enrollment.sequence_id, step_no) is not None else None


def _replied_since(session: Session, lead_id, since: datetime | None) -> bool:
    """Has this lead replied since the last message went out?

    Scoped by time rather than "has ever replied", because a lead can reply to
    one campaign and be legitimately enrolled in another later. `outcomes` has
    no enrollment_id to filter on -- it is keyed on lead and message -- so the
    send time of the message we are following up is the boundary that makes
    the question answerable.
    """
    query = select(Outcome.id).where(Outcome.lead_id == lead_id,
                                     Outcome.event == OutcomeEvent.REPLIED)
    if since is not None:
        query = query.where(Outcome.ts >= since)
    return session.execute(query.limit(1)).scalars().first() is not None


def _step_for(session: Session, sequence_id, step_no: int):
    from app.db.models import SequenceStep  # noqa: PLC0415

    return session.execute(
        select(SequenceStep).where(SequenceStep.sequence_id == sequence_id,
                                   SequenceStep.step_no == step_no)
    ).scalars().first()


def check_followup_due_impl(session: Session, now: datetime | None = None,
                            enqueue=None) -> list[tuple[str, int]]:
    """Find enrollments whose last message has gone unanswered past its step's
    follow-up window. Returns the (enrollment_id, step_no) pairs enqueued.

    `step_no` is the step the follow-up WILL send: last_sent + 1. It is part
    of the lock key, so a lead can receive step 3's follow-up after step 2's
    without the second being mistaken for a retry of the first.
    """
    now = now or _now()
    enqueue = enqueue or (lambda eid, step: send_followup_task.delay(eid, step))

    enrollments = session.execute(
        select(SequenceEnrollment).where(
            SequenceEnrollment.status == EnrollmentStatus.ACTIVE
        )
    ).scalars().all()

    due: list[tuple[str, int]] = []
    for enrollment in enrollments:
        # The guard that keeps this out of the sequence engine's way. See the
        # section comment above.
        if _has_pending_message(session, enrollment):
            continue

        last = _last_sent_message(session, enrollment)
        if last is None or last.sent_at is None:
            continue

        step = _step_for(session, enrollment.sequence_id, last.step_no)
        if step is not None and not step.followup_enabled:
            continue
        delay_hours = step.followup_delay_hours if step is not None else 72

        sent_at = _aware(last.sent_at)
        if now - sent_at < timedelta(hours=delay_hours):
            continue
        if _replied_since(session, enrollment.lead_id, sent_at):
            continue

        # A campaign the user paused sends nothing, automatic or not. Checked
        # here as well as in send_message_impl so a paused campaign does not
        # burn a lock and a task per sweep.
        lead = session.get(Lead, enrollment.lead_id)
        strategy = session.get(Strategy, lead.strategy_id) if lead else None
        if strategy is None or strategy.campaign_state != engine.CAMPAIGN_ACTIVE:
            continue
        # Local business mode: the automatic follow-up is model-written; a
        # business's own customer only ever receives the owner's copy.
        from app.services.local_business import is_customer  # noqa: PLC0415

        if is_customer(lead):
            continue

        next_step_no = _next_followup_step(session, enrollment, last)
        if next_step_no is None:
            continue
        enqueue(str(enrollment.id), next_step_no)
        due.append((str(enrollment.id), next_step_no))

    return due


def send_followup_impl(session: Session, enrollment_id: uuid.UUID, step_no: int,
                       now: datetime | None = None) -> str:
    """Send one automatic follow-up. Idempotent; safe to call twice.

    Two shapes, decided by whether the sequence has a step `step_no`:
      * IT DOES  -- schedule that step's message and send it now. This is the
        stalled-sequence recovery: the step exists, its message failed or was
        never created, and this puts the lead back on the rails.
      * IT DOES NOT -- generate a follow-up brief from the lead's own history
        (message_personalization.build_followup_brief) and send it as step
        `step_no` on the same channel as the last message. The message row
        carries the generated brief as its `template`, so the ordinary render
        path personalizes it at send time exactly like a human-written step.
    """
    now = now or _now()
    enrollment = session.get(SequenceEnrollment, enrollment_id)
    if enrollment is None:
        return "missing"
    if enrollment.status is not EnrollmentStatus.ACTIVE:
        return f"skipped_{enrollment.status.value}"
    if _has_pending_message(session, enrollment):
        # The engine scheduled something between the sweep and now.
        return "skipped_already_pending"
    if step_no in _rejected_steps(session, enrollment):
        # A reviewer rejected this step in the send-review queue: decided,
        # never re-created.
        return "skipped_rejected_in_review"

    if not _acquire_followup_lock(enrollment_id, step_no):
        return "locked"

    last = _last_sent_message(session, enrollment)
    if last is None or last.sent_at is None:
        return "no_prior_send"
    if _replied_since(session, enrollment.lead_id, _aware(last.sent_at)):
        return "skipped_replied"

    lead = session.get(Lead, enrollment.lead_id)
    sequence = session.get(Sequence, enrollment.sequence_id)
    strategy = session.get(Strategy, lead.strategy_id)
    if strategy.campaign_state != engine.CAMPAIGN_ACTIVE:
        return "campaign_paused"

    step = _step_for(session, enrollment.sequence_id, step_no)
    if step is not None:
        channel = step.effective_channel(sequence)
        template = step.template
        variant = step.variant
        whatsapp_kind = step.whatsapp_kind
        whatsapp_template_id = step.whatsapp_template_id
        mode = "sequence_step"
    else:
        # The auto-DM fallback. Same channel as the last message: a lead who
        # has only ever been emailed should not suddenly receive a WhatsApp
        # message from an automation, and the opt-in state that would make
        # that legal is a decision a human makes, not a fallback.
        channel = last.channel
        if channel is ChannelType.WHATSAPP:
            # A cold WhatsApp message requires an approved template, and there
            # is no step to name one. Generating free-form copy here would
            # either be blocked by the adapter's compliance guard or, worse,
            # sent inside a window the lead did not open. Email-only fallback.
            logger.info("no auto follow-up for enrollment %s: last channel was "
                        "WhatsApp and a generated follow-up has no approved "
                        "template", enrollment_id)
            return "skipped_whatsapp_needs_template"
        template = personalization.build_followup_brief(
            session, strategy, lead, channel=channel.value
        )
        variant = last.variant
        whatsapp_kind = None
        whatsapp_template_id = None
        mode = "auto_dm"

    message = Message(
        sequence_id=enrollment.sequence_id,
        lead_id=lead.id,
        channel=channel,
        step_no=step_no,
        template=template,
        variant=variant,
        whatsapp_kind=whatsapp_kind,
        whatsapp_template_id=whatsapp_template_id,
        status=MessageStatus.SCHEDULED,
        # See _FOLLOWUP_DISPATCH_GUARD: keeps the beat dispatcher off this row
        # for the moment it takes to send it here. Not a delay.
        scheduled_at=now + _FOLLOWUP_DISPATCH_GUARD,
    )
    session.add(message)
    session.commit()

    result = send_message_impl(session, message.id, now=now,
                               outcome_source="auto_followup")
    logger.info("auto follow-up (%s) for enrollment %s step %s: %s",
                mode, enrollment_id, step_no, result)
    return result


@celery_app.task(name="leadpilot.outreach.check_followup_due")
def check_followup_due() -> int:
    session = SessionLocal()
    try:
        return len(check_followup_due_impl(session))
    finally:
        session.close()


def _enrollment_scope(session: Session, enrollment_id):
    """owner_scope(strategy, "outreach") for an enrollment's campaign."""
    from app.services import usage_meter  # noqa: PLC0415

    enrollment = session.get(SequenceEnrollment, uuid.UUID(str(enrollment_id)))
    sequence = session.get(Sequence, enrollment.sequence_id) if enrollment else None
    strategy = session.get(Strategy, sequence.strategy_id) if sequence else None
    return usage_meter.owner_scope(session, strategy, "outreach")


@celery_app.task(name="leadpilot.outreach.send_followup", bind=True, max_retries=3)
def send_followup_task(self, enrollment_id: str, step_no: int) -> str:
    session = SessionLocal()
    try:
        # P0-5b: the follow-up brief and the render are metered like any send.
        with _enrollment_scope(session, enrollment_id):
            return send_followup_impl(session, uuid.UUID(enrollment_id), int(step_no))
    except Exception as exc:
        session.rollback()
        logger.exception("auto follow-up failed for enrollment %s step %s — "
                         "retrying", enrollment_id, step_no)
        raise self.retry(exc=exc, countdown=300)
    finally:
        session.close()


# --------------------------------------------------------------------------
# Feature 5 — opt-in post-sequence re-engagement
# --------------------------------------------------------------------------
#
# The sweep only ENQUEUES, and never more per campaign than the campaign's
# remaining re-engagement allowance. The send claims a reengagement_attempts
# row (UNIQUE per enrollment) BEFORE creating a message, then hands the message
# to send_message_impl like any other send. See app/services/reengagement.py.


def check_reengagement_due_impl(session: Session, now: datetime | None = None,
                                enqueue=None) -> list[str]:
    now = now or _now()
    enqueue = enqueue or (lambda eid: send_reengagement_task.delay(eid))
    if not reengagement.allowed(session):
        return []

    strategies = session.execute(
        select(Strategy).where(Strategy.reengagement_enabled.is_(True),
                               Strategy.campaign_state == engine.CAMPAIGN_ACTIVE)
    ).scalars().all()

    due: list[str] = []
    for strategy in strategies:
        left = reengagement.allowance_left(session, strategy, now)
        for enrollment in reengagement.candidates(session, strategy, now, left):
            enqueue(str(enrollment.id))
            due.append(str(enrollment.id))
    return due


def send_reengagement_impl(session: Session, enrollment_id: uuid.UUID,
                           now: datetime | None = None) -> str:
    """Send one re-engagement. Idempotent: a second call for the same
    enrollment returns skipped_already_attempted and creates nothing."""
    now = now or _now()
    enrollment = session.get(SequenceEnrollment, enrollment_id)
    if enrollment is None:
        return "missing"
    reason = reengagement.ineligibility(session, enrollment, now)
    if reason is not None:
        return f"skipped_{reason}"

    lead = session.get(Lead, enrollment.lead_id)
    strategy = session.get(Strategy, lead.strategy_id)
    # Checked before claiming: an over-cap enrollment stays unclaimed, so a
    # later sweep can still re-engage it once allowance frees up.
    if reengagement.allowance_left(session, strategy, now) <= 0:
        return "deferred_reengagement_cap"

    last = reengagement._last_sent(session, enrollment)  # noqa: SLF001
    attempt = reengagement.claim(session, enrollment, lead, strategy)
    if attempt is None:
        return "skipped_already_attempted"

    template = personalization.build_reengagement_brief(
        session, strategy, lead, channel=last.channel.value)
    message = Message(
        sequence_id=enrollment.sequence_id,
        lead_id=lead.id,
        channel=last.channel,
        step_no=last.step_no + 1,
        template=template,
        variant=last.variant,
        origin=reengagement.ORIGIN,
        status=MessageStatus.SCHEDULED,
        # Same reason as the follow-up: keeps the beat dispatcher off the row
        # for the moment it takes to send it here, so the outcome keeps its tag.
        scheduled_at=now + _FOLLOWUP_DISPATCH_GUARD,
    )
    session.add(message)
    session.flush()
    attempt.message_id = message.id
    attempt.status = "scheduled"
    session.commit()

    result = send_message_impl(session, message.id, now=now,
                               outcome_source=reengagement.OUTCOME_SOURCE)
    attempt.status = "sent" if result == "sent" else "held"
    attempt.detail = result[:200]
    session.commit()
    logger.info("re-engagement for enrollment %s: %s", enrollment_id, result)
    return result


@celery_app.task(name="leadpilot.outreach.check_reengagement_due")
def check_reengagement_due() -> int:
    session = SessionLocal()
    try:
        return len(check_reengagement_due_impl(session))
    finally:
        session.close()


@celery_app.task(name="leadpilot.outreach.send_reengagement", bind=True, max_retries=3)
def send_reengagement_task(self, enrollment_id: str) -> str:
    session = SessionLocal()
    try:
        # P0-5b: the re-engagement brief and the render are metered like any send.
        with _enrollment_scope(session, enrollment_id):
            return send_reengagement_impl(session, uuid.UUID(enrollment_id))
    except Exception as exc:
        session.rollback()
        logger.exception("re-engagement failed for enrollment %s — retrying", enrollment_id)
        raise self.retry(exc=exc, countdown=300)
    finally:
        session.close()
