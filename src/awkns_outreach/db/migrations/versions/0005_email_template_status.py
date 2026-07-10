"""add email_template.status

Revision ID: 0005_email_template_status
Revises: 0004_mailboxes_templates
Create Date: 2026-07-10

Template list gains archive/unarchive, mirroring campaign.status: a new
status column defaulting existing rows to "active" so nothing already
saved disappears from the default list view.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0005_email_template_status"
down_revision: Union[str, None] = "0004_mailboxes_templates"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "email_template",
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
    )


def downgrade() -> None:
    op.drop_column("email_template", "status")
