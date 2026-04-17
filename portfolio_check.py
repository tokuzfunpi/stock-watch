from __future__ import annotations

import sys

from daily_theme_watchlist import (
    PORTFOLIO,
    build_macro_message,
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

        market_regime = get_market_regime()
        us_market = get_us_market_reference()
        df_rank = run_watchlist()
        save_portfolio_reports(df_rank, market_regime, us_market)

        macro_message = build_macro_message(market_regime, us_market)
        portfolio_message = build_portfolio_message(df_rank)

        print(macro_message)
        print()
        print(portfolio_message)
        logger.info("Portfolio review printed to CLI and reports saved.")
        return 0
    except Exception as exc:
        err_msg = f"Portfolio check failed: {exc}"
        logger.exception(err_msg)
        print(err_msg, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
