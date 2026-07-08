"""Shared web dependencies: DB session, templates, and admin auth."""
from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from awkns_outreach.config import settings
from awkns_outreach.db.session import get_db  # noqa: F401  (re-exported)

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

_security = HTTPBasic(auto_error=True)


def require_admin(creds: HTTPBasicCredentials = Depends(_security)) -> str:
    """HTTP Basic gate. Username is ignored; the password must match
    ADMIN_PASSWORD. Refuses outright if no password is configured."""
    if not settings.admin_password:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ADMIN_PASSWORD is not set — admin UI disabled.",
        )
    if not secrets.compare_digest(creds.password, settings.admin_password):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return creds.username
