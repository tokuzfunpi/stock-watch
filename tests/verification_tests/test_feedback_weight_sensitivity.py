from __future__ import annotations

import unittest
from datetime import datetime

import pandas as pd

from daily_theme_watchlist import LOCAL_TZ
from verification.feedback_weight_sensitivity import (
    build_feedback_summary_for_weights,
    build_markdown,
    compare_weight_configs,
    parse_weight_configs,
)


class FeedbackWeightSensitivityTests(unittest.TestCase):
    def test_parse_weight_configs_normalizes_to_one(self) -> None:
        configs = parse_weight_configs("70:30,80:20")
        self.assertEqual([cfg.name for cfg in configs], ["70/30", "80/20"])
        self.assertAlmostEqual(configs[0].base_weight + configs[0].recent_weight, 1.0, places=6)

    def test_compare_weight_configs_detects_rank_shift(self) -> None:
        hist = pd.DataFrame(
            [
                {"alert_date": "2026-04-20", "watch_type": "short", "action_label": "等拉回", "ret5_future_pct": 2.0},
                {"alert_date": "2026-04-19", "watch_type": "short", "action_label": "等拉回", "ret5_future_pct": 2.0},
                {"alert_date": "2026-04-18", "watch_type": "short", "action_label": "等拉回", "ret5_future_pct": 2.0},
                {"alert_date": "2026-04-20", "watch_type": "short", "action_label": "續追蹤", "ret5_future_pct": 8.0},
                {"alert_date": "2026-04-19", "watch_type": "short", "action_label": "續追蹤", "ret5_future_pct": -3.0},
                {"alert_date": "2026-04-18", "watch_type": "short", "action_label": "續追蹤", "ret5_future_pct": -3.0},
                {"alert_date": "2026-04-17", "watch_type": "short", "action_label": "續追蹤", "ret5_future_pct": -3.0},
                {"alert_date": "2026-04-16", "watch_type": "short", "action_label": "續追蹤", "ret5_future_pct": -3.0},
            ]
        )

        summaries = []
        for config in parse_weight_configs("70:30,10:90"):
            summaries.append(build_feedback_summary_for_weights(hist, config))
        summary = pd.concat(summaries, ignore_index=True)
        compare_df = compare_weight_configs(summary, baseline_name="70/30")

        shifted = compare_df[
            (compare_df["config_name"] == "10/90")
            & (compare_df["watch_type"] == "short")
            & (compare_df["action_label"] == "續追蹤")
        ].iloc[0]
        self.assertNotEqual(float(shifted["score_delta"]), 0.0)

    def test_build_markdown_renders_sections(self) -> None:
        hist = pd.DataFrame(
            [
                {"alert_date": "2026-04-20", "watch_type": "midlong", "action_label": "續抱", "ret20_future_pct": 5.0},
                {"alert_date": "2026-04-19", "watch_type": "midlong", "action_label": "續抱", "ret20_future_pct": 4.0},
                {"alert_date": "2026-04-18", "watch_type": "midlong", "action_label": "可分批", "ret20_future_pct": 1.0},
                {"alert_date": "2026-04-17", "watch_type": "midlong", "action_label": "可分批", "ret20_future_pct": -1.0},
                {"alert_date": "2026-04-16", "watch_type": "midlong", "action_label": "可分批", "ret20_future_pct": -2.0},
            ]
        )
        configs = parse_weight_configs("70:30,80:20")
        summary = pd.concat([build_feedback_summary_for_weights(hist, config) for config in configs], ignore_index=True)
        compare_df = compare_weight_configs(summary, baseline_name="70/30")
        md = build_markdown(summary, compare_df, baseline_name="70/30", source="alert_tracking.csv", now_local=datetime(2026, 4, 23, 9, 30, tzinfo=LOCAL_TZ))

        self.assertIn("# Feedback Weight Sensitivity", md)
        self.assertIn("## Findings", md)
        self.assertIn("## Action Scores", md)
        self.assertIn("## Rank Deltas vs Baseline", md)
