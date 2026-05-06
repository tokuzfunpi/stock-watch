from __future__ import annotations

import os
from dataclasses import dataclass
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from stock_watch.paths import REPO_ROOT

FINMIND_API_URL = "https://api.finmindtrade.com/api/v4/data"
TWSE_VALUATION_URL = "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL"
TPEX_VALUATION_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis"
TWSE_MONTHLY_REVENUE_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap05_L"
TPEX_MONTHLY_REVENUE_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O"
TWSE_STATEMENT_BASE_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap{report}_L{suffix}"
TPEX_STATEMENT_BASE_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap{report}_O{suffix}"
OFFICIAL_STATEMENT_SUFFIXES = ["_ci", "_fh", "_basi", "_bd", "_ins", "_mim"]
LOCAL_ENV_FILES = [
    REPO_ROOT / ".env.local",
    REPO_ROOT / "stock-watch.local.env",
    Path.home() / ".stock-watch.env",
]
LOCAL_TOKEN_FILES = [
    REPO_ROOT / "finmind_token",
    Path.home() / ".finmind_token",
]


@dataclass(frozen=True)
class FundamentalSummary:
    ticker: str
    pe: float | None = None
    pbr: float | None = None
    dividend_yield: float | None = None
    revenue_yoy_pct: float | None = None
    eps_ttm: float | None = None
    eps_yoy_pct: float | None = None
    roe_pct: float | None = None
    gross_margin_pct: float | None = None
    operating_margin_pct: float | None = None
    debt_to_equity_pct: float | None = None
    current_ratio: float | None = None
    operating_cashflow_ttm: float | None = None
    free_cashflow_ttm: float | None = None
    quality_score: int = 0
    value_score: int = 0
    fundamental_action: str = "資料不足"
    fundamental_reason: str = "FinMind 基本面資料不足"
    data_status: str = "missing"

    def as_dict(self) -> dict[str, object]:
        return {
            "ticker": self.ticker,
            "pe": self.pe,
            "pbr": self.pbr,
            "dividend_yield": self.dividend_yield,
            "revenue_yoy_pct": self.revenue_yoy_pct,
            "eps_ttm": self.eps_ttm,
            "eps_yoy_pct": self.eps_yoy_pct,
            "roe_pct": self.roe_pct,
            "gross_margin_pct": self.gross_margin_pct,
            "operating_margin_pct": self.operating_margin_pct,
            "debt_to_equity_pct": self.debt_to_equity_pct,
            "current_ratio": self.current_ratio,
            "operating_cashflow_ttm": self.operating_cashflow_ttm,
            "free_cashflow_ttm": self.free_cashflow_ttm,
            "quality_score": self.quality_score,
            "value_score": self.value_score,
            "fundamental_action": self.fundamental_action,
            "fundamental_reason": self.fundamental_reason,
            "fundamental_data_status": self.data_status,
        }


def stock_id_from_ticker(ticker: str) -> str:
    symbol = str(ticker or "").strip().upper()
    if "." in symbol:
        symbol = symbol.split(".", 1)[0]
    if not symbol:
        raise ValueError("empty ticker")
    return symbol


def _to_float(value: object) -> float | None:
    try:
        number = float(value)
    except Exception:
        return None
    if pd.isna(number):
        return None
    return number


def _strip_env_value(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1].strip()
    return text


def _read_env_file_value(path: Path, key: str) -> str:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    prefix = f"{key}="
    export_prefix = f"export {key}="
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(export_prefix):
            return _strip_env_value(line[len(export_prefix) :])
        if line.startswith(prefix):
            return _strip_env_value(line[len(prefix) :])
    return ""


def _read_token_file_value(path: Path, key: str) -> str:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            value = _read_env_file_value(path, key)
            return value
        return _strip_env_value(line)
    return ""


def _local_env_value(key: str) -> str:
    value = os.getenv(key, "").strip()
    if value:
        return value
    for path in LOCAL_ENV_FILES:
        value = _read_env_file_value(path, key)
        if value:
            return value
    for path in LOCAL_TOKEN_FILES:
        value = _read_token_file_value(path, key)
        if value:
            return value
    return ""


