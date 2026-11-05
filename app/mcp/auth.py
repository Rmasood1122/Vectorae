"""Bearer-token verification for /mcp (the resource-server half of OAuth).

Only connector access tokens are accepted: `typ="mcp_access"` with `aud`
equal to this deployment's MCP URL (auth.decode_mcp_access_token). A web
session token (`typ="access"`) and a personal API key (`lpk_...`) are both
refused here, and an MCP token is refused by the REST API, because each
side checks `typ` (DECISIONS.md CONN-AS).

The token resolves to the same User get_current_user would return for that
person. Its connector session must still be live (a disconnect, a family
logout or a refresh-token reuse ends it at once:
auth_sessions.connector_session_live), and it passes the same account
checks (erased, suspended, unverified email: auth.check_account_usable) plus
the password-change sign-out. A token that fails any of them is answered 401
by the SDK's bearer middleware -- the client's refresh then fails too, and
the person reconnects.

Never logs the token.
"""

from __future__ import annotations

import logging
import uuid

import anyio.to_thread
from fastapi import HTTPException
from mcp.server.auth.provider import AccessToken
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import User
from app.services import auth as auth_svc
from app.services.auth_sessions import (
    _issued_before_password_change,
    connector_session_live,
)

logger = logging.getLogger(__name__)


class PrincipalRejected(Exception):
    """The token is genuine but its account may not act now. `str()` is the
    same detail the REST API gives (e.g. "account suspended")."""


def load_principal(db: Session, claims: dict) -> User:
    """The User a verified connector token acts for, or PrincipalRejected.

    CONN-WORKSPACE: always the signed-in person's own account; there is no
    workspace switching on the connector in v1.
    """
    user = db.get(User, uuid.UUID(str(claims["sub"])))
    if user is None:
        raise PrincipalRejected("user not found")
    if not connector_session_live(db, session_id=claims.get("sid"),
                                  family_id=claims.get("fam"), user_id=user.id):
        raise PrincipalRejected("connection revoked")
    if _issued_before_password_change(user, claims):
        raise PrincipalRejected("session expired")
    try:
        auth_svc.check_account_usable(user)
    except HTTPException as exc:
        raise PrincipalRejected(str(exc.detail)) from None
    # The paywall (auth.check_plan_active): every connector tool reads or
    # changes product data, so none of it is exempt.
    if settings.active_plan_required:
        from app.services.billing import plan_access  # noqa: PLC0415

        if not plan_access(db, user)["has_active_plan"]:
            raise PrincipalRejected(auth_svc.SUBSCRIPTION_REQUIRED)
    return user


def access_token_from_claims(token: str, claims: dict) -> AccessToken:
    return AccessToken(
        token=token,
        client_id=str(claims.get("client_id") or ""),
        scopes=str(claims.get("scope") or "").split(),
        expires_at=int(claims["exp"]),
        resource=str(claims.get("aud") or ""),
        subject=str(claims["sub"]),
        claims={k: claims[k] for k in ("sid", "fam", "iat") if k in claims},
    )


class McpTokenVerifier:
    """mcp.server.auth.provider.TokenVerifier for connector access tokens."""

    def __init__(self, session_factory):
        self._session_factory = session_factory

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            claims = auth_svc.decode_mcp_access_token(
                token, audience=settings.mcp_resource)
        except auth_svc.TokenRejected as exc:
            logger.info("mcp: bearer token refused (%s)", exc)
            return None
        try:
            await anyio.to_thread.run_sync(self._check_account, claims)
        except PrincipalRejected as exc:
            logger.info("mcp: token for user %s refused (%s)", claims.get("sub"), exc)
            return None
        return access_token_from_claims(token, claims)

    def _check_account(self, claims: dict) -> None:
        db = self._session_factory()
        try:
            load_principal(db, claims)
        finally:
            db.close()
