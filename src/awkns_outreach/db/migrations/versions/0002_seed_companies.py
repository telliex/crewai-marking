"""replace campaign.seed_domains (text[]) with seed_companies (jsonb)

Revision ID: 0002_seed_companies
Revises: 0001_initial
Create Date: 2026-07-07

The flat `seed_domains` string list could not carry per-company metadata
(name/country/category/priority/angle). Replace it with `seed_companies`, a
JSONB list of dicts (companies.json shape). Existing domains are backfilled as
`{"website": <domain>}` so no seed data is lost.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0002_seed_companies"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "campaign",
        sa.Column("seed_companies", postgresql.JSONB(), nullable=False,
                  server_default="[]"),
    )
    # Backfill: each old domain string -> {"website": <domain>}.
    op.execute(
        """
        UPDATE campaign
        SET seed_companies = COALESCE(
            (SELECT jsonb_agg(jsonb_build_object('website', d))
             FROM unnest(seed_domains) AS d),
            '[]'::jsonb
        )
        """
    )
    op.drop_column("campaign", "seed_domains")


def downgrade() -> None:
    op.add_column(
        "campaign",
        sa.Column("seed_domains", postgresql.ARRAY(sa.String()), nullable=False,
                  server_default="{}"),
    )
    op.execute(
        """
        UPDATE campaign
        SET seed_domains = COALESCE(
            (SELECT array_agg(elem->>'website')
             FROM jsonb_array_elements(seed_companies) AS elem
             WHERE elem->>'website' IS NOT NULL),
            '{}'::text[]
        )
        """
    )
    op.drop_column("campaign", "seed_companies")
