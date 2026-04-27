# CODEX Handoff (2026-04-25)

## 1. 目前已落地的主線
- `stock-watch` 已完成主要結構化：
  - `signals`
  - `ranking`
  - `providers`
  - `reports`
  - `backtesting`
  - `verification`
  - `local runners / doctor / housekeeping / weekly review`
- `daily_theme_watchlist.py` 與 `portfolio_check.py` 現在主要是 orchestrator / compatibility wrapper。
- 穩健路線的效能優化已經完成到可長期使用的 checkpoint：
  - in-memory history cache
  - disk-backed history cache
  - indicator cache
  - incremental backtest
  - market-aware stale-data guardrails
  - runtime metrics / doctor / housekeeping

## 2. `spec_risk` / 疑似炒作線目前做到哪裡
- `detect_row()` 現在會產出：
  - `spec_risk_score`
  - `spec_risk_label`
  - `spec_risk_subtype`
  - `spec_risk_note`
  - `spec_risk_flags`
  - `spec_price_action_score`
  - `spec_crowding_score`
  - `spec_extension_score`
  - `spec_structure_score`
- `spec_risk_subtype` 目前可讀 bucket：
  - `急拉爆量型`
  - `高檔脫離型`
  - `結構失配型`
  - `急拉追價型`
  - `資金擁擠型`
  - `高檔無回檔型`
  - `一般投機型`
- `daily_report.md/html` 已有 `疑似炒作觀察`
- `verification_report.md` 已有：
  - `spec risk counts`
  - `Spec Risk Watchlist`
- `outcomes_summary.md` 已有：
  - `Overall By Spec Risk`
  - `Spec Risk Check`
  - `Overall By Spec Subtype`
- `weekly_review.md/json` 已有：
  - `spec_risk` decision
  - `Overall By Spec Risk`
  - `Overall By Spec Subtype`
  - `Spec Risk Highlights`
  - 低樣本 `confidence / interpretation` guardrails
- `local_run_status` / `local_doctor` 也會直接顯示：
  - `spec_risk_high_rows`
  - `spec_risk_watch_rows`
  - `spec_risk_top_tickers`

## 3. 目前最重要的分析結論
- 這條 `spec_risk` 線**已經有辨識力，但還沒有足夠證據變成硬規則**。
- live 名單能抓到像：
  - `2388.TW`
  - `3661.TW`
  - `3443.TW`
  - `6669.TW`
  - `3017.TW`
  這種盤後看起來就可疑的名字，方向是對的。
- 但 verification / weekly review 目前仍顯示：
  - `watch/high` 樣本太少
  - `high vs normal` 還沒有足夠成熟樣本
  - subtype 切片也還是小樣本
- 目前比較合理的定位是：
  - **提醒層 / 風險意識層**
  - 不是 production hard filter

## 4. 要不要補更多標的的判斷
- **有需要，但不應該直接大幅亂擴。**
- 現在更合理的方向是：
  - 補「更容易產生投機樣本」的候選池
  - 不是單純增加更多大型權值 / ETF
- 換句話說：
  - 要補的是 **spec-risk coverage**
  - 不是純 watchlist size

## 5. 下一步最值得做的事
- 做 `watchlist coverage / candidate mix` 分析：
  - 哪些 group 幾乎不會產生 `spec_risk` 樣本
  - 哪些 group 最有機會補到 `high/subtype` 樣本
- 如果要繼續推 `spec_risk`，優先順序應該是：
  1. 先補 coverage 分析
  2. 再決定是否擴 universe
  3. 最後才考慮外部資料（TWSE 注意/處置、集中度、新聞失配）

## 6. 目前不建議做的事
- 不要現在就把 `spec_risk` 變成硬排除條件
- 不要因為 weekly review 出現某個 subtype 就直接下結論說它最危險
- 不要為了湊樣本去亂加很多低品質標的
- 不要把 `testv` branch 整包 merge 回來

## 7. 最近已驗證通過的方向
- `spec_risk` subtype / fallback / weekly highlight 這條線的 focused tests 是通過的
- 最新一輪有跑：
  - `tests/test_core.py`
  - `tests/test_signal_library.py`
  - `tests/verification_tests/test_evaluate_recommendations.py`
  - `tests/verification_tests/test_summarize_outcomes.py`
  - `tests/verification_tests/test_verify_recommendations.py`
  - `tests/test_run_weekly_review.py`
- 以及 local dashboard / doctor / weekly review smoke checks

## 8. 常看輸出
- `theme_watchlist_daily/daily_report.md`
- `verification/watchlist_daily/verification_report.md`
- `verification/watchlist_daily/outcomes_summary.md`
- `theme_watchlist_daily/local_run_status.md`
- `theme_watchlist_daily/local_doctor.md`
- `theme_watchlist_daily/weekly_review.md`

## 9. 本機執行
- 本機固定 venv：`/Users/tokuzfunpi/codes/nvidia/311env`
- 建議直接用：
  - `VENV_PY=/Users/tokuzfunpi/codes/nvidia/311env/bin/python`
