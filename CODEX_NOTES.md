# Codex 維護筆記

這份文件是給之後維護這個 repo 時快速接手用的，不是對外 README。重點是把執行路徑、設定、輸出和已知風險整理清楚，避免每次都要重新讀完整份程式。

## 協作預設

- 這個 repo 的預設協作節奏：只要改動已驗證且適合入庫，就直接 `commit + push`
- 不需要每次再額外詢問一次是否要提交，除非使用者明確說先不要 push
- 若工作樹還混有使用者自己的未整理資料檔，先拆出本次改動範圍後再提交，不要把無關檔案一起推上去

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
  - `scenario_policy` 可調整：
    - 修正盤短線上限
    - Heat Bias 偏強時短線上限
    - 修正盤中長線上限
    - 修正盤樣本提醒門檻
    - 新加入追蹤股摘要顯示上限
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
  - 自動補進或已存在但還是 placeholder 名稱時，程式會盡量用 Yahoo Finance metadata 補正式名稱
    - 台股會先試官方市場 metadata，再試 Yahoo 台股頁面的中文標題，最後才 fallback 到 Yahoo Finance metadata
    - 例如 `2412` 不再只顯示 `2412`
- `portfolio.csv.example`
  - repo 內提供的公開範例
  - 真正個人持股請放在本機 `portfolio.csv`
  - `portfolio.csv` 已加入 `.gitignore`，避免再被 push
  - `chat_ids`
    - 本機保留的 Telegram chat id 筆記
    - 已加入 `.gitignore`
  - `chat_id_map.csv`
    - 本機保留的 `chat_id` 和使用者對照表
    - repo 內有 `chat_id_map.csv.example` 作為格式範本
    - 已加入 `.gitignore`
  - `telegram_getupdates_url`
    - 本機保留的 Telegram `getUpdates` URL
    - 已加入 `.gitignore`
  - `theme_watchlist_daily/portfolio_report.md`
  - `theme_watchlist_daily/portfolio_report.html`
  - `theme_watchlist_daily/.yfinance_cache/`
    - 這三個也屬於本機私有 / cache 輸出，已加入 `.gitignore`

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
- `TELEGRAM_CHAT_IDS` 目前支援雙來源：
  - 先讀 env
  - env 沒設時，再讀本機 `chat_ids`
- `chat_ids` 檔案格式可用：
  - 一行一個 id
  - 或逗號分隔
- `update_chat_id_map.py`
  - 本機小工具，用 Telegram `getUpdates` 自動更新 `chat_id_map.csv`
  - 來源優先順序：
    - `TELEGRAM_GETUPDATES_URL`
    - `TELEGRAM_TOKEN`
    - 本機 `telegram_getupdates_url`
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

## 2026-04-22 更新紀錄（給未來 Codex 接手）

這一段是今天密集調整後的交接摘要，目標是讓未來接手的人不用重翻整串對話。

### 一、今天完成的功能更新

#### 1) Verification / outcomes 分析線

- `verification/evaluate_recommendations.py`
  - 新增 `market_heat` / `market_heat_reason`
  - 依 `ret5_pct` / `ret20_pct` / `risk_score` / `volume_ratio20` 將樣本標成：
    - `normal`
    - `warm`
    - `hot`
  - 舊的 `reco_outcomes.csv` 重新跑 `evaluate` 後，也會自動補上這些欄位

- `verification/summarize_outcomes.py`
  - 新增 `Overall By Market Heat (all dates)`
  - `Notes` 補上：若 `hot/warm` 樣本很多，近期績效可能被強勢盤墊高
  - 既有功能仍保留：
    - `Coverage By Horizon`
    - `Overall By Signal + reco_status`
    - `Delta (ok - below_threshold) By Signal`
    - `Delta ... By Signal Date`
    - `Weekly Checkpoint (min_n>=5)`

- 驗證解讀（目前最新共識）
  - `short` 主看 `5D`
  - `midlong` 主看 `20D`
  - `1D` 只作輔助觀察，不單獨當策略成敗依據
  - 目前 `20D` 仍需時間慢慢累積 OK rows；不要因為 1D / 5D 漂亮就過度調 midlong

#### 2) 市場情境判斷（大盤 4 情境）

- `daily_theme_watchlist.py`
  - 新增 `build_market_scenario(...)`
  - 每日會把盤勢分成 4 類：
    - `強勢延伸盤`
    - `高檔震盪盤`
    - `權值撐盤、個股轉弱`
    - `明顯修正盤`
  - 判斷依據：
    - `market_regime`（`ret20_pct` / `volume_ratio20` / `is_bullish`）
    - 美股前一晚摘要
    - 今日候選池前段樣本的熱度 / 強度分布

- `build_macro_message(...)`
  - Telegram / CLI 的 macro 訊息現在會多帶：
    - `盤勢情境`
    - `操作重點`
    - `出場提醒`

#### 3) 持股檢查升級成 scenario-aware

- `portfolio_advice_label(...)`
  - 不再只看個股自身報酬 / 風險，也會依當日盤勢情境調整建議

- `build_portfolio_message(...)`
  - 現在會先顯示：
    - `持股節奏`
    - `今天重點`

