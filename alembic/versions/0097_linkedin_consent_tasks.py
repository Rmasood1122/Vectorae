"""LinkedIn, user-permission-based: consent records and assisted tasks.

CREATES
  linkedin_consents  one row per consent/attestation a user gave for one
                     LinkedIn scope (scope, purpose, consent_text_version,
                     lawful_basis, granted_at, ip_hash, revoked_at). A changed
                     consent text is a new row (re-consent), never an edit.
  linkedin_tasks     L0: one LinkedIn action the USER performs on LinkedIn
                     (drafted text, the prospect's public profile URL, a
                     per-task approval that expires, done/skipped/replied).

WHY. evals/vectorae gap GAP-LI-01: LinkedIn may only be reached through an
official API the user authorized or by the user themselves. These tables
hold the consent evidence and the user's task queue. Nothing here calls
LinkedIn.

Create-only. user_id is ON DELETE CASCADE on both tables, and connection_id /
lead_id on linkedin_tasks, so erasing an account, a lead or the network
erases the tasks with it. Portable types: the same file runs on SQLite
(offline migration test) and PostgreSQL. New empty tables take no lock on
existing ones beyond the FK reference check.

DOWNGRADE drops both tables (consent records and pending tasks are lost).

Revision ID: 0097_linkedin_consent_tasks
Revises: 0096_message_threading
"""

import sqlalchemy as sa
from alembic import op

revision = "0097_linkedin_consent_tasks"
down_revision = "0096_message_threading"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "linkedin_consents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=True),
        sa.Column("scope", sa.String(60), nullable=False),
        sa.Column("purpose", sa.String(300), nullable=False),
        sa.Column("consent_text_version", sa.String(40), nullable=False),
        sa.Column("lawful_basis", sa.String(40), nullable=True),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ip_hash", sa.String(64), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_linkedin_consents_user_id", "linkedin_consents", ["user_id"])

    op.create_table(
        "linkedin_tasks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("connection_id", sa.Uuid(),
                  sa.ForeignKey("linkedin_connections.id", ondelete="CASCADE"), nullable=True),
        sa.Column("lead_id", sa.Uuid(), sa.ForeignKey("leads.id", ondelete="CASCADE"),
                  nullable=True),
        sa.Column("profile_url", sa.String(500), nullable=False),
        sa.Column("profile_key", sa.String(200), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approval_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_reason", sa.String(60), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_linkedin_tasks_user_id", "linkedin_tasks", ["user_id"])
    op.create_index("ix_linkedin_tasks_connection_id", "linkedin_tasks", ["connection_id"])
    op.create_index("ix_linkedin_tasks_lead_id", "linkedin_tasks", ["lead_id"])
    op.create_index("ix_linkedin_tasks_profile_key", "linkedin_tasks", ["profile_key"])


def downgrade() -> None:
    op.drop_table("linkedin_tasks")
    op.drop_table("linkedin_consents")
