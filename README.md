這是目前使用中的 stock watch 版本。

你這次要的三件事都做進去了：
1. attack 濾網更嚴，降低假突破
2. Telegram 改成更口語、像真人提醒
3. watchlist 擴大，加入更多題材 / 權值 / ETF / 衛星標的

重點邏輯：
- attack 候選需同時滿足：ret5 > 8、volume_ratio > 1.3、ret20 > 0，或出現 ACCEL
- theme / satellite 優先，theme 另外加分
- 排名偏重 setup、ret5、volume_ratio、ret20
- 通知給前 3 檔

主要執行檔：
- `daily_theme_watchlist.py`
  - 每日觀察清單、排行、回測、主通知
- `portfolio_check.py`
  - 本機持股檢查專用
  - 共用同一套資料抓取與判讀邏輯
  - 不送 Telegram，只輸出本機報表與 CLI 內容

主要設定檔：
- `config.json`
- `watchlist.csv`
- `portfolio.csv.example`
- `.github/workflows/stock-watch.yml`
- `SIGNAL_GLOSSARY.md`
  - 訊號規則與報表語意對照
  - 也包含 template bundles（例如 `Momentum Leader`、`Reclaim Breakout`）
  - 以及 first-pass 的 `spec_risk_score / spec_risk_subtype / spec_risk_note` 說明

安裝與執行：
- `python3 -m venv .venv`
- `source .venv/bin/activate`
- `pip install -r requirements.txt`
- `python3 -m unittest discover -s tests`
- `python3 daily_theme_watchlist.py`
- 同一天需要強制重跑時：
  - `python3 daily_theme_watchlist.py --force`

本機日常流程也可以直接用單一入口：
- `python3 run_local_daily.py --mode preopen`
- `python3 run_local_daily.py --mode postclose`
- `python3 run_local_daily.py --mode full`
- `python3 run_local_daily.py --mode portfolio`
- 先做環境檢查也可以：
  - `python3 run_local_doctor.py`
  - `python3 run_local_doctor.py --skip-network`
- 每週整理 decision note 也可以：
  - `python3 run_weekly_review.py`
  - `python3 run_weekly_review.py --max-signal-dates 5`
- 清理舊的 local verification 產物也可以：
  - `python3 run_local_housekeeping.py`
  - `python3 run_local_housekeeping.py --apply`
  - `python3 run_local_housekeeping.py --verification-outdir verification/watchlist_daily --apply`

說明：
- `preopen`：跑 `daily_theme_watchlist.py` + verification snapshot
- `postclose`：跑 `daily_theme_watchlist.py` + `portfolio_check.py` + verification 後半段
- `full`：整套本機流程一次跑完
- `portfolio`：只跑本機持股檢查
- 每次執行後會更新：
  - `theme_watchlist_daily/local_run_status.md`
  - `theme_watchlist_daily/local_run_status.json`
  - 用來快速看本次哪些 step 有跑、成功與否、最新 verification row 狀態，以及 watchlist / portfolio / verification runtime
  - 也會列出目前 daily rank 的 `spec_risk` 高風險/觀察名單數量與前幾檔 ticker
- `run_local_doctor.py` 會更新：
  - `theme_watchlist_daily/local_doctor.md`
  - `theme_watchlist_daily/local_doctor.json`
  - 用來檢查 Python / 本機設定檔 / Telegram / cache / Yahoo DNS readiness
  - 也會列出 `history_cache` 的檔案數與總容量，以及目前 `spec_risk` 的高風險/觀察名單摘要
- `run_weekly_review.py` 會更新：
  - `theme_watchlist_daily/weekly_review.md`
  - `theme_watchlist_daily/weekly_review.json`
  - 用來整理最近幾個 `signal_date` 的 threshold / ATR / feedback / `spec_risk` 決策建議
