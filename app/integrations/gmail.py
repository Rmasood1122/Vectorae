"""Gmail adapter — Milestone 3.

Chunk 1 (this file's current state): OAuth 2.0 per user — auth URL,
code exchange, ENCRYPTED token storage, automatic refresh — plus the
OutreachChannel skeleton and health check.
Chunk 3 implements send(); Chunk 4 implements fetch_replies()/status().

Endpoints, scopes and request shapes were checked against Google's public
Gmail API and OAuth docs on 2026-10-27 (developers.google.com/workspace/
gmail/api, developers.google.com/identity/protocols/oauth2/web-server);
tests/test_gmail_api_contract.py pins what was checked.
Reuses the M2 plumbing (retry/backoff, structured logging, circuit
breaker) for all Gmail API calls via BaseHttpAdapter.
"""

import base64
import logging
import re
import uuid
from email.message import EmailMessage
from email.utils import parseaddr
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import ChannelType, GmailAccount, Lead, Message, Product, Strategy, User
from app.integrations.outreach_base import (
    InboundMessage,
    OutboundMessage,
    OutreachChannel,
    SendResult,
    register_channel,
)
from app.core.mime_policy import OUTBOUND_POLICY
from app.integrations.plumbing import BaseHttpAdapter, ExternalAPIError, OutcomeUnknown
from app.services import crypto

logger = logging.getLogger(__name__)

# Scope strings as in gmail/api/auth/scopes. gmail.readonly and gmail.compose
# are Restricted scopes (Google requires restricted-scope verification for
# public use); gmail.send is Sensitive.
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
    # Engagement Hub: app/integrations/google_meet.py creates a Calendar event
    # (which is the only way to mint a Google Meet link) on this same grant,
    # rather than putting the user through a second Google consent flow for
    # the same account.
    #
    # An account connected BEFORE this line existed does not gain the scope
    # retroactively. Feature 7: google_meet.calendar_scope_state() reads the
    # stored grant, Settings shows a Reconnect prompt, and meeting creation
    # refuses before calling Google (platform_action "reconnect_google"). The
    # reconnect adds the scope incrementally -- see include_granted_scopes below.
    "https://www.googleapis.com/auth/calendar.events",
    # Feature Group 7: "Log Meeting Outcome" saves the follow-up email as a
    # Gmail DRAFT for the user to review, which gmail.send cannot do. Same
    # retroactivity caveat as calendar.events: an older grant answers 403, and
    # create_draft() turns that into GmailScopeMissing ("reconnect Gmail").
    "https://www.googleapis.com/auth/gmail.compose",
]

GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1"

# handle-errors: 403s that are quota, not the request; Google says retry later.
_GMAIL_RETRY_LATER_REASONS = {"rateLimitExceeded", "userRateLimitExceeded",
                              "dailyLimitExceeded"}
# users.messages.list: maxResults up to 500; pages followed per poll.
_INBOX_PAGE_SIZE = 100
_INBOX_PAGES_MAX = 10
# Prospect addresses per `{from:a from:b ...}` search (Gmail's OR group).
_FROM_CHUNK = 25
_PLAIN_ADDRESS = re.compile(r"^[^\s\"'(){}<>,;:\[\]]+@[^\s\"'(){}<>,;:\[\]]+$")


class GmailNotConnected(Exception):
    pass


class ChannelNotReady(NotImplementedError):
    """Raised by capabilities that land in a later M3 chunk."""


# --------------------------------------------------------------------------
# Signed state for the OAuth round-trip (encrypts the user id + a nonce)
# --------------------------------------------------------------------------


def make_state(user_id: uuid.UUID) -> str:
    return crypto.encrypt_json({"user_id": str(user_id), "nonce": uuid.uuid4().hex})


def parse_state(state: str) -> uuid.UUID:
    try:
        return uuid.UUID(crypto.decrypt_json(state)["user_id"])
    except Exception as exc:
        raise ValueError("invalid or tampered OAuth state") from exc


# --------------------------------------------------------------------------
# OAuth flow
# --------------------------------------------------------------------------


