from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from stock_watch.cli.weekly_review import build_decisions
from stock_watch.cli.weekly_review import build_atr_exit_verification
from stock_watch.cli.weekly_review import build_atr_exit_policy_simulation
from stock_watch.cli.weekly_review import build_atr_exit_policy_segment_simulation
from stock_watch.cli.weekly_review import build_candidate_expansion_plan
from stock_watch.cli.weekly_review import build_candidate_fill_directions
from stock_watch.cli.weekly_review import build_candidate_source_plan
from stock_watch.cli.weekly_review import build_short_gate_tuning_draft
from stock_watch.cli.weekly_review import build_watchlist_gap_snapshot
from stock_watch.cli.weekly_review import build_rank_candidate_source_summary
from stock_watch.cli.weekly_review import build_rank_coverage_guidance
from stock_watch.cli.weekly_review import build_rank_spec_risk_coverage
from stock_watch.cli.weekly_review import build_research_diagnostics
from stock_watch.cli.weekly_review import build_data_quality_gate
from stock_watch.cli.weekly_review import build_pullback_confirmation_diagnostics
from stock_watch.cli.weekly_review import build_pullback_exit_guard_recommendations
from stock_watch.cli.weekly_review import build_pullback_quality_diagnostics
from stock_watch.cli.weekly_review import build_pullback_rule_recommendations
from stock_watch.cli.weekly_review import build_short_pullback_trade_simulation_shadow
from stock_watch.cli.weekly_review import build_hold_continuation_diagnostics
from stock_watch.cli.weekly_review import build_spec_risk_overview
from stock_watch.cli.weekly_review import build_weekly_decision_panel
from stock_watch.cli.weekly_review import build_weekly_review_payload
from stock_watch.cli.weekly_review import filter_recent_signal_dates
from stock_watch.cli.weekly_review import render_weekly_review_markdown
from stock_watch.cli.weekly_review import write_outputs


