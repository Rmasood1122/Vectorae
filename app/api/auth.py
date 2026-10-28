"""Auth + current-user endpoints (M5).

POST /auth/signup   — create account (or claim a pre-auth row with no
                      password yet), returns access+refresh tokens AND sends
                      a verification email (Feature 1)
POST /auth/login    — email+password -> tokens
POST /auth/refresh  — refresh token -> new token pair (rotating; see below)
POST /auth/logout   — revoke the refresh token's session and clear its cookie
GET  /auth/me       — the authenticated user

PERSISTENT SIGN-IN (app/services/auth_sessions.py). A client that sends
`X-Auth-Transport: cookie` (the web app) receives its refresh token ONLY as an
HttpOnly cookie scoped to AUTH_REFRESH_COOKIE_PATH, never in a response body
where script could read it, and /auth/refresh and /auth/logout read it from
there. Every other client (native app, SDK, CLI) keeps the body transport it
always had. The cookie is never read without the header -- that header is what
makes the cookie routes CSRF-proof (a cross-origin page cannot send it past
CORS).

`user.has_active_plan` on every sign-in, refresh and /auth/me response is what
the frontend routes on: false -> /pricing, true -> the dashboard.
GET  /auth/verify   — target of the emailed link; marks the account verified
                      and 302s to the frontend login page (Feature 1)
POST /auth/resend-verification — send a fresh link; always 202, never reveals
                      whether the address has an account (Feature 1)
POST /auth/password-reset/request — email a single-use reset link; always
                      200, never reveals whether the address has an account
POST /auth/password-reset/confirm — token + new password; signs out
                      everywhere (docs/features/password-reset.md)
GET  /me/theme      — the user's theme JSON (section H)
PUT  /me/theme      — persist theme JSON (validated; contrast_warnings
                      flag persisted so the user was demonstrably informed)
POST /me/theme/background — upload a background image; served at /media/*

NOTE: only these user-scoped endpoints enforce auth in M5. Wiring
get_current_user into every pre-existing endpoint (and scoping strategies/
leads by owner) is a deliberate follow-up — flagged, not silently done,
because it changes every existing API contract and test.
"""

import logging
import re
import secrets
from pathlib import Path

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Body,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.core.rate_limiting import (
    AUTH_WINDOW_SECONDS,
    client_ip,
    enforce_rate_limit,
)
from app.db.base import get_db
from app.db.models import User
from app.services import auth as auth_svc
from app.services import auth_sessions
from app.services import billing as billing_svc
from app.services import email_verification as verify_svc
from app.services import identity as identity_svc
from app.services import network_guard
from app.services import security_audit
from app.services.email_sender import EmailSendError

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])

_ALLOWED_IMAGE = {"image/png": ".png", "image/jpeg": ".jpg",
                  "image/webp": ".webp"}
_MAX_IMAGE_BYTES = 5 * 1024 * 1024

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)


class LoginIn(Credentials):
    # "Keep me signed in". Default true: staying signed in is the product's
    # promise; unticking it opts into a browser-session cookie instead.
    remember_me: bool = True


class RefreshIn(BaseModel):
    # Optional: a cookie-transport client sends no body at all.
    refresh_token: str | None = None


class ResendIn(BaseModel):
    email: EmailStr


class ThemeIn(BaseModel):
    """Mirrors the frontend Theme type (section H)."""
    preset: str | None = None
    background_color: str = "#ffffff"
    background_image_url: str | None = None
    background_overlay: float = Field(default=0.0, ge=0.0, le=0.9)
    primary_color: str = "#1d4ed8"
    accent_color: str = "#0891b2"
    font_family: str = "Inter"
    font_size_scale: float = Field(default=1.0, ge=0.8, le=1.4)
    radius_px: int = Field(default=8, ge=0, le=24)
    density: str = Field(default="comfortable", pattern="^(comfortable|compact)$")
    contrast_warnings: list[str] = Field(default_factory=list)

    def validated(self) -> dict:
        for name in ("background_color", "primary_color", "accent_color"):
            if not _HEX.match(getattr(self, name)):
                raise HTTPException(status_code=422,
                                    detail=f"{name} must be #rrggbb")
        return self.model_dump()


