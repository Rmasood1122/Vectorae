"""Unsubscribe endpoint — CAN-SPAM one-click, no login, instant.

GET renders a plain confirmation page (human clicked the footer link);
POST supports RFC 8058 one-click (List-Unsubscribe-Post). Both do the
same thing immediately: suppression + hard stop of every sequence for
that lead. Invalid or tampered tokens are rejected safely with no
information leak.
"""

import logging

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.db.models import Lead, SuppressionEntry
from app.services import sequence_engine as engine

logger = logging.getLogger(__name__)

router = APIRouter(tags=["unsubscribe"])

_CONFIRM_HTML = """<!doctype html>
<html><head><title>Unsubscribed</title></head>
<body style="font-family: sans-serif; max-width: 480px; margin: 80px auto;">
<h2>You're unsubscribed.</h2>
<p>You will not receive any further emails from this sender. This took
effect immediately.</p>
</body></html>"""

_INVALID_HTML = """<!doctype html>
<html><head><title>Invalid link</title></head>
<body style="font-family: sans-serif; max-width: 480px; margin: 80px auto;">
<h2>This unsubscribe link is not valid.</h2>
<p>The link may be incomplete. If you keep receiving unwanted email,
reply with "unsubscribe" and you will be removed immediately.</p>
</body></html>"""


def _suppress_address(db: Session, email: str) -> None:
    """Honour an unsubscribe whose lead record no longer holds the address --
    the lead was GDPR-erased, or the sending account was deleted. CAN-SPAM
    (15 U.S.C. 7704(a)(4)) and CASL require the request to be honoured; the
    address travels in the (encrypted) token for exactly this case. Global
    suppression + a consent-ledger event with no lead and no account."""
    from app.services import consent  # noqa: PLC0415
    from app.workers.lead_tasks import is_suppressed  # noqa: PLC0415

    address = email.strip().lower()
    if not is_suppressed(db, email=address):
        db.add(SuppressionEntry(email=address, reason="unsubscribed_link"))
    consent.record(db, lead=None, kind=consent.SUPPRESSED, channel=consent.EMAIL,
                   source="unsubscribe_link", identifier=address,
                   detail="unsubscribed via link after the sender's records were erased",
                   commit=False)
    db.commit()
    from app.services import email_network  # noqa: PLC0415

    email_network.purge_address(db, address)    # never raises


def _process(token: str, db: Session) -> HTMLResponse:
    try:
        lead_id, email = engine.parse_unsubscribe_payload(token)
    except Exception:
        return HTMLResponse(_INVALID_HTML, status_code=400)
    lead = db.get(Lead, lead_id)
    if lead is not None and lead.email:
        engine.unsubscribe_lead(db, lead, source="link")
    elif email:
        # The lead is gone (account deleted) or a GDPR tombstone: the address
        # in the token is the only copy left -- suppress it.
        _suppress_address(db, email)
    elif lead is not None:
        engine.unsubscribe_lead(db, lead, source="link")
    # else: a token minted before the address was carried, whose lead is gone.
    # Nothing can be recorded; the account that sent it no longer exists and
    # can send nothing more through Vectorae.
    return HTMLResponse(_CONFIRM_HTML, status_code=200)


@router.get("/unsubscribe/{token}", response_class=HTMLResponse)
def unsubscribe_get(token: str, db: Session = Depends(get_db)) -> HTMLResponse:
    return _process(token, db)


@router.post("/unsubscribe/{token}", response_class=HTMLResponse)
def unsubscribe_post(token: str, db: Session = Depends(get_db)) -> HTMLResponse:
    """RFC 8058 one-click target (mail clients POST here)."""
    return _process(token, db)
