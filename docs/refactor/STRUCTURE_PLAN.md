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
- Moved daily watchlist top-level orchestration into `stock_watch/workflows/daily_watchlist.py`; `daily_theme_watchlist.main()` is now a compatibility shim.
- Moved shared runtime constants/logger into `stock_watch/runtime.py` so weekly and verification modules do not import the legacy daily module for path/time/logger globals.
- Moved daily run-state helpers into `stock_watch/state/run_state.py` and daily runtime metrics into `stock_watch/workflows/runtime_metrics.py`.
- Moved market/session/runtime-context helpers into `stock_watch/workflows/market_context.py`.
- Moved market scenario classification and scenario-adjusted strategy preview into `stock_watch/strategy/scenario.py`.
- Moved candidate ranking pools and short/midlong action labels into `stock_watch/strategy/candidates.py`.
- Updated GitHub Actions and runbooks to use `python -m stock_watch`.
- Stopped local website generation from copying artifact files into root compatibility paths.

Still intentionally present:

- `daily_theme_watchlist.py`: still owns legacy helper globals and much of the strategy/report implementation.
- `stock_watch/runtime.py`: owns shared runtime constants/logger used across daily, weekly, and verification workflows.
- `stock_watch/state/run_state.py`: owns last-state, success-signature, and rank-state helpers.
- `stock_watch/strategy/scenario.py`: owns market scenario classification and scenario-adjusted strategy preview.
- `stock_watch/strategy/candidates.py`: owns candidate ranking pools and short/midlong action labels.
- `stock_watch/workflows/market_context.py`: owns history freshness dates, market session phase, and schedule-delay context helpers.
- `verification/cli/*.py`: retained as subcommand adapters for `stock_watch.cli.main`.
- `runs/`: retained as local state/report/cache root; individual files are classified in `DUPLICATE_CLEANUP_PLAN.md`.

## Next structural target

The next safe migration is not more wrapper deletion; it is splitting `daily_theme_watchlist.py`:

1. Move remaining candidate cap/feedback selection and message/report helper logic into package modules.
2. Make `stock_watch/workflows/daily_watchlist.py` depend on package modules instead of legacy globals.
3. Keep `python -m stock_watch daily` calling package workflows directly.
4. Delete or shrink `daily_theme_watchlist.py` after parity tests pass.
