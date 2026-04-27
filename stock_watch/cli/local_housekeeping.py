from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from stock_watch.paths import THEME_OUTDIR
from stock_watch.paths import VERIFICATION_OUTDIR

HOUSEKEEPING_MD = THEME_OUTDIR / "local_housekeeping.md"
HOUSEKEEPING_JSON = THEME_OUTDIR / "local_housekeeping.json"


@dataclass(frozen=True)
class HousekeepingAction:
    category: str
    path: str
    action: str
    status: str
    detail: str
    size_bytes: int


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prune older local verification artifacts and caches.")
    parser.add_argument("--apply", action="store_true", help="Actually delete the selected files. Default is dry-run.")
    parser.add_argument(
        "--theme-outdir",
        default=str(THEME_OUTDIR),
        help="Theme watch output directory that contains local runtime outputs and history cache files.",
    )
    parser.add_argument(
        "--verification-outdir",
        default=str(VERIFICATION_OUTDIR),
        help="Verification output directory that contains contexts, backfill_reports, backups, and cache files.",
    )
    parser.add_argument("--keep-contexts", type=int, default=5, help="How many codex context snapshots to keep.")
    parser.add_argument(
        "--keep-backfill-reports",
        type=int,
        default=10,
        help="How many backfill verification reports to keep.",
    )
    parser.add_argument("--keep-backups", type=int, default=5, help="How many CSV backup files to keep per backup family.")
    parser.add_argument(
        "--cache-max-age-days",
        type=int,
        default=14,
        help="Delete verification yfinance cache CSV files older than this many days.",
    )
    parser.add_argument(
        "--history-cache-max-age-days",
        type=int,
        default=30,
        help="Delete theme history cache CSV files older than this many days.",
    )
    parser.add_argument("--out", default=str(HOUSEKEEPING_MD))
    parser.add_argument("--json-out", default=str(HOUSEKEEPING_JSON))
    return parser.parse_args(argv)


def _sort_newest(paths: list[Path]) -> list[Path]:
    return sorted(paths, key=lambda path: (path.stat().st_mtime, path.name), reverse=True)


def _build_action(*, category: str, path: Path, action: str, status: str, detail: str) -> HousekeepingAction:
    size_bytes = int(path.stat().st_size) if path.exists() else 0
    return HousekeepingAction(
        category=category,
        path=str(path),
        action=action,
        status=status,
        detail=detail,
        size_bytes=size_bytes,
    )


def _collect_keep_latest_actions(*, paths: list[Path], keep: int, category: str, detail_prefix: str) -> list[HousekeepingAction]:
    actions: list[HousekeepingAction] = []
    ordered = _sort_newest(paths)
    keep = max(int(keep), 0)
    for index, path in enumerate(ordered):
        if index < keep:
            actions.append(
                _build_action(
                    category=category,
                    path=path,
                    action="keep",
                    status="kept",
                    detail=f"{detail_prefix}; within newest {keep}",
                )
            )
            continue
        actions.append(
            _build_action(
                category=category,
                path=path,
                action="delete",
                status="planned",
                detail=f"{detail_prefix}; older than newest {keep}",
            )
        )
    return actions


def _backup_group_name(path: Path) -> str:
    return path.name.split(".bak", 1)[0]


def collect_housekeeping_actions(
    *,
    theme_outdir: Path = THEME_OUTDIR,
    verification_outdir: Path = VERIFICATION_OUTDIR,
    keep_contexts: int,
    keep_backfill_reports: int,
    keep_backups: int,
    cache_max_age_days: int,
    history_cache_max_age_days: int,
    now: datetime | None = None,
) -> list[HousekeepingAction]:
    actions: list[HousekeepingAction] = []
    now = now or datetime.now()

    contexts_dir = verification_outdir / "contexts"
    if contexts_dir.exists():
        actions.extend(
            _collect_keep_latest_actions(
                paths=[path for path in contexts_dir.glob("*.json") if path.is_file()],
                keep=keep_contexts,
                category="contexts",
                detail_prefix="codex context snapshot",
            )
        )

    backfill_dir = verification_outdir / "backfill_reports"
    if backfill_dir.exists():
        actions.extend(
            _collect_keep_latest_actions(
                paths=[path for path in backfill_dir.glob("*.md") if path.is_file()],
                keep=keep_backfill_reports,
                category="backfill_reports",
                detail_prefix="backfill verification report",
            )
        )

    backup_files = [path for path in verification_outdir.glob("*.bak*") if path.is_file()]
    backup_groups: dict[str, list[Path]] = {}
    for path in backup_files:
        backup_groups.setdefault(_backup_group_name(path), []).append(path)
    for group_name, paths in sorted(backup_groups.items()):
        actions.extend(
            _collect_keep_latest_actions(
                paths=paths,
                keep=keep_backups,
                category="csv_backups",
                detail_prefix=f"CSV backup family `{group_name}`",
            )
        )

    cache_dir = verification_outdir / "yfinance_cache"
    if cache_dir.exists():
        cutoff = now.timestamp() - max(int(cache_max_age_days), 0) * 86400
        for path in _sort_newest([path for path in cache_dir.glob("*.csv") if path.is_file()]):
            if path.stat().st_mtime < cutoff:
                actions.append(
                    _build_action(
                        category="verification_cache",
                        path=path,
                        action="delete",
                        status="planned",
                        detail=f"verification cache older than {cache_max_age_days} day(s)",
                    )
                )
            else:
                actions.append(
                    _build_action(
                        category="verification_cache",
                        path=path,
                        action="keep",
                        status="kept",
                        detail=f"verification cache within {cache_max_age_days} day(s)",
                    )
                )

    history_cache_dir = theme_outdir / "history_cache"
    if history_cache_dir.exists():
        cutoff = now.timestamp() - max(int(history_cache_max_age_days), 0) * 86400
        for path in _sort_newest([path for path in history_cache_dir.glob("*.csv") if path.is_file()]):
            if path.stat().st_mtime < cutoff:
                actions.append(
                    _build_action(
                        category="history_cache",
                        path=path,
                        action="delete",
                        status="planned",
                        detail=f"theme history cache older than {history_cache_max_age_days} day(s)",
                    )
                )
            else:
                actions.append(
                    _build_action(
                        category="history_cache",
                        path=path,
                        action="keep",
                        status="kept",
                        detail=f"theme history cache within {history_cache_max_age_days} day(s)",
                    )
                )
    return actions


