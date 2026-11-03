"""Write to, and send to, the user's OWN LinkedIn connections.

Every path (copy, one-click, bulk) shares these rules:
  * Growth plan or higher (checked again at send time, not just at queue time)
  * the connection and the product belong to the caller
  * what is sent is the text the user SAVED. A draft is never sent.
  * LinkedIn suppression list honoured
  * the per-account daily message cap honoured (linkedin_limits)
  * only people who are still 1st-degree connections; never a stranger
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    LinkedInConnection, LinkedInConnectionMatch, LinkedInNetworkMessage, Product, User,
)
from app.services import anthropic_client, linkedin_limits, prompt_safety, style_profile
from app.services import linkedin_network as ln

MESSAGE_MAX = 1500
BULK_MAX = 25
BULK_SPACING_SECONDS = 90

logger = logging.getLogger(__name__)

SYSTEM = (
    "You write ONE short LinkedIn message from a person to someone they are ALREADY "
    "connected with. Respond with ONLY a JSON object: {\"text\": \"...\"}. RULES. Plain "
    "text. Under 100 words. Warm and specific to the recipient's role and company. One "
    "easy question at the end. No links, hashtags, emojis, placeholders or flattery. "
    "Never invent facts about the recipient. Never claim a relationship you were not "
    "told about."
)

NETWORK_MESSAGE_SCHEMA = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
    "additionalProperties": False,
}


class MessageError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def _own_connection(db: Session, user: User, connection_id) -> LinkedInConnection:
    row = db.execute(select(LinkedInConnection).where(
        LinkedInConnection.id == connection_id, LinkedInConnection.user_id == user.id)).scalar_one_or_none()
    if row is None:
        raise MessageError(404, "Connection not found.")
    return row


def _own_product(db: Session, user: User, product_id) -> Product:
    row = db.execute(select(Product).where(
        Product.id == product_id, Product.user_id == user.id)).scalar_one_or_none()
    if row is None:
        raise MessageError(404, "Product not found.")
    return row


def own_message(db: Session, user: User, message_id) -> LinkedInNetworkMessage:
    row = db.execute(select(LinkedInNetworkMessage).where(
        LinkedInNetworkMessage.id == message_id,
        LinkedInNetworkMessage.user_id == user.id)).scalar_one_or_none()
    if row is None:
        raise MessageError(404, "Message not found.")
    return row


def draft(db: Session, user: User, connection_id, product_id) -> LinkedInNetworkMessage:
    conn = _own_connection(db, user, connection_id)
    product = _own_product(db, user, product_id)
    existing = db.execute(select(LinkedInNetworkMessage).where(
        LinkedInNetworkMessage.user_id == user.id,
        LinkedInNetworkMessage.connection_id == conn.id)).scalar_one_or_none()
    if existing is not None and existing.status in ("sent", "queued"):
        raise MessageError(409, "This message was already sent or is queued.")
    if conn.profile_url:
        from app.workers.lead_tasks import is_suppressed  # noqa: PLC0415

        if is_suppressed(db, linkedin=conn.profile_url):
            # Checked before any model call: nothing is drafted for a person
            # who asked not to be contacted.
            raise MessageError(409, "This person is on the do-not-contact list.")
    match = db.execute(select(LinkedInConnectionMatch).where(
        LinkedInConnectionMatch.user_id == user.id,
        LinkedInConnectionMatch.connection_id == conn.id,
        LinkedInConnectionMatch.product_id == product.id)).scalar_one_or_none()
    why = "; ".join(match.reasons_json or []) if match else ""
    # F-AI-02: name / position / company come from the LinkedIn export and
    # were written by the connection. The whole RECIPIENT line is one
    # delimited, escaped value folded onto a single line, so a newline in a
    # headline cannot open a new section. The product and match reasons (our
    # ICP terms) are OURS and stay unwrapped.
    prompt = "\n".join([
        prompt_safety.NOTICE,
        "WRITE THE MESSAGE.",
        prompt_safety.inline(
            f"RECIPIENT: {conn.full_name} - {conn.position or 'unknown role'}"
            f" at {conn.company or 'unknown company'}"),
        f"WHAT THE SENDER OFFERS: {product.name}. {(product.description or '')[:600]}",
        f"WHY THIS PERSON MAY BE RELEVANT: {why or 'role and company fit'}",
        "Return the JSON object now.",
    ])
    try:
        data = anthropic_client.get_client().complete_json(
            system=SYSTEM + style_profile.system_suffix(user.style_profile_json),
            prompt=prompt, max_tokens=600, schema=NETWORK_MESSAGE_SCHEMA)
    except Exception as exc:  # noqa: BLE001
        raise MessageError(502, "The writer is unavailable. Try again.") from exc
    text = _without_unsupported(
        str((data or {}).get("text") or "").strip(), prompt, conn.id)[:MESSAGE_MAX]
    if not text:
        raise MessageError(502, "The writer returned nothing. Try again.")
    if existing is None:
        existing = LinkedInNetworkMessage(user_id=user.id, connection_id=conn.id,
                                          product_id=product.id, text=text, status="draft")
        db.add(existing)
    else:
        existing.product_id, existing.text = product.id, text
        existing.status, existing.error, existing.channel = "draft", None, None
    db.commit()
    return existing


def _without_unsupported(text: str, prompt: str, connection_id) -> str:
    """T25: the draft without sentences our records cannot back.

    A LinkedIn connection tells us nothing about a meeting or a conversation,
    so a sentence that says or implies one ('great meeting you', 'as we
    discussed') is removed, as the email network refuses it without a two-way
    thread. A sentence stating a figure, funding round, offer or deadline the
    prompt (recipient, product, match reasons) lacks is removed too. The
    user's own edits in save() are theirs and are not checked here."""
    from app.services import grounding  # noqa: PLC0415
    from app.services.email_network_messages import _SPOKEN  # noqa: PLC0415

    kept, removed = [], []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        found = grounding.problems(sentence, prompt)
        if _SPOKEN.search(sentence):
            found.append("implies a conversation the records do not show")
        if found:
            removed.extend(found)
        elif sentence:
            kept.append(sentence)
    if removed:
        logger.warning("network message for connection %s: removed unsupported sentences: %s",
                       connection_id, "; ".join(removed))
    return " ".join(kept)


def save(db: Session, user: User, message_id, text: str) -> LinkedInNetworkMessage:
    msg = own_message(db, user, message_id)
    if msg.status in ("sent", "queued"):
        raise MessageError(409, "This message was already sent or is queued.")
    text = (text or "").strip()
    if not text or len(text) > MESSAGE_MAX:
        raise MessageError(422, f"Message must be 1 to {MESSAGE_MAX} characters.")
    msg.text, msg.status, msg.error = text, "approved", None
    db.commit()
    return msg


def mark_sent_manually(db: Session, user: User, message_id) -> LinkedInNetworkMessage:
    msg = own_message(db, user, message_id)
    if msg.status != "approved":
        raise MessageError(409, "Save the message first.")
    conn = db.get(LinkedInConnection, msg.connection_id)
    if conn is not None and conn.profile_url:
        from app.workers.lead_tasks import is_suppressed  # noqa: PLC0415

        if is_suppressed(db, linkedin=conn.profile_url):
            raise MessageError(409, "This person is on the do-not-contact list.")
    msg.status, msg.channel, msg.sent_at = "sent", "copy", datetime.now(timezone.utc)
    db.commit()
    return msg


def deliver(db: Session, user: User, msg: LinkedInNetworkMessage, channel: str) -> LinkedInNetworkMessage:
    """Send ONE saved message through the user's connected LinkedIn account.
    Raises MessageError with the status the API should answer; leaves the row
    in a state the user can act on."""
    from app.services import linkedin_outreach as lo  # noqa: PLC0415
    from app.workers.lead_tasks import is_suppressed  # noqa: PLC0415
    from app.integrations.unipile_linkedin import UnipileError  # noqa: PLC0415

    if not ln.is_eligible(user):
        raise MessageError(402, "Your plan does not include the LinkedIn network.")
    if msg.status not in ("approved", "queued"):
        raise MessageError(409, "Only a saved message can be sent.")
    conn = db.get(LinkedInConnection, msg.connection_id)
    if conn is None or conn.user_id != user.id:
        raise MessageError(404, "Connection not found.")
    slug = lo.normalize_profile(conn.profile_url)
    if not slug:
        raise _fail(db, msg, 409, "This connection has no LinkedIn profile link.")
    if is_suppressed(db, linkedin=conn.profile_url):
        raise _fail(db, msg, 409, "This person is on the do-not-contact list.")
    client = lo.client_for(db, user.id)
    if client is None or not lo.active_accounts(db, user.id):
        raise MessageError(409, "Connect a LinkedIn account in Settings first.")
    account = linkedin_limits.pick_account(db, user.id, "message")
    if account is None:
        raise MessageError(429, "Daily LinkedIn message limit reached. Try again tomorrow.")
    try:
        profile = client.profile(slug, account.unipile_account_id)
        if not profile.get("is_connected"):
            linkedin_limits.release(account, "message")
            raise _fail(db, msg, 409, "This person is no longer a 1st-degree connection.")
        client.start_chat(profile["provider_id"], account.unipile_account_id, msg.text)
    except UnipileError as exc:
        linkedin_limits.release(account, "message")
        # Unipile's text goes to the log; the message row and the 502 say
        # what happened without the provider's body (gaps-sweep-2 review L3).
        logger.warning("network message %s: Unipile send failed: %s", msg.id, exc)
        raise _fail(db, msg, 502, "LinkedIn send failed: the LinkedIn provider "
                                  "refused it. Try again later.") from exc
    now = datetime.now(timezone.utc)
    account.last_used_at = now
    msg.status, msg.channel, msg.sent_at, msg.error = "sent", channel, now, None
    db.commit()
    return msg


def _fail(db: Session, msg: LinkedInNetworkMessage, status: int, detail: str) -> MessageError:
    msg.status, msg.error = "failed", detail[:300]
    db.commit()
    return MessageError(status, detail)


def queue_bulk(db: Session, user: User, ids: list) -> list[LinkedInNetworkMessage]:
    if not ids or len(ids) > BULK_MAX:
        raise MessageError(422, f"Choose 1 to {BULK_MAX} messages per batch.")
    rows = [own_message(db, user, i) for i in ids]          # 404 on any foreign id
    bad = [r for r in rows if r.status != "approved"]
    if bad:
        raise MessageError(409, "Every message must be saved before it can be queued.")
    for r in rows:
        r.status, r.channel, r.error = "queued", "bulk", None
    db.commit()
    return rows
