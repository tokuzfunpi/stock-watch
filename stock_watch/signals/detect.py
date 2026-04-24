from __future__ import annotations

from typing import Any

import pandas as pd


def add_indicators(df: pd.DataFrame, ma_period: int = 20) -> pd.DataFrame:
    out = df.copy()
    for n in [5, 10, 20, 60, 120, 250]:
        out[f"MA{n}"] = out["Close"].rolling(n).mean()

    out["AvgVol20"] = out["Volume"].rolling(20).mean()
    out["Ret1D"] = out["Close"].pct_change(1)
    out["Ret5D"] = out["Close"].pct_change(5)
    out["Ret10D"] = out["Close"].pct_change(10)
    out["Ret20D"] = out["Close"].pct_change(20)

    out["High120D"] = out["Close"].rolling(120).max()
    out["High250D"] = out["Close"].rolling(250).max()
    out["Low250D"] = out["Close"].rolling(250).min()

    out["Drawdown120D"] = out["Close"] / out["High120D"] - 1.0
    out["Range20"] = (
        out["High"].rolling(20).max() - out["Low"].rolling(20).min()
    ) / out["Close"]
    out["DistToLow250"] = out["Close"] / out["Low250D"] - 1.0
    out["VolumeRatio20"] = out["Volume"] / out["AvgVol20"]

    tr = pd.concat(
        [
            out["High"] - out["Low"],
            (out["High"] - out["Close"].shift(1)).abs(),
            (out["Low"] - out["Close"].shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)
    out["ATR14"] = tr.rolling(14).mean()
    out["ATR_Pct"] = out["ATR14"] / out["Close"]

    if ma_period not in [5, 10, 20, 60, 120, 250]:
        out[f"MA{ma_period}"] = out["Close"].rolling(ma_period).mean()
    return out


def apply_group_weight(base_score: int, group: str, group_weights: Any) -> int:
    score = base_score
    if group == "theme":
        score += int(getattr(group_weights, "theme_bonus", 0))
    elif group == "core":
        score -= int(getattr(group_weights, "core_penalty", 0))
    elif group == "etf":
        score -= int(getattr(group_weights, "etf_penalty", 0))
    return max(score, 0)


def score_band(setup_score: int, risk_score: int) -> str:
    if risk_score >= 6:
        return "高風險追價區"
    if setup_score >= 8:
        return "進攻優勢區"
    if setup_score >= 6:
        return "偏強可追蹤"
    if setup_score >= 4:
        return "開始轉強"
    return "一般觀察"


def speculative_risk_score(
    ret5_pct: float,
    ret20_pct: float,
    volume_ratio20: float,
    bias20_pct: float,
    risk_score: int,
    signals: str,
    group: str,
) -> int:
    score = 0
    if ret5_pct >= 15:
        score += 2
    if ret5_pct >= 25:
        score += 1
    if ret20_pct >= 30:
        score += 2
    if volume_ratio20 >= 1.8:
        score += 1
    if volume_ratio20 >= 2.5:
        score += 1
    if bias20_pct >= 12:
        score += 2
    if risk_score >= 5:
        score += 1
    if "TREND" not in signals and "REBREAK" not in signals and ret5_pct >= 15:
        score += 1

    if "TREND" in signals:
        score -= 1
    if "REBREAK" in signals:
        score -= 1
    if group in {"core", "etf"}:
        score -= 1

    return max(score, 0)


def speculative_risk_label(score: int) -> str:
    if score >= 6:
        return "疑似炒作風險高"
    if score >= 3:
        return "投機偏高"
    return "正常"


def volatility_label(atr_pct: float) -> str:
    if atr_pct <= 0:
        return "未知"
    if atr_pct < 2.0:
        return "穩健"
    if atr_pct < 4.0:
        return "標準"
    if atr_pct < 6.5:
        return "活潑"
    return "劇烈"


def detect_row(
    df: pd.DataFrame,
    ticker: str,
    name: str,
    group: str,
    layer: str,
    strat: Any,
    group_weights: Any,
) -> dict:
    x = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else x

    close_ = float(x["Close"])
    volume = float(x["Volume"])
    avg_vol20 = float(x["AvgVol20"]) if pd.notna(x["AvgVol20"]) else 0.0
    vol_ratio20 = float(x["VolumeRatio20"]) if pd.notna(x["VolumeRatio20"]) else 0.0

    ma20 = float(x["MA20"]) if pd.notna(x["MA20"]) else None
    ma60 = float(x["MA60"]) if pd.notna(x["MA60"]) else None
    ma120 = float(x["MA120"]) if pd.notna(x["MA120"]) else None
    low250 = float(x["Low250D"]) if pd.notna(x["Low250D"]) else None

    ret1 = float(x["Ret1D"]) if pd.notna(x["Ret1D"]) else 0.0
    ret5 = float(x["Ret5D"]) if pd.notna(x["Ret5D"]) else 0.0
    ret10 = float(x["Ret10D"]) if pd.notna(x["Ret10D"]) else 0.0
    ret20 = float(x["Ret20D"]) if pd.notna(x["Ret20D"]) else 0.0

    drawdown120 = float(x["Drawdown120D"]) if pd.notna(x["Drawdown120D"]) else 0.0
    range20 = float(x["Range20"]) if pd.notna(x["Range20"]) else 999.0
    dist_low250 = float(x["DistToLow250"]) if pd.notna(x["DistToLow250"]) else 999.0

    base_signal = bool(
        low250 is not None
        and close_ <= low250 * strat.base_low250_mult
        and avg_vol20 > 0
        and volume < avg_vol20
        and range20 < strat.base_range20_max
    )
    rebreak_signal = bool(
        ma20 is not None and ma60 is not None and avg_vol20 > 0
        and close_ > ma20 and close_ > ma60
        and vol_ratio20 > strat.rebreak_vol_ratio
        and pd.notna(prev.get("MA20"))
        and float(prev["Close"]) <= float(prev["MA20"])
    )
    surge_signal = bool(ret20 > strat.surge_ret20 and vol_ratio20 > strat.surge_vol_ratio)
    trend_signal = bool(
        ma20 is not None and ma60 is not None
        and close_ > ma20 and ma20 > ma60 and ret20 > strat.trend_ret20
    )
    accel_signal = bool(
        (ret5 > strat.accel_ret5 and vol_ratio20 > strat.accel_vol_ratio_fast and ret20 > 0)
        or (ret10 > strat.accel_ret10 and vol_ratio20 > strat.accel_vol_ratio_slow and ret20 > 0)
    )
    pullback_signal = bool(drawdown120 <= -0.20)

    setup_score = 0
    if low250 is not None and close_ <= low250 * strat.base_low250_mult:
        setup_score += 2
    elif low250 is not None and close_ <= low250 * (strat.base_low250_mult + 0.15):
        setup_score += 1

    if avg_vol20 > 0 and volume < avg_vol20:
        setup_score += 1
    if range20 < strat.base_range20_max:
        setup_score += 1
    if range20 < max(strat.base_range20_max - 0.05, 0.0):
        setup_score += 1
    if ma20 is not None and close_ > ma20:
        setup_score += 1
    if ma60 is not None and close_ > ma60:
        setup_score += 2

    if vol_ratio20 > 1.5:
        setup_score += 2
    elif vol_ratio20 > 1.2:
        setup_score += 1

    if dist_low250 < 0.25 and ret20 > 0.10:
        setup_score += 1
    if ret20 > 0.12:
        setup_score += 1
    if rebreak_signal:
        setup_score += 1
    if surge_signal:
        setup_score += 1
    if trend_signal:
        setup_score += 1

    if ret5 > 0.08:
        setup_score += 2
    elif ret5 > 0.04:
        setup_score += 1

    if vol_ratio20 > 1.5:
        setup_score += 1

    if group == "theme" and ret5 > 0.06:
        setup_score += 2
    elif group == "satellite" and ret5 > 0.06:
        setup_score += 1

    risk_score = 0
    if ret5 > 0.18:
        risk_score += 2
    if ret20 > 0.30:
        risk_score += 2
    if ret20 > 0.50:
        risk_score += 2
    if vol_ratio20 > 2.5:
        risk_score += 2
    elif vol_ratio20 > 1.8:
        risk_score += 1
    if drawdown120 > -0.05:
        risk_score += 1

    if ma20 is not None and ma20 > 0:
        bias20 = close_ / ma20 - 1.0
        if bias20 > 0.15:
            risk_score += 2
        elif bias20 > 0.08:
            risk_score += 1
    else:
        bias20 = 0.0

    setup_score = apply_group_weight(setup_score, group, group_weights)

    signals: list[str] = []
    if base_signal:
        signals.append("BASE")
    if rebreak_signal:
        signals.append("REBREAK")
    if surge_signal:
        signals.append("SURGE")
    if trend_signal:
        signals.append("TREND")
    if accel_signal:
        signals.append("ACCEL")
    if pullback_signal:
        signals.append("PULLBACK")

    if risk_score >= 6:
        regime = "有點過熱，別硬追"
    elif surge_signal:
        regime = "題材正在發酵"
    elif rebreak_signal:
        regime = "重新站上來了"
    elif accel_signal:
        regime = "轉強速度有出來"
    elif trend_signal:
        regime = "中段延續中"
    elif base_signal:
        regime = "低檔慢慢墊高"
    elif pullback_signal:
        regime = "高檔拉回整理"
    else:
        regime = "還在觀察"

    signal_text = ",".join(signals) if signals else "NONE"
    spec_score = speculative_risk_score(
        ret5_pct=ret5 * 100,
        ret20_pct=ret20 * 100,
        volume_ratio20=vol_ratio20,
        bias20_pct=bias20 * 100,
        risk_score=risk_score,
        signals=signal_text,
        group=group,
    )
    atr_pct = round(float(x["ATR_Pct"]) * 100, 2) if pd.notna(x.get("ATR_Pct")) else 0.0

    return {
        "date": df.index[-1].strftime("%Y-%m-%d"),
        "ticker": ticker,
        "name": name,
        "group": group,
        "layer": layer,
        "close": round(close_, 2),
        "ret1_pct": round(ret1 * 100, 2),
        "ret5_pct": round(ret5 * 100, 2),
        "ret10_pct": round(ret10 * 100, 2),
        "ret20_pct": round(ret20 * 100, 2),
        "volume": int(volume),
        "avg_vol20": int(avg_vol20) if avg_vol20 else 0,
        "volume_ratio20": round(vol_ratio20, 2),
        "ma20": round(ma20, 2) if ma20 is not None else None,
        "ma60": round(ma60, 2) if ma60 is not None else None,
        "ma120": round(ma120, 2) if ma120 is not None else None,
        "drawdown120_pct": round(drawdown120 * 100, 2),
        "bias20_pct": round(bias20 * 100, 2),
        "setup_score": int(setup_score),
        "risk_score": int(risk_score),
        "signals": signal_text,
        "score_band": score_band(setup_score, risk_score),
        "regime": regime,
        "spec_risk_score": int(spec_score),
        "spec_risk_label": speculative_risk_label(spec_score),
        "atr_pct": atr_pct,
        "volatility_tag": volatility_label(atr_pct),
    }


def grade_signal(row: dict) -> str:
    setup = row["setup_score"]
    risk = row["risk_score"]
    signals = row["signals"]
    ret5 = row["ret5_pct"]
    vol_ratio20 = row["volume_ratio20"]
    ret20 = row["ret20_pct"]

    if setup >= 7 and risk <= 4 and (("ACCEL" in signals) or ("REBREAK" in signals) or ("SURGE" in signals)) and ret20 > 0:
        return "A"
    if setup >= 5 and risk <= 4 and (ret5 >= 5 or vol_ratio20 >= 1.3):
        return "B"
    if risk >= 6:
        return "C"
    return "X"

