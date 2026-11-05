"""Auth (M5) — password hashing + JWT access/refresh tokens.

Hashing: PBKDF2-HMAC-SHA256 (stdlib, no extra native deps), 600k
iterations, per-user random salt, constant-time compare.
Tokens: HS256 JWTs via PyJWT. Access tokens are short-lived; refresh
tokens are long-lived and can ONLY be exchanged at /auth/refresh (the
`typ` claim is checked so a refresh token never works as an access token
and vice versa). JWT_SECRET must be set in production; in development a
process-local random secret is generated (tokens won't survive restarts —
that's intentional pressure to set the env var).
"""

import base64
import hashlib
import hmac
import logging
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import settings
from app.db.base import get_db
from app.db.models import User

logger = logging.getLogger(__name__)

_PBKDF2_ITERATIONS = 600_000
_dev_secret: str | None = None


def _secret() -> str:
    global _dev_secret
    if settings.jwt_secret:
        return settings.jwt_secret
    if _dev_secret is None:
        _dev_secret = secrets.token_urlsafe(48)
        logger.warning("JWT_SECRET unset — using a process-local dev secret; "
                       "tokens will not survive restarts")
    return _dev_secret


# --------------------------------------------------------------------------
# Passwords
# --------------------------------------------------------------------------


def hash_password(password: str) -> str:
    if len(password) < 8:
        raise ValueError("password must be at least 8 characters")
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt,
                                 _PBKDF2_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        _PBKDF2_ITERATIONS,
        base64.b64encode(salt).decode(),
        base64.b64encode(digest).decode(),
    )


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        _algo, iters, salt_b64, digest_b64 = stored.split("$")
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt,
                                     int(iters))
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


# --------------------------------------------------------------------------
# Tokens
# --------------------------------------------------------------------------


def _issue(user_id: uuid.UUID, typ: str, ttl_seconds: int,
           extra: dict | None = None) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {**(extra or {}), "sub": str(user_id), "typ": typ, "iat": now,
         "exp": now + timedelta(seconds=ttl_seconds)},
        _secret(), algorithm="HS256",
    )


def issue_access_token(user_id: uuid.UUID) -> str:
    return _issue(user_id, "access", settings.jwt_access_ttl_seconds)


def issue_refresh_token(user_id: uuid.UUID, *, session_id: uuid.UUID,
                        family_id: uuid.UUID, ttl_seconds: int) -> str:
    """A refresh token bound to an auth_sessions row (jti) -- see
    app/services/auth_sessions.py, the only caller."""
    return _issue(user_id, "refresh", ttl_seconds,
                  {"jti": str(session_id), "fam": str(family_id)})


def issue_tokens(user_id: uuid.UUID) -> dict:
    """STATELESS token pair (no server-side session). Kept for callers that
    only need an access token -- tests, scripts. Anything that signs a person
    in uses auth_sessions.issue_session_tokens instead, so the refresh token
    can be rotated and revoked."""
    return {
        "access_token": _issue(user_id, "access",
                               settings.jwt_access_ttl_seconds),
        "refresh_token": _issue(user_id, "refresh",
                                settings.jwt_refresh_ttl_seconds),
        "token_type": "bearer",
        "expires_in": settings.jwt_access_ttl_seconds,
    }


# --------------------------------------------------------------------------
# Connector (MCP) access tokens -- DECISIONS.md CONN-AS
# --------------------------------------------------------------------------

# A separate token type so a web session token and a connector token can
# never stand in for each other: decode_token_payload(..., "access") refuses
# this `typ` (and its `aud`), and decode_mcp_access_token refuses everything
# else. `aud` is the MCP URL exactly as published in the RFC 9728 metadata.
MCP_ACCESS_TYP = "mcp_access"
# A connector refresh token: only the OAuth token endpoint accepts it, and it
# names its auth_sessions row (jti) and family (fam) like a web refresh token.
MCP_REFRESH_TYP = "mcp_refresh"


class TokenRejected(Exception):
    """A connector token that cannot be used. The message says why in a few
    words and never contains the token."""


def issue_mcp_access_token(user_id: uuid.UUID, *, client_id: str, scope: str,
                           audience: str, session_id: uuid.UUID,
                           family_id: uuid.UUID, ttl_seconds: int | None = None) -> str:
    """`sid`/`fam` bind the token to its connector session, so disconnecting
    (or a refresh-token reuse) stops it before it expires
    (auth_sessions.connector_session_live)."""
    extra = {"aud": audience, "client_id": client_id, "scope": scope,
             "sid": str(session_id), "fam": str(family_id)}
    ttl = settings.mcp_access_ttl_seconds if ttl_seconds is None else ttl_seconds
    return _issue(user_id, MCP_ACCESS_TYP, ttl, extra)


def issue_mcp_refresh_token(user_id: uuid.UUID, *, client_id: str, session_id: uuid.UUID,
                            family_id: uuid.UUID, ttl_seconds: int) -> str:
    return _issue(user_id, MCP_REFRESH_TYP, ttl_seconds,
                  {"jti": str(session_id), "fam": str(family_id), "client_id": client_id})


