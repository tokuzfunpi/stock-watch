from __future__ import annotations

import unittest

from stock_watch.strategy.classification import (
    DEFAULT_THRESHOLDS,
    ClassificationThresholds,
    is_attack_event,
    is_steady_event,
)


def _legacy_is_steady(row: dict) -> bool:
    return row["setup_score"] >= 5 and row["risk_score"] <= 4


def _legacy_is_attack(row: dict) -> bool:
    return (
        row["ret5_pct"] > 8 and row["volume_ratio20"] > 1.3 and row["ret20_pct"] > 0
    ) or ("ACCEL" in row["signals"])


class ClassificationDefaultsTest(unittest.TestCase):
    def test_defaults_match_documented_values(self) -> None:
        t = DEFAULT_THRESHOLDS
        self.assertEqual(t.steady_min_setup_score, 5.0)
        self.assertEqual(t.steady_max_risk_score, 4.0)
        self.assertEqual(t.attack_min_ret5_pct, 8.0)
        self.assertEqual(t.attack_min_volume_ratio, 1.3)
        self.assertEqual(t.attack_min_ret20_pct, 0.0)
        self.assertEqual(t.attack_breakout_signals, ("ACCEL",))


class ClassificationParityTest(unittest.TestCase):
    """The new config-driven classifiers must reproduce the old hardcoded logic
    exactly when using the default thresholds."""

    def _rows(self):
        return [
            {"setup_score": 5, "risk_score": 4, "ret5_pct": 9.0, "volume_ratio20": 1.4, "ret20_pct": 1.0, "signals": "ACCEL,TREND"},
            {"setup_score": 4, "risk_score": 4, "ret5_pct": 8.0, "volume_ratio20": 1.3, "ret20_pct": 0.0, "signals": ""},
            {"setup_score": 7, "risk_score": 5, "ret5_pct": 8.1, "volume_ratio20": 1.31, "ret20_pct": 0.1, "signals": "TREND"},
            {"setup_score": 6, "risk_score": 3, "ret5_pct": -2.0, "volume_ratio20": 0.5, "ret20_pct": -5.0, "signals": "BASE"},
            {"setup_score": 5, "risk_score": 5, "ret5_pct": 20.0, "volume_ratio20": 2.0, "ret20_pct": 10.0, "signals": "SURGE"},
            {"setup_score": 2, "risk_score": 2, "ret5_pct": 0.0, "volume_ratio20": 0.0, "ret20_pct": 0.0, "signals": "ACCEL"},
        ]

    def test_steady_parity(self) -> None:
        for row in self._rows():
            self.assertEqual(
                is_steady_event(row), _legacy_is_steady(row), msg=f"steady mismatch: {row}"
            )

    def test_attack_parity(self) -> None:
        for row in self._rows():
            self.assertEqual(
                is_attack_event(row), _legacy_is_attack(row), msg=f"attack mismatch: {row}"
            )

    def test_steady_boundary(self) -> None:
        self.assertTrue(is_steady_event({"setup_score": 5, "risk_score": 4}))
        self.assertFalse(is_steady_event({"setup_score": 4, "risk_score": 4}))
        self.assertFalse(is_steady_event({"setup_score": 5, "risk_score": 5}))

    def test_attack_requires_strict_inequalities(self) -> None:
        # Exactly at the thresholds should NOT trigger (uses > not >=).
        self.assertFalse(
            is_attack_event(
                {"ret5_pct": 8.0, "volume_ratio20": 1.3, "ret20_pct": 0.0, "signals": ""}
            )
        )
        self.assertTrue(
            is_attack_event(
                {"ret5_pct": 8.1, "volume_ratio20": 1.31, "ret20_pct": 0.1, "signals": ""}
            )
        )

    def test_handles_missing_keys_gracefully(self) -> None:
        self.assertFalse(is_steady_event({}))
        self.assertFalse(is_attack_event({}))


class ClassificationConfigTest(unittest.TestCase):
    def test_from_mapping_overrides(self) -> None:
        t = ClassificationThresholds.from_mapping(
            {
                "steady_min_setup_score": 6,
                "attack_min_ret5_pct": 10,
                "attack_breakout_signals": ["ACCEL", "SURGE"],
            }
        )
        self.assertEqual(t.steady_min_setup_score, 6.0)
        self.assertEqual(t.attack_min_ret5_pct, 10.0)
        self.assertEqual(t.attack_breakout_signals, ("ACCEL", "SURGE"))
        # Untouched fields keep defaults.
        self.assertEqual(t.steady_max_risk_score, 4.0)

    def test_from_mapping_comma_string_signals(self) -> None:
        t = ClassificationThresholds.from_mapping({"attack_breakout_signals": "ACCEL, SURGE"})
        self.assertEqual(t.attack_breakout_signals, ("ACCEL", "SURGE"))

    def test_from_mapping_none_is_defaults(self) -> None:
        self.assertEqual(ClassificationThresholds.from_mapping(None), DEFAULT_THRESHOLDS)

    def test_custom_thresholds_change_outcome(self) -> None:
        strict = ClassificationThresholds(steady_min_setup_score=7)
        row = {"setup_score": 5, "risk_score": 4}
        self.assertTrue(is_steady_event(row))  # default
        self.assertFalse(is_steady_event(row, strict))  # custom


if __name__ == "__main__":
    unittest.main()
