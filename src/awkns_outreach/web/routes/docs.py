"""Docs: render the project's top-level Markdown docs (docs/*.md) as pages.

A lightweight in-app documentation viewer — the Docs nav item lists every
top-level `.md` file under the repo's `docs/` directory and renders the
selected one to HTML. Subdirectories (e.g. docs/superpowers dev plans) are
intentionally excluded; only human-facing guides at the top level show.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from markdown_it import MarkdownIt

from awkns_outreach.web.deps import require_admin, templates

router = APIRouter(dependencies=[Depends(require_admin)])

# Repo root is 4 parents up from this file (…/src/awkns_outreach/web/routes/).
_DOCS_DIR = Path(__file__).resolve().parents[4] / "docs"
_md = MarkdownIt("gfm-like")  # CommonMark + tables/strikethrough/linkify


def _title(path: Path) -> str:
    """The doc's first `# ` heading, else a title-cased slug."""
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("# "):
                return line[2:].strip()
    except OSError:
        pass
    return path.stem.replace("-", " ").replace("_", " ").title()


def _list_docs() -> list[dict]:
    """Top-level docs/*.md, alphabetical — {slug, title}."""
    if not _DOCS_DIR.is_dir():
        return []
    return [
        {"slug": p.stem, "title": _title(p)}
        for p in sorted(_DOCS_DIR.glob("*.md"))
    ]


def _doc_path(slug: str) -> Optional[Path]:
    """Resolve a slug to a top-level docs/*.md file, or None. Guards against
    path traversal by only matching the flat, discovered listing."""
    for p in _DOCS_DIR.glob("*.md"):
        if p.stem == slug:
            return p
    return None


@router.get("/docs", response_class=HTMLResponse)
def docs_index(request: Request):
    docs = _list_docs()
    if not docs:
        return templates.TemplateResponse(
            request, "docs.html", {"docs": [], "current": None, "content": None},
        )
    return _render(request, docs[0]["slug"], docs)


@router.get("/docs/{slug}", response_class=HTMLResponse)
def doc_page(slug: str, request: Request):
    return _render(request, slug, _list_docs())


def _render(request: Request, slug: str, docs: list[dict]) -> HTMLResponse:
    path = _doc_path(slug)
    if path is None:
        raise HTTPException(404, "Doc not found")
    content = _md.render(path.read_text(encoding="utf-8"))
    return templates.TemplateResponse(
        request, "docs.html",
        {"docs": docs, "current": slug, "content": content},
    )
