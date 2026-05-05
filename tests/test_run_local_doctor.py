from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from stock_watch.cli.local_doctor import DoctorCheck
from stock_watch.cli.local_doctor import _check_verification_health
from stock_watch.cli.local_doctor import build_compact_summary
from stock_watch.cli.local_doctor import build_doctor_summary
from stock_watch.cli.local_doctor import main
from stock_watch.cli.local_doctor import overall_status
from stock_watch.cli.local_doctor import should_exit_nonzero
from stock_watch.cli.local_doctor import write_doctor_outputs


class RunLocalDoctorTests(unittest.TestCase):
    def test_overall_status_prioritizes_fail_then_warn(self) -> None:
        self.assertEqual(overall_status([DoctorCheck("a", "ok", "ok")]), "ok")
        self.assertEqual(overall_status([DoctorCheck("a", "info", "info")]), "ok")
        self.assertEqual(overall_status([DoctorCheck("a", "warn", "warn")]), "warn")
        self.assertEqual(
            overall_status([DoctorCheck("a", "warn", "warn"), DoctorCheck("b", "fail", "fail")]),
            "fail",
        )

    def test_build_doctor_summary_separates_warnings_and_advisories(self) -> None:
        summary = build_doctor_summary(
            [
                DoctorCheck("python_runtime", "ok", "ok"),
                DoctorCheck("telegram_config", "info", "not configured"),
                DoctorCheck("verification_health", "warn", "needs review"),
                DoctorCheck("config_json", "fail", "missing"),
            ]
        )

        self.assertEqual(summary["fail_count"], 1)
        self.assertEqual(summary["warn_count"], 1)
        self.assertEqual(summary["info_count"], 1)
        self.assertEqual(summary["failing_checks"], ["config_json"])
        self.assertEqual(summary["warning_checks"], ["verification_health"])
        self.assertEqual(summary["advisory_checks"], ["telegram_config"])

    def test_build_compact_summary_highlights_key_health_axes(self) -> None:
        checks = [
            DoctorCheck("telegram_config", "ok", "ok"),
            DoctorCheck("verification_health", "warn", "warn"),
        ]
        metrics = {
            "verification_gate_status": "review",
            "watchlist_artifact_freshness_status": "current",
        }

        line = build_compact_summary(overall="warn", checks=checks, metrics=metrics)

        self.assertIn("overall=warn", line)
        self.assertIn("warnings=verification_health", line)
        self.assertIn("notification=ok", line)
        self.assertIn("verification=review", line)
        self.assertIn("report=current", line)

    def test_should_exit_nonzero_respects_fail_threshold(self) -> None:
        self.assertFalse(should_exit_nonzero(overall="ok", fail_on="fail"))
        self.assertFalse(should_exit_nonzero(overall="warn", fail_on="fail"))
        self.assertTrue(should_exit_nonzero(overall="fail", fail_on="fail"))

    def test_should_exit_nonzero_respects_warn_threshold(self) -> None:
        self.assertFalse(should_exit_nonzero(overall="ok", fail_on="warn"))
        self.assertTrue(should_exit_nonzero(overall="warn", fail_on="warn"))
        self.assertTrue(should_exit_nonzero(overall="fail", fail_on="warn"))

    def test_check_telegram_config_accepts_token_from_local_getupdates_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            chat_ids_path = root / "chat_ids"
            getupdates_path = root / "telegram_getupdates_url"
            chat_ids_path.write_text("12345\n", encoding="utf-8")
            getupdates_path.write_text("https://api.telegram.org/bot123456:ABCDEF/getUpdates", encoding="utf-8")

            with patch.dict(os.environ, {}, clear=True), patch(
                "stock_watch.cli.local_doctor.REPO_ROOT", root
            ), patch("stock_watch.telegram_config.GETUPDATES_URL_PATH", getupdates_path):
                from stock_watch.cli.local_doctor import _check_telegram_config

                check = _check_telegram_config(chat_ids_path)

        self.assertEqual(check.status, "ok")
        self.assertIn("telegram_getupdates_url", check.detail)
        self.assertIn("1 chat id(s)", check.detail)

    def test_write_doctor_outputs_writes_markdown_and_json(self) -> None:
        checks = [DoctorCheck(name="python_runtime", status="ok", detail="Python 3.11")]
        metrics = {
            "daily_rank_rows": 1,
            "watchlist_artifact_freshness_status": "current",
            "watchlist_artifact_freshness_detail": "daily_rank.csv, daily_report.md, and runtime_metrics.json look in sync",
            "alert_tracking_rows": 2,
            "snapshot_rows": 3,
            "outcome_rows": 4,
            "verification_gate_status": "ok",
            "latest_snapshot_signal_date": "2026-04-27",
            "latest_outcome_signal_date": "2026-04-27",
            "snapshot_dup_keys": 0,
            "outcome_dup_keys": 0,
            "outcome_ok_rows": 2,
            "outcome_pending_rows": 2,
            "signal_date_missing_rows": 0,
            "no_price_series_rows": 0,
            "history_cache_files": 5,
            "history_cache_bytes": 600,
            "spec_risk_high_rows": 1,
            "spec_risk_watch_rows": 2,
            "spec_risk_top_tickers": ["3057.TW", "6669.TW"],
            "watchlist_runtime_seconds": 1.2,
            "portfolio_runtime_seconds": 0.4,
            "verification_runtime_seconds": 2.1,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            output_md = Path(tmpdir) / "local_doctor.md"
            output_json = Path(tmpdir) / "local_doctor.json"
            output_summary = Path(tmpdir) / "local_doctor_summary.txt"
            write_doctor_outputs(
                checks=checks,
                overall="ok",
                metrics=metrics,
                output_md=output_md,
                output_json=output_json,
                output_summary_txt=output_summary,
            )

            markdown = output_md.read_text(encoding="utf-8")
            payload = json.loads(output_json.read_text(encoding="utf-8"))
            summary_line = output_summary.read_text(encoding="utf-8").strip()

        self.assertIn("Local Doctor", markdown)
        self.assertIn("## Summary", markdown)
        self.assertIn("python_runtime", markdown)
        self.assertIn("History cache files", markdown)
        self.assertIn("Spec risk high rows", markdown)
        self.assertIn("Watchlist artifact freshness", markdown)
        self.assertIn("3057.TW", markdown)
        self.assertIn("Verification runtime seconds", markdown)
        self.assertIn("Verification gate status", markdown)
        self.assertIn("Verification duplicate keys", markdown)
        self.assertEqual(payload["overall"], "ok")
        self.assertIn("summary", payload)
        self.assertIn("summary_line", payload)
        self.assertIn("overall=ok", summary_line)
        self.assertEqual(payload["checks"][0]["status"], "ok")

    def test_check_verification_health_reports_clean_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            snapshots = root / "reco_snapshots.csv"
            outcomes = root / "reco_outcomes.csv"
            snapshots.write_text("signal_date,watch_type,ticker\n2026-04-27,short,2330.TW\n", encoding="utf-8")
            outcomes.write_text(
                "signal_date,horizon_days,watch_type,ticker,status\n2026-04-27,1,short,2330.TW,insufficient_forward_data\n",
                encoding="utf-8",
            )

            check = _check_verification_health(snapshots, outcomes)

        self.assertEqual(check.name, "verification_health")
        self.assertEqual(check.status, "ok")
        self.assertIn("pending=1", check.detail)

    def test_main_returns_zero_for_warn_only_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            theme_outdir = root / "theme_watchlist_daily"
            verification_outdir = root / "verification" / "watchlist_daily"
            history_cache_dir = theme_outdir / "history_cache"
            theme_outdir.mkdir(parents=True, exist_ok=True)
            verification_outdir.mkdir(parents=True, exist_ok=True)
            history_cache_dir.mkdir(parents=True, exist_ok=True)

            (root / "requirements.txt").write_text("pandas\n", encoding="utf-8")
            (root / "config.json").write_text(
                json.dumps(
                    {
                        "market_filter": {},
                        "notify": {},
                        "backtest": {},
                        "group_weights": {},
                    }
                ),
                encoding="utf-8",
            )
            (root / "watchlist.csv").write_text("ticker,name\n2330.TW,台積電\n", encoding="utf-8")
            (root / "portfolio.csv.example").write_text("ticker,shares\n2330,1\n", encoding="utf-8")
            (root / "chat_id_map.csv.example").write_text("chat_id,name\n", encoding="utf-8")
            (root / "telegram_getupdates_url.example").write_text("https://example.com\n", encoding="utf-8")
            (history_cache_dir / "2330_TW__5y.csv").write_text("Date,Close\n2026-04-24,1\n", encoding="utf-8")
            (theme_outdir / "daily_rank.csv").write_text(
                "ticker,spec_risk_score,spec_risk_label,rank\n3057.TW,8,疑似炒作風險高,1\n6669.TW,4,投機偏高,2\n2330.TW,0,正常,3\n",
                encoding="utf-8",
            )
            (theme_outdir / "daily_report.md").write_text("# report\n", encoding="utf-8")
            (theme_outdir / "runtime_metrics.json").write_text(json.dumps({"wall_seconds": 1.1}), encoding="utf-8")
            (theme_outdir / "portfolio_runtime_metrics.json").write_text(json.dumps({"wall_seconds": 0.7}), encoding="utf-8")
            (verification_outdir / "runtime_metrics.json").write_text(json.dumps({"wall_seconds": 2.2}), encoding="utf-8")
            (verification_outdir / "reco_snapshots.csv").write_text(
                "signal_date,watch_type,ticker\n2026-04-27,short,2330.TW\n",
                encoding="utf-8",
            )
            (verification_outdir / "reco_outcomes.csv").write_text(
                "signal_date,horizon_days,watch_type,ticker,status\n2026-04-27,1,short,2330.TW,insufficient_forward_data\n",
                encoding="utf-8",
            )

            with patch.dict(os.environ, {}, clear=True), patch(
                "stock_watch.cli.local_doctor.REPO_ROOT", root
            ), patch("stock_watch.cli.local_doctor.THEME_OUTDIR", theme_outdir), patch(
                "stock_watch.cli.local_doctor.VERIFICATION_OUTDIR", verification_outdir
            ), patch("stock_watch.cli.local_doctor.DOCTOR_MD", theme_outdir / "local_doctor.md"), patch(
                "stock_watch.cli.local_doctor.DOCTOR_JSON", theme_outdir / "local_doctor.json"
            ):
                code = main(["--skip-network"])

            payload = json.loads((theme_outdir / "local_doctor.json").read_text(encoding="utf-8"))
            check_names = {check["name"] for check in payload["checks"]}

        self.assertEqual(code, 0)
        self.assertEqual(payload["overall"], "ok")
        self.assertEqual(payload["summary"]["info_count"], 2)
        self.assertEqual(payload["summary"]["warn_count"], 0)
        self.assertIn("history_cache_dir", check_names)
        self.assertEqual(payload["metrics"]["history_cache_files"], 1)
        self.assertGreater(payload["metrics"]["history_cache_bytes"], 0)
        self.assertEqual(payload["metrics"]["spec_risk_high_rows"], 1)
        self.assertEqual(payload["metrics"]["spec_risk_watch_rows"], 1)
        self.assertEqual(payload["metrics"]["spec_risk_top_tickers"], ["3057.TW", "6669.TW"])
        self.assertEqual(payload["metrics"]["verification_runtime_seconds"], 2.2)
        self.assertEqual(payload["metrics"]["verification_gate_status"], "ok")
        self.assertEqual(payload["metrics"]["outcome_pending_rows"], 1)
        self.assertEqual(payload["metrics"]["watchlist_artifact_freshness_status"], "current")
        self.assertIn("verification_health", check_names)
        self.assertIn("watchlist_artifact_freshness", check_names)

    def test_main_flags_stale_watchlist_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            theme_outdir = root / "theme_watchlist_daily"
            verification_outdir = root / "verification" / "watchlist_daily"
            history_cache_dir = theme_outdir / "history_cache"
            theme_outdir.mkdir(parents=True, exist_ok=True)
            verification_outdir.mkdir(parents=True, exist_ok=True)
            history_cache_dir.mkdir(parents=True, exist_ok=True)

            (root / "requirements.txt").write_text("pandas\n", encoding="utf-8")
            (root / "config.json").write_text(
                json.dumps(
                    {
                        "market_filter": {},
                        "notify": {},
                        "backtest": {},
                        "group_weights": {},
                    }
                ),
                encoding="utf-8",
            )
            (root / "watchlist.csv").write_text("ticker,name\n2330.TW,台積電\n", encoding="utf-8")
            (root / "portfolio.csv.example").write_text("ticker,shares\n2330,1\n", encoding="utf-8")
            (root / "chat_id_map.csv.example").write_text("chat_id,name\n", encoding="utf-8")
            (root / "telegram_getupdates_url.example").write_text("https://example.com\n", encoding="utf-8")
            (theme_outdir / "daily_rank.csv").write_text(
                "ticker,spec_risk_score,spec_risk_label,rank\n2330.TW,0,正常,1\n",
                encoding="utf-8",
            )
            (theme_outdir / "daily_report.md").write_text("# stale report\n", encoding="utf-8")
            (theme_outdir / "runtime_metrics.json").write_text(json.dumps({"wall_seconds": 1.1}), encoding="utf-8")
            stale_ts = (theme_outdir / "daily_rank.csv").stat().st_mtime - 10
            os.utime(theme_outdir / "daily_report.md", (stale_ts, stale_ts))
            os.utime(theme_outdir / "runtime_metrics.json", (stale_ts, stale_ts))
            (theme_outdir / "portfolio_runtime_metrics.json").write_text(json.dumps({"wall_seconds": 0.7}), encoding="utf-8")
            (verification_outdir / "runtime_metrics.json").write_text(json.dumps({"wall_seconds": 2.2}), encoding="utf-8")
            (verification_outdir / "reco_snapshots.csv").write_text(
                "signal_date,watch_type,ticker\n2026-04-27,short,2330.TW\n",
                encoding="utf-8",
            )
            (verification_outdir / "reco_outcomes.csv").write_text(
                "signal_date,horizon_days,watch_type,ticker,status\n2026-04-27,1,short,2330.TW,insufficient_forward_data\n",
                encoding="utf-8",
            )

            with patch.dict(os.environ, {}, clear=True), patch(
                "stock_watch.cli.local_doctor.REPO_ROOT", root
            ), patch("stock_watch.cli.local_doctor.THEME_OUTDIR", theme_outdir), patch(
                "stock_watch.cli.local_doctor.VERIFICATION_OUTDIR", verification_outdir
            ), patch("stock_watch.cli.local_doctor.DOCTOR_MD", theme_outdir / "local_doctor.md"), patch(
                "stock_watch.cli.local_doctor.DOCTOR_JSON", theme_outdir / "local_doctor.json"
            ):
                code = main(["--skip-network"])

            payload = json.loads((theme_outdir / "local_doctor.json").read_text(encoding="utf-8"))
            freshness_check = next(
                check for check in payload["checks"] if check["name"] == "watchlist_artifact_freshness"
            )

        self.assertEqual(code, 0)
        self.assertEqual(payload["overall"], "warn")
        self.assertEqual(payload["summary"]["warn_count"], 1)
        self.assertEqual(payload["metrics"]["watchlist_artifact_freshness_status"], "stale_report")
        self.assertEqual(freshness_check["status"], "warn")
        self.assertIn("daily_report.md", freshness_check["detail"])

    def test_main_returns_one_for_warn_when_fail_on_warn(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            theme_outdir = root / "theme_watchlist_daily"
            verification_outdir = root / "verification" / "watchlist_daily"
            history_cache_dir = theme_outdir / "history_cache"
            theme_outdir.mkdir(parents=True, exist_ok=True)
            verification_outdir.mkdir(parents=True, exist_ok=True)
            history_cache_dir.mkdir(parents=True, exist_ok=True)

            (root / "requirements.txt").write_text("pandas\n", encoding="utf-8")
            (root / "config.json").write_text(
                json.dumps(
                    {
                        "market_filter": {},
                        "notify": {},
                        "backtest": {},
                        "group_weights": {},
                    }
                ),
                encoding="utf-8",
            )
            (root / "watchlist.csv").write_text("ticker,name\n2330.TW,台積電\n", encoding="utf-8")
            (root / "portfolio.csv.example").write_text("ticker,shares\n2330,1\n", encoding="utf-8")
            (root / "chat_id_map.csv.example").write_text("chat_id,name\n", encoding="utf-8")
            (root / "telegram_getupdates_url.example").write_text("https://example.com\n", encoding="utf-8")
            (theme_outdir / "daily_rank.csv").write_text(
                "ticker,spec_risk_score,spec_risk_label,rank\n2330.TW,0,正常,1\n",
                encoding="utf-8",
            )
            (theme_outdir / "daily_report.md").write_text("# stale report\n", encoding="utf-8")
            (theme_outdir / "runtime_metrics.json").write_text(json.dumps({"wall_seconds": 1.1}), encoding="utf-8")
            stale_ts = (theme_outdir / "daily_rank.csv").stat().st_mtime - 10
            os.utime(theme_outdir / "daily_report.md", (stale_ts, stale_ts))
            os.utime(theme_outdir / "runtime_metrics.json", (stale_ts, stale_ts))
            (theme_outdir / "portfolio_runtime_metrics.json").write_text(json.dumps({"wall_seconds": 0.7}), encoding="utf-8")
            (verification_outdir / "runtime_metrics.json").write_text(json.dumps({"wall_seconds": 2.2}), encoding="utf-8")
            (verification_outdir / "reco_snapshots.csv").write_text(
                "signal_date,watch_type,ticker\n2026-04-27,short,2330.TW\n",
                encoding="utf-8",
            )
            (verification_outdir / "reco_outcomes.csv").write_text(
                "signal_date,horizon_days,watch_type,ticker,status\n2026-04-27,1,short,2330.TW,insufficient_forward_data\n",
                encoding="utf-8",
            )

            with patch.dict(os.environ, {}, clear=True), patch(
                "stock_watch.cli.local_doctor.REPO_ROOT", root
            ), patch("stock_watch.cli.local_doctor.THEME_OUTDIR", theme_outdir), patch(
                "stock_watch.cli.local_doctor.VERIFICATION_OUTDIR", verification_outdir
            ), patch("stock_watch.cli.local_doctor.DOCTOR_MD", theme_outdir / "local_doctor.md"), patch(
                "stock_watch.cli.local_doctor.DOCTOR_JSON", theme_outdir / "local_doctor.json"
            ):
                code = main(["--skip-network", "--fail-on", "warn"])

        self.assertEqual(code, 1)

    def test_main_accepts_synced_report_with_old_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            theme_outdir = root / "theme_watchlist_daily"
            verification_outdir = root / "verification" / "watchlist_daily"
            history_cache_dir = theme_outdir / "history_cache"
            theme_outdir.mkdir(parents=True, exist_ok=True)
            verification_outdir.mkdir(parents=True, exist_ok=True)
            history_cache_dir.mkdir(parents=True, exist_ok=True)

            (root / "requirements.txt").write_text("pandas\n", encoding="utf-8")
            (root / "config.json").write_text(
                json.dumps(
                    {
                        "market_filter": {},
                        "notify": {},
                        "backtest": {},
                        "group_weights": {},
                    }
                ),
                encoding="utf-8",
            )
            (root / "watchlist.csv").write_text("ticker,name\n2330.TW,台積電\n", encoding="utf-8")
            (root / "portfolio.csv.example").write_text("ticker,shares\n2330,1\n", encoding="utf-8")
            (root / "chat_id_map.csv.example").write_text("chat_id,name\n", encoding="utf-8")
            (root / "telegram_getupdates_url.example").write_text("https://example.com\n", encoding="utf-8")
            (theme_outdir / "daily_rank.csv").write_text(
                "ticker,spec_risk_score,spec_risk_label,rank\n2330.TW,0,正常,1\n",
                encoding="utf-8",
            )
            (theme_outdir / "daily_report.md").write_text("# synced report\n", encoding="utf-8")
            (theme_outdir / "runtime_metrics.json").write_text(json.dumps({"wall_seconds": 1.1}), encoding="utf-8")
            stale_ts = (theme_outdir / "daily_rank.csv").stat().st_mtime - 10
            os.utime(theme_outdir / "runtime_metrics.json", (stale_ts, stale_ts))
            (theme_outdir / "portfolio_runtime_metrics.json").write_text(json.dumps({"wall_seconds": 0.7}), encoding="utf-8")
            (verification_outdir / "runtime_metrics.json").write_text(json.dumps({"wall_seconds": 2.2}), encoding="utf-8")
            (verification_outdir / "reco_snapshots.csv").write_text(
                "signal_date,watch_type,ticker\n2026-04-27,short,2330.TW\n",
                encoding="utf-8",
            )
            (verification_outdir / "reco_outcomes.csv").write_text(
                "signal_date,horizon_days,watch_type,ticker,status\n2026-04-27,1,short,2330.TW,insufficient_forward_data\n",
                encoding="utf-8",
            )

            with patch.dict(os.environ, {}, clear=True), patch(
                "stock_watch.cli.local_doctor.REPO_ROOT", root
            ), patch("stock_watch.cli.local_doctor.THEME_OUTDIR", theme_outdir), patch(
                "stock_watch.cli.local_doctor.VERIFICATION_OUTDIR", verification_outdir
            ), patch("stock_watch.cli.local_doctor.DOCTOR_MD", theme_outdir / "local_doctor.md"), patch(
                "stock_watch.cli.local_doctor.DOCTOR_JSON", theme_outdir / "local_doctor.json"
            ):
                code = main(["--skip-network"])

            payload = json.loads((theme_outdir / "local_doctor.json").read_text(encoding="utf-8"))
            freshness_check = next(
                check for check in payload["checks"] if check["name"] == "watchlist_artifact_freshness"
            )

        self.assertEqual(code, 0)
        self.assertEqual(payload["metrics"]["watchlist_artifact_freshness_status"], "report_current_runtime_stale")
        self.assertEqual(freshness_check["status"], "ok")

    def test_main_returns_one_for_fail_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            theme_outdir = root / "theme_watchlist_daily"
            verification_outdir = root / "verification" / "watchlist_daily"
            theme_outdir.mkdir(parents=True, exist_ok=True)
            verification_outdir.mkdir(parents=True, exist_ok=True)

            (root / "requirements.txt").write_text("pandas\n", encoding="utf-8")
            (root / "portfolio.csv.example").write_text("ticker,shares\n2330,1\n", encoding="utf-8")
            (root / "chat_id_map.csv.example").write_text("chat_id,name\n", encoding="utf-8")
            (root / "telegram_getupdates_url.example").write_text("https://example.com\n", encoding="utf-8")

            with patch.dict(os.environ, {}, clear=True), patch(
                "stock_watch.cli.local_doctor.REPO_ROOT", root
            ), patch("stock_watch.cli.local_doctor.THEME_OUTDIR", theme_outdir), patch(
                "stock_watch.cli.local_doctor.VERIFICATION_OUTDIR", verification_outdir
            ), patch("stock_watch.cli.local_doctor.DOCTOR_MD", theme_outdir / "local_doctor.md"), patch(
                "stock_watch.cli.local_doctor.DOCTOR_JSON", theme_outdir / "local_doctor.json"
            ):
                code = main(["--skip-network"])

            payload = json.loads((theme_outdir / "local_doctor.json").read_text(encoding="utf-8"))

        self.assertEqual(code, 1)
        self.assertEqual(payload["overall"], "fail")
