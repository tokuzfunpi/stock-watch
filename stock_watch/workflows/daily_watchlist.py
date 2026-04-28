from __future__ import annotations

import sys

from stock_watch.paths import REPO_ROOT


def _load_legacy_daily_workflow():
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    import daily_theme_watchlist

    return daily_theme_watchlist


def run_daily_watchlist(*, force_run: bool = False) -> int:
    daily_theme_watchlist = _load_legacy_daily_workflow()
    return daily_theme_watchlist.main(force_run=force_run)
