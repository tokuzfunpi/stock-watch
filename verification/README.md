# Verification Tools

這個資料夾放「推薦驗算」相關工具：把早上推薦的快照存下來、收盤後回填結果、再做彙整報告。

## 檔案與用途

- `verify_recommendations.py`
  - 目的：用 `theme_watchlist_daily/daily_rank.csv` 的資料，產出當日短線/中線推薦驗算報告，並把推薦清單存成快照。
  - 輸出：
    - `verification/watchlist_daily/verification_report.md`
    - `verification/watchlist_daily/reco_snapshots.csv`
- `evaluate_recommendations.py`
  - 目的：把 `reco_snapshots.csv` 的推薦，對照未來 N 個交易日的收盤價，回填 outcome（報酬% / 狀態）。
  - 輸出：`verification/watchlist_daily/reco_outcomes.csv`
  - 注意：需要網路能抓到 yfinance；抓不到會寫入 `status`（best effort）。
- `summarize_outcomes.py`
  - 目的：把 `reco_outcomes.csv` 彙整成可讀的勝率/平均報酬/樣本數報告。
  - 輸出：`verification/watchlist_daily/outcomes_summary.md`

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
```

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
- `reco_snapshots.csv`：每天早上推薦快照（偏「可追溯」）
- `reco_outcomes.csv`：每個 ticker * 每個 horizon 的 realized return（偏「校正/評估」）
- `outcomes_summary.md`：把 `reco_outcomes.csv` 聚合成勝率/平均報酬/樣本數（偏「管理 dashboard」）

## GitHub Actions（手動）

Repo 有一個手動 workflow 會跑 `verify_recommendations.py` 並上傳 artifact：

- `.github/workflows/verify-recommendations.yml`

目前 workflow 不會把輸出 commit 回 repo，只會上傳 artifact。

## 版本控制

`verification/watchlist_daily/` 內是本機產出資料，預設已在 `.gitignore` 忽略，不會被 commit。
