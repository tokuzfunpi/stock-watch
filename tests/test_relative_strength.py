from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from stock_watch.signals.relative_strength import (
    compute_relative_strength,
    rs_rating,
    rs_ratio_series,
)


class RelativeStrengthTest(unittest.TestCase):
    def setUp(self) -> None:
        self.idx = pd.date_range("2024-01-01", periods=60, freq="B")

    def test_outperformer_flagged(self) -> None:
        stock = pd.Series(np.linspace(100, 140, 60), index=self.idx)
        bench = pd.Series(np.linspace(100, 110, 60), index=self.idx)
        r = compute_relative_strength(stock, bench, lookback=20)
        self.assertTrue(r.outperforming)
        self.assertGreater(r.excess_return_pct, 0)
        self.assertGreater(r.rs_momentum_pct, 0)
        self.assertGreater(r.rs_ratio, 1.0)

    def test_underperformer_flagged(self) -> None:
        stock = pd.Series(np.linspace(100, 103, 60), index=self.idx)
        bench = pd.Series(np.linspace(100, 120, 60), index=self.idx)
        r = compute_relative_strength(stock, bench, lookback=20)
        self.assertFalse(r.outperforming)
        self.assertLess(r.excess_return_pct, 0)

    def test_short_series_returns_none(self) -> None:
        stock = pd.Series([100.0, 101.0, 102.0])
        bench = pd.Series([100.0, 100.5, 101.0])
        r = compute_relative_strength(stock, bench, lookback=20)
        self.assertIsNone(r.rs_ratio)
        self.assertIsNone(r.outperforming)

    def test_ratio_series_rebased_to_one(self) -> None:
        stock = pd.Series(np.linspace(100, 140, 60), index=self.idx)
        bench = pd.Series(np.linspace(100, 110, 60), index=self.idx)
        ratio = rs_ratio_series(stock, bench)
        self.assertAlmostEqual(float(ratio.iloc[0]), 1.0, places=6)

    def test_misaligned_indices_are_intersected(self) -> None:
        stock = pd.Series(np.linspace(100, 140, 60), index=self.idx)
        bench = pd.Series(np.linspace(100, 110, 40), index=self.idx[:40])
        r = compute_relative_strength(stock, bench, lookback=10)
        self.assertIsNotNone(r.rs_ratio)

    def test_rs_rating_bounds_and_order(self) -> None:
        dist = pd.Series([-5, -2, 0, 1, 3, 8])
        self.assertEqual(rs_rating(dist, 8), 99)
        self.assertGreaterEqual(rs_rating(dist, -5), 1)
        self.assertEqual(rs_rating(pd.Series([], dtype=float), 1.0), 50)
        high = rs_rating(dist, 8)
        low = rs_rating(dist, -5)
        self.assertGreater(high, low)


if __name__ == "__main__":
    unittest.main()
