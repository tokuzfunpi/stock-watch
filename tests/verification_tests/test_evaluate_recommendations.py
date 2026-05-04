from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from verification.workflows import evaluate_recommendations as er
from verification.workflows.evaluate_recommendations import EvalConfig
from verification.workflows.evaluate_recommendations import compute_forward_return_pct
from verification.workflows.evaluate_recommendations import dedupe_outcomes_by_key
from verification.workflows.evaluate_recommendations import dedupe_snapshots_by_key
from verification.workflows.evaluate_recommendations import enrich_scenario_label_columns
from verification.workflows.evaluate_recommendations import fetch_close_series
from verification.workflows.evaluate_recommendations import is_valid_signal_date
from verification.workflows.evaluate_recommendations import _spec_profile_from_snapshot_row
from verification.workflows.evaluate_recommendations import _cache_covers_required_date
from verification.workflows.evaluate_recommendations import _chunked


class EvaluateRecommendationsTests(unittest.TestCase):
    def test_dedupe_snapshots_by_key_keeps_latest_generated_row(self) -> None:
        snapshots = pd.DataFrame(
            [
                {
                    "generated_at": "2026-04-23 08:45:00 CST",
                    "signal_date": "2026-04-22",
                    "watch_type": "short",
                    "ticker": "3231.TW",
                    "action": "等拉回",
                },
                {
                    "generated_at": "2026-04-23 09:55:00 CST",
                    "signal_date": "2026-04-22",
                    "watch_type": "short",
                    "ticker": "3231.TW",
                    "action": "開高不追",
                },
            ]
        )

        out = dedupe_snapshots_by_key(snapshots)

        self.assertEqual(len(out), 1)
        self.assertEqual(out.iloc[0]["action"], "開高不追")

    def test_dedupe_outcomes_by_key_prefers_ok_and_latest_evaluated_row(self) -> None:
        outcomes = pd.DataFrame(
            [
                {
                    "evaluated_at": "2026-04-24 14:00:00 CST",
                    "signal_date": "2026-04-17",
                    "horizon_days": 1,
                    "watch_type": "short",
                    "ticker": "2495.TW",
                    "status": "insufficient_forward_data",
                    "action": "開高不追",
                },
                {
                    "evaluated_at": "2026-04-25 14:00:00 CST",
                    "signal_date": "2026-04-17",
                    "horizon_days": 1,
                    "watch_type": "short",
                    "ticker": "2495.TW",
                    "status": "ok",
                    "action": "開高不追",
                },
                {
                    "evaluated_at": "2026-04-25 15:00:00 CST",
                    "signal_date": "2026-04-17",
                    "horizon_days": 1,
                    "watch_type": "short",
                    "ticker": "2495.TW",
                    "status": "ok",
                    "action": "分批落袋",
                },
            ]
        )

        out = dedupe_outcomes_by_key(outcomes)

        self.assertEqual(len(out), 1)
        self.assertEqual(out.iloc[0]["status"], "ok")
        self.assertEqual(out.iloc[0]["action"], "分批落袋")

    def test_is_valid_signal_date_accepts_yyyy_mm_dd(self) -> None:
        self.assertTrue(is_valid_signal_date("2026-04-17"))
        self.assertFalse(is_valid_signal_date("2026/04/17"))
        self.assertFalse(is_valid_signal_date("2026-4-7"))

    def test_chunked_splits(self) -> None:
        self.assertEqual(_chunked(["a", "b", "c"], 2), [["a", "b"], ["c"]])

    def test_compute_forward_return_pct_ok(self) -> None:
        s = pd.Series(
            [100.0, 110.0, 105.0],
            index=pd.to_datetime(["2026-04-17", "2026-04-18", "2026-04-21"]),
            name="Close",
        )
        ret_pct, out_close, status, detail = compute_forward_return_pct(s, "2026-04-17", 1)
        self.assertEqual(status, "ok")
        self.assertEqual(detail, "")
        self.assertAlmostEqual(out_close or 0.0, 110.0)
        self.assertAlmostEqual(ret_pct or 0.0, 10.0)

    def test_compute_forward_return_pct_missing_date(self) -> None:
        s = pd.Series(
            [100.0, 110.0],
            index=pd.to_datetime(["2026-04-18", "2026-04-21"]),
            name="Close",
        )
        ret_pct, out_close, status, detail = compute_forward_return_pct(s, "2026-04-17", 1)
        self.assertEqual(status, "ok")
        self.assertIn("signal_date_shifted", detail)
        self.assertAlmostEqual(out_close or 0.0, 110.0)
        self.assertAlmostEqual(ret_pct or 0.0, 10.0)

    def test_cache_covers_required_date_checks_latest_index(self) -> None:
        s = pd.Series(
            [100.0, 110.0],
            index=pd.to_datetime(["2026-04-28", "2026-04-29"]),
            name="Close",
        )

        self.assertTrue(_cache_covers_required_date(s, "2026-04-29"))
        self.assertFalse(_cache_covers_required_date(s, "2026-04-30"))

    def test_fetch_close_series_refreshes_stale_cache_for_required_signal_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = Path(tmpdir)
            pd.DataFrame(
                {
                    "Date": ["2026-04-28", "2026-04-29"],
                    "Close": [100.0, 101.0],
                }
            ).to_csv(cache_dir / "2330_TW.csv", index=False)
            cfg = EvalConfig(cache_dir=cache_dir)

            fresh_df = pd.DataFrame(
                {
                    "Close": [100.0, 101.0, 102.0],
                },
                index=pd.to_datetime(["2026-04-28", "2026-04-29", "2026-04-30"]),
            )

            with patch.object(er, "_download_prices", return_value=(fresh_df, "")) as mock_download:
                series_map, errors = fetch_close_series(["2330.TW"], cfg, required_end_date="2026-04-30")

            self.assertEqual(errors, {})
            self.assertIn("2330.TW", series_map)
            self.assertEqual(str(series_map["2330.TW"].index.max().date()), "2026-04-30")
            self.assertEqual(len(series_map["2330.TW"]), 3)
            mock_download.assert_called()
            refreshed = pd.read_csv(cache_dir / "2330_TW.csv")
            self.assertEqual(refreshed.iloc[-1]["Date"], "2026-04-30")

    def test_enrich_scenario_label_columns_prefers_snapshots_then_alert_tracking(self) -> None:
        outcomes = pd.DataFrame(
            [
                {"signal_date": "2026-04-22", "watch_type": "short", "ticker": "2356.TW", "scenario_label": ""},
                {"signal_date": "2026-04-21", "watch_type": "midlong", "ticker": "2330.TW", "scenario_label": ""},
                {"signal_date": "2026-04-20", "watch_type": "short", "ticker": "3013.TW", "scenario_label": ""},
            ]
        )
        snapshots = pd.DataFrame(
            [
                {"signal_date": "2026-04-22", "watch_type": "short", "ticker": "2356.TW", "scenario_label": "高檔震盪盤"},
            ]
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            alert_csv = Path(tmpdir) / "alert_tracking.csv"
            pd.DataFrame(
                [
                    {"alert_date": "2026-04-21", "watch_type": "midlong", "ticker": "2330.TW", "scenario_label": "權值撐盤、個股轉弱"},
                ]
            ).to_csv(alert_csv, index=False)

            out = enrich_scenario_label_columns(outcomes, snapshots=snapshots, alert_tracking_csv=alert_csv)

        self.assertEqual(out.loc[0, "scenario_label"], "高檔震盪盤")
        self.assertEqual(out.loc[1, "scenario_label"], "權值撐盤、個股轉弱")
        self.assertEqual(out.loc[2, "scenario_label"], "unknown")

    def test_enrich_scenario_label_columns_treats_unknown_as_missing(self) -> None:
        outcomes = pd.DataFrame(
            [
                {"signal_date": "2026-04-20", "watch_type": "short", "ticker": "3013.TW", "scenario_label": "unknown"},
            ]
        )
        snapshots = pd.DataFrame(
            [
                {"signal_date": "2026-04-20", "watch_type": "short", "ticker": "3013.TW", "scenario_label": "強勢延伸盤"},
            ]
        )

        out = enrich_scenario_label_columns(outcomes, snapshots=snapshots)

        self.assertEqual(out.loc[0, "scenario_label"], "強勢延伸盤")

    def test_spec_profile_from_snapshot_row_backfills_legacy_snapshot_fields(self) -> None:
        row = pd.Series(
            {
                "ticker": "3057.TW",
                "signals": "ACCEL",
                "risk_score": 6,
                "ret5_pct": 24.0,
                "ret20_pct": 52.0,
                "volume_ratio20": 2.9,
                "bias20_pct": 16.0,
            }
        )

        score, label, subtype, note = _spec_profile_from_snapshot_row(row)

        self.assertIsNotNone(score)
        self.assertGreaterEqual(score or 0, 6)
        self.assertEqual(label, "疑似炒作風險高")
        self.assertEqual(subtype, "急拉爆量型")
        self.assertTrue(note)

    def test_main_dedupes_existing_outcomes_on_rerun_without_new_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            snapshot_csv = root / "reco_snapshots.csv"
            outcomes_csv = root / "reco_outcomes.csv"

            pd.DataFrame(
                [
                    {
                        "generated_at": "2026-04-24 09:00:00 CST",
                        "signal_date": "2026-04-17",
                        "watch_type": "short",
                        "ticker": "2495.TW",
                        "name": "普安",
                        "action": "開高不追",
                        "reco_status": "below_threshold",
                        "grade": "B",
                        "setup_score": 5,
                        "risk_score": 3,
                        "ret5_pct": 3.0,
                        "ret20_pct": 7.0,
                        "volume_ratio20": 1.1,
                        "signals": "ACCEL",
                        "scenario_label": "強勢延伸盤",
                    }
                ]
            ).to_csv(snapshot_csv, index=False)

            duplicate_rows = pd.DataFrame(
                [
                    {
                        "evaluated_at": "2026-04-25 14:00:00 CST",
                        "signal_date": "2026-04-17",
                        "horizon_days": 1,
                        "watch_type": "short",
                        "ticker": "2495.TW",
                        "name": "普安",
                        "reco_status": "below_threshold",
                        "action": "開高不追",
                        "grade": "B",
                        "setup_score": 5,
                        "risk_score": 3,
                        "ret5_pct": 3.0,
                        "ret20_pct": 7.0,
                        "volume_ratio20": 1.1,
                        "signals": "ACCEL",
                        "scenario_label": "強勢延伸盤",
                        "market_heat": "hot",
                        "market_heat_reason": "",
                        "out_close": 100.0,
                        "realized_ret_pct": 1.23,
                        "status": "ok",
                        "status_detail": "",
                    },
                    {
                        "evaluated_at": "2026-04-25 14:05:00 CST",
                        "signal_date": "2026-04-17",
                        "horizon_days": 1,
                        "watch_type": "short",
                        "ticker": "2495.TW",
                        "name": "普安",
                        "reco_status": "below_threshold",
                        "action": "開高不追",
                        "grade": "B",
                        "setup_score": 5,
                        "risk_score": 3,
                        "ret5_pct": 3.0,
                        "ret20_pct": 7.0,
                        "volume_ratio20": 1.1,
                        "signals": "ACCEL",
                        "scenario_label": "強勢延伸盤",
                        "market_heat": "hot",
                        "market_heat_reason": "",
                        "out_close": 100.0,
                        "realized_ret_pct": 1.23,
                        "status": "ok",
                        "status_detail": "",
                    },
                ]
            )
            duplicate_rows.to_csv(outcomes_csv, index=False)

            with patch.object(er, "fetch_close_series", return_value=({}, {})):
                code = er.main(
                    [
                        "--snapshot-csv",
                        str(snapshot_csv),
                        "--outcomes-csv",
                        str(outcomes_csv),
                        "--horizons",
                        "1",
                        "--signal-date",
                        "2026-04-17",
                    ]
                )

            self.assertEqual(code, 0)
            cleaned = pd.read_csv(outcomes_csv)
            self.assertEqual(len(cleaned), 1)
            self.assertEqual(cleaned.iloc[0]["ticker"], "2495.TW")

    def test_main_refreshes_stale_cache_before_evaluating_latest_signal_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            snapshot_csv = root / "reco_snapshots.csv"
            outcomes_csv = root / "reco_outcomes.csv"
            cache_dir = root / "cache"
            cache_dir.mkdir(parents=True, exist_ok=True)

            pd.DataFrame(
                [
                    {
                        "generated_at": "2026-05-01 14:00:00 CST",
                        "signal_date": "2026-04-30",
                        "watch_type": "short",
                        "ticker": "2330.TW",
                        "name": "台積電",
                        "action": "等拉回",
                        "reco_status": "ok",
                        "grade": "A",
                        "setup_score": 8,
                        "risk_score": 2,
                        "ret5_pct": 6.0,
                        "ret20_pct": 18.0,
                        "volume_ratio20": 1.6,
                        "signals": "TREND",
                        "scenario_label": "高檔震盪盤",
                    }
                ]
            ).to_csv(snapshot_csv, index=False)

            pd.DataFrame(
                {
                    "Date": ["2026-04-28", "2026-04-29"],
                    "Close": [100.0, 101.0],
                }
            ).to_csv(cache_dir / "2330_TW.csv", index=False)

            fresh_df = pd.DataFrame(
                {"Close": [100.0, 101.0, 102.0, 103.0]},
                index=pd.to_datetime(["2026-04-28", "2026-04-29", "2026-04-30", "2026-05-01"]),
            )

            with patch.object(er, "_download_prices", return_value=(fresh_df, "")) as mock_download:
                code = er.main(
                    [
                        "--snapshot-csv",
                        str(snapshot_csv),
                        "--outcomes-csv",
                        str(outcomes_csv),
                        "--cache-dir",
                        str(cache_dir),
                        "--signal-date",
                        "2026-04-30",
                        "--horizons",
                        "1",
                    ]
                )

            self.assertEqual(code, 0)
            mock_download.assert_called()
            outcomes = pd.read_csv(outcomes_csv)
            self.assertEqual(len(outcomes), 1)
            self.assertEqual(str(outcomes.iloc[0]["status"]), "ok")
            self.assertEqual(str(outcomes.iloc[0]["signal_date"]), "2026-04-30")
            refreshed = pd.read_csv(cache_dir / "2330_TW.csv")
            self.assertEqual(refreshed.iloc[-1]["Date"], "2026-05-01")
