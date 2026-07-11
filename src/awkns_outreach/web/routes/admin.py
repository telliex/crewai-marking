"""Admin dashboard (HTTP-Basic gated): campaigns, leads, and the enrich / angle /
run actions. Server-rendered (Jinja2 + HTMX) so the whole service is one Python
app with one deploy."""
from __future__ import annotations

from math import ceil
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from awkns_outreach.apollo.client import domain_from_website
from awkns_outreach.apollo.enrich import enrich_campaign
from awkns_outreach.apollo.seed import SEED_FIELDS, parse_seed_companies
from awkns_outreach.db.models import Campaign, Lead, MailSequence, Mailbox, Suppression
from awkns_outreach.sequencer import process_campaign
from awkns_outreach.web.deps import get_db, require_admin, templates
from awkns_outreach.web.stats import campaign_stats

router = APIRouter(dependencies=[Depends(require_admin)])

# Dashboard pagination + status filter. "default" (absent/unknown) hides
# archived campaigns; explicit "all" shows everything.
PAGE_SIZE = 20
_STATUS_FILTERS = ("active", "paused", "archived", "all")
# Whitelisted status transitions for POST /campaigns/{id}/status: action -> {from: to}.
_STATUS_TRANSITIONS = {
    "archive": {"active": "archived", "paused": "archived"},
    "unarchive": {"archived": "active"},
    "pause": {"active": "paused"},
    "resume": {"paused": "active"},
}
# Mirror a campaign-status change onto its own running/paused MailSequence
# (if any) so the two status fields don't silently drift when an operator
# uses the dashboard's own pause/resume/archive buttons instead of the Tasks
# page: action -> (sequence statuses to match on, new sequence status).
_SEQUENCE_MIRROR = {
    "pause": (("running",), "paused"),
    "resume": (("paused",), "running"),
    "archive": (("running", "paused"), "stopped"),
}

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
def dashboard(
    request: Request, db: Session = Depends(get_db),
    status: Optional[str] = None, page: int = 1, msg: Optional[str] = None,
):
    """Campaign list: status filter (default hides archived) + a simple
    offset pager. `msg` lands here after status-change/edit redirects."""
    status_filter = status if status in _STATUS_FILTERS else "default"
    stmt = select(Campaign)
    if status_filter == "default":
        stmt = stmt.where(Campaign.status.in_(["active", "paused"]))
    elif status_filter != "all":
        stmt = stmt.where(Campaign.status == status_filter)

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    pages = max(1, ceil(total / PAGE_SIZE))
    page = min(max(1, page), pages)  # clamp to a valid page

    campaigns = db.scalars(
        stmt.order_by(Campaign.created_at.desc())
        .limit(PAGE_SIZE).offset((page - 1) * PAGE_SIZE)
    ).all()
    rows = [{"c": c, "stats": campaign_stats(db, c)} for c in campaigns]
    any_campaigns = (db.scalar(select(func.count()).select_from(Campaign)) or 0) > 0
    suppressed = db.scalar(select(Suppression).with_only_columns(Suppression.email).limit(1))
    return templates.TemplateResponse(
        request, "dashboard.html",
        {
            "rows": rows, "has_suppressions": suppressed is not None,
            "status_filter": status_filter, "page": page, "pages": pages, "total": total,
            "page_size": PAGE_SIZE, "any_campaigns": any_campaigns, "msg": msg,
        },
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


@router.post("/campaigns/{campaign_id}/status")
def change_campaign_status(
    campaign_id: str,
    action: str = Form(...),
    status: str = Form("default"),
    page: int = Form(1),
    db: Session = Depends(get_db),
):
    """Archive / unarchive / pause / resume — one endpoint, a whitelisted
    transition table. A no-op transition (e.g. pausing an already-paused
    campaign) just redirects with a message, no error. Redirects back to the
    operator's filtered/paginated dashboard view."""
    c = _get_campaign(db, campaign_id)
    transitions = _STATUS_TRANSITIONS.get(action)
    if transitions is None:
        raise HTTPException(400, f"Unknown action: {action}")
    new_status = transitions.get(c.status)
    if new_status is None:
        msg = f"Campaign “{c.name}” is already {c.status}."
    else:
        c.status = new_status
        mirror = _SEQUENCE_MIRROR.get(action)
        if mirror:
            from_statuses, to_status = mirror
            seq = db.scalar(
                select(MailSequence).where(
                    MailSequence.campaign_id == c.id, MailSequence.status.in_(from_statuses),
                )
            )
            if seq is not None:
                seq.status = to_status
        db.commit()
        msg = f"Campaign “{c.name}” {action}d."
    return RedirectResponse(f"/?status={status}&page={page}&msg={msg}", status_code=303)


def _archived_edit_guard(c: Campaign) -> Optional[RedirectResponse]:
    """Server-side backup for the disabled Edit button/link: archived
    campaigns can't be edited (both GET and POST) until unarchived."""
    if c.status == "archived":
        return RedirectResponse(
            "/?msg=Archived campaigns can’t be edited — unarchive first.", status_code=303
        )
    return None


@router.get("/campaigns/{campaign_id}/edit", response_class=HTMLResponse)
def edit_campaign_form(campaign_id: str, request: Request, db: Session = Depends(get_db)):
    c = _get_campaign(db, campaign_id)
    blocked = _archived_edit_guard(c)
    if blocked:
        return blocked
    mailboxes = db.scalars(
        select(Mailbox).where(Mailbox.status == "connected").order_by(Mailbox.email)
    ).all()
    return templates.TemplateResponse(
        request, "campaign_edit.html", {"c": c, "mailboxes": mailboxes}
    )


@router.post("/campaigns/{campaign_id}/edit")
def save_campaign_edit(
    campaign_id: str,
    name: str = Form(...),
    description: str = Form(""),
    titles: str = Form(""),
    angle_prompt: str = Form(""),
    mailbox_id: str = Form(""),
    db: Session = Depends(get_db),
):
    c = _get_campaign(db, campaign_id)
    blocked = _archived_edit_guard(c)
    if blocked:
        return blocked
    c.name = name.strip()
    c.description = description.strip() or None
    c.target_titles = _split_lines(titles)
    c.angle_prompt = angle_prompt.strip() or None
    # Empty option = "Default (Resend)" -> NULL mailbox_id, today's behaviour.
    c.mailbox_id = mailbox_id or None
    db.commit()
    return RedirectResponse(f"/campaigns/{c.id}?msg=Campaign updated.", status_code=303)


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


@router.get("/campaigns/{campaign_id}/sequence")
def edit_sequence_form(campaign_id: str, db: Session = Depends(get_db)):
    _get_campaign(db, campaign_id)  # 404s if the campaign doesn't exist, same as before
    return RedirectResponse("/sequences", status_code=303)


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
