"""The one-time onboarding email, sent once an account's address is verified.

FLOW
    user clicks the verification link -> /auth/verify commits email_verified
    -> notification_tasks.enqueue_onboarding_email(user_id)
    -> worker: send_once(db, user_id)

Before this module a new account received exactly one email -- "Verify your
Vectorae account" -- and nothing after it. This is the second and last
automatic email of signup: what to do next, in order.

ONCE PER ACCOUNT, ENFORCED BY THE DATABASE
`users.onboarding_email_sent_at` (migration 0098) is claimed with a single
conditional UPDATE ... WHERE onboarding_email_sent_at IS NULL before anything
is sent. Two workers, a Celery retry, or a link opened twice can each try; only
the one whose UPDATE changed a row sends. If the transport then fails, the
claim is released so the retry can take it again -- an account is never marked
"sent" for a message that did not go out.

WHO IS NEVER MAILED
Unverified accounts (the address is unproven), suspended accounts, and
passwordless pre-auth rows nobody has claimed. All three answer a status
instead of raising: they are ordinary outcomes, not errors, and the worker must
not retry them.

WHAT IT SAYS
Only the steps this account still has to take: the identity and phone checks
if it is held by them, choosing a plan if the deployment requires one and it
has none, then the product steps. It is a service message about an account the
recipient just created and verified -- no offers, no prices, no tracking pixel,
and no marketing (CAN-SPAM "transactional or relationship" content). The
links are the same in-app routes as the onboarding checklist
(app/api/onboarding.py STEP_ACTIONS); tests/test_onboarding_email.py keeps
them in step.
"""

from __future__ import annotations

import html as _html
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import User
from app.services.email_sender import send_email

logger = logging.getLogger(__name__)

__all__ = [
    "send_once",
    "render",
    "SENT",
    "ALREADY_SENT",
    "NOT_ELIGIBLE",
    "NO_SUCH_USER",
]

# send_once() outcomes.
SENT = "sent"
ALREADY_SENT = "already_sent"
NOT_ELIGIBLE = "not_eligible"
NO_SUCH_USER = "no_such_user"

