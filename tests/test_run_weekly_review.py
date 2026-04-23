from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from run_weekly_review import build_decisions
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

    def test_build_weekly_review_payload_and_write_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            outcomes_csv = root / "reco_outcomes.csv"
            feedback_csv = root / "feedback_weight_sensitivity.csv"
            alert_csv = root / "alert_tracking.csv"
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
                        "realized_ret_pct": 4.0,
                        "status": "ok",
                    },
                ]
            ).to_csv(outcomes_csv, index=False)

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
                max_signal_dates=5,
            )
            markdown = render_weekly_review_markdown(payload)
            write_outputs(payload, out=out_md, json_out=out_json)

            saved = json.loads(out_json.read_text(encoding="utf-8"))
            self.assertTrue(out_md.exists())
            self.assertTrue(out_json.exists())

        self.assertIn("Weekly Review", markdown)
        self.assertEqual(payload["summary"]["ok_rows"], 2)
        self.assertEqual(saved["summary"]["ok_rows"], 2)
