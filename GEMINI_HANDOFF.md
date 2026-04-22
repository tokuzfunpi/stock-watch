# GEMINI Handoff (2026-04-22)

這份文件是給 Gemini / 其他 agent 快速接手 `stock-watch` 現況用的。重點不是重講全部架構，而是講清楚：

- `main` 現在已經整合到哪裡
- 哪些是正式上線的行為
- 哪些還只是觀察 / 微調層
- 下一步最適合怎麼接

## 1) 現在 `main` 已經不是靜態策略版

目前 `main` 已整合：

- `StrategyConfig` / `config.strategy`
- `ATR14` / `ATR_Pct` / `volatility_tag`
- ATR 輕量進價位帶
- `market_scenario`
- `scenario_label` 進 `alert_tracking.csv`
- `market_heat`
- `pl_ratio` 進 `feedback_summary.csv`
- `Heat Bias` 提示進 Telegram / CLI / verification
- `adjust_strategy_by_scenario()` 已正式進 `run_watchlist()`
- `feedback_score` 已納入 `pl_ratio`
- `feedback_score` 已加入保守版 recency weighting
- `feedback_score` + `feedback_pl_ratio` 已進候選池微調
- verification summary 已新增：
  - `Overall By Scenario`
  - `Overall By Scenario + Action`
  - `Scenario Coverage`

## 2) 目前正式上線、真的會改每日結果的部分

### A. Scenario-aware thresholds 已正式生效

主流程現在是：

1. `get_market_regime()`
2. `get_us_market_reference()`
3. `initial_scenario = build_market_scenario(...)`
4. `adjusted_strat = adjust_strategy_by_scenario(CONFIG.strategy, initial_scenario)`
5. `run_watchlist(strat=adjusted_strat)`

所以：

- `detect_row()` 現在不是用固定門檻
- 會先吃當日 scenario 調整後的 `StrategyConfig`

### B. Feedback 微調已正式生效

目前 `select_*_candidates()` 已經會走：

- `apply_feedback_adjustment(df, watch_type)`

排序順序是：

1. `feedback_score`
2. `feedback_pl_ratio`
3. `_base_order`

注意：

- 這一層**只影響候選池微調**
- **還不影響 `daily_rank.csv` 主排序**
- `feedback_score` 現在已不是單純歷史平均，而是：
  - base score（較長期）
  - recent score（近況）
  - 以保守比例混合

## 3) 目前仍屬觀察層 / 報表層的部分

### A. `pl_ratio`

- 已寫進 `feedback_summary.csv`
- 已顯示在 `Prediction Feedback`
- 已直接進 `feedback_score` 公式
- 仍保留在候選池內作 `feedback_pl_ratio` tie-breaker

目前還沒有進得更深的是：

- `daily_rank.csv` 主排序
- 更積極的 `feedback_score` 權重放大

### B. Heat Bias

- 已有：
  - Telegram / CLI / macro 提示
  - verification 的 `Heat Bias Check (hot - normal)`
  - verification 的 `Heat Bias By Scenario`
  - verification 的 `Heat Bias By Date`

但還**沒有**直接控制主排序或直接淘汰標的。

另外，short 候選池現在已經有保守限流：

- `明顯修正盤`：最多 1 檔 short
- `Heat Bias 偏強`：最多 2 檔 short

### C. ATR

- 已進：
  - `watch_price_plan(...)`
  - `volatility_tag`
  - Telegram / portfolio / report 呈現

但 ATR 目前只影響：

- `add_price`
- `stop_price`

不影響：

- `trim_price`
- `detect_row()` 選股訊號本體

## 4) 目前策略共識（很重要）

### A. short 的主要邏輯

- short 主看 `5D`
- `1D` 只作輔助觀察
- 真正可買主池已收斂成：
  - 幾乎只保留 `等拉回`

其它 action：

- `開高不追`
- `分批落袋`
- `續追蹤`

主要是風險提示 / 觀察用途。

### B. midlong 的主要邏輯

- midlong 主看 `20D`
- 目前 20D 樣本還在持續累積
- 不要因為 1D / 5D 很漂亮就過度放寬 midlong 門檻

## 5) `testv` / `GEMINI.md` 要怎麼看

`testv` 的方向是有價值的，但在現在這個 repo 裡：

- 請把 `GEMINI.md` / `GEMINI_UPDATES_2026_04_22.md`
  - 當作 **設計藍圖**
  - 不要當作 `main` 已全部落地的事實

