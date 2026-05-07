from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path

import pandas as pd

from stock_watch.reports import messages


CandidateSetsBuilder = Callable[[pd.DataFrame, dict, dict], tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]]


def _compact_text(value: object, *, limit: int = 88) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1]}…"


def subscriber_scenario_lines(scenario: dict) -> list[str]:
    label = str(scenario.get("label", "") or "")

    if label == "盤中保守觀察":
        return [
            "今日盤勢：盤中保守觀察",
            "今日策略：先保守，等收盤再定案。",
            "白話說：盤中先縮手、少追高，但不要只因即時噪音就把整天當成修正盤。",
        ]

    if label == "明顯修正盤":
        return [
            "今日盤勢：明顯修正盤",
            "今日策略：先防守，短線名單縮小。",
            "白話說：今天先保留資金、少做少追高，等盤勢穩定再提高出手頻率。",
        ]

    if label == "高檔震盪盤":
        return [
            "今日盤勢：高檔震盪盤",
            "今日策略：可以挑股，但節奏放慢。",
            "白話說：盤面還不差，但追價容錯率下降，優先等拉回、強中選強。",
        ]

    if label == "權值撐盤、個股轉弱":
        return [
            "今日盤勢：權值撐盤、個股轉弱",
            "今日策略：指數可看，選股更要保守。",
            "白話說：不是整個市場都好做，先看真正有量有延續性的個股，不要被指數撐住騙進去。",
        ]

    return [
        "今日盤勢：強勢延伸盤",
        "今日策略：正常推送，可做但不追高。",
        "白話說：市場資金偏正向，名單可正常參考，但仍以拉回分批進場為主。",
    ]


def subscriber_watchlist_lines(scenario: dict, watch_type: str, candidate_limit: int) -> list[str]:
    label = str(scenario.get("label", "") or "")
    title = "短線" if watch_type == "short" else "中長線"

    if watch_type == "short":
        if label == "盤中保守觀察":
            return [
                f"今天{title}策略：先保守，暫時只看 {candidate_limit} 檔，等收盤再決定要不要放大。",
                "白話提醒：盤中先縮手，不急著把每次拉回都當買點。",
            ]
        if label == "明顯修正盤":
            return [
                f"今天{title}策略：先縮手，最多看 {candidate_limit} 檔最清楚的標的。",
                "白話提醒：少做比做錯更重要，先等盤勢穩定。",
            ]
        if label == "高檔震盪盤":
            return [
                f"今天{title}策略：可以做，但只挑前排 {candidate_limit} 檔，優先等拉回。",
                "白話提醒：盤還熱，但追價風險明顯上來了。",
            ]
        if label == "權值撐盤、個股轉弱":
            return [
                f"今天{title}策略：只做最強的 {candidate_limit} 檔，不被指數撐盤帶著追。",
                "白話提醒：指數不差，不代表個股都值得追。",
            ]
        return [
            f"今天{title}策略：正常推送，最多看 {candidate_limit} 檔，拉回再切入。",
            "白話提醒：有行情可以做，但仍以分批進場取代追高。",
        ]

    if label == "盤中保守觀察":
        return [
            f"今天{title}策略：先守結構，只留 {candidate_limit} 檔核心觀察，收盤後再定案。",
            "白話提醒：盤中先保守，不急著把部位一次放大。",
        ]
    if label == "明顯修正盤":
        return [
            f"今天{title}策略：偏保守，只留 {candidate_limit} 檔核心觀察。",
            "白話提醒：先守部位、看結構，不急著擴大進場。",
        ]
    if label == "高檔震盪盤":
        return [
            f"今天{title}策略：可以布局，但只挑 {candidate_limit} 檔結構最穩的標的。",
            "白話提醒：這種盤要挑買點，不要因為看好就直接追。",
        ]
    if label == "權值撐盤、個股轉弱":
        return [
            f"今天{title}策略：偏精挑細選，只留 {candidate_limit} 檔延續性最好的標的。",
            "白話提醒：先看個股有沒有真延續，不要只看指數撐住。",
        ]
    return [
        f"今天{title}策略：正常布局，最多看 {candidate_limit} 檔。",
        "白話提醒：趨勢還在，但仍以等拉回與分批進場為主。",
    ]


