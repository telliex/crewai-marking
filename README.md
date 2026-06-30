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
