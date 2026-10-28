"""Per-mailbox deliverability health, with auto-throttle (Part 1, Feature 4).

WHY THIS EXISTS BESIDE deliverability.py. That module scores a sending
DOMAIN: SPF/DKIM/DMARC, blocklists, the account's overall bounce rate. Useful,
but the domain is the wrong grain for acting on. Two mailboxes on one domain
can have very different reputations -- one warmed for a year, one connected
last week and already generating complaints -- and slowing the whole domain
punishes the good one. This module scores the MAILBOX and throttles exactly
the mailbox that is in trouble.

THE FOUR INPUTS
  auth       SPF / DKIM / DMARC for the mailbox's domain (reused verbatim
             from deliverability.dns_auth -- one implementation of the DNS
             reading, not two that can disagree)
  complaints the complaint rate over the window (see the honesty note below)
  bounces    the bounce rate over the window, counted for THIS mailbox
  volume     sends today and over 7 days, against the mailbox's daily cap --
             a mailbox suddenly sending four times its recent average is the
             single most reliable precursor of a reputation problem

HONESTY ABOUT THE COMPLAINT RATE. Vectorae has no feedback-loop (FBL) or
Postmaster Tools integration, so a true "marked as spam" signal is not
available. `complaint_rate` is a documented PROXY: unsubscribes plus replies
asking to stop, over sends in the window. It correlates with complaints and it
is honest about what it is -- `complaint_source` says "proxy" in every payload,
and the UI prints it. When an FBL is connected later, only `complaint_rate`
and that label change; the score, the thresholds and the gate do not.

F03 (Deliverability Autopilot, flag `deliverability_autopilot_enabled`) is
that change: with a fresh Google Postmaster reading for the mailbox's domain,
`complaint_rate` is the real Gmail spam rate and `complaint_source` is
"postmaster" (stored on the row). It also adds explicit brakes -- >= 0.10%
throttles, >= 0.30% pauses -- on top of the score. Flag off: unchanged.

WHAT HAPPENS TO A BAD MAILBOX
  score >= THROTTLE_BELOW      healthy    -- normal cap
  PAUSE_BELOW <= score < ...   throttled  -- cap cut to THROTTLE_FRACTION of
                                            the ramped allowance, minimum
                                            MIN_THROTTLE_CAP. Sending slows;
                                            nothing is dropped.
  score < PAUSE_BELOW          paused     -- no sends from this mailbox.
                                            Messages are DEFERRED, never
                                            cancelled, so nothing is lost.

Resuming a paused mailbox is a HUMAN decision (`resume()`), like the bounce
pause and the blacklist pause before it: a refresh that happens to score
higher must not quietly restart sending from a mailbox that burned.
"""

from __future__ import annotations

import logging
import uuid as uuid_module
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import (
    GmailAccount,
    InboundReply,
    Lead,
    MailboxHealth,
    Message,
    MessageStatus,
    Outcome,
    OutcomeEvent,
    Product,
    Strategy,
)

logger = logging.getLogger(__name__)

HEALTHY, THROTTLED, PAUSED = "healthy", "throttled", "paused"

#: Score bands. Sized against the deductions below so that a mailbox with NO
#: SPF, DMARC or DKIM lands on 45 -- throttled, not paused. Those are DNS
#: records fixable in an afternoon, and stopping outreach outright would cost
#: more than slowing it. A mailbox is only PAUSED by evidence of actual
#: recipient harm: a complaint or bounce rate past the industry danger line
#: takes 65 points on its own, which is below PAUSE_BELOW from full marks.
THROTTLE_BELOW = 70
PAUSE_BELOW = 40

#: While throttled, send this fraction of the normal ramped allowance.
THROTTLE_FRACTION = 0.25
MIN_THROTTLE_CAP = 2

WINDOW_DAYS = 30
VOLUME_DAYS = 7

#: Industry danger lines. Above the first, a mailbox is in trouble; above the
#: second it is doing real damage.
COMPLAINT_WARN = 0.001      # 0.1% -- Google Postmaster's "bad" threshold
COMPLAINT_BAD = 0.003       # 0.3%
BOUNCE_WARN = 0.02          # 2%
BOUNCE_BAD = 0.05           # 5%

