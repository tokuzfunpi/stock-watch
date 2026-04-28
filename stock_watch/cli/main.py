from __future__ import annotations

import argparse
import sys
from types import ModuleType

from stock_watch.cli import local_daily
from stock_watch.cli import local_doctor
from stock_watch.cli import local_housekeeping
from stock_watch.cli import local_website
from stock_watch.cli import weekly_review
from verification.cli import backfill_from_git
from verification.cli import evaluate_recommendations
from verification.cli import feedback_weight_sensitivity
from verification.cli import run_daily_verification
from verification.cli import summarize_outcomes
from verification.cli import verify_recommendations


COMMANDS: dict[str, tuple[str, ModuleType]] = {
    "daily": ("Run the daily local workflow.", local_daily),
    "doctor": ("Check local environment readiness.", local_doctor),
    "housekeeping": ("Clean or inspect generated local artifacts.", local_housekeeping),
    "website": ("Generate the local static dashboard.", local_website),
    "weekly": ("Generate weekly review outputs.", weekly_review),
}


VERIFICATION_COMMANDS: dict[str, tuple[str, ModuleType]] = {
    "daily": ("Run the verification workflow.", run_daily_verification),
    "snapshot": ("Snapshot current recommendations.", verify_recommendations),
    "evaluate": ("Evaluate recommendation outcomes.", evaluate_recommendations),
    "summary": ("Summarize realized outcomes.", summarize_outcomes),
    "feedback": ("Run feedback weight sensitivity.", feedback_weight_sensitivity),
    "backfill": ("Backfill recommendation snapshots from git history.", backfill_from_git),
}


ALIASES = {
    "preopen": ["daily", "--mode", "preopen"],
    "postclose": ["daily", "--mode", "postclose"],
    "full": ["daily", "--mode", "full"],
    "portfolio": ["daily", "--mode", "portfolio"],
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m stock_watch",
        description="Single CLI for stock-watch local workflows.",
    )
    parser.add_argument(
        "command",
        nargs="?",
        help=(
            "Command to run: "
            + ", ".join(sorted([*COMMANDS.keys(), *ALIASES.keys(), "verification"]))
        ),
    )
    parser.add_argument("args", nargs=argparse.REMAINDER, help="Arguments passed to the selected command.")
    return parser


def _print_commands() -> None:
    print("Commands:")
    for name, (description, _) in sorted(COMMANDS.items()):
        print(f"  {name:<12} {description}")
    print("  verification Run verification subcommands.")
    print()
    print("Daily aliases:")
    for name, target in sorted(ALIASES.items()):
        print(f"  {name:<12} {' '.join(target)}")
    print()
    print("Verification subcommands:")
    for name, (description, _) in sorted(VERIFICATION_COMMANDS.items()):
        print(f"  {name:<12} {description}")


def _dispatch_verification(argv: list[str]) -> int:
    if not argv or argv[0] in {"-h", "--help"}:
        print("Usage: python -m stock_watch verification <subcommand> [args...]")
        print()
        print("Subcommands:")
        for name, (description, _) in sorted(VERIFICATION_COMMANDS.items()):
            print(f"  {name:<12} {description}")
        return 0

    command = argv[0]
    if command not in VERIFICATION_COMMANDS:
        print(f"Unknown verification command: {command}", file=sys.stderr)
        return 2
    return VERIFICATION_COMMANDS[command][1].main(argv[1:])


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = _build_parser()
    parsed = parser.parse_args(argv)
    command = parsed.command
    args = list(parsed.args)

    if not command:
        parser.print_help()
        print()
        _print_commands()
        return 0

    if command in {"-h", "--help"}:
        parser.print_help()
        print()
        _print_commands()
        return 0

    if command in ALIASES:
        expanded = [*ALIASES[command], *args]
        return main(expanded)

    if command == "verification":
        return _dispatch_verification(args)

    if command not in COMMANDS:
        print(f"Unknown command: {command}", file=sys.stderr)
        return 2

    return COMMANDS[command][1].main(args)


if __name__ == "__main__":
    raise SystemExit(main())