class GmailOAuth:
    """Auth URL generation, code exchange, token refresh (plain OAuth 2.0)."""

    def __init__(self, http: httpx.Client | None = None):
        self.http = http or httpx.Client(timeout=30.0)

    def build_auth_url(self, state: str) -> str:
        params = {
            "client_id": settings.google_client_id,
            "redirect_uri": settings.google_redirect_uri,
            "response_type": "code",
            "scope": " ".join(GMAIL_SCOPES),
            "access_type": "offline",   # required for a refresh_token
            "prompt": "consent",        # re-consent so refresh_token is always returned
            # Incremental authorization (Google OAuth 2.0 web-server docs,
            # checked 2026-09-13): the new token also covers scopes granted
            # earlier, so reconnecting for calendar.events keeps Gmail access.
            "include_granted_scopes": "true",
            "state": state,
        }
        return f"{GOOGLE_AUTH_ENDPOINT}?{urlencode(params)}"

    def exchange_code(self, code: str) -> dict:
        resp = self.http.post(GOOGLE_TOKEN_ENDPOINT, data={
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": settings.google_redirect_uri,
        })
        resp.raise_for_status()
        return resp.json()  # access_token, refresh_token, expires_in, scope, ...

    def refresh(self, refresh_token: str) -> dict:
        resp = self.http.post(GOOGLE_TOKEN_ENDPOINT, data={
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        })
        resp.raise_for_status()
        return resp.json()  # access_token, expires_in (no new refresh_token usually)

    def fetch_profile_email(self, access_token: str) -> str | None:
        """The connected mailbox's address (users.getProfile: emailAddress)."""
        try:
            resp = self.http.get(
                f"{GMAIL_API_BASE}/users/me/profile",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            resp.raise_for_status()
            return resp.json().get("emailAddress")
        except httpx.HTTPError:
            logger.warning("could not fetch Gmail profile email", exc_info=True)
            return None


def get_oauth() -> GmailOAuth:
    """Factory the API layer uses — tests monkeypatch THIS function."""
    return GmailOAuth()


# --------------------------------------------------------------------------
# Encrypted token storage + automatic refresh
# --------------------------------------------------------------------------


def _expiry_from(tokens: dict) -> datetime | None:
    expires_in = tokens.get("expires_in")
    if expires_in is None:
        return None
    return datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))


def store_tokens(session: Session, user: User, tokens: dict,
                 oauth: GmailOAuth | None = None) -> GmailAccount:
    """Create/update the user's GmailAccount with ENCRYPTED tokens."""
    oauth = oauth or get_oauth()
    account = session.query(GmailAccount).filter_by(user_id=user.id).one_or_none()
    if account is None:
        account = GmailAccount(user_id=user.id)
        session.add(account)

    existing: dict = {}
    if account.token_ciphertext:
        try:
            existing = crypto.decrypt_json(account.token_ciphertext)
        except ValueError:
            existing = {}
    # Google omits refresh_token on re-auth — never lose the one we have.
    merged = {**existing, **{k: v for k, v in tokens.items() if v is not None}}
    if "refresh_token" not in merged:
        raise GmailNotConnected(
            "Google did not return a refresh_token — re-run the auth flow "
            "(prompt=consent) so offline access is granted."
        )

    account.token_ciphertext = crypto.encrypt_json(merged)
    account.token_expires_at = _expiry_from(tokens) or account.token_expires_at
    account.scopes = tokens.get("scope") or account.scopes or " ".join(GMAIL_SCOPES)
    if tokens.get("access_token"):
        account.email_address = (
            oauth.fetch_profile_email(tokens["access_token"]) or account.email_address
        )
    session.commit()
    return account