class RunWeeklyReviewTests(unittest.TestCase):
    def test_filter_recent_signal_dates_keeps_latest_sorted_dates(self) -> None:
        outcomes = pd.DataFrame(
            [
                {"signal_date": "2026-04-10", "status": "ok"},
                {"signal_date": "2026-04-12", "status": "ok"},
                {"signal_date": "2026-04-11", "status": "ok"},
            ]
        )

        recent, dates = filter_recent_signal_dates(outcomes, max_signal_dates=2)

        self.assertEqual(dates, ["2026-04-11", "2026-04-12"])
        self.assertEqual(sorted(recent["signal_date"].astype(str).unique().tolist()), dates)

    def test_build_decisions_flags_threshold_review_and_feedback_hold(self) -> None:
        parts = {
            "midlong_threshold_gate": pd.DataFrame(),
            "delta_ok_minus_below": pd.DataFrame(
                [
                    {
                        "horizon_days": 1,
                        "watch_type": "midlong",
                        "min_n": 8,
                        "confidence": "medium",
                        "delta_avg_ret": -1.68,
                    }
                ]
            ),
            "heat_bias_check": pd.DataFrame(
                [
                    {
                        "horizon_days": 1,
                        "watch_type": "midlong",
                        "delta_avg_ret_hot_minus_normal": 4.22,
                    }
                ]
            ),
            "spec_risk_check": pd.DataFrame(
                [
                    {
                        "horizon_days": 1,
                        "watch_type": "short",
                        "min_n": 6,
                        "confidence": "medium",
                        "delta_avg_ret_high_minus_normal": -1.25,
                    }
                ]
            ),
            "overall_by_spec_subtype": pd.DataFrame(
                [
                    {
                        "horizon_days": 1,
                        "watch_type": "short",
                        "spec_risk_subtype": "急拉爆量型",
                        "n": 6,
                        "win_rate": 33.3,
                        "avg_ret": -1.25,
                    }
                ]
            ),
            "short_gate_promotion_watch": pd.DataFrame(
                [
                    {
                        "horizon_days": 1,
                        "watch_type": "short",
                        "action": "開高不追",
                        "below_n": 5,
                        "ok_n": 28,
                        "min_n": 5,
                        "confidence": "medium",
                        "delta_avg_ret_below_minus_ok": 2.3,
                        "verdict": "watch_upgrade",
                    }
                ]
            ),
            "short_gate_simulation": pd.DataFrame(
                [
                    {
                        "horizon_days": 1,
                        "watch_type": "short",
                        "promoted_actions": "開高不追",
                        "promoted_n": 5,
                        "delta_avg_ret_simulated_minus_current": 0.9,
                    }
                ]
            ),
        }
        band_parts = {
            "band_coverage": pd.DataFrame(
                [
                    {"horizon_days": 5, "watch_type": "midlong", "matured_rows": 0},
                    {"horizon_days": 20, "watch_type": "midlong", "matured_rows": 0},
                ]
            )
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            feedback_csv = Path(tmpdir) / "feedback.csv"
            pd.DataFrame(
                [
                    {"config_name": "70/30", "watch_type": "midlong", "action_label": "續抱", "rank_delta": 0, "score_delta": 0},
                    {"config_name": "60/40", "watch_type": "midlong", "action_label": "續抱", "rank_delta": 0, "score_delta": 0.12},
                ]
            ).to_csv(feedback_csv, index=False)

            decisions = build_decisions(parts, band_parts, feedback_csv)

        self.assertEqual(decisions["threshold"]["status"], "review")
        self.assertEqual(decisions["short_gate"]["status"], "review")
        self.assertEqual(decisions["atr"]["status"], "hold")
        self.assertEqual(decisions["feedback"]["status"], "hold")
        self.assertEqual(decisions["spec_risk"]["status"], "review")

    def test_build_decisions_blocks_when_midlong_gate_blocks_loosening(self) -> None:
        parts = {
            "midlong_threshold_gate": pd.DataFrame(
                [
                    {
                        "horizon_days": 1,
                        "below_n": 9,
                        "normal_below_n": 0,
                        "below_hot_share_pct": 88.9,
                        "ok_hot_share_pct": 15.1,
                        "heat_share_gap_pct": 73.8,
                        "decision": "block_loosening",
                    }
                ]
            ),
            "delta_ok_minus_below": pd.DataFrame(
                [
                    {
                        "horizon_days": 1,
                        "watch_type": "midlong",
                        "min_n": 9,
                        "confidence": "medium",
                        "delta_avg_ret": -2.62,
                    }
                ]
            ),
            "heat_bias_check": pd.DataFrame(),
            "spec_risk_check": pd.DataFrame(),
            "short_gate_promotion_watch": pd.DataFrame(),
            "short_gate_simulation": pd.DataFrame(),
        }
        band_parts = {
            "band_coverage": pd.DataFrame(
                [
                    {"horizon_days": 5, "watch_type": "midlong", "matured_rows": 0},
                    {"horizon_days": 20, "watch_type": "midlong", "matured_rows": 0},
                ]
            )
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            feedback_csv = Path(tmpdir) / "feedback.csv"
            pd.DataFrame(
                [
                    {"config_name": "70/30", "watch_type": "midlong", "action_label": "續抱", "rank_delta": 0, "score_delta": 0},
                ]
            ).to_csv(feedback_csv, index=False)

            decisions = build_decisions(parts, band_parts, feedback_csv)

        self.assertEqual(decisions["threshold"]["status"], "block")
        self.assertIn("block_loosening", decisions["threshold"]["detail"])

    def test_build_spec_risk_overview_summarizes_top_and_weakest_subtypes(self) -> None:
        parts = {
            "overall_by_spec_risk": pd.DataFrame(
                [
                    {"horizon_days": 1, "watch_type": "short", "spec_risk_bucket": "watch", "n": 3},
                    {"horizon_days": 1, "watch_type": "midlong", "spec_risk_bucket": "watch", "n": 2},
                ]
            ),
            "overall_by_spec_subtype": pd.DataFrame(
                [
                    {"horizon_days": 1, "watch_type": "short", "spec_risk_subtype": "急拉追價型", "n": 3, "avg_ret": 1.2, "win_rate": 66.7},
                    {"horizon_days": 1, "watch_type": "midlong", "spec_risk_subtype": "結構失配型", "n": 2, "avg_ret": -0.8, "win_rate": 50.0},
                ]
            ),
        }

        overview = build_spec_risk_overview(parts)

        self.assertEqual(overview["non_normal_rows"], 5)
        self.assertEqual(overview["top_subtype"]["spec_risk_subtype"], "急拉追價型")
        self.assertEqual(overview["weakest_subtype"]["spec_risk_subtype"], "結構失配型")

    def test_build_short_gate_tuning_draft_prefers_action_level_shadow_plan(self) -> None:
        full_parts = {
            "short_gate_promotion_watch": pd.DataFrame(
                [
                    {
                        "horizon_days": 1,
                        "watch_type": "short",
                        "action": "開高不追",
                        "below_n": 5,
                        "ok_n": 28,
                        "confidence": "medium",
                        "delta_avg_ret_below_minus_ok": 2.3,
                        "promotion_ready": True,
                        "verdict": "watch_upgrade",
                    }
                ]
            ),
            "short_gate_action_context": pd.DataFrame(
                [
                    {
                        "horizon_days": 1,
                        "reco_status": "below_threshold",
                        "action": "開高不追",
                        "scenario_label": "強勢延伸盤",
                        "market_heat": "hot",
                        "spec_risk_bucket": "normal",
                        "n": 3,
                        "signal_dates": 3,
                        "win_rate": 66.7,
                        "avg_ret": 5.45,
                        "med_ret": 7.33,
                    }
                ]
            ),
            "short_gate_simulation": pd.DataFrame(
                [
                    {
                        "horizon_days": 1,
                        "watch_type": "short",
                        "promoted_actions": "開高不追",
                        "promoted_n": 5,
                        "delta_avg_ret_simulated_minus_current": 0.35,
                        "delta_win_rate_simulated_minus_current": -1.7,
                    }
                ]
            ),
        }
        recent_parts = {
            "short_gate_promotion_watch": pd.DataFrame(
                [
                    {
                        "horizon_days": 1,
                        "watch_type": "short",
                        "action": "開高不追",
                        "below_n": 2,
                        "ok_n": 6,
                        "confidence": "low",
                        "delta_avg_ret_below_minus_ok": 1.66,
                        "promotion_ready": False,
                        "verdict": "mixed",
                    }
                ]
            )
        }

        draft = build_short_gate_tuning_draft(full_parts, recent_parts)

        self.assertEqual(draft["status"], "draft_ready")
        self.assertIn("shadow promotion", draft["proposal"])
        self.assertEqual(draft["historical"]["below_n"], 5)
        self.assertFalse(draft["recent"]["promotion_ready"])
        self.assertEqual(draft["simulation"]["promoted_n"], 5)
        self.assertTrue(draft["contexts"])

    def test_build_research_diagnostics_picks_factor_sensitivity_and_tail(self) -> None:
        parts = {
            "factor_high_low_spread": pd.DataFrame(
                [
                    {
                        "horizon_days": 5,
                        "watch_type": "midlong",
                        "factor_name": "ret5_pct",
                        "min_n": 6,
                        "confidence": "medium",
                        "delta_avg_ret_high_minus_low": 4.2,
                    }
                ]
            ),
            "sensitivity_matrix": pd.DataFrame(
                [
                    {
                        "horizon_days": 5,
                        "watch_type": "midlong",
                        "config_name": "ret5_ge_median_9.4",
                        "n": 8,
                        "avg_ret": 12.7,
                        "delta_avg_ret_vs_baseline": 3.1,
                    }
                ]
            ),
            "tail_risk_by_action": pd.DataFrame(
                [
                    {
                        "horizon_days": 1,
                        "watch_type": "short",
                        "reco_status": "below_threshold",
                        "action": "只觀察不追",
                        "n": 3,
                        "tail25_ret": -7.3,
                        "worst_ret": -9.9,
                        "risk_label": "watch_drawdown",
                    }
                ]
            ),
        }

        diagnostics = build_research_diagnostics(parts, parts)

        self.assertEqual(diagnostics["full_factor"]["factor_name"], "ret5_pct")
        self.assertEqual(diagnostics["full_sensitivity"]["config_name"], "ret5_ge_median_9.4")
        self.assertEqual(diagnostics["full_tail"]["action"], "只觀察不追")
        self.assertTrue(diagnostics["notes"])

    def test_build_pullback_quality_diagnostics_splits_short_pullbacks(self) -> None:
        outcomes = pd.DataFrame(
            [
                {
                    "horizon_days": 5,
                    "watch_type": "short",
                    "action": "等拉回",
                    "status": "ok",
                    "risk_score": 2,
                    "spec_risk_score": 0,
                    "spec_risk_label": "正常",
                    "ret5_pct": 6.0,
                    "ret20_pct": 12.0,
                    "volume_ratio20": 1.1,
                    "signals": "TREND,ACCEL",
                    "market_heat": "warm",
                    "realized_ret_pct": 2.0,
                },
                {
                    "horizon_days": 5,
                    "watch_type": "short",
                    "action": "等拉回",
                    "status": "ok",
                    "risk_score": 2,
                    "spec_risk_score": 0,
                    "spec_risk_label": "正常",
                    "ret5_pct": 3.0,
                    "ret20_pct": -1.0,
                    "volume_ratio20": 0.7,
                    "signals": "PULLBACK",
                    "market_heat": "normal",
                    "realized_ret_pct": -4.0,
                },
                {
                    "horizon_days": 5,
                    "watch_type": "short",
                    "action": "等拉回",
                    "status": "ok",
                    "risk_score": 5,
                    "spec_risk_score": 7,
                    "spec_risk_label": "疑似炒作風險高",
                    "ret5_pct": 18.0,
                    "ret20_pct": 35.0,
                    "volume_ratio20": 1.2,
                    "signals": "TREND",
                    "market_heat": "hot",
                    "realized_ret_pct": -8.0,
                },
            ]
        )

        table = build_pullback_quality_diagnostics(outcomes)

        qualities = set(table["pullback_quality"].astype(str))
        self.assertEqual(qualities, {"健康拉回", "弱承接/疑似破位", "高風險拉回"})
        self.assertEqual(set(table["action_guide"].astype(str)), {"可等買點", "暫不買", "等轉強小試"})
        self.assertEqual(set(table["position_size"].astype(str)), {"0.5 倉", "0 倉", "0 倉（轉強後 0.25 倉）"})
        self.assertTrue(table["guidance"].astype(str).str.contains("小倉|支撐|量價").all())
        high_risk = table[table["pullback_quality"] == "高風險拉回"].iloc[0]
        self.assertEqual(high_risk["worst_ret"], -8.0)

    def test_build_pullback_confirmation_diagnostics_pairs_1d_and_5d(self) -> None:
        base = {
            "watch_type": "short",
            "action": "等拉回",
            "status": "ok",
            "risk_score": 2,
            "spec_risk_score": 0,
            "spec_risk_label": "正常",
            "ret20_pct": 12.0,
            "volume_ratio20": 1.2,
            "signals": "TREND,ACCEL",
            "market_heat": "warm",
        }
        rows = [
            {**base, "signal_date": "2026-04-01", "ticker": "AAA.TW", "name": "Alpha", "horizon_days": 1, "ret5_pct": 6.0, "realized_ret_pct": 1.5},
            {**base, "signal_date": "2026-04-01", "ticker": "AAA.TW", "name": "Alpha", "horizon_days": 5, "ret5_pct": 6.0, "realized_ret_pct": 4.0},
            {**base, "signal_date": "2026-04-02", "ticker": "BBB.TW", "name": "Beta", "horizon_days": 1, "ret5_pct": 3.0, "ret20_pct": -1.0, "volume_ratio20": 0.7, "signals": "PULLBACK", "realized_ret_pct": -0.5},
            {**base, "signal_date": "2026-04-02", "ticker": "BBB.TW", "name": "Beta", "horizon_days": 5, "ret5_pct": 3.0, "ret20_pct": -1.0, "volume_ratio20": 0.7, "signals": "PULLBACK", "realized_ret_pct": -3.0},
            {**base, "signal_date": "2026-04-03", "ticker": "CCC.TW", "name": "Gamma", "horizon_days": 1, "ret5_pct": 11.0, "ret20_pct": 26.0, "market_heat": "hot", "realized_ret_pct": -2.5},
            {**base, "signal_date": "2026-04-03", "ticker": "CCC.TW", "name": "Gamma", "horizon_days": 5, "ret5_pct": 11.0, "ret20_pct": 26.0, "market_heat": "hot", "realized_ret_pct": -8.0},
            {**base, "signal_date": "2026-04-04", "ticker": "DDD.TW", "name": "Delta", "horizon_days": 1, "ret5_pct": 16.0, "ret20_pct": 35.0, "market_heat": "hot", "realized_ret_pct": 1.2},
            {**base, "signal_date": "2026-04-04", "ticker": "DDD.TW", "name": "Delta", "horizon_days": 5, "ret5_pct": 16.0, "ret20_pct": 35.0, "market_heat": "hot", "realized_ret_pct": 5.0},
            {**base, "signal_date": "2026-04-05", "ticker": "EEE.TW", "name": "Echo", "horizon_days": 1, "ret5_pct": 16.0, "ret20_pct": 35.0, "market_heat": "hot", "realized_ret_pct": -0.5},
            {**base, "signal_date": "2026-04-05", "ticker": "EEE.TW", "name": "Echo", "horizon_days": 5, "ret5_pct": 16.0, "ret20_pct": 35.0, "market_heat": "hot", "realized_ret_pct": -4.0},
        ]

        table = build_pullback_confirmation_diagnostics(pd.DataFrame(rows))

        confirmations = set(table["confirmation"].astype(str))
        self.assertEqual(confirmations, {"隔日轉強", "隔日小跌", "隔日失守"})
        high_risk_confirmed = table[
            (table["pullback_quality"] == "高風險拉回") & (table["confirmation"] == "隔日轉強")
        ].iloc[0]
        high_risk_unconfirmed = table[
            (table["pullback_quality"] == "高風險拉回") & (table["confirmation"] == "隔日小跌")
        ].iloc[0]
        self.assertEqual(high_risk_confirmed["action_guide"], "可小試")
        self.assertEqual(high_risk_confirmed["position_size"], "0.25 倉")
        self.assertEqual(high_risk_unconfirmed["action_guide"], "只觀察")
        self.assertEqual(high_risk_unconfirmed["position_size"], "0 倉")
        failed = table[(table["pullback_quality"] == "需確認拉回") & (table["confirmation"] == "隔日失守")].iloc[0]
        self.assertEqual(failed["worst_5d"], -8.0)

    def test_build_short_pullback_trade_simulation_shadow_uses_confirm_close_entry(self) -> None:
        base = {
            "watch_type": "short",
            "action": "等拉回",
            "status": "ok",
            "risk_score": 2,
            "spec_risk_score": 0,
            "spec_risk_label": "正常",
            "ret20_pct": 12.0,
            "volume_ratio20": 1.2,
            "signals": "TREND,ACCEL",
            "market_heat": "warm",
        }
        rows = [
            {**base, "signal_date": "2026-04-01", "ticker": "AAA.TW", "name": "Alpha", "horizon_days": 1, "ret5_pct": 16.0, "ret20_pct": 35.0, "market_heat": "hot", "realized_ret_pct": 1.2},
            {**base, "signal_date": "2026-04-01", "ticker": "AAA.TW", "name": "Alpha", "horizon_days": 5, "ret5_pct": 16.0, "ret20_pct": 35.0, "market_heat": "hot", "realized_ret_pct": 5.0},
            {**base, "signal_date": "2026-04-02", "ticker": "BBB.TW", "name": "Beta", "horizon_days": 1, "risk_score": 4, "ret5_pct": 3.0, "realized_ret_pct": 1.4},
            {**base, "signal_date": "2026-04-02", "ticker": "BBB.TW", "name": "Beta", "horizon_days": 5, "risk_score": 4, "ret5_pct": 3.0, "realized_ret_pct": -10.0},
            {**base, "signal_date": "2026-04-03", "ticker": "CCC.TW", "name": "Gamma", "horizon_days": 1, "ret5_pct": 16.0, "ret20_pct": 35.0, "market_heat": "hot", "realized_ret_pct": -0.5},
            {**base, "signal_date": "2026-04-03", "ticker": "CCC.TW", "name": "Gamma", "horizon_days": 5, "ret5_pct": 16.0, "ret20_pct": 35.0, "market_heat": "hot", "realized_ret_pct": -4.0},
        ]

        table = build_short_pullback_trade_simulation_shadow(pd.DataFrame(rows))

        high_risk_confirmed = table[(table["rule"] == "高風險拉回") & (table["confirmation"] == "隔日轉強")].iloc[0]
        confirm_pullback = table[(table["rule"] == "需確認拉回") & (table["confirmation"] == "隔日轉強")].iloc[0]
        high_risk_unconfirmed = table[(table["rule"] == "高風險拉回") & (table["confirmation"] == "隔日小跌")].iloc[0]
        self.assertEqual(high_risk_confirmed["entry_assumption"], "隔日確認收盤進")
        self.assertEqual(high_risk_confirmed["mode"], "shadow")
        self.assertEqual(high_risk_confirmed["position_size"], "0.25 倉")
        self.assertEqual(high_risk_confirmed["status"], "shadow_low_sample")
        self.assertEqual(high_risk_confirmed["avg_trade_ret_5d"], 3.75)
        self.assertEqual(high_risk_confirmed["avg_position_ret_5d"], 0.94)
        self.assertEqual(confirm_pullback["status"], "blocked_no_entry")
        self.assertEqual(high_risk_unconfirmed["position_size"], "0 倉")

    def test_build_hold_continuation_diagnostics_pairs_1d_and_5d(self) -> None:
        rows = []
        for idx in range(6):
            ticker = f"HOLD{idx}.TW"
            rows.append(
                {
                    "signal_date": f"2026-04-{idx + 1:02d}",
                    "ticker": ticker,
                    "name": f"Hold {idx}",
                    "watch_type": "midlong",
                    "action": "續抱",
                    "scenario_label": "強勢延伸盤",
                    "market_heat": "warm",
                    "reco_status": "ok",
                    "horizon_days": 1,
                    "realized_ret_pct": 1.0,
                    "status": "ok",
                }
            )
            rows.append(
                {
                    "signal_date": f"2026-04-{idx + 1:02d}",
                    "ticker": ticker,
                    "name": f"Hold {idx}",
                    "watch_type": "midlong",
                    "action": "續抱",
                    "scenario_label": "強勢延伸盤",
                    "market_heat": "warm",
                    "reco_status": "ok",
                    "horizon_days": 5,
                    "realized_ret_pct": 6.0,
                    "status": "ok",
                }
            )
            fade_ticker = f"FADE{idx}.TW"
            rows.append(
                {
                    "signal_date": f"2026-04-{idx + 1:02d}",
                    "ticker": fade_ticker,
                    "watch_type": "short",
                    "action": "等拉回",
                    "scenario_label": "高檔震盪盤",
                    "market_heat": "hot",
                    "reco_status": "ok",
                    "horizon_days": 1,
                    "realized_ret_pct": 3.0,
                    "status": "ok",
                }
            )
            rows.append(
                {
                    "signal_date": f"2026-04-{idx + 1:02d}",
                    "ticker": fade_ticker,
                    "watch_type": "short",
                    "action": "等拉回",
                    "scenario_label": "高檔震盪盤",
                    "market_heat": "hot",
                    "reco_status": "ok",
                    "horizon_days": 5,
                    "realized_ret_pct": 1.0,
                    "status": "ok",
                }
            )

        table = build_hold_continuation_diagnostics(pd.DataFrame(rows))

        hold_action = table[
            (table["segment_type"] == "action")
            & (table["segment_value"] == "續抱")
            & (table["watch_type"] == "midlong")
        ].iloc[0]
        fade_action = table[
            (table["segment_type"] == "action")
            & (table["segment_value"] == "等拉回")
            & (table["watch_type"] == "short")
        ].iloc[0]
        self.assertEqual(hold_action["status"], "hold_candidate")
        self.assertEqual(hold_action["avg_continuation_1d_to_5d"], 4.95)
        self.assertEqual(hold_action["hold_edge_5d_vs_1d"], 5.0)
        self.assertEqual(fade_action["status"], "fade_after_1d")

    def test_build_weekly_decision_panel_buckets_shadow_and_tail_risk(self) -> None:
        decisions = {
            "trade_simulation": {
                "status": "shadow_only",
                "detail": "不進 Telegram",
            },
            "atr": {
                "status": "review",
                "detail": "需要人工確認",
            },
        }
        trade_simulation = pd.DataFrame(
            [
                {
                    "rule": "高風險拉回",
                    "confirmation": "隔日轉強",
                    "status": "shadow_low_sample",
                    "position_fraction": 0.25,
                    "n": 2,
                    "avg_trade_ret_5d": 10.6,
                    "tail25_trade_ret_5d": 6.86,
                    "worst_trade_ret_5d": 3.11,
                },
                {
                    "rule": "需確認拉回",
                    "confirmation": "隔日轉強",
                    "status": "blocked_no_entry",
                    "position_fraction": 0.0,
                    "n": 3,
                    "avg_trade_ret_5d": -1.33,
                    "tail25_trade_ret_5d": -9.19,
                    "worst_trade_ret_5d": -14.11,
                },
            ]
        )
        pullback_rules = pd.DataFrame(
            [
                {
                    "rule": "需確認拉回",
                    "condition": "即使隔日轉強",
                    "status": "block_upgrade",
                    "evidence": "n=3",
                    "note": "不能升級",
                }
            ]
        )
        atr_exit = pd.DataFrame(
            [
                {
                    "horizon_days": 5,
                    "watch_type": "short",
                    "n": 20,
                    "touch_stop_rate_pct": 5.0,
                    "close_stop_rate_pct": 0.0,
                    "trim_first_rate_pct": 55.0,
                    "worst_mae_pct": -16.62,
                    "status": "review_intraday_tail",
                    "next_action": "驗證 touched-stop 提醒是否能降低 worst MAE。",
                }
            ]
        )
        atr_policy = pd.DataFrame(
            [
                {
                    "horizon_days": 5,
                    "watch_type": "short",
                    "policy": "touched_stop_exit",
                    "status": "research_candidate",
                    "n": 20,
                    "avg_ret": 5.0,
                    "worst_ret": -5.0,
                    "delta_avg_vs_baseline": 0.1,
                    "delta_worst_vs_baseline": 7.0,
                    "read": "值得進一步拆樣本。",
                }
            ]
        )

        panel = build_weekly_decision_panel(decisions, trade_simulation, pullback_rules, atr_exit, atr_policy)

        buckets = set(panel["bucket"].astype(str))
        self.assertIn("Need Human Decision", buckets)
        self.assertIn("Ready to Review", buckets)
        self.assertIn("Need More Samples", buckets)
        self.assertIn("Blocked by Tail Risk", buckets)
        self.assertIn("Keep Shadow", buckets)
        tail_row = panel[panel["bucket"] == "Blocked by Tail Risk"].iloc[0]
        self.assertIn("需確認拉回", tail_row["rule"])
        atr_row = panel[panel["source"] == "atr_exit_verification"].iloc[0]
        self.assertEqual(atr_row["bucket"], "Ready to Review")

    def test_build_atr_exit_verification_compares_touch_and_close_stop(self) -> None:
        checkpoints = pd.DataFrame(
            [
                {
                    "horizon_days": 5,
                    "watch_type": "short",
                    "n": 20,
                    "path_n": 20,
                    "sequence_n": 20,
                    "closed_below_stop_rate_pct": 0.0,
                    "touched_below_stop_rate_pct": 5.0,
                    "stop_touch_recovered_rate_pct": 100.0,
                    "trim_before_stop_rate_pct": 55.0,
                    "stop_before_trim_rate_pct": 5.0,
                    "same_day_stop_trim_rate_pct": 0.0,
                    "trim_touch_failed_rate_pct": 18.2,
                    "avg_ret_pct": 5.12,
                    "avg_mfe_pct": 11.37,
                    "avg_mae_pct": -4.74,
                    "worst_mae_pct": -16.62,
                },
                {
                    "horizon_days": 5,
                    "watch_type": "midlong",
                    "n": 4,
                    "path_n": 4,
                    "sequence_n": 4,
                    "closed_below_stop_rate_pct": 0.0,
                    "touched_below_stop_rate_pct": 0.0,
                    "trim_before_stop_rate_pct": 0.0,
                    "stop_before_trim_rate_pct": 0.0,
                    "worst_mae_pct": -2.0,
                },
            ]
        )

        table = build_atr_exit_verification(checkpoints)

        short_row = table[table["watch_type"] == "short"].iloc[0]
        thin_row = table[table["watch_type"] == "midlong"].iloc[0]
        self.assertEqual(short_row["intraday_stop_only_rate_pct"], 5.0)
        self.assertEqual(short_row["status"], "review_close_stop_bias")
        self.assertIn("盤中碰 stop", short_row["exit_read"])
        self.assertEqual(thin_row["status"], "need_more_samples")

    def test_build_atr_exit_policy_simulation_compares_exit_policies(self) -> None:
        rows = []
        for idx in range(10):
            ret = 4.0
            stop_day = 0
            trim_day = 0
            trim_before = 0
            stop_before = 0
            if idx == 0:
                ret = -12.0
                stop_day = 1
                stop_before = 1
            elif idx in {1, 2, 3}:
                ret = 8.0
                trim_day = 2
                trim_before = 1
            rows.append(
                {
                    "watch_type": "short",
                    "alert_close": 100.0,
                    "trim_price": 110.0,
                    "stop_price": 95.0,
                    "ret5_future_pct": ret,
                    "trim5_touch_day": trim_day,
                    "stop5_touch_day": stop_day,
                    "trim5_before_stop": trim_before,
                    "stop5_before_trim": stop_before,
                }
            )

        table = build_atr_exit_policy_simulation(pd.DataFrame(rows))

        baseline = table[table["policy"] == "baseline_close"].iloc[0]
        touched_stop = table[table["policy"] == "touched_stop_exit"].iloc[0]
        trim_half = table[table["policy"] == "trim_touch_half"].iloc[0]
        self.assertEqual(int(baseline["n"]), 10)
        self.assertEqual(float(baseline["worst_ret"]), -12.0)
        self.assertEqual(float(touched_stop["worst_ret"]), -5.0)
        self.assertGreater(float(touched_stop["delta_worst_vs_baseline"]), 0)
        self.assertEqual(int(trim_half["trim_exit_count"]), 3)
        self.assertIn(touched_stop["status"], {"research_candidate", "tail_hedge_costly"})

    def test_build_atr_exit_policy_segment_simulation_splits_action_labels(self) -> None:
        rows = []
        for action_label in ["等拉回", "續抱"]:
            for idx in range(10):
                ret = 4.0
                stop_day = 0
                trim_day = 0
                trim_before = 0
                stop_before = 0
                if action_label == "等拉回" and idx == 0:
                    ret = -12.0
                    stop_day = 1
                    stop_before = 1
                if action_label == "續抱" and idx in {0, 1, 2}:
                    ret = 8.0
                    trim_day = 2
                    trim_before = 1
                rows.append(
                    {
                        "watch_type": "short" if action_label == "等拉回" else "midlong",
                        "action_label": action_label,
                        "scenario_label": "高檔震盪盤",
                        "alert_close": 100.0,
                        "trim_price": 110.0,
                        "stop_price": 95.0,
                        "ret5_future_pct": ret,
                        "trim5_touch_day": trim_day,
                        "stop5_touch_day": stop_day,
                        "trim5_before_stop": trim_before,
                        "stop5_before_trim": stop_before,
                    }
                )

        table = build_atr_exit_policy_segment_simulation(pd.DataFrame(rows), min_segment_n=10)

        self.assertIn("action_label", set(table["segment_type"].astype(str)))
        pullback_stop = table[
            (table["segment_type"] == "action_label")
            & (table["segment_value"] == "等拉回")
            & (table["policy"] == "touched_stop_exit")
        ].iloc[0]
        self.assertEqual(int(pullback_stop["n"]), 10)
        self.assertGreater(float(pullback_stop["delta_worst_vs_baseline"]), 0)

    def test_build_pullback_rule_recommendations_blocks_tail_risk_upgrades(self) -> None:
        confirmation = pd.DataFrame(
            [
                {
                    "pullback_quality": "高風險拉回",
                    "confirmation": "隔日轉強",
                    "n": 2,
                    "win_rate_5d": 100.0,
                    "avg_5d": 14.12,
                    "worst_5d": 4.23,
                },
                {
                    "pullback_quality": "需確認拉回",
                    "confirmation": "隔日轉強",
                    "n": 3,
                    "win_rate_5d": 66.7,
                    "avg_5d": 2.69,
                    "worst_5d": -12.41,
                },
            ]
        )

        rules = build_pullback_rule_recommendations(confirmation)

        high_risk = rules[(rules["rule"] == "高風險拉回") & (rules["condition"] == "隔日轉強")].iloc[0]
        confirm_risk = rules[rules["rule"] == "需確認拉回"].iloc[0]
        self.assertEqual(high_risk["status"], "active_low_sample")
        self.assertEqual(high_risk["action_guide"], "可小試")
        self.assertEqual(confirm_risk["status"], "block_upgrade")
        self.assertEqual(confirm_risk["position_size"], "0 倉")

    def test_build_pullback_exit_guard_recommendations_adds_close_based_guards(self) -> None:
        confirmation = pd.DataFrame(
            [
                {
                    "pullback_quality": "高風險拉回",
                    "confirmation": "隔日轉強",
                    "n": 2,
                    "win_rate_5d": 100.0,
                    "avg_5d": 14.12,
                    "worst_5d": 4.23,
                },
                {
                    "pullback_quality": "健康拉回",
                    "confirmation": "隔日小跌",
                    "n": 4,
                    "win_rate_5d": 50.0,
                    "avg_5d": 3.19,
                    "worst_5d": -4.59,
                },
                {
                    "pullback_quality": "需確認拉回",
                    "confirmation": "隔日轉強",
                    "n": 3,
                    "win_rate_5d": 66.7,
                    "avg_5d": 2.69,
                    "worst_5d": -12.41,
                },
            ]
        )

        guards = build_pullback_exit_guard_recommendations(confirmation)

        high_risk = guards[guards["setup"] == "高風險拉回 / 可小試"].iloc[0]
        blocked = guards[guards["setup"] == "需確認拉回 / 只觀察"].iloc[0]
        self.assertEqual(high_risk["initial_size"], "0.25 倉")
        self.assertIn("單日 -2%", high_risk["close_exit_guard"])
        self.assertEqual(blocked["status"], "blocked_tail_risk")

    def test_build_data_quality_gate_flags_clean_and_pending_rows(self) -> None:
        snapshots = pd.DataFrame(
            [
                {"signal_date": "2026-04-20", "watch_type": "short", "ticker": "2330.TW"},
                {"signal_date": "2026-04-21", "watch_type": "midlong", "ticker": "2317.TW"},
            ]
        )
        outcomes = pd.DataFrame(
            [
                {"signal_date": "2026-04-20", "horizon_days": 1, "watch_type": "short", "ticker": "2330.TW", "status": "ok"},
                {"signal_date": "2026-04-21", "horizon_days": 5, "watch_type": "midlong", "ticker": "2317.TW", "status": "insufficient_forward_data"},
            ]
        )

        gate = build_data_quality_gate(outcomes, snapshots)

        self.assertEqual(gate["status"], "ok")
        self.assertEqual(gate["metrics"]["snapshot_dup_keys"], 0)
        self.assertEqual(gate["metrics"]["outcome_dup_keys"], 0)
        self.assertEqual(gate["metrics"]["pending_rows"], 1)
        self.assertTrue(gate["coverage_by_horizon"])

    def test_build_rank_spec_risk_coverage_groups_current_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            rank_csv = Path(tmpdir) / "daily_rank.csv"
            pd.DataFrame(
                [
                    {"ticker": "3057.TW", "name": "喬鼎", "group": "theme", "layer": "short_attack", "spec_risk_score": 8, "spec_risk_label": "疑似炒作風險高", "spec_risk_subtype": "急拉爆量型", "ret5_pct": 24.0, "ret20_pct": 52.0, "rank": 1},
                    {"ticker": "6669.TW", "name": "緯穎", "group": "theme", "layer": "short_attack", "spec_risk_score": 4, "spec_risk_label": "投機偏高", "spec_risk_subtype": "急拉追價型", "ret5_pct": 19.0, "ret20_pct": 20.0, "rank": 2},
                    {"ticker": "2330.TW", "name": "台積電", "group": "core", "layer": "midlong_core", "spec_risk_score": 0, "spec_risk_label": "正常", "spec_risk_subtype": "正常", "ret5_pct": 4.0, "ret20_pct": 12.0, "rank": 3},
                ]
            ).to_csv(rank_csv, index=False)

            coverage = build_rank_spec_risk_coverage(rank_csv)

        self.assertEqual(coverage["by_group"][0]["group"], "theme")
        self.assertEqual(coverage["by_group"][0]["high_rows"], 1)
        self.assertEqual(coverage["by_group"][0]["watch_rows"], 1)
        self.assertEqual(coverage["top_candidates"][0]["ticker"], "3057.TW")

    def test_build_rank_candidate_source_summary_derives_archetypes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            rank_csv = Path(tmpdir) / "daily_rank.csv"
            pd.DataFrame(
                [
                    {"ticker": "3057.TW", "name": "喬鼎", "group": "theme", "layer": "short_attack", "signals": "SURGE,ACCEL", "volume_ratio20": 3.1, "ret5_pct": 24.0, "ret20_pct": 52.0, "spec_risk_score": 8, "spec_risk_label": "疑似炒作風險高"},
                    {"ticker": "3661.TW", "name": "世芯-KY", "group": "satellite", "layer": "midlong_core", "signals": "TREND", "volatility_tag": "活潑", "volume_ratio20": 1.4, "ret5_pct": 21.0, "ret20_pct": 35.0, "spec_risk_score": 8, "spec_risk_label": "疑似炒作風險高"},
                    {"ticker": "2330.TW", "name": "台積電", "group": "core", "layer": "midlong_core", "signals": "TREND", "volume_ratio20": 1.1, "ret5_pct": 4.0, "ret20_pct": 12.0, "spec_risk_score": 0, "spec_risk_label": "正常"},
                ]
            ).to_csv(rank_csv, index=False)

            summary = build_rank_candidate_source_summary(rank_csv)

        self.assertEqual(summary["by_source"][0]["candidate_source"], "Satellite high-beta leaders")
        self.assertTrue(any(row["candidate_source"] == "Theme momentum burst" for row in summary["by_source"]))

    def test_build_candidate_source_plan_prioritizes_hot_archetypes(self) -> None:
        plan = build_candidate_source_plan(
            {
                "by_source": [
                    {"candidate_source": "Theme momentum burst", "total_rows": 4, "high_rows": 3, "watch_rows": 1, "non_normal_rows": 4, "non_normal_rate_pct": 100.0},
                    {"candidate_source": "Satellite high-beta leaders", "total_rows": 5, "high_rows": 4, "watch_rows": 0, "non_normal_rows": 4, "non_normal_rate_pct": 80.0},
                    {"candidate_source": "Core trend compounders", "total_rows": 6, "high_rows": 0, "watch_rows": 1, "non_normal_rows": 1, "non_normal_rate_pct": 16.7},
                ]
            }
        )

        self.assertEqual(plan["sources"][0]["candidate_source"], "Theme momentum burst")
        self.assertEqual(plan["sources"][0]["suggested_additions"], 3)
        self.assertTrue(any("Current best source-side expansion targets" in note for note in plan["notes"]))

    def test_build_candidate_fill_directions_adds_search_hints_and_examples(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            rank_csv = Path(tmpdir) / "daily_rank.csv"
            pd.DataFrame(
                [
                    {"ticker": "2388.TW", "name": "威盛", "group": "theme", "layer": "short_attack", "signals": "SURGE,TREND,ACCEL", "volume_ratio20": 2.6, "ret5_pct": 26.5, "ret20_pct": 59.1, "spec_risk_score": 9, "rank": 1},
                    {"ticker": "3661.TW", "name": "世芯-KY", "group": "satellite", "layer": "midlong_core", "signals": "TREND", "volatility_tag": "活潑", "volume_ratio20": 1.4, "ret5_pct": 21.0, "ret20_pct": 35.0, "spec_risk_score": 8, "rank": 2},
                    {"ticker": "6669.TW", "name": "緯穎", "group": "theme", "layer": "short_attack", "signals": "ACCEL", "volume_ratio20": 1.6, "ret5_pct": 19.3, "ret20_pct": 20.7, "spec_risk_score": 6, "rank": 3},
                ]
            ).to_csv(rank_csv, index=False)

            plan = {
                "sources": [
                    {"candidate_source": "Satellite high-beta leaders", "suggested_additions": 3},
                    {"candidate_source": "Theme trend acceleration", "suggested_additions": 3},
                    {"candidate_source": "Theme momentum burst", "suggested_additions": 2},
                ]
            }
            directions = build_candidate_fill_directions(rank_csv, plan)

        self.assertEqual(directions["directions"][0]["preferred_group"], "satellite")
        self.assertIn("高 beta", directions["directions"][0]["search_hint"])
        self.assertIn("3661.TW 世芯-KY", directions["directions"][0]["current_examples"])
        self.assertIn("2388.TW 威盛", directions["directions"][2]["current_examples"])

    def test_build_watchlist_gap_snapshot_compares_current_counts_with_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            watchlist_csv = Path(tmpdir) / "watchlist.csv"
            pd.DataFrame(
                [
                    {"ticker": "2388.TW", "name": "威盛", "group": "theme", "layer": "short_attack", "enabled": True},
                    {"ticker": "6669.TW", "name": "緯穎", "group": "theme", "layer": "short_attack", "enabled": True},
                    {"ticker": "3661.TW", "name": "世芯-KY", "group": "satellite", "layer": "midlong_core", "enabled": True},
                    {"ticker": "2330.TW", "name": "台積電", "group": "core", "layer": "midlong_core", "enabled": True},
                ]
            ).to_csv(watchlist_csv, index=False)

            gap = build_watchlist_gap_snapshot(
                watchlist_csv,
                {"groups": [{"group": "theme", "suggested_additions": 3}, {"group": "satellite", "suggested_additions": 2}]},
                {"sources": [{"candidate_source": "Theme momentum burst", "suggested_additions": 2}]},
            )

        self.assertEqual(gap["by_group"][0]["group"], "theme")
        self.assertEqual(gap["by_group"][0]["next_target_count"], 5)
        self.assertEqual(gap["by_source"][0]["preferred_group"], "theme")

    def test_build_rank_coverage_guidance_prioritizes_hot_groups_and_skips_cold_buckets(self) -> None:
        guidance = build_rank_coverage_guidance(
            {
                "by_group": [
                    {"group": "satellite", "total_rows": 7, "high_rows": 4, "watch_rows": 1, "non_normal_rows": 5, "non_normal_rate_pct": 71.4},
                    {"group": "theme", "total_rows": 18, "high_rows": 4, "watch_rows": 2, "non_normal_rows": 6, "non_normal_rate_pct": 33.3},
                    {"group": "etf", "total_rows": 7, "high_rows": 0, "watch_rows": 0, "non_normal_rows": 0, "non_normal_rate_pct": 0.0},
                ],
                "by_layer": [
                    {"layer": "midlong_core", "total_rows": 23, "high_rows": 4, "watch_rows": 4, "non_normal_rows": 8, "non_normal_rate_pct": 34.8},
                    {"layer": "short_attack", "total_rows": 18, "high_rows": 4, "watch_rows": 2, "non_normal_rows": 6, "non_normal_rate_pct": 33.3},
                    {"layer": "defensive_watch", "total_rows": 4, "high_rows": 0, "watch_rows": 0, "non_normal_rows": 0, "non_normal_rate_pct": 0.0},
                ],
            }
        )

        self.assertEqual(guidance["focus_groups"][0]["group"], "theme")
        self.assertEqual(guidance["focus_layers"][0]["layer"], "midlong_core")
        self.assertEqual(guidance["deprioritize_groups"][0]["group"], "etf")
        self.assertEqual(guidance["deprioritize_layers"][0]["layer"], "defensive_watch")
        self.assertTrue(any("prioritize groups like" in note for note in guidance["notes"]))
        self.assertTrue(any("Do not broaden low-yield areas" in note for note in guidance["notes"]))

    def test_build_candidate_expansion_plan_recommends_hot_groups_and_layers(self) -> None:
        plan = build_candidate_expansion_plan(
            {
                "by_group": [
                    {"group": "theme", "total_rows": 18, "high_rows": 4, "watch_rows": 2, "non_normal_rows": 6, "non_normal_rate_pct": 33.3},
                    {"group": "satellite", "total_rows": 7, "high_rows": 4, "watch_rows": 1, "non_normal_rows": 5, "non_normal_rate_pct": 71.4},
                    {"group": "core", "total_rows": 13, "high_rows": 0, "watch_rows": 3, "non_normal_rows": 3, "non_normal_rate_pct": 23.1},
                    {"group": "etf", "total_rows": 7, "high_rows": 0, "watch_rows": 0, "non_normal_rows": 0, "non_normal_rate_pct": 0.0},
                ],
                "by_layer": [
                    {"layer": "midlong_core", "total_rows": 23, "high_rows": 4, "watch_rows": 4, "non_normal_rows": 8, "non_normal_rate_pct": 34.8},
                    {"layer": "short_attack", "total_rows": 18, "high_rows": 4, "watch_rows": 2, "non_normal_rows": 6, "non_normal_rate_pct": 33.3},
                    {"layer": "defensive_watch", "total_rows": 4, "high_rows": 0, "watch_rows": 0, "non_normal_rows": 0, "non_normal_rate_pct": 0.0},
                ],
            }
        )

        self.assertEqual(plan["groups"][0]["group"], "satellite")
        self.assertEqual(plan["groups"][0]["suggested_additions"], 3)
        self.assertEqual(plan["layers"][0]["layer"], "midlong_core")
        self.assertGreaterEqual(plan["layers"][0]["suggested_additions"], 2)
        self.assertTrue(all(row["group"] != "etf" for row in plan["groups"]))

    def test_build_weekly_review_payload_and_write_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            outcomes_csv = root / "reco_outcomes.csv"
            feedback_csv = root / "feedback_weight_sensitivity.csv"
            snapshots_csv = root / "reco_snapshots.csv"
            alert_csv = root / "alert_tracking.csv"
            rank_csv = root / "daily_rank.csv"
            watchlist_csv = root / "watchlist.csv"
            out_md = root / "weekly_review.md"
            out_json = root / "weekly_review.json"

            pd.DataFrame(
                [
                    {
                        "signal_date": "2026-04-20",
                        "horizon_days": 1,
                        "watch_type": "midlong",
                        "ticker": "2330.TW",
                        "reco_status": "ok",
                        "action": "續抱",
                        "scenario_label": "強勢延伸盤",
                        "market_heat": "hot",
                        "spec_risk_score": 0,
                        "spec_risk_label": "正常",
                        "realized_ret_pct": 2.0,
                        "status": "ok",
                    },
                    {
                        "signal_date": "2026-04-21",
                        "horizon_days": 1,
                        "watch_type": "midlong",
                        "ticker": "2317.TW",
                        "reco_status": "below_threshold",
                        "action": "可分批",
                        "scenario_label": "高檔震盪盤",
                        "market_heat": "normal",
                        "spec_risk_score": 0,
                        "spec_risk_label": "正常",
                        "realized_ret_pct": 4.0,
                        "status": "ok",
                    },
                    {
                        "signal_date": "2026-04-21",
                        "horizon_days": 1,
                        "watch_type": "short",
                        "ticker": "3057.TW",
                        "reco_status": "ok",
                        "action": "等拉回",
                        "scenario_label": "強勢延伸盤",
                        "market_heat": "hot",
                        "spec_risk_score": 8,
                        "spec_risk_label": "疑似炒作風險高",
                        "realized_ret_pct": -2.0,
                        "status": "ok",
                    },
                    {
                        "signal_date": "2026-04-20",
                        "horizon_days": 1,
                        "watch_type": "short",
                        "ticker": "2330.TW",
                        "reco_status": "ok",
                        "action": "等拉回",
                        "scenario_label": "強勢延伸盤",
                        "market_heat": "normal",
                        "spec_risk_score": 0,
                        "spec_risk_label": "正常",
                        "realized_ret_pct": 1.0,
                        "status": "ok",
                    },
                ]
            ).to_csv(outcomes_csv, index=False)

            pd.DataFrame(
                [
                    {"signal_date": "2026-04-20", "watch_type": "midlong", "ticker": "2330.TW"},
                    {"signal_date": "2026-04-21", "watch_type": "midlong", "ticker": "2317.TW"},
                    {"signal_date": "2026-04-21", "watch_type": "short", "ticker": "3057.TW"},
                    {"signal_date": "2026-04-20", "watch_type": "short", "ticker": "2330.TW"},
                ]
            ).to_csv(snapshots_csv, index=False)

            pd.DataFrame(
                [
                    {"ticker": "3057.TW", "name": "喬鼎", "group": "theme", "layer": "short_attack", "spec_risk_score": 8, "spec_risk_label": "疑似炒作風險高", "spec_risk_subtype": "急拉爆量型", "ret5_pct": 24.0, "ret20_pct": 52.0, "rank": 1},
                    {"ticker": "6669.TW", "name": "緯穎", "group": "theme", "layer": "short_attack", "spec_risk_score": 4, "spec_risk_label": "投機偏高", "spec_risk_subtype": "急拉追價型", "ret5_pct": 19.0, "ret20_pct": 20.0, "rank": 2},
                    {"ticker": "2330.TW", "name": "台積電", "group": "core", "layer": "midlong_core", "spec_risk_score": 0, "spec_risk_label": "正常", "spec_risk_subtype": "正常", "ret5_pct": 4.0, "ret20_pct": 12.0, "rank": 3},
                ]
            ).to_csv(rank_csv, index=False)

            pd.DataFrame(
                [
                    {"ticker": "3057.TW", "name": "喬鼎", "group": "theme", "layer": "short_attack", "enabled": True},
                    {"ticker": "6669.TW", "name": "緯穎", "group": "theme", "layer": "short_attack", "enabled": True},
                    {"ticker": "2330.TW", "name": "台積電", "group": "core", "layer": "midlong_core", "enabled": True},
                ]
            ).to_csv(watchlist_csv, index=False)

            pd.DataFrame(
                [
                    {
                        "config_name": "70/30",
                        "watch_type": "midlong",
                        "action_label": "續抱",
                        "rank_delta": 0,
                        "score_delta": 0,
                    },
                    {
                        "config_name": "60/40",
                        "watch_type": "midlong",
                        "action_label": "續抱",
                        "rank_delta": 0,
                        "score_delta": 0.12,
                    },
                ]
            ).to_csv(feedback_csv, index=False)

            pd.DataFrame(
                [
                    {
                        "watch_type": "midlong",
                        "alert_close": 100.0,
                        "add_price": 95.0,
                        "trim_price": 105.0,
                        "stop_price": 90.0,
                        "ret1_future_pct": 2.0,
                    }
                ]
            ).to_csv(alert_csv, index=False)

            payload = build_weekly_review_payload(
                outcomes_csv=outcomes_csv,
                snapshots_csv=snapshots_csv,
                feedback_csv=feedback_csv,
                alert_csv=alert_csv,
                rank_csv=rank_csv,
                watchlist_csv=watchlist_csv,
                max_signal_dates=5,
            )
            markdown = render_weekly_review_markdown(payload)
            write_outputs(payload, out=out_md, json_out=out_json)

            saved = json.loads(out_json.read_text(encoding="utf-8"))
            self.assertTrue(out_md.exists())
            self.assertTrue(out_json.exists())

        self.assertIn("Weekly Review", markdown)
        self.assertIn("Midlong Threshold Gate", markdown)
        self.assertEqual(payload["summary"]["ok_rows"], 4)
        self.assertEqual(saved["summary"]["ok_rows"], 4)
        self.assertIn("spec_risk_overview", payload["summary"])
        self.assertIn("research_diagnostics", payload["summary"])
        self.assertIn("data_quality_gate", payload["summary"])
        self.assertIn("## Data Quality Gate", markdown)
        self.assertIn("## Data Quality Coverage By Horizon", markdown)
        self.assertIn("## Research Diagnostics", markdown)
        self.assertIn("## Spec Risk Highlights", markdown)
        self.assertIn("## Candidate Mix Guidance", markdown)
        self.assertIn("## Candidate Expansion Targets", markdown)
        self.assertIn("### By Group", markdown)
        self.assertIn("### By Layer", markdown)
        self.assertIn("### By Source Archetype", markdown)
        self.assertIn("### Practical Fill Directions", markdown)
        self.assertIn("### Watchlist Gap Snapshot By Group", markdown)
        self.assertIn("### Watchlist Gap Snapshot By Source", markdown)
        self.assertIn("## 開高不追 Tuning Draft", markdown)
        self.assertIn("Confidence note", markdown)
        self.assertIn("## Overall By Spec Risk", markdown)
        self.assertIn("## Overall By Spec Subtype", markdown)
        self.assertIn("## Spec Risk Check", markdown)
        self.assertIn("## Short Gate Promotion Watch", markdown)
        self.assertIn("## Short Gate Simulation", markdown)
        self.assertIn("## Weekly Decision Panel", markdown)
        self.assertIn("weekly_decision_panel", payload["tables"])
        self.assertIn("## Full Short Gate Promotion Watch", markdown)
        self.assertIn("## Recent Factor High-Low Spread", markdown)
        self.assertIn("## Full Factor High-Low Spread", markdown)
        self.assertIn("## Recent Factor Tear Sheet", markdown)
        self.assertIn("## Full Factor Tear Sheet", markdown)
        self.assertIn("## Recent Sensitivity Matrix", markdown)
        self.assertIn("## Full Sensitivity Matrix", markdown)
        self.assertIn("## Recent Tail Risk By Action", markdown)
        self.assertIn("## Recent Short Pullback Quality", markdown)
        self.assertTrue(payload["tables"]["recent_short_pullback_quality"])
        self.assertIn("## Full Short Pullback Confirmation", markdown)
        self.assertIn("full_short_pullback_confirmation", payload["tables"])
        self.assertIn("## Recent Short Pullback Trade Simulation Shadow", markdown)
        self.assertIn("recent_short_pullback_trade_simulation_shadow", payload["tables"])
        self.assertIn("## Full Short Pullback Trade Simulation Shadow", markdown)
        self.assertIn("full_short_pullback_trade_simulation_shadow", payload["tables"])
        self.assertIn("## Recent Hold Continuation Diagnostics", markdown)
        self.assertIn("recent_hold_continuation_diagnostics", payload["tables"])
        self.assertIn("## Full Hold Continuation Diagnostics", markdown)
        self.assertIn("full_hold_continuation_diagnostics", payload["tables"])
        self.assertIn("## Short Pullback Rule Recommendations", markdown)
        self.assertIn("short_pullback_rule_recommendations", payload["tables"])
        self.assertIn("## Short Pullback Exit Guard Recommendations", markdown)
        self.assertIn("short_pullback_exit_guard_recommendations", payload["tables"])
        self.assertIn("## Full Tail Risk By Action", markdown)
        self.assertIn("## Current Rank Spec Risk By Group", markdown)
        self.assertIn("## Current Rank Spec Risk By Layer", markdown)
        self.assertIn("## Current Rank Spec Risk By Source", markdown)
        self.assertIn("## Current Suspicious Candidates", markdown)
        self.assertIn("## ATR Band Checkpoints", markdown)
        self.assertIn("atr_band_checkpoints", payload["tables"])
        self.assertIn("## ATR Exit Verification", markdown)
        self.assertIn("atr_exit_verification", payload["tables"])
        self.assertIn("## ATR Exit Policy Simulation", markdown)
        self.assertIn("atr_exit_policy_simulation", payload["tables"])
        self.assertIn("## ATR Exit Policy Segment Simulation", markdown)
        self.assertIn("atr_exit_policy_segment_simulation", payload["tables"])
        self.assertIn("## Path Risk Sequencing", markdown)
        self.assertIn("path_risk_sequencing", payload["tables"])
        self.assertIn("prioritize groups like", markdown)
        self.assertIn("3057.TW", markdown)
        self.assertIn("spec_risk", payload["decisions"])
        self.assertIn("short_gate", payload["decisions"])
        self.assertIn("trade_simulation", payload["decisions"])
        self.assertIn("`trade_simulation`", markdown)
        self.assertIn("short_gate_tuning_draft", payload["summary"])