- 持股建議語氣已升級成更接近交易管理：
  - `分批落袋`
  - `續抱但設停利`
  - `續抱但盯盤`
  - `有賺先收一點`
  - `先降部位`
  - `保守觀察`

#### 4) 持股分類（進攻 / 核心 / 防守）

- 新增 `holding_style_label(...)`
  - `進攻持股`：題材 / 高波動 / `ACCEL` 類
  - `核心持股`：主流趨勢股 / 核心權值 / 結構較穩的中線股
  - `防守持股`：ETF / 金融 / 債券

- 持股訊息現在會直接顯示：
  - `[進攻持股]`
  - `[核心持股]`
  - `[防守持股]`

#### 5) 觀察股 / 追蹤股價位帶（非單一預測價）

- 新增：
  - `watch_price_plan(...)`
  - `watch_price_plan_text(...)`

- 每檔追蹤股現在會有三個價位：
  - `加碼參考價`
  - `減碼參考價`
  - `失效價`

- 定義：
  - `加碼參考`：拉回到這附近才考慮補
  - `減碼參考`：漲到這附近可先收一部分
  - `失效`：跌到這附近代表原本這次追蹤邏輯要重看

- 目前已接入：
  - `短線可買`
  - `短線觀察`
  - `中長線可布局`
  - `中長線觀察`
  - `早期轉強觀察`
  - `daily_report.md`
  - `alert_tracking.csv`（新增 `add_price` / `trim_price` / `stop_price`）

#### 6) 價位帶也已分持股風格

`watch_price_plan(...)` 現在不再所有股票共用同一套算法，而是先看 `holding_style`：

- `進攻持股`
  - `add_price` 更低：等更深回檔
  - `trim_price` 更近：較早收
  - `stop_price` 更緊：做錯不要拖

- `核心持股`
  - 介於進攻與防守之間，偏平衡

- `防守持股`
  - `add_price` 較高：小回檔即可觀察
  - `trim_price` 較近：偏配置思維，不預期大幅噴出
  - `stop_price` 不走激進波動邏輯

#### 7) ATR 已輕量接進價位帶（但不碰選股）

- `daily_theme_watchlist.py`
  - `add_indicators()` 已新增：
    - `ATR14`
    - `ATR_Pct`
  - `detect_row()` 已新增輸出：
    - `atr_pct`
    - `volatility_tag`

- `watch_price_plan(...)`
  - 現在會依 `atr_pct` 對價位帶做輕量調整
  - 目前設計原則非常重要：
    - **只影響 `add_price` / `stop_price`**
    - **不影響 `trim_price`**
    - **不影響選股、分數、排序**

- 目前 ATR 輕量接法：
  - 高波動標的：`add_price` 更深、`stop_price` 更寬
  - 低波動標的：`add_price` / `stop_price` 稍微收斂
  - 目的只是讓價位帶更貼近股性，不是改策略核心

- 維護原則：
  - 若未來要再讓 ATR 影響進出場，優先先從價位帶做小步驗證
  - 不要直接讓 ATR 進入 `detect_row()` 改推薦結果，除非已有 outcomes / feedback 證據支持

### 二、今天確認過的策略共識

#### 1) Short 不做「可追」

目前 short 的主邏輯已收斂成：

- 真正可買主池：幾乎只保留 `等拉回`
- 其它 action（例如 `開高不追` / `分批落袋` / `續追蹤`）主要是風險提示或觀察用途

這是因為目前驗算結果顯示：

- `short / 等拉回` 最穩
- `可追` 容易在 1D 被震盪洗掉

#### 2) 英業達（2356）案例解讀

今天有特別釐清 `2356.TW 英業達`：

- 它會出現在 `早期轉強觀察`
- 也會在 short 那側被看見
- 但**不是 short 主推可買股**

原因：

- 它屬於 `short_attack`
- `grade=A`, `setup_score=12`, `risk_score=1`
- `ret5=6.17`, `ret20=10.63`, `volume_ratio20=1.57`
- `signals=ACCEL`

所以它是：

- 有動能
- 有潛力
- 已開始轉強
- 但還比較像「觀察升級中的候選股」

#### 3) `等拉回` 的實際定義

目前在這套系統裡，`等拉回` 的白話定義是：

- **標的是對的**
- **但現在不是最舒服的追價點**
- **要等價格回到更合理的位置再處理**

這不是「不看好」，而是：

- 可以列入 short 主池
- 但執行上不鼓勵直接追現價
- 要等回檔、整理、量縮或靠近支撐再看

目前 short 主邏輯的核心共識：

- short 主看 `5D`
- `等拉回` 是主策略
- `1D` 只看噪音/延續性，不拿來單獨定策略生死

### 三、`testv` / `GEMINI` 分支如何理解

今天有額外比對過 `testv` branch 與其中的：

- `GEMINI.md`
- `GEMINI_UPDATES_2026_04_22.md`

這兩份檔的定位不是 `main` 目前的既成事實，而比較像：

- `testv` 的設計藍圖
- 下一代自適應策略引擎的方向說明

#### 1) 已經在 `main` 落地的 GEMINI 方向

