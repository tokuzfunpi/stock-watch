這是目前使用中的 stock watch 版本。

你這次要的三件事都做進去了：
1. attack 濾網更嚴，降低假突破
2. Telegram 改成更口語、像真人提醒
3. watchlist 擴大，加入更多題材 / 權值 / ETF / 衛星標的

重點邏輯：
- attack 候選需同時滿足：ret5 > 8、volume_ratio > 1.3、ret20 > 0，或出現 ACCEL
- theme / satellite 優先，theme 另外加分
- 排名偏重 setup、ret5、volume_ratio、ret20
- 通知給前 3 檔

主要執行檔：
- `daily_theme_watchlist.py`
  - 每日觀察清單、排行、回測、主通知
- `portfolio_check.py`
  - 本機持股檢查專用
  - 共用同一套資料抓取與判讀邏輯
  - 不送 Telegram，只輸出本機報表與 CLI 內容

主要設定檔：
- `config.json`
- `watchlist.csv`
- `portfolio.csv.example`
- `.github/workflows/stock-watch.yml`

安裝與執行：
- `python3 -m venv .venv`
- `source .venv/bin/activate`
- `pip install -r requirements.txt`
- `python3 -m unittest discover -s tests`
- `python3 daily_theme_watchlist.py`

如果要跑持股檢查：
- 複製 `portfolio.csv.example` 成本機的 `portfolio.csv`
- 填入自己的持股資料
- `python3 portfolio_check.py`
- 執行後會：
  - 更新 `theme_watchlist_daily/portfolio_report.md`
  - 更新 `theme_watchlist_daily/portfolio_report.html`
  - 直接在 CLI 印出大盤摘要與持股建議

補充：
- `daily_report.md` 會包含 Signals 對照表與 Regime 解釋，方便直接看報表判讀
- `portfolio.csv` 是本機私有檔，不進 git
- `theme_watchlist_daily/portfolio_report.md` 與 `portfolio_report.html` 由 `portfolio_check.py` 產生

新增：
- daily_report.md 內含 Grade 對照表
- Telegram 推播前面會先給你一段盤面總結
- theme_watchlist_daily/alert_tracking.csv 會追蹤提醒後 1D / 5D / 20D 表現
