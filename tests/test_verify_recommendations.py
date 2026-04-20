from __future__ import annotations

import unittest
from datetime import datetime

import pandas as pd

from daily_theme_watchlist import LOCAL_TZ
from verify_recommendations import build_verification_report_markdown


class VerifyRecommendationsTests(unittest.TestCase):
    def test_build_verification_report_markdown_renders_tables_without_tabulate(self) -> None:
        df = pd.DataFrame(
            [
                {
                    "rank": 1,
                    "ticker": "2330.TW",
                    "name": "台積電",
                    "group": "core",
                    "layer": "midlong_core",
                    "grade": "A",
                    "setup_score": 8,
                    "risk_score": 2,
                    "ret5_pct": 8.5,
                    "ret20_pct": 12.0,
                    "volume_ratio20": 1.4,
                    "signals": "TREND",
                    "spec_risk_label": "正常",
                    "rank_change": 0,
                    "setup_change": 0,
                    "close": 950.0,
                    "date": "2026-04-17",
                }
            ]
        )

        report = build_verification_report_markdown(
            df,
            source="theme_watchlist_daily/daily_rank.csv",
            now_local=datetime(2026, 4, 20, 8, 50, tzinfo=LOCAL_TZ),
        )

        self.assertIn("# Recommendation Verification", report)
        self.assertIn("## Short-Term Candidates", report)
        self.assertIn("| ticker |", report)
        self.assertIn("2330.TW", report)

