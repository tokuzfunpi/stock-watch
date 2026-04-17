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

## 檔名命名原則

目前 repo 已經把舊的版本尾巴檔名拿掉，未來維護請盡量維持簡潔命名：

- `daily_theme_watchlist.py`
- `config.json`
- `watchlist.csv`
- `portfolio.csv`
- `portfolio.csv.example`
- `README.md`
- `.github/workflows/stock-watch.yml`

原則是：

- 不把 `v2.2`、`v3` 這類版本號放在檔名
- 版本演進盡量記在 git history、release note、或 `CODEX_NOTES.md`
- 如果策略真的大改版到無法相容，再考慮分支或新資料夾，不要先用檔名堆版本

## 主要入口

- `daily_theme_watchlist.py`
  - 正式主程式
  - 載入 config 與 watchlist
  - 下載資料
  - 計算排行
  - 產生報表
  - 更新 alert tracking
  - 發送 Telegram
- `portfolio_check.py`
  - 持股檢查專用執行檔
  - 共用 `daily_theme_watchlist.py` 的資料抓取、排行與判讀邏輯
  - 只產生持股專用報表與 CLI 輸出
  - 成功結尾訊息目前降為 `debug`，避免 CLI 多一行狀態字樣
- `backtest_runner.py`
  - 簡單的 CLI 包裝
  - 直接呼叫 `run_backtest_dual()`
  - 印出 `steady` 與 `attack` 回測摘要

## 重要輸入檔

- `config.json`
  - 全域行為設定中心
  - 目前 `always_notify` 是 `true`
  - 大盤濾網、通知門檻、回測 horizon 都在這裡
- `watchlist.csv`
  - 股票池
  - 群組有 `theme`、`core`、`etf`、`satellite`
  - `enabled=false` 的列會被略過
- `portfolio.csv`
  - 本機個人持股檔
  - 目前由 `portfolio_check.py` 使用
  - 建議欄位：
    - `ticker`
    - `shares`
    - `avg_cost`
    - `target_profit_pct`
  - `ticker` 建議只放股票代碼，例如 `2495`、`2330`
  - 程式會自動轉成 Yahoo Finance 用的格式，例如：
    - `2495` -> `2495.TW`
    - `00772B` -> `00772B.TWO`
  - 讀檔時會保留前導零，避免 `0050` / `0052` / `00878` 被誤讀成 `50` / `52` / `878`
  - 如果 `portfolio.csv` 裡的代碼不在 `watchlist.csv`
    - 程式會自動補進 `watchlist.csv`
    - 日常維護時記得把更新 commit / push
- `portfolio.csv.example`
  - repo 內提供的公開範例
  - 真正個人持股請放在本機 `portfolio.csv`
  - `portfolio.csv` 已加入 `.gitignore`，避免再被 push

## 輸出產物

程式會把結果寫到 `theme_watchlist_daily/`：

- `daily_rank.csv`
- `prev_daily_rank.csv`
- `daily_report.md`
- `daily_report.html`
- `portfolio_report.md`
- `portfolio_report.html`
- `alert_tracking.csv`
- `feedback_summary.csv`
- `backtest_events_steady.csv`
- `backtest_events_attack.csv`
- `backtest_summary_steady.csv`
- `backtest_summary_attack.csv`
- `logs/*.csv` 各股票歷史快照
- `last_rank_state.txt`

資料夾中也還看得到像 `backtest_summary.csv`、`backtest_events.csv` 這種舊檔，但目前程式實際使用的是 `steady` / `attack` 分開輸出的版本。

## 主流程

`daily_theme_watchlist.py` 的 `main()` 高層執行順序如下：

1. `get_market_regime()`
2. `run_watchlist()`
3. `run_backtest_dual()`
4. `select_push_candidates()`
5. `upsert_alert_tracking()`
6. `save_reports()`
7. `should_alert()`
8. `send_telegram_message()`
9. `save_last_state()`

`portfolio_check.py` 的流程則是：

1. 讀取本機 `portfolio.csv`
2. `get_market_regime()`
3. `get_us_market_reference()`
4. `run_watchlist()`
5. `save_portfolio_reports()`
6. 在 CLI 印出大盤 / 美股摘要
7. 在 CLI 印出 `持股檢查`

## 訊號與排序重點

- `detect_row()` 是整份策略最核心的計分函式
- `ACCEL` 是偏動能型訊號
- 目前 `ACCEL` 已經和以下兩邊保持一致：
  - attack backtest 納入條件
  - Telegram 候選通知條件
- `run_watchlist()` 逐檔成功訊息目前降為 `debug`
  - 預設 CLI 不再印出每檔 `OK: ticker name`
  - 失敗與摘要仍會保留
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
- `daily_theme_watchlist.py` 不再夾帶 `持股檢查`
- `持股檢查` 改由 `portfolio_check.py` 單獨執行
  - 來源是本機 `portfolio.csv`
  - 會根據成本、目標報酬、當前走勢給出分層建議
  - 不走 Telegram，只留本機檔案與 CLI 顯示
  - 目前常見標籤：
    - `強勢續抱`
    - `續抱`
    - `續抱觀察`
    - `中性觀察`
    - `達標續抱`
    - `達標可落袋`
    - `轉弱留意`

