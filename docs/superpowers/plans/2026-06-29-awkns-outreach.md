# Awkns Outreach — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a CrewAI-based daily sales outreach automation that reads unprocessed leads from Google Sheets, researches each company online, and generates personalized cold email drafts saved as local `.md` files.

**Architecture:** A sequential CrewAI crew processes one lead at a time — `sales_rep_agent` researches the company using SerperDevTool, then `lead_sales_rep_agent` picks the correct Tier template and writes a personalized email draft. A Python `schedule` loop in `main.py` runs this daily at 09:00, reading from Google Sheets and writing Status back when done.

**Tech Stack:** Python 3.11+, CrewAI, Anthropic Claude (claude-sonnet-4-6), SerperDevTool, gspread, python-dotenv, schedule, uv

## Global Constraints

- Python ≥ 3.11
- LLM: `claude-sonnet-4-6` via `ANTHROPIC_API_KEY`
- Web search: SerperDevTool via `SERPER_API_KEY`
- Google Sheets ID: `1UB0PPVOBd68lv_1YgEuaQBnZkTV9KHRrZ7FXUSGQmi8` (from `.env`)
- Google Sheets auth: service account JSON at path in `GOOGLE_SERVICE_ACCOUNT_JSON`
- Sender signature: `pounds.network` / `Awkns`
- All email drafts in English
- Output files: `outputs/{shop_name}.md` (sanitize filename: spaces → underscores, remove special chars)
- Schedule time: `SCHEDULE_TIME=09:00` from `.env`
- Tier default for unknown industry: Tier 2
- Package manager: `uv`
- Process type: `Process.sequential`

---

## File Map

| File | Responsibility |
|---|---|
| `pyproject.toml` | Dependencies and project metadata |
| `.env.example` | Template for required env vars |
| `src/awkns_outreach/__init__.py` | Package marker |
| `src/awkns_outreach/tools/__init__.py` | Package marker |
| `src/awkns_outreach/tools/tier_classifier.py` | Maps Industry string → int (1/2/3) |
| `src/awkns_outreach/tools/sheets_tool.py` | Read leads from Google Sheets; write Status back |
| `src/awkns_outreach/instructions/tier1_template.md` | Tier 1 cold email template |
| `src/awkns_outreach/instructions/tier2_template.md` | Tier 2 cold email template |
| `src/awkns_outreach/instructions/tier3_template.md` | Tier 3 cold email template |
| `src/awkns_outreach/config/agents.yaml` | Agent role/goal/backstory definitions |
| `src/awkns_outreach/config/tasks.yaml` | Task description/expected_output definitions |
| `src/awkns_outreach/crew.py` | MyProjectCrew class with agents, tasks, crew() |
| `src/awkns_outreach/main.py` | Scheduler entry point; lead loop |
| `tests/test_tier_classifier.py` | Unit tests for tier classification |
| `tests/test_sheets_tool.py` | Unit tests for sheets read/write (mocked) |
| `tests/test_main.py` | Integration test for the lead-processing loop (mocked) |

---

## Task 1: Project Scaffold

**Files:**
- Create: `pyproject.toml`
- Create: `.env.example`
- Create: `src/awkns_outreach/__init__.py`
- Create: `src/awkns_outreach/tools/__init__.py`
- Create: `src/awkns_outreach/config/` (directory)
- Create: `src/awkns_outreach/instructions/` (directory)
- Create: `outputs/` (directory with `.gitkeep`)
- Create: `logs/` (directory with `.gitkeep`)
- Create: `tests/__init__.py`
- Create: `.gitignore`

- [ ] **Step 1: Initialize uv project**

```bash
cd "/Users/telliex/Documents/95-work/爾惟肯/crew-mail-marketing"
uv init --name awkns-outreach --python 3.11
```

Expected output: `pyproject.toml` created.

- [ ] **Step 2: Add dependencies**

```bash
uv add crewai crewai-tools gspread google-auth python-dotenv schedule anthropic
uv add --dev pytest pytest-mock
```

- [ ] **Step 3: Create directory structure**

```bash
mkdir -p src/awkns_outreach/tools
mkdir -p src/awkns_outreach/config
mkdir -p src/awkns_outreach/instructions
mkdir -p outputs logs tests
touch src/awkns_outreach/__init__.py
touch src/awkns_outreach/tools/__init__.py
touch tests/__init__.py
touch outputs/.gitkeep logs/.gitkeep
```