def _wants_cookie(request: Request) -> bool:
    value = request.headers.get(auth_sessions.TRANSPORT_HEADER) or ""
    return value.strip().lower() == auth_sessions.COOKIE_TRANSPORT


def _set_refresh_cookie(response: Response, token: str, persistent: bool) -> None:
    max_age = auth_sessions.ttl_seconds(True) if persistent else None
    response.set_cookie(
        key=settings.auth_refresh_cookie_name, value=token,
        max_age=max_age, expires=max_age,
        path=settings.auth_refresh_cookie_path or "/",
        domain=settings.auth_refresh_cookie_domain or None,
        secure=settings.refresh_cookie_secure, httponly=True,
        samesite=settings.refresh_cookie_samesite,
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        key=settings.auth_refresh_cookie_name,
        path=settings.auth_refresh_cookie_path or "/",
        domain=settings.auth_refresh_cookie_domain or None,
        secure=settings.refresh_cookie_secure, httponly=True,
        samesite=settings.refresh_cookie_samesite,
    )


def _deliver(request: Request, response: Response,
             tokens: auth_sessions.IssuedTokens) -> dict:
    """The token bundle for the response, with the refresh token moved into
    the cookie for a cookie-transport client."""
    bundle = tokens.bundle()
    if _wants_cookie(request):
        refresh = bundle.pop("refresh_token", None)
        if refresh is not None:
            _set_refresh_cookie(response, refresh, tokens.persistent)
    return bundle


def _user_out(user: User, db: Session) -> dict:
    return {
        "id": str(user.id),
        "email": user.email,
        "full_name": user.full_name,
        "plan": user.plan.value,
        # has_active_plan + subscription_status. Recomputed from the database
        # on every response so a purchase (or a cancellation) takes effect on
        # the very next sign-in or page load. See billing.plan_access.
        **billing_svc.plan_access(db, user),
        # M8-C3 admin UI reads this to gate the /admin route group.
        "is_admin": bool(getattr(user, "is_admin", False)),
        # Feature 1: the frontend routes an unverified user to the
        # check-your-email screen straight off the signup/login response,
        # instead of letting them reach the dashboard and discover it as a 403
        # on the first data fetch.
        "email_verified": bool(getattr(user, "email_verified", False)),
        # Identity onboarding (migration 0039). The Shell routes a user with
        # identity_required and an incomplete identity/phone to
        # /onboarding/verify, the same way it routes email_verified=false.
        "identity_required": bool(getattr(user, "identity_required", False)),
        "identity_complete": identity_svc.identity_complete(user),
        "phone_verified": bool(getattr(user, "phone_verified", False)),
        # Product defect #5: false when the deployment does not require a
        # verified phone (switched off, or no SMS transport that can deliver).
        "phone_required": identity_svc.phone_verification_required(),
    }


def _deliver_verification(db: Session, user: User) -> bool:
    """Persist a fresh token, then email it. Returns whether the mail went out.

    Never raises. Both callers (signup, resend) treat a mail-transport failure
    as a degraded success rather than an error, because the alternatives are
    worse: failing signup with a 500 destroys an account the user just created
    and cannot retry (the address is now taken), and rolling the account back
    loses their password entry for a fault that is entirely ours.

    The commit happens BEFORE the send so the token is durable by the time the
    link can be clicked -- see email_verification.send_link.
    """
    raw = verify_svc.issue_token(db, user)
    db.commit()
    try:
        verify_svc.send_link(user, raw)
    except EmailSendError as exc:
        # Logged at error level with the address, because this is the failure
        # mode that silently strands users and it must be greppable.
        logger.error("verification email FAILED for %s: %s", user.email, exc)
        return False
    return True


SIGNUP_CLOSED_DETAIL = ("Sign-up is closed on this Vectorae deployment. "
                        "Ask your administrator for an account.")