def _latest_value(df: pd.DataFrame, column: str) -> float | None:
    if df.empty or column not in df.columns:
        return None
    values = pd.to_numeric(df[column], errors="coerce").dropna()
    if values.empty:
        return None
    return float(values.iloc[-1])


def _quarter_type_series(df: pd.DataFrame, field_type: str) -> pd.Series:
    if df.empty or not {"date", "type", "value"}.issubset(df.columns):
        return pd.Series(dtype=float)
    work = df[df["type"].astype(str) == field_type].copy()
    if work.empty:
        return pd.Series(dtype=float)
    work["date"] = pd.to_datetime(work["date"], errors="coerce")
    work["value"] = pd.to_numeric(work["value"], errors="coerce")
    work = work.dropna(subset=["date", "value"]).sort_values("date")
    return pd.Series(work["value"].to_numpy(), index=work["date"])


def _ttm(series: pd.Series, periods: int = 4) -> float | None:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    if clean.empty:
        return None
    return float(clean.tail(periods).sum())


def _latest_from_series(series: pd.Series) -> float | None:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    if clean.empty:
        return None
    return float(clean.iloc[-1])


def _yoy_from_latest_quarter(series: pd.Series) -> float | None:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    if len(clean) < 5:
        return None
    latest = float(clean.iloc[-1])
    previous = float(clean.iloc[-5])
    if previous == 0:
        return None
    return (latest / abs(previous) - 1.0) * 100.0


def _latest_balance_value(df: pd.DataFrame, field_type: str) -> float | None:
    return _latest_from_series(_quarter_type_series(df, field_type))


def _first_float(row: pd.Series | dict[str, object], *columns: str) -> float | None:
    for column in columns:
        value = row.get(column) if isinstance(row, dict) else row.get(column)
        number = _to_float(value)
        if number is not None:
            return number
    return None


def _row_stock_id(row: pd.Series | dict[str, object]) -> str:
    for column in ["公司代號", "SecuritiesCompanyCode", "Code", "stock_id"]:
        value = row.get(column) if isinstance(row, dict) else row.get(column)
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _revenue_yoy_pct(monthly_revenue: pd.DataFrame) -> float | None:
    if monthly_revenue.empty or not {"revenue", "revenue_month", "revenue_year"}.issubset(monthly_revenue.columns):
        return None
    work = monthly_revenue.copy()
    work["revenue"] = pd.to_numeric(work["revenue"], errors="coerce")
    work["revenue_month"] = pd.to_numeric(work["revenue_month"], errors="coerce")
    work["revenue_year"] = pd.to_numeric(work["revenue_year"], errors="coerce")
    if "date" in work.columns:
        work["date"] = pd.to_datetime(work["date"], errors="coerce")
        work = work.sort_values("date")
    work = work.dropna(subset=["revenue", "revenue_month", "revenue_year"])
    if work.empty:
        return None
    latest = work.iloc[-1]
    prior = work[
        (work["revenue_month"] == latest["revenue_month"])
        & (work["revenue_year"] == latest["revenue_year"] - 1)
    ]
    if prior.empty:
        return None
    prior_revenue = float(prior.iloc[-1]["revenue"])
    if prior_revenue == 0:
        return None
    return (float(latest["revenue"]) / abs(prior_revenue) - 1.0) * 100.0


def _safe_ratio(numerator: float | None, denominator: float | None, multiplier: float = 1.0) -> float | None:
    if numerator is None or denominator in {None, 0}:
        return None
    return numerator / denominator * multiplier


