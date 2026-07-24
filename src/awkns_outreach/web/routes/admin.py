"""Admin dashboard (HTTP-Basic gated): campaigns, leads, and the enrich / angle /
run actions. Server-rendered (Jinja2 + HTMX) so the whole service is one Python
app with one deploy."""
from __future__ import annotations

import csv
import io
from math import ceil
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from awkns_outreach.apollo.client import domain_from_website
from awkns_outreach.apollo.enrich import enrich_campaign
from awkns_outreach.apollo.seed import SEED_FIELDS, parse_seed_companies
from awkns_outreach.db.models import Campaign, Lead, Mailbox, Suppression, Task
from awkns_outreach.web.deps import get_db, require_admin, templates
from awkns_outreach.web.stats import campaign_stats
from awkns_outreach.writer.tiers import TIERS, classify_campaign_tiers

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
# Mirror a campaign-status change onto its own running/paused Task (if any)
# so the two status fields don't silently drift when an operator uses the
# dashboard's own pause/resume/archive buttons instead of the Tasks page:
# action -> (task statuses to match on, new task status).
_TASK_MIRROR = {
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


@router.get("/campaigns/seed-template.csv")
def seed_template_csv():
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=SEED_FIELDS)
    writer.writeheader()
    writer.writerow({
        "name": "Toyota", "website": "toyota.co.jp", "country": "JP",
        "category": "automotive", "tier": "A", "angle": "why this fits them",
        "email": "jamie@toyota.co.jp", "contact_name": "Jamie Rivera", "contact_title": "VP Finance",
    })
    writer.writerow({
        "name": "Acme Barbershop", "website": "acmebarbershop.com",
        "country": "US", "category": "barbershop", "tier": "B", "angle": "",
    })
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=seed_companies_template.csv"},
    )


@router.post("/campaigns")
def create_campaign(
    request: Request,
    name: str = Form(...),
    titles: str = Form(""),
    angle_prompt: str = Form(""),
    seed_text: str = Form(""),
    seed_file: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
):
    try:
        seed_companies = _read_seed_input(seed_file, seed_text)
    except ValueError as exc:
        return templates.TemplateResponse(
            request, "new_campaign.html",
            {
                "msg": f"Seed import failed: {exc}",
                "name": name,
                "titles": titles,
                "angle_prompt": angle_prompt,
                "seed_text": seed_text,
            },
        )
    c = Campaign(
        name=name.strip(),
        target_titles=_split_lines(titles),
        seed_companies=seed_companies,
        angle_prompt=angle_prompt.strip() or None,
        sender_identity={},
    )
    db.add(c)
    db.commit()
    msg = f"Campaign created with {len(seed_companies)} seed companies."
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
        mirror = _TASK_MIRROR.get(action)
        if mirror:
            from_statuses, to_status = mirror
            task = db.scalar(
                select(Task).where(
                    Task.campaign_id == c.id, Task.status.in_(from_statuses),
                )
            )
            if task is not None:
                task.status = to_status
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


_TIER_FILTERS = ("A", "B", "C", "unclassified")


@router.get("/campaigns/{campaign_id}", response_class=HTMLResponse)
def campaign_detail(
    campaign_id: str, request: Request, db: Session = Depends(get_db),
    msg: Optional[str] = None, tier: Optional[str] = None,
):
    c = _get_campaign(db, campaign_id)
    tier_filter = tier if tier in _TIER_FILTERS else None

    counts = dict(
        db.execute(
            select(Lead.tier, func.count())
            .where(Lead.campaign_id == c.id)
            .group_by(Lead.tier)
        ).all()
    )
    tier_counts = {t: counts.get(t, 0) for t in (*TIERS, None)}
    tier_total = sum(tier_counts.values())

    stmt = select(Lead).where(Lead.campaign_id == c.id)
    if tier_filter == "unclassified":
        stmt = stmt.where(Lead.tier.is_(None))
    elif tier_filter in TIERS:
        stmt = stmt.where(Lead.tier == tier_filter)
    leads = db.scalars(stmt.order_by(Lead.created_at.desc()).limit(200)).all()

    return templates.TemplateResponse(
        request, "campaign.html",
        {
            "c": c, "stats": campaign_stats(db, c), "leads": leads, "msg": msg,
            "tier_filter": tier_filter, "tier_counts": tier_counts, "tier_total": tier_total,
        },
    )


@router.post("/campaigns/{campaign_id}/enrich")
def run_enrich(
    campaign_id: str,
    reveal: str = Form(""),
    limit: int = Form(10),
    db: Session = Depends(get_db),
):
    c = _get_campaign(db, campaign_id)
    try:
        summary = enrich_campaign(db, c, reveal=bool(reveal), limit=limit)
    except RuntimeError as exc:
        return RedirectResponse(f"/campaigns/{c.id}?msg=Enrich failed: {exc}", status_code=303)
    db.commit()
    verb = "revealed" if reveal else "found (preview)"
    msg = f"Enrich: {summary.total_found} {verb}; created {summary.created}, skipped {summary.skipped_existing}."
    return RedirectResponse(f"/campaigns/{c.id}?msg={msg}", status_code=303)


