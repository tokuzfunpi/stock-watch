from __future__ import annotations

import unittest
from datetime import date
from tempfile import TemporaryDirectory
from pathlib import Path

from stock_watch.data.fundamentals import FinMindFundamentalProvider
from stock_watch.data.fundamentals import OfficialValuationProvider
from stock_watch.data.fundamentals import _read_env_file_value
from stock_watch.data.fundamentals import _read_token_file_value
from stock_watch.data.fundamentals import stock_id_from_ticker


class _FakeResponse:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return {"data": self._data}


class _FakeRawResponse(_FakeResponse):
    def json(self):
        return self._data


class _FakeSession:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def get(self, url, params, headers, timeout):
        dataset = params["dataset"]
        stock_id = params["data_id"]
        self.calls.append((dataset, stock_id))
        if dataset == "TaiwanStockPER":
            return _FakeResponse(
                [
                    {"date": "2026-01-01", "stock_id": stock_id, "dividend_yield": 4.0, "PER": 15.0, "PBR": 2.0}
                ]
            )
        if dataset == "TaiwanStockMonthRevenue":
            return _FakeResponse(
                [
                    {"date": "2025-02-01", "stock_id": stock_id, "revenue": 100.0, "revenue_month": 1, "revenue_year": 2025},
                    {"date": "2026-02-01", "stock_id": stock_id, "revenue": 125.0, "revenue_month": 1, "revenue_year": 2026},
                ]
            )
        if dataset == "TaiwanStockFinancialStatements":
            rows = []
            for idx, quarter in enumerate(["2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31"], start=1):
                rows.extend(
                    [
                        {"date": quarter, "stock_id": stock_id, "type": "EPS", "value": float(idx)},
                        {"date": quarter, "stock_id": stock_id, "type": "Revenue", "value": 1000.0},
                        {"date": quarter, "stock_id": stock_id, "type": "GrossProfit", "value": 400.0},
                        {"date": quarter, "stock_id": stock_id, "type": "OperatingIncome", "value": 250.0},
                        {"date": quarter, "stock_id": stock_id, "type": "IncomeAfterTaxes", "value": 200.0},
                    ]
                )
            return _FakeResponse(rows)
        if dataset == "TaiwanStockBalanceSheet":
            return _FakeResponse(
                [
                    {"date": "2026-03-31", "stock_id": stock_id, "type": "Equity", "value": 2000.0},
                    {"date": "2026-03-31", "stock_id": stock_id, "type": "Liabilities", "value": 1000.0},
                    {"date": "2026-03-31", "stock_id": stock_id, "type": "CurrentAssets", "value": 900.0},
                    {"date": "2026-03-31", "stock_id": stock_id, "type": "CurrentLiabilities", "value": 300.0},
                ]
            )
        if dataset == "TaiwanStockCashFlowsStatement":
            rows = []
            for quarter in ["2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31"]:
                rows.extend(
                    [
                        {"date": quarter, "stock_id": stock_id, "type": "NetCashInflowFromOperatingActivities", "value": 150.0},
                        {"date": quarter, "stock_id": stock_id, "type": "PropertyAndPlantAndEquipment", "value": -20.0},
                    ]
                )
            return _FakeResponse(rows)
        return _FakeResponse([])