def decode_mcp_refresh_token(token: str, *, verify_exp: bool = True) -> dict:
    try:
        payload = jwt.decode(token, _secret(), algorithms=["HS256"],
                             options={"verify_exp": verify_exp,
                                      "require": ["exp", "iat", "sub", "jti"]})
    except jwt.ExpiredSignatureError:
        raise TokenRejected("expired") from None
    except jwt.InvalidTokenError:
        raise TokenRejected("invalid") from None
    if payload.get("typ") != MCP_REFRESH_TYP:
        raise TokenRejected("not a connector refresh token")
    try:
        uuid.UUID(str(payload.get("sub")))
        uuid.UUID(str(payload.get("jti")))
    except ValueError:
        raise TokenRejected("invalid") from None
    return payload


def decode_mcp_access_token(token: str, *, audience: str) -> dict:
    try:
        payload = jwt.decode(token, _secret(), algorithms=["HS256"], audience=audience,
                             options={"require": ["exp", "iat", "sub", "aud", "sid", "fam"]})
    except jwt.ExpiredSignatureError:
        raise TokenRejected("expired") from None
    except jwt.InvalidAudienceError:
        raise TokenRejected("issued for another resource") from None
    except jwt.InvalidTokenError:
        raise TokenRejected("invalid") from None
    if payload.get("typ") != MCP_ACCESS_TYP:
        raise TokenRejected("not a connector token")
    try:
        uuid.UUID(str(payload.get("sub")))
    except ValueError:
        raise TokenRejected("invalid") from None
    return payload


def decode_token_payload(token: str, expected_typ: str, *,
                         verify_exp: bool = True) -> dict:
    try:
        payload = jwt.decode(token, _secret(), algorithms=["HS256"],
                             options={"verify_exp": verify_exp})
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="invalid token")
    if payload.get("typ") != expected_typ:
        raise HTTPException(status_code=401,
                            detail=f"not a {expected_typ} token")
    try:
        uuid.UUID(str(payload.get("sub")))
    except ValueError:
        raise HTTPException(status_code=401, detail="invalid token")
    return payload


def decode_token(token: str, expected_typ: str) -> uuid.UUID:
    return uuid.UUID(decode_token_payload(token, expected_typ)["sub"])


# --------------------------------------------------------------------------
# Dependency
# --------------------------------------------------------------------------


# The exact string every unverified-user rejection carries. The frontend
# matches on it to decide "send this person to the check-your-email screen"
# rather than "log them out", so it is a contract, not a message: changing it
# silently breaks that branch. It lives here, next to the raise, so the two
# cannot drift.
EMAIL_NOT_VERIFIED = "EMAIL_NOT_VERIFIED"


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """Resolve the bearer token to a User, or raise.

    THIS IS THE SINGLE EMAIL-VERIFICATION CHOKEPOINT.

    Every authenticated route in the application resolves its user through this
    one function (app/api/deps.py re-exports it rather than reimplementing it,
    and require_admin builds on it). Putting the check here means an unverified
    account is refused everywhere at once, including routes added later that
    nobody remembered to guard. The alternative considered and rejected was
    adding a dependency to each router: ~30 files, ~30 chances to miss one, and
    a miss is a silent hole rather than a visible error.

    It is a 403, not a 401: the credentials ARE valid. A 401 would send
    frontend/src/lib/api/client.ts into its refresh-then-retry path, which would
    succeed at refreshing, retry, get 401 again, and eventually clear the
    session -- logging out a user whose only problem is an unread email.

    REQUIRE_EMAIL_VERIFICATION=false disables the gate without a code change.
    That switch exists because this is the highest-blast-radius line in the
    feature: if verification ever wrongly locks real users out of a running
    production system, the fix must be an environment variable and a restart,
    not a deploy.
    """
    header = request.headers.get("Authorization") or ""
    if not header.startswith("Bearer ") and request.headers.get("X-API-Key"):
        header = f"Bearer {request.headers['X-API-Key']}"
    if not header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = header.removeprefix("Bearer ").strip()

    # Feature Group 4: a personal API key (Zapier, Make, scripts) instead of
    # a short-lived access token. It passes through the SAME verification
    # gate below; deps.require_admin refuses it (request.state.auth_via).
    from app.services import api_keys  # noqa: PLC0415

    if token.startswith(api_keys.PREFIX):
        user = api_keys.authenticate(db, token)
        if user is None:
            raise HTTPException(status_code=401, detail="invalid or revoked API key")
        request.state.auth_via = "api_key"
    else:
        claims = decode_token_payload(token, "access")
        user = db.get(User, uuid.UUID(claims["sub"]))
        if user is None:
            raise HTTPException(status_code=401, detail="user not found")
        # A password change or reset signs out every device: an access token
        # minted before it is refused now, not when it expires.
        from app.services.auth_sessions import (  # noqa: PLC0415
            _issued_before_password_change,
        )

        if _issued_before_password_change(user, claims):
            raise HTTPException(status_code=401, detail="session expired")
    check_account_usable(user)
    check_plan_active(request, db, user)
    return _workspace_principal(request, db, user)


