""""Scan my connected Gmail" for the email network -- HEADERS ONLY.

Google's Gmail API, checked against the official reference on 2026-10-12
(developers.google.com/workspace/gmail/api/reference/rest/v1):
  * users.messages.list  GET /users/me/messages  q, maxResults (default 100,
    max 500), pageToken -> messages[] (id, threadId only), nextPageToken.
    q supports Gmail search syntax; `after:` accepts seconds
    (guides/filtering). q cannot be used with the gmail.metadata scope -- the
    existing grant has gmail.readonly, which allows it.
  * users.messages.get   GET /users/me/messages/{id}  format=metadata
    ("Returns only email message ID, labels, and email headers") and
    metadataHeaders[] ("When given and format is METADATA, only include
    headers specified"). Requires gmail.readonly (or modify / metadata /
    mail.google.com).
  * `fields` (partial response, guides/performance) restricts the reply to
    id, internalDate, labelIds and payload/headers -- so not even the
    `snippet` (a short part of the body text) is returned to us.
  * Quota: 6,000 units per user per minute; messages.get costs 20 units,
    messages.list 5. One chunk below = 1 list + up to 100 gets = 2,005 units,
    and chunks are spaced CHUNK_SPACING_SECONDS apart (about 4,000/min).
UNVERIFIED (no live call was made from here): that repeated metadataHeaders
query parameters are encoded as metadataHeaders=From&metadataHeaders=To (the
standard Google API convention for repeated fields), and the exact error codes
Google returns for an expired / narrowed grant (401 and 403 are both treated
as "reconnect Gmail").

Only the connected Gmail account(s) of the scanning account are read: the
account row is looked up by the scan row's own user_id, never by an id the
caller sends. No model calls.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import EmailNetworkImport, GmailAccount, User
from app.services import email_network as en
from app.services import email_network_filter as flt
from app.services import email_network_parse as parse

logger = logging.getLogger(__name__)

KIND = "gmail_scan"
LOOKBACK = timedelta(days=365)
MAX_MESSAGES = 5_000
PAGE_SIZE = 100
CHUNK_SPACING_SECONDS = 30
COOLDOWN = timedelta(hours=1)
STALE_RUNNING = timedelta(hours=2)
METADATA_HEADERS = ("From", "To", "Cc", "Date", "List-Unsubscribe", "List-Id",
                    "Precedence", "Auto-Submitted")
GET_FIELDS = "id,internalDate,labelIds,payload/headers"
LIST_FIELDS = "messages/id,nextPageToken"
READONLY_SCOPES = ("https://www.googleapis.com/auth/gmail.readonly",
                   "https://www.googleapis.com/auth/gmail.modify",
                   "https://mail.google.com/")

QUEUED, RUNNING, DONE, FAILED, RECONNECT = ("queued", "running", "done", "failed",
                                            "reconnect_required")
ACTIVE = (QUEUED, RUNNING)


class ScanError(Exception):
    def __init__(self, status: int, detail: str, code: str | None = None):
        super().__init__(detail)
        self.status, self.detail, self.code = status, detail, code


class Transient(Exception):
    """A temporary Gmail failure (429 / 5xx / network): retry the chunk."""


class NeedsReconnect(Exception):
    """The grant no longer allows reading headers (401 / 403)."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def account_for(db: Session, user_id) -> GmailAccount | None:
    """The scanning account's OWN connected Gmail. Never another account's.
    Vectorae stores one Gmail connection per account
    (integrations/gmail.store_tokens); should there ever be several, the
    oldest connection is used -- a deterministic, documented choice."""
    return db.execute(select(GmailAccount).where(GmailAccount.user_id == user_id)
                      .order_by(GmailAccount.created_at, GmailAccount.id)).scalars().first()


def scope_ok(account: GmailAccount) -> bool:
    granted = set((account.scopes or "").replace(",", " ").split())
    return any(s in granted for s in READONLY_SCOPES)


def latest_scan(db: Session, user_id) -> EmailNetworkImport | None:
    return db.execute(select(EmailNetworkImport)
                      .where(EmailNetworkImport.user_id == user_id,
                             EmailNetworkImport.kind == KIND)
                      .order_by(EmailNetworkImport.created_at.desc(),
                                EmailNetworkImport.id.desc())).scalars().first()


def scan_out(row: EmailNetworkImport | None) -> dict | None:
    if row is None:
        return None
    out = en.import_out(row)
    out["max_messages"] = MAX_MESSAGES
    return out


def gmail_state(db: Session, user: User) -> dict:
    account = account_for(db, user.id)
    return {"connected": account is not None,
            "address": account.email_address if account else None,
            "can_scan": bool(account and scope_ok(account)),
            "reconnect_required": bool(account and not scope_ok(account)),
            "scan": scan_out(latest_scan(db, user.id))}


