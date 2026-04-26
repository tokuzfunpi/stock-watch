from __future__ import annotations

import unittest

import pandas as pd

from stock_watch.signals.library import apply_signal_template_labels
from stock_watch.signals.library import match_signal_templates
from stock_watch.signals.library import parse_signal_tokens
from stock_watch.signals.library import summarize_signal_templates
from stock_watch.signals.library import template_labels


class SignalLibraryTests(unittest.TestCase):
    def test_parse_signal_tokens_normalizes_and_dedupes(self) -> None:
        self.assertEqual(parse_signal_tokens(" accel,trend,ACCEL "), ("ACCEL", "TREND"))

    def test_match_signal_templates_detects_momentum_leader(self) -> None:
        matches = match_signal_templates("ACCEL,TREND,SURGE")
        self.assertEqual(matches[0].key, "momentum_leader")
        self.assertEqual(template_labels("ACCEL,TREND,SURGE"), "Momentum Leader + Theme Heat")

    def test_apply_signal_template_labels_adds_general_default(self) -> None:
        df = pd.DataFrame([{"signals": "TREND"}, {"signals": "REBREAK,ACCEL"}])
        out = apply_signal_template_labels(df)
        self.assertEqual(out.loc[0, "signal_template"], "General")
        self.assertEqual(out.loc[1, "signal_template"], "Reclaim Breakout")

    def test_summarize_signal_templates_counts_labels(self) -> None:
        df = pd.DataFrame(
            [
                {"signals": "ACCEL,TREND"},
                {"signals": "SURGE"},
                {"signals": "SURGE"},
            ]
        )
        summary = summarize_signal_templates(df)
        self.assertEqual(summary["Theme Heat"], 2)
        self.assertEqual(summary["Momentum Leader"], 1)
