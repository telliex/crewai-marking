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
