這是 20D 精修版（方案 B）。

定位：
- 保留 v2 / v3 的設定檔、報表、state、分組、回測框架
- 評分邏輯回到 20D 中波段
- 通知偏向題材股中段延續 / 重新啟動
- 回測重點回到 1D / 5D / 20D

這版特性：
- 排名依 setup_score, risk_score, ret20_pct, ret10_pct
- theme 小幅加分，ETF 降權
- 通知條件：setup >= 5, risk <= 4, 且有 REBREAK/SURGE 或排名/分數轉強
- 大盤濾網保留，但 A 級訊號在弱盤仍可放行

你要放進 repo 的檔案：
- daily_theme_watchlist_20d_refined.py
- config_20d_refined.json
- watchlist_20d_refined.csv
- .github/workflows/stock-watch.yml

建議驗證：
1. 手動跑 workflow
2. 看 daily_rank.csv 是否比短線版更穩定
3. 看 backtest_summary.csv 的 20D 是否恢復優勢
4. 看 Telegram 是否開始出現少量但更有品質的訊號
