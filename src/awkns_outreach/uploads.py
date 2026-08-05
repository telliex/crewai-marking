"""Shared local-disk upload location for user-supplied files (template body
images, email attachments). Its own module because both the web layer
(upload endpoints, static file mount) and the send layer (mailer.py reads
attachment bytes back off disk at send time) need it — putting it on either
side would create a circular import between the two.
"""
from __future__ import annotations

import os
from pathlib import Path

# Default lives inside the package only for local dev / tests. In production set
# the UPLOAD_DIR env var to a data directory OUTSIDE the code tree that the
# service user owns (the systemd units set it to the unit's StateDirectory,
# /var/lib/awkns-outreach/uploads) — otherwise uploads land in a dir the service
# user can't write, or get entangled with `git pull` / `uv sync` on redeploy.
_DEFAULT_UPLOAD_DIR = Path(__file__).resolve().parent / "web" / "static" / "uploads"

_env_dir = os.environ.get("UPLOAD_DIR")
UPLOAD_DIR = Path(_env_dir).expanduser() if _env_dir else _DEFAULT_UPLOAD_DIR
