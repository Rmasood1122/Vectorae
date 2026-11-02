"""Feature 2 background work: classify one inbound reply.

  process_inbound_reply(reply_id)   dispatched the moment an InboundReply row
                                    is committed, from every channel that
                                    creates one (email polling, the WhatsApp
                                    webhook). LinkedIn replies happen in the
                                    user's own inbox; the user marks the task
                                    "replied" (app/services/linkedin_tasks.py)

On the `outreach` queue, with every other reply-path task: this is the same
work as routing a reply, it just answers a different question about it.

IDEMPOTENT BY CONSTRUCTION. A reply that already carries `classified_at` is
skipped -- a duplicate webhook delivery, a Celery redelivery after a lost
worker, or a manual re-enqueue costs nothing and cannot overwrite a draft the
user may already be editing. `force=True` is the deliberate override.

WHY THIS IS A TASK AND NOT INLINE. Classifying a reply is two model calls in
the worst case. Running that inside the webhook handler would hold the request
open for seconds and make Meta retry on timeout -- creating duplicate
replies to classify. The webhook stores the reply, routes it the way it always
did, and hands this off.
"""

import logging

from sqlalchemy.orm import Session

from app.db.base import SessionLocal
from app.db.models import InboundReply
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


def process_inbound_reply_impl(session: Session, reply_id, force: bool = False) -> dict:
    """Classify one reply and persist the result.

    WHAT IT RETURNS. {"status": "classified"|"skipped"|"not_found"|"failed",
    "category": str|None}.

    WHAT IT NEVER RAISES. Anything reachable from a reply. classify_reply and
    apply both swallow their own failures; this adds the already-classified
    guard and turns a missing row into a status rather than an exception, so a
    retry of a deleted reply does not spin.
    """
    import uuid  # noqa: PLC0415

    from app.services import reply_intelligence  # noqa: PLC0415

    try:
        reply = session.get(InboundReply, uuid.UUID(str(reply_id)))
    except (ValueError, TypeError):
        logger.warning("reply intelligence: %r is not a reply id", reply_id)
        return {"status": "not_found", "category": None}
    if reply is None:
        return {"status": "not_found", "category": None}
    # P0-5b: every model call below is metered to the account that owns the
    # reply -- counted, never capped (it drives STOP / not-now handling).
    from app.services import usage_meter  # noqa: PLC0415

    with usage_meter.user_scope(session, _reply_owner(session, reply), "reply_handling"):
        # Part 1 Feature 1: the positive-reply label is a SEPARATE question with
        # its own idempotency marker (intent_at), so a reply classified before
        # that feature existed still gets a label on the next pass without
        # regenerating the draft the user may already be editing.
        from app.services import reply_intent  # noqa: PLC0415

        intent = reply_intent.classify_and_apply(session, reply, force=force)

        # Part 1 Feature 7: a "not now" is a DATE, not a dead end. Turning it into
        # a plan happens here, right after the label is decided, and never raises
        # -- a prospect who said "not now" must still be stored and routed exactly
        # as before if the plan cannot be made.
        from app.services import reengagement_memory  # noqa: PLC0415

        plan = reengagement_memory.plan_for_reply(session, reply)

    # F04 Reply Copilot: a grounded, claim-verified answer queued for a person.
    # Behind reply_copilot_enabled (default off): off = one settings read and a
    # return value identical to before. Its own idempotency (one draft per
    # reply, UNIQUE), so a redelivery or force=True never replaces a draft a
    # person may be editing. Never raises. Deliberately OUTSIDE the
    # reply_handling scope: it opens its own "reply_copilot" scope, which is
    # account-capped, and a nested scope would join (and be counted as) the
    # uncapped reply_handling one.
    from app.services import reply_copilot  # noqa: PLC0415

    copilot = (reply_copilot.draft_for_reply(session, reply)
               if reply_copilot.enabled(session) else None)

    def _out(result: dict) -> dict:
        if copilot is not None:
            result["copilot"] = copilot
        return result

    if reply.classified_at is not None and not force:
        return _out({"status": "skipped", "category": reply.reply_category,
                     "intent": intent["label"],
                     "reengagement_plan_id": str(plan.id) if plan else None})

    with usage_meter.user_scope(session, _reply_owner(session, reply), "reply_handling"):
        result = reply_intelligence.classify_reply(session, reply.id)
        if result.get("error"):
            return _out({"status": "failed", "category": None, "intent": intent["label"],
                         "reengagement_plan_id": str(plan.id) if plan else None})
        reply_intelligence.apply(session, reply, result)
    return _out({"status": "classified", "category": reply.reply_category,
                 "intent": intent["label"],
                 "reengagement_plan_id": str(plan.id) if plan else None})

