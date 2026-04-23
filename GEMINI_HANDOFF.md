# GEMINI Handoff (2026-04-23)

## 1. What Changed
- **Midlong Threshold 結構性分析完成**：量化解讀了「為什麼 `below_threshold` 目前沒有比 `ok` 差」的悖論，定性為 **「熱盤紅利 (Market Heat Bias)」** 導致的統計倖存者偏差。
- **驗證工作流升級 (已上線)**：
    - `run_daily_verification.py` 支援 `--mode preopen/postclose/full`。
    - `reco_snapshots.csv` 實作同鍵去重 (Signal Date + Watch Type + Ticker)。
- **分析工具擴充 (已上線)**：
    - `summarize_outcomes.py` 新增 ATR Band 與 Scenario 的數據切片分析能力。

## 2. What is Hypothesis Only
- **`below_threshold` 優於 `ok` 的結論 (待驗證)**：目前僅有 8 筆樣本，且 87.5% 集中於 `hot` 盤勢。在 `normal` 盤勢下的韌性仍屬假設。
- **現有 Midlong 門檻的防守效度 (分析假設)**：基於目前的 `ok` 樣本在 `normal` 盤勢下僅有 +0.25% 報酬，假設現有門檻在平淡盤中僅具備「生存能力」而非「獲利能力」。
- **ATR 價位帶的減損效果 (待驗證)**：5D/20D 的 ATR band 樣本不足，尚無法證明其能有效保護資產。

## 3. Evidence
- **Heat Bias 數據隔離**：
    - `1D midlong / below_threshold`：報酬 +4.18%，其中 **87.5% 樣本為 `hot`**。
    - `1D midlong / ok`：報酬 +2.29%，其中 **41.7% 樣本為 `normal`**（該組報酬僅 +0.25%）。
- **風險特徵**：`below_threshold` 樣本的 `risk_score` (3-6) 顯著高於 `ok` (0-1)，顯示其高報酬來自「波動紅利」而非「結構穩定」。
- **樣本分佈**：所有 `below_threshold` 高報酬樣本均集中在 04-14, 04-16, 04-17 等極強勢交易日，具備明顯的小樣本偏差。

## 4. What Not to Change
- **嚴禁放寬 Midlong 門檻**：雖然 `below_threshold` 目前績效漂亮，但那是行情抬轎的「帶毒紅利」。
- **維持 Scenario-aware 限流**：在 `Normal` 與 `明顯修正盤` 樣本累積足夠前，保持防禦姿態。
- **不要回退 `verification` 的去重邏輯**：確保同一天重跑不會導致數據膨脹。

## 5. Sensitive Files (Do Not Edit Directly)
- `daily_theme_watchlist.py`：自適應門檻邏輯所在。
- `portfolio_check.py`：持股建議邏輯所在。
- `verification/evaluate_recommendations.py`：報酬計算核心。
- `verification/summarize_outcomes.py`：驗證報表生成核心。

## 6. Needs Verification Before Merge
- **Normal 盤勢下的策略韌性**：需等待 `market_heat == normal` 的樣本累積至 >50 筆。
- **Scenario × Action 交叉影響**：需補齊 `scenario_label` 後重新跑 `summarize_outcomes`。

---

## 7. Strategic Analysis: Midlong Threshold Problem
### 最可信的主因排序：
1. **熱盤紅利 (Market Heat Bias)**：樣本完全避開了低溫區。
2. **小樣本偏差 (Small Sample Size)**：僅 8 筆樣本且日期過度集中。
3. **風險補償 (Risk-Reward Tradeoff)**：以安全性換取動能。

### 下一階段觀察指標：
> **Normal 盤勢下的 OK Rate 與回撤 (Drawdown)**
- 只有當 `ok` 樣本在平淡盤中展現勝率優勢，才具備調整參數的科學基礎。

---

## 8. Development Reference (for Codex)
- **驗證程式**：本次分析所使用的原始腳本已存放在 **`testv`** 分支中的 `verification/research/analyze_midlong.py`。
- **Action**：請 Codex 前往該分支 review 腳本邏輯，並評估是否將此分析切片整合至 `summarize_outcomes.py`。

---
*本文件依照 GEMINI_HANDOFF_TEMPLATE.md 格式撰寫，為 2026-04-23 決策脈絡。*
