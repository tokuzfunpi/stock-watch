from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

import pandas as pd


ALERT_TRACK_COLUMNS = [
    "alert_date",
    "watch_type",
    "ticker",
    "name",
    "group",
    "grade",
    "rank",
    "setup_score",
    "risk_score",
    "layer",
    "signals",
    "regime",
    "action_label",
    "feedback_score",
    "feedback_label",
    "scenario_label",
    "add_price",
    "trim_price",
    "stop_price",
    "alert_close",
    "ret1_future_pct",
    "ret5_future_pct",
    "ret20_future_pct",
    "status",
]


def upsert_alert_tracking(
    short_candidates: pd.DataFrame,
    midlong_candidates: pd.DataFrame,
    *,
    alert_track_csv: Path,
    market_scenario: Optional[dict],
    yf_period: str,
    feedback_action_label: Callable[[pd.Series, str], str],
    watch_price_plan: Callable[[pd.Series, str], dict],
    yf_download_one: Callable[[str, str], pd.DataFrame],
) -> None:
    if alert_track_csv.exists():
        try:
            hist = pd.read_csv(alert_track_csv)
        except Exception:
            hist = pd.DataFrame(columns=ALERT_TRACK_COLUMNS)
    else:
        hist = pd.DataFrame(columns=ALERT_TRACK_COLUMNS)

    candidate_groups = [
        ("short", short_candidates),
        ("midlong", midlong_candidates),
    ]
    scenario_label = str((market_scenario or {}).get("label", "") or "")

    for watch_type, candidates in candidate_groups:
        if candidates is None or candidates.empty:
            continue
        for _, row in candidates.iterrows():
            alert_date = str(row["date"])
            mask = (
                (hist.get("alert_date", pd.Series(dtype=str)).astype(str) == alert_date)
                & (hist.get("watch_type", pd.Series(dtype=str)).astype(str) == watch_type)
                & (hist.get("ticker", pd.Series(dtype=str)).astype(str) == str(row["ticker"]))
            )
            price_plan = watch_price_plan(row, watch_type)
            payload = {
                "alert_date": alert_date,
                "watch_type": watch_type,
                "ticker": row["ticker"],
                "name": row["name"],
                "group": row["group"],
                "layer": row.get("layer", ""),
                "grade": row["grade"],
                "rank": int(row["rank"]),
                "setup_score": int(row["setup_score"]),
                "risk_score": int(row["risk_score"]),
                "signals": row["signals"],
                "regime": row["regime"],
                "action_label": feedback_action_label(row, watch_type),
                "feedback_score": float(row.get("feedback_score", 0.0)),
                "feedback_label": str(row.get("feedback_label", "樣本不足")),
                "scenario_label": scenario_label,
                "add_price": price_plan.get("add_price"),
                "trim_price": price_plan.get("trim_price"),
                "stop_price": price_plan.get("stop_price"),
                "alert_close": float(row["close"]),
                "ret1_future_pct": None,
                "ret5_future_pct": None,
                "ret20_future_pct": None,
                "status": "OPEN",
            }
            if mask.any():
                hist.loc[mask, list(payload.keys())] = list(payload.values())
            else:
                hist.loc[len(hist), list(payload.keys())] = list(payload.values())

    if not hist.empty:
        open_rows = hist[hist.get("status", pd.Series(dtype=str)).astype(str) != "CLOSED"]
        if not open_rows.empty:
            for ticker, ticker_rows in open_rows.groupby(hist.get("ticker", pd.Series(dtype=str)).astype(str)):
                try:
                    df = yf_download_one(str(ticker), yf_period)
                except Exception:
                    continue
                if df.empty:
                    continue

                closes = df["Close"].reset_index(drop=True)
                date_to_idx = {dt: idx for idx, dt in enumerate(df.index.strftime("%Y-%m-%d"))}

                for row_idx, row in ticker_rows.iterrows():
                    idx = date_to_idx.get(str(row["alert_date"]))
                    if idx is None:
                        continue
                    entry = float(closes.iloc[idx])

                    if pd.isna(row.get("ret1_future_pct")) and idx + 1 < len(closes):
                        hist.at[row_idx, "ret1_future_pct"] = round((float(closes.iloc[idx + 1]) / entry - 1.0) * 100, 2)
                    if pd.isna(row.get("ret5_future_pct")) and idx + 5 < len(closes):
                        hist.at[row_idx, "ret5_future_pct"] = round((float(closes.iloc[idx + 5]) / entry - 1.0) * 100, 2)
                    if pd.isna(row.get("ret20_future_pct")) and idx + 20 < len(closes):
                        hist.at[row_idx, "ret20_future_pct"] = round((float(closes.iloc[idx + 20]) / entry - 1.0) * 100, 2)
                        hist.at[row_idx, "status"] = "CLOSED"

    hist.to_csv(alert_track_csv, index=False, encoding="utf-8-sig")
