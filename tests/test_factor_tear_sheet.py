from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from stock_watch.backtesting.tear_sheet import factor_tear_sheet, monotonicity_score


def _predictive_events(seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for s in (2, 4, 6, 8):
        for _ in range(40):
            rows.append(
                {
                    "setup_score": s,
                    "risk_score": 6 - s // 2,
                    "ret_1d": rng.normal(s * 0.1, 1.0),
                    "ret_5d": rng.normal(s * 0.5, 2.0),
                    "ret_20d": rng.normal(s * 1.0, 4.0),
                }
            )
    return pd.DataFrame(rows)


class FactorTearSheetTest(unittest.TestCase):
    def test_basic_shape(self) -> None:
        ts = factor_tear_sheet(_predictive_events(), factor_col="setup_score", horizons=(1, 5, 20))
        self.assertFalse(ts.empty)
        self.assertEqual(
            set(ts.columns),
            {"factor", "bucket", "horizon", "trades", "win_rate_pct", "avg_return_pct", "median_return_pct"},
        )
        # 4 buckets x 3 horizons
        self.assertEqual(len(ts), 12)

    def test_predictive_factor_is_monotonic(self) -> None:
        ts = factor_tear_sheet(_predictive_events(), factor_col="setup_score", horizons=(5,))
        score = monotonicity_score(ts, horizon=5, higher_is_better=True)
        self.assertIsNotNone(score)
        self.assertGreater(score, 0.8)

    def test_quantile_mode_labels(self) -> None:
        ts = factor_tear_sheet(
            _predictive_events(), factor_col="setup_score", use_quantiles=True, n_buckets=4, horizons=(5,)
        )
        self.assertTrue(ts["factor"].iloc[0].endswith("_quantile"))
        self.assertTrue(all(str(b).startswith("Q") for b in ts["bucket"]))

    def test_empty_input(self) -> None:
        self.assertTrue(factor_tear_sheet(pd.DataFrame()).empty)
        self.assertTrue(factor_tear_sheet(None).empty)

    def test_missing_factor_column(self) -> None:
        df = _predictive_events().drop(columns=["setup_score"])
        self.assertTrue(factor_tear_sheet(df, factor_col="setup_score").empty)

    def test_missing_horizon_columns(self) -> None:
        df = _predictive_events()[["setup_score", "risk_score"]]
        self.assertTrue(factor_tear_sheet(df, factor_col="setup_score").empty)

    def test_monotonicity_none_when_single_bucket(self) -> None:
        df = pd.DataFrame({"setup_score": [5, 5, 5], "ret_5d": [1.0, 2.0, 3.0]})
        ts = factor_tear_sheet(df, factor_col="setup_score", horizons=(5,))
        self.assertIsNone(monotonicity_score(ts, horizon=5))


if __name__ == "__main__":
    unittest.main()
