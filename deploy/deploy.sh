#!/usr/bin/env bash
#
# Routine redeploy for the Awkns Outreach EC2 box.
#
# Run as the `ubuntu` user (owns the tree, has sudo):
#     /opt/awkns-outreach/deploy/deploy.sh
#
# What it does: pull the latest code, sync dependencies, run DB migrations,
# restart the web + cron services, and health-check the web app. Migrations
# run BEFORE the restart, so if a migration fails the script aborts and the
# services keep running the old code — fix the migration and re-run.
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

echo "==> [1/5] Pulling latest code"
git pull --ff-only

echo "==> [2/5] Syncing dependencies (uv sync --frozen --no-dev)"
uv sync --frozen --no-dev

echo "==> [3/5] Running database migrations (alembic upgrade head)"
uv run alembic upgrade head

echo "==> [4/5] Restarting services"
sudo systemctl restart awkns-web awkns-cron

echo "==> [5/5] Health check"
sleep 2
if curl -fsS http://localhost:8000/healthz > /dev/null; then
    echo "    web /healthz OK"
else
    echo "    web /healthz FAILED — check: journalctl -u awkns-web -n 50" >&2
    exit 1
fi

echo "==> Deploy complete"
systemctl --no-pager --lines=0 status awkns-web awkns-cron || true
