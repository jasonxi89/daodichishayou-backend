import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR / 'food_trends.db'}")
API_PORT = int(os.getenv("API_PORT", "8900"))
CRAWL_INTERVAL_HOURS = int(os.getenv("CRAWL_INTERVAL_HOURS", "6"))
CLAUDE_API_KEY = os.getenv("CLAUDE_API_KEY", "")
