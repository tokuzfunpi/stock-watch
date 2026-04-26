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
        self.assertIn("threshold_guard_check", parts)
        self.assertFalse(parts["threshold_guard_check"].empty)
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

    def test_summarize_outcomes_builds_signal_template_slices(self) -> None:
        df = pd.DataFrame(
            [
                {
                    "signal_date": "2026-04-17",
                    "horizon_days": 5,
                    "watch_type": "midlong",
                    "reco_status": "ok",
                    "market_heat": "hot",
                    "scenario_label": "強勢延伸盤",
                    "signals": "ACCEL,TREND",
                    "spec_risk_score": 7,
                    "spec_risk_label": "疑似炒作風險高",
                    "action": "續抱",
                    "realized_ret_pct": 5.0,
                    "status": "ok",
                },
                {
                    "signal_date": "2026-04-16",
                    "horizon_days": 5,
                    "watch_type": "midlong",
                    "reco_status": "ok",
                    "market_heat": "normal",
                    "scenario_label": "強勢延伸盤",
                    "signals": "ACCEL,TREND",
                    "spec_risk_score": 1,
                    "spec_risk_label": "正常",
                    "action": "續抱",
                    "realized_ret_pct": 3.0,
                    "status": "ok",
                },
                {
                    "signal_date": "2026-04-16",
                    "horizon_days": 5,
                    "watch_type": "midlong",
                    "reco_status": "ok",
                    "market_heat": "normal",
                    "scenario_label": "高檔震盪盤",
                    "signals": "REBREAK",
                    "spec_risk_score": 0,
                    "spec_risk_label": "正常",
                    "action": "續抱",
                    "realized_ret_pct": 1.0,
                    "status": "ok",
                },
                {
                    "signal_date": "2026-04-15",
                    "horizon_days": 5,
                    "watch_type": "midlong",
                    "reco_status": "ok",
                    "market_heat": "hot",
                    "scenario_label": "強勢延伸盤",
                    "signals": "ACCEL",
                    "spec_risk_score": 8,
                    "spec_risk_label": "疑似炒作風險高",
                    "action": "續抱",
                    "realized_ret_pct": -2.0,
                    "status": "ok",
                },
            ]
        )
        parts = summarize_outcomes(df)
        self.assertIn("overall_by_signal_template", parts)
        self.assertFalse(parts["overall_by_signal_template"].empty)
        self.assertIn("signal_template", parts["overall_by_signal_template"].columns)
        self.assertIn("Momentum Leader", parts["overall_by_signal_template"]["signal_template"].tolist())
        self.assertIn("overall_by_scenario_template", parts)
        self.assertFalse(parts["overall_by_scenario_template"].empty)
        self.assertIn("overall_by_spec_risk", parts)
        self.assertFalse(parts["overall_by_spec_risk"].empty)
        self.assertIn("spec_risk_bucket", parts["overall_by_spec_risk"].columns)
        self.assertIn("overall_by_spec_subtype", parts)
        self.assertFalse(parts["overall_by_spec_subtype"].empty)
        self.assertIn("spec_risk_subtype", parts["overall_by_spec_subtype"].columns)
        self.assertIn("spec_risk_check", parts)
        self.assertFalse(parts["spec_risk_check"].empty)

    def test_summarize_outcomes_backfills_spec_risk_bucket_from_legacy_fields(self) -> None:
        df = pd.DataFrame(
            [
                {
                    "signal_date": "2026-04-17",
                    "horizon_days": 1,
                    "watch_type": "short",
                    "reco_status": "ok",
                    "market_heat": "hot",
                    "scenario_label": "強勢延伸盤",
                    "signals": "ACCEL",
                    "risk_score": 6,
                    "ret5_pct": 24.0,
                    "ret20_pct": 52.0,
                    "volume_ratio20": 2.9,
                    "bias20_pct": 16.0,
                    "action": "等拉回",
                    "realized_ret_pct": -2.0,
                    "status": "ok",
                },
                {
                    "signal_date": "2026-04-16",
                    "horizon_days": 1,
                    "watch_type": "short",
                    "reco_status": "ok",
                    "market_heat": "normal",
                    "scenario_label": "強勢延伸盤",
                    "signals": "TREND",
                    "risk_score": 2,
                    "ret5_pct": 5.0,
                    "ret20_pct": 12.0,
                    "volume_ratio20": 1.1,
                    "bias20_pct": 4.0,
                    "action": "等拉回",
                    "realized_ret_pct": 1.0,
                    "status": "ok",
                },
            ]
        )
        parts = summarize_outcomes(df)
        self.assertIn("overall_by_spec_risk", parts)
        buckets = parts["overall_by_spec_risk"]["spec_risk_bucket"].tolist()
        self.assertIn("high", buckets)
        self.assertIn("normal", buckets)
        self.assertFalse(parts["spec_risk_check"].empty)
        self.assertFalse(parts["overall_by_spec_subtype"].empty)

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
                    "signals": "REBREAK",
                    "spec_risk_score": 1,
                    "spec_risk_label": "正常",
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
                    "signals": "ACCEL,TREND",
                    "spec_risk_score": 7,
                    "spec_risk_label": "疑似炒作風險高",
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
                    "signals": "ACCEL,TREND",
                    "spec_risk_score": 1,
                    "spec_risk_label": "正常",
                    "action": "等拉回",
                    "realized_ret_pct": 1.0,
                    "status": "ok",
                },
                {
                    "signal_date": "2026-04-15",
                    "horizon_days": 1,
                    "watch_type": "short",
                    "reco_status": "ok",
                    "market_heat": "hot",
                    "scenario_label": "強勢延伸盤",
                    "signals": "ACCEL",
                    "spec_risk_score": 8,
                    "spec_risk_label": "疑似炒作風險高",
                    "action": "等拉回",
                    "realized_ret_pct": -1.5,
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
        self.assertIn("## Overall By Signal Template", md)
        self.assertIn("## Overall By Spec Risk", md)
        self.assertIn("## Overall By Spec Subtype", md)
        self.assertIn("## Overall By Scenario", md)
        self.assertIn("## Overall By Scenario + Signal Template", md)
        self.assertIn("## Heat Bias Check (hot - normal)", md)
        self.assertIn("## Heat Bias By Scenario (hot - normal)", md)
        self.assertIn("## Heat Bias By Date (hot - normal, top 20)", md)
        self.assertIn("## Spec Risk Check (high - normal)", md)
        self.assertIn("## Threshold Guard Check (ok - below_threshold)", md)
        self.assertIn("## Weekly Checkpoint", md)
        self.assertIn("## Overall By Action", md)
        self.assertIn("## Short Threshold Diagnostics", md)
        self.assertIn("## Short Gate Promotion Watch", md)
        self.assertIn("## Overall By Scenario + Action", md)
        self.assertIn("reco_status", md)
        self.assertIn("Momentum Leader", md)
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

    def test_build_key_findings_surfaces_threshold_guard_when_below_threshold_is_stronger(self) -> None:
        rows = []
        for idx in range(6):
            rows.append(
                {
                    "signal_date": f"2026-04-{10 + idx:02d}",
                    "horizon_days": 1,
                    "watch_type": "short",
                    "reco_status": "ok",
                    "market_heat": "warm",
                    "scenario_label": "強勢延伸盤",
                    "action": "等拉回",
                    "realized_ret_pct": 1.0,
                    "status": "ok",
                }
            )
            rows.append(
                {
                    "signal_date": f"2026-04-{10 + idx:02d}",
                    "horizon_days": 1,
                    "watch_type": "short",
                    "reco_status": "below_threshold",
                    "market_heat": "hot",
                    "scenario_label": "強勢延伸盤",
                    "action": "開高不追",
                    "realized_ret_pct": 4.0,
                    "status": "ok",
                }
            )

        findings = build_key_findings(summarize_outcomes(pd.DataFrame(rows)))
        joined = "\n".join(findings)
        self.assertIn("below_threshold", joined)
        self.assertIn("短線 `ok` 門檻可能偏保守", joined)
        self.assertIn("升格觀察", joined)

    def test_summarize_outcomes_builds_short_gate_promotion_watch(self) -> None:
        rows = []
        for idx in range(6):
            rows.append(
                {
                    "signal_date": f"2026-04-{10 + idx:02d}",
                    "horizon_days": 1,
                    "watch_type": "short",
                    "reco_status": "ok",
                    "market_heat": "warm",
                    "scenario_label": "強勢延伸盤",
                    "action": "等拉回",
                    "realized_ret_pct": 1.0,
                    "status": "ok",
                }
            )
        for idx in range(4):
            rows.append(
                {
                    "signal_date": f"2026-04-{10 + idx:02d}",
                    "horizon_days": 1,
                    "watch_type": "short",
                    "reco_status": "below_threshold",
                    "market_heat": "hot",
                    "scenario_label": "強勢延伸盤",
                    "action": "開高不追",
                    "realized_ret_pct": 4.0,
                    "status": "ok",
                }
            )

        parts = summarize_outcomes(pd.DataFrame(rows))
        self.assertFalse(parts["short_gate_promotion_watch"].empty)
        top_row = parts["short_gate_promotion_watch"].iloc[0]
        self.assertEqual(str(top_row["action"]), "開高不追")
        self.assertEqual(str(top_row["verdict"]), "watch_upgrade")
        self.assertGreater(float(top_row["delta_avg_ret_below_minus_ok"]), 0.0)

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
