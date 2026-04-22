# CODEX Handoff (From Gemini) - Sync 3 (Phase 1 Findings)

## 1. 任務進度：Phase 1 核心分析完成
- **驗證工具強化**：`summarize_outcomes.py` 現在具備強大的 `Heat Bias` (市場熱度偏差) 分析功能。
- **數據閉環修復**：修復了 `scenario_label` 在 `backfill_from_git` -> `snapshot` -> `outcome` 鏈條中的丟失問題。

## 2. Phase 1 關鍵發現 (Critical Insights)
- **熱度依賴證實**：
    - Midlong 5D 報酬：`hot` 盤勢平均 **+15.13%**，`normal` 盤勢平均 **-0.19%**。
    - 這證明目前的策略門檻在「正常盤」幾乎沒有超額報酬。我們過去的績效高度依賴大盤整體熱度。
- **情境感知啟動**：
    - 今日 (2026-04-22) 系統已正確識別為 **「明顯修正盤」**。
    - 這是測試「自適應行為」的最佳時機。

## 3. 我的想法與後續建議
既然 Phase 1 已經明確量化了風險，我建議接下來的改進應聚焦於**「生存」**而非「增益」：

### A. 實施 Phase 3 的限流保護 (急迫)
- 不要只在訊息中警告「明顯修正盤」。
- **具體建議**：修改 `daily_theme_watchlist.py` 中的 `select_push_candidates`。當 `scenario_label == "明顯修正盤"` 時，強制將 `top_n_short` 鎖定為 0 或 1。

### B. Phase 2 反饋公式調優
- 觀察 `pl_ratio` 是否能在 `normal` 盤勢中有效過濾掉那些「勝率高但獲利薄」的標的。

## 4. 給 CODEX 的提醒 (Technical Alignment)
- **Schema 異動**：`reco_snapshots.csv` 現在正式包含 `scenario_label` 欄位。若要手動跑 evaluation，請務必先跑一次 `backfill_from_git.py` 以補齊歷史標籤。
- **不要被 1D 績效騙了**：數據顯示 `below_threshold` 的 1D 表現有時優於 `ok`，但 5D 報酬通常會快速收斂。請堅持以 5D/20D 為主要校準依據。
