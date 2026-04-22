# CODEX Handoff (From Gemini)

## 1. 任務現況
- 已接收 `GEMINI_HANDOFF.md` 的所有指示。
- 已完成 `main` 分支與 `origin/main` 的同步，解決了合併衝突。
- 目前系統已具備：ATR 波動標籤、情境感知 (Scenario-aware) 門檻調整、盈虧比 (P/L Ratio) 追蹤、以及基礎的驗算 (Verification) 工具。

## 2. 策略共識分析
我非常認同「保守版自適應」的開發節奏。系統目前不應追求極致的複雜度，而是追求**預測與實際結果的閉環驗證**。

- **核心原則**：驗證驅動改動。沒有數據支撐的「自適應」一律不進入主排序邏輯。
- **短期目標**：穩住 `5D` (short) 與 `20D` (midlong) 的獲利期望值，避免追逐 `1D` 的虛假動能。

## 3. 下一步執行計畫 (ADAPTIVE_ENGINE_PLAN.md)
詳細計畫請參考 `ADAPTIVE_ENGINE_PLAN.md`。接下來的工作將按以下順序執行：

### Phase 1: 數據閉環 (當前重點)
- **量化 Heat Bias**：在 `summarize_outcomes.py` 中區分 `hot` vs `normal` 樣本，確認大盤熱度對勝率的實質影響。
- **價位帶回測**：驗證 ATR 調節後的 `add_price` 與 `stop_price` 是否真的有用。

### Phase 2: 反饋優化
- 將 `pl_ratio` 正式納入 `feedback_score` 公式。
- 引入 Rolling Window，讓系統對「近期」行情更敏感。

## 4. 給 CODEX 的提醒
- **不要整包合併 `testv`**：該分支僅作為設計參考。
- **維持 `main` 的簡潔**：所有的改進必須通過 `Phase 1` 的量化驗證後，才能進入 `Phase 3` 的自動化限流與調優。
- **關注 20D 樣本**：目前中長線樣本仍不足，調優需保持耐心。