def get_valid_access_token(session: Session, account: GmailAccount,
                           oauth: GmailOAuth | None = None) -> str:
    """Return a live access token, refreshing (and re-encrypting) if it
    expires within the configured leeway."""
    oauth = oauth or get_oauth()
    tokens = crypto.decrypt_json(account.token_ciphertext)

    leeway = timedelta(seconds=settings.gmail_token_refresh_leeway_seconds)
    expires_at = account.token_expires_at
    needs_refresh = (
        not tokens.get("access_token")
        or expires_at is None
        or expires_at <= datetime.now(timezone.utc) + leeway
    )
    if needs_refresh:
        refreshed = oauth.refresh(tokens["refresh_token"])
        tokens = {**tokens, **{k: v for k, v in refreshed.items() if v is not None}}
        account.token_ciphertext = crypto.encrypt_json(tokens)
        account.token_expires_at = _expiry_from(refreshed) or account.token_expires_at
        session.commit()
        logger.info("gmail access token refreshed for account %s", account.id)

    return tokens["access_token"]


def get_account(session: Session, user: User) -> GmailAccount:
    account = session.query(GmailAccount).filter_by(user_id=user.id).one_or_none()
    if account is None:
        raise GmailNotConnected(f"user {user.email} has no connected Gmail account")
    return account


# --------------------------------------------------------------------------
# The channel (skeleton — send lands in Chunk 3, replies in Chunk 4)
# --------------------------------------------------------------------------


def build_mime(from_address: str | None, message: OutboundMessage) -> EmailMessage:
    """The exact RFC-2822 message GmailChannel.send transmits.

    A module function (not inlined in send) so the F03 launch preflight can
    inspect the bytes the send path really produces -- including whether the
    one-click unsubscribe headers survive MIME assembly -- without a mailbox,
    a token or a network call.
    """
    # OUTBOUND_POLICY: long structured headers (List-Unsubscribe with its
    # token, a long Message-ID in In-Reply-To/References) go out raw on one
    # line; the default policy RFC-2047-encoded them (mime_policy.py).
    mime = EmailMessage(policy=OUTBOUND_POLICY)
    mime["From"] = from_address or "me"
    mime["To"] = message.to_address
    if message.subject:
        mime["Subject"] = message.subject
    for name, value in (message.headers or {}).items():
        mime[name] = value
    mime.set_content(message.body)
    # Feature Group 3: the engine may supply an HTML twin (open pixel).
    # Plain text stays the first part, so text-only clients and spam
    # filters see exactly the message that was written.
    html_part = (message.metadata or {}).get("html_body")
    if html_part:
        mime.add_alternative(html_part, subtype="html")
    return mime


