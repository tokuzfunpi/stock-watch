from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from stock_watch.signals.detect import add_indicators, add_momentum_indicators


def _make_ohlcv(n: int = 120, seed: int = 0, trend: float = 0.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100.0 + np.cumsum(rng.normal(trend, 1.0, n))
    high = close + np.abs(rng.normal(0, 0.5, n))
    low = close - np.abs(rng.normal(0, 0.5, n))
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.DataFrame(
        {
            "Open": close,
            "High": high,
            "Low": low,
            "Close": close,
            "Volume": rng.integers(1000, 5000, n),
        },
        index=idx,
    )


class MomentumIndicatorsTest(unittest.TestCase):
    def test_columns_added(self) -> None:
        out = add_momentum_indicators(_make_ohlcv())
        for col in (
            "RSI14",
            "MACD",
            "MACD_Signal",
            "MACD_Hist",
            "ADX14",
            "DI_Plus14",
            "DI_Minus14",
        ):
            self.assertIn(col, out.columns)

    def test_rsi_within_bounds(self) -> None:
        out = add_momentum_indicators(_make_ohlcv(seed=3))
        rsi = out["RSI14"].dropna()
        self.assertFalse(rsi.empty)
        self.assertTrue(bool(rsi.between(0, 100).all()))

    def test_adx_within_bounds(self) -> None:
        out = add_momentum_indicators(_make_ohlcv(seed=4))
        adx = out["ADX14"].dropna()
        self.assertFalse(adx.empty)
        self.assertTrue(bool(adx.between(0, 100).all()))

    def test_macd_hist_is_macd_minus_signal(self) -> None:
        out = add_momentum_indicators(_make_ohlcv(seed=5))
        tail = out.dropna(subset=["MACD", "MACD_Signal", "MACD_Hist"]).tail(10)
        self.assertFalse(tail.empty)
        for _, row in tail.iterrows():
            self.assertAlmostEqual(row["MACD_Hist"], row["MACD"] - row["MACD_Signal"], places=6)

    def test_rsi_high_in_strong_uptrend(self) -> None:
        # Monotonically rising close => only gains => RSI should be 100.
        n = 60
        idx = pd.date_range("2024-01-01", periods=n, freq="B")
        close = np.linspace(100, 160, n)
        df = pd.DataFrame(
            {"Open": close, "High": close + 0.5, "Low": close - 0.5, "Close": close, "Volume": 1000},
            index=idx,
        )
        out = add_momentum_indicators(df)
        self.assertAlmostEqual(float(out["RSI14"].iloc[-1]), 100.0, places=2)

    def test_add_indicators_includes_momentum(self) -> None:
        out = add_indicators(_make_ohlcv())
        self.assertIn("RSI14", out.columns)
        self.assertIn("MACD", out.columns)
        self.assertIn("ADX14", out.columns)

    def test_does_not_mutate_input(self) -> None:
        df = _make_ohlcv()
        before = set(df.columns)
        add_momentum_indicators(df)
        self.assertEqual(before, set(df.columns))


if __name__ == "__main__":
    unittest.main()
