"""FastAPI application factory.

One service serves everything: the public compliance endpoints (unsubscribe +
Resend webhook) and the HTTP-Basic-gated admin dashboard. Run with:

    uv run uvicorn awkns_outreach.web.app:app --reload
"""
from __future__ import annotations

from fastapi import FastAPI

from awkns_outreach.web.routes import admin, public


def create_app() -> FastAPI:
    app = FastAPI(title="Awkns Outreach", docs_url="/docs")
    app.include_router(public.router)
    app.include_router(admin.router)

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