# The exact detail every paywall refusal carries; the frontend matches on it
# (frontend/src/lib/api/client.ts) the way it matches EMAIL_NOT_VERIFIED.
SUBSCRIPTION_REQUIRED = "SUBSCRIPTION_REQUIRED"

# Routes an account WITHOUT a paid plan still reaches: signing in and out,
# buying a plan, the identity/phone checks that come before buying, accepting
# a team invite (a member of a paying owner has access), support tickets, and
# the GDPR rights (export, delete the account). Everything else is the product.
# /admin/* is admin-only already (deps.require_admin, which answers 403); an
# admin always has plan access, so paywalling it would only turn a non-admin's
# 403 into a misleading 402.
_PLAN_EXEMPT_PREFIXES = ("/auth/", "/billing/", "/onboarding/", "/devices/", "/admin/")
_PLAN_EXEMPT_EXACT = frozenset({
    "/auth", "/billing", "/devices", "/me", "/me/data-export", "/me/theme",
    "/workspaces/invitations/accept", "/support/faq", "/support/tickets",
})


def plan_exempt(method: str, path: str) -> bool:
    """True when a no-plan account may call METHOD PATH."""
    path = (path or "/").rstrip("/") or "/"
    if path in _PLAN_EXEMPT_EXACT or path.startswith(_PLAN_EXEMPT_PREFIXES):
        return True
    # Removing your own data (voice clone, style profile) is never paywalled:
    # withdrawing consent must not depend on paying.
    return method.upper() == "DELETE" and path.startswith("/me/")


def check_plan_active(request: Request, db: Session, user: User) -> None:
    """The paywall (settings.active_plan_required): 402 for an account with no
    paid plan, a trial, or a paying workspace owner (billing.plan_access, the
    same answer /auth/me gives as has_active_plan). 402, not 401/403: the
    credentials are valid and the account is usable; it has not paid."""
    if not settings.active_plan_required:
        return
    root = request.scope.get("root_path") or ""
    path = request.url.path
    if root and path.startswith(root):
        path = path[len(root):]
    if plan_exempt(request.method, path):
        return
    from app.services.billing import plan_access  # noqa: PLC0415

    if not plan_access(db, user)["has_active_plan"]:
        raise HTTPException(status_code=402, detail={
            "error": SUBSCRIPTION_REQUIRED,
            "message": "Choose a plan to use Vectorae.",
        })


def check_account_usable(user: User) -> None:
    """Raise unless this account may act right now, whatever the credential.

    Shared by get_current_user and the connector (app/mcp/auth.py), so an
    erased, suspended or unverified account is refused by both with the same
    status and detail.
    """
    # GDPR erasure (app/services/account_erasure.py): an erased account is a
    # tombstone row, not a missing one, so an access token issued before the
    # erasure would still resolve. It gets the same answer as a missing user
    # -- 401, so the client's refresh fails (its sessions are gone) and it
    # signs out -- rather than the suspended account's 403.
    from app.services.account_erasure import is_tombstone  # noqa: PLC0415

    if is_tombstone(user):
        raise HTTPException(status_code=401, detail="user not found")
    # Suspension is enforced HERE, at the chokepoint, not only in
    # require_admin: otherwise a suspended account keeps every non-admin
    # route. 403 for the same reason as the verification gate below -- the
    # credentials are valid. Same detail string require_admin uses.
    if user.is_suspended:
        raise HTTPException(status_code=403, detail="account suspended")
    if settings.require_email_verification and not user.email_verified:
        raise HTTPException(status_code=403, detail=EMAIL_NOT_VERIFIED)


def _workspace_principal(request: Request, db: Session, actor: User) -> User:
    """Feature Group 8: WHOSE data this request acts on.

    With an X-Workspace-Id header naming a workspace the actor is a member of,
    the request acts on that workspace owner's records -- every route that
    scopes by user_id keeps working unchanged -- and the member's role is
    enforced here (app/services/rbac.py). `request.state.actor` is always the
    person who signed in; admin checks and audit use it, never the owner.
    Personal routes (/auth, /me, /devices, ...) ignore the header.
    """
    from app.services import rbac, workspaces  # noqa: PLC0415

    request.state.actor = actor
    request.state.workspace_role = "owner"
    header = request.headers.get(workspaces.HEADER)
    if not header or rbac.is_personal(request.url.path):
        return actor
    ctx = workspaces.resolve(db, actor, header)
    request.state.workspace_id = ctx.workspace.id
    request.state.workspace_role = ctx.role
    if ctx.workspace.owner_user_id == actor.id:
        return actor
    rbac.enforce(ctx.role, request.method, request.url.path)
    owner = db.get(User, ctx.workspace.owner_user_id)
    if owner is None or getattr(owner, "is_suspended", False):
        raise HTTPException(status_code=403, detail="this workspace is unavailable")
    return owner
