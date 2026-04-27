from __future__ import annotations

from datetime import datetime
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from verification.workflows.backfill_from_git import (
    append_snapshot_rows,
    build_market_regime_from_history,
    build_us_market_reference_from_histories,
    parse_git_log_dates,
)


class BackfillFromGitTests(unittest.TestCase):
    def _history_df(self, closes: list[float], volumes: list[float] | None = None) -> pd.DataFrame:
        idx = pd.date_range("2025-01-01", periods=len(closes), freq="B")
        vols = volumes or ([1000.0] * len(closes))
        return pd.DataFrame(
            {
                "Open": closes,
                "High": [c + 1 for c in closes],
                "Low": [c - 1 for c in closes],
                "Close": closes,
                "Volume": vols,
            },
            index=idx,
        )

    def test_parse_git_log_dates_parses_sha_and_date(self) -> None:
        text = "\n".join(
            [
                "acbe56600000000000000000000000000000000 2026-04-19",
                "db6307c00000000000000000000000000000000 2026-04-15",
                "badline",
                "",
            ]
        )
        items = parse_git_log_dates(text)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].signal_date, "2026-04-19")
        self.assertTrue(items[0].commit_sha.startswith("acbe566"))

    def test_append_snapshot_rows_keeps_scenario_label_column(self) -> None:
        forced = pd.DataFrame(
            [
                {
                    "scenario_label": "明顯修正盤",
                    "rank": 1,
                    "ticker": "TEST1.TW",
                    "name": "Test 1",
                    "grade": "A",
                    "setup_score": 7,
                    "risk_score": 2,
                    "ret5_pct": 5.0,
                    "ret20_pct": 10.0,
                    "volume_ratio20": 1.2,
                    "signals": "ACCEL",
                    "action": "等拉回",
                    "reco_status": "ok",
                }
            ]
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            snapshot_csv = Path(tmpdir) / "reco_snapshots.csv"
            with patch("verification.workflows.backfill_from_git.select_forced_recommendations", return_value=forced):
                rows = append_snapshot_rows(
                    forced,
                    generated_at=datetime(2026, 4, 22),
                    signal_date="2026-04-22",
                    source="git",
                    source_sha="abc123",
                    snapshot_csv=snapshot_csv,
                )

            self.assertEqual(rows, 2)
            out = pd.read_csv(snapshot_csv)
            self.assertIn("scenario_label", out.columns)
            self.assertTrue((out["scenario_label"] == "明顯修正盤").all())

    def test_build_market_regime_from_history_marks_bearish_when_below_ma(self) -> None:
        closes = [100.0] * 240 + [99.0, 98.0, 97.0, 96.0, 95.0, 94.0, 93.0, 92.0, 91.0, 90.0, 89.0, 88.0, 87.0, 86.0, 85.0, 84.0, 83.0, 82.0, 81.0, 80.0]
        df = self._history_df(closes)

        regime = build_market_regime_from_history(df)

        self.assertFalse(regime["is_bullish"])
        self.assertLess(regime["ret20_pct"], 0.0)

    def test_build_us_market_reference_from_histories_preserves_negative_tone(self) -> None:
        closes = [100.0] * 255 + [99.0, 98.0, 97.0, 96.0, 95.0]
        histories = {
            "^GSPC": self._history_df(closes),
            "^IXIC": self._history_df(closes),
            "SOXX": self._history_df(closes),
            "NVDA": self._history_df(closes),
        }

        us_market = build_us_market_reference_from_histories(histories)

        self.assertIn("美股昨晚偏弱", us_market["summary"])
