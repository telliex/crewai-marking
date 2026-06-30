# Awkns Outreach — Design Spec
Date: 2026-06-29

## Overview

A CrewAI-based sales outreach automation system for **Awkns / pounds.network** that reads a Google Sheets prospect list, researches each lead, and generates personalized cold email drafts — organized by business type (Tier).

Runs on a daily schedule. Outputs one `.md` draft file per lead. Does not send emails automatically.

---

## Goals

- Read unprocessed leads from Google Sheets (Status = blank)
- For each lead: research the company online, classify into a Tier, generate a personalized email draft
- Save drafts to `outputs/{company_name}.md`
- Mark processed leads in Google Sheets (Status = "Drafted", Last Contact Date = today)
- Run automatically every morning via Python scheduler

---

## Google Sheets Schema

| Column | Use |
|---|---|
| Shop Name | `lead_name` — primary identifier |
| Industry | Tier classification input |
| Website | Passed to research agent |
| Social Media | Passed to research agent |
| Contact Name | `key_decision_maker` |
| Email | Stored in output draft |
| Phone | Stored in output draft |
| City / State | Context for personalization |
| Status | Filter: blank = unprocessed; written back as "Drafted" |
| Last Contact Date | Written back after processing |
| Next Follow Up | Left blank (human fills) |
| Notes | Read as additional context if present |

---

## Tier Classification

| Tier | Industries |
|---|---|
| Tier 1 | Nail Salon, Facial Studio, Pilates |
| Tier 2 | Barbershop, Yoga, Personal Trainers |
| Tier 3 | Coffee Shop, Bubble Tea, Dessert Shop |
| Default | Any industry not in the above → Tier 2 |

---

## Email Templates

Each Tier has one English-language cold email template stored in `src/awkns_outreach/instructions/`.

Sender signature in all emails:
```
Best,
pounds.network
Awkns
```

### Tier 1 — Subject: "A growth idea for {company_name}"
Focus: turning first-time visitors into loyal repeat customers via a complete growth system (brand, content, loyalty, referrals).

### Tier 2 — Subject: "A simpler way to grow {company_name}"
Focus: reducing marketing overhead by building one complete system for communication, follow-ups, memberships.

### Tier 3 — Subject: "An idea to increase repeat visits at {company_name}"
Focus: encouraging repeat visits without discounts via loyalty, engagement, and referral systems.

Template variables filled by the Lead Sales Rep Agent:
- `{{company_name}}` → Shop Name
- `{{first_name}}` → first word of Contact Name
- `{{service}}` → inferred from Industry by agent

---

## Agents

### `sales_rep_agent`
- **Role**: Sales Representative
- **Goal**: Research the lead company and compile a profile
- **Tools**: `SerperDevTool`, `FileReadTool` (reads instructions/), `DirectoryReadTool`
- **Output**: Company profile with background, key decision maker context, recent activity, inferred service/milestone

### `lead_sales_rep_agent`
- **Role**: Senior Sales Representative
- **Goal**: Use the research profile and the correct Tier template to write a personalized email draft
- **Tools**: `FileReadTool` (reads tier template), `SentimentAnalysisTool`
- **Output**: Finalized email draft written to `outputs/{company_name}.md`

---

## Tasks

### `lead_profiling_task`
- Agent: `sales_rep_agent`
- Description: Research `{lead_name}` in the `{industry}` sector using all available sources. Gather company background, key decision maker info, recent milestones, and inferred service focus. Use website `{website}` and social `{social_media}` as starting points.
- Expected output: Structured markdown company profile

### `outreach_draft_task`
- Agent: `lead_sales_rep_agent`
- Description: Using the research profile and Tier `{tier}` template from `instructions/`, write a personalized cold email for `{key_decision_maker}` at `{lead_name}`. Fill all template variables. Sign as pounds.network / Awkns.
- Expected output: Complete email with subject line and body, saved to `outputs/{lead_name}.md`

---

## Project Structure

```
crew-mail-marketing/
├── src/awkns_outreach/
│   ├── config/
│   │   ├── agents.yaml
│   │   └── tasks.yaml
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── sheets_tool.py        # Google Sheets read/write via gspread
│   │   └── tier_classifier.py   # Maps Industry → Tier (1/2/3), default Tier 2
│   ├── instructions/
│   │   ├── tier1_template.md
│   │   ├── tier2_template.md
│   │   └── tier3_template.md
│   ├── __init__.py
│   ├── crew.py
│   └── main.py                  # Scheduler entry point
├── outputs/                     # Generated email drafts (.md per lead)
├── logs/                        # Execution logs
├── docs/superpowers/specs/
│   └── 2026-06-29-awkns-outreach-design.md
├── .env                         # GOOGLE_SHEETS_ID, SERPER_API_KEY, ANTHROPIC_API_KEY
├── .env.example
├── pyproject.toml
└── README.md
```

---

## Technology Stack

| Component | Choice |
|---|---|
| Agent framework | CrewAI |
| LLM | Claude (claude-sonnet-4-6 via Anthropic API) |
| Web research | SerperDevTool |
| Google Sheets | `gspread` + service account JSON |
| Scheduler | Python `schedule` library |
| Package manager | `uv` + `pyproject.toml` |

---

## `.env` Variables

```
ANTHROPIC_API_KEY=
SERPER_API_KEY=
GOOGLE_SHEETS_ID=1UB0PPVOBd68lv_1YgEuaQBnZkTV9KHRrZ7FXUSGQmi8
GOOGLE_SERVICE_ACCOUNT_JSON=./service_account.json
SCHEDULE_TIME=09:00
```

---

## Data Flow

```
Google Sheets (Status=blank)
        ↓
main.py reads rows → filters unprocessed leads
        ↓
For each lead:
  tier_classifier(Industry) → tier (1/2/3)
  MyProjectCrew(inputs).kickoff()
        ↓
  sales_rep_agent → lead_profiling_task
        ↓
  lead_sales_rep_agent → outreach_draft_task → outputs/{name}.md
        ↓
  sheets_tool.update_status(row, "Drafted", today)
```

---

## Out of Scope (v1)

- Automatic email sending
- Follow-up email sequences (emails 2–5)
- Web UI / dashboard
- CRM integration
- Multi-language support