SIGNUP_DOMAIN_DETAIL = ("Sign-up on this Vectorae deployment is limited to "
                        "approved email domains. Ask your administrator for an "
                        "account.")


def _enforce_signup_policy(email: str) -> None:
    """P0-D-ENF: refuse a self-registration the deployment does not allow.

    Runs before any account lookup, so a closed deployment answers the same
    403 whether or not the address already has an account (no enumeration),
    and a pre-auth row cannot be claimed through a closed door either. The
    allowed domains are not echoed back: they name the customer.
    """
    if not settings.signup_enabled:
        raise HTTPException(status_code=403, detail=SIGNUP_CLOSED_DETAIL)
    allowed = settings.signup_allowed_domains
    if allowed:
        domain = email.rsplit("@", 1)[-1].strip().lower()
        if domain not in allowed:
            raise HTTPException(status_code=403, detail=SIGNUP_DOMAIN_DETAIL)


@router.post("/auth/signup", status_code=201)
def signup(request: Request, response: Response, body: Credentials,
           db: Session = Depends(get_db)) -> dict:
    """Create an account (or claim a pre-auth row that has no password yet).

    Rate limited per IP: unlimited signups let one caller farm accounts, and
    an account-creation flood is the cheapest way to pollute the user table.
    """
    # Quota AFTER validation, before any DB work — same ordering as
    # create_strategy. FastAPI validates `body` before this line runs, so a
    # malformed payload cannot burn a slot.
    enforce_rate_limit(f"ip:{client_ip(request)}", "auth_ip",
                       "RATE_LIMIT_AUTH", AUTH_WINDOW_SECONDS)
    # P0-D-ENF: after the limiter (probing a closed door still spends quota),
    # before the VPN lookup (no provider call for a request we refuse anyway).
    _enforce_signup_policy(body.email.lower().strip())
    # Section D: no signups through a VPN/proxy/Tor/datacenter address. After
    # the limiter (a blocked flood still spends quota, and cannot turn the
    # provider lookup into the thing being flooded), before any account work.
    network_guard.enforce_clean_network(db, request, "signup", email=body.email)

    email = body.email.lower().strip()
    user = db.execute(select(User).where(User.email == email)).scalars().first()
    if user is not None and user.password_hash:
        raise HTTPException(status_code=409, detail="account already exists")
    if user is None:
        user = User(email=email)
        db.add(user)
    try:
        user.password_hash = auth_svc.hash_password(body.password)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    # New accounts start UNVERIFIED. Pre-auth rows being claimed here are new
    # accounts too as far as verification goes -- nobody has ever proved they
    # control the address. (Rows that existed before migration 0015 were
    # backfilled to verified by that migration and are not affected: this line
    # only runs for a row that had no password, i.e. one nobody has used.)
    user.email_verified = False
    user.email_verified_at = None
    # Migration 0039: accounts created from here on go through the identity
    # questions and phone verification. Rows that existed before 0039 keep the
    # FALSE server default and are never forced through it.
    user.identity_required = True
    user.signup_ip = (client_ip(request) or "")[:64] or None
    db.commit()

    # Tokens are still returned. The account cannot DO anything with them --
    # get_current_user rejects every authenticated route with 403
    # EMAIL_NOT_VERIFIED -- but handing them over means the client is already
    # signed in the instant the link is clicked, with no second login, and the
    # check-your-email screen has a session to resend from.
    sent = _deliver_verification(db, user)
    tokens = auth_sessions.issue_session_tokens(db, user, persistent=True, request=request)
    return {
        "user": _user_out(user, db),
        **_deliver(request, response, tokens),
        "email_verification_required": True,
        "verification_email_sent": sent,
    }


