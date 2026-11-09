"""
Configuration settings for Bale Helm Telegram Bot.
"""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

# Load environment variables
load_dotenv()

BOT_TOKEN: str = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID_RAW: str = os.getenv("ADMIN_ID", "").strip()

try:
    ADMIN_ID: int = int(ADMIN_ID_RAW) if ADMIN_ID_RAW else 0
except ValueError:
    ADMIN_ID = 0

CHANNEL_URL: str = "https://t.me/bale_helm"
CHANNEL_HANDLE: str = "@bale_helm"

# Photo Campaign Limits
MAX_PHOTOS_PER_USER: int = 5

# Database path
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "bot.db"

# Configure logging (Never expose BOT_TOKEN in logs)
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("bale_helm_bot")
