from __future__ import annotations

from collections.abc import Callable

import pandas as pd

from stock_watch.signals.detect import volatility_label


def layer_label(layer: str) -> str:
    labels = {
        "short_attack": "短線主攻",
        "midlong_core": "中長線核心",
        "defensive_watch": "防守觀察",
    }
    return labels.get(layer, layer)


def volatility_emoji(tag: str) -> str:
    return {
        "穩健": "🧊",
        "標準": "⚖️",
        "活潑": "🔥",
        "劇烈": "⚡",
    }.get(tag, "❔")


def volatility_badge_text(row: pd.Series) -> str:
    tag = str(row.get("volatility_tag", "") or "")
    atr_pct = row.get("atr_pct")
    if not tag:
        try:
            atr_value = float(atr_pct)
            if atr_value > 0:
                tag = volatility_label(atr_value)
        except Exception:
            tag = ""
    if not tag:
        return ""
    emoji = volatility_emoji(tag)
    try:
        atr_value = float(atr_pct)
        if atr_value > 0:
            return f"{emoji}{tag}({atr_value:.2f}%)"
    except Exception:
        pass
    return f"{emoji}{tag}"


def format_ticker_name(row: pd.Series) -> str:
    name = str(row.get("name", "") or "").strip()
    ticker = str(row.get("ticker", "") or "").strip()
    if name and ticker:
        return f"{name} ({ticker})"
    if ticker:
        return ticker
    if name:
        return name
    rank = row.get("rank")
    if pd.notna(rank):
        return f"rank#{int(rank)}"
    return "未命名標的"


def primary_watch_summary(
    candidates: pd.DataFrame,
    *,
    watch_type: str,
    short_term_action_label: Callable[[pd.Series], str],
    midlong_action_label: Callable[[pd.Series], str],
) -> list[str]:
    if candidates is None or candidates.empty:
        return []
    top = candidates.head(min(len(candidates), 3)).copy()
    names = "、".join(format_ticker_name(row) for _, row in top.iterrows())
    if watch_type == "short":
        actions = top.apply(short_term_action_label, axis=1).value_counts().to_dict()
        if actions.get("等拉回", 0) >= 1:
            stance = "以等拉回為主，不用急著追第一根。"
        elif actions.get("開高不追", 0) >= 1 or actions.get("只觀察不追", 0) >= 1:
            stance = "前排偏熱，重點是看強弱，不是直接追價。"
        else:
            stance = "有名單可看，但仍先看買點品質。"
        return [f"先看：{names}", f"一句話：{stance}"]

    actions = top.apply(midlong_action_label, axis=1).value_counts().to_dict()
    if actions.get("續抱", 0) >= 1:
        stance = "以結構穩、可續抱的趨勢股為主。"
    elif actions.get("可分批", 0) >= 1:
        stance = "可以布局，但偏向分批而不是一次買滿。"
    else:
        stance = "目前先看結構穩定度，不急著放大部位。"
    return [f"先看：{names}", f"一句話：{stance}"]


def observation_summary(backups: pd.DataFrame, *, watch_type: str) -> list[str]:
    if backups is None or backups.empty:
        return []
    high_spec = int((backups.get("spec_risk_label", pd.Series(index=backups.index, dtype=object)).astype(str) == "疑似炒作風險高").sum())
    hot_names = "、".join(format_ticker_name(row) for _, row in backups.head(min(len(backups), 2)).iterrows())
    if watch_type == "short":
        if high_spec >= 1:
            return [f"觀察重點：{hot_names}", "提醒：觀察區比較像熱股/續看名單，不代表今天適合直接出手。"]
        return [f"觀察重點：{hot_names}", "提醒：觀察區是備選，不是主推；要等訊號再更完整一點。"]
    if high_spec >= 1:
        return [f"觀察重點：{hot_names}", "提醒：中長線觀察區偏強但偏熱，先看能不能整理後再接。"]
    return [f"觀察重點：{hot_names}", "提醒：這區先看結構，不急著把每檔都當成可布局。"]


def unique_by_ticker(df: pd.DataFrame | None) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    ranked = df.sort_values(by=["rank"], ascending=[True]).copy()
    if "ticker" in ranked.columns:
        ranked = ranked.drop_duplicates(subset=["ticker"], keep="first")
    return ranked.reset_index(drop=True)


