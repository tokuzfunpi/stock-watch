這是 v3 短線版，重點不是抓 20D 中波段，而是抓 1D / 3D / 5D 的短線轉強。

這版強化：
- 新增 EARLY / BREAK20 / TIGHT 短線訊號
- setup_score 明顯偏向短線動能
- theme 股加分，core / etf 降權
- 通知優先看 theme
- 回測改成 1D / 3D / 5D
- 允許大盤偏弱時，A 級訊號仍可通知

你要放進 repo 的檔案：
- daily_theme_watchlist_v3_short.py
- config_v3_short.json
- watchlist_v3_short.csv
- .github/workflows/stock-watch.yml

建議驗證順序：
1. 手動跑 workflow
2. 看 daily_rank.csv 前三名是否以 theme 為主
3. 看 backtest_summary.csv 的 1D / 3D / 5D 是否比上一版更貼近你的目標
4. 看 Telegram 是否開始出現候選股
