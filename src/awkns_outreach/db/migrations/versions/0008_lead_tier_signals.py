"""lead tier + apollo signals

Revision ID: 0008_lead_tier_signals
Revises: 0007_mail_sequences
Create Date: 2026-07-10

Lead tiering foundation for the AI classifier + Task entity that land in
later tasks. Renames `lead.priority` -> `lead.tier` (values stay A|B|C, data
preserved) and adds two Apollo-sourced classifier signals: `seniority` and
`employee_count`. Also drops `campaign.tier`, which was never read by any
route/template/logic — verified dead via grep before dropping.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0008_lead_tier_signals"
down_revision: Union[str, None] = "0007_mail_sequences"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("lead", "priority", new_column_name="tier")
    op.add_column("lead", sa.Column("seniority", sa.String(), nullable=True))
    op.add_column("lead", sa.Column("employee_count", sa.Integer(), nullable=True))
    op.drop_column("campaign", "tier")


def downgrade() -> None:
    op.add_column("campaign", sa.Column("tier", sa.String(), nullable=True))
    op.drop_column("lead", "employee_count")
    op.drop_column("lead", "seniority")
    op.alter_column("lead", "tier", new_column_name="priority")