- `StrategyConfig` / `config.strategy`
- `ATR14` / `ATR_Pct` / `volatility_tag`
- ATR 輕量進價位帶
- `build_market_scenario(...)`
- `holding_style_label(...)`
- style-specific price bands
- verification / outcomes / delta / weekly checkpoint
- `market_heat`

#### 2) 只算部分落地的

- scenario-aware adaptivity
  - `main` 有 scenario-aware 的通知與持股建議
  - 但**還沒有正式讓 scenario 動態改 `detect_row()` 的選股門檻**
- feedback ranking
  - `main` 有 feedback / outcomes 線
  - 但**還沒有把 `feedback_score` 變成每日推薦排序主權重**
- portfolio / watchlist 共用同一套完整 adaptive engine
  - 方向上正在靠近，但還沒完全統一

#### 3) 還沒落地、先不要直接搬的

- `adjust_strategy_by_scenario()` 直接影響選股門檻
- `feedback_score` 成為 final push candidate 主排序依據
- 完整 Heat Bias 警示進入每日推薦主流程
- `scenario_label` / 價位帶 / feedback 完整閉環直接驅動 daily ranking
- 直接整包 merge `testv` 的 `daily_theme_watchlist.py`

#### 4) 目前建議的整合態度

一句話：

- **把 `GEMINI` 當成設計參考，不要當成 `main` 已全面採納的規格**

實作上要維持：

- 小步吸收
- 先進觀察欄位，再進行為
- 每次改動都能被 verification / outcomes 解讀

### 四、2026-04-22 後續已再往前整合的內容

這一段是晚一點完成、但很關鍵的補充，因為它代表系統已經從「只有觀察」進到「開始小幅改行為」。

#### 1) `scenario_label` 已正式進 `alert_tracking.csv`

- `upsert_alert_tracking(...)` 現在會吃 `market_scenario`
- `alert_tracking.csv` 已新增：
  - `scenario_label`

這表示後續可以直接驗證：

- 哪種盤勢下，`short / 等拉回` 最有效
- 哪種盤勢下，`below_threshold` 比較容易失真
- 哪種盤勢下，推薦結果可能只是大盤抬轎

#### 2) `pl_ratio` 已進 `feedback_summary.csv`

- `build_feedback_summary()` 現在會輸出：
  - `avg_win_return_pct`
  - `avg_loss_return_pct`
  - `pl_ratio`

- `Prediction Feedback` 報表也已顯示 `盈虧比`

目前這一步先是**觀察層**，不是主排序核心。

#### 3) Heat Bias 已完成閉環

目前三端都已經接上：

- 主流程通知
  - `build_macro_message(...)` 會顯示 `Heat Bias` 提醒
- 本機持股 / Telegram / 報表
  - 都已帶 `volatility_tag` / `🧊⚖️🔥⚡`
- verification
  - `verification/summarize_outcomes.py` 已新增：
    - `Overall By Market Heat`
    - `Heat Bias Check (hot - normal)`

解讀原則：

- `hot - normal > 0`
  - 代表熱盤樣本更漂亮，近期績效可能被行情墊高
- `hot - normal < 0`
  - 代表過熱開始傷害延續性，追價風險提高

#### 4) `adjust_strategy_by_scenario()` 已正式進主流程

這是目前最重要的演進。

現在 `main()` 已經改成：

1. 先抓 `market_regime`
2. 先抓 `us_market`
3. 用這兩個資訊建立 `initial_scenario`
4. `adjust_strategy_by_scenario(CONFIG.strategy, initial_scenario)`
5. `run_watchlist(strat=adjusted_strat)`

也就是說：

- `scenario-aware thresholds` **已經正式影響選股**
- 不再只是 report-only preview

但目前仍是**保守版**：

- 只改少數門檻
- 不改 feedback 主排序
- 不整包改策略核心

#### 5) `feedback_score` 已加上 `pl_ratio` tie-breaker

目前 `apply_feedback_adjustment()` 已進一步變成：

- 第一優先：`feedback_score`
- 第二優先：`feedback_pl_ratio`
- 第三優先：原本 base order

這一步非常重要，但仍然克制：

- **不改 `daily_rank.csv` 主排序**
- **只在候選池微調**

也就是：

- `select_short_term_candidates()`
- `select_midlong_candidates()`
- backup candidate 選取

這幾層會更偏好：

- 不只勝率高
- 還更像「大賺小賠」的 action 類型

#### 6) 目前還沒整合的「最後幾塊」

如果之後還要往前推，剩下真正會改行為的大塊主要只有：

- 讓 `pl_ratio` 不只當 tie-breaker，而是直接進 `feedback_score` 公式
- 讓 feedback / P&L 進一步影響 `daily_rank` 主排序
- 讓 ATR 更深地進 `portfolio_advice_label()` 的停利 / 減碼邏輯
- 做更多 `scenario × market_heat × action` 的 outcomes 切片

#### 7) 目前最重要的維護原則

對未來 Codex / Gemini 都一樣：

- 先觀察幾天再做下一步
- 先確認：
  - 候選名單是否變得更合理
  - `5D / 20D` 是否有改善
  - 是否只是讓熱盤時的熱門股更容易被推前

換句話說：

- 現在系統已經不是純靜態規則版
- 但也還沒走到「完全自我學習」的高風險版本
- 目前最好的節奏仍然是：
  - **小步整合**
  - **先有 verification**
  - **再放大到主流程**
