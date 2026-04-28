from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd


def load_last_state(*, state_file: Path, state_enabled: bool) -> str:
    if not state_enabled or not state_file.exists():
        return ""
    return state_file.read_text(encoding="utf-8").strip()


def save_last_state(*, state_file: Path, state_enabled: bool, state: str) -> None:
    if state_enabled:
        state_file.write_text(state, encoding="utf-8")


def today_local_str(*, local_tz: ZoneInfo) -> str:
    return datetime.now(local_tz).strftime("%Y-%m-%d")


def load_last_success_date(*, success_file: Path) -> str:
    if not success_file.exists():
        return ""
    raw = success_file.read_text(encoding="utf-8").strip()
    if not raw:
        return ""
    try:
        data = json.loads(raw)
        return str(data.get("date", ""))
    except json.JSONDecodeError:
        return raw


def current_run_signature(paths: list[Path]) -> str:
    hasher = hashlib.sha256()
    for path in paths:
        hasher.update(str(path).encode("utf-8"))
        hasher.update(path.read_bytes())
    return hasher.hexdigest()[:16]


def load_last_success_signature(*, success_file: Path) -> str:
    if not success_file.exists():
        return ""
    raw = success_file.read_text(encoding="utf-8").strip()
    if not raw:
        return ""
    try:
        data = json.loads(raw)
        return str(data.get("signature", ""))
    except json.JSONDecodeError:
        return ""


def save_last_success_date(*, success_file: Path, success_date: str, signature: str) -> None:
    success_file.write_text(
        json.dumps({"date": success_date, "signature": signature}, ensure_ascii=False),
        encoding="utf-8",
    )


def build_rank_state(df_rank: pd.DataFrame, market_regime: dict) -> str:
    base_state = "|".join(
        f"{row.ticker}:{row.setup_score}:{row.risk_score}:{row.signals}:{row.rank}:{row.grade}"
        for row in df_rank.itertuples(index=False)
    )
    return f"market={market_regime.get('is_bullish', True)}||{base_state}"
