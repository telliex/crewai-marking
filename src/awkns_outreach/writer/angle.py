"""AI writer — the ONLY AI-generated part of a cold email.

Deliverability-first design (per the plan): the sequence body is a hand-typed
template; the writer produces a single, specific `angle` sentence per lead —
"here's why this matters *for your company*" — which the template injects via
the {angle} placeholder. Research is done with SerperDevTool, same as the
original crew, but the output is one sentence, not a whole email.

The generated angle is stored on `Lead.angle`; the mailer picks it up
automatically (send/mailer.py `_angle_line`).
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from awkns_outreach.config import settings
from awkns_outreach.db.models import Campaign, Lead

DEFAULT_ANGLE_PROMPT = (
    "Research {company} (industry: {industry}; website: {website}). In ONE "
    "sentence, write a specific, concrete reason our product would be genuinely "
    "useful to them — reference something real about their business. No greeting, "
    "no sign-off, no hype. Just the single sentence."
)


def generate_angle(
    inputs: dict[str, Any],
    angle_prompt: Optional[str] = None,
    model: Optional[str] = None,
) -> str:
    """Run a one-agent research crew and return a single angle sentence.

    Imports CrewAI lazily so the web/sequencer paths don't pay its import cost.
    """
    from crewai import Agent, Crew, LLM, Process, Task
    from crewai_tools import SerperDevTool

    llm = LLM(model=model or settings.crew_model)
    researcher = Agent(
        role="B2B outreach researcher",
        goal="Find one concrete, specific reason our product fits this company.",
        backstory="You research prospects fast and write one sharp, personalized line.",
        tools=[SerperDevTool()],
        llm=llm,
        allow_delegation=False,
        verbose=False,
    )
    task = Task(
        description=angle_prompt or DEFAULT_ANGLE_PROMPT,
        expected_output="A single specific sentence, no greeting or sign-off.",
        agent=researcher,
    )
    crew = Crew(agents=[researcher], tasks=[task], process=Process.sequential, verbose=False)
    result = crew.kickoff(inputs=inputs)
    return str(result).strip()


def backfill_campaign_angles(
    session: Session,
    campaign: Campaign,
    *,
    limit: int = 20,
    generate: Callable[..., str] = generate_angle,
) -> int:
    """Fill `angle` for active leads in this campaign that don't have one yet.
    `generate` is injectable so tests can avoid calling the LLM. Returns the
    number of leads updated."""
    leads = session.scalars(
        select(Lead)
        .where(Lead.campaign_id == campaign.id, Lead.status == "active",
               (Lead.angle.is_(None)) | (Lead.angle == ""))
        .limit(limit)
    ).all()

    updated = 0
    for lead in leads:
        inputs = {
            "company": lead.company or "",
            "industry": lead.category or "",
            "website": lead.website or "",
            "contact_name": lead.contact_name or "",
            "contact_title": lead.contact_title or "",
        }
        try:
            angle = generate(inputs, campaign.angle_prompt, settings.crew_model)
        except Exception:
            continue  # leave angle empty; the mailer falls back to a generic line
        if angle:
            lead.angle = angle
            updated += 1
    session.commit()
    return updated
