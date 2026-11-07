"""Repair: add the three ppm_credits_* columns production never got.

WHAT HAPPENED. 0099_ppm_credits_limit shipped adding ONE column
(ppm_credits_limit_cents) and ran in production. Commit a7a2fc8 then edited
that already-applied revision to also add ppm_credits_expires_at,
ppm_credits_carryover_cents and ppm_credits_reset_day. Alembic never re-runs an
applied revision, so production's billing_subscriptions lacks those three
columns while the ORM model selects them -- every query that loads a
BillingSubscription (including POST /auth/login via plan_access) fails with
UndefinedColumn and returns 500. Sign-in was down.

THE FIX. Add the columns here, idempotently: a database that ran the edited
0099 (fresh installs, the test suite) already has them and is left alone.

DOWNGRADE is a no-op: on a fresh database these columns belong to 0099, and
dropping them here would break 0099's own downgrade.

Revision ID: 0105_ppm_credits_columns_repair
Revises: 0104_bulk_import
"""

import sqlalchemy as sa
from alembic import op

revision = "0105_ppm_credits_columns_repair"
down_revision = "0104_bulk_import"
branch_labels = None
depends_on = None

_COLUMNS = (
    ("ppm_credits_limit_cents", lambda: sa.Column("ppm_credits_limit_cents", sa.Integer, nullable=True)),
    ("ppm_credits_expires_at", lambda: sa.Column("ppm_credits_expires_at", sa.DateTime(timezone=True), nullable=True)),
    ("ppm_credits_carryover_cents", lambda: sa.Column("ppm_credits_carryover_cents", sa.Integer, nullable=True, server_default="0")),
    ("ppm_credits_reset_day", lambda: sa.Column("ppm_credits_reset_day", sa.Integer, nullable=True)),
)


def upgrade() -> None:
    existing = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("billing_subscriptions")}
    missing = [make() for name, make in _COLUMNS if name not in existing]
    if missing:
        with op.batch_alter_table("billing_subscriptions") as batch:
            for col in missing:
                batch.add_column(col)
    if "ppm_credits_reset_day" not in existing:
        op.execute(
            "UPDATE billing_subscriptions SET ppm_credits_reset_day = 1 "
            "WHERE billing_model = 'pay_per_meeting' AND ppm_credits_reset_day IS NULL"
        )


def downgrade() -> None:
    pass