現在比較正確的理解是：

- `main` 已吸收 GEMINI 的方向
- 但還沒有整包採用 testv 的完整 adaptive engine

## 6) 下一步最推薦做什麼

目前最推薦的是：

### 先觀察，不要立刻再加大行為改動

先觀察幾天：

- 候選名單是否更合理
- `5D / 20D` 結果是否改善
- 是否只是讓熱盤時的熱門股更容易被推前
- `Scenario Coverage` 的 `known_scenario_rate_pct` 是否開始上升

### 若要再往前推，建議順序

1. 觀察新的 `feedback_score`（含 `pl_ratio` + recency weighting）是否讓候選更合理
2. 先累積更多有 `scenario_label` 的新 outcomes，讓 `By Scenario + Action` 真正可用
3. 再評估要不要讓 feedback 進一步影響 `daily_rank` 主排序
4. 最後才考慮讓 ATR 更深地進 `portfolio` 出場邏輯

## 7) 如果要請 Gemini 做研究 / 測試，最值得做的題目

建議 Gemini 先做 **分析型任務**，不要直接改主排序：

1. **`By Scenario + Action` 差異解讀**
   - 當 `known_scenario_rate_pct` 變高後，
   - 分析不同 scenario 下：
     - `等拉回`
     - `開高不追`
     - `可分批`
     - `續抱`
     哪些 action 最穩

2. **Heat Bias 與 Scenario 交叉觀察**
   - 比較：
     - `hot vs normal`
     - `scenario_label`
   - 看哪一個才是真正的主因
   - 避免把熱盤效果誤判成 scenario 效果

3. **Feedback 權重敏感度測試**
   - 不改 production code
   - 只做離線比較：
     - `70/30`
     - `80/20`
     - `60/40`
   - 看候選名單是否變得更穩，還是開始過度追逐近況

4. **ATR band 驗證**
   - 離線比較 ATR band 與舊版 band：
     - `add_price`
     - `stop_price`
   - 看哪一版更接近「合理加碼 / 合理失效」

## 8) 不建議現在直接做的事

- 不要整包 merge `testv`
- 不要一次同時改：
  - scenario thresholds
  - feedback 主排序
  - ATR deeper exits
- 不要為了補歷史資料而用推估方式硬回填舊的 `scenario_label`
- 不要在沒有 verification 支撐時，直接大改 `detect_row()` 核心條件

## 8.5) 明確禁止重帶的改動

這段請直接當 hard guardrails：

1. 不要再把 `vol_tag` 這種未定義變數塞回訊息模板
   - `testv` 曾在 short/midlong Telegram 文字裡插入 `vol_tag`
   - 但那個變數沒有定義，會把通知流程炸掉
   - 如果要改訊息格式，只能重用已存在且有測試覆蓋的 `volatility_badge_text(...)`

2. 不要把新的 Heat Bias 分析寫成「一出錯就整段吞掉」
   - `summarize_outcomes.py` 的 `hot vs normal` / `scenario + heat` 是核心驗證，不是可有可無的 decoration
   - 不要用會把 `heat_bias_check`、`heat_bias_by_scenario` 一起清空的大範圍 `except` 包住錯誤 merge
   - 先讓 merge 寫對，再加最小範圍的防呆

3. 不要宣稱 `portfolio_check.py` 已經做了更深的 scenario exit 自動化
   - 目前主程式只有文字建議與節奏調整
   - 更深層的 `trim_price` / 自動收緊出場價，現在還沒正式落地

4. 不要把文件當成程式已完成的證據
   - `GEMINI.md` / 更新筆記可以是設計方向
   - 但只有 `main` 上真的存在、且測試過的東西，才算已整合能力

5. 不要再改動本機執行環境描述
   - 這個 repo 本機固定用 `/Users/tokuzfunpi/codes/nvidia/311env`
   - 文件與指令請都沿用這個 venv，不要再換回泛用 `python3.11`

## 9) 給 Gemini 的一句話

現在的 `main` 已經開始變成「保守版 adaptive strategy system」，  
但最重要的工作不是再加更多花樣，而是確認：

- 這些改動到底有沒有真的讓 `5D / 20D` 更穩
- 還是只是讓強多盤裡的熱門股更容易被往前排

如果要接手，請維持現在這個節奏：

- 小步整合
- 先驗證
- 再放大到主流程
