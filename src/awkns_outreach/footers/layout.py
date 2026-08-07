"""Compile a footer's structured block layout into email-safe HTML + plain
text. Nested tables + inline styles only — Gmail/Outlook strip position/flex/
grid, so tables are the one portable layout method. This renderer is the single
source of truth: the save path stores its output as body_html/body_text and the
live preview renders from it, so the editor never diverges from the sent email.

`{unsubscribe_url}` is passed through verbatim (never escaped) — it is
substituted per-recipient at send time by compliance.footer_html/footer_text.
"""
from __future__ import annotations

import re
from html import escape
from typing import Any, Callable

_DEFAULT_WIDTH = 560

# Gmail-style font sizes (Small / Normal / Large / Huge) → px. "normal" keeps
# the footer's original 13px so existing text blocks render unchanged.
_TEXT_SIZES = {"small": 11, "normal": 13, "large": 18, "huge": 28}


def _text_block(b: dict[str, Any]) -> str:
    align = b.get("align", "left")
    size = _TEXT_SIZES.get(b.get("size", "normal"), _TEXT_SIZES["normal"])
    # Operator-authored inline HTML (links, {unsubscribe_url}); trusted content.
    return (
        f'<div style="font-size:{size}px;line-height:1.6;color:#5f6368;'
        f'text-align:{align}">{b.get("html", "")}</div>'
    )


def _image_block(b: dict[str, Any]) -> str:
    src = escape(str(b.get("src", "")), quote=True)
    alt = escape(str(b.get("alt", "")), quote=True)
    align = b.get("align", "left")
    width = int(b.get("width") or 0)
    # `offset` is the gap in px from the aligned side (left→margin-left,
    # right→margin-right). Ignored for center. Kept separate from width so the
    # two aren't confused in the editor.
    offset = int(b.get("offset") or 0)
    wattr = f' width="{width}"' if width else ""
    margin = ""
    if offset and align == "left":
        margin = f"margin-left:{offset}px;"
    elif offset and align == "right":
        margin = f"margin-right:{offset}px;"
    img = (
        f'<img src="{src}" alt="{alt}"{wattr} '
        f'style="display:inline-block;border:0;max-width:100%;height:auto;{margin}">'
    )
    href = b.get("href")
    if href:
        img = f'<a href="{escape(str(href), quote=True)}" target="_blank">{img}</a>'
    return f'<div style="text-align:{align}">{img}</div>'


def _button_block(b: dict[str, Any]) -> str:
    label = escape(str(b.get("label", "")))
    href = escape(str(b.get("href", "")), quote=True)
    bg = b.get("bg", "#0f172a")
    color = b.get("color", "#ffffff")
    align = b.get("align", "left")
    return (
        f'<div style="text-align:{align}">'
        f'<a href="{href}" target="_blank" style="display:inline-block;'
        f'background:{bg};color:{color};text-decoration:none;padding:10px 18px;'
        f'border-radius:6px;font-size:14px;font-weight:600">{label}</a></div>'
    )


def _divider_block(b: dict[str, Any]) -> str:
    if b.get("type") == "spacer":
        h = int(b.get("height") or 16)
        return f'<div style="height:{h}px;line-height:{h}px;font-size:0">&nbsp;</div>'
    return '<div style="border-top:1px solid #e0e0e0;margin:12px 0"></div>'


_RENDERERS: dict[str, Callable[[dict[str, Any]], str]] = {
    "text": _text_block,
    "image": _image_block,
    "button": _button_block,
    "divider": _divider_block,
    "spacer": _divider_block,
}


def _block_html(b: dict[str, Any]) -> str:
    return _RENDERERS.get(b.get("type", ""), lambda _b: "")(b)


def _column_html(col: dict[str, Any]) -> str:
    return "".join(
        f'<div style="padding:4px 0">{_block_html(b)}</div>'
        for b in col.get("blocks", []) or []
    )


def _strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s or "")


def _plain_text(layout: dict[str, Any]) -> str:
    lines: list[str] = []
    for row in layout.get("rows", []) or []:
        for col in row.get("columns", []) or []:
            for b in col.get("blocks", []) or []:
                t = b.get("type")
                if t == "text":
                    # Keep {unsubscribe_url} if present in an href.
                    hrefs = re.findall(r'href="([^"]+)"', b.get("html", ""))
                    line = _strip_tags(b.get("html", "")).strip()
                    for h in hrefs:
                        if h not in line:
                            line = f"{line} ({h})".strip()
                    lines.append(line)
                elif t == "button":
                    lines.append(f'{b.get("label", "")} ({b.get("href", "")})'.strip())
                elif t == "image" and b.get("href"):
                    lines.append(f'{b.get("alt") or "image"} ({b["href"]})')
    return "\n".join(x for x in lines if x).strip()


def render_layout(layout: dict[str, Any]) -> tuple[str, str]:
    """Compile a layout dict into (body_html, body_text)."""
    layout = layout or {}
    width = int(layout.get("width") or _DEFAULT_WIDTH)
    trs: list[str] = []
    for row in layout.get("rows", []) or []:
        cols = row.get("columns", []) or []
        n = max(len(cols), 1)
        pct = 100 // n
        tds = "".join(
            f'<td valign="top" width="{pct}%" '
            f'style="padding:0 6px;vertical-align:top">{_column_html(col)}</td>'
            for col in cols
        )
        trs.append(f"<tr>{tds}</tr>")
    html = (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        f'width="100%" style="max-width:{width}px;margin:0 auto">{"".join(trs)}</table>'
    )
    return html, _plain_text(layout)