def start_scan(db: Session, user: User, *, attested: bool, own_extra=(),
               now: datetime | None = None) -> tuple[EmailNetworkImport, bool]:
    """(scan row, True when a NEW scan must be enqueued). Idempotent: a scan
    already queued or running is returned as it is."""
    now = now or _now()
    if not attested:
        raise ScanError(422, "Confirm that these are your own business contacts first.")
    account = account_for(db, user.id)
    if account is None:
        raise ScanError(409, "Connect Gmail in Settings first.", code="gmail_not_connected")
    if not scope_ok(account):
        raise ScanError(409, "Your Gmail connection does not allow reading message headers. "
                             "Reconnect Gmail in Settings.", code="reconnect_gmail")
    latest = latest_scan(db, user.id)
    if latest is not None and latest.status in ACTIVE:
        updated = en._aware(latest.updated_at) or now  # noqa: SLF001
        if now - updated < STALE_RUNNING:
            return latest, False
        _mark(db, latest, FAILED, "The scan stopped responding; start it again.")
    # Review round: the cooldown counts from the START of the last scan,
    # whatever became of it (done, failed, reconnect required).
    if latest is not None and latest.created_at is not None and \
            now - en._aware(latest.created_at) < COOLDOWN:  # noqa: SLF001
        raise ScanError(429, "A Gmail scan was started less than an hour ago. "
                             "Try again later.")
    try:
        row = en.new_import(db, user, KIND, attested=True, status=QUEUED)
    except IntegrityError:
        # Another request started a scan a moment ago: the partial UNIQUE
        # index uq_email_network_imports_active_scan allows one active scan
        # per account. Return that one.
        db.rollback()
        return latest_scan(db, user.id), False
    row.tally_json = {"own_extra": flt.parse_own_addresses(list(own_extra or [])),
                      "after": int((now - LOOKBACK).timestamp())}
    db.commit()
    return row, True


def sweep_stale(db: Session, now: datetime | None = None) -> int:
    """Fail every scan (any account) queued or running with no progress for
    STALE_RUNNING, and clear its transient tallies. Run by the weekly
    refresh whatever the flag. Commits."""
    now = now or _now()
    rows = db.execute(select(EmailNetworkImport).where(
        EmailNetworkImport.kind == KIND, EmailNetworkImport.status.in_(ACTIVE))).scalars().all()
    failed = 0
    for row in rows:
        updated = en._aware(row.updated_at) or now  # noqa: SLF001
        if now - updated >= STALE_RUNNING:
            _mark(db, row, FAILED, "The scan stopped responding; start it again.")
            failed += 1
    return failed


# --------------------------------------------------------------------------
# One chunk (a list page and its metadata gets)
# --------------------------------------------------------------------------


def _gmail_http():
    from app.integrations import gmail  # noqa: PLC0415

    return gmail._gmail_http()  # noqa: SLF001 -- the module's own monkeypatchable factory


