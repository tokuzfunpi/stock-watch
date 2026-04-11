你現在要換成這版設定：

1. 把 daily_theme_watchlist_production_v2.py 放到 repo 根目錄
2. 把 watchlist.csv 放到 repo 根目錄
3. 把 workflow 放到 .github/workflows/stock-watch.yml

GitHub Secrets 仍然只需要這兩個：
- TELEGRAM_TOKEN
- TELEGRAM_CHAT_IDS

這版跟上一版不同的地方：
- watchlist.csv 可直接改追蹤股票，不用改 python
- ALWAYS_NOTIFY=false
- ENABLE_STATE=true
- workflow 會自動把 theme_watchlist_daily/ 內容 commit 回 repo
- 只有 state 改變且有重點訊號時才通知

之後要新增股票，只要改 watchlist.csv，例如：

ticker,name,enabled
2330.TW,台積電,true
2317.TW,鴻海,true

若某支先不追蹤：
enabled 改成 false 即可。
