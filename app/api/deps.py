"""Shared FastAPI dependencies for M8+ routers.

get_current_user — canonical JWT dependency (delegates to app.services.auth,
                   the M5 implementation used across the whole API).
require_admin    — get_current_user + users.is_admin gate (column added in
                   migration 0009_m8c3).
is_admin         — the same test as require_admin, as a yes/no, for routes
                   that serve admins and non-admins differently.
require_growth_viewer — require_admin AND the signed-in address is on
                   GROWTH_DASHBOARD_VIEWERS (Admin > Growth).
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request

from app.config import settings
from app.services.auth import get_current_user  # canonical implementation
from app.db.models import User

__all__ = ["get_current_user", "is_admin", "require_admin", "require_growth_viewer"]


def require_admin(request: Request = None,
                  current_user: User = Depends(get_current_user)) -> User:
    # Feature Group 4: personal API keys never reach the admin panel -- a key
    # pasted into a Zap must not be able to suspend users or read secrets.
    if request is not None and getattr(request.state, "auth_via", None) == "api_key":
        raise HTTPException(status_code=403, detail="admin routes do not accept API keys")
    # Feature Group 8: in a workspace the principal is the workspace OWNER;
    # admin rights belong to whoever actually signed in.
    actor = getattr(request.state, "actor", None) if request is not None else None
    current_user = actor or current_user
    if not bool(getattr(current_user, "is_admin", False)):
        raise HTTPException(status_code=403, detail="admin privileges required")
    if bool(getattr(current_user, "is_suspended", False)):
        raise HTTPException(status_code=403, detail="account suspended")
    return current_user


def is_admin(request: Request, current_user: User) -> bool:
    """The same test require_admin applies, as a yes/no."""
    try:
        require_admin(request, current_user)
    except HTTPException:
        return False
    return True


def require_growth_viewer(request: Request = None,
                          current_user: User = Depends(get_current_user)) -> User:
    """Admin > Growth: an admin AND on the GROWTH_DASHBOARD_VIEWERS list.

    Both, not either: the list alone would let anyone who registers a listed
    address (or a typo'd domain on it) in before an operator promotes them,
    and the admin flag alone would show revenue to every operator. The same
    403 detail for both refusals, so a caller learns nothing about the list.
    """
    try:
        admin = require_admin(request, current_user)
    except HTTPException as exc:
        if exc.detail == "account suspended":
            raise
        raise HTTPException(status_code=403, detail="growth dashboard access required")
    if (admin.email or "").strip().lower() not in settings.growth_dashboard_viewer_emails:
        raise HTTPException(status_code=403, detail="growth dashboard access required")
    return admin
