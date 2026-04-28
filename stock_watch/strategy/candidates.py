from __future__ import annotations

from collections.abc import Callable

import pandas as pd

from stock_watch.strategy import scenario as strategy_scenario


FeedbackAdjuster = Callable[[pd.DataFrame, str], pd.DataFrame]


def _identity_feedback_adjustment(df: pd.DataFrame, watch_type: str) -> pd.DataFrame:
    return df


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


def heat_bias_message(df_rank: pd.DataFrame | None, scenario: dict) -> str:
    if df_rank is None or df_rank.empty:
        return ""
    working = df_rank.head(10).copy()
    if working.empty:
        return ""
    for col in ["risk_score", "ret5_pct"]:
        if col in working.columns:
            working[col] = pd.to_numeric(working[col], errors="coerce")
    hot_mask = (
        working.get("risk_score", pd.Series(dtype=float)).fillna(0).ge(5)
        | working.get("ret5_pct", pd.Series(dtype=float)).fillna(0).ge(12)
        | working.get("volatility_tag", pd.Series(dtype=str)).astype(str).isin(["活潑", "劇烈"])
        | working.get("spec_risk_label", pd.Series(dtype=str)).astype(str).eq("疑似炒作風險高")
    )
    hot_ratio = float(hot_mask.mean()) if len(working) else 0.0
    label = str(scenario.get("label", "") or "")
    if hot_ratio >= 0.5:
        return "⚠️ Heat Bias 偏強：前排標的偏熱，最近績效可能有行情抬轎，追價風險高。"
    if hot_ratio >= 0.3 and label in {"高檔震盪盤", "強勢延伸盤"}:
        return "⚠️ Heat Bias 提醒：前排標的已有明顯熱度，請把拉回買點與分批落袋看得比平常更重。"
    return ""


def effective_short_top_n(
    df_rank: pd.DataFrame,
    *,
    top_n_short: int,
    correction_short_top_n: int,
    heat_bias_short_top_n: int,
    market_regime: dict | None = None,
    us_market: dict | None = None,
) -> int:
    top_n = int(top_n_short)
    if market_regime is None or us_market is None or df_rank.empty:
        return top_n

    scenario = strategy_scenario.build_market_scenario(market_regime, us_market, df_rank)
    if str(scenario.get("label", "") or "") in {"明顯修正盤", "盤中保守觀察"}:
        return min(top_n, int(correction_short_top_n))

    heat_bias = heat_bias_message(df_rank, scenario)
    if "Heat Bias 偏強" in heat_bias:
        return min(top_n, int(heat_bias_short_top_n))
    return top_n


def effective_midlong_top_n(
    df_rank: pd.DataFrame,
    *,
    top_n_midlong: int,
    correction_midlong_top_n: int,
    market_regime: dict | None = None,
    us_market: dict | None = None,
) -> int:
    top_n = int(top_n_midlong)
    if market_regime is None or us_market is None or df_rank.empty:
        return top_n

    scenario = strategy_scenario.build_market_scenario(market_regime, us_market, df_rank)
    if str(scenario.get("label", "") or "") in {"明顯修正盤", "盤中保守觀察"}:
        return min(top_n, int(correction_midlong_top_n))
    return top_n


def select_short_term_candidates(
    df_rank: pd.DataFrame,
    *,
    priority_groups: list[str] | tuple[str, ...] = (),
    top_n_short: int,
    correction_short_top_n: int,
    heat_bias_short_top_n: int,
    market_regime: dict | None = None,
    us_market: dict | None = None,
    feedback_adjuster: FeedbackAdjuster = _identity_feedback_adjustment,
) -> pd.DataFrame:
    df = rank_short_term_pool(df_rank, priority_groups)
    if df.empty:
        return df
    buyable_mask = df.apply(is_short_term_buyable, axis=1)
    candidate_limit = effective_short_top_n(
        df_rank,
        top_n_short=top_n_short,
        correction_short_top_n=correction_short_top_n,
        heat_bias_short_top_n=heat_bias_short_top_n,
        market_regime=market_regime,
        us_market=us_market,
    )
    return feedback_adjuster(df[buyable_mask].copy(), "short").head(candidate_limit).copy()


def select_short_term_backup_candidates(
    df_rank: pd.DataFrame,
    *,
    priority_groups: list[str] | tuple[str, ...] = (),
    exclude_tickers: set[str] | None = None,
    feedback_adjuster: FeedbackAdjuster = _identity_feedback_adjustment,
) -> pd.DataFrame:
    df = rank_short_term_pool(df_rank, priority_groups)
    if not df.empty:
        buyable_mask = df.apply(is_short_term_buyable, axis=1)
        df = df[~buyable_mask].copy()
    if exclude_tickers:
        df = df[~df["ticker"].astype(str).isin(exclude_tickers)].copy()
    return feedback_adjuster(df.copy(), "short").head(5).copy()


