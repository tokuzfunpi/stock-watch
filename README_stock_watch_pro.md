這版是完整強化版，包含：

- 強訊號分級：A / B / C / X
- 只通知前 N 檔強訊號
- 大盤濾網
- 排名變化 / setup 變化
- watchlist 分組
- Markdown / HTML 日報
- 簡單回測摘要
- state 持久化
- GitHub Actions 自動 commit

你要放進 repo 的檔案：
- daily_theme_watchlist_pro_max.py
- backtest_runner.py
- config.json
- watchlist.csv
- .github/workflows/stock-watch.yml

日常操作：
1. 改追蹤標的：改 watchlist.csv
2. 改通知規則 / 大盤條件：改 config.json
3. 看輸出：
   - theme_watchlist_daily/daily_rank.csv
   - theme_watchlist_daily/daily_report.md
   - theme_watchlist_daily/daily_report.html
   - theme_watchlist_daily/backtest_summary.csv
   - theme_watchlist_daily/backtest_events.csv

建議你先手動跑一次 workflow，確認：
- Telegram 正常
- repo 有自動 commit artifacts
- daily_report.html 有產出
