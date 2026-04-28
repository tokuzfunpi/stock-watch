from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

import pandas as pd

from stock_watch.reports.common import dataframe_to_html


def build_portfolio_report_markdown(
    df_rank: pd.DataFrame,
    market_regime: dict,
    us_market: dict,
    *,
    build_portfolio_review_df: Callable[[pd.DataFrame, dict, dict], pd.DataFrame],
    build_market_scenario: Callable[[dict, dict, pd.DataFrame], dict],
    realtime_quote_interval: str,
    realtime_quotes_enabled: bool,
    auto_added_tickers: Iterable[str],
    volatility_badge_text: Callable[[pd.Series], str],
) -> str:
    review = build_portfolio_review_df(df_rank, market_regime, us_market)
    scenario = build_market_scenario(market_regime, us_market, df_rank)
    today = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    quote_line = f"- Quote: realtime({realtime_quote_interval}) if available, else daily close"
    if not realtime_quotes_enabled:
        quote_line = "- Quote: daily close (realtime disabled)"
    lines = [
        "# Portfolio Review",
        f"- Generated: {today}",
        f"- Market Regime: {market_regime['comment']}",
        f"- US Summary: {us_market['summary']}",
        f"- Market Scenario: {scenario['label']} | {scenario['stance']}",
        f"- Exit Focus: {scenario['exit_note']}",
        quote_line,
        "",
    ]
    auto_added_tickers = list(auto_added_tickers)
    if auto_added_tickers:
        lines.append(f"- Auto-added to watchlist: {', '.join(auto_added_tickers)}")
        lines.append("")

    lines.extend(["## Holdings", ""])
    if review.empty:
        lines.append("- None")
        return "\n".join(lines)

    for _, row in review.iterrows():
        current_close = row.get("current_close")
        if pd.isna(current_close):
            lines.append(f"- {row['ticker'].split('.')[0]} | {row['advice']} | 尚未抓到行情，已同步加入觀察清單")
            continue
        lines.append(
            f"- {row['name']} ({row['ticker'].split('.')[0]}) | {row['holding_style']} | 現價 {round(float(current_close), 2)} | "
            f"成本 {round(float(row['avg_cost']), 2)} | 報酬 {row['unrealized_pnl_pct']}% | "
            f"目標 {row['target_profit_pct']}% | 波動 {volatility_badge_text(row)} | 建議 {row['advice']} | "
            f"價格帶 {row.get('price_plan', '')}"
        )
    return "\n".join(lines)


def build_portfolio_report_html(
    df_rank: pd.DataFrame,
    market_regime: dict,
    us_market: dict,
    *,
    build_portfolio_review_df: Callable[[pd.DataFrame, dict, dict], pd.DataFrame],
    build_market_scenario: Callable[[dict, dict, pd.DataFrame], dict],
    auto_added_tickers: Iterable[str],
) -> str:
    review = build_portfolio_review_df(df_rank, market_regime, us_market)
    scenario = build_market_scenario(market_regime, us_market, df_rank)
    review_html = "<p>None</p>" if review.empty else dataframe_to_html(review)
    auto_added_tickers = list(auto_added_tickers)
    auto_added_html = ""
    if auto_added_tickers:
        auto_added_html = f"<p><strong>Auto-added to watchlist:</strong> {', '.join(auto_added_tickers)}</p>"
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Portfolio Review</title>
<style>
body {{ font-family: Arial, sans-serif; margin: 24px; }}
table {{ border-collapse: collapse; width: 100%; margin-bottom: 24px; }}
th, td {{ border: 1px solid #ddd; padding: 8px; font-size: 14px; }}
th {{ background: #f4f4f4; }}
</style></head><body>
<h1>Portfolio Review</h1>
<p><strong>Market:</strong> {market_regime['comment']}</p>
<p><strong>US Summary:</strong> {us_market['summary']}</p>
<p><strong>Scenario:</strong> {scenario['label']} | {scenario['stance']}</p>
<p><strong>Exit Focus:</strong> {scenario['exit_note']}</p>
{auto_added_html}
<h2>Holdings</h2>{review_html}
</body></html>"""


def save_portfolio_reports(
    df_rank: pd.DataFrame,
    market_regime: dict,
    us_market: dict,
    *,
    markdown_path: Path,
    html_path: Path,
    build_portfolio_review_df: Callable[[pd.DataFrame, dict, dict], pd.DataFrame],
    build_market_scenario: Callable[[dict, dict, pd.DataFrame], dict],
    realtime_quote_interval: str,
    realtime_quotes_enabled: bool,
    auto_added_tickers: Iterable[str],
    volatility_badge_text: Callable[[pd.Series], str],
) -> None:
    markdown_path.write_text(
        build_portfolio_report_markdown(
            df_rank,
            market_regime,
            us_market,
            build_portfolio_review_df=build_portfolio_review_df,
            build_market_scenario=build_market_scenario,
            realtime_quote_interval=realtime_quote_interval,
            realtime_quotes_enabled=realtime_quotes_enabled,
            auto_added_tickers=auto_added_tickers,
            volatility_badge_text=volatility_badge_text,
        ),
        encoding="utf-8",
    )
    html_path.write_text(
        build_portfolio_report_html(
            df_rank,
            market_regime,
            us_market,
            build_portfolio_review_df=build_portfolio_review_df,
            build_market_scenario=build_market_scenario,
            auto_added_tickers=auto_added_tickers,
        ),
        encoding="utf-8",
    )
