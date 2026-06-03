from __future__ import annotations

import math
import unittest

from stock_watch.signals.market_breadth import compute_breadth


class MarketBreadthTest(unittest.TestCase):
    def test_empty_universe(self) -> None:
        snap = compute_breadth([])
        self.assertEqual(snap.universe, 0)
        self.assertIsNone(snap.pct_above_ma)
        self.assertEqual(snap.label, "未知")

    def test_all_above_ma_strong(self) -> None:
        rows = [{"close": 10, "ma20": 9, "ret1_pct": 1.0} for _ in range(10)]
        snap = compute_breadth(rows)
        self.assertEqual(snap.pct_above_ma, 100.0)
        self.assertEqual(snap.label, "強勢普漲")
        self.assertEqual(snap.advancers, 10)
        self.assertEqual(snap.decliners, 0)

    def test_mixed_breadth(self) -> None:
        rows = [
            {"close": 10, "ma20": 9, "ret1_pct": 1.0},
            {"close": 8, "ma20": 9, "ret1_pct": -1.0},
            {"close": 11, "ma20": 9, "ret1_pct": 2.0},
            {"close": 7, "ma20": 9, "ret1_pct": -0.5},
        ]
        snap = compute_breadth(rows)
        self.assertEqual(snap.pct_above_ma, 50.0)
        self.assertEqual(snap.advancers, 2)
        self.assertEqual(snap.decliners, 2)
        self.assertEqual(snap.advance_decline_ratio, 1.0)
        self.assertEqual(snap.label, "分歧")

    def test_advance_decline_infinite_when_no_decliners(self) -> None:
        rows = [{"close": 10, "ma20": 9, "ret1_pct": 1.0}, {"close": 12, "ma20": 9, "ret1_pct": 3.0}]
        snap = compute_breadth(rows)
        self.assertTrue(math.isinf(snap.advance_decline_ratio))

    def test_missing_ma_values_ignored(self) -> None:
        rows = [
            {"close": 10, "ma20": None, "ret1_pct": 1.0},
            {"close": 11, "ma20": 9, "ret1_pct": 1.0},
        ]
        snap = compute_breadth(rows)
        # Only one row has a usable MA, and it is above => 100%.
        self.assertEqual(snap.pct_above_ma, 100.0)

    def test_weak_breadth_label(self) -> None:
        rows = [{"close": 8, "ma20": 9, "ret1_pct": -1.0} for _ in range(10)]
        snap = compute_breadth(rows)
        self.assertEqual(snap.pct_above_ma, 0.0)
        self.assertEqual(snap.label, "普遍走弱")

    def test_as_dict_keys(self) -> None:
        snap = compute_breadth([{"close": 10, "ma20": 9, "ret1_pct": 1.0}])
        self.assertEqual(
            set(snap.as_dict().keys()),
            {"universe", "pct_above_ma", "advancers", "decliners", "advance_decline_ratio", "breadth_label"},
        )


if __name__ == "__main__":
    unittest.main()
