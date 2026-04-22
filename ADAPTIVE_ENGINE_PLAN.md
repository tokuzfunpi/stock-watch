# Adaptive Trading Engine Roadmap (2026-04-22)

這個計畫旨在將 `stock-watch` 轉向環境感知與自我校正的動能交易系統。

## Phase 1: 數據閉環與量化驗證 (The Foundation)
**關鍵點：** 在進行行為變更前，透過數據確認現有邏輯的有效性。

1.  **量化 Heat Bias 衝擊**：
    *   在 `summarize_outcomes.py` 中實作 `hot` vs `normal` 的勝率/報酬差。
    *   目的：確認熱盤時的推薦是否僅是大盤抬轎（High 1D return but poor 5D/20D outcome）。
2.  **價位帶有效性驗證 (Price-band Verification)**：
    *   分析 `alert_tracking.csv` 的 `add_price` (加碼) 與 `stop_price` (失效)。
    *   目的：驗證 ATR 調節後的價位是否真的捕捉到買點，或有效過濾掉轉弱標的。
3.  **Action-level Delta 分析**：
    *   比較 `reco_status=ok` 與 `below_threshold` (強制補滿檔數用) 的實質表現差異。

## Phase 2: 反饋機制升級 (Intelligence Refinement)
**關鍵點：** 引入「期望值」思維，讓系統學習市場近期的脾氣。

1.  **P/L Ratio 正式納入評分公式**：
    *   將盈虧比從 tie-breaker 提升為 `feedback_score` 的核心變數。
    *   公式預想：`score = (win_rate_component + return_component + pl_ratio_bias) * shrink`。
2.  **Rolling Window Feedback**：
    *   將反饋計算從全歷史改為「近 60 筆」或「近 30 交易日」。
    *   目的：避免半年前的舊行情干擾當前的參數判定。

## Phase 3: 自動化防呆與限流 (Safety Valve)
**關鍵點：** 將「警告文字」轉化為「系統保護行為」。

1.  **基於 Heat Bias 的推播限流**：
    *   當 `heat_bias_message` 判定為「極度過熱」或「明顯修正盤」時，自動縮減推薦檔數（例如 Top 5 -> Top 2 或 0）。
2.  **情境感知持股管理自動化**：
    *   讓 `portfolio_check.py` 根據 scenario 自動調節出場節奏（例如修正盤時，停利點自動收緊，主動建議先降部位）。

## Phase 4: 中長線與細節調優 (Fine-tuning)
**關鍵點：** 基於大樣本進行最後的閾值校準。

1.  **20D 門檻校準**：根據長期累積的 outcomes，調整 midlong 訊號的 `setup_score` 與 `risk_score` 門檻。
2.  **ATR 選股聯動**：評估是否讓 ATR 直接參與 `detect_row` 的初步篩選，排除波動過於混亂的標的。