def fill_rows_to_limit(
    base: pd.DataFrame | None,
    fallback: pd.DataFrame | None,
    *,
    limit: int,
    exclude_tickers: set[str] | None = None,
) -> pd.DataFrame:
    exclude = {str(t) for t in (exclude_tickers or set())}
    frames: list[pd.DataFrame] = []
    if base is not None and not base.empty:
        frames.append(base.copy())
        if "ticker" in base.columns:
            exclude.update(base["ticker"].astype(str))
    if fallback is not None and not fallback.empty:
        filler = fallback.copy()
        if "ticker" in filler.columns:
            filler = filler[~filler["ticker"].astype(str).isin(exclude)].copy()
        frames.append(filler)
    if not frames:
        return pd.DataFrame()
    return unique_by_ticker(pd.concat(frames, ignore_index=True)).head(limit).copy()


def compact_summary_line(
    row: pd.Series,
    *,
    watch_type: str,
    short_term_action_label: Callable[[pd.Series], str],
    midlong_action_label: Callable[[pd.Series], str],
) -> str:
    action = short_term_action_label(row) if watch_type == "short" else midlong_action_label(row)
    role = "短線" if watch_type == "short" else "中線"
    return f"- {format_ticker_name(row)}｜{role}｜{action}"


def no_chase_reason(
    row: pd.Series,
    *,
    short_term_action_label: Callable[[pd.Series], str],
    midlong_action_label: Callable[[pd.Series], str],
) -> str:
    short_action = short_term_action_label(row)
    midlong_action = midlong_action_label(row)
    spec_label = str(row.get("spec_risk_label", "正常"))
    if spec_label == "疑似炒作風險高":
        return "疑似炒作風險高"
    if short_action in {"只觀察不追", "開高不追", "分批落袋"}:
        return short_action
    if midlong_action in {"減碼觀察", "分批落袋"}:
        return midlong_action
    if float(row.get("ret5_pct", 0.0) or 0.0) >= 12:
        return "短線過熱"
    return "偏熱先別追"


def compact_briefing_lines(
    df_rank: pd.DataFrame | None,
    market_regime: dict,
    us_market: dict,
    *,
    build_candidate_sets: Callable[[pd.DataFrame, dict, dict], tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]],
    short_term_action_label: Callable[[pd.Series], str],
    midlong_action_label: Callable[[pd.Series], str],
) -> list[str]:
    if df_rank is None or df_rank.empty:
        return []

    required = {
        "rank",
        "ticker",
        "name",
        "group",
        "layer",
        "grade",
        "setup_score",
        "risk_score",
        "ret5_pct",
        "ret20_pct",
        "volume_ratio20",
        "signals",
        "rank_change",
        "setup_change",
        "spec_risk_label",
    }
    if not required.issubset(df_rank.columns):
        return []

    short_candidates, short_backups, midlong_candidates, midlong_backups = build_candidate_sets(
        df_rank,
        market_regime,
        us_market,
    )
    avoid_mask = df_rank.apply(
        lambda row: (
            str(row.get("spec_risk_label", "正常")) == "疑似炒作風險高"
            or short_term_action_label(row) in {"只觀察不追", "開高不追", "分批落袋"}
            or midlong_action_label(row) in {"減碼觀察", "分批落袋"}
        ),
        axis=1,
    )
    primary_base = unique_by_ticker(pd.concat([short_candidates, midlong_candidates], ignore_index=True))
    ranked_fallback = unique_by_ticker(df_rank.copy())
    safe_fallback = ranked_fallback[~ranked_fallback["ticker"].astype(str).isin(set(df_rank[avoid_mask]["ticker"].astype(str)))].copy()
    primary = fill_rows_to_limit(primary_base, safe_fallback, limit=5)

    observation_base = unique_by_ticker(pd.concat([short_backups, midlong_backups], ignore_index=True))
    if not primary.empty and "ticker" in observation_base.columns:
        observation_base = observation_base[
            ~observation_base["ticker"].astype(str).isin(set(primary["ticker"].astype(str)))
        ].copy()
    observation = fill_rows_to_limit(
        observation_base,
        ranked_fallback,
        limit=5,
        exclude_tickers=set(primary["ticker"].astype(str)) if not primary.empty else set(),
    )

    avoid_exclude = set(primary["ticker"].astype(str)) if not primary.empty else set()
    avoid_seed = df_rank[avoid_mask].copy()
    if avoid_exclude:
        avoid_seed = avoid_seed[~avoid_seed["ticker"].astype(str).isin(avoid_exclude)].copy()
    avoid_fallback = df_rank.sort_values(by=["risk_score", "ret5_pct", "rank"], ascending=[False, False, True]).copy()
    existing_avoid = set(avoid_seed["ticker"].astype(str)) if not avoid_seed.empty else set()
    avoid_fallback = avoid_fallback[
        ~avoid_fallback["ticker"].astype(str).isin(avoid_exclude | existing_avoid)
    ].copy()
    avoid_frames = [frame for frame in [avoid_seed, avoid_fallback] if not frame.empty]
    avoid = pd.concat(avoid_frames, ignore_index=True) if avoid_frames else pd.DataFrame()
    if not avoid.empty:
        avoid = (
            avoid.drop_duplicates(subset=["ticker"], keep="first")
            .sort_values(by=["risk_score", "ret5_pct", "rank"], ascending=[False, False, True])
            .head(3)
            .reset_index(drop=True)
        )

    lines: list[str] = []
    if not primary.empty:
        lines.append("")
        lines.append("先看 5 檔")
        for _, row in primary.head(5).iterrows():
            lines.append(
                compact_summary_line(
                    row,
                    watch_type="short" if str(row.get("layer", "")) == "short_attack" else "midlong",
                    short_term_action_label=short_term_action_label,
                    midlong_action_label=midlong_action_label,
                )
            )
    if not observation.empty:
        lines.append("")
        lines.append("觀察 5 檔")
        for _, row in observation.head(5).iterrows():
            lines.append(
                compact_summary_line(
                    row,
                    watch_type="short" if str(row.get("layer", "")) == "short_attack" else "midlong",
                    short_term_action_label=short_term_action_label,
                    midlong_action_label=midlong_action_label,
                )
            )
    if not avoid.empty:
        lines.append("")
        lines.append("今天不要追的 3 檔")
        for _, row in avoid.head(3).iterrows():
            lines.append(
                f"- {format_ticker_name(row)}｜"
                f"{no_chase_reason(row, short_term_action_label=short_term_action_label, midlong_action_label=midlong_action_label)}"
            )
    return lines


