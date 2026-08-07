from awkns_outreach.footers.layout import render_layout


def _two_image_row():
    return {"rows": [{"columns": [
        {"blocks": [{"type": "image", "src": "http://x/a.png", "alt": "A"}]},
        {"blocks": [{"type": "image", "src": "http://x/b.png", "alt": "B"}]},
    ]}]}


def test_two_images_render_side_by_side_in_one_row():
    html, _ = render_layout(_two_image_row())
    assert html.count("<td") == 2          # two columns → two cells in one row
    assert html.count("<tr") == 1
    assert "http://x/a.png" in html and "http://x/b.png" in html


def _image(**kw):
    b = {"type": "image", "src": "http://x/a.png", "alt": "A"}
    b.update(kw)
    return {"rows": [{"columns": [{"blocks": [b]}]}]}


def test_image_offset_applies_margin_on_aligned_side():
    # left → margin-left, right → margin-right (gap FROM that side).
    left, _ = render_layout(_image(align="left", offset=20))
    assert "margin-left:20px" in left and "margin-right" not in left
    right, _ = render_layout(_image(align="right", offset=30))
    assert "margin-right:30px" in right and "margin-left" not in right


def test_image_offset_ignored_for_center_and_when_zero():
    center, _ = render_layout(_image(align="center", offset=50))
    assert "margin-left" not in center and "margin-right" not in center
    zero, _ = render_layout(_image(align="left", offset=0))
    assert "margin-left" not in zero


def _text(**kw):
    b = {"type": "text", "html": "Hi"}
    b.update(kw)
    return {"rows": [{"columns": [{"blocks": [b]}]}]}


def test_text_size_maps_to_px():
    assert "font-size:11px" in render_layout(_text(size="small"))[0]
    assert "font-size:18px" in render_layout(_text(size="large"))[0]
    assert "font-size:28px" in render_layout(_text(size="huge"))[0]
    # missing/unknown size → the original 13px "normal", unchanged behavior.
    assert "font-size:13px" in render_layout(_text())[0]
    assert "font-size:13px" in render_layout(_text(size="bogus"))[0]


def test_button_renders_inline_styled_anchor():
    html, _ = render_layout({"rows": [{"columns": [{"blocks": [
        {"type": "button", "label": "Visit", "href": "http://x", "bg": "#111", "color": "#fff"},
    ]}]}]})
    assert "background:#111" in html and ">Visit</a>" in html and 'href="http://x"' in html


def test_unsubscribe_token_passes_through_unescaped():
    html, text = render_layout({"rows": [{"columns": [{"blocks": [
        {"type": "text", "html": 'Bye · <a href="{unsubscribe_url}">Unsubscribe</a>'},
    ]}]}]})
    assert "{unsubscribe_url}" in html
    assert "{unsubscribe_url}" in text  # link href surfaced in plain text


def test_plain_text_includes_text_and_button():
    _, text = render_layout({"rows": [{"columns": [{"blocks": [
        {"type": "text", "html": "<b>Hello</b> world"},
        {"type": "button", "label": "Go", "href": "http://x"},
    ]}]}]})
    assert "Hello world" in text
    assert "Go (http://x)" in text


def test_empty_layout_does_not_crash():
    html, text = render_layout({"rows": []})
    assert "<table" in html
    assert text == ""
