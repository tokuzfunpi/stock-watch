# Stock-Watch Analysis Improvement Plan

This document tracks a prioritized plan to improve the quality of the stock market
and price analysis produced by `stock-watch`. It is organized so the highest-value,
correctness-first work lands before breadth and structural work.

Status legend: `[ ]` planned · `[~]` in progress · `[x]` done

---

## A. Analysis correctness (highest priority)

Trustworthy conclusions depend on this layer. Do it first.

- [ ] **A1. Single source of truth for strategy thresholds.**
  The "attack/steady" definitions and magic numbers currently appear in multiple
  places (`backtesting/core.py`, `README`, `config.notify`) and can drift, which makes
  backtest rules diverge from the live `detect_row` rules. Consolidate into
  `config.json` / a `strategy` dataclass shared by both paths.

- [ ] **A2. Reduce backtest bias.**
  - Yahoo `auto_adjust=True` retroactively changes historical prices on
    dividends/splits, so signals computed today differ from what was visible then
    (look-ahead bias). Preserve raw + adjusted series or label the limitation.
  - The watchlist is hand-picked → survivorship bias inflates win rates. Add a clear
    caveat to reports.

- [x] **A3. Risk-adjusted backtest metrics.** *(first increment — done)*
  `summarize_events` previously reported only win rate / avg / median. Added
  `avg_win_pct`, `avg_loss_pct`, `payoff_ratio`, `profit_factor`, `std_return_pct`,
  and `max_drawdown_pct`, plus a `_max_drawdown_pct` helper. The daily report now
  surfaces Profit Factor / Payoff / Max DD when present (backward compatible).
  Next step within A3: per-quantile forward-return tear sheet by `setup_score` /
  `risk_score` / `spec_risk` to validate predictive power.

- [ ] **A4. Execution realism.**
  Forward returns are close-to-close and ignore Taiwan transaction tax (0.3% on sells)
  + brokerage fees (0.1425%) and slippage. Incorporate costs before any P&L claim.

## B. Analysis breadth (improve stock selection quality)

- [ ] **B1. Relative strength vs the index (`^TWII`).** No relative-to-market strength
  exists today; this is highly relevant for Taiwan stock selection.
- [ ] **B2. Additional technical indicators.** RSI / MACD / ADX, volume–price
  divergence, gap detection.
- [ ] **B3. Market filter upgrade.** Currently a binary TWII MA20 check; add market
  breadth (% of names above their MA) and sector rotation.
- [ ] **B4. Data-quality gate.** Validate minimum history length, NaNs, and staleness
  before scoring; reduce fragility of the single (Yahoo) source.

## C. Structure & maintainability (supports safe iteration)

- [ ] **C1. Finish the data-layer refactor.** Move data fetch / cache / provider wiring
  out of the legacy `daily_theme_watchlist.py` god module into `stock_watch/data/`,
  removing the reverse dependency injection so scoring/ranking can be unit-tested.
- [ ] **C2. Break up oversized files.** `local_daily.py`, `weekly_review.py`,
  `summarize_outcomes.py`.
- [ ] **C3. CI quality gates.** Add ruff + mypy + coverage, pin dependencies, and add a
  CI cron fallback to reduce the single-point-of-failure local scheduler.

---

## Suggested execution order

1. **A1 + C1** — establish a trustworthy backtest foundation.
2. **A3 (tear sheet) + B1** — validate factors and add relative strength.
3. **A4 + C3** — execution realism and CI hardening.

## Changelog

- 2026-06-03: Branch `feature/analysis-improvements` created. Implemented the first
  A3 increment (risk-adjusted backtest summary metrics) with unit tests; full suite
  (276 tests) green.
