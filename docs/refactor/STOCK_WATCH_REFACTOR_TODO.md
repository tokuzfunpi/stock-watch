# Stock Watch Refactor TODO

This document is the forward-looking refactor queue after single CLI consolidation.

## Already done

- Single public CLI: `python -m stock_watch ...`.
- Root workflow wrappers removed.
- Root verification wrappers removed.
- `portfolio_check.py` removed.
- Portfolio-only workflow runs through `stock_watch.cli.local_daily.run_portfolio_step()`.
- `stock_watch.cli.local_daily` delegates watchlist and portfolio work to `stock_watch/workflows/` instead of importing `daily_theme_watchlist.py` directly.
- Daily watchlist top-level orchestration lives in `stock_watch/workflows/daily_watchlist.py`; `daily_theme_watchlist.main()` is now only a compatibility shim.
- Shared runtime constants/logger live in `stock_watch/runtime.py`; weekly/verification no longer import the legacy daily module for `LOCAL_TZ`, `ALERT_TRACK_CSV`, or logger.
- GitHub Actions and runbooks point at the single CLI.
- Local website no longer writes root compatibility artifact copies.

## Current constraints

- `daily_theme_watchlist.py` still owns several strategy/report/state implementation details and workflow helper functions.
- Verification is already split into `verification/cli/`, `verification/reports/`, and `verification/workflows/`; do not fold it back into the daily script.
- `runs/theme_watchlist_daily/daily_rank.csv`, `runs/verification/watchlist_daily/reco_snapshots.csv`, and `runs/verification/watchlist_daily/reco_outcomes.csv` are canonical local state, not disposable duplicates.
- Cache/log/report files under `runs/` should be handled by regeneration or housekeeping, not ad-hoc deletion.

## Next phases

### Phase 1: Extract workflow dependencies

Objective: make `stock_watch/workflows/daily_watchlist.py` depend on package modules instead of `daily_theme_watchlist.py` helpers/globals.

Tasks:

- Move strategy/report/state/helper dependencies used by `stock_watch/workflows/daily_watchlist.py` into package modules.
- Keep output files and schemas identical.
- Keep `daily_theme_watchlist.py` importable only as a temporary legacy helper holder.

Acceptance:

- `python -m stock_watch preopen` produces the same key artifacts.
- Existing tests pass.
- No generated `runs/` files are committed as part of the refactor.

### Phase 2: Extract remaining pure strategy logic

Objective: move deterministic logic out of `daily_theme_watchlist.py` without changing behavior.

Tasks:

- Continue moving signal detection into `stock_watch/signals/`.
- Continue moving scoring/ranking into `stock_watch/ranking/`.
- Keep returned fields and column names stable.

Acceptance:

- Fixed fixture outputs stay materially unchanged.
- Focused tests cover signal/scoring helpers.

### Phase 3: Separate side effects

Objective: keep reporting, Telegram, state, and cache effects behind clearer boundaries.

Tasks:

- Move report rendering into `stock_watch/reports/`.
- Move alert tracking and last-run state into `stock_watch/state/`.
- Separate Telegram message building from Telegram sending.

Acceptance:

- The daily workflow reads like a pipeline.
- Report and notification wording can change without touching score logic.

### Phase 4: Data provider abstraction

Objective: reduce Yahoo-only fragility.

Tasks:

- Define provider interfaces for OHLCV, market index, and US reference lookup.
- Wrap current Yahoo behavior first.
- Add FinMind fallback for Taiwan daily data.

Acceptance:

- Provider choice is configurable.
- Best-effort runs surface provider failures clearly.

### Phase 5: Optional universe filters

Objective: improve input quality before ranking without forcing a fundamental overlay.

Tasks:

- Add optional liquidity/revenue/quality filters.
- Preserve manual `watchlist.csv` as first-class input.
- Label recommendation source mode in verification.

## Definition of done

- Daily operation uses only `python -m stock_watch ...`.
- `daily_theme_watchlist.py` is either removed or reduced to a thin compatibility module.
- Strategy logic lives in small tested modules.
- Reports and notifications are generated from explicit data structures.
- Verification keeps running without bespoke patches after structural changes.
