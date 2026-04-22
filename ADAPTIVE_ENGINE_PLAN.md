# Adaptive Trading Engine Roadmap (2026-04-22)

這個計畫旨在將 `stock-watch` 轉向環境感知與自我校正的動能交易系統。

## Phase 1: 數據閉環與量化驗證 (The Foundation) - [Current Focus: Core Done]
**關鍵點：** 在進行行為變更前，透過數據確認現有邏輯的有效性。

1.  **量化 Heat Bias 衝擊**：
    *   `summarize_outcomes.py` 已提供 `hot vs normal`、`scenario + heat`、`date + heat` 的彙總表。
    *   已知發現：Midlong 5D 在 `hot` 與 `normal` 盤勢之間有明顯落差，代表市場熱度仍是現階段策略表現的重要驅動。
2.  **情境標籤閉環**：
    *   `reco_snapshots.csv` / backfill 現在正式保留 `scenario_label`。
    *   目的：讓 snapshot -> outcome -> summary 能完整串起來。
3.  **價位帶有效性驗證 (Price-band Verification)**：
    *   分析 `alert_tracking.csv` 的 `add_price` (加碼) 與 `stop_price` (失效)。
    *   目的：驗證 ATR 調節後的價位是否真的捕捉到買點，或有效過濾掉轉弱標的。
4.  **Action-level Delta 分析**：
    *   比較 `reco_status=ok` 與 `below_threshold` (強制補滿檔數用) 的實質表現差異。

## Phase 2: 反饋機制升級 (Intelligence Refinement) - [Codex Started]
**關鍵點：** 引入「期望值」思維，讓系統學習市場近期的脾氣。

1.  **P/L Ratio 正式納入評分公式**：(✅ Codex 已初步整合)
    *   公式：`score = (win_rate_component + return_component + pl_ratio_bias) * shrink`。
2.  **Rolling Window Feedback**：(✅ Codex 已初步整合)
    *   加入 `recency weighting`。後續應觀察在「明顯修正盤」中，近況權重是否會過快導致系統「恐懼」。

## Phase 3: 自動化防呆與限流 (Safety Valve) - [Partially On Main]
**關鍵點：** 將「警告文字」轉化為「系統保護行為」。

1.  **基於 Heat Bias 的推播限流**：
    *   `main` 現在已在短線候選池落地：
      - `明顯修正盤`：`top_n_short` 最多 1
      - `Heat Bias 偏強`：`top_n_short` 最多 2
2.  **情境感知持股管理自動化**：
    *   `portfolio_check.py` 的文字建議已依 scenario 調整，但更深的 `trim_price` / 出場價格自動化還沒做。

## Phase 4: 中長線與細節調優 (Fine-tuning)
**關鍵點：** 基於大樣本進行最後的閾值校準。
