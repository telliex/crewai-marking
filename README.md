# Awkns Outreach

A self-serve **cold-email sales funnel**: Apollo finds the decision-makers and
their verified emails; everything after — CRM state, AI-personalized copy,
compliant rate-limited sending, unsubscribe/bounce handling — is in-house.

```
Campaign (titles + seed domains)
   │  Apollo searchPeople (free)  →  bulkMatch (unlock email, spends credits)
   ▼
Lead (Postgres) ──CrewAI writes {angle}, AI tier A/B/C──▶  Task  ──▶  Sequencer  ──Resend/Gmail──▶  inbox
   ▲                                                         ▲              │  warmup · 24h cap · business hours
   │                                   EmailTemplate ──▶ MailSequence        │  · suppression · CAS claim
   └──── unsubscribe / bounce webhook ───────────────────────────────────────┘
```

Apollo is the only external dependency for lead data; sending (Resend/Gmail),
CRM (Postgres), and copy (Claude/CrewAI) are all ours. Leads get an A/B/C
fit tier — AI-classified from the campaign's leads page (`writer/tiers.py`),
or set manually — and a `Task` picks a `Campaign` plus a `MailSequence` per
tier before it can be scheduled; a lead with no tier set is treated as tier B
when a Task sends.

## Setup

### 1. Install dependencies
```bash
uv sync
```

### 2. Configure environment
```bash
cp .env.example .env
# Fill in APOLLO_API_KEY, RESEND_API_KEY, ANTHROPIC_API_KEY, SERPER_API_KEY,
# DATABASE_URL, the OUTREACH_* sender identity, and ADMIN_PASSWORD.
```

### 3. Create the database schema
```bash
# Postgres (production):
uv run alembic upgrade head
# Local dev without Postgres: point DATABASE_URL at SQLite — tables are created
# from the models (see tests). SQLite maps JSONB/ARRAY to plain JSON.
```
**Deploying schema changes:** before upgrading past migration `0009_tasks_restructure`
(the Campaign → Task cutover), stop any running/scheduled sequence-based send —
any in-flight send halts at that migration by design, and there's no automatic
backfill to a `Task`. Recreate the send as a `Task` after upgrading. See the
migration file's docstring for the full detail.

### 4. Run the web service (admin dashboard + compliance endpoints)
```bash
uv run uvicorn awkns_outreach.web.app:app --reload
```
- Admin dashboard: `/` (HTTP Basic, password = `ADMIN_PASSWORD`)
- One-click unsubscribe: `/outreach/unsubscribe` · Resend webhook: `/webhooks/resend`

### 5. Drive the funnel from the CLI
```bash
uv run outreach list
uv run outreach enrich <campaign_id> --limit 10            # search only (free)
uv run outreach enrich <campaign_id> --limit 10 --reveal   # unlock emails (credits)
uv run outreach angles <campaign_id>                       # AI-write per-lead angle
uv run outreach run <campaign_id>                          # DRY-RUN (default; sends nothing)
uv run outreach run <campaign_id> --send                   # send for real
uv run outreach cron --interval 15 --send                  # scheduled small batches
```
`list` shows each campaign's currently active `Task` (or "no active task").
`run` now resolves and advances the campaign's `running` Task and exits with
an error if there isn't one, even with leads and angles ready; `cron` shares
the same `run_all_campaigns()` helper and just skips campaigns with no
running Task. Create and start a Task from the admin dashboard's Tasks page
first.

## Guardrails (why this stays out of spam)

- **Warmup ramp** — per-campaign daily cap grows 5→100/day over ~2.5 weeks from
  `Campaign.warmup_start`.
- **Rolling-24h cap** — counted from `outreach_event` rows, not a calendar day.
- **Business hours** — only sends Mon–Fri 09:00–17:00 in the recipient's local TZ
  (from `country`).
- **Suppression list** — checked before every send; unsubscribes + bounces feed it
  automatically via one-click headers and the Resend webhook.
- **CAS claim** — an `active → sending` compare-and-swap prevents double-sends under
  concurrent runs.
- **Legal gate** — real sends are blocked unless a postal address is set
  (`OUTREACH_POSTAL_ADDRESS` or the campaign's identity).

## Deliverability (DNS, do this before volume)

Use a dedicated **sending subdomain** (e.g. `mail.yourdomain.com`) with SPF, DKIM,
and DMARC configured, and set `warmup_start` when you begin. Start manual (`run`)
for the first week, watch deliverability, then turn on `cron`.

## Architecture

| Module | Role |
|---|---|
| `apollo/` | Apollo client + enrich (search → reveal → upsert leads) |
| `db/` | ORM models (Campaign / Lead / Event / Suppression / EmailTemplate / MailSequence / Task) + Alembic |
| `sequencer/` | the engine: caps, hours, suppression, CAS claim, pacing, retry; `lifecycle.py` owns Task schedule/start/pause/stop |
| `send/` | Resend send + inbox-friendly rendering |
| `compliance.py` | unsubscribe tokens, headers, footer, legal gate, suppression |
| `writer/` | `angle.py`: CrewAI per-lead `{angle}` generator; `tiers.py`: AI A/B/C lead-tier classifier |
| `web/` | FastAPI: admin dashboard + unsubscribe + webhook |
| `cli.py` / `scheduler.py` | manual and cron drivers |

Copy is **not campaign-bound** — an `EmailTemplate` (single email) or
`MailSequence` (ordered steps, reusable) is written once and assigned to a
campaign's leads only via a `Task`, which snapshots the sequence assigned to
each lead's tier into `Task.steps_by_tier` when it starts (later edits to the
`MailSequence` don't affect an in-flight Task). The AI still writes only the
one-sentence `{angle}` — that generator (`writer/angle.py`) is unchanged by
the tiering/Task work. Reply handling (inbound → AI draft → human approval)
is a planned v2 — `Lead.thread_ref` is already tracked for it.

## Tests
```bash
uv run pytest -q
```
