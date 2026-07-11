"""add email_template.attachments

Revision ID: 0006_email_template_attachments
Revises: 0005_email_template_status
Create Date: 2026-07-10

Real outgoing-email attachments for the template library, distinct from
inline body images: list of {filename, stored_name, content_type, size}.
Existing rows default to an empty list.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0006_email_template_attachments"
down_revision: Union[str, None] = "0005_email_template_status"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "email_template",
        sa.Column("attachments", sa.JSON(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("email_template", "attachments")
