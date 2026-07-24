"""parse_seed_companies: JSON and CSV import into canonical seed dicts."""
import pytest

from awkns_outreach.apollo.seed import parse_seed_companies


def test_parse_json_array_normalizes_website_and_aliases():
    raw = (
        '[{"company": "Toyota", "url": "https://www.toyota.co.jp",'
        ' "industry": "automotive", "tier": "A", "angle": "cars"},'
        ' {"name": "Sony", "website": "sony.co.jp"}]'
    )
    out = parse_seed_companies(raw, "companies.json")
    assert out == [
        {"name": "Toyota", "website": "toyota.co.jp",
         "category": "automotive", "tier": "A", "angle": "cars"},
        {"name": "Sony", "website": "sony.co.jp"},
    ]


def test_parse_json_array_legacy_priority_key_maps_to_tier():
    # Both spellings of the column are accepted; either maps to canonical "tier".
    out = parse_seed_companies('[{"name": "Toyota", "priority": "A"}]', None)
    assert out == [{"name": "Toyota", "tier": "A"}]


def test_parse_single_json_object_wrapped():
    out = parse_seed_companies('{"name": "Solo", "website": "solo.io"}', None)
    assert out == [{"name": "Solo", "website": "solo.io"}]


def test_parse_csv_drops_blank_rows_and_trims():
    raw = "name,website,country\nToyota, toyota.co.jp ,JP\n,,\nSony,sony.co.jp,JP\n"
    out = parse_seed_companies(raw, "seed.csv")
    assert out == [
        {"name": "Toyota", "website": "toyota.co.jp", "country": "JP"},
        {"name": "Sony", "website": "sony.co.jp", "country": "JP"},
    ]


def test_parse_row_without_website_is_kept_but_has_no_domain():
    # A metadata-only row survives (website is optional); enrich will skip it
    # at query time since it can't derive a domain.
    out = parse_seed_companies('[{"name": "NoSite", "priority": "C"}]', None)
    assert out == [{"name": "NoSite", "tier": "C"}]


def test_parse_json_array_captures_email_and_contact_fields():
    raw = (
        '[{"name": "Toyota", "email": "jamie@toyota.co.jp",'
        ' "contact_name": "Jamie Rivera", "contact_title": "VP Finance"}]'
    )
    out = parse_seed_companies(raw, None)
    assert out == [{
        "name": "Toyota", "email": "jamie@toyota.co.jp",
        "contact_name": "Jamie Rivera", "contact_title": "VP Finance",
    }]


def test_parse_csv_accepts_contact_and_title_aliases():
    raw = "name,email,contact,title\nToyota,jamie@toyota.co.jp,Jamie Rivera,VP Finance\n"
    out = parse_seed_companies(raw, "seed.csv")
    assert out == [{
        "name": "Toyota", "email": "jamie@toyota.co.jp",
        "contact_name": "Jamie Rivera", "contact_title": "VP Finance",
    }]


def test_parse_empty_returns_empty():
    assert parse_seed_companies("", None) == []
    assert parse_seed_companies("   ", "x.json") == []


def test_parse_bad_json_raises():
    with pytest.raises(ValueError):
        parse_seed_companies("{not json", "x.json")


def test_parse_json_row_missing_name_raises_with_row_number():
    raw = '[{"name": "Toyota", "website": "toyota.co.jp"}, {"website": "sony.co.jp"}]'
    with pytest.raises(ValueError, match="row 2: missing required field 'name'"):
        parse_seed_companies(raw, None)


def test_parse_csv_row_missing_name_raises_with_row_number():
    raw = "name,website\nToyota,toyota.co.jp\n,sony.co.jp\n"
    with pytest.raises(ValueError, match="row 2: missing required field 'name'"):
        parse_seed_companies(raw, "seed.csv")


def test_parse_multiple_bad_rows_lists_every_row_in_one_error():
    raw = '[{"website": "a.com"}, {"name": "Ok"}, {"website": "b.com"}]'
    with pytest.raises(ValueError) as exc_info:
        parse_seed_companies(raw, None)
    message = str(exc_info.value)
    assert "row 1: missing required field 'name'" in message
    assert "row 3: missing required field 'name'" in message
    assert "row 2" not in message  # the valid row must not be reported
