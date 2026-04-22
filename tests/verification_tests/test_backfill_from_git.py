from __future__ import annotations

from datetime import datetime
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from verification.backfill_from_git import append_snapshot_rows, parse_git_log_dates


class BackfillFromGitTests(unittest.TestCase):
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
            with patch("verification.backfill_from_git.select_forced_recommendations", return_value=forced):
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
