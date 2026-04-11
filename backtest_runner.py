from __future__ import annotations

from pathlib import Path
import json
import os

import pandas as pd

from daily_theme_watchlist_pro_max import (
    CONFIG_PATH,
    OUTDIR,
    run_backtest_snapshot,
)

if __name__ == "__main__":
    result = run_backtest_snapshot()
    if result is None or result.empty:
        print("No backtest results.")
    else:
        print(result.to_string(index=False))
        print(f"Saved to: {OUTDIR / 'backtest_summary.csv'}")
