from __future__ import annotations

import unittest

import pandas as pd

from stock_watch.backtesting.costs import (
    DEFAULT_COST_MODEL,
    CostModel,
    apply_costs_to_returns,
)


class ExecutionCostTest(unittest.TestCase):
    def test_default_round_trip_cost(self) -> None:
        # 2 * 0.1425% fee + 0.3% sell tax = 0.585%
        self.assertAlmostEqual(DEFAULT_COST_MODEL.round_trip_cost_pct(), 0.585, places=3)

    def test_net_return_less_than_gross_for_gain(self) -> None:
        net = DEFAULT_COST_MODEL.net_return_pct(10.0)
        self.assertLess(net, 10.0)

    def test_zero_gross_is_negative_after_costs(self) -> None:
        self.assertLess(DEFAULT_COST_MODEL.net_return_pct(0.0), 0.0)

    def test_loss_becomes_larger_loss(self) -> None:
        net = DEFAULT_COST_MODEL.net_return_pct(-5.0)
        self.assertLess(net, -5.0)

    def test_slippage_increases_cost(self) -> None:
        with_slip = CostModel(slippage_rate=0.001).round_trip_cost_pct()
        self.assertGreater(with_slip, DEFAULT_COST_MODEL.round_trip_cost_pct())

    def test_apply_costs_series(self) -> None:
        out = apply_costs_to_returns(pd.Series([10.0, -5.0, 0.0]))
        self.assertIsInstance(out, pd.Series)
        self.assertEqual(len(out), 3)
        self.assertTrue(all(out < pd.Series([10.0, -5.0, 0.0]).values))

    def test_apply_costs_list(self) -> None:
        out = apply_costs_to_returns([10.0, -5.0])
        self.assertEqual(len(out), 2)
        self.assertTrue(all(isinstance(v, float) for v in out))

    def test_zero_cost_model_is_identity(self) -> None:
        free = CostModel(fee_rate=0.0, sell_tax_rate=0.0, slippage_rate=0.0)
        self.assertAlmostEqual(free.net_return_pct(7.5), 7.5, places=6)
        self.assertEqual(free.round_trip_cost_pct(), 0.0)


if __name__ == "__main__":
    unittest.main()