#: A day whose volume is this many times the recent daily average is a spike.
SPIKE_MULTIPLE = 3.0
SPIKE_MIN_SENDS = 20        # below this, "3x the average" is noise


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid(value) -> uuid_module.UUID:
    return value if isinstance(value, uuid_module.UUID) else uuid_module.UUID(str(value))


# --------------------------------------------------------------------------
# The mailboxes a user sends from
# --------------------------------------------------------------------------


def mailboxes(db: Session, user_id) -> list[dict]:
    """Every sending identity of one user, keyed the way the send path keys
    them (`messages.sender_ref`).

    Email only for now: LinkedIn has its own per-account limiter
    (app/services/linkedin_limits.py) and WhatsApp's reputation is Meta's
    template approval, so neither has a deliverability score to compute.
    Their refs are already shaped for this table when that changes.
    """
    out = []
    for account in db.execute(select(GmailAccount)
                              .where(GmailAccount.user_id == _uuid(user_id))).scalars():
        address = account.email_address or ""
        out.append({
            "ref": str(account.id),
            "channel": "email",
            "address": address or None,
            "domain": address.rsplit("@", 1)[1].lower() if "@" in address else None,
            "account": account,
        })
    return out


# --------------------------------------------------------------------------
# The four inputs
# --------------------------------------------------------------------------


def _owned_lead_ids(user_id):
    return (select(Lead.id).join(Strategy, Strategy.id == Lead.strategy_id)
            .join(Product, Product.id == Strategy.product_id)
            .where(Product.user_id == _uuid(user_id)))


def sends_in_window(db: Session, mailbox_ref: str, since: datetime) -> int:
    return db.execute(select(func.count(Message.id)).where(
        Message.sender_ref == mailbox_ref,
        Message.sent_at >= since,
        Message.status.in_([MessageStatus.SENT, MessageStatus.BOUNCED]),
    )).scalar_one()


def bounce_rate(db: Session, mailbox_ref: str, now: datetime,
                days: int = WINDOW_DAYS) -> float | None:
    """Bounces for THIS mailbox, not for the account as a whole."""
    since = now - timedelta(days=days)
    sent = sends_in_window(db, mailbox_ref, since)
    if not sent:
        return None
    bounced = db.execute(
        select(func.count(Outcome.id))
        .join(Message, Message.id == Outcome.message_id)
        .where(Message.sender_ref == mailbox_ref,
               Outcome.event == OutcomeEvent.BOUNCED, Outcome.ts >= since)
    ).scalar_one()
    return round(bounced / sent, 4)


def complaint_rate(db: Session, user_id, mailbox_ref: str, now: datetime,
                   days: int = WINDOW_DAYS) -> tuple[float | None, str]:
    """A documented PROXY for the spam-complaint rate. See the module docstring.

    Counts unsubscribes recorded for this mailbox's recipients, plus inbound
    replies the classifier read as a request to stop, over sends in the
    window. Returns (rate, source) where source is always "proxy" until a real
    feedback loop is connected.
    """
    since = now - timedelta(days=days)
    sent = sends_in_window(db, mailbox_ref, since)
    if not sent:
        return None, "proxy"

    unsubscribes = db.execute(
        select(func.count(Outcome.id))
        .join(Message, Message.id == Outcome.message_id)
        .where(Message.sender_ref == mailbox_ref,
               Outcome.event.in_([OutcomeEvent.UNSUBSCRIBED, OutcomeEvent.OPTED_OUT]),
               Outcome.ts >= since)
    ).scalar_one()

    stop_replies = db.execute(
        select(func.count(InboundReply.id))
        .join(Message, Message.id == InboundReply.message_id)
        .where(Message.sender_ref == mailbox_ref,
               InboundReply.classification == "unsubscribe_request",
               InboundReply.created_at >= since)
    ).scalar_one()

    return round((unsubscribes + stop_replies) / sent, 5), "proxy"


