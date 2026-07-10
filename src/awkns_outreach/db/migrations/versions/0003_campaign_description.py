"""add campaign.description

Revision ID: 0003_campaign_description
Revises: 0002_seed_companies
Create Date: 2026-07-09

The dashboard now shows a second line under each campaign name so operators
juggling several campaigns can tell them apart at a glance. Plain nullable
text — no backfill needed.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003_campaign_description"
down_revision: Union[str, None] = "0002_seed_companies"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("campaign", sa.Column("description", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("campaign", "description")
