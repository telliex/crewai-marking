#!/usr/bin/env python
import logging
import os
import re
from datetime import date
from pathlib import Path

import schedule
import time
from dotenv import load_dotenv

from awkns_outreach.crew import OutreachCrew
from awkns_outreach.tools.sheets_tool import get_unprocessed_leads, mark_as_drafted

load_dotenv()

_OUTPUT_DIR = Path(__file__).parent.parent.parent / "outputs"
_LOG_DIR = Path(__file__).parent.parent.parent / "logs"
_OUTPUT_DIR.mkdir(exist_ok=True)
_LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(_LOG_DIR / f"run_{date.today()}.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


def sanitize_filename(name: str) -> str:
    """Replace spaces with underscores and remove non-alphanumeric chars."""
    name = re.sub(r"[^\w\s]", "", name, flags=re.ASCII)
    name = re.sub(r"\s+", "_", name.strip())
    return name


def process_lead(lead: dict, output_dir: Path = _OUTPUT_DIR) -> None:
    """Run the outreach crew for one lead and save the draft."""
    shop_name = lead["shop_name"]
    log.info(f"Processing: {shop_name}")

    inputs = {
        "lead_name": shop_name,
        "industry": lead.get("industry", ""),
        "website": lead.get("website", ""),
        "social_media": lead.get("social_media", ""),
        "key_decision_maker": lead.get("contact_name", ""),
        "position": "",
        "city": lead.get("city", ""),
        "state": lead.get("state", ""),
        "notes": lead.get("notes", ""),
    }

    draft = OutreachCrew().run(inputs)

    filename = sanitize_filename(shop_name) + ".md"
    (output_dir / filename).write_text(draft, encoding="utf-8")
    log.info(f"Draft saved: {filename}")

    mark_as_drafted(lead["row_index"])
    log.info(f"Marked as Drafted in Google Sheets: row {lead['row_index']}")


def run_daily() -> None:
    """Fetch all unprocessed leads and process them one by one."""
    log.info("=== Daily outreach run started ===")
    leads = get_unprocessed_leads()
    log.info(f"Found {len(leads)} unprocessed leads")

    for lead in leads:
        try:
            process_lead(lead)
        except Exception as e:
            log.error(f"Failed to process {lead.get('shop_name')}: {e}")

    log.info("=== Daily outreach run complete ===")


def main() -> None:
    schedule_time = os.getenv("SCHEDULE_TIME", "09:00")
    log.info(f"Scheduler starting — daily run at {schedule_time}")
    schedule.every().day.at(schedule_time).do(run_daily)

    while True:
        schedule.run_pending()
        time.sleep(60)


if __name__ == "__main__":
    main()
