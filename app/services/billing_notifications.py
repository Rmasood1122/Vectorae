"""Billing notifications — credit limits, purchases, receipts.

Sends emails and push notifications for:
- Credit limit reached (meeting blocked)
- Credit purchase confirmation with receipt
- Credit purchase failed
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.models import User
from app.services.email_sender import send_email, EmailSendError
from app.services.notification_service import NotificationService

logger = logging.getLogger(__name__)


async def notify_credit_limit_hit(user: User, amount_blocked_cents: int,
                                   limit_cents: int) -> None:
    """Send email + push when a meeting is blocked by credit limit."""
    try:
        subject = "You've reached your Vectorae monthly credit limit"
        body_text = f"""Hi {user.full_name or 'there'},

A meeting booking hit your monthly Vectorae credit limit. You've used ${limit_cents/100:.2f} this month.

To continue booking meetings, purchase additional credits:
https://app.vectorae.com/usage?action=purchase_credits

Amount blocked: ${amount_blocked_cents/100:.2f}
Your limit: ${limit_cents/100:.2f}

Questions? Reply to this email or visit: https://help.vectorae.com/billing

— Vectorae Team
"""

        # Send email
        try:
            await asyncio.to_thread(
                send_email,
                to_email=user.email,
                subject=subject,
                body_text=body_text,
            )
            logger.info("credit limit notification sent to %s", user.id)
        except EmailSendError as exc:
            logger.warning("credit limit email failed for user %s: %s", user.id, exc)

        # Send push
        try:
            await NotificationService.send_to_user(
                user_id=str(user.id),
                title="Credit limit reached",
                body=f"Your ${limit_cents/100:.2f} credit limit is reached. Purchase more to continue.",
                data={"action": "purchase_credits"}
            )
        except Exception as exc:
            logger.warning("credit limit push failed for user %s: %s", user.id, exc)

    except Exception as exc:
        logger.error("credit limit notification failed for user %s: %s", user.id, exc)


async def notify_credit_purchase_success(user: User, amount_cents: int,
                                        new_limit_cents: int) -> None:
    """Send email receipt + push when credits are successfully purchased."""
    try:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        subject = f"Vectorae credit purchase confirmation – ${amount_cents/100:.2f}"
        body_text = f"""Hi {user.full_name or 'there'},

Your Vectorae credit purchase was successful!

========================================
RECEIPT
========================================
Amount: ${amount_cents/100:.2f}
Date: {timestamp}
New monthly limit: ${new_limit_cents/100:.2f}
========================================

Your credits are active immediately. You can now book more meetings.

View your usage & transactions: https://app.vectorae.com/usage

Questions? Reply to this email or visit: https://help.vectorae.com/billing

— Vectorae Team
"""

        # Send email receipt
        try:
            await asyncio.to_thread(
                send_email,
                to_email=user.email,
                subject=subject,
                body_text=body_text,
            )
            logger.info("credit purchase receipt sent to %s", user.id)
        except EmailSendError as exc:
            logger.warning("credit purchase email failed for user %s: %s", user.id, exc)

        # Send push confirmation
        try:
            await NotificationService.send_to_user(
                user_id=str(user.id),
                title="Credits purchased",
                body=f"${amount_cents/100:.2f} added. New limit: ${new_limit_cents/100:.2f}",
                data={"action": "view_usage"}
            )
        except Exception as exc:
            logger.warning("credit purchase push failed for user %s: %s", user.id, exc)

    except Exception as exc:
        logger.error("credit purchase notification failed for user %s: %s", user.id, exc)


async def notify_credit_purchase_failed(user: User, amount_cents: int, error_message: str) -> None:
    """Send email when a credit purchase fails."""
    try:
        subject = "Vectorae credit purchase failed"
        body_text = f"""Hi {user.full_name or 'there'},

Your Vectorae credit purchase for ${amount_cents/100:.2f} could not be completed.

Error: {error_message[:500]}

Please try again, or contact support if the problem persists:
https://help.vectorae.com/support

— Vectorae Team
"""

        # Send email notification
        try:
            await asyncio.to_thread(
                send_email,
                to_email=user.email,
                subject=subject,
                body_text=body_text,
            )
            logger.info("credit purchase failure email sent to %s", user.id)
        except EmailSendError as exc:
            logger.warning("credit purchase failure email failed for user %s: %s", user.id, exc)

    except Exception as exc:
        logger.error("credit purchase failure notification failed for user %s: %s", user.id, exc)
