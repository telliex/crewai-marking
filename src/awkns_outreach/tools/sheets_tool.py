import os
from datetime import date

import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

load_dotenv()

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]

def _get_worksheet() -> gspread.Worksheet:
    creds = Credentials.from_service_account_file(
        os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"], scopes=_SCOPES
    )
    client = gspread.authorize(creds)
    sheet = client.open_by_key(os.environ["GOOGLE_SHEETS_ID"])
    return sheet.sheet1


def get_unprocessed_leads() -> list[dict]:
    """Return rows where Status is blank, with 1-indexed row_index."""
    ws = _get_worksheet()
    records = ws.get_all_records()
    leads = []
    for i, row in enumerate(records, start=2):  # row 1 is header
        if not row.get("Status", "").strip():
            leads.append({
                "shop_name": row.get("Shop Name", ""),
                "industry": row.get("Industry", ""),
                "website": row.get("Website", ""),
                "social_media": row.get("Social Media", ""),
                "contact_name": row.get("Contact Name", ""),
                "email": row.get("Email", ""),
                "phone": row.get("Phone", ""),
                "city": row.get("City", ""),
                "state": row.get("State", ""),
                "notes": row.get("Notes", ""),
                "row_index": i,
            })
    return leads


def _get_col_index(ws: gspread.Worksheet, header_name: str) -> int:
    """Return 1-indexed column number for the given header name."""
    headers = ws.row_values(1)
    return headers.index(header_name) + 1


def mark_as_drafted(row_index: int) -> None:
    """Set Status='Drafted' and Last Contact Date=today for the given row."""
    ws = _get_worksheet()
    status_col = _get_col_index(ws, "Status")
    date_col = _get_col_index(ws, "Last Contact Date")
    ws.update_cell(row_index, status_col, "Drafted")
    ws.update_cell(row_index, date_col, str(date.today()))
