# Public Stock Repo Scouting Notes

Generated: 2026-04-27

## Current Recommendation

Keep our core strategy local and use public repos as references for infrastructure, not as a source of copied stock-picking rules.

Best reference themes for this repo:

1. Verification hygiene: snapshot/outcome schema, forward-return horizons, status handling.
2. Backtest realism: event timing, transaction cost, slippage, position sizing.
3. Factor evaluation: quantile/decile analysis, IC-like checks, decay/turnover.
4. Risk reporting: drawdown, Sharpe-like metrics, win rate, tail checks, Monte Carlo.
5. Local workflow reliability: cache, retry, report generation, reproducible runbooks.

## Shortlist

| Repo | Why It Matters | What We Should Borrow | What Not To Borrow Blindly |
| --- | --- | --- | --- |
| [polakowo/vectorbt](https://github.com/polakowo/vectorbt) | Very strong vectorized research/backtest stack with signal tooling, parameter sweeps, analytics, and automation ideas. | Use as inspiration for fast multi-horizon/signal experiments and robustness tables. | Do not move our daily workflow into vectorized backtesting yet; it may add complexity before our sample is mature. |
| [mementum/backtrader](https://github.com/mementum/backtrader) | Mature event-driven backtesting model with broker simulation, slippage/commission, sizing, multiple feeds/timeframes. | Borrow mental model for realistic execution assumptions and event timing. | Do not adopt as dependency unless we need true order simulation. |
| [kernc/backtesting.py](https://github.com/kernc/backtesting.py) | Compact, readable backtesting API with optimizer and visual result summaries. | Good reference for lightweight strategy experiment API and quick report shape. | Avoid over-optimizing parameters on our tiny current sample. |
| [microsoft/qlib](https://github.com/microsoft/qlib) | Full quant research pipeline: data processing, model training, backtesting, risk modeling, portfolio optimization, execution. | Borrow modular pipeline vocabulary and offline data-store ideas. | Too heavy for our current local daily watchlist; skip ML/RL infra for now. |
| [cloudQuant/alphalens](https://github.com/cloudQuant/alphalens) | Factor-performance analysis with quantile returns, IC, turnover, event studies, tear sheets. | Best conceptual fit for our `setup_score`, `risk_score`, `spec_risk_score`, action-label validation. | Requires factor/price alignment discipline; do not force all recommendation logic into factor quantiles. |
| [ranaroussi/quantstats](https://github.com/ranaroussi/quantstats) | Portfolio profiling, performance/risk metrics, plots, HTML tear sheets, Monte Carlo risk checks. | Borrow reporting sections for `portfolio_report` and weekly review: drawdown, rolling metrics, Monte Carlo framing. | It is return-series oriented; recommendation outcomes still need our custom snapshot schema. |
| [bukosabino/ta](https://github.com/bukosabino/ta) | Pandas/Numpy technical indicator library with ATR, Bollinger, RSI, returns, etc. | Good reference for standardizing indicator names and tests. | Our signals already exist; do not add indicators unless tied to a measured decision. |
| [quantopian/zipline](https://github.com/quantopian/zipline) / [quantopian/pyfolio](https://github.com/quantopian/pyfolio) | Historically important event-driven backtest + tear-sheet ecosystem. | Useful design reference for event timing and tear-sheet thinking. | Old/deprecated ecosystem; avoid as new dependency. |
| [AI4Finance-Foundation/FinRL](https://github.com/AI4Finance-Foundation/FinRL) | Train/test/trade pipeline for RL agents and portfolio allocation experiments. | Only borrow train/test/trade separation vocabulary if we later test model-driven allocation. | Do not introduce RL now; it is way past the useful complexity boundary. |

## Scouting Checklist

When reviewing any public stock repo, score it on:

- Data timing: Can it prove no look-ahead bias?
- Data schema: Does it preserve raw signal snapshots before outcomes are known?
- Outcome maturity: Does it distinguish pending, missing price, invalid signal date, and mature result?
- Execution assumptions: Does it model entry timing, fees, slippage, liquidity, and position size?
- Robustness: Does it include walk-forward, out-of-sample, regime split, or parameter sensitivity?
- Reporting: Does it generate stable artifacts we can compare day by day?
- Tests: Does it test edge cases around dates, missing bars, and duplicate records?
- Maintenance: Is it active enough to trust as dependency, or only useful as reference?

## Best Next Experiments For This Repo

1. Add an `alphalens`-style factor table for our daily rows:
   - factor: `setup_score`, `risk_score`, `spec_risk_score`, `volume_ratio20`, `ret5_pct`, `ret20_pct`
   - group keys: `watch_type`, `action`, `scenario_label`, `market_heat`, `spec_risk_bucket`
   - outcomes: 1D/5D/20D return, win rate, median return

2. Add a `quantstats`-style weekly risk section:
   - drawdown-like worst case per signal group
   - rolling 1D/5D hit rate
   - simple Monte Carlo/bootstrap confidence for small samples

3. Add a `backtrader`-inspired execution assumption note:
   - recommendation timestamp
   - hypothetical entry price rule
   - transaction cost/slippage placeholder
   - liquidity guardrail

4. Add a `vectorbt`-style sensitivity report:
   - test top N = 3/5/8
   - test score thresholds
   - compare `開高不追` shadow promotion vs current gate

## Current Decision

Do not change the live strategy gate yet.

The best near-term move is to keep collecting true preopen snapshots and postclose outcomes, while using the public-repo patterns above to improve our evaluation/reporting layer.
