from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from stock_watch.cli import report_sync


class ReportSyncTests(unittest.TestCase):
    def test_main_rebuilds_reports_from_latest_daily_rank(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            outdir = root / "runs"
            outdir.mkdir(parents=True, exist_ok=True)
            rank_csv = outdir / "daily_rank.csv"
            report_md = outdir / "daily_report.md"
            report_html = outdir / "daily_report.html"
            pd.DataFrame([{"ticker": "2330.TW", "name": "台積電", "rank": 1}]).to_csv(rank_csv, index=False)
            pd.DataFrame([{"horizon": 1, "trades": 10}]).to_csv(outdir / "backtest_summary_steady.csv", index=False)
            pd.DataFrame([{"horizon": 5, "trades": 8}]).to_csv(outdir / "backtest_summary_attack.csv", index=False)

            captured: dict[str, object] = {}

            def _save_reports(df_rank, market_regime, bt_steady, bt_attack, us_market=None) -> None:
                captured["rows"] = len(df_rank)
                captured["market_comment"] = market_regime["comment"]
                captured["steady_rows"] = 0 if bt_steady is None else len(bt_steady)
                captured["attack_rows"] = 0 if bt_attack is None else len(bt_attack)
                captured["us_summary"] = us_market["summary"]
                report_md.write_text("rebuilt", encoding="utf-8")
                report_html.write_text("<p>rebuilt</p>", encoding="utf-8")

            fake_workflow = SimpleNamespace(
                RANK_CSV=rank_csv,
                REPORT_MD=report_md,
                REPORT_HTML=report_html,
                OUTDIR=outdir,
                logger=SimpleNamespace(exception=lambda *args, **kwargs: None),
                get_market_regime=lambda: {"comment": "market ok", "is_bullish": True},
                get_us_market_reference=lambda: {"summary": "us ok", "rows": []},
                save_reports=_save_reports,
            )

            with patch.object(report_sync, "_load_legacy_daily_workflow", return_value=fake_workflow):
                code = report_sync.main([])
            metrics_json_exists = (outdir / "report_sync_metrics.json").exists()
            metrics_md_exists = (outdir / "report_sync_metrics.md").exists()

        self.assertEqual(code, 0)
        self.assertEqual(captured["rows"], 1)
        self.assertEqual(captured["market_comment"], "market ok")
        self.assertEqual(captured["steady_rows"], 1)
        self.assertEqual(captured["attack_rows"], 1)
        self.assertEqual(captured["us_summary"], "us ok")
        self.assertTrue(metrics_json_exists)
        self.assertTrue(metrics_md_exists)

    def test_main_returns_one_when_daily_rank_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            outdir = root / "runs"
            outdir.mkdir(parents=True, exist_ok=True)
            fake_workflow = SimpleNamespace(
                RANK_CSV=outdir / "daily_rank.csv",
                REPORT_MD=outdir / "daily_report.md",
                REPORT_HTML=outdir / "daily_report.html",
                OUTDIR=outdir,
                logger=SimpleNamespace(exception=lambda *args, **kwargs: None),
                get_market_regime=lambda: {"comment": "market ok", "is_bullish": True},
                get_us_market_reference=lambda: {"summary": "us ok", "rows": []},
                save_reports=lambda *args, **kwargs: None,
            )

            with patch.object(report_sync, "_load_legacy_daily_workflow", return_value=fake_workflow):
                code = report_sync.main([])

        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
