"""add mailbox, email_template, campaign.mailbox_id, lead.last_message_id

Revision ID: 0004_mailboxes_templates
Revises: 0003_campaign_description
Create Date: 2026-07-09

Apollo-style mailbox model: connect a Gmail account via OAuth and send a
campaign's sequence as that account instead of the implicit Resend default
(NULL campaign.mailbox_id keeps today's behaviour exactly). Also adds a
standalone email_template library and lead.last_message_id for Gmail
In-Reply-To/References threading. No backfill needed — all new, nullable
where the row can predate the feature.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0004_mailboxes_templates"
down_revision: Union[str, None] = "0003_campaign_description"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mailbox",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("provider", sa.String(), nullable=False, server_default="gmail"),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=True),
        sa.Column("access_token", sa.String(), nullable=True),
        sa.Column("refresh_token", sa.String(), nullable=True),
        sa.Column("token_expiry", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scopes", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="connected"),
        sa.Column("last_error", sa.String(), nullable=True),
        sa.Column("last_poll_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("email", name="uq_mailbox_email"),
    )

    op.create_table(
        "email_template",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("subject", sa.String(), nullable=False),
        sa.Column("body", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.add_column("campaign", sa.Column("mailbox_id", sa.String(), nullable=True))
    op.create_foreign_key(
        "fk_campaign_mailbox_id", "campaign", "mailbox", ["mailbox_id"], ["id"],
        ondelete="SET NULL",
    )

    op.add_column("lead", sa.Column("last_message_id", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("lead", "last_message_id")

    op.drop_constraint("fk_campaign_mailbox_id", "campaign", type_="foreignkey")
    op.drop_column("campaign", "mailbox_id")

    op.drop_table("email_template")
    op.drop_table("mailbox")
