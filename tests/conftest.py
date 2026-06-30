import os

# Set required env vars before any project modules are imported
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("SERPER_API_KEY", "test-key")
os.environ.setdefault("GOOGLE_SHEETS_ID", "test-sheet-id")
