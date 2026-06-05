# Stock-Watch Analysis Improvement Plan

This document tracks a prioritized plan to improve the quality of the stock market
and price analysis produced by `stock-watch`. It is organized so the highest-value,
correctness-first work lands before breadth and structural work.

Status legend: `[ ]` planned · `[~]` in progress · `[x]` done

---

## A. Analysis correctness (highest priority)

Trustworthy conclusions depend on this layer. Do it first.

- [x] **A1. Single source of truth for strategy thresholds.**
  The steady/attack definitions are now centralized in
  `stock_watch/strategy/classification.py` (`ClassificationThresholds`,
  `is_steady_event`, `is_attack_event`), loaded from a new `config.json`
  `classification` block and shared by the backtest. Defaults reproduce the prior
  hardcoded behavior; README points to the single source. Parity tests included.

- [x] **A2. Reduce backtest bias.** Documented in `docs/research/BACKTEST_BIASES.md`:
  auto-adjust look-ahead, survivorship/selection bias, missing execution costs, and
  no intraday path, with guidance on how to read results despite the biases.

- [x] **A3. Risk-adjusted backtest metrics + factor tear sheet.**
  `summarize_events` now reports `avg_win_pct`, `avg_loss_pct`, `payoff_ratio`,
  `profit_factor`, `std_return_pct`, and `max_drawdown_pct` (daily report surfaces
  Profit Factor / Payoff / Max DD, backward compatible). Added
  `stock_watch/backtesting/tear_sheet.py` (`factor_tear_sheet`, `monotonicity_score`)
  to validate whether `setup_score` / `risk_score` actually predict forward returns.

- [x] **A4. Execution realism.**
  `stock_watch/backtesting/costs.py` adds a Taiwan `CostModel` (~0.585% round trip =
  2×0.1425% brokerage + 0.3% sell-side tax) with configurable slippage and
  `net_return_pct()` to convert gross close-to-close returns to net.

## B. Analysis breadth (improve stock selection quality)

- [x] **B1. Relative strength vs the index (`^TWII`).**
  `stock_watch/signals/relative_strength.py` — RS ratio line, RS momentum, excess
  return, outperformance flag, and a 1–99 RS rating.
- [x] **B2. Additional technical indicators.** RSI / MACD / ADX added via
  `add_momentum_indicators` and wired into `add_indicators` (`signals/detect.py`).
  (Volume–price divergence and gap detection remain future work.)
- [x] **B3. Market filter upgrade (breadth).**
  `stock_watch/signals/market_breadth.py` — % of names above MA and advance/decline
  ratio with a breadth label. (Sector rotation remains future work.)
- [x] **B4. Data-quality gate.** `stock_watch/data/quality.py` validates minimum
  history, NaNs, staleness, missing columns, and non-positive closes before scoring.

## C. Structure & maintainability (supports safe iteration)

- [ ] **C1. Finish the data-layer refactor.** Move data fetch / cache / provider wiring
  out of the legacy `daily_theme_watchlist.py` god module into `stock_watch/data/`,
  removing the reverse dependency injection so scoring/ranking can be unit-tested.
- [ ] **C2. Break up oversized files.** `local_daily.py`, `weekly_review.py`,
  `summarize_outcomes.py`.
- [x] **C3. CI quality gates.** Added ruff + mypy config (`pyproject.toml`),
  `requirements-dev.txt`, and ruff (blocking) + mypy (non-blocking) steps in both
  workflows. (Coverage gate and CI cron fallback remain future work.)

---

## Suggested execution order

1. **A1 + C1** — establish a trustworthy backtest foundation.
2. **A3 (tear sheet) + B1** — validate factors and add relative strength.
3. **A4 + C3** — execution realism and CI hardening.

## Changelog

- 2026-06-03: Branch `feature/analysis-improvements` created. Implemented the first
  A3 increment (risk-adjusted backtest summary metrics) with unit tests; full suite
  (276 tests) green.
- 2026-06-05: Consolidated all completed work onto `feature/market-analysis-all`:
  A1 (classification single source), A2 (bias docs), A3 (metrics + factor tear sheet),
  A4 (execution costs), B1 (relative strength), B2 (RSI/MACD/ADX), B3 (market breadth),
  B4 (data-quality gate), C3 (ruff/mypy CI). Full suite 329 tests green; ruff clean.
  Remaining: C1 (data-layer refactor) and C2 (split oversized files).
