"""Data-quality gate for price history.

The scan currently relies on a single price source (Yahoo) and scores whatever
frame comes back. This module provides a cheap, dependency-free validation step
to run *before* scoring so we can skip or flag tickers with insufficient or
stale data instead of producing misleading scores.

Pure functions only — no network, no global state — so it is easy to test and
to wire into either the live scan or the backtest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

import pandas as pd

REQUIRED_OHLCV_COLUMNS = ("Open", "High", "Low", "Close", "Volume")


@dataclass(frozen=True)
class DataQualityReport:
    ok: bool
    rows: int
    last_date: Optional[str]
    staleness_days: Optional[int]
    issues: tuple[str, ...] = field(default=())

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "rows": self.rows,
            "last_date": self.last_date,
            "staleness_days": self.staleness_days,
            "issues": ",".join(self.issues),
        }


def _to_date(value) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return pd.Timestamp(value).date()
    except (ValueError, TypeError):
        return None


def check_price_history(
    df: Optional[pd.DataFrame],
    *,
    min_rows: int = 60,
    max_staleness_days: int = 7,
    as_of: Optional[date] = None,
    nan_tail_window: int = 5,
) -> DataQualityReport:
    """Validate a daily OHLCV frame.

    Checks performed:
      - frame exists and is non-empty
      - required OHLCV columns present
      - at least ``min_rows`` rows of history
      - the most recent ``nan_tail_window`` Close values are not NaN
      - non-positive Close values are flagged
      - data is not stale beyond ``max_staleness_days`` relative to ``as_of``
        (skipped if the index is not date-like or ``as_of`` is None)
    """
    issues: list[str] = []

    if df is None or len(df) == 0:
        return DataQualityReport(
            ok=False, rows=0, last_date=None, staleness_days=None, issues=("empty",)
        )

    rows = int(len(df))

    missing = [c for c in REQUIRED_OHLCV_COLUMNS if c not in df.columns]
    if missing:
        issues.append("missing_columns:" + "|".join(missing))

    if rows < min_rows:
        issues.append(f"insufficient_history:{rows}<{min_rows}")

    last_date: Optional[str] = None
    staleness_days: Optional[int] = None

    last_dt = _to_date(df.index[-1]) if rows else None
    if last_dt is not None:
        last_date = last_dt.isoformat()
        reference = as_of or date.today()
        staleness_days = (reference - last_dt).days
        if staleness_days > max_staleness_days:
            issues.append(f"stale:{staleness_days}d>{max_staleness_days}d")

    if "Close" in df.columns and rows:
        tail = df["Close"].tail(max(1, nan_tail_window))
        if tail.isna().any():
            issues.append("nan_recent_close")
        valid_close = df["Close"].dropna()
        if not valid_close.empty and (valid_close <= 0).any():
            issues.append("non_positive_close")

    ok = not issues
    return DataQualityReport(
        ok=ok,
        rows=rows,
        last_date=last_date,
        staleness_days=staleness_days,
        issues=tuple(issues),
    )