def build_early_gem_message(
    df_rank: pd.DataFrame,
    *,
    select_early_gem_candidates: Callable[[pd.DataFrame], pd.DataFrame],
    early_gem_reason: Callable[[pd.Series], str],
    watch_price_plan_text: Callable[[pd.Series, str], str],
) -> str:
    gem_candidates = select_early_gem_candidates(df_rank)
    lines = [
        "📣 早期轉強觀察",
    ]
    if gem_candidates.empty:
        lines.append("今天沒有特別像『還沒完全被市場定價，但已開始轉強』的標的。")
        return "\n".join(lines).strip()

    top_names = "、".join(messages.format_ticker_name(row) for _, row in gem_candidates.head(min(len(gem_candidates), 3)).iterrows())
    lines.append(f"先看：{top_names}")
    lines.append("一句話：這區是『剛轉強、還沒太擁擠』的候選，不是追最熱。")
    lines.append("")
    lines.append("定位：剛轉強、還沒太擁擠；先研究，不追熱。")
    lines.append("")
    for _, row in gem_candidates.iterrows():
        lines.append(
            f"{messages.format_ticker_name(row)}｜{messages.layer_label(row['layer'])}\n"
            f"   5日 {row['ret5_pct']}%｜20日 {row['ret20_pct']}%｜{messages.volatility_badge_text(row)}\n"
            f"   理由：{early_gem_reason(row)}\n"
            f"   買點：{watch_price_plan_text(row, 'short')}"
        )
    return "\n".join(lines).strip()


def build_simple_early_gem_message(
    df_rank: pd.DataFrame,
    *,
    select_early_gem_candidates: Callable[[pd.DataFrame], pd.DataFrame],
    watch_price_plan_text: Callable[[pd.Series, str], str],
) -> str:
    gem_candidates = select_early_gem_candidates(df_rank).head(3)
    lines = ["🆕 早期轉強精簡版"]
    if gem_candidates.empty:
        lines.append("今天沒有特別適合新手先看的早期轉強標的。")
        return "\n".join(lines).strip()

    lines.append("定位：剛轉強候選，先研究買點，不追熱。")
    lines.append("")
    for _, row in gem_candidates.iterrows():
        plan = watch_price_plan_text(row, "short")
        lines.append(
            f"• {messages.format_ticker_name(row)}\n"
            f"  5日 {row['ret5_pct']}%｜{messages.volatility_badge_text(row)}｜{row['regime']}\n"
            f"  買點：{plan}"
        )
    return "\n".join(lines).strip()


def build_special_etf_message(
    df_rank: pd.DataFrame,
    *,
    select_special_etf_candidates: Callable[[pd.DataFrame], pd.DataFrame],
    build_special_etf_summary: Callable[[pd.DataFrame], list[str]],
    special_etf_action_label: Callable[[pd.Series], str],
) -> str:
    etf_candidates = select_special_etf_candidates(df_rank)
    lines = [
        "📣 ETF / 債券觀察",
    ]
    lines.extend(build_special_etf_summary(etf_candidates))
    if etf_candidates.empty:
        return "\n".join(lines).strip()

    lines.append("")
    lines.append("定位：0050/00878 看台股風向；00772B/00773B 看利率與防守。")
    lines.append("")
    for _, row in etf_candidates.iterrows():
        action = special_etf_action_label(row)
        lines.append(
            f"• {row['name']} ({row['ticker']})｜{action}\n"
            f"  5日 {row['ret5_pct']}%｜20日 {row['ret20_pct']}%｜{messages.layer_label(row['layer'])}"
        )
    return "\n".join(lines).strip()


def _simple_candidate_lines(
    candidates: pd.DataFrame,
    *,
    watch_type: str,
    short_term_action_label: Callable[[pd.Series], str],
    midlong_action_label: Callable[[pd.Series], str],
    watch_price_plan_text: Callable[[pd.Series, str], str],
    limit: int = 3,
) -> list[str]:
    lines: list[str] = []
    for _, row in candidates.head(limit).iterrows():
        lines.append(
            messages.candidate_card(
                row,
                watch_type=watch_type,
                short_term_action_label=short_term_action_label,
                midlong_action_label=midlong_action_label,
                watch_price_plan_text=watch_price_plan_text,
            )
        )
    return lines


