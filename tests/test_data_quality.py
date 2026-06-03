from __future__ import annotations

import unittest
from datetime import date

import numpy as np
import pandas as pd

from stock_watch.data.quality import DataQualityReport, check_price_history


def _frame(n: int = 80) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.DataFrame(
        {"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": 1.0, "Volume": 100},
        index=idx,
    )


class DataQualityTest(unittest.TestCase):
    def test_good_frame_passes(self) -> None:
        df = _frame()
        report = check_price_history(df, as_of=df.index[-1].date())
        self.assertTrue(report.ok)
        self.assertEqual(report.rows, 80)
        self.assertEqual(report.issues, ())

    def test_empty_frame_fails(self) -> None:
        report = check_price_history(None)
        self.assertFalse(report.ok)
        self.assertIn("empty", report.issues)
        report2 = check_price_history(pd.DataFrame())
        self.assertFalse(report2.ok)

    def test_insufficient_history(self) -> None:
        df = _frame(10)
        report = check_price_history(df, as_of=df.index[-1].date(), min_rows=60)
        self.assertFalse(report.ok)
        self.assertTrue(any("insufficient_history" in i for i in report.issues))

    def test_staleness(self) -> None:
        df = _frame()
        report = check_price_history(df, as_of=date(2024, 12, 31), max_staleness_days=7)
        self.assertFalse(report.ok)
        self.assertTrue(any(i.startswith("stale") for i in report.issues))

    def test_recent_nan_close(self) -> None:
        df = _frame()
        df.loc[df.index[-1], "Close"] = np.nan
        report = check_price_history(df, as_of=df.index[-1].date())
        self.assertFalse(report.ok)
        self.assertIn("nan_recent_close", report.issues)

    def test_missing_columns(self) -> None:
        df = _frame().drop(columns=["Volume"])
        report = check_price_history(df, as_of=df.index[-1].date())
        self.assertFalse(report.ok)
        self.assertTrue(any("missing_columns" in i for i in report.issues))

    def test_non_positive_close(self) -> None:
        df = _frame()
        df.loc[df.index[-1], "Close"] = -1.0
        report = check_price_history(df, as_of=df.index[-1].date())
        self.assertFalse(report.ok)
        self.assertIn("non_positive_close", report.issues)

    def test_as_dict_shape(self) -> None:
        report = DataQualityReport(ok=True, rows=5, last_date="2024-01-01", staleness_days=0)
        d = report.as_dict()
        self.assertEqual(
            set(d.keys()), {"ok", "rows", "last_date", "staleness_days", "issues"}
        )


if __name__ == "__main__":
    unittest.main()
