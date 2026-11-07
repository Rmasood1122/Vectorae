"""billing_subscriptions.ppm_credits_limit_cents: enforce $60 credit limit on pay-per-meeting plans.

ADDS
  billing_subscriptions.ppm_credits_limit_cents  integer NOT NULL, default 6000 (cents).
                                                 Maximum credits a user can spend this
                                                 month on pay-per-meeting meetings before
                                                 they must purchase more.

WHY. Pay-per-meeting users start with a $60 credit limit per month. Once they hit it,
meetings are not charged and a message prompts them to purchase more credits. This
field tracks the limit per subscription so it can be increased when users buy more.

Backfill: All existing pay-per-meeting subscriptions get the default $60 limit (6000 cents).
All monthly/annual subscriptions get NULL (they don't use this limit).

Server default 6000 ensures newly created subscriptions get the limit without needing
to query the code.

DOWNGRADE drops the column (all users lose their limit tracking; the next upgrade
re-establishes it at the default).

Revision ID: 0099_ppm_credits_limit
Revises: 0098_onboarding_email
"""

import sqlalchemy as sa
from alembic import op

revision = "0099_ppm_credits_limit"
down_revision = "0098_onboarding_email"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("billing_subscriptions") as batch:
        batch.add_column(sa.Column("ppm_credits_limit_cents",
                                   sa.Integer, nullable=True))

    # Backfill: pay_per_meeting subscriptions get $60 (6000 cents), others get NULL
    op.execute("""
        UPDATE billing_subscriptions
        SET ppm_credits_limit_cents = 6000
        WHERE billing_model = 'pay_per_meeting'
    """)


def downgrade() -> None:
    with op.batch_alter_table("billing_subscriptions") as batch:
        batch.drop_column("ppm_credits_limit_cents")
