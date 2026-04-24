from __future__ import annotations

from typing import Protocol

import pandas as pd


class DailyPriceProvider(Protocol):
    name: str

    def download_daily_ohlcv(self, ticker: str, period: str) -> pd.DataFrame:
        ...

