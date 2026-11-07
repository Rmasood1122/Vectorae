"""Fix credit_transaction_log foreign key ondelete policy.

The credit_transaction_log table tracks user credit transactions and should
cascade delete when a user is deleted (GDPR erasure).

GUARDED (hotfix). No migration or model ever creates credit_transaction_log,
so on the production database this revision raised on drop_constraint and
crash-looped every deploy from c40c48d on. It is now a no-op unless the table
and the named constraint actually exist. It never committed anywhere it failed
(transactional DDL), so changing it is safe.

Revision ID: 0103_credit_transaction_log_fk
Revises: 0099_ppm_credits_limit
"""

import sqlalchemy as sa
from alembic import op

revision = "0103_credit_transaction_log_fk"
down_revision = "0099_ppm_credits_limit"


_FK = "fk_credit_transaction_log_user_id"


def _fk_exists() -> bool:
    insp = sa.inspect(op.get_bind())
    if not insp.has_table("credit_transaction_log"):
        return False
    return any(fk.get("name") == _FK for fk in insp.get_foreign_keys("credit_transaction_log"))


def upgrade() -> None:
    if not _fk_exists():
        return
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
    if not _fk_exists():
        return
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
