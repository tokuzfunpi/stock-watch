from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from verification.evaluate_recommendations import compute_forward_return_pct
from verification.evaluate_recommendations import enrich_scenario_label_columns
from verification.evaluate_recommendations import is_valid_signal_date
from verification.evaluate_recommendations import _chunked


class EvaluateRecommendationsTests(unittest.TestCase):
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
