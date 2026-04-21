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
        return {
            "by_action": empty,
            "by_signal": empty,
            "overall_by_action": empty,
            "overall_by_signal": empty,
            "overall_by_signal_status": empty,
            "overall_by_action_status": empty,
        }

    df = outcomes.copy()
    df["status"] = df.get("status", "").astype(str)
    df = df[df["status"] == "ok"].copy()
    if df.empty:
        empty = pd.DataFrame()
        return {
            "by_action": empty,
            "by_signal": empty,
            "overall_by_action": empty,
            "overall_by_signal": empty,
            "overall_by_signal_status": empty,
            "overall_by_action_status": empty,
        }

    if "watch_type" in df.columns:
        df["watch_type"] = df["watch_type"].astype(str).str.strip().str.lower()
        df = df[df["watch_type"].isin(["short", "midlong"])].copy()
        if df.empty:
            empty = pd.DataFrame()
            return {
                "by_action": empty,
                "by_signal": empty,
                "overall_by_action": empty,
                "overall_by_signal": empty,
                "overall_by_signal_status": empty,
                "overall_by_action_status": empty,
            }

    # Split analysis: ok vs below_threshold (forced-fill).
    if "reco_status" in df.columns:
        df["reco_status"] = df["reco_status"].astype(str).str.strip()
        df.loc[df["reco_status"] == "", "reco_status"] = "unknown"
    else:
        df["reco_status"] = "unknown"

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

    overall_by_action = (
        df.groupby(["horizon_days", "watch_type", "action"], dropna=False)
        .agg(
            n=("realized_ret_pct", "count"),
            win_rate=("win", "mean"),
            avg_ret=("realized_ret_pct", "mean"),
            med_ret=("realized_ret_pct", "median"),
            min_ret=("realized_ret_pct", "min"),
            max_ret=("realized_ret_pct", "max"),
        )
        .reset_index()
        .sort_values(by=["horizon_days", "watch_type", "n", "avg_ret"], ascending=[True, True, False, False])
    )
    overall_by_action["win_rate"] = (overall_by_action["win_rate"] * 100).round(1)
    for c in ["avg_ret", "med_ret", "min_ret", "max_ret"]:
        overall_by_action[c] = overall_by_action[c].round(2)

    overall_by_signal = (
        df.groupby(["horizon_days", "watch_type"], dropna=False)
        .agg(
            n=("realized_ret_pct", "count"),
            win_rate=("win", "mean"),
            avg_ret=("realized_ret_pct", "mean"),
            med_ret=("realized_ret_pct", "median"),
        )
        .reset_index()
        .sort_values(by=["horizon_days", "watch_type"], ascending=[True, True])
    )
    overall_by_signal["win_rate"] = (overall_by_signal["win_rate"] * 100).round(1)
    for c in ["avg_ret", "med_ret"]:
        overall_by_signal[c] = overall_by_signal[c].round(2)

    overall_by_signal_status = (
        df.groupby(["horizon_days", "watch_type", "reco_status"], dropna=False)
        .agg(
            n=("realized_ret_pct", "count"),
            win_rate=("win", "mean"),
            avg_ret=("realized_ret_pct", "mean"),
            med_ret=("realized_ret_pct", "median"),
        )
        .reset_index()
        .sort_values(by=["horizon_days", "watch_type", "reco_status"], ascending=[True, True, True])
    )
    overall_by_signal_status["win_rate"] = (overall_by_signal_status["win_rate"] * 100).round(1)
    for c in ["avg_ret", "med_ret"]:
        overall_by_signal_status[c] = overall_by_signal_status[c].round(2)

    overall_by_action_status = (
        df.groupby(["horizon_days", "watch_type", "reco_status", "action"], dropna=False)
        .agg(
            n=("realized_ret_pct", "count"),
            win_rate=("win", "mean"),
            avg_ret=("realized_ret_pct", "mean"),
            med_ret=("realized_ret_pct", "median"),
            min_ret=("realized_ret_pct", "min"),
            max_ret=("realized_ret_pct", "max"),
        )
        .reset_index()
        .sort_values(by=["horizon_days", "watch_type", "reco_status", "n", "avg_ret"], ascending=[True, True, True, False, False])
    )
    overall_by_action_status["win_rate"] = (overall_by_action_status["win_rate"] * 100).round(1)
    for c in ["avg_ret", "med_ret", "min_ret", "max_ret"]:
        overall_by_action_status[c] = overall_by_action_status[c].round(2)

    return {
        "by_action": by_action,
        "by_signal": by_signal,
        "overall_by_action": overall_by_action,
        "overall_by_signal": overall_by_signal,
        "overall_by_signal_status": overall_by_signal_status,
        "overall_by_action_status": overall_by_action_status,
    }


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

    # Extra coverage diagnostics (helps understand why 20D isn't showing up yet).
    try:
        cov = outcomes.copy()
        cov["horizon_days"] = pd.to_numeric(cov.get("horizon_days"), errors="coerce").astype("Int64")
        cov["status"] = cov.get("status", "").astype(str)
        cov["is_ok"] = cov["status"] == "ok"
        by_h = (
            cov.groupby(["horizon_days"], dropna=False)
            .agg(
                total=("status", "count"),
                ok=("is_ok", "sum"),
                pending=("status", lambda s: int((s.astype(str) == "insufficient_forward_data").sum())),
                no_price=("status", lambda s: int((s.astype(str) == "no_price_series").sum())),
            )
            .reset_index()
            .sort_values(by=["horizon_days"])
        )
        by_h["ok_rate_pct"] = ((by_h["ok"] / by_h["total"]) * 100).round(1)
        lines.extend(["## Coverage By Horizon", _table_markdown(by_h).rstrip(), ""])
    except Exception:
        pass

    lines.extend(["## Overall By Signal (all dates)", _table_markdown(parts["overall_by_signal"]).rstrip(), ""])
    if not parts["overall_by_signal_status"].empty:
        lines.extend(["## Overall By Signal + reco_status (all dates)", _table_markdown(parts["overall_by_signal_status"]).rstrip(), ""])
    lines.extend(["## Overall By Action (all dates, top 80)", _table_markdown(parts["overall_by_action"].head(80)).rstrip(), ""])
    if not parts["overall_by_action_status"].empty:
        lines.extend(["## Overall By Action + reco_status (all dates, top 80)", _table_markdown(parts["overall_by_action_status"].head(80)).rstrip(), ""])
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
