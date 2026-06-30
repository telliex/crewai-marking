#!/usr/bin/env python
"""One-shot runner to test the crew with a hardcoded lead — no Sheets required."""
from pathlib import Path
from awkns_outreach.crew import OutreachCrew

TEST_LEAD = {
    "lead_name": "Sunny Nails",
    "industry": "Nail Salon",
    "website": "",
    "social_media": "",
    "key_decision_maker": "Jane Doe",
    "position": "Owner",
    "city": "Austin",
    "state": "TX",
    "notes": "",
}

if __name__ == "__main__":
    print("Running outreach crew for test lead...")
    result = OutreachCrew().run(TEST_LEAD)
    out = Path(__file__).parent.parent.parent / "outputs" / "test_Sunny_Nails.md"
    out.write_text(result, encoding="utf-8")
    print(f"\nDraft saved to {out}")
    print("\n--- DRAFT ---")
    print(result)
