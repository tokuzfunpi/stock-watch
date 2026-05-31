from __future__ import annotations

import unittest

import pandas as pd

from stock_watch.strategy.heat_policy import build_market_heat_policy


class HeatPolicyTests(unittest.TestCase):
    def test_hot_trend_allows_small_open_not_chase_trial_for_normal_momentum(self) -> None:
        df_rank = pd.DataFrame(
            [
                {
                    "risk_score": 3,
                    "ret5_pct": 16.0,
                    "ret20_pct": 20.0,
                    "volume_ratio20": 1.8,
                    "signals": "TREND,ACCEL",
                    "spec_risk_label": "正常",
                    "volatility_tag": "活潑",
                },
                {
                    "risk_score": 3,
                    "ret5_pct": 11.0,
                    "ret20_pct": 18.0,
                    "volume_ratio20": 1.5,
                    "signals": "TREND",
                    "spec_risk_label": "正常",
                    "volatility_tag": "標準",
                },
            ]
        )

        policy = build_market_heat_policy(df_rank, {"label": "高檔震盪盤"})

        self.assertEqual(policy.state, "hot_trend")
        self.assertEqual(policy.market_heat, "hot")
        self.assertTrue(policy.allow_open_not_chase_trial)
        self.assertEqual(policy.open_not_chase_trial_cap, "<= 1/4 test position")

    def test_blowoff_blocks_open_not_chase_trial_when_heat_is_only_high_risk(self) -> None:
        rows = [
            {
                "risk_score": 7,
                "ret5_pct": 28.0,
                "ret20_pct": 45.0,
                "volume_ratio20": 2.5,
                "signals": "SURGE,TREND,ACCEL",
                "spec_risk_label": "疑似炒作風險高",
                "volatility_tag": "劇烈",
            }
            for _ in range(10)
        ]
        df_rank = pd.DataFrame(rows)

        policy = build_market_heat_policy(df_rank, {"label": "高檔震盪盤"})

        self.assertEqual(policy.state, "blowoff")
        self.assertEqual(policy.market_heat, "hot")
        self.assertFalse(policy.allow_open_not_chase_trial)
        self.assertEqual(policy.open_not_chase_trial_cap, "0%")

    def test_correction_blocks_participation_even_with_strong_rows(self) -> None:
        df_rank = pd.DataFrame(
            [
                {
                    "risk_score": 3,
                    "ret5_pct": 16.0,
                    "ret20_pct": 20.0,
                    "volume_ratio20": 1.8,
                    "signals": "TREND,ACCEL",
                    "spec_risk_label": "正常",
                    "volatility_tag": "活潑",
                }
            ]
        )

        policy = build_market_heat_policy(df_rank, {"label": "明顯修正盤"})

        self.assertEqual(policy.state, "correction")
        self.assertFalse(policy.allow_open_not_chase_trial)
        self.assertEqual(policy.open_not_chase_trial_cap, "0%")


if __name__ == "__main__":
    unittest.main()