def build_short_term_message(
    df_rank: pd.DataFrame,
    market_regime: dict,
    us_market: dict,
    *,
    build_candidate_sets: CandidateSetsBuilder,
    build_market_scenario: Callable[[dict, dict, pd.DataFrame], dict],
    effective_short_top_n: Callable[[pd.DataFrame, dict, dict], int],
    short_term_action_label: Callable[[pd.Series], str],
    midlong_action_label: Callable[[pd.Series], str],
    watch_price_plan_text: Callable[[pd.Series, str], str],
) -> str:
    short_candidates, short_backups, _, _ = build_candidate_sets(df_rank, market_regime, us_market)
    scenario = build_market_scenario(market_regime, us_market, df_rank)
    short_top_n = effective_short_top_n(df_rank, market_regime, us_market)
    total_a = int((df_rank["grade"] == "A").sum()) if not df_rank.empty else 0
    total_b = int((df_rank["grade"] == "B").sum()) if not df_rank.empty else 0
    total_up = int((df_rank["status_change"] == "UP").sum()) if "status_change" in df_rank.columns else 0

    lines = [
        "⚡ 短線可買",
    ]
    summary_parts = [f"A級 {total_a} 檔", f"B級 {total_b} 檔", f"轉強 {total_up} 檔"]
    lines.append(f"概況：{' / '.join(summary_parts)}")
    lines.extend(subscriber_watchlist_lines(scenario, "short", short_top_n))
    if short_candidates.empty:
        lines.append("今天短線沒有夠清楚的可買標的，先等。")
        return "\n".join(lines)

    lines.append("")
    lines.extend(
        messages.primary_watch_summary(
            short_candidates,
            watch_type="short",
            short_term_action_label=short_term_action_label,
            midlong_action_label=midlong_action_label,
        )
    )
    lines.append("")
    lines.append("📌 主名單")
    lines.append("")
    for _, row in short_candidates.iterrows():
        lines.append(
            messages.candidate_card(
                row,
                watch_type="short",
                short_term_action_label=short_term_action_label,
                midlong_action_label=midlong_action_label,
                watch_price_plan_text=watch_price_plan_text,
            )
        )
    if not short_backups.empty:
        lines.append("")
        lines.append("👀 短線觀察")
        lines.extend(messages.observation_summary(short_backups, watch_type="short"))
        lines.append("")
        for _, row in short_backups.head(3).iterrows():
            lines.append(
                messages.candidate_card(
                    row,
                    watch_type="short",
                    short_term_action_label=short_term_action_label,
                    midlong_action_label=midlong_action_label,
                    watch_price_plan_text=watch_price_plan_text,
                )
            )
    return "\n".join(lines).strip()


def build_simple_short_term_message(
    df_rank: pd.DataFrame,
    market_regime: dict,
    us_market: dict,
    *,
    build_candidate_sets: CandidateSetsBuilder,
    build_market_scenario: Callable[[dict, dict, pd.DataFrame], dict],
    effective_short_top_n: Callable[[pd.DataFrame, dict, dict], int],
    short_term_action_label: Callable[[pd.Series], str],
    midlong_action_label: Callable[[pd.Series], str],
    watch_price_plan_text: Callable[[pd.Series, str], str],
) -> str:
    short_candidates, _, _, _ = build_candidate_sets(df_rank, market_regime, us_market)
    scenario = build_market_scenario(market_regime, us_market, df_rank)
    short_top_n = effective_short_top_n(df_rank, market_regime, us_market)
    lines = [
        "⚡ 短線精簡版",
        f"策略：{subscriber_watchlist_lines(scenario, 'short', short_top_n)[0]}",
    ]
    if short_candidates.empty:
        lines.append("今天沒有夠清楚的短線可小買標的。")
        return "\n".join(lines).strip()

    lines.append("")
    lines.extend(
        _simple_candidate_lines(
            short_candidates,
            watch_type="short",
            short_term_action_label=short_term_action_label,
            midlong_action_label=midlong_action_label,
            watch_price_plan_text=watch_price_plan_text,
        )
    )
    return "\n".join(lines).strip()


def build_midlong_message(
    df_rank: pd.DataFrame,
    market_regime: dict,
    us_market: dict,
    *,
    build_candidate_sets: CandidateSetsBuilder,
    build_market_scenario: Callable[[dict, dict, pd.DataFrame], dict],
    effective_midlong_top_n: Callable[[pd.DataFrame, dict, dict], int],
    short_term_action_label: Callable[[pd.Series], str],
    midlong_action_label: Callable[[pd.Series], str],
    watch_price_plan_text: Callable[[pd.Series, str], str],
) -> str:
    _, _, midlong_candidates, midlong_backups = build_candidate_sets(df_rank, market_regime, us_market)
    scenario = build_market_scenario(market_regime, us_market, df_rank)
    midlong_top_n = effective_midlong_top_n(df_rank, market_regime, us_market)
    total_b = int((df_rank["grade"] == "B").sum()) if not df_rank.empty else 0
    lines = [
        "🧱 中長線可布局",
        f"概況：B級結構股 {total_b} 檔",
    ]
    lines.extend(subscriber_watchlist_lines(scenario, "midlong", midlong_top_n))
    if midlong_candidates.empty:
        lines.append("今天中長線沒有夠穩、夠適合布局的標的，先觀察。")
        return "\n".join(lines)

    lines.append("")
    lines.extend(
        messages.primary_watch_summary(
            midlong_candidates,
            watch_type="midlong",
            short_term_action_label=short_term_action_label,
            midlong_action_label=midlong_action_label,
        )
    )
    lines.append("")
    lines.append("📌 主名單")
    lines.append("")
    for _, row in midlong_candidates.iterrows():
        lines.append(
            messages.candidate_card(
                row,
                watch_type="midlong",
                short_term_action_label=short_term_action_label,
                midlong_action_label=midlong_action_label,
                watch_price_plan_text=watch_price_plan_text,
            )
        )
    if not midlong_backups.empty:
        lines.append("")
        lines.append("👀 中長線觀察")
        lines.extend(messages.observation_summary(midlong_backups, watch_type="midlong"))
        lines.append("")
        for _, row in midlong_backups.head(3).iterrows():
            lines.append(
                messages.candidate_card(
                    row,
                    watch_type="midlong",
                    short_term_action_label=short_term_action_label,
                    midlong_action_label=midlong_action_label,
                    watch_price_plan_text=watch_price_plan_text,
                )
            )
    return "\n".join(lines).strip()