@router.get("/auth/verify", include_in_schema=True)
def verify_email(token: str = Query(default="", max_length=512),
                 db: Session = Depends(get_db)) -> RedirectResponse:
    """The target of the link in the verification email.

    Always REDIRECTS, never renders. The API has no templates and no styling,
    and a bare JSON body is not something to show a person who just clicked a
    button in their inbox. Every outcome lands on the frontend login page with
    a query flag it can turn into a message:

        ?verified=true            success
        ?verified=already         the account was already verified
        ?error=expired            link older than EMAIL_VERIFICATION_TTL_HOURS
        ?error=invalid            unknown or malformed token

    302 (not 307) is deliberate: this is a GET being answered with a GET, and
    302 is what every mail client and link scanner handles predictably.

    NOT rate limited. The token is 43 URL-safe characters of os.urandom inside
    a 24-hour window; there is nothing to brute force, and a limiter keyed on
    IP would punish the shared corporate NAT of exactly the customer we want.
    """
    frontend = settings.frontend_url.rstrip("/")
    status, _user = verify_svc.consume_token(db, token or "")
    if status == verify_svc.VERIFIED:
        db.commit()
        target = f"{frontend}/login?verified=true"
    elif status == verify_svc.ALREADY_VERIFIED:
        target = f"{frontend}/login?verified=already"
    elif status == verify_svc.EXPIRED:
        target = f"{frontend}/login?error=expired"
    else:
        target = f"{frontend}/login?error=invalid"
    logger.info("email verification outcome=%s", status)
    return RedirectResponse(url=target, status_code=302)


@router.post("/auth/resend-verification", status_code=202)
def resend_verification(request: Request, body: ResendIn,
                        db: Session = Depends(get_db)) -> dict:
    """Send a fresh verification link.

    ALWAYS returns 202 with the same body, whatever the address is. Reporting
    "no such account" here would hand anyone an account-existence oracle for
    every address they care to try -- and /auth/login went to the trouble of
    avoiding exactly that with its single "invalid email or password". A
    resend endpoint that leaks it would undo that.

    Rate limited on BOTH keys for the same reason /auth/login is: per IP stops
    one host hammering the mail transport, per account stops a distributed
    mail-bomb aimed at one person's inbox. Both consume on every call.
    """
    enforce_rate_limit(f"ip:{client_ip(request)}", "auth_ip",
                       "RATE_LIMIT_AUTH", AUTH_WINDOW_SECONDS)
    email = body.email.lower().strip()
    enforce_rate_limit(f"acct:{email}", "auth_account",
                       "RATE_LIMIT_AUTH", AUTH_WINDOW_SECONDS)

    user = db.execute(select(User).where(User.email == email)).scalars().first()
    # Nothing to send for an unknown address, a passwordless pre-auth row, or
    # an account that is already verified -- and the caller is told none of it.
    if user is not None and user.password_hash and not user.email_verified:
        _deliver_verification(db, user)
    return {"status": "accepted",
            "detail": "If that address has an unverified account, "
                      "a verification email has been sent."}


@router.post("/auth/login")
def login(request: Request, response: Response, body: LoginIn,
          db: Session = Depends(get_db)) -> dict:
    """Email + password -> tokens.

    Two independent limits, both governed by RATE_LIMIT_AUTH:

      per IP       stops one host brute-forcing passwords.
      per account  stops CREDENTIAL STUFFING, where an attacker spreads
                   attempts against a single account across many IPs. A
                   per-IP limit alone does nothing about that, which is why
                   both keys exist rather than just the obvious one.

    Both are consumed on every attempt, successful or not — enforce_rate_limit
    is INCR-then-compare, so the check and the spend are one step. Counting
    only failures would be gentler on shared IPs, but it needs a
    check-then-conditionally-record split this codebase does not have, and
    adding one here would mean two divergent limiter mechanisms. The trade-off
    is recorded in CLAUDE_CODE_HANDOFF.md rather than hidden.

    /auth/refresh is deliberately NOT limited: a legitimate client hits it
    every time a 15-minute access token expires, so it is the one auth route
    where normal use approaches the threshold, and guessing a signed refresh
    token is not a brute-forceable attack in the first place.
    """
    enforce_rate_limit(f"ip:{client_ip(request)}", "auth_ip",
                       "RATE_LIMIT_AUTH", AUTH_WINDOW_SECONDS)

    email = body.email.lower().strip()
    # Keyed on the normalised address, so Foo@x.com and foo@x.com share a
    # bucket and casing cannot be used to multiply the allowance.
    enforce_rate_limit(f"acct:{email}", "auth_account",
                       "RATE_LIMIT_AUTH", AUTH_WINDOW_SECONDS)
    # Section D: same network rule as signup, same position (after both
    # limiters, before the password is checked -- so a VPN'd attacker learns
    # nothing about whether the credentials were right).
    network_guard.enforce_clean_network(db, request, "login", email=email)

    user = db.execute(select(User).where(User.email == email)).scalars().first()
    if user is None or not auth_svc.verify_password(body.password,
                                                    user.password_hash):
        # one message for both cases: no account enumeration
        raise HTTPException(status_code=401, detail="invalid email or password")
    tokens = auth_sessions.issue_session_tokens(db, user, persistent=body.remember_me,
                                                request=request)
    return {"user": _user_out(user, db), **_deliver(request, response, tokens)}


