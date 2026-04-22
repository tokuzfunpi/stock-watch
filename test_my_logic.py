
import pandas as pd
from daily_theme_watchlist import (
    watch_price_plan, 
    adjust_strategy_by_scenario, 
    CONFIG, 
    StrategyConfig
)

# 1. 驗證 ATR 波動調節邏輯
print("=== 1. ATR 波動調節驗證 ===")
base_row = pd.Series({
    "close": 100.0,
    "ma20": 95.0,
    "ma60": 90.0,
    "ret5_pct": 5.0,
    "ret20_pct": 10.0,
    "risk_score": 2,
    "signals": "ACCEL",
    "holding_style": "進攻持股"
})

# 低波動 (ATR 3%)
row_low_vol = base_row.copy()
row_low_vol["atr_pct"] = 3.0
plan_low = watch_price_plan(row_low_vol, "short")

# 高波動 (ATR 6%)
row_high_vol = base_row.copy()
row_high_vol["atr_pct"] = 6.0
plan_high = watch_price_plan(row_high_vol, "short")

print(f"低波動 (ATR 3%) - 加碼價: {plan_low['add_price']}, 失效價: {plan_low['stop_price']}")
print(f"高波動 (ATR 6%) - 加碼價: {plan_high['add_price']}, 失效價: {plan_high['stop_price']}")
print("-> 預期結果：高波動標的的加碼價應該更低，失效價也應該更遠（防守範圍變大）。")

# 2. 驗證情境感知門檻調整
print("\n=== 2. 情境感知門檻驗證 ===")
normal_strat = CONFIG.strategy
修正盤_scenario = {"label": "明顯修正盤"}
strict_strat = adjust_strategy_by_scenario(normal_strat, 修正盤_scenario)

print(f"正常盤 - ACCEL 成交量比率門檻: {normal_strat.accel_vol_ratio}")
print(f"修正盤 - ACCEL 成交量比率門檻: {strict_strat.accel_vol_ratio}")
print("-> 預期結果：修正盤的門檻應該更高（更嚴格）。")