def build_simple_midlong_message(
    df_rank: pd.DataFrame,
    market_regime: dict,
    us_market: dict,
    *,
    build_candidate_sets: CandidateSetsBuilder,
    build_market_scenario: Callable[[dict, dict, pd.DataFrame], dict],
    effective_midlong_top_n: Callable[[pd.DataFrame, dict, dict], int],
    short_term_action_label: Callable[[pd.Series], str],
    midlong_action_label: Callable[[pd.Series], str],
    watch_price_plan_text: Callable[[pd.Series, str], str],
) -> str:
    _, _, midlong_candidates, _ = build_candidate_sets(df_rank, market_regime, us_market)
    scenario = build_market_scenario(market_regime, us_market, df_rank)
    midlong_top_n = effective_midlong_top_n(df_rank, market_regime, us_market)
    lines = [
        "🧱 中長線精簡版",
        f"策略：{subscriber_watchlist_lines(scenario, 'midlong', midlong_top_n)[0]}",
    ]
    if midlong_candidates.empty:
        lines.append("今天沒有夠穩、適合新手先看的中長線標的。")
        return "\n".join(lines).strip()

    lines.append("")
    lines.extend(
        _simple_candidate_lines(
            midlong_candidates,
            watch_type="midlong",
            short_term_action_label=short_term_action_label,
            midlong_action_label=midlong_action_label,
            watch_price_plan_text=watch_price_plan_text,
        )
    )
    return "\n".join(lines).strip()


def new_watchlist_spotlight_lines(
    df_rank: pd.DataFrame | None,
    *,
    new_watch_spotlight_limit: int,
    prev_rank_csv: Path,
    short_term_action_label: Callable[[pd.Series], str],
    midlong_action_label: Callable[[pd.Series], str],
) -> list[str]:
    limit = int(new_watch_spotlight_limit)
    if limit <= 0 or df_rank is None or df_rank.empty or "status_change" not in df_rank.columns:
        return []
    if not prev_rank_csv.exists():
        return []

    fresh = df_rank[df_rank["status_change"].astype(str).eq("NEW")].copy().head(limit)
    if fresh.empty:
        return []

    lines = ["🆕 新加入追蹤觀察", "定位：先分角色，不等於主推。"]
    for _, row in fresh.iterrows():
        watch_type = "short" if str(row.get("layer", "")) == "short_attack" else "midlong"
        action = short_term_action_label(row) if watch_type == "short" else midlong_action_label(row)
        lines.append(
            f"• {messages.format_ticker_name(row)}｜{messages.layer_label(str(row.get('layer', '')))}\n"
            f"  初步看法：{action}｜{messages.volatility_badge_text(row)}｜{row['regime']}"
        )
    return lines


