"""F-DB-04, step 1 of 3: playbook_scores.score becomes nullable.

WHY
  `score` has only ever held a copy of `reply_rate`: the nightly upsert
  (app/workers/learning_tasks._upsert_playbook_score) binds :reply_rate into
  both, and nothing outside that upsert reads it. The founder approved
  dropping it (2026-10-25). It is dropped in three deploys, because the API
  runs migrations at start (scripts/render_api_entrypoint.sh) while the worker
  deploys on its own, so for a while either schema can meet either code:

    1. this revision: `score` becomes nullable; the code still writes it, and
       the trend is now computed from reply_rate (equal values). Old code and
       new code both work on the old and the new schema.
    2. the code stops writing `score` (safe once 1 is deployed everywhere).
    3. a revision drops the column (safe once 2 is deployed everywhere).

CHANGES
  playbook_scores.score  Float NOT NULL  ->  Float NULL. No data changes.

DOWNGRADE
  Fills any NULL `score` from reply_rate (0.0 where that is NULL too), then
  restores NOT NULL, so the pre-0095 code's upsert keeps working.

Revision ID: 0095_playbook_score_nullable
Revises: 0094_help_chunks
"""

import sqlalchemy as sa
from alembic import op

revision = "0095_playbook_score_nullable"
down_revision = "0094_help_chunks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("playbook_scores") as batch:
        batch.alter_column("score", existing_type=sa.Float(), nullable=True)


def downgrade() -> None:
    op.execute("UPDATE playbook_scores SET score = COALESCE(reply_rate, 0.0) "
               "WHERE score IS NULL")
    with op.batch_alter_table("playbook_scores") as batch:
        batch.alter_column("score", existing_type=sa.Float(), nullable=False)
