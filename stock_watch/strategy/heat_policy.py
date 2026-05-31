from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


HOT_SCENARIOS = {"高檔震盪盤", "強勢延伸盤"}
CORRECTION_SCENARIOS = {"明顯修正盤", "盤中保守觀察"}


@dataclass(frozen=True)
class MarketHeatPolicy:
    state: str
    market_heat: str
    participation_bias: str
    open_not_chase_trial_cap: str
    open_not_chase_trial_rule: str
    observe_only_trial_cap: str
    observe_only_trial_rule: str
    entry_confirmation: str
    stop_rule: str
    allow_open_not_chase_trial: bool
    reason: str
    metrics: dict[str, float | int] = field(default_factory=dict)


def _numeric_series(df: pd.DataFrame, column: str, default: float = 0.0) -> pd.Series:
    if column not in df.columns:
        return pd.Series([default] * len(df), index=df.index, dtype=float)
    return pd.to_numeric(df[column], errors="coerce").fillna(default)


def _text_series(df: pd.DataFrame, column: str) -> pd.Series:
    if column not in df.columns:
        return pd.Series([""] * len(df), index=df.index, dtype=object)
    return df[column].fillna("").astype(str)


def build_heat_metrics(df_rank: pd.DataFrame | None, *, focus_n: int = 20) -> dict[str, float | int]:
    if df_rank is None or df_rank.empty:
        return {
            "focus_n": 0,
            "hot_ratio": 0.0,
            "high_spec_ratio": 0.0,
            "normal_momentum_count": 0,
            "extreme_ret5_ratio": 0.0,
        }

    work = df_rank.head(int(focus_n)).copy()
    if work.empty:
        return {
            "focus_n": 0,
            "hot_ratio": 0.0,
            "high_spec_ratio": 0.0,
            "normal_momentum_count": 0,
            "extreme_ret5_ratio": 0.0,
        }

    risk = _numeric_series(work, "risk_score")
    ret5 = _numeric_series(work, "ret5_pct")
    ret20 = _numeric_series(work, "ret20_pct")
    volume_ratio = _numeric_series(work, "volume_ratio20", 1.0)
    signals = _text_series(work, "signals")
    spec_label = _text_series(work, "spec_risk_label")
    volatility = _text_series(work, "volatility_tag")

    high_spec = spec_label.eq("疑似炒作風險高")
    watch_spec = spec_label.isin(["投機偏高", "偏熱", "留意"])
    hot_mask = risk.ge(5) | ret5.ge(15) | volatility.isin(["活潑", "劇烈"]) | high_spec
    normal_momentum = (
        ~high_spec
        & ~watch_spec
        & ret5.ge(8)
        & ret20.ge(0)
        & volume_ratio.ge(1.0)
        & signals.str.contains("TREND|ACCEL|REBREAK", regex=True)
    )
    focus_count = int(len(work))
    return {
        "focus_n": focus_count,
        "hot_ratio": round(float(hot_mask.mean()) if focus_count else 0.0, 4),
        "high_spec_ratio": round(float(high_spec.mean()) if focus_count else 0.0, 4),
        "normal_momentum_count": int(normal_momentum.sum()),
        "extreme_ret5_ratio": round(float(ret5.ge(25).mean()) if focus_count else 0.0, 4),
    }


def classify_heat_state(df_rank: pd.DataFrame | None, scenario: dict) -> tuple[str, dict[str, float | int]]:
    label = str(scenario.get("label", "") or "")
    metrics = build_heat_metrics(df_rank)
    hot_ratio = float(metrics.get("hot_ratio", 0.0) or 0.0)
    high_spec_ratio = float(metrics.get("high_spec_ratio", 0.0) or 0.0)
    normal_momentum_count = int(metrics.get("normal_momentum_count", 0) or 0)
    extreme_ret5_ratio = float(metrics.get("extreme_ret5_ratio", 0.0) or 0.0)

    if label in CORRECTION_SCENARIOS:
        return "correction", metrics
    if (
        label in HOT_SCENARIOS
        and hot_ratio >= 0.65
        and high_spec_ratio >= 0.45
        and (normal_momentum_count == 0 or extreme_ret5_ratio >= 0.35)
    ):
        return "blowoff", metrics
    if label in HOT_SCENARIOS and (hot_ratio >= 0.30 or normal_momentum_count >= 2):
        return "hot_trend", metrics
    if label == "強勢延伸盤" or hot_ratio >= 0.15 or normal_momentum_count >= 1:
        return "warm_trend", metrics
    return "normal", metrics


