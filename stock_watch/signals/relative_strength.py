"""Relative strength of a stock versus a benchmark index (default: ^TWII).

Relative strength is highly relevant for Taiwan stock selection: a name that is
merely rising with the whole market is weaker than one outperforming it. This
module is intentionally a set of pure functions operating on price series so it
can be unit-tested without any network access and reused by both the live
scan and the backtest.

Key outputs:
  - RS ratio line  = stock_close / benchmark_close (normalized)
  - RS momentum     = percentage change of the RS ratio over a lookback window
  - excess return   = stock return minus benchmark return over a window
  - RS rating (1-99): percentile rank of the trailing relative return, in the
    spirit of the well-known IBD-style RS rating.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd


@dataclass(frozen=True)
class RelativeStrengthResult:
    rs_ratio: Optional[float]
    rs_momentum_pct: Optional[float]
    excess_return_pct: Optional[float]
    outperforming: Optional[bool]

    def as_dict(self) -> dict:
        return {
            "rs_ratio": self.rs_ratio,
            "rs_momentum_pct": self.rs_momentum_pct,
            "excess_return_pct": self.excess_return_pct,
            "rs_outperforming": self.outperforming,
        }


def _align(stock_close: pd.Series, benchmark_close: pd.Series) -> tuple[pd.Series, pd.Series]:
    joined = pd.concat(
        [stock_close.rename("stock"), benchmark_close.rename("bench")], axis=1
    ).dropna()
    return joined["stock"], joined["bench"]


def rs_ratio_series(stock_close: pd.Series, benchmark_close: pd.Series) -> pd.Series:
    """RS ratio line, rebased to 1.0 at the first common date."""
    stock, bench = _align(stock_close, benchmark_close)
    if stock.empty or float(bench.iloc[0]) == 0.0:
        return pd.Series(dtype=float)
    ratio = stock / bench
    base = float(ratio.iloc[0])
    if base == 0.0:
        return pd.Series(dtype=float)
    return ratio / base


def compute_relative_strength(
    stock_close: pd.Series,
    benchmark_close: pd.Series,
    *,
    lookback: int = 20,
) -> RelativeStrengthResult:
    """Compute relative-strength metrics over the trailing ``lookback`` window."""
    stock, bench = _align(stock_close, benchmark_close)
    if len(stock) <= lookback:
        return RelativeStrengthResult(None, None, None, None)

    ratio = rs_ratio_series(stock, bench)
    if ratio.empty:
        return RelativeStrengthResult(None, None, None, None)

    rs_now = float(ratio.iloc[-1])
    rs_past = float(ratio.iloc[-1 - lookback])
    rs_momentum_pct = round((rs_now / rs_past - 1.0) * 100, 2) if rs_past != 0 else None

    stock_now = float(stock.iloc[-1])
    stock_past = float(stock.iloc[-1 - lookback])
    bench_now = float(bench.iloc[-1])
    bench_past = float(bench.iloc[-1 - lookback])
    stock_ret = (stock_now / stock_past - 1.0) if stock_past != 0 else 0.0
    bench_ret = (bench_now / bench_past - 1.0) if bench_past != 0 else 0.0
    excess_return_pct = round((stock_ret - bench_ret) * 100, 2)

    return RelativeStrengthResult(
        rs_ratio=round(rs_now, 4),
        rs_momentum_pct=rs_momentum_pct,
        excess_return_pct=excess_return_pct,
        outperforming=bool(stock_ret > bench_ret),
    )


def rs_rating(
    excess_returns: pd.Series,
    value: float,
) -> int:
    """Percentile rank (1-99) of ``value`` within a cross-section of excess
    returns. Higher means stronger relative performance.

    ``excess_returns`` is the distribution across the watchlist for a given day;
    ``value`` is the candidate's excess return. Empty/degenerate input yields 50.
    """
    series = pd.Series(excess_returns).dropna()
    if series.empty:
        return 50
    pct = float((series <= value).mean()) * 100.0
    return int(max(1, min(99, round(pct))))
