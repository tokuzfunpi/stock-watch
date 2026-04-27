# Stock Watch Signal Glossary

This glossary is the readable rulebook for the current watchlist strategy.

## `TREND`

- Trigger shape: price is above `MA20`, `MA20` is above `MA60`, and `ret20` stays above the trend threshold.
- Intent: keep names that are already in a healthy uptrend near the top of the list.
- Common failure mode: a mature move can still look strong even when near-term upside is already crowded.
- Score effect: adds to setup score and reduces speculative-risk scoring.
- Messaging tone: "中段延續中".

## `ACCEL`

- Trigger shape: short-horizon momentum is expanding with supportive volume and positive `ret20`.
- Intent: catch names that are not only trending, but accelerating.
- Common failure mode: late-stage chasing after a vertical move.
- Score effect: strongly improves setup score and is part of the highest-grade setup path.
- Messaging tone: "轉強速度有出來".

## `SURGE`

- Trigger shape: `ret20` is already strong and volume ratio is elevated.
- Intent: surface names where the market is clearly crowding into the theme.
- Common failure mode: heat-driven continuation that can reverse sharply.
- Score effect: boosts setup score but often pushes risk and speculative-risk higher.
- Messaging tone: "題材正在發酵".

## `REBREAK`

- Trigger shape: price reclaims key moving averages with meaningful volume after previously sitting below `MA20`.
- Intent: identify renewed leadership after a reset or shakeout.
- Common failure mode: false reclaim that fails immediately after the breakout day.
- Score effect: supports higher setup score and reduces speculative-risk scoring.
- Messaging tone: "重新站上來了".

## `PULLBACK`

- Trigger shape: deeper drawdown from the 120-day high.
- Intent: tag names that are no longer extended and may be resetting.
- Common failure mode: weak names can look like pullbacks before turning into full breakdowns.
- Score effect: does not automatically create a top-ranked setup; mostly changes interpretation.
- Messaging tone: "高檔拉回整理".

## `BASE`

- Trigger shape: price stays near longer-term lows, volume is quiet, and short-term range remains compressed.
- Intent: detect early basing behavior before a clearer breakout signal appears.
- Common failure mode: dead money that remains range-bound for too long.
- Score effect: adds a smaller setup contribution than momentum-led signals.
- Messaging tone: "低檔慢慢墊高".

## Practical Notes

- `TREND` and `REBREAK` lower speculative-risk scoring because they imply more structure than pure heat.
- `ACCEL` is the most important momentum-style signal in the current strategy because it is aligned with notifications and attack-style backtest logic.
- `SURGE` is useful, but it should be read together with `risk_score` and `spec_risk_label` to avoid blind chasing.
- The report wording and Telegram wording should stay aligned with the descriptions above.

## Speculation Heuristics

- `spec_risk_score` 現在不只看漲很多，還會拆成 4 類：
  - `price_action`：短線/波段漲太快
  - `crowding`：爆量、震幅過大、波動劇烈
  - `extension`：20MA 乖離太大、高檔無回檔
  - `structure`：缺少 `TREND/REBREAK` 這種較健康的結構支撐
- `spec_risk_subtype` 會把高風險型態再翻成更直白的 bucket，例如：`急拉爆量型`、`高檔脫離型`、`結構失配型`
- `spec_risk_note` 會把最主要的 2–3 個可疑訊號寫出來，例如：`短線急漲、爆量、乖離過大`
- `core` / `etf`、`TREND`、`REBREAK`、`BASE` 會有折減，因為這些比較不像純題材硬拉
- 這一版還是 first-pass heuristic，不代表「證明有人炒作」，而是把**價量與結構失衡**的名字先框出來

## Template Bundles

- `Momentum Leader` = `ACCEL` + `TREND`
- `Reclaim Breakout` = `REBREAK` (+ `ACCEL` / `TREND` if present)
- `Theme Heat` = `SURGE` (+ `ACCEL` if present, but not `PULLBACK`)
- `Reset Pullback` = `PULLBACK` (+ `TREND` or `REBREAK` if present)
- `Early Base` = `BASE` (+ `REBREAK` if present)

These bundles now live in `stock_watch/signals/library.py` so verification reports can describe common 台股型態 with one label instead of only raw signal strings.
