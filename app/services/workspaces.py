"""Workspaces: membership, invitations, approvals and white label (Feature Group 8).

See Workspace in app/db/models.py for the data model: a workspace IS its
owner's data, and members act on it.
"""

from __future__ import annotations

import hashlib
import logging
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape

from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import User, Workspace, WorkspaceInvitation, WorkspaceMember
from app.services import rbac

logger = logging.getLogger(__name__)

INVITE_TTL = timedelta(days=7)
HEADER = "X-Workspace-Id"
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_DOMAIN = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
VERIFY_PREFIX = "_leadpilot-verify"


@dataclass
class Context:
    workspace: Workspace
    role: str
    actor: User


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (slug or "workspace")[:40]


# --------------------------------------------------------------------------
# Membership
# --------------------------------------------------------------------------


def personal_workspace(db: Session, user: User) -> Workspace:
    """The workspace `user` owns, created on first use."""
    ws = db.execute(select(Workspace).where(Workspace.owner_user_id == user.id)
                    ).scalar_one_or_none()
    if ws is not None:
        return ws
    base = _slugify(user.email.split("@", 1)[0])
    slug = base
    while db.execute(select(Workspace.id).where(Workspace.slug == slug)).first():
        slug = f"{base}-{secrets.token_hex(3)}"
    ws = Workspace(name=f"{user.email.split('@', 1)[0]}'s workspace", slug=slug,
                   owner_user_id=user.id)
    db.add(ws)
    db.flush()
    db.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="owner"))
    db.commit()
    db.refresh(ws)
    return ws


def role_in(db: Session, workspace_id, user_id) -> str | None:
    return db.execute(select(WorkspaceMember.role).where(
        WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.user_id == user_id)
    ).scalar_one_or_none()