def select_midlong_candidates(
    df_rank: pd.DataFrame,
    *,
    priority_groups: list[str] | tuple[str, ...] = (),
    top_n_midlong: int,
    correction_midlong_top_n: int,
    market_regime: dict | None = None,
    us_market: dict | None = None,
    exclude_tickers: set[str] | None = None,
    feedback_adjuster: FeedbackAdjuster = _identity_feedback_adjustment,
) -> pd.DataFrame:
    df = rank_midlong_pool(df_rank, priority_groups)
    if not df.empty:
        buyable_mask = df.apply(is_midlong_buyable, axis=1)
        df = df[buyable_mask].copy()
    if exclude_tickers:
        df = df[~df["ticker"].astype(str).isin(exclude_tickers)].copy()
    candidate_limit = effective_midlong_top_n(
        df_rank,
        top_n_midlong=top_n_midlong,
        correction_midlong_top_n=correction_midlong_top_n,
        market_regime=market_regime,
        us_market=us_market,
    )
    return feedback_adjuster(df.copy(), "midlong").head(candidate_limit).copy()


def select_midlong_backup_candidates(
    df_rank: pd.DataFrame,
    *,
    priority_groups: list[str] | tuple[str, ...] = (),
    exclude_tickers: set[str] | None = None,
    feedback_adjuster: FeedbackAdjuster = _identity_feedback_adjustment,
) -> pd.DataFrame:
    df = rank_midlong_pool(df_rank, priority_groups)
    if not df.empty:
        buyable_mask = df.apply(is_midlong_buyable, axis=1)
        df = df[~buyable_mask].copy()
    if exclude_tickers:
        df = df[~df["ticker"].astype(str).isin(exclude_tickers)].copy()
    return feedback_adjuster(df.copy(), "midlong").head(5).copy()


def select_push_candidates(
    df_rank: pd.DataFrame,
    *,
    priority_groups: list[str] | tuple[str, ...] = (),
    top_n_short: int,
    top_n_midlong: int,
    correction_short_top_n: int,
    heat_bias_short_top_n: int,
    correction_midlong_top_n: int,
    market_regime: dict | None = None,
    us_market: dict | None = None,
    feedback_adjuster: FeedbackAdjuster = _identity_feedback_adjustment,
) -> pd.DataFrame:
    short_candidates = select_short_term_candidates(
        df_rank,
        priority_groups=priority_groups,
        top_n_short=top_n_short,
        correction_short_top_n=correction_short_top_n,
        heat_bias_short_top_n=heat_bias_short_top_n,
        market_regime=market_regime,
        us_market=us_market,
        feedback_adjuster=feedback_adjuster,
    )
    midlong_candidates = select_midlong_candidates(
        df_rank,
        priority_groups=priority_groups,
        top_n_midlong=top_n_midlong,
        correction_midlong_top_n=correction_midlong_top_n,
        market_regime=market_regime,
        us_market=us_market,
        feedback_adjuster=feedback_adjuster,
    )
    return pd.concat([short_candidates, midlong_candidates], ignore_index=True)


def build_candidate_sets(
    df_rank: pd.DataFrame,
    *,
    priority_groups: list[str] | tuple[str, ...] = (),
    top_n_short: int,
    top_n_midlong: int,
    correction_short_top_n: int,
    heat_bias_short_top_n: int,
    correction_midlong_top_n: int,
    market_regime: dict | None = None,
    us_market: dict | None = None,
    feedback_adjuster: FeedbackAdjuster = _identity_feedback_adjustment,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    short_candidates = select_short_term_candidates(
        df_rank,
        priority_groups=priority_groups,
        top_n_short=top_n_short,
        correction_short_top_n=correction_short_top_n,
        heat_bias_short_top_n=heat_bias_short_top_n,
        market_regime=market_regime,
        us_market=us_market,
        feedback_adjuster=feedback_adjuster,
    )
    short_backups = select_short_term_backup_candidates(
        df_rank,
        priority_groups=priority_groups,
        exclude_tickers=set(short_candidates["ticker"].astype(str)),
        feedback_adjuster=feedback_adjuster,
    )
    midlong_candidates = select_midlong_candidates(
        df_rank,
        priority_groups=priority_groups,
        top_n_midlong=top_n_midlong,
        correction_midlong_top_n=correction_midlong_top_n,
        market_regime=market_regime,
        us_market=us_market,
        feedback_adjuster=feedback_adjuster,
    )
    midlong_backups = select_midlong_backup_candidates(
        df_rank,
        priority_groups=priority_groups,
        exclude_tickers=set(midlong_candidates["ticker"].astype(str)),
        feedback_adjuster=feedback_adjuster,
    )
    return short_candidates, short_backups, midlong_candidates, midlong_backups
