from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from io import StringIO
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from daily_theme_watchlist import LOCAL_TZ
from verification.verify_recommendations import (
    build_verification_report_markdown,
    select_midlong_candidates,
    select_short_term_candidates,
    midlong_action_label,
    short_term_action_label,
    _maybe_date_from_rank,  # type: ignore[attr-defined]
)


@dataclass(frozen=True)
class BackfillItem:
    signal_date: str
    commit_sha: str


def run_git(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(REPO_ROOT),
        text=True,
        capture_output=True,
        check=False,
    )


def parse_git_log_dates(text: str) -> list[BackfillItem]:
    """
    Expects lines like:
      <sha> <YYYY-MM-DD>
    Returns one item per line (not yet de-duped).
    """
    items: list[BackfillItem] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        sha, date_str = parts[0], parts[1]
        if len(sha) < 7 or len(date_str) != 10:
            continue
        items.append(BackfillItem(signal_date=date_str, commit_sha=sha))
    return items


def list_daily_rank_commits(path: str) -> list[BackfillItem]:
    proc = run_git(["log", "--date=short", "--pretty=format:%H %ad", "--", path])
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "git log failed")
    # De-dupe to latest commit per day (log is newest first).
    out: list[BackfillItem] = []
    seen_dates: set[str] = set()
    for item in parse_git_log_dates(proc.stdout):
        if item.signal_date in seen_dates:
            continue
        seen_dates.add(item.signal_date)
        out.append(item)
    return out


def read_file_at_commit(path: str, commit_sha: str) -> str:
    proc = run_git(["show", f"{commit_sha}:{path}"])
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"git show failed for {commit_sha}:{path}")
    return proc.stdout


def append_snapshot_rows(
    df_rank: pd.DataFrame,
    *,
    generated_at: datetime,
    signal_date: str,
    source: str,
    source_sha: str,
    snapshot_csv: Path,
) -> int:
    short_candidates = select_short_term_candidates(df_rank).copy()
    midlong_candidates = select_midlong_candidates(df_rank).copy()
    if not short_candidates.empty:
        short_candidates["watch_type"] = "short"
        short_candidates["action"] = short_candidates.apply(short_term_action_label, axis=1)
    if not midlong_candidates.empty:
        midlong_candidates["watch_type"] = "midlong"
        midlong_candidates["action"] = midlong_candidates.apply(midlong_action_label, axis=1)

    combined = pd.concat([short_candidates, midlong_candidates], ignore_index=True)
    if combined.empty:
        return 0

    combined = combined.copy()
    combined["generated_at"] = generated_at.strftime("%Y-%m-%d %H:%M:%S %Z")
    combined["signal_date"] = signal_date
    combined["source"] = source
    combined["source_sha"] = source_sha

    keep = [
        "generated_at",
        "signal_date",
        "source",
        "source_sha",
        "watch_type",
        "rank",
        "ticker",
        "name",
        "grade",
        "setup_score",
        "risk_score",
        "ret5_pct",
        "ret20_pct",
        "volume_ratio20",
        "signals",
        "action",
    ]
    combined = combined[[c for c in keep if c in combined.columns]].copy()

    snapshot_csv.parent.mkdir(parents=True, exist_ok=True)
    write_header = not snapshot_csv.exists()
    with snapshot_csv.open("a", encoding="utf-8", newline="") as f:
        combined.to_csv(f, index=False, header=write_header)
    return int(len(combined))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill verification snapshots from git history.")
    parser.add_argument("--path", default="theme_watchlist_daily/daily_rank.csv")
    parser.add_argument("--since", default="", help="YYYY-MM-DD (inclusive)")
    parser.add_argument("--until", default="", help="YYYY-MM-DD (inclusive)")
    parser.add_argument("--limit", type=int, default=30, help="Max number of days to backfill (0=unlimited)")
    parser.add_argument("--out-dir", default=str(Path("verification") / "watchlist_daily" / "backfill_reports"))
    parser.add_argument("--snapshot-csv", default=str(Path("verification") / "watchlist_daily" / "reco_snapshots.csv"))
    parser.add_argument("--no-snapshot", action="store_true", help="Do not append to reco_snapshots.csv")
    parser.add_argument(
        "--rebuild-snapshot",
        action="store_true",
        help="Overwrite reco_snapshots.csv from scratch (makes a .bak copy if file exists).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    out_dir = Path(args.out_dir)
    snapshot_csv = Path(args.snapshot_csv)

    items = list_daily_rank_commits(str(args.path))
    if args.since:
        items = [x for x in items if x.signal_date >= args.since]
    if args.until:
        items = [x for x in items if x.signal_date <= args.until]
    limit = int(args.limit)
    if limit > 0:
        items = items[:limit]

    if not items:
        print("No backfill items.")
        return 0

    now_local = datetime.now(LOCAL_TZ)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not args.no_snapshot and args.rebuild_snapshot:
        if snapshot_csv.exists():
            bak = snapshot_csv.with_suffix(snapshot_csv.suffix + f".bak.{now_local.strftime('%Y%m%d_%H%M%S')}")
            snapshot_csv.replace(bak)
            print(f"Backed up snapshot CSV to: {bak}")
        snapshot_csv.parent.mkdir(parents=True, exist_ok=True)

    total_reports = 0
    total_snapshots = 0
    for item in items:
        try:
            content = read_file_at_commit(str(args.path), item.commit_sha)
            df_rank = pd.read_csv(StringIO(content))
            signal_date = _maybe_date_from_rank(df_rank) or item.signal_date
            source = f"git:{item.commit_sha}:{args.path}"
            report = build_verification_report_markdown(df_rank, source=source, now_local=now_local)
            report_path = out_dir / f"verification_report_{signal_date}.md"
            report_path.write_text(report, encoding="utf-8")
            total_reports += 1

            if not args.no_snapshot:
                total_snapshots += append_snapshot_rows(
                    df_rank,
                    generated_at=now_local,
                    signal_date=signal_date,
                    source=source,
                    source_sha=item.commit_sha,
                    snapshot_csv=snapshot_csv,
                )
        except Exception as exc:
            print(f"SKIP {item.signal_date} {item.commit_sha[:8]}: {exc}")
            continue

    print(f"Backfill done: reports={total_reports} snapshot_rows_appended={total_snapshots}")
    print(f"Reports dir: {out_dir}")
    if not args.no_snapshot:
        print(f"Snapshot CSV: {snapshot_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
