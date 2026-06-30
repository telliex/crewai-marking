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

# Column positions (1-indexed) matching the sheet schema
_COL_STATUS = 10
_COL_LAST_CONTACT = 11


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


def mark_as_drafted(row_index: int) -> None:
    """Set Status='Drafted' and Last Contact Date=today for the given row."""
    ws = _get_worksheet()
    ws.update_cell(row_index, _COL_STATUS, "Drafted")
    ws.update_cell(row_index, _COL_LAST_CONTACT, str(date.today()))
