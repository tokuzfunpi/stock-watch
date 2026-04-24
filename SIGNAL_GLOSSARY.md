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
