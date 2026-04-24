from __future__ import annotations

from typing import Optional

import pandas as pd

from stock_watch.signals.detect import grade_signal

RANK_SORT_COLUMNS = ["setup_score", "ret5_pct", "volume_ratio20", "ret20_pct", "risk_score"]
RANK_SORT_ASCENDING = [False, False, False, False, True]


def enrich_rank_changes(df_rank: pd.DataFrame, prev_rank: Optional[pd.DataFrame]) -> pd.DataFrame:
    df = df_rank.copy()
    df["rank_change"] = 0
    df["setup_change"] = 0
    df["risk_change"] = 0
    df["status_change"] = "NEW"

    if prev_rank is None or prev_rank.empty:
        return df

    prev = prev_rank.copy()
    prev["ticker"] = prev["ticker"].astype(str)
    prev = prev.set_index("ticker")

    for i, row in df.iterrows():
        ticker = str(row["ticker"])
        if ticker in prev.index:
            old = prev.loc[ticker]
            old_rank = int(old["rank"]) if pd.notna(old["rank"]) else 0
            old_setup = int(old["setup_score"]) if pd.notna(old["setup_score"]) else 0
            old_risk = int(old["risk_score"]) if pd.notna(old["risk_score"]) else 0
            df.at[i, "rank_change"] = old_rank - int(row["rank"])
            df.at[i, "setup_change"] = int(row["setup_score"]) - old_setup
            df.at[i, "risk_change"] = int(row["risk_score"]) - old_risk
            if df.at[i, "setup_change"] > 0 or df.at[i, "rank_change"] > 0:
                df.at[i, "status_change"] = "UP"
            elif df.at[i, "setup_change"] < 0 or df.at[i, "rank_change"] < 0:
                df.at[i, "status_change"] = "DOWN"
            else:
                df.at[i, "status_change"] = "FLAT"
    return df


def build_rank_table(rows: list[dict], prev_rank: Optional[pd.DataFrame]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["grade"] = df.apply(lambda r: grade_signal(r.to_dict()), axis=1)
    df = df.sort_values(
        by=RANK_SORT_COLUMNS,
        ascending=RANK_SORT_ASCENDING,
    ).reset_index(drop=True)
    df.insert(0, "rank", range(1, len(df) + 1))
    return enrich_rank_changes(df, prev_rank)
