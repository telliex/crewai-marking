# Deploying Awkns Outreach to EC2

This is the runbook for the single-box EC2 deployment. It covers the
architecture, one-time provisioning, the routine deploy command, the
`.env` / `DATABASE_URL` traps, and troubleshooting.

## Architecture

```
                EC2 (Ubuntu 24.04, user: ubuntu / service user: awkns)
                ┌───────────────────────────────────────────────┐
   you  ──ssh──▶│  /opt/awkns-outreach   (git clone, uv, .venv)  │
                │                                                │
                │  systemd: awkns-web    → uvicorn :8000  ───────┼──▶ RDS Postgres
                │  systemd: awkns-cron   → outreach cron --send  │    (Aurora cluster)
                └───────────────────────────────────────────────┘
```

- **Not** Docker. The app runs from a git clone at `/opt/awkns-outreach` with a
  `uv`-managed virtualenv, driven by two systemd services.
- Postgres is remote (RDS); the box holds no database.
- `awkns-web` serves the admin dashboard + compliance endpoints on port 8000.
- `awkns-cron` runs the sequencer send loop. **It runs with `--send`, i.e. it
  sends real email.** Keep this in mind on a fresh box.

### Permission model (why deploys don't fight over ownership)

The whole `/opt/awkns-outreach` tree is owned by **`ubuntu:ubuntu`, mode `0755`**.
`ubuntu` does all git/uv work; the `awkns` service user only needs read+execute,
which `0755` under `/opt` provides. Two rules make this robust:

- uv's managed CPython is installed **inside** the app dir via
  `UV_PYTHON_INSTALL_DIR=/opt/awkns-outreach/.uv-python`. Nothing lives under
  `/home/ubuntu`, so the old `chmod o+x /home/ubuntu` hack is not needed.
- `.env` is the one exception: `ubuntu:awkns`, mode `0640` — the service reads
  secrets via its group, but they are not world-readable.

## Routine deploy

After pushing code to `main`, on the box (as `ubuntu`):

```bash
/opt/awkns-outreach/deploy/deploy.sh
```

It runs: `git pull` → `uv sync` → `alembic upgrade head` → restart both services
→ `curl /healthz`. Migrations run before the restart, so a failed migration
aborts the deploy while the services keep serving the old code — fix the
migration and re-run.

## One-time provisioning

Run as `ubuntu` on a fresh Ubuntu 24.04 instance.

### 1. System packages + uv

```bash
sudo apt-get update
sudo apt-get install -y git build-essential libpq-dev curl postgresql-client
curl -LsSf https://astral.sh/uv/install.sh | sh
source "$HOME/.local/bin/env"
```

### 2. Service user + code

```bash
# Service account: no login shell, home is the app dir.
sudo useradd -r -m -d /opt/awkns-outreach -s /usr/sbin/nologin awkns

# ubuntu owns the tree and does all git/uv work; awkns only reads+executes.
sudo chown -R ubuntu:ubuntu /opt/awkns-outreach
sudo chmod 755 /opt/awkns-outreach

git clone https://github.com/telliex/crewai-marking.git /opt/awkns-outreach
cd /opt/awkns-outreach
```

### 3. Dependencies (Python inside /opt)

```bash
export UV_PYTHON_INSTALL_DIR=/opt/awkns-outreach/.uv-python
uv sync --frozen --no-dev
```

`.venv/bin/python` now resolves under `/opt/awkns-outreach/.uv-python`, readable
by `awkns`. Confirm:

```bash
readlink -f .venv/bin/python   # should print a path under /opt, NOT /home/ubuntu
```

### 4. Environment file

```bash
cp .env.example .env
nano .env                       # fill in real values (see traps below)
sudo chown ubuntu:awkns .env
sudo chmod 640 .env
```

At minimum set: `APOLLO_API_KEY`, `RESEND_API_KEY`, `ANTHROPIC_API_KEY`,
`SERPER_API_KEY`, `DATABASE_URL`, the `OUTREACH_*` sender identity (including
`OUTREACH_POSTAL_ADDRESS`, required for real sends), `APP_BASE_URL`, and
`ADMIN_PASSWORD`.

