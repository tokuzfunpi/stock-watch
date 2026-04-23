# CODEX Handoff (Integration Sync 4)

## 1. 任務進度與已落地內容
- Phase 1 的核心分析已經在 `main` 有可用產出：
  - `summarize_outcomes.py` 可看 `hot vs normal`
  - 也可看 `scenario + heat`
  - 也可看 `date + heat`
- `verification/backfill_from_git.py` 現在正式保留 `scenario_label`，補歷史 snapshot 時不會再掉欄位。
- 短線推播已從「只有提醒」升級成「有行為保護」：
  - `明顯修正盤` 最多只留 1 檔 short 候選
  - `Heat Bias 偏強` 最多只留 2 檔 short 候選

## 2. Phase 1 關鍵發現
- 熱度依賴仍然成立：
  - Midlong 5D 在 `hot` 與 `normal` 盤勢間仍有明顯差距 (Hot +15.1% vs Normal -0.2%)
  - 代表現在不能只看近期漂亮績效，就認為規則本身已穩
- `scenario_label` 已進入 snapshot -> outcome 鏈條
  - 接下來可以更清楚分辨「盤熱」和「情境」到底哪個才是主因

## 3. 已知風險與 guardrails
- 不要把 `testv` 整包 merge 回來，尤其不要重帶那版 `vol_tag` 訊息變更；那版引用了未定義變數。
- 不要把 `summarize_outcomes.py` 的 Heat Bias 分析包在會吞掉整段結果的大範圍 `except` 裡。
- 不要把 `1D` 漂亮數字當成策略有效證據；校準仍以 `5D/20D` 為主。

## 4. 下一步最值得做的事
- 用新的 `heat_bias_by_scenario` / `heat_bias_by_date` 看 `hot` 與 `scenario` 到底誰才是主因。
- 驗證 short 限流後，`5D` 結果是否比單純顯示警告更穩。
- 若要再往前推，優先做 `portfolio_check.py` 的更深層 trim/exit 自動化，不要急著動主排序。

## 5. 本機執行
- 本機固定 venv：`/Users/tokuzfunpi/codes/nvidia/311env`
- 建議直接用：
  - `VENV_PY=/Users/tokuzfunpi/codes/nvidia/311env/bin/python`
