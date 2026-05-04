from __future__ import annotations

import os
import re
from pathlib import Path

from stock_watch.paths import REPO_ROOT

GETUPDATES_URL_PATH = Path(os.getenv("GETUPDATES_URL_PATH", REPO_ROOT / "telegram_getupdates_url"))
_BOT_TOKEN_RE = re.compile(r"/bot([^/]+)/getUpdates(?:\?|$)")


def _extract_token_from_getupdates_url(raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    match = _BOT_TOKEN_RE.search(text)
    if not match:
        return ""
    return str(match.group(1) or "").strip()


def resolve_telegram_token(*, getupdates_url_path: Path | None = None) -> tuple[str, str]:
    env_token = os.getenv("TELEGRAM_TOKEN", "").strip()
    if env_token:
        return env_token, "env:TELEGRAM_TOKEN"

    env_getupdates_url = os.getenv("TELEGRAM_GETUPDATES_URL", "").strip()
    env_getupdates_token = _extract_token_from_getupdates_url(env_getupdates_url)
    if env_getupdates_token:
        return env_getupdates_token, "env:TELEGRAM_GETUPDATES_URL"

    getupdates_url_path = getupdates_url_path or GETUPDATES_URL_PATH
    if getupdates_url_path.exists():
        try:
            file_token = _extract_token_from_getupdates_url(getupdates_url_path.read_text(encoding="utf-8-sig"))
        except Exception:
            file_token = ""
        if file_token:
            return file_token, str(getupdates_url_path)

    return "", ""