### 5. Database schema

```bash
uv run alembic upgrade head
```

### 6. systemd services

```bash
sudo cp deploy/systemd/awkns-web.service  /etc/systemd/system/
sudo cp deploy/systemd/awkns-cron.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now awkns-web awkns-cron
sudo systemctl status awkns-web awkns-cron --no-pager
```

### 7. Network access

The web app listens on `0.0.0.0:8000`. Open port 8000 (or the port behind a
reverse proxy) to your IP in the instance's security group
(`awkns-outreach-ec2-sg`). `ufw` on the box is inactive; access is governed by
the AWS security group. Check the attached group with:

```bash
TOKEN=$(curl -s -X PUT "http://169.254.169.254/latest/api/token" \
  -H "X-aws-ec2-metadata-token-ttl-seconds: 21600")
curl -s -H "X-aws-ec2-metadata-token: $TOKEN" \
  http://169.254.169.254/latest/meta-data/security-groups
```

## `.env` / `DATABASE_URL` traps

These caused the "Could not parse SQLAlchemy URL" failures during setup.

1. **No inline comments.** `pydantic-settings` does not strip a trailing
   `value  # comment` — the `# comment` becomes part of the value and silently
   corrupts keys, the From address, and the postal address. Put comments on
   their own line (also warned at the top of `.env.example`).

2. **URL-encode special characters in the DB password.** The `DATABASE_URL` is a
   URL, so a password containing `@ : / % # ?` etc. must be percent-encoded
   (e.g. `@` → `%40`). Format:

   ```
   DATABASE_URL=postgresql+psycopg://USER:ENCODED_PASSWORD@HOST:5432/outreach?sslmode=require
   ```

   RDS host looks like
   `awkns-outreach-db.cluster-XXXX.us-east-2.rds.amazonaws.com`.

3. **A literal `%` needs escaping for Alembic.** Alembic reads the URL through
   Python's `configparser`, where `%` is special; the code escapes it before
   handing off (fixed in commit `2c9a27a`), but if you hit a `%`-related parse
   error, that is the area to look at.

Verify the URL actually parses before running migrations:

```bash
uv run python3 -c "from awkns_outreach.config import get_settings; print(repr(get_settings().database_url))"
```

## Fixing the venv-python permission on an already-provisioned box

If the box was set up the old way (uv's Python under `/home/ubuntu`, requiring
`chmod o+x /home/ubuntu`), migrate it to the model above once:

```bash
cd /opt/awkns-outreach
export UV_PYTHON_INSTALL_DIR=/opt/awkns-outreach/.uv-python
uv sync --reinstall --frozen --no-dev
readlink -f .venv/bin/python           # confirm it now points under /opt
sudo chmod o-x /home/ubuntu            # revert the old hack
sudo systemctl restart awkns-web awkns-cron
```

If the tree was previously `chown`ed to `awkns`, hand it back to `ubuntu`:

```bash
sudo chown -R ubuntu:ubuntu /opt/awkns-outreach
sudo chown ubuntu:awkns /opt/awkns-outreach/.env
sudo chmod 640 /opt/awkns-outreach/.env
```

## Troubleshooting

```bash
# Service state
sudo systemctl status awkns-web awkns-cron --no-pager

# Live logs
journalctl -u awkns-web  -f
journalctl -u awkns-cron -f

# Health check
curl -s http://localhost:8000/healthz     # -> {"status":"ok"}
```

- **500 "column ... does not exist"** — migrations not applied; run
  `uv run alembic upgrade head` (or just re-run `deploy.sh`).
- **Admin login fails** — the password is `ADMIN_PASSWORD` in `.env`; the
  username is ignored. Restart `awkns-web` after editing `.env` (settings load
  once at startup).
- **Nothing sends** — a campaign needs a *running task*; sending also respects
  warmup, the rolling-24h cap, business hours, and the suppression list.