- 不是今天最標準的 `等拉回` 主推股

### 三、今天已推上 GitHub 的 commits

依時間順序（只列今天主要功能）：

- `e34770c` — `Add market scenario and heat-aware exits`
- `9cee576` — `Refine style-specific watch price bands`
- `a1956b7` — `Clarify time windows in notifications`

> 注意：remote 上也有 `Update stock watch artifacts [skip ci]` 這類產物 commit，不是主要邏輯變更。

### 四、目前本地文件狀態

- `verification/LOCAL_RUNBOOK.md`
  - 已補：
    - `short = 5D`
    - `midlong = 20D`
    - `1D = 輔助觀察`
    - `進攻 / 核心 / 防守持股` 的白話解讀
    - `加碼參考 / 減碼參考 / 失效` 的白話解讀
  - 這是本機 runbook，未必都已推上 GitHub；若未來需要共享，請確認使用者是否要一起提交

### 五、下次 Codex 最適合接著做的事

以下是高價值、但今天還沒做的後續項目：

1. **Heat Bias Check**
   - 在 `summarize_outcomes.py` 補一段：
     - `hot - normal` 的勝率 / 報酬差
   - 用來直接量化「熱盤把結果墊高多少」

2. **Price-band validation**
   - 用 `alert_tracking.csv` 回頭驗證：
     - `add_price` 是否真的比現價追更好
     - `trim_price` 是否有助於保留報酬
     - `stop_price` 是否能有效避免大回撤

3. **20D threshold tuning**
   - 等 `20D` 的 OK rows 累積到足夠樣本後
   - 再開始調 `midlong` 的 `續抱 / 可分批` 門檻

4. **Action-level delta**
   - 如果要更精細調整，可在 summary 增加：
     - `ok - below_threshold` by `action`
   - 用來找出到底是哪種 action 在稀釋 / 墊高績效

### 六、目前不建議做的事

- 不要因為短期強多盤績效太漂亮就放寬 short 門檻
- 不要用 `1D` 的表現去調 `midlong`
- 不要把 `失效價` 當成「公司完蛋價」；它只是這次交易 / 觀察邏輯的失效點
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
- `.github/workflows/verify-recommendations.yml`（驗算檢查：手動跑）

目前執行方式：

