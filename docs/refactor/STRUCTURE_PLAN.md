# Stock Watch Folder Structure Plan

This plan reorganizes the repository gradually without breaking the current local workflow.

## Why this exists

The repo now mixes several concerns at the root:

- runnable CLI entrypoints such as `run_local_daily.py`
- strategy/orchestration scripts such as `daily_theme_watchlist.py`
- package code under `stock_watch/`
- verification tools under `verification/`
- local/generated outputs under `runs/theme_watchlist_daily/` and `runs/verification/watchlist_daily/`
- runbooks, handoffs, and research notes as root-level Markdown files
- local private files such as `portfolio.csv`, `chat_ids`, and API/token helper files

The goal is not to make the tree look pretty. The goal is to make daily operation, testing, and future changes safer.

## Guardrails

- Always keep `git pull --ff-only` as the first step before workflow analysis or code changes.
- Keep current CLI commands working while files move.
- Move low-risk docs first, then generated artifacts, then implementation internals.
- Keep generated output roots configurable through `stock_watch/paths.py`.
- Do not change strategy behavior while doing structure-only refactors.
- Run tests after each phase.

## Target shape

```text
docs/
  runbooks/
  research/
  handoff/
  refactor/

tools/
  run_local_daily.py
  run_local_doctor.py
  run_local_housekeeping.py
  run_local_website.py
  run_weekly_review.py
  draft_watchlist_additions.py
  augment_low_price_watchlist.py
  update_chat_id_map.py

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

data/
  config/
  examples/

tests/
  unit/
  integration/
  verification/
```

This is a final direction, not a one-shot migration.

## Phase 0 — Inventory and policy

Status: completed.

Tasks:

- Add this plan.
- Keep a clear migration map before moving files.
- Mark which paths are stable public/local workflow interfaces.

Stable interfaces for now:

- `run_local_daily.py`
- `run_local_doctor.py`
- `run_local_housekeeping.py`
- `run_local_website.py`
- `run_weekly_review.py`
- `daily_theme_watchlist.py`
- `portfolio_check.py`
- `verification/run_daily_verification.py`
- `runs/theme_watchlist_daily/`
- `runs/verification/watchlist_daily/`
- `LOCAL_RUNBOOK.md`

Acceptance:

- No file moves yet.
- The next phase can be done with minimal risk.

## Phase 1 — Move documentation only

Status: completed.

This is the first safe implementation phase.

Proposed moves:

```text
LOCAL_RUNBOOK.md                  -> docs/runbooks/LOCAL_RUNBOOK.md
SIGNAL_GLOSSARY.md                -> docs/runbooks/SIGNAL_GLOSSARY.md
PUBLIC_REPO_SCOUTING.md           -> docs/research/PUBLIC_REPO_SCOUTING.md
ADAPTIVE_ENGINE_PLAN.md           -> docs/research/ADAPTIVE_ENGINE_PLAN.md
CODEX_HANDOFF.md                  -> docs/handoff/CODEX_HANDOFF.md
CODEX_NOTES.md                    -> docs/handoff/CODEX_NOTES.md
GEMINI.md                         -> docs/handoff/GEMINI.md
GEMINI_HANDOFF.md                 -> docs/handoff/GEMINI_HANDOFF.md
GEMINI_UPDATES_2026_04_22.md      -> docs/handoff/GEMINI_UPDATES_2026_04_22.md
STOCK_WATCH_REFACTOR_TODO.md      -> docs/refactor/STOCK_WATCH_REFACTOR_TODO.md
TESTV_INTEGRATION_CHECKLIST.md    -> docs/refactor/TESTV_INTEGRATION_CHECKLIST.md
STRUCTURE_PLAN.md                 -> docs/refactor/STRUCTURE_PLAN.md
```

Compatibility:

- Keep small root `LOCAL_RUNBOOK.md` and `STRUCTURE_PLAN.md` pointer files for discoverability.
- Keep `README.md` at root.

Tests:

- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3.11 -m pytest tests/test_run_local_website.py -q`
- Optionally full `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3.11 -m pytest -q`.

## Phase 2 — Introduce CLI wrappers

Status: completed.

Move implementation into package modules while keeping root commands.

Target:

```text
stock_watch/cli/local_daily.py
stock_watch/cli/local_doctor.py
stock_watch/cli/local_housekeeping.py
stock_watch/cli/local_website.py
stock_watch/cli/weekly_review.py
```

Root files stay as wrappers:

```python
from stock_watch.cli.local_daily import main

