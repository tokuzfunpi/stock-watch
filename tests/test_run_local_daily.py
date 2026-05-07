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
from stock_watch.cli.local_daily import build_shadow_open_not_chase_tracking_df
from stock_watch.cli.local_daily import collect_status_metrics
from stock_watch.cli.local_daily import configure_local_telegram_chat_ids
from stock_watch.cli.local_daily import main
from stock_watch.cli.local_daily import parse_local_telegram_chat_ids
from stock_watch.cli.local_daily import parse_args
from stock_watch.cli.local_daily import should_run_step
from stock_watch.cli.local_daily import update_quality_value_tracking
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
        args = parse_args([])
        self.assertEqual(args.mode, "full")
        self.assertEqual(args.local_telegram_chat_ids, "7758949915")

    def test_parse_local_telegram_chat_ids_supports_commas_and_newlines(self) -> None:
        self.assertEqual(parse_local_telegram_chat_ids("7758949915,123\n-1001"), [7758949915, 123, -1001])

    def test_configure_local_telegram_chat_ids_overrides_daily_module(self) -> None:
        class FakeDailyModule:
            TELEGRAM_CHAT_IDS = [111, 222]

        chat_ids = configure_local_telegram_chat_ids("7758949915", FakeDailyModule)

        self.assertEqual(chat_ids, [7758949915])
        self.assertEqual(FakeDailyModule.TELEGRAM_CHAT_IDS, [7758949915])

    def test_build_action_summary_notification_renders_five_lines(self) -> None:
        message = build_action_summary_notification(
            {
                "action_trial_tickers": ["6161.TWO 捷波"],
                "action_pullback_tickers": ["3515.TW 華擎"],
                "action_wait_strength_tickers": ["3005.TW 神基"],
                "action_cooldown_tickers": ["2376.TW 技嘉"],
                "portfolio_trim_tickers": ["英業達 (2356)"],
            }
        )

        self.assertEqual(len(message.splitlines()), 5)
        self.assertIn("🟢 可試單：6161.TWO 捷波", message)
        self.assertIn("💼 持股落袋：英業達 (2356)", message)

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
                "- 英業達 (2356) | 進攻持股 | 建議 分批落袋 | 價格帶 加碼≤47.95\n",
                encoding="utf-8",
            )
            pd.DataFrame(
                [
                    {"ticker": "6161.TWO", "name": "捷波", "decision_priority": 34, "entry_bias": "分批試單"},
                    {"ticker": "4966.TWO", "name": "譜瑞-KY", "decision_priority": 25, "entry_bias": "等拉回"},
                    {"ticker": "3005.TW", "name": "神基", "decision_priority": 23, "entry_bias": "等轉強"},
                    {"ticker": "6525.TW", "name": "捷敏-KY", "decision_priority": -6, "entry_bias": "等待降溫"},
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
        self.assertEqual(metrics["action_trial_tickers"], ["6161.TWO 捷波"])
        self.assertEqual(metrics["action_pullback_tickers"], ["4966.TWO 譜瑞-KY"])
        self.assertEqual(metrics["action_wait_strength_tickers"], ["3005.TW 神基"])
        self.assertEqual(metrics["action_cooldown_tickers"], ["6525.TW 捷敏-KY"])
        self.assertEqual(metrics["portfolio_trim_tickers"], ["英業達 (2356)"])

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
        self.assertIn("Quality value candidate review", markdown)
        self.assertEqual(payload["mode"], "preopen")
        self.assertEqual(payload["overall_status"], "ok")
        self.assertEqual(payload["steps"][0]["status"], "completed")
        self.assertIn("watchlist_runtime", payload["outputs"])
        self.assertIn("portfolio_runtime", payload["outputs"])
        self.assertIn("verification_runtime", payload["outputs"])
        self.assertIn("shadow_tracking", payload["outputs"])
        self.assertIn("quality_value_tracking", payload["outputs"])
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

        self.assertIn("開高不追 Daily Tracking", markdown)
        self.assertIn("Promotion Criteria", markdown)
        self.assertIn("2026-05-04", markdown)
        self.assertEqual(len(tracking_csv), 1)
        self.assertEqual(str(tracking_csv.iloc[0]["ticker"]), "5386.TWO")

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
        mock_watchlist.assert_called_once_with(force_run=True, success_scope="preopen")

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
        mock_watchlist.assert_called_once_with(force_run=True, success_scope="postclose")

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
            watchlist_calls: list[tuple[bool, str | None]] = []
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

            def _watchlist(*, force_run: bool, success_scope: str | None) -> int:
                watchlist_calls.append((force_run, success_scope))
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
        self.assertEqual(watchlist_calls, [(False, "preopen"), (True, "postclose")])
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
