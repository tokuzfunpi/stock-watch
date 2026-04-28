from __future__ import annotations

import sys
import time

from stock_watch.paths import REPO_ROOT


def _load_legacy_daily_workflow():
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    import daily_theme_watchlist

    return daily_theme_watchlist


def run_daily_watchlist(*, force_run: bool | None = False) -> int:
    daily_theme_watchlist = _load_legacy_daily_workflow()
    main_started = time.perf_counter()
    effective_force_run = daily_theme_watchlist.FORCE_RUN if force_run is None else bool(force_run)
    for key in daily_theme_watchlist._CACHE_STATS:
        daily_theme_watchlist._CACHE_STATS[key] = 0
    step_timings: dict[str, float] = {}
    warnings: list[str] = []
    try:
        if (
            not effective_force_run
            and daily_theme_watchlist.load_last_success_date() == daily_theme_watchlist.today_local_str()
            and daily_theme_watchlist.load_last_success_signature() == daily_theme_watchlist.current_run_signature()
        ):
            daily_theme_watchlist.logger.info(
                "Already completed successfully for %s with same code/config. Skip duplicate run.",
                daily_theme_watchlist.today_local_str(),
            )
            return 0

        try:
            market_regime = daily_theme_watchlist._timed_call(
                step_timings, "market_regime", daily_theme_watchlist.get_market_regime
            )
        except Exception as exc:
            warnings.append(f"market_regime: {exc}")
            daily_theme_watchlist.logger.exception("Market regime fetch failed (best effort): %s", exc)
            market_regime = {"comment": "加權指數資料抓不到（best effort）", "is_bullish": True}

        try:
            us_market = daily_theme_watchlist._timed_call(
                step_timings, "us_market", daily_theme_watchlist.get_us_market_reference
            )
        except Exception as exc:
            warnings.append(f"us_market: {exc}")
            daily_theme_watchlist.logger.exception("US market reference failed (best effort): %s", exc)
            us_market = {"summary": "美股參考暫時抓不到（best effort）。", "rows": []}

        initial_scenario = daily_theme_watchlist.build_market_scenario(market_regime, us_market)
        adjusted_strat = daily_theme_watchlist.adjust_strategy_by_scenario(
            daily_theme_watchlist.CONFIG.strategy, initial_scenario
        )
        daily_theme_watchlist._timed_call(
            step_timings, "cache_warmup", daily_theme_watchlist.prewarm_watchlist_indicator_cache
        )
        df_rank = daily_theme_watchlist._timed_call(
            step_timings, "watchlist", daily_theme_watchlist.run_watchlist, strat=adjusted_strat
        )
        bt_steady, bt_attack = daily_theme_watchlist._timed_call(
            step_timings, "backtest", daily_theme_watchlist.run_backtest_dual
        )

        daily_theme_watchlist.logger.info("=== 今日排行榜 ===\n%s", df_rank.to_string(index=False))
        daily_theme_watchlist.logger.info("Market regime: %s", market_regime["comment"])

        short_candidates, short_backups, midlong_candidates, _ = daily_theme_watchlist._timed_call(
            step_timings,
            "candidate_sets",
            daily_theme_watchlist.build_candidate_sets,
            df_rank,
            market_regime,
            us_market,
        )
        market_scenario = daily_theme_watchlist.build_market_scenario(market_regime, us_market, df_rank)
        daily_theme_watchlist._timed_call(
            step_timings,
            "reports",
            daily_theme_watchlist.save_reports,
            df_rank,
            market_regime,
            bt_steady,
            bt_attack,
            us_market,
        )
        try:
            daily_theme_watchlist._timed_call(
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
            daily_theme_watchlist._timed_call(
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
                for line in daily_theme_watchlist.strategy_preview_lines(daily_theme_watchlist.CONFIG.strategy, initial_scenario)
            ),
        )

        current_state = daily_theme_watchlist.build_state(df_rank, market_regime)
        last_state = daily_theme_watchlist.load_last_state()

        should_send = daily_theme_watchlist._timed_call(
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
                    daily_theme_watchlist.build_macro_message(market_regime, us_market, df_rank)
                )
                daily_theme_watchlist.send_telegram_message(
                    daily_theme_watchlist.build_short_term_message(df_rank, market_regime, us_market)
                )
                daily_theme_watchlist.send_telegram_message(
                    daily_theme_watchlist.build_early_gem_message(df_rank, market_regime, us_market)
                )
                daily_theme_watchlist.send_telegram_message(
                    daily_theme_watchlist.build_midlong_message(df_rank, market_regime, us_market)
                )

            daily_theme_watchlist._timed_call(step_timings, "notifications", _send_notifications)
            daily_theme_watchlist.logger.info("Notification sent.")
        else:
            daily_theme_watchlist.logger.info("No notification sent.")

        daily_theme_watchlist._timed_call(
            step_timings, "persist_state", daily_theme_watchlist.save_last_state, current_state
        )
        daily_theme_watchlist._timed_call(
            step_timings, "persist_success", daily_theme_watchlist.save_last_success_date, daily_theme_watchlist.today_local_str()
        )
        daily_theme_watchlist.write_runtime_metrics(
            status="ok",
            step_timings=step_timings,
            warnings=warnings,
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
        daily_theme_watchlist.write_runtime_metrics(
            status="failed",
            step_timings=step_timings,
            warnings=warnings,
            wall_seconds=time.perf_counter() - main_started,
        )
        daily_theme_watchlist.send_telegram_message(err_msg)
        return 1
