"""One-off, READ ONLY: count stored inbox emails that match no Vectorae thread.

WHY
    Until tests/test_gmail_inbox_scope.py's guard, the Gmail reply poll
    downloaded every inbox message not sent by the user and
    outreach_tasks._route_inbound stored each one as an InboundReply (with its
    subject and body), even when it answered no email Vectorae sent. The
    privacy policy (section 3) says we do not store other email. This script
    counts the rows already stored so the founder can decide what to do with
    them. It deletes nothing and has no option to.

WHAT COUNTS AS UNRELATED
    An email InboundReply whose thread_ref is empty, or is not the thread of
    any Message sent to a lead owned by the user of the Gmail account it
    arrived in (InboundReply.account_ref = GmailAccount.id) -- the same test
    GmailChannel.fetch_replies now applies before downloading anything.

USAGE
    python -m scripts.count_unrelated_inbox_replies
"""

from __future__ import annotations

import uuid
from collections import Counter

from sqlalchemy import select

from app.db.base import SessionLocal
from app.db.models import GmailAccount, InboundReply, Lead, Message, Product, Strategy

NO_ACCOUNT = "(gmail account no longer exists)"


def _owned_threads(session, user_id) -> set[str]:
    owned_lead_ids = (
        select(Lead.id)
        .join(Strategy, Strategy.id == Lead.strategy_id)
        .join(Product, Product.id == Strategy.product_id)
        .where(Product.user_id == user_id)
    )
    return set(session.execute(
        select(Message.thread_ref).where(Message.thread_ref.isnot(None),
                                         Message.lead_id.in_(owned_lead_ids))
    ).scalars())


def _account(session, account_ref):
    try:
        return session.get(GmailAccount, uuid.UUID(str(account_ref)))
    except (TypeError, ValueError):
        return None


def count_unrelated(session) -> Counter:
    """{account email or NO_ACCOUNT: number of unrelated stored emails}."""
    counts: Counter = Counter()
    threads_by_user: dict = {}
    for reply in session.execute(
        select(InboundReply).where(InboundReply.channel == "email")
    ).scalars():
        account = _account(session, reply.account_ref)
        if account is None:
            counts[NO_ACCOUNT] += 1
            continue
        if account.user_id not in threads_by_user:
            threads_by_user[account.user_id] = _owned_threads(session, account.user_id)
        if not reply.thread_ref or reply.thread_ref not in threads_by_user[account.user_id]:
            counts[account.email_address or str(account.id)] += 1
    return counts


def main() -> int:
    session = SessionLocal()
    try:
        counts = count_unrelated(session)
        print(f"Unrelated stored inbox emails: {sum(counts.values())}")
        for who, n in counts.most_common():
            print(f"  {n:>6}  {who}")
        print("Nothing was deleted.")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