def _presented_refresh_token(request: Request, body: RefreshIn | None) -> str | None:
    if body is not None and body.refresh_token:
        return body.refresh_token
    if _wants_cookie(request):
        return request.cookies.get(settings.auth_refresh_cookie_name) or None
    return None


@router.post("/auth/refresh")
def refresh(request: Request, response: Response,
            body: RefreshIn | None = Body(default=None),
            db: Session = Depends(get_db)):
    """Rotate a refresh token. Called silently on app load (session restore)
    and whenever an access token expires.

    Deliberately NOT rate limited -- see login() and
    tests/test_rate_limit_auth.py::test_refresh_is_deliberately_not_limited.

    A cookie-transport failure CLEARS the cookie in the same 401, so a dead
    session does not get re-presented on every page load.
    """
    token = _presented_refresh_token(request, body)
    try:
        if not token:
            raise HTTPException(status_code=401, detail="missing refresh token")
        tokens = auth_sessions.rotate(db, token, request=request)
    except HTTPException as exc:
        if exc.status_code != 401 or not _wants_cookie(request):
            raise
        failed = JSONResponse(status_code=401, content={"detail": exc.detail})
        _clear_refresh_cookie(failed)
        return failed
    return {"user": _user_out(tokens.user, db), **_deliver(request, response, tokens)}


@router.post("/auth/logout", status_code=204)
def logout(request: Request, body: RefreshIn | None = Body(default=None),
           db: Session = Depends(get_db)) -> Response:
    """Sign out: revoke the session (and every token rotated from it) and
    clear the cookie. Unauthenticated on purpose -- the access token may have
    expired, and signing out must still work. Always 204."""
    token = _presented_refresh_token(request, body)
    if token:
        auth_sessions.revoke(db, token, request=request)
    response = Response(status_code=204)
    if _wants_cookie(request):
        _clear_refresh_cookie(response)
    return response


@router.get("/auth/me")
def me(user: User = Depends(auth_svc.get_current_user),
       db: Session = Depends(get_db)) -> dict:
    return _user_out(user, db)


class ProfileIn(BaseModel):
    # Extra keys (plan, is_admin, email ...) are ignored by pydantic, so this
    # endpoint can only ever write the display name.
    full_name: str | None = Field(default=None, max_length=200)


_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


@router.patch("/auth/me")
def update_profile(body: ProfileIn, user: User = Depends(auth_svc.get_current_user),
                   db: Session = Depends(get_db)) -> dict:
    name = (body.full_name or "").strip()
    if _CONTROL.search(name):
        raise HTTPException(status_code=422, detail="Name contains invalid characters.")
    if len(name) > 120:
        raise HTTPException(status_code=422, detail="Name must be 120 characters or fewer.")
    user.full_name = name or None
    db.commit()
    return _user_out(user, db)


class ChangePasswordIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=256)


