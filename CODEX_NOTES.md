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