def _score_summary(summary: dict[str, float | None]) -> tuple[int, int, str, str]:
    quality_score = 0
    reasons: list[str] = []

    if (summary.get("eps_ttm") or 0) > 0:
        quality_score += 1
        reasons.append("EPS TTM>0")
    if (summary.get("revenue_yoy_pct") or -999) > 0:
        quality_score += 1
        reasons.append("營收YoY正")
    if (summary.get("roe_pct") or 0) >= 12:
        quality_score += 1
        reasons.append("ROE>=12%")
    if (summary.get("debt_to_equity_pct") is not None) and (summary.get("debt_to_equity_pct") or 9999) <= 120:
        quality_score += 1
        reasons.append("負債權益可控")
    if (summary.get("free_cashflow_ttm") or 0) > 0:
        quality_score += 1
        reasons.append("FCF TTM>0")

    value_score = 0
    pe = summary.get("pe")
    pbr = summary.get("pbr")
    dividend_yield = summary.get("dividend_yield")
    if pe is not None and 0 < pe <= 18:
        value_score += 2
    elif pe is not None and 18 < pe <= 25:
        value_score += 1
    if pbr is not None and 0 < pbr <= 3:
        value_score += 1
    if dividend_yield is not None and dividend_yield >= 3:
        value_score += 1

    if quality_score >= 4 and value_score >= 2:
        action = "品質價值優先"
    elif quality_score >= 4:
        action = "品質佳但估值要等"
    elif quality_score >= 3 and value_score >= 2:
        action = "可研究但需確認"
    elif quality_score >= 3:
        action = "品質觀察"
    else:
        action = "資料/品質不足"

    reason = "、".join(reasons) if reasons else "基本面條件尚未明確"
    return quality_score, value_score, action, reason


