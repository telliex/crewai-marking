"""footer visual-builder layout column

Revision ID: 0012_footer_layout
Revises: 0011_footer_templates
Create Date: 2026-08-04

Adds a nullable `footer_template.layout` JSON column — the structured block
layout the visual builder edits. body_html/body_text stay the compiled,
sent fields; layout is NULL for legacy raw-HTML footers.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0012_footer_layout"
down_revision: Union[str, None] = "0011_footer_templates"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("footer_template", sa.Column("layout", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("footer_template", "layout")
