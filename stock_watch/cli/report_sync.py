from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

from stock_watch.paths import REPO_ROOT


def _load_legacy_daily_workflow():
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    import daily_theme_watchlist

    return daily_theme_watchlist


def _load_optional_csv(path: Path) -> pd.DataFrame | None:
    if not path.exists():
        return None
    try:
        df = pd.read_csv(path)
    except Exception:
        return None
    return None if df.empty else df


def _build_report_sync_metrics_markdown(
    *,
    generated_at: str,
    status: str,
    source_rank_csv: Path,
    rows: int,
    wall_seconds: float,
    warnings: list[str],
) -> str:
    lines = [
        "# Report Sync Metrics",
        f"- Generated: {generated_at}",
        f"- Status: `{status}`",
        f"- Source rank CSV: `{source_rank_csv}`",
        f"- Rows: `{rows}`",
        f"- Wall-clock seconds: `{wall_seconds:.3f}`",
    ]
    if warnings:
        lines.extend(["", "## Warnings", ""])
        for warning in warnings:
            lines.append(f"- {warning}")
    return "\n".join(lines)


def _write_report_sync_metrics(
    *,
    outdir: Path,
    source_rank_csv: Path,
    rows: int,
    wall_seconds: float,
    warnings: list[str],
) -> None:
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    payload = {
        "generated_at": generated_at,
        "status": "ok",
        "source_rank_csv": str(source_rank_csv),
        "rows": int(rows),
        "wall_seconds": round(wall_seconds, 3),
        "warnings": warnings,
    }
    metrics_json = outdir / "report_sync_metrics.json"
    metrics_md = outdir / "report_sync_metrics.md"
    metrics_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    metrics_md.write_text(
        _build_report_sync_metrics_markdown(
            generated_at=generated_at,
            status="ok",
            source_rank_csv=source_rank_csv,
            rows=rows,
            wall_seconds=wall_seconds,
            warnings=warnings,
        ),
        encoding="utf-8",
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rebuild daily watchlist reports from the latest daily_rank.csv without rerunning watchlist scans or Telegram notifications."
    )
    return parser.parse_args(argv)


def sync_reports_from_latest_rank(daily_theme_watchlist) -> tuple[int, list[str], int, float]:
    started = time.perf_counter()
    warnings: list[str] = []

    if not daily_theme_watchlist.RANK_CSV.exists():
        print(f"daily_rank.csv not found: {daily_theme_watchlist.RANK_CSV}", file=sys.stderr)
        return 1, warnings, 0, time.perf_counter() - started

    try:
        df_rank = pd.read_csv(daily_theme_watchlist.RANK_CSV)
    except Exception as exc:
        print(f"Failed to read daily_rank.csv: {exc}", file=sys.stderr)
        return 1, warnings, 0, time.perf_counter() - started

    if df_rank.empty:
        print(f"daily_rank.csv is empty: {daily_theme_watchlist.RANK_CSV}", file=sys.stderr)
        return 1, warnings, 0, time.perf_counter() - started

    try:
        market_regime = daily_theme_watchlist.get_market_regime()
    except Exception as exc:
        warnings.append(f"market_regime: {exc}")
        daily_theme_watchlist.logger.exception("Market regime fetch failed during report sync (best effort): %s", exc)
        market_regime = {"comment": "加權指數資料抓不到（best effort）", "is_bullish": True}

    try:
        us_market = daily_theme_watchlist.get_us_market_reference()
    except Exception as exc:
        warnings.append(f"us_market: {exc}")
        daily_theme_watchlist.logger.exception("US market reference failed during report sync (best effort): %s", exc)
        us_market = {"summary": "美股參考暫時抓不到（best effort）。", "rows": []}

    bt_steady = _load_optional_csv(daily_theme_watchlist.OUTDIR / "backtest_summary_steady.csv")
    bt_attack = _load_optional_csv(daily_theme_watchlist.OUTDIR / "backtest_summary_attack.csv")

    daily_theme_watchlist.save_reports(
        df_rank,
        market_regime,
        bt_steady,
        bt_attack,
        us_market=us_market,
    )
    wall_seconds = time.perf_counter() - started
    _write_report_sync_metrics(
        outdir=daily_theme_watchlist.OUTDIR,
        source_rank_csv=daily_theme_watchlist.RANK_CSV,
        rows=len(df_rank),
        wall_seconds=wall_seconds,
        warnings=warnings,
    )

    return 0, warnings, len(df_rank), wall_seconds


def main(argv: list[str] | None = None) -> int:
    parse_args(argv)
    daily_theme_watchlist = _load_legacy_daily_workflow()
    code, warnings, rows, wall_seconds = sync_reports_from_latest_rank(daily_theme_watchlist)
    if code:
        return code

    print(f"Synced report from {daily_theme_watchlist.RANK_CSV}")
    print(f"- markdown: {daily_theme_watchlist.REPORT_MD}")
    print(f"- html: {daily_theme_watchlist.REPORT_HTML}")
    print(f"- rows: {rows}")
    print(f"- wall_seconds: {wall_seconds:.3f}")
    if warnings:
        print("- warnings:")
        for warning in warnings:
            print(f"  - {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