- [ ] **Step 4: Create `.env.example`**

```
ANTHROPIC_API_KEY=
SERPER_API_KEY=
GOOGLE_SHEETS_ID=1UB0PPVOBd68lv_1YgEuaQBnZkTV9KHRrZ7FXUSGQmi8
GOOGLE_SERVICE_ACCOUNT_JSON=./service_account.json
SCHEDULE_TIME=09:00
```

Save to `.env.example`.

- [ ] **Step 5: Create `.gitignore`**

```
.env
service_account.json
outputs/
logs/
__pycache__/
.venv/
*.pyc
.DS_Store
```

Save to `.gitignore`.

- [ ] **Step 6: Verify structure**

```bash
find src tests -type f | sort
```

Expected output:
```
src/awkns_outreach/__init__.py
src/awkns_outreach/tools/__init__.py
tests/__init__.py
```

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml .env.example .gitignore src/ tests/ outputs/.gitkeep logs/.gitkeep
git commit -m "chore: scaffold awkns-outreach project"
```

---

## Task 2: Tier Classifier

**Files:**
- Create: `src/awkns_outreach/tools/tier_classifier.py`
- Create: `tests/test_tier_classifier.py`

**Interfaces:**
- Produces: `classify_tier(industry: str) -> int` — returns 1, 2, or 3

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tier_classifier.py`:

```python
import pytest
from awkns_outreach.tools.tier_classifier import classify_tier


def test_tier1_nail():
    assert classify_tier("Nail Salon") == 1

def test_tier1_facial():
    assert classify_tier("Facial Studio") == 1

def test_tier1_pilates():
    assert classify_tier("PILATES STUDIO") == 1

def test_tier1_lash():
    assert classify_tier("Lash Extensions") == 1

def test_tier2_barber():
    assert classify_tier("Barbershop") == 2

def test_tier2_yoga():
    assert classify_tier("Yoga & Wellness") == 2

def test_tier2_trainer():
    assert classify_tier("Personal Trainer") == 2

def test_tier3_coffee():
    assert classify_tier("Coffee Shop") == 3

def test_tier3_boba():
    assert classify_tier("Boba Tea Shop") == 3

def test_tier3_dessert():
    assert classify_tier("Dessert Cafe") == 3

def test_default_unknown():
    assert classify_tier("Pet Grooming") == 2

def test_default_empty():
    assert classify_tier("") == 2

def test_case_insensitive():
    assert classify_tier("nail salon") == 1
    assert classify_tier("COFFEE") == 3
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_tier_classifier.py -v
```

Expected: `ModuleNotFoundError` or `ImportError`.

- [ ] **Step 3: Implement `tier_classifier.py`**

Create `src/awkns_outreach/tools/tier_classifier.py`:

```python
TIER_KEYWORDS: dict[int, list[str]] = {
    1: ["nail", "facial", "pilates", "spa", "esthetics", "lash", "waxing", "threading", "skincare", "beauty salon"],
    2: ["barber", "barbershop", "yoga", "trainer", "fitness", "gym", "crossfit", "boxing", "martial arts", "dance studio"],
    3: ["coffee", "café", "cafe", "bubble tea", "boba", "dessert", "bakery", "ice cream", "smoothie", "juice bar"],
}


def classify_tier(industry: str) -> int:
    """Return the outreach tier (1, 2, or 3) for a given industry string.

    Defaults to 2 for unrecognised industries.
    """
    lower = industry.lower()
    for tier, keywords in TIER_KEYWORDS.items():
        if any(kw in lower for kw in keywords):
            return tier
    return 2
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_tier_classifier.py -v
```

