# Backtest Biases & Caveats (A2)

The backtest is a research aid, not a promise of realized performance. These
known biases inflate or distort results and must be kept in mind when reading
`backtest_summary_*.csv` or the factor tear sheet.

## 1. Adjusted-price look-ahead

Price history is fetched from Yahoo with `auto_adjust=True`
(`stock_watch/data/providers/yahoo.py`). Adjusted prices are recomputed
retroactively on every dividend/split, so the historical series seen *today*
is not what was visible on the original signal date. A signal computed on the
adjusted series can therefore be subtly different from the one a live run would
have produced at the time.

- Impact: mild look-ahead in levels around dividend/split dates.
- Mitigation options (future work): persist both raw and adjusted series, or
  snapshot indicators at signal time rather than recomputing from the latest
  adjusted history.

## 2. Survivorship / selection bias

The watchlist is hand-curated. The backtest only ever evaluates names that
made it onto the list, which over-represents stocks that already looked
attractive and survived. Reported win rates and average returns are therefore
optimistic relative to a blind universe.

- Impact: upward bias in win rate and average return.
- Mitigation: treat absolute win rates as a ceiling; prefer *relative*
  comparisons (e.g. bucket A vs bucket B in the factor tear sheet) which are
  less sensitive to this bias.

## 3. Execution costs not in raw forward returns

Forward returns (`ret_1d`, `ret_5d`, `ret_20d`) are close-to-close and exclude
trading frictions. For Taiwan equities a round trip costs roughly **0.585%**
(2 x 0.1425% brokerage + 0.3% sell-side transaction tax), before slippage.

- Use `stock_watch/backtesting/costs.py` (`CostModel`) to convert gross returns
  to net when a figure is meant to resemble realized P&L.
- Short horizons are affected most: a +0.5% gross 1-day move is roughly flat
  after costs.

## 4. No intraday path

Forward returns assume entry/exit at the close. Intraday stop/trim touches,
gaps, and liquidity limits are not modeled here (tracked separately in the
path-risk work). Treat single-print returns as idealized.

## How to read results despite the biases

- Favor monotonic *ordering* of score buckets over absolute return levels.
- Apply `CostModel` before quoting any P&L-like number.
- Require a minimum sample size per bucket before drawing conclusions.
- Remember the universe is curated; do not generalize win rates to all stocks.
