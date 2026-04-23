from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from run_local_doctor import DoctorCheck
from run_local_doctor import main
from run_local_doctor import overall_status
from run_local_doctor import write_doctor_outputs


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
        metrics = {"daily_rank_rows": 1, "alert_tracking_rows": 2, "snapshot_rows": 3, "outcome_rows": 4}

        with tempfile.TemporaryDirectory() as tmpdir:
            output_md = Path(tmpdir) / "local_doctor.md"
            output_json = Path(tmpdir) / "local_doctor.json"
            write_doctor_outputs(checks=checks, overall="ok", metrics=metrics, output_md=output_md, output_json=output_json)

            markdown = output_md.read_text(encoding="utf-8")
            payload = json.loads(output_json.read_text(encoding="utf-8"))

        self.assertIn("Local Doctor", markdown)
        self.assertIn("python_runtime", markdown)
        self.assertEqual(payload["overall"], "ok")
        self.assertEqual(payload["checks"][0]["status"], "ok")

    def test_main_returns_zero_for_warn_only_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            theme_outdir = root / "theme_watchlist_daily"
            verification_outdir = root / "verification" / "watchlist_daily"
            theme_outdir.mkdir(parents=True, exist_ok=True)
            verification_outdir.mkdir(parents=True, exist_ok=True)

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

            with patch("run_local_doctor.REPO_ROOT", root), patch("run_local_doctor.THEME_OUTDIR", theme_outdir), patch(
                "run_local_doctor.VERIFICATION_OUTDIR", verification_outdir
            ), patch("run_local_doctor.DOCTOR_MD", theme_outdir / "local_doctor.md"), patch(
                "run_local_doctor.DOCTOR_JSON", theme_outdir / "local_doctor.json"
            ):
                code = main(["--skip-network"])

            payload = json.loads((theme_outdir / "local_doctor.json").read_text(encoding="utf-8"))

        self.assertEqual(code, 0)
        self.assertEqual(payload["overall"], "warn")

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

            with patch("run_local_doctor.REPO_ROOT", root), patch("run_local_doctor.THEME_OUTDIR", theme_outdir), patch(
                "run_local_doctor.VERIFICATION_OUTDIR", verification_outdir
            ), patch("run_local_doctor.DOCTOR_MD", theme_outdir / "local_doctor.md"), patch(
                "run_local_doctor.DOCTOR_JSON", theme_outdir / "local_doctor.json"
            ):
                code = main(["--skip-network"])

            payload = json.loads((theme_outdir / "local_doctor.json").read_text(encoding="utf-8"))

        self.assertEqual(code, 1)
        self.assertEqual(payload["overall"], "fail")
