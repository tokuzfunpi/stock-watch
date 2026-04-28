# Stock Watch Folder Structure Plan

This plan tracks the repository structure after the single-CLI consolidation.

## Current policy

- Operational commands use one public CLI: `python -m stock_watch ...`.
- Generated output roots stay configurable through `stock_watch/paths.py`.
- Root-level workflow wrappers are no longer part of the public interface.
- Historical handoff notes can mention old files, but runbooks and workflows should not.
- Strategy behavior should not change during structure-only refactors.

## Current shape

```text
docs/
  runbooks/
  research/
  handoff/
  refactor/

stock_watch/
  cli/
  workflows/
  data/
  signals/
  ranking/
  reports/
  state/
  backtesting/

verification/
  cli/
  reports/
  workflows/

runs/
  theme_watchlist_daily/
  verification/watchlist_daily/

tools/
tests/
```

## Stable command interface

Use:

- `python -m stock_watch preopen`
- `python -m stock_watch postclose`
- `python -m stock_watch full`
- `python -m stock_watch portfolio`
- `python -m stock_watch daily --mode <preopen|postclose|full|portfolio>`
- `python -m stock_watch weekly`
- `python -m stock_watch doctor`
- `python -m stock_watch housekeeping`
- `python -m stock_watch website`
- `python -m stock_watch verification <daily|snapshot|evaluate|summary|feedback|backfill>`

Removed compatibility wrappers:

- `run_local_daily.py`
- `run_local_doctor.py`
- `run_local_housekeeping.py`
- `run_local_website.py`
- `run_weekly_review.py`
- `portfolio_check.py`
- root `verification/*.py` command wrappers

## Output roots

Path defaults live in `stock_watch/paths.py`:

- `STOCK_WATCH_THEME_OUTDIR` → `runs/theme_watchlist_daily/`
- `STOCK_WATCH_VERIFICATION_OUTDIR` → `runs/verification/watchlist_daily/`
- `STOCK_WATCH_SITE_OUTDIR` → `runs/theme_watchlist_daily/local_site/`

## Duplicate cleanup status

Completed:

- Added `stock_watch/cli/main.py` and `stock_watch/__main__.py`.
- Removed root local workflow wrappers.
- Removed root verification command wrappers.
- Removed `portfolio_check.py` and moved portfolio-only execution into `stock_watch.cli.local_daily`.
- Moved legacy watchlist/portfolio calls behind `stock_watch/workflows/` adapters so the daily CLI no longer imports `daily_theme_watchlist.py` directly.
- Updated GitHub Actions and runbooks to use `python -m stock_watch`.
- Stopped local website generation from copying artifact files into root compatibility paths.

Still intentionally present:

- `daily_theme_watchlist.py`: still owns coupled watchlist orchestration and legacy helper globals.
- `verification/cli/*.py`: retained as subcommand adapters for `stock_watch.cli.main`.
- `runs/`: retained as local state/report/cache root; individual files are classified in `DUPLICATE_CLEANUP_PLAN.md`.

## Next structural target

The next safe migration is not more wrapper deletion; it is splitting `daily_theme_watchlist.py`:

1. Replace the `stock_watch/workflows/` legacy adapters with native workflow orchestration.
2. Move remaining report/state/helper logic into package modules.
3. Keep `python -m stock_watch daily` calling package workflows directly.
4. Delete or shrink `daily_theme_watchlist.py` after parity tests pass.
