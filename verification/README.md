# Verification Tools

這個資料夾放「推薦驗算」相關工具：把早上推薦的快照存下來、收盤後回填結果、再做彙整報告。

## 檔案與用途

- `verify_recommendations.py`
  - 目的：用 `theme_watchlist_daily/daily_rank.csv` 的資料，產出當日短線/中線推薦驗算報告，並把推薦清單存成快照。
  - 輸出：
    - `verification/watchlist_daily/verification_report.md`
    - `verification/watchlist_daily/reco_snapshots.csv`
    - `verification/watchlist_daily/codex_context.json`（給 Codex/人工分析用的結構化 JSON）
    - `verification/watchlist_daily/contexts/codex_context_*.json`（每次執行留一份）
- `evaluate_recommendations.py`
  - 目的：把 `reco_snapshots.csv` 的推薦，對照未來 N 個交易日的收盤價，回填 outcome（報酬% / 狀態）。
  - 輸出：`verification/watchlist_daily/reco_outcomes.csv`
  - 注意：需要網路能抓到 yfinance；抓不到會寫入 `status`（best effort）。
- `summarize_outcomes.py`
  - 目的：把 `reco_outcomes.csv` 彙整成可讀的勝率/平均報酬/樣本數報告。
  - 輸出：`verification/watchlist_daily/outcomes_summary.md`
- `feedback_weight_sensitivity.py`
  - 目的：離線比較 `feedback_score` 的 `base/recent` 權重組合，不改 production 預設。
  - 輸出：
    - `verification/watchlist_daily/feedback_weight_sensitivity.md`
    - `verification/watchlist_daily/feedback_weight_sensitivity.csv`
- `run_daily_verification.py`
  - 目的：把 `verify -> evaluate -> summarize -> feedback sensitivity` 串成單一入口，並支援 `盤前 / 盤後 / 全流程` 模式。
  - 適合：想用同一支指令跑早上快照、收盤後回填，或完整 workflow 時使用。

## 建議執行時機（台灣時間）

- 08:45：`verify_recommendations.py`（把「今天早上推薦」定格成快照；會強制補滿短線/中線各 5 檔，低於門檻會標示 `below_threshold`）
- 14:00（收盤後）：`evaluate_recommendations.py`（回填 1/5/20 天等 horizon 的結果）
- 任何時候：`summarize_outcomes.py`（產出彙整報告）

## 使用方式（在 repo root 執行）

```bash
# 1) 早上產生驗算報告 + 快照
python3.11 verification/verify_recommendations.py

# 若想調整「強制補滿」的數量（預設 5）
python3.11 verification/verify_recommendations.py --top-n-short 5 --top-n-midlong 5

# 2) 收盤後回填 outcomes（horizons 預設 1,5,20）
python3.11 verification/evaluate_recommendations.py --horizons 1,5,20

# yfinance 偶爾不穩時，可提高穩定性（分批 + retry + backoff + 拉長 period）
python3.11 verification/evaluate_recommendations.py --horizons 1,5,20 --period 180d --batch-size 25 --retries 3 --backoff-seconds 1

# 用本機 cache（網路不穩時更容易補齊 OK rows；cache 會寫在 verification/watchlist_daily/ 下）
python3.11 verification/evaluate_recommendations.py --horizons 1,5,20 --cache-dir verification/watchlist_daily/yfinance_cache

# 一次把所有日期都補齊（會跑 snapshots 裡所有 signal_date）
python3.11 verification/evaluate_recommendations.py --all-dates --horizons 1,5,20

# 只補指定區間（需搭配 --all-dates）
python3.11 verification/evaluate_recommendations.py --all-dates --since 2026-04-10 --until 2026-04-17 --horizons 1,5,20

# 3) 彙整 outcomes
python3.11 verification/summarize_outcomes.py

# 4) 比較 feedback 權重敏感度（預設 70/30, 80/20, 60/40）
python3.11 verification/feedback_weight_sensitivity.py

# 自訂權重組合
python3.11 verification/feedback_weight_sensitivity.py --weights 70:30,85:15,50:50

# 5) 一次跑完整個 verification workflow
python3.11 verification/run_daily_verification.py

# 5a) 盤前流程：只做 verify / snapshot
python3.11 verification/run_daily_verification.py --mode preopen

# 5b) 盤後流程：做 evaluate -> summarize -> feedback sensitivity
python3.11 verification/run_daily_verification.py --mode postclose

# 常用調整：指定 horizons / weights
python3.11 verification/run_daily_verification.py --horizons 1,5,20 --weights 70:30,85:15,50:50

# 若要在 mode 上再局部跳步
python3.11 verification/run_daily_verification.py --mode postclose --skip-feedback
```

`run_daily_verification.py` 的 mode 規則：

- `--mode preopen`：只跑 `verify_recommendations.py`
- `--mode postclose`：跑 `evaluate_recommendations.py`、`summarize_outcomes.py`、`feedback_weight_sensitivity.py`
- `--mode full`：從 `verify` 一路跑到 `feedback`（預設）
- `--skip-*` 旗標仍然有效，會在 mode 的基礎上再跳過指定步驟
- 同一天重跑 `preopen` 會以 `signal_date + watch_type + ticker` 覆蓋 snapshot，不再重複累積同一筆推薦

## 用 Git 歷史回填（補齊過去樣本）

如果你之前沒有每天早上跑 `verify` 存快照，可以用 repo 裡歷史的
`theme_watchlist_daily/daily_rank.csv`（artifact commits）來重建快照：

```bash
# 回填最近 30 天（每一天取最新一次 daily_rank.csv）
python3.11 verification/backfill_from_git.py

# 回填全部可用日期（0=unlimited）
python3.11 verification/backfill_from_git.py --limit 0

# 直接重建 reco_snapshots.csv（避免檔案不小心壞掉 / 欄位錯位）
python3.11 verification/backfill_from_git.py --limit 0 --rebuild-snapshot

# 指定區間（YYYY-MM-DD）
python3.11 verification/backfill_from_git.py --since 2026-04-15 --until 2026-04-19
```

會產生：

- `verification/watchlist_daily/backfill_reports/verification_report_YYYY-MM-DD.md`
- 並追加到 `verification/watchlist_daily/reco_snapshots.csv`

## 結果怎麼看

- `verification_report.md`：當天短線/中線推薦清單 + warnings/diagnostics（偏「質檢」）
- `codex_context.json`：同一份資料但用 JSON（你可以直接貼到 Codex 做後續分析/迭代）
- `reco_snapshots.csv`：每天早上推薦快照（偏「可追溯」）
- `reco_outcomes.csv`：每個 ticker * 每個 horizon 的 realized return（偏「校正/評估」）
- `outcomes_summary.md`：把 `reco_outcomes.csv` 聚合成勝率/平均報酬/樣本數（偏「管理 dashboard」）
- `feedback_weight_sensitivity.md`：看不同 `feedback_score` 權重下，action 排名會不會明顯洗牌（偏「離線研究」）

## GitHub Actions（手動）

Repo 有一個手動 workflow 會跑 `verify_recommendations.py` 並上傳 artifact：

- `.github/workflows/verify-recommendations.yml`

目前 workflow 不會把輸出 commit 回 repo，只會上傳 artifact。

## 版本控制

`verification/watchlist_daily/` 內是本機產出資料，預設已在 `.gitignore` 忽略，不會被 commit。
