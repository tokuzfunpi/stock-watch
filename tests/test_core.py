from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from daily_theme_watchlist import (
    add_indicators,
    apply_feedback_adjustment,
    build_feedback_summary,
    build_daily_report_markdown,
    build_early_gem_message,
    build_macro_message,
    build_portfolio_message,
    build_portfolio_report_markdown,
    build_special_etf_message,
    build_midlong_message,
    build_short_term_message,
    detect_row,
    grade_signal,
    load_portfolio,
    normalize_ticker_symbol,
    speculative_risk_label,
    speculative_risk_score,
    select_midlong_candidates,
    select_short_term_candidates,
    select_push_candidates,
    split_message,
    sync_watchlist_with_portfolio,
)


class DetectRowTests(unittest.TestCase):
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
        self.assertIn("date", out)


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

                candidates = pd.DataFrame(
                    [
                        {"ticker": "PULL.TW", "risk_score": 2, "ret5_pct": 10.0, "volume_ratio20": 1.1, "signals": "", "setup_change": 0, "rank_change": 0},
                        {"ticker": "CHASE.TW", "risk_score": 2, "ret5_pct": 6.0, "volume_ratio20": 1.4, "signals": "ACCEL", "setup_change": 0, "rank_change": 0},
                    ]
                )

                adjusted = apply_feedback_adjustment(candidates, "short")

                self.assertEqual(adjusted.iloc[0]["ticker"], "CHASE.TW")
                self.assertIn("feedback_label", adjusted.columns)


class PortfolioTests(unittest.TestCase):
    def test_normalize_ticker_symbol_supports_plain_codes(self) -> None:
        self.assertEqual(normalize_ticker_symbol("2495"), "2495.TW")
        self.assertEqual(normalize_ticker_symbol("00772B"), "00772B.TWO")

    def test_sync_watchlist_with_portfolio_adds_missing_symbols(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            watchlist_csv = Path(tmpdir) / "watchlist.csv"
            portfolio_csv = Path(tmpdir) / "portfolio.csv"
            watchlist_csv.write_text("ticker,name,group,layer,enabled\n2495.TW,2495,core,midlong_core,true\n", encoding="utf-8")
            portfolio_csv.write_text("ticker,shares,avg_cost,target_profit_pct\n2330,1000,950,15\n00772B,1000,35,10\n", encoding="utf-8")

            added = sync_watchlist_with_portfolio(watchlist_csv, portfolio_csv)

            self.assertEqual(added, ["2330.TW", "00772B.TWO"])
            content = watchlist_csv.read_text(encoding="utf-8")
            self.assertIn("2330.TW", content)
            self.assertIn("00772B.TWO", content)

    def test_load_portfolio_and_build_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            portfolio_csv = Path(tmpdir) / "portfolio.csv"
            portfolio_csv.write_text("ticker,shares,avg_cost,target_profit_pct\n2495,4000,36.35,20\n", encoding="utf-8")
            loaded = load_portfolio(portfolio_csv)
            self.assertEqual(loaded.iloc[0]["ticker"], "2495.TW")

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
                }
            ]
        )

        with patch(
            "daily_theme_watchlist.PORTFOLIO",
            pd.DataFrame([{"ticker": "2495.TW", "shares": 4000, "avg_cost": 36.35, "target_profit_pct": 20.0}]),
        ):
            message = build_portfolio_message(df)

        self.assertIn("持股檢查", message)
        self.assertIn("2495", message)
        self.assertIn("報酬", message)

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
        market_regime = {"comment": "盤勢中性偏多"}
        us_market = {"summary": "Nasdaq 小漲"}

        with patch(
            "daily_theme_watchlist.PORTFOLIO",
            pd.DataFrame([{"ticker": "2330.TW", "shares": 1000, "avg_cost": 900.0, "target_profit_pct": 15.0}]),
        ):
            daily_report = build_daily_report_markdown(df, market_regime, None, None)
            portfolio_report = build_portfolio_report_markdown(df, market_regime, us_market)

        self.assertNotIn("## Portfolio Review", daily_report)
        self.assertIn("# Portfolio Review", portfolio_report)
        self.assertIn("台積電", portfolio_report)


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
                    "setup_score": 6,
                    "risk_score": 3,
                    "ret5_pct": 6.0,
                    "ret10_pct": 13.0,
                    "ret20_pct": 4.0,
                    "volume_ratio20": 1.4,
                    "signals": "ACCEL",
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
                    "risk_score": 3,
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
        market_regime = {"comment": "加權指數目前偏多"}
        us_market = {"summary": "美股昨晚偏強，台股開盤情緒通常較正面。", "tech_bias": "美股科技偏強，台股電子股若量價配合可積極一點。"}

        message = build_macro_message(market_regime, us_market)

        self.assertIn("大盤 / 美股摘要", message)
        self.assertIn("加權指數目前偏多", message)
        self.assertIn("美股昨晚偏強", message)
        self.assertIn("觸發來源", message)
        self.assertIn("台灣時間", message)

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
                    "risk_score": 3,
                    "ret5_pct": 9.0,
                    "ret10_pct": 12.0,
                    "ret20_pct": 15.0,
                    "volume_ratio20": 1.6,
                    "spec_risk_label": "正常",
                    "signals": "ACCEL",
                    "rank_change": 1,
                    "setup_change": 1,
                    "status_change": "UP",
                    "regime": "轉強速度有出來",
                    "date": "2026-04-14",
                    "close": 100.0,
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
                },
            ]
        )

        market_regime = {"comment": "加權指數目前偏多"}
        us_market = {"summary": "美股昨晚偏強，台股開盤情緒通常較正面。"}
        short_message = build_short_term_message(df, market_regime, us_market)
        midlong_message = build_midlong_message(df, market_regime, us_market)

        self.assertIn("短線可買", short_message)
        self.assertNotIn("美股昨晚偏強", short_message)
        self.assertNotIn("觸發來源", short_message)
        self.assertIn("5日 9.0%", short_message)
        self.assertTrue(any(label in short_message for label in ["可追", "等拉回", "開高不追", "續抱觀察", "分批落袋"]))

        self.assertIn("中長線可布局", midlong_message)
        self.assertNotIn("美股昨晚偏強", midlong_message)
        self.assertNotIn("觸發來源", midlong_message)
        self.assertIn("20日 14.0%", midlong_message)
        self.assertTrue(any(label in midlong_message for label in ["續抱", "可分批", "觀察", "分批落袋"]))

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
                }
            ]
        )

        market_regime = {"comment": "加權指數目前偏多"}
        us_market = {"summary": "美股昨晚偏強，台股開盤情緒通常較正面。"}
        message = build_early_gem_message(df, market_regime, us_market)

        self.assertIn("早期轉強觀察", message)
        self.assertNotIn("觸發來源", message)
        self.assertIn("GEM1.TW", message)
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

        report = build_daily_report_markdown(df, {"comment": "加權指數目前偏多"}, None, None)

        self.assertIn("## Prediction Feedback", report)


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


if __name__ == "__main__":
    unittest.main()
