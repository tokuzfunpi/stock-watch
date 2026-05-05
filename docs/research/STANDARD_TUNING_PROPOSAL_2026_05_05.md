# Strategy Standard Tuning Proposal (2026-05-05)

這份文件記錄截至 `2026-05-05` 的最新判斷：哪些正式標準先不要動，哪些只適合先做 shadow / research 驗證。

## Evidence Window

- Full-history verification source:
  - `runs/verification/watchlist_daily/reco_outcomes.csv`
  - `runs/verification/watchlist_daily/outcomes_summary.md`
- Recent realized window:
  - `signal_date = 2026-04-29 .. 2026-05-04`
  - `runs/theme_watchlist_daily/weekly_review.md`
  - `runs/theme_watchlist_daily/recent_estimate_effectiveness.md`
  - `runs/theme_watchlist_daily/recent_action_effectiveness.csv`

## Decision Summary

### Keep live standards unchanged

Do **not** change the live gate on `2026-05-05`.

Current evidence is strong enough to open research lines, but not strong enough to justify changing production selection rules.

### What stays unchanged

1. `midlong threshold`
   - Keep current live threshold unchanged.
   - Reason: recent `1D midlong below_threshold` is still heat-heavy, and `normal below_threshold n=0`.
   - Latest weekly decision: `block`.

2. Overall `short gate`
   - Keep current live short gate unchanged.
   - Reason: there is no action that reached `promotion_ready`.
   - Latest weekly decision: `hold`.

3. Feedback weight
   - Keep `70/30`.
   - Reason: `60/40`, `70/30`, and `80/20` change scores only slightly and do not change rank order.
   - Latest weekly decision: `hold`.

## What can change in research mode

### 1. Keep `開高不追` as a shadow tuning candidate

This is the most credible research candidate right now, but it is still **not** ready for live promotion.

Why it is interesting:

- Full-history `1D short / 開高不追`: `below-ok = +3.69%`
- Recent `2026-04-29 .. 2026-05-04`: `below-ok = +1.28%`
- Recent realized action stats:
  - `n=2`
  - `win_rate=50.0%`
  - `avg_ret=4.35%`

Why it is still blocked:

- `below_n = 2`
- `action_signal_dates = 1`
- `dominant_positive_share_pct = 100%`
- `promotion_ready = false`

Operational stance:

- Keep it in the weekly short-gate tuning watchlist.
- Keep it as shadow-only / paper-only.
- Do not promote it into the live short candidate gate yet.

### 2. Continue monitoring recent short-strength filters

Recent `1D short` evidence suggests the following research filters are worth tracking:

- `ret5_ge_median_8.3`
  - recent `delta_avg_ret_vs_baseline = +2.19%`
  - `n = 9`
- `setup_ge_median_13.0`
  - recent `delta_avg_ret_vs_baseline = +0.98%`
  - `n = 9`
- `live_ok_plus_open_not_chase`
  - recent `delta_avg_ret_vs_baseline = +1.53%`
  - `n = 6`

Operational stance:

- Treat these as research hints only.
- Do not turn them into live hard gates yet.
- Re-check once `1D short` sample size is larger and less concentrated by date.

## Promotion Criteria

The earliest acceptable upgrade path is **action-level promotion**, not full short-gate loosening.

### Minimum criteria for promoting `開高不追`

All of the following should be true before live promotion is discussed:

1. `below_n >= 3`
2. `action_signal_dates >= 2`
3. `dominant_positive_share_pct <= 70`
4. recent window still shows `delta_avg_ret_below_minus_ok > 0`
5. recent edge is not explained only by `hot` or high `spec_risk`

If these are not met, keep the action in shadow mode.

## Explicit Non-Goals

Do **not** do the following based on current evidence:

- Do not loosen overall `short gate`.
- Do not loosen `midlong threshold`.
- Do not convert `spec_risk` into a hard exclusion rule.
- Do not change feedback weighting away from `70/30`.

## Next Review Trigger

Revisit this proposal only after one of these happens:

1. `開高不追` satisfies the promotion criteria above.
2. Recent `1D short` sample size grows enough that the same edge survives across multiple `signal_date` values.
3. `midlong below_threshold` starts accumulating normal-market samples instead of heat-heavy samples.

## Current Recommendation

Use this as the operating rule on `2026-05-05`:

- Keep live standards unchanged.
- Open or continue shadow research for `開高不追`.
- Use weekly review plus recent effectiveness notes as the decision checkpoint.
