from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from stock_watch.cli import quality_value


class QualityValueReportTests(unittest.TestCase):
    def test_main_writes_low_price_and_research_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            rank_csv = root / "daily_rank.csv"
            outdir = root / "out"
            pd.DataFrame(
                [
                    {
                        "rank": 1,
                        "ticker": "2356.TW",
                        "name": "英業達",
                        "group": "theme",
                        "layer": "short_attack",
                        "close": 47.9,
                        "ret20_pct": 17.2,
                        "volume_ratio20": 0.32,
                        "ma20": 45.0,
                        "ma60": 43.0,
                        "setup_score": 10,
                        "risk_score": 0,
                        "spec_risk_score": 0,
                        "spec_risk_label": "正常",
                        "signals": "TREND",
                        "score_band": "進攻優勢區",
                        "atr_pct": 3.3,
                    },
                    {
                        "rank": 2,
                        "ticker": "1560.TW",
                        "name": "中砂",
                        "group": "satellite",
                        "layer": "quality_value",
                        "close": 900.0,
                        "ret20_pct": 8.0,
                        "volume_ratio20": 0.7,
                        "ma20": 870.0,
                        "ma60": 830.0,
                        "setup_score": 7,
                        "risk_score": 1,
                        "spec_risk_score": 0,
                        "spec_risk_label": "正常",
                        "signals": "TREND",
                        "score_band": "偏強可追蹤",
                        "atr_pct": 2.5,
                    },
                    {
                        "rank": 3,
                        "ticker": "9999.TW",
                        "name": "過熱股",
                        "group": "theme",
                        "layer": "short_attack",
                        "close": 20.0,
                        "ret20_pct": 50.0,
                        "volume_ratio20": 3.0,
                        "ma20": 15.0,
                        "ma60": 12.0,
                        "setup_score": 12,
                        "risk_score": 8,
                        "spec_risk_score": 8,
                        "spec_risk_label": "疑似炒作風險高",
                        "signals": "SURGE,TREND",
                        "score_band": "高風險追價區",
                        "atr_pct": 9.0,
                    },
                ]
            ).to_csv(rank_csv, index=False)

            code = quality_value.main(
                ["--rank-csv", str(rank_csv), "--outdir", str(outdir), "--no-fundamentals", "--no-similar-scout"]
            )

            report = (outdir / "quality_value_report.md").read_text(encoding="utf-8")
            candidates = pd.read_csv(outdir / "quality_value_candidates.csv")
            entry_plan = pd.read_csv(outdir / "quality_value_entry_plan.csv")

        self.assertEqual(code, 0)
        self.assertIn("英業達", report)
        self.assertIn("中砂", report)
        self.assertIn("買點 / 停損 / 加碼紀律", report)
        self.assertNotIn("| 3 | 9999.TW", report)
        self.assertEqual(set(candidates["bucket"]), {"low_price_health", "quality_value_research"})
        self.assertIn("entry_bias", entry_plan.columns)

    def test_main_merges_fundamental_overlay_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            rank_csv = root / "daily_rank.csv"
            outdir = root / "out"
            pd.DataFrame(
                [
                    {
                        "rank": 1,
                        "ticker": "3034.TW",
                        "name": "聯詠",
                        "group": "satellite",
                        "layer": "quality_value",
                        "close": 439.5,
                        "ret20_pct": 14.3,
                        "volume_ratio20": 1.1,
                        "ma20": 410.0,
                        "ma60": 390.0,
                        "setup_score": 13,
                        "risk_score": 1,
                        "spec_risk_score": 0,
                        "spec_risk_label": "正常",
                        "signals": "TREND",
                        "score_band": "進攻優勢區",
                        "atr_pct": 3.0,
                    }
                ]
            ).to_csv(rank_csv, index=False)

            fake_fundamentals = pd.DataFrame(
                [
                    {
                        "ticker": "3034.TW",
                        "pe": 15.2,
                        "quality_score": 5,
                        "value_score": 3,
                        "fundamental_action": "品質價值優先",
                        "fundamental_reason": "EPS TTM>0",
                        "fundamental_data_status": "ok",
                    }
                ]
            )

            original_provider = quality_value.FinMindFundamentalProvider

            class FakeProvider:
                def fetch_many(self, tickers):
                    assert tickers == ["3034.TW"]
                    return fake_fundamentals

            try:
                quality_value.FinMindFundamentalProvider = lambda: FakeProvider()
                code = quality_value.main(["--rank-csv", str(rank_csv), "--outdir", str(outdir), "--no-similar-scout"])
            finally:
                quality_value.FinMindFundamentalProvider = original_provider

            report = (outdir / "quality_value_report.md").read_text(encoding="utf-8")
            candidates = pd.read_csv(outdir / "quality_value_candidates.csv")

        self.assertEqual(code, 0)
        self.assertIn("品質價值優先", report)
        self.assertEqual(candidates.iloc[0]["fundamental_action"], "品質價值優先")

    def test_entry_plan_separates_priority_pullback_and_overheated(self) -> None:
        rows = pd.DataFrame(
            [
                {
                    "ticker": "3034.TW",
                    "name": "聯詠",
                    "bucket": "quality_value_research",
                    "close": 439.5,
                    "ma20": 410.93,
                    "ma60": 390.48,
                    "setup_score": 13,
                    "risk_score": 1,
                    "spec_risk_score": 0,
                    "spec_risk_label": "正常",
                    "signals": "TREND",
                    "atr_pct": 3.04,
                    "_action": "優先研究",
                    "fundamental_action": "品質價值優先",
                    "quality_score": 4,
                    "value_score": 3,
                },
                {
                    "ticker": "3005.TW",
                    "name": "神基",
                    "bucket": "quality_value_research",
                    "close": 97.3,
                    "ma20": 98.75,
                    "ma60": 107.8,
                    "setup_score": 5,
                    "risk_score": 0,
                    "spec_risk_score": 0,
                    "spec_risk_label": "正常",
                    "signals": "BASE,PULLBACK",
                    "atr_pct": 2.17,
                    "_action": "觀察轉強",
                    "fundamental_action": "品質價值優先",
                    "quality_score": 4,
                    "value_score": 4,
                },
                {
                    "ticker": "2376.TW",
                    "name": "技嘉",
                    "bucket": "quality_value_research",
                    "close": 301.0,
                    "ma20": 275.2,
                    "ma60": 245.68,
                    "setup_score": 11,
                    "risk_score": 5,
                    "spec_risk_score": 5,
                    "spec_risk_label": "投機偏高",
                    "signals": "SURGE,TREND",
                    "atr_pct": 3.52,
                    "_action": "過熱先等",
                    "fundamental_action": "品質價值優先",
                    "quality_score": 4,
                    "value_score": 3,
                },
            ]
        )

        plan = quality_value.build_entry_plan(rows)

        by_ticker = plan.set_index("ticker")
        self.assertEqual(by_ticker.loc["3034.TW", "entry_bias"], "分批試單")
        self.assertEqual(by_ticker.loc["3005.TW", "entry_bias"], "等轉強")
        self.assertEqual(by_ticker.loc["2376.TW", "entry_bias"], "等待降溫")
        self.assertGreater(by_ticker.loc["3034.TW", "decision_priority"], by_ticker.loc["2376.TW", "decision_priority"])

    def test_entry_plan_notification_groups_biases(self) -> None:
        plan = pd.DataFrame(
            [
                {
                    "ticker": "3034.TW",
                    "name": "聯詠",
                    "decision_priority": 29.5,
                    "entry_bias": "分批試單",
                    "buy_zone_low": 426.14,
                    "buy_zone_high": 439.5,
                    "stop_loss": 406.5,
                },
                {
                    "ticker": "2376.TW",
                    "name": "技嘉",
                    "decision_priority": 2.5,
                    "entry_bias": "等待降溫",
                    "buy_zone_low": 275.2,
                    "buy_zone_high": 285.8,
                    "stop_loss": 260.75,
                },
            ]
        )

        message = quality_value.build_entry_plan_notification(plan)

        self.assertIn("🟢 聯詠(3034.TW)", message)
        self.assertIn("🔴 技嘉(2376.TW)", message)
        self.assertIn("買區 426.14–439.50", message)


if __name__ == "__main__":
    unittest.main()
