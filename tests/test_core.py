from __future__ import annotations

import unittest

import pandas as pd

from daily_theme_watchlist_20d_v22 import (
    add_indicators,
    detect_row,
    grade_signal,
    select_push_candidates,
    split_message,
)


class DetectRowTests(unittest.TestCase):
    def test_detect_row_generates_expected_fields_for_accel_case(self) -> None:
        dates = pd.date_range("2025-01-01", periods=260, freq="B")
        closes = [100.0] * 249 + [100.0, 101.0, 102.0, 103.0, 105.0, 112.0, 113.0, 114.0, 115.0, 116.0, 117.0]
        volumes = [1000] * 255 + [2500, 2600, 2700, 2800, 2900]

        df = pd.DataFrame(
            {
                "Open": closes,
                "High": [c + 1 for c in closes],
                "Low": [c - 1 for c in closes],
                "Close": closes,
                "Volume": volumes,
            },
            index=dates,
        )

        out = detect_row(add_indicators(df), "TEST1.TW", "Accel Name", "theme")

        self.assertEqual(out["ticker"], "TEST1.TW")
        self.assertEqual(out["name"], "Accel Name")
        self.assertIn("ACCEL", out["signals"])
        self.assertGreater(out["setup_score"], 0)
        self.assertIn("date", out)


class GradeSignalTests(unittest.TestCase):
    def test_grade_signal_returns_a_for_strong_accel_setup(self) -> None:
        row = {
            "setup_score": 8,
            "risk_score": 3,
            "signals": "ACCEL",
            "ret5_pct": 9.0,
            "volume_ratio20": 1.4,
            "ret20_pct": 12.0,
        }

        self.assertEqual(grade_signal(row), "A")

    def test_grade_signal_returns_c_for_overheated_name(self) -> None:
        row = {
            "setup_score": 9,
            "risk_score": 6,
            "signals": "SURGE",
            "ret5_pct": 20.0,
            "volume_ratio20": 2.0,
            "ret20_pct": 35.0,
        }

        self.assertEqual(grade_signal(row), "C")


class SelectPushCandidatesTests(unittest.TestCase):
    def test_accel_signal_is_included_in_candidates(self) -> None:
        df = pd.DataFrame(
            [
                {
                    "rank": 5,
                    "ticker": "TEST1.TW",
                    "name": "Accel Name",
                    "group": "theme",
                    "grade": "B",
                    "setup_score": 6,
                    "risk_score": 3,
                    "ret5_pct": 6.0,
                    "ret10_pct": 13.0,
                    "ret20_pct": 4.0,
                    "volume_ratio20": 1.2,
                    "signals": "ACCEL",
                    "rank_change": 0,
                    "setup_change": 0,
                    "regime": "轉強速度有出來",
                    "date": "2026-04-14",
                    "close": 100.0,
                }
            ]
        )

        out = select_push_candidates(df)

        self.assertEqual(len(out), 1)
        self.assertEqual(out.iloc[0]["ticker"], "TEST1.TW")


class SplitMessageTests(unittest.TestCase):
    def test_split_message_respects_limit(self) -> None:
        message = "line1\nline2\nline3\nline4"

        parts = split_message(message, limit=12)

        self.assertGreater(len(parts), 1)
        for part in parts:
            self.assertLessEqual(len(part), 12)

    def test_split_message_keeps_short_message_intact(self) -> None:
        message = "short text"

        parts = split_message(message, limit=100)

        self.assertEqual(parts, [message])


if __name__ == "__main__":
    unittest.main()
