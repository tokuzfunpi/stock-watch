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

## 9. 2026-04-23 `testv` Re-check
- 已重新檢查最新 `testv` tip：`5afec8b`
- 這次 Gemini 在 `testv` 的最新更新，**主要是 roadmap / handoff 的整理**，不是新的 production-ready code
- `testv` 最新想傳達的核心方向是：
  - `Phase 1` 已足夠證明 heat bias 是主驅動
  - 下一步應優先考慮 safety valve / 限流，而不是加大 aggressiveness
  - `midlong threshold` 與 `heat bias` 的拆解仍是最重要研究題

### 這次 `testv` 新結論，哪些已值得吸收
- **Heat bias 主導，不要被短期 1D 漂亮結果誤導**
  - `testv` handoff 明確強調：`midlong` 在 `normal` 盤的超額報酬很弱，這和 `main` 現在的 verification 結論一致
- **下一步應偏向保護，而不是擴張**
  - 若未來要往前推，優先順序應是：
    - 先做 scenario / heat-aware safety valve
    - 再談 feedback / ATR 的更深 integration
- **5D / 20D 仍比 1D 更重要**
  - `testv` 最新 handoff 也再次提醒：不要只因 `below_threshold` 的 1D 不差，就過早放寬 production threshold

### 這次 `testv` 不該直接搬回來的部分
- 不要直接回灌 `testv` 的整包 code
- `testv` 目前相對 `main` 會回退掉多個已落地的 local / verification workflow：
  - `run_local_daily.py`
  - `run_local_doctor.py`
  - `run_local_housekeeping.py`
  - `run_weekly_review.py`
  - `verification/run_daily_verification.py`
  - `verification/feedback_weight_sensitivity.py`
- `testv` 也會回退掉部分已確認有用的 guardrails，例如：
  - `.TW / .TWO` fallback
  - `盤中保守觀察` 分流
  - 同日 snapshot safe rerun 的主線流程配套

### 給 Gemini / 後續 agent 的一句話
`testv` 最新狀態最有價值的是「研究結論與優先順序」，不是 branch 上的 code 本身。若要吸收，請以 `main` 現有 workflow 為基底重做，而不是倒回 `testv`。

## 10. Direct Guidance for Gemini

如果 Gemini 下一輪要接手，請直接照下面這個邊界工作，不要發散：

### 先做什麼
- **先做分析，不先改 production code**
- **先拆 `midlong threshold` 與 `heat bias`**
- **先看 `5D / 20D` 是否支持結論，不要只看 `1D`**

### 這輪最該回答的問題
1. 為什麼 `midlong below_threshold` 目前沒有比 `ok` 差？
2. 這個現象有多少是：
   - `market_heat`
   - `scenario_label`
   - `action mix`
   - `signal_date concentration`
3. 若只看 `normal` 盤，`ok` 是否開始顯示出真正的防守優勢？

### 明確不要做的事
- 不要提議直接 merge `testv`
- 不要直接修改：
  - `daily_theme_watchlist.py`
  - `verification/backfill_from_git.py`
  - `verification/summarize_outcomes.py`
- 不要因為 `1D` 表現漂亮就主張放寬 `midlong threshold`
- 不要把 heat bias 的結果誤解成 scenario-aware 規則已被獨立證明

### 比較好的輸出格式
- 主因排序
- 哪些結論可信、哪些仍是小樣本
- 下一步應觀察哪個指標
- 若未來真的要動 code，最小可行變更是什麼

### 最後一句指令
請把 `testv` 當作**研究分支**，不是回灌來源；任何值得落地的東西，都應在 `main` 重新設計、補測試、再整合。

---
*本文件整合遠端最新 threshold 分析與 2026-04-23 `testv` re-check，供後續 agent 接手。*