def volume(db: Session, mailbox_ref: str, now: datetime) -> dict:
    """Today's sends, the 7-day total, and whether today is a spike."""
    day_start = now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    today = sends_in_window(db, mailbox_ref, day_start)
    week = sends_in_window(db, mailbox_ref, now - timedelta(days=VOLUME_DAYS))
    # The average EXCLUDES today, so today is compared against the baseline it
    # is supposed to be a spike above rather than against itself.
    baseline = max(0, week - today) / VOLUME_DAYS
    spike = bool(today >= SPIKE_MIN_SENDS and baseline > 0
                 and today >= baseline * SPIKE_MULTIPLE)
    return {"today": today, "week": week, "daily_baseline": round(baseline, 2), "spike": spike}


# --------------------------------------------------------------------------
# The score
# --------------------------------------------------------------------------


def score_for(auth: dict, complaints: float | None, bounces: float | None,
              vol: dict, complaint_label: str = "Complaint rate") -> tuple[int, list[str]]:
    """0-100 and the reasons behind every deduction.

    Deliberately the same shape as deliverability.health_score so the two
    numbers are comparable, with complaints and volume added -- the two things
    a domain-level score cannot see. `complaint_label` names the rate's source
    in the reasons (F03: the real Postmaster spam rate is not "complaints").
    """
    score, reasons = 100, []
    if auth.get("spf") is False:
        score -= 25
        reasons.append("No SPF record on this mailbox's domain")
    if auth.get("dmarc") is False:
        score -= 20
        reasons.append("No DMARC record on this mailbox's domain")
    elif auth.get("dmarc_policy") == "none":
        score -= 10
        reasons.append("DMARC policy is p=none (monitoring only)")
    if auth.get("dkim") is False:
        # Softest of the three: this only checks the Google selector, so a
        # miss may mean "signed with another selector", not "unsigned".
        score -= 10
        reasons.append("DKIM not found under the Google selector")

    # The harm deductions are the big ones, and deliberately sized so that a
    # rate past the danger line PAUSES on its own (100 - 65 = 35 < 40) while a
    # fully unauthenticated but quiet mailbox only throttles (100 - 55 = 45).
    if complaints is not None and complaints >= COMPLAINT_BAD:
        score -= 65
        reasons.append(f"{complaint_label} {complaints:.2%} — above the 0.3% danger line")
    elif complaints is not None and complaints >= COMPLAINT_WARN:
        score -= 35
        reasons.append(f"{complaint_label} {complaints:.2%} — above the 0.1% warning line")

    if bounces is not None and bounces >= BOUNCE_BAD:
        score -= 65
        reasons.append(f"Bounce rate {bounces:.1%} — above the 5% danger line")
    elif bounces is not None and bounces >= BOUNCE_WARN:
        score -= 35
        reasons.append(f"Bounce rate {bounces:.1%} — above the 2% warning line")

    if vol.get("spike"):
        score -= 10
        reasons.append(f"Volume spike: {vol['today']} today against a "
                       f"{vol['daily_baseline']}/day baseline")

    return max(0, min(100, score)), reasons


def state_for(score: int) -> str:
    if score < PAUSE_BELOW:
        return PAUSED
    if score < THROTTLE_BELOW:
        return THROTTLED
    return HEALTHY


def throttled_cap(base_cap: int) -> int:
    """The reduced allowance. Never zero — a throttle slows sending, it does
    not silently become a pause."""
    return max(MIN_THROTTLE_CAP, int(base_cap * THROTTLE_FRACTION))


# --------------------------------------------------------------------------
# Refresh
# --------------------------------------------------------------------------


def _row(db: Session, user_id, ref: str) -> MailboxHealth | None:
    return db.execute(select(MailboxHealth).where(
        MailboxHealth.user_id == _uuid(user_id),
        MailboxHealth.mailbox_ref == ref)).scalar_one_or_none()


