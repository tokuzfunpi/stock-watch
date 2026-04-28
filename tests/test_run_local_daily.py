from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from stock_watch.cli.local_daily import build_verification_argv
from stock_watch.cli.local_daily import collect_status_metrics
from stock_watch.cli.local_daily import main
from stock_watch.cli.local_daily import parse_args
from stock_watch.cli.local_daily import should_run_step
from stock_watch.cli.local_daily import write_local_status_dashboard


class RunLocalDailyTests(unittest.TestCase):
    def test_parse_args_defaults_to_full_mode(self) -> None:
        args = parse_args([])
        self.assertEqual(args.mode, "full")

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

    def test_write_local_status_dashboard_writes_markdown_and_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            theme_outdir = Path(tmpdir) / "theme_watchlist_daily"
            verification_outdir = Path(tmpdir) / "verification" / "watchlist_daily"
            status_md = theme_outdir / "local_run_status.md"
            status_json = theme_outdir / "local_run_status.json"
            theme_outdir.mkdir(parents=True, exist_ok=True)
            verification_outdir.mkdir(parents=True, exist_ok=True)
            pd.DataFrame([{"ticker": "2330.TW"}]).to_csv(theme_outdir / "daily_rank.csv", index=False)
            pd.DataFrame(
                [
                    {"ticker": "3057.TW", "spec_risk_score": 8, "spec_risk_label": "疑似炒作風險高", "rank": 1},
                    {"ticker": "2330.TW", "spec_risk_score": 0, "spec_risk_label": "正常", "rank": 2},
                ]
            ).to_csv(theme_outdir / "daily_rank.csv", index=False)
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

        self.assertIn("Local Run Status", markdown)
        self.assertIn("Watchlist", markdown)
        self.assertIn("Watchlist runtime", markdown)
        self.assertIn("Verification runtime", markdown)
        self.assertIn("Verification gate status", markdown)
        self.assertIn("Verification duplicate keys", markdown)
        self.assertIn("Midlong threshold gate", markdown)
        self.assertIn("Spec risk high rows", markdown)
        self.assertIn("3057.TW", markdown)
        self.assertEqual(payload["mode"], "preopen")
        self.assertEqual(payload["overall_status"], "ok")
        self.assertEqual(payload["steps"][0]["status"], "completed")
        self.assertIn("watchlist_runtime", payload["outputs"])
        self.assertIn("portfolio_runtime", payload["outputs"])
        self.assertIn("verification_runtime", payload["outputs"])
        self.assertEqual(payload["metrics"]["spec_risk_high_rows"], 1)
        self.assertEqual(payload["metrics"]["verification_gate_status"], "ok")

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
        mock_watchlist.assert_called_once_with(force_run=True)

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
            "stock_watch.cli.local_daily.write_local_status_dashboard"
        ) as mock_status:
            code = main(["--mode", "postclose"])

        self.assertEqual(code, 0)
        self.assertEqual(calls, ["watchlist", "portfolio", "verification"])
        mock_status.assert_called_once()

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
