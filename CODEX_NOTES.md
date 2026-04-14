# Codex 維護筆記

這份文件是給之後維護這個 repo 時快速接手用的，不是對外 README。重點是把執行路徑、設定、輸出和已知風險整理清楚，避免每次都要重新讀完整份程式。

## Repo 目的

這個 repo 會建立台股題材觀察名單流程，主要做這幾件事：

- 透過 `yfinance` 下載 Yahoo Finance 日線資料
- 計算技術指標與訊號標記
- 每日產生排行
- 輸出 markdown、html、csv 報表
- 視條件送出 Telegram 通知
- 跑兩種回測：`steady` 與 `attack`

## 主要入口

- `daily_theme_watchlist_20d_v22.py`
  - 正式主程式
  - 載入 config 與 watchlist
  - 下載資料
  - 計算排行
  - 產生報表
  - 更新 alert tracking
  - 發送 Telegram
- `backtest_runner.py`
  - 簡單的 CLI 包裝
  - 直接呼叫 `run_backtest_dual()`
  - 印出 `steady` 與 `attack` 回測摘要

## 重要輸入檔

- `config_20d_v22.json`
  - 全域行為設定中心
  - 目前 `always_notify` 是 `true`
  - 大盤濾網、通知門檻、回測 horizon 都在這裡
- `watchlist_20d_v22.csv`
  - 股票池
  - 群組有 `theme`、`core`、`etf`、`satellite`
  - `enabled=false` 的列會被略過

## 輸出產物

程式會把結果寫到 `theme_watchlist_daily/`：

- `daily_rank.csv`
- `prev_daily_rank.csv`
- `daily_report.md`
- `daily_report.html`
- `alert_tracking.csv`
- `backtest_events_steady.csv`
- `backtest_events_attack.csv`
- `backtest_summary_steady.csv`
- `backtest_summary_attack.csv`
- `logs/*.csv` 各股票歷史快照
- `last_rank_state.txt`

資料夾中也還看得到像 `backtest_summary.csv`、`backtest_events.csv` 這種舊檔，但目前程式實際使用的是 `steady` / `attack` 分開輸出的版本。

## 主流程

`main()` 的高層執行順序如下：

1. `get_market_regime()`
2. `run_watchlist()`
3. `run_backtest_dual()`
4. `select_push_candidates()`
5. `upsert_alert_tracking()`
6. `save_reports()`
7. `should_alert()`
8. `send_telegram_message()`
9. `save_last_state()`

## 訊號與排序重點

- `detect_row()` 是整份策略最核心的計分函式
- `ACCEL` 是偏動能型訊號
- 目前 `ACCEL` 已經和以下兩邊保持一致：
  - attack backtest 納入條件
  - Telegram 候選通知條件
- 最終排行排序依序偏重：
  - `setup_score`
  - `ret5_pct`
  - `volume_ratio20`
  - `ret20_pct`
  - `risk_score`

## 通知邏輯

- Telegram 只有在下面兩個 env var 都存在時才會真的送：
  - `TELEGRAM_TOKEN`
  - `TELEGRAM_CHAT_IDS`
- 目前 config 的 `always_notify=true`
  - 代表 state 沒變也照樣送
  - 但訊息內容仍然只會從 `select_push_candidates()` 選出的標的組成

## Alert Tracking 重點

- `alert_tracking.csv` 用來追蹤通知後的 1D / 5D / 20D 表現
- `alert_date` 必須使用市場資料日期，不能用電腦當下日期
- 現在程式使用 `r["date"]`
  - 這樣週末、假日、盤後重跑時，之後的 forward return 才找得到對應資料列

## GitHub Actions 重點

workflow 檔案：

- `.github/workflows/stock-watch-20d-v22.yml`

目前排程：

- `37 0 * * 1-5`
- 對應台灣時間平日 `08:37`

目前 workflow 會做的事：

1. 安裝 `requirements.txt`
2. 跑 `python -m unittest discover -s tests`
3. 執行主程式
4. commit 產出的 artifacts 並 push

GitHub 排程要注意：

- GitHub cron 一律用 UTC
- schedule 只會跑在 default branch
- schedule 可能因為 GitHub 高負載延遲甚至掉單
- `workflow_dispatch` 能跑，不代表 `schedule` 一定準時觸發

## 本機執行方式

這台機器建議使用 `python3`：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 -m py_compile daily_theme_watchlist_20d_v22.py backtest_runner.py tests/test_core.py
python3 -m unittest discover -s tests
python3 backtest_runner.py
python3 daily_theme_watchlist_20d_v22.py
```

依賴套件目前記在：

```bash
requirements.txt
```

## 環境變數

程式目前會使用的 env var：

- `CONFIG_PATH`
- `WATCHLIST_CSV`
- `OUTDIR`
- `TELEGRAM_TOKEN`
- `TELEGRAM_CHAT_IDS`
- `HTTP_TIMEOUT`
- `LOG_LEVEL`

## 目前已補的測試

在 `tests/test_core.py` 已經先有最小骨架：

- `select_push_candidates()` 有測 `ACCEL` 標的會被納入
- `split_message()` 有測訊息切段限制

## 已知脆弱點

- 自動化測試目前仍然不夠完整，特別是：
  - `detect_row()`
  - `grade_signal()`
  - 報表內容輸出
- `yfinance` 有時會回空資料或欄位形狀變動
- `run_backtest_dual()` 很吃網路，因為每個 ticker 都各自下載
- workflow 排程異常時，常常不是 YAML 寫錯，而是 GitHub 排程端的問題
- 產生檔會被 workflow commit，看到 artifact diff 很多通常是正常的

## 下一步最值得做的改善

- 幫 `detect_row()` 補更完整的單元測試
- 幫 `grade_signal()` 補測試
- 把更多訊號門檻從 hardcode 拉進 config
- workflow 加一個 debug step，印出 UTC 與台灣時間
- 如果回測越來越慢，可以考慮做資料快取

## 最近已處理的修正

- `backtest_runner.py` 已改成匯入正確的 module 與 backtest function
- 通知候選條件已把 `ACCEL` 和 attack backtest 對齊
- alert tracking 已改成使用 row 的市場日期
- workflow 排程已調整為台灣時間 `08:37`
- config 已改成固定發 Telegram 通知
- workflow 已改成使用 `requirements.txt` 並先跑測試
