# Project Update: 2026-04-22 (Adaptive Strategy & Volatility Awareness)

> Archived design update from `testv`. Use this as historical context and compare against `GEMINI_HANDOFF.md` before assuming the same behavior is present on `main`.
>
> 2026-04-28 note: `portfolio_check.py` was later removed from `main`; the supported portfolio entry is now `python -m stock_watch portfolio`.

## 1. 核心邏輯更新 (Core Logic Enhancements)

### 策略參數化 (Strategy Parameterization)
- **改動**：將原本硬編碼在 `detect_row` 中的技術指標閾值提取至 `config.json`。
- **目的**：方便透過配置檔快速調整訊號強度，無需修改程式碼。
- **新增配置項**：
    - `base_low250_mult`, `base_range20_max`
    - `rebreak_vol_ratio`, `surge_ret20`, `surge_vol_ratio`
    - `trend_ret20`, `accel_ret5`, `accel_vol_ratio`

### 波動率感知價位帶 (Volatility-Adjusted Bands - ATR)
- **改動**：
    - `add_indicators` 新增 ATR14 計算。
    - `watch_price_plan` 引入 `vol_mult` (基於 ATR_Pct / 3%)。
- **效果**：
    - 高波動標的 (⚡ 劇烈)：自動拉深回檔買點，並拉遠止損距離。
    - 低波動標的 (🧊 穩健)：提供更精確、緊湊的交易區間。

### 情境感知自適應門檻 (Scenario-Aware Adaptivity)
- **改動**：新增 `adjust_strategy_by_scenario` 函數。
- **效果**：
    - **明顯修正盤**：自動提高成交量與轉強門檻，過濾假突破，減少空頭市場的無效交易。
    - **強勢延伸盤**：適度放寬門檻，捕捉動能。

## 2. 反饋機制優化 (Feedback Loop Improvements)

### 盈虧比導向排序 (P/L Ratio Integration)
- **改動**：`build_feedback_summary` 新增 `pl_ratio` 計算。
- **目的**：反饋評分 (`feedback_score`) 現在同時考慮「勝率」與「盈虧比」。
- **優點**：即使勝率普通，但若該類訊號具備「大賺小賠」特性，其推薦權重會自動提升。

### 數據結構擴展
- `alert_tracking.csv` 現在會記錄：
    - `scenario_label`：當時大盤的情境標籤。
    - `add_price`, `trim_price`, `stop_price`：當時算出的動態價位。

## 3. 使用者體驗與通報優化 (UX & Notifications)

### 股性視覺化標籤 (Volatility Tags)
- **改動**：Telegram 訊息中每檔標的後方新增 Emoji 標籤。
    - `🧊 穩健` (ATR < 2%)
    - `⚖️ 標準` (ATR 2-4%)
    - `🔥 活潑` (ATR 4-6.5%)
    - `⚡ 劇烈` (ATR > 6.5%)

### 熱度偏誤警示 (Heat Bias Warning)
- **改動**：大盤摘要訊息新增 Heat Bias 偵測。
- **效果**：當前排標的過熱且處於高檔盤勢時，自動噴出「⚠️ 注意：Heat Bias 偏強」或「警訊：極度過熱」的提醒。

## 4. 檔案變動清單
- `daily_theme_watchlist.py`: 核心邏輯重構、新增指標與報表邏輯。
- `portfolio_check.py`: 當時同步更新以支援自適應策略調整；目前 `main` 已改由 `python -m stock_watch portfolio` / `stock_watch/workflows/portfolio.py` 承接。
- `config.json`: 新增 `strategy` 區塊。
- `test_my_logic.py`: (新) 供開發者驗證 ATR 與情境邏輯的單元測試。

## 5. 下一步建議
1. **熱度偏誤量化**：在 `summarize_outcomes.py` 中直接計算 `hot` vs `normal` 樣本的勝率差。
2. **價位帶回測驗證**：利用 `alert_tracking.csv` 的數據驗證 ATR 調節後的價位是否真的提高了獲利能力。
