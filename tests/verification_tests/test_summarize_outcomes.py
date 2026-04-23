from __future__ import annotations

import unittest
from datetime import datetime

import pandas as pd

from daily_theme_watchlist import LOCAL_TZ
from verification.summarize_outcomes import (
    build_atr_band_findings,
    build_key_findings,
    build_summary_markdown,
    summarize_atr_band_checkpoints,
    summarize_outcomes,
)


class SummarizeOutcomesTests(unittest.TestCase):
    def test_summarize_outcomes_filters_non_ok_rows(self) -> None:
        df = pd.DataFrame(
            [
                {
                    "signal_date": "2026-04-17",
                    "horizon_days": 1,
                    "watch_type": "short",
                    "reco_status": "ok",
                    "action": "等拉回",
                    "realized_ret_pct": 1.2,
                    "status": "ok",
                },
                {
                    "signal_date": "2026-04-17",
                    "horizon_days": 1,
                    "watch_type": "short",
                    "reco_status": "below_threshold",
                    "action": "等拉回",
                    "realized_ret_pct": -0.5,
                    "status": "no_price_series",
                },
            ]
        )
        parts = summarize_outcomes(df)
        self.assertEqual(int(parts["by_action"].iloc[0]["n"]), 1)

    def test_delta_ok_minus_below_is_computed(self) -> None:
        df = pd.DataFrame(
            [
                {
                    "signal_date": "2026-04-17",
                    "horizon_days": 1,
                    "watch_type": "short",
                    "reco_status": "ok",
                    "market_heat": "normal",
                    "scenario_label": "高檔震盪盤",
                    "action": "等拉回",
                    "realized_ret_pct": 1.0,
                    "status": "ok",
                },
                {
                    "signal_date": "2026-04-17",
                    "horizon_days": 1,
                    "watch_type": "short",
                    "reco_status": "below_threshold",
                    "market_heat": "hot",
                    "scenario_label": "高檔震盪盤",
                    "action": "等拉回",
                    "realized_ret_pct": 0.0,
                    "status": "ok",
                },
            ]
        )
        parts = summarize_outcomes(df)
        delta = parts["delta_ok_minus_below"]
        self.assertFalse(delta.empty)
        self.assertIn("delta_avg_ret", delta.columns)
        self.assertIn("confidence", delta.columns)
        self.assertIn("min_n", delta.columns)
        self.assertIn("overall_by_market_heat", parts)
        self.assertFalse(parts["overall_by_market_heat"].empty)

    def test_heat_bias_check_is_computed(self) -> None:
        df = pd.DataFrame(
            [
                {
                    "signal_date": "2026-04-17",
                    "horizon_days": 1,
                    "watch_type": "short",
                    "reco_status": "ok",
                    "market_heat": "hot",
                    "scenario_label": "強勢延伸盤",
                    "action": "等拉回",
                    "realized_ret_pct": 3.0,
                    "status": "ok",
                },
                {
                    "signal_date": "2026-04-16",
                    "horizon_days": 1,
                    "watch_type": "short",
                    "reco_status": "ok",
                    "market_heat": "normal",
                    "scenario_label": "高檔震盪盤",
                    "action": "等拉回",
                    "realized_ret_pct": 1.0,
                    "status": "ok",
                },
            ]
        )
        parts = summarize_outcomes(df)
        heat = parts["heat_bias_check"]
        self.assertFalse(heat.empty)
        self.assertIn("delta_avg_ret_hot_minus_normal", heat.columns)
        self.assertEqual(float(heat.iloc[0]["delta_avg_ret_hot_minus_normal"]), 2.0)
        self.assertIn("overall_by_scenario", parts)
        self.assertFalse(parts["overall_by_scenario"].empty)
        self.assertIn("overall_by_scenario_action", parts)
        self.assertFalse(parts["overall_by_scenario_action"].empty)

    def test_heat_bias_by_scenario_and_date_are_computed(self) -> None:
        df = pd.DataFrame(
            [
                {
                    "signal_date": "2026-04-17",
                    "horizon_days": 5,
                    "watch_type": "midlong",
                    "reco_status": "ok",
                    "market_heat": "hot",
                    "scenario_label": "強勢延伸盤",
                    "action": "續抱",
                    "realized_ret_pct": 5.0,
                    "status": "ok",
                },
                {
                    "signal_date": "2026-04-17",
                    "horizon_days": 5,
                    "watch_type": "midlong",
                    "reco_status": "ok",
                    "market_heat": "normal",
                    "scenario_label": "強勢延伸盤",
                    "action": "續抱",
                    "realized_ret_pct": 1.0,
                    "status": "ok",
                },
            ]
        )
        parts = summarize_outcomes(df)
        self.assertFalse(parts["heat_bias_by_scenario"].empty)
        self.assertFalse(parts["heat_bias_by_date"].empty)
        self.assertEqual(float(parts["heat_bias_by_scenario"].iloc[0]["delta_avg_ret_hot_minus_normal"]), 4.0)
        self.assertEqual(float(parts["heat_bias_by_date"].iloc[0]["delta_avg_ret_hot_minus_normal"]), 4.0)

    def test_build_summary_markdown_renders_sections(self) -> None:
        df = pd.DataFrame(
            [
                {
                    "signal_date": "2026-04-17",
                    "horizon_days": 1,
                    "watch_type": "midlong",
                    "reco_status": "ok",
                    "market_heat": "warm",
                    "scenario_label": "權值撐盤、個股轉弱",
                    "action": "續抱",
                    "realized_ret_pct": -2.0,
                    "status": "ok",
                },
                {
                    "signal_date": "2026-04-16",
                    "horizon_days": 1,
                    "watch_type": "short",
                    "reco_status": "ok",
                    "market_heat": "hot",
                    "scenario_label": "強勢延伸盤",
                    "action": "等拉回",
                    "realized_ret_pct": 3.0,
                    "status": "ok",
                },
                {
                    "signal_date": "2026-04-16",
                    "horizon_days": 1,
                    "watch_type": "short",
                    "reco_status": "ok",
                    "market_heat": "normal",
                    "scenario_label": "強勢延伸盤",
                    "action": "等拉回",
                    "realized_ret_pct": 1.0,
                    "status": "ok",
                },
            ]
        )
        md = build_summary_markdown(df, source="verification/watchlist_daily/reco_outcomes.csv", now_local=datetime(2026, 4, 21, 8, 50, tzinfo=LOCAL_TZ))
        self.assertIn("# Recommendation Outcomes Summary", md)
        self.assertIn("## Coverage", md)
        self.assertIn("## Scenario Coverage", md)
        self.assertIn("## Notes", md)
        self.assertIn("## Key Findings", md)
        self.assertIn("market_heat", md)
        self.assertIn("## Overall By Market Heat", md)
        self.assertIn("## Overall By Scenario", md)
        self.assertIn("## Heat Bias Check (hot - normal)", md)
        self.assertIn("## Heat Bias By Scenario (hot - normal)", md)
        self.assertIn("## Heat Bias By Date (hot - normal, top 20)", md)
        self.assertIn("## Weekly Checkpoint", md)
        self.assertIn("## Overall By Action", md)
        self.assertIn("## Overall By Scenario + Action", md)
        self.assertIn("reco_status", md)
        self.assertIn("## By Action", md)

    def test_build_key_findings_summarizes_heat_and_scenario(self) -> None:
        df = pd.DataFrame(
            [
                {
                    "signal_date": "2026-04-20",
                    "horizon_days": 5,
                    "watch_type": "midlong",
                    "reco_status": "ok",
                    "market_heat": "hot",
                    "scenario_label": "強勢延伸盤",
                    "action": "續抱",
                    "realized_ret_pct": 13.0,
                    "status": "ok",
                },
                {
                    "signal_date": "2026-04-19",
                    "horizon_days": 5,
                    "watch_type": "midlong",
                    "reco_status": "ok",
                    "market_heat": "hot",
                    "scenario_label": "強勢延伸盤",
                    "action": "續抱",
                    "realized_ret_pct": 11.0,
                    "status": "ok",
                },
                {
                    "signal_date": "2026-04-18",
                    "horizon_days": 5,
                    "watch_type": "midlong",
                    "reco_status": "ok",
                    "market_heat": "hot",
                    "scenario_label": "強勢延伸盤",
                    "action": "續抱",
                    "realized_ret_pct": 12.0,
                    "status": "ok",
                },
                {
                    "signal_date": "2026-04-20",
                    "horizon_days": 5,
                    "watch_type": "midlong",
                    "reco_status": "ok",
                    "market_heat": "normal",
                    "scenario_label": "強勢延伸盤",
                    "action": "續抱",
                    "realized_ret_pct": 4.0,
                    "status": "ok",
                },
                {
                    "signal_date": "2026-04-19",
                    "horizon_days": 5,
                    "watch_type": "midlong",
                    "reco_status": "ok",
                    "market_heat": "normal",
                    "scenario_label": "強勢延伸盤",
                    "action": "續抱",
                    "realized_ret_pct": 5.0,
                    "status": "ok",
                },
                {
                    "signal_date": "2026-04-18",
                    "horizon_days": 5,
                    "watch_type": "midlong",
                    "reco_status": "ok",
                    "market_heat": "normal",
                    "scenario_label": "強勢延伸盤",
                    "action": "續抱",
                    "realized_ret_pct": 3.0,
                    "status": "ok",
                },
            ]
        )
        findings = build_key_findings(summarize_outcomes(df))
        self.assertTrue(findings)
        joined = "\n".join(findings)
        self.assertIn("5D midlong", joined)
        self.assertIn("強勢延伸盤", joined)
        self.assertIn("2026-04-20", joined)

    def test_summarize_atr_band_checkpoints_tracks_maturity_and_levels(self) -> None:
        alert_tracking = pd.DataFrame(
            [
                {
                    "alert_close": 100.0,
                    "add_price": 95.0,
                    "trim_price": 106.0,
                    "stop_price": 92.0,
                    "watch_type": "short",
                    "ret1_future_pct": 8.0,
                    "ret5_future_pct": None,
                    "ret20_future_pct": None,
                },
                {
                    "alert_close": 100.0,
                    "add_price": 94.0,
                    "trim_price": 108.0,
                    "stop_price": 92.0,
                    "watch_type": "short",
                    "ret1_future_pct": -9.0,
                    "ret5_future_pct": None,
                    "ret20_future_pct": None,
                },
                {
                    "alert_close": 200.0,
                    "add_price": 190.0,
                    "trim_price": 214.0,
                    "stop_price": 184.0,
                    "watch_type": "midlong",
                    "ret1_future_pct": None,
                    "ret5_future_pct": None,
                    "ret20_future_pct": None,
                },
            ]
        )
        parts = summarize_atr_band_checkpoints(alert_tracking)
        self.assertFalse(parts["band_coverage"].empty)
        self.assertFalse(parts["band_checkpoints"].empty)
        short_1d = parts["band_checkpoints"][
            (parts["band_checkpoints"]["horizon_days"] == 1)
            & (parts["band_checkpoints"]["watch_type"] == "short")
        ].iloc[0]
        self.assertEqual(int(short_1d["closed_above_trim"]), 1)
        self.assertEqual(int(short_1d["closed_below_stop"]), 1)

    def test_build_atr_band_findings_reports_insufficient_maturity(self) -> None:
        alert_tracking = pd.DataFrame(
            [
                {
                    "alert_close": 100.0,
                    "add_price": 95.0,
                    "trim_price": 106.0,
                    "stop_price": 92.0,
                    "watch_type": "short",
                    "ret1_future_pct": 1.0,
                    "ret5_future_pct": None,
                    "ret20_future_pct": None,
                }
            ]
        )
        findings = build_atr_band_findings(summarize_atr_band_checkpoints(alert_tracking))
        self.assertTrue(findings)
        self.assertIn("5D", "\n".join(findings))
        self.assertIn("還沒有成熟資料", "\n".join(findings))