@router.post("/auth/change-password")
def change_password(request: Request, body: ChangePasswordIn,
                    user: User = Depends(auth_svc.get_current_user),
                    db: Session = Depends(get_db)) -> dict:
    """Change the password and sign the account out everywhere.

    400 (not 401) on a wrong current password: the web client treats a 401 as
    an expired session and tries to refresh or sign out. Rate limited per
    account so a hijacked session cannot be used to guess the old password.
    """
    enforce_rate_limit(f"user:{user.id}", "change_password",
                       "RATE_LIMIT_AUTH", AUTH_WINDOW_SECONDS)
    if not auth_svc.verify_password(body.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect.")
    if body.new_password == body.current_password:
        raise HTTPException(status_code=400,
                            detail="New password must be different from the current one.")
    user.password_hash = auth_svc.hash_password(body.new_password)
    signed_out = _sign_out_everywhere(db, user, "password_changed")
    security_audit.record(db, action=security_audit.AUTH_PASSWORD_CHANGED, request=request,
                          user_id=user.id, target_type="user", target_id=user.id,
                          details=signed_out)
    db.commit()
    return {"status": "changed", "signed_out_everywhere": True,
            "api_keys_revoked": signed_out["api_keys_revoked"]}


def _sign_out_everywhere(db: Session, user: User, reason: str) -> dict:
    """What a password change or reset does to every credential the account
    has out there. Does not commit (the caller's transaction holds the new
    hash too):

      * every refresh session revoked (auth_sessions.revoke_all_for_user);
      * users.password_changed_at = now, so any refresh or access token
        issued before it -- legacy session-less refresh tokens included -- is
        refused (auth_sessions._issued_before_password_change);
      * every personal API key revoked: a key is a standing credential, and a
        password change is how a person shuts out whoever had their account.
    """
    from datetime import datetime, timezone  # noqa: PLC0415

    from app.services import api_keys  # noqa: PLC0415

    now = datetime.now(timezone.utc)
    user.password_changed_at = now
    return {"sessions_revoked": auth_sessions.revoke_all_for_user(db, user.id, reason, now),
            "api_keys_revoked": api_keys.revoke_all_for_user(db, user.id, now)}


# --------------------------------------------------------------------------
# Forgotten password (docs/features/password-reset.md)
# --------------------------------------------------------------------------


class PasswordResetRequestIn(BaseModel):
    email: EmailStr


class PasswordResetConfirmIn(BaseModel):
    token: str = Field(min_length=1, max_length=512)
    # The rules change-password applies: 8-256 characters (and hash_password's
    # own 8-character floor, re-checked server side).
    new_password: str = Field(min_length=8, max_length=256)


PASSWORD_RESET_ACCEPTED = ("If that address has a Vectorae account, we have "
                           "emailed it a link to reset the password.")
PASSWORD_RESET_INVALID = ("This reset link is invalid or has expired. "
                          "Request a new one.")


def _send_reset_link(email: str, raw: str) -> None:
    """Runs after the response (BackgroundTasks). Never raises: the token is
    already committed, and a transport failure is logged, greppable, and
    recoverable by asking again."""
    from app.services import password_reset  # noqa: PLC0415

    try:
        password_reset.send_link(email, raw)
    except EmailSendError as exc:
        logger.error("password reset email FAILED for %s: %s", email, exc)
    except Exception:  # a background task must not crash the worker
        logger.exception("password reset email FAILED for %s", email)


@router.post("/auth/password-reset/request")
def request_password_reset(request: Request, body: PasswordResetRequestIn,
                           background: BackgroundTasks,
                           db: Session = Depends(get_db)) -> dict:
    """Email a single-use reset link. ALWAYS 200 with the same body.

    Same no-enumeration rule as /auth/resend-verification: unknown address,
    passwordless pre-auth row, suspended account and real account all get the
    identical answer, and the email goes out after the response so timing does
    not tell them apart either. Rate limited per IP and per address on its own
    buckets (not login's), so flooding resets cannot lock someone out of
    signing in. A 429 is keyed on the address, never on whether it exists.
    """
    from app.services import password_reset  # noqa: PLC0415

    enforce_rate_limit(f"ip:{client_ip(request)}", "password_reset_ip",
                       "RATE_LIMIT_AUTH", AUTH_WINDOW_SECONDS)
    email = body.email.lower().strip()
    enforce_rate_limit(f"acct:{email}", "password_reset_account",
                       "RATE_LIMIT_AUTH", AUTH_WINDOW_SECONDS)

    user = password_reset.find_eligible(db, email)
    if user is not None:
        raw = password_reset.issue_token(db, user, ip=client_ip(request))
        if raw is not None:
            security_audit.record(db, action=security_audit.AUTH_PASSWORD_RESET_REQUESTED,
                                  request=request, user_id=user.id,
                                  target_type="user", target_id=user.id)
            # Commit BEFORE the send (email_verification.send_link explains why).
            db.commit()
            background.add_task(_send_reset_link, user.email, raw)
    return {"status": "accepted", "detail": PASSWORD_RESET_ACCEPTED}


@router.post("/auth/password-reset/confirm")
def confirm_password_reset(request: Request, body: PasswordResetConfirmIn,
                           db: Session = Depends(get_db)) -> dict:
    """Set a new password from a reset link, then sign out everywhere.

    400 with one message for every bad link (unknown, used, superseded,
    expired, or an account that became suspended since the link was sent): the
    holder of a dead link learns nothing about the account from which kind it
    was. The reason is kept in the audit row. Issues NO tokens: the person signs
    in normally afterwards, through the same limiter and network guard as any
    other sign-in.
    """
    from app.services import password_reset  # noqa: PLC0415

    enforce_rate_limit(f"ip:{client_ip(request)}", "password_reset_confirm_ip",
                       "RATE_LIMIT_AUTH", AUTH_WINDOW_SECONDS)
    try:
        status, user = password_reset.redeem(db, body.token.strip(), body.new_password)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc))
    if status != password_reset.RESET:
        if user is not None:
            security_audit.record(db, action=security_audit.AUTH_PASSWORD_RESET_REFUSED,
                                  request=request, user_id=user.id, target_type="user",
                                  target_id=user.id, details={"reason": status})
        db.commit()  # the burnt link and the refusal record are kept
        raise HTTPException(status_code=400, detail=PASSWORD_RESET_INVALID)
    signed_out = _sign_out_everywhere(db, user, "password_reset")
    security_audit.record(db, action=security_audit.AUTH_PASSWORD_RESET, request=request,
                          user_id=user.id, target_type="user", target_id=user.id,
                          details=signed_out)
    db.commit()
    return {"status": "reset", "signed_out_everywhere": True,
            "api_keys_revoked": signed_out["api_keys_revoked"]}


