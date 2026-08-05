"""Settings → Footer: manage the footer-template library.

One row is the immutable `is_default` footer (the Pounds Network block seeded by
migration 0011); it can't be edited, archived, or deleted. Operators can add
more footers, which sequence steps / email templates pick via `footer_choice`.
A footer body may contain `{unsubscribe_url}`, substituted per-recipient at send
time — see compliance.footer_html / footer_text.
"""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from awkns_outreach import compliance
from awkns_outreach.db.models import FooterTemplate
from awkns_outreach.footers.layout import render_layout
from awkns_outreach.web.deps import get_db, require_admin, templates

router = APIRouter(dependencies=[Depends(require_admin)])

# Sample recipient used to render the {unsubscribe_url} placeholder in previews.
_PREVIEW_EMAIL = "jamie@acmestudios.example"


def _get_footer(db: Session, footer_id: str) -> FooterTemplate:
    f = db.get(FooterTemplate, footer_id)
    if not f:
        raise HTTPException(404, "Footer not found")
    return f


def _default_guard(f: FooterTemplate) -> Optional[RedirectResponse]:
    """The seeded default footer is immutable — reject edit/archive/delete."""
    if f.is_default:
        return RedirectResponse(
            "/settings/footers?msg=The default footer can't be modified.",
            status_code=303,
        )
    return None


def _preview_html(f: FooterTemplate) -> str:
    return compliance.footer_html(f, _PREVIEW_EMAIL)


def _apply_layout(f: FooterTemplate, layout_json: str) -> Optional[str]:
    """Compile a builder layout into the footer's stored fields. Returns a
    warning message (or None). Raises ValueError on malformed JSON."""
    layout = json.loads(layout_json)
    html, text = render_layout(layout)
    f.layout = layout
    f.body_html = html
    f.body_text = text
    if "{unsubscribe_url}" not in html and "{unsubscribe_url}" not in text:
        return "Saved, but no unsubscribe link — add {unsubscribe_url} (legally required)."
    return None


@router.get("/settings/footers", response_class=HTMLResponse)
def list_footers(request: Request, db: Session = Depends(get_db), msg: Optional[str] = None):
    # Default first, then newest.
    footers = db.scalars(
        select(FooterTemplate).order_by(
            FooterTemplate.is_default.desc(), FooterTemplate.created_at.desc()
        )
    ).all()
    rows = [{"f": f, "preview": _preview_html(f)} for f in footers]
    return templates.TemplateResponse(
        request, "footer_list.html", {"rows": rows, "msg": msg},
    )


@router.get("/settings/footers/new", response_class=HTMLResponse)
def new_footer_form(request: Request):
    return templates.TemplateResponse(
        request, "footer_edit.html", {"f": None, "msg": None, "preview": None},
    )


@router.post("/settings/footers")
def create_footer(
    name: str = Form(...), body_html: str = Form(""), body_text: str = Form(""),
    layout: str = Form(""), db: Session = Depends(get_db),
):
    f = FooterTemplate(
        name=name.strip(), body_html=body_html.strip(), body_text=body_text.strip(),
        is_default=False,
    )
    warn = None
    if layout.strip():
        try:
            warn = _apply_layout(f, layout)
        except ValueError:
            return RedirectResponse(
                "/settings/footers/new?msg=Invalid layout data.", status_code=303)
    db.add(f)
    db.commit()
    msg = warn or "Footer created."
    return RedirectResponse(f"/settings/footers/{f.id}/edit?msg={msg}", status_code=303)


@router.get("/settings/footers/{footer_id}/edit", response_class=HTMLResponse)
def edit_footer_form(
    footer_id: str, request: Request, db: Session = Depends(get_db), msg: Optional[str] = None,
):
    f = _get_footer(db, footer_id)
    blocked = _default_guard(f)
    if blocked:
        return blocked
    return templates.TemplateResponse(
        request, "footer_edit.html", {"f": f, "msg": msg, "preview": _preview_html(f)},
    )


@router.post("/settings/footers/{footer_id}/edit")
def update_footer(
    footer_id: str,
    action: str = Form("save"),
    name: str = Form(""),
    body_html: str = Form(""),
    body_text: str = Form(""),
    layout: str = Form(""),
    db: Session = Depends(get_db),
):
    f = _get_footer(db, footer_id)
    blocked = _default_guard(f)
    if blocked:
        return blocked

    if action == "delete":
        db.delete(f)
        db.commit()
        return RedirectResponse("/settings/footers?msg=Footer deleted.", status_code=303)
    if action in ("archive", "unarchive"):
        f.status = "archived" if action == "archive" else "active"
        db.commit()
        return RedirectResponse(
            f"/settings/footers?msg=Footer {action}d.", status_code=303
        )
    if action != "save":
        raise HTTPException(400, f"Unknown action: {action}")

    f.name = name.strip()
    warn = None
    if layout.strip():
        try:
            warn = _apply_layout(f, layout)
        except ValueError:
            return RedirectResponse(
                f"/settings/footers/{f.id}/edit?msg=Invalid layout data.",
                status_code=303)
    else:
        f.body_html = body_html.strip()
        f.body_text = body_text.strip()
    db.commit()
    msg = warn or "Footer saved."
    return RedirectResponse(f"/settings/footers/{f.id}/edit?msg={msg}", status_code=303)


@router.post("/settings/footers/preview-fragment", response_class=HTMLResponse)
def footer_preview_fragment(request: Request, layout: str = Form("{}")):
    try:
        html, _ = render_layout(json.loads(layout))
    except (ValueError, TypeError):
        html = '<p style="color:#b91c1c">Invalid layout.</p>'
    # Show a placeholder unsubscribe target so the preview link isn't literal.
    html = html.replace("{unsubscribe_url}", "#preview-unsubscribe")
    return templates.TemplateResponse(
        request, "_footer_preview_fragment.html", {"preview": html})