def refresh(db: Session, user_id, now: datetime | None = None,
            check_dns: bool = True) -> list[dict]:
    """Recompute every mailbox of one user and upsert its row.

    `check_dns=False` skips the DNS lookups (used by tests and by a refresh
    that only needs the volume/complaint half); the auth fields then keep
    whatever the last DNS-backed refresh found, rather than being blanked.
    """
    from app.services import deliverability, deliverability_autopilot  # noqa: PLC0415
    from app.workers import notification_tasks  # noqa: PLC0415

    now = now or _now()
    # F03: with the Autopilot off, nothing below differs from Part 1 Feature 4.
    autopilot = deliverability_autopilot.enabled(db)
    out = []
    for mailbox in mailboxes(db, user_id):
        ref, account = mailbox["ref"], mailbox["account"]
        row = _row(db, user_id, ref)
        auth = {"spf": row.spf_ok if row else None, "dmarc": row.dmarc_ok if row else None,
                "dkim": row.dkim_ok if row else None,
                "dmarc_policy": row.dmarc_policy if row else None}
        if check_dns and mailbox["domain"]:
            if mailbox["domain"] in deliverability.CONSUMER_DOMAINS:
                # A consumer mailbox's reputation is the provider's, not the
                # user's. Scoring its (absent) DNS records against them would
                # throttle a perfectly fine gmail.com sender.
                auth = {"spf": None, "dmarc": None, "dkim": None, "dmarc_policy": None}
            else:
                try:
                    auth = deliverability.dns_auth(mailbox["domain"])
                except Exception as exc:  # noqa: BLE001 -- DNS is not always reachable
                    logger.warning("DNS auth lookup for %s failed: %s", mailbox["domain"], exc)

        complaints, complaint_source = complaint_rate(db, user_id, ref, now)
        complaint_label = "Complaint rate"
        reading = None
        if (autopilot and mailbox["domain"]
                and mailbox["domain"] not in deliverability.CONSUMER_DOMAINS):
            # F03: the real Gmail spam rate replaces the proxy while fresh.
            reading = deliverability_autopilot.postmaster_reading(
                db, user_id, mailbox["domain"], now)
            if reading is not None:
                complaints = reading["spam_rate"]
                complaint_source = deliverability_autopilot.POSTMASTER_SOURCE
                complaint_label = "Gmail spam rate (Google Postmaster)"
        bounces = bounce_rate(db, ref, now)
        vol = volume(db, ref, now)
        score, reasons = score_for(auth, complaints, bounces, vol, complaint_label)

        base_cap = None
        try:
            from app.services import sequence_engine  # noqa: PLC0415

            base_cap = sequence_engine.ramp_allowance(
                db, account, now.astimezone(timezone.utc).date(), autopilot=autopilot)
        except Exception:  # noqa: BLE001
            base_cap = settings.gmail_daily_cap

        new_state = state_for(score)
        braked = False
        if autopilot:
            # F03 brakes: >= 0.10% throttles, >= 0.30% pauses, whatever the
            # rest of the score says. A brake never makes a state better.
            braked_state = deliverability_autopilot.apply_brake(
                new_state, deliverability_autopilot.brake_for(complaints))
            if braked_state != new_state:
                braked = True
                reasons.append(f"Spam-rate brake: {complaint_label.lower()} "
                               f"{complaints:.2%} forces '{braked_state}'")
                new_state = braked_state
        if row is None:
            row = MailboxHealth(user_id=_uuid(user_id), mailbox_ref=ref,
                                channel=mailbox["channel"])
            db.add(row)
        # A mailbox a PERSON paused (or that paused itself) stays paused until
        # a person resumes it -- a refresh that happens to score higher must
        # not quietly restart sending from a mailbox that burned.
        was_paused = row.state == PAUSED
        previous_state = row.state
        if was_paused:
            new_state = PAUSED
        elif new_state == PAUSED:
            row.paused_at = now

        row.address = mailbox["address"]
        row.domain = mailbox["domain"]
        row.score = score
        row.state = new_state
        row.throttle_cap = throttled_cap(base_cap) if new_state == THROTTLED else None
        row.reason = ("; ".join(reasons) or None) if new_state != HEALTHY else None
        row.reasons_json = reasons
        row.spf_ok = auth.get("spf")
        row.dkim_ok = auth.get("dkim")
        row.dmarc_ok = auth.get("dmarc")
        row.dmarc_policy = auth.get("dmarc_policy")
        row.complaint_rate = complaints
        # NULL (= "proxy") with the Autopilot off, exactly as before F03.
        row.complaint_source = complaint_source if autopilot else None
        row.bounce_rate = bounces
        row.sends_today = vol["today"]
        row.sends_7d = vol["week"]
        row.daily_cap = base_cap
        row.checked_at = now
        db.commit()

        if new_state == PAUSED and not was_paused:
            _notify_paused(notification_tasks, user_id, row)
        elif (braked and new_state == THROTTLED
              and previous_state not in (THROTTLED, PAUSED)):
            _notify_braked(notification_tasks, user_id, row)
        out.append({**mailbox_out(row), "complaint_source": complaint_source})
    return out