def resolve(db: Session, actor: User, workspace_id: str | None) -> Context:
    """The workspace named by the X-Workspace-Id header (the actor's own
    when absent), or 404 -- a workspace you are not in does not exist."""
    if not workspace_id:
        return Context(personal_workspace(db, actor), "owner", actor)
    try:
        wid = uuid.UUID(str(workspace_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid X-Workspace-Id") from exc
    ws = db.get(Workspace, wid)
    role = role_in(db, wid, actor.id) if ws is not None else None
    if ws is None or role is None:
        raise HTTPException(status_code=404, detail="workspace not found")
    return Context(ws, role, actor)


def context_for_request(request: Request, db: Session, actor: User) -> Context:
    return resolve(db, actor, request.headers.get(HEADER))


def memberships(db: Session, user: User) -> list[dict]:
    personal_workspace(db, user)
    rows = db.execute(
        select(Workspace, WorkspaceMember.role)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == user.id)
        .order_by(WorkspaceMember.created_at)
    ).all()
    return [{"id": str(ws.id), "name": ws.name, "slug": ws.slug, "role": role,
             "is_personal": ws.owner_user_id == user.id,
             "brand_name": ws.brand_name if ws.white_label_enabled else None}
            for ws, role in rows]


def members(db: Session, ws: Workspace) -> list[dict]:
    rows = db.execute(
        select(WorkspaceMember, User.email)
        .join(User, User.id == WorkspaceMember.user_id)
        .where(WorkspaceMember.workspace_id == ws.id)
        .order_by(WorkspaceMember.created_at)
    ).all()
    return [{"user_id": str(m.user_id), "email": email, "role": m.role,
             "joined_at": m.created_at.isoformat() if m.created_at else None}
            for m, email in rows]


def approvers(db: Session, ws: Workspace) -> list[uuid.UUID]:
    return list(db.execute(select(WorkspaceMember.user_id).where(
        WorkspaceMember.workspace_id == ws.id,
        WorkspaceMember.role.in_(["owner", "manager"]))).scalars())


def _require(ctx: Context, minimum: str, what: str) -> None:
    if not rbac.at_least(ctx.role, minimum):
        raise HTTPException(status_code=403, detail=f"{what} needs a {minimum} or above.")


def _may_manage(ctx: Context, target_role: str) -> bool:
    """Owners manage anyone but the owner; managers manage SDRs and viewers."""
    if target_role == "owner":
        return False
    if ctx.role == "owner":
        return True
    return ctx.role == "manager" and target_role in ("sdr", "viewer")


def set_role(db: Session, ctx: Context, user_id, role: str) -> None:
    if role not in ("manager", "sdr", "viewer"):
        raise HTTPException(status_code=422, detail="role must be manager, sdr or viewer")
    member = db.execute(select(WorkspaceMember).where(
        WorkspaceMember.workspace_id == ctx.workspace.id,
        WorkspaceMember.user_id == uuid.UUID(str(user_id)))).scalar_one_or_none()
    if member is None:
        raise HTTPException(status_code=404, detail="member not found")
    if not (_may_manage(ctx, member.role) and _may_manage(ctx, role)):
        raise HTTPException(status_code=403, detail="You cannot give or change that role.")
    member.role = role
    db.commit()


def remove_member(db: Session, ctx: Context, user_id) -> None:
    uid = uuid.UUID(str(user_id))
    member = db.execute(select(WorkspaceMember).where(
        WorkspaceMember.workspace_id == ctx.workspace.id, WorkspaceMember.user_id == uid)
    ).scalar_one_or_none()
    if member is None:
        raise HTTPException(status_code=404, detail="member not found")
    leaving = uid == ctx.actor.id
    if member.role == "owner":
        raise HTTPException(status_code=403, detail="The owner cannot be removed.")
    if not leaving and not _may_manage(ctx, member.role):
        raise HTTPException(status_code=403, detail="You cannot remove that member.")
    db.delete(member)
    db.commit()


# --------------------------------------------------------------------------
# Invitations
# --------------------------------------------------------------------------


def invite(db: Session, ctx: Context, email: str, role: str) -> tuple[WorkspaceInvitation, str]:
    email = (email or "").strip().lower()
    if not _EMAIL.match(email):
        raise HTTPException(status_code=422, detail="enter a valid email address")
    if role not in ("manager", "sdr", "viewer"):
        raise HTTPException(status_code=422, detail="role must be manager, sdr or viewer")
    _require(ctx, "manager", "Inviting people")
    if not _may_manage(ctx, role):
        raise HTTPException(status_code=403, detail="Only the owner can invite managers.")
    existing_user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if existing_user is not None and role_in(db, ctx.workspace.id, existing_user.id):
        raise HTTPException(status_code=409, detail="already a member of this workspace")
    for pending in db.execute(select(WorkspaceInvitation).where(
            WorkspaceInvitation.workspace_id == ctx.workspace.id,
            WorkspaceInvitation.email == email,
            WorkspaceInvitation.accepted_at.is_(None),
            WorkspaceInvitation.revoked_at.is_(None))).scalars():
        pending.revoked_at = _now()   # a re-invite replaces the old link
    token = secrets.token_urlsafe(32)
    invitation = WorkspaceInvitation(workspace_id=ctx.workspace.id, email=email, role=role,
                                     token_hash=_hash(token), invited_by_user_id=ctx.actor.id,
                                     expires_at=_now() + INVITE_TTL)
    db.add(invitation)
    db.commit()
    db.refresh(invitation)
    _send_invite(ctx, email, role, token)
    return invitation, token


def invite_link(token: str) -> str:
    return f"{settings.frontend_url.rstrip('/')}/invite?token={token}"


def _send_invite(ctx: Context, email: str, role: str, token: str) -> None:
    from app.services.email_sender import send_email  # noqa: PLC0415

    brand = (ctx.workspace.brand_name if ctx.workspace.white_label_enabled else None) \
        or "Vectorae"
    link = invite_link(token)
    subject = f"{ctx.actor.email} invited you to {ctx.workspace.name} on {brand}"
    text = (f"{ctx.actor.email} invited you to join {ctx.workspace.name} as {role}.\n\n"
            f"Accept: {link}\n\nThe link expires in 7 days.")
    html = (f"<p>{escape(ctx.actor.email)} invited you to join "
            f"<strong>{escape(ctx.workspace.name)}</strong> as {escape(role)}.</p>"
            f'<p><a href="{escape(link)}">Accept the invitation</a></p>'
            "<p>The link expires in 7 days.</p>")
    try:
        send_email(to=email, subject=subject, html=html, text=text)
    except Exception:  # noqa: BLE001 -- the invitation stands; it can be resent
        logger.exception("could not email the invitation to %s", email)


def invitations(db: Session, ws: Workspace) -> list[dict]:
    rows = db.execute(select(WorkspaceInvitation).where(
        WorkspaceInvitation.workspace_id == ws.id, WorkspaceInvitation.accepted_at.is_(None),
        WorkspaceInvitation.revoked_at.is_(None)).order_by(WorkspaceInvitation.created_at)
    ).scalars().all()
    now = _now()
    return [{"id": str(i.id), "email": i.email, "role": i.role,
             "expires_at": i.expires_at.isoformat(), "expired": _aware(i.expires_at) < now}
            for i in rows]


def revoke_invitation(db: Session, ctx: Context, invitation_id) -> None:
    _require(ctx, "manager", "Revoking invitations")
    inv = db.get(WorkspaceInvitation, uuid.UUID(str(invitation_id)))
    if inv is None or inv.workspace_id != ctx.workspace.id:
        raise HTTPException(status_code=404, detail="invitation not found")
    inv.revoked_at = _now()
    db.commit()


def accept(db: Session, actor: User, token: str) -> dict:
    inv = db.execute(select(WorkspaceInvitation).where(
        WorkspaceInvitation.token_hash == _hash(token or ""))).scalar_one_or_none()
    if inv is None or inv.revoked_at is not None:
        raise HTTPException(status_code=404, detail="invitation not found")
    if inv.accepted_at is not None:
        raise HTTPException(status_code=409, detail="invitation already used")
    if _aware(inv.expires_at) < _now():
        raise HTTPException(status_code=410, detail="invitation expired -- ask for a new one")
    if actor.email.strip().lower() != inv.email:
        raise HTTPException(status_code=403,
                            detail="This invitation was sent to a different email address.")
    if role_in(db, inv.workspace_id, actor.id) is None:
        db.add(WorkspaceMember(workspace_id=inv.workspace_id, user_id=actor.id, role=inv.role,
                               invited_by_user_id=inv.invited_by_user_id))
    inv.accepted_at = _now()
    db.commit()
    ws = db.get(Workspace, inv.workspace_id)
    return {"workspace_id": str(ws.id), "name": ws.name, "role": inv.role}


# --------------------------------------------------------------------------
# White label
# --------------------------------------------------------------------------


def default_branding() -> dict:
    return {"white_label": False, "brand_name": "Vectorae", "logo_url": None,
            "primary_color": None, "support_email": None}


def branding_out(ws: Workspace, *, private: bool = False) -> dict:
    out = {
        "white_label": bool(ws.white_label_enabled),
        "brand_name": ws.brand_name or ("Vectorae" if not ws.white_label_enabled else ws.name),
        "logo_url": ws.logo_url, "primary_color": ws.primary_color,
        "support_email": ws.support_email,
    }
    if private:
        base = settings.white_label_base_domain
        out.update({
            "subdomain": f"{ws.slug}.{base}" if base else None,
            "custom_domain": ws.custom_domain,
            "domain_verified": ws.domain_verified_at is not None,
            "verification_record": ({"type": "TXT",
                                     "name": f"{VERIFY_PREFIX}.{ws.custom_domain}",
                                     "value": ws.domain_verification_token}
                                    if ws.custom_domain else None),
            "cname_target": settings.white_label_cname_target or None,
            "approval_required": bool(ws.approval_required),
        })
    return out


def update_branding(db: Session, ctx: Context, changes: dict) -> Workspace:
    from app.services import system_settings  # noqa: PLC0415

    _require(ctx, "owner", "Changing branding")
    ws = ctx.workspace
    if changes.get("white_label_enabled") and not system_settings.get(db, "white_label_allowed"):
        raise HTTPException(status_code=403, detail="White label is disabled on this deployment.")
    if "primary_color" in changes and changes["primary_color"] is not None \
            and not _HEX.match(changes["primary_color"]):
        raise HTTPException(status_code=422, detail="primary_color must look like #1d4ed8")
    if changes.get("support_email") and not _EMAIL.match(changes["support_email"]):
        raise HTTPException(status_code=422, detail="support_email is not a valid address")
    if "custom_domain" in changes:
        domain = (changes["custom_domain"] or "").strip().lower().rstrip(".") or None
        if domain is not None and not _DOMAIN.match(domain):
            raise HTTPException(status_code=422, detail="custom_domain must be a hostname "
                                                        "like app.youragency.com")
        if domain != ws.custom_domain:
            taken = domain and db.execute(select(Workspace.id).where(
                Workspace.custom_domain == domain, Workspace.id != ws.id)).first()
            if taken:
                raise HTTPException(status_code=409, detail="that domain is already in use")
            ws.custom_domain = domain
            ws.domain_verified_at = None
            ws.domain_verification_token = secrets.token_hex(16) if domain else None
    for key in ("white_label_enabled", "brand_name", "primary_color", "support_email", "name",
                "approval_required"):
        if key in changes:
            value = changes[key]
            setattr(ws, key, value.strip() if isinstance(value, str) else value)
    db.commit()
    db.refresh(ws)
    return ws


def _txt_records(name: str) -> list[str]:
    """Factory tests monkeypatch."""
    import dns.resolver  # noqa: PLC0415

    answers = dns.resolver.resolve(name, "TXT", lifetime=5)
    return [b"".join(r.strings).decode(errors="replace") for r in answers]


def _cname(name: str) -> str | None:
    import dns.resolver  # noqa: PLC0415

    answers = dns.resolver.resolve(name, "CNAME", lifetime=5)
    return str(answers[0].target).rstrip(".").lower() if answers else None


def verify_domain(db: Session, ctx: Context) -> bool:
    """Verified when the TXT record `_leadpilot-verify.<domain>` carries the
    token, or the domain is a CNAME to the configured target."""
    _require(ctx, "owner", "Verifying a domain")
    ws = ctx.workspace
    if not ws.custom_domain:
        raise HTTPException(status_code=409, detail="set a custom domain first")
    ok = False
    try:
        ok = ws.domain_verification_token in _txt_records(f"{VERIFY_PREFIX}.{ws.custom_domain}")
    except Exception:  # noqa: BLE001 -- NXDOMAIN, timeout: simply not verified yet
        ok = False
    target = (settings.white_label_cname_target or "").rstrip(".").lower()
    if not ok and target:
        try:
            ok = _cname(ws.custom_domain) == target
        except Exception:  # noqa: BLE001
            ok = False
    if ok:
        ws.domain_verified_at = _now()
        db.commit()
    return ok


def branding_for_host(db: Session, host: str | None) -> dict:
    """Public: the branding a visitor to `host` should see."""
    host = (host or "").split(":", 1)[0].strip().lower().rstrip(".")
    ws = None
    base = (settings.white_label_base_domain or "").lower()
    if host and base and host.endswith("." + base):
        ws = db.execute(select(Workspace).where(
            Workspace.slug == host[: -len(base) - 1])).scalar_one_or_none()
    elif host:
        ws = db.execute(select(Workspace).where(Workspace.custom_domain == host,
                                                Workspace.domain_verified_at.isnot(None))
                        ).scalar_one_or_none()
    if ws is None or not ws.white_label_enabled:
        return default_branding()
    return branding_out(ws)
