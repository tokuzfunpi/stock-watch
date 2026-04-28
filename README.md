# Stock Watch

這是目前使用中的 stock watch 版本：每日觀察清單、portfolio 檢查、recommendation verification、weekly review 與本機 dashboard 都收斂到同一個 CLI 入口。

## 重點邏輯

- Attack 候選需同時滿足：`ret5 > 8`、`volume_ratio > 1.3`、`ret20 > 0`，或出現 `ACCEL`。
- Theme / satellite 優先，theme 另外加分。
- 排名偏重 `setup`、`ret5`、`volume_ratio`、`ret20`。
- `spec_risk_score / spec_risk_subtype / spec_risk_note` 會標示急漲、爆量、乖離大、缺少結構支撐等疑似炒作型態。

## 主要設定檔

- `config.json`
- `watchlist.csv`
- `portfolio.csv.example`
- `.github/workflows/stock-watch.yml`
- `docs/runbooks/SIGNAL_GLOSSARY.md`
- `docs/runbooks/LOCAL_RUNBOOK.md`
- `docs/refactor/STRUCTURE_PLAN.md`

## 安裝

- `python3 -m venv .venv`
- `source .venv/bin/activate`
- `pip install -r requirements.txt`
- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest`

## 單一 CLI 入口

優先使用：

- `python3 -m stock_watch daily --mode preopen`
- `python3 -m stock_watch daily --mode preopen --force-watchlist`
- `python3 -m stock_watch daily --mode postclose`
- `python3 -m stock_watch daily --mode full`
- `python3 -m stock_watch daily --mode portfolio`
- `python3 -m stock_watch doctor`
- `python3 -m stock_watch doctor --skip-network`
- `python3 -m stock_watch weekly`
- `python3 -m stock_watch weekly --max-signal-dates 5`
- `python3 -m stock_watch housekeeping`
- `python3 -m stock_watch housekeeping --apply`
- `python3 -m stock_watch website`

Daily aliases 也可以用：

- `python3 -m stock_watch preopen`
- `python3 -m stock_watch postclose`
- `python3 -m stock_watch full`
- `python3 -m stock_watch portfolio`

Verification 子命令：

- `python3 -m stock_watch verification daily --mode preopen`
- `python3 -m stock_watch verification daily --mode postclose`
- `python3 -m stock_watch verification snapshot`
- `python3 -m stock_watch verification evaluate --all-dates --horizons 1,5,20`
- `python3 -m stock_watch verification summary`
- `python3 -m stock_watch verification feedback`
- `python3 -m stock_watch verification backfill --limit 0 --rebuild-snapshot`

## Daily 模式

- `preopen`：跑 watchlist + verification snapshot。
- `postclose`：跑 watchlist + portfolio + verification 後半段。
- `full`：整套本機流程一次跑完。
- `portfolio`：只跑本機持股檢查。

每次 `daily` 執行後會更新：

- `runs/theme_watchlist_daily/local_run_status.md`
- `runs/theme_watchlist_daily/local_run_status.json`

## Portfolio 檢查

- 複製 `portfolio.csv.example` 成本機的 `portfolio.csv`。
- 填入自己的持股資料。
- 執行 `python3 -m stock_watch portfolio`。

輸出：

- `runs/theme_watchlist_daily/portfolio_report.md`
- `runs/theme_watchlist_daily/portfolio_report.html`
- `runs/theme_watchlist_daily/portfolio_runtime_metrics.md`
- `runs/theme_watchlist_daily/portfolio_runtime_metrics.json`

## 重要輸出

Watchlist：

- `runs/theme_watchlist_daily/daily_report.md`
- `runs/theme_watchlist_daily/daily_report.html`
- `runs/theme_watchlist_daily/daily_rank.csv`
- `runs/theme_watchlist_daily/runtime_metrics.md`
- `runs/theme_watchlist_daily/runtime_metrics.json`
- `runs/theme_watchlist_daily/alert_tracking.csv`

Verification：

- `runs/verification/watchlist_daily/verification_report.md`
- `runs/verification/watchlist_daily/reco_snapshots.csv`
- `runs/verification/watchlist_daily/reco_outcomes.csv`
- `runs/verification/watchlist_daily/outcomes_summary.md`
- `runs/verification/watchlist_daily/feedback_weight_sensitivity.md`
- `runs/verification/watchlist_daily/codex_context.json`

Weekly / maintenance：

- `runs/theme_watchlist_daily/weekly_review.md`
- `runs/theme_watchlist_daily/weekly_review.json`
- `runs/theme_watchlist_daily/local_doctor.md`
- `runs/theme_watchlist_daily/local_doctor.json`
- `runs/theme_watchlist_daily/local_housekeeping.md`
- `runs/theme_watchlist_daily/local_housekeeping.json`

本機網站：

- `python3 -m stock_watch website`
- `open runs/theme_watchlist_daily/local_site/index.html`

## Telegram chat id

- 優先讀 `TELEGRAM_CHAT_IDS`。
- 如果 env 沒設，會改讀本機 `chat_ids`。
- `chat_ids` 可一行一個 id，或用逗號分隔。

如果要保留 `chat_id` 和使用者對照表：

- repo 內有 `chat_id_map.csv.example`
- 本機可維護 `chat_id_map.csv`
- `chat_id_map.csv` 已加入 `.gitignore`

如果要從 Telegram `getUpdates` 自動更新：

- 設 `TELEGRAM_GETUPDATES_URL`、`TELEGRAM_TOKEN`，或建立本機 `telegram_getupdates_url`
- 執行 `python3 update_chat_id_map.py`

## 資料來源與 cache

- 預設主來源：`yahoo`
- 預設備援：`finmind`
- 預設會開啟記憶體 cache 與磁碟 history cache
- 可用 env 覆寫：
  - `STOCK_DATA_PROVIDER`
  - `STOCK_DATA_FALLBACKS`
  - `FINMIND_TOKEN`
  - `ENABLE_HISTORY_CACHE`
  - `ENABLE_DISK_HISTORY_CACHE`

## 舊入口狀態

Root-level local wrappers have been removed. Use `python3 -m stock_watch ...` for local workflows and `python3 -m stock_watch verification ...` for verification workflows.
