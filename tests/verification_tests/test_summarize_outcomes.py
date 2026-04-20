from __future__ import annotations

import unittest
from datetime import datetime

import pandas as pd

from daily_theme_watchlist import LOCAL_TZ
from verification.summarize_outcomes import build_summary_markdown, summarize_outcomes


class SummarizeOutcomesTests(unittest.TestCase):
    def test_summarize_outcomes_filters_non_ok_rows(self) -> None:
        df = pd.DataFrame(
            [
                {
                    "signal_date": "2026-04-17",
                    "horizon_days": 1,
                    "watch_type": "short",
                    "action": "等拉回",
                    "realized_ret_pct": 1.2,
                    "status": "ok",
                },
                {
                    "signal_date": "2026-04-17",
                    "horizon_days": 1,
                    "watch_type": "short",
                    "action": "等拉回",
                    "realized_ret_pct": -0.5,
                    "status": "no_price_series",
                },
            ]
        )
        parts = summarize_outcomes(df)
        self.assertEqual(int(parts["by_action"].iloc[0]["n"]), 1)

    def test_build_summary_markdown_renders_sections(self) -> None:
        df = pd.DataFrame(
            [
                {
                    "signal_date": "2026-04-17",
                    "horizon_days": 1,
                    "watch_type": "midlong",
                    "action": "續抱",
                    "realized_ret_pct": -2.0,
                    "status": "ok",
                }
            ]
        )
        md = build_summary_markdown(df, source="verification/watchlist_daily/reco_outcomes.csv", now_local=datetime(2026, 4, 21, 8, 50, tzinfo=LOCAL_TZ))
        self.assertIn("# Recommendation Outcomes Summary", md)
        self.assertIn("## Coverage", md)
        self.assertIn("## By Action", md)
