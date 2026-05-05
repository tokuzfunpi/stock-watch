# Recent Estimate Effectiveness
- Generated: `2026-05-05 22:06 CST`
- Signal window: `2026-04-29 .. 2026-05-04`
- Mature focus: `1D` outcomes only (`ok rows = 36`)
- Source detail:
  - `weekly review`: `runs/theme_watchlist_daily/weekly_review.md`
  - `action ranking csv`: `runs/theme_watchlist_daily/recent_action_effectiveness.csv`

## Fast Read
- Recent realized performance is better than full-history baseline.
- `1D midlong`: recent `n=21`, win rate `52.4%`, avg ret `1.15%`
- `1D short`: recent `n=15`, win rate `46.7%`, avg ret `1.97%`
- Best current live-style actions are still `short / 等拉回` and `midlong / 續抱`.
- `開高不追` is still interesting, but not ready for promotion; keep it as a shadow tuning watch item.

## Action Ranking
### More usable now
| watch_type | reco_status | action | n | win_rate_pct | avg_ret_pct | note |
| --- | --- | --- | --- | --- | --- | --- |
| short | ok | 等拉回 | 4 | 75.0 | 3.07 | Best recent live short action |
| midlong | ok | 續抱 | 14 | 64.3 | 1.84 | Best recent live midlong action |

### Positive but not ready to promote
| watch_type | reco_status | action | n | win_rate_pct | avg_ret_pct | note |
| --- | --- | --- | --- | --- | --- | --- |
| short | below_threshold | 開高不追 | 2 | 50.0 | 4.35 | Strong avg ret, but sample too small and concentrated |
| short | below_threshold | 只觀察不追 | 1 | 100.0 | 9.98 | Too small; keep guardrail |

### Weak or still noisy
| watch_type | reco_status | action | n | win_rate_pct | avg_ret_pct | note |
| --- | --- | --- | --- | --- | --- | --- |
| midlong | ok | 可分批 | 5 | 20.0 | -1.05 | Recent live weakness |
| short | below_threshold | 分批落袋 | 4 | 25.0 | 1.48 | Positive mean but weak hit rate / unstable median |
| short | below_threshold | 續追蹤 | 4 | 25.0 | -1.84 | Do not promote |

## Gate Calls
### `midlong threshold`
- Current call: `block`
- Why: `1D below_threshold` is still heat-heavy and `normal below_threshold n=0`
- Action: do not loosen this gate yet

### `short gate`
- Current call: `hold`
- Why: no action reached `promotion_ready`
- Action: keep the overall short gate unchanged

### `開高不追`
- Current call: `watch`
- Historical `below-ok = +3.69%`
- Recent `below-ok = +1.28%`
- Blocking issue: `below_n=2`, `action_signal_dates=1`, `dominant_positive_share_pct=100%`
- Action: keep as shadow-only watchlist tuning, not a live promotion

### `feedback weight`
- Current call: `hold`
- `70/30`, `80/20`, `60/40` do not change rank order
- Action: keep `70/30`

## Research Lines Worth Watching
- `1D short / ret5_ge_median_8.3` improved recent avg ret by `+2.19%` vs baseline (`n=9`)
- `1D short / live_ok_plus_open_not_chase` improved recent avg ret by `+1.53%` vs baseline (`n=6`)
- These are research hints only; do not turn them into live gates yet

## Date Context
- `2026-04-29`: weak day, especially `1D short` (`avg=-2.47%`, `win_rate=0%`)
- `2026-04-30`: strongest recent day (`1D short avg=4.57%`, `1D midlong avg=2.93%`)
- `2026-05-04`: still constructive (`1D short avg=3.8%`, `1D midlong avg=1.55%`)
