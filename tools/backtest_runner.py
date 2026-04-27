from __future__ import annotations

from daily_theme_watchlist import OUTDIR, run_backtest_dual


def main() -> int:
    steady_result, attack_result = run_backtest_dual()
    if (steady_result is None or steady_result.empty) and (attack_result is None or attack_result.empty):
        print("No backtest results.")
        return 0

    if steady_result is not None and not steady_result.empty:
        print("Steady backtest summary:")
        print(steady_result.to_string(index=False))
        print(f"Saved to: {OUTDIR / 'backtest_summary_steady.csv'}")
    else:
        print("Steady backtest summary: none")

    print()

    if attack_result is not None and not attack_result.empty:
        print("Attack backtest summary:")
        print(attack_result.to_string(index=False))
        print(f"Saved to: {OUTDIR / 'backtest_summary_attack.csv'}")
    else:
        print("Attack backtest summary: none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
