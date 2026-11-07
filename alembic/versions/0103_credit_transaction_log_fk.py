"""Fix credit_transaction_log foreign key ondelete policy.

The credit_transaction_log table tracks user credit transactions and should
cascade delete when a user is deleted (GDPR erasure).

Revision ID: 0103_credit_transaction_log_fk
Revises: 0099_ppm_credits_limit
"""

import sqlalchemy as sa
from alembic import op

revision = "0103_credit_transaction_log_fk"
down_revision = "0099_ppm_credits_limit"


def upgrade() -> None:
    op.drop_constraint(
        "fk_credit_transaction_log_user_id",
        "credit_transaction_log",
        type_="foreignkey"
    )
    op.create_foreign_key(
        "fk_credit_transaction_log_user_id",
        "credit_transaction_log",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE"
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_credit_transaction_log_user_id",
        "credit_transaction_log",
        type_="foreignkey"
    )
    op.create_foreign_key(
        "fk_credit_transaction_log_user_id",
        "credit_transaction_log",
        "users",
        ["user_id"],
        ["id"],
        ondelete="RESTRICT"
    )