class _FakeOfficialSession:
    def get(self, url, headers, timeout):
        if "BWIBBU_ALL" in url:
            return _FakeRawResponse(
                [
                    {"Code": "3034", "PEratio": "16.5", "PBratio": "2.1", "DividendYield": "4.2"},
                ]
            )
        if "peratio" in url:
            return _FakeRawResponse(
                [
                    {
                        "SecuritiesCompanyCode": "6510",
                        "PriceEarningRatio": "22.0",
                        "PriceBookRatio": "3.2",
                        "YieldRatio": "1.0",
                    },
                ]
            )
        if "t187ap05_L" in url:
            return _FakeRawResponse(
                [
                    {"公司代號": "3034", "營業收入-去年同月增減(%)": "25.5", "營業收入-當月營收": "1000"},
                ]
            )
        if "t187ap05_O" in url:
            return _FakeRawResponse(
                [
                    {"公司代號": "6510", "營業收入-去年同月增減(%)": "-5.0", "營業收入-當月營收": "900"},
                ]
            )
        if "t187ap06_L_ci" in url:
            return _FakeRawResponse(
                [
                    {
                        "公司代號": "3034",
                        "營業收入": "1000",
                        "營業毛利（毛損）": "400",
                        "營業利益（損失）": "250",
                        "淨利（淨損）歸屬於母公司業主": "200",
                        "基本每股盈餘（元）": "2.5",
                    },
                ]
            )
        if "t187ap07_L_ci" in url:
            return _FakeRawResponse(
                [
                    {
                        "公司代號": "3034",
                        "流動資產": "900",
                        "流動負債": "300",
                        "負債總額": "1000",
                        "權益總額": "2000",
                    },
                ]
            )
        return _FakeRawResponse([])


class FundamentalProviderTests(unittest.TestCase):
    def test_stock_id_from_ticker_normalizes_exchange_suffix(self) -> None:
        self.assertEqual(stock_id_from_ticker("6510.TWO"), "6510")

    def test_read_env_file_value_accepts_export_and_quotes(self) -> None:
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / ".env.local"
            path.write_text(
                "OTHER=value\nexport FINMIND_TOKEN='abc123'\n",
                encoding="utf-8",
            )

            self.assertEqual(_read_env_file_value(path, "FINMIND_TOKEN"), "abc123")

    def test_read_token_file_value_accepts_raw_secret(self) -> None:
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "finmind_token"
            path.write_text("# local only\nraw-token-123\n", encoding="utf-8")

            self.assertEqual(_read_token_file_value(path, "FINMIND_TOKEN"), "raw-token-123")

    def test_fetch_one_builds_quality_and_value_summary(self) -> None:
        session = _FakeSession()
        provider = FinMindFundamentalProvider(session=session, token="token")

        summary = provider.fetch_one("3034.TW", today=date(2026, 5, 6))

        self.assertEqual(summary.data_status, "ok")
        self.assertEqual(summary.pe, 15.0)
        self.assertEqual(round(summary.revenue_yoy_pct or 0, 2), 25.0)
        self.assertEqual(summary.quality_score, 5)
        self.assertEqual(summary.value_score, 4)
        self.assertEqual(summary.fundamental_action, "品質價值優先")
        self.assertIn(("TaiwanStockPER", "3034"), session.calls)

    def test_official_valuation_provider_builds_value_only_summary(self) -> None:
        provider = OfficialValuationProvider(session=_FakeOfficialSession())

        summary = provider.fetch_one("6510.TWO")

        self.assertEqual(summary.data_status, "official_public")
        self.assertEqual(summary.pe, 22.0)
        self.assertEqual(summary.pbr, 3.2)
        self.assertEqual(summary.value_score, 1)
        self.assertEqual(summary.revenue_yoy_pct, -5.0)
        self.assertEqual(summary.fundamental_action, "資料/品質不足")

    def test_official_provider_builds_public_financial_summary(self) -> None:
        provider = OfficialValuationProvider(session=_FakeOfficialSession())

        summary = provider.fetch_one("3034.TW")

        self.assertEqual(summary.data_status, "official_public")
        self.assertEqual(summary.pe, 16.5)
        self.assertEqual(summary.revenue_yoy_pct, 25.5)
        self.assertEqual(summary.eps_ttm, 2.5)
        self.assertEqual(summary.quality_score, 4)
        self.assertEqual(summary.value_score, 4)
        self.assertEqual(summary.fundamental_action, "品質價值優先")


if __name__ == "__main__":
    unittest.main()
