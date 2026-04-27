from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime
from datetime import timedelta
from pathlib import Path

from stock_watch.cli.local_housekeeping import apply_housekeeping_actions
from stock_watch.cli.local_housekeeping import collect_housekeeping_actions
from stock_watch.cli.local_housekeeping import HousekeepingAction
from stock_watch.cli.local_housekeeping import main


class RunLocalHousekeepingTests(unittest.TestCase):
    def test_collect_housekeeping_actions_keeps_latest_and_marks_older_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            theme_outdir = Path(tmpdir) / "theme_watchlist_daily"
            verification_outdir = Path(tmpdir) / "verification" / "watchlist_daily"
            contexts_dir = verification_outdir / "contexts"
            backfill_dir = verification_outdir / "backfill_reports"
            cache_dir = verification_outdir / "yfinance_cache"
            history_cache_dir = theme_outdir / "history_cache"
            contexts_dir.mkdir(parents=True, exist_ok=True)
            backfill_dir.mkdir(parents=True, exist_ok=True)
            cache_dir.mkdir(parents=True, exist_ok=True)
            history_cache_dir.mkdir(parents=True, exist_ok=True)

            now = datetime(2026, 4, 23, 12, 0, 0)
            for index in range(3):
                path = contexts_dir / f"context_{index}.json"
                path.write_text("{}", encoding="utf-8")
                stamp = (now - timedelta(days=index)).timestamp()
                os.utime(path, (stamp, stamp))

            for index in range(2):
                path = backfill_dir / f"report_{index}.md"
                path.write_text("# ok\n", encoding="utf-8")
                stamp = (now - timedelta(days=index)).timestamp()
                os.utime(path, (stamp, stamp))

            backup_new = verification_outdir / "reco_snapshots.csv.bak.20260423"
            backup_old = verification_outdir / "reco_snapshots.csv.bak.20260420"
            backup_new.write_text("x\n", encoding="utf-8")
            backup_old.write_text("x\n", encoding="utf-8")

            os.utime(backup_new, (now.timestamp(), now.timestamp()))
            os.utime(backup_old, ((now - timedelta(days=3)).timestamp(), (now - timedelta(days=3)).timestamp()))

            stale_cache = cache_dir / "old.csv"
            fresh_cache = cache_dir / "fresh.csv"
            stale_cache.write_text("x\n", encoding="utf-8")
            fresh_cache.write_text("x\n", encoding="utf-8")
            os.utime(stale_cache, ((now - timedelta(days=20)).timestamp(), (now - timedelta(days=20)).timestamp()))
            os.utime(fresh_cache, ((now - timedelta(days=1)).timestamp(), (now - timedelta(days=1)).timestamp()))

            stale_history_cache = history_cache_dir / "2330_TW__5y.csv"
            fresh_history_cache = history_cache_dir / "2454_TW__5y.csv"
            stale_history_cache.write_text("x\n", encoding="utf-8")
            fresh_history_cache.write_text("x\n", encoding="utf-8")
            os.utime(
                stale_history_cache,
                ((now - timedelta(days=40)).timestamp(), (now - timedelta(days=40)).timestamp()),
            )
            os.utime(
                fresh_history_cache,
                ((now - timedelta(days=2)).timestamp(), (now - timedelta(days=2)).timestamp()),
            )

            actions = collect_housekeeping_actions(
                theme_outdir=theme_outdir,
                verification_outdir=verification_outdir,
                keep_contexts=2,
                keep_backfill_reports=1,
                keep_backups=1,
                cache_max_age_days=14,
                history_cache_max_age_days=30,
                now=now,
            )

        statuses = {(Path(action.path).name, action.status, action.category) for action in actions}
        self.assertIn(("context_2.json", "planned", "contexts"), statuses)
        self.assertIn(("report_1.md", "planned", "backfill_reports"), statuses)
        self.assertIn(("reco_snapshots.csv.bak.20260420", "planned", "csv_backups"), statuses)
        self.assertIn(("old.csv", "planned", "verification_cache"), statuses)
        self.assertIn(("fresh.csv", "kept", "verification_cache"), statuses)
        self.assertIn(("2330_TW__5y.csv", "planned", "history_cache"), statuses)
        self.assertIn(("2454_TW__5y.csv", "kept", "history_cache"), statuses)

    def test_apply_housekeeping_actions_deletes_planned_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "old.csv"
            path.write_text("x\n", encoding="utf-8")
            actions = [
                HousekeepingAction(
                    category="verification_cache",
                    path=str(path),
                    action="delete",
                    status="planned",
                    detail="stale",
                    size_bytes=2,
                )
            ]

            applied = apply_housekeeping_actions(actions, apply=True)
            self.assertFalse(path.exists())

        self.assertEqual(applied[0].status, "deleted")

    def test_main_writes_dry_run_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            verification_outdir = root / "verification" / "watchlist_daily"
            contexts_dir = verification_outdir / "contexts"
            contexts_dir.mkdir(parents=True, exist_ok=True)
            context = contexts_dir / "context.json"
            context.write_text("{}", encoding="utf-8")

            out_md = root / "theme_watchlist_daily" / "local_housekeeping.md"
            out_json = root / "theme_watchlist_daily" / "local_housekeeping.json"

            code = main(
                [
                    "--theme-outdir",
                    str(root / "theme_watchlist_daily"),
                    "--verification-outdir",
                    str(verification_outdir),
                    "--keep-contexts",
                    "0",
                    "--out",
                    str(out_md),
                    "--json-out",
                    str(out_json),
                ]
            )

            payload = json.loads(out_json.read_text(encoding="utf-8"))

        self.assertEqual(code, 0)
        self.assertEqual(payload["summary"]["mode"], "dry-run")
        self.assertEqual(payload["summary"]["planned_delete_count"], 1)