Expected: all 13 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/awkns_outreach/tools/tier_classifier.py tests/test_tier_classifier.py
git commit -m "feat: add tier classifier with keyword matching"
```

---

## Task 3: Google Sheets Tool

**Files:**
- Create: `src/awkns_outreach/tools/sheets_tool.py`
- Create: `tests/test_sheets_tool.py`

**Interfaces:**
- Consumes: env vars `GOOGLE_SHEETS_ID`, `GOOGLE_SERVICE_ACCOUNT_JSON`
- Produces:
  - `get_unprocessed_leads() -> list[dict]` — list of row dicts with keys: `shop_name`, `industry`, `website`, `social_media`, `contact_name`, `email`, `phone`, `city`, `state`, `notes`, `row_index`
  - `mark_as_drafted(row_index: int) -> None` — sets Status="Drafted", Last Contact Date=today

- [ ] **Step 1: Write the failing tests**

Create `tests/test_sheets_tool.py`:

```python
import pytest
from datetime import date
from unittest.mock import MagicMock, patch
from awkns_outreach.tools.sheets_tool import get_unprocessed_leads, mark_as_drafted


@pytest.fixture
def mock_worksheet():
    ws = MagicMock()
    ws.get_all_records.return_value = [
        {
            "Shop Name": "Sunny Nails",
            "Industry": "Nail Salon",
            "Website": "sunnynails.com",
            "Social Media": "@sunnynails",
            "Contact Name": "Jane Doe",
            "Email": "jane@sunnynails.com",
            "Phone": "555-1234",
            "City": "Austin",
            "State": "TX",
            "Status": "",
            "Last Contact Date": "",
            "Next Follow Up": "",
            "Notes": "",
        },
        {
            "Shop Name": "Cool Cuts",
            "Industry": "Barbershop",
            "Website": "",
            "Social Media": "",
            "Contact Name": "Bob Smith",
            "Email": "bob@coolcuts.com",
            "Phone": "",
            "City": "Dallas",
            "State": "TX",
            "Status": "Drafted",
            "Last Contact Date": "2026-06-28",
            "Next Follow Up": "",
            "Notes": "",
        },
    ]
    return ws


@patch("awkns_outreach.tools.sheets_tool._get_worksheet")
def test_get_unprocessed_leads_filters_drafted(mock_get_ws, mock_worksheet):
    mock_get_ws.return_value = mock_worksheet
    leads = get_unprocessed_leads()
    assert len(leads) == 1
    assert leads[0]["shop_name"] == "Sunny Nails"
    assert leads[0]["row_index"] == 2  # 1-indexed header + 1 data row


@patch("awkns_outreach.tools.sheets_tool._get_worksheet")
def test_get_unprocessed_leads_fields(mock_get_ws, mock_worksheet):
    mock_get_ws.return_value = mock_worksheet
    leads = get_unprocessed_leads()
    lead = leads[0]
    assert lead["industry"] == "Nail Salon"
    assert lead["contact_name"] == "Jane Doe"
    assert lead["email"] == "jane@sunnynails.com"


@patch("awkns_outreach.tools.sheets_tool._get_worksheet")
def test_mark_as_drafted_updates_cells(mock_get_ws, mock_worksheet):
    mock_get_ws.return_value = mock_worksheet
    mark_as_drafted(2)
    # Status is column 10 (J), Last Contact Date is column 11 (K)
    mock_worksheet.update_cell.assert_any_call(2, 10, "Drafted")
    mock_worksheet.update_cell.assert_any_call(2, 11, str(date.today()))
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_sheets_tool.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: Implement `sheets_tool.py`**

Create `src/awkns_outreach/tools/sheets_tool.py`:

```python
import os
from datetime import date

import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

load_dotenv()

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]

# Column positions (1-indexed) matching the sheet schema
_COL_STATUS = 10
_COL_LAST_CONTACT = 11


def _get_worksheet() -> gspread.Worksheet:
    creds = Credentials.from_service_account_file(
        os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"], scopes=_SCOPES
    )
    client = gspread.authorize(creds)
    sheet = client.open_by_key(os.environ["GOOGLE_SHEETS_ID"])
    return sheet.sheet1


def get_unprocessed_leads() -> list[dict]:
    """Return rows where Status is blank, with 1-indexed row_index."""
    ws = _get_worksheet()
    records = ws.get_all_records()
    leads = []
    for i, row in enumerate(records, start=2):  # row 1 is header
        if not row.get("Status", "").strip():
            leads.append({
                "shop_name": row.get("Shop Name", ""),
                "industry": row.get("Industry", ""),
                "website": row.get("Website", ""),
                "social_media": row.get("Social Media", ""),
                "contact_name": row.get("Contact Name", ""),
                "email": row.get("Email", ""),
                "phone": row.get("Phone", ""),
                "city": row.get("City", ""),
                "state": row.get("State", ""),
                "notes": row.get("Notes", ""),
                "row_index": i,
            })
    return leads


def mark_as_drafted(row_index: int) -> None:
    """Set Status='Drafted' and Last Contact Date=today for the given row."""
    ws = _get_worksheet()
    ws.update_cell(row_index, _COL_STATUS, "Drafted")
    ws.update_cell(row_index, _COL_LAST_CONTACT, str(date.today()))
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_sheets_tool.py -v
```

