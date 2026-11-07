"""Bulk CSV import tracking tables.

Add two tables to track bulk imports:
  - bulk_imports: the job (file, status, counts, timestamps)
  - bulk_import_rows: one row per CSV row (raw data, validation status, created lead)

Owned by account for export and erasure (GDPR Art. 15/17).

Revision ID: 0104_bulk_import
Revises: 0103_credit_transaction_log_fk
"""

import sqlalchemy as sa
from alembic import op

revision = "0104_bulk_import"
down_revision = "0103_credit_transaction_log_fk"


def upgrade() -> None:
    op.create_table(
        "bulk_imports",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("strategy_id", sa.UUID(), nullable=True),
        sa.Column("filename", sa.String(500), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("total_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("valid_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_leads", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("preview_json", sa.JSON(), nullable=True),
        sa.Column("error_summary", sa.JSON(), nullable=True),
        sa.Column("validation_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("validation_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("import_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("import_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_bulk_imports_user_id_users", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["strategy_id"], ["strategies.id"], name="fk_bulk_imports_strategy_id_strategies", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_bulk_imports"),
        sa.Index("ix_bulk_imports_user_id", "user_id"),
        sa.Index("ix_bulk_imports_strategy_id", "strategy_id"),
        sa.Index("ix_bulk_imports_status", "status"),
    )

    op.create_table(
        "bulk_import_rows",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("bulk_import_id", sa.UUID(), nullable=False),
        sa.Column("lead_id", sa.UUID(), nullable=True),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("raw_data_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("error_message", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["bulk_import_id"], ["bulk_imports.id"], name="fk_bulk_import_rows_bulk_import_id", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], name="fk_bulk_import_rows_lead_id_leads", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_bulk_import_rows"),
        sa.UniqueConstraint("bulk_import_id", "row_number", name="uq_bulk_import_row_number"),
        sa.Index("ix_bulk_import_rows_bulk_import_id", "bulk_import_id"),
        sa.Index("ix_bulk_import_rows_lead_id", "lead_id"),
        sa.Index("ix_bulk_import_rows_status", "status"),
    )


def downgrade() -> None:
    op.drop_table("bulk_import_rows")
    op.drop_table("bulk_imports")