def candidate_line(
    row: pd.Series,
    *,
    watch_type: str,
    short_term_action_label: Callable[[pd.Series], str],
    midlong_action_label: Callable[[pd.Series], str],
    watch_price_plan_text: Callable[[pd.Series, str], str],
) -> str:
    action = short_term_action_label(row) if watch_type == "short" else midlong_action_label(row)
    period_label = "5日" if watch_type == "short" else "20日"
    period_value = row["ret5_pct"] if watch_type == "short" else row["ret20_pct"]
    vol_text = volatility_badge_text(row)
    return (
        f"- #{int(row['rank'])} {format_ticker_name(row)}｜{action}\n"
        f"  {period_label} {period_value}% / 量比 {row['volume_ratio20']}｜{vol_text}｜{row['regime']}\n"
        f"  {watch_price_plan_text(row, watch_type)}"
    )


def special_etf_summary(etf_candidates: pd.DataFrame) -> list[str]:
    if etf_candidates.empty:
        return ["今天指定 ETF / 債券標的沒有抓到完整資料，先看盤中報表。"]

    equity = etf_candidates[etf_candidates["ticker"].isin(["0050.TW", "00878.TW"])].copy()
    bonds = etf_candidates[etf_candidates["ticker"].isin(["00772B.TWO", "00773B.TWO"])].copy()
    summary: list[str] = []

    if not equity.empty:
        eq_ret20 = float(equity["ret20_pct"].mean())
        if eq_ret20 >= 5:
            summary.append("股票 ETF 偏多，台股大盤與高股息風格仍有撐。")
        elif eq_ret20 >= 0:
            summary.append("股票 ETF 偏穩，較像整理後續看量價。")
        else:
            summary.append("股票 ETF 偏弱，今天台股大型權值先別太急。")

    if not bonds.empty:
        bond_ret20 = float(bonds["ret20_pct"].mean())
        if bond_ret20 >= 3:
            summary.append("債券 ETF 偏穩，防守資金和利率壓力都還算可控。")
        elif bond_ret20 >= 0:
            summary.append("債券 ETF 中性，先當防守觀察，不急著加碼。")
        else:
            summary.append("債券 ETF 偏弱，代表利率面壓力仍在。")

    return summary or ["ETF / 債券整體訊號還不夠明確，先觀察。"]
