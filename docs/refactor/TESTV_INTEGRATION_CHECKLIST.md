# `testv` Integration Checklist

最後更新：2026-04-23

這份清單是拿來判斷 `testv` 的內容是否值得回到 `main`。

原則只有三個：

- 不整包 merge `testv`
- 優先保留 `main` 已經多出來的 guardrails
- 若 `testv` 的想法有價值，優先「重做」而不是直接搬 code

## 1) 已在 `main`

下面這些方向，`main` 已經有正式版本，後續不要再從 `testv` 倒退回舊寫法：

- `StrategyConfig` / `config.strategy`
- `adjust_strategy_by_scenario(...)` 已正式接進主流程
- `feedback_score` + `pl_ratio` 候選微調
- `scenario_label` 已進 snapshot / outcome / summary 鏈
- `Heat Bias` 報表輸出：
  - `hot vs normal`
  - `scenario + heat`
  - `date + heat`
- short scenario cap / heat-bias cap
- `python -m stock_watch portfolio` / `stock_watch/workflows/portfolio.py` 已吃 scenario-adjusted strategy
- `盤中保守觀察` 與 `明顯修正盤` 已分流
- `^TWII` 量比異常值會先按中性處理
- `.TW / .TWO` ticker fallback 已在 `main`
- `3491.TWO` 修正已在 `main`
- Telegram 最後一則 `ETF / 債券觀察` 已移除

## 2) 這次已整合

這一輪已先把 `testv` 裡缺失但安全的參考文件補進 `main`：

- [GEMINI.md](../handoff/GEMINI.md)
- [GEMINI_UPDATES_2026_04_22.md](../handoff/GEMINI_UPDATES_2026_04_22.md)

注意：

- 這兩份是設計脈絡與歷史背景
- 不是 `main` 目前行為的單一真相來源
- 實際整合判準仍以 [GEMINI_HANDOFF.md](../handoff/GEMINI_HANDOFF.md) 與現行程式碼為準

## 3) 值得搬，但要重做

下面這些是 `testv` 的方向有價值，但不能直接搬 code；若要進 `main`，應該在主線上重新設計、補測試、跑 verification：

- `stock_watch/workflows/portfolio.py` 更深層的 `trim_price` / exit 自動化
- `feedback_score` 權重敏感度實驗
- `ATR` band 的離線驗證與校準
- `By Scenario + Action` 的持續分析結論整理成可執行規則
- 用 `heat_bias_by_scenario` / `heat_bias_by_date` 拆解「熱盤效果」和「scenario 效果」

這一類工作比較像：

- 研究題
- 驗證題
- 下一輪實作需求來源

而不是直接 cherry-pick `testv` commit。

## 4) 不要直接搬

下面這些目前明確不建議從 `testv` 直接整合：

- `testv` 的整包 `daily_theme_watchlist.py`
- `testv` 的整包 `portfolio_check.py`
- `testv` 的整包 `verification/backfill_from_git.py`
- `testv` 的整包 `verification/summarize_outcomes.py`
- `testv` 的 `config.json` 回退版本
- `theme_watchlist_daily/` 產物、log、report 類檔案
- `test_my_logic.py` 直接納入 `pytest` 收集範圍

原因：

- 有些邏輯比 `main` 舊，會把已修好的 guardrails 拿掉
- 有些變更會把分析輸出降級或搞壞
- 有些是本地驗證腳本，不適合直接當正式測試入口
- 有些只是報表產物，不是可維護的 source change

## 5) 已知不要回退的點

如果未來再看 `testv` diff，下面這些點要特別小心，不要回退：

- 不要拿掉 `scenario_policy`
- 不要拿掉 `.TW / .TWO` fallback
- 不要拿掉 `盤中保守觀察`
- 不要把 `^TWII` 的異常量比直接翻成偏空
- 不要把 `vol_tag` 這類未定義變數塞回訊息模板
- 不要把 `Heat Bias` / `By Scenario` / `By Date` 包進會吞掉整段輸出的寬鬆 `except`

## 6) 下一輪可執行順序

如果下一步要真的從 `testv` 吸收東西，建議照這個順序：

1. 先做研究型整理，不改 production：
   - heat vs scenario
   - ATR band 驗證
   - feedback 權重比較
2. 把研究結果整理成小規模需求
3. 在 `main` 直接實作最小變更
4. 補 `tests/` 與 verification
5. 再決定是否放大到主流程

## 7) 一句話結論

`testv` 現在最有價值的是設計方向與研究脈絡，不是可直接回灌的 production code。

## 8) 2026-04-23 最新 `testv` branch 判讀

這次重新檢查 `testv` 最新 tip（`5afec8b`）後，判斷如下：

- 最新 commit **主要是文件整理**
  - [ADAPTIVE_ENGINE_PLAN.md](../research/ADAPTIVE_ENGINE_PLAN.md)
  - [CODEX_HANDOFF.md](../handoff/CODEX_HANDOFF.md)
- 真正有策略層動作的是前一個 `1877619`
- 但整體 branch 相對 `main` 仍偏舊，會回退多個已經在 `main` 上穩定運作的 local / verification 工具

所以：

- **可以吸收結論**
- **不要直接 merge code**

## 9) 目前仍值得 cherry-pick 的極小清單

注意：這裡的「cherry-pick」比較接近「吸收想法 / 小塊整理」，不是直接搬整個 commit。

### A. 可直接吸收的文件結論

1. **Phase 1 的優先順序敘述**
   - `heat bias` 已被量化證明是主因
   - 下一步要先做保護，不是先做更激進的 adaptive 擴張

2. **對 `midlong threshold` 的研究 framing**
   - 問題不是「要不要立刻放寬」
   - 而是先拆清楚：
     - heat bias
     - scenario
     - action mix
     - date concentration

3. **對 5D / 20D 判讀權重的提醒**
   - `1D` 可以當早期觀察
   - 真正的 threshold 校準仍應以 `5D / 20D` 為主

### B. 可在 `main` 上另做的新需求

1. **Scenario / Heat safety valve**
   - 不是直接搬 `testv` code
   - 而是在 `main` 上設計一個更保守的版本：
     - 只限流
     - 不重寫主排序
     - 必須先有 verification 支持

2. **Portfolio workflow 的研究版 scenario-aware 收斂提醒**
   - 先做提示 / report
   - 不要直接做全自動 exit 執行

## 10) 目前不值得 cherry-pick 的東西

這次重新檢查後，下面這些仍然是 **不要動**：

- `testv` 的整包 `daily_theme_watchlist.py`
- `testv` 的整包 `verification/summarize_outcomes.py`
- `testv` 的整包 `verification/backfill_from_git.py`
- `testv` 對 local workflow 的刪減版本
- `testv` 裡把 runtime notification 又加回 `觸發來源：Manual` 的方向

## 11) 最新一句話結論

`testv` 最新更新值得吸收的是：「先保護、先驗證、先拆 heat bias 與 threshold」，不是 branch 上那份已經落後於 `main` 的實作。
