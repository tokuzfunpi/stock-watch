from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime
from importlib import util
from pathlib import Path
from unittest.mock import patch

import daily_theme_watchlist as dtw
import pandas as pd
from stock_watch.cli import local_daily as local_daily_module
from stock_watch.backtesting.core import run_backtest_dual as run_backtest_dual_module
from stock_watch.ranking.scoring import build_rank_table
from stock_watch.signals.detect import (
    add_indicators as module_add_indicators,
    build_speculative_risk_profile as module_build_speculative_risk_profile,
    detect_row as module_detect_row,
    grade_signal as module_grade_signal,
)

from daily_theme_watchlist import (
    add_indicators,
    alternate_taiwan_ticker,
    adjust_strategy_by_scenario,
    apply_feedback_adjustment,
    build_feedback_summary,
    build_daily_report_markdown,
    build_early_gem_message,
    build_macro_message,
    build_portfolio_message,
    build_portfolio_report_markdown,
    build_runtime_metrics_markdown,
    holding_style_label,
    is_placeholder_name,
    lookup_twse_display_name,
    lookup_yahoo_tw_name,
    resolve_security_name,
    should_refresh_watchlist_name,
    portfolio_advice_label,
    portfolio_price_plan,
    portfolio_price_plan_text,
    build_special_etf_message,
    build_midlong_message,
    build_short_term_message,
    build_speculative_risk_profile,
    detect_row,
    grade_signal,
    load_telegram_chat_ids,
    load_portfolio,
    normalize_ticker_symbol,
    parse_chat_ids,
    speculative_risk_label,
    speculative_risk_score,
    select_midlong_candidates,
    select_short_term_candidates,
    select_push_candidates,
    split_message,
    CONFIG,
    _DAILY_OHLCV_CACHE,
    _INDICATOR_FRAME_CACHE,
    main as watchlist_main,
    sync_watchlist_with_portfolio,
    upsert_alert_tracking,
    watch_price_plan,
    watch_price_plan_text,
    yf_download_one,
)

UPDATE_CHAT_ID_MAP_PATH = Path(__file__).resolve().parent.parent / "tools" / "update_chat_id_map.py"
UPDATE_CHAT_ID_MAP_SPEC = util.spec_from_file_location("update_chat_id_map", UPDATE_CHAT_ID_MAP_PATH)
update_chat_id_map = util.module_from_spec(UPDATE_CHAT_ID_MAP_SPEC)
assert UPDATE_CHAT_ID_MAP_SPEC and UPDATE_CHAT_ID_MAP_SPEC.loader
UPDATE_CHAT_ID_MAP_SPEC.loader.exec_module(update_chat_id_map)


class DetectRowTests(unittest.TestCase):
    def test_main_skips_duplicate_run_without_force(self) -> None:
        with patch("daily_theme_watchlist.load_last_success_date", return_value=dtw.today_local_str()), patch(
            "daily_theme_watchlist.load_last_success_signature", return_value="sig"
        ), patch("daily_theme_watchlist.current_run_signature", return_value="sig"), patch(
            "daily_theme_watchlist.get_market_regime"
        ) as market_regime_mock:
            result = watchlist_main()

        self.assertEqual(result, 0)
        market_regime_mock.assert_not_called()

    def test_main_force_run_bypasses_duplicate_guard(self) -> None:
        df_rank = pd.DataFrame(
            [
                {
                    "rank": 1,
                    "ticker": "AAA.TW",
                    "name": "Alpha",
                    "group": "theme",
                    "layer": "short_attack",
                    "setup_score": 7,
                    "risk_score": 2,
                    "signals": "ACCEL",
                    "ret5_pct": 9.0,
                    "ret10_pct": 12.0,
                    "ret20_pct": 15.0,
                    "volume_ratio20": 1.4,
                    "spec_risk_label": "正常",
                    "regime": "轉強速度有出來",
                    "rank_change": 0,
                    "setup_change": 0,
                    "grade": "A",
                }
            ]
        )

        with patch("daily_theme_watchlist.load_last_success_date", return_value=dtw.today_local_str()), patch(
            "daily_theme_watchlist.load_last_success_signature", return_value="sig"
        ), patch("daily_theme_watchlist.current_run_signature", return_value="sig"), patch(
            "daily_theme_watchlist.get_market_regime", return_value={"comment": "ok", "is_bullish": True}
        ) as market_regime_mock, patch(
            "daily_theme_watchlist.get_us_market_reference", return_value={"summary": "ok", "rows": []}
        ), patch(
            "daily_theme_watchlist.build_market_scenario",
            return_value={"label": "正常盤", "stance": "中性", "exit_note": "照計畫"},
        ), patch(
            "daily_theme_watchlist.adjust_strategy_by_scenario", return_value=dtw.CONFIG.strategy
        ), patch(
            "daily_theme_watchlist.prewarm_watchlist_indicator_cache"
        ), patch(
            "daily_theme_watchlist.run_watchlist", return_value=df_rank
        ), patch(
            "daily_theme_watchlist.run_backtest_dual", return_value=(None, None)
        ), patch(
            "daily_theme_watchlist.build_candidate_sets",
            return_value=(pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame()),
        ), patch(
            "daily_theme_watchlist.save_reports"
        ), patch(
            "daily_theme_watchlist.upsert_alert_tracking"
        ), patch(
            "daily_theme_watchlist.strategy_preview_lines", return_value=["- noop"]
        ), patch(
            "daily_theme_watchlist.build_state", return_value="STATE"
        ), patch(
            "daily_theme_watchlist.load_last_state", return_value=""
        ), patch(
            "daily_theme_watchlist.should_alert", return_value=False
        ), patch(
            "daily_theme_watchlist.save_last_state"
        ), patch(
            "daily_theme_watchlist.save_last_success_date"
        ):
            result = watchlist_main(force_run=True)

        self.assertEqual(result, 0)
        market_regime_mock.assert_called_once()

    def test_detect_row_generates_expected_fields_for_accel_case(self) -> None:
        dates = pd.date_range("2025-01-01", periods=260, freq="B")
        closes = [100.0] * 249 + [100.0, 101.0, 102.0, 103.0, 105.0, 112.0, 113.0, 114.0, 115.0, 116.0, 117.0]
        volumes = [1000] * 255 + [2500, 2600, 2700, 2800, 2900]

        df = pd.DataFrame(
            {
                "Open": closes,
                "High": [c + 1 for c in closes],
                "Low": [c - 1 for c in closes],
                "Close": closes,
                "Volume": volumes,
            },
            index=dates,
        )

        out = detect_row(add_indicators(df), "TEST1.TW", "Accel Name", "theme", "short_attack")

        self.assertEqual(out["ticker"], "TEST1.TW")
        self.assertEqual(out["name"], "Accel Name")
        self.assertIn("ACCEL", out["signals"])
        self.assertGreater(out["setup_score"], 0)
        self.assertIn("atr_pct", out)
        self.assertIn("volatility_tag", out)
        self.assertIn("date", out)
        self.assertIn("spec_risk_note", out)
        self.assertIn("spec_risk_flags", out)
        self.assertIn("spec_risk_subtype", out)

    def test_detect_row_module_matches_legacy_wrapper(self) -> None:
        dates = pd.date_range("2025-01-01", periods=260, freq="B")
        closes = [100.0] * 249 + [100.0, 101.0, 102.0, 103.0, 105.0, 112.0, 113.0, 114.0, 115.0, 116.0, 117.0]
        volumes = [1000] * 255 + [2500, 2600, 2700, 2800, 2900]

        df = pd.DataFrame(
            {
                "Open": closes,
                "High": [c + 1 for c in closes],
                "Low": [c - 1 for c in closes],
                "Close": closes,
                "Volume": volumes,
            },
            index=dates,
        )

        legacy = detect_row(add_indicators(df), "TEST1.TW", "Accel Name", "theme", "short_attack")
        modular = module_detect_row(
            module_add_indicators(df),
            "TEST1.TW",
            "Accel Name",
            "theme",
            "short_attack",
            dtw.CONFIG.strategy,
            dtw.CONFIG.group_weights,
        )

        self.assertEqual(legacy, modular)

    def test_adjust_strategy_by_scenario_is_preview_only_helper(self) -> None:
        scenario = {"label": "明顯修正盤"}
        adjusted = adjust_strategy_by_scenario(CONFIG.strategy, scenario)

        self.assertGreater(adjusted.rebreak_vol_ratio, CONFIG.strategy.rebreak_vol_ratio)
        self.assertGreater(adjusted.accel_vol_ratio_fast, CONFIG.strategy.accel_vol_ratio_fast)

    def test_yf_download_one_reuses_in_memory_history_cache(self) -> None:
        dates = pd.date_range("2025-01-01", periods=260, freq="B")
        df = pd.DataFrame(
            {
                "Open": [100.0] * 260,
                "High": [101.0] * 260,
                "Low": [99.0] * 260,
                "Close": [100.0] * 260,
                "Volume": [1000.0] * 260,
            },
            index=dates,
        )

        class FakeProvider:
            name = "fake"

            def __init__(self) -> None:
                self.calls = 0

            def download_daily_ohlcv(self, ticker: str, period: str) -> pd.DataFrame:
                self.calls += 1
                return df.copy()

        provider = FakeProvider()
        _DAILY_OHLCV_CACHE.clear()
        with patch("daily_theme_watchlist.DAILY_PRICE_PROVIDERS", [provider]), patch(
            "daily_theme_watchlist.PRIMARY_DAILY_PROVIDER", provider
        ), patch("daily_theme_watchlist.ENABLE_HISTORY_CACHE", True), patch(
            "daily_theme_watchlist.ENABLE_DISK_HISTORY_CACHE", False
        ):
            first = dtw.yf_download_one("2330.TW", "3y")
            second = dtw.yf_download_one("2330.TW", "3y")

        self.assertEqual(provider.calls, 1)
        self.assertEqual(len(first), len(second))
        self.assertIsNot(first, second)

    def test_yf_download_one_reuses_superset_history_cache(self) -> None:
        dates = pd.date_range("2020-01-01", periods=1600, freq="B")
        df = pd.DataFrame(
            {
                "Open": [100.0] * len(dates),
                "High": [101.0] * len(dates),
                "Low": [99.0] * len(dates),
                "Close": [100.0] * len(dates),
                "Volume": [1000.0] * len(dates),
            },
            index=dates,
        )

        class FailProvider:
            name = "fail"

            def download_daily_ohlcv(self, ticker: str, period: str) -> pd.DataFrame:
                raise AssertionError("provider should not be called when superset cache is available")

        _DAILY_OHLCV_CACHE.clear()
        dtw._CACHE_STATS["history_hit"] = 0
        dtw._CACHE_STATS["history_superset_hit"] = 0
        dtw._CACHE_STATS["history_miss"] = 0
        _DAILY_OHLCV_CACHE[("2330.TW", "5y")] = df.copy()

        with patch("daily_theme_watchlist.DAILY_PRICE_PROVIDERS", [FailProvider()]), patch(
            "daily_theme_watchlist.ENABLE_HISTORY_CACHE", True
        ):
            out = dtw.yf_download_one("2330.TW", "3y")

        self.assertLess(len(out), len(df))
        self.assertEqual(dtw._CACHE_STATS["history_superset_hit"], 1)
        self.assertEqual(dtw._CACHE_STATS["history_miss"], 0)

    def test_yf_download_one_reuses_disk_history_cache(self) -> None:
        dates = pd.date_range("2023-01-01", periods=800, freq="B")
        df = pd.DataFrame(
            {
                "Open": [100.0] * len(dates),
                "High": [101.0] * len(dates),
                "Low": [99.0] * len(dates),
                "Close": [100.0] * len(dates),
                "Volume": [1000.0] * len(dates),
            },
            index=dates,
        )

        class FailProvider:
            name = "fail"

            def download_daily_ohlcv(self, ticker: str, period: str) -> pd.DataFrame:
                raise AssertionError("provider should not be called when disk cache is available")

        _DAILY_OHLCV_CACHE.clear()
        dtw._CACHE_STATS["history_hit"] = 0
        dtw._CACHE_STATS["history_disk_hit"] = 0
        dtw._CACHE_STATS["history_superset_hit"] = 0
        dtw._CACHE_STATS["history_miss"] = 0

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = Path(tmpdir)
            cache_path = cache_dir / "2330_TW__3y.csv"
            cached = df.copy()
            cached.index.name = "Date"
            cached.to_csv(cache_path, encoding="utf-8")

            with patch("daily_theme_watchlist.HISTORY_CACHE_DIR", cache_dir), patch(
                "daily_theme_watchlist.DAILY_PRICE_PROVIDERS", [FailProvider()]
            ), patch("daily_theme_watchlist.ENABLE_HISTORY_CACHE", True), patch(
                "daily_theme_watchlist.ENABLE_DISK_HISTORY_CACHE", True
            ), patch(
                "daily_theme_watchlist._required_history_end_date", return_value=pd.Timestamp("2025-03-20")
            ):
                out = dtw.yf_download_one("2330.TW", "3y")

        self.assertEqual(len(out), len(df))
        self.assertEqual(dtw._CACHE_STATS["history_disk_hit"], 1)
        self.assertEqual(dtw._CACHE_STATS["history_miss"], 0)

    def test_required_history_end_date_uses_taiwan_session(self) -> None:
        preopen = datetime(2026, 4, 24, 8, 30, tzinfo=dtw.LOCAL_TZ)
        postclose = datetime(2026, 4, 24, 14, 0, tzinfo=dtw.LOCAL_TZ)

        self.assertEqual(dtw._required_history_end_date("2330.TW", preopen), pd.Timestamp("2026-04-23"))
        self.assertEqual(dtw._required_history_end_date("2330.TW", postclose), pd.Timestamp("2026-04-24"))

    def test_required_history_end_date_uses_us_session(self) -> None:
        before_us_close = datetime(2026, 4, 24, 3, 0, tzinfo=dtw.LOCAL_TZ)
        after_us_close = datetime(2026, 4, 24, 9, 0, tzinfo=dtw.LOCAL_TZ)

        self.assertEqual(dtw._required_history_end_date("NVDA", before_us_close), pd.Timestamp("2026-04-22"))
        self.assertEqual(dtw._required_history_end_date("NVDA", after_us_close), pd.Timestamp("2026-04-23"))

    def test_required_history_end_date_rolls_weekend_to_previous_business_day(self) -> None:
        saturday = datetime(2026, 4, 25, 15, 0, tzinfo=dtw.LOCAL_TZ)
        sunday_evening_taipei = datetime(2026, 4, 26, 22, 0, tzinfo=dtw.LOCAL_TZ)

        self.assertEqual(dtw._required_history_end_date("2330.TW", saturday), pd.Timestamp("2026-04-24"))
        self.assertEqual(dtw._required_history_end_date("SOXX", sunday_evening_taipei), pd.Timestamp("2026-04-24"))

    def test_get_indicator_frame_reuses_cached_indicator_dataframe(self) -> None:
        dates = pd.date_range("2025-01-01", periods=260, freq="B")
        df = pd.DataFrame(
            {
                "Open": [100.0] * 260,
                "High": [101.0] * 260,
                "Low": [99.0] * 260,
                "Close": [100.0] * 260,
                "Volume": [1000.0] * 260,
            },
            index=dates,
        )

        _INDICATOR_FRAME_CACHE.clear()
        with patch("daily_theme_watchlist.yf_download_one", return_value=df.copy()) as download_mock, patch(
            "daily_theme_watchlist.ENABLE_HISTORY_CACHE", True
        ):
            first = dtw.get_indicator_frame("2330.TW", "3y")
            second = dtw.get_indicator_frame("2330.TW", "3y")

        self.assertEqual(download_mock.call_count, 1)
        self.assertIn("MA20", first.columns)
        self.assertIsNot(first, second)

    def test_get_indicator_frame_reuses_superset_indicator_cache(self) -> None:
        dates = pd.date_range("2020-01-01", periods=1600, freq="B")
        df = pd.DataFrame(
            {
                "Open": [100.0] * len(dates),
                "High": [101.0] * len(dates),
                "Low": [99.0] * len(dates),
                "Close": [100.0] * len(dates),
                "Volume": [1000.0] * len(dates),
                "MA20": [100.0] * len(dates),
                "Ret20D": [0.0] * len(dates),
                "Ret5D": [0.0] * len(dates),
                "VolumeMA20": [1000.0] * len(dates),
                "VolumeRatio20": [1.0] * len(dates),
                "ATR14": [1.0] * len(dates),
                "ATR14Pct": [0.01] * len(dates),
            },
            index=dates,
        )

        _INDICATOR_FRAME_CACHE.clear()
        dtw._CACHE_STATS["indicator_hit"] = 0
        dtw._CACHE_STATS["indicator_superset_hit"] = 0
        dtw._CACHE_STATS["indicator_miss"] = 0
        _INDICATOR_FRAME_CACHE[("2330.TW", "5y", 20)] = df.copy()

        with patch("daily_theme_watchlist.yf_download_one", side_effect=AssertionError("should not download")), patch(
            "daily_theme_watchlist.ENABLE_HISTORY_CACHE", True
        ):
            out = dtw.get_indicator_frame("2330.TW", "3y")

        self.assertLess(len(out), len(df))
        self.assertEqual(dtw._CACHE_STATS["indicator_superset_hit"], 1)
        self.assertEqual(dtw._CACHE_STATS["indicator_miss"], 0)

    def test_build_runtime_metrics_markdown_includes_cache_and_backtest_sections(self) -> None:
        markdown = build_runtime_metrics_markdown(
            generated_at="2026-04-24 11:00:00",
            status="ok",
            step_timings={"watchlist": 0.123, "backtest": 0.456},
            warnings=["market_regime: best effort"],
            cache_stats={
                "history_hit": 5,
                "history_disk_hit": 2,
                "history_superset_hit": 4,
                "history_miss": 2,
                "indicator_hit": 3,
                "indicator_superset_hit": 2,
                "indicator_miss": 1,
            },
            backtest_meta={"last_run_mode": "incremental_noop", "last_run_scanned_cutoffs": 0},
            wall_seconds=0.789,
        )

        self.assertIn("## Cache", markdown)
        self.assertIn("History cache", markdown)
        self.assertIn("## Backtest", markdown)
        self.assertIn("incremental_noop", markdown)
        self.assertIn("Wall-clock seconds", markdown)


