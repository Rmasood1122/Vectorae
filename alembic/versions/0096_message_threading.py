"""Outbound email threading headers: messages.rfc_message_id / rfc_references.

ADDS
  messages.rfc_message_id   VARCHAR(502), nullable -- the sent email's own
                            Message-ID, as Gmail recorded it
  messages.rfc_references   TEXT, nullable -- its References chain

WHY. Sequence follow-ups and meeting follow-up drafts were sent with Gmail's
threadId only. Gmail threads with threadId AND In-Reply-To / References AND a
matching Subject (users.messages); every other mail client threads on
In-Reply-To / References alone (RFC 5322 section 3.6.4). Without the parent's
Message-ID a follow-up arrived in the prospect's client as a new email. The
Gmail adapter now reads the Message-ID of each email it sends back from Gmail
and stores it here; the next email in the conversation replies to it.

No backfill: emails sent before this migration keep NULL and their follow-ups
go out as before (threadId only). No index (rows are found by lead and
sequence, never by these columns). Only additive; portable types, so the same
file runs on SQLite (offline migration test) and PostgreSQL.

LOCKING (PostgreSQL): adding nullable columns with no default is a catalog
change that still takes ACCESS EXCLUSIVE on messages for an instant. Bounded
with lock_timeout = 15s (as 0086): a stuck lock fails the migration and the
old instance keeps serving.

DOWNGRADE drops both columns. Stored Message-IDs are lost; follow-ups then
thread by Gmail threadId only (the pre-0096 behaviour).

Revision ID: 0096_message_threading
Revises: 0095_playbook_score_nullable
"""

import sqlalchemy as sa
from alembic import op

revision = "0096_message_threading"
down_revision = "0095_playbook_score_nullable"
branch_labels = None
depends_on = None


def _bounded_lock_wait() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '15s'")


def upgrade() -> None:
    _bounded_lock_wait()
    op.add_column("messages", sa.Column("rfc_message_id", sa.String(502), nullable=True))
    op.add_column("messages", sa.Column("rfc_references", sa.Text(), nullable=True))


def downgrade() -> None:
    _bounded_lock_wait()
    with op.batch_alter_table("messages") as batch:
        batch.drop_column("rfc_references")
        batch.drop_column("rfc_message_id")