- 只保留 `workflow_dispatch`（全手動觸發），不使用 GitHub `schedule`

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
python3 verification/verify_recommendations.py
python3 verification/evaluate_recommendations.py
python3 verification/summarize_outcomes.py
```

驗算檢查輸出（本機）：

- `verification/watchlist_daily/verification_report.md`
- `verification/watchlist_daily/reco_snapshots.csv`
- `verification/watchlist_daily/reco_outcomes.csv`（需另外跑 evaluate）
- `verification/watchlist_daily/outcomes_summary.md`

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
- workflow 已改為全手動觸發（不使用 GitHub schedule）
- config 已改成固定發 Telegram 通知
- workflow 已改成使用 `requirements.txt` 並先跑測試

## 2026-04-22 補充：`testv/CODEX_HANDOFF.md` 對齊重點

今天另外直接讀過 GitHub 上的 `testv/CODEX_HANDOFF.md`。這份 handoff 和目前 `main` 的整合方向**基本一致**，可當作設計共識參考，但不要把 `testv` 當成可整包 merge 的來源。

### 這份 handoff 的核心訊息

- 採用「**保守版 adaptive strategy**」節奏是正確的
- 核心原則是：**驗算（verification）先行，沒有數據支撐的不進主排序**
- 短線 / 中線的主要 horizon 仍然是：
  - `short = 5D`
  - `midlong = 20D`
  - `1D` 只當輔助，不要追逐 `1D` 假動能
- `testv` 的下一步規劃可概括成兩段：
  - **Phase 1：**量化 `Heat Bias`、驗證 ATR 價位帶是否真的有用
  - **Phase 2：**把 `pl_ratio` 正式納入 `feedback_score`，再考慮 rolling window

### 和目前 `main` 的對照

目前 `main` 已經落地：

- `StrategyConfig` / `config.strategy`
- `scenario-aware thresholds` 已正式進 `run_watchlist()`
- ATR / `volatility_tag`
- ATR 輕量價位帶（只動 `add_price` / `stop_price`）
- `market_heat`、`Heat Bias` 提示與 verification summary 的 `Heat Bias Check`
- `pl_ratio` 已進 `feedback_summary.csv`
- `feedback_score` + `pl_ratio` tie-breaker 已進候選池微調

目前 `main` **還沒有**完全照 `testv` handoff 的更深版本做：

- feedback 直接主導 `daily_rank.csv` 主排序

## 2026-04-22 補充：feedback score 已升級

今天已完成 `testv/CODEX_HANDOFF.md` 提到的 Phase 2 前半段，且已推上 `main`：

- `pl_ratio` 已正式納入 `feedback_score` 公式
- `feedback_score` 已加入保守版 recency weighting
  - `short` 取最近 `12` 筆已完成樣本
  - `midlong` 取最近 `8` 筆已完成樣本
  - 最終分數採：
    - `70% base_feedback_score`
    - `30% recent_feedback_score`

### 目前 feedback 排序的實際狀態

候選池微調目前順序為：

1. `feedback_score`
2. `feedback_pl_ratio`
3. `_base_order`

其中：

- `feedback_score` 已包含：
  - 勝率
  - 平均報酬
  - `pl_ratio`
  - recent weighting
- `feedback_pl_ratio` 仍保留為 tie-breaker

### 這代表什麼

- 系統現在不只看「哪種 action 歷史上勝率高」
- 也會看：
  - 哪種 action 比較像 **大賺小賠**
  - 哪種 action **最近這段行情仍然有效**

### 目前仍維持的邊界

- 這些改動**只影響候選池微調**
- 還**沒有**直接重寫 `daily_rank.csv` 主排序
- 仍然符合：
  - 小步整合
  - 先驗證
  - 不把 adaptive 權重一次放太大

### 觀察到的初步效果

- `short / 等拉回` 仍是最穩主策略，recent score 沒失真
- `midlong / 續抱` 與 `midlong / 可分批` 近期有改善訊號
- 代表 recency weighting 目前看起來是在「校正近況」，不是把排序帶歪

## 2026-04-22 補充：verification 已接上 scenario 切片

今天已把 verification 的 scenario 資料鏈補到「未來可用」的狀態：

- `verify_recommendations.py`
  - 新快照會把當天 `scenario_label` 一起寫進 `reco_snapshots.csv`
- `evaluate_recommendations.py`
  - 會把 snapshot / alert tracking 裡可取得的 `scenario_label` 一起帶進 `reco_outcomes.csv`
  - 如果沒有新 outcome row，也會做 metadata refresh，盡量把 `scenario_label` / `market_heat` 補齊
- `summarize_outcomes.py`
  - 已新增：
    - `Overall By Scenario (all dates)`
    - `Overall By Scenario + Action (all dates, top 80)`

### 目前看到的限制

- 現有歷史 outcomes 幾乎仍是 `scenario_label = unknown`
- 這不是程式壞掉，而是因為：
  - `scenario_label` 是後期才正式接進 verification 流程
  - 舊資料大多沒有可追溯的 scenario metadata

### 維護判斷

這裡先停在「讓新資料自然長出 scenario 切片」是合理的。

不建議現在做的事：

- 用推估方式硬回填舊的 `scenario_label`
- 為了讓報表立刻漂亮，去猜舊資料當時屬於哪個盤勢

因為這會開始進入高風險資料污染區。

更好的做法是：

- 讓新 outcomes 持續累積
- 等 `By Scenario + Action` 有足夠非 `unknown` 樣本後，再決定要不要往主排序加更深的 adaptive 權重

## 2026-04-22 補充：handoff 後續研究方向

目前最適合交給 Gemini / 其他 agent 的，不是直接改 production 排序，而是做下面這幾種分析型工作：

1. **Scenario × Action 研究**
   - 等 `Scenario Coverage` 的 `known_scenario_rate_pct` 長起來後，
   - 分析不同盤勢下哪種 action 最穩

2. **Heat Bias vs Scenario 的拆解**
   - 釐清「熱盤墊高」和「盤勢情境」哪個才是主因
   - 避免把 hot market 的效果誤判成 scenario-aware 邏輯有效

3. **Feedback 權重敏感度**
   - 先做離線比較，不直接改主流程
   - 例如比較：
     - `70/30`
     - `80/20`
     - `60/40`
   - 看 recency weighting 是否過強或過弱

4. **ATR 價位帶回顧**
   - 比較 ATR band 與舊版 band 的 add/stop 合理性

### 目前不建議交出去做的高風險任務

- 用推估方式補舊 `scenario_label`
- 直接讓 feedback 進 `daily_rank.csv` 主排序
- 一次同時大改：
  - scenario thresholds
  - feedback ranking
  - ATR exits

### 維護原則

如果之後還要繼續吸收 `testv` / Gemini 的設計，請維持下面原則：

- **不要整包 merge `testv`**
- **先做 report / verification，再做 ranking**
- **先觀察 `5D` / `20D` 結果，再考慮放大 adaptive 權重**
- **20D 樣本不足時，不要急著重調中線門檻**

## 2026-04-23 補充：Gemini 方向已進一步落成 verification 與操作 guardrails

今天主要做了三類工作：

1. **補文件脈絡**
   - 新增 / 整理：
     - `GEMINI.md`
     - `GEMINI_UPDATES_2026_04_22.md`
     - `TESTV_INTEGRATION_CHECKLIST.md`
   - 重點不是把 `testv` 當可直接 merge 的分支，而是把它降維成：
     - 設計背景
     - 研究題來源
     - 不該回退的 guardrail 清單

2. **把 Gemini 的研究題變成主線 verification 能力**
   - `verification/summarize_outcomes.py`
     - 新增 `Key Findings`
     - 新增 `ATR Band Findings`
     - 新增 `ATR Band Coverage`
     - 新增 `ATR Band Checkpoints`
   - 新增 `verification/feedback_weight_sensitivity.py`
     - 可離線比較 `70/30`、`80/20`、`60/40`
   - 新增 `verification/run_daily_verification.py`
     - 後來又補成支援：
       - `--mode preopen`
       - `--mode postclose`
       - `--mode full`

3. **把 verification 的操作風險補齊**
   - 修掉同一天重跑 `preopen` 會一直 append snapshot 的問題
   - 現在 `verification/verify_recommendations.py`
     - 會用 `signal_date + watch_type + ticker` 做 upsert
   - `verification/backfill_from_git.py`
     - 也同步改成同鍵 upsert
   - `verification/evaluate_recommendations.py`
     - 會先對 snapshots 做去重，再計算 outcomes
   - 已手動清掉本機 `verification/watchlist_daily/reco_snapshots.csv` 的重複資料
     - 從 `120` 筆整理到 `97` 筆
     - 備份檔：`verification/watchlist_daily/reco_snapshots.csv.bak.dedupe.20260423_095839`

### 這代表目前的實際狀態

- `preopen` / `postclose` / `full` 現在都可以安全重跑
- 但「安全重跑」的定義是：
  - **不會把樣本數越堆越亂**
  - **同一天同一檔 snapshot 以最後一次為準**
- 不是：
  - 幫你保留每一次盤前版本快照

### 今天驗證後最重要的分析結論

1. **Gemini 的大方向是對的，但目前最強證據仍是 Heat Bias**
   - 最新 summary 顯示：
     - `1D midlong`
     - `hot - normal = +4.22%`
     - `min_n=11`
     - `confidence=high`
   - 這表示 midlong 近期績效仍高度受熱盤支撐

2. **Scenario 已接上，但還沒有壓過 heat 成為主解釋變數**
   - `強勢延伸盤` 下的 `hot - normal` 仍約 `+4.44%`
   - 所以目前不要把 scenario-aware 邏輯的存在，直接解讀成策略已獨立證明有效

3. **forced-fill 不是現在最該先砍掉的東西**
   - `1D midlong` 的 `ok - below_threshold` 仍約 `-1.68%`
   - 代表 forced-fill 沒有明顯比較差
   - 這不是叫人放寬門檻，而是提醒：
     - 下一步若要調規則，應優先重看 `midlong threshold`

4. **ATR 與 feedback 還在「可觀察、不可急改」階段**
   - ATR：
     - `1D` 有 checkpoint
     - `5D / 20D` band 樣本仍不足
   - feedback：
     - 權重調整會改分數
     - 但目前還不會洗掉 action 排名
   - 所以 production 先維持：
     - ATR 當 verification 報表
     - feedback 維持 `70/30`

### 目前最合理的維護節奏

- production 先不動
- 每天照常跑：
  - 盤前：`python3.11 verification/run_daily_verification.py --mode preopen`
  - 盤後：`python3.11 verification/run_daily_verification.py --mode postclose`
- 繼續累積 `5D / 20D`
- 之後最先重看的，不是 feedback 權重，也不是 ATR exit，而是：
  - `midlong threshold`

### 給之後維護者的一句話

今天之後，Gemini 的價值已不再只是「設計想法」，而是已經有一套可以安全重跑、持續累積、可量化驗證的 verification workflow；但主線策略本身仍應維持保守，不要因短期熱盤結果而過早放寬門檻。

## 2026-04-23 補充：重新檢查 `testv` 最新 Gemini 更新

今天又重新檢查了一次 `testv` branch，確認 Gemini 說的更新已經推上去。

### 目前 branch 狀態

- `testv` 最新 tip：`5afec8b`
- 這次最新 commit 主要更新的是：
  - `ADAPTIVE_ENGINE_PLAN.md`
  - `CODEX_HANDOFF.md`
- 不是新的 production-ready code merge

### 這次重新確認後的結論

1. **Gemini 在 `testv` 最新強調的方向，和 `main` 現在的 verification 結論一致**
   - heat bias 仍是主驅動
   - `midlong threshold` 仍是下一步最值得研究的題目
   - 不應因短期 1D 表現就過早放寬 threshold

2. **`testv` 的 branch code 目前仍不適合直接回灌 `main`**
   - 會回退 local workflow：
     - `run_local_daily.py`
     - `run_local_doctor.py`
     - `run_local_housekeeping.py`
     - `run_weekly_review.py`
   - 也會回退部分 verification 能力與 guardrails

3. **真正值得吸收的是文件裡的「優先順序」**
   - 先保護，再優化
   - 先驗證，再放大 adaptive 權重
   - 先拆 heat bias / threshold，再談大規模 scenario policy

### 這次已同步到 `main` 的整理

- `GEMINI_HANDOFF.md`
  - 補了最新 `testv` re-check 的判讀
- `TESTV_INTEGRATION_CHECKLIST.md`
  - 補了目前仍值得 cherry-pick 的極小清單
  - 也補了這次明確不值得回灌的項目

### 維護判斷

之後如果 Gemini 再說 `testv` 有更新，正確做法仍是：

- 先看它是文件結論還是 code
- 若是文件結論，就整理進 `main` notes
- 若是 code，先看會不會回退 `main` 已有 guardrails
- 原則上仍避免直接 merge `testv`

## 2026-04-25 補充：`spec_risk` 線的最新 handoff

### 這次完成的內容

1. **把 `spec_risk` 從單一分數補成一條可追蹤的分析線**
   - `detect_row()` 現在會產出：
     - `spec_risk_score`
     - `spec_risk_label`
     - `spec_risk_subtype`
     - `spec_risk_note`
     - `spec_risk_flags`
     - `spec_price_action_score`
     - `spec_crowding_score`
     - `spec_extension_score`
     - `spec_structure_score`

2. **`spec_risk` 已串進主要輸出**
   - `daily_report.md/html`
     - 有 `疑似炒作觀察`
   - `verification_report.md`
     - 有 `spec risk counts`
     - 有 `Spec Risk Watchlist`
   - `outcomes_summary.md`
     - 有 `Overall By Spec Risk`
     - 有 `Spec Risk Check`
     - 有 `Overall By Spec Subtype`
   - `weekly_review.md/json`
     - 有 `spec_risk` decision
     - 有 `Spec Risk Highlights`
     - 有 `Overall By Spec Risk`
     - 有 `Overall By Spec Subtype`
   - `local_run_status` / `local_doctor`
     - 也會顯示 `spec_risk_high_rows`
     - `spec_risk_watch_rows`
     - `spec_risk_top_tickers`

3. **legacy 資料也支援 fallback**
   - 舊 snapshot / outcomes 就算沒有 `spec_risk_*` 欄位
   - 現在也會從既有的價量 / risk / signals 欄位回推：
     - bucket
     - subtype
     - note
   - 所以不用先重建整包歷史資料，summary / weekly review 就能先開始工作

### 目前 subtype bucket

- `急拉爆量型`
- `高檔脫離型`
- `結構失配型`
- `急拉追價型`
- `資金擁擠型`
- `高檔無回檔型`
- `一般投機型`
- `正常`

### 現在最重要的判讀

1. **這條線已經有辨識力，但還沒有足夠證據變成 hard filter**
   - live 名單確實能抓到像：
     - `2388.TW`
     - `3661.TW`
     - `3443.TW`
     - `6669.TW`
     - `3017.TW`
   - 這代表 heuristic 方向是對的

2. **verification / weekly review 的樣本還是偏少**
   - `spec_risk high vs normal` 還沒有足夠成熟樣本
   - subtype 目前也還是小樣本
   - 所以 `spec_risk` 現在比較適合作為：
     - 風險提醒
     - 推播保守化參考
   - 還不適合直接拿來當硬排除

3. **目前 weekly review 已經能把「過度解讀小樣本」擋下來**
   - `Spec Risk Highlights` 現在會顯示：
     - most frequent subtype
     - weakest subtype
   - 但也會加上：
     - confidence note
     - same subtype extremes note
   - 避免因為只有 `n=2` 就誤以為某 subtype 已被證明最危險

### 對「要不要補更多標的」的判斷

目前結論是：

- **有需要補，但不應該直接亂擴 watchlist**
- 應該補的是：
  - 更容易產生投機 / 異常價量樣本的候選池
- 不應該只是：
  - 再加更多大型權值 / ETF
  - 或單純為了湊數而加低品質標的

換句話說，下一步真正該做的是：

- `watchlist coverage / candidate mix` 分析
  - 哪些 group 幾乎不會產生 `spec_risk` 樣本
  - 哪些 group 最有機會補到 `high / subtype` 樣本

### 目前最合理的下一步

1. 先做 `watchlist coverage` 分析
2. 再決定要不要擴 universe
3. 如果之後證據還是不夠，再考慮外部資料：
   - `TWSE 注意/處置`
   - 集中度 / 當沖比
   - 新聞 / 基本面失配

### 目前不建議做的事

- 不要現在就把 `spec_risk` 變成硬排除條件
- 不要因為某個 subtype 暫時看起來弱，就直接下 production 規則
- 不要為了補樣本而大幅亂擴 watchlist
- 不要把 `testv` 整包 merge 回來

### 這一輪對後續 Codex 最重要的一句話

如果新對話要繼續接手，最值得延續的方向不是再加更多報表，而是：

- **先回答 coverage 問題，再決定是否擴 candidate universe**

因為目前 `spec_risk` 的主要瓶頸已不是「看不到」，而是「成熟樣本還不夠」。

## 2026-04-25 補充：`spec_risk` coverage / candidate mix 分析

### 這次新增的輸出

- `run_weekly_review.py` 現在除了看近期 outcomes 的 `spec_risk` 外，還會直接吃當前 `theme_watchlist_daily/daily_rank.csv`
- 新增區塊：
  - `Current Rank Spec Risk By Group`
  - `Current Rank Spec Risk By Layer`
  - `Current Suspicious Candidates`

### 目前得到的第一個實際結論

這份 coverage output 很清楚：

- `theme`：18 檔裡有 6 檔是 non-normal `spec_risk`
- `satellite`：7 檔裡有 5 檔是 non-normal `spec_risk`
- `core`：13 檔裡有 3 檔是 non-normal `spec_risk`
- `etf`：7 檔裡是 0

layer 看起來則是：

- `midlong_core`：23 檔裡 8 檔 non-normal
- `short_attack`：18 檔裡 6 檔 non-normal
- `defensive_watch`：4 檔裡 0

### 對「要不要補更多標的」的最新判斷

這讓我們可以把前面的直覺講得更精準：

- **如果目的是補 `spec_risk` 樣本，就不要加 ETF / defensive_watch。**
- 真正值得補的是：
  - `theme`
  - `satellite`
  - 以及會進 `short_attack` / `midlong_core` 的題材型標的

所以未來若要擴 universe，應優先朝：

- 更容易出現異常價量 / 題材波動的台股候選池

而不是：

- 再增加一批低波動大型權值
- 再增加 ETF
- 或為了湊數擴充 defensive watch

### 目前仍然不能做的事

即使 coverage 已經更清楚，production 仍不該立刻把 `spec_risk` 變成硬規則。

原因不是 coverage 不夠，而是：

- 成熟 `high vs normal` outcome 樣本仍然少
- subtype 比較也仍然是小樣本

所以：

- coverage 現在回答的是「該去哪裡補樣本」
- 不是「已經可以下硬結論」

## 2026-04-25 補充：weekly coverage guidance
- `run_weekly_review.py` 新增 `Candidate Mix Guidance`，直接從 `theme_watchlist_daily/daily_rank.csv` 的 `spec_risk` coverage 推出擴池方向。
- 目前 weekly review 的結論是：若要補更多可能標的以累積 `spec_risk` 樣本，優先補 `theme`、`satellite`，以及會流入 `midlong_core`、`short_attack` 的候選；不要為了湊數擴 `etf` 或 `defensive_watch`。
- 這仍然只是 coverage recommendation，不代表 `spec_risk` 已可當 hard filter；成熟 outcome 樣本依然偏薄。

## 2026-04-25 補充：candidate expansion targets
- `run_weekly_review.py` 新增 `Candidate Expansion Targets`，把 coverage guidance 再細化成建議補幾檔的 group/layer heuristic。
- 目前基於現有 `daily_rank.csv`，建議優先補：`satellite +3`、`theme +3`、`core +1`；layer 以 `midlong_core +3`、`short_attack +3` 為主。
- 這是用來提高 `spec_risk` 樣本 coverage 的工程性建議，不代表已經要改 production 策略或把 `spec_risk` 變硬過濾。

## 2026-04-25 補充：source-side expansion analysis
- `run_weekly_review.py` 現在會從 `daily_rank.csv` 推導 `candidate_source` archetype，並在 weekly review 顯示 `Current Rank Spec Risk By Source` 與 `By Source Archetype`。
- 目前 source-side 最值得先補的方向是：`Satellite high-beta leaders`、`Theme trend acceleration`、`Theme momentum burst`。
- 這些 archetype 只是幫我們回答「新增的 theme/satellite 名額比較像該從哪種型態去找」，還不是 production source schema。

## 2026-04-26 補充：watchlist gap snapshot
- `run_weekly_review.py` 現在新增 `Watchlist Gap Snapshot By Group/By Source`，把目前 `watchlist.csv` 的 group 數量與建議擴池目標直接對照。
- 目前 snapshot：`satellite 7 -> 10`、`theme 18 -> 21`、`core 14 -> 15`。
- 這一層已經足夠支撐下一個真正的決策：要不要實際擴 `watchlist`，以及先擴 `satellite/theme` 哪些 archetype。

## 2026-04-26 補充：watchlist addition draft
- 新增 `draft_watchlist_additions.py`，會從台股活躍報價池做 best-effort universe scan，套用現有 signal/spec-risk 邏輯，輸出 `theme_watchlist_daily/watchlist_addition_draft.md/json`。
- 本次 draft（2026-04-26）主草案：
  - satellite: `6962.TW 奕力-KY`, `2340.TW 台亞`, `2464.TW 盟立`
  - theme: `6182.TWO 合晶`, `2312.TW 金寶`, `5347.TWO 世界`
  - core: `8064.TWO 東捷`
  - reserve only: `4927.TW 泰鼎-KY`, `8240.TWO 華宏`
- 目前已經到真正需要 user 拍板的地方：要不要把哪幾檔正式加進 `watchlist.csv`。

## 2026-04-26 補充：aggressive watchlist expansion approved
- User approved the aggressive draft and `watchlist.csv` was updated with 7 additions.
- Added tickers:
  - theme: `6182.TWO 合晶`, `2312.TW 金寶`, `5347.TWO 世界`
  - core: `8064.TWO 東捷`
  - satellite: `6962.TW 奕力-KY`, `2340.TW 台亞`, `2464.TW 盟立`
- `run_weekly_review.py` and `draft_watchlist_additions.py` were rerun after the change.
- Current watchlist count is now `55`, and weekly gap snapshot moved to `satellite 10 -> 13`, `theme 21 -> 24`, `core 15 -> 16`.
- The refreshed addition draft now proposes a next wave rather than the already-added names.

## 2026-04-26 補充：new additions priority
- 新增 `/theme_watchlist_daily/new_additions_priority.md`，把 aggressive expansion 加入的 7 檔分成主看 / 觀察 / 高風險觀察。
- 目前排序：
  - 主看：`2312.TW`, `8064.TWO`, `6182.TWO`
  - 觀察：`5347.TWO`
  - 高風險觀察：`6962.TW`, `2340.TW`, `2464.TW`
- 這份筆記適合直接當開盤前的新增名單閱讀順序。
