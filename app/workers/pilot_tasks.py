"""F09 30-Day Pilot Mode -- the hourly sweep (docs/features/f09-pilot-mode.md).

  run_pilot_sweep   hourly at :55

Ends pilots whose 30 days are over (always with a verdict), sends each active
pilot's weekly scorecard once per week, and sends the once-only verdict
notice. All email goes to the account owner through Vectorae's transactional
gateway -- never to a prospect, never through the outreach send path.

On the `default` queue: short database work plus one transactional email at
most per pilot, like the other account-mail work. A no-op while
`pilot_mode_enabled` is off.

SAFE TO RETRY / OVERLAP. Scorecards are claimed against UNIQUE (pilot_id,
week) before sending; ending is a compare-and-set on status; the verdict
notice is a compare-and-set on its attempt count.
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.db.base import SessionLocal
from app.services import pilot_mode
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


def run_pilot_sweep_impl(session: Session, now: datetime | None = None) -> dict:
    return pilot_mode.sweep(session, now=now)


@celery_app.task(name="app.workers.pilot_tasks.run_pilot_sweep")
def run_pilot_sweep() -> dict:
    session = SessionLocal()
    try:
        result = run_pilot_sweep_impl(session)
        logger.info("pilot sweep: %s", result)
        return result
    finally:
        session.close()
