"""sender_profile library + campaign.sender_profile_id

Revision ID: 0015_sender_profile
Revises: 0014_task_identity_snapshot
Create Date: 2026-08-06

A library of named sender identities ("sender groups"). A Campaign selects one
via a new nullable `sender_profile_id` FK (NULL = use the global Variables
identity). Missing profile fields fall back per-field to the global default;
a deleted profile degrades to the global default via ON DELETE SET NULL.
Seeded empty — no profile rows until an operator creates one.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0015_sender_profile"
down_revision: Union[str, None] = "0014_task_identity_snapshot"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sender_profile",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("from_email", sa.String(), nullable=False, server_default=""),
        sa.Column("from_name", sa.String(), nullable=False, server_default=""),
        sa.Column("reply_to", sa.String(), nullable=False, server_default=""),
        sa.Column("sender_name", sa.String(), nullable=False, server_default=""),
        sa.Column("company", sa.String(), nullable=False, server_default=""),
        sa.Column("postal_address", sa.String(), nullable=False, server_default=""),
        sa.Column("unsubscribe_mailto", sa.String(), nullable=False, server_default=""),
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.add_column("campaign", sa.Column("sender_profile_id", sa.String(), nullable=True))
    op.create_foreign_key(
        "fk_campaign_sender_profile_id", "campaign", "sender_profile",
        ["sender_profile_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_campaign_sender_profile_id", "campaign", type_="foreignkey")
    op.drop_column("campaign", "sender_profile_id")
    op.drop_table("sender_profile")