def build_market_heat_policy(df_rank: pd.DataFrame | None, scenario: dict) -> MarketHeatPolicy:
    state, metrics = classify_heat_state(df_rank, scenario)
    if state == "correction":
        return MarketHeatPolicy(
            state=state,
            market_heat="normal",
            participation_bias="防守優先",
            open_not_chase_trial_cap="0%",
            open_not_chase_trial_rule="修正盤不追開高，等重新站回再評估",
            observe_only_trial_cap="0%",
            observe_only_trial_rule="修正盤不做人工追價試單",
            entry_confirmation="只接受收盤重新站回或量價轉強後再看",
            stop_rule="跌破短線支撐先降風險",
            allow_open_not_chase_trial=False,
            reason="market scenario is defensive",
            metrics=metrics,
        )
    if state == "blowoff":
        return MarketHeatPolicy(
            state=state,
            market_heat="hot",
            participation_bias="降追價，只處理持股",
            open_not_chase_trial_cap="0%",
            open_not_chase_trial_rule="過熱疑似尾端，不開新試單",
            observe_only_trial_cap="<= 1/3 test position",
            observe_only_trial_rule="僅限人工點名；不得自動推播或自動升格",
            entry_confirmation="等待隔日降溫後仍守住支撐再看",
            stop_rule="有獲利先分批落袋，爆量不漲就降風險",
            allow_open_not_chase_trial=False,
            reason="heat is concentrated in high-risk or extreme movers",
            metrics=metrics,
        )
    if state == "hot_trend":
        return MarketHeatPolicy(
            state=state,
            market_heat="hot",
            participation_bias="小倉參與",
            open_not_chase_trial_cap="<= 1/4 test position",
            open_not_chase_trial_rule="強勢盤小倉參與；隔日不失守才試，收盤破前低或 1 ATR 出，不攤平",
            observe_only_trial_cap="<= 1/3 test position",
            observe_only_trial_rule="僅限人工點名；不得自動推播或自動升格",
            entry_confirmation="隔日不跌破前收，或盤中回測守 VWAP/5MA",
            stop_rule="收盤破前低或 1 ATR 出，不攤平",
            allow_open_not_chase_trial=True,
            reason="hot tape has enough normal-risk momentum to allow small participation",
            metrics=metrics,
        )
    if state == "warm_trend":
        return MarketHeatPolicy(
            state=state,
            market_heat="warm",
            participation_bias="照原規則，略偏參與",
            open_not_chase_trial_cap="0%",
            open_not_chase_trial_rule="先維持 shadow，不追開高",
            observe_only_trial_cap="<= 1/3 test position",
            observe_only_trial_rule="僅限人工點名；不得自動推播或自動升格",
            entry_confirmation="照原本等拉回或收盤確認",
            stop_rule="照原本停損與拉回規則",
            allow_open_not_chase_trial=False,
            reason="trend is constructive but not hot enough for open-not-chase trials",
            metrics=metrics,
        )
    return MarketHeatPolicy(
        state="normal",
        market_heat="normal",
        participation_bias="照原規則",
        open_not_chase_trial_cap="0%",
        open_not_chase_trial_rule="只做 shadow，不試單",
        observe_only_trial_cap="<= 1/3 test position",
        observe_only_trial_rule="僅限人工點名；不得自動推播或自動升格",
        entry_confirmation="照原本等拉回或收盤確認",
        stop_rule="照原本停損與拉回規則",
        allow_open_not_chase_trial=False,
        reason="market heat is not elevated enough to change participation",
        metrics=metrics,
    )


def policy_summary_lines(policy: MarketHeatPolicy) -> list[str]:
    return [
        f"- Heat state: `{policy.state}` / legacy heat `{policy.market_heat}`",
        f"- Participation: `{policy.participation_bias}`",
        f"- Open-not-chase trial: `{policy.open_not_chase_trial_cap}`",
        f"- Entry confirmation: {policy.entry_confirmation}",
        f"- Stop rule: {policy.stop_rule}",
    ]