Expected: all 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/awkns_outreach/tools/sheets_tool.py tests/test_sheets_tool.py
git commit -m "feat: add Google Sheets reader and status updater"
```

---

## Task 4: Email Templates

**Files:**
- Create: `src/awkns_outreach/instructions/tier1_template.md`
- Create: `src/awkns_outreach/instructions/tier2_template.md`
- Create: `src/awkns_outreach/instructions/tier3_template.md`

No tests for this task — content is read by the agent at runtime.

- [ ] **Step 1: Create Tier 1 template**

Create `src/awkns_outreach/instructions/tier1_template.md`:

```markdown
# Tier 1 Outreach Template
Target: Nail Salon, Facial Studio, Pilates, Spa, Lash, Skincare

## Subject Line
A growth idea for {{company_name}}

## Email Body
Hi {{first_name}},

I came across {{company_name}} and noticed you're doing a great job helping clients with {{service}}.

One thing we've seen with growing studios is that getting new customers isn't usually the biggest challenge—it's building a system that consistently turns first-time visitors into loyal, repeat customers.

Most businesses end up relying on constant promotions or hiring agencies to keep marketing going. Once they stop, growth slows down.

Our approach is different.

Instead of managing ads for you, we help businesses build a complete growth system—from brand positioning and marketing content to lead capture, customer follow-up, memberships, loyalty programs, and referrals.

The goal is to create a system that keeps working long after it's been set up.

Would you be open to a quick 15-minute conversation? I'd be happy to share a few ideas specifically for {{company_name}}.

Best,
pounds.network
Awkns

## Personalization Instructions
- {{company_name}}: the business name from the lead profile
- {{first_name}}: the first name of the key decision maker
- {{service}}: infer from the company's industry and website (e.g., "nail care", "facial treatments", "pilates classes")
- Replace the subject line placeholder with the actual company name
```

- [ ] **Step 2: Create Tier 2 template**

Create `src/awkns_outreach/instructions/tier2_template.md`:

```markdown
# Tier 2 Outreach Template
Target: Barbershop, Yoga, Personal Trainers, Fitness, Gym, Dance

## Subject Line
A simpler way to grow {{company_name}}

## Email Body
Hi {{first_name}},

Running a growing business means you're constantly switching between serving customers, managing staff, answering questions, posting on social media, and trying to attract new clients.

We've been working with local service businesses that want to simplify all of that.

Instead of adding more marketing work every month, we help build one complete system that handles customer communication, marketing campaigns, follow-ups, memberships, and repeat business.

The idea is simple: spend less time managing growth, and more time serving your customers.

If you're open to it, I'd love to share how this could look for {{company_name}}.

Best,
pounds.network
Awkns

## Personalization Instructions
- {{company_name}}: the business name from the lead profile
- {{first_name}}: the first name of the key decision maker
- This template is also the default for industries not in Tier 1 or Tier 3
- Optionally mention a specific detail from the company's website or social media to show genuine interest
```

- [ ] **Step 3: Create Tier 3 template**

Create `src/awkns_outreach/instructions/tier3_template.md`:

```markdown
# Tier 3 Outreach Template
Target: Coffee Shop, Bubble Tea, Boba, Dessert Shop, Bakery, Café

## Subject Line
An idea to increase repeat visits at {{company_name}}

## Email Body
Hi {{first_name}},

I noticed {{company_name}} and wanted to reach out with an idea.

Many cafés and dessert shops are already doing a great job attracting customers. The bigger opportunity is finding simple ways to encourage those customers to come back more often.

Rather than relying on discounts or one-time promotions, we help businesses build a complete customer growth system that combines marketing, loyalty, customer engagement, and referrals into one process.

Our goal isn't to run ads for you—it's to help you build a system that keeps generating repeat business over time.

Would you be interested in a quick conversation to see if some of these ideas could work for your shop?