@register_channel
class GmailChannel(BaseHttpAdapter, OutreachChannel):
    channel = "email"
    provider = "gmail"
    base_url = GMAIL_API_BASE

    def __init__(self, account: GmailAccount, session: Session,
                 oauth: GmailOAuth | None = None, **kwargs):
        super().__init__(**kwargs)
        self.account = account
        self.session = session
        self.oauth = oauth or get_oauth()

    def _auth(self) -> dict:
        token = get_valid_access_token(self.session, self.account, self.oauth)
        return {"headers": {"Authorization": f"Bearer {token}"}}

    # ---- sending ---------------------------------------------------------
    def send(self, message: OutboundMessage) -> SendResult:
        """Send one RFC-2822 message via the Gmail API. Compliance headers
        (List-Unsubscribe etc.) arrive pre-built in message.headers — this
        channel only transports them."""
        mime = build_mime(self.account.email_address, message)
        raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()
        payload: dict = {"raw": raw}
        if message.thread_ref:
            # Gmail threads a message only with threadId AND RFC 2822
            # In-Reply-To/References AND a matching Subject (users.messages);
            # the headers come in message.headers when the caller has them.
            payload["threadId"] = message.thread_ref

        try:
            # users.messages.send. Never retried once it may have reached Gmail:
            # a timeout or 5xx does not prove the email was not sent.
            data = self.call("POST", "/users/me/messages/send", json_body=payload,
                             idempotent=False)
        except OutcomeUnknown as exc:
            return SendResult(
                ok=False,
                error=(f"{exc} -- Gmail did not confirm this send, so it is not "
                       "resent automatically; check the mailbox's Sent folder"),
                permanent_failure=True,
                raw={"status": exc.status, "outcome_unknown": True},
            )
        except Exception as exc:
            status_code = getattr(exc, "status", None)
            reason = exc.provider_reason() if isinstance(exc, ExternalAPIError) else None
            # handle-errors: these 403s are quota, not the recipient; retry later.
            rate_limited = status_code == 429 or reason in _GMAIL_RETRY_LATER_REASONS
            return SendResult(
                ok=False,
                error=str(exc),
                permanent_failure=status_code in (400, 403) and not rate_limited,
                raw={"status": status_code, "reason": reason},
            )
        rfc_message_id, rfc_references = self._sent_threading_headers(data.get("id"))
        return SendResult(
            ok=True,
            provider_message_id=data.get("id"),
            thread_ref=data.get("threadId"),
            rfc_message_id=rfc_message_id,
            rfc_references=rfc_references,
            raw=data,
        )

    def _sent_threading_headers(self, gmail_id: str | None) -> tuple[str | None, str | None]:
        """(Message-ID, References) of a message just sent, read back from
        Gmail (users.messages.get, format=metadata) rather than set by us:
        what the prospect's client threads on is the id Gmail actually sent.
        A read failure costs only the threading of the NEXT email (it falls
        back to threadId only); the send itself already succeeded, so this
        never raises."""
        if not gmail_id:
            return None, None
        from app.services import email_threading  # noqa: PLC0415

        try:
            data = self.call("GET", f"/users/me/messages/{gmail_id}",
                             params={"format": "metadata",
                                     "metadataHeaders": ["Message-ID", "References"]})
        except Exception as exc:  # noqa: BLE001 -- see the docstring
            logger.warning("gmail sent-message headers unavailable for %s: %s",
                           gmail_id, type(exc).__name__)
            return None, None
        headers = {h.get("name", "").lower(): h.get("value", "")
                   for h in ((data or {}).get("payload") or {}).get("headers") or []}
        return (email_threading.clean_message_id(headers.get("message-id")),
                email_threading.clean_references(headers.get("references")))

    # ---- inbound ----------------------------------------------------------
    def fetch_replies(self, since: datetime | None = None) -> list[InboundMessage]:
        """New inbound messages for this mailbox. Uses a Gmail search query
        window; message payloads are fetched individually and parsed
        tolerantly. users.messages.list returns ids only (paged by
        nextPageToken); users.messages.get fetches each one.

        `after:<epoch seconds>` is not in Google's current search docs (they
        show dates such as after:2004/04/16) but is the exact cut-off this
        poll needs: a date would re-read up to a day of mail, and replies
        are not de-duplicated downstream.

        Only two kinds of message are fetched (the privacy policy, section
        3: "We do not read, store or use other email in your mailbox"):
          * a message in a Gmail thread that holds an email Vectorae sent for
            this account's owner -- the list call returns each message's id
            and threadId, so the thread decides before anything is fetched;
          * a new email FROM a prospect Vectorae has emailed for this owner
            (a reply written as a fresh message, e.g. "please stop") -- found
            by a Gmail search on `from:` those addresses, so Gmail filters
            and other senders' mail is never listed.
        Other mail is never downloaded, stored or sent to a model. Threads
        are matched by threadId: sent rows keep Gmail's threadId
        (Message.thread_ref) but not their own Message-ID, so In-Reply-To /
        References cannot be matched yet."""
        query = "in:inbox -from:me"
        if since is not None:
            query += f" after:{int(since.timestamp())}"
        wanted: list[str] = []
        for stubs in self._listed(query):
            ours = self._vectorae_threads({s.get("threadId") for s in stubs} - {None})
            wanted += [s["id"] for s in stubs if s.get("threadId") in ours]
        addresses = self._emailed_lead_addresses()
        for i in range(0, len(addresses), _FROM_CHUNK):
            senders = " ".join(f"from:{a}" for a in addresses[i:i + _FROM_CHUNK])
            for stubs in self._listed(f"{query} {{{senders}}}"):
                wanted += [s["id"] for s in stubs]
        out: list[InboundMessage] = []
        for msg_id in dict.fromkeys(wanted):   # de-duplicated, order kept
            data = self.call("GET", f"/users/me/messages/{msg_id}",
                             params={"format": "full"})
            out.append(self._parse_inbound(data))
        return out

    def _listed(self, query: str):
        """users.messages.list pages for `query`: lists of {id, threadId}."""
        params: dict = {"q": query, "maxResults": _INBOX_PAGE_SIZE}
        for _ in range(_INBOX_PAGES_MAX):
            listing = self.call("GET", "/users/me/messages", params=params)
            yield [s for s in listing.get("messages") or [] if s.get("id")]
            token = listing.get("nextPageToken")
            if not token:
                return
            params = {**params, "pageToken": token}

    def _owned_lead_ids(self):
        """Leads this account's owner owns (the same ownership join as
        outreach_tasks._route_inbound)."""
        return (
            select(Lead.id)
            .join(Strategy, Strategy.id == Lead.strategy_id)
            .join(Product, Product.id == Strategy.product_id)
            .where(Product.user_id == self.account.user_id)
        )

    def _emailed_lead_addresses(self) -> list[str]:
        """Addresses of the owner's leads that Vectorae has sent an email to.
        Only plain addresses go into a Gmail query; anything with a space,
        quote, brace or parenthesis is left out rather than escaped."""
        rows = self.session.execute(
            select(Lead.email).join(Message, Message.lead_id == Lead.id)
            .where(Lead.id.in_(self._owned_lead_ids()), Lead.email.isnot(None),
                   Message.channel == ChannelType.EMAIL, Message.sent_at.isnot(None))
        ).scalars()
        return sorted({e.strip().lower() for e in rows if _PLAIN_ADDRESS.match(e.strip())})

    def _vectorae_threads(self, thread_ids: set[str]) -> set[str]:
        """The threads among `thread_ids` that hold a message Vectorae sent to
        a lead this account's owner owns."""
        if not thread_ids:
            return set()
        return set(self.session.execute(
            select(Message.thread_ref).where(Message.thread_ref.in_(thread_ids),
                                             Message.lead_id.in_(self._owned_lead_ids()))
        ).scalars())

    def status(self, provider_message_id: str) -> dict:
        data = self.call("GET", f"/users/me/messages/{provider_message_id}",
                         params={"format": "metadata"})
        return {"labelIds": data.get("labelIds", []), "threadId": data.get("threadId")}

    _AUTO_REPLY_HEADERS = ("auto-submitted", "x-autoreply", "x-autorespond",
                           "x-auto-response-suppress", "precedence", "x-mailer")

    @staticmethod
    def _parse_inbound(data: dict) -> InboundMessage:
        payload = data.get("payload") or {}
        headers = {h.get("name", "").lower(): h.get("value", "")
                   for h in payload.get("headers") or []}

        def _decode(part: dict) -> str:
            body = (part.get("body") or {}).get("data")
            if not body:
                return ""
            try:
                return base64.urlsafe_b64decode(body + "===").decode(errors="replace")
            except Exception:
                return ""

        text = ""
        if payload.get("mimeType", "").startswith("text/"):
            text = _decode(payload)
        for part in payload.get("parts") or []:
            if part.get("mimeType") == "text/plain":
                text = _decode(part) or text
                break
        if not text:
            text = data.get("snippet", "")

        _, from_addr = parseaddr(headers.get("from", ""))
        _, to_addr = parseaddr(headers.get("to", ""))
        raw = {"labelIds": data.get("labelIds", [])}
        # Feature A3: keep the handful of headers auto-responders set (RFC
        # 3834 Auto-Submitted and friends) -- the strongest evidence that a
        # "reply" is a machine. Only when present, and never anything else.
        auto = {name: headers[name] for name in GmailChannel._AUTO_REPLY_HEADERS
                if headers.get(name)}
        if auto:
            raw["headers"] = auto
        # gaps-sweep-2 #6: the reply's own Message-ID and References chain, so
        # an answer can set In-Reply-To / References and thread in the
        # prospect's client too (app/services/email_threading.py sanitises).
        from app.services import email_threading  # noqa: PLC0415

        return InboundMessage(
            provider_message_id=data.get("id", ""),
            thread_ref=data.get("threadId"),
            from_address=from_addr or headers.get("from", ""),
            to_address=to_addr or None,
            subject=headers.get("subject"),
            body=text,
            received_at=None,
            raw=raw,
            rfc_message_id=email_threading.clean_message_id(headers.get("message-id")),
            rfc_references=email_threading.clean_references(headers.get("references"),
                                                            headers.get("in-reply-to")),
        )

    # ---- live now --------------------------------------------------------
    def health_check(self) -> bool:
        if self.breaker.is_open():
            return False
        try:
            # users.getProfile (needs readonly or compose, both requested).
            self.call("GET", "/users/me/profile")
            return True
        except Exception:
            return False


