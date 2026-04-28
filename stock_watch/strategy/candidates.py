from __future__ import annotations

import pandas as pd


def reorder_priority_groups(df_rank: pd.DataFrame, priority_groups: list[str] | tuple[str, ...]) -> pd.DataFrame:
    df = df_rank.copy()
    if priority_groups:
        pri = df[df["group"].isin(priority_groups)].copy()
        non = df[~df["group"].isin(priority_groups)].copy()
        df = pd.concat([pri, non], ignore_index=True)
    return df


def _apply_grade_rank(df: pd.DataFrame) -> pd.Series:
    rank_map = {"A": 3, "B": 2, "X": 1, "C": 0}
    return df["grade"].map(rank_map).fillna(0)


def _signal_strength(df: pd.DataFrame, patterns: str) -> pd.Series:
    return df["signals"].fillna("").str.contains(patterns).astype(int)


def rank_short_term_pool(df_rank: pd.DataFrame, priority_groups: list[str] | tuple[str, ...] = ()) -> pd.DataFrame:
    df = reorder_priority_groups(df_rank, priority_groups)
    if "layer" in df.columns:
        df = df[df["layer"].isin(["short_attack", "midlong_core"])].copy()

    df = df[
        (df["setup_score"] >= 4)
        & (df["risk_score"] <= 6)
        & (df["ret20_pct"] >= -5)
    ].copy()
    if df.empty:
        return df

    df["_grade_rank"] = _apply_grade_rank(df)
    df["_signal_rank"] = _signal_strength(df, "ACCEL|TREND|REBREAK")
    df = df.sort_values(
        by=[
            "_grade_rank",
            "_signal_rank",
            "setup_score",
            "ret5_pct",
            "volume_ratio20",
            "setup_change",
            "rank_change",
            "risk_score",
            "rank",
        ],
        ascending=[False, False, False, False, False, False, False, True, True],
    ).reset_index(drop=True)
    return df.drop(columns=["_grade_rank", "_signal_rank"])


def rank_midlong_pool(df_rank: pd.DataFrame, priority_groups: list[str] | tuple[str, ...] = ()) -> pd.DataFrame:
    df = reorder_priority_groups(df_rank, priority_groups)
    if "layer" in df.columns:
        df = df[df["layer"].isin(["midlong_core", "defensive_watch"])].copy()

    df = df[
        (df["setup_score"] >= 4)
        & (df["risk_score"] <= 6)
        & (df["ret20_pct"] >= -5)
    ].copy()
    if df.empty:
        return df

    df["_grade_rank"] = _apply_grade_rank(df)
    df["_signal_rank"] = _signal_strength(df, "TREND|REBREAK|BASE")
    df = df.sort_values(
        by=[
            "_grade_rank",
            "_signal_rank",
            "setup_score",
            "ret20_pct",
            "rank_change",
            "setup_change",
            "risk_score",
            "rank",
        ],
        ascending=[False, False, False, False, False, False, True, True],
    ).reset_index(drop=True)
    return df.drop(columns=["_grade_rank", "_signal_rank"])


def short_term_action_label(row: pd.Series) -> str:
    risk = int(row["risk_score"])
    ret5 = float(row["ret5_pct"])
    vol_ratio = float(row["volume_ratio20"])
    signals = str(row["signals"])
    spec_label = str(row.get("spec_risk_label", "正常"))

    if spec_label == "疑似炒作風險高":
        return "只觀察不追"
    if risk >= 5 or ret5 >= 25:
        return "分批落袋"
    if ret5 >= 15 or (risk >= 4 and ret5 >= 10):
        return "開高不追"
    if ("ACCEL" in signals and "TREND" in signals) and risk <= 3 and vol_ratio >= 1.0 and float(row.get("ret20_pct", 0.0) or 0.0) >= 0 and ret5 >= 4:
        return "等拉回"
    if ret5 >= 8:
        return "等拉回"
    if row["setup_change"] > 0 or row["rank_change"] > 0:
        return "續抱觀察"
    return "續追蹤"


def is_short_term_buyable(row: pd.Series) -> bool:
    return short_term_action_label(row) == "等拉回"


def midlong_action_label(row: pd.Series) -> str:
    risk = int(row["risk_score"])
    ret20 = float(row["ret20_pct"])
    signals = str(row["signals"])
    spec_label = str(row.get("spec_risk_label", "正常"))

    if spec_label == "疑似炒作風險高":
        return "減碼觀察"
    if risk >= 5 or ret20 >= 25:
        return "分批落袋"
    if "TREND" in signals or "REBREAK" in signals:
        return "續抱"
    if row["setup_change"] > 0 or row["rank_change"] > 0:
        return "可分批"
    return "觀察"


def is_midlong_buyable(row: pd.Series) -> bool:
    return midlong_action_label(row) in {"續抱", "可分批"}
