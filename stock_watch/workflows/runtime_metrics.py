from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


def build_runtime_metrics_markdown(
    *,
    generated_at: str,
    status: str,
    step_timings: dict[str, float],
    warnings: list[str],
    cache_stats: dict[str, int],
    backtest_meta: dict[str, object],
    wall_seconds: float | None = None,
) -> str:
    lines = [
        "# Runtime Metrics",
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
    total = sum(step_timings.values())
    lines.extend(["", f"- Total tracked seconds: `{total:.3f}`"])
    if wall_seconds is not None:
        lines.append(f"- Wall-clock seconds: `{wall_seconds:.3f}`")
    lines.extend(
        [
            "",
            "## Cache",
            "",
            (
                f"- History cache: `{cache_stats.get('history_hit', 0)}` exact hit / "
                f"`{cache_stats.get('history_disk_hit', 0)}` disk hit / "
                f"`{cache_stats.get('history_superset_hit', 0)}` superset hit / "
                f"`{cache_stats.get('history_miss', 0)}` miss"
            ),
            (
                f"- Indicator cache: `{cache_stats.get('indicator_hit', 0)}` exact hit / "
                f"`{cache_stats.get('indicator_superset_hit', 0)}` superset hit / "
                f"`{cache_stats.get('indicator_miss', 0)}` miss"
            ),
        ]
    )
    if backtest_meta:
        lines.extend(
            [
                "",
                "## Backtest",
                "",
                f"- Mode: `{backtest_meta.get('last_run_mode', 'unknown')}`",
                f"- Scanned cutoffs: `{backtest_meta.get('last_run_scanned_cutoffs', 0)}`",
            ]
        )
    if warnings:
        lines.extend(["", "## Warnings", ""])
        for warning in warnings:
            lines.append(f"- {warning}")
    return "\n".join(lines)


def load_backtest_meta(backtest_state_path: Path) -> dict[str, object]:
    if not backtest_state_path.exists():
        return {}
    try:
        return json.loads(backtest_state_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_runtime_metrics(
    *,
    runtime_metrics_json: Path,
    runtime_metrics_md: Path,
    backtest_state_path: Path,
    local_tz: ZoneInfo,
    status: str,
    step_timings: dict[str, float],
    warnings: list[str],
    cache_stats: dict[str, int],
    wall_seconds: float | None = None,
) -> None:
    generated_at = datetime.now(local_tz).strftime("%Y-%m-%d %H:%M:%S")
    backtest_meta = load_backtest_meta(backtest_state_path)
    payload = {
        "generated_at": generated_at,
        "status": status,
        "step_timings": step_timings,
        "warnings": warnings,
        "total_seconds": round(sum(step_timings.values()), 3),
        "wall_seconds": round(wall_seconds, 3) if wall_seconds is not None else None,
        "cache_stats": dict(cache_stats),
        "backtest_meta": backtest_meta,
    }
    runtime_metrics_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    runtime_metrics_md.write_text(
        build_runtime_metrics_markdown(
            generated_at=generated_at,
            status=status,
            step_timings=step_timings,
            warnings=warnings,
            cache_stats=dict(cache_stats),
            backtest_meta=backtest_meta,
            wall_seconds=wall_seconds,
        ),
        encoding="utf-8",
    )
