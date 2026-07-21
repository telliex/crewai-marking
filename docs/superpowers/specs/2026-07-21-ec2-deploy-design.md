# EC2 Deploy Cleanup — Design

**Date:** 2026-07-21
**Goal:** Turn the ad-hoc, error-prone EC2 setup into a clean, repeatable deploy:
a one-command redeploy script, a runbook that captures the one-time
provisioning, a fix for the fragile venv-python permission model, and
documentation of the `.env` / `DATABASE_URL` traps that bit us.

## Context

The app already runs on a single EC2 box (Ubuntu 24.04) against an RDS Postgres
cluster, driven by two systemd services (already in the repo under
`deploy/systemd/`):

- `awkns-web.service` — `uvicorn awkns_outreach.web.app:app --host 0.0.0.0 --port 8000`
- `awkns-cron.service` — `outreach cron --send --interval 1 --max 5 --poll-interval 5`

Deploy tooling that exists: `Dockerfile`, `.dockerignore`, `deploy/systemd/*.service`,
`.env.example`. The box is **not** run via Docker — it uses `uv` + a git clone at
`/opt/awkns-outreach`. Routine updates are currently manual and undocumented.

Chosen deploy model (confirmed with the operator): **an on-server one-command
script** that reuses the existing systemd + uv setup. Not Docker, not CI/CD.

## Root cause of the current pain: two owners fighting over the tree

1. `uv sync` was run as `ubuntu`, so uv installed its managed CPython under
   `/home/ubuntu/.local/share/uv/python/...`. The `.venv/bin/python` symlink
   points there.
2. The service runs as `awkns`, which cannot traverse `/home/ubuntu` (mode
   `0750`) — hence the `chmod o+x /home/ubuntu` hack.
3. `chown -R awkns:awkns /opt/awkns-outreach` was then applied, after which
   `ubuntu` could no longer `git pull` — forcing more ownership juggling.

## Design decision: single owner (`ubuntu`) + a readable `/opt` tree

- Relocate uv's managed Python into the app dir via
  `UV_PYTHON_INSTALL_DIR=/opt/awkns-outreach/.uv-python`.
- Keep the whole `/opt/awkns-outreach` tree owned by `ubuntu:ubuntu`, mode `0755`
  (others get read + execute — enough for the `awkns` service to run the code
  and the interpreter). No per-deploy `chown`.
- Nothing references `/home/ubuntu` anymore, so the `chmod o+x /home/ubuntu`
  hack is dropped (and can be reverted).
- `.env` is the one exception: `ubuntu:awkns`, mode `0640`, so the service can
  read secrets via group while they are **not** world-readable.

Rejected alternative: run everything as `awkns`. `awkns` is a `nologin` service
account; git and interactive fixes against it are awkward. The single-owner
model above is simpler and matches how the box is already driven (operator logs
in as `ubuntu`).

## Deliverables

### 1. `deploy/deploy.sh` — routine redeploy

Run as `ubuntu`. `set -euo pipefail`. Steps:

1. `cd /opt/awkns-outreach`
2. `export UV_PYTHON_INSTALL_DIR="$APP_DIR/.uv-python"`
3. `git pull --ff-only`
4. `uv sync --frozen --no-dev`
5. `uv run alembic upgrade head`
6. `sudo systemctl restart awkns-web awkns-cron`
7. health check: `curl -fsS http://localhost:8000/healthz`
8. print `systemctl status` (no pager) for both services

Failure behavior: because migrations (step 5) run **before** the restart (step
6), a failed migration aborts under `set -e` while the services keep running the
**old** code. The operator fixes the migration and re-runs. This ordering is
intentional and documented.

Chicken-and-egg caveat (documented): the running copy of `deploy.sh` is the one
already on disk; the `git pull` inside it may fetch a newer `deploy.sh` that
only takes effect next run. Acceptable.

### 2. `deploy/DEPLOY.md` — the runbook

Sections:

1. **Architecture** — EC2 + RDS, two systemd services, uv + git clone at `/opt`.
2. **One-time provisioning** — cleaned from the operator's shell history:
   packages (`git build-essential libpq-dev curl postgresql-client`), install
   uv, create the `awkns` service user, clone to `/opt/awkns-outreach`, set
   ownership/modes, `UV_PYTHON_INSTALL_DIR` + `uv sync`, `.env` (mode `0640`,
   `ubuntu:awkns`), `alembic upgrade head`, install + enable the systemd units
   from `deploy/systemd/`, security-group note for port 8000.
3. **Routine deploy** — `./deploy/deploy.sh`.
4. **`.env` / `DATABASE_URL` traps** — dedicated section: pydantic-settings does
   not strip inline `# comments` (already warned in `.env.example`); RDS
   passwords with special characters must be URL-encoded in the SQLAlchemy URL,
   and a literal `%` must be escaped for Alembic's configparser (the cause of
   the "Could not parse SQLAlchemy URL" failures; already fixed in code by
   commit `2c9a27a`). Verify with:
   `uv run python3 -c "from awkns_outreach.config import get_settings; print(repr(get_settings().database_url))"`.
5. **Fixing the venv-python permission on the existing box** — one-time
   migration to the new model: set `UV_PYTHON_INSTALL_DIR`, `uv sync --reinstall`
   (or `uv python install`), confirm `.venv/bin/python` now resolves under
   `/opt`, then `sudo chmod o-x /home/ubuntu` to revert the hack; restart
   services.
6. **Troubleshooting** — `systemctl status`, `journalctl -u awkns-web -f`,
   health check, "no active task → nothing sends" reminder.

### 3. systemd units — unchanged

`deploy/systemd/*.service` are already correct; the runbook only references how
to install them (`sudo cp` → `daemon-reload` → `enable --now`). Note in the
runbook that `awkns-cron` runs with `--send` (sends **real** email).

## Out of scope (YAGNI)

- Docker / ECS, CI/CD pipelines, nginx/TLS termination, blue-green or
  zero-downtime deploys, multi-host. Single box, restart-in-place.
- No secret manager integration; `.env` on the box stays the source of secrets.

## Testing / verification

- `bash -n deploy/deploy.sh` (syntax) and `shellcheck` if available.
- The script is not runnable in this repo's CI (no EC2); correctness is verified
  by review against the runbook and the operator's next real deploy.
