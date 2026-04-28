from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import pandas as pd

from stock_watch.paths import REPO_ROOT


def _load_legacy_daily_workflow():
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    import daily_theme_watchlist

    return daily_theme_watchlist


def _timed_call(step_timings: dict[str, float], name: str, func, *args, **kwargs):
    started = time.perf_counter()
    try:
        return func(*args, **kwargs)
    finally:
        step_timings[name] = time.perf_counter() - started


def _build_runtime_metrics_markdown(
    *,
    generated_at: str,
    status: str,
    step_timings: dict[str, float],
    warnings: list[str],
    wall_seconds: float,
) -> str:
    lines = [
        "# Portfolio Runtime Metrics",
        f"- Generated: {generated_at}",
        f"- Status: `{status}`",
        "",
        "## Steps",
        "",
        "| Step | Seconds |",
        "| --- | --- |",
    ]
    for name, seconds in step_timings.items():
        lines.append(f"| {name} | {seconds:.4f} |")
    lines.extend(
        [
            "",
            f"- Total tracked seconds: `{sum(step_timings.values()):.3f}`",
            f"- Wall-clock seconds: `{wall_seconds:.3f}`",
        ]
    )
    if warnings:
        lines.extend(["", "## Warnings", ""])
        for warning in warnings:
            lines.append(f"- {warning}")
    return "\n".join(lines)


def _write_runtime_metrics(
    *,
    runtime_metrics_md: Path | None,
    runtime_metrics_json: Path | None,
    status: str,
    step_timings: dict[str, float],
    warnings: list[str],
    wall_seconds: float,
) -> None:
    if runtime_metrics_md is None and runtime_metrics_json is None:
        return
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    payload = {
        "generated_at": generated_at,
        "status": status,
        "step_timings": step_timings,
        "warnings": warnings,
        "total_seconds": round(sum(step_timings.values()), 3),
        "wall_seconds": round(wall_seconds, 3),
    }
    if runtime_metrics_json is not None:
        runtime_metrics_json.parent.mkdir(parents=True, exist_ok=True)
        runtime_metrics_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if runtime_metrics_md is not None:
        runtime_metrics_md.parent.mkdir(parents=True, exist_ok=True)
        runtime_metrics_md.write_text(
            _build_runtime_metrics_markdown(
                generated_at=generated_at,
                status=status,
                step_timings=step_timings,
                warnings=warnings,
                wall_seconds=wall_seconds,
            ),
            encoding="utf-8",
        )


def run_portfolio_check(
    *,
    portfolio: pd.DataFrame,
    base_strategy,
    logger,
    get_market_regime: Callable[[], dict],
    get_us_market_reference: Callable[[], dict],
    build_market_scenario: Callable[[dict, dict], dict],
    adjust_strategy_by_scenario: Callable,
    run_watchlist: Callable[..., pd.DataFrame],
    save_portfolio_reports: Callable[[pd.DataFrame, dict, dict], None],
    build_macro_message: Callable[[dict, dict, pd.DataFrame], str],
    build_portfolio_message: Callable[[pd.DataFrame, dict, dict], str],
    runtime_metrics_md: Path | None = None,
    runtime_metrics_json: Path | None = None,
    print_fn: Callable[..., None] = print,
    stderr = sys.stderr,
) -> int:
    started = time.perf_counter()
    step_timings: dict[str, float] = {}
    if portfolio.empty:
        logger.info("Portfolio is empty. Skip portfolio check.")
        print_fn("portfolio.csv 目前沒有可分析的持股。")
        _write_runtime_metrics(
            runtime_metrics_md=runtime_metrics_md,
            runtime_metrics_json=runtime_metrics_json,
            status="ok",
            step_timings=step_timings,
            warnings=[],
            wall_seconds=time.perf_counter() - started,
        )
        return 0

    warnings: list[str] = []
    market_regime: dict
    us_market: dict

    try:
        market_regime = _timed_call(step_timings, "market_regime", get_market_regime)
    except Exception as exc:
        warnings.append(f"market_regime: {exc}")
        logger.exception("Market regime fetch failed (best effort): %s", exc)
        market_regime = {
            "comment": "加權指數資料抓不到（best effort）",
            "is_bullish": True,
            "ret20_pct": 0.0,
            "volume_ratio20": 1.0,
            "session_phase": "postclose",
        }

    try:
        us_market = _timed_call(step_timings, "us_market", get_us_market_reference)
    except Exception as exc:
        warnings.append(f"us_market: {exc}")
        logger.exception("US market reference failed (best effort): %s", exc)
        us_market = {"summary": "美股參考暫時抓不到（best effort）。", "rows": []}

    try:
        initial_scenario = build_market_scenario(market_regime, us_market)
        adjusted_strat = adjust_strategy_by_scenario(base_strategy, initial_scenario)
        df_rank = _timed_call(step_timings, "watchlist", run_watchlist, strat=adjusted_strat)
    except Exception as exc:
        warnings.append(f"watchlist: {exc}")
        logger.exception("Watchlist scan failed (best effort): %s", exc)
        df_rank = pd.DataFrame()

    _timed_call(step_timings, "reports", save_portfolio_reports, df_rank, market_regime, us_market)

    macro_message = _timed_call(step_timings, "macro_message", build_macro_message, market_regime, us_market, df_rank)
    portfolio_message = _timed_call(
        step_timings, "portfolio_message", build_portfolio_message, df_rank, market_regime, us_market
    )

    if warnings:
        print_fn("⚠️ Best effort: 部分資料抓取失敗，已用可用資料輸出。", file=stderr)
        for warning in warnings:
            print_fn(f"- {warning}", file=stderr)

    def _print_outputs() -> None:
        print_fn(macro_message)
        print_fn()
        print_fn(portfolio_message)

    _timed_call(step_timings, "print_output", _print_outputs)
    _write_runtime_metrics(
        runtime_metrics_md=runtime_metrics_md,
        runtime_metrics_json=runtime_metrics_json,
        status="ok",
        step_timings=step_timings,
        warnings=warnings,
        wall_seconds=time.perf_counter() - started,
    )
    logger.debug("Portfolio review printed to CLI and reports saved.")
    return 0


def run_default_portfolio_check(
    *,
    runtime_metrics_md: Path | None = None,
    runtime_metrics_json: Path | None = None,
    print_fn: Callable[..., None] = print,
    stderr = sys.stderr,
) -> int:
    daily_theme_watchlist = _load_legacy_daily_workflow()
    try:
        return run_portfolio_check(
            portfolio=daily_theme_watchlist.PORTFOLIO,
            base_strategy=daily_theme_watchlist.CONFIG.strategy,
            logger=daily_theme_watchlist.logger,
            get_market_regime=daily_theme_watchlist.get_market_regime,
            get_us_market_reference=daily_theme_watchlist.get_us_market_reference,
            build_market_scenario=daily_theme_watchlist.build_market_scenario,
            adjust_strategy_by_scenario=daily_theme_watchlist.adjust_strategy_by_scenario,
            run_watchlist=daily_theme_watchlist.run_watchlist,
            save_portfolio_reports=daily_theme_watchlist.save_portfolio_reports,
            build_macro_message=daily_theme_watchlist.build_macro_message,
            build_portfolio_message=daily_theme_watchlist.build_portfolio_message,
            runtime_metrics_md=runtime_metrics_md,
            runtime_metrics_json=runtime_metrics_json,
            print_fn=print_fn,
            stderr=stderr,
        )
    except Exception as exc:
        err_msg = f"Portfolio check failed: {exc}"
        daily_theme_watchlist.logger.exception(err_msg)
        print_fn(err_msg, file=stderr)
        return 1
