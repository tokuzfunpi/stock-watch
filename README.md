# Stock Watch

這是目前使用中的 stock watch 版本：每日觀察清單、portfolio 檢查、recommendation verification、weekly review 與本機 dashboard 都收斂到同一個 CLI 入口。

## 重點邏輯

- Steady / Attack 事件分類的門檻集中定義在 `config.json` 的 `classification` 區塊，並由
  `stock_watch/strategy/classification.py` 作為唯一真實來源 (single source of truth)，
  回測 (`run_backtest_dual`) 與線上判斷共用同一份規則，避免漂移。
  - Steady：`setup_score >= 5` 且 `risk_score <= 4`。
  - Attack：同時滿足 `ret5 > 8`、`volume_ratio > 1.3`、`ret20 > 0`，或出現 `ACCEL`。
  - 要調整門檻時只改 `config.json`，不要在程式碼裡再寫死一份。
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
- `docs/research/STANDARD_TUNING_PROPOSAL_2026_05_05.md`

## 流動性策略（通知 vs 操作）

本 repo 支援「投資視角通知」與「交易視角操作」同時存在：

- Telegram 通知（偏投資）：預設 `tag_only`（不移出桶，只加註流動性/量縮提示）
- Dashboard / Portfolio（偏交易）：預設 `per_bucket`（低流動性/量縮會進 `量縮先等`）

設定優先序：環境變數 > `config.json:liquidity`。

常用 env：

- `STOCK_WATCH_LIQUIDITY_POLICY_NOTIFY=tag_only`
- `STOCK_WATCH_LIQUIDITY_POLICY_DASHBOARD=per_bucket`
- `STOCK_WATCH_LIQUIDITY_VR20_THRESHOLD=0.9`
- `STOCK_WATCH_LIQUIDITY_TO20_TRIAL_THRESHOLD_M=30`
- `STOCK_WATCH_LIQUIDITY_TO20_PULLBACK_THRESHOLD_M=10`
- `STOCK_WATCH_LIQUIDITY_TO20_WAIT_STRENGTH_THRESHOLD_M=20`

## 安裝

- 固定 venv：`export VENV_PY=/Users/tokuzfunpi/codes/nvidia/311env/bin/python`
- 如需 activate：`source /Users/tokuzfunpi/codes/nvidia/311env/bin/activate`
- 安裝依賴：`$VENV_PY -m pip install -r requirements.txt`
- 跑測試：`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 $VENV_PY -m pytest`
- CI 安靜版 unittest：`$VENV_PY scripts/run_unittest_quiet.py`

## 單一 CLI 入口

優先使用：

- `$VENV_PY -m stock_watch daily --mode preopen`
- `$VENV_PY -m stock_watch daily --mode preopen --force-watchlist`
- `$VENV_PY -m stock_watch daily --mode postclose`
- `$VENV_PY -m stock_watch daily --mode full`
- `$VENV_PY -m stock_watch daily --mode portfolio`
- `$VENV_PY -m stock_watch doctor`
- `$VENV_PY -m stock_watch doctor --skip-network`
- `$VENV_PY -m stock_watch doctor --skip-network --fail-on warn`
- `$VENV_PY -m stock_watch report-sync`
- `$VENV_PY -m stock_watch daily --mode portfolio --sync-watchlist-report`
- `$VENV_PY -m stock_watch daily --mode portfolio --no-sync-watchlist-report`
- `$VENV_PY -m stock_watch weekly`
- `$VENV_PY -m stock_watch weekly --max-signal-dates 5`
- `$VENV_PY -m stock_watch backup-artifacts`
- `$VENV_PY -m stock_watch backup-artifacts --dry-run`
- `$VENV_PY -m stock_watch housekeeping`
- `$VENV_PY -m stock_watch housekeeping --apply`
- `$VENV_PY -m stock_watch website`

Daily aliases 也可以用：

- `$VENV_PY -m stock_watch preopen`
- `$VENV_PY -m stock_watch postclose`
- `$VENV_PY -m stock_watch full`
- `$VENV_PY -m stock_watch portfolio`

Verification 子命令：

- `$VENV_PY -m stock_watch verification daily --mode preopen`
- `$VENV_PY -m stock_watch verification daily --mode postclose`
- `$VENV_PY -m stock_watch verification snapshot`
- `$VENV_PY -m stock_watch verification evaluate --all-dates --horizons 1,5,20`
- `$VENV_PY -m stock_watch verification summary`
- `$VENV_PY -m stock_watch verification feedback`
- `$VENV_PY -m stock_watch verification backfill --limit 0 --rebuild-snapshot`