## Alert Tracking 重點

- `alert_tracking.csv` 用來追蹤通知後的 1D / 5D / 20D 表現
- `alert_date` 必須使用市場資料日期，不能用電腦當下日期
- 現在程式使用 `r["date"]`
  - 這樣週末、假日、盤後重跑時，之後的 forward return 才找得到對應資料列
- 現在 `alert_tracking.csv` 也會一起記：
  - `watch_type`
  - `action_label`
  - `feedback_score`
  - `feedback_label`
  - 目的是讓後面的自我校正邏輯可以回頭看「哪種建議最近比較有用」

## 自我校正 / Feedback Loop

這個 repo 現在已經有第一版的「自我校正」機制，目標不是做 ML，而是每天根據過去 alert 的實際結果，對排序做小幅調整。

核心檔案：

- `daily_theme_watchlist.py`

核心輸出：

- `theme_watchlist_daily/alert_tracking.csv`
- `theme_watchlist_daily/feedback_summary.csv`
- `daily_report.md` / `daily_report.html` 裡的 `Prediction Feedback`

### 目前做法

1. 每次送出通知時，`alert_tracking.csv` 會記下：
- 這檔是 `short` 還是 `midlong`
- 當時對它給的 `action_label`
- 當時套用到的 `feedback_score` / `feedback_label`

2. 之後每天重跑時，系統會回填 future return：
- `short` 優先看 `5D`，沒有就退回 `1D`
- `midlong` 優先看 `20D`，沒有就退回 `5D` / `1D`

3. `build_feedback_summary()` 會把歷史資料整理成：
- `watch_type`
- `action_label`
- `samples`
- `win_rate_pct`
- `avg_return_pct`
- `feedback_score`
- `feedback_label`

4. `apply_feedback_adjustment()` 會在原本排序完成後，再根據：
- `watch_type`
- `action_label`
去查最近這類建議的 `feedback_score`

5. 排序只做「小幅前後調整」
- 不會整個推翻原本技術面/風險邏輯
- 目前是用 `feedback_score` 再做一次穩定排序
- 原本的 base order 仍然保留，因此這是一層微調，不是完全重排

### 公式概念

目前 `feedback_score` 的想法很簡單：

- 先看最近這種建議的勝率是否高於 50%
- 再看平均報酬率是否為正
- 樣本太少時，效果會被縮小

目前程式裡的縮放概念是：

- `((win_rate_pct - 50) / 10 + avg_return_pct / 5) * shrink`
- `shrink = min(samples / 8, 1)`

也就是：

- 樣本少時，不要太相信
- 樣本夠多時，再慢慢放大影響

### Feedback Label 解讀

- `樣本不足`
- `中性`
- `近期有效`
- `近期偏弱`

這個 label 目前主要用在：

- `feedback_summary.csv`
- `Prediction Feedback` 報表區塊

### 很重要的維護原則

- 這個 feedback loop 目前是「輕量輔助」，不是主策略
- 不要一下子把 `feedback_score` 權重放太大
- 不要讓它直接凌駕：
  - `setup_score`
  - `risk_score`
  - `signals`
  - `spec_risk_label`

如果未來要調強，建議一小步一小步做。

### 如果未來 AI 要接手，優先注意這幾點

1. 先確認 `alert_tracking.csv` 欄位沒有被改壞
2. 再確認 `history_target_return()` 對 `short` / `midlong` 的 horizon 選擇還合理
3. 確認 `feedback_score` 仍然只是微調，而不是變成主排序
4. 如果樣本很少，不要過度解讀 `feedback_summary.csv`
5. 如果要升級成更進階模型，先把回測和 walk-forward 驗證補齊

### 下一步如果要升級，建議方向

- 把 `early_gem` 也納入獨立 feedback 類型
- 除了 `action_label`，也一起看：
  - `signals`
  - `group`
  - `layer`
- 做近 20 筆 / 60 筆的滾動視窗，而不是全部歷史混在一起
- 把 `feedback_score` 顯示進 Telegram 但先只做摘要，不要把訊息變吵

## GitHub Actions 重點

workflow 檔案：

- `.github/workflows/stock-watch.yml`

目前排程：

- `37 0 * * 1-5`
- 對應台灣時間平日 `08:37`

目前 workflow 會做的事：

1. 安裝 `requirements.txt`
2. 跑 `python -m unittest discover -s tests`
3. 執行主程式
4. commit 產出的 artifacts 並 push

目前 workflow 只跑主程式：

- `python daily_theme_watchlist.py`

如果之後想把持股檢查也自動化，應另外加一個明確步驟或獨立 workflow，不要再混回主流程。

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
python3 -m py_compile daily_theme_watchlist.py portfolio_check.py backtest_runner.py tests/test_core.py
python3 -m unittest discover -s tests
python3 backtest_runner.py
python3 daily_theme_watchlist.py
python3 portfolio_check.py
```

依賴套件目前記在：

```bash
requirements.txt
```

目前 `requirements.txt` 的定位是：

- 新環境安裝入口
- 給 workflow 與其他人 clone repo 後直接安裝
- 只保留實際執行需要的核心依賴，避免放太多暫時性套件

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
