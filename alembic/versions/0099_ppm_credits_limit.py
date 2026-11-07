"""billing_subscriptions PPM (pay-per-meeting) credits tracking.

ADDS
  billing_subscriptions.ppm_credits_limit_cents   integer nullable, default 6000 (cents).
                                                  Maximum credits a user can spend this
                                                  month on pay-per-meeting meetings before
                                                  they must purchase more.
  billing_subscriptions.ppm_credits_expires_at    datetime nullable. When the current
                                                  month's credits expire.
  billing_subscriptions.ppm_credits_carryover_cents  integer nullable, default 0.
                                                  Unused credits from previous month
                                                  carried over (up to 50% of limit).
  billing_subscriptions.ppm_credits_reset_day     integer nullable. Day of month
                                                  credits reset (1-31; NULL = 1st).

WHY. Pay-per-meeting users start with a $60 credit limit per month. Once they hit it,
meetings are not charged. Carryover and reset tracking allows month-to-month credit
management.

Backfill: All existing pay-per-meeting subscriptions get the default $60 limit (6000 cents)
and reset day 1. All monthly/annual subscriptions get NULL (they don't use credits).

DOWNGRADE drops all four columns.

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
        batch.add_column(sa.Column("ppm_credits_expires_at",
                                   sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("ppm_credits_carryover_cents",
                                   sa.Integer, nullable=True, server_default="0"))
        batch.add_column(sa.Column("ppm_credits_reset_day",
                                   sa.Integer, nullable=True))

    # Backfill: pay_per_meeting subscriptions get $60 (6000 cents) limit and reset day 1
    op.execute("""
        UPDATE billing_subscriptions
        SET ppm_credits_limit_cents = 6000, ppm_credits_reset_day = 1
        WHERE billing_model = 'pay_per_meeting'
    """)


def downgrade() -> None:
    with op.batch_alter_table("billing_subscriptions") as batch:
        batch.drop_column("ppm_credits_limit_cents")
        batch.drop_column("ppm_credits_expires_at")
        batch.drop_column("ppm_credits_carryover_cents")
        batch.drop_column("ppm_credits_reset_day")
