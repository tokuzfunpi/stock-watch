from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import pandas as pd

from stock_watch.reports.common import dataframe_to_html


def build_daily_report_markdown(
    df_rank: pd.DataFrame,
    market_regime: dict,
    bt_steady: Optional[pd.DataFrame],
    bt_attack: Optional[pd.DataFrame],
    *,
    us_market: Optional[dict],
    build_market_scenario: Callable[[dict, dict, pd.DataFrame], dict],
    layer_label: Callable[[str], str],
    build_candidate_sets: Callable[[pd.DataFrame, dict, dict], tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]],
    build_feedback_summary: Callable[[], pd.DataFrame],
    watch_price_plan_text: Callable[[pd.Series, str], str],
    select_special_etf_candidates: Callable[[pd.DataFrame], pd.DataFrame],
    build_special_etf_summary: Callable[[pd.DataFrame], list[str]],
    special_etf_action_label: Callable[[pd.Series], str],
    select_early_gem_candidates: Callable[[pd.DataFrame], pd.DataFrame],
    early_gem_reason: Callable[[pd.Series], str],
    strategy_preview_lines: Callable[[object, dict], list[str]],
    config_strategy,
    alert_track_csv: Path,
) -> str:
    today = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    scenario = build_market_scenario(market_regime, us_market or {}, df_rank)
    lines = [
        "# Daily 20D v2.2 Attack Report",
        f"- Generated: {today}",
        f"- Market Regime: {market_regime['comment']}",
        "",
        "## Top Ranking",
        "",
        "| 排名 | 等級 | 股票 | 分類 | 近況 | 5日 | 20日 | 波動 | 投機風險 | 重點 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for _, row in df_rank.iterrows():
        lines.append(
            f"| {int(row['rank'])} | {row['grade']} | {row['name']} ({row['ticker']}) | "
            f"{layer_label(row['layer'])} | {row['regime']} | "
            f"{row['ret5_pct']}% | {row['ret20_pct']}% | {row.get('volatility_tag', '')} ({row.get('atr_pct', '')}%) | {row['spec_risk_label']} | "
            f"{row['signals']} / 量比 {row['volume_ratio20']} |"
        )

    short_candidates, short_backups, midlong_candidates, midlong_backups = build_candidate_sets(
        df_rank,
        market_regime,
        us_market or {},
    )
    feedback_summary = build_feedback_summary()

    lines.extend(["", "## Short-Term Candidates", ""])
    if short_candidates.empty:
        lines.append("- None")
    else:
        for _, row in short_candidates.iterrows():
            lines.append(
                f"- #{int(row['rank'])} {row['name']} {row['ticker']} [{row['group']}/{layer_label(row['layer'])}] | "
                f"setup {row['setup_score']} risk {row['risk_score']} | "
                f"5D {row['ret5_pct']}% 10D {row['ret10_pct']}% 20D {row['ret20_pct']}% | 投機 {row['spec_risk_label']} | "
                f"{row['signals']} | rankΔ {int(row['rank_change']):+d} setupΔ {int(row['setup_change']):+d}"
            )

    lines.extend(["", "## Short-Term Backups", ""])
    if short_backups.empty:
        lines.append("- None")
    else:
        for _, row in short_backups.iterrows():
            lines.append(
                f"- #{int(row['rank'])} {row['name']} {row['ticker']} [{row['group']}/{layer_label(row['layer'])}] | "
                f"setup {row['setup_score']} risk {row['risk_score']} | "
                f"5D {row['ret5_pct']}% 10D {row['ret10_pct']}% 20D {row['ret20_pct']}% | 投機 {row['spec_risk_label']} | "
                f"{row['signals']} | rankΔ {int(row['rank_change']):+d} setupΔ {int(row['setup_change']):+d} | "
                f"{watch_price_plan_text(row, 'short')}"
            )

    lines.extend(["", "## Mid-Long Candidates", ""])
    if midlong_candidates.empty:
        lines.append("- None")
    else:
        for _, row in midlong_candidates.iterrows():
            lines.append(
                f"- #{int(row['rank'])} {row['name']} {row['ticker']} [{row['group']}/{layer_label(row['layer'])}] | "
                f"setup {row['setup_score']} risk {row['risk_score']} | "
                f"10D {row['ret10_pct']}% 20D {row['ret20_pct']}% | 投機 {row['spec_risk_label']} | "
                f"{row['signals']} | rankΔ {int(row['rank_change']):+d} setupΔ {int(row['setup_change']):+d}"
            )

    lines.extend(["", "## Mid-Long Backups", ""])
    if midlong_backups.empty:
        lines.append("- None")
    else:
        for _, row in midlong_backups.iterrows():
            lines.append(
                f"- #{int(row['rank'])} {row['name']} {row['ticker']} [{row['group']}/{layer_label(row['layer'])}] | "
                f"setup {row['setup_score']} risk {row['risk_score']} | "
                f"10D {row['ret10_pct']}% 20D {row['ret20_pct']}% | 投機 {row['spec_risk_label']} | "
                f"{row['signals']} | rankΔ {int(row['rank_change']):+d} setupΔ {int(row['setup_change']):+d} | "
                f"{watch_price_plan_text(row, 'midlong')}"
            )

    special_etf_candidates = select_special_etf_candidates(df_rank)
    gem_candidates = select_early_gem_candidates(df_rank)
    lines.extend(["", "## ETF / 債券觀察", ""])
    if special_etf_candidates.empty:
        lines.append("- None")
    else:
        for summary in build_special_etf_summary(special_etf_candidates):
            lines.append(f"- {summary}")
        lines.append("")
        for _, row in special_etf_candidates.iterrows():
            lines.append(
                f"- #{int(row['rank'])} {row['name']} {row['ticker']} [{row['group']}/{layer_label(row['layer'])}] | "
                f"setup {row['setup_score']} risk {row['risk_score']} | "
                f"5D {row['ret5_pct']}% 20D {row['ret20_pct']}% | 投機 {row['spec_risk_label']} | "
                f"{row['signals']} | 操作 {special_etf_action_label(row)}"
            )

    lines.extend(["", "## Early Gem Watch", ""])
    if gem_candidates.empty:
        lines.append("- None")
    else:
        for _, row in gem_candidates.iterrows():
            lines.append(
                f"- #{int(row['rank'])} {row['name']} {row['ticker']} [{row['group']}/{layer_label(row['layer'])}] | "
                f"setup {row['setup_score']} risk {row['risk_score']} | "
                f"5D {row['ret5_pct']}% 20D {row['ret20_pct']}% | 投機 {row['spec_risk_label']} | "
                f"{row['signals']} | 理由 {early_gem_reason(row)} | {watch_price_plan_text(row, 'short')}"
            )

    lines.extend(["", "## Prediction Feedback", ""])
    if feedback_summary.empty:
        lines.append("- None")
    else:
        lines.append("| 類型 | 操作 | 樣本 | 勝率 | 平均報酬 | 盈虧比 | 回饋分數 | 判讀 |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
        for _, row in feedback_summary.iterrows():
            action_label = "整體" if str(row["action_label"]) == "__all__" else str(row["action_label"])
            watch_type = "短線" if str(row["watch_type"]) == "short" else "中長線"
            lines.append(
                f"| {watch_type} | {action_label} | {int(row['samples'])} | {row['win_rate_pct']}% | "
                f"{row['avg_return_pct']}% | {row['pl_ratio']} | {row['feedback_score']} | {row['feedback_label']} |"
            )

    lines.extend(["", "## Adaptive Strategy Adjustments", ""])
    lines.append(f"- 情境：{scenario['label']} | 目前節奏：{scenario['stance']}")
    lines.extend(strategy_preview_lines(config_strategy, scenario))

    lines.extend([
        "",
        "## Grade 對照表",
        "",
        "- `A`：這檔現在最值得看，通常代表結構、量能、動能都有對上。",
        "- `B`：有在轉強，但還沒有強到非看不可，適合放進追蹤名單。",
        "- `C`：不是不能漲，而是現在風險偏高，容易追在不舒服的位置。",
        "- `X`：目前沒有足夠清楚的優勢，先觀察就好。",
        "",
        "## Signals 對照表",
        "",
        "- `BASE`：低檔整理後，股價還沒真正噴出，但看起來有在慢慢打底。",
        "- `REBREAK`：前面壓著的均線重新站上去，而且量也開始放大，常見在第二波重新轉強。",
        "- `SURGE`：這段時間漲幅已經很明顯，量也大，代表市場資金真的有在追。",
        "- `TREND`：不是突然暴衝，而是沿著趨勢穩穩往上走，比較像中段延續。",
        "- `ACCEL`：最近 5 天或 10 天速度變快，通常是剛開始被市場注意到的加速段。",
        "- `PULLBACK`：前面漲過一段後，現在在拉回整理，不一定壞，但短線不是最舒服的位置。",
        "",
        "## Regime 解釋",
        "",
        "- `有點過熱，別硬追`：漲太快、乖離太大，容易追在短線高點。",
        "- `題材正在發酵`：市場開始聚焦這檔，量價有一起上來，屬於比較有熱度的階段。",
        "- `重新站上來了`：整理過後再次轉強，這種型態常常是比較漂亮的重新發動。",
        "- `轉強速度有出來`：還不一定是最強主升段，但動能有在加速，值得盯。",
        "- `中段延續中`：這檔不是剛起漲，而是已經走在趨勢裡，偏中波段續強。",
        "- `低檔慢慢墊高`：還在打底或剛離開底部，適合先放觀察名單，不一定要急著追。",
        "- `高檔拉回整理`：先前強過，但現在進入整理區，重點是看能不能整理完再上。",
        "- `還在觀察`：目前沒有特別明確的訊號，先不用太急。",
    ])

    for title, backtest in [("Steady Backtest", bt_steady), ("Attack Backtest", bt_attack)]:
        lines.extend(["", f"## {title}", ""])
        if backtest is None or backtest.empty:
            lines.append("- None")
        else:
            lines.append("| Horizon | Trades | Win Rate | Avg Return | Median Return |")
            lines.append("| --- | --- | --- | --- | --- |")
            for _, row in backtest.iterrows():
                lines.append(
                    f"| {int(row['horizon'])}D | {int(row['trades'])} | {row['win_rate_pct']}% | "
                    f"{row['avg_return_pct']}% | {row['median_return_pct']}% |"
                )

    if alert_track_csv.exists():
        try:
            alert_df = pd.read_csv(alert_track_csv)
            if not alert_df.empty:
                recent = alert_df.tail(10)
                lines.extend(["", "## Recent Alert Tracking", ""])
                lines.append("| Alert Date | Type | Ticker | Name | Grade | 1D% | 5D% | 20D% | Status |")
                lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
                for _, row in recent.iterrows():
                    lines.append(
                        f"| {row.get('alert_date','')} | {row.get('watch_type','')} | {row.get('ticker','')} | {row.get('name','')} | {row.get('grade','')} | "
                        f"{row.get('ret1_future_pct','')} | {row.get('ret5_future_pct','')} | {row.get('ret20_future_pct','')} | {row.get('status','')} |"
                    )
        except Exception:
            pass

    return "\n".join(lines)


def build_daily_report_html(
    df_rank: pd.DataFrame,
    market_regime: dict,
    bt_steady: Optional[pd.DataFrame],
    bt_attack: Optional[pd.DataFrame],
    *,
    us_market: Optional[dict],
    build_market_scenario: Callable[[dict, dict, pd.DataFrame], dict],
    build_candidate_sets: Callable[[pd.DataFrame, dict, dict], tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]],
    select_special_etf_candidates: Callable[[pd.DataFrame], pd.DataFrame],
    select_early_gem_candidates: Callable[[pd.DataFrame], pd.DataFrame],
    build_feedback_summary: Callable[[], pd.DataFrame],
    strategy_preview_lines: Callable[[object, dict], list[str]],
    config_strategy,
) -> str:
    steady_html = "<p>None</p>" if bt_steady is None or bt_steady.empty else dataframe_to_html(bt_steady)
    attack_html = "<p>None</p>" if bt_attack is None or bt_attack.empty else dataframe_to_html(bt_attack)
    short_candidates, short_backups, midlong_candidates, midlong_backups = build_candidate_sets(
        df_rank,
        market_regime,
        us_market or {},
    )
    short_html = "<p>None</p>" if short_candidates.empty else dataframe_to_html(short_candidates)
    short_backup_html = "<p>None</p>" if short_backups.empty else dataframe_to_html(short_backups)
    midlong_html = "<p>None</p>" if midlong_candidates.empty else dataframe_to_html(midlong_candidates)
    midlong_backup_html = "<p>None</p>" if midlong_backups.empty else dataframe_to_html(midlong_backups)
    special_etf_candidates = select_special_etf_candidates(df_rank)
    special_etf_html = "<p>None</p>" if special_etf_candidates.empty else dataframe_to_html(special_etf_candidates)
    gem_candidates = select_early_gem_candidates(df_rank)
    gem_html = "<p>None</p>" if gem_candidates.empty else dataframe_to_html(gem_candidates)
    feedback_summary = build_feedback_summary()
    feedback_html = "<p>None</p>" if feedback_summary.empty else dataframe_to_html(feedback_summary)
    scenario = build_market_scenario(market_regime, us_market or {}, df_rank)
    adaptive_preview_html = "<br>".join(
        [f"情境：{scenario['label']} | 目前節奏：{scenario['stance']}"] + strategy_preview_lines(config_strategy, scenario)
    )
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Daily 20D v2.2 Attack Report</title>
<style>
body {{ font-family: Arial, sans-serif; margin: 24px; }}
table {{ border-collapse: collapse; width: 100%; margin-bottom: 24px; }}
th, td {{ border: 1px solid #ddd; padding: 8px; font-size: 14px; }}
th {{ background: #f4f4f4; }}
</style></head><body>
<h1>Daily 20D v2.2 Attack Report</h1>
<p><strong>Market:</strong> {market_regime['comment']}</p>
<h2>Top Ranking</h2>{dataframe_to_html(df_rank)}
<h2>Short-Term Candidates</h2>{short_html}
<h2>Short-Term Backups</h2>{short_backup_html}
<h2>Mid-Long Candidates</h2>{midlong_html}
<h2>Mid-Long Backups</h2>{midlong_backup_html}
<h2>ETF / 債券觀察</h2>{special_etf_html}
<h2>Early Gem Watch</h2>{gem_html}
<h2>Prediction Feedback</h2>{feedback_html}
<h2>Adaptive Strategy Adjustments</h2><p>{adaptive_preview_html}</p>
<h2>Steady Backtest</h2>{steady_html}
<h2>Attack Backtest</h2>{attack_html}
</body></html>"""


def save_reports(
    df_rank: pd.DataFrame,
    market_regime: dict,
    bt_steady: Optional[pd.DataFrame],
    bt_attack: Optional[pd.DataFrame],
    *,
    markdown_path: Path,
    html_path: Path,
    us_market: Optional[dict],
    build_market_scenario: Callable[[dict, dict, pd.DataFrame], dict],
    layer_label: Callable[[str], str],
    build_candidate_sets: Callable[[pd.DataFrame, dict, dict], tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]],
    build_feedback_summary: Callable[[], pd.DataFrame],
    watch_price_plan_text: Callable[[pd.Series, str], str],
    select_special_etf_candidates: Callable[[pd.DataFrame], pd.DataFrame],
    build_special_etf_summary: Callable[[pd.DataFrame], list[str]],
    special_etf_action_label: Callable[[pd.Series], str],
    select_early_gem_candidates: Callable[[pd.DataFrame], pd.DataFrame],
    early_gem_reason: Callable[[pd.Series], str],
    strategy_preview_lines: Callable[[object, dict], list[str]],
    config_strategy,
    alert_track_csv: Path,
) -> None:
    markdown_path.write_text(
        build_daily_report_markdown(
            df_rank,
            market_regime,
            bt_steady,
            bt_attack,
            us_market=us_market,
            build_market_scenario=build_market_scenario,
            layer_label=layer_label,
            build_candidate_sets=build_candidate_sets,
            build_feedback_summary=build_feedback_summary,
            watch_price_plan_text=watch_price_plan_text,
            select_special_etf_candidates=select_special_etf_candidates,
            build_special_etf_summary=build_special_etf_summary,
            special_etf_action_label=special_etf_action_label,
            select_early_gem_candidates=select_early_gem_candidates,
            early_gem_reason=early_gem_reason,
            strategy_preview_lines=strategy_preview_lines,
            config_strategy=config_strategy,
            alert_track_csv=alert_track_csv,
        ),
        encoding="utf-8",
    )
    html_path.write_text(
        build_daily_report_html(
            df_rank,
            market_regime,
            bt_steady,
            bt_attack,
            us_market=us_market,
            build_market_scenario=build_market_scenario,
            build_candidate_sets=build_candidate_sets,
            select_special_etf_candidates=select_special_etf_candidates,
            select_early_gem_candidates=select_early_gem_candidates,
            build_feedback_summary=build_feedback_summary,
            strategy_preview_lines=strategy_preview_lines,
            config_strategy=config_strategy,
        ),
        encoding="utf-8",
    )
