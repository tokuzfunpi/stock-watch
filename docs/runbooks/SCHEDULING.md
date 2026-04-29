# 自動排程（盤前 / 盤後）

本專案建議用本機排程（macOS `launchd`）去跑「盤前」與「盤後」兩段 daily workflow。

> 注意：之前刻意沒有用 GitHub Actions `schedule`（cron）是因為 GitHub schedule 可能延遲或掉單；本機排程比較準時。

## 預設時間（Asia/Taipei）

- 盤前：08:45（`python -m stock_watch preopen`）
- 盤後：14:00（`python -m stock_watch daily --mode postclose --all-dates --max-days 60`）

時間跟 `stock_watch/workflows/market_context.py` 的預設 schedule target 一致（08:45 / 14:00）。

盤後多帶 `--all-dates --max-days 60` 是為了讓 verification outcomes 會每天順便更新「近 60 個 signal_date」，
讓 1D/5D/20D 隨著時間成熟時能自動轉成 `ok`，而不是只更新最新一天。

## 安裝（一次）

1. 確認你可以手動跑成功（至少先跑一次）：
   - `python3.11 -m stock_watch preopen`
   - `python3.11 -m stock_watch postclose`
2. 安裝 LaunchAgents：
   - `bash tools/scheduling/install_launchd.sh`

## 解除安裝

- `bash tools/scheduling/uninstall_launchd.sh`

## 看 log / 狀態

- 主 log：
  - `runs/scheduler/preopen.log`
  - `runs/scheduler/postclose.log`
- launchd stdout/stderr：
  - `runs/scheduler/launchd-preopen.out.log`
  - `runs/scheduler/launchd-preopen.err.log`
  - `runs/scheduler/launchd-postclose.out.log`
  - `runs/scheduler/launchd-postclose.err.log`
- 檢查 job 狀態（把 `<uid>` 換成 `id -u` 的輸出）：
  - `launchctl print gui/<uid>/com.stockwatch.preopen`
  - `launchctl print gui/<uid>/com.stockwatch.postclose`

## 調整時間

修改：

- `tools/scheduling/com.stockwatch.preopen.plist`
- `tools/scheduling/com.stockwatch.postclose.plist`

調整 `<integer>Hour</integer>` / `<integer>Minute</integer>` 後，重新跑：

- `bash tools/scheduling/install_launchd.sh`
