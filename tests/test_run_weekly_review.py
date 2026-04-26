from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from run_weekly_review import build_decisions
from run_weekly_review import build_candidate_expansion_plan
from run_weekly_review import build_candidate_fill_directions
from run_weekly_review import build_candidate_source_plan
from run_weekly_review import build_watchlist_gap_snapshot
from run_weekly_review import build_rank_candidate_source_summary
from run_weekly_review import build_rank_coverage_guidance
from run_weekly_review import build_rank_spec_risk_coverage
from run_weekly_review import build_spec_risk_overview
from run_weekly_review import build_weekly_review_payload
from run_weekly_review import filter_recent_signal_dates
from run_weekly_review import render_weekly_review_markdown
from run_weekly_review import write_outputs


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
        self.assertEqual(decisions["atr"]["status"], "hold")
        self.assertEqual(decisions["feedback"]["status"], "hold")
        self.assertEqual(decisions["spec_risk"]["status"], "review")

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
        self.assertFalse(overview["same_subtype_extremes"])

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
        self.assertEqual(payload["summary"]["ok_rows"], 4)
        self.assertEqual(saved["summary"]["ok_rows"], 4)
        self.assertIn("spec_risk_overview", payload["summary"])
        self.assertIn("## Spec Risk Highlights", markdown)
        self.assertIn("## Candidate Mix Guidance", markdown)
        self.assertIn("## Candidate Expansion Targets", markdown)
        self.assertIn("### By Group", markdown)
        self.assertIn("### By Layer", markdown)
        self.assertIn("### By Source Archetype", markdown)
        self.assertIn("### Practical Fill Directions", markdown)
        self.assertIn("### Watchlist Gap Snapshot By Group", markdown)
        self.assertIn("### Watchlist Gap Snapshot By Source", markdown)
        self.assertIn("Confidence note", markdown)
        self.assertIn("## Overall By Spec Risk", markdown)
        self.assertIn("## Overall By Spec Subtype", markdown)
        self.assertIn("## Spec Risk Check", markdown)
        self.assertIn("## Current Rank Spec Risk By Group", markdown)
        self.assertIn("## Current Rank Spec Risk By Layer", markdown)
        self.assertIn("## Current Rank Spec Risk By Source", markdown)
        self.assertIn("## Current Suspicious Candidates", markdown)
        self.assertIn("prioritize groups like", markdown)
        self.assertIn("3057.TW", markdown)
        self.assertIn("spec_risk", payload["decisions"])
