"""Shared local-disk upload location for user-supplied files (template body
images, email attachments). Its own module because both the web layer
(upload endpoints, static file mount) and the send layer (mailer.py reads
attachment bytes back off disk at send time) need it — putting it on either
side would create a circular import between the two.
"""
from __future__ import annotations

from pathlib import Path

UPLOAD_DIR = Path(__file__).resolve().parent / "web" / "static" / "uploads"