class MarketRegimeTests(unittest.TestCase):
    def test_get_market_regime_treats_zero_volume_ratio_as_neutral(self) -> None:
        dates = pd.date_range("2025-01-01", periods=260, freq="B")
        closes = [100.0] * 240 + [101.0 + i for i in range(20)]
        volumes = [1000.0] * 259 + [0.0]
        df = pd.DataFrame(
            {
                "Open": closes,
                "High": [c + 1 for c in closes],
                "Low": [c - 1 for c in closes],
                "Close": closes,
                "Volume": volumes,
            },
            index=dates,
        )

        with patch("daily_theme_watchlist.yf_download_one", return_value=df), patch.object(
            dtw, "market_session_phase", return_value="postclose"
        ):
            regime = dtw.get_market_regime()

        self.assertTrue(regime["is_bullish"])
        self.assertFalse(regime["volume_ratio20_valid"])
        self.assertEqual(regime["volume_ratio20"], 1.0)
        self.assertIn("量比資料異常", regime["comment"])

    def test_build_market_scenario_uses_intraday_guard_before_close(self) -> None:
        market_regime = {
            "comment": "加權指數盤中偏弱",
            "ret20_pct": 2.0,
            "volume_ratio20": 0.9,
            "is_bullish": False,
            "session_phase": "intraday",
        }
        us_market = {"summary": "美股昨晚偏弱，台股早盤要提防開高走低或續殺。"}

        scenario = dtw.build_market_scenario(market_regime, us_market, pd.DataFrame())

        self.assertEqual(scenario["label"], "盤中保守觀察")