## Daily 模式

- `preopen`：跑 watchlist + verification snapshot。
- `postclose`：跑 watchlist + portfolio + verification 後半段，且 watchlist 會強制重跑，不沿用同日 preopen 的 duplicate guard。
- `postclose`：流程尾端還會自動執行 `doctor --skip-network --fail-on warn`，把每日健康摘要寫進 scheduler log；只要 doctor 退化到 `warn/fail`，排程就會明確失敗。
- `postclose` / `full`：若 portfolio 步驟讓 `daily_rank.csv` 比 `daily_report.md` 新，會自動補跑 `report-sync`。
- `full`：整套本機流程一次跑完。
- `portfolio`：只跑本機持股檢查。

每次 `daily` 執行後會更新：

- `runs/theme_watchlist_daily/local_run_status.md`
- `runs/theme_watchlist_daily/local_run_status.json`
- `runs/theme_watchlist_daily/local_doctor_summary.txt`
- `runs/theme_watchlist_daily/report_sync_metrics.md`
- `runs/theme_watchlist_daily/report_sync_metrics.json`

## Portfolio 檢查

- 複製 `portfolio.csv.example` 成本機的 `portfolio.csv`。
- 填入自己的持股資料。
- 執行 `$VENV_PY -m stock_watch portfolio`。
- 這個流程會更新 `runs/theme_watchlist_daily/daily_rank.csv` 供持股檢查使用，但不會重建 `daily_report.md/html`。
- `daily --mode portfolio`、`daily --mode postclose`、`daily --mode full` 現在預設都會在需要時自動補跑 `report-sync`；若不想補跑，可加 `--no-sync-watchlist-report`。
- 如果想手動把 watchlist 報表補齊到最新排行，再跑 `$VENV_PY -m stock_watch report-sync`。

輸出：

- `runs/theme_watchlist_daily/portfolio_report.md`
- `runs/theme_watchlist_daily/portfolio_report.html`
- `runs/theme_watchlist_daily/portfolio_runtime_metrics.md`
- `runs/theme_watchlist_daily/portfolio_runtime_metrics.json`
- `runs/theme_watchlist_daily/report_sync_metrics.md`
- `runs/theme_watchlist_daily/report_sync_metrics.json`

## 重要輸出

`runs/theme_watchlist_daily/` 與 `runs/verification/watchlist_daily/` 是本機產物目錄，預設不納入 git；這些檔案由 workflow 重建或持續累積。若需要保存某次結果，請另外做明確的 artifact snapshot commit，例如 `git add -f runs/theme_watchlist_daily/daily_rank.csv runs/theme_watchlist_daily/alert_tracking.csv`。

在執行任何會清理本機產物的操作前，建議先跑 `$VENV_PY -m stock_watch backup-artifacts`；預設會備份重要報告與 CSV 到 `runs/artifact_backups/`，但不包含 cache/log。若只想檢查會備份哪些檔案，先跑 `$VENV_PY -m stock_watch backup-artifacts --dry-run`。

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

- `$VENV_PY -m stock_watch website`
- `open runs/theme_watchlist_daily/local_site/index.html`

## 自動排程（本機）

- 盤前 / 盤後自動跑：`docs/runbooks/SCHEDULING.md`

## Telegram chat id

- 優先讀 `TELEGRAM_CHAT_IDS`。
- 如果 env 沒設，會改讀本機 `chat_ids`。
- `chat_ids` 可一行一個 id，或用逗號分隔。
- `python -m stock_watch daily` 會先用 `STOCK_WATCH_LOCAL_TELEGRAM_CHAT_IDS`，再 fallback 到 `TELEGRAM_CHAT_IDS`，最後才預設只送到 `7758949915`；可用 `--local-telegram-chat-ids` 覆寫。
- GitHub Actions 手動跑或重跑 `stock-watch` 時，會固定 bypass 當日重複執行 guard 並送完整通知；可填 `telegram_chat_ids` input 覆寫該次收件人，留空就用 `TELEGRAM_CHAT_IDS` secret 的完整清單。

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

Root-level local wrappers have been removed. Use `$VENV_PY -m stock_watch ...` for local workflows and `$VENV_PY -m stock_watch verification ...` for verification workflows.
