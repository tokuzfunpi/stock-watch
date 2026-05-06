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

            code = quality_value.main(["--rank-csv", str(rank_csv), "--outdir", str(outdir), "--no-fundamentals"])

            report = (outdir / "quality_value_report.md").read_text(encoding="utf-8")
            candidates = pd.read_csv(outdir / "quality_value_candidates.csv")

        self.assertEqual(code, 0)
        self.assertIn("英業達", report)
        self.assertIn("中砂", report)
        self.assertNotIn("| 3 | 9999.TW", report)
        self.assertEqual(set(candidates["bucket"]), {"low_price_health", "quality_value_research"})

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
                code = quality_value.main(["--rank-csv", str(rank_csv), "--outdir", str(outdir)])
            finally:
                quality_value.FinMindFundamentalProvider = original_provider

            report = (outdir / "quality_value_report.md").read_text(encoding="utf-8")
            candidates = pd.read_csv(outdir / "quality_value_candidates.csv")

        self.assertEqual(code, 0)
        self.assertIn("品質價值優先", report)
        self.assertEqual(candidates.iloc[0]["fundamental_action"], "品質價值優先")


if __name__ == "__main__":
    unittest.main()
