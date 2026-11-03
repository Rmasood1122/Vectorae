"""LinkedIn consent records (Phase 5B step 3, evals/vectorae).

Every LinkedIn capability a user turns on is backed by a row in
linkedin_consents: who, which scope, for what purpose, under which version of
the consent text, when, from which origin (hashed), and when revoked. A
capability checks `has_current()` before it does anything; a changed text
version means the old consent no longer counts and the user consents again.

SCOPES
  connections_import  L3: the user uploads their own LinkedIn connections
                      export and attests to a lawful basis. Nothing from
                      LinkedIn is involved.
  (L1 openid/profile/email and L2 w_member_social are added here when those
  tiers are built; each gets its own text and version.)

The consent TEXT shown to the user lives here too, so the version a record
names is always the text the API served.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import LinkedInConsent

CONNECTIONS_IMPORT = "connections_import"

# GDPR Art. 6(1) bases a user can actually rely on to keep their own
# contacts' names and job titles for B2B outreach. "consent" is listed because
# some users will have it; most will rely on legitimate interests.
LAWFUL_BASES = ("legitimate_interests", "consent")


@dataclass(frozen=True)
class ConsentText:
    scope: str
    version: str
    purpose: str
    text: str


TEXTS: dict[str, ConsentText] = {
    CONNECTIONS_IMPORT: ConsentText(
        scope=CONNECTIONS_IMPORT,
        version="2026-11-02.1",
        purpose="Find people in your own LinkedIn network who fit your offer and draft "
                "messages you send yourself on LinkedIn.",
        text=(
            "You are uploading the Connections file that you downloaded from your own "
            "LinkedIn account (Settings > Data privacy > Get a copy of your data). "
            "It contains other people's names, companies and job titles.\n\n"
            "By uploading it you confirm that you have a lawful basis to use these people's "
            "data for business outreach (for most users, legitimate interests under GDPR "
            "Art. 6(1)(f) / UK GDPR), and that you will honour anyone who asks you to stop.\n\n"
            "What Vectorae does: keeps names, companies, job titles, profile links and "
            "connection dates in your account only; discards any email address or phone "
            "number in the file; scores the people against your offer without any AI "
            "model; drafts messages that YOU send on LinkedIn.\n\n"
            "What Vectorae never does: contact anyone on LinkedIn for you, add these people "
            "to email campaigns, share them with other accounts, or read anything from "
            "LinkedIn.\n\n"
            "You can delete the whole network at any time (LinkedIn network > Delete), "
            "which also cancels every open LinkedIn task built on it. People in your "
            "network are told nothing by Vectorae; if one of them asks what data you "
            "hold, the export in Settings shows it."
        ),
    ),
}


class ConsentRequired(PermissionError):
    """No current consent for the scope. The message is user-facing."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ip_hash(ip: str | None) -> str | None:
    if not ip:
        return None
    from app.config import settings  # noqa: PLC0415

    key = (getattr(settings, "jwt_secret", "") or "vectorae-consent").encode()
    return hmac.new(key, ip.encode(), hashlib.sha256).hexdigest()


def current_text(scope: str) -> ConsentText:
    return TEXTS[scope]


def record(db: Session, *, user_id, scope: str, text_version: str, lawful_basis: str | None,
           ip: str | None = None, workspace_id=None) -> LinkedInConsent:
    """Store a consent the user just gave. Refuses a stale text version (the
    user saw an older text) and an unknown lawful basis."""
    text = TEXTS.get(scope)
    if text is None:
        raise ValueError(f"unknown LinkedIn consent scope {scope!r}")
    if text_version != text.version:
        raise ConsentRequired("The consent text has changed. Reload and read it again.")
    if scope == CONNECTIONS_IMPORT and lawful_basis not in LAWFUL_BASES:
        raise ConsentRequired("Choose the lawful basis you rely on before uploading.")
    row = LinkedInConsent(
        id=uuid.uuid4(), user_id=user_id, workspace_id=workspace_id, scope=scope,
        purpose=text.purpose, consent_text_version=text.version, lawful_basis=lawful_basis,
        granted_at=_now(), ip_hash=_ip_hash(ip),
    )
    db.add(row)
    db.flush()
    return row


def current(db: Session, user_id, scope: str) -> LinkedInConsent | None:
    """The live consent for the scope at the CURRENT text version, if any."""
    text = TEXTS.get(scope)
    if text is None:
        return None
    return db.execute(
        select(LinkedInConsent)
        .where(LinkedInConsent.user_id == user_id, LinkedInConsent.scope == scope,
               LinkedInConsent.consent_text_version == text.version,
               LinkedInConsent.revoked_at.is_(None))
        .order_by(LinkedInConsent.granted_at.desc())
        .limit(1)
    ).scalars().first()


def require(db: Session, user_id, scope: str) -> LinkedInConsent:
    row = current(db, user_id, scope)
    if row is None:
        raise ConsentRequired("This needs your consent first.")
    return row


def revoke(db: Session, user_id, scope: str) -> int:
    """Revoke every live consent for the scope. Returns how many."""
    rows = db.execute(
        select(LinkedInConsent).where(LinkedInConsent.user_id == user_id,
                                      LinkedInConsent.scope == scope,
                                      LinkedInConsent.revoked_at.is_(None))
    ).scalars().all()
    now = _now()
    for row in rows:
        row.revoked_at = now
    return len(rows)
