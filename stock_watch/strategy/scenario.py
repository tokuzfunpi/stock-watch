from __future__ import annotations

from dataclasses import replace
from typing import TypeVar

import pandas as pd

StrategyT = TypeVar("StrategyT")


def build_market_scenario(market_regime: dict, us_market: dict, df_rank: pd.DataFrame | None = None) -> dict:
    ret20_pct = float(market_regime.get("ret20_pct", 0.0) or 0.0)
    volume_ratio20 = float(market_regime.get("volume_ratio20", 1.0) or 1.0)
    is_bullish = bool(market_regime.get("is_bullish", False))
    session_phase = str(market_regime.get("session_phase", "postclose") or "postclose")
    us_summary = str(us_market.get("summary", ""))
    us_weak = ("偏弱" in us_summary) or ("續殺" in us_summary)

    hot_count = 0
    strong_count = 0
    candidate_count = 0
    if df_rank is not None and not df_rank.empty:
        working = df_rank.copy()
        for col in ["risk_score", "ret5_pct", "ret20_pct", "volume_ratio20", "setup_score"]:
            if col in working.columns:
                working[col] = pd.to_numeric(working[col], errors="coerce")
        candidate_count = int(min(len(working), 20))
        focus = working.head(candidate_count).copy()
        if not focus.empty:
            hot_mask = (
                (focus.get("risk_score", pd.Series(dtype=float)).fillna(0) >= 5)
                | (focus.get("ret5_pct", pd.Series(dtype=float)).fillna(0) >= 15)
            )
            strong_mask = (
                (focus.get("setup_score", pd.Series(dtype=float)).fillna(0) >= 6)
                & (focus.get("risk_score", pd.Series(dtype=float)).fillna(9) <= 3)
                & (focus.get("ret20_pct", pd.Series(dtype=float)).fillna(-99) >= 0)
            )
            hot_count = int(hot_mask.sum())
            strong_count = int(strong_mask.sum())

    hot_ratio = (hot_count / candidate_count) if candidate_count > 0 else 0.0
    strong_ratio = (strong_count / candidate_count) if candidate_count > 0 else 0.0

    correction_condition = (not is_bullish) or (ret20_pct <= 3 and us_weak)
    if correction_condition and session_phase != "postclose":
        return {
            "label": "盤中保守觀察",
            "stance": "先保守，等收盤定案",
            "focus": "盤中先縮手，避免被即時波動或大盤欄位異常誤導；等收盤後再確認是否真轉修正盤。",
            "exit_note": "盤中若持股轉弱先減碼，但不要只因盤中噪音就全面翻空。",
        }

    if correction_condition:
        return {
            "label": "明顯修正盤",
            "stance": "先保守",
            "focus": "短線先縮手，中線也以守部位、等重新站回為主。",
            "exit_note": "若持股跌破短線支撐或反彈無量，優先減碼，不用硬等。",
        }

    if is_bullish and ret20_pct >= 12 and strong_ratio < 0.35:
        return {
            "label": "權值撐盤、個股轉弱",
            "stance": "選股更重要",
            "focus": "指數可能還不差，但不是每一檔都好做，先看個股延續性。",
            "exit_note": "若持股不跟漲、開高走低或量縮轉弱，要比大盤更早處理。",
        }

    if is_bullish and (ret20_pct >= 10 or volume_ratio20 >= 1.2) and (hot_ratio >= 0.3 or us_weak):
        return {
            "label": "高檔震盪盤",
            "stance": "邊做邊收",
            "focus": "行情還熱，但追價風險明顯變高，進場要更挑買點。",
            "exit_note": "若隔日不續強、出現長上影或爆量不漲，就先分批落袋。",
        }

    return {
        "label": "強勢延伸盤",
        "stance": "順勢但不追價",
        "focus": "主流趨勢仍在，但仍以等拉回取代追高。",
        "exit_note": "有獲利的部位可採分批落袋，避免只看進場不管出場。",
    }


def adjust_strategy_by_scenario(base_strat: StrategyT, scenario: dict) -> StrategyT:
    strat = replace(base_strat)
    label = str(scenario.get("label", "") or "")

    if label in {"明顯修正盤", "盤中保守觀察"}:
        strat.rebreak_vol_ratio += 0.10
        strat.trend_ret20 += 0.01
        strat.accel_ret5 += 0.01
        strat.accel_ret10 += 0.02
        strat.accel_vol_ratio_fast += 0.15
        strat.accel_vol_ratio_slow += 0.10
    elif label == "權值撐盤、個股轉弱":
        strat.rebreak_vol_ratio += 0.05
        strat.accel_ret5 += 0.005
        strat.accel_vol_ratio_fast += 0.10
        strat.accel_vol_ratio_slow += 0.05
    elif label == "高檔震盪盤":
        strat.rebreak_vol_ratio += 0.05
        strat.accel_ret5 += 0.01
        strat.accel_vol_ratio_fast += 0.05
        strat.accel_vol_ratio_slow += 0.05
    elif label == "強勢延伸盤":
        strat.rebreak_vol_ratio = max(strat.rebreak_vol_ratio - 0.05, 1.0)
        strat.accel_ret5 = max(strat.accel_ret5 - 0.005, 0.0)
        strat.accel_ret10 = max(strat.accel_ret10 - 0.01, strat.accel_ret5)
        strat.accel_vol_ratio_fast = max(strat.accel_vol_ratio_fast - 0.05, 1.0)
        strat.accel_vol_ratio_slow = max(strat.accel_vol_ratio_slow - 0.05, 1.0)
    return strat


def strategy_preview_lines(base_strat, scenario: dict) -> list[str]:
    adjusted = adjust_strategy_by_scenario(base_strat, scenario)
    field_labels = {
        "rebreak_vol_ratio": "rebreak 量比",
        "trend_ret20": "trend 20D",
        "accel_ret5": "accel 5D",
        "accel_ret10": "accel 10D",
        "accel_vol_ratio_fast": "accel 快速量比",
        "accel_vol_ratio_slow": "accel 緩速量比",
    }
    changed: list[str] = []
    for field, label in field_labels.items():
        before = getattr(base_strat, field)
        after = getattr(adjusted, field)
        if round(before, 4) == round(after, 4):
            continue
        changed.append(f"{label}: {before:.2f} → {after:.2f}")
    if not changed:
        return ["- 今日情境下，adaptive preview 不調整門檻。"]
    return [f"- {line}" for line in changed]