def build_macro_message(
    market_regime: dict,
    us_market: dict,
    df_rank: pd.DataFrame | None = None,
    *,
    build_market_scenario: Callable[[dict, dict, pd.DataFrame | None], dict],
    heat_bias_message: Callable[[pd.DataFrame | None, dict], str],
    correction_sample_warning_message: Callable[[dict], str],
    runtime_context_lines: Callable[[], list[str]],
    build_candidate_sets: CandidateSetsBuilder,
    short_term_action_label: Callable[[pd.Series], str],
    midlong_action_label: Callable[[pd.Series], str],
    auto_added_tickers: Iterable[str],
    new_watch_spotlight_limit: int,
    prev_rank_csv: Path,
) -> str:
    scenario = build_market_scenario(market_regime, us_market, df_rank)
    scenario_lines = subscriber_scenario_lines(scenario)
    lines = [
        "📌 大盤 / 美股摘要",
        scenario_lines[0],
        scenario_lines[1],
        scenario_lines[2],
        "",
        f"台股：{_compact_text(market_regime['comment'])}",
        f"美股：{_compact_text(us_market['summary'])}",
        "",
        f"盤勢情境：{scenario['label']}｜{scenario['stance']}",
        f"操作重點：{scenario['focus']}",
        f"出場提醒：{scenario['exit_note']}",
    ]
    heat_bias = heat_bias_message(df_rank, scenario)
    if heat_bias:
        lines.append(heat_bias)
    correction_note = correction_sample_warning_message(scenario)
    if correction_note:
        lines.append(correction_note)
    lines.extend(runtime_context_lines())
    if us_market.get("tech_bias"):
        lines.append(us_market["tech_bias"])
    auto_added_tickers = list(auto_added_tickers)
    if auto_added_tickers:
        lines.append(f"持股同步加入觀察清單：{', '.join(auto_added_tickers)}")
    if df_rank is not None and not df_rank.empty:
        top_names = "、".join(messages.format_ticker_name(row) for _, row in df_rank.head(min(len(df_rank), 3)).iterrows())
        lines.extend(["", f"快速判讀：先看 {top_names}；能不能出手看後續名單。"])
        lines.extend(
            messages.compact_briefing_lines(
                df_rank,
                market_regime,
                us_market,
                build_candidate_sets=build_candidate_sets,
                short_term_action_label=short_term_action_label,
                midlong_action_label=midlong_action_label,
            )
        )
    lines.extend(
        new_watchlist_spotlight_lines(
            df_rank,
            new_watch_spotlight_limit=new_watch_spotlight_limit,
            prev_rank_csv=prev_rank_csv,
            short_term_action_label=short_term_action_label,
            midlong_action_label=midlong_action_label,
        )
    )
    return "\n".join(lines).strip()


def build_simple_macro_message(
    market_regime: dict,
    us_market: dict,
    df_rank: pd.DataFrame | None = None,
    *,
    build_market_scenario: Callable[[dict, dict, pd.DataFrame | None], dict],
) -> str:
    scenario = build_market_scenario(market_regime, us_market, df_rank)
    scenario_lines = subscriber_scenario_lines(scenario)
    lines = [
        "📌 今日盤勢精簡版",
        scenario_lines[0],
        scenario_lines[1],
        "",
        f"台股：{_compact_text(market_regime.get('comment', ''), limit=64)}",
        f"美股：{_compact_text(us_market.get('summary', ''), limit=64)}",
        f"重點：{scenario['focus']}",
    ]
    return "\n".join(lines).strip()


def build_portfolio_message(
    df_rank: pd.DataFrame,
    market_regime: dict | None = None,
    us_market: dict | None = None,
    *,
    build_portfolio_review_df: Callable[[pd.DataFrame, dict | None, dict | None], pd.DataFrame],
    build_market_scenario: Callable[[dict, dict, pd.DataFrame], dict],
    heat_bias_message: Callable[[pd.DataFrame | None, dict], str],
) -> str:
    review = build_portfolio_review_df(df_rank, market_regime, us_market)
    lines = ["💼 持股檢查"]
    if market_regime is not None and us_market is not None:
        scenario = build_market_scenario(market_regime, us_market, df_rank)
        lines.append(f"持股節奏：{scenario['label']}｜{scenario['stance']}")
        lines.append(f"重點：{scenario['exit_note']}")
        heat_bias = heat_bias_message(df_rank, scenario)
        if heat_bias:
            lines.append(heat_bias)
    if review.empty:
        lines.append("portfolio.csv 目前沒有可分析的持股。")
        return "\n".join(lines)

    for _, row in review.iterrows():
        current_close = row.get("current_close")
        if pd.isna(current_close):
            lines.append(f"{row['ticker'].split('.')[0]} {row['advice']} | 尚未抓到行情，已同步加入觀察清單")
            continue
        vol_text = messages.volatility_badge_text(row)
        lines.append(
            f"• {row['name']} ({row['ticker'].split('.')[0]})｜{row['holding_style']}｜{row['advice']}\n"
            f"  現價 {round(float(current_close), 2)}｜成本 {round(float(row['avg_cost']), 2)}｜{vol_text}\n"
            f"  報酬 {row['unrealized_pnl_pct']}% / 目標 {row['target_profit_pct']}%\n"
            f"  買賣帶：{row.get('price_plan', '')}"
        )
    return "\n".join(lines).strip()
