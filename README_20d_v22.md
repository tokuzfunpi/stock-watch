這是 20D v2.2 進攻優化版。

你這次要的三件事都做進去了：
1. attack 濾網更嚴，降低假突破
2. Telegram 改成更口語、像真人提醒
3. watchlist 擴大，加入更多題材 / 權值 / ETF / 衛星標的

重點邏輯：
- attack 候選需同時滿足：ret5 > 8、volume_ratio > 1.3、ret20 > 0，或出現 ACCEL
- theme / satellite 優先，theme 另外加分
- 排名偏重 setup、ret5、volume_ratio、ret20
- 通知給前 3 檔

你要放進 repo：
- daily_theme_watchlist_20d_v22.py
- config_20d_v22.json
- watchlist_20d_v22.csv
- .github/workflows/stock-watch.yml


補充：daily_report.md 現在會包含 Signals 對照表與 Regime 解釋，方便直接看報表判讀。

新增：
- daily_report.md 內含 Grade 對照表
- Telegram 推播前面會先給你一段盤面總結
- theme_watchlist_daily/alert_tracking.csv 會追蹤提醒後 1D / 5D / 20D 表現