def _notify_braked(notification_tasks, user_id, row: MailboxHealth) -> None:
    """F03: the 0.10% brake engaged. Never raises, like _notify_paused."""
    try:
        notification_tasks.enqueue_event(
            user_id, "deliverability_warning",
            title=f"Sending slowed from {row.address or row.mailbox_ref}",
            body=(row.reason or "Spam rate above 0.10%.")
                 + " Today's limit is reduced until the rate recovers.",
            deep_link="/settings?tab=deliverability",
            data={"mailbox_ref": row.mailbox_ref},
            webhook_payload={"domain": row.domain, "mailbox_ref": row.mailbox_ref,
                             "score": row.score, "reasons": row.reasons_json or [],
                             "complaint_source": row.complaint_source or "proxy"})
    except Exception:  # noqa: BLE001
        logger.exception("could not notify mailbox brake for %s", row.mailbox_ref)


def _notify_paused(notification_tasks, user_id, row: MailboxHealth) -> None:
    """Never raises: a notification failure must not undo a pause that has
    already been committed."""
    try:
        notification_tasks.enqueue_event(
            user_id, "mailbox_paused",
            title=f"Sending paused from {row.address or row.mailbox_ref}",
            body=(row.reason or "Deliverability health dropped below the safe threshold.")
                 + " Queued messages are held, not cancelled.",
            deep_link="/settings?tab=deliverability",
            data={"mailbox_ref": row.mailbox_ref},
            webhook_payload={"mailbox_ref": row.mailbox_ref, "address": row.address,
                             "score": row.score, "reasons": row.reasons_json or []})
    except Exception:  # noqa: BLE001 -- see the docstring
        logger.exception("could not notify mailbox pause for %s", row.mailbox_ref)


def refresh_all(db: Session, now: datetime | None = None) -> dict:
    """The scheduled sweep (every few hours). One user's failure never stops
    the others."""
    users = db.execute(select(GmailAccount.user_id).distinct()).scalars().all()
    checked = failed = 0
    for user_id in users:
        try:
            refresh(db, user_id, now=now)
            checked += 1
        except Exception:  # noqa: BLE001
            db.rollback()
            failed += 1
            logger.exception("mailbox health refresh failed for user %s", user_id)
    return {"users": len(users), "checked": checked, "failed": failed}


# --------------------------------------------------------------------------
# The send gate
# --------------------------------------------------------------------------


def gate(db: Session, user_id, mailbox_ref: str) -> dict:
    """What the send path is allowed to do with this mailbox right now.

    RETURNS {"state", "cap", "reason"} where `cap` is a ceiling to apply to the
    daily allowance (None = no extra ceiling) and `state` is PAUSED when the
    message must be deferred.

    NEVER RAISES, and defaults to HEALTHY when there is no row: a mailbox
    nothing has scored yet must send normally, not be blocked by the absence
    of a check.
    """
    try:
        row = _row(db, user_id, mailbox_ref)
    except Exception:  # noqa: BLE001 -- see the docstring
        logger.exception("mailbox health lookup failed for %s", mailbox_ref)
        return {"state": HEALTHY, "cap": None, "reason": None}
    if row is None:
        return {"state": HEALTHY, "cap": None, "reason": None}
    return {"state": row.state, "cap": row.throttle_cap, "reason": row.reason}


