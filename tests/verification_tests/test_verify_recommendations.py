from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import pandas as pd

from stock_watch.runtime import LOCAL_TZ
from verification.reports.verify_recommendations import _load_outcomes_aggregate
from verification.reports.verify_recommendations import build_verification_report_markdown
from verification.reports.verify_recommendations import upsert_csv_with_existing_header


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

    def test_load_outcomes_aggregate_includes_midlong_threshold_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            outcomes_csv = Path(tmpdir) / "reco_outcomes.csv"
            pd.DataFrame(
                [
                    {
                        "signal_date": "2026-04-22",
                        "horizon_days": 1,
                        "watch_type": "midlong",
                        "reco_status": "below_threshold",
                        "market_heat": "hot",
                        "action": "減碼觀察",
                        "realized_ret_pct": 4.0,
                        "status": "ok",
                    },
                    {
                        "signal_date": "2026-04-22",
                        "horizon_days": 1,
                        "watch_type": "midlong",
                        "reco_status": "ok",
                        "market_heat": "normal",
                        "action": "續抱",
                        "realized_ret_pct": 1.0,
                        "status": "ok",
                    },
                ]
            ).to_csv(outcomes_csv, index=False)

            aggregate = _load_outcomes_aggregate(outcomes_csv)

        self.assertIn("midlong_threshold_gate", aggregate)
        self.assertEqual(aggregate["midlong_threshold_gate"][0]["decision"], "block_loosening")

    def test_build_verification_report_markdown_renders_tables_without_tabulate(self) -> None:
        df = pd.DataFrame(
            [
                {
                    "rank": 1,
                    "ticker": "3057.TW",
                    "name": "喬鼎",
                    "group": "theme",
                    "layer": "short_attack",
                    "grade": "B",
                    "setup_score": 6,
                    "risk_score": 6,
                    "spec_risk_score": 8,
                    "spec_risk_label": "疑似炒作風險高",
                    "spec_risk_subtype": "急拉爆量型",
                    "spec_risk_note": "短線急漲、爆量、缺少趨勢支撐",
                    "ret5_pct": 24.0,
                    "ret20_pct": 52.0,
                    "volume_ratio20": 2.9,
                    "signals": "ACCEL",
                    "rank_change": 0,
                    "setup_change": 0,
                    "close": 42.0,
                    "date": "2026-04-17",
                },
                {
                    "rank": 2,
                    "ticker": "2330.TW",
                    "name": "台積電",
                    "group": "core",
                    "layer": "midlong_core",
                    "grade": "A",
                    "setup_score": 8,
                    "risk_score": 2,
                    "spec_risk_score": 0,
                    "ret5_pct": 8.5,
                    "ret20_pct": 12.0,
                    "volume_ratio20": 1.4,
                    "signals": "TREND",
                    "spec_risk_label": "正常",
                    "spec_risk_subtype": "正常",
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
        self.assertIn("3057.TW", report)
        self.assertIn("signal_template", report)
        self.assertIn("General", report)
        self.assertIn("## Spec Risk Watchlist", report)
        self.assertIn("高疑似炒作樣本", report)
        self.assertIn("Short spec risk counts", report)
        self.assertIn("急拉爆量型", report)