class OfficialValuationProvider:
    def __init__(self, session: Any | None = None, timeout: int = 20) -> None:
        self._session = session or requests.Session()
        self._timeout = timeout
        self._cache: pd.DataFrame | None = None
        self._json_cache: dict[str, object] = {}
        self._statement_cache: dict[tuple[str, str], pd.DataFrame] = {}

    def _request_json(self, url: str) -> object:
        if url in self._json_cache:
            return self._json_cache[url]
        response = self._session.get(url, headers={"User-Agent": "stock-watch/1.0"}, timeout=self._timeout)
        response.raise_for_status()
        payload = response.json()
        self._json_cache[url] = payload
        return payload

    def fetch_all(self) -> pd.DataFrame:
        if self._cache is not None:
            return self._cache.copy()

        rows: list[dict[str, object]] = []
        twse_payload = self._request_json(TWSE_VALUATION_URL)
        if isinstance(twse_payload, list):
            for item in twse_payload:
                if not isinstance(item, dict):
                    continue
                rows.append(
                    {
                        "ticker": f"{item.get('Code', '')}.TW",
                        "stock_id": str(item.get("Code", "")).strip(),
                        "pe": _first_float(item, "PEratio"),
                        "pbr": _first_float(item, "PBratio"),
                        "dividend_yield": _first_float(item, "DividendYield"),
                        "fundamental_data_status": "official_valuation",
                    }
                )

        tpex_payload = self._request_json(TPEX_VALUATION_URL)
        if isinstance(tpex_payload, list):
            for item in tpex_payload:
                if not isinstance(item, dict):
                    continue
                rows.append(
                    {
                        "ticker": f"{item.get('SecuritiesCompanyCode', '')}.TWO",
                        "stock_id": str(item.get("SecuritiesCompanyCode", "")).strip(),
                        "pe": _first_float(item, "PriceEarningRatio"),
                        "pbr": _first_float(item, "PriceBookRatio"),
                        "dividend_yield": _first_float(item, "YieldRatio"),
                        "fundamental_data_status": "official_valuation",
                    }
                )

        self._cache = pd.DataFrame(rows)
        return self._cache.copy()

    def _fetch_monthly_revenue(self) -> pd.DataFrame:
        rows: list[dict[str, object]] = []
        for market, url in [("TW", TWSE_MONTHLY_REVENUE_URL), ("TWO", TPEX_MONTHLY_REVENUE_URL)]:
            payload = self._request_json(url)
            if not isinstance(payload, list):
                continue
            for item in payload:
                if not isinstance(item, dict):
                    continue
                stock_id = _row_stock_id(item)
                if not stock_id:
                    continue
                rows.append(
                    {
                        "ticker": f"{stock_id}.{market}",
                        "stock_id": stock_id,
                        "revenue_yoy_pct": _first_float(item, "營業收入-去年同月增減(%)"),
                        "revenue": _first_float(item, "營業收入-當月營收"),
                    }
                )
        return pd.DataFrame(rows)

    def _fetch_statement_rows(self, *, market: str, report: str) -> pd.DataFrame:
        cache_key = (market, report)
        if cache_key in self._statement_cache:
            return self._statement_cache[cache_key].copy()

        base_url = TWSE_STATEMENT_BASE_URL if market == "TW" else TPEX_STATEMENT_BASE_URL
        rows: list[dict[str, object]] = []
        for suffix in OFFICIAL_STATEMENT_SUFFIXES:
            url = base_url.format(report=report, suffix=suffix)
            payload = self._request_json(url)
            if not isinstance(payload, list):
                continue
            for item in payload:
                if not isinstance(item, dict):
                    continue
                stock_id = _row_stock_id(item)
                if not stock_id:
                    continue
                rows.append({**item, "stock_id": stock_id, "statement_suffix": suffix})
        frame = pd.DataFrame(rows)
        self._statement_cache[cache_key] = frame
        return frame.copy()

    def _match_official_row(self, df: pd.DataFrame, stock_id: str) -> pd.Series | None:
        if df.empty or "stock_id" not in df.columns:
            return None
        match = df[df["stock_id"].astype(str) == str(stock_id)]
        if match.empty:
            return None
        return match.iloc[0]

    def fetch_one(self, ticker: str) -> FundamentalSummary:
        stock_id = stock_id_from_ticker(ticker)
        try:
            valuations = self.fetch_all()
            monthly_revenue = self._fetch_monthly_revenue()
        except Exception as exc:
            return FundamentalSummary(
                ticker=ticker,
                fundamental_reason=f"Official public fetch failed: {exc}",
                data_status="failed",
            )
        valuation_row = self._match_official_row(valuations, stock_id)
        revenue_row = self._match_official_row(monthly_revenue, stock_id)
        market = "TWO" if str(ticker).upper().endswith(".TWO") else "TW"
        try:
            income_row = self._match_official_row(self._fetch_statement_rows(market=market, report="06"), stock_id)
            balance_row = self._match_official_row(self._fetch_statement_rows(market=market, report="07"), stock_id)
        except Exception:
            income_row = None
            balance_row = None

        if valuation_row is None and revenue_row is None and income_row is None and balance_row is None:
            return FundamentalSummary(ticker=ticker, fundamental_reason="官方公開資料找不到代號")

        revenue = _first_float(income_row, "營業收入", "收入") if income_row is not None else None
        gross_profit = (
            _first_float(income_row, "營業毛利（毛損）淨額", "營業毛利（毛損）")
            if income_row is not None
            else None
        )
        operating_income = _first_float(income_row, "營業利益（損失）", "營業利益") if income_row is not None else None
        net_income = (
            _first_float(
                income_row,
                "淨利（淨損）歸屬於母公司業主",
                "本期淨利（淨損）",
                "本期稅後淨利（淨損）",
                "本期淨利（淨損）歸屬於母公司業主",
                "繼續營業單位本期淨利（淨損）",
            )
            if income_row is not None
            else None
        )
        equity = (
            _first_float(balance_row, "權益總額", "權益總計", "歸屬於母公司業主之權益合計", "歸屬於母公司業主之權益")
            if balance_row is not None
            else None
        )
        liabilities = _first_float(balance_row, "負債總額", "負債總計") if balance_row is not None else None
        current_assets = _first_float(balance_row, "流動資產") if balance_row is not None else None
        current_liabilities = _first_float(balance_row, "流動負債") if balance_row is not None else None
        summary: dict[str, float | None] = {
            "pe": _to_float(valuation_row.get("pe")) if valuation_row is not None else None,
            "pbr": _to_float(valuation_row.get("pbr")) if valuation_row is not None else None,
            "dividend_yield": _to_float(valuation_row.get("dividend_yield")) if valuation_row is not None else None,
            "revenue_yoy_pct": _to_float(revenue_row.get("revenue_yoy_pct")) if revenue_row is not None else None,
            "eps_ttm": _first_float(income_row, "基本每股盈餘（元）") if income_row is not None else None,
            "eps_yoy_pct": None,
            "roe_pct": _safe_ratio(net_income, equity, 400.0),
            "gross_margin_pct": _safe_ratio(gross_profit, revenue, 100.0),
            "operating_margin_pct": _safe_ratio(operating_income, revenue, 100.0),
            "debt_to_equity_pct": _safe_ratio(liabilities, equity, 100.0),
            "current_ratio": _safe_ratio(current_assets, current_liabilities),
            "operating_cashflow_ttm": None,
            "free_cashflow_ttm": None,
        }
        quality_score, value_score, action, reason = _score_summary(summary)
        reason_parts = [reason]
        if income_row is not None or balance_row is not None:
            reason_parts.append("官方公開財報")
        if revenue_row is not None:
            reason_parts.append("月營收YoY")
        if valuation_row is not None:
            reason_parts.append("PE/PBR/殖利率")
        if summary["free_cashflow_ttm"] is None:
            reason_parts.append("FCF需FINMIND_TOKEN")
        data_status = "official_public" if income_row is not None or balance_row is not None or revenue_row is not None else "official_valuation"
        if data_status == "official_valuation":
            action = "估值可研究" if value_score >= 2 else "估值偏高/待確認"
        return FundamentalSummary(
            ticker=ticker,
            pe=summary["pe"],
            pbr=summary["pbr"],
            dividend_yield=summary["dividend_yield"],
            revenue_yoy_pct=summary["revenue_yoy_pct"],
            eps_ttm=summary["eps_ttm"],
            eps_yoy_pct=summary["eps_yoy_pct"],
            roe_pct=summary["roe_pct"],
            gross_margin_pct=summary["gross_margin_pct"],
            operating_margin_pct=summary["operating_margin_pct"],
            debt_to_equity_pct=summary["debt_to_equity_pct"],
            current_ratio=summary["current_ratio"],
            operating_cashflow_ttm=summary["operating_cashflow_ttm"],
            free_cashflow_ttm=summary["free_cashflow_ttm"],
            quality_score=quality_score,
            value_score=value_score,
            fundamental_action=action,
            fundamental_reason="、".join(dict.fromkeys(reason_parts)),
            data_status=data_status,
        )

    def fetch_many(self, tickers: list[str]) -> pd.DataFrame:
        rows = [self.fetch_one(ticker).as_dict() for ticker in tickers]
        return pd.DataFrame(rows)