def apply_housekeeping_actions(actions: list[HousekeepingAction], *, apply: bool) -> list[HousekeepingAction]:
    updated: list[HousekeepingAction] = []
    for action in actions:
        if action.action != "delete":
            updated.append(action)
            continue
        if not apply:
            updated.append(action)
            continue
        path = Path(action.path)
        if not path.exists():
            updated.append(
                HousekeepingAction(
                    category=action.category,
                    path=action.path,
                    action=action.action,
                    status="missing",
                    detail="file already missing before cleanup",
                    size_bytes=0,
                )
            )
            continue
        path.unlink()
        updated.append(
            HousekeepingAction(
                category=action.category,
                path=action.path,
                action=action.action,
                status="deleted",
                detail=action.detail,
                size_bytes=action.size_bytes,
            )
        )
    return updated


def build_summary(actions: list[HousekeepingAction], *, apply: bool) -> dict[str, object]:
    planned = sum(1 for action in actions if action.status == "planned")
    deleted = sum(1 for action in actions if action.status == "deleted")
    kept = sum(1 for action in actions if action.status == "kept")
    reclaimable = sum(action.size_bytes for action in actions if action.action == "delete")
    return {
        "mode": "apply" if apply else "dry-run",
        "planned_delete_count": planned,
        "deleted_count": deleted,
        "kept_count": kept,
        "reclaimable_bytes": reclaimable,
    }


def render_housekeeping_markdown(*, generated_at: str, summary: dict[str, object], actions: list[HousekeepingAction]) -> str:
    lines = [
        "# Local Housekeeping",
        f"- Generated: {generated_at}",
        f"- Mode: `{summary.get('mode', 'dry-run')}`",
        f"- Planned delete count: `{summary.get('planned_delete_count', 0)}`",
        f"- Deleted count: `{summary.get('deleted_count', 0)}`",
        f"- Kept count: `{summary.get('kept_count', 0)}`",
        f"- Reclaimable bytes: `{summary.get('reclaimable_bytes', 0)}`",
        "",
        "## Actions",
        "",
        "| Category | Action | Status | Size (bytes) | Path | Detail |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for action in actions:
        lines.append(
            "| "
            + " | ".join(
                [
                    action.category,
                    action.action,
                    action.status,
                    str(action.size_bytes),
                    action.path.replace("|", "\\|"),
                    action.detail.replace("|", "\\|"),
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def write_outputs(
    *,
    actions: list[HousekeepingAction],
    summary: dict[str, object],
    out: Path,
    json_out: Path,
) -> None:
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    out.parent.mkdir(parents=True, exist_ok=True)
    json_out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        render_housekeeping_markdown(generated_at=generated_at, summary=summary, actions=actions),
        encoding="utf-8",
    )
    payload = {
        "generated_at": generated_at,
        "summary": summary,
        "actions": [asdict(action) for action in actions],
    }
    json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    actions = collect_housekeeping_actions(
        theme_outdir=Path(args.theme_outdir),
        verification_outdir=Path(args.verification_outdir),
        keep_contexts=args.keep_contexts,
        keep_backfill_reports=args.keep_backfill_reports,
        keep_backups=args.keep_backups,
        cache_max_age_days=args.cache_max_age_days,
        history_cache_max_age_days=args.history_cache_max_age_days,
    )
    actions = apply_housekeeping_actions(actions, apply=args.apply)
    summary = build_summary(actions, apply=args.apply)
    write_outputs(actions=actions, summary=summary, out=Path(args.out), json_out=Path(args.json_out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