Best,
pounds.network
Awkns

## Personalization Instructions
- {{company_name}}: the business name from the lead profile
- {{first_name}}: the first name of the key decision maker
- Optionally reference a specific product or menu item found on their social media or website
```

- [ ] **Step 4: Commit**

```bash
git add src/awkns_outreach/instructions/
git commit -m "feat: add three-tier email templates"
```

---

## Task 5: Agents and Tasks Config

**Files:**
- Create: `src/awkns_outreach/config/agents.yaml`
- Create: `src/awkns_outreach/config/tasks.yaml`

- [ ] **Step 1: Create `agents.yaml`**

Create `src/awkns_outreach/config/agents.yaml`:

```yaml
sales_rep_agent:
  role: >
    Sales Research Specialist
  goal: >
    Research the target company {lead_name} in the {industry} sector.
    Collect company background, business model, key decision maker context,
    recent milestones, and infer what primary service they offer.
    Use only information you are confident about — no assumptions.
  backstory: >
    You are a sharp sales researcher at Awkns / pounds.network.
    Your job is to find the information that makes a cold email feel personal and relevant.
    You use web search tools to gather facts about local businesses —
    their website, social media presence, recent news, and what makes them stand out.
    You deliver a structured profile that your senior colleague uses to write the outreach email.

lead_sales_rep_agent:
  role: >
    Senior Sales Representative
  goal: >
    Using the research profile for {lead_name}, select the correct Tier {tier} email template
    and write a personalized cold email for {key_decision_maker} ({position}) at {lead_name}.
    Fill all template variables with real, researched details.
    The email must be professional, warm, and feel genuinely tailored — not generic.
  backstory: >
    You are a senior outreach specialist at Awkns / pounds.network.
    You take research profiles and transform them into compelling first-touch emails
    that feel personal and relevant to each recipient.
    You understand that the best cold emails are short, specific, and focused on the recipient's world.
    You always sign off as pounds.network / Awkns.
```

- [ ] **Step 2: Create `tasks.yaml`**

Create `src/awkns_outreach/config/tasks.yaml`:

```yaml
lead_profiling_task:
  description: >
    Research the company {lead_name} in the {industry} industry.
    Start with their website ({website}) and social media ({social_media}) if available.
    Find: company background, what service they primarily offer, who {key_decision_maker} is,
    any recent milestones, awards, expansions, or notable achievements.
    Note the city ({city}, {state}) for geographic context.
    Additional notes from our records: {notes}
    Do not fabricate information. Only report what you find with confidence.
  expected_output: >
    A structured markdown profile with these sections:
    ## Company Overview
    ## Key Decision Maker
    ## Primary Service (inferred)
    ## Recent Milestones / Notable Details
    ## Personalization Hooks (2-3 specific details to reference in the email)

personalized_outreach_task:
  description: >
    Using the research profile for {lead_name}, write a personalized cold email.
    Use the Tier {tier} template from the instructions folder as your base.
    Fill in all variables:
    - {{company_name}} = {lead_name}
    - {{first_name}} = first name of {key_decision_maker}
    - {{service}} = primary service inferred from research
    Where the template allows personalization, add one specific detail from the research
    (a milestone, a product, a social media observation) to make the email feel genuine.
    Sign off as pounds.network / Awkns.
    Output the final email with subject line and body only — no explanations.
  expected_output: >
    A complete email draft in this format:
    Subject: [subject line here]

    [email body here]

    Best,
    pounds.network
    Awkns
```

- [ ] **Step 3: Commit**

```bash
git add src/awkns_outreach/config/
git commit -m "feat: add agents and tasks YAML config"
```

---

## Task 6: Crew

**Files:**
- Create: `src/awkns_outreach/crew.py`

**Interfaces:**
- Consumes:
  - `classify_tier(industry: str) -> int` from `tools/tier_classifier.py`
  - agents.yaml and tasks.yaml configs
- Produces: `OutreachCrew(inputs: dict).run() -> str` — returns the email draft text

- [ ] **Step 1: Create `crew.py`**

Create `src/awkns_outreach/crew.py`:

```python
import os
from pathlib import Path

from crewai import Agent, Crew, Process, Task
from crewai.project import CrewBase, agent, crew, task
from crewai_tools import DirectoryReadTool, FileReadTool, SerperDevTool
from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic

from awkns_outreach.tools.tier_classifier import classify_tier

load_dotenv()

_INSTRUCTIONS_DIR = Path(__file__).parent / "instructions"

llm = ChatAnthropic(
    model="claude-sonnet-4-6",
    api_key=os.environ["ANTHROPIC_API_KEY"],
)


@CrewBase
class OutreachCrew:
    """Runs lead profiling + email draft generation for one lead."""

    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    @agent
    def sales_rep_agent(self) -> Agent:
        return Agent(
            config=self.agents_config["sales_rep_agent"],
            tools=[
                SerperDevTool(),
                DirectoryReadTool(directory=str(_INSTRUCTIONS_DIR)),
                FileReadTool(),
            ],
            allow_delegation=False,
            verbose=True,
            llm=llm,
        )

    @agent
    def lead_sales_rep_agent(self) -> Agent:
        return Agent(
            config=self.agents_config["lead_sales_rep_agent"],
            tools=[
                FileReadTool(),
                DirectoryReadTool(directory=str(_INSTRUCTIONS_DIR)),
            ],
            allow_delegation=False,
            verbose=True,
            llm=llm,
        )

    @task
    def lead_profiling_task(self) -> Task:
        return Task(
            config=self.tasks_config["lead_profiling_task"],
            agent=self.sales_rep_agent(),
        )

    @task
    def personalized_outreach_task(self) -> Task:
        return Task(
            config=self.tasks_config["personalized_outreach_task"],
            agent=self.lead_sales_rep_agent(),
            context=[self.lead_profiling_task()],
        )

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True,
        )

    def run(self, inputs: dict) -> str:
        """Add tier to inputs and kick off the crew. Returns email draft text."""
        inputs["tier"] = classify_tier(inputs.get("industry", ""))
        result = self.crew().kickoff(inputs=inputs)
        return str(result)
```

- [ ] **Step 2: Commit**

```bash
git add src/awkns_outreach/crew.py
git commit -m "feat: add OutreachCrew with sequential agents"
```

---

## Task 7: Main Scheduler

**Files:**
- Create: `src/awkns_outreach/main.py`
- Create: `tests/test_main.py`

**Interfaces:**
- Consumes:
  - `get_unprocessed_leads() -> list[dict]` from `tools/sheets_tool.py`
  - `mark_as_drafted(row_index: int) -> None` from `tools/sheets_tool.py`
  - `OutreachCrew(inputs).run() -> str` from `crew.py`
- Produces:
  - `outputs/{sanitized_name}.md` per lead
  - `logs/run_{date}.log` per day

- [ ] **Step 1: Write failing tests**

Create `tests/test_main.py`:

```python
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, call
from awkns_outreach.main import sanitize_filename, process_lead, run_daily


def test_sanitize_filename_spaces():
    assert sanitize_filename("Sunny Nails") == "Sunny_Nails"


def test_sanitize_filename_special_chars():
    assert sanitize_filename("Bob's Café & Grill!") == "Bobs_Caf_Grill"


def test_sanitize_filename_multiple_spaces():
    assert sanitize_filename("Cool  Cuts") == "Cool_Cuts"


@patch("awkns_outreach.main.OutreachCrew")
@patch("awkns_outreach.main.mark_as_drafted")
def test_process_lead_writes_file(mock_mark, mock_crew_cls, tmp_path):
    mock_crew = MagicMock()
    mock_crew.run.return_value = "Subject: Test\n\nHello Jane"
    mock_crew_cls.return_value = mock_crew

    lead = {
        "shop_name": "Sunny Nails",
        "industry": "Nail Salon",
        "website": "sunnynails.com",
        "social_media": "@sunnynails",
        "contact_name": "Jane Doe",
        "email": "jane@sunnynails.com",
        "phone": "555-1234",
        "city": "Austin",
        "state": "TX",
        "notes": "",
        "row_index": 2,
    }

    process_lead(lead, output_dir=tmp_path)

    output_file = tmp_path / "Sunny_Nails.md"
    assert output_file.exists()
    content = output_file.read_text()
    assert "Subject: Test" in content
    assert "Hello Jane" in content
    mock_mark.assert_called_once_with(2)


