# GEMINI Handoff Template

最後更新：2026-04-22

這份文件是給 Gemini / 其他分析型 agent 的固定交接模板。目的不是重講 repo 全部架構，而是把下面幾件事講清楚：

- `main` 現在真正已上線的是什麼
- 哪些只是假設 / 分析方向，不是已完成能力
- Gemini 應該做什麼，不應該做什麼
- 哪些檔案是敏感區，不要直接改

## 1) Collaboration Rule

- `Gemini` 負責：
  - 分析結果
  - 策略假設
  - verification 解讀
  - 文件草稿 / handoff / plan
- `Codex` 負責：
  - 改 code
  - 補測試
  - 跑驗證
  - 整合進 `main`

一句話版本：

- `Gemini 負責想，Codex 負責落地。`

## 2) What Changed On Main

目前 `main` 已正式整合：

- `market_scenario`
- `scenario-aware thresholds`
- `feedback_score` + `pl_ratio` 候選微調
- `scenario_label` 進 verification 鏈
- `Heat Bias` 提示與 verification summary
- short / midlong subscriber-facing 白話訊息
- short / midlong scenario cap 已可由 `config.json -> scenario_policy` 控制
- `portfolio_check.py` 已與主流程共用同一套 scenario-adjusted strategy
- 盤中 / 收盤後 scenario 已分流：
  - 盤中：`盤中保守觀察`
  - 收盤後：才可能正式定案為 `明顯修正盤`
- `^TWII` 量比異常值（例如 `0.0`）現在會中性處理，不再直接把整天判成修正盤
- `昇達科` Yahoo ticker 已修正為 `3491.TWO`
- `.TW / .TWO` 下載有 fallback
- Telegram 最後一則 `ETF / 債券觀察` 已移除，不再發送

## 3) What Is Hypothesis Only

下面這些現在還不能當成「已驗證完成」：

- `明顯修正盤` 的實戰效果已被充分驗證
- `portfolio_check.py` 已完成更深層的自動出場邏輯
- `ATR` 已深度進到 `trim_price` / 全部 exit 邏輯
- `feedback_score` 已進 `daily_rank.csv` 主排序
- `testv` 的 adaptive engine 已整包進 `main`

如果 Gemini 要寫結論，請明確標成：

- `已上線`
- `分析假設`
- `待驗證`

不要混寫。

## 4) Gemini Should Focus On

Gemini 最適合做的題目：

1. `By Scenario + Action` 解讀
2. `Heat Bias` 與 `Scenario` 的交叉解讀
3. `feedback_score` 權重敏感度的離線比較
4. `ATR band` 的離線驗證
5. subscriber 文案與策略說法的清楚化

Gemini 的預設產出應該是：

- 分析
- 結論
- 假設
- 建議
- 風險提示

不是直接改 `main` 的核心邏輯。

## 5) Sensitive Files

這些檔案是敏感區。Gemini 不要直接改 `main` 上的這些檔案：

- [daily_theme_watchlist.py](/Users/tokuzfunpi/codes/joe-notes/stock-watch/daily_theme_watchlist.py)
- [portfolio_check.py](/Users/tokuzfunpi/codes/joe-notes/stock-watch/portfolio_check.py)
- [verification/backfill_from_git.py](/Users/tokuzfunpi/codes/joe-notes/stock-watch/verification/backfill_from_git.py)
- [verification/evaluate_recommendations.py](/Users/tokuzfunpi/codes/joe-notes/stock-watch/verification/evaluate_recommendations.py)
- [verification/summarize_outcomes.py](/Users/tokuzfunpi/codes/joe-notes/stock-watch/verification/summarize_outcomes.py)

如果 Gemini 想試改，請只在實驗分支做，並把變更留給 Codex review / integrate。

## 6) Hard Guardrails

這段請直接當成 Gemini 的禁止事項。

### A. 不要直接改主流程核心檔

- 不要直接在 `main` 改：
  - `daily_theme_watchlist.py`
  - `portfolio_check.py`
  - `verification/*` 核心腳本
- 若需要改，先寫分析建議，再交由 Codex 整合

### B. 不要把文件當成已完成證據

- `GEMINI.md`
- `GEMINI_UPDATES_*`
- handoff 文件

這些都只能算設計方向或分析紀錄，不算已落地能力。

### C. 不要再把未定義變數塞回訊息模板

- 特別是 `vol_tag`
- 已知這種改法會直接把通知流程炸掉
- 訊息內如需波動資訊，只能使用現有的 `volatility_badge_text(...)`

### D. 不要用大範圍 except 把 verification 失敗靜默吞掉

- 尤其是 `Heat Bias`
- `By Scenario`
- `By Date`

這些不是 decoration，是核心驗證輸出。寧可報錯，也不要靜默清空結果。

### E. 不要把盤中資料直接定案成修正盤

- `盤中保守觀察` 和 `明顯修正盤` 現在是兩個不同狀態
- 盤中先保守，不代表收盤後一定是修正盤
- 不要再把這兩者混成同一件事

### F. 不要因為 `^TWII` 量比異常值就直接翻空

- `volume_ratio20 = 0.0`
- `nan`
- 明顯不合理的即時值

現在主線邏輯是先按中性處理，不要再改回「直接判偏空」。

### G. 不要亂改 ticker 市場別

- 台股有 `.TW`
- 上櫃有 `.TWO`
- 已知：`昇達科 = 3491.TWO`

若 Gemini 提出新增標的，先確認 Yahoo ticker 正確，再交 Codex 整合。

### H. 不要把 ETF 最後一則 Telegram 訊息加回來

- `ETF / 債券觀察` 報表區塊仍保留
- 但 Telegram 最後一則已明確移除
- 不要自行恢復

### I. 不要更動本機執行環境描述

- 本 repo 本機固定使用：
  - `/Users/tokuzfunpi/codes/nvidia/311env`
- 文件和執行指令都沿用這個 venv

## 7) What Gemini May Edit Safely

相對安全的編輯範圍：

- [GEMINI_HANDOFF.md](/Users/tokuzfunpi/codes/joe-notes/stock-watch/GEMINI_HANDOFF.md)
- [CODEX_HANDOFF.md](/Users/tokuzfunpi/codes/joe-notes/stock-watch/CODEX_HANDOFF.md)
- 分析型文件
- subscriber 文案草稿
- strategy / research note

即使如此，也要明確標記：

- `已上線`
- `待驗證`
- `分析假設`

## 8) Preferred Handoff Format

Gemini 每次交接，至少應包含這 6 段：

1. `What changed`
2. `What is hypothesis only`
3. `Evidence`
4. `What not to change`
5. `Sensitive files`
6. `Needs verification before merge`

建議直接照這個格式寫，不要只寫一段長散文。

## 9) Needs Verification Before Merge

以下類型的改動，在沒有 verification / tests 前不要建議直接進 `main`：

- scenario thresholds 大改
- `detect_row()` 主訊號邏輯大改
- `feedback_score` 權重大改
- `daily_rank.csv` 主排序變動
- `portfolio` 自動出場價邏輯加深
- verification schema 變更

## 10) Current Recommendation

目前最佳合作模式：

- Gemini 先做分析與假設
- Codex 再做 code integration
- `main` 只收經過測試與驗證的改動

不要把 Gemini 變成直接寫 production core logic 的角色。
