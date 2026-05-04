from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd


def _load_success_payload(success_file: Path) -> dict[str, object]:
    if not success_file.exists():
        return {}
    raw = success_file.read_text(encoding="utf-8").strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {"date": raw}
    return payload if isinstance(payload, dict) else {}


def _scoped_success_payload(payload: dict[str, object], scope: str | None) -> dict[str, object]:
    if not scope:
        return payload
    scopes = payload.get("scopes", {})
    if not isinstance(scopes, dict):
        return {}
    scoped = scopes.get(scope, {})
    return scoped if isinstance(scoped, dict) else {}


def load_last_state(*, state_file: Path, state_enabled: bool) -> str:
    if not state_enabled or not state_file.exists():
        return ""
    return state_file.read_text(encoding="utf-8").strip()


def save_last_state(*, state_file: Path, state_enabled: bool, state: str) -> None:
    if state_enabled:
        state_file.write_text(state, encoding="utf-8")


def today_local_str(*, local_tz: ZoneInfo) -> str:
    return datetime.now(local_tz).strftime("%Y-%m-%d")


def load_last_success_date(*, success_file: Path, success_scope: str | None = None) -> str:
    payload = _load_success_payload(success_file)
    if success_scope:
        return str(_scoped_success_payload(payload, success_scope).get("date", ""))
    return str(payload.get("date", ""))


def current_run_signature(paths: list[Path]) -> str:
    hasher = hashlib.sha256()
    for path in paths:
        hasher.update(str(path).encode("utf-8"))
        hasher.update(path.read_bytes())
    return hasher.hexdigest()[:16]


def load_last_success_signature(*, success_file: Path, success_scope: str | None = None) -> str:
    payload = _load_success_payload(success_file)
    if success_scope:
        return str(_scoped_success_payload(payload, success_scope).get("signature", ""))
    return str(payload.get("signature", ""))


def save_last_success_date(
    *,
    success_file: Path,
    success_date: str,
    signature: str,
    success_scope: str | None = None,
) -> None:
    payload = _load_success_payload(success_file)
    payload["date"] = success_date
    payload["signature"] = signature
    if success_scope:
        scopes = payload.get("scopes", {})
        if not isinstance(scopes, dict):
            scopes = {}
        scopes[success_scope] = {"date": success_date, "signature": signature}
        payload["scopes"] = scopes
    success_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def build_rank_state(df_rank: pd.DataFrame, market_regime: dict) -> str:
    base_state = "|".join(
        f"{row.ticker}:{row.setup_score}:{row.risk_score}:{row.signals}:{row.rank}:{row.grade}"
        for row in df_rank.itertuples(index=False)
    )
    return f"market={market_regime.get('is_bullish', True)}||{base_state}"
