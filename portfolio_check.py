from __future__ import annotations

import sys

import pandas as pd

from daily_theme_watchlist import (
    CONFIG,
    PORTFOLIO,
    adjust_strategy_by_scenario,
    build_macro_message,
    build_market_scenario,
    build_portfolio_message,
    get_market_regime,
    get_us_market_reference,
    logger,
    run_watchlist,
    save_portfolio_reports,
)


def main() -> int:
    try:
        if PORTFOLIO.empty:
            logger.info("Portfolio is empty. Skip portfolio check.")
            print("portfolio.csv 目前沒有可分析的持股。")
            return 0

        warnings: list[str] = []
        market_regime: dict
        us_market: dict

        try:
            market_regime = get_market_regime()
        except Exception as exc:
            warnings.append(f"market_regime: {exc}")
            logger.exception("Market regime fetch failed (best effort): %s", exc)
            market_regime = {"comment": "加權指數資料抓不到（best effort）"}

        try:
            us_market = get_us_market_reference()
        except Exception as exc:
            warnings.append(f"us_market: {exc}")
            logger.exception("US market reference failed (best effort): %s", exc)
            us_market = {"summary": "美股參考暫時抓不到（best effort）。", "rows": []}

        # Point 3: Scenario-aware thresholds
        initial_scenario = build_market_scenario(market_regime, us_market)
        adjusted_strat = adjust_strategy_by_scenario(CONFIG.strategy, initial_scenario)

        try:
            df_rank = run_watchlist(strat=adjusted_strat)
        except Exception as exc:
            warnings.append(f"watchlist: {exc}")
            logger.exception("Watchlist scan failed (best effort): %s", exc)
            df_rank = pd.DataFrame()

        save_portfolio_reports(df_rank, market_regime, us_market)

        macro_message = build_macro_message(market_regime, us_market, df_rank)
        portfolio_message = build_portfolio_message(df_rank, market_regime, us_market)

        if warnings:
            print("⚠️ Best effort: 部分資料抓取失敗，已用可用資料輸出。", file=sys.stderr)
            for w in warnings:
                print(f"- {w}", file=sys.stderr)

        print(macro_message)
        print()
        print(portfolio_message)
        logger.debug("Portfolio review printed to CLI and reports saved.")
        return 0
    except Exception as exc:
        err_msg = f"Portfolio check failed: {exc}"
        logger.exception(err_msg)
        print(err_msg, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