if __name__ == "__main__":
    raise SystemExit(main())
```

Do this only after Phase 1 is stable.

Progress:

- `run_local_website.py` now delegates to `stock_watch/cli/local_website.py`.
- `run_local_doctor.py` now delegates to `stock_watch/cli/local_doctor.py`.
- `run_weekly_review.py` now delegates to `stock_watch/cli/weekly_review.py`.
- `run_local_housekeeping.py` now delegates to `stock_watch/cli/local_housekeeping.py`.
- `run_local_daily.py` now delegates to `stock_watch/cli/local_daily.py`.

Acceptance:

- Existing commands still work:
  - `python3.11 run_local_daily.py --mode postclose`
  - `python3.11 run_local_doctor.py --skip-network`
  - `python3.11 run_local_website.py`
- Tests still import either old wrapper names or new package modules deliberately.

## Phase 3 — Consolidate generated outputs behind path config

Status: completed.

Current output roots are deeply coupled:

- `runs/theme_watchlist_daily/`
- `runs/verification/watchlist_daily/`

Before moving them, add one config module that owns these paths:

```text
stock_watch/paths.py
```

It should define defaults and allow environment overrides:

- `STOCK_WATCH_THEME_OUTDIR`
- `STOCK_WATCH_VERIFICATION_OUTDIR`
- `STOCK_WATCH_SITE_OUTDIR`

Implemented move:

```text
theme_watchlist_daily/              -> runs/theme_watchlist_daily/
verification/watchlist_daily/       -> runs/verification/watchlist_daily/
```

Compatibility:

- Root commands stay unchanged.
- Override paths with `STOCK_WATCH_THEME_OUTDIR`, `STOCK_WATCH_VERIFICATION_OUTDIR`, and `STOCK_WATCH_SITE_OUTDIR` when testing alternate output locations.

Progress:

- Added `stock_watch/paths.py` with current defaults plus `STOCK_WATCH_THEME_OUTDIR`, `STOCK_WATCH_VERIFICATION_OUTDIR`, and `STOCK_WATCH_SITE_OUTDIR`.
- Wired local CLI wrappers and verification CLI defaults to the shared path module.
- Moved generated output directories to `runs/theme_watchlist_daily/` and `runs/verification/watchlist_daily/`.
- Updated runbook and verification docs to point to `runs/`.

## Phase 4 — Clean root scripts

Status: completed.

After CLI wrappers and path config are stable:

- Move helper scripts to `tools/`.
- Keep root wrappers only for commands used by runbooks or automation.
- Remove stale `__pycache__/` and ensure ignored generated folders stay ignored.

Candidates:

```text
augment_low_price_watchlist.py
draft_watchlist_additions.py
backtest_runner.py
update_chat_id_map.py
```

Progress:

- `augment_low_price_watchlist.py`, `draft_watchlist_additions.py`, `backtest_runner.py`, and `update_chat_id_map.py` now delegate to implementations under `tools/`.

## Phase 5 — Verification package cleanup

Status: completed.

The current `verification/` folder is useful but half CLI, half library.

Target:

```text
verification/
  cli/
  reports/
  workflows/
```

Keep `verification/run_daily_verification.py` as a stable wrapper until the runbook changes.

Progress:

- Root `verification/*.py` files remain compatibility wrappers for runbook commands.
- `verification/cli/` files are thin module wrappers for `python3.11 -m verification.cli...`.
- Report/snapshot implementations moved under `verification/reports/`.
- Orchestration and data-update implementations moved under `verification/workflows/`.
- Compatibility wrappers add the repository root to `sys.path`, so both
  `python3.11 verification/run_daily_verification.py ...` and
  `python3.11 -m verification.cli.run_daily_verification ...` work.
- Tests cover the moved verification implementations through package paths.

## Do not move yet

These are too coupled to move immediately:

- `daily_theme_watchlist.py`
- `portfolio_check.py`
- `runs/theme_watchlist_daily/`
- `runs/verification/watchlist_daily/`
- `.github/workflows/*.yml`

Reason:

- They are referenced by local runbook, tests, generated reports, and existing automation.
- Moving them too early will create noise and make it hard to distinguish path bugs from strategy bugs.

## Recommended next action

Pause structural moves and return to product/data work:

1. Keep using stable wrapper commands in the local runbook.
2. Let the new `stock_watch/cli/`, `tools/`, `verification/reports/`, and
   `verification/workflows/` layout settle.
3. Only move `daily_theme_watchlist.py` or `portfolio_check.py` when we are ready
   to split the core ranking engine itself.
