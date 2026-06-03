"""Factor tear sheet: do the scores actually predict forward returns?

The backtest records, for each scanned event, the candidate's ``setup_score`` /
``risk_score`` / ``spec_risk_score`` alongside realized forward returns
(``ret_1d``, ``ret_5d``, ``ret_20d`` ...). This module groups those events by
score bucket and summarizes the forward-return distribution per bucket, in the
spirit of an alphalens factor tear sheet.

If a factor has predictive power, average forward returns should increase
monotonically with ``setup_score`` (and decrease with ``risk_score``). The
output makes that easy to eyeball and to test over time.

Pure pandas; no network, no global state.
"""

from __future__ import annotations

from typing import Iterable, Optional

import pandas as pd


def _quantile_buckets(series: pd.Series, n_buckets: int) -> pd.Series:
    """Assign each value to a quantile bucket label like ``Q1`` (low) .. ``Qn``.

    Falls back gracefully when there are fewer distinct values than buckets.
    """
    clean = series.dropna()
    if clean.empty:
        return pd.Series(index=series.index, dtype="object")
    try:
        codes = pd.qcut(clean, q=n_buckets, labels=False, duplicates="drop")
    except (ValueError, IndexError):
        return pd.Series(index=series.index, dtype="object")
    labels = codes.map(lambda c: f"Q{int(c) + 1}" if pd.notna(c) else None)
    return labels.reindex(series.index)


def factor_tear_sheet(
    events_df: pd.DataFrame,
    *,
    factor_col: str = "setup_score",
    horizons: Iterable[int] = (1, 5, 20),
    n_buckets: int = 5,
    use_quantiles: bool = False,
) -> pd.DataFrame:
    """Summarize forward returns grouped by factor value (or quantile bucket).

    Returns one row per (bucket, horizon) with count, win rate, mean / median
    forward return. An empty DataFrame is returned when the input lacks the
    factor column or any usable horizon columns.
    """
    if events_df is None or events_df.empty or factor_col not in events_df.columns:
        return pd.DataFrame()

    df = events_df.copy()
    if use_quantiles:
        df["_bucket"] = _quantile_buckets(df[factor_col], n_buckets)
        bucket_name = f"{factor_col}_quantile"
    else:
        df["_bucket"] = df[factor_col]
        bucket_name = factor_col

    rows = []
    for horizon in horizons:
        col = f"ret_{horizon}d"
        if col not in df.columns:
            continue
        for bucket_value, group in df.groupby("_bucket", dropna=True):
            series = group[col].dropna()
            if series.empty:
                continue
            rows.append(
                {
                    "factor": bucket_name,
                    "bucket": bucket_value,
                    "horizon": horizon,
                    "trades": int(series.shape[0]),
                    "win_rate_pct": round(float(series.gt(0).mean()) * 100, 2),
                    "avg_return_pct": round(float(series.mean()), 2),
                    "median_return_pct": round(float(series.median()), 2),
                }
            )

    result = pd.DataFrame(rows)
    if not result.empty:
        result = result.sort_values(["horizon", "bucket"]).reset_index(drop=True)
    return result


def monotonicity_score(
    tear_sheet_df: pd.DataFrame,
    *,
    horizon: int,
    higher_is_better: bool = True,
) -> Optional[float]:
    """Spearman-like rank correlation between bucket order and avg forward
    return for a single horizon. +1 means perfectly monotonic in the expected
    direction; near 0 means the factor carries little ordering information.

    Returns None when there are not enough buckets to assess.
    """
    if tear_sheet_df is None or tear_sheet_df.empty:
        return None
    sub = tear_sheet_df[tear_sheet_df["horizon"] == horizon].copy()
    if len(sub) < 2:
        return None

    # Order buckets; quantile labels (Q1..Qn) and numeric scores both sort fine.
    sub = sub.sort_values("bucket")
    bucket_rank = pd.Series(range(len(sub)), index=sub.index, dtype=float)
    avg = sub["avg_return_pct"].astype(float)
    if avg.nunique() < 2:
        return 0.0
    corr = bucket_rank.corr(avg.rank())
    if corr is None or pd.isna(corr):
        return 0.0
    return round(float(corr if higher_is_better else -corr), 3)
