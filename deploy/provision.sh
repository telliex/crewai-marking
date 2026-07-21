#!/usr/bin/env bash
#
# One-time provisioning for a fresh Awkns Outreach EC2 box (Ubuntu 24.04).
#
# Run as the `ubuntu` user (needs sudo) AFTER cloning the repo:
#     git clone https://github.com/telliex/crewai-marking.git /opt/awkns-outreach
#     /opt/awkns-outreach/deploy/provision.sh
#
# Idempotent: safe to re-run. On the first run it creates .env from the template
# and pauses so you can fill in secrets; edit .env, then re-run to finish
# (migrations + systemd services). For routine updates afterwards, use deploy.sh.
#
# The permission model and .env traps are documented in DEPLOY.md.
set -euo pipefail

APP_DIR=/opt/awkns-outreach
SERVICE_USER=awkns

export PATH="$HOME/.local/bin:$PATH"
# Keep uv's managed CPython inside the app dir so the service user can run it
# without any access to /home/ubuntu.
export UV_PYTHON_INSTALL_DIR="$APP_DIR/.uv-python"

if [ ! -f "$APP_DIR/pyproject.toml" ]; then
    echo "Repo not found at $APP_DIR (no pyproject.toml)." >&2
    echo "Clone it first:" >&2
    echo "    git clone https://github.com/telliex/crewai-marking.git $APP_DIR" >&2
    exit 1
fi

echo "==> [1/7] System packages"
sudo apt-get update -qq
sudo apt-get install -y git build-essential libpq-dev curl postgresql-client

echo "==> [2/7] uv"
if ! command -v uv > /dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # shellcheck disable=SC1091
    source "$HOME/.local/bin/env"
fi

echo "==> [3/7] Service user ($SERVICE_USER)"
if ! id "$SERVICE_USER" > /dev/null 2>&1; then
    # Home is the app dir (already exists as the clone); no login shell.
    sudo useradd -r -d "$APP_DIR" -s /usr/sbin/nologin "$SERVICE_USER"
fi

echo "==> [4/7] Ownership + modes (single owner: ubuntu; tree world-readable)"
sudo chown -R ubuntu:ubuntu "$APP_DIR"
sudo chmod 755 "$APP_DIR"

echo "==> [5/7] Dependencies (Python under /opt)"
cd "$APP_DIR"
uv sync --frozen --no-dev
echo "    interpreter: $(readlink -f .venv/bin/python)"

echo "==> [6/7] Environment file"
if [ ! -f "$APP_DIR/.env" ]; then
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    sudo chown ubuntu:"$SERVICE_USER" "$APP_DIR/.env"
    sudo chmod 640 "$APP_DIR/.env"
    cat >&2 <<MSG

    Created $APP_DIR/.env from the template.
    Fill in real values (see "'.env / DATABASE_URL traps'" in deploy/DEPLOY.md),
    then re-run this script to finish setup:

        nano $APP_DIR/.env
        $APP_DIR/deploy/provision.sh
MSG
    exit 0
fi
# Keep perms correct even if .env already existed.
sudo chown ubuntu:"$SERVICE_USER" "$APP_DIR/.env"
sudo chmod 640 "$APP_DIR/.env"
echo "    verifying DATABASE_URL parses..."
uv run python3 -c "from awkns_outreach.config import get_settings; get_settings().database_url" \
    || { echo "    .env is not valid yet — fix it and re-run (see DEPLOY.md)." >&2; exit 1; }

echo "==> [7/7] Migrations + systemd services"
uv run alembic upgrade head
sudo cp "$APP_DIR"/deploy/systemd/awkns-web.service  /etc/systemd/system/
sudo cp "$APP_DIR"/deploy/systemd/awkns-cron.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now awkns-web awkns-cron

echo "==> Provisioning complete"
sleep 2
if curl -fsS http://localhost:8000/healthz > /dev/null; then
    echo "    web /healthz OK"
else
    echo "    web /healthz FAILED — check: journalctl -u awkns-web -n 50" >&2
fi
systemctl --no-pager --lines=0 status awkns-web awkns-cron || true

echo
echo "Next: open port 8000 to your IP in the security group (awkns-outreach-ec2-sg)."
echo "The awkns-cron service runs with --send and will send REAL email."
