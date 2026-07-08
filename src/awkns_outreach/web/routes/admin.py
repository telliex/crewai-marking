"""Admin dashboard (HTTP-Basic gated): campaigns, leads, and the enrich / angle /
run actions. Server-rendered (Jinja2 + HTMX) so the whole service is one Python
app with one deploy."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from awkns_outreach.apollo.client import domain_from_website
from awkns_outreach.apollo.enrich import enrich_campaign
from awkns_outreach.apollo.seed import SEED_FIELDS, parse_seed_companies
from awkns_outreach.db.models import Campaign, Lead, Suppression
from awkns_outreach.sequencer import process_campaign
from awkns_outreach.web.deps import get_db, require_admin, templates
from awkns_outreach.web.stats import campaign_stats

router = APIRouter(dependencies=[Depends(require_admin)])

# Placeholders the mailer fills per lead (send/mailer.py `_context`). Shown as a
# cheatsheet in the sequence editor.
SEQUENCE_PLACEHOLDERS = [
    "first_name", "company", "contact_name", "contact_title",
    "country", "angle", "sender_name",
]


def _split_lines(text: str) -> list[str]:
    return [ln.strip() for ln in (text or "").replace(",", "\n").splitlines() if ln.strip()]


def _read_seed_input(seed_file: Optional[UploadFile], seed_text: str) -> list[dict]:
    """Parse seed companies from an uploaded file (preferred) or pasted text.

    Raises ValueError (via parse_seed_companies) on malformed JSON so callers
    can surface it to the operator.
    """
    if seed_file is not None and seed_file.filename:
        raw = seed_file.file.read().decode("utf-8", "replace")
        return parse_seed_companies(raw, seed_file.filename)
    if seed_text.strip():
        return parse_seed_companies(seed_text, None)
    return []


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    campaigns = db.scalars(select(Campaign).order_by(Campaign.created_at.desc())).all()
    rows = [{"c": c, "stats": campaign_stats(db, c)} for c in campaigns]
    suppressed = db.scalar(select(Suppression).with_only_columns(Suppression.email).limit(1))
    return templates.TemplateResponse(
        request, "dashboard.html", {"rows": rows, "has_suppressions": suppressed is not None}
    )


@router.get("/campaigns/new", response_class=HTMLResponse)
def new_campaign_form(request: Request):
    return templates.TemplateResponse(request, "new_campaign.html", {})


@router.post("/campaigns")
def create_campaign(
    name: str = Form(...),
    titles: str = Form(""),
    angle_prompt: str = Form(""),
    seed_text: str = Form(""),
    seed_file: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
):
    try:
        seed_companies = _read_seed_input(seed_file, seed_text)
        import_note = ""
    except ValueError as exc:
        seed_companies = []
        import_note = f" (seed import failed: {exc} — add companies on the edit page)"
    c = Campaign(
        name=name.strip(),
        target_titles=_split_lines(titles),
        seed_companies=seed_companies,
        angle_prompt=angle_prompt.strip() or None,
        sequence=[],
        sender_identity={},
    )
    db.add(c)
    db.commit()
    msg = f"Campaign created with {len(seed_companies)} seed companies.{import_note}"
    return RedirectResponse(f"/campaigns/{c.id}?msg={msg}", status_code=303)


def _get_campaign(db: Session, campaign_id: str) -> Campaign:
    c = db.get(Campaign, campaign_id)
    if not c:
        raise HTTPException(404, "Campaign not found")
    return c


@router.get("/campaigns/{campaign_id}", response_class=HTMLResponse)
def campaign_detail(
    campaign_id: str, request: Request, db: Session = Depends(get_db),
    msg: Optional[str] = None,
):
    c = _get_campaign(db, campaign_id)
    leads = db.scalars(
        select(Lead).where(Lead.campaign_id == c.id)
        .order_by(Lead.created_at.desc()).limit(200)
    ).all()
    return templates.TemplateResponse(
        request, "campaign.html",
        {"c": c, "stats": campaign_stats(db, c), "leads": leads, "msg": msg},
    )


@router.post("/campaigns/{campaign_id}/enrich")
def run_enrich(
    campaign_id: str,
    reveal: str = Form(""),
    limit: int = Form(10),
    db: Session = Depends(get_db),
):
    c = _get_campaign(db, campaign_id)
    summary = enrich_campaign(db, c, reveal=bool(reveal), limit=limit)
    db.commit()
    verb = "revealed" if reveal else "found (preview)"
    msg = f"Enrich: {summary.total_found} {verb}; created {summary.created}, skipped {summary.skipped_existing}."
    return RedirectResponse(f"/campaigns/{c.id}?msg={msg}", status_code=303)


@router.post("/campaigns/{campaign_id}/run")
def run_sequencer(
    campaign_id: str,
    send: str = Form(""),
    max_this_run: int = Form(5),
    db: Session = Depends(get_db),
):
    c = _get_campaign(db, campaign_id)
    dry = not bool(send)
    s = process_campaign(db, c, dry_run=dry, max_this_run=max_this_run, gap_ms=0)
    mode = "DRY-RUN" if dry else "SENT"
    if s.blocked:
        msg = f"Blocked: {s.blocked}"
    else:
        msg = f"{mode}: sent {s.sent}, skipped {s.skipped}, suppressed {s.suppressed}, errors {s.errors} (cap {s.cap}, remaining {s.daily_remaining})."
    return RedirectResponse(f"/campaigns/{c.id}?msg={msg}", status_code=303)


@router.get("/campaigns/{campaign_id}/sequence", response_class=HTMLResponse)
def edit_sequence_form(campaign_id: str, request: Request, db: Session = Depends(get_db)):
    c = _get_campaign(db, campaign_id)
    return templates.TemplateResponse(
        request, "sequence_edit.html",
        {"c": c, "steps": c.sequence or [], "placeholders": SEQUENCE_PLACEHOLDERS},
    )


@router.post("/campaigns/{campaign_id}/sequence")
def save_sequence(
    campaign_id: str,
    step_key: list[str] = Form(default=[]),
    delay_days: list[str] = Form(default=[]),
    subject: list[str] = Form(default=[]),
    body: list[str] = Form(default=[]),
    db: Session = Depends(get_db),
):
    c = _get_campaign(db, campaign_id)
    steps: list[dict] = []
    for i, (k, d, subj, b) in enumerate(zip(step_key, delay_days, subject, body)):
        # Skip fully blank rows (a step needs at least a subject or a body).
        if not subj.strip() and not b.strip():
            continue
        try:
            delay = max(0, int(d))
        except (TypeError, ValueError):
            delay = 0
        steps.append({
            "key": k.strip() or f"step{i + 1}",
            "delay_days": delay,
            "subject": subj.strip(),
            "body": b.rstrip(),
        })
    c.sequence = steps
    db.commit()
    msg = f"Sequence saved: {len(steps)} step(s)."
    return RedirectResponse(f"/campaigns/{c.id}?msg={msg}", status_code=303)


@router.get("/campaigns/{campaign_id}/companies", response_class=HTMLResponse)
def edit_companies_form(
    campaign_id: str, request: Request, db: Session = Depends(get_db),
    msg: Optional[str] = None,
):
    c = _get_campaign(db, campaign_id)
    return templates.TemplateResponse(
        request, "seed_companies_edit.html",
        {"c": c, "companies": c.seed_companies or [], "fields": SEED_FIELDS, "msg": msg},
    )


def _rows_from_form(values: dict[str, list[str]]) -> list[dict]:
    """Zip the per-field form arrays into seed dicts, dropping blank rows."""
    columns = [values.get(f, []) for f in SEED_FIELDS]
    rows: list[dict] = []
    for cells in zip(*columns):
        row = {f: v.strip() for f, v in zip(SEED_FIELDS, cells) if v.strip()}
        if row.get("website"):
            row["website"] = domain_from_website(row["website"]) or row["website"]
        if row:
            rows.append(row)
    return rows


@router.post("/campaigns/{campaign_id}/companies")
def save_companies(
    campaign_id: str,
    action: str = Form("save"),
    import_mode: str = Form("replace"),
    seed_text: str = Form(""),
    seed_file: Optional[UploadFile] = File(None),
    name: list[str] = Form(default=[]),
    website: list[str] = Form(default=[]),
    country: list[str] = Form(default=[]),
    category: list[str] = Form(default=[]),
    priority: list[str] = Form(default=[]),
    angle: list[str] = Form(default=[]),
    db: Session = Depends(get_db),
):
    c = _get_campaign(db, campaign_id)
    if action == "clear":
        c.seed_companies = []
        msg = "Seed companies cleared."
    elif action == "import":
        try:
            imported = _read_seed_input(seed_file, seed_text)
        except ValueError as exc:
            return RedirectResponse(
                f"/campaigns/{c.id}/companies?msg=Import failed: {exc}", status_code=303
            )
        if import_mode == "append":
            c.seed_companies = (c.seed_companies or []) + imported
            msg = f"Appended {len(imported)} companies (total {len(c.seed_companies)})."
        else:
            c.seed_companies = imported
            msg = f"Imported {len(imported)} companies (replaced)."
    else:  # save row edits
        rows = _rows_from_form({
            "name": name, "website": website, "country": country,
            "category": category, "priority": priority, "angle": angle,
        })
        c.seed_companies = rows
        msg = f"Saved {len(rows)} companies."
    db.commit()
    return RedirectResponse(f"/campaigns/{c.id}/companies?msg={msg}", status_code=303)
