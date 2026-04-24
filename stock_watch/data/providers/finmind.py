from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pandas as pd

FINMIND_API_URL = "https://api.finmindtrade.com/api/v4/data"
FINMIND_INDEX_MAP = {
    "^TWII": "TAIEX",
}


def period_to_date_range(period: str, end_date: date | None = None) -> tuple[str, str]:
    end = end_date or date.today()
    raw = str(period or "").strip().lower()
    if not raw:
        raw = "3y"

    digits = "".join(ch for ch in raw if ch.isdigit())
    unit = raw[len(digits):] or "d"
    count = int(digits or "1")

    if unit == "y":
        delta = timedelta(days=365 * count + 7)
    elif unit == "mo":
        delta = timedelta(days=31 * count + 7)
    elif unit == "wk":
        delta = timedelta(days=7 * count + 7)
    else:
        delta = timedelta(days=count + 7)

    start = end - delta
    return start.isoformat(), end.isoformat()


def resolve_finmind_dataset(ticker: str) -> tuple[str, str]:
    symbol = str(ticker or "").strip().upper()
    if symbol in FINMIND_INDEX_MAP:
        return "TaiwanStockTotalReturnIndex", FINMIND_INDEX_MAP[symbol]
    if "." in symbol:
        base, suffix = symbol.split(".", 1)
        if suffix in {"TW", "TWO"} and len(base) == 4 and base.isdigit():
            return "TaiwanStockPrice", base
    if len(symbol) == 4 and symbol.isdigit():
        return "TaiwanStockPrice", symbol
    raise ValueError(f"FinMind unsupported ticker: {ticker}")


def normalize_finmind_frame(dataset: str, data: pd.DataFrame) -> pd.DataFrame:
    if data.empty:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])

    if dataset == "TaiwanStockPrice":
        frame = data.rename(
            columns={
                "open": "Open",
                "max": "High",
                "min": "Low",
                "close": "Close",
                "Trading_Volume": "Volume",
            }
        )[["date", "Open", "High", "Low", "Close", "Volume"]].copy()
    elif dataset == "TaiwanStockTotalReturnIndex":
        frame = data.rename(columns={"price": "Close"})[["date", "Close"]].copy()
        frame["Open"] = frame["Close"]
        frame["High"] = frame["Close"]
        frame["Low"] = frame["Close"]
        frame["Volume"] = 0.0
        frame = frame[["date", "Open", "High", "Low", "Close", "Volume"]]
    else:
        raise ValueError(f"Unsupported FinMind dataset: {dataset}")

    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame.set_index("date").sort_index()
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    return frame.dropna(subset=["Close"]).copy()


class FinMindPriceProvider:
    name = "finmind"

    def __init__(self, session, token: str = "") -> None:
        self._session = session
        self._token = token.strip()

    def _request_data(self, dataset: str, data_id: str, period: str) -> pd.DataFrame:
        start_date, end_date = period_to_date_range(period)
        params: dict[str, Any] = {
            "dataset": dataset,
            "data_id": data_id,
            "start_date": start_date,
            "end_date": end_date,
        }
        headers = {}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        response = self._session.get(
            FINMIND_API_URL,
            params=params,
            headers=headers,
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") or []
        return pd.DataFrame(data)

    def download_daily_ohlcv(self, ticker: str, period: str) -> pd.DataFrame:
        dataset, data_id = resolve_finmind_dataset(ticker)
        data = self._request_data(dataset, data_id, period)
        return normalize_finmind_frame(dataset, data)
