"""Phone verification by SMS one-time code (Section C).

    send_code(db, user, phone)   -> issues a 6-digit code, texts it
    verify_code(db, user, code)  -> proves it; marks users.phone_verified

WHAT IS STORED. An HMAC-SHA256 of the code keyed by the JWT secret and bound
to (user, number) -- never the code. Binding the number in means a code sent to
one number cannot verify a different one the user types afterwards.

LIMITS, THREE LAYERS, EACH FOR A DIFFERENT ATTACK
  * resend cooldown (PHONE_OTP_RESEND_COOLDOWN_SECONDS) per account -- the
    double-click / impatient-user case, answered with retry_after;
  * RATE_LIMIT_OTP_SEND per account AND per number per hour (enforced in the
    API, like /auth/login's two keys) -- SMS pumping spreads across accounts
    at one premium number, or one account across many numbers;
  * PHONE_OTP_MAX_ATTEMPTS wrong guesses kill a code, and RATE_LIMIT_OTP_VERIFY
    bounds guessing across resends.

ONE NUMBER, ONE VERIFIED ACCOUNT. A number already proved by another account
is refused. Without it, one SIM verifies an unlimited farm of accounts, which
is the thing phone verification is here to stop.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import PhoneVerificationCode, User
from app.integrations import sms
from app.services import identity

_E164 = re.compile(r"^\+[1-9]\d{7,14}$")
_STRIP = re.compile(r"[\s\-().]")

# verify_code outcomes
VERIFIED = "verified"
INVALID = "invalid"
EXPIRED = "expired"
LOCKED = "locked"
NO_CODE = "no_code"


class PhoneVerificationError(ValueError):
    """User-facing refusal. `code` is a stable machine-readable reason."""

    def __init__(self, message: str, code: str, status_code: int = 422,
                 retry_after: int | None = None):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.retry_after = retry_after


@dataclass
class SendResult:
    phone_number: str
    expires_at: datetime
    resend_available_at: datetime


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def normalize_phone(raw: str | None) -> str:
    """E.164 or PhoneVerificationError. Accepts spaces, dashes, dots,
    parentheses and a leading 00 in place of +."""
    value = _STRIP.sub("", (raw or "").strip())
    if value.startswith("00"):
        value = "+" + value[2:]
    if not _E164.match(value):
        raise PhoneVerificationError(
            "Enter your number in international format, e.g. +14155550123.",
            code="invalid_phone")
    return value


def _digest(user_id, phone: str, code: str) -> str:
    from app.services.auth import _secret  # noqa: PLC0415 -- the one signing key

    message = f"{user_id}:{phone}:{code}".encode()
    return hmac.new(_secret().encode(), message, hashlib.sha256).hexdigest()


def _latest(db: Session, user: User) -> PhoneVerificationCode | None:
    return db.execute(
        select(PhoneVerificationCode)
        .where(PhoneVerificationCode.user_id == user.id)
        .order_by(PhoneVerificationCode.created_at.desc(), PhoneVerificationCode.id.desc())
    ).scalars().first()


def send_code(db: Session, user: User, raw_phone: str,
              now: datetime | None = None) -> SendResult:
    now = now or _now()
    phone = normalize_phone(raw_phone)

    taken = db.execute(
        select(User.id).where(User.phone_number == phone, User.phone_verified.is_(True),
                              User.id != user.id)
    ).first()
    if taken is not None:
        raise PhoneVerificationError(
            "That number is already verified on another account.",
            code="phone_in_use", status_code=409)

    cooldown = timedelta(seconds=max(int(settings.phone_otp_resend_cooldown_seconds), 0))
    latest = _latest(db, user)
    if latest is not None and latest.consumed_at is None:
        available = _aware(latest.created_at) + cooldown
        if available > now:
            raise PhoneVerificationError(
                "A code was just sent. Wait a moment before asking for another.",
                code="resend_cooldown", status_code=429,
                retry_after=max(int((available - now).total_seconds()), 1))

    # Kill every outstanding code: only the newest one may ever verify, or an
    # intercepted earlier SMS would stay live for its whole TTL.
    for row in db.execute(
        select(PhoneVerificationCode).where(PhoneVerificationCode.user_id == user.id,
                                            PhoneVerificationCode.consumed_at.is_(None))
    ).scalars():
        row.expires_at = min(_aware(row.expires_at), now)

    code = f"{secrets.randbelow(1_000_000):06d}"
    ttl = timedelta(seconds=max(int(settings.phone_otp_ttl_seconds), 60))
    row = PhoneVerificationCode(user_id=user.id, phone_number=phone,
                                code_hash=_digest(user.id, phone, code),
                                expires_at=now + ttl, created_at=now)
    db.add(row)
    # Committed BEFORE the send, so the code is durable by the time it can be
    # typed in (the email-verification ordering).
    db.commit()

    minutes = max(int(ttl.total_seconds() // 60), 1)
    try:
        sms.send_sms(phone, f"Your Vectorae verification code is {code}. "
                            f"It expires in {minutes} minutes. Never share this code.")
    except sms.SmsSendError as exc:
        row.expires_at = now  # an undeliverable code must not stay live
        db.commit()
        raise PhoneVerificationError(
            "We could not send a text to that number right now. Check the number "
            "and try again.", code="sms_failed", status_code=502) from exc

    identity.record_event(db, event="phone_code_sent", user=user,
                          details={"phone": sms.mask_phone(phone)})
    db.commit()
    return SendResult(phone_number=phone, expires_at=now + ttl,
                      resend_available_at=now + cooldown)


def verify_code(db: Session, user: User, code: str,
                now: datetime | None = None) -> tuple[str, int | None]:
    """(outcome, attempts_remaining). Commits every outcome."""
    now = now or _now()
    candidate = re.sub(r"\D", "", code or "")
    row = _latest(db, user)
    if row is None or row.consumed_at is not None:
        return NO_CODE, None
    max_attempts = max(int(settings.phone_otp_max_attempts), 1)
    if row.attempts >= max_attempts:
        return LOCKED, 0
    if _aware(row.expires_at) <= now:
        return EXPIRED, None

    expected = row.code_hash
    actual = _digest(user.id, row.phone_number, candidate) if len(candidate) == 6 else ""
    if not actual or not hmac.compare_digest(expected, actual):
        row.attempts += 1
        remaining = max_attempts - row.attempts
        db.commit()
        return (LOCKED, 0) if remaining <= 0 else (INVALID, remaining)

    # The same guard as send: a race where another account verified this
    # number between send and verify must still not produce two owners.
    taken = db.execute(
        select(User.id).where(User.phone_number == row.phone_number,
                              User.phone_verified.is_(True), User.id != user.id)
    ).first()
    if taken is not None:
        row.consumed_at = now
        db.commit()
        raise PhoneVerificationError("That number is already verified on another account.",
                                     code="phone_in_use", status_code=409)

    row.consumed_at = now
    user.phone_number = row.phone_number
    user.phone_verified = True
    user.phone_verified_at = now
    identity.record_event(db, event="phone_verified", user=user,
                          details={"phone": sms.mask_phone(row.phone_number)})
    db.commit()
    return VERIFIED, None
