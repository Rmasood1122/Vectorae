"""users.onboarding_email_sent_at: the once-only marker for the onboarding email.

ADDS
  users.onboarding_email_sent_at  timestamptz NULL. Set when the onboarding
                                  email is claimed for sending, cleared again
                                  if the send fails (onboarding_email.py).

WHY. Before this, a new account got exactly one email -- the verification
link -- and nothing after it. The onboarding email goes out once, right after
the address is verified; this column is what makes "once" hold across a
retried Celery task, a double-clicked link and two workers racing.

No backfill. Every pre-existing row reads NULL, meaning "never received it",
which is true: no onboarding email existed before this revision. Nothing
sends to those rows automatically -- the send is triggered only by a fresh
verification -- so adding the column mails nobody.

Nullable add with no default: metadata-only on PostgreSQL, no table rewrite.

DOWNGRADE drops the column (the record of who was sent the email is lost).

Revision ID: 0098_onboarding_email
Revises: 0097_linkedin_consent_tasks
"""

import sqlalchemy as sa
from alembic import op

revision = "0098_onboarding_email"
down_revision = "0097_linkedin_consent_tasks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("onboarding_email_sent_at",
                                   sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("onboarding_email_sent_at")
