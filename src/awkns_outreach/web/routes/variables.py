"""Settings → Variables: edit global config variables from the admin UI.

Values are stored as overrides in the `app_setting` table and pushed into the
live Settings singleton + os.environ via config_store.apply_overrides on every
save. See config_store.REGISTRY for the editable set.

Sender-identity variables are frozen onto a Task at start (see
sequencer/lifecycle.start_task), so changing them only affects tasks started
afterwards — the save page's confirmation dialog spells this out. API keys and
AI models apply globally and immediately.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from awkns_outreach import config_store as cs
from awkns_outreach.db.models import SenderProfile
from awkns_outreach.web.deps import get_db, require_admin, templates
from awkns_outreach.web.routes.senders import profile_locked

router = APIRouter(dependencies=[Depends(require_admin)])


def _groups(db: Session) -> list[dict]:
    """Registry grouped by category, with each field's current display value
    and whether it's overridden — the shape the template iterates."""
    effective = cs.effective_values(db)
    overridden = cs.overridden_keys(db)
    by_cat: dict[str, list[dict]] = {}
    for spec in cs.REGISTRY:
        value = effective[spec.env_name]
        by_cat.setdefault(spec.category, []).append({
            "env_name": spec.env_name,
            "label": spec.label,
            "is_secret": spec.is_secret,
            "freeze": spec.freeze,
            # Never send a raw secret to the browser.
            "display": cs.mask_secret(value) if spec.is_secret else value,
            "overridden": spec.env_name in overridden,
        })
    return [{"category": cat, "fields": fields} for cat, fields in by_cat.items()]


@router.get("/settings/variables", response_class=HTMLResponse)
def variables_form(request: Request, db: Session = Depends(get_db), msg: Optional[str] = None):
    # The Sender Identity section renders the saved SenderProfiles as a list
    # under the global default (management + add live on /settings/senders).
    profiles = db.scalars(
        select(SenderProfile).order_by(
            SenderProfile.status.asc(), SenderProfile.name.asc()
        )
    ).all()
    locked = {p.id for p in profiles if profile_locked(db, p.id)}
    return templates.TemplateResponse(
        request, "variables.html",
        {
            "groups": _groups(db), "msg": msg,
            "sender_profiles": profiles, "locked_profiles": locked,
        },
    )


@router.post("/settings/variables")
async def save_variables(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    for spec in cs.REGISTRY:
        raw = form.get(spec.env_name)
        if raw is None:
            continue
        value = str(raw).strip()
        # Secrets: a blank field means "keep the current value" (the form only
        # ever shows a mask, never the real secret), so don't overwrite it.
        if spec.is_secret and value == "":
            continue
        cs.set_override(db, spec.env_name, value)
    db.commit()  # persist before apply_overrides re-reads the table
    cs.apply_overrides(db)
    return RedirectResponse("/settings/variables?msg=Variables saved.", status_code=303)


@router.post("/settings/variables/reset")
def reset_variable(key: str = Form(...), db: Session = Depends(get_db)):
    if key not in cs.BY_KEY:
        return RedirectResponse("/settings/variables?msg=Unknown variable.", status_code=303)
    cs.clear_override(db, key)
    db.commit()  # persist before apply_overrides re-reads the table
    cs.apply_overrides(db)
    label = cs.BY_KEY[key].label
    return RedirectResponse(
        f"/settings/variables?msg={label} reset to the .env default.", status_code=303,
    )