- `run_local_housekeeping.py` 會更新：
  - `theme_watchlist_daily/local_housekeeping.md`
  - `theme_watchlist_daily/local_housekeeping.json`
  - 預設是 dry-run，先列出會刪掉哪些舊 `contexts`、`backfill_reports`、`*.bak*`、verification cache、`history_cache`；加 `--apply` 才會真的刪

如果要跑持股檢查：
- 複製 `portfolio.csv.example` 成本機的 `portfolio.csv`
- 填入自己的持股資料
- `python3 portfolio_check.py`
- 執行後會：
  - 更新 `theme_watchlist_daily/portfolio_report.md`
  - 更新 `theme_watchlist_daily/portfolio_report.html`
  - 直接在 CLI 印出大盤摘要與持股建議

Telegram chat id 也支援本機 fallback：
- 優先讀 `TELEGRAM_CHAT_IDS`
- 如果 env 沒設，會改讀本機 `chat_ids`
- `chat_ids` 可用：
  - 一行一個 id
  - 或逗號分隔

如果你想保留 `chat_id` 和使用者對照表：
- repo 內有 `chat_id_map.csv.example`
- 本機可維護 `chat_id_map.csv`
- `chat_id_map.csv` 已加入 `.gitignore`，不會被 push

如果你想從 Telegram `getUpdates` 自動更新：
- repo 內有 `update_chat_id_map.py`
- 可用其中一種方式提供來源：
  - 設 `TELEGRAM_GETUPDATES_URL`
  - 或設 `TELEGRAM_TOKEN`
  - 或建立本機 `telegram_getupdates_url`
- 執行：
  - `python3 update_chat_id_map.py`

補充：
- `daily_report.md` 會包含 Signals 對照表與 Regime 解釋，方便直接看報表判讀
- `portfolio.csv` 是本機私有檔，不進 git
- `theme_watchlist_daily/portfolio_report.md` 與 `portfolio_report.html` 由 `portfolio_check.py` 產生
- 資料抓取目前支援 provider fallback：
  - 預設主來源：`yahoo`
  - 預設備援：`finmind`
  - 預設會開啟記憶體 cache 與磁碟 history cache
  - 磁碟 history cache 會依台股 / 美股各自的 market close 節奏判斷是否可重用；遇到不確定的 weekday 會保守地重抓
  - 可用 env 覆寫：
    - `STOCK_DATA_PROVIDER`
    - `STOCK_DATA_FALLBACKS`
    - `FINMIND_TOKEN`（可選）
    - `ENABLE_HISTORY_CACHE`
    - `ENABLE_DISK_HISTORY_CACHE`

- 每次 `daily_theme_watchlist.py` 執行後也會更新：
  - `theme_watchlist_daily/runtime_metrics.md`
  - `theme_watchlist_daily/runtime_metrics.json`
  - 可快速看 warmup / watchlist / backtest / notifications 各階段耗時
  - 也會顯示 history cache、disk cache、superset cache 命中情況
  - `backtest_state.json` 會記錄 incremental backtest 的模式與掃描範圍
  - daily rank / report 內的 `spec_risk` 現在會進一步拆出 `spec_risk_note`，方便辨識「急漲、爆量、乖離大、缺少結構支撐」這類疑似炒作型態
- 每次 `portfolio_check.py` 執行後也會更新：
  - `theme_watchlist_daily/portfolio_runtime_metrics.md`
  - `theme_watchlist_daily/portfolio_runtime_metrics.json`
  - 用來看持股檢查的 market / watchlist / report / print 各階段耗時
- 每次 `verification/run_daily_verification.py` 執行後也會更新：
  - `verification/watchlist_daily/runtime_metrics.md`
  - `verification/watchlist_daily/runtime_metrics.json`
  - 用來看 verify / evaluate / summary / feedback 各階段耗時，以及 verification cache 檔數/容量

新增：
- daily_report.md 內含 Grade 對照表
- Telegram 推播前面會先給你一段盤面總結
- theme_watchlist_daily/alert_tracking.csv 會追蹤提醒後 1D / 5D / 20D 表現