# --------------------------------------------------------------------------
# Drafts (Feature Group 7 — the post-meeting follow-up)
# --------------------------------------------------------------------------
#
# Deliberately NOT routed through GmailChannel/BaseHttpAdapter. The channel is
# the OUTREACH transport: its circuit breaker, caps and send windows govern
# cold email, and a user saving one draft for a prospect they just met is not
# outreach volume. Sharing the breaker would let a burst of draft failures
# pause the user's campaigns, and vice versa.


class GmailScopeMissing(GmailNotConnected):
    """The grant predates gmail.compose — the user must reconnect Gmail."""


def _gmail_http() -> httpx.Client:
    """Factory tests monkeypatch."""
    return httpx.Client(base_url=GMAIL_API_BASE, timeout=30.0)


def _raw_message(from_addr: str | None, to: str, subject: str, body: str,
                 reply_headers: dict | None = None) -> str:
    mime = EmailMessage(policy=OUTBOUND_POLICY)
    mime["From"] = from_addr or "me"
    mime["To"] = to
    mime["Subject"] = subject
    for name, value in (reply_headers or {}).items():
        mime[name] = value
    mime.set_content(body)
    return base64.urlsafe_b64encode(mime.as_bytes()).decode()


def _drafts_call(session: Session, account: GmailAccount, method: str, path: str,
                 payload: dict, oauth: GmailOAuth | None = None) -> dict:
    token = get_valid_access_token(session, account, oauth)
    with _gmail_http() as http:
        resp = http.request(method, path, json=payload,
                            headers={"Authorization": f"Bearer {token}"})
    if resp.status_code == 403:
        raise GmailScopeMissing(
            "Gmail refused to create a draft (403). This connection was made "
            "before draft access was requested -- reconnect Gmail in Settings."
        )
    resp.raise_for_status()
    return resp.json()


