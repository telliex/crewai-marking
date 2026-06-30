import pytest
from datetime import date
from unittest.mock import MagicMock, patch
from awkns_outreach.tools.sheets_tool import get_unprocessed_leads, mark_as_drafted


@pytest.fixture
def mock_worksheet():
    ws = MagicMock()
    ws.row_values.return_value = ["Shop Name", "Industry", "Website", "Social Media", "Contact Name", "Email", "Phone", "City", "State", "Status", "Last Contact Date", "Next Follow Up", "Notes"]
    ws.get_all_records.return_value = [
        {
            "Shop Name": "Sunny Nails",
            "Industry": "Nail Salon",
            "Website": "sunnynails.com",
            "Social Media": "@sunnynails",
            "Contact Name": "Jane Doe",
            "Email": "jane@sunnynails.com",
            "Phone": "555-1234",
            "City": "Austin",
            "State": "TX",
            "Status": "",
            "Last Contact Date": "",
            "Next Follow Up": "",
            "Notes": "",
        },
        {
            "Shop Name": "Cool Cuts",
            "Industry": "Barbershop",
            "Website": "",
            "Social Media": "",
            "Contact Name": "Bob Smith",
            "Email": "bob@coolcuts.com",
            "Phone": "",
            "City": "Dallas",
            "State": "TX",
            "Status": "Drafted",
            "Last Contact Date": "2026-06-28",
            "Next Follow Up": "",
            "Notes": "",
        },
    ]
    return ws


@patch("awkns_outreach.tools.sheets_tool._get_worksheet")
def test_get_unprocessed_leads_filters_drafted(mock_get_ws, mock_worksheet):
    mock_get_ws.return_value = mock_worksheet
    leads = get_unprocessed_leads()
    assert len(leads) == 1
    assert leads[0]["shop_name"] == "Sunny Nails"
    assert leads[0]["row_index"] == 2  # 1-indexed header + 1 data row


@patch("awkns_outreach.tools.sheets_tool._get_worksheet")
def test_get_unprocessed_leads_fields(mock_get_ws, mock_worksheet):
    mock_get_ws.return_value = mock_worksheet
    leads = get_unprocessed_leads()
    lead = leads[0]
    assert lead["industry"] == "Nail Salon"
    assert lead["contact_name"] == "Jane Doe"
    assert lead["email"] == "jane@sunnynails.com"


@patch("awkns_outreach.tools.sheets_tool._get_worksheet")
def test_mark_as_drafted_updates_cells(mock_get_ws, mock_worksheet):
    mock_get_ws.return_value = mock_worksheet
    mark_as_drafted(2)
    # Status is column 10 (J), Last Contact Date is column 11 (K)
    mock_worksheet.update_cell.assert_any_call(2, 10, "Drafted")
    mock_worksheet.update_cell.assert_any_call(2, 11, str(date.today()))
