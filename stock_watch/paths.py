from __future__ import annotations

import os
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _env_path(name: str, default: Path) -> Path:
    raw = os.getenv(name, "").strip()
    return Path(raw).expanduser() if raw else default


THEME_OUTDIR = _env_path("STOCK_WATCH_THEME_OUTDIR", REPO_ROOT / "runs" / "theme_watchlist_daily")
VERIFICATION_OUTDIR = _env_path("STOCK_WATCH_VERIFICATION_OUTDIR", REPO_ROOT / "runs" / "verification" / "watchlist_daily")
SITE_OUTDIR = _env_path("STOCK_WATCH_SITE_OUTDIR", THEME_OUTDIR / "local_site")
