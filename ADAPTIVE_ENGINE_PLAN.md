# Adaptive Trading Engine Roadmap (2026-04-22)

這個計畫旨在將 `stock-watch` 轉向環境感知與自我校正的動能交易系統。

## Phase 1: 數據閉環與量化驗證 (The Foundation) - [Current Focus: ✅ Core Done]
**關鍵點：** 在進行行為變更前，透過數據確認現有邏輯的有效性。

1.  **量化 Heat Bias 衝擊**：(✅ 已實作於 `summarize_outcomes.py`)
    *   **發現**：Midlong 5D 報酬在 `hot` (15.1%) 與 `normal` (-0.2%) 之間存在巨大斷層。證實「市場熱度」是當前策略表現的主驅動力。
2.  **情境感知追蹤**：(✅ 已修復鏈條)
    *   `scenario_label` 現在能正確在 snapshot -> outcome 中傳遞。今日 (04-22) 已識別為「明顯修正盤」。
3.  **價位帶有效性驗證**：(🛠 待處理)
    *   分析 ATR 調節後的 `add_price` 觸發率與隨後勝率。
4.  **Action-level Delta 分析**：(✅ 已實作)
    *   發現 `below_threshold` 在某些短線情境下表現不輸給 `ok` 樣本，暗示門檻有優化空間。

## Phase 2: 反饋機制升級 (Intelligence Refinement) - [Codex Started]
**關鍵點：** 引入「期望值」思維，讓系統學習市場近期的脾氣。

1.  **P/L Ratio 正式納入評分公式**：(✅ Codex 已初步整合)
    *   公式：`score = (win_rate_component + return_component + pl_ratio_bias) * shrink`。
2.  **Rolling Window Feedback**：(✅ Codex 已初步整合)
    *   加入 `recency weighting`。後續應觀察在「明顯修正盤」中，近況權重是否會過快導致系統「恐懼」。

## Phase 3: 自動化防呆與限流 (Safety Valve) - [Next Critical Step]
**關鍵點：** 將「警告文字」轉化為「系統保護行為」。

1.  **基於 Heat Bias 的推播限流**：
    *   當 Scenario 為「明顯修正盤」或 Heat Bias 過高時，自動將推播檔數 `top_n_short` 從 5 降至 2 或 0。
2.  **情境感知持股管理自動化**：
    *   `portfolio_check.py` 聯動 scenario，在修正盤自動收緊 `trim_price`。

## Phase 4: 中長線與細節調優 (Fine-tuning)
**關鍵點：** 基於大樣本進行最後的閾值校準。
