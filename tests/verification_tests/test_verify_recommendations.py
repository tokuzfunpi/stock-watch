from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import pandas as pd

from daily_theme_watchlist import LOCAL_TZ
from verification.verify_recommendations import build_verification_report_markdown
from verification.verify_recommendations import upsert_csv_with_existing_header


class VerifyRecommendationsTests(unittest.TestCase):
    def test_upsert_csv_with_existing_header_replaces_duplicate_snapshot_keys(self) -> None:
        first = pd.DataFrame(
            [
                {
                    "generated_at": "2026-04-23 08:45:00 CST",
                    "signal_date": "2026-04-22",
                    "watch_type": "short",
                    "ticker": "3231.TW",
                    "name": "緯創",
                    "action": "等拉回",
                    "reco_status": "ok",
                }
            ]
        )
        second = pd.DataFrame(
            [
                {
                    "generated_at": "2026-04-23 09:55:00 CST",
                    "signal_date": "2026-04-22",
                    "watch_type": "short",
                    "ticker": "3231.TW",
                    "name": "緯創",
                    "action": "開高不追",
                    "reco_status": "below_threshold",
                }
            ]
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            snapshot_csv = Path(tmpdir) / "reco_snapshots.csv"
            upsert_csv_with_existing_header(snapshot_csv, first, key_cols=["signal_date", "watch_type", "ticker"])
            upsert_csv_with_existing_header(snapshot_csv, second, key_cols=["signal_date", "watch_type", "ticker"])

            out = pd.read_csv(snapshot_csv)

        self.assertEqual(len(out), 1)
        self.assertEqual(out.loc[0, "action"], "開高不追")
        self.assertEqual(out.loc[0, "reco_status"], "below_threshold")

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
