"""app_setting: runtime overrides for global config variables

Revision ID: 0013_app_setting
Revises: 0012_footer_layout
Create Date: 2026-08-05

A key/value table holding UI-editable overrides for the global config variables
(sender identity, API keys, AI model names). `key` is the env-var name; absence
of a row means "use the .env/default baseline". Seeded empty — no overrides
until an admin saves one on the Variables settings page.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0013_app_setting"
down_revision: Union[str, None] = "0012_footer_layout"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "app_setting",
        sa.Column("key", sa.String(), primary_key=True),
        sa.Column("value", sa.String(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("app_setting")
