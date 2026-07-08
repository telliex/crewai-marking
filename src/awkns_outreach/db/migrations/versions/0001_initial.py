"""initial outreach schema: campaign, lead, event, suppression

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-04

Targets Postgres (JSONB + text[]). Hand-written so it does not require a live
DB to generate; regenerate with `alembic revision --autogenerate` once a
Postgres instance is available if the models drift.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "campaign",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
        sa.Column("tier", sa.Integer(), nullable=True),
        sa.Column("target_titles", postgresql.ARRAY(sa.String()), nullable=False,
                  server_default="{}"),
        sa.Column("seed_domains", postgresql.ARRAY(sa.String()), nullable=False,
                  server_default="{}"),
        sa.Column("sequence", postgresql.JSONB(), nullable=False,
                  server_default="[]"),
        sa.Column("angle_prompt", sa.String(), nullable=True),
        sa.Column("sender_identity", postgresql.JSONB(), nullable=False,
                  server_default="{}"),
        sa.Column("warmup_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "lead",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("campaign_id", sa.String(), nullable=False),
        sa.Column("apollo_person_id", sa.String(), nullable=True),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("company", sa.String(), nullable=False),
        sa.Column("contact_name", sa.String(), nullable=True),
        sa.Column("contact_title", sa.String(), nullable=True),
        sa.Column("country", sa.String(), nullable=True),
        sa.Column("category", sa.String(), nullable=True),
        sa.Column("priority", sa.String(), nullable=True),
        sa.Column("website", sa.String(), nullable=True),
        sa.Column("angle", sa.String(), nullable=True),
        sa.Column("vars", postgresql.JSONB(), nullable=True),
        sa.Column("step", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
        sa.Column("thread_ref", sa.String(), nullable=True),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_action_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaign.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("campaign_id", "email", name="uq_lead_campaign_email"),
    )
    op.create_index("ix_lead_due", "lead", ["status", "next_action_at"])

    op.create_table(
        "outreach_event",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("lead_id", sa.String(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("step", sa.Integer(), nullable=True),
        sa.Column("detail", sa.String(), nullable=True),
        sa.Column("subject", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["lead_id"], ["lead.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_event_lead_created", "outreach_event", ["lead_id", "created_at"])
    op.create_index("ix_event_type_created", "outreach_event", ["type", "created_at"])

    op.create_table(
        "outreach_suppression",
        sa.Column("email", sa.String(), primary_key=True),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("outreach_suppression")
    op.drop_index("ix_event_type_created", table_name="outreach_event")
    op.drop_index("ix_event_lead_created", table_name="outreach_event")
    op.drop_table("outreach_event")
    op.drop_index("ix_lead_due", table_name="lead")
    op.drop_table("lead")
    op.drop_table("campaign")