class GradeSignalTests(unittest.TestCase):
    def test_grade_signal_returns_a_for_strong_accel_setup(self) -> None:
        row = {
            "setup_score": 8,
            "risk_score": 3,
            "signals": "ACCEL",
            "ret5_pct": 9.0,
            "volume_ratio20": 1.4,
            "ret20_pct": 12.0,
        }

        self.assertEqual(grade_signal(row), "A")
        self.assertEqual(module_grade_signal(row), "A")

    def test_grade_signal_returns_c_for_overheated_name(self) -> None:
        row = {
            "setup_score": 9,
            "risk_score": 6,
            "signals": "SURGE",
            "ret5_pct": 20.0,
            "volume_ratio20": 2.0,
            "ret20_pct": 35.0,
        }

        self.assertEqual(grade_signal(row), "C")
        self.assertEqual(module_grade_signal(row), "C")

    def test_build_rank_table_sorts_and_tracks_changes(self) -> None:
        rows = [
            {
                "ticker": "AAA.TW",
                "setup_score": 7,
                "risk_score": 2,
                "signals": "ACCEL",
                "ret5_pct": 10.0,
                "volume_ratio20": 1.4,
                "ret20_pct": 12.0,
            },
            {
                "ticker": "BBB.TW",
                "setup_score": 5,
                "risk_score": 2,
                "signals": "TREND",
                "ret5_pct": 6.0,
                "volume_ratio20": 1.2,
                "ret20_pct": 8.0,
            },
        ]
        prev_rank = pd.DataFrame(
            [
                {"ticker": "BBB.TW", "rank": 1, "setup_score": 4, "risk_score": 2},
                {"ticker": "AAA.TW", "rank": 2, "setup_score": 6, "risk_score": 2},
            ]
        )

        ranked = build_rank_table(rows, prev_rank)

        self.assertEqual(list(ranked["ticker"]), ["AAA.TW", "BBB.TW"])
        self.assertEqual(list(ranked["rank_change"]), [1, -1])
        self.assertEqual(list(ranked["status_change"]), ["UP", "UP"])

    def test_incremental_backtest_scans_only_new_cutoff_dates(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            outdir = Path(tmpdir)
            dates = pd.date_range("2025-01-01", periods=280, freq="B")
            df = pd.DataFrame(
                {
                    "Open": [100.0] * 280,
                    "High": [101.0] * 280,
                    "Low": [99.0] * 280,
                    "Close": [100.0 + i * 0.1 for i in range(280)],
                    "Volume": [1000.0] * 280,
                },
                index=dates,
            )

            watchlist = [{"ticker": "2330.TW", "name": "台積電", "group": "core", "layer": "midlong_core"}]
            detect_calls: list[str] = []

            def _detect_row(cut: pd.DataFrame, ticker: str, name: str, group: str, layer: str) -> dict:
                detect_calls.append(cut.index[-1].strftime("%Y-%m-%d"))
                return {
                    "setup_score": 6,
                    "risk_score": 2,
                    "signals": "ACCEL",
                    "ret5_pct": 9.0,
                    "ret20_pct": 12.0,
                    "volume_ratio20": 1.5,
                }

            run_backtest_dual_module(
                backtest_enabled=True,
                signature="sig-1",
                watchlist=watchlist,
                backtest_period="3y",
                lookahead_days=[1, 5, 20],
                outdir=outdir,
                get_indicator_frame=lambda ticker, period: df,
                detect_row=_detect_row,
                logger=dtw.logger,
            )
            first_call_count = len(detect_calls)
            self.assertGreater(first_call_count, 0)

            detect_calls.clear()
            run_backtest_dual_module(
                backtest_enabled=True,
                signature="sig-1",
                watchlist=watchlist,
                backtest_period="3y",
                lookahead_days=[1, 5, 20],
                outdir=outdir,
                get_indicator_frame=lambda ticker, period: df,
                detect_row=_detect_row,
                logger=dtw.logger,
            )

            self.assertEqual(detect_calls, [])


class SpeculativeRiskTests(unittest.TestCase):
    def test_speculative_risk_flags_hot_non_trend_name(self) -> None:
        score = speculative_risk_score(
            ret5_pct=26.0,
            ret20_pct=38.0,
            volume_ratio20=2.6,
            bias20_pct=14.0,
            risk_score=6,
            signals="SURGE",
            group="theme",
        )

        self.assertGreaterEqual(score, 6)
        self.assertEqual(speculative_risk_label(score), "疑似炒作風險高")

    def test_speculative_risk_profile_surfaces_flags_and_note(self) -> None:
        profile = build_speculative_risk_profile(
            ret1_pct=8.0,
            ret5_pct=24.0,
            ret20_pct=52.0,
            volume_ratio20=3.1,
            bias20_pct=19.0,
            atr_pct=7.2,
            range20_pct=28.0,
            drawdown120_pct=-2.0,
            risk_score=6,
            setup_score=6,
            signals="SURGE",
            group="theme",
        )

        self.assertGreaterEqual(profile.score, 6)
        self.assertEqual(profile.label, "疑似炒作風險高")
        self.assertEqual(profile.subtype, "急拉爆量型")
        self.assertIn("短線急漲", profile.flags)
        self.assertIn("爆量", profile.flags)
        self.assertIn("乖離偏大", profile.flags)
        self.assertNotEqual(profile.note, "結構相對正常")

    def test_speculative_risk_profile_gives_structure_discount_to_core_trend(self) -> None:
        profile = module_build_speculative_risk_profile(
            ret1_pct=1.0,
            ret5_pct=6.0,
            ret20_pct=14.0,
            volume_ratio20=1.0,
            bias20_pct=4.0,
            atr_pct=2.4,
            range20_pct=8.0,
            drawdown120_pct=-12.0,
            risk_score=2,
            setup_score=8,
            signals="TREND",
            group="core",
        )

        self.assertEqual(profile.label, "正常")
        self.assertEqual(profile.subtype, "正常")
        self.assertEqual(profile.note, "結構相對正常")


class FeedbackTests(unittest.TestCase):
    def test_feedback_summary_and_adjustment_use_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            alert_csv = Path(tmpdir) / "alert_tracking.csv"
            feedback_csv = Path(tmpdir) / "feedback_summary.csv"
            pd.DataFrame(
                [
                    {"watch_type": "short", "action_label": "可追", "ret1_future_pct": 2.0, "ret5_future_pct": 6.0},
                    {"watch_type": "short", "action_label": "可追", "ret1_future_pct": 1.0, "ret5_future_pct": 4.0},
                    {"watch_type": "short", "action_label": "等拉回", "ret1_future_pct": -1.0, "ret5_future_pct": -2.0},
                    {"watch_type": "short", "action_label": "等拉回", "ret1_future_pct": 0.5, "ret5_future_pct": -1.0},
                ]
            ).to_csv(alert_csv, index=False)

            with patch("daily_theme_watchlist.ALERT_TRACK_CSV", alert_csv), patch(
                "daily_theme_watchlist.FEEDBACK_SUMMARY_CSV", feedback_csv
            ):
                summary = build_feedback_summary()

                self.assertFalse(summary.empty)
                self.assertIn("feedback_score", summary.columns)
                self.assertIn("pl_ratio", summary.columns)
                short_all = summary[
                    (summary["watch_type"] == "short")
                    & (summary["action_label"] == "__all__")
                ].iloc[0]
                self.assertAlmostEqual(float(short_all["pl_ratio"]), 3.33, places=2)

                candidates = pd.DataFrame(
                    [
                        {"ticker": "PULL.TW", "risk_score": 2, "ret5_pct": 10.0, "volume_ratio20": 1.1, "signals": "", "setup_change": 0, "rank_change": 0},
                        {"ticker": "CHASE.TW", "risk_score": 2, "ret5_pct": 6.0, "volume_ratio20": 1.4, "signals": "ACCEL", "setup_change": 0, "rank_change": 0},
                    ]
                )

                adjusted = apply_feedback_adjustment(candidates, "short")

                self.assertEqual(adjusted.iloc[0]["ticker"], "CHASE.TW")
                self.assertIn("feedback_label", adjusted.columns)

    def test_watchlist_main_writes_reports_before_best_effort_alert_tracking(self) -> None:
        df_rank = pd.DataFrame(
            [
                {
                    "rank": 1,
                    "ticker": "AAA.TW",
                    "name": "Alpha",
                    "group": "theme",
                    "layer": "short_attack",
                    "setup_score": 7,
                    "risk_score": 2,
                    "signals": "ACCEL",
                    "ret5_pct": 9.0,
                    "ret10_pct": 12.0,
                    "ret20_pct": 15.0,
                    "volume_ratio20": 1.4,
                    "spec_risk_label": "正常",
                    "regime": "轉強速度有出來",
                    "rank_change": 0,
                    "setup_change": 0,
                    "grade": "A",
                }
            ]
        )

        with patch("daily_theme_watchlist.load_last_success_date", return_value=""), patch(
            "daily_theme_watchlist.current_run_signature", return_value="sig"
        ), patch("daily_theme_watchlist.get_market_regime", return_value={"comment": "ok", "is_bullish": True}), patch(
            "daily_theme_watchlist.get_us_market_reference", return_value={"summary": "ok", "rows": []}
        ), patch(
            "daily_theme_watchlist.build_market_scenario",
            return_value={"label": "正常盤", "stance": "中性", "exit_note": "照計畫"},
        ), patch(
            "daily_theme_watchlist.adjust_strategy_by_scenario", return_value=dtw.CONFIG.strategy
        ), patch(
            "daily_theme_watchlist.run_watchlist", return_value=df_rank
        ), patch(
            "daily_theme_watchlist.run_backtest_dual", return_value=(None, None)
        ), patch(
            "daily_theme_watchlist.build_candidate_sets",
            return_value=(pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame()),
        ), patch(
            "daily_theme_watchlist.save_reports"
        ) as mock_save_reports, patch(
            "daily_theme_watchlist.upsert_alert_tracking", side_effect=RuntimeError("tracking failed")
        ), patch(
            "daily_theme_watchlist.strategy_preview_lines", return_value=["- noop"]
        ), patch(
            "daily_theme_watchlist.build_state", return_value="STATE"
        ), patch(
            "daily_theme_watchlist.load_last_state", return_value=""
        ), patch(
            "daily_theme_watchlist.should_alert", return_value=False
        ), patch(
            "daily_theme_watchlist.save_last_state"
        ), patch(
            "daily_theme_watchlist.save_last_success_date"
        ):
            result = watchlist_main()

        self.assertEqual(result, 0)
        mock_save_reports.assert_called_once()

    def test_upsert_alert_tracking_persists_scenario_label(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            alert_csv = Path(tmpdir) / "alert_tracking.csv"
            short_candidates = pd.DataFrame(
                [
                    {
                        "date": "2026-04-22",
                        "ticker": "2356.TW",
                        "name": "英業達",
                        "group": "theme",
                        "grade": "A",
                        "rank": 1,
                        "setup_score": 12,
                        "risk_score": 1,
                        "layer": "short_attack",
                        "signals": "ACCEL",
                        "regime": "轉強速度有出來",
                        "feedback_score": 0.0,
                        "feedback_label": "樣本不足",
                        "close": 52.0,
                        "ma20": 50.0,
                        "ma60": 48.0,
                        "ret5_pct": 6.0,
                        "ret20_pct": 10.0,
                        "atr_pct": 4.5,
                        "volume_ratio20": 1.3,
                        "setup_change": 1,
                        "rank_change": 1,
                    }
                ]
            )

            with patch("daily_theme_watchlist.ALERT_TRACK_CSV", alert_csv), patch(
                "daily_theme_watchlist.yf_download_one", return_value=pd.DataFrame()
            ):
                upsert_alert_tracking(
                    short_candidates,
                    pd.DataFrame(),
                    {"label": "高檔震盪盤"},
                )

            saved = pd.read_csv(alert_csv)
            self.assertEqual(saved.iloc[0]["scenario_label"], "高檔震盪盤")

    def test_feedback_adjustment_uses_pl_ratio_as_tiebreaker(self) -> None:
        candidates = pd.DataFrame(
            [
                {"ticker": "PULL.TW", "risk_score": 2, "ret5_pct": 10.0, "volume_ratio20": 1.1, "signals": "", "setup_change": 0, "rank_change": 0},
                {"ticker": "CHASE.TW", "risk_score": 2, "ret5_pct": 6.0, "volume_ratio20": 1.4, "signals": "ACCEL", "setup_change": 0, "rank_change": 0},
            ]
        )
        summary = pd.DataFrame(
            [
                {"watch_type": "short", "action_label": "等拉回", "feedback_score": 1.0, "feedback_label": "近期有效", "pl_ratio": 1.2},
                {"watch_type": "short", "action_label": "續追蹤", "feedback_score": 1.0, "feedback_label": "近期有效", "pl_ratio": 2.8},
            ]
        )

        with patch("daily_theme_watchlist.build_feedback_summary", return_value=summary):
            adjusted = apply_feedback_adjustment(candidates, "short")

        self.assertEqual(adjusted.iloc[0]["ticker"], "CHASE.TW")
        self.assertIn("feedback_pl_ratio", adjusted.columns)

    def test_build_feedback_summary_includes_pl_ratio_in_feedback_score(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            alert_csv = Path(tmpdir) / "alert_tracking.csv"
            feedback_csv = Path(tmpdir) / "feedback_summary.csv"
            pd.DataFrame(
                [
                    {"watch_type": "short", "action_label": "高盈虧比", "ret1_future_pct": 0.0, "ret5_future_pct": 6.0},
                    {"watch_type": "short", "action_label": "高盈虧比", "ret1_future_pct": 0.0, "ret5_future_pct": 4.0},
                    {"watch_type": "short", "action_label": "高盈虧比", "ret1_future_pct": 0.0, "ret5_future_pct": -2.0},
                    {"watch_type": "short", "action_label": "低盈虧比", "ret1_future_pct": 0.0, "ret5_future_pct": 4.0},
                    {"watch_type": "short", "action_label": "低盈虧比", "ret1_future_pct": 0.0, "ret5_future_pct": 4.0},
                    {"watch_type": "short", "action_label": "低盈虧比", "ret1_future_pct": 0.0, "ret5_future_pct": -4.0},
                ]
            ).to_csv(alert_csv, index=False)

            with patch("daily_theme_watchlist.ALERT_TRACK_CSV", alert_csv), patch(
                "daily_theme_watchlist.FEEDBACK_SUMMARY_CSV", feedback_csv
            ):
                summary = build_feedback_summary()

            high = summary[
                (summary["watch_type"] == "short")
                & (summary["action_label"] == "高盈虧比")
            ].iloc[0]
            low = summary[
                (summary["watch_type"] == "short")
                & (summary["action_label"] == "低盈虧比")
            ].iloc[0]

            self.assertGreater(float(high["pl_ratio"]), float(low["pl_ratio"]))
            self.assertGreater(float(high["feedback_score"]), float(low["feedback_score"]))

    def test_build_feedback_summary_blends_recent_window_signal(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            alert_csv = Path(tmpdir) / "alert_tracking.csv"
            feedback_csv = Path(tmpdir) / "feedback_summary.csv"
            pd.DataFrame(
                [
                    {"alert_date": "2026-04-01", "watch_type": "short", "action_label": "近況轉弱", "ret1_future_pct": 0.0, "ret5_future_pct": 6.0},
                    {"alert_date": "2026-04-02", "watch_type": "short", "action_label": "近況轉弱", "ret1_future_pct": 0.0, "ret5_future_pct": 5.0},
                    {"alert_date": "2026-04-03", "watch_type": "short", "action_label": "近況轉弱", "ret1_future_pct": 0.0, "ret5_future_pct": 4.0},
                    {"alert_date": "2026-04-20", "watch_type": "short", "action_label": "近況轉弱", "ret1_future_pct": 0.0, "ret5_future_pct": -3.0},
                    {"alert_date": "2026-04-21", "watch_type": "short", "action_label": "近況轉弱", "ret1_future_pct": 0.0, "ret5_future_pct": -4.0},
                    {"alert_date": "2026-04-22", "watch_type": "short", "action_label": "近況轉弱", "ret1_future_pct": 0.0, "ret5_future_pct": -5.0},
                ]
            ).to_csv(alert_csv, index=False)

            with patch("daily_theme_watchlist.ALERT_TRACK_CSV", alert_csv), patch(
                "daily_theme_watchlist.FEEDBACK_SUMMARY_CSV", feedback_csv
            ):
                summary = build_feedback_summary()

            row = summary[
                (summary["watch_type"] == "short")
                & (summary["action_label"] == "近況轉弱")
            ].iloc[0]
            self.assertIn("recent_feedback_score", summary.columns)
            self.assertIn("base_feedback_score", summary.columns)
            self.assertEqual(int(row["recent_samples"]), 6)
            self.assertLess(float(row["recent_feedback_score"]), float(row["base_feedback_score"]))


class PortfolioTests(unittest.TestCase):
    def test_normalize_ticker_symbol_supports_plain_codes(self) -> None:
        self.assertEqual(normalize_ticker_symbol("2495"), "2495.TW")
        self.assertEqual(normalize_ticker_symbol("50"), "0050.TW")
        self.assertEqual(normalize_ticker_symbol("878"), "00878.TW")
        self.assertEqual(normalize_ticker_symbol("00772B"), "00772B.TWO")
        self.assertEqual(alternate_taiwan_ticker("3491.TW"), "3491.TWO")
        self.assertEqual(alternate_taiwan_ticker("3491.TWO"), "3491.TW")

    def test_sync_watchlist_with_portfolio_adds_missing_symbols(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            watchlist_csv = Path(tmpdir) / "watchlist.csv"
            portfolio_csv = Path(tmpdir) / "portfolio.csv"
            watchlist_csv.write_text("ticker,name,group,layer,enabled\n2495.TW,2495,core,midlong_core,true\n", encoding="utf-8")
            portfolio_csv.write_text("ticker,shares,avg_cost,target_profit_pct\n2330,1000,950,15\n00772B,1000,35,10\n", encoding="utf-8")

            with patch("daily_theme_watchlist.resolve_security_name", side_effect=lambda ticker: {"2330.TW": "台積電", "00772B.TWO": "中信高評級公司債"}[ticker]):
                added = sync_watchlist_with_portfolio(watchlist_csv, portfolio_csv)

            self.assertEqual(added, ["2330.TW", "00772B.TWO"])
            content = watchlist_csv.read_text(encoding="utf-8")
            self.assertIn("2330.TW", content)
            self.assertIn("00772B.TWO", content)
            self.assertIn("台積電", content)

    def test_sync_watchlist_with_portfolio_refreshes_placeholder_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            watchlist_csv = Path(tmpdir) / "watchlist.csv"
            portfolio_csv = Path(tmpdir) / "portfolio.csv"
            watchlist_csv.write_text("ticker,name,group,layer,enabled\n2412.TW,2412,core,midlong_core,true\n", encoding="utf-8")
            portfolio_csv.write_text("ticker,shares,avg_cost,target_profit_pct\n2412,1000,10,20\n", encoding="utf-8")

            with patch("daily_theme_watchlist.resolve_security_name", return_value="中華電"):
                added = sync_watchlist_with_portfolio(watchlist_csv, portfolio_csv)

            self.assertEqual(added, [])
            self.assertIn("中華電", watchlist_csv.read_text(encoding="utf-8"))

    def test_placeholder_name_detection(self) -> None:
        self.assertTrue(is_placeholder_name("2412", "2412.TW"))
        self.assertFalse(is_placeholder_name("中華電", "2412.TW"))
        self.assertTrue(should_refresh_watchlist_name("CHUNGHWA TELECOM", "2412.TW"))
        self.assertFalse(should_refresh_watchlist_name("中華電", "2412.TW"))

    def test_lookup_twse_display_name_reads_official_name(self) -> None:
        class FakeResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {"msgArray": [{"n": "中華電"}]}

        with patch("daily_theme_watchlist.HTTP.get", return_value=FakeResponse()), patch.dict(
            "daily_theme_watchlist.TWSE_NAME_CACHE", {}, clear=True
        ):
            self.assertEqual(lookup_twse_display_name("2412.TW"), "中華電")

    def test_resolve_security_name_prefers_twse_chinese_name(self) -> None:
        with patch("daily_theme_watchlist.lookup_twse_display_name", return_value="中華電"):
            self.assertEqual(resolve_security_name("2412.TW"), "中華電")

    def test_lookup_yahoo_tw_name_reads_chinese_title(self) -> None:
        class FakeResponse:
            text = "<html><body><h1>中華電</h1></body></html>"

            def raise_for_status(self) -> None:
                return None

        with patch("daily_theme_watchlist.HTTP.get", return_value=FakeResponse()):
            self.assertEqual(lookup_yahoo_tw_name("2412.TW"), "中華電")


class TelegramChatIdTests(unittest.TestCase):
    def test_parse_chat_ids_supports_commas_and_newlines(self) -> None:
        self.assertEqual(parse_chat_ids("123,-1001\n-1002"), [123, -1001, -1002])

    def test_load_telegram_chat_ids_prefers_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            chat_ids_path = Path(tmpdir) / "chat_ids"
            chat_ids_path.write_text("111\n222\n", encoding="utf-8")
            with patch.dict("os.environ", {"TELEGRAM_CHAT_IDS": "333,444"}, clear=False):
                self.assertEqual(load_telegram_chat_ids(chat_ids_path), [333, 444])

    def test_load_telegram_chat_ids_reads_local_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            chat_ids_path = Path(tmpdir) / "chat_ids"
            chat_ids_path.write_text("123456789\n-1001111111111\n", encoding="utf-8")
            with patch.dict("os.environ", {"TELEGRAM_CHAT_IDS": ""}, clear=False):
                self.assertEqual(load_telegram_chat_ids(chat_ids_path), [123456789, -1001111111111])


class ChatIdMapUpdateTests(unittest.TestCase):
    def test_extract_chat_rows_deduplicates_by_chat_id(self) -> None:
        rows = update_chat_id_map.extract_chat_rows(
            [
                {"message": {"chat": {"id": 1, "first_name": "A", "type": "private"}}},
                {"message": {"chat": {"id": 1, "first_name": "A2", "type": "private"}}},
                {"message": {"chat": {"id": 2, "first_name": "B", "type": "private"}}},
            ]
        )

        self.assertEqual(len(rows), 2)
        self.assertEqual({row["chat_id"] for row in rows}, {"1", "2"})
        latest = next(row for row in rows if row["chat_id"] == "1")
        self.assertEqual(latest["first_name"], "A2")

    def test_merge_rows_counts_added_and_updated(self) -> None:
        existing = {
            "1": {"chat_id": "1", "first_name": "Old", "last_name": "", "username": "", "chat_type": "private", "source": "telegram getUpdates"}
        }
        incoming = [
            {"chat_id": "1", "first_name": "New", "last_name": "", "username": "", "chat_type": "private", "source": "telegram getUpdates"},
            {"chat_id": "2", "first_name": "Two", "last_name": "", "username": "", "chat_type": "private", "source": "telegram getUpdates"},
        ]

        rows, added, updated = update_chat_id_map.merge_rows(existing, incoming)

        self.assertEqual((added, updated), (1, 1))
        self.assertEqual(len(rows), 2)

    def test_load_portfolio_and_build_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            portfolio_csv = Path(tmpdir) / "portfolio.csv"
            portfolio_csv.write_text("ticker,shares,avg_cost,target_profit_pct\n2495,4000,36.35,20\n", encoding="utf-8")
            loaded = load_portfolio(portfolio_csv)
            self.assertEqual(loaded.iloc[0]["ticker"], "2495.TW")

        with tempfile.TemporaryDirectory() as tmpdir:
            portfolio_csv = Path(tmpdir) / "portfolio.csv"
            portfolio_csv.write_text("ticker,shares,avg_cost,target_profit_pct\n0050,1408,63.11,50\n00878,4574,21.41,50\n", encoding="utf-8")
            loaded = load_portfolio(portfolio_csv)
            self.assertEqual(loaded.iloc[0]["ticker"], "0050.TW")
            self.assertEqual(loaded.iloc[1]["ticker"], "00878.TW")

        df = pd.DataFrame(
            [
                {
                    "ticker": "2495.TW",
                    "name": "普安",
                    "close": 41.15,
                    "signals": "TREND",
                    "regime": "中段延續中",
                    "risk_score": 3,
                    "ret5_pct": 5.0,
                    "ret20_pct": 15.0,
                    "volume_ratio20": 1.2,
                    "atr_pct": 4.4,
                    "volatility_tag": "活潑",
                }
            ]
        )

        with patch(
            "daily_theme_watchlist.PORTFOLIO",
            pd.DataFrame([{"ticker": "2495.TW", "shares": 4000, "avg_cost": 36.35, "target_profit_pct": 20.0}]),
        ), patch.dict(os.environ, {"REALTIME_QUOTES": "0"}):
            market_regime = {"comment": "加權指數目前偏多", "ret20_pct": 14.0, "volume_ratio20": 1.2, "is_bullish": True}
            us_market = {"summary": "美股昨晚偏強，台股開盤情緒通常較正面。"}
            message = build_portfolio_message(df, market_regime, us_market)

        self.assertIn("持股檢查", message)
        self.assertIn("持股節奏", message)
        self.assertIn("2495", message)
        self.assertIn("進攻持股", message)
        self.assertIn("🔥活潑", message)
        self.assertIn("報酬", message)
        self.assertIn("加碼≤", message)
        self.assertIn("賣出≥", message)
        self.assertIn("跌破逃跑", message)

    def test_portfolio_report_is_separate_from_daily_report(self) -> None:
        df = pd.DataFrame(
            [
                {
                    "rank": 1,
                    "grade": "A",
                    "ticker": "2330.TW",
                    "name": "台積電",
                    "group": "core",
                    "layer": "midlong_core",
                    "regime": "中段延續中",
                    "ret5_pct": 3.0,
                    "ret10_pct": 6.0,
                    "ret20_pct": 12.0,
                    "spec_risk_label": "正常",
                    "signals": "TREND",
                    "volume_ratio20": 1.1,
                    "setup_score": 7,
                    "risk_score": 2,
                    "rank_change": 0,
                    "setup_change": 0,
                    "close": 950.0,
                    "date": "2026-04-17",
                }
            ]
        )
        market_regime = {"comment": "盤勢中性偏多", "ret20_pct": 12.0, "volume_ratio20": 1.1, "is_bullish": True}
        us_market = {"summary": "Nasdaq 小漲"}

        with patch(
            "daily_theme_watchlist.PORTFOLIO",
            pd.DataFrame([{"ticker": "2330.TW", "shares": 1000, "avg_cost": 900.0, "target_profit_pct": 15.0}]),
        ), patch.dict(os.environ, {"REALTIME_QUOTES": "0"}):
            daily_report = build_daily_report_markdown(df, market_regime, None, None)
            portfolio_report = build_portfolio_report_markdown(df, market_regime, us_market)

        self.assertNotIn("## Portfolio Review", daily_report)
        self.assertIn("# Portfolio Review", portfolio_report)
        self.assertIn("Market Scenario", portfolio_report)
        self.assertIn("核心持股", portfolio_report)
        self.assertIn("台積電", portfolio_report)
        self.assertIn("價格帶", portfolio_report)
        self.assertIn("跌破逃跑", portfolio_report)

    def test_portfolio_advice_promotes_low_risk_accel_holding(self) -> None:
        row = pd.Series(
            {
                "ticker": "3013.TW",
                "current_close": 113.0,
                "unrealized_pnl_pct": 8.54,
                "target_profit_pct": 20.0,
                "risk_score": 1,
                "signals": "ACCEL",
                "ret20_pct": 12.68,
                "volume_ratio20": 1.30,
            }
        )

        self.assertEqual(portfolio_advice_label(row), "強勢續抱")

    def test_portfolio_advice_turns_more_defensive_in_high_vol_scenario(self) -> None:
        row = pd.Series(
            {
                "ticker": "3013.TW",
                "current_close": 113.0,
                "unrealized_pnl_pct": 8.54,
                "target_profit_pct": 20.0,
                "risk_score": 2,
                "signals": "ACCEL,TREND",
                "ret20_pct": 12.68,
                "volume_ratio20": 1.30,
            }
        )

        scenario = {"label": "高檔震盪盤", "stance": "邊做邊收"}
        self.assertEqual(portfolio_advice_label(row, scenario), "分批落袋")

    def test_holding_style_marks_etf_and_financial_as_defensive(self) -> None:
        etf_row = pd.Series({"ticker": "0050.TW", "group": "etf"})
        fin_row = pd.Series({"ticker": "2886.TW", "group": "theme"})
        attack_row = pd.Series({"ticker": "3013.TW", "group": "theme", "signals": "ACCEL", "risk_score": 4, "ret20_pct": 16.0})

        self.assertEqual(holding_style_label(etf_row), "防守持股")
        self.assertEqual(holding_style_label(fin_row), "防守持股")
        self.assertEqual(holding_style_label(attack_row), "進攻持股")

    def test_watch_price_plan_produces_price_bands(self) -> None:
        row = pd.Series(
            {
                "ticker": "2356.TW",
                "close": 47.35,
                "ma20": 43.32,
                "ma60": 43.99,
                "ret5_pct": 6.17,
                "ret20_pct": 10.63,
                "risk_score": 1,
                "signals": "ACCEL",
            }
        )
        plan = watch_price_plan(row, "short")
        text = watch_price_plan_text(row, "short")

        self.assertGreater(plan["trim_price"], plan["add_price"])
        self.assertGreater(plan["add_price"], plan["stop_price"])
        self.assertIn("加碼參考", text)
        self.assertIn("減碼參考", text)
        self.assertIn("失效", text)

    def test_watch_price_plan_changes_by_holding_style(self) -> None:
        attack = pd.Series(
            {
                "ticker": "3013.TW",
                "group": "theme",
                "signals": "ACCEL,TREND",
                "close": 100.0,
                "ma20": 95.0,
                "ma60": 92.0,
                "ret5_pct": 9.0,
                "ret20_pct": 14.0,
                "risk_score": 2,
            }
        )
        core = pd.Series(
            {
                "ticker": "2330.TW",
                "group": "core",
                "signals": "TREND",
                "close": 100.0,
                "ma20": 95.0,
                "ma60": 92.0,
                "ret5_pct": 9.0,
                "ret20_pct": 14.0,
                "risk_score": 2,
            }
        )
        defensive = pd.Series(
            {
                "ticker": "0050.TW",
                "group": "etf",
                "signals": "TREND",
                "close": 100.0,
                "ma20": 95.0,
                "ma60": 92.0,
                "ret5_pct": 9.0,
                "ret20_pct": 14.0,
                "risk_score": 2,
            }
        )

        attack_plan = watch_price_plan(attack, "short")
        core_plan = watch_price_plan(core, "short")
        defensive_plan = watch_price_plan(defensive, "short")

        self.assertLessEqual(attack_plan["add_price"], core_plan["add_price"])
        self.assertLessEqual(core_plan["add_price"], defensive_plan["add_price"])
        self.assertLessEqual(attack_plan["trim_price"], core_plan["trim_price"])
        self.assertLessEqual(defensive_plan["trim_price"], core_plan["trim_price"])

    def test_watch_price_plan_uses_atr_to_widen_add_and_stop(self) -> None:
        low_vol = pd.Series(
            {
                "ticker": "3013.TW",
                "group": "theme",
                "signals": "ACCEL,TREND",
                "close": 100.0,
                "ma20": 95.0,
                "ma60": 92.0,
                "ret5_pct": 9.0,
                "ret20_pct": 14.0,
                "risk_score": 2,
                "atr_pct": 2.0,
            }
        )
        high_vol = pd.Series(
            {
                "ticker": "3013.TW",
                "group": "theme",
                "signals": "ACCEL,TREND",
                "close": 100.0,
                "ma20": 95.0,
                "ma60": 92.0,
                "ret5_pct": 9.0,
                "ret20_pct": 14.0,
                "risk_score": 2,
                "atr_pct": 6.0,
            }
        )

        low_plan = watch_price_plan(low_vol, "short")
        high_plan = watch_price_plan(high_vol, "short")

        self.assertLess(high_plan["add_price"], low_plan["add_price"])
        self.assertLess(high_plan["stop_price"], low_plan["stop_price"])
        self.assertEqual(high_plan["trim_price"], low_plan["trim_price"])

    def test_portfolio_advice_flags_high_risk_target_hit(self) -> None:
        row = pd.Series(
            {
                "current_close": 120.0,
                "unrealized_pnl_pct": 25.0,
                "target_profit_pct": 20.0,
                "risk_score": 4,
                "signals": "TREND",
                "ret20_pct": 30.0,
                "volume_ratio20": 1.1,
            }
        )

        self.assertEqual(portfolio_advice_label(row), "達標可落袋")

    def test_portfolio_price_plan_adds_sell_and_escape_prices(self) -> None:
        row = pd.Series(
            {
                "ticker": "3013.TW",
                "current_close": 120.0,
                "avg_cost": 100.0,
                "target_profit_pct": 15.0,
                "unrealized_pnl_pct": 20.0,
                "risk_score": 4,
                "signals": "ACCEL,TREND",
                "ret5_pct": 9.0,
                "ret20_pct": 18.0,
                "volume_ratio20": 1.4,
                "holding_style": "進攻持股",
                "advice": "達標可落袋",
                "ma20": 110.0,
                "ma60": 102.0,
                "atr_pct": 5.0,
            }
        )

        plan = portfolio_price_plan(row, {"label": "高檔震盪盤"})
        row_with_plan = row.copy()
        for key, value in plan.items():
            row_with_plan[key] = value
        text = portfolio_price_plan_text(row_with_plan)

        self.assertGreater(plan["sell_price"], 0)
        self.assertGreaterEqual(plan["add_price"], plan["escape_price"])
        self.assertGreaterEqual(plan["sell_price"], row["current_close"])
        self.assertGreaterEqual(plan["escape_price"], row["avg_cost"])
        self.assertIn("加碼≤", text)
        self.assertIn("賣出≥", text)
        self.assertIn("跌破逃跑", text)


class SelectPushCandidatesTests(unittest.TestCase):
    def test_accel_signal_is_included_in_short_term_candidates(self) -> None:
        df = pd.DataFrame(
            [
                {
                    "rank": 5,
                    "ticker": "TEST1.TW",
                    "name": "Accel Name",
                    "group": "theme",
                    "layer": "midlong_core",
                    "grade": "B",
                    "setup_score": 7,
                    "risk_score": 2,
                    "ret5_pct": 6.0,
                    "ret10_pct": 13.0,
                    "ret20_pct": 6.0,
                    "volume_ratio20": 1.4,
                    "signals": "ACCEL,TREND",
                    "rank_change": 0,
                    "setup_change": 0,
                    "regime": "轉強速度有出來",
                    "date": "2026-04-14",
                    "close": 100.0,
                }
            ]
        )

        out = select_short_term_candidates(df)

        self.assertEqual(len(out), 1)
        self.assertEqual(out.iloc[0]["ticker"], "TEST1.TW")

    def test_midlong_candidates_pick_trend_name(self) -> None:
        df = pd.DataFrame(
            [
                {
                    "rank": 2,
                    "ticker": "MID1.TW",
                    "name": "Trend Name",
                    "group": "theme",
                    "layer": "midlong_core",
                    "grade": "B",
                    "setup_score": 7,
                    "risk_score": 3,
                    "ret5_pct": 3.0,
                    "ret10_pct": 8.0,
                    "ret20_pct": 14.0,
                    "volume_ratio20": 1.1,
                    "signals": "TREND",
                    "rank_change": 1,
                    "setup_change": 1,
                    "regime": "中段延續中",
                    "date": "2026-04-14",
                    "close": 120.0,
                }
            ]
        )

        out = select_midlong_candidates(df)

        self.assertEqual(len(out), 1)
        self.assertEqual(out.iloc[0]["ticker"], "MID1.TW")

    def test_combined_candidates_allow_overlap_between_short_and_midlong(self) -> None:
        df = pd.DataFrame(
            [
                {
                    "rank": 1,
                    "ticker": "BOTH1.TW",
                    "name": "Both Name",
                    "group": "theme",
                    "layer": "midlong_core",
                    "grade": "A",
                    "setup_score": 8,
                    "risk_score": 2,
                    "ret5_pct": 9.0,
                    "ret10_pct": 12.0,
                    "ret20_pct": 15.0,
                    "volume_ratio20": 1.5,
                    "signals": "ACCEL,TREND",
                    "rank_change": 1,
                    "setup_change": 1,
                    "regime": "轉強速度有出來",
                    "date": "2026-04-14",
                    "close": 100.0,
                },
                {
                    "rank": 2,
                    "ticker": "MID2.TW",
                    "name": "Mid Two",
                    "group": "core",
                    "layer": "midlong_core",
                    "grade": "B",
                    "setup_score": 7,
                    "risk_score": 2,
                    "ret5_pct": 2.0,
                    "ret10_pct": 6.0,
                    "ret20_pct": 10.0,
                    "volume_ratio20": 0.8,
                    "signals": "TREND",
                    "rank_change": 1,
                    "setup_change": 1,
                    "regime": "中段延續中",
                    "date": "2026-04-14",
                    "close": 80.0,
                },
            ]
        )

        out = select_push_candidates(df)

        self.assertGreaterEqual(len(out), 3)
        self.assertGreaterEqual(list(out["ticker"]).count("BOTH1.TW"), 2)
        self.assertIn("MID2.TW", list(out["ticker"]))

    def test_select_push_candidates_caps_short_list_in_correction_scenario(self) -> None:
        df = pd.DataFrame(
            [
                {
                    "rank": 1,
                    "ticker": "SHORT1.TW",
                    "name": "Short One",
                    "group": "theme",
                    "layer": "short_attack",
                    "grade": "A",
                    "setup_score": 8,
                    "risk_score": 2,
                    "ret5_pct": 9.0,
                    "ret10_pct": 10.0,
                    "ret20_pct": 12.0,
                    "volume_ratio20": 1.5,
                    "signals": "ACCEL,TREND",
                    "rank_change": 1,
                    "setup_change": 1,
                    "regime": "轉強速度有出來",
                    "date": "2026-04-22",
                    "close": 100.0,
                },
                {
                    "rank": 2,
                    "ticker": "SHORT2.TW",
                    "name": "Short Two",
                    "group": "theme",
                    "layer": "short_attack",
                    "grade": "A",
                    "setup_score": 7,
                    "risk_score": 2,
                    "ret5_pct": 8.0,
                    "ret10_pct": 9.0,
                    "ret20_pct": 10.0,
                    "volume_ratio20": 1.4,
                    "signals": "ACCEL,TREND",
                    "rank_change": 1,
                    "setup_change": 1,
                    "regime": "轉強速度有出來",
                    "date": "2026-04-22",
                    "close": 90.0,
                },
                {
                    "rank": 3,
                    "ticker": "MID1.TW",
                    "name": "Mid One",
                    "group": "theme",
                    "layer": "midlong_core",
                    "grade": "B",
                    "setup_score": 7,
                    "risk_score": 2,
                    "ret5_pct": 2.0,
                    "ret10_pct": 6.0,
                    "ret20_pct": 9.0,
                    "volume_ratio20": 0.9,
                    "signals": "TREND",
                    "rank_change": 1,
                    "setup_change": 1,
                    "regime": "中段延續中",
                    "date": "2026-04-22",
                    "close": 80.0,
                },
            ]
        )

        market_regime = {"comment": "加權回檔", "ret20_pct": 2.0, "volume_ratio20": 0.9, "is_bullish": False}
        us_market = {"summary": "美股昨晚偏弱，科技股續殺。"}

        out = select_push_candidates(df, market_regime, us_market)

        short_names = [ticker for ticker in list(out["ticker"]) if ticker.startswith("SHORT")]
        self.assertEqual(len(short_names), 1)
        self.assertIn("MID1.TW", list(out["ticker"]))

    def test_select_push_candidates_caps_midlong_list_in_correction_scenario(self) -> None:
        df = pd.DataFrame(
            [
                {
                    "rank": 1,
                    "ticker": f"MID{i}.TW",
                    "name": f"Mid {i}",
                    "group": "theme",
                    "layer": "midlong_core",
                    "grade": "B",
                    "setup_score": 7,
                    "risk_score": 2,
                    "ret5_pct": 2.0,
                    "ret10_pct": 6.0,
                    "ret20_pct": 9.0,
                    "volume_ratio20": 0.9,
                    "signals": "TREND",
                    "rank_change": 1,
                    "setup_change": 1,
                    "regime": "中段延續中",
                    "date": "2026-04-22",
                    "close": 80.0 + i,
                }
                for i in range(1, 6)
            ]
        )

        market_regime = {"comment": "加權回檔", "ret20_pct": 2.0, "volume_ratio20": 0.9, "is_bullish": False}
        us_market = {"summary": "美股昨晚偏弱，科技股續殺。"}

        out = select_midlong_candidates(df, market_regime, us_market)

        self.assertEqual(len(out), CONFIG.scenario_policy.correction_midlong_top_n)

    def test_midlong_candidates_allow_lower_volume_ratio_when_trend_is_valid(self) -> None:
        df = pd.DataFrame(
            [
                {
                    "rank": 2,
                    "ticker": "LOWVOL.TW",
                    "name": "Low Vol Trend",
                    "group": "theme",
                    "layer": "midlong_core",
                    "grade": "B",
                    "setup_score": 7,
                    "risk_score": 3,
                    "ret5_pct": 4.0,
                    "ret10_pct": 7.0,
                    "ret20_pct": 12.0,
                    "volume_ratio20": 0.35,
                    "signals": "TREND",
                    "rank_change": 1,
                    "setup_change": 1,
                    "regime": "中段延續中",
                    "date": "2026-04-14",
                    "close": 88.0,
                }
            ]
        )

        out = select_midlong_candidates(df)

        self.assertEqual(len(out), 1)
        self.assertEqual(out.iloc[0]["ticker"], "LOWVOL.TW")


class PushMessageTests(unittest.TestCase):
    def test_macro_message_renders_market_and_us_summary_once(self) -> None:
        market_regime = {"comment": "加權指數目前偏多", "ret20_pct": 14.0, "volume_ratio20": 1.2, "is_bullish": True}
        us_market = {"summary": "美股昨晚偏強，台股開盤情緒通常較正面。", "tech_bias": "美股科技偏強，台股電子股若量價配合可積極一點。"}
        df_rank = pd.DataFrame(
            [
                {"setup_score": 7, "risk_score": 5, "ret5_pct": 16.0, "ret20_pct": 12.0, "volume_ratio20": 1.2, "volatility_tag": "劇烈"},
                {"setup_score": 8, "risk_score": 3, "ret5_pct": 13.0, "ret20_pct": 10.0, "volume_ratio20": 1.1, "volatility_tag": "活潑"},
                {"setup_score": 6, "risk_score": 2, "ret5_pct": 5.0, "ret20_pct": 8.0, "volume_ratio20": 1.0, "volatility_tag": "標準"},
            ]
        )

        message = build_macro_message(market_regime, us_market, df_rank)

        self.assertIn("大盤 / 美股摘要", message)
        self.assertIn("今日盤勢", message)
        self.assertIn("今日策略", message)
        self.assertIn("白話說", message)
        self.assertIn("加權指數目前偏多", message)
        self.assertIn("美股昨晚偏強", message)
        self.assertIn("盤勢情境", message)
        self.assertIn("操作重點", message)
        self.assertIn("出場提醒", message)
        self.assertIn("Heat Bias", message)
        self.assertNotIn("觸發來源", message)
        self.assertIn("台灣時間", message)

    def test_macro_message_uses_correction_copy_for_subscribers(self) -> None:
        market_regime = {"comment": "加權指數轉弱", "ret20_pct": 2.0, "volume_ratio20": 0.9, "is_bullish": False}
        us_market = {"summary": "美股昨晚偏弱，台股早盤要提防開高走低或續殺。"}

        with tempfile.TemporaryDirectory() as tmpdir:
            outcomes_csv = Path(tmpdir) / "reco_outcomes.csv"
            pd.DataFrame([{"scenario_label": "強勢延伸盤", "status": "ok"}]).to_csv(outcomes_csv, index=False)
            with patch.object(dtw, "VERIFICATION_OUTCOMES_CSV", outcomes_csv):
                message = build_macro_message(market_regime, us_market, pd.DataFrame())

        self.assertIn("今日盤勢：明顯修正盤", message)
        self.assertIn("今日策略：先防守，短線名單縮小。", message)
        self.assertIn("白話說：今天先保留資金、少做少追高", message)
        self.assertIn("修正盤驗證提醒", message)

    def test_short_and_midlong_messages_render_independently(self) -> None:
        df = pd.DataFrame(
            [
                {
                    "rank": 1,
                    "ticker": "SHORT1.TW",
                    "name": "Short Name",
                    "group": "theme",
                    "layer": "short_attack",
                    "grade": "A",
                    "setup_score": 8,
                    "risk_score": 2,
                    "ret5_pct": 9.0,
                    "ret10_pct": 12.0,
                    "ret20_pct": 15.0,
                    "volume_ratio20": 1.6,
                    "spec_risk_label": "正常",
                    "signals": "ACCEL,TREND",
                    "rank_change": 1,
                    "setup_change": 1,
                    "status_change": "UP",
                    "regime": "轉強速度有出來",
                    "date": "2026-04-14",
                    "close": 100.0,
                    "atr_pct": 4.8,
                    "volatility_tag": "活潑",
                },
                {
                    "rank": 2,
                    "ticker": "MID1.TW",
                    "name": "Mid Name",
                    "group": "theme",
                    "layer": "midlong_core",
                    "grade": "B",
                    "setup_score": 7,
                    "risk_score": 3,
                    "ret5_pct": 3.0,
                    "ret10_pct": 8.0,
                    "ret20_pct": 14.0,
                    "volume_ratio20": 1.1,
                    "spec_risk_label": "正常",
                    "signals": "TREND",
                    "rank_change": 1,
                    "setup_change": 1,
                    "status_change": "UP",
                    "regime": "中段延續中",
                    "date": "2026-04-14",
                    "close": 120.0,
                    "atr_pct": 1.8,
                    "volatility_tag": "穩健",
                },
                {
                    "rank": 3,
                    "ticker": "MID2.TW",
                    "name": "Mid Two",
                    "group": "core",
                    "layer": "midlong_core",
                    "grade": "B",
                    "setup_score": 6,
                    "risk_score": 2,
                    "ret5_pct": 1.0,
                    "ret10_pct": 4.0,
                    "ret20_pct": 9.0,
                    "volume_ratio20": 0.9,
                    "spec_risk_label": "正常",
                    "signals": "REBREAK",
                    "rank_change": 1,
                    "setup_change": 1,
                    "status_change": "UP",
                    "regime": "重新站上來了",
                    "date": "2026-04-14",
                    "close": 90.0,
                    "atr_pct": 3.2,
                    "volatility_tag": "標準",
                },
            ]
        )

        market_regime = {"comment": "加權指數目前偏多", "ret20_pct": 14.0, "volume_ratio20": 1.2, "is_bullish": True}
        us_market = {"summary": "美股昨晚偏強，台股開盤情緒通常較正面。"}
        short_message = build_short_term_message(df, market_regime, us_market)
        midlong_message = build_midlong_message(df, market_regime, us_market)

        self.assertIn("短線可買", short_message)
        self.assertIn("今天短線策略", short_message)
        self.assertNotIn("美股昨晚偏強", short_message)
        self.assertNotIn("觸發來源", short_message)
        self.assertIn("5日 9.0%", short_message)
        self.assertIn("🔥活潑", short_message)
        self.assertIn("加碼參考", short_message)
        self.assertIn("減碼參考", short_message)
        self.assertIn("失效", short_message)
        self.assertTrue(any(label in short_message for label in ["等拉回", "開高不追", "續抱觀察", "分批落袋"]))

        self.assertIn("中長線可布局", midlong_message)
        self.assertIn("今天中長線策略", midlong_message)
        self.assertNotIn("美股昨晚偏強", midlong_message)
        self.assertNotIn("觸發來源", midlong_message)
        self.assertIn("20日 14.0%", midlong_message)
        self.assertIn("🧊穩健", midlong_message)
        self.assertIn("加碼參考", midlong_message)
        self.assertTrue(any(label in midlong_message for label in ["續抱", "可分批", "觀察", "分批落袋"]))

    def test_macro_message_includes_new_watchlist_spotlight(self) -> None:
        market_regime = {"comment": "加權指數目前偏多", "ret20_pct": 14.0, "volume_ratio20": 1.2, "is_bullish": True}
        us_market = {"summary": "美股昨晚偏強，台股開盤情緒通常較正面。"}
        df_rank = pd.DataFrame(
            [
                {
                    "rank": 7,
                    "ticker": "3491.TWO",
                    "name": "昇達科",
                    "group": "satellite",
                    "layer": "midlong_core",
                    "grade": "B",
                    "setup_score": 6,
                    "risk_score": 2,
                    "ret5_pct": 4.0,
                    "ret10_pct": 8.0,
                    "ret20_pct": 12.0,
                    "volume_ratio20": 1.1,
                    "signals": "TREND",
                    "rank_change": 0,
                    "setup_change": 1,
                    "status_change": "NEW",
                    "regime": "中段延續中",
                    "date": "2026-04-22",
                    "close": 320.0,
                    "atr_pct": 3.1,
                    "volatility_tag": "標準",
                }
            ]
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            prev_rank_csv = Path(tmpdir) / "prev_daily_rank.csv"
            prev_rank_csv.write_text("ticker\n2330.TW\n", encoding="utf-8")
            with patch.object(dtw, "PREV_RANK_CSV", prev_rank_csv):
                message = build_macro_message(market_regime, us_market, df_rank)

        self.assertIn("新加入追蹤觀察", message)
        self.assertIn("昇達科 (3491.TWO)", message)
        self.assertIn("初步看法", message)

    def test_macro_message_compact_briefing_uses_5_5_3_layout(self) -> None:
        market_regime = {"comment": "加權指數目前偏多", "ret20_pct": 14.0, "volume_ratio20": 1.2, "is_bullish": True}
        us_market = {"summary": "美股昨晚偏強，台股開盤情緒通常較正面。"}
        base_rows = [
            {
                "rank": 1,
                "ticker": "A1.TW",
                "name": "主看一",
                "group": "theme",
                "layer": "short_attack",
                "grade": "A",
                "setup_score": 8,
                "risk_score": 2,
                "ret5_pct": 7.0,
                "ret20_pct": 12.0,
                "volume_ratio20": 1.4,
                "signals": "ACCEL,TREND",
                "rank_change": 1,
                "setup_change": 1,
                "spec_risk_label": "正常",
            },
            {
                "rank": 2,
                "ticker": "A2.TW",
                "name": "主看二",
                "group": "theme",
                "layer": "midlong_core",
                "grade": "B",
                "setup_score": 7,
                "risk_score": 2,
                "ret5_pct": 3.0,
                "ret20_pct": 14.0,
                "volume_ratio20": 1.1,
                "signals": "TREND",
                "rank_change": 1,
                "setup_change": 1,
                "spec_risk_label": "正常",
            },
            {
                "rank": 3,
                "ticker": "A3.TW",
                "name": "主看三",
                "group": "theme",
                "layer": "short_attack",
                "grade": "A",
                "setup_score": 8,
                "risk_score": 3,
                "ret5_pct": 8.0,
                "ret20_pct": 13.0,
                "volume_ratio20": 1.5,
                "signals": "ACCEL,TREND",
                "rank_change": 0,
                "setup_change": 1,
                "spec_risk_label": "正常",
            },
            {
                "rank": 4,
                "ticker": "A4.TW",
                "name": "主看四",
                "group": "core",
                "layer": "midlong_core",
                "grade": "B",
                "setup_score": 6,
                "risk_score": 2,
                "ret5_pct": 2.0,
                "ret20_pct": 9.0,
                "volume_ratio20": 1.0,
                "signals": "REBREAK",
                "rank_change": 1,
                "setup_change": 0,
                "spec_risk_label": "正常",
            },
            {
                "rank": 5,
                "ticker": "A5.TW",
                "name": "主看五",
                "group": "satellite",
                "layer": "short_attack",
                "grade": "A",
                "setup_score": 7,
                "risk_score": 2,
                "ret5_pct": 6.0,
                "ret20_pct": 11.0,
                "volume_ratio20": 1.3,
                "signals": "ACCEL,TREND",
                "rank_change": 1,
                "setup_change": 1,
                "spec_risk_label": "正常",
            },
            {
                "rank": 6,
                "ticker": "B1.TW",
                "name": "觀察一",
                "group": "theme",
                "layer": "short_attack",
                "grade": "B",
                "setup_score": 5,
                "risk_score": 3,
                "ret5_pct": 4.0,
                "ret20_pct": 8.0,
                "volume_ratio20": 1.2,
                "signals": "ACCEL",
                "rank_change": 0,
                "setup_change": 1,
                "spec_risk_label": "正常",
            },
            {
                "rank": 7,
                "ticker": "B2.TW",
                "name": "觀察二",
                "group": "theme",
                "layer": "midlong_core",
                "grade": "B",
                "setup_score": 5,
                "risk_score": 3,
                "ret5_pct": 2.0,
                "ret20_pct": 8.0,
                "volume_ratio20": 1.0,
                "signals": "BASE",
                "rank_change": 0,
                "setup_change": 1,
                "spec_risk_label": "正常",
            },
            {
                "rank": 8,
                "ticker": "B3.TW",
                "name": "觀察三",
                "group": "theme",
                "layer": "short_attack",
                "grade": "B",
                "setup_score": 4,
                "risk_score": 3,
                "ret5_pct": 3.0,
                "ret20_pct": 7.0,
                "volume_ratio20": 1.1,
                "signals": "ACCEL",
                "rank_change": 0,
                "setup_change": 1,
                "spec_risk_label": "正常",
            },
            {
                "rank": 9,
                "ticker": "B4.TW",
                "name": "觀察四",
                "group": "satellite",
                "layer": "midlong_core",
                "grade": "B",
                "setup_score": 4,
                "risk_score": 2,
                "ret5_pct": 1.0,
                "ret20_pct": 7.0,
                "volume_ratio20": 1.0,
                "signals": "BASE",
                "rank_change": 0,
                "setup_change": 1,
                "spec_risk_label": "正常",
            },
            {
                "rank": 10,
                "ticker": "B5.TW",
                "name": "觀察五",
                "group": "theme",
                "layer": "short_attack",
                "grade": "B",
                "setup_score": 4,
                "risk_score": 3,
                "ret5_pct": 2.0,
                "ret20_pct": 6.0,
                "volume_ratio20": 1.1,
                "signals": "ACCEL",
                "rank_change": 0,
                "setup_change": 1,
                "spec_risk_label": "正常",
            },
            {
                "rank": 11,
                "ticker": "C1.TW",
                "name": "別追一",
                "group": "theme",
                "layer": "short_attack",
                "grade": "A",
                "setup_score": 8,
                "risk_score": 5,
                "ret5_pct": 18.0,
                "ret20_pct": 28.0,
                "volume_ratio20": 2.0,
                "signals": "ACCEL,TREND",
                "rank_change": 0,
                "setup_change": 1,
                "spec_risk_label": "正常",
            },
            {
                "rank": 12,
                "ticker": "C2.TW",
                "name": "別追二",
                "group": "theme",
                "layer": "midlong_core",
                "grade": "B",
                "setup_score": 7,
                "risk_score": 4,
                "ret5_pct": 8.0,
                "ret20_pct": 26.0,
                "volume_ratio20": 1.5,
                "signals": "TREND",
                "rank_change": 0,
                "setup_change": 1,
                "spec_risk_label": "正常",
            },
            {
                "rank": 13,
                "ticker": "C3.TW",
                "name": "別追三",
                "group": "satellite",
                "layer": "short_attack",
                "grade": "A",
                "setup_score": 8,
                "risk_score": 3,
                "ret5_pct": 10.0,
                "ret20_pct": 18.0,
                "volume_ratio20": 2.3,
                "signals": "ACCEL,TREND",
                "rank_change": 1,
                "setup_change": 1,
                "spec_risk_label": "疑似炒作風險高",
            },
        ]
        df_rank = pd.DataFrame(base_rows)
        short_candidates = pd.DataFrame(base_rows[0:3] + [base_rows[4]])
        midlong_candidates = pd.DataFrame([base_rows[1], base_rows[3]])
        short_backups = pd.DataFrame([base_rows[5], base_rows[7], base_rows[9]])
        midlong_backups = pd.DataFrame([base_rows[6], base_rows[8]])

        with patch.object(
            dtw,
            "build_candidate_sets",
            return_value=(short_candidates, short_backups, midlong_candidates, midlong_backups),
        ):
            message = build_macro_message(market_regime, us_market, df_rank)

        self.assertIn("先看 5 檔", message)
        self.assertIn("觀察 5 檔", message)
        self.assertIn("今天不要追的 3 檔", message)
        self.assertIn("主看一 (A1.TW)", message)
        self.assertIn("觀察五 (B5.TW)", message)
        self.assertIn("別追三 (C3.TW)", message)

    def test_yf_download_one_falls_back_to_two_suffix(self) -> None:
        dates = pd.date_range("2025-01-01", periods=260, freq="B")
        df_hist = pd.DataFrame(
            {
                "Open": [100.0] * 260,
                "High": [101.0] * 260,
                "Low": [99.0] * 260,
                "Close": [100.0] * 260,
                "Volume": [1000.0] * 260,
            },
            index=dates,
        )

        def fake_download(ticker: str, **_: object) -> pd.DataFrame:
            if ticker == "3491.TW":
                return pd.DataFrame()
            if ticker == "3491.TWO":
                return df_hist
            raise AssertionError(f"unexpected ticker {ticker}")

        with patch("daily_theme_watchlist.yf.download", side_effect=fake_download):
            out = yf_download_one("3491.TW", "3y")

        self.assertEqual(len(out), 260)

    def test_build_open_not_chase_shadow_observations_marks_only_normal_rows_eligible(self) -> None:
        df_rank = pd.DataFrame(
            [
                {
                    "rank": 1,
                    "ticker": "AAA.TW",
                    "name": "Alpha",
                    "group": "theme",
                    "layer": "short_attack",
                    "grade": "A",
                    "setup_score": 9,
                    "risk_score": 3,
                    "ret5_pct": 16.0,
                    "ret20_pct": 20.0,
                    "volume_ratio20": 1.8,
                    "signals": "TREND,ACCEL",
                    "rank_change": 0,
                    "setup_change": 1,
                    "spec_risk_label": "正常",
                    "volatility_tag": "活潑",
                },
                {
                    "rank": 2,
                    "ticker": "BBB.TW",
                    "name": "Beta",
                    "group": "theme",
                    "layer": "short_attack",
                    "grade": "A",
                    "setup_score": 8,
                    "risk_score": 3,
                    "ret5_pct": 15.5,
                    "ret20_pct": 19.0,
                    "volume_ratio20": 1.7,
                    "signals": "TREND,ACCEL",
                    "rank_change": 0,
                    "setup_change": 1,
                    "spec_risk_label": "投機偏高",
                    "volatility_tag": "活潑",
                },
            ]
        )
        market_regime = {"ret20_pct": 12.0, "volume_ratio20": 1.1, "is_bullish": True, "session_phase": "postclose"}
        us_market = {"summary": "偏強"}

        out = dtw.build_open_not_chase_shadow_observations(df_rank, market_regime, us_market)

        self.assertEqual(list(out["ticker"]), ["AAA.TW", "BBB.TW"])
        self.assertEqual(str(out.iloc[0]["scenario_label"]), "高檔震盪盤")
        self.assertEqual(str(out.iloc[0]["market_heat"]), "hot")
        self.assertTrue(bool(out.iloc[0]["shadow_eligible"]))
        self.assertFalse(bool(out.iloc[1]["shadow_eligible"]))
        self.assertIn("spec_risk 非 normal", str(out.iloc[1]["shadow_reason"]))

    def test_save_open_not_chase_shadow_observations_writes_local_and_snapshot_files(self) -> None:
        df_rank = pd.DataFrame(
            [
                {
                    "rank": 1,
                    "ticker": "AAA.TW",
                    "name": "Alpha",
                    "group": "theme",
                    "layer": "short_attack",
                    "grade": "A",
                    "setup_score": 9,
                    "risk_score": 3,
                    "ret5_pct": 16.0,
                    "ret20_pct": 20.0,
                    "volume_ratio20": 1.8,
                    "signals": "TREND,ACCEL",
                    "rank_change": 0,
                    "setup_change": 1,
                    "spec_risk_label": "正常",
                    "volatility_tag": "活潑",
                }
            ]
        )
        market_regime = {"ret20_pct": 12.0, "volume_ratio20": 1.1, "is_bullish": True, "session_phase": "postclose"}
        us_market = {"summary": "偏強"}

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            with patch.object(dtw, "SHADOW_OPEN_NOT_CHASE_CSV", tmp / "shadow.csv"), patch.object(
                dtw, "SHADOW_OPEN_NOT_CHASE_MD", tmp / "shadow.md"
            ), patch.object(
                dtw, "SHADOW_OPEN_NOT_CHASE_SNAPSHOTS_CSV", tmp / "shadow_snapshots.csv"
            ), patch.object(
                dtw, "RANK_CSV", tmp / "daily_rank.csv"
            ):
                out = dtw.save_open_not_chase_shadow_observations(df_rank, market_regime, us_market)

            self.assertFalse(out.empty)
            self.assertTrue((tmp / "shadow.csv").exists())
            self.assertTrue((tmp / "shadow.md").exists())
            self.assertTrue((tmp / "shadow_snapshots.csv").exists())
            md = (tmp / "shadow.md").read_text(encoding="utf-8")
            snap = pd.read_csv(tmp / "shadow_snapshots.csv")

        self.assertIn("Shadow Observation", md)
        self.assertIn("AAA.TW", md)
        self.assertEqual(len(snap), 1)
        self.assertEqual(str(snap.iloc[0]["ticker"]), "AAA.TW")

    def test_save_open_not_chase_shadow_observations_writes_header_only_when_empty(self) -> None:
        df_rank = pd.DataFrame(
            [
                {
                    "rank": 1,
                    "ticker": "AAA.TW",
                    "name": "Alpha",
                    "group": "theme",
                    "layer": "short_attack",
                    "grade": "A",
                    "setup_score": 9,
                    "risk_score": 2,
                    "ret5_pct": 6.0,
                    "ret20_pct": 20.0,
                    "volume_ratio20": 1.8,
                    "signals": "TREND,ACCEL",
                    "rank_change": 0,
                    "setup_change": 1,
                    "spec_risk_label": "正常",
                    "volatility_tag": "活潑",
                }
            ]
        )
        market_regime = {"ret20_pct": 12.0, "volume_ratio20": 1.1, "is_bullish": True, "session_phase": "postclose"}
        us_market = {"summary": "偏強"}

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            with patch.object(dtw, "SHADOW_OPEN_NOT_CHASE_CSV", tmp / "shadow.csv"), patch.object(
                dtw, "SHADOW_OPEN_NOT_CHASE_MD", tmp / "shadow.md"
            ), patch.object(
                dtw, "SHADOW_OPEN_NOT_CHASE_SNAPSHOTS_CSV", tmp / "shadow_snapshots.csv"
            ), patch.object(
                dtw, "RANK_CSV", tmp / "daily_rank.csv"
            ):
                out = dtw.save_open_not_chase_shadow_observations(df_rank, market_regime, us_market)

            self.assertTrue(out.empty)
            csv_df = pd.read_csv(tmp / "shadow.csv")
            self.assertEqual(len(csv_df), 0)
            self.assertIn("shadow_status", csv_df.columns)
            self.assertFalse((tmp / "shadow_snapshots.csv").exists())

    def test_special_etf_message_renders_requested_tickers(self) -> None:
        df = pd.DataFrame(
            [
                {
                    "rank": 10,
                    "ticker": "0050.TW",
                    "name": "元大台灣50",
                    "group": "etf",
                    "layer": "midlong_core",
                    "grade": "B",
                    "setup_score": 6,
                    "risk_score": 2,
                    "ret5_pct": 2.0,
                    "ret10_pct": 4.0,
                    "ret20_pct": 8.0,
                    "volume_ratio20": 1.1,
                    "spec_risk_label": "正常",
                    "signals": "TREND",
                    "rank_change": 1,
                    "setup_change": 1,
                    "status_change": "UP",
                    "regime": "中段延續中",
                    "date": "2026-04-14",
                    "close": 100.0,
                },
                {
                    "rank": 11,
                    "ticker": "00878.TW",
                    "name": "國泰永續高股息",
                    "group": "etf",
                    "layer": "midlong_core",
                    "grade": "B",
                    "setup_score": 5,
                    "risk_score": 2,
                    "ret5_pct": 1.0,
                    "ret10_pct": 3.0,
                    "ret20_pct": 4.0,
                    "volume_ratio20": 0.9,
                    "spec_risk_label": "正常",
                    "signals": "REBREAK",
                    "rank_change": 0,
                    "setup_change": 1,
                    "status_change": "UP",
                    "regime": "重新站上來了",
                    "date": "2026-04-14",
                    "close": 22.0,
                },
                {
                    "rank": 12,
                    "ticker": "00772B.TWO",
                    "name": "中信高評級公司債",
                    "group": "etf",
                    "layer": "defensive_watch",
                    "grade": "B",
                    "setup_score": 4,
                    "risk_score": 1,
                    "ret5_pct": 0.3,
                    "ret10_pct": 1.0,
                    "ret20_pct": 3.5,
                    "volume_ratio20": 0.7,
                    "spec_risk_label": "正常",
                    "signals": "TREND",
                    "rank_change": 0,
                    "setup_change": 0,
                    "status_change": "FLAT",
                    "regime": "中段延續中",
                    "date": "2026-04-14",
                    "close": 33.0,
                },
                {
                    "rank": 13,
                    "ticker": "00773B.TWO",
                    "name": "中信優先金融債",
                    "group": "etf",
                    "layer": "defensive_watch",
                    "grade": "B",
                    "setup_score": 4,
                    "risk_score": 1,
                    "ret5_pct": 0.2,
                    "ret10_pct": 0.8,
                    "ret20_pct": 2.5,
                    "volume_ratio20": 0.6,
                    "spec_risk_label": "正常",
                    "signals": "BASE",
                    "rank_change": 0,
                    "setup_change": 0,
                    "status_change": "FLAT",
                    "regime": "低檔慢慢墊高",
                    "date": "2026-04-14",
                    "close": 36.0,
                },
            ]
        )

        market_regime = {"comment": "加權指數目前偏多"}
        us_market = {"summary": "美股昨晚偏強，台股開盤情緒通常較正面。"}
        message = build_special_etf_message(df, market_regime, us_market)

        self.assertIn("ETF / 債券觀察", message)
        self.assertNotIn("觸發來源", message)
        self.assertIn("0050.TW", message)
        self.assertIn("00878.TW", message)
        self.assertIn("00772B.TWO", message)
        self.assertIn("00773B.TWO", message)

    def test_early_gem_message_renders_turning_candidates(self) -> None:
        df = pd.DataFrame(
            [
                {
                    "rank": 6,
                    "ticker": "GEM1.TW",
                    "name": "Gem One",
                    "group": "core",
                    "layer": "midlong_core",
                    "grade": "B",
                    "setup_score": 5,
                    "risk_score": 2,
                    "ret5_pct": 3.0,
                    "ret10_pct": 5.0,
                    "ret20_pct": 8.0,
                    "volume_ratio20": 1.1,
                    "spec_risk_label": "正常",
                    "signals": "REBREAK",
                    "rank_change": 1,
                    "setup_change": 1,
                    "status_change": "UP",
                    "regime": "重新站上來了",
                    "date": "2026-04-14",
                    "close": 55.0,
                    "atr_pct": 2.5,
                    "volatility_tag": "標準",
                }
            ]
        )

        market_regime = {"comment": "加權指數目前偏多"}
        us_market = {"summary": "美股昨晚偏強，台股開盤情緒通常較正面。"}
        message = build_early_gem_message(df, market_regime, us_market)

        self.assertIn("早期轉強觀察", message)
        self.assertNotIn("觸發來源", message)
        self.assertIn("GEM1.TW", message)
        self.assertIn("⚖️標準", message)
        self.assertIn("重新站回結構", message)

    def test_daily_report_markdown_includes_prediction_feedback_section(self) -> None:
        df = pd.DataFrame(
            [
                {
                    "rank": 1,
                    "ticker": "RPT1.TW",
                    "name": "Report One",
                    "group": "theme",
                    "layer": "short_attack",
                    "grade": "A",
                    "setup_score": 8,
                    "risk_score": 2,
                    "ret5_pct": 6.0,
                    "ret10_pct": 9.0,
                    "ret20_pct": 12.0,
                    "volume_ratio20": 1.4,
                    "spec_risk_score": 2,
                    "spec_risk_label": "正常",
                    "spec_risk_note": "結構相對正常",
                    "signals": "ACCEL,TREND",
                    "rank_change": 1,
                    "setup_change": 1,
                    "status_change": "UP",
                    "regime": "轉強速度有出來",
                    "date": "2026-04-14",
                    "close": 100.0,
                }
                ,
                {
                    "rank": 2,
                    "ticker": "RPT2.TW",
                    "name": "Report Two",
                    "group": "theme",
                    "layer": "short_attack",
                    "grade": "C",
                    "setup_score": 7,
                    "risk_score": 6,
                    "ret5_pct": 24.0,
                    "ret10_pct": 28.0,
                    "ret20_pct": 52.0,
                    "volume_ratio20": 3.1,
                    "spec_risk_score": 9,
                    "spec_risk_label": "疑似炒作風險高",
                    "spec_risk_note": "短線急漲、爆量、乖離過大",
                    "signals": "SURGE",
                    "rank_change": 0,
                    "setup_change": 0,
                    "status_change": "NEW",
                    "regime": "有點過熱，別硬追",
                    "date": "2026-04-14",
                    "close": 120.0,
                }
            ]
        )

        report = build_daily_report_markdown(
            df,
            {"comment": "加權指數目前偏多", "ret20_pct": 12.0, "volume_ratio20": 1.2, "is_bullish": True},
            None,
            None,
            {"summary": "美股偏弱"},
        )

        self.assertIn("## Prediction Feedback", report)
        self.assertIn("## Adaptive Strategy Adjustments", report)
        self.assertIn("情境：高檔震盪盤", report)
        self.assertIn("## 疑似炒作觀察", report)
        self.assertIn("RPT2.TW", report)

    def test_main_applies_scenario_adjusted_strategy_to_watchlist(self) -> None:
        df = pd.DataFrame(
            [
                {
                    "rank": 1,
                    "ticker": "RPT1.TW",
                    "name": "Report One",
                    "group": "theme",
                    "layer": "short_attack",
                    "grade": "A",
                    "setup_score": 8,
                    "risk_score": 2,
                    "ret5_pct": 6.0,
                    "ret10_pct": 9.0,
                    "ret20_pct": 12.0,
                    "volume_ratio20": 1.4,
                    "spec_risk_label": "正常",
                    "signals": "ACCEL,TREND",
                    "rank_change": 1,
                    "setup_change": 1,
                    "status_change": "UP",
                    "regime": "轉強速度有出來",
                    "date": "2026-04-14",
                    "close": 100.0,
                }
            ]
        )
        market_regime = {"comment": "加權指數目前偏多", "ret20_pct": 12.0, "volume_ratio20": 1.2, "is_bullish": True}
        us_market = {"summary": "美股偏弱"}

        with patch("daily_theme_watchlist.load_last_success_date", return_value=""), patch(
            "daily_theme_watchlist.current_run_signature", return_value="sig"
        ), patch("daily_theme_watchlist.get_market_regime", return_value=market_regime), patch(
            "daily_theme_watchlist.get_us_market_reference", return_value=us_market
        ), patch("daily_theme_watchlist.run_watchlist", return_value=df) as run_watchlist_mock, patch(
            "daily_theme_watchlist.run_backtest_dual", return_value=(None, None)
        ), patch("daily_theme_watchlist.upsert_alert_tracking"), patch(
            "daily_theme_watchlist.save_reports"
        ), patch("daily_theme_watchlist.build_state", return_value="state"), patch(
            "daily_theme_watchlist.load_last_state", return_value=""
        ), patch("daily_theme_watchlist.should_alert", return_value=False), patch(
            "daily_theme_watchlist.save_last_state"
        ), patch("daily_theme_watchlist.save_last_success_date"), patch(
            "daily_theme_watchlist.logger"
        ):
            result = watchlist_main()

        self.assertEqual(result, 0)
        _, kwargs = run_watchlist_mock.call_args
        self.assertIn("strat", kwargs)
        self.assertGreater(kwargs["strat"].rebreak_vol_ratio, CONFIG.strategy.rebreak_vol_ratio)

    def test_main_does_not_send_special_etf_telegram_message(self) -> None:
        df = pd.DataFrame(
            [
                {
                    "rank": 1,
                    "ticker": "TEST1.TW",
                    "name": "Test One",
                    "group": "theme",
                    "layer": "short_attack",
                    "grade": "A",
                    "setup_score": 8,
                    "risk_score": 2,
                    "ret5_pct": 9.0,
                    "ret10_pct": 12.0,
                    "ret20_pct": 15.0,
                    "volume_ratio20": 1.6,
                    "signals": "ACCEL,TREND",
                    "rank_change": 1,
                    "setup_change": 1,
                    "status_change": "UP",
                    "regime": "轉強速度有出來",
                    "date": "2026-04-22",
                    "close": 100.0,
                    "spec_risk_label": "正常",
                    "atr_pct": 4.8,
                    "volatility_tag": "活潑",
                }
            ]
        )
        market_regime = {"comment": "加權指數目前偏多", "ret20_pct": 12.0, "volume_ratio20": 1.2, "is_bullish": True}
        us_market = {"summary": "美股偏強"}

        with patch("daily_theme_watchlist.load_last_success_date", return_value=""), patch(
            "daily_theme_watchlist.current_run_signature", return_value="sig"
        ), patch("daily_theme_watchlist.get_market_regime", return_value=market_regime), patch(
            "daily_theme_watchlist.get_us_market_reference", return_value=us_market
        ), patch("daily_theme_watchlist.run_watchlist", return_value=df), patch(
            "daily_theme_watchlist.run_backtest_dual", return_value=(None, None)
        ), patch("daily_theme_watchlist.upsert_alert_tracking"), patch(
            "daily_theme_watchlist.save_reports"
        ), patch("daily_theme_watchlist.build_state", return_value="state"), patch(
            "daily_theme_watchlist.load_last_state", return_value=""
        ), patch("daily_theme_watchlist.should_alert", return_value=True), patch(
            "daily_theme_watchlist.save_last_state"
        ), patch("daily_theme_watchlist.save_last_success_date"), patch(
            "daily_theme_watchlist.send_telegram_message"
        ) as send_mock, patch("daily_theme_watchlist.logger"):
            result = watchlist_main()

        self.assertEqual(result, 0)
        self.assertEqual(send_mock.call_count, 4)
        sent_messages = [call.args[0] for call in send_mock.call_args_list]
        self.assertTrue(all("ETF / 債券觀察" not in msg for msg in sent_messages))


class SplitMessageTests(unittest.TestCase):
    def test_split_message_respects_limit(self) -> None:
        message = "line1\nline2\nline3\nline4"

        parts = split_message(message, limit=12)

        self.assertGreater(len(parts), 1)
        for part in parts:
            self.assertLessEqual(len(part), 12)

    def test_split_message_keeps_short_message_intact(self) -> None:
        message = "short text"

        parts = split_message(message, limit=100)

        self.assertEqual(parts, [message])


class PortfolioStepTests(unittest.TestCase):
    def test_portfolio_step_applies_scenario_adjusted_strategy(self) -> None:
        market_regime = {"comment": "加權指數目前偏多", "ret20_pct": 12.0, "volume_ratio20": 1.2, "is_bullish": True, "session_phase": "postclose"}
        us_market = {"summary": "美股偏弱"}
        df_rank = pd.DataFrame(
            [
                {
                    "rank": 1,
                    "ticker": "2330.TW",
                    "name": "台積電",
                    "group": "core",
                    "layer": "midlong_core",
                    "grade": "B",
                    "setup_score": 7,
                    "risk_score": 2,
                    "ret5_pct": 4.0,
                    "ret10_pct": 6.0,
                    "ret20_pct": 10.0,
                    "volume_ratio20": 1.1,
                    "signals": "TREND",
                    "rank_change": 1,
                    "setup_change": 1,
                    "status_change": "UP",
                    "regime": "中段延續中",
                    "date": "2026-04-22",
                    "close": 950.0,
                    "spec_risk_label": "正常",
                    "atr_pct": 2.0,
                    "volatility_tag": "標準",
                }
            ]
        )

        with patch.object(dtw, "PORTFOLIO", pd.DataFrame([{"ticker": "2330.TW", "shares": 1, "avg_cost": 900, "target_profit_pct": 15}])), patch(
            "daily_theme_watchlist.get_market_regime", return_value=market_regime
        ), patch("daily_theme_watchlist.get_us_market_reference", return_value=us_market), patch(
            "daily_theme_watchlist.run_watchlist", return_value=df_rank
        ) as run_watchlist_mock, patch("daily_theme_watchlist.save_portfolio_reports"), patch(
            "builtins.print"
        ), patch("daily_theme_watchlist.logger"):
            result = local_daily_module.run_portfolio_step()

        self.assertEqual(result, 0)
        _, kwargs = run_watchlist_mock.call_args
        self.assertIn("strat", kwargs)
        self.assertGreater(kwargs["strat"].rebreak_vol_ratio, CONFIG.strategy.rebreak_vol_ratio)

    def test_portfolio_step_writes_runtime_metrics(self) -> None:
        market_regime = {"comment": "加權指數目前偏多", "ret20_pct": 12.0, "volume_ratio20": 1.2, "is_bullish": True, "session_phase": "postclose"}
        us_market = {"summary": "美股偏弱"}
        df_rank = pd.DataFrame(
            [
                {
                    "rank": 1,
                    "ticker": "2330.TW",
                    "name": "台積電",
                    "group": "core",
                    "layer": "midlong_core",
                    "grade": "B",
                    "setup_score": 7,
                    "risk_score": 2,
                    "ret5_pct": 4.0,
                    "ret10_pct": 6.0,
                    "ret20_pct": 10.0,
                    "volume_ratio20": 1.1,
                    "signals": "TREND",
                    "rank_change": 1,
                    "setup_change": 1,
                    "status_change": "UP",
                    "regime": "中段延續中",
                    "date": "2026-04-22",
                    "close": 950.0,
                    "spec_risk_label": "正常",
                    "atr_pct": 2.0,
                    "volatility_tag": "標準",
                }
            ]
        )

        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            dtw, "PORTFOLIO", pd.DataFrame([{"ticker": "2330.TW", "shares": 1, "avg_cost": 900, "target_profit_pct": 15}])
        ), patch("daily_theme_watchlist.get_market_regime", return_value=market_regime), patch(
            "daily_theme_watchlist.get_us_market_reference", return_value=us_market
        ), patch("daily_theme_watchlist.run_watchlist", return_value=df_rank), patch(
            "daily_theme_watchlist.save_portfolio_reports"
        ), patch(
            "builtins.print"
        ), patch("daily_theme_watchlist.logger"), patch.object(
            local_daily_module, "PORTFOLIO_RUNTIME_METRICS_MD", Path(tmpdir) / "portfolio_runtime_metrics.md"
        ), patch.object(
            local_daily_module, "PORTFOLIO_RUNTIME_METRICS_JSON", Path(tmpdir) / "portfolio_runtime_metrics.json"
        ):
            result = local_daily_module.run_portfolio_step()
            payload = json.loads((Path(tmpdir) / "portfolio_runtime_metrics.json").read_text(encoding="utf-8"))

        self.assertEqual(result, 0)
        self.assertEqual(payload["status"], "ok")
        self.assertIn("watchlist", payload["step_timings"])


if __name__ == "__main__":
    unittest.main()
