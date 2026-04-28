from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from stock_watch.cli.local_website import build_site_html
from stock_watch.cli.local_website import markdown_to_html
from stock_watch.cli.local_website import write_local_website


class RunLocalWebsiteTests(unittest.TestCase):
    def test_markdown_to_html_renders_headings_lists_and_tables(self) -> None:
        html = markdown_to_html(
            "\n".join(
                [
                    "# Title",
                    "- item `one`",
                    "",
                    "| a | b |",
                    "| --- | --- |",
                    "| 1 | 2 |",
                ]
            )
        )

        self.assertIn("<h1>Title</h1>", html)
        self.assertIn("<li>item <code>one</code></li>", html)
        self.assertIn("<table>", html)
        self.assertIn("<td>1</td>", html)

    def test_write_local_website_collects_reports_and_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            theme_outdir = root / "theme_watchlist_daily"
            verification_outdir = root / "verification" / "watchlist_daily"
            site_dir = theme_outdir / "local_site"
            theme_outdir.mkdir(parents=True)
            verification_outdir.mkdir(parents=True)

            (theme_outdir / "local_run_status.json").write_text(
                json.dumps(
                    {
                        "mode": "postclose",
                        "overall_status": "ok",
                        "metrics": {
                            "verification_gate_status": "ok",
                            "snapshot_rows": 2,
                            "outcome_rows": 6,
                            "outcome_ok_rows": 3,
                            "outcome_pending_rows": 3,
                            "latest_snapshot_signal_date": "2026-04-27",
                            "signal_date_missing_rows": 0,
                            "no_price_series_rows": 0,
                            "snapshot_dup_keys": 0,
                            "outcome_dup_keys": 0,
                        },
                    }
                ),
                encoding="utf-8",
            )
            (theme_outdir / "local_doctor.json").write_text(json.dumps({"overall": "warn"}), encoding="utf-8")
            (theme_outdir / "weekly_review.json").write_text(json.dumps({"generated_at": "2026-04-27"}), encoding="utf-8")
            (theme_outdir / "local_run_status.md").write_text("# Local Run Status\n- Overall: `ok`\n", encoding="utf-8")
            (theme_outdir / "weekly_review.md").write_text("# Weekly Review\n- Status: `ok`\n", encoding="utf-8")
            (theme_outdir / "daily_report.md").write_text("# Daily Watchlist\n", encoding="utf-8")
            (verification_outdir / "outcomes_summary.md").write_text("# Outcomes Summary\n", encoding="utf-8")
            pd.DataFrame(
                [
                    {
                        "rank": 1,
                        "ticker": "2330.TW",
                        "name": "台積電",
                        "group": "core",
                        "layer": "midlong_core",
                        "grade": "A",
                        "setup_score": 11,
                        "risk_score": 2,
                        "ret5_pct": 4.2,
                        "ret20_pct": 8.5,
                        "spec_risk_label": "正常",
                    }
                ]
            ).to_csv(theme_outdir / "daily_rank.csv", index=False)
            pd.DataFrame(
                [
                    {
                        "signal_date": "2026-04-27",
                        "horizon_days": 1,
                        "watch_type": "short",
                        "ticker": "2330.TW",
                        "name": "台積電",
                        "status": "ok",
                    }
                ]
            ).to_csv(verification_outdir / "reco_outcomes.csv", index=False)
            pd.DataFrame(
                columns=["rank", "ticker", "name", "shadow_eligible", "shadow_status"]
            ).to_csv(theme_outdir / "shadow_open_not_chase_candidates.csv", index=False)

            index_path = write_local_website(outdir=site_dir, theme_outdir=theme_outdir, verification_outdir=verification_outdir)
            content = index_path.read_text(encoding="utf-8")
            root_compat_exists = (site_dir / "daily_report.md").exists()
            copied_artifact_exists = (site_dir / "artifacts" / "daily_report.md").exists()
            review_page = site_dir / "views" / "daily_report.md.html"
            review_page_exists = review_page.exists()
            review_page_content = review_page.read_text(encoding="utf-8")
            ticker_page = site_dir / "views" / "tickers" / "2330_TW.html"
            ticker_page_exists = ticker_page.exists()
            ticker_page_content = ticker_page.read_text(encoding="utf-8")

        self.assertIn("Stock Watch Local Dashboard", content)
        self.assertIn("Verification Gate", content)
        self.assertIn("Read First", content)
        self.assertIn("資料健康度", content)
        self.assertIn("Reading Queue", content)
        self.assertIn("Rule Decisions", content)
        self.assertIn("Report Library", content)
        self.assertIn("views/tickers/2330_TW.html", content)
        self.assertIn("Daily Rank Preview", content)
        self.assertIn("2330.TW", content)
        self.assertIn("Local Run Status", content)
        self.assertIn("Outcomes Summary", content)
        self.assertIn("views/daily_report.md.html", content)
        self.assertNotIn("../daily_report.md", content)
        self.assertFalse(root_compat_exists)
        self.assertTrue(copied_artifact_exists)
        self.assertTrue(review_page_exists)
        self.assertIn("<h1>Daily Watchlist</h1>", review_page_content)
        self.assertIn('href="../artifacts/daily_report.md"', review_page_content)
        self.assertTrue(ticker_page_exists)
        self.assertIn("At a glance", ticker_page_content)
        self.assertIn("Recent verification outcomes", ticker_page_content)

    def test_build_site_html_handles_missing_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            theme_outdir = root / "theme"
            verification_outdir = root / "verification"
            outdir = root / "site"
            theme_outdir.mkdir()
            verification_outdir.mkdir()
            outdir.mkdir()

            content = build_site_html(outdir=outdir, theme_outdir=theme_outdir, verification_outdir=verification_outdir)

        self.assertIn("missing", content)
        self.assertIn("Report Library", content)
        self.assertIn("No rows available", content)


if __name__ == "__main__":
    unittest.main()