def _reply_owner(session: Session, reply):
    """The account a reply belongs to: its lead's owner, else the Gmail
    account it arrived in (unmatched email). None when neither is known."""
    try:
        if reply.lead_id is not None:
            from app.db.models import Lead  # noqa: PLC0415
            from app.services import notifications  # noqa: PLC0415

            lead = session.get(Lead, reply.lead_id)
            if lead is not None:
                return notifications.owner_of_lead(session, lead)
        import uuid  # noqa: PLC0415

        from app.db.models import GmailAccount  # noqa: PLC0415

        account = session.get(GmailAccount, uuid.UUID(str(reply.account_ref)))
        return account.user_id if account is not None else None
    except Exception:  # noqa: BLE001 -- attribution must never block a reply
        return None


@celery_app.task(name="app.workers.reply_tasks.process_inbound_reply")
def process_inbound_reply(reply_id: str, force: bool = False) -> dict:
    """Celery entry point. Safe to retry; see the module docstring."""
    session = SessionLocal()
    try:
        return process_inbound_reply_impl(session, reply_id, force=force)
    finally:
        session.close()


def enqueue(reply_id) -> bool:
    """Publish process_inbound_reply for `reply_id`.

    WHAT IT RETURNS. True when the job was published, False when the broker
    refused it.

    WHAT IT NEVER RAISES. Anything. This is called from inside webhook
    handlers that have already committed the reply and already routed it; a
    broker that is down must not turn a successfully stored reply into a 500
    and a provider retry. The reply simply goes unclassified.
    """
    try:
        process_inbound_reply.apply_async(args=[str(reply_id)], retry=False)
        return True
    except Exception:  # noqa: BLE001 -- see the docstring
        logger.exception("reply intelligence: could not enqueue reply %s", reply_id)
        return False


# --------------------------------------------------------------------------
# F04 Reply Copilot: send an approved answer THROUGH the chokepoint
# --------------------------------------------------------------------------


def send_reply_draft_impl(session: Session, message_id) -> str:
    """Send one approved Reply Copilot message via
    outreach_tasks.send_message_impl -- every gate applies (suppression,
    exclusions, Bounce Shield, consent, send window, caps, send review,
    forbidden phrases, one-click unsubscribe headers). Records the result on
    the draft. Inside the same `outreach` usage scope as every send."""
    import uuid  # noqa: PLC0415

    from app.db.models import Message, Sequence, Strategy  # noqa: PLC0415
    from app.services import reply_copilot, usage_meter  # noqa: PLC0415
    from app.workers import outreach_tasks  # noqa: PLC0415

    mid = uuid.UUID(str(message_id))
    message = session.get(Message, mid)
    sequence = session.get(Sequence, message.sequence_id) if message else None
    strategy = session.get(Strategy, sequence.strategy_id) if sequence else None
    with usage_meter.owner_scope(session, strategy, "outreach"):
        result = outreach_tasks.send_message_impl(
            session, mid, outcome_source=reply_copilot.OUTCOME_SOURCE)
    reply_copilot.after_send_attempt(session, mid, result)
    return result


@celery_app.task(name="app.workers.reply_tasks.send_reply_draft", bind=True, max_retries=3)
def send_reply_draft(self, message_id: str) -> str:
    """Celery entry point. Retried on a transient failure like every send
    (send_message_impl leaves the row SCHEDULED, and its idempotency guards
    make a retry unable to double-send)."""
    session = SessionLocal()
    try:
        return send_reply_draft_impl(session, message_id)
    except Exception as exc:
        session.rollback()
        logger.exception("reply copilot: send failed for message %s -- retrying", message_id)
        raise self.retry(exc=exc, countdown=60)
    finally:
        session.close()


def enqueue_send(message_id) -> bool:
    """Publish send_reply_draft. Never raises: if the broker refuses, the
    message is still SCHEDULED and the beat dispatcher sends it once its
    dispatch guard has passed."""
    try:
        send_reply_draft.apply_async(args=[str(message_id)], retry=False)
        return True
    except Exception:  # noqa: BLE001 -- see the docstring
        logger.exception("reply copilot: could not enqueue send of message %s", message_id)
        return False


@celery_app.task(name="app.workers.reply_tasks.run_reengagement_memory")
def run_reengagement_memory() -> dict:
    """Part 1 Feature 7: the daily sweep over dated return visits.

    DAILY, not hourly: the unit of this feature is a day ("come back in
    March"), and nothing is made better by noticing at 03:00 rather than at
    06:00. It runs early enough that a plan coming due today is in the user's
    list before they start work.
    """
    from app.services import reengagement_memory  # noqa: PLC0415

    session = SessionLocal()
    try:
        return reengagement_memory.run_due(session)
    finally:
        session.close()
