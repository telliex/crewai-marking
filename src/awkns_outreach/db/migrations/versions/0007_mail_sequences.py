"""add mail_sequence

Revision ID: 0007_mail_sequences
Revises: 0006_email_template_attachments
Create Date: 2026-07-10

Mail Sequences: a standalone marketing campaign that targets one existing
Campaign ("Group") and holds an ordered list of email steps cloned from
EmailTemplate rows. New table only, no backfill needed. The
(campaign_id, status) index backs the "one active sequence per group" check
and the tasks page query.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0007_mail_sequences"
down_revision: Union[str, None] = "0006_email_template_attachments"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mail_sequence",
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
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("steps", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(
        "ix_mail_sequence_campaign_status",
        "mail_sequence",
        ["campaign_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_mail_sequence_campaign_status", table_name="mail_sequence")
    op.drop_table("mail_sequence")
