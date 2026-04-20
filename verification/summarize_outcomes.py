from __future__ import annotations

import argparse
from datetime import datetime
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from daily_theme_watchlist import LOCAL_TZ


def _pct(v: float | None) -> str:
    if v is None or pd.isna(v):
        return ""
    return f"{float(v):.2f}"


def _table_markdown(df: pd.DataFrame) -> str:
    if df.empty:
        return "_None_\n"
    headers = [str(c) for c in df.columns.tolist()]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for _, r in df.iterrows():
        row: list[str] = []
        for c in headers:
            val = r.get(c)
            if pd.isna(val):
                text = ""
            else:
                text = str(val)
            text = text.replace("|", "\\|").replace("\n", " ")
            row.append(text)
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines) + "\n"


def summarize_outcomes(outcomes: pd.DataFrame) -> dict[str, pd.DataFrame]:
    if outcomes.empty:
        empty = pd.DataFrame()
        return {"by_action": empty, "by_signal": empty}

    df = outcomes.copy()
    df["status"] = df.get("status", "").astype(str)
    df = df[df["status"] == "ok"].copy()
    if df.empty:
        empty = pd.DataFrame()
        return {"by_action": empty, "by_signal": empty}

    df["realized_ret_pct"] = pd.to_numeric(df["realized_ret_pct"], errors="coerce")
    df["horizon_days"] = pd.to_numeric(df["horizon_days"], errors="coerce").astype("Int64")
    df["win"] = df["realized_ret_pct"] > 0

    group_cols = ["signal_date", "horizon_days", "watch_type", "action"]
    by_action = (
        df.groupby(group_cols, dropna=False)
        .agg(
            n=("realized_ret_pct", "count"),
            win_rate=("win", "mean"),
            avg_ret=("realized_ret_pct", "mean"),
            med_ret=("realized_ret_pct", "median"),
            min_ret=("realized_ret_pct", "min"),
            max_ret=("realized_ret_pct", "max"),
        )
        .reset_index()
        .sort_values(by=["signal_date", "horizon_days", "watch_type", "avg_ret"], ascending=[False, True, True, False])
    )
    by_action["win_rate"] = (by_action["win_rate"] * 100).round(1)
    for c in ["avg_ret", "med_ret", "min_ret", "max_ret"]:
        by_action[c] = by_action[c].round(2)

    by_signal = (
        df.groupby(["signal_date", "horizon_days", "watch_type"], dropna=False)
        .agg(
            n=("realized_ret_pct", "count"),
            win_rate=("win", "mean"),
            avg_ret=("realized_ret_pct", "mean"),
            med_ret=("realized_ret_pct", "median"),
        )
        .reset_index()
        .sort_values(by=["signal_date", "horizon_days", "watch_type"], ascending=[False, True, True])
    )
    by_signal["win_rate"] = (by_signal["win_rate"] * 100).round(1)
    for c in ["avg_ret", "med_ret"]:
        by_signal[c] = by_signal[c].round(2)

    return {"by_action": by_action, "by_signal": by_signal}


def build_summary_markdown(outcomes: pd.DataFrame, source: str, now_local: datetime | None = None) -> str:
    now_local = now_local or datetime.now(LOCAL_TZ)
    parts = summarize_outcomes(outcomes)

    lines: list[str] = [
        "# Recommendation Outcomes Summary",
        f"- Generated: {now_local.strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"- Source: {source}",
        "",
    ]

    if outcomes.empty:
        lines.extend(["_No outcomes file rows._", ""])
        return "\n".join(lines)

    ok = outcomes[outcomes.get("status", "").astype(str) == "ok"]
    lines.extend(
        [
            "## Coverage",
            f"- Total rows: {len(outcomes)}",
            f"- OK rows: {len(ok)}",
            "",
        ]
    )

    lines.extend(["## By Signal (watch_type)", _table_markdown(parts["by_signal"].head(30)).rstrip(), ""])
    lines.extend(["## By Action (top 50)", _table_markdown(parts["by_action"].head(50)).rstrip(), ""])
    lines.append("")
    return "\n".join(lines).strip() + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize reco outcomes (win rate / average returns).")
    out_dir = Path("verification") / "watchlist_daily"
    parser.add_argument("--outcomes-csv", default=str(out_dir / "reco_outcomes.csv"))
    parser.add_argument("--out", default=str(out_dir / "outcomes_summary.md"))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    outcomes_csv = Path(args.outcomes_csv)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not outcomes_csv.exists():
        report = build_summary_markdown(pd.DataFrame(), source=str(outcomes_csv))
        out_path.write_text(report, encoding="utf-8")
        print(report)
        return 0

    outcomes = pd.read_csv(outcomes_csv)
    report = build_summary_markdown(outcomes, source=str(outcomes_csv))
    out_path.write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
