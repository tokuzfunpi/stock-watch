# GEMINI.md - AI Agent Context & Guidelines

> Reference note: this document is design context from the `testv` line of work. Treat it as guidance to cross-check with `GEMINI_HANDOFF.md`, not as the sole source of truth for what `main` currently does.
>
> 2026-04-28 update: `main` now uses the single CLI (`python -m stock_watch ...`) as the canonical operator surface. Removed root wrappers such as `portfolio_check.py` and `verification/*.py` should be treated as historical references only.

This file provides critical context and operational mandates for AI agents working on the `stock-watch` project. Adhere to these instructions to maintain system integrity and strategy consistency.

## 1. Project Identity & Purpose
`stock-watch` is an adaptive Taiwan stock market tracking and notification system. It combines technical analysis, market regime sensing, and a feedback-loop mechanism to provide high-signal trading alerts via Telegram and local reports.

## 2. Core Logic Mandates (Mandatory Compliance)

### 2.1 Strategy Adaptive Thresholds
- **Location**: `detect_row()` in `daily_theme_watchlist.py`.
- **Mandate**: NEVER hardcode thresholds (e.g., volume ratio, return %). Always use the `StrategyConfig` object passed via the `strat` parameter.
- **Scenario Awareness**: Strategy thresholds MUST be adjusted based on `build_market_scenario()` output using `adjust_strategy_by_scenario()`.

### 2.2 Volatility-Adjusted Bands (ATR)
- **Mandate**: Price bands (Add/Trim/Stop) MUST be calculated using ATR-based volatility multipliers.
- **Standard**: Base ATR_Pct for normalization is 3% (0.03).
- **Visualization**: Always use `volatility_label()` and the associated Emojis (`🧊`, `⚖️`, `🔥`, `⚡`) in user-facing messages.

### 2.3 Feedback Loop (P/L & Win Rate)
- **Mandate**: Ranking priority MUST consider both Win Rate and P/L Ratio.
- **Scoring Formula**: `feedback_score` is the primary sorting weight for final push candidates. Ensure `build_feedback_summary()` is called to refresh these scores daily.

## 3. Critical Files & Data Schema

| File | Role |
| :--- | :--- |
| `python -m stock_watch` | Canonical CLI for daily, portfolio, weekly, website, doctor, housekeeping, and verification workflows. |
| `daily_theme_watchlist.py` | Legacy compatibility shim; package modules own runtime, strategy, workflow, and report logic. |
| `config.json` | Global settings & Strategy Parameter source. |
| `alert_tracking.csv` | Historical signal performance data (with `scenario_label`). |
| `stock_watch/workflows/portfolio.py` | Portfolio workflow used by `python -m stock_watch portfolio`. |
| `CODEX_NOTES.md` | Detailed maintenance and architectural history. |

## 4. Development Constraints

- **Dependency Management**: Maintain strictly through `requirements.txt`.
- **Environment**: Primarily executed in macOS/Linux with Python 3.11.
- **Testing**:
    - Always run focused tests after logic changes, then `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3.11 -m pytest -q` before merge when feasible.
    - Use `test_my_logic.py` for isolated strategy/ATR verification.
- **Reporting**: Reports are output to `theme_watchlist_daily/`. Do NOT change output paths without explicit instruction.

## 5. Security & Safety
- **Credentials**: Never log or commit `TELEGRAM_TOKEN` or `TELEGRAM_CHAT_IDS`. These are handled via environment variables or git-ignored local files.
- **Git Hygiene**: `portfolio.csv`, `chat_id_map.csv`, and `theme_watchlist_daily/` contents are git-ignored. Do NOT force-add them.

## Appendix: 2026-04-22 Strategy Upgrade Details

The following updates transitioned the strategy from "static rules" to a **Dynamic Adaptive System**:

### 1. Strategy Parameterization & Adaptive Thresholds
- **Configuration**: Signal thresholds (e.g., `accel_vol_ratio`) moved to `config.json`.
- **Dynamic Adjustment**: `adjust_strategy_by_scenario()` now modifies thresholds based on `build_market_scenario()`.
- **Operational Stance**: Stricter thresholds are applied during "明顯修正盤" (Corrective Market) to enforce defensive trading.

### 2. ATR Volatility Awareness
- **Indicator**: Added ATR14 calculation to `add_indicators()`.
- **Price Planning**: `watch_price_plan()` now uses a `vol_mult` concept to scale pullback buys and stop-losses based on stock-specific volatility.
- **Robustness**: High-volatility stocks automatically get wider defense zones to prevent premature stops.

### 3. Feedback Loop Refinement
- **P/L Ratio**: `build_feedback_summary()` now includes P/L ratio calculations.
- **Ranking**: Push candidate ordering prioritizes historical performance (Win Rate + P/L Ratio).

### 4. User Experience (Heat Bias & Volatility Tags)
- **Heat Bias Warning**: Telegram alerts now include "⚠️ 注意：Heat Bias 偏強" if top candidates are overheated.
- **Volatility Tags**: Emojis added to notifications (`🧊`, `⚖️`, `🔥`, `⚡`) to signal stock personality.

### Updated Main Flow
1. `get_market_regime()`
2. `get_us_market_reference()`
3. `initial_scenario = build_market_scenario()`
4. `adjusted_strat = adjust_strategy_by_scenario(CONFIG.strategy, initial_scenario)`
5. `run_watchlist(strat=adjusted_strat)`
6. `run_backtest_dual()`
...
9. `upsert_alert_tracking(..., scenario=initial_scenario)`
