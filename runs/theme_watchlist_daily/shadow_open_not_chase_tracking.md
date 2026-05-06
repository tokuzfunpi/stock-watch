# 開高不追 Daily Tracking
- Generated: 2026-05-06 14:32:53
- Scope: `開高不追` / `1D short` / shadow-only daily tracking
- Recent signal window: `2026-05-04 -> 2026-05-06` (`3` dates)

## Summary

- Observed rows: `4`
- Eligible rows: `0`
- Matured 1D rows: `3`
- Matured eligible rows: `0`
- Current draft status: `watch`
- Why now: 全歷史 `1D short / 開高不追` 雖然偏強（`below-ok=3.35%`），但近週樣本還不夠穩。
- Proposal: 先維持現行規則，只把 `開高不追` 放進每週的 short-gate tuning watchlist，等 recent-only 也轉成 `promotion_ready` 再討論是否進一步升格。
- Historical gate progress: `below_n=2` / `ok_n=18` / `below-ok=3.35%` / `promotion_ready=False`
- Recent gate progress: `below_n=2` / `ok_n=3` / `below-ok=2.04%` / `promotion_ready=False`

## Promotion Criteria

- `below_n >= 3`
- `action_signal_dates >= 2`
- `dominant_positive_share_pct <= 70`
- recent `below-ok > 0`
- edge should not come only from `hot` + non-normal `spec_risk`

## Daily Rows

| Signal Date | Ticker | Name | Rank | Scenario | Heat | Spec | Eligible | Status | 1D Outcome | 1D Ret |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-05-05 | 2374.TW | 佳能 | 4 | 高檔震盪盤 | hot | watch | False | observe_only | ok | -2.22% |
| 2026-05-05 | 5386.TWO | 青雲 | 5 | 高檔震盪盤 | hot | watch | False | observe_only | pending |  |
| 2026-05-04 | 2374.TW | 佳能 | 4 | 高檔震盪盤 | hot | watch | False | observe_only | ok | -1.22% |
| 2026-05-04 | 5386.TWO | 青雲 | 5 | 高檔震盪盤 | hot | watch | False | observe_only | ok | 9.92% |
