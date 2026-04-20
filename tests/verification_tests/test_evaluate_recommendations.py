from __future__ import annotations

import unittest

import pandas as pd

from verification.evaluate_recommendations import compute_forward_return_pct


class EvaluateRecommendationsTests(unittest.TestCase):
    def test_compute_forward_return_pct_ok(self) -> None:
        s = pd.Series(
            [100.0, 110.0, 105.0],
            index=pd.to_datetime(["2026-04-17", "2026-04-18", "2026-04-21"]),
            name="Close",
        )
        ret_pct, out_close, status = compute_forward_return_pct(s, "2026-04-17", 1)
        self.assertEqual(status, "ok")
        self.assertAlmostEqual(out_close or 0.0, 110.0)
        self.assertAlmostEqual(ret_pct or 0.0, 10.0)

    def test_compute_forward_return_pct_missing_date(self) -> None:
        s = pd.Series(
            [100.0, 110.0],
            index=pd.to_datetime(["2026-04-18", "2026-04-21"]),
            name="Close",
        )
        ret_pct, out_close, status = compute_forward_return_pct(s, "2026-04-17", 1)
        self.assertIsNone(ret_pct)
        self.assertIsNone(out_close)
        self.assertEqual(status, "signal_date_missing")
