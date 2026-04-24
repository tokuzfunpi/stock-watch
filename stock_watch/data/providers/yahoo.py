from __future__ import annotations

import logging

import pandas as pd


def alternate_taiwan_ticker(ticker: str) -> str:
    symbol = str(ticker or "").strip().upper()
    if "." not in symbol:
        return ""
    base, suffix = symbol.split(".", 1)
    if not (len(base) == 4 and base.isdigit()):
        return ""
    if suffix == "TW":
        return f"{base}.TWO"
    if suffix == "TWO":
        return f"{base}.TW"
    return ""


class YahooFinancePriceProvider:
    name = "yahoo"

    def __init__(self, yf_module, logger: logging.Logger | None = None) -> None:
        self._yf = yf_module
        self._logger = logger or logging.getLogger(__name__)

    def _download_daily_ohlcv(self, ticker: str, period: str) -> pd.DataFrame:
        df = self._yf.download(
            ticker,
            period=period,
            interval="1d",
            auto_adjust=True,
            progress=False,
            threads=False,
        )
        if df.empty:
            return df
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.rename(columns=str.title)
        return df[["Open", "High", "Low", "Close", "Volume"]].dropna().copy()

    def download_daily_ohlcv(self, ticker: str, period: str) -> pd.DataFrame:
        df = self._download_daily_ohlcv(ticker, period)
        if df.empty:
            alt_ticker = alternate_taiwan_ticker(ticker)
            if alt_ticker:
                df = self._download_daily_ohlcv(alt_ticker, period)
                if not df.empty:
                    self._logger.warning("Ticker fallback: %s -> %s", ticker, alt_ticker)
        return df

