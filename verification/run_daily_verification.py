from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from verification import evaluate_recommendations
from verification import feedback_weight_sensitivity
from verification import summarize_outcomes
from verification import verify_recommendations


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    out_dir = Path("verification") / "watchlist_daily"
    parser = argparse.ArgumentParser(description="Run the daily verification workflow in one command.")

    parser.add_argument("--rank-csv", default=str(Path("theme_watchlist_daily") / "daily_rank.csv"))
    parser.add_argument("--verification-out", default=str(out_dir / "verification_report.md"))
    parser.add_argument("--snapshot-csv", default=str(out_dir / "reco_snapshots.csv"))
    parser.add_argument("--outcomes-csv", default=str(out_dir / "reco_outcomes.csv"))
    parser.add_argument("--summary-out", default=str(out_dir / "outcomes_summary.md"))
    parser.add_argument("--feedback-out", default=str(out_dir / "feedback_weight_sensitivity.md"))
    parser.add_argument("--feedback-csv-out", default=str(out_dir / "feedback_weight_sensitivity.csv"))

    parser.add_argument("--top-n-short", type=int, default=5)
    parser.add_argument("--top-n-midlong", type=int, default=5)
    parser.add_argument("--horizons", default="1,5,20")
    parser.add_argument("--weights", default="70:30,80:20,60:40")
    parser.add_argument("--period", default="180d")
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--backoff-seconds", type=float, default=1.0)
    parser.add_argument("--cache-dir", default=str(out_dir / "yfinance_cache"))
    parser.add_argument("--signal-date", default="")
    parser.add_argument("--since", default="")
    parser.add_argument("--until", default="")
    parser.add_argument("--max-days", type=int, default=0)

    parser.add_argument("--all-dates", action="store_true")
    parser.add_argument("--no-snapshot", action="store_true")
    parser.add_argument("--skip-verify", action="store_true")
    parser.add_argument("--skip-evaluate", action="store_true")
    parser.add_argument("--skip-summary", action="store_true")
    parser.add_argument("--skip-feedback", action="store_true")
    return parser.parse_args(argv)


def build_verify_argv(args: argparse.Namespace) -> list[str]:
    argv = [
        "--rank-csv",
        str(args.rank_csv),
        "--out",
        str(args.verification_out),
        "--snapshot-csv",
        str(args.snapshot_csv),
        "--top-n-short",
        str(args.top_n_short),
        "--top-n-midlong",
        str(args.top_n_midlong),
    ]
    if args.no_snapshot:
        argv.append("--no-snapshot")
    return argv


def build_evaluate_argv(args: argparse.Namespace) -> list[str]:
    argv = [
        "--snapshot-csv",
        str(args.snapshot_csv),
        "--outcomes-csv",
        str(args.outcomes_csv),
        "--horizons",
        str(args.horizons),
        "--period",
        str(args.period),
        "--batch-size",
        str(args.batch_size),
        "--retries",
        str(args.retries),
        "--backoff-seconds",
        str(args.backoff_seconds),
        "--cache-dir",
        str(args.cache_dir),
    ]
    if args.signal_date:
        argv.extend(["--signal-date", str(args.signal_date)])
    if args.all_dates:
        argv.append("--all-dates")
    if args.since:
        argv.extend(["--since", str(args.since)])
    if args.until:
        argv.extend(["--until", str(args.until)])
    if args.max_days:
        argv.extend(["--max-days", str(args.max_days)])
    return argv


def build_summary_argv(args: argparse.Namespace) -> list[str]:
    return [
        "--outcomes-csv",
        str(args.outcomes_csv),
        "--out",
        str(args.summary_out),
    ]


def build_feedback_argv(args: argparse.Namespace) -> list[str]:
    return [
        "--alert-csv",
        str(Path("theme_watchlist_daily") / "alert_tracking.csv"),
        "--weights",
        str(args.weights),
        "--out",
        str(args.feedback_out),
        "--csv-out",
        str(args.feedback_csv_out),
    ]


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if not args.skip_verify:
        code = verify_recommendations.main(build_verify_argv(args))
        if code:
            return code

    if not args.skip_evaluate:
        code = evaluate_recommendations.main(build_evaluate_argv(args))
        if code:
            return code

    if not args.skip_summary:
        code = summarize_outcomes.main(build_summary_argv(args))
        if code:
            return code

    if not args.skip_feedback:
        code = feedback_weight_sensitivity.main(build_feedback_argv(args))
        if code:
            return code

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
