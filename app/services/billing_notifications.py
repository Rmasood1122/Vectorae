"""Async notification handlers for billing events: credits, purchases, adjustments."""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.orm import Session

from app.db.models import User
from app.services import notifications
from app.services import email_sender

logger = logging.getLogger(__name__)


async def notify_credit_limit_hit(db: Session, user: User, limit_cents: int,
                                  amount_needed_cents: int) -> None:
    """Notify user when a meeting is blocked by credit limit."""
    if not user.email:
        logger.warning(f"User {user.id} has no email; skipping notification")
        return

    formatted_limit = f"${limit_cents / 100:.2f}"
    formatted_needed = f"${amount_needed_cents / 100:.2f}"
    subject = "Credit Limit Reached — Purchase More to Continue"
    text = (
        f"Hello {user.name or 'there'},\n\n"
        f"Your booking was blocked because you reached your monthly credit limit of {formatted_limit}.\n"
        f"You need at least {formatted_needed} to proceed.\n\n"
        f"Visit https://app.calendarharvest.com/usage to purchase more credits.\n"
    )
    html = f"""
    <p>Hello {user.name or 'there'},</p>
    <p>Your booking was blocked because you reached your monthly credit limit of <strong>{formatted_limit}</strong>.</p>
    <p>You need at least <strong>{formatted_needed}</strong> to proceed.</p>
    <p><a href="https://app.calendarharvest.com/usage">Purchase additional credits</a></p>
    """

    try:
        await asyncio.to_thread(email_sender.send_email, to=user.email, subject=subject, html=html, text=text)
    except Exception as e:
        logger.exception(f"Failed to send credit limit email to {user.id}: {e}")

    try:
        await notifications.NotificationService.send_to_user(
            db, user.id, title="Credit Limit Reached", body=f"Need {formatted_needed} to proceed.",
            notification_type="billing", action_url="/usage"
        )
    except Exception as e:
        logger.exception(f"Failed to send push to {user.id}: {e}")


async def notify_credit_purchase_success(db: Session, user: User, amount_cents: int,
                                        new_limit_cents: int) -> None:
    """Notify user when credit purchase completes successfully."""
    if not user.email:
        logger.warning(f"User {user.id} has no email; skipping notification")
        return

    formatted_amount = f"${amount_cents / 100:.2f}"
    formatted_limit = f"${new_limit_cents / 100:.2f}"
    subject = "Credits Purchased Successfully"
    text = (
        f"Hello {user.name or 'there'},\n\n"
        f"Your purchase of {formatted_amount} has been processed successfully.\n"
        f"Your account now has {formatted_limit} in monthly credits.\n\n"
        f"Visit https://app.calendarharvest.com/usage to view your balance.\n"
    )
    html = f"""
    <p>Hello {user.name or 'there'},</p>
    <p>Your purchase of <strong>{formatted_amount}</strong> has been processed successfully.</p>
    <p>Your account now has <strong>{formatted_limit}</strong> in monthly credits.</p>
    <p><a href="https://app.calendarharvest.com/usage">View your balance</a></p>
    """

    try:
        await asyncio.to_thread(email_sender.send_email, to=user.email, subject=subject, html=html, text=text)
    except Exception as e:
        logger.exception(f"Failed to send purchase success email to {user.id}: {e}")

    try:
        await notifications.NotificationService.send_to_user(
            db, user.id, title="Credits Added", body=f"New balance: {formatted_limit}",
            notification_type="billing", action_url="/usage"
        )
    except Exception as e:
        logger.exception(f"Failed to send push to {user.id}: {e}")


async def notify_credit_purchase_failed(db: Session, user: User, amount_cents: int,
                                       error_reason: str) -> None:
    """Notify user when credit purchase fails."""
    if not user.email:
        logger.warning(f"User {user.id} has no email; skipping notification")
        return

    formatted_amount = f"${amount_cents / 100:.2f}"
    subject = "Credit Purchase Failed"
    text = (
        f"Hello {user.name or 'there'},\n\n"
        f"Your {formatted_amount} credit purchase could not be processed.\n"
        f"Reason: {error_reason}\n\n"
        f"Please try again or contact support.\n"
    )
    html = f"""
    <p>Hello {user.name or 'there'},</p>
    <p>Your {formatted_amount} credit purchase could not be processed.</p>
    <p><strong>Reason:</strong> {error_reason}</p>
    <p>Please try again or contact support.</p>
    """

    try:
        await asyncio.to_thread(email_sender.send_email, to=user.email, subject=subject, html=html, text=text)
    except Exception as e:
        logger.exception(f"Failed to send failure email to {user.id}: {e}")

    try:
        await notifications.NotificationService.send_to_user(
            db, user.id, title="Credit Purchase Failed", body="Please try again.",
            notification_type="billing", action_url="/usage"
        )
    except Exception as e:
        logger.exception(f"Failed to send push to {user.id}: {e}")


async def notify_credit_adjustment(db: Session, user: User, old_limit_cents: int,
                                   new_limit_cents: int, reason: str) -> None:
    """Notify user when admin adjusts their credit limit."""
    if not user.email:
        logger.warning(f"User {user.id} has no email; skipping notification")
        return

    old_formatted = f"${old_limit_cents / 100:.2f}"
    new_formatted = f"${new_limit_cents / 100:.2f}"
    adjustment = new_limit_cents - old_limit_cents
    adjustment_formatted = f"${abs(adjustment) / 100:.2f}"
    change_type = "increased" if adjustment > 0 else "decreased"

    subject = "Your Credits Have Been Adjusted"
    text = (
        f"Hello {user.name or 'there'},\n\n"
        f"Your monthly credit limit has been {change_type} by {adjustment_formatted}.\n"
        f"Old balance: {old_formatted}\n"
        f"New balance: {new_formatted}\n\n"
        f"Reason: {reason}\n"
    )
    html = f"""
    <p>Hello {user.name or 'there'},</p>
    <p>Your monthly credit limit has been <strong>{change_type}</strong> by {adjustment_formatted}.</p>
    <ul>
        <li>Old balance: {old_formatted}</li>
        <li>New balance: {new_formatted}</li>
    </ul>
    <p><strong>Reason:</strong> {reason}</p>
    """

    try:
        await asyncio.to_thread(email_sender.send_email, to=user.email, subject=subject, html=html, text=text)
    except Exception as e:
        logger.exception(f"Failed to send adjustment email to {user.id}: {e}")

    try:
        await notifications.NotificationService.send_to_user(
            db, user.id, title="Credits Adjusted",
            body=f"Changed from {old_formatted} to {new_formatted}.",
            notification_type="billing", action_url="/usage"
        )
    except Exception as e:
        logger.exception(f"Failed to send push to {user.id}: {e}")
