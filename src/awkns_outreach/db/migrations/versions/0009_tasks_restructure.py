"""tasks restructure

Revision ID: 0009_tasks_restructure
Revises: 0008_lead_tier_signals
Create Date: 2026-07-12

The big cutover: `MailSequence` becomes pure reusable content (like
EmailTemplate — no campaign_id, no lifecycle) and a new `Task` entity becomes
the send campaign. A Task picks one Campaign, assigns a sequence per lead
tier (`sequences` = `{"A": seq_id, "B": seq_id, "C": seq_id}`, partial
allowed), owns a schedule window (`scheduled_start_at` + optional `end_at`),
owns the lifecycle (draft/scheduled/running/paused/stopped/completed), and
owns the execution snapshot (`steps_by_tier`, populated at start, cleared at
stop). `Campaign.sequence` is dropped entirely — it was the source of every
status-drift hack in the codebase; dropping it makes any missed consumer
fail loudly instead of silently misbehaving on a changed JSON shape.

DEPLOY PREREQUISITE: stop any running/scheduled MailSequence before applying
this upgrade. Any in-flight send halts at upgrade time — this is intentional;
recreate the send as a Task afterward. There is no automatic sequence->task
backfill (internal tool, minimal production data); the six old MailSequence
lifecycle statuses collapse to a single content-status `active` on upgrade.

Downgrade is best-effort: MailSequence gets its campaign_id/lifecycle
columns back (nullable — no data to backfill them with), the task table is
dropped, and campaign.sequence is re-added empty. MailSequence rows cannot
be reconnected to a Campaign or have their original lifecycle status
restored; the downgrade does not attempt to recreate Task-owned assignments
or in-flight sends.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0009_tasks_restructure"
down_revision: Union[str, None] = "0008_lead_tier_signals"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "task",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column(
            "campaign_id",
            sa.String(),
            sa.ForeignKey("campaign.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(), nullable=False, server_default="draft"),
        sa.Column("scheduled_start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sequences", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("steps_by_tier", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_task_campaign_status", "task", ["campaign_id", "status"])

    op.drop_index("ix_mail_sequence_campaign_status", table_name="mail_sequence")
    op.drop_column("mail_sequence", "campaign_id")
    op.drop_column("mail_sequence", "scheduled_start_at")
    op.drop_column("mail_sequence", "started_at")
    op.drop_column("mail_sequence", "completed_at")
    op.execute("UPDATE mail_sequence SET status = 'active'")
    op.alter_column("mail_sequence", "status", server_default="active")

    op.drop_column("campaign", "sequence")


def downgrade() -> None:
    op.add_column("campaign", sa.Column("sequence", sa.JSON(), nullable=False, server_default="[]"))

    op.alter_column("mail_sequence", "status", server_default="draft")
    op.add_column("mail_sequence", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("mail_sequence", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("mail_sequence", sa.Column("scheduled_start_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "mail_sequence",
        sa.Column("campaign_id", sa.String(), sa.ForeignKey("campaign.id", ondelete="CASCADE"), nullable=True),
    )
    op.execute("UPDATE mail_sequence SET status = 'draft'")
    op.create_index("ix_mail_sequence_campaign_status", "mail_sequence", ["campaign_id", "status"])

    op.drop_index("ix_task_campaign_status", table_name="task")
    op.drop_table("task")
