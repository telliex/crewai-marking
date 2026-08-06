"""task.identity_snapshot: freeze sender identity per running task

Revision ID: 0014_task_identity_snapshot
Revises: 0013_app_setting
Create Date: 2026-08-05

Adds a nullable `task.identity_snapshot` JSON column. Populated at start_task
with the sender identity resolved from the global settings at that moment, so a
mid-run change to a sender-identity variable (e.g. OUTREACH_FROM) doesn't alter
an already-running task — the new value only applies to tasks started later.
NULL for legacy/not-running tasks → the send path re-resolves live.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0014_task_identity_snapshot"
down_revision: Union[str, None] = "0013_app_setting"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("task", sa.Column("identity_snapshot", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("task", "identity_snapshot")
