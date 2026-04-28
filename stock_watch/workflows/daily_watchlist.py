from __future__ import annotations

import sys
import time
from pathlib import Path

from stock_watch.paths import REPO_ROOT
from stock_watch.reports import daily as daily_reports
from stock_watch.reports import telegram as telegram_reports
from stock_watch.state import run_state
from stock_watch.strategy import scenario as strategy_scenario
from stock_watch.workflows import runtime_metrics


def _timed_call(step_timings: dict[str, float], name: str, func, *args, **kwargs):
    started = time.perf_counter()
    try:
        return func(*args, **kwargs)
    finally:
        step_timings[name] = time.perf_counter() - started


def _load_legacy_daily_workflow():
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    import daily_theme_watchlist

    return daily_theme_watchlist


def _build_macro_message(daily_theme_watchlist, market_regime: dict, us_market: dict, df_rank):
    return telegram_reports.build_macro_message(
        market_regime,
        us_market,
        df_rank,
        build_market_scenario=strategy_scenario.build_market_scenario,
        heat_bias_message=daily_theme_watchlist.heat_bias_message,
        correction_sample_warning_message=daily_theme_watchlist.correction_sample_warning_message,
        runtime_context_lines=daily_theme_watchlist.runtime_context_lines,
        build_candidate_sets=daily_theme_watchlist.build_candidate_sets,
        short_term_action_label=daily_theme_watchlist.short_term_action_label,
        midlong_action_label=daily_theme_watchlist.midlong_action_label,
        auto_added_tickers=daily_theme_watchlist.AUTO_ADDED_TICKERS,
        new_watch_spotlight_limit=daily_theme_watchlist.CONFIG.scenario_policy.new_watch_spotlight_limit,
        prev_rank_csv=daily_theme_watchlist.PREV_RANK_CSV,
    )


def _build_short_term_message(daily_theme_watchlist, df_rank, market_regime: dict, us_market: dict):
    return telegram_reports.build_short_term_message(
        df_rank,
        market_regime,
        us_market,
        build_candidate_sets=daily_theme_watchlist.build_candidate_sets,
        build_market_scenario=strategy_scenario.build_market_scenario,
        effective_short_top_n=daily_theme_watchlist.effective_short_top_n,
        short_term_action_label=daily_theme_watchlist.short_term_action_label,
        midlong_action_label=daily_theme_watchlist.midlong_action_label,
        watch_price_plan_text=daily_theme_watchlist.watch_price_plan_text,
    )


def _build_early_gem_message(daily_theme_watchlist, df_rank):
    return telegram_reports.build_early_gem_message(
        df_rank,
        select_early_gem_candidates=daily_theme_watchlist.select_early_gem_candidates,
        early_gem_reason=daily_theme_watchlist.early_gem_reason,
        watch_price_plan_text=daily_theme_watchlist.watch_price_plan_text,
    )


def _build_midlong_message(daily_theme_watchlist, df_rank, market_regime: dict, us_market: dict):
    return telegram_reports.build_midlong_message(
        df_rank,
        market_regime,
        us_market,
        build_candidate_sets=daily_theme_watchlist.build_candidate_sets,
        build_market_scenario=strategy_scenario.build_market_scenario,
        effective_midlong_top_n=daily_theme_watchlist.effective_midlong_top_n,
        short_term_action_label=daily_theme_watchlist.short_term_action_label,
        midlong_action_label=daily_theme_watchlist.midlong_action_label,
        watch_price_plan_text=daily_theme_watchlist.watch_price_plan_text,
    )


def _save_reports(daily_theme_watchlist, df_rank, market_regime: dict, bt_steady, bt_attack, us_market: dict) -> None:
    daily_reports.save_reports(
        df_rank,
        market_regime,
        bt_steady,
        bt_attack,
        markdown_path=daily_theme_watchlist.REPORT_MD,
        html_path=daily_theme_watchlist.REPORT_HTML,
        us_market=us_market,
        build_market_scenario=strategy_scenario.build_market_scenario,
        layer_label=daily_theme_watchlist.layer_label,
        build_candidate_sets=daily_theme_watchlist.build_candidate_sets,
        build_feedback_summary=daily_theme_watchlist.build_feedback_summary,
        watch_price_plan_text=daily_theme_watchlist.watch_price_plan_text,
        select_special_etf_candidates=daily_theme_watchlist.select_special_etf_candidates,
        build_special_etf_summary=daily_theme_watchlist.build_special_etf_summary,
        special_etf_action_label=daily_theme_watchlist.special_etf_action_label,
        select_early_gem_candidates=daily_theme_watchlist.select_early_gem_candidates,
        early_gem_reason=daily_theme_watchlist.early_gem_reason,
        strategy_preview_lines=strategy_scenario.strategy_preview_lines,
        config_strategy=daily_theme_watchlist.CONFIG.strategy,
        alert_track_csv=daily_theme_watchlist.ALERT_TRACK_CSV,
    )


