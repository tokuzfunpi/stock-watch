from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from verification import evaluate_recommendations
from verification import feedback_weight_sensitivity
from verification import summarize_outcomes
from verification import verify_recommendations

MODE_STEPS: dict[str, tuple[str, ...]] = {
    "full": ("verify", "evaluate", "summary", "feedback"),
    "preopen": ("verify",),
    "postclose": ("evaluate", "summary", "feedback"),
}


def _timed_call(step_timings: dict[str, float], name: str, func, *args, **kwargs):
    started = time.perf_counter()
    try:
        return func(*args, **kwargs)
    finally:
        step_timings[name] = time.perf_counter() - started


def _safe_dir_file_count(path: Path, pattern: str = "*") -> int:
    if not path.exists():
        return 0
    return sum(1 for file_path in path.glob(pattern) if file_path.is_file())


def _safe_dir_total_bytes(path: Path, pattern: str = "*") -> int:
    if not path.exists():
        return 0
    total = 0
    for file_path in path.glob(pattern):
        if not file_path.is_file():
            continue
        try:
            total += file_path.stat().st_size
        except OSError:
            continue
    return total


def _build_runtime_metrics_markdown(
    *,
    generated_at: str,
    status: str,
    step_timings: dict[str, float],
    warnings: list[str],
    cache_stats: dict[str, int],
    wall_seconds: float,
) -> str:
    lines = [
        "# Verification Runtime Metrics",
        f"- Generated: {generated_at}",
        f"- Status: `{status}`",
        "",
        "## Steps",
        "",
        "| Step | Seconds |",
        "| --- | --- |",
    ]
    for name, seconds in step_timings.items():
        lines.append(f"| {name} | {seconds:.4f} |")
    lines.extend(
        [
            "",
            f"- Total tracked seconds: `{sum(step_timings.values()):.3f}`",
            f"- Wall-clock seconds: `{wall_seconds:.3f}`",
            "",
            "## Cache",
            "",
            f"- Cache files: `{cache_stats.get('cache_files', 0)}`",
            f"- Cache bytes: `{cache_stats.get('cache_bytes', 0)}`",
        ]
    )
    if warnings:
        lines.extend(["", "## Warnings", ""])
        for warning in warnings:
            lines.append(f"- {warning}")
    return "\n".join(lines)


def _write_runtime_metrics(
    *,
    runtime_metrics_md: Path,
    runtime_metrics_json: Path,
    cache_dir: Path,
    status: str,
    step_timings: dict[str, float],
    warnings: list[str],
    wall_seconds: float,
) -> None:
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cache_stats = {
        "cache_files": _safe_dir_file_count(cache_dir, "*.csv"),
        "cache_bytes": _safe_dir_total_bytes(cache_dir, "*.csv"),
    }
    payload = {
        "generated_at": generated_at,
        "status": status,
        "step_timings": step_timings,
        "warnings": warnings,
        "total_seconds": round(sum(step_timings.values()), 3),
        "wall_seconds": round(wall_seconds, 3),
        "cache_stats": cache_stats,
    }
    runtime_metrics_json.parent.mkdir(parents=True, exist_ok=True)
    runtime_metrics_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    runtime_metrics_md.parent.mkdir(parents=True, exist_ok=True)
    runtime_metrics_md.write_text(
        _build_runtime_metrics_markdown(
            generated_at=generated_at,
            status=status,
            step_timings=step_timings,
            warnings=warnings,
            cache_stats=cache_stats,
            wall_seconds=wall_seconds,
        ),
        encoding="utf-8",
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    out_dir = Path("verification") / "watchlist_daily"
    parser = argparse.ArgumentParser(description="Run the daily verification workflow in one command.")

    parser.add_argument(
        "--mode",
        choices=tuple(MODE_STEPS),
        default="full",
        help="Choose `preopen` for the morning snapshot, `postclose` for outcome updates, or `full` for both.",
    )
    parser.add_argument("--rank-csv", default=str(Path("theme_watchlist_daily") / "daily_rank.csv"))
    parser.add_argument("--verification-out", default=str(out_dir / "verification_report.md"))
    parser.add_argument("--snapshot-csv", default=str(out_dir / "reco_snapshots.csv"))
    parser.add_argument("--outcomes-csv", default=str(out_dir / "reco_outcomes.csv"))
    parser.add_argument("--summary-out", default=str(out_dir / "outcomes_summary.md"))
    parser.add_argument("--feedback-out", default=str(out_dir / "feedback_weight_sensitivity.md"))
    parser.add_argument("--feedback-csv-out", default=str(out_dir / "feedback_weight_sensitivity.csv"))
    parser.add_argument("--runtime-metrics-md", default=str(out_dir / "runtime_metrics.md"))
    parser.add_argument("--runtime-metrics-json", default=str(out_dir / "runtime_metrics.json"))

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


def should_run_step(args: argparse.Namespace, step: str) -> bool:
    if getattr(args, f"skip_{step}"):
        return False
    return step in MODE_STEPS[args.mode]


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    started = time.perf_counter()
    step_timings: dict[str, float] = {}
    warnings: list[str] = []

    def _finish(status: str, code: int) -> int:
        _write_runtime_metrics(
            runtime_metrics_md=Path(str(args.runtime_metrics_md)),
            runtime_metrics_json=Path(str(args.runtime_metrics_json)),
            cache_dir=Path(str(args.cache_dir)),
            status=status,
            step_timings=step_timings,
            warnings=warnings,
            wall_seconds=time.perf_counter() - started,
        )
        return code

    if should_run_step(args, "verify"):
        code = _timed_call(step_timings, "verify", verify_recommendations.main, build_verify_argv(args))
        if code:
            warnings.append(f"verify exited with code {code}")
            return _finish("failed", code)

    if should_run_step(args, "evaluate"):
        code = _timed_call(step_timings, "evaluate", evaluate_recommendations.main, build_evaluate_argv(args))
        if code:
            warnings.append(f"evaluate exited with code {code}")
            return _finish("failed", code)

    if should_run_step(args, "summary"):
        code = _timed_call(step_timings, "summary", summarize_outcomes.main, build_summary_argv(args))
        if code:
            warnings.append(f"summary exited with code {code}")
            return _finish("failed", code)

    if should_run_step(args, "feedback"):
        code = _timed_call(step_timings, "feedback", feedback_weight_sensitivity.main, build_feedback_argv(args))
        if code:
            warnings.append(f"feedback exited with code {code}")
            return _finish("failed", code)

    return _finish("ok", 0)


if __name__ == "__main__":
    raise SystemExit(main())
