"""footer templates library + per-email footer choice

Revision ID: 0011_footer_templates
Revises: 0010_task_send_window_overrides
Create Date: 2026-08-02

Adds a `footer_template` library (a reusable email footer: brand block +
unsubscribe link) and seeds one immutable `is_default` row with the current
Pounds Network footer. Emails select a footer via a new nullable
`email_template.footer_choice` ("default"/NULL | "none" | "<id>"); sequence
steps carry the same key inside their JSON (no column). The seeded body carries
a `{unsubscribe_url}` placeholder, substituted per-recipient at send time.
"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0011_footer_templates"
down_revision: Union[str, None] = "0010_task_send_window_overrides"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Seed content for the immutable Default footer — the pre-existing Pounds
# Network block, with the unsubscribe link turned into a {unsubscribe_url}
# placeholder. Kept in sync with compliance.DEFAULT_FOOTER_* (fallback).
_DEFAULT_FOOTER_HTML = (
    '<br><br><div style="font-size:13px;line-height:1.6">'
    '<div style="font-weight:600;color:#202124">Pounds Network</div>'
    '<div style="color:#9aa0a6;font-size:12px">AI-Powered Marketing Campaigns</div>'
    '<div style="color:#9aa0a6;font-size:12px">Pounds.Network | gm@wing.studio | (425)628-4276 | Seattle, WA</div>'
    '<div style="color:#9aa0a6;font-size:12px">'
    '<a href="{unsubscribe_url}" style="color:#9aa0a6">Unsubscribe</a> '
    "and I won't email again.</div></div>"
)
_DEFAULT_FOOTER_TEXT = (
    "\n—\nPounds Network\nAI-Powered Marketing Campaigns\n"
    "Pounds.Network | gm@wing.studio | (425)628-4276 | Seattle, WA\n"
    "Not relevant? Unsubscribe and I won't email again: {unsubscribe_url}"
)


def upgrade() -> None:
    op.create_table(
        "footer_template",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("body_html", sa.String(), nullable=False),
        sa.Column("body_text", sa.String(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.add_column(
        "email_template",
        sa.Column("footer_choice", sa.String(), nullable=True),
    )
    # Seed the immutable Default footer.
    footer = sa.table(
        "footer_template",
        sa.column("id", sa.String),
        sa.column("name", sa.String),
        sa.column("body_html", sa.String),
        sa.column("body_text", sa.String),
        sa.column("is_default", sa.Boolean),
        sa.column("status", sa.String),
    )
    op.bulk_insert(
        footer,
        [{
            "id": uuid.uuid4().hex,
            "name": "Default (Pounds Network)",
            "body_html": _DEFAULT_FOOTER_HTML,
            "body_text": _DEFAULT_FOOTER_TEXT,
            "is_default": True,
            "status": "active",
        }],
    )


def downgrade() -> None:
    op.drop_column("email_template", "footer_choice")
    op.drop_table("footer_template")