def resume(db: Session, user_id, mailbox_ref: str, *, actor_user_id=None,
           now: datetime | None = None) -> MailboxHealth:
    """Un-pause a mailbox. A human decision, and recorded as one."""
    row = _row(db, user_id, mailbox_ref)
    if row is None:
        raise LookupError(f"no health row for mailbox {mailbox_ref}")
    row.state = HEALTHY if (row.score or 0) >= THROTTLE_BELOW else THROTTLED
    row.throttle_cap = throttled_cap(row.daily_cap or settings.gmail_daily_cap) \
        if row.state == THROTTLED else None
    row.resumed_at = now or _now()
    row.resumed_by_user_id = _uuid(actor_user_id) if actor_user_id else None
    db.commit()
    return row


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------


def band(score: int | None) -> str:
    if score is None:
        return "unchecked"
    if score >= THROTTLE_BELOW:
        return "good"
    if score >= PAUSE_BELOW:
        return "at_risk"
    return "bad"


def mailbox_out(row: MailboxHealth) -> dict:
    return {
        "mailbox_ref": row.mailbox_ref,
        "channel": row.channel,
        "address": row.address,
        "domain": row.domain,
        "score": row.score,
        "band": band(row.score),
        "state": row.state,
        "throttle_cap": row.throttle_cap,
        "daily_cap": row.daily_cap,
        "reason": row.reason,
        "reasons": row.reasons_json or [],
        "auth": {"spf": row.spf_ok, "dkim": row.dkim_ok, "dmarc": row.dmarc_ok,
                 "dmarc_policy": row.dmarc_policy},
        "complaint_rate": row.complaint_rate,
        # Always present, always honest: "proxy" unless F03 stored the real
        # Postmaster spam rate on this row. See the module docstring.
        "complaint_source": row.complaint_source or "proxy",
        "bounce_rate": row.bounce_rate,
        "sends_today": row.sends_today,
        "sends_7d": row.sends_7d,
        "paused_at": row.paused_at.isoformat() if row.paused_at else None,
        "resumed_at": row.resumed_at.isoformat() if row.resumed_at else None,
        "checked_at": row.checked_at.isoformat() if row.checked_at else None,
    }


def status(db: Session, user_id) -> dict:
    """Every mailbox of one user, scored or not.

    A connected mailbox with no row yet is listed as `unchecked` rather than
    omitted -- "we have not looked" is a state the settings page must show.
    """
    rows = {r.mailbox_ref: r for r in db.execute(
        select(MailboxHealth).where(MailboxHealth.user_id == _uuid(user_id))).scalars()}
    items = []
    for mailbox in mailboxes(db, user_id):
        row = rows.get(mailbox["ref"])
        if row is not None:
            items.append(mailbox_out(row))
        else:
            items.append({"mailbox_ref": mailbox["ref"], "channel": mailbox["channel"],
                          "address": mailbox["address"], "domain": mailbox["domain"],
                          "score": None, "band": "unchecked", "state": HEALTHY,
                          "throttle_cap": None, "daily_cap": None, "reason": None,
                          "reasons": [], "auth": {"spf": None, "dkim": None, "dmarc": None,
                                                  "dmarc_policy": None},
                          "complaint_rate": None, "complaint_source": "proxy",
                          "bounce_rate": None, "sends_today": 0, "sends_7d": 0,
                          "paused_at": None, "resumed_at": None, "checked_at": None})
    return {
        "thresholds": {"throttle_below": THROTTLE_BELOW, "pause_below": PAUSE_BELOW,
                       "throttle_fraction": THROTTLE_FRACTION},
        "complaint_note": ("Complaint rate is a proxy (unsubscribes plus replies asking "
                           "to stop) — Vectorae has no feedback-loop feed."),
        "mailboxes": items,
    }