def run_daily_watchlist(*, force_run: bool | None = False) -> int:
    daily_theme_watchlist = _load_legacy_daily_workflow()
    main_started = time.perf_counter()
    effective_force_run = daily_theme_watchlist.FORCE_RUN if force_run is None else bool(force_run)
    run_signature_paths = [
        Path(daily_theme_watchlist.__file__),
        daily_theme_watchlist.CONFIG_PATH,
        daily_theme_watchlist.WATCHLIST_CSV,
    ]
    for key in daily_theme_watchlist._CACHE_STATS:
        daily_theme_watchlist._CACHE_STATS[key] = 0
    step_timings: dict[str, float] = {}
    warnings: list[str] = []
    try:
        today = run_state.today_local_str(local_tz=daily_theme_watchlist.LOCAL_TZ)
        run_signature = run_state.current_run_signature(run_signature_paths)
        if (
            not effective_force_run
            and run_state.load_last_success_date(success_file=daily_theme_watchlist.SUCCESS_FILE) == today
            and run_state.load_last_success_signature(success_file=daily_theme_watchlist.SUCCESS_FILE) == run_signature
        ):
            daily_theme_watchlist.logger.info(
                "Already completed successfully for %s with same code/config. Skip duplicate run.",
                today,
            )
            return 0

        try:
            market_regime = _timed_call(
                step_timings, "market_regime", daily_theme_watchlist.get_market_regime
            )
        except Exception as exc:
            warnings.append(f"market_regime: {exc}")
            daily_theme_watchlist.logger.exception("Market regime fetch failed (best effort): %s", exc)
            market_regime = {"comment": "加權指數資料抓不到（best effort）", "is_bullish": True}

        try:
            us_market = _timed_call(
                step_timings, "us_market", daily_theme_watchlist.get_us_market_reference
            )
        except Exception as exc:
            warnings.append(f"us_market: {exc}")
            daily_theme_watchlist.logger.exception("US market reference failed (best effort): %s", exc)
            us_market = {"summary": "美股參考暫時抓不到（best effort）。", "rows": []}

        initial_scenario = strategy_scenario.build_market_scenario(market_regime, us_market)
        adjusted_strat = strategy_scenario.adjust_strategy_by_scenario(
            daily_theme_watchlist.CONFIG.strategy, initial_scenario
        )
        _timed_call(
            step_timings, "cache_warmup", daily_theme_watchlist.prewarm_watchlist_indicator_cache
        )
        df_rank = _timed_call(
            step_timings, "watchlist", daily_theme_watchlist.run_watchlist, strat=adjusted_strat
        )
        bt_steady, bt_attack = _timed_call(
            step_timings, "backtest", daily_theme_watchlist.run_backtest_dual
        )

        daily_theme_watchlist.logger.info("=== 今日排行榜 ===\n%s", df_rank.to_string(index=False))
        daily_theme_watchlist.logger.info("Market regime: %s", market_regime["comment"])

        short_candidates, short_backups, midlong_candidates, _ = _timed_call(
            step_timings,
            "candidate_sets",
            daily_theme_watchlist.build_candidate_sets,
            df_rank,
            market_regime,
            us_market,
        )
        market_scenario = strategy_scenario.build_market_scenario(market_regime, us_market, df_rank)
        _timed_call(
            step_timings,
            "reports",
            _save_reports,
            daily_theme_watchlist,
            df_rank,
            market_regime,
            bt_steady,
            bt_attack,
            us_market,
        )
        try:
            _timed_call(
                step_timings,
                "shadow_observation",
                daily_theme_watchlist.save_open_not_chase_shadow_observations,
                df_rank,
                market_regime,
                us_market,
            )
        except Exception as exc:
            warnings.append(f"shadow_observation: {exc}")
            daily_theme_watchlist.logger.exception("Shadow observation update failed (best effort): %s", exc)
        try:
            _timed_call(
                step_timings,
                "alert_tracking",
                daily_theme_watchlist.upsert_alert_tracking,
                short_candidates,
                midlong_candidates,
                market_scenario,
            )
        except Exception as exc:
            warnings.append(f"alert_tracking: {exc}")
            daily_theme_watchlist.logger.exception("Alert tracking update failed (best effort): %s", exc)
        daily_theme_watchlist.logger.info(
            "Adaptive strategy applied (%s): %s",
            initial_scenario["label"],
            " | ".join(
                line.removeprefix("- ")
                for line in strategy_scenario.strategy_preview_lines(daily_theme_watchlist.CONFIG.strategy, initial_scenario)
            ),
        )

        current_state = run_state.build_rank_state(df_rank, market_regime)
        last_state = run_state.load_last_state(
            state_file=daily_theme_watchlist.STATE_FILE,
            state_enabled=daily_theme_watchlist.CONFIG.state_enabled,
        )

        should_send = _timed_call(
            step_timings,
            "should_alert",
            daily_theme_watchlist.should_alert,
            df_rank,
            current_state,
            last_state,
            market_regime,
            us_market,
        )
        if should_send:

            def _send_notifications() -> None:
                daily_theme_watchlist.send_telegram_message(
                    _build_macro_message(daily_theme_watchlist, market_regime, us_market, df_rank)
                )
                daily_theme_watchlist.send_telegram_message(
                    _build_short_term_message(daily_theme_watchlist, df_rank, market_regime, us_market)
                )
                daily_theme_watchlist.send_telegram_message(
                    _build_early_gem_message(daily_theme_watchlist, df_rank)
                )
                daily_theme_watchlist.send_telegram_message(
                    _build_midlong_message(daily_theme_watchlist, df_rank, market_regime, us_market)
                )

            _timed_call(step_timings, "notifications", _send_notifications)
            daily_theme_watchlist.logger.info("Notification sent.")
        else:
            daily_theme_watchlist.logger.info("No notification sent.")

        _timed_call(
            step_timings,
            "persist_state",
            run_state.save_last_state,
            state_file=daily_theme_watchlist.STATE_FILE,
            state_enabled=daily_theme_watchlist.CONFIG.state_enabled,
            state=current_state,
        )
        _timed_call(
            step_timings,
            "persist_success",
            run_state.save_last_success_date,
            success_file=daily_theme_watchlist.SUCCESS_FILE,
            success_date=today,
            signature=run_signature,
        )
        runtime_metrics.write_runtime_metrics(
            runtime_metrics_json=daily_theme_watchlist.RUNTIME_METRICS_JSON,
            runtime_metrics_md=daily_theme_watchlist.RUNTIME_METRICS_MD,
            backtest_state_path=daily_theme_watchlist.OUTDIR / "backtest_state.json",
            local_tz=daily_theme_watchlist.LOCAL_TZ,
            status="ok",
            step_timings=step_timings,
            warnings=warnings,
            cache_stats=dict(daily_theme_watchlist._CACHE_STATS),
            wall_seconds=time.perf_counter() - main_started,
        )
        daily_theme_watchlist.logger.info(
            "Runtime timings: %s", ", ".join(f"{name}={seconds:.3f}s" for name, seconds in step_timings.items())
        )

        if warnings:
            daily_theme_watchlist.logger.warning("Best effort warnings: %s", " | ".join(warnings))
        return 0
    except Exception as exc:
        err_msg = f"Watchlist job failed: {exc}"
        daily_theme_watchlist.logger.exception(err_msg)
        warnings.append(err_msg)
        runtime_metrics.write_runtime_metrics(
            runtime_metrics_json=daily_theme_watchlist.RUNTIME_METRICS_JSON,
            runtime_metrics_md=daily_theme_watchlist.RUNTIME_METRICS_MD,
            backtest_state_path=daily_theme_watchlist.OUTDIR / "backtest_state.json",
            local_tz=daily_theme_watchlist.LOCAL_TZ,
            status="failed",
            step_timings=step_timings,
            warnings=warnings,
            cache_stats=dict(daily_theme_watchlist._CACHE_STATS),
            wall_seconds=time.perf_counter() - main_started,
        )
        daily_theme_watchlist.send_telegram_message(err_msg)
        return 1