def _get(http, path: str, params, token: str) -> dict:
    try:
        resp = http.request("GET", path, params=params,
                            headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as exc:
        raise Transient(type(exc).__name__) from exc  # provider text only in the log chain
    if resp.status_code in (401, 403):
        raise NeedsReconnect(f"HTTP {resp.status_code}")
    if resp.status_code == 429 or resp.status_code >= 500:
        raise Transient(f"HTTP {resp.status_code}")
    if resp.status_code >= 400:
        raise RuntimeError(f"Gmail answered HTTP {resp.status_code}")
    return resp.json() or {}


def metadata_params() -> list[tuple[str, str]]:
    return ([("format", "metadata")] + [("metadataHeaders", h) for h in METADATA_HEADERS]
            + [("fields", GET_FIELDS)])


def _message_into(tally: parse.Tally, data: dict, now: datetime) -> None:
    """Only payload.headers, labelIds and internalDate are read. Anything else
    in the reply (there should be nothing: `fields` asks for nothing else) is
    ignored."""
    headers: dict[str, str] = {}
    for h in ((data.get("payload") or {}).get("headers") or []):
        name = str(h.get("name") or "").lower()
        if name in parse.WANTED_HEADERS:
            value = str(h.get("value") or "")
            if name in headers and name in ("to", "cc"):
                headers[name] += ", " + value
            elif name not in headers:
                headers[name] = value
    when = None
    try:
        ms = int(data.get("internalDate") or 0)
        if ms:
            when = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        when = None
    if when is not None and not (parse._EARLIEST <= when <= now + parse._FUTURE_SLACK):  # noqa: SLF001
        when = None
    parse.message_from_headers(tally, headers, labels=data.get("labelIds") or [],
                               when=when, now=now)


def run_chunk(db: Session, scan_id, expected_seq: int, now: datetime | None = None) -> str:
    """Process one page. Returns "continue", "done", "stale" (another worker
    already did this chunk), "stopped", "reconnect" or raises Transient.
    The tally, the page token and the chunk counter are committed together
    with a conditional UPDATE on chunk_seq, so a duplicate delivery of the
    same chunk can never count a message twice."""
    now = now or _now()
    scan_id = uuid.UUID(str(scan_id))
    row = db.get(EmailNetworkImport, scan_id)
    if row is None or row.kind != KIND or row.status not in ACTIVE:
        return "stopped"
    if row.chunk_seq != expected_seq:
        return "stale"
    user = db.get(User, row.user_id)
    account = account_for(db, row.user_id)
    if user is None or account is None or not scope_ok(account):
        _mark(db, row, RECONNECT, "Reconnect Gmail in Settings, then scan again.")
        return "reconnect"
    state = dict(row.tally_json or {})
    own = flt.own_identity(db, user, state.get("own_extra") or [])
    tally = parse.Tally.from_json(state.get("tally"), own)
    remaining = MAX_MESSAGES - (row.messages_scanned or 0)
    if remaining <= 0:
        en.store(db, user, row, tally, source="gmail")
        return "done"

    from app.integrations import gmail  # noqa: PLC0415

    try:
        token = gmail.get_valid_access_token(db, account)
    except Exception as exc:  # noqa: BLE001 -- a refresh refused = the grant is gone
        logger.warning("gmail scan %s: token refresh failed: %s", row.id, type(exc).__name__)
        _mark(db, row, RECONNECT, "Reconnect Gmail in Settings, then scan again.")
        return "reconnect"
    params: list[tuple[str, str]] = [("q", f"after:{int(state.get('after') or 0)}"),
                                     ("maxResults", str(min(PAGE_SIZE, remaining))),
                                     ("fields", LIST_FIELDS)]
    if row.page_token:
        params.append(("pageToken", row.page_token))
    scanned = 0
    try:
        with _gmail_http() as http:
            listing = _get(http, "/users/me/messages", params, token)
            ids = [m.get("id") for m in (listing.get("messages") or []) if m.get("id")]
            for mid in ids[:remaining]:
                data = _get(http, f"/users/me/messages/{mid}", metadata_params(), token)
                _message_into(tally, data, now)
                scanned += 1
    except NeedsReconnect:
        db.rollback()
        _mark(db, db.get(EmailNetworkImport, scan_id), RECONNECT,
              "Gmail refused access. Reconnect Gmail in Settings, then scan again.")
        return "reconnect"
    # Review round M2: people touched in this chunk who are suppressed
    # (unsubscribed / erased) or excluded are dropped from the tally now,
    # not held until the end of the scan.
    if tally.touched:
        lists = flt.load_blocklists(db, row.user_id, tally.touched)
        for email in list(tally.touched):
            reason = lists.reason(email)
            if reason is not None:
                tally.drop(email, reason)
    next_token = listing.get("nextPageToken")
    total = (row.messages_scanned or 0) + scanned
    capped = total >= MAX_MESSAGES and bool(next_token)
    finished = not next_token or total >= MAX_MESSAGES
    state["tally"] = tally.to_json()
    claimed = db.execute(
        update(EmailNetworkImport)
        .where(EmailNetworkImport.id == row.id, EmailNetworkImport.chunk_seq == expected_seq,
               EmailNetworkImport.status.in_(ACTIVE))
        .values(chunk_seq=expected_seq + 1, status=RUNNING, messages_scanned=total,
                rows_in_file=total, page_token=None if finished else next_token,
                tally_json=state, updated_at=now)
        .execution_options(synchronize_session=False))
    if (claimed.rowcount or 0) != 1:
        db.rollback()
        return "stale"
    db.commit()
    if not finished:
        return "continue"
    db.refresh(row)
    if capped:
        row.cap_note = (f"Only the {MAX_MESSAGES:,} most recent messages of the last 12 "
                        "months were read.")
    en.store(db, user, row, tally, source="gmail")
    return "done"


def _mark(db: Session, row: EmailNetworkImport | None, status: str, error: str) -> None:
    if row is None:
        return
    row.status, row.error, row.tally_json = status, error[:300], None
    row.finished_at = _now()
    db.commit()


def fail(db: Session, scan_id, error: str) -> None:
    """Give up on a scan after its retries (the transient tallies go)."""
    _mark(db, db.get(EmailNetworkImport, uuid.UUID(str(scan_id))), FAILED, error)
