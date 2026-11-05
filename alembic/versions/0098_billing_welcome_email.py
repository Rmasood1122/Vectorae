"""Billing: remember when the post-payment welcome email went out.

ADDS
  billing_subscriptions.welcome_email_sent_at  (nullable timestamp)

WHY. After a customer's first checkout completes, Vectorae emails them a
welcome (sign-in link + a link to book the onboarding call). Stripe can
deliver checkout.session.completed again, and a customer can check out again
to change plan; this column makes the email go out once per account.
No credentials are ever emailed: the customer set their own password.

Add-column only, nullable, no default, no backfill: existing paying accounts
read NULL but are never welcomed retroactively, because the email is sent
only from a NEW checkout.session.completed event. Portable types (SQLite
offline migration test and PostgreSQL). ADD COLUMN of a nullable column is a
metadata-only change on PostgreSQL.

DOWNGRADE drops the column (the record of who was welcomed is lost; a later
checkout could then send a second welcome).

Revision ID: 0098_billing_welcome_email
Revises: 0097_linkedin_consent_tasks
"""

import sqlalchemy as sa
from alembic import op

revision = "0098_billing_welcome_email"
down_revision = "0097_linkedin_consent_tasks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("billing_subscriptions") as batch:
        batch.add_column(sa.Column("welcome_email_sent_at", sa.DateTime(timezone=True),
                                   nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("billing_subscriptions") as batch:
        batch.drop_column("welcome_email_sent_at")
