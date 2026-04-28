from __future__ import annotations

import logging
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from stock_watch.paths import THEME_OUTDIR

THEME_RUN_DIR = Path(os.getenv("OUTDIR", str(THEME_OUTDIR)))
ALERT_TRACK_CSV = THEME_RUN_DIR / "alert_tracking.csv"
FEEDBACK_SUMMARY_CSV = THEME_RUN_DIR / "feedback_summary.csv"
LOCAL_TZ = ZoneInfo(os.getenv("LOCAL_TZ", "Asia/Taipei"))

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("theme_watchlist")