# (label, in-app path). The paths are the onboarding checklist's own
# (app/api/onboarding.py STEP_ACTIONS), so a link here is never a dead route.
_ACCOUNT_QUESTIONS = "Answer a few questions about you and your company"
_PHONE_CHECK = "verify your phone number"
_CHECKS_PATH = "/onboarding/verify"  # where the Shell sends an account held by them
_CHOOSE_PLAN = ("Choose a plan", "/pricing")
PRODUCT_STEPS = (
    ("Connect your Gmail account, so outreach goes out from your own address",
     "/settings?tab=integrations"),
    ("Describe your product and run your first strategy", "/strategies/new"),
    ("Review the leads it finds before anything is sent", "/strategies"),
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _needs_plan(db: Session, user: User) -> bool:
    if not settings.active_plan_required:
        return False
    from app.services import billing  # noqa: PLC0415 -- heavy module, used once

    try:
        return not billing.plan_access(db, user)["has_active_plan"]
    except Exception:  # noqa: BLE001
        # Wording, not access control: if the lookup fails, mention the plan
        # step. Telling a paying user to "choose a plan" is a lesser fault than
        # failing the whole email over it.
        logger.warning("onboarding email: plan lookup failed for %s", user.id)
        return True


def steps_for(db: Session, user: User) -> list[tuple[str, str]]:
    """The ordered steps this account still has in front of it."""
    from app.services import identity  # noqa: PLC0415

    steps: list[tuple[str, str]] = []
    if getattr(user, "identity_required", False):
        need_identity = not identity.identity_complete(user)
        need_phone = (identity.phone_verification_required()
                      and not getattr(user, "phone_verified", False))
        if need_identity and need_phone:
            steps.append((f"{_ACCOUNT_QUESTIONS}, then {_PHONE_CHECK}", _CHECKS_PATH))
        elif need_identity:
            steps.append((_ACCOUNT_QUESTIONS, _CHECKS_PATH))
        elif need_phone:
            steps.append((_PHONE_CHECK.capitalize(), _CHECKS_PATH))
    if _needs_plan(db, user):
        steps.append(_CHOOSE_PLAN)
    steps.extend(PRODUCT_STEPS)
    return steps


def render(user: User, steps: list[tuple[str, str]]) -> tuple[str, str, str]:
    """(subject, html, text). Every link is absolute, on FRONTEND_URL."""
    app = settings.app_name
    base = settings.frontend_url.rstrip("/")
    name = (user.full_name or "").strip()
    greeting = f"Hi {name}," if name else "Hi,"
    subject = f"Your {app} account is ready: what to do next"

    text_steps = "\n".join(
        f"{i}. {label}\n   {base}{path}" for i, (label, path) in enumerate(steps, 1))
    footer = (f"You are receiving this because you created and verified a {app} "
              f"account with {user.email}. It is a one-time message about that "
              "account, not marketing; there is nothing to unsubscribe from.")
    text = (
        f"{greeting}\n\n"
        f"Your email address is verified and your {app} account is ready.\n\n"
        f"Here is what to do next, in order:\n\n"
        f"{text_steps}\n\n"
        f"Sign in: {base}/login\n\n"
        f"Questions? Reply to this email or open Help inside the app: {base}/help\n\n"
        f"-- The {app} team\n\n"
        f"{footer}\n"
    )

    esc = _html.escape
    html_steps = "".join(
        f'<li style="margin:0 0 10px"><a href="{esc(base + path)}" '
        f'style="color:#1d4ed8">{esc(label)}</a></li>'
        for label, path in steps)
    html = (
        '<!doctype html><html><body style="font-family:system-ui,-apple-system,'
        'Segoe UI,Roboto,sans-serif;line-height:1.6;color:#111">'
        f"<p>{esc(greeting)}</p>"
        f"<p>Your email address is verified and your {esc(app)} account is ready.</p>"
        "<p>Here is what to do next, in order:</p>"
        f'<ol style="padding-left:20px">{html_steps}</ol>'
        f'<p style="margin:24px 0"><a href="{esc(base)}/login" '
        'style="background:#1d4ed8;color:#fff;padding:12px 20px;'
        'border-radius:8px;text-decoration:none;display:inline-block">'
        f"Sign in to {esc(app)}</a></p>"
        "<p>Questions? Reply to this email or open "
        f'<a href="{esc(base)}/help" style="color:#1d4ed8">Help</a> inside the app.</p>'
        f"<p>&mdash; The {esc(app)} team</p>"
        '<hr style="border:none;border-top:1px solid #eee;margin:24px 0">'
        f'<p style="font-size:12px;color:#777">{esc(footer)}</p>'
        "</body></html>"
    )
    return subject, html, text


def _claim(db: Session, user_id: uuid.UUID) -> bool:
    """Atomically take the once-only slot. True only for the caller that
    changed the row; commits so a concurrent claimer sees it immediately."""
    result = db.execute(
        update(User)
        .where(User.id == user_id,
               User.onboarding_email_sent_at.is_(None),
               User.email_verified.is_(True),
               User.is_suspended.is_(False),
               User.password_hash.is_not(None))
        .values(onboarding_email_sent_at=_now())
        .execution_options(synchronize_session=False)
    )
    db.commit()
    return result.rowcount == 1


def _release(db: Session, user_id: uuid.UUID) -> None:
    db.execute(
        update(User).where(User.id == user_id)
        .values(onboarding_email_sent_at=None)
        .execution_options(synchronize_session=False)
    )
    db.commit()


def send_once(db: Session, user_id) -> str:
    """Send the onboarding email to this account if it has never had it.

    Returns SENT, ALREADY_SENT, NOT_ELIGIBLE or NO_SUCH_USER. Raises
    EmailSendError when the transport fails -- after releasing the claim, so
    the caller (a Celery task with retries) can try again.
    """
    uid = user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(str(user_id))
    user = db.get(User, uid)
    if user is None:
        return NO_SUCH_USER
    if user.onboarding_email_sent_at is not None:
        return ALREADY_SENT
    if not user.email_verified or user.is_suspended or not user.password_hash:
        return NOT_ELIGIBLE

    if not _claim(db, uid):
        # Lost the race to another sender, or the account changed under us.
        db.refresh(user)
        return ALREADY_SENT if user.onboarding_email_sent_at is not None else NOT_ELIGIBLE

    db.refresh(user)
    subject, html, text = render(user, steps_for(db, user))
    try:
        send_email(to=user.email, subject=subject, html=html, text=text)
    except Exception:
        _release(db, uid)
        logger.error("onboarding email FAILED for user %s; claim released", uid)
        raise
    logger.info("onboarding email sent to user %s", uid)
    return SENT
