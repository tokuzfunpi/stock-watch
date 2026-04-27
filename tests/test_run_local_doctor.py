from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from stock_watch.cli.local_doctor import DoctorCheck
from stock_watch.cli.local_doctor import _check_verification_health
from stock_watch.cli.local_doctor import main
from stock_watch.cli.local_doctor import overall_status
from stock_watch.cli.local_doctor import write_doctor_outputs


class RunLocalDoctorTests(unittest.TestCase):
    def test_overall_status_prioritizes_fail_then_warn(self) -> None:
        self.assertEqual(overall_status([DoctorCheck("a", "ok", "ok")]), "ok")
        self.assertEqual(overall_status([DoctorCheck("a", "warn", "warn")]), "warn")
        self.assertEqual(
            overall_status([DoctorCheck("a", "warn", "warn"), DoctorCheck("b", "fail", "fail")]),
            "fail",
        )

    def test_write_doctor_outputs_writes_markdown_and_json(self) -> None:
        checks = [DoctorCheck(name="python_runtime", status="ok", detail="Python 3.11")]
        metrics = {
            "daily_rank_rows": 1,
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
            write_doctor_outputs(checks=checks, overall="ok", metrics=metrics, output_md=output_md, output_json=output_json)

            markdown = output_md.read_text(encoding="utf-8")
            payload = json.loads(output_json.read_text(encoding="utf-8"))

        self.assertIn("Local Doctor", markdown)
        self.assertIn("python_runtime", markdown)
        self.assertIn("History cache files", markdown)
        self.assertIn("Spec risk high rows", markdown)
        self.assertIn("3057.TW", markdown)
        self.assertIn("Verification runtime seconds", markdown)
        self.assertIn("Verification gate status", markdown)
        self.assertIn("Verification duplicate keys", markdown)
        self.assertEqual(payload["overall"], "ok")
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

            with patch("stock_watch.cli.local_doctor.REPO_ROOT", root), patch(
                "stock_watch.cli.local_doctor.THEME_OUTDIR", theme_outdir
            ), patch("stock_watch.cli.local_doctor.VERIFICATION_OUTDIR", verification_outdir), patch(
                "stock_watch.cli.local_doctor.DOCTOR_MD", theme_outdir / "local_doctor.md"
            ), patch(
                "stock_watch.cli.local_doctor.DOCTOR_JSON", theme_outdir / "local_doctor.json"
            ):
                code = main(["--skip-network"])

            payload = json.loads((theme_outdir / "local_doctor.json").read_text(encoding="utf-8"))
            check_names = {check["name"] for check in payload["checks"]}

        self.assertEqual(code, 0)
        self.assertEqual(payload["overall"], "warn")
        self.assertIn("history_cache_dir", check_names)
        self.assertEqual(payload["metrics"]["history_cache_files"], 1)
        self.assertGreater(payload["metrics"]["history_cache_bytes"], 0)
        self.assertEqual(payload["metrics"]["spec_risk_high_rows"], 1)
        self.assertEqual(payload["metrics"]["spec_risk_watch_rows"], 1)
        self.assertEqual(payload["metrics"]["spec_risk_top_tickers"], ["3057.TW", "6669.TW"])
        self.assertEqual(payload["metrics"]["verification_runtime_seconds"], 2.2)
        self.assertEqual(payload["metrics"]["verification_gate_status"], "ok")
        self.assertEqual(payload["metrics"]["outcome_pending_rows"], 1)
        self.assertIn("verification_health", check_names)

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

            with patch("stock_watch.cli.local_doctor.REPO_ROOT", root), patch(
                "stock_watch.cli.local_doctor.THEME_OUTDIR", theme_outdir
            ), patch("stock_watch.cli.local_doctor.VERIFICATION_OUTDIR", verification_outdir), patch(
                "stock_watch.cli.local_doctor.DOCTOR_MD", theme_outdir / "local_doctor.md"
            ), patch(
                "stock_watch.cli.local_doctor.DOCTOR_JSON", theme_outdir / "local_doctor.json"
            ):
                code = main(["--skip-network"])

            payload = json.loads((theme_outdir / "local_doctor.json").read_text(encoding="utf-8"))

        self.assertEqual(code, 1)
        self.assertEqual(payload["overall"], "fail")
