from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

from stock_watch.paths import REPO_ROOT
from stock_watch.paths import THEME_OUTDIR
from stock_watch.paths import VERIFICATION_OUTDIR

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import daily_theme_watchlist
from stock_watch.cli.weekly_review import build_data_quality_gate
from stock_watch.workflows.portfolio import run_portfolio_check
from verification.reports.summarize_outcomes import summarize_outcomes
from verification.workflows import run_daily_verification

LOCAL_STATUS_MD = THEME_OUTDIR / "local_run_status.md"
LOCAL_STATUS_JSON = THEME_OUTDIR / "local_run_status.json"
RUNTIME_METRICS_JSON = THEME_OUTDIR / "runtime_metrics.json"
PORTFOLIO_RUNTIME_METRICS_JSON = THEME_OUTDIR / "portfolio_runtime_metrics.json"
VERIFICATION_RUNTIME_METRICS_JSON = VERIFICATION_OUTDIR / "runtime_metrics.json"
PORTFOLIO_RUNTIME_METRICS_MD = THEME_OUTDIR / "portfolio_runtime_metrics.md"

MODE_STEPS: dict[str, tuple[str, ...]] = {
    "preopen": ("watchlist", "verification"),
    "postclose": ("watchlist", "portfolio", "verification"),
    "full": ("watchlist", "portfolio", "verification"),
    "portfolio": ("portfolio",),
}

VERIFICATION_MODE_BY_LOCAL_MODE = {
    "preopen": "preopen",
    "postclose": "postclose",
    "full": "full",
}

