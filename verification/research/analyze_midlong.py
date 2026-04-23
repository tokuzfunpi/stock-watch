
import pandas as pd

df = pd.read_csv("verification/watchlist_daily/reco_outcomes.csv")
# Filter for 1D midlong
ml = df[(df["horizon_days"] == 1) & (df["watch_type"] == "midlong")].copy()

print("=== 1D Midlong: Overall by reco_status ===")
print(ml.groupby("reco_status").agg({"realized_ret_pct": ["count", "mean", "median"], "status": lambda x: (x == "ok").mean()}))

print("\n=== 1D Midlong: below_threshold vs ok by Market Heat ===")
heat_stats = ml.groupby(["reco_status", "market_heat"]).agg({"realized_ret_pct": ["count", "mean"]})
print(heat_stats)

print("\n=== 1D Midlong: below_threshold vs ok by Signal Date ===")
date_stats = ml.groupby(["signal_date", "reco_status"]).agg({"realized_ret_pct": ["count", "mean"]})
print(date_stats)

print("\n=== 1D Midlong: below_threshold vs ok by Action ===")
action_stats = ml.groupby(["reco_status", "action"]).agg({"realized_ret_pct": ["count", "mean"]})
print(action_stats)

# Check specifically for the 8 below_threshold rows
print("\n=== Details of below_threshold samples ===")
below = ml[ml["reco_status"] == "below_threshold"]
print(below[["signal_date", "ticker", "name", "action", "market_heat", "realized_ret_pct", "risk_score"]])