def create_draft(session: Session, account: GmailAccount, *, to: str,
                 subject: str, body: str, thread_ref: str | None = None,
                 reply_headers: dict | None = None,
                 oauth: GmailOAuth | None = None) -> dict:
    """users.drafts.create. Returns {"id": draftId, "message": {...}}.

    threadId alone does not thread a draft: Gmail also needs In-Reply-To /
    References and a matching Subject (users.messages). `reply_headers` are
    those headers (email_threading.reply_headers) when the parent's
    Message-ID is stored (migration 0096); a different Subject still opens a
    new conversation in Gmail, though other clients thread on the headers."""
    message: dict = {"raw": _raw_message(account.email_address, to, subject, body,
                                         reply_headers)}
    if thread_ref:
        message["threadId"] = thread_ref
    return _drafts_call(session, account, "POST", "/users/me/drafts",
                        {"message": message}, oauth)


def update_draft(session: Session, account: GmailAccount, draft_id: str, *,
                 to: str, subject: str, body: str,
                 thread_ref: str | None = None, reply_headers: dict | None = None,
                 oauth: GmailOAuth | None = None) -> dict:
    """users.drafts.update (full replace)."""
    message: dict = {"raw": _raw_message(account.email_address, to, subject, body,
                                         reply_headers)}
    if thread_ref:
        message["threadId"] = thread_ref
    return _drafts_call(session, account, "PUT", f"/users/me/drafts/{draft_id}",
                        {"id": draft_id, "message": message}, oauth)


def send_draft(session: Session, account: GmailAccount, draft_id: str,
               oauth: GmailOAuth | None = None) -> dict:
    """users.drafts.send. Returns the sent Message resource."""
    return _drafts_call(session, account, "POST", "/users/me/drafts/send",
                        {"id": draft_id}, oauth)