# --------------------------------------------------------------------------
# Theme (section H) — stored as users.theme_json
# --------------------------------------------------------------------------


@router.get("/me/theme")
def get_theme(user: User = Depends(auth_svc.get_current_user)) -> dict:
    return {"theme": user.theme_json or {}}


@router.put("/me/theme")
def put_theme(body: ThemeIn, db: Session = Depends(get_db),
              user: User = Depends(auth_svc.get_current_user)) -> dict:
    user.theme_json = body.validated()
    db.commit()
    return {"theme": user.theme_json}


@router.post("/me/theme/background", status_code=201)
async def upload_background(file: UploadFile, db: Session = Depends(get_db),
                            user: User = Depends(auth_svc.get_current_user)
                            ) -> dict:
    ext = _ALLOWED_IMAGE.get(file.content_type or "")
    if ext is None:
        raise HTTPException(status_code=422,
                            detail="only png, jpeg or webp images")
    data = await file.read()
    if len(data) > _MAX_IMAGE_BYTES:
        raise HTTPException(status_code=422, detail="image over 5MB")
    media = Path(settings.media_dir)
    media.mkdir(parents=True, exist_ok=True)
    name = f"bg_{user.id.hex}_{secrets.token_hex(6)}{ext}"
    (media / name).write_bytes(data)
    url = f"{settings.public_base_url.rstrip('/')}/media/{name}"
    return {"url": url}
