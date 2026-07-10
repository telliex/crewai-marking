"""ORM models — the outreach CRM.

Ported from yoh's Prisma schema (OutreachLead / OutreachEvent /
OutreachSuppression), plus a `Campaign` parent so one deployment can run many
targets ("針對不同的目標"): each campaign owns its target titles, seed domains,
sequence copy, angle prompt, and sender identity.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from awkns_outreach.db.session import Base

# Portable column types: native JSONB / text[] on Postgres (production), plain
# JSON on SQLite (local tests). We never query INSIDE these columns, so the
# generic fallback loses nothing.
JSONType = JSON().with_variant(JSONB(), "postgresql")
StrArray = JSON().with_variant(ARRAY(String), "postgresql")


def _uuid() -> str:
    return uuid.uuid4().hex


class Campaign(Base):
    """One outreach target/ICP: its titles, seed domains, sequence, and sender."""

    __tablename__ = "campaign"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="active")  # active | paused | archived
    tier: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Apollo people-search inputs.
    target_titles: Mapped[list[str]] = mapped_column(
        StrArray, default=list, nullable=False
    )
    # Seed company list (companies.json shape). Each entry is a dict with all
    # fields optional — only `website` is required to derive the Apollo query
    # domain. Extra metadata (name/country/category/priority/angle) is carried
    # onto the resulting leads; Apollo-returned facts overwrite on conflict.
    seed_companies: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONType, default=list, nullable=False
    )

    # Sequence copy: list of {key, delay_days, subject, body} (body/subject are
    # templates rendered against the lead — see sequencer.templating).
    sequence: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONType, default=list, nullable=False
    )
    # Prompt the writer uses to generate each lead's personalized `angle`.
    angle_prompt: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # Per-campaign sender identity overrides (from, from_name, reply_to,
    # sender_name, company, postal_address). Missing keys fall back to settings.
    sender_identity: Mapped[dict[str, Any]] = mapped_column(
        JSONType, default=dict, nullable=False
    )
    # ISO date this campaign's sending domain started warming up.
    warmup_start: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    leads: Mapped[list["Lead"]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan"
    )


class Lead(Base):
    """A decision-maker in a campaign's sequence. `step` is the cursor; `status`
    gates whether the sequencer will actually send."""

    __tablename__ = "lead"
    __table_args__ = (
        UniqueConstraint("campaign_id", "email", name="uq_lead_campaign_email"),
        Index("ix_lead_due", "status", "next_action_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("campaign.id", ondelete="CASCADE"), nullable=False
    )
    apollo_person_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    email: Mapped[str] = mapped_column(String, nullable=False)  # lowercased
    company: Mapped[str] = mapped_column(String, nullable=False)
    contact_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    contact_title: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    country: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    category: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    priority: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # A|B|C
    website: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    angle: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    vars: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONType, nullable=True)

    # Sequence cursor = index of the NEXT step to send = steps already sent.
    step: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # active | sending | completed | replied | bounced | suppressed | paused | failed
    status: Mapped[str] = mapped_column(String, default="active", nullable=False)
    thread_ref: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    last_sent_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_action_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    replied_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    campaign: Mapped["Campaign"] = relationship(back_populates="leads")
    events: Mapped[list["Event"]] = relationship(
        back_populates="lead", cascade="all, delete-orphan"
    )


class Event(Base):
    """An append-only send/engagement log. The rolling-24h send cap and the
    per-step retry cap are COUNTED FROM THIS TABLE, not a calendar day."""

    __tablename__ = "outreach_event"
    __table_args__ = (
        Index("ix_event_lead_created", "lead_id", "created_at"),
        Index("ix_event_type_created", "type", "created_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    lead_id: Mapped[str] = mapped_column(
        ForeignKey("lead.id", ondelete="CASCADE"), nullable=False
    )
    # sent | skipped | reply | bounce | open | click | unsubscribe | error
    type: Mapped[str] = mapped_column(String, nullable=False)
    step: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    detail: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    subject: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    lead: Mapped["Lead"] = relationship(back_populates="events")


class Suppression(Base):
    """Global do-not-contact list, checked before EVERY send across all
    campaigns. Fed by unsubscribe clicks, hard bounces, complaints, and manual
    additions."""

    __tablename__ = "outreach_suppression"

    email: Mapped[str] = mapped_column(String, primary_key=True)  # lowercased
    reason: Mapped[str] = mapped_column(String, nullable=False)  # unsubscribe|bounce|complaint|manual
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
