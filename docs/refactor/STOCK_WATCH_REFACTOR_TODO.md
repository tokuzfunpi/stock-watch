# Stock Watch Refactor TODO

This document turns the current refactor direction into an execution plan that we can implement incrementally without breaking the existing daily workflow.

## Goals

- Keep the current `run_local_daily.py` and `daily_theme_watchlist.py` flow working during the migration.
- Make signal logic easier to reason about and test.
- Decouple data fetching from ranking so Yahoo-only failures do not dominate the daily run.
- Keep verification as a first-class workflow rather than an afterthought.

## Current Constraints

- `daily_theme_watchlist.py` is still the main orchestration entrypoint.
- `portfolio_check.py` reuses parts of the watchlist flow and should stay aligned.
- `verification/` already has meaningful independent value and should not be folded back into the main script.
- The current strategy depends on explainable signals such as `TREND`, `ACCEL`, `SURGE`, `REBREAK`, and `PULLBACK`.
- The current ranking still centers on `setup_score`, `ret5_pct`, `volume_ratio20`, `ret20_pct`, and `risk_score`.

## Migration Principles

- Prefer extract-and-wrap over rewrite-from-scratch.
- Keep old CLI entrypoints stable until the new modules are proven.
- Move pure logic first, then side effects, then data providers.
- Add or update tests each time a logic boundary moves.
- Do not widen strategy scope while refactoring structural code.

## Target Shape

Suggested module layout after the first two phases:

```text
stock_watch/
  data/
    providers/
      base.py
      yahoo.py
      finmind.py
      twstock.py
    market_context.py
  signals/
    detect.py
    glossary.py
    thresholds.py
  ranking/
    scoring.py
    candidates.py
  reports/
    daily_report.py
    telegram.py
    portfolio_report.py
  state/
    alert_tracking.py
    last_state.py
```

This does not need to happen in one pass. The first safe step is extracting modules while keeping `daily_theme_watchlist.py` as the caller.

## Phase 1: Extract Pure Strategy Logic

Objective: move deterministic logic out of `daily_theme_watchlist.py` without changing behavior.

Tasks:

- Extract indicator preparation helpers into a module such as `stock_watch/signals/detect.py`.
- Extract the core row scoring and signal detection logic around `detect_row()` into a reusable function.
- Extract ranking helpers into `stock_watch/ranking/scoring.py`.
- Keep the returned fields and names stable so downstream report and verification code does not break.

Acceptance:

- Existing watchlist output for a fixed input dataset stays materially unchanged.
- Existing tests still pass.
- New focused tests cover the extracted signal/scoring functions.

Notes:

- This is the highest-leverage phase because it reduces the blast radius for future rule changes.
- If a helper is still tightly coupled to IO, leave it in place for now and only move the pure parts.

## Phase 2: Write the Signal Glossary

Objective: make the strategy readable like a rulebook, not just executable code.

Tasks:

- Add a document describing each signal:
  - `TREND`
  - `ACCEL`
  - `SURGE`
  - `REBREAK`
  - `PULLBACK`
  - `BASE`
- For each signal, describe:
  - trigger conditions
  - what market shape it is trying to capture
  - common failure mode
  - how it should affect score or messaging
- Align report wording and Telegram wording to the same glossary terms.

Acceptance:

- One person can read the glossary and explain the strategy without opening the scoring code.
- Notification text and report text stop drifting from signal meaning.

Notes:

- This phase is inspired more by XQ-style rule readability than by backtest architecture.

## Phase 3: Introduce Data Provider Abstraction

Objective: reduce fragility from Yahoo-only dependencies.

Tasks:

- Define a small provider interface for:
  - daily OHLCV download
  - market index lookup
  - US reference lookup
- Wrap the current Yahoo implementation first.
- Add one fallback provider, preferably `FinMind`, for Taiwan daily data.
- Keep provider selection configurable via env or config, with Yahoo still as the default during rollout.

Acceptance:

- The main workflow can switch providers without changing ranking logic.
- Best-effort market regime and watchlist generation still complete when one provider fails.
- Provider errors are surfaced clearly in the report/logs.

Notes:

- This phase directly targets the most common operational failure mode in local and automation runs.

## Phase 4: Separate Reporting and Notification Side Effects

Objective: keep output generation and messaging from being tangled with strategy logic.

Tasks:

- Extract Markdown/HTML report rendering into `reports/`.
- Extract Telegram message building from Telegram sending.
- Move alert tracking update logic and last-state persistence into `state/`.
- Keep `daily_theme_watchlist.py` as the orchestrator that calls these modules.

Acceptance:

- The orchestration layer reads like a pipeline instead of a monolith.
- Telegram formatting can be changed without touching score logic.
- Report generation is testable with fixture inputs.

## Phase 5: Align `portfolio_check.py` With Shared Modules

Objective: stop duplicating watchlist interpretation logic across portfolio and daily watchlist flows.

Tasks:

- Replace copied or coupled logic with shared imports from the extracted modules.
- Keep portfolio-specific recommendation wording separate from daily ranking.
- Verify that portfolio reports still render the same key sections.

Acceptance:

- Changes to market regime or signal calculation only need to be made once.
- Portfolio output remains locally useful and does not accidentally inherit Telegram-specific assumptions.

## Phase 6: Add Optional Universe Filters

Objective: improve input quality before ranking, without forcing a fundamental overlay on every run.

Tasks:

- Add an optional pre-ranking filter layer for:
  - liquidity thresholds
  - revenue growth
  - ROE or EPS quality
  - sector or theme inclusion
- Allow the filter to be disabled so the current workflow remains available.
- Keep `watchlist.csv` support so manually curated names remain first-class.

Acceptance:

- We can compare "manual watchlist only" versus "filtered universe then ranked" as separate modes.
- Verification can label which mode produced a recommendation.

Notes:

- This is the last phase on purpose. It is a strategy extension, not a prerequisite for structural cleanup.

## Recommended Order of Work

1. Extract `detect_row()` and ranking helpers.
2. Add tests around signal and score outputs.
3. Create the signal glossary document.
4. Introduce provider interface and wrap Yahoo.
5. Add FinMind fallback for Taiwan daily data.
6. Extract reporting, Telegram formatting, and state persistence.
7. Align `portfolio_check.py` with shared modules.
8. Explore optional universe filters.

## Concrete First PR Scope

The safest first implementation slice should be narrow:

- Add `stock_watch/signals/detect.py`
- Add `stock_watch/ranking/scoring.py`
- Move pure helper functions only
- Update `daily_theme_watchlist.py` imports to call the new modules
- Add targeted tests for extracted functions

Avoid in the first PR:

- changing provider behavior
- changing ranking thresholds
- changing Telegram output shape
- changing verification CSV schemas

## Risks to Watch

- Hidden coupling between signal fields and report templates
- Verification scripts depending on exact column names or action labels
- Portfolio flow relying on helpers that look pure but still read config or global state
- Refactor churn turning into strategy churn

## Definition of Done for the Refactor

The refactor is successful when:

- the daily workflow still runs from the current entrypoints
- signal logic lives in small testable modules
- at least one non-Yahoo provider can backfill Taiwan daily data
- report and Telegram wording are driven by a shared signal vocabulary
- verification keeps running without bespoke patches after each structural change

## Immediate Next Step

Open the first refactor slice around signal detection and ranking extraction, then stop and verify parity before touching providers or reports.
