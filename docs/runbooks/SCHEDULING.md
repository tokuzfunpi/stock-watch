# 自動排程（盤前 / 盤後）

本專案建議用本機排程（macOS `launchd`）去跑「盤前」與「盤後」兩段 daily workflow。

> 注意：之前刻意沒有用 GitHub Actions `schedule`（cron）是因為 GitHub schedule 可能延遲或掉單；本機排程比較準時。

## 預設時間（Asia/Taipei）

- 盤前：08:45（`python -m stock_watch preopen`）
- 盤後：14:00（`python -m stock_watch daily --mode postclose --all-dates --max-days 60`）

時間跟 `stock_watch/workflows/market_context.py` 的預設 schedule target 一致（08:45 / 14:00）。

盤後多帶 `--all-dates --max-days 60` 是為了讓 verification outcomes 會每天順便更新「近 60 個 signal_date」，
讓 1D/5D/20D 隨著時間成熟時能自動轉成 `ok`，而不是只更新最新一天。

## 固定 Python 環境

- 本機固定 venv：`/Users/tokuzfunpi/codes/nvidia/311env`
- 建議先設：
  - `export VENV_PY=/Users/tokuzfunpi/codes/nvidia/311env/bin/python`
- `tools/scheduling/stock-watch-preopen.sh` / `stock-watch-postclose.sh` 會優先使用這個 venv；不要再優先依賴 repo 內 `.venv`

## 安裝（一次）

1. 確認你可以手動跑成功（至少先跑一次）：
   - `$VENV_PY -m stock_watch preopen`
   - `$VENV_PY -m stock_watch postclose`
2. 安裝 LaunchAgents：
   - `bash tools/scheduling/install_launchd.sh`

## 解除安裝

- `bash tools/scheduling/uninstall_launchd.sh`

## 看 log / 狀態

- 主 log：
  - `runs/scheduler/preopen.log`
  - `runs/scheduler/postclose.log`
- 每日健康摘要：
  - `runs/theme_watchlist_daily/local_doctor_summary.txt`
- launchd stdout/stderr：
  - `runs/scheduler/launchd-preopen.out.log`
  - `runs/scheduler/launchd-preopen.err.log`
  - `runs/scheduler/launchd-postclose.out.log`
  - `runs/scheduler/launchd-postclose.err.log`
- 檢查 job 狀態（把 `<uid>` 換成 `id -u` 的輸出）：
  - `launchctl print gui/<uid>/com.stockwatch.preopen`
  - `launchctl print gui/<uid>/com.stockwatch.postclose`

## 盤後流程現在會多做什麼

- `postclose` 會自動重跑 watchlist，不沿用同日 `preopen` 的 duplicate guard
- 若 portfolio 步驟讓 `daily_rank.csv` 比 `daily_report.md` 新，會自動補跑 `report-sync`
- 流程尾端會自動執行 `doctor --skip-network --fail-on warn`
- `doctor` 只要退化到 `warn` 或 `fail`，排程就會明確失敗
- `postclose.log` 會直接寫出 `doctor_summary=...`

## 調整時間

修改：

- `tools/scheduling/com.stockwatch.preopen.plist`
- `tools/scheduling/com.stockwatch.postclose.plist`

調整 `<integer>Hour</integer>` / `<integer>Minute</integer>` 後，重新跑：

- `bash tools/scheduling/install_launchd.sh`