@router.post("/campaigns/{campaign_id}/classify")
def run_classify(
    campaign_id: str,
    reclassify: str = Form(""),
    limit: int = Form(500),
    db: Session = Depends(get_db),
):
    c = _get_campaign(db, campaign_id)
    try:
        summary = classify_campaign_tiers(db, c, reclassify_all=bool(reclassify), limit=limit)
    except RuntimeError as exc:
        return RedirectResponse(f"/campaigns/{c.id}?msg={exc}", status_code=303)
    a, b, cc = (summary.per_tier.get(t, 0) for t in TIERS)
    msg = (
        f"Classified {summary.classified}/{summary.examined}: "
        f"A {a} · B {b} · C {cc} "
        f"(skipped {summary.skipped}, failed batches {summary.errors})"
    )
    return RedirectResponse(f"/campaigns/{c.id}?msg={msg}", status_code=303)


@router.post("/campaigns/{campaign_id}/leads/from-seed-companies")
def convert_seed_companies_to_leads(
    campaign_id: str,
    db: Session = Depends(get_db),
):
    c = _get_campaign(db, campaign_id)
    existing = db.scalar(
        select(func.count()).select_from(Lead).where(Lead.campaign_id == c.id)
    ) or 0
    if existing > 0:
        return RedirectResponse(
            f"/campaigns/{c.id}?msg=Convert failed — this campaign already has leads.",
            status_code=303,
        )

    rows = c.seed_companies or []
    if not rows:
        return RedirectResponse(f"/campaigns/{c.id}?msg=No seed companies to convert.", status_code=303)

    missing = [r.get("name") or "(unnamed)" for r in rows if not (r.get("name") and r.get("email"))]
    emails = [r["email"].strip().lower() for r in rows if r.get("email")]
    dupes = sorted({e for e in emails if emails.count(e) > 1})
    if missing or dupes:
        parts = []
        if missing:
            parts.append(f"missing name/email: {', '.join(missing)}")
        if dupes:
            parts.append(f"duplicate email: {', '.join(dupes)}")
        return RedirectResponse(
            f"/campaigns/{c.id}?msg=Convert failed — {'; '.join(parts)}.", status_code=303,
        )

    for r in rows:
        db.add(Lead(
            campaign_id=c.id, email=r["email"].strip().lower(), company=r["name"].strip(),
            contact_name=r.get("contact_name") or None, contact_title=r.get("contact_title") or None,
            country=r.get("country") or None, category=r.get("category") or None,
            tier=r.get("tier") or None, angle=r.get("angle") or None, website=r.get("website") or None,
            step=0, status="active",
        ))
    db.commit()
    plural = "y" if len(rows) == 1 else "ies"
    return RedirectResponse(
        f"/campaigns/{c.id}?msg=Converted {len(rows)} seed compan{plural} to leads.", status_code=303,
    )


@router.post("/campaigns/{campaign_id}/leads/{lead_id}/tier", response_class=HTMLResponse)
def set_lead_tier(
    campaign_id: str, lead_id: str, request: Request,
    tier: str = Form(""), db: Session = Depends(get_db),
):
    c = _get_campaign(db, campaign_id)
    lead = db.get(Lead, lead_id)
    if not lead or lead.campaign_id != c.id:
        raise HTTPException(404, "Lead not found")
    if tier not in ("", *TIERS):
        raise HTTPException(400, f"Invalid tier: {tier!r}")
    # empty string must become NULL, not "" — SQL coalesce(tier, "B") and
    # Python "tier or 'B'" treat "" differently, so an un-normalized ""
    # would silently skip the "unset" default-B behavior both rely on.
    lead.tier = tier or None
    db.commit()
    return templates.TemplateResponse(
        request, "_lead_tier_cell.html", {"c": c, "l": lead},
    )


# Manual pause/resume only toggles between these two statuses — every other
# status (sending/completed/replied/bounced/suppressed/failed) is system-
# driven and not meant to be hand-edited from this control.
_MANUAL_LEAD_STATUSES = ("active", "paused")


@router.post("/campaigns/{campaign_id}/leads/{lead_id}/status", response_class=HTMLResponse)
def set_lead_status(
    campaign_id: str, lead_id: str, request: Request,
    status: str = Form(...), db: Session = Depends(get_db),
):
    c = _get_campaign(db, campaign_id)
    lead = db.get(Lead, lead_id)
    if not lead or lead.campaign_id != c.id:
        raise HTTPException(404, "Lead not found")
    if status not in _MANUAL_LEAD_STATUSES:
        raise HTTPException(400, f"Invalid status: {status!r}")
    if lead.status not in _MANUAL_LEAD_STATUSES:
        raise HTTPException(400, f"Lead can't be manually toggled while {lead.status}.")
    lead.status = status
    db.commit()
    return templates.TemplateResponse(
        request, "_lead_status_cell.html", {"c": c, "l": lead},
    )


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
    tier: list[str] = Form(default=[]),
    angle: list[str] = Form(default=[]),
    email: list[str] = Form(default=[]),
    contact_name: list[str] = Form(default=[]),
    contact_title: list[str] = Form(default=[]),
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
            "category": category, "tier": tier, "angle": angle,
            "email": email, "contact_name": contact_name, "contact_title": contact_title,
        })
        c.seed_companies = rows
        msg = f"Saved {len(rows)} companies."
    db.commit()
    return RedirectResponse(f"/campaigns/{c.id}/companies?msg={msg}", status_code=303)
