"""The `notifications` queue: fan-out of event_bus events off the request path.

WHY A QUEUE OF ITS OWN
Push, Slack and webhook fan-out are small, latency-sensitive jobs -- a
reminder that arrives twenty minutes late is a reminder for a call already in
progress. On `default` they would queue behind the learning loop; on
`pipeline` behind a 144-step strategy build. A dedicated queue with its own
worker concurrency keeps them prompt whatever else the system is doing.

enqueue_event() is what request handlers call. If the broker is unreachable it
falls back to delivering inline rather than dropping the event: a notification
is best effort, but "Redis blipped" is not a reason to lose one.
"""

from __future__ import annotations

import logging
import uuid

from app.db.base import SessionLocal
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


def _emit(user_id: str, event: str, kwargs: dict) -> dict:
    from app.services import event_bus  # noqa: PLC0415

    session = SessionLocal()
    try:
        return event_bus.emit(session, uuid.UUID(user_id), event, **kwargs)
    finally:
        session.close()


@celery_app.task(name="app.workers.notification_tasks.emit_event",
                 bind=True, max_retries=2)
def emit_event(self, user_id: str, event: str, kwargs: dict) -> dict:
    try:
        return _emit(user_id, event, kwargs)
    except Exception as exc:
        logger.exception("event %s for %s failed -- retrying", event, user_id)
        raise self.retry(exc=exc, countdown=30)


@celery_app.task(name="app.workers.notification_tasks.signup_alert",
                 bind=True, max_retries=2)
def signup_alert(self, event_id: str) -> dict:
    from app.services import signup_watch  # noqa: PLC0415

    session = SessionLocal()
    try:
        return signup_watch.alert_admins(session, event_id)
    except Exception as exc:
        logger.exception("sign-up alert %s failed -- retrying", event_id)
        raise self.retry(exc=exc, countdown=30)
    finally:
        session.close()


def enqueue_signup_alert(event_id) -> bool:
    """Queue the admin alert for one sign-up. Never raises; tests monkeypatch.

    Broker down: nothing is sent inline. The country lookup and the mail
    would then run on the sign-up request itself, and the sign-up is already
    listed under Admin > Sign-ups whatever happens here."""
    try:
        signup_alert.apply_async(args=[str(event_id)], retry=False)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not queue the sign-up alert for %s: %s", event_id,
                       type(exc).__name__)
        return False


def enqueue_event(user_id, event: str, **kwargs) -> bool:
    """Queue one event_bus.emit. Returns True when queued, False when it had
    to be delivered inline. Never raises. Tests monkeypatch this."""
    try:
        emit_event.apply_async(args=[str(user_id), event, kwargs], retry=False)
        return True
    except Exception as exc:  # noqa: BLE001 -- broker down: deliver inline
        logger.warning("could not queue event %s (%s) -- delivering inline",
                       event, exc)
        try:
            _emit(str(user_id), event, kwargs)
        except Exception as inline_exc:  # noqa: BLE001
            logger.warning("inline delivery of %s failed: %s", event, inline_exc)
        return False