STEP_LABELS = {
    "watchlist": "Watchlist",
    "portfolio": "Portfolio",
    "verification": "Verification",
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the local stock-watch workflow in one command.")
    parser.add_argument(
        "--mode",
        choices=tuple(MODE_STEPS),
        default="full",
        help="Choose `preopen` for morning watchlist + snapshot, `postclose` for local review after close, `full` for all local steps, or `portfolio` for holdings only.",
    )
    parser.add_argument("--skip-watchlist", action="store_true")
    parser.add_argument("--skip-portfolio", action="store_true")
    parser.add_argument("--skip-verification", action="store_true")
    parser.add_argument("--force-watchlist", action="store_true", help="Ignore same-day watchlist duplicate guard.")

    parser.add_argument("--top-n-short", type=int, default=5)
    parser.add_argument("--top-n-midlong", type=int, default=5)
    parser.add_argument("--horizons", default="1,5,20")
    parser.add_argument("--weights", default="70:30,80:20,60:40")
    parser.add_argument("--period", default="180d")
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--backoff-seconds", type=float, default=1.0)
    parser.add_argument("--signal-date", default="")
    parser.add_argument("--since", default="")
    parser.add_argument("--until", default="")
    parser.add_argument("--max-days", type=int, default=0)

    parser.add_argument("--all-dates", action="store_true")
    parser.add_argument("--no-snapshot", action="store_true")
    return parser.parse_args(argv)


def should_run_step(args: argparse.Namespace, step: str) -> bool:
    if getattr(args, f"skip_{step}"):
        return False
    return step in MODE_STEPS[args.mode]


def build_verification_argv(args: argparse.Namespace) -> list[str]:
    local_mode = args.mode
    if local_mode not in VERIFICATION_MODE_BY_LOCAL_MODE:
        return []

    argv = [
        "--mode",
        VERIFICATION_MODE_BY_LOCAL_MODE[local_mode],
        "--top-n-short",
        str(args.top_n_short),
        "--top-n-midlong",
        str(args.top_n_midlong),
        "--horizons",
        str(args.horizons),
        "--weights",
        str(args.weights),
        "--period",
        str(args.period),
        "--batch-size",
        str(args.batch_size),
        "--retries",
        str(args.retries),
        "--backoff-seconds",
        str(args.backoff_seconds),
    ]
    if args.no_snapshot:
        argv.append("--no-snapshot")
    if args.all_dates:
        argv.append("--all-dates")
    if args.signal_date:
        argv.extend(["--signal-date", str(args.signal_date)])
    if args.since:
        argv.extend(["--since", str(args.since)])
    if args.until:
        argv.extend(["--until", str(args.until)])
    if args.max_days:
        argv.extend(["--max-days", str(args.max_days)])
    return argv


def run_portfolio_step() -> int:
    try:
        return run_portfolio_check(
            portfolio=daily_theme_watchlist.PORTFOLIO,
            base_strategy=daily_theme_watchlist.CONFIG.strategy,
            logger=daily_theme_watchlist.logger,
            get_market_regime=daily_theme_watchlist.get_market_regime,
            get_us_market_reference=daily_theme_watchlist.get_us_market_reference,
            build_market_scenario=daily_theme_watchlist.build_market_scenario,
            adjust_strategy_by_scenario=daily_theme_watchlist.adjust_strategy_by_scenario,
            run_watchlist=daily_theme_watchlist.run_watchlist,
            save_portfolio_reports=daily_theme_watchlist.save_portfolio_reports,
            build_macro_message=daily_theme_watchlist.build_macro_message,
            build_portfolio_message=daily_theme_watchlist.build_portfolio_message,
            runtime_metrics_md=PORTFOLIO_RUNTIME_METRICS_MD,
            runtime_metrics_json=PORTFOLIO_RUNTIME_METRICS_JSON,
            print_fn=print,
            stderr=sys.stderr,
        )
    except Exception as exc:
        err_msg = f"Portfolio check failed: {exc}"
        daily_theme_watchlist.logger.exception(err_msg)
        print(err_msg, file=sys.stderr)
        return 1


def _count_csv_rows(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        df = pd.read_csv(path)
    except Exception:
        return 0
    return int(len(df))


def _load_csv_safely(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception:
        return pd.DataFrame()


def _latest_signal_date(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        df = pd.read_csv(path, dtype={"signal_date": "string"})
    except Exception:
        return ""
    if "signal_date" not in df.columns or df.empty:
        return ""
    non_empty = df["signal_date"].dropna().astype(str).str.strip()
    non_empty = non_empty[non_empty != ""]
    if non_empty.empty:
        return ""
    return str(sorted(non_empty.tolist())[-1])


def _load_runtime_metrics(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def _spec_risk_bucket(df: pd.DataFrame) -> pd.Series:
    if df.empty:
        return pd.Series(dtype=str)
    score = pd.to_numeric(df.get("spec_risk_score"), errors="coerce")
    label = df.get("spec_risk_label", pd.Series(index=df.index, dtype=object)).fillna("").astype(str).str.strip()
    bucket = pd.Series("normal", index=df.index, dtype=object)
    bucket[(score >= 3) | label.isin(["投機偏高", "偏熱", "留意"])] = "watch"
    bucket[(score >= 6) | (label == "疑似炒作風險高")] = "high"
    return bucket.astype(str)


def _collect_spec_risk_metrics(daily_rank_csv: Path) -> dict[str, object]:
    if not daily_rank_csv.exists():
        return {
            "spec_risk_high_rows": 0,
            "spec_risk_watch_rows": 0,
            "spec_risk_top_tickers": [],
        }
    try:
        df = pd.read_csv(daily_rank_csv)
    except Exception:
        return {
            "spec_risk_high_rows": 0,
            "spec_risk_watch_rows": 0,
            "spec_risk_top_tickers": [],
        }
    if df.empty:
        return {
            "spec_risk_high_rows": 0,
            "spec_risk_watch_rows": 0,
            "spec_risk_top_tickers": [],
        }
    work = df.copy()
    work["spec_risk_bucket"] = _spec_risk_bucket(work)
    high_rows = int((work["spec_risk_bucket"] == "high").sum())
    watch_rows = int((work["spec_risk_bucket"] == "watch").sum())
    if "rank" not in work.columns:
        work["rank"] = range(1, len(work) + 1)
    work["_spec_risk_order"] = work["spec_risk_bucket"].map({"high": 0, "watch": 1, "normal": 2}).fillna(3)
    work["_spec_risk_score_num"] = pd.to_numeric(work.get("spec_risk_score"), errors="coerce").fillna(0)
    top = (
        work[work["spec_risk_bucket"].isin(["high", "watch"])]
        .sort_values(by=["_spec_risk_order", "_spec_risk_score_num", "rank"], ascending=[True, False, True])
        .head(5)
    )
    return {
        "spec_risk_high_rows": high_rows,
        "spec_risk_watch_rows": watch_rows,
        "spec_risk_top_tickers": top.get("ticker", pd.Series(dtype=str)).astype(str).tolist(),
    }


def collect_status_metrics(theme_outdir: Path = THEME_OUTDIR, verification_outdir: Path = VERIFICATION_OUTDIR) -> dict[str, object]:
    snapshots_csv = verification_outdir / "reco_snapshots.csv"
    outcomes_csv = verification_outdir / "reco_outcomes.csv"
    daily_rank_csv = theme_outdir / "daily_rank.csv"
    watchlist_runtime = _load_runtime_metrics(theme_outdir / "runtime_metrics.json")
    portfolio_runtime = _load_runtime_metrics(theme_outdir / "portfolio_runtime_metrics.json")
    verification_runtime = _load_runtime_metrics(verification_outdir / "runtime_metrics.json")
    spec_risk_metrics = _collect_spec_risk_metrics(daily_rank_csv)
    snapshots_df = _load_csv_safely(snapshots_csv)
    outcomes_df = _load_csv_safely(outcomes_csv)
    verification_gate = build_data_quality_gate(outcomes_df, snapshots_df)
    verification_gate_metrics = verification_gate.get("metrics", {})
    if not isinstance(verification_gate_metrics, dict):
        verification_gate_metrics = {}

    outcomes_total = 0
    outcomes_ok = 0
    outcomes_pending = 0
    midlong_gate_status = ""
    midlong_gate_horizon = ""
    midlong_gate_detail = ""
    if not outcomes_df.empty:
        outcomes_total = int(len(outcomes_df))
        if "status" in outcomes_df.columns:
            status = outcomes_df["status"].astype(str).str.strip()
            outcomes_ok = int((status == "ok").sum())
            outcomes_pending = int((status == "insufficient_forward_data").sum())
        try:
            parts = summarize_outcomes(outcomes_df)
            gate = parts.get("midlong_threshold_gate", pd.DataFrame())
        except Exception:
            gate = pd.DataFrame()
        if not gate.empty:
            blocked = gate[gate.get("decision", pd.Series(dtype=str)).astype(str) == "block_loosening"].copy()
            selected = blocked.iloc[0] if not blocked.empty else gate.iloc[0]
            midlong_gate_status = str(selected.get("decision", ""))
            horizon = selected.get("horizon_days", "")
            midlong_gate_horizon = "" if pd.isna(horizon) else str(int(horizon))
            midlong_gate_detail = (
                f"normal_below_n={int(selected.get('normal_below_n', 0))}, "
                f"below_hot_share={float(selected.get('below_hot_share_pct', 0.0)):.1f}%, "
                f"heat_gap={float(selected.get('heat_share_gap_pct', 0.0)):.1f}pp"
            )

    return {
        "latest_snapshot_signal_date": _latest_signal_date(snapshots_csv),
        "latest_outcome_signal_date": _latest_signal_date(outcomes_csv),
        "daily_rank_rows": _count_csv_rows(daily_rank_csv),
        "snapshot_rows": _count_csv_rows(snapshots_csv),
        "outcome_rows": outcomes_total,
        "outcome_ok_rows": outcomes_ok,
        "outcome_pending_rows": outcomes_pending,
        "midlong_threshold_gate_status": midlong_gate_status,
        "midlong_threshold_gate_horizon": midlong_gate_horizon,
        "midlong_threshold_gate_detail": midlong_gate_detail,
        "verification_gate_status": str(verification_gate.get("status", "unknown") or "unknown"),
        "snapshot_dup_keys": int(verification_gate_metrics.get("snapshot_dup_keys", 0) or 0),
        "outcome_dup_keys": int(verification_gate_metrics.get("outcome_dup_keys", 0) or 0),
        "signal_date_missing_rows": int(verification_gate_metrics.get("signal_date_missing_rows", 0) or 0),
        "no_price_series_rows": int(verification_gate_metrics.get("no_price_series_rows", 0) or 0),
        "watchlist_runtime_seconds": float(watchlist_runtime.get("wall_seconds", 0.0) or 0.0),
        "watchlist_runtime_status": str(watchlist_runtime.get("status", "") or ""),
        "portfolio_runtime_seconds": float(portfolio_runtime.get("wall_seconds", 0.0) or 0.0),
        "portfolio_runtime_status": str(portfolio_runtime.get("status", "") or ""),
        "verification_runtime_seconds": float(verification_runtime.get("wall_seconds", 0.0) or 0.0),
        "verification_runtime_status": str(verification_runtime.get("status", "") or ""),
        "spec_risk_high_rows": int(spec_risk_metrics["spec_risk_high_rows"]),
        "spec_risk_watch_rows": int(spec_risk_metrics["spec_risk_watch_rows"]),
        "spec_risk_top_tickers": list(spec_risk_metrics["spec_risk_top_tickers"]),
    }


def render_local_status_markdown(
    *,
    generated_at: str,
    mode: str,
    overall_status: str,
    steps: list[dict[str, str]],
    metrics: dict[str, object],
) -> str:
    lines = [
        "# Local Run Status",
        f"- Generated: {generated_at}",
        f"- Mode: `{mode}`",
        f"- Overall: `{overall_status}`",
        "",
        "## Steps",
        "",
        "| Step | Status | Detail |",
        "| --- | --- | --- |",
    ]
    for step in steps:
        lines.append(f"| {step['label']} | {step['status']} | {step['detail']} |")

    lines.extend(
        [
            "",
            "## Metrics",
            "",
            f"- Latest snapshot signal date: `{metrics.get('latest_snapshot_signal_date') or 'n/a'}`",
            f"- Latest outcome signal date: `{metrics.get('latest_outcome_signal_date') or 'n/a'}`",
            f"- Daily rank rows: `{metrics.get('daily_rank_rows', 0)}`",
            f"- Snapshot rows: `{metrics.get('snapshot_rows', 0)}`",
            f"- Outcome rows: `{metrics.get('outcome_rows', 0)}`",
            f"- Outcome OK rows: `{metrics.get('outcome_ok_rows', 0)}`",
            f"- Outcome pending rows: `{metrics.get('outcome_pending_rows', 0)}`",
            f"- Midlong threshold gate: `{metrics.get('midlong_threshold_gate_status') or 'n/a'}`"
            + (
                f" (`{metrics.get('midlong_threshold_gate_horizon')}D`, {metrics.get('midlong_threshold_gate_detail')})"
                if metrics.get("midlong_threshold_gate_status")
                else ""
            ),
            f"- Verification gate status: `{metrics.get('verification_gate_status') or 'unknown'}`",
            f"- Verification duplicate keys: snapshots=`{metrics.get('snapshot_dup_keys', 0)}`, outcomes=`{metrics.get('outcome_dup_keys', 0)}`",
            f"- Verification missing price rows: signal_date_missing=`{metrics.get('signal_date_missing_rows', 0)}`, no_price_series=`{metrics.get('no_price_series_rows', 0)}`",
            f"- Spec risk high rows: `{metrics.get('spec_risk_high_rows', 0)}`",
            f"- Spec risk watch rows: `{metrics.get('spec_risk_watch_rows', 0)}`",
            f"- Spec risk top tickers: `{', '.join(metrics.get('spec_risk_top_tickers', [])) or 'n/a'}`",
            f"- Watchlist runtime: `{metrics.get('watchlist_runtime_seconds', 0.0):.3f}s` ({metrics.get('watchlist_runtime_status') or 'n/a'})",
            f"- Portfolio runtime: `{metrics.get('portfolio_runtime_seconds', 0.0):.3f}s` ({metrics.get('portfolio_runtime_status') or 'n/a'})",
            f"- Verification runtime: `{metrics.get('verification_runtime_seconds', 0.0):.3f}s` ({metrics.get('verification_runtime_status') or 'n/a'})",
            "",
            "## Key Outputs",
            "",
            f"- Watchlist report: `{theme_outdir_str('daily_report.md')}`",
            f"- Watchlist runtime: `{theme_outdir_str('runtime_metrics.md')}`",
            f"- Portfolio report: `{theme_outdir_str('portfolio_report.md')}`",
            f"- Portfolio runtime: `{theme_outdir_str('portfolio_runtime_metrics.md')}`",
            f"- Verification report: `{verification_outdir_str('verification_report.md')}`",
            f"- Verification runtime: `{verification_outdir_str('runtime_metrics.md')}`",
            f"- Outcomes summary: `{verification_outdir_str('outcomes_summary.md')}`",
            f"- Feedback sensitivity: `{verification_outdir_str('feedback_weight_sensitivity.md')}`",
        ]
    )
    return "\n".join(lines)


def theme_outdir_str(name: str) -> str:
    return str(THEME_OUTDIR / name)


def verification_outdir_str(name: str) -> str:
    return str(VERIFICATION_OUTDIR / name)


def write_local_status_dashboard(
    *,
    args: argparse.Namespace,
    steps: list[dict[str, str]],
    overall_status: str,
    theme_outdir: Path = THEME_OUTDIR,
    verification_outdir: Path = VERIFICATION_OUTDIR,
    status_md: Path = LOCAL_STATUS_MD,
    status_json: Path = LOCAL_STATUS_JSON,
) -> None:
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    metrics = collect_status_metrics(theme_outdir, verification_outdir)
    payload = {
        "generated_at": generated_at,
        "mode": args.mode,
        "overall_status": overall_status,
        "steps": steps,
        "metrics": metrics,
        "outputs": {
            "watchlist_report": str(theme_outdir / "daily_report.md"),
            "watchlist_runtime": str(theme_outdir / "runtime_metrics.md"),
            "portfolio_report": str(theme_outdir / "portfolio_report.md"),
            "portfolio_runtime": str(theme_outdir / "portfolio_runtime_metrics.md"),
            "verification_report": str(verification_outdir / "verification_report.md"),
            "verification_runtime": str(verification_outdir / "runtime_metrics.md"),
            "outcomes_summary": str(verification_outdir / "outcomes_summary.md"),
            "feedback_sensitivity": str(verification_outdir / "feedback_weight_sensitivity.md"),
        },
    }
    status_md.parent.mkdir(parents=True, exist_ok=True)
    status_json.parent.mkdir(parents=True, exist_ok=True)
    status_md.write_text(
        render_local_status_markdown(
            generated_at=generated_at,
            mode=args.mode,
            overall_status=overall_status,
            steps=steps,
            metrics=metrics,
        ),
        encoding="utf-8",
    )
    status_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    steps: list[dict[str, str]] = []
    overall_status = "ok"

    step_runners = {
        "watchlist": lambda: daily_theme_watchlist.main(force_run=args.force_watchlist),
        "portfolio": run_portfolio_step,
        "verification": lambda: run_daily_verification.main(build_verification_argv(args)),
    }
    execution_order = ("watchlist", "portfolio", "verification")

    for index, step_name in enumerate(execution_order):
        if not should_run_step(args, step_name):
            steps.append({"name": step_name, "label": STEP_LABELS[step_name], "status": "skipped", "detail": "Not selected for this mode"})
            continue

        code = step_runners[step_name]()
        if code:
            overall_status = "failed"
            steps.append({"name": step_name, "label": STEP_LABELS[step_name], "status": "failed", "detail": f"Exit code {code}"})
            for blocked_step in execution_order[index + 1 :]:
                if should_run_step(args, blocked_step):
                    steps.append({"name": blocked_step, "label": STEP_LABELS[blocked_step], "status": "blocked", "detail": f"Blocked by {step_name} failure"})
                else:
                    steps.append({"name": blocked_step, "label": STEP_LABELS[blocked_step], "status": "skipped", "detail": "Not selected for this mode"})
            write_local_status_dashboard(args=args, steps=steps, overall_status=overall_status)
            return code

        steps.append({"name": step_name, "label": STEP_LABELS[step_name], "status": "completed", "detail": "OK"})

    write_local_status_dashboard(args=args, steps=steps, overall_status=overall_status)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