class FinMindFundamentalProvider:
    def __init__(self, session: Any | None = None, token: str = "", timeout: int = 20) -> None:
        self._session = session or requests.Session()
        self._token = token.strip() or _local_env_value("FINMIND_TOKEN")
        self._timeout = timeout
        self._valuation_provider = OfficialValuationProvider(session=self._session, timeout=timeout)

    def _request_dataset(self, dataset: str, ticker: str, start_date: str) -> pd.DataFrame:
        params = {
            "dataset": dataset,
            "data_id": stock_id_from_ticker(ticker),
            "start_date": start_date,
        }
        headers = {}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        response = self._session.get(FINMIND_API_URL, params=params, headers=headers, timeout=self._timeout)
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") or []
        return pd.DataFrame(data)

    def fetch_one(self, ticker: str, *, today: date | None = None) -> FundamentalSummary:
        if not self._token:
            return self._valuation_provider.fetch_one(ticker)

        end = today or date.today()
        start_recent = date(end.year - 1, 1, 1).isoformat()
        try:
            per = self._request_dataset("TaiwanStockPER", ticker, start_recent)
            revenue = self._request_dataset("TaiwanStockMonthRevenue", ticker, start_recent)
            financials = self._request_dataset("TaiwanStockFinancialStatements", ticker, start_recent)
            balance = self._request_dataset("TaiwanStockBalanceSheet", ticker, start_recent)
            cashflow = self._request_dataset("TaiwanStockCashFlowsStatement", ticker, start_recent)
        except Exception as exc:
            fallback = self._valuation_provider.fetch_one(ticker)
            if fallback.data_status in {"official_public", "official_valuation"}:
                return replace(
                    fallback,
                    fundamental_reason=f"{fallback.fundamental_reason}；FinMind fetch failed: {exc}",
                )
            return FundamentalSummary(
                ticker=ticker,
                fundamental_reason=f"FinMind fetch failed: {exc}",
                data_status="failed",
            )

        eps = _quarter_type_series(financials, "EPS")
        revenue_quarter = _quarter_type_series(financials, "Revenue")
        gross_profit = _quarter_type_series(financials, "GrossProfit")
        operating_income = _quarter_type_series(financials, "OperatingIncome")
        income_after_taxes = _quarter_type_series(financials, "IncomeAfterTaxes")
        operating_cashflow = _quarter_type_series(cashflow, "NetCashInflowFromOperatingActivities")
        if operating_cashflow.empty:
            operating_cashflow = _quarter_type_series(cashflow, "CashFlowsFromOperatingActivities")
        capex = _quarter_type_series(cashflow, "PropertyAndPlantAndEquipment")

        latest_revenue = _latest_from_series(revenue_quarter)
        gross_margin = _safe_ratio(_latest_from_series(gross_profit), latest_revenue, 100.0)
        operating_margin = _safe_ratio(_latest_from_series(operating_income), latest_revenue, 100.0)
        equity = _latest_balance_value(balance, "Equity") or _latest_balance_value(balance, "EquityAttributableToOwnersOfParent")
        liabilities = _latest_balance_value(balance, "Liabilities")
        current_assets = _latest_balance_value(balance, "CurrentAssets")
        current_liabilities = _latest_balance_value(balance, "CurrentLiabilities")
        net_income_ttm = _ttm(income_after_taxes)
        operating_cashflow_ttm = _ttm(operating_cashflow)
        capex_ttm = _ttm(capex)
        free_cashflow_ttm = None
        if operating_cashflow_ttm is not None:
            free_cashflow_ttm = operating_cashflow_ttm + (capex_ttm or 0.0)

        summary: dict[str, float | None] = {
            "pe": _latest_value(per, "PER"),
            "pbr": _latest_value(per, "PBR"),
            "dividend_yield": _latest_value(per, "dividend_yield"),
            "revenue_yoy_pct": _revenue_yoy_pct(revenue),
            "eps_ttm": _ttm(eps),
            "eps_yoy_pct": _yoy_from_latest_quarter(eps),
            "roe_pct": _safe_ratio(net_income_ttm, equity, 100.0),
            "gross_margin_pct": gross_margin,
            "operating_margin_pct": operating_margin,
            "debt_to_equity_pct": _safe_ratio(liabilities, equity, 100.0),
            "current_ratio": _safe_ratio(current_assets, current_liabilities),
            "operating_cashflow_ttm": operating_cashflow_ttm,
            "free_cashflow_ttm": free_cashflow_ttm,
        }
        quality_score, value_score, action, reason = _score_summary(summary)
        return FundamentalSummary(
            ticker=ticker,
            pe=summary["pe"],
            pbr=summary["pbr"],
            dividend_yield=summary["dividend_yield"],
            revenue_yoy_pct=summary["revenue_yoy_pct"],
            eps_ttm=summary["eps_ttm"],
            eps_yoy_pct=summary["eps_yoy_pct"],
            roe_pct=summary["roe_pct"],
            gross_margin_pct=summary["gross_margin_pct"],
            operating_margin_pct=summary["operating_margin_pct"],
            debt_to_equity_pct=summary["debt_to_equity_pct"],
            current_ratio=summary["current_ratio"],
            operating_cashflow_ttm=summary["operating_cashflow_ttm"],
            free_cashflow_ttm=summary["free_cashflow_ttm"],
            quality_score=quality_score,
            value_score=value_score,
            fundamental_action=action,
            fundamental_reason=reason,
            data_status="ok",
        )

    def fetch_many(self, tickers: list[str], *, today: date | None = None) -> pd.DataFrame:
        rows = [self.fetch_one(ticker, today=today).as_dict() for ticker in tickers]
        return pd.DataFrame(rows)
