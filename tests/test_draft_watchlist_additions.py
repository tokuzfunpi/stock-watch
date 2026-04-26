from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from draft_watchlist_additions import build_addition_draft
from draft_watchlist_additions import load_existing_tickers
from draft_watchlist_additions import render_markdown


class DraftWatchlistAdditionsTests(unittest.TestCase):
    def test_load_existing_tickers_reads_watchlist(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "watchlist.csv"
            pd.DataFrame(
                [
                    {"ticker": "2330.TW", "name": "台積電"},
                    {"ticker": "3661.TW", "name": "世芯-KY"},
                ]
            ).to_csv(path, index=False)
            existing = load_existing_tickers(path)

        self.assertEqual(existing, {"2330.TW", "3661.TW"})

    def test_build_addition_draft_splits_sections_and_dedupes(self) -> None:
        scored = pd.DataFrame(
            [
                {"ticker": "6190.TWO", "name": "萬泰科", "scan_group": "satellite", "candidate_source": "Satellite high-beta leaders", "setup_score": 12, "risk_score": 5, "spec_risk_score": 8, "ret20_pct": 35.7, "ret5_pct": 18.8, "volume_ratio20": 2.34, "spec_risk_label": "疑似炒作風險高", "signals": "SURGE,TREND,ACCEL", "regime": "有點過熱，別硬追", "quote_price": 44.0, "quote_volume": 10000},
                {"ticker": "6182.TWO", "name": "合晶", "scan_group": "theme", "candidate_source": "Theme trend acceleration", "setup_score": 14, "risk_score": 2, "spec_risk_score": 3, "ret20_pct": 17.6, "ret5_pct": 13.7, "volume_ratio20": 1.97, "spec_risk_label": "投機偏高", "signals": "TREND,ACCEL", "regime": "轉強速度有出來", "quote_price": 35.0, "quote_volume": 9000},
                {"ticker": "4927.TW", "name": "泰鼎-KY", "scan_group": "core", "candidate_source": "Core trend compounders", "setup_score": 10, "risk_score": 3, "spec_risk_score": 2, "ret20_pct": 24.9, "ret5_pct": 6.4, "volume_ratio20": 1.89, "spec_risk_label": "正常", "signals": "SURGE,TREND,ACCEL", "regime": "題材正在發酵", "quote_price": 60.0, "quote_volume": 8000},
                {"ticker": "2340.TW", "name": "台亞", "scan_group": "theme", "candidate_source": "Theme momentum burst", "setup_score": 15, "risk_score": 5, "spec_risk_score": 7, "ret20_pct": 30.2, "ret5_pct": 14.1, "volume_ratio20": 1.88, "spec_risk_label": "疑似炒作風險高", "signals": "SURGE,TREND,ACCEL", "regime": "題材正在發酵", "quote_price": 38.0, "quote_volume": 7000},
            ]
        )

        draft = build_addition_draft(scored)

        self.assertEqual(draft["satellite_add"][0]["ticker"], "6190.TWO")
        self.assertEqual(draft["theme_add"][0]["ticker"], "6182.TWO")
        self.assertEqual(draft["core_add"][0]["ticker"], "4927.TW")
        self.assertEqual(draft["theme_reserve"][0]["proposal_status"], "reserve")

    def test_render_markdown_includes_sections(self) -> None:
        payload = {
            "generated_at": "2026-04-26 00:00:00 CST",
            "summary": {"existing_watchlist_count": 48, "candidate_quote_count": 60, "scored_rows": 180},
            "sections": {
                "satellite_add": [{"ticker": "6190.TWO", "name": "萬泰科"}],
                "theme_add": [{"ticker": "6182.TWO", "name": "合晶"}],
                "core_add": [{"ticker": "4927.TW", "name": "泰鼎-KY"}],
                "theme_reserve": [{"ticker": "2340.TW", "name": "台亞"}],
            },
        }

        markdown = render_markdown(payload)

        self.assertIn("# Watchlist Addition Draft", markdown)
        self.assertIn("## Proposed Satellite Adds", markdown)
        self.assertIn("6190.TWO", markdown)
        self.assertIn("## Theme Reserve Only", markdown)


if __name__ == "__main__":
    unittest.main()