@patch("awkns_outreach.main.get_unprocessed_leads")
@patch("awkns_outreach.main.process_lead")
def test_run_daily_processes_all_leads(mock_process, mock_get_leads):
    mock_get_leads.return_value = [
        {"shop_name": "A", "row_index": 2},
        {"shop_name": "B", "row_index": 3},
    ]
    run_daily()
    assert mock_process.call_count == 2


@patch("awkns_outreach.main.get_unprocessed_leads")
@patch("awkns_outreach.main.process_lead")
def test_run_daily_continues_on_error(mock_process, mock_get_leads):
    mock_get_leads.return_value = [
        {"shop_name": "A", "row_index": 2},
        {"shop_name": "B", "row_index": 3},
    ]
    mock_process.side_effect = [Exception("API error"), None]
    run_daily()  # should not raise
    assert mock_process.call_count == 2
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_main.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: Implement `main.py`**

Create `src/awkns_outreach/main.py`:

```python
#!/usr/bin/env python
import logging
import os
import re
from datetime import date
from pathlib import Path

import schedule
import time
from dotenv import load_dotenv

from awkns_outreach.crew import OutreachCrew
from awkns_outreach.tools.sheets_tool import get_unprocessed_leads, mark_as_drafted

load_dotenv()

_OUTPUT_DIR = Path(__file__).parent.parent.parent / "outputs"
_LOG_DIR = Path(__file__).parent.parent.parent / "logs"
_OUTPUT_DIR.mkdir(exist_ok=True)
_LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(_LOG_DIR / f"run_{date.today()}.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


def sanitize_filename(name: str) -> str:
    """Replace spaces with underscores and remove non-alphanumeric chars."""
    name = re.sub(r"[^\w\s]", "", name)
    name = re.sub(r"\s+", "_", name.strip())
    return name


def process_lead(lead: dict, output_dir: Path = _OUTPUT_DIR) -> None:
    """Run the outreach crew for one lead and save the draft."""
    shop_name = lead["shop_name"]
    log.info(f"Processing: {shop_name}")

    inputs = {
        "lead_name": shop_name,
        "industry": lead.get("industry", ""),
        "website": lead.get("website", ""),
        "social_media": lead.get("social_media", ""),
        "key_decision_maker": lead.get("contact_name", ""),
        "position": "",
        "city": lead.get("city", ""),
        "state": lead.get("state", ""),
        "notes": lead.get("notes", ""),
    }

    draft = OutreachCrew().run(inputs)

    filename = sanitize_filename(shop_name) + ".md"
    (output_dir / filename).write_text(draft, encoding="utf-8")
    log.info(f"Draft saved: {filename}")

    mark_as_drafted(lead["row_index"])
    log.info(f"Marked as Drafted in Google Sheets: row {lead['row_index']}")


def run_daily() -> None:
    """Fetch all unprocessed leads and process them one by one."""
    log.info("=== Daily outreach run started ===")
    leads = get_unprocessed_leads()
    log.info(f"Found {len(leads)} unprocessed leads")

    for lead in leads:
        try:
            process_lead(lead)
        except Exception as e:
            log.error(f"Failed to process {lead.get('shop_name')}: {e}")

    log.info("=== Daily outreach run complete ===")


def main() -> None:
    schedule_time = os.getenv("SCHEDULE_TIME", "09:00")
    log.info(f"Scheduler starting — daily run at {schedule_time}")
    schedule.every().day.at(schedule_time).do(run_daily)

    while True:
        schedule.run_pending()
        time.sleep(60)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_main.py -v
```

Expected: all 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/awkns_outreach/main.py tests/test_main.py
git commit -m "feat: add main scheduler with lead processing loop"
```

---

## Task 8: Integration Smoke Test

This task verifies the full pipeline runs without errors using a single hardcoded lead (no real Google Sheets call needed).

**Files:**
- Create: `src/awkns_outreach/run_once.py` — one-shot runner for manual testing

- [ ] **Step 1: Ensure `.env` is populated**

Copy `.env.example` to `.env` and fill in:
```
ANTHROPIC_API_KEY=<your key>
SERPER_API_KEY=<your key>
GOOGLE_SERVICE_ACCOUNT_JSON=./service_account.json
```

Place your Google service account JSON at `./service_account.json`.

- [ ] **Step 2: Create `run_once.py`**

Create `src/awkns_outreach/run_once.py`:

```python
#!/usr/bin/env python
"""One-shot runner to test the crew with a hardcoded lead — no Sheets required."""
from pathlib import Path
from awkns_outreach.crew import OutreachCrew

