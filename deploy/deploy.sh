#!/usr/bin/env bash
#
# Routine redeploy for the Awkns Outreach EC2 box.
#
# Run as the `ubuntu` user (owns the tree, has sudo):
#     /opt/awkns-outreach/deploy/deploy.sh
#
# What it does: pull the latest code, sync dependencies, run DB migrations,
# sync the systemd unit files, restart the web + cron services, and health-check
# the web app. Migrations run BEFORE the restart, so if a migration fails the
# script aborts and the services keep running the old code — fix the migration
# and re-run.
#
# One-time provisioning and the permission model are documented in DEPLOY.md.
set -euo pipefail

APP_DIR=/opt/awkns-outreach

# uv installs to ~/.local/bin; a non-interactive shell may not have it on PATH.
export PATH="$HOME/.local/bin:$PATH"
# Keep uv's managed CPython inside the app dir (owned by ubuntu, world-readable)
# so the `awkns` service user can run it without touching /home/ubuntu.
export UV_PYTHON_INSTALL_DIR="$APP_DIR/.uv-python"

cd "$APP_DIR"

echo "==> [1/6] Pulling latest code"
git pull --ff-only

echo "==> [2/6] Syncing dependencies (uv sync --frozen --no-dev)"
uv sync --frozen --no-dev

echo "==> [3/6] Running database migrations (alembic upgrade head)"
# --no-dev so this `uv run` doesn't re-sync the dev packages that step 2 just
# removed (otherwise every deploy churns pytest et al. back in).
uv run --no-dev alembic upgrade head

echo "==> [4/6] Syncing systemd unit files"
# systemctl reads units from /etc/systemd/system, NOT the repo — so a `restart`
# alone silently keeps the OLD unit. Without this step, an edit to a unit here
# (e.g. the cron `--send` flag) never actually deploys. Mirror provision.sh.
sudo cp deploy/systemd/awkns-web.service  /etc/systemd/system/
sudo cp deploy/systemd/awkns-cron.service /etc/systemd/system/
sudo systemctl daemon-reload

echo "==> [5/6] Restarting services"
sudo systemctl restart awkns-web awkns-cron

echo "==> [6/6] Health check"
sleep 2
if curl -fsS http://localhost:8000/healthz > /dev/null; then
    echo "    web /healthz OK"
else
    echo "    web /healthz FAILED — check: journalctl -u awkns-web -n 50" >&2
    exit 1
fi

echo "==> Deploy complete"
systemctl --no-pager --lines=0 status awkns-web awkns-cron || true
