"""task send-window overrides

Revision ID: 0010_task_send_window_overrides
Revises: 0009_tasks_restructure
Create Date: 2026-07-26

Adds two per-task booleans that relax the sequencer's send-window gate:
`ignore_business_hours` (bypass the recipient-local 09:00–17:00 check) and
`ignore_workdays` (bypass the Mon–Fri check). Both default false, so existing
tasks keep today's business-hours-only sending.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0010_task_send_window_overrides"
down_revision: Union[str, None] = "0009_tasks_restructure"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "task",
        sa.Column("ignore_business_hours", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
    )
    op.add_column(
        "task",
        sa.Column("ignore_workdays", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("task", "ignore_workdays")
    op.drop_column("task", "ignore_business_hours")
