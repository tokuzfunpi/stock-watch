from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from stock_watch.cli.local_daily import build_verification_argv
from stock_watch.cli.local_daily import build_action_summary_notification
from stock_watch.cli.local_daily import build_simple_action_summary_notification
from stock_watch.cli.local_daily import build_shadow_open_not_chase_tracking_df
from stock_watch.cli.local_daily import collect_status_metrics
from stock_watch.cli.local_daily import configure_local_telegram_chat_ids
from stock_watch.cli.local_daily import default_local_telegram_chat_ids
from stock_watch.cli.local_daily import main
from stock_watch.cli.local_daily import parse_local_telegram_chat_ids
from stock_watch.cli.local_daily import parse_args
from stock_watch.cli.local_daily import send_quality_value_notification
from stock_watch.cli.local_daily import should_run_step
from stock_watch.cli.local_daily import update_quality_value_tracking
from stock_watch.cli.local_daily import _collect_new_additions_action_summary
from stock_watch.cli.local_daily import _collect_high_risk_reward_action_summary
from stock_watch.cli.local_daily import _collect_watchlist_action_summary
from stock_watch.cli.local_daily import _build_lucky_pick_line
from stock_watch.cli.local_daily import LUCKY_PICK_TAGLINES
from stock_watch.cli.local_daily import _merge_action_summary_metrics
from stock_watch.cli.local_daily import write_quality_value_new_additions_tracking
from stock_watch.cli.local_daily import write_quality_value_trial_ledger
from stock_watch.cli.local_daily import write_shadow_open_not_chase_tracking_outputs
from stock_watch.cli.local_daily import write_local_status_dashboard


class RunLocalDailyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._quality_value_patch = patch("stock_watch.cli.local_daily.quality_value.main", return_value=0)
        self._quality_value_patch.start()
        self.addCleanup(self._quality_value_patch.stop)
        self._quality_value_tracking_patch = patch("stock_watch.cli.local_daily.update_quality_value_tracking")
        self._quality_value_tracking_patch.start()
        self.addCleanup(self._quality_value_tracking_patch.stop)
        self._quality_value_notification_patch = patch("stock_watch.cli.local_daily.send_quality_value_notification")
        self._quality_value_notification_patch.start()
        self.addCleanup(self._quality_value_notification_patch.stop)

    def test_parse_args_defaults_to_full_mode(self) -> None:
        with patch.dict("os.environ", {"STOCK_WATCH_LOCAL_TELEGRAM_CHAT_IDS": "", "TELEGRAM_CHAT_IDS": ""}, clear=False):
            args = parse_args([])
        self.assertEqual(args.mode, "full")
        self.assertEqual(args.local_telegram_chat_ids, "")

    def test_default_local_telegram_chat_ids_falls_back_to_telegram_secret(self) -> None:
        with patch.dict("os.environ", {"TELEGRAM_CHAT_IDS": "8496266754,8723698446"}, clear=False):
            os.environ.pop("STOCK_WATCH_LOCAL_TELEGRAM_CHAT_IDS", None)

            self.assertEqual(default_local_telegram_chat_ids(), "8496266754,8723698446")

    def test_default_local_telegram_chat_ids_uses_safe_single_chat_when_unset(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(default_local_telegram_chat_ids(), "7758949915")

    def test_parse_local_telegram_chat_ids_supports_commas_and_newlines(self) -> None:
        self.assertEqual(parse_local_telegram_chat_ids("7758949915,123\n-1001"), [7758949915, 123, -1001])

    def test_parse_local_telegram_chat_ids_deduplicates_recipients(self) -> None:
        self.assertEqual(parse_local_telegram_chat_ids("7758949915,123,7758949915\n123"), [7758949915, 123])

    def test_configure_local_telegram_chat_ids_overrides_daily_module(self) -> None:
        class FakeDailyModule:
            TELEGRAM_CHAT_IDS = [111, 222]

        chat_ids = configure_local_telegram_chat_ids("7758949915", FakeDailyModule)

        self.assertEqual(chat_ids, [7758949915])
        self.assertEqual(FakeDailyModule.TELEGRAM_CHAT_IDS, [7758949915])

    def test_build_action_summary_notification_renders_action_lines(self) -> None:
        message = build_action_summary_notification(
            {
                "market_context_lines": ["盤勢：高檔震盪盤｜邊做邊收", "重點：進場要更挑買點"],
                "lucky_pick_lines": ["星期三幸運籤抽到 台積電 (2330.TW)：一眼不看，心態自來。"],
                "action_trial_tickers": ["6161.TWO 捷波"],
                "action_pullback_tickers": ["3515.TW 華擎", "3213.TWO 茂訊 可試單"],
                "action_midlong_tickers": ["3014.TW 聯陽 中長線 續抱"],
                "action_wait_strength_tickers": ["3005.TW 神基"],
                "action_cooldown_tickers": ["2376.TW 技嘉"],
                "action_watch_tickers": ["3231.TW 緯創 短線備選 續追蹤"],
                "trial_ledger_action_tickers": ["3213.TWO 茂訊 active_trial/risk_watch 第一筆 1/3 可研究"],
                "portfolio_trim_tickers": ["英業達 (2356)"],
            }
        )

        self.assertIn("📌 今日可買名單", message)
        self.assertEqual(message.splitlines()[1], "星期三幸運籤抽到 台積電 (2330.TW)：一眼不看，心態自來。")
        self.assertNotIn("小彩蛋：", message)
        self.assertIn("盤勢：高檔震盪盤｜邊做邊收", message)
        self.assertIn("重點：進場要更挑買點", message)
        self.assertIn("原則：只列可買 / 可等價位買；買不買和何時買由你決定，逃跑價要先看。", message)
        self.assertIn("🟢 可小買：小買試水溫，不重壓\n• 捷波 (6161.TWO)", message)
        self.assertIn("🟡 等到價位再買：不要追高\n• 華擎 (3515.TW)\n• 茂訊 (3213.TWO) 可小買", message)
        self.assertIn("🧱 中長線可分批：波段倉\n• 聯陽 (3014.TW) 繼續看好", message)
        self.assertNotIn("備選觀察", message)
        self.assertNotIn("買後檢查", message)
        self.assertNotIn("等變強再買", message)
        self.assertNotIn("太熱別追", message)
        self.assertNotIn("新加入觀察", message)
        self.assertNotIn("短線備選", message)
        self.assertNotIn("中長線：", message)
        self.assertNotIn("active_trial", message)
        self.assertNotIn("risk_watch", message)
        self.assertNotIn("1/3", message)
        self.assertNotIn("英業達", message)
        self.assertNotIn("持股", message)
        self.assertNotIn("6161.TWO 捷波, 3515.TW 華擎", message)

    def test_build_simple_action_summary_notification_keeps_only_actionable_sections(self) -> None:
        message = build_simple_action_summary_notification(
            {
                "market_context_simple_lines": ["盤勢：高檔震盪盤｜邊做邊收", "重點：進場要更挑買點"],
                "lucky_pick_lines": ["星期三雷達嗶到 台積電 (2330.TW)：不是叫你衝，是叫你假裝很懂地觀察。"],
                "action_trial_tickers": [
                    "6161.TWO 捷波｜買區 42.92–44｜停損 40.85",
                    "4995.TWO 晶達｜買區 44.41–45.5｜停損 42.26",
                    "8261.TW 富鼎｜買區 120.72–128｜停損 110.21",
                    "3213.TWO 茂訊｜買區 111.92–114.5｜停損 106.42",
                ],
                "action_pullback_tickers": ["3515.TW 華擎｜買區 300–315｜停損 286"],
                "action_midlong_tickers": ["3014.TW 聯陽｜買 133–161｜逃 117.7"],
                "action_wait_strength_tickers": ["3005.TW 神基"],
                "action_cooldown_tickers": ["2376.TW 技嘉"],
                "trial_ledger_action_tickers": ["3213.TWO 茂訊 active_trial/risk_watch 第一筆 1/3 可研究"],
                "portfolio_trim_tickers": ["英業達 (2356)"],
            }
        )

        self.assertIn("📌 今日可買名單", message)
        self.assertEqual(message.splitlines()[1], "星期三雷達嗶到 台積電 (2330.TW)：不是叫你衝，是叫你假裝很懂地觀察。")
        self.assertIn("盤勢：高檔震盪盤｜邊做邊收", message)
        self.assertIn("原則：只列可買 / 可等價位買；買不買和何時買由你決定，逃跑價要先看。", message)
        self.assertIn("🟢 可小買：小買試水溫，不重壓\n• 捷波 (6161.TWO)｜買 42.92–44｜逃 40.85", message)
        self.assertIn("• 富鼎 (8261.TW)｜買 120.72–128｜逃 110.21", message)
        self.assertNotIn("可可買區", message)
        self.assertNotIn("茂訊 (3213.TWO)", message)
        self.assertIn("🟡 等到價位再買：不要追高\n• 華擎 (3515.TW)｜等買 300–315｜逃 286", message)
        self.assertNotIn("英業達", message)
        self.assertNotIn("持股", message)
        self.assertNotIn("新加入：", message)
        self.assertNotIn("等轉強", message)
        self.assertNotIn("過熱先等", message)
        self.assertNotIn("新加入觀察", message)
        self.assertNotIn("買後檢查", message)

    def test_build_action_summary_notification_marks_high_risk_reward_separately(self) -> None:
        message = build_action_summary_notification(
            {
                "action_trial_tickers": ["2881.TW 富邦金｜買 108–110｜逃 104"],
                "action_high_risk_reward_tickers": [
                    "2495.TW 普安｜高風險高報酬｜HRR 71.2｜自動試單候選｜5D 16.5%、20D 28.1%｜hot_trend｜≤1/5 HRR試單｜總HRR≤1單位；破前低或1ATR停損"
                ],
                "action_cooldown_tickers": ["2312.TW 金寶"],
            }
        )

        self.assertIn("🟢 可小買", message)
        self.assertIn("🔥 高風險小試 Top 5：自動評分，倉位更小", message)
        self.assertIn("普安 (2495.TW)｜高風險高報酬｜HRR 71.2", message)
        self.assertIn("自動試單候選", message)
        self.assertIn("≤1/5 HRR試單", message)
        self.assertNotIn("標準: 高投機風險 + 強動能報酬", message)
        self.assertIn("🟢 可小買：小買試水溫，不重壓\n• 富邦金 (2881.TW)", message)

    def test_collect_high_risk_reward_action_summary_reads_shadow_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            shadow_csv = Path(tmpdir) / "shadow_open_not_chase_candidates.csv"
            pd.DataFrame(
                [
                    {
                        "rank": 2,
                        "ticker": "2495.TW",
                        "name": "普安",
                        "shadow_target": "只觀察不追",
                        "spec_risk_bucket": "high",
                        "shadow_status": "decision_required",
                        "heat_policy_state": "hot_trend",
                        "setup_score": 15,
                        "risk_score": 4,
                        "volume_ratio20": 2.03,
                        "signals": "SURGE,TREND,ACCEL",
                        "ret5_pct": 16.52,
                        "ret20_pct": 28.11,
                    },
                    {
                        "rank": 13,
                        "ticker": "2881.TW",
                        "name": "富邦金",
                        "shadow_target": "開高不追",
                        "spec_risk_bucket": "normal",
                        "shadow_status": "eligible",
                        "heat_policy_state": "hot_trend",
                        "setup_score": 11,
                        "risk_score": 4,
                        "volume_ratio20": 2.75,
                        "signals": "SURGE,TREND,ACCEL",
                        "ret5_pct": 15.67,
                        "ret20_pct": 22.22,
                    },
                ]
            ).to_csv(shadow_csv, index=False)

            result = _collect_high_risk_reward_action_summary(shadow_csv)

        self.assertEqual(len(result["action_high_risk_reward_tickers"]), 1)
        self.assertIn("2495.TW 普安", result["action_high_risk_reward_tickers"][0])
        self.assertIn("高風險高報酬", result["action_high_risk_reward_tickers"][0])
        self.assertIn("HRR", result["action_high_risk_reward_tickers"][0])
        self.assertIn("自動試單候選", result["action_high_risk_reward_tickers"][0])
        self.assertIn("≤1/5 HRR試單", result["action_high_risk_reward_tickers"][0])

    def test_collect_high_risk_reward_action_summary_ranks_daily_top_five(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            shadow_csv = root / "shadow_open_not_chase_candidates.csv"
            daily_rank_csv = root / "daily_rank.csv"
            pd.DataFrame(
                [
                    {
                        "rank": idx,
                        "ticker": f"24{idx:02d}.TW",
                        "name": f"高風險{idx}",
                        "setup_score": 8 + idx,
                        "risk_score": 4 + idx,
                        "ret5_pct": 10 + idx,
                        "ret20_pct": 25 + (idx * 3),
                        "volume_ratio20": 1.2 + (idx / 10),
                        "signals": "SURGE,TREND,ACCEL",
                        "spec_risk_label": "疑似炒作風險高",
                        "spec_risk_score": 6,
                    }
                    for idx in range(1, 8)
                ]
            ).to_csv(daily_rank_csv, index=False)

            result = _collect_high_risk_reward_action_summary(shadow_csv, daily_rank_csv=daily_rank_csv)

        items = result["action_high_risk_reward_tickers"]
        self.assertEqual(len(items), 5)
        self.assertIn("2407.TW 高風險7", items[0])
        self.assertIn("2403.TW 高風險3", items[-1])
        self.assertTrue(all("HRR" in item and "自動試單候選" in item for item in items))

    def test_merge_prefers_high_risk_reward_over_generic_cooldown_for_same_ticker(self) -> None:
        merged = _merge_action_summary_metrics(
            {"action_cooldown_tickers": ["2495.TW 普安｜別追，等 40–42"]},
            {"action_high_risk_reward_tickers": ["2495.TW 普安｜高風險高報酬｜自動試單候選"]},
        )

        self.assertEqual(merged["action_cooldown_tickers"], [])
        self.assertEqual(len(merged["action_high_risk_reward_tickers"]), 1)
        self.assertIn("高風險高報酬", merged["action_high_risk_reward_tickers"][0])

    def test_quality_value_notification_does_not_send_portfolio_only_actions(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            portfolio_report = root / "portfolio_report.md"
            portfolio_report.write_text(
                "- 英業達 (2356) | 進攻持股 | 建議 分批落袋 | 價格帶 賣≥49.95 / 逃 46.45\n",
                encoding="utf-8",
            )

            with patch("daily_theme_watchlist.send_telegram_message") as send_mock:
                send_quality_value_notification(
                    entry_plan_csv=root / "missing_entry_plan.csv",
                    portfolio_report_md=portfolio_report,
                    new_additions_tracking_csv=root / "missing_new_additions.csv",
                    trial_ledger_csv=root / "missing_trial_ledger.csv",
                )

        send_mock.assert_not_called()

    def test_should_run_step_uses_mode_defaults_and_skip_overrides(self) -> None:
        preopen_args = parse_args(["--mode", "preopen"])
        self.assertTrue(should_run_step(preopen_args, "watchlist"))
        self.assertTrue(should_run_step(preopen_args, "verification"))
        self.assertFalse(should_run_step(preopen_args, "portfolio"))

        postclose_args = parse_args(["--mode", "postclose", "--skip-portfolio"])
        self.assertTrue(should_run_step(postclose_args, "watchlist"))
        self.assertTrue(should_run_step(postclose_args, "verification"))
        self.assertFalse(should_run_step(postclose_args, "portfolio"))

    def test_build_verification_argv_maps_local_mode(self) -> None:
        args = parse_args(
            [
                "--mode",
                "postclose",
                "--horizons",
                "1,5",
                "--weights",
                "70:30,60:40",
                "--all-dates",
            ]
        )

        argv = build_verification_argv(args)

        self.assertIn("--mode", argv)
        self.assertIn("postclose", argv)
        self.assertIn("1,5", argv)
        self.assertIn("70:30,60:40", argv)
        self.assertIn("--all-dates", argv)

    def test_collect_status_metrics_reads_latest_signal_dates_and_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            theme_outdir = Path(tmpdir) / "theme_watchlist_daily"
            verification_outdir = Path(tmpdir) / "verification" / "watchlist_daily"
            theme_outdir.mkdir(parents=True, exist_ok=True)
            verification_outdir.mkdir(parents=True, exist_ok=True)

            pd.DataFrame(
                [
                    {"ticker": "2330.TW", "spec_risk_score": 0, "spec_risk_label": "正常", "rank": 2},
                    {"ticker": "3057.TW", "spec_risk_score": 8, "spec_risk_label": "疑似炒作風險高", "rank": 1},
                    {"ticker": "6669.TW", "spec_risk_score": 4, "spec_risk_label": "投機偏高", "rank": 3},
                ]
            ).to_csv(theme_outdir / "daily_rank.csv", index=False)
            pd.DataFrame(
                [
                    {"signal_date": "2026-04-22", "watch_type": "short", "ticker": "2330.TW"},
                    {"signal_date": "2026-04-23", "watch_type": "midlong", "ticker": "2317.TW"},
                ]
            ).to_csv(verification_outdir / "reco_snapshots.csv", index=False)
            (theme_outdir / "daily_report.md").write_text("# report\n", encoding="utf-8")
            pd.DataFrame(
                [
                    {"signal_date": "2026-04-22", "horizon_days": 1, "watch_type": "short", "ticker": "2330.TW", "status": "ok"},
                    {
                        "signal_date": "2026-04-23",
                        "horizon_days": 5,
                        "watch_type": "midlong",
                        "ticker": "2317.TW",
                        "status": "insufficient_forward_data",
                    },
                ]
            ).to_csv(verification_outdir / "reco_outcomes.csv", index=False)
            (theme_outdir / "runtime_metrics.json").write_text(
                json.dumps({"status": "ok", "wall_seconds": 1.234}),
                encoding="utf-8",
            )
            (theme_outdir / "portfolio_runtime_metrics.json").write_text(
                json.dumps({"status": "ok", "wall_seconds": 0.456}),
                encoding="utf-8",
            )
            (theme_outdir / "portfolio_report.md").write_text(
                "- 英業達 (2356) | 進攻持股 | 建議 分批落袋 | 價格帶 買≤47.95\n",
                encoding="utf-8",
            )
            pd.DataFrame(
                [
                    {"ticker": "6161.TWO", "name": "捷波", "decision_priority": 34, "entry_bias": "分批試單", "buy_zone_low": 42.92, "buy_zone_high": 44.0, "stop_loss": 40.85},
                    {"ticker": "4966.TWO", "name": "譜瑞-KY", "decision_priority": 25, "entry_bias": "等拉回", "buy_zone_low": 453.0, "buy_zone_high": 468.5, "stop_loss": 431.0},
                    {"ticker": "3005.TW", "name": "神基", "decision_priority": 23, "entry_bias": "等轉強", "buy_zone_low": 121.5, "buy_zone_high": 126.0, "stop_loss": 115.0},
                    {"ticker": "6525.TW", "name": "捷敏-KY", "decision_priority": -6, "entry_bias": "等待降溫", "buy_zone_low": 101.0, "buy_zone_high": 105.0, "stop_loss": 96.0},
                ]
            ).to_csv(theme_outdir / "quality_value_entry_plan.csv", index=False)
            (verification_outdir / "runtime_metrics.json").write_text(
                json.dumps({"status": "ok", "wall_seconds": 2.5}),
                encoding="utf-8",
            )

            metrics = collect_status_metrics(theme_outdir, verification_outdir)

        self.assertEqual(metrics["latest_snapshot_signal_date"], "2026-04-23")
        self.assertEqual(metrics["latest_outcome_signal_date"], "2026-04-23")
        self.assertEqual(metrics["daily_rank_rows"], 3)
        self.assertEqual(metrics["snapshot_rows"], 2)
        self.assertEqual(metrics["outcome_rows"], 2)
        self.assertEqual(metrics["outcome_ok_rows"], 1)
        self.assertEqual(metrics["outcome_pending_rows"], 1)
        self.assertEqual(metrics["verification_gate_status"], "ok")
        self.assertEqual(metrics["snapshot_dup_keys"], 0)
        self.assertEqual(metrics["outcome_dup_keys"], 0)
        self.assertEqual(metrics["signal_date_missing_rows"], 0)
        self.assertEqual(metrics["no_price_series_rows"], 0)
        self.assertEqual(metrics["watchlist_runtime_status"], "ok")
        self.assertEqual(metrics["portfolio_runtime_status"], "ok")
        self.assertEqual(metrics["verification_runtime_status"], "ok")
        self.assertAlmostEqual(metrics["watchlist_runtime_seconds"], 1.234)
        self.assertAlmostEqual(metrics["portfolio_runtime_seconds"], 0.456)
        self.assertAlmostEqual(metrics["verification_runtime_seconds"], 2.5)
        self.assertEqual(metrics["spec_risk_high_rows"], 1)
        self.assertEqual(metrics["spec_risk_watch_rows"], 1)
        self.assertEqual(metrics["spec_risk_top_tickers"], ["3057.TW", "6669.TW"])
        self.assertEqual(metrics["watchlist_artifact_freshness_status"], "current")
        self.assertEqual(metrics["action_trial_tickers"], ["捷波 (6161.TWO)｜買 42.92–44｜逃 40.85"])
        self.assertEqual(metrics["action_pullback_tickers"], ["譜瑞-KY (4966.TWO)｜等買 453–468.5｜逃 431"])
        self.assertEqual(metrics["action_low_liquidity_tickers"], [])
        self.assertEqual(metrics["action_wait_strength_tickers"], ["神基 (3005.TW)｜等強再買 121.5–126｜逃 115"])
        self.assertEqual(metrics["action_cooldown_tickers"], ["捷敏-KY (6525.TW)｜別追，等 101–105｜逃 96"])
        self.assertEqual(metrics["portfolio_trim_tickers"], ["英業達 (2356)"])

    def test_collect_status_metrics_moves_low_liquidity_items_to_hold_bucket(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            theme_outdir = Path(tmpdir) / "theme_watchlist_daily"
            verification_outdir = Path(tmpdir) / "verification" / "watchlist_daily"
            theme_outdir.mkdir(parents=True, exist_ok=True)
            verification_outdir.mkdir(parents=True, exist_ok=True)

            pd.DataFrame(
                [
                    {"ticker": "2330.TW", "spec_risk_score": 0, "spec_risk_label": "正常", "rank": 1, "close": 100.0, "avg_vol20": 10_000_000},
                    {"ticker": "3005.TW", "spec_risk_score": 0, "spec_risk_label": "正常", "rank": 2, "close": 100.0, "avg_vol20": 100_000},
                    {"ticker": "6161.TWO", "spec_risk_score": 0, "spec_risk_label": "正常", "rank": 3, "close": 100.0, "avg_vol20": 10_000_000},
                ]
            ).to_csv(theme_outdir / "daily_rank.csv", index=False)
            (theme_outdir / "daily_report.md").write_text("# report\n", encoding="utf-8")
            (theme_outdir / "runtime_metrics.json").write_text(
                json.dumps({"status": "ok", "wall_seconds": 1.234}),
                encoding="utf-8",
            )

            pd.DataFrame([{"signal_date": "2026-04-23", "watch_type": "short", "ticker": "2330.TW"}]).to_csv(
                verification_outdir / "reco_snapshots.csv", index=False
            )
            pd.DataFrame(
                [
                    {
                        "signal_date": "2026-04-23",
                        "horizon_days": 1,
                        "watch_type": "short",
                        "ticker": "2330.TW",
                        "status": "ok",
                    }
                ]
            ).to_csv(verification_outdir / "reco_outcomes.csv", index=False)
            (verification_outdir / "runtime_metrics.json").write_text(
                json.dumps({"status": "ok", "wall_seconds": 2.5}),
                encoding="utf-8",
            )

            pd.DataFrame(
                [
                    {
                        "ticker": "3005.TW",
                        "name": "神基",
                        "decision_priority": 23,
                        "entry_bias": "等轉強",
                        "buy_zone_low": 121.5,
                        "buy_zone_high": 126.0,
                        "stop_loss": 115.0,
                    },
                    {
                        "ticker": "6161.TWO",
                        "name": "捷波",
                        "decision_priority": 30,
                        "entry_bias": "研究試單",
                        "buy_zone_low": 42.92,
                        "buy_zone_high": 44.0,
                        "stop_loss": 40.85,
                    },
                ]
            ).to_csv(theme_outdir / "quality_value_entry_plan.csv", index=False)
            pd.DataFrame(
                [
                    {"ticker": "3005.TW", "name": "神基", "volume_ratio20": 0.8},
                    {"ticker": "6161.TWO", "name": "捷波", "volume_ratio20": 1.2},
                ]
            ).to_csv(theme_outdir / "quality_value_candidates.csv", index=False)

            metrics = collect_status_metrics(theme_outdir, verification_outdir)

        self.assertEqual(metrics["action_trial_tickers"], ["捷波 (6161.TWO)｜買 42.92–44｜逃 40.85"])
        self.assertEqual(metrics["action_wait_strength_tickers"], [])
        self.assertTrue(metrics["action_low_liquidity_tickers"])
        self.assertIn("神基 (3005.TW)", metrics["action_low_liquidity_tickers"][0])
        self.assertIn("流動性低", metrics["action_low_liquidity_tickers"][0])
        self.assertIn("量縮", metrics["action_low_liquidity_tickers"][0])

    def test_collect_status_metrics_tag_only_policy_keeps_items_in_buckets_with_notes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            theme_outdir = Path(tmpdir) / "theme_watchlist_daily"
            verification_outdir = Path(tmpdir) / "verification" / "watchlist_daily"
            theme_outdir.mkdir(parents=True, exist_ok=True)
            verification_outdir.mkdir(parents=True, exist_ok=True)

            pd.DataFrame(
                [
                    {"ticker": "2330.TW", "spec_risk_score": 0, "spec_risk_label": "正常", "rank": 1, "close": 100.0, "avg_vol20": 10_000_000},
                    {"ticker": "3005.TW", "spec_risk_score": 0, "spec_risk_label": "正常", "rank": 2, "close": 100.0, "avg_vol20": 100_000},
                ]
            ).to_csv(theme_outdir / "daily_rank.csv", index=False)
            (theme_outdir / "daily_report.md").write_text("# report\n", encoding="utf-8")
            (theme_outdir / "runtime_metrics.json").write_text(
                json.dumps({"status": "ok", "wall_seconds": 1.234}),
                encoding="utf-8",
            )
            pd.DataFrame([{"signal_date": "2026-04-23", "watch_type": "short", "ticker": "2330.TW"}]).to_csv(
                verification_outdir / "reco_snapshots.csv", index=False
            )
            pd.DataFrame(
                [
                    {
                        "signal_date": "2026-04-23",
                        "horizon_days": 1,
                        "watch_type": "short",
                        "ticker": "2330.TW",
                        "status": "ok",
                    }
                ]
            ).to_csv(verification_outdir / "reco_outcomes.csv", index=False)
            (verification_outdir / "runtime_metrics.json").write_text(
                json.dumps({"status": "ok", "wall_seconds": 2.5}),
                encoding="utf-8",
            )
            pd.DataFrame(
                [
                    {
                        "ticker": "3005.TW",
                        "name": "神基",
                        "decision_priority": 23,
                        "entry_bias": "等轉強",
                        "buy_zone_low": 121.5,
                        "buy_zone_high": 126.0,
                        "stop_loss": 115.0,
                    }
                ]
            ).to_csv(theme_outdir / "quality_value_entry_plan.csv", index=False)
            pd.DataFrame([{"ticker": "3005.TW", "name": "神基", "volume_ratio20": 1.2}]).to_csv(
                theme_outdir / "quality_value_candidates.csv", index=False
            )

            with patch.dict(os.environ, {"STOCK_WATCH_LIQUIDITY_POLICY": "tag_only"}, clear=False):
                metrics = collect_status_metrics(theme_outdir, verification_outdir)

        self.assertTrue(metrics["action_wait_strength_tickers"])
        self.assertIn("神基 (3005.TW)", metrics["action_wait_strength_tickers"][0])
        self.assertIn("流動性低", metrics["action_wait_strength_tickers"][0])
        self.assertEqual(metrics["action_low_liquidity_tickers"], [])

    def test_update_quality_value_tracking_writes_lifecycle_and_review_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            theme_outdir = Path(tmpdir) / "theme_watchlist_daily"
            theme_outdir.mkdir(parents=True, exist_ok=True)
            daily_rank_csv = theme_outdir / "daily_rank.csv"
            entry_plan_csv = theme_outdir / "quality_value_entry_plan.csv"
            draft_csv = theme_outdir / "quality_value_watchlist_draft.csv"
            tracking_csv = theme_outdir / "quality_value_tracking.csv"
            pruning_md = theme_outdir / "quality_value_pruning_report.md"
            review_csv = theme_outdir / "quality_value_candidate_review.csv"
            review_md = theme_outdir / "quality_value_candidate_review.md"

            pd.DataFrame(
                [
                    {
                        "date": "2026-05-07",
                        "ticker": "6161.TWO",
                        "name": "捷波",
                        "layer": "quality_value",
                        "rank": 1,
                        "close": 44.0,
                        "ret5_pct": 6.5,
                        "ret20_pct": 9.0,
                        "volume_ratio20": 1.7,
                        "setup_score": 12,
                        "risk_score": 0,
                        "spec_risk_label": "正常",
                    },
                    {
                        "date": "2026-05-07",
                        "ticker": "6525.TW",
                        "name": "捷敏-KY",
                        "layer": "quality_value",
                        "rank": 2,
                        "close": 109.5,
                        "ret5_pct": 20.0,
                        "ret20_pct": 31.0,
                        "volume_ratio20": 3.8,
                        "setup_score": 12,
                        "risk_score": 9,
                        "spec_risk_label": "疑似炒作風險高",
                    },
                    {
                        "date": "2026-05-07",
                        "ticker": "5288.TWO",
                        "name": "豐祥-KY",
                        "layer": "quality_value",
                        "rank": 3,
                        "close": 120.0,
                        "ret5_pct": -1.0,
                        "ret20_pct": 0.5,
                        "volume_ratio20": 0.8,
                        "setup_score": 4,
                        "risk_score": 1,
                        "spec_risk_label": "正常",
                    },
                ]
            ).to_csv(daily_rank_csv, index=False)
            pd.DataFrame(
                [
                    {"ticker": "6161.TWO", "entry_bias": "分批試單", "decision_priority": 34, "buy_zone_low": 42.9, "buy_zone_high": 44.0, "stop_loss": 40.8},
                    {"ticker": "6525.TW", "entry_bias": "等待降溫", "decision_priority": -6, "buy_zone_low": 90.0, "buy_zone_high": 95.0, "stop_loss": 85.0},
                    {"ticker": "5288.TWO", "entry_bias": "暫不急", "decision_priority": 1, "buy_zone_low": 110.0, "buy_zone_high": 115.0, "stop_loss": 105.0},
                ]
            ).to_csv(entry_plan_csv, index=False)
            pd.DataFrame(
                [
                    {"ticker": "3213.TWO", "name": "茂訊", "radar_priority": "A加入觀察", "similar_score": 19.54},
                    {"ticker": "2414.TW", "name": "精技", "radar_priority": "B研究追蹤", "similar_score": 15.10},
                ]
            ).to_csv(draft_csv, index=False)
            pd.DataFrame([{"ticker": "5288.TWO", "first_seen_date": "2026-05-01"}]).to_csv(tracking_csv, index=False)

            tracking = update_quality_value_tracking(
                daily_rank_csv=daily_rank_csv,
                entry_plan_csv=entry_plan_csv,
                draft_csv=draft_csv,
                tracking_csv=tracking_csv,
                pruning_md=pruning_md,
                candidate_review_csv=review_csv,
                candidate_review_md=review_md,
            )
            review = pd.read_csv(review_csv)
            pruning_exists = pruning_md.exists()
            review_md_exists = review_md.exists()

            action_by_ticker = dict(zip(tracking["ticker"], tracking["lifecycle_action"]))

        self.assertEqual(action_by_ticker["6161.TWO"], "promote")
        self.assertEqual(action_by_ticker["6525.TW"], "cooldown")
        self.assertEqual(action_by_ticker["5288.TWO"], "drop_review")
        self.assertTrue(pruning_exists)
        self.assertTrue(review_md_exists)
        self.assertIn("needs_decision_add_watchlist", review["review_action"].tolist())

    def test_write_quality_value_new_additions_tracking_marks_actions(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tracking_csv = Path(tmpdir) / "quality_value_new_additions_tracking.csv"
            tracking_md = Path(tmpdir) / "quality_value_new_additions_tracking.md"
            tracking = pd.DataFrame(
                [
                    {
                        "ticker": "3213.TWO",
                        "name": "茂訊",
                        "last_seen_date": "2026-05-07",
                        "rank": 11,
                        "close": 114.5,
                        "ret5_pct": 8.53,
                        "ret10_pct": 6.51,
                        "ret20_pct": 9.57,
                        "volume_ratio20": 1.2,
                        "setup_score": 12,
                        "risk_score": 2,
                        "spec_risk_label": "正常",
                        "entry_bias": "分批試單",
                        "buy_zone_low": 111.92,
                        "buy_zone_high": 114.50,
                        "stop_loss": 106.42,
                    },
                    {
                        "ticker": "6292.TWO",
                        "name": "迅德",
                        "last_seen_date": "2026-05-07",
                        "rank": 3,
                        "close": 50.8,
                        "ret5_pct": 11.04,
                        "ret10_pct": 2.63,
                        "ret20_pct": 12.89,
                        "volume_ratio20": 3.09,
                        "setup_score": 14,
                        "risk_score": 4,
                        "spec_risk_label": "正常",
                        "entry_bias": "等拉回",
                        "buy_zone_low": 47.90,
                        "buy_zone_high": 49.83,
                        "stop_loss": 44.27,
                    },
                ]
            )

            result = write_quality_value_new_additions_tracking(
                tracking,
                tracking_csv=tracking_csv,
                tracking_md=tracking_md,
                new_addition_tickers=("3213.TWO", "6292.TWO"),
            )

            action_by_ticker = dict(zip(result["ticker"], result["next_action"]))
            tracking_csv_exists = tracking_csv.exists()
            tracking_md_exists = tracking_md.exists()

        self.assertEqual(action_by_ticker["3213.TWO"], "可試單")
        self.assertEqual(action_by_ticker["6292.TWO"], "等拉回")
        self.assertTrue(tracking_csv_exists)
        self.assertTrue(tracking_md_exists)

    def test_collect_new_additions_action_summary_merges_into_action_buckets(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tracking_csv = Path(tmpdir) / "quality_value_new_additions_tracking.csv"
            pd.DataFrame(
                [
                    {
                        "ticker": "3213.TWO",
                        "name": "茂訊",
                        "rank": 11,
                        "close": 114.5,
                        "next_action": "可試單",
                        "buy_zone_low": 111.92,
                        "buy_zone_high": 114.50,
                        "stop_loss": 106.42,
                    },
                    {
                        "ticker": "6292.TWO",
                        "name": "迅德",
                        "rank": 3,
                        "close": 57.0,
                        "next_action": "先不追",
                        "buy_zone_low": 48.31,
                        "buy_zone_high": 50.94,
                        "stop_loss": 45.05,
                    },
                ]
            ).to_csv(tracking_csv, index=False)

            result = _collect_new_additions_action_summary(tracking_csv)

        self.assertEqual(result["action_pullback_tickers"], [])
        self.assertIn("茂訊", result["action_trial_tickers"][0])
        self.assertIn("新加入：可小買", result["action_trial_tickers"][0])
        self.assertIn("迅德", result["action_cooldown_tickers"][0])
        self.assertIn("新加入：先不追", result["action_cooldown_tickers"][0])

    def test_merge_action_summary_metrics_keeps_existing_bucket_and_adds_new_addition_note(self) -> None:
        result = _merge_action_summary_metrics(
            {
                "action_low_liquidity_tickers": [
                    "3158.TWO 嘉實｜等量再說 89.82–90.38｜逃 87.5｜流動性低 to20=1.6M"
                ],
                "action_wait_strength_tickers": [],
            },
            {
                "action_wait_strength_tickers": [
                    "3158.TWO 嘉實｜現價 88.3｜等強再買 89.82–90.38｜逃 87.5｜新加入：等變強再買"
                ],
            },
        )

        self.assertEqual(result["action_wait_strength_tickers"], [])
        self.assertIn("流動性低", result["action_low_liquidity_tickers"][0])
        self.assertIn("新加入：等變強再買", result["action_low_liquidity_tickers"][0])

    def test_merge_action_summary_metrics_uses_weights_for_final_bucket(self) -> None:
        result = _merge_action_summary_metrics(
            {
                "action_trial_tickers": ["5299.TWO 杰力｜買 88–92｜逃 83"],
                "action_cooldown_tickers": [],
            },
            {
                "action_cooldown_tickers": ["5299.TWO 杰力｜別追，等 88–92｜逃 83｜短線：太熱別追"],
            },
        )

        self.assertEqual(result["action_trial_tickers"], [])
        self.assertIn("杰力", result["action_cooldown_tickers"][0])
        self.assertIn("短線：太熱別追", result["action_cooldown_tickers"][0])

    def test_collect_watchlist_action_summary_maps_short_and_midlong_into_shared_buckets(self) -> None:
        short = pd.DataFrame([{"ticker": "5347.TWO", "name": "世界"}])
        midlong = pd.DataFrame([{"ticker": "3014.TW", "name": "聯陽"}])
        short_backup = pd.DataFrame([{"ticker": "3231.TW", "name": "緯創"}])
        midlong_backup = pd.DataFrame([{"ticker": "3711.TW", "name": "日月光投控"}])

        class FakeDailyModule:
            @staticmethod
            def build_candidate_sets(df_rank, market_regime, us_market):
                return short, short_backup, midlong, midlong_backup

            @staticmethod
            def short_term_action_label(row):
                return "等拉回" if row["ticker"] == "5347.TWO" else "續追蹤"

            @staticmethod
            def midlong_action_label(row):
                return "續抱" if row["ticker"] == "3014.TW" else "分批落袋"

            @staticmethod
            def watch_price_plan_text(row, watch_type, **kwargs):
                return "買 10 / 賣 12 / 逃 9"

        result = _collect_watchlist_action_summary(
            pd.DataFrame([{"ticker": "seed"}]),
            {},
            {},
            daily_module=FakeDailyModule,
        )

        self.assertIn("世界", result["action_pullback_tickers"][0])
        self.assertIn("短線：等便宜買", result["action_pullback_tickers"][0])
        self.assertIn("聯陽", result["action_midlong_tickers"][0])
        self.assertIn("中長線：繼續看好", result["action_midlong_tickers"][0])
        self.assertIn("緯創", result["action_watch_tickers"][0])
        self.assertIn("日月光投控", result["action_cooldown_tickers"][0])

    def test_build_lucky_pick_line_uses_positive_pool_and_date(self) -> None:
        df = pd.DataFrame(
            [
                {
                    "date": "2026-05-13",
                    "ticker": "9999.TW",
                    "name": "過熱股",
                    "grade": "A",
                    "setup_score": 12,
                    "risk_score": 8,
                    "signals": "TREND,ACCEL",
                    "spec_risk_label": "疑似炒作風險高",
                    "rank": 1,
                },
                {
                    "date": "2026-05-13",
                    "ticker": "2330.TW",
                    "name": "台積電",
                    "grade": "A",
                    "setup_score": 8,
                    "risk_score": 1,
                    "signals": "TREND",
                    "spec_risk_label": "正常",
                    "rank": 2,
                },
            ]
        )

        line = _build_lucky_pick_line(df)

        self.assertIn("星期三", line)
        self.assertIn("台積電 (2330.TW)", line)
        self.assertNotIn("小彩蛋：", line)
        self.assertNotIn("趨勢還站得住", line)
        self.assertNotIn("過熱股", line)

    def test_build_lucky_pick_line_uses_rng_each_call(self) -> None:
        df = pd.DataFrame(
            [
                {
                    "date": "2026-05-13",
                    "ticker": f"23{i:02d}.TW",
                    "name": f"測試股{i}",
                    "grade": "A",
                    "setup_score": 8,
                    "risk_score": 1,
                    "signals": "TREND",
                    "spec_risk_label": "正常",
                    "rank": i,
                }
                for i in range(20)
            ]
        )

        class FakeRng:
            def __init__(self, values: list[int]) -> None:
                self.values = list(values)

            def randrange(self, stop: int) -> int:
                return self.values.pop(0) % stop

        first = _build_lucky_pick_line(df, rng=FakeRng([0, 0]))
        repeated = _build_lucky_pick_line(df, rng=FakeRng([0, 0]))
        second = _build_lucky_pick_line(df, rng=FakeRng([1, 1]))

        self.assertEqual(first, repeated)
        self.assertNotEqual(first, second)

    def test_lucky_pick_tagline_pool_has_enough_safe_variants(self) -> None:
        self.assertGreaterEqual(len(LUCKY_PICK_TAGLINES), 10)
        self.assertLessEqual(len(LUCKY_PICK_TAGLINES), 20)
        self.assertTrue(all("{stock}" in template for template in LUCKY_PICK_TAGLINES))
        self.assertTrue(all("{weekday}" in template for template in LUCKY_PICK_TAGLINES))
        self.assertTrue(all("{signal}" not in template for template in LUCKY_PICK_TAGLINES))
        self.assertTrue(
            any(token in template for token in ["不追價", "不漂亮", "衝動", "好球帶", "紀律"])
            for template in LUCKY_PICK_TAGLINES
        )

    def test_write_quality_value_trial_ledger_tracks_active_simulated_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger_csv = Path(tmpdir) / "quality_value_trial_ledger.csv"
            ledger_md = Path(tmpdir) / "quality_value_trial_ledger.md"
            tracking = pd.DataFrame(
                [
                    {
                        "ticker": "3213.TWO",
                        "name": "茂訊",
                        "added_date": "2026-05-07",
                        "days_tracked": 1,
                        "close": 114.5,
                        "entry_bias": "分批試單",
                        "buy_zone_low": 111.92,
                        "buy_zone_high": 114.50,
                        "stop_loss": 106.42,
                        "zone_status": "買區內",
                        "heat_status": "偏熱",
                    }
                ]
            )

            result = write_quality_value_trial_ledger(
                tracking,
                ledger_csv=ledger_csv,
                ledger_md=ledger_md,
                trial_tickers=("3213.TWO",),
            )
            row = result.iloc[0].to_dict()
            ledger_csv_exists = ledger_csv.exists()
            ledger_md_exists = ledger_md.exists()

        self.assertEqual(row["trial_status"], "active_trial")
        self.assertEqual(row["decision_state"], "risk_watch")
        self.assertEqual(row["next_action"], "第一筆 1/3 可研究")
        self.assertEqual(row["simulated_entry_price"], 114.5)
        self.assertEqual(row["add_trigger_price"], 117.94)
        self.assertEqual(row["trim_watch_price"], 123.66)
        self.assertEqual(row["risk_to_stop_pct"], -7.06)
        self.assertEqual(row["days_to_review"], 9)
        self.assertTrue(ledger_csv_exists)
        self.assertTrue(ledger_md_exists)

    def test_collect_status_metrics_reads_midlong_threshold_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            theme_outdir = Path(tmpdir) / "theme_watchlist_daily"
            verification_outdir = Path(tmpdir) / "verification" / "watchlist_daily"
            theme_outdir.mkdir(parents=True, exist_ok=True)
            verification_outdir.mkdir(parents=True, exist_ok=True)

            pd.DataFrame(
                [{"ticker": "2330.TW", "spec_risk_score": 0, "spec_risk_label": "正常", "rank": 1}]
            ).to_csv(theme_outdir / "daily_rank.csv", index=False)
            pd.DataFrame(
                [
                    {
                        "signal_date": "2026-04-22",
                        "horizon_days": 1,
                        "watch_type": "midlong",
                        "reco_status": "below_threshold",
                        "market_heat": "hot",
                        "action": "減碼觀察",
                        "realized_ret_pct": 4.0,
                        "status": "ok",
                    },
                    {
                        "signal_date": "2026-04-22",
                        "horizon_days": 1,
                        "watch_type": "midlong",
                        "reco_status": "ok",
                        "market_heat": "normal",
                        "action": "續抱",
                        "realized_ret_pct": 1.0,
                        "status": "ok",
                    },
                ]
            ).to_csv(verification_outdir / "reco_outcomes.csv", index=False)

            metrics = collect_status_metrics(theme_outdir, verification_outdir)

        self.assertEqual(metrics["midlong_threshold_gate_status"], "block_loosening")
        self.assertEqual(metrics["midlong_threshold_gate_horizon"], "1")
        self.assertIn("normal_below_n=0", metrics["midlong_threshold_gate_detail"])

    def test_collect_status_metrics_marks_stale_watchlist_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            theme_outdir = Path(tmpdir) / "theme_watchlist_daily"
            verification_outdir = Path(tmpdir) / "verification" / "watchlist_daily"
            theme_outdir.mkdir(parents=True, exist_ok=True)
            verification_outdir.mkdir(parents=True, exist_ok=True)

            daily_rank_csv = theme_outdir / "daily_rank.csv"
            daily_report_md = theme_outdir / "daily_report.md"
            runtime_metrics_json = theme_outdir / "runtime_metrics.json"
            pd.DataFrame([{"ticker": "2330.TW", "spec_risk_score": 0, "spec_risk_label": "正常", "rank": 1}]).to_csv(
                daily_rank_csv, index=False
            )
            daily_report_md.write_text("# report\n", encoding="utf-8")
            runtime_metrics_json.write_text(json.dumps({"status": "ok", "wall_seconds": 1.0}), encoding="utf-8")
            stale_ts = daily_rank_csv.stat().st_mtime - 10
            os.utime(daily_report_md, (stale_ts, stale_ts))
            os.utime(runtime_metrics_json, (stale_ts, stale_ts))

            metrics = collect_status_metrics(theme_outdir, verification_outdir)

        self.assertEqual(metrics["watchlist_artifact_freshness_status"], "stale_report")
        self.assertIn("daily_report.md", metrics["watchlist_artifact_freshness_detail"])
        self.assertIn("runtime_metrics.json", metrics["watchlist_artifact_freshness_detail"])

    def test_collect_status_metrics_treats_synced_report_with_old_runtime_as_expected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            theme_outdir = Path(tmpdir) / "theme_watchlist_daily"
            verification_outdir = Path(tmpdir) / "verification" / "watchlist_daily"
            theme_outdir.mkdir(parents=True, exist_ok=True)
            verification_outdir.mkdir(parents=True, exist_ok=True)

            daily_rank_csv = theme_outdir / "daily_rank.csv"
            daily_report_md = theme_outdir / "daily_report.md"
            runtime_metrics_json = theme_outdir / "runtime_metrics.json"
            pd.DataFrame([{"ticker": "2330.TW", "spec_risk_score": 0, "spec_risk_label": "正常", "rank": 1}]).to_csv(
                daily_rank_csv, index=False
            )
            daily_report_md.write_text("# report\n", encoding="utf-8")
            runtime_metrics_json.write_text(json.dumps({"status": "ok", "wall_seconds": 1.0}), encoding="utf-8")
            stale_ts = daily_rank_csv.stat().st_mtime - 10
            os.utime(runtime_metrics_json, (stale_ts, stale_ts))

            metrics = collect_status_metrics(theme_outdir, verification_outdir)

        self.assertEqual(metrics["watchlist_artifact_freshness_status"], "report_current_runtime_stale")
        self.assertIn("daily_report.md is synced", metrics["watchlist_artifact_freshness_detail"])

    def test_write_local_status_dashboard_writes_markdown_and_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            theme_outdir = Path(tmpdir) / "theme_watchlist_daily"
            verification_outdir = Path(tmpdir) / "verification" / "watchlist_daily"
            status_md = theme_outdir / "local_run_status.md"
            status_json = theme_outdir / "local_run_status.json"
            theme_outdir.mkdir(parents=True, exist_ok=True)
            verification_outdir.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(
                [
                    {"ticker": "3057.TW", "spec_risk_score": 8, "spec_risk_label": "疑似炒作風險高", "rank": 1},
                    {"ticker": "2330.TW", "spec_risk_score": 0, "spec_risk_label": "正常", "rank": 2},
                ]
            ).to_csv(theme_outdir / "daily_rank.csv", index=False)
            (theme_outdir / "daily_report.md").write_text("# report\n", encoding="utf-8")
            (theme_outdir / "runtime_metrics.json").write_text(
                json.dumps({"status": "ok", "wall_seconds": 1.0}),
                encoding="utf-8",
            )
            pd.DataFrame(
                [{"signal_date": "2026-04-23", "watch_type": "short", "ticker": "3057.TW"}]
            ).to_csv(verification_outdir / "reco_snapshots.csv", index=False)
            pd.DataFrame(
                [
                    {
                        "signal_date": "2026-04-23",
                        "horizon_days": 1,
                        "watch_type": "short",
                        "ticker": "3057.TW",
                        "status": "insufficient_forward_data",
                    }
                ]
            ).to_csv(verification_outdir / "reco_outcomes.csv", index=False)

            args = parse_args(["--mode", "preopen"])
            steps = [{"name": "watchlist", "label": "Watchlist", "status": "completed", "detail": "OK"}]

            write_local_status_dashboard(
                args=args,
                steps=steps,
                overall_status="ok",
                theme_outdir=theme_outdir,
                verification_outdir=verification_outdir,
                status_md=status_md,
                status_json=status_json,
            )

            markdown = status_md.read_text(encoding="utf-8")
            payload = json.loads(status_json.read_text(encoding="utf-8"))
            shadow_md_exists = (theme_outdir / "shadow_open_not_chase_tracking.md").exists()
            shadow_csv_exists = (theme_outdir / "shadow_open_not_chase_tracking.csv").exists()

        self.assertIn("Local Run Status", markdown)
        self.assertIn("Watchlist", markdown)
        self.assertIn("Watchlist runtime", markdown)
        self.assertIn("Verification runtime", markdown)
        self.assertIn("Verification gate status", markdown)
        self.assertIn("Verification duplicate keys", markdown)
        self.assertIn("Midlong threshold gate", markdown)
        self.assertIn("Spec risk high rows", markdown)
        self.assertIn("Watchlist artifact freshness", markdown)
        self.assertIn("3057.TW", markdown)
        self.assertIn("Quality value lifecycle rows", markdown)
        self.assertIn("Quality value new-addition rows", markdown)
        self.assertIn("Quality value trial ledger rows", markdown)
        self.assertIn("Quality value candidate review", markdown)
        self.assertEqual(payload["mode"], "preopen")
        self.assertEqual(payload["overall_status"], "ok")
        self.assertEqual(payload["steps"][0]["status"], "completed")
        self.assertIn("watchlist_runtime", payload["outputs"])
        self.assertIn("portfolio_runtime", payload["outputs"])
        self.assertIn("verification_runtime", payload["outputs"])
        self.assertIn("shadow_tracking", payload["outputs"])
        self.assertIn("quality_value_tracking", payload["outputs"])
        self.assertIn("quality_value_new_additions_tracking", payload["outputs"])
        self.assertIn("quality_value_trial_ledger", payload["outputs"])
        self.assertIn("quality_value_candidate_review", payload["outputs"])
        self.assertEqual(payload["metrics"]["spec_risk_high_rows"], 1)
        self.assertEqual(payload["metrics"]["watchlist_artifact_freshness_status"], "current")
        self.assertEqual(payload["metrics"]["verification_gate_status"], "ok")
        self.assertTrue(shadow_md_exists)
        self.assertTrue(shadow_csv_exists)

    def test_build_shadow_open_not_chase_tracking_df_joins_1d_outcomes(self) -> None:
        shadow_snapshots = pd.DataFrame(
            [
                {
                    "signal_date": "2026-05-04",
                    "ticker": "5386.TWO",
                    "name": "青雲",
                    "rank": 1,
                    "scenario_label": "高檔震盪盤",
                    "market_heat": "hot",
                    "spec_risk_bucket": "normal",
                    "shadow_status": "eligible",
                    "shadow_eligible": True,
                    "action_label": "開高不追",
                }
            ]
        )
        outcomes = pd.DataFrame(
            [
                {
                    "signal_date": "2026-05-04",
                    "ticker": "5386.TWO",
                    "watch_type": "short",
                    "horizon_days": 1,
                    "status": "ok",
                    "realized_ret_pct": 9.92,
                },
                {
                    "signal_date": "2026-05-04",
                    "ticker": "5386.TWO",
                    "watch_type": "short",
                    "horizon_days": 5,
                    "status": "insufficient_forward_data",
                },
            ]
        )

        tracking = build_shadow_open_not_chase_tracking_df(shadow_snapshots, outcomes)

        self.assertEqual(len(tracking), 1)
        self.assertEqual(str(tracking.iloc[0]["outcome_status_1d"]), "ok")
        self.assertAlmostEqual(float(tracking.iloc[0]["realized_ret_pct_1d"]), 9.92, places=2)
        self.assertTrue(bool(tracking.iloc[0]["matured_1d"]))
        self.assertTrue(bool(tracking.iloc[0]["win_1d"]))
        self.assertEqual(str(tracking.iloc[0]["manual_trial_cap"]), "<= 1/4 test position")
        self.assertIn("收盤破前低或 1 ATR 出", str(tracking.iloc[0]["manual_trial_rule"]))

    def test_write_shadow_open_not_chase_tracking_outputs_writes_summary_and_csv(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            theme_outdir = Path(tmpdir) / "theme_watchlist_daily"
            verification_outdir = Path(tmpdir) / "verification" / "watchlist_daily"
            theme_outdir.mkdir(parents=True, exist_ok=True)
            verification_outdir.mkdir(parents=True, exist_ok=True)

            pd.DataFrame(
                [
                    {
                        "signal_date": "2026-05-04",
                        "ticker": "5386.TWO",
                        "name": "青雲",
                        "rank": 1,
                        "scenario_label": "高檔震盪盤",
                        "market_heat": "hot",
                        "spec_risk_bucket": "normal",
                        "shadow_status": "eligible",
                        "shadow_eligible": True,
                        "action_label": "開高不追",
                    },
                    {
                        "signal_date": "2026-05-04",
                        "ticker": "9999.TW",
                        "name": "人工觀察",
                        "rank": 2,
                        "scenario_label": "高檔震盪盤",
                        "market_heat": "hot",
                        "spec_risk_bucket": "high",
                        "shadow_status": "decision_required",
                        "shadow_eligible": False,
                        "action_label": "只觀察不追",
                    }
                ]
            ).to_csv(verification_outdir / "shadow_open_not_chase_snapshots.csv", index=False)
            pd.DataFrame(
                [
                    {
                        "signal_date": "2026-05-04",
                        "horizon_days": 1,
                        "watch_type": "short",
                        "ticker": "5386.TWO",
                        "name": "青雲",
                        "reco_status": "below_threshold",
                        "action": "開高不追",
                        "status": "ok",
                        "realized_ret_pct": 9.92,
                        "market_heat": "hot",
                    },
                    {
                        "signal_date": "2026-05-04",
                        "horizon_days": 1,
                        "watch_type": "short",
                        "ticker": "2374.TW",
                        "name": "佳能",
                        "reco_status": "ok",
                        "action": "等拉回",
                        "status": "ok",
                        "realized_ret_pct": 3.07,
                        "market_heat": "hot",
                    },
                ]
            ).to_csv(verification_outdir / "reco_outcomes.csv", index=False)

            write_shadow_open_not_chase_tracking_outputs(
                theme_outdir=theme_outdir,
                verification_outdir=verification_outdir,
                tracking_md=theme_outdir / "shadow_open_not_chase_tracking.md",
                tracking_csv=theme_outdir / "shadow_open_not_chase_tracking.csv",
            )

            markdown = (theme_outdir / "shadow_open_not_chase_tracking.md").read_text(encoding="utf-8")
            tracking_csv = pd.read_csv(theme_outdir / "shadow_open_not_chase_tracking.csv")

        self.assertIn("短線候補 Daily Tracking", markdown)
        self.assertIn("Promotion Criteria", markdown)
        self.assertIn("HRR Top 5 可進自動試單候選", markdown)
        self.assertIn("2026-05-04", markdown)
        self.assertEqual(len(tracking_csv), 2)
        self.assertEqual(str(tracking_csv.iloc[0]["ticker"]), "5386.TWO")
        self.assertEqual(str(tracking_csv.iloc[0]["manual_trial_cap"]), "<= 1/4 test position")
        manual_row = tracking_csv[tracking_csv["ticker"].astype(str) == "9999.TW"].iloc[0]
        self.assertEqual(str(manual_row["manual_trial_cap"]), "<= 1/3 test position")

    def test_main_runs_preopen_steps_in_order(self) -> None:
        calls: list[str] = []

        def _runner(name: str):
            def _inner(*args, **kwargs) -> int:
                calls.append(name)
                return 0

            return _inner

        with patch("stock_watch.cli.local_daily.run_daily_watchlist", side_effect=_runner("watchlist")), patch(
            "stock_watch.cli.local_daily.run_portfolio_step", side_effect=_runner("portfolio")
        ), patch("stock_watch.cli.local_daily.run_daily_verification.main", side_effect=_runner("verification")), patch(
            "stock_watch.cli.local_daily.write_local_status_dashboard"
        ) as mock_status:
            code = main(["--mode", "preopen"])

        self.assertEqual(code, 0)
        self.assertEqual(calls, ["watchlist", "verification"])
        mock_status.assert_called_once()

    def test_main_passes_force_watchlist_to_watchlist_step(self) -> None:
        with patch("stock_watch.cli.local_daily.run_daily_watchlist", return_value=0) as mock_watchlist, patch(
            "stock_watch.cli.local_daily.run_daily_verification.main", return_value=0
        ), patch("stock_watch.cli.local_daily.write_local_status_dashboard"):
            code = main(["--mode", "preopen", "--force-watchlist"])

        self.assertEqual(code, 0)
        mock_watchlist.assert_called_once_with(force_run=True, success_scope="preopen", send_notifications=False)

    def test_main_forces_watchlist_for_postclose_mode(self) -> None:
        with patch("stock_watch.cli.local_daily.run_daily_watchlist", return_value=0) as mock_watchlist, patch(
            "stock_watch.cli.local_daily.run_portfolio_step", return_value=0
        ), patch("stock_watch.cli.local_daily.run_daily_verification.main", return_value=0), patch(
            "stock_watch.cli.local_daily._watchlist_artifact_freshness",
            return_value={"status": "current", "detail": "synced"},
        ), patch(
            "stock_watch.cli.local_daily.write_local_status_dashboard"
        ):
            code = main(["--mode", "postclose"])

        self.assertEqual(code, 0)
        mock_watchlist.assert_called_once_with(force_run=True, success_scope="postclose", send_notifications=False)

    def test_main_portfolio_mode_auto_syncs_watchlist_report_by_default(self) -> None:
        calls: list[str] = []

        def _portfolio(*args, **kwargs) -> int:
            calls.append("portfolio")
            return 0

        with patch("stock_watch.cli.local_daily.run_portfolio_step", side_effect=_portfolio), patch(
            "stock_watch.cli.local_daily.report_sync.main", return_value=0
        ) as mock_report_sync, patch(
            "stock_watch.cli.local_daily.quality_value.main", return_value=0
        ), patch(
            "stock_watch.cli.local_daily._watchlist_artifact_freshness",
            return_value={"status": "stale_report", "detail": "rank newer than report"},
        ), patch("stock_watch.cli.local_daily.write_local_status_dashboard") as mock_status:
            code = main(["--mode", "portfolio"])

        self.assertEqual(code, 0)
        self.assertEqual(calls, ["portfolio"])
        mock_report_sync.assert_called_once_with([])
        steps = mock_status.call_args.kwargs["steps"]
        self.assertEqual(steps[-2]["name"], "report_sync")
        self.assertEqual(steps[-2]["status"], "completed")
        self.assertEqual(steps[-1]["name"], "quality_value")
        self.assertEqual(steps[-1]["status"], "completed")

    def test_main_portfolio_mode_can_disable_auto_sync(self) -> None:
        with patch("stock_watch.cli.local_daily.run_portfolio_step", return_value=0), patch(
            "stock_watch.cli.local_daily.report_sync.main", return_value=0
        ) as mock_report_sync, patch(
            "stock_watch.cli.local_daily.write_local_status_dashboard"
        ) as mock_status:
            code = main(["--mode", "portfolio", "--no-sync-watchlist-report"])

        self.assertEqual(code, 0)
        mock_report_sync.assert_not_called()
        self.assertTrue(all(step["name"] != "report_sync" for step in mock_status.call_args.kwargs["steps"]))

    def test_main_runs_postclose_steps_in_order(self) -> None:
        calls: list[str] = []

        def _runner(name: str):
            def _inner(*args, **kwargs) -> int:
                calls.append(name)
                return 0

            return _inner

        with patch("stock_watch.cli.local_daily.run_daily_watchlist", side_effect=_runner("watchlist")), patch(
            "stock_watch.cli.local_daily.run_portfolio_step", side_effect=_runner("portfolio")
        ), patch("stock_watch.cli.local_daily.run_daily_verification.main", side_effect=_runner("verification")), patch(
            "stock_watch.cli.local_daily._watchlist_artifact_freshness",
            return_value={"status": "current", "detail": "synced"},
        ), patch(
            "stock_watch.cli.local_daily.write_local_status_dashboard"
        ) as mock_status:
            code = main(["--mode", "postclose"])

        self.assertEqual(code, 0)
        self.assertEqual(calls, ["watchlist", "portfolio", "verification"])
        mock_status.assert_called_once()

    def test_main_postclose_auto_syncs_when_portfolio_refreshes_rank(self) -> None:
        calls: list[str] = []

        def _runner(name: str):
            def _inner(*args, **kwargs) -> int:
                calls.append(name)
                return 0

            return _inner

        with patch("stock_watch.cli.local_daily.run_daily_watchlist", side_effect=_runner("watchlist")), patch(
            "stock_watch.cli.local_daily.run_portfolio_step", side_effect=_runner("portfolio")
        ), patch("stock_watch.cli.local_daily.run_daily_verification.main", side_effect=_runner("verification")), patch(
            "stock_watch.cli.local_daily._watchlist_artifact_freshness",
            return_value={"status": "stale_report", "detail": "rank newer than report"},
        ), patch("stock_watch.cli.local_daily.quality_value.main", return_value=0), patch(
            "stock_watch.cli.local_daily.report_sync.main", return_value=0
        ) as mock_report_sync, patch(
            "stock_watch.cli.local_daily.write_local_status_dashboard"
        ) as mock_status:
            code = main(["--mode", "postclose"])

        self.assertEqual(code, 0)
        self.assertEqual(calls, ["watchlist", "portfolio", "verification"])
        mock_report_sync.assert_called_once_with([])
        steps = mock_status.call_args.kwargs["steps"]
        self.assertEqual(steps[-2]["name"], "report_sync")
        self.assertEqual(steps[-2]["status"], "completed")
        self.assertEqual(steps[-1]["name"], "quality_value")
        self.assertEqual(steps[-1]["status"], "completed")

    def test_main_preopen_then_postclose_smoke_keeps_outputs_in_sync(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            theme_outdir = Path(tmpdir) / "theme_watchlist_daily"
            verification_outdir = Path(tmpdir) / "verification" / "watchlist_daily"
            theme_outdir.mkdir(parents=True, exist_ok=True)
            verification_outdir.mkdir(parents=True, exist_ok=True)

            write_status = write_local_status_dashboard
            watchlist_calls: list[tuple[bool, str | None, bool]] = []
            verification_modes: list[str] = []

            def _write_watchlist_outputs(tag: str) -> None:
                pd.DataFrame(
                    [{"ticker": "2330.TW", "spec_risk_score": 0, "spec_risk_label": "正常", "rank": 1, "tag": tag}]
                ).to_csv(theme_outdir / "daily_rank.csv", index=False)
                (theme_outdir / "daily_report.md").write_text(f"# {tag} report\n", encoding="utf-8")
                (theme_outdir / "runtime_metrics.json").write_text(
                    json.dumps({"status": "ok", "wall_seconds": 1.0, "tag": tag}),
                    encoding="utf-8",
                )

            def _watchlist(*, force_run: bool, success_scope: str | None, send_notifications: bool = True) -> int:
                watchlist_calls.append((force_run, success_scope, send_notifications))
                _write_watchlist_outputs(success_scope or "watchlist")
                return 0

            def _portfolio() -> int:
                (theme_outdir / "portfolio_report.md").write_text("# portfolio\n", encoding="utf-8")
                (theme_outdir / "portfolio_runtime_metrics.json").write_text(
                    json.dumps({"status": "ok", "wall_seconds": 0.5}),
                    encoding="utf-8",
                )
                return 0

            def _verification(argv: list[str]) -> int:
                mode = argv[argv.index("--mode") + 1]
                verification_modes.append(mode)
                pd.DataFrame(
                    [{"signal_date": "2026-05-04", "watch_type": "short", "ticker": "2330.TW"}]
                ).to_csv(verification_outdir / "reco_snapshots.csv", index=False)
                pd.DataFrame(
                    [
                        {
                            "signal_date": "2026-05-04",
                            "horizon_days": 1,
                            "watch_type": "short",
                            "ticker": "2330.TW",
                            "status": "insufficient_forward_data",
                        }
                    ]
                ).to_csv(verification_outdir / "reco_outcomes.csv", index=False)
                (verification_outdir / "runtime_metrics.json").write_text(
                    json.dumps({"status": "ok", "wall_seconds": 2.0, "mode": mode}),
                    encoding="utf-8",
                )
                return 0

            def _status_proxy(*, args, steps, overall_status, **kwargs) -> None:
                write_status(
                    args=args,
                    steps=steps,
                    overall_status=overall_status,
                    theme_outdir=theme_outdir,
                    verification_outdir=verification_outdir,
                    status_md=theme_outdir / "local_run_status.md",
                    status_json=theme_outdir / "local_run_status.json",
                )

            with patch("stock_watch.cli.local_daily.run_daily_watchlist", side_effect=_watchlist), patch(
                "stock_watch.cli.local_daily.run_portfolio_step", side_effect=_portfolio
            ), patch("stock_watch.cli.local_daily.run_daily_verification.main", side_effect=_verification), patch(
                "stock_watch.cli.local_daily.quality_value.main", return_value=0
            ), patch(
                "stock_watch.cli.local_daily.write_local_status_dashboard", side_effect=_status_proxy
            ):
                preopen_code = main(["--mode", "preopen"])
                postclose_code = main(["--mode", "postclose"])

            payload = json.loads((theme_outdir / "local_run_status.json").read_text(encoding="utf-8"))

        self.assertEqual(preopen_code, 0)
        self.assertEqual(postclose_code, 0)
        self.assertEqual(watchlist_calls, [(False, "preopen", False), (True, "postclose", False)])
        self.assertEqual(verification_modes, ["preopen", "postclose"])
        self.assertEqual(payload["mode"], "postclose")
        self.assertEqual(payload["overall_status"], "ok")
        self.assertEqual(payload["metrics"]["watchlist_artifact_freshness_status"], "current")
        self.assertEqual([step["status"] for step in payload["steps"][:3]], ["completed", "completed", "completed"])
        self.assertEqual(payload["steps"][-2]["name"], "report_sync")
        self.assertEqual(payload["steps"][-2]["status"], "skipped")
        self.assertEqual(payload["steps"][-1]["name"], "quality_value")
        self.assertEqual(payload["steps"][-1]["status"], "completed")
        self.assertEqual(payload["metrics"]["verification_gate_status"], "ok")

    def test_main_runs_portfolio_only_mode(self) -> None:
        calls: list[str] = []

        def _runner(name: str):
            def _inner(*args, **kwargs) -> int:
                calls.append(name)
                return 0

            return _inner

        with patch("stock_watch.cli.local_daily.run_daily_watchlist", side_effect=_runner("watchlist")), patch(
            "stock_watch.cli.local_daily.run_portfolio_step", side_effect=_runner("portfolio")
        ), patch("stock_watch.cli.local_daily.run_daily_verification.main", side_effect=_runner("verification")), patch(
            "stock_watch.cli.local_daily.write_local_status_dashboard"
        ) as mock_status:
            code = main(["--mode", "portfolio"])

        self.assertEqual(code, 0)
        self.assertEqual(calls, ["portfolio"])
        mock_status.assert_called_once()

    def test_main_writes_failed_status_when_step_errors(self) -> None:
        calls: list[str] = []

        def _watchlist(*args, **kwargs) -> int:
            calls.append("watchlist")
            return 1

        with patch("stock_watch.cli.local_daily.run_daily_watchlist", side_effect=_watchlist), patch(
            "stock_watch.cli.local_daily.write_local_status_dashboard"
        ) as mock_status:
            code = main(["--mode", "postclose"])

        self.assertEqual(code, 1)
        self.assertEqual(calls, ["watchlist"])
        self.assertEqual(mock_status.call_args.kwargs["overall_status"], "failed")
