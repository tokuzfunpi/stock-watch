# GEMINI Handoff (2026-04-23)

## 1. What Changed
- **Verification runner 已分成盤前 / 盤後 / 全流程**：`verification/run_daily_verification.py` 現在支援 `--mode preopen`、`--mode postclose`、`--mode full`。
- **Snapshot 重跑安全性已補齊**：同一天重跑 `preopen` 不再重複 append `reco_snapshots.csv`，改為用 `signal_date + watch_type + ticker` upsert。
- **Evaluate 端也加了去重保護**：`verification/evaluate_recommendations.py` 會先對 snapshots 做去重，避免舊資料把 outcomes / summary 的樣本數灌大。
- **Verification 研究輸出已更完整**：`summarize_outcomes.py` 現在穩定產出 `Key Findings`、`Heat Bias`、`ATR Band Findings/Coverage/Checkpoints`；`feedback_weight_sensitivity.py` 已可離線比較 `70/30`、`80/20`、`60/40` 權重。

## 2. Current Evidence
- **目前最穩的結論仍是 Heat Bias，不是 scenario 單獨勝出**：
  - `1D midlong` 在 `hot` 盤相較 `normal` 平均報酬差約 `+4.22%`
  - `min_n=11`、`confidence=high`
- **Scenario 已能切片，但尚未取代 heat 作為主解釋變數**：
  - `強勢延伸盤` 下 `1D midlong` 的 `hot - normal` 仍約 `+4.44%`
- **forced-fill 目前不能直接視為劣質樣本**：
  - `1D midlong` 的 `ok - below_threshold = -1.68%`
  - 代表目前不應因表面績效就放寬門檻，但也不能把 forced-fill 一概當雜訊
- **ATR 結論仍偏早期**：
  - `1D` 有 checkpoint
  - `5D / 20D` band 成熟樣本仍不足，先累積
- **feedback 權重目前不是最急的調整點**：
  - `60/40`、`70/30`、`80/20` 之間分數會動
  - 但 action 排名尚未洗牌

## 3. What is No Longer Hypothesis Only
- **Scenario coverage 已接上可用鏈路**：目前 `OK rows` 的 `scenario_label` coverage 已達 `100%`
- **2026-04-22 樣本不再只是待觀察基準點**：
  - `1D midlong` 已有 1 筆成熟樣本
  - 報酬約 `+2.26%`
- **同日 verification 重跑安全性** 已不再只是操作建議，而是已在主線程式與測試中落地

## 4. What Remains Hypothesis Only
- **修正盤限流是否真正改善中長期報酬**
  - 目前 `5D / 20D` 樣本仍不足，不要過早宣稱 scenario-aware policy 已被證明
- **ATR 出場邏輯是否優於現行 exit 判讀**
  - band coverage 還不夠，暫不應直接推進 production exit 自動化
- **Heat-adjusted scoring 是否值得進主排序**
  - 現階段只適合先做離線實驗，不能直接改主排名

## 5. What Not to Change
- **不要因 `below_threshold` 短期表現漂亮就放寬 midlong 門檻**
- **不要把目前的 midlong 成績解讀成規則本身已全面穩健**
- **不要直接從 `testv` 回灌 production code**
- **不要手動編修 `reco_outcomes.csv` / `reco_snapshots.csv` 來修數據**
  - 若要修，請透過正式腳本 / upsert / 去重邏輯處理

## 6. Sensitive Files
- `daily_theme_watchlist.py`
  - 主策略與 scenario policy 核心，不要在沒有 verification 證據下大改
- `verification/verify_recommendations.py`
  - 現在負責 snapshot upsert；若動到 key 或欄位，需同步檢查 backfill / evaluate
- `verification/evaluate_recommendations.py`
  - 不要改 forward return 定義；去重只應影響輸入清理，不應改績效計算本身
- `verification/summarize_outcomes.py`
  - 目前是 Gemini 設計方向最主要的驗證出口

## 7. Recommended Next Work
1. **繼續累積 5D / 20D 樣本**
   - 目前最重要的是增加成熟樣本，不是立刻改規則
2. **優先研究 midlong threshold**
   - 因為 `ok vs below_threshold` 仍顯示 forced-fill 沒有明顯更差
3. **延後 ATR / feedback production 調整**
   - 先維持 `70/30`
   - 先把 ATR 當 coverage / checkpoint 報表，而不是直接下交易規則
4. **若要讓 Gemini 幫忙，優先交研究題，不交直接改 production**
   - `heat vs scenario` 拆解
   - `midlong threshold` 的樣本結構分析
   - ATR band 成熟度追蹤

## 8. Operational Notes for Gemini
- `preopen` / `postclose` / `full` 現在都可以安全重跑，但**同一天同一檔 snapshot 會以最後一次為準**
- 本機 `LOCAL_RUNBOOK.md` 已同步更新，但它是 local-only，不在 git 追蹤範圍
- 目前主線協作預設是：**只要改動已驗證且適合入庫，就直接 commit + push**

## 9. One-Line Conclusion
目前最值得相信的訊號仍是：**midlong 績效高度受熱盤支撐，主線應先維持 guardrails、持續累積樣本，再決定是否調整 threshold / ATR / feedback 權重。**
