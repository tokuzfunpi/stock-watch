from __future__ import annotations

import sys

from stock_watch.workflows.portfolio import run_portfolio_check

from daily_theme_watchlist import (
    CONFIG,
    OUTDIR,
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

PORTFOLIO_RUNTIME_METRICS_MD = OUTDIR / "portfolio_runtime_metrics.md"
PORTFOLIO_RUNTIME_METRICS_JSON = OUTDIR / "portfolio_runtime_metrics.json"


def main() -> int:
    try:
        return run_portfolio_check(
            portfolio=PORTFOLIO,
            base_strategy=CONFIG.strategy,
            logger=logger,
            get_market_regime=get_market_regime,
            get_us_market_reference=get_us_market_reference,
            build_market_scenario=build_market_scenario,
            adjust_strategy_by_scenario=adjust_strategy_by_scenario,
            run_watchlist=run_watchlist,
            save_portfolio_reports=save_portfolio_reports,
            build_macro_message=build_macro_message,
            build_portfolio_message=build_portfolio_message,
            runtime_metrics_md=PORTFOLIO_RUNTIME_METRICS_MD,
            runtime_metrics_json=PORTFOLIO_RUNTIME_METRICS_JSON,
            print_fn=print,
            stderr=sys.stderr,
        )
    except Exception as exc:
        err_msg = f"Portfolio check failed: {exc}"
        logger.exception(err_msg)
        print(err_msg, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
