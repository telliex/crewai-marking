"""FastAPI application factory.

One service serves everything: the public compliance endpoints (unsubscribe +
Resend webhook) and the HTTP-Basic-gated admin dashboard. Run with:

    uv run uvicorn awkns_outreach.web.app:app --reload
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from awkns_outreach.config_store import apply_overrides
from awkns_outreach.db.session import session_scope
from awkns_outreach.uploads import UPLOAD_DIR
from awkns_outreach.web.routes import (
    admin,
    docs,
    mailboxes,
    public,
    senders,
    sequences,
    settings,
    tasks,
    templates_lib,
    variables,
)

log = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Push DB-backed variable overrides into settings + os.environ so a restart
    # still respects values saved on the Variables page. Never block startup if
    # the table isn't migrated yet — fall back to .env.
    try:
        with session_scope() as db:
            apply_overrides(db)
    except Exception:  # pragma: no cover - defensive
        log.warning("apply_overrides failed at startup; using .env values", exc_info=True)
    yield


def create_app() -> FastAPI:
    # Swagger UI moved off /docs so that path can serve the human docs viewer.
    app = FastAPI(title="Awkns Outreach", docs_url="/api-docs", lifespan=_lifespan)
    app.include_router(public.router)
    app.include_router(admin.router)
    app.include_router(mailboxes.router)
    app.include_router(templates_lib.router)
    app.include_router(sequences.router)
    app.include_router(tasks.router)
    app.include_router(settings.router)
    app.include_router(variables.router)
    app.include_router(senders.router)
    app.include_router(docs.router)

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
