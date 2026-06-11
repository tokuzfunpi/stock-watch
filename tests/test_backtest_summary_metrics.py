from __future__ import annotations

import math
import unittest

import pandas as pd

from stock_watch.backtesting.core import _max_drawdown_pct, summarize_events


class SummarizeEventsMetricsTest(unittest.TestCase):
    def test_basic_summary_columns_present(self) -> None:
        events = pd.DataFrame({"ret_5d": [10.0, -5.0, 20.0, -2.0]})
        out = summarize_events(events, [5])
        self.assertEqual(len(out), 1)
        row = out.iloc[0]
        # Backward-compatible columns.
        for col in ("horizon", "trades", "win_rate_pct", "avg_return_pct", "median_return_pct"):
            self.assertIn(col, out.columns)
        # New risk-adjusted columns.
        for col in (
            "avg_win_pct",
            "avg_loss_pct",
            "payoff_ratio",
            "profit_factor",
            "std_return_pct",
            "max_drawdown_pct",
        ):
            self.assertIn(col, out.columns)
        self.assertEqual(int(row["trades"]), 4)
        self.assertEqual(int(row["horizon"]), 5)

    def test_win_rate_and_profit_factor(self) -> None:
        # wins: 10 + 20 = 30 ; losses: 5 + 2 = 7 (magnitude)
        events = pd.DataFrame({"ret_5d": [10.0, -5.0, 20.0, -2.0]})
        row = summarize_events(events, [5]).iloc[0]
        self.assertEqual(row["win_rate_pct"], 50.0)
        self.assertEqual(row["profit_factor"], round(30.0 / 7.0, 2))
        self.assertEqual(row["avg_win_pct"], 15.0)
        self.assertEqual(row["avg_loss_pct"], -3.5)
        self.assertEqual(row["payoff_ratio"], round(15.0 / 3.5, 2))

    def test_profit_factor_infinite_when_no_losses(self) -> None:
        events = pd.DataFrame({"ret_5d": [3.0, 4.0, 5.0]})
        row = summarize_events(events, [5]).iloc[0]
        self.assertTrue(math.isinf(row["profit_factor"]))
        self.assertEqual(row["win_rate_pct"], 100.0)
        self.assertEqual(row["max_drawdown_pct"], 0.0)

    def test_empty_horizon_skipped(self) -> None:
        events = pd.DataFrame({"ret_5d": [float("nan"), float("nan")]})
        out = summarize_events(events, [5])
        self.assertTrue(out.empty)

    def test_max_drawdown_is_non_positive(self) -> None:
        # Up then a sharp drop should produce a negative drawdown.
        returns = pd.Series([10.0, 10.0, -30.0, 5.0])
        dd = _max_drawdown_pct(returns)
        self.assertLess(dd, 0.0)

    def test_max_drawdown_zero_for_monotonic_gains(self) -> None:
        returns = pd.Series([1.0, 2.0, 3.0])
        self.assertEqual(_max_drawdown_pct(returns), 0.0)

    def test_max_drawdown_empty(self) -> None:
        self.assertEqual(_max_drawdown_pct(pd.Series([], dtype=float)), 0.0)


if __name__ == "__main__":
    unittest.main()
