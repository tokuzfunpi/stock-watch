from __future__ import annotations

import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import stock_watch.paths as paths


class StockWatchPathsTests(unittest.TestCase):
    def test_default_paths_stay_in_repo_compatible_locations(self) -> None:
        reloaded = importlib.reload(paths)

        self.assertEqual(reloaded.THEME_OUTDIR, reloaded.REPO_ROOT / "runs" / "theme_watchlist_daily")
        self.assertEqual(reloaded.VERIFICATION_OUTDIR, reloaded.REPO_ROOT / "runs" / "verification" / "watchlist_daily")
        self.assertEqual(reloaded.SITE_OUTDIR, reloaded.THEME_OUTDIR / "local_site")

    def test_env_overrides_paths_without_moving_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            env = {
                "STOCK_WATCH_THEME_OUTDIR": str(root / "theme"),
                "STOCK_WATCH_VERIFICATION_OUTDIR": str(root / "verification"),
                "STOCK_WATCH_SITE_OUTDIR": str(root / "site"),
            }
            with patch.dict(os.environ, env):
                reloaded = importlib.reload(paths)

            self.assertEqual(reloaded.THEME_OUTDIR, root / "theme")
            self.assertEqual(reloaded.VERIFICATION_OUTDIR, root / "verification")
            self.assertEqual(reloaded.SITE_OUTDIR, root / "site")

        importlib.reload(paths)


if __name__ == "__main__":
    unittest.main()
