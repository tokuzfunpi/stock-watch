這是 20D v2.1 交易版。

這版重點：
- setup 門檻放寬到 4 起跳，提早抓轉強
- 不再依賴單一 REBREAK / SURGE
- 新增 ACCEL 訊號，抓 5D / 10D 加速
- 通知邏輯改成四條件擇一：
  A. grade A
  B. setup >= 5 且 rank <= 3
  C. ret5 > 10
  D. setup_change > 0 或 rank_change > 0
- 回測拆成兩組：
  - steady：setup >= 5 且 risk <= 4
  - attack：ret5 > 8 或 vol_ratio > 1.5 或 ACCEL

你要放進 repo：
- daily_theme_watchlist_20d_v21.py
- config_20d_v21.json
- watchlist_20d_v21.csv
- .github/workflows/stock-watch.yml
