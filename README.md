# Awkns Outreach

A self-serve **cold-email sales funnel**: Apollo finds the decision-makers and
their verified emails; everything after — CRM state, AI-personalized copy,
compliant rate-limited sending, unsubscribe/bounce handling — is in-house.

```
Campaign (titles + seed domains)
   │  Apollo searchPeople (free)  →  bulkMatch (unlock email, spends credits)
   ▼
Lead (Postgres)  ──CrewAI writes {angle}──▶  Sequencer  ──Resend──▶  inbox
   ▲                                             │  warmup · 24h cap · business hours
   └──── unsubscribe / bounce webhook ───────────┘  · suppression · CAS claim
```

Apollo is the only external dependency for lead data; sending (Resend), CRM
(Postgres), and copy (Claude/CrewAI) are all ours.

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
| `db/` | ORM models (Campaign / Lead / Event / Suppression) + Alembic |
| `sequencer/` | the engine: caps, hours, suppression, CAS claim, pacing, retry |
| `send/` | Resend send + inbox-friendly rendering |
| `compliance.py` | unsubscribe tokens, headers, footer, legal gate, suppression |
| `writer/` | CrewAI angle generator (the only AI-written part of the email) |
| `web/` | FastAPI: admin dashboard + unsubscribe + webhook |
| `cli.py` / `scheduler.py` | manual and cron drivers |

Copy is **templated per campaign** (`Campaign.sequence`); the AI writes only the
one-sentence `{angle}`. Reply handling (inbound → AI draft → human approval) is a
planned v2 — `Lead.thread_ref` is already tracked for it.

## Tests
```bash
uv run pytest -q
```
