import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, call
from awkns_outreach.main import sanitize_filename, process_lead, run_daily


def test_sanitize_filename_spaces():
    assert sanitize_filename("Sunny Nails") == "Sunny_Nails"


def test_sanitize_filename_special_chars():
    assert sanitize_filename("Bob's Café & Grill!") == "Bobs_Caf_Grill"


def test_sanitize_filename_multiple_spaces():
    assert sanitize_filename("Cool  Cuts") == "Cool_Cuts"


@patch("awkns_outreach.main.OutreachCrew")
@patch("awkns_outreach.main.mark_as_drafted")
def test_process_lead_writes_file(mock_mark, mock_crew_cls, tmp_path):
    mock_crew = MagicMock()
    mock_crew.run.return_value = "Subject: Test\n\nHello Jane"
    mock_crew_cls.return_value = mock_crew

    lead = {
        "shop_name": "Sunny Nails",
        "industry": "Nail Salon",
        "website": "sunnynails.com",
        "social_media": "@sunnynails",
        "contact_name": "Jane Doe",
        "email": "jane@sunnynails.com",
        "phone": "555-1234",
        "city": "Austin",
        "state": "TX",
        "notes": "",
        "row_index": 2,
    }

    process_lead(lead, output_dir=tmp_path)

    output_file = tmp_path / "Sunny_Nails.md"
    assert output_file.exists()
    content = output_file.read_text()
    assert "Subject: Test" in content
    assert "Hello Jane" in content
    mock_mark.assert_called_once_with(2)


@patch("awkns_outreach.main.get_unprocessed_leads")
@patch("awkns_outreach.main.process_lead")
def test_run_daily_processes_all_leads(mock_process, mock_get_leads):
    mock_get_leads.return_value = [
        {"shop_name": "A", "row_index": 2},
        {"shop_name": "B", "row_index": 3},
    ]
    run_daily()
    assert mock_process.call_count == 2


@patch("awkns_outreach.main.get_unprocessed_leads")
@patch("awkns_outreach.main.process_lead")
def test_run_daily_continues_on_error(mock_process, mock_get_leads):
    mock_get_leads.return_value = [
        {"shop_name": "A", "row_index": 2},
        {"shop_name": "B", "row_index": 3},
    ]
    mock_process.side_effect = [Exception("API error"), None]
    run_daily()  # should not raise
    assert mock_process.call_count == 2
