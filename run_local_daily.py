from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import daily_theme_watchlist
import portfolio_check
from verification import run_daily_verification

THEME_OUTDIR = REPO_ROOT / "theme_watchlist_daily"
VERIFICATION_OUTDIR = REPO_ROOT / "verification" / "watchlist_daily"
LOCAL_STATUS_MD = THEME_OUTDIR / "local_run_status.md"
LOCAL_STATUS_JSON = THEME_OUTDIR / "local_run_status.json"

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


def _count_csv_rows(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        df = pd.read_csv(path)
    except Exception:
        return 0
    return int(len(df))


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


def collect_status_metrics(theme_outdir: Path = THEME_OUTDIR, verification_outdir: Path = VERIFICATION_OUTDIR) -> dict[str, object]:
    snapshots_csv = verification_outdir / "reco_snapshots.csv"
    outcomes_csv = verification_outdir / "reco_outcomes.csv"
    daily_rank_csv = theme_outdir / "daily_rank.csv"

    outcomes_total = 0
    outcomes_ok = 0
    outcomes_pending = 0
    if outcomes_csv.exists():
        try:
            outcomes_df = pd.read_csv(outcomes_csv)
        except Exception:
            outcomes_df = pd.DataFrame()
        if not outcomes_df.empty:
            outcomes_total = int(len(outcomes_df))
            if "status" in outcomes_df.columns:
                status = outcomes_df["status"].astype(str).str.strip()
                outcomes_ok = int((status == "ok").sum())
                outcomes_pending = int((status == "insufficient_forward_data").sum())

    return {
        "latest_snapshot_signal_date": _latest_signal_date(snapshots_csv),
        "latest_outcome_signal_date": _latest_signal_date(outcomes_csv),
        "daily_rank_rows": _count_csv_rows(daily_rank_csv),
        "snapshot_rows": _count_csv_rows(snapshots_csv),
        "outcome_rows": outcomes_total,
        "outcome_ok_rows": outcomes_ok,
        "outcome_pending_rows": outcomes_pending,
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
            "",
            "## Key Outputs",
            "",
            f"- Watchlist report: `{theme_outdir_str('daily_report.md')}`",
            f"- Portfolio report: `{theme_outdir_str('portfolio_report.md')}`",
            f"- Verification report: `{verification_outdir_str('verification_report.md')}`",
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
            "portfolio_report": str(theme_outdir / "portfolio_report.md"),
            "verification_report": str(verification_outdir / "verification_report.md"),
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
        "watchlist": lambda: daily_theme_watchlist.main(),
        "portfolio": lambda: portfolio_check.main(),
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
