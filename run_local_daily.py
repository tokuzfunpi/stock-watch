from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import daily_theme_watchlist
import portfolio_check
from verification import run_daily_verification

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


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if should_run_step(args, "watchlist"):
        code = daily_theme_watchlist.main()
        if code:
            return code

    if should_run_step(args, "portfolio"):
        code = portfolio_check.main()
        if code:
            return code

    if should_run_step(args, "verification"):
        code = run_daily_verification.main(build_verification_argv(args))
        if code:
            return code

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
