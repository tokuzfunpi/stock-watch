from __future__ import annotations

import unittest

import pandas as pd

from stock_watch.data.providers.finmind import (
    normalize_finmind_frame,
    period_to_date_range,
    resolve_finmind_dataset,
)
from stock_watch.data.providers.yahoo import alternate_taiwan_ticker


class ProviderHelperTests(unittest.TestCase):
    def test_alternate_taiwan_ticker_flips_exchange_suffix(self) -> None:
        self.assertEqual(alternate_taiwan_ticker("2330.TW"), "2330.TWO")
        self.assertEqual(alternate_taiwan_ticker("2330.TWO"), "2330.TW")
        self.assertEqual(alternate_taiwan_ticker("NVDA"), "")

    def test_resolve_finmind_dataset_handles_tw_stock_and_index(self) -> None:
        self.assertEqual(resolve_finmind_dataset("2330.TW"), ("TaiwanStockPrice", "2330"))
        self.assertEqual(resolve_finmind_dataset("2330.TWO"), ("TaiwanStockPrice", "2330"))
        self.assertEqual(resolve_finmind_dataset("^TWII"), ("TaiwanStockTotalReturnIndex", "TAIEX"))

    def test_period_to_date_range_returns_iso_dates(self) -> None:
        start_date, end_date = period_to_date_range("3y")

        self.assertRegex(start_date, r"^\d{4}-\d{2}-\d{2}$")
        self.assertRegex(end_date, r"^\d{4}-\d{2}-\d{2}$")
        self.assertLess(start_date, end_date)

    def test_normalize_finmind_frame_maps_stock_price_schema(self) -> None:
        raw = pd.DataFrame(
            [
                {
                    "date": "2026-01-02",
                    "open": 100.0,
                    "max": 103.0,
                    "min": 99.0,
                    "close": 102.0,
                    "Trading_Volume": 12345,
                }
            ]
        )

        frame = normalize_finmind_frame("TaiwanStockPrice", raw)

        self.assertEqual(list(frame.columns), ["Open", "High", "Low", "Close", "Volume"])
        self.assertEqual(float(frame.iloc[0]["Close"]), 102.0)

    def test_normalize_finmind_frame_maps_index_schema(self) -> None:
        raw = pd.DataFrame(
            [
                {
                    "date": "2026-01-02",
                    "price": 20000.0,
                }
            ]
        )

        frame = normalize_finmind_frame("TaiwanStockTotalReturnIndex", raw)

        self.assertEqual(float(frame.iloc[0]["Open"]), 20000.0)
        self.assertEqual(float(frame.iloc[0]["Volume"]), 0.0)