TEST_LEAD = {
    "lead_name": "Sunny Nails",
    "industry": "Nail Salon",
    "website": "",
    "social_media": "",
    "key_decision_maker": "Jane Doe",
    "position": "Owner",
    "city": "Austin",
    "state": "TX",
    "notes": "",
}

if __name__ == "__main__":
    print("Running outreach crew for test lead...")
    result = OutreachCrew().run(TEST_LEAD)
    out = Path("outputs/test_Sunny_Nails.md")
    out.write_text(result, encoding="utf-8")
    print(f"\nDraft saved to {out}")
    print("\n--- DRAFT ---")
    print(result)
```

- [ ] **Step 3: Run the smoke test**

```bash
uv run python src/awkns_outreach/run_once.py
```

Expected:
- Terminal shows CrewAI agent output (verbose)
- `outputs/test_Sunny_Nails.md` is created
- File contains a subject line starting with "A growth idea for Sunny Nails" and a signed email body

- [ ] **Step 4: Inspect the output**

```bash
cat outputs/test_Sunny_Nails.md
```

Verify:
- Subject line present
- `{{company_name}}`, `{{first_name}}`, `{{service}}` placeholders are filled (not literal)
- Signed "pounds.network / Awkns"

- [ ] **Step 5: Run full test suite**

```bash
uv run pytest tests/ -v
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/awkns_outreach/run_once.py
git commit -m "feat: add run_once smoke test runner"
```

---

## Task 9: README

**Files:**
- Create: `README.md`

- [ ] **Step 1: Create README**

Create `README.md`:

```markdown
# Awkns Outreach

Daily sales email draft generator for Awkns / pounds.network.

Reads leads from Google Sheets → researches each company online → writes a personalized cold email draft as a `.md` file.

## Setup

### 1. Install dependencies
```bash
uv sync
```

### 2. Configure environment
```bash
cp .env.example .env
# Fill in ANTHROPIC_API_KEY, SERPER_API_KEY
```

### 3. Google Sheets access
- Create a Google Cloud service account
- Share the Google Sheet with the service account email
- Download the JSON key and save as `service_account.json` in the project root

### 4. Test with one lead (no Sheets required)
```bash
uv run python src/awkns_outreach/run_once.py
```

### 5. Start daily scheduler
```bash
uv run python src/awkns_outreach/main.py
```

Runs every day at 09:00 (set `SCHEDULE_TIME` in `.env` to change).

## Output

Email drafts are saved to `outputs/{company_name}.md`.  
Google Sheets `Status` column is updated to `Drafted` after each lead is processed.

## Tier Classification

| Tier | Industries | Template |
|---|---|---|
| 1 | Nail Salon, Facial Studio, Pilates, Spa, Lash, Skincare | Growth system for service studios |
| 2 | Barbershop, Yoga, Personal Trainers, Gym, Fitness | Simplified marketing system (also default) |
| 3 | Coffee Shop, Bubble Tea, Dessert Shop, Café, Bakery | Repeat visit / loyalty focus |

Any industry not matching Tier 1 or 3 defaults to Tier 2.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: add README with setup instructions"
```

---

## Self-Review Checklist

- [x] **Spec coverage**: All spec requirements have a task — Google Sheets read/write (Task 3), tier classification (Task 2), email templates (Task 4), agents (Task 5), crew (Task 6), scheduler (Task 7), smoke test (Task 8)
- [x] **Placeholder scan**: No TBDs, all code blocks complete
- [x] **Type consistency**: `classify_tier` returns `int` used as `inputs["tier"]` in crew.py and referenced as `{tier}` in tasks.yaml ✓; `get_unprocessed_leads` returns `list[dict]` consumed by `process_lead` ✓; `mark_as_drafted(row_index: int)` called with `lead["row_index"]` ✓
- [x] **Default Tier 2**: `classify_tier` returns `2` for unmatched industry ✓
- [x] **Sender signature**: All templates end with `pounds.network / Awkns` ✓
- [x] **Output path**: `outputs/{sanitized_name}.md` ✓
- [x] **Error handling**: `run_daily` catches per-lead exceptions and continues ✓
