# Duplicate Cleanup Plan

這份文件記錄 single CLI 整合後，哪些重複入口與重複資料已移除，哪些還要保留到下一階段。

## 1) CLI entrypoint duplicates

已移除：

- `portfolio_check.py`
- `run_local_daily.py`
- `run_local_doctor.py`
- `run_local_housekeeping.py`
- `run_local_website.py`
- `run_weekly_review.py`
- `verification/backfill_from_git.py`
- `verification/evaluate_recommendations.py`
- `verification/feedback_weight_sensitivity.py`
- `verification/run_daily_verification.py`
- `verification/summarize_outcomes.py`
- `verification/verify_recommendations.py`

保留的單一入口：

- `python -m stock_watch daily --mode preopen`
- `python -m stock_watch daily --mode postclose`
- `python -m stock_watch daily --mode full`
- `python -m stock_watch daily --mode portfolio`
- `python -m stock_watch weekly`
- `python -m stock_watch doctor`
- `python -m stock_watch housekeeping`
- `python -m stock_watch website`
- `python -m stock_watch verification <daily|snapshot|evaluate|summary|feedback|backfill>`

保留的內部實作入口：

- `stock_watch/cli/*.py`：single CLI dispatch 的目標模組。
- `stock_watch/workflows/daily_watchlist.py`：daily watchlist orchestration 的 package workflow。
- `stock_watch/workflows/portfolio.py`：portfolio workflow 與目前的 legacy wiring。
- `verification/cli/*.py`：verification subcommands 的 module wrappers。
- `verification/reports/*.py`、`verification/workflows/*.py`：真正的 implementation modules。

## 2) Still-coupled legacy module

`daily_theme_watchlist.py` 目前不是「可直接刪的 duplicate」。它已不再擁有 daily watchlist 的 top-level orchestration，但仍是 ranking/strategy/report/state helper 與多個共用 globals 的主要來源。

已完成的下一步：

- `stock_watch.cli.local_daily` 不再直接 import `daily_theme_watchlist.py`。
- Watchlist 的 top-level orchestration 已移到 `stock_watch/workflows/daily_watchlist.py`。
- `daily_theme_watchlist.main()` 現在只是相容入口，委派到 package workflow。
- Portfolio 的 legacy 呼叫集中在 `stock_watch/workflows/portfolio.py`。
- Runtime constants (`LOCAL_TZ`, `ALERT_TRACK_CSV`, `FEEDBACK_SUMMARY_CSV`, logger) 已移到 `stock_watch/runtime.py`，weekly/verification 不再為了這些常數 import legacy daily module。
- Daily run-state helpers 已移到 `stock_watch/state/run_state.py`；runtime metrics rendering/writing 已移到 `stock_watch/workflows/runtime_metrics.py`。

下一階段才拆：

- 把仍留在 `daily_theme_watchlist.py` 的 strategy/report/helper 邏輯抽到 package modules。
- 等 package workflows 不再 import `daily_theme_watchlist.py` helpers/globals 時，再刪或改成內部相容層。

## 3) Generated artifact duplicates

已停止新增：

- `local_site/` root compatibility copies，例如 `local_site/daily_report.md`。
- legacy root output folder `theme_watchlist_daily/` has been removed locally; generated outputs should live under `runs/theme_watchlist_daily/`.
- legacy `.gitignore` entries for root `theme_watchlist_daily/` and `verification/watchlist_daily/` were removed so accidental root output recreation becomes visible.

保留：

- `local_site/artifacts/*`：本機網站下載/檢視來源 artifact 的唯一 copy。
- `local_site/views/*`：由 artifact render 出來的 HTML view，可重新生成。

應視為 canonical local state：

- `runs/theme_watchlist_daily/daily_rank.csv`
- `runs/theme_watchlist_daily/alert_tracking.csv`
- `runs/verification/watchlist_daily/reco_snapshots.csv`
- `runs/verification/watchlist_daily/reco_outcomes.csv`

應視為 derived reports，可重新生成：

- `runs/theme_watchlist_daily/daily_report.md`
- `runs/theme_watchlist_daily/daily_report.html`
- `runs/theme_watchlist_daily/portfolio_report.md`
- `runs/theme_watchlist_daily/portfolio_report.html`
- `runs/theme_watchlist_daily/weekly_review.md`
- `runs/verification/watchlist_daily/outcomes_summary.md`
- `runs/verification/watchlist_daily/feedback_weight_sensitivity.md`

應視為 cache/log，可由 housekeeping 管理：

- `runs/theme_watchlist_daily/history_cache/`
- `runs/theme_watchlist_daily/.yfinance_cache/`
- `runs/theme_watchlist_daily/logs/`
- `runs/verification/watchlist_daily/yfinance_cache/`
- `runs/verification/watchlist_daily/contexts/`
- `runs/verification/watchlist_daily/backfill_reports/`

## 4) Documentation policy

Runbooks should only teach the single CLI:

- `README.md`
- `docs/runbooks/LOCAL_RUNBOOK.md`
- `verification/README.md`
- `.github/workflows/*.yml`

Historical handoff notes may still mention deleted files because they describe past work. Do not treat `docs/handoff/*` as current operational runbooks.
