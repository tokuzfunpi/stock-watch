# GEMINI Handoff (Integration Sync 6)
最後更新：2026-04-27

## 1) What changed (已上線)
- `spec_risk` 的 coverage 分析已在 `main` 有可用產出：
  - `run_weekly_review.py` 現在可以產出 `Candidate Mix Guidance`、`Candidate Expansion Targets` (By Group/By Layer) 以及 `Watchlist Gap Snapshot` 等報表。
  - 根據 draft proposals，已在 2026-04-26 對 `watchlist.csv` 完成一波積極的擴池 (新增 7 檔標的，包含 `satellite`、`theme` 與 `core`)。
- **Short Gate 觀察線 (Shadow Observation)** 已上線：
  - 目前針對短線 `below_threshold` 中表現極佳的 `開高不追` 動作，已實作隔離的影子觀察線 (`shadow_open_not_chase_candidates.csv` 與 `.md`)。
  - 這條觀察線不影響正式推播與正式候選排序，僅在 `強勢延伸盤 / 高檔震盪盤` 且 `hot` market、`spec_risk_bucket == normal` 時進行記錄與驗證。

## 2) Deep-Dive Insights for Strategy Tuning (深度分析建議)
基於 2026-04-27 的數據探鑽，以下為給 Codex 的具體策略優化方向：

### A. 「TREND 陷阱」：Normal 盤勢下的動能失效
- **數據證據**：在 `market_heat == normal` 時，標記為 `TREND` 的訊號平均報酬率為 **-0.09%**；反觀 `BASE` (底部分離) 或 `NONE` (靜態觀察) 仍能維持正報酬。
- **建議**：Codex 在調整 `short_gate` 時，可考慮在非熱盤 (Normal/Warm) 下，對純 `TREND` 訊號實施更嚴格的排擠，優先保留 `BASE` 類型。

### B. 「04-15 投機噴出」：SURGE/ACCEL 的極端值解析
- **數據證據**：2026-04-15 出現集體噴出，`4919.TW (新唐)`、`2388.TW (威盛)` 靠著 `SURGE, TREND, ACCEL` 訊號在 5D 內產出 **+44% / +28%** 的極端報酬。
- **分析**：這些標的全數處於 `hot` market。代表在過熱盤中，`SURGE` (放量起跳) 具有極強的短期延續性。
- **建議**：可以研究將 `SURGE` 在 `hot` 盤下從 `below_threshold` 升格為 `ok` 的條件。

### C. ATR Trim 實戰驗證：雙鴻 (3324.TWO) 案例
- **數據證據**：目前 34 筆帶有 Price Bands 的樣本中，僅 `3324.TWO` 成功觸及 `trim_price` (1134)。其 5D 最高回報達 10%，顯示 `trim_price` 的設定位置 (約 ATR 加權) 足夠支撐到強勢噴出段，並未過早離場。
- **建議**：維持目前的 ATR 係數，但可考慮對 `進攻持股` 調緊 0.5x ATR 的 `stop_price` 以保護利潤。

## 3) What is hypothesis only (分析假設與待驗證)
- **`spec_risk` 升級為硬排除規則 (Hard Filter)：待驗證**
  目前 `spec_risk` 的 `high vs normal` 樣本數仍然不足。雖然 `急拉追價型` 目前勝率 100%，但樣本極少，不能直接當作 production 階段的排除條件。
- **20D Threshold (中長線門檻) 調整：待驗證**
  目前 20D 樣本 OK rows 為 0，嚴禁因為 1D/5D 表現好就去改中長線門檻。

## 4) Evidence (數據證據摘要)
- **Heat Bias**: `1D midlong` 在 `hot` 狀態下比 `normal` 報酬高出 **4.28%** (信心度 `high`)。
- **Candidate Coverage**: `satellite` (80.0%) 與 `theme` (38.1%) 是異常樣本主來源，`etf` 貢獻度為 0。
- **Feedback 穩定性**: 權重測試 (70/30 vs 60/40) 顯示排名 0 位移，基準配置穩固。

## 5) What not to change (禁止事項)
- **不要把 `spec_risk` 變成 production 的 hard filter**：維持觀察層角色。
- **不要放寬 `midlong` threshold**：20D 樣本成熟前嚴禁動手。
- **不要將 `開高不追` 直接升格**：維持影子觀察線隔離驗證。
- **不要為了湊數擴充 ETF 或低波動股**：擴池目標鎖定 `theme/satellite` 的特定 Archetype。

## 6) Sensitive files (敏感檔案)
Gemini 不直接修改以下檔案，交由 Codex 實作：
- `daily_theme_watchlist.py` / `portfolio_check.py`
- `verification/summarize_outcomes.py` (包含其中的 Signal Template 邏輯)

## 7) Needs verification before merge (合併前須驗證)
- **ATR Stop-loss Tuning**: 若要對 `進攻持股` 收緊停損，須先在 `evaluate_recommendations.py` 模擬是否會造成過早止損。
- **Scenario Thresholds**: 在 `known_scenario_rate_pct` 提升前，不可大幅修改情境權重。