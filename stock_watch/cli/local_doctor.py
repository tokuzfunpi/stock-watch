from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

from stock_watch.paths import REPO_ROOT
from stock_watch.paths import THEME_OUTDIR
from stock_watch.paths import VERIFICATION_OUTDIR
from stock_watch.telegram_config import resolve_telegram_token
from stock_watch.cli.weekly_review import build_data_quality_gate

DOCTOR_MD = THEME_OUTDIR / "local_doctor.md"
DOCTOR_JSON = THEME_OUTDIR / "local_doctor.json"
DOCTOR_SUMMARY_TXT = THEME_OUTDIR / "local_doctor_summary.txt"


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    status: str
    detail: str


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check local stock-watch readiness before daily runs.")
    parser.add_argument("--skip-network", action="store_true", help="Skip the best-effort Yahoo DNS check.")
    parser.add_argument(
        "--fail-on",
        choices=("fail", "warn"),
        default="fail",
        help="Choose which overall status should return a non-zero exit code. `fail` keeps warnings informational; `warn` turns warnings into a failing health gate.",
    )
    return parser.parse_args(argv)


def _parse_chat_ids(raw: str) -> list[int]:
    tokens = [token.strip() for token in str(raw or "").replace(",", " ").split()]
    out: list[int] = []
    for token in tokens:
        if not token:
            continue
        out.append(int(token))
    return out


def _safe_count_csv_rows(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        return int(len(pd.read_csv(path)))
    except Exception:
        return 0


def _safe_dir_file_count(path: Path, pattern: str = "*") -> int:
    if not path.exists() or not path.is_dir():
        return 0
    try:
        return sum(1 for item in path.glob(pattern) if item.is_file())
    except Exception:
        return 0


def _safe_dir_total_bytes(path: Path, pattern: str = "*") -> int:
    if not path.exists() or not path.is_dir():
        return 0
    total = 0
    try:
        for item in path.glob(pattern):
            if item.is_file():
                total += int(item.stat().st_size)
    except Exception:
        return 0
    return total


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


def _load_csv_safely(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception:
        return pd.DataFrame()


def _watchlist_artifact_freshness(theme_outdir: Path) -> dict[str, str]:
    daily_rank_csv = theme_outdir / "daily_rank.csv"
    daily_report_md = theme_outdir / "daily_report.md"
    runtime_metrics_json = theme_outdir / "runtime_metrics.json"
    required = [daily_rank_csv, daily_report_md, runtime_metrics_json]
    missing = [path.name for path in required if not path.exists()]
    if missing:
        return {
            "status": "missing",
            "detail": f"missing: {', '.join(missing)}",
        }

    rank_mtime = daily_rank_csv.stat().st_mtime
    report_lag_seconds = int(rank_mtime - daily_report_md.stat().st_mtime)
    runtime_lag_seconds = int(rank_mtime - runtime_metrics_json.stat().st_mtime)

    if report_lag_seconds > 1:
        stale_targets = ["daily_report.md"]
        if runtime_lag_seconds > 1:
            stale_targets.append("runtime_metrics.json")
        return {
            "status": "stale_report",
            "detail": f"daily_rank.csv newer than {', '.join(stale_targets)} by up to {max(report_lag_seconds, runtime_lag_seconds)}s",
        }

    if runtime_lag_seconds > 1:
        return {
            "status": "report_current_runtime_stale",
            "detail": f"daily_report.md is synced to daily_rank.csv; runtime_metrics.json is older by {runtime_lag_seconds}s",
        }

    return {
        "status": "current",
        "detail": "daily_rank.csv, daily_report.md, and runtime_metrics.json look in sync",
    }


def _spec_risk_bucket(df: pd.DataFrame) -> pd.Series:
    if df.empty:
        return pd.Series(dtype=str)
    score = pd.to_numeric(df.get("spec_risk_score"), errors="coerce")
    label = df.get("spec_risk_label", pd.Series(index=df.index, dtype=object)).fillna("").astype(str).str.strip()
    bucket = pd.Series("normal", index=df.index, dtype=object)
    bucket[(score >= 3) | label.isin(["投機偏高", "偏熱", "留意"])] = "watch"
    bucket[(score >= 6) | (label == "疑似炒作風險高")] = "high"
    return bucket.astype(str)


def _collect_spec_risk_metrics(path: Path) -> dict[str, object]:
    if not path.exists():
        return {
            "spec_risk_high_rows": 0,
            "spec_risk_watch_rows": 0,
            "spec_risk_top_tickers": [],
        }
    try:
        df = pd.read_csv(path)
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


def _check_python_runtime() -> DoctorCheck:
    version = sys.version_info
    status = "ok" if version >= (3, 11) else "warn"
    detail = f"{sys.executable} (Python {version.major}.{version.minor}.{version.micro})"
    if status == "warn":
        detail += " — 建議用 Python 3.11+ 跑本機流程"
    return DoctorCheck(name="python_runtime", status=status, detail=detail)


def _check_required_file(path: Path, *, label: str) -> DoctorCheck:
    if not path.exists():
        return DoctorCheck(name=label, status="fail", detail=f"Missing: {path}")
    return DoctorCheck(name=label, status="ok", detail=str(path))


def _check_config_json(path: Path) -> DoctorCheck:
    if not path.exists():
        return DoctorCheck(name="config_json", status="fail", detail=f"Missing: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return DoctorCheck(name="config_json", status="fail", detail=f"Unreadable JSON: {exc}")

    required_keys = {"market_filter", "notify", "backtest", "group_weights"}
    missing = sorted(required_keys - set(payload))
    if missing:
        return DoctorCheck(name="config_json", status="fail", detail=f"Missing keys: {', '.join(missing)}")
    return DoctorCheck(name="config_json", status="ok", detail=f"Loaded {path.name} with required sections")


def _check_watchlist_csv(path: Path) -> DoctorCheck:
    if not path.exists():
        return DoctorCheck(name="watchlist_csv", status="fail", detail=f"Missing: {path}")
    try:
        df = pd.read_csv(path, dtype={"ticker": "string", "name": "string"})
    except Exception as exc:
        return DoctorCheck(name="watchlist_csv", status="fail", detail=f"Unreadable CSV: {exc}")

    if df.empty:
        return DoctorCheck(name="watchlist_csv", status="fail", detail="watchlist.csv is empty")
    cols = set(df.columns)
    required_cols = {"ticker", "name"}
    missing = sorted(required_cols - cols)
    if missing:
        return DoctorCheck(name="watchlist_csv", status="fail", detail=f"Missing columns: {', '.join(missing)}")
    return DoctorCheck(name="watchlist_csv", status="ok", detail=f"{len(df)} rows")


def _check_portfolio_csv(path: Path) -> DoctorCheck:
    if not path.exists():
        return DoctorCheck(name="portfolio_csv", status="info", detail=f"Missing optional local file: {path}")
    try:
        df = pd.read_csv(path, dtype={"ticker": "string"})
    except Exception as exc:
        return DoctorCheck(name="portfolio_csv", status="warn", detail=f"Unreadable CSV: {exc}")
    if df.empty:
        return DoctorCheck(name="portfolio_csv", status="info", detail="portfolio.csv exists but has no rows")
    if "ticker" not in df.columns:
        return DoctorCheck(name="portfolio_csv", status="warn", detail="portfolio.csv missing `ticker` column")
    return DoctorCheck(name="portfolio_csv", status="ok", detail=f"{len(df)} holdings rows")


def _check_telegram_config(chat_ids_path: Path) -> DoctorCheck:
    token, token_source = resolve_telegram_token(getupdates_url_path=REPO_ROOT / "telegram_getupdates_url")
    env_chat_ids = os.getenv("TELEGRAM_CHAT_IDS", "").strip()
    file_chat_ids = ""
    if chat_ids_path.exists():
        try:
            file_chat_ids = chat_ids_path.read_text(encoding="utf-8-sig").strip()
        except Exception as exc:
            return DoctorCheck(name="telegram_config", status="warn", detail=f"chat_ids unreadable: {exc}")

    try:
        parsed_chat_ids = _parse_chat_ids(env_chat_ids or file_chat_ids)
    except Exception as exc:
        return DoctorCheck(name="telegram_config", status="warn", detail=f"chat id parse failed: {exc}")

    if token and parsed_chat_ids:
        source = "env" if env_chat_ids else str(chat_ids_path)
        token_label = token_source or "resolved token source"
        return DoctorCheck(
            name="telegram_config",
            status="ok",
            detail=f"Token present from {token_label}, {len(parsed_chat_ids)} chat id(s) from {source}",
        )
    if token and not parsed_chat_ids:
        return DoctorCheck(name="telegram_config", status="warn", detail="TELEGRAM_TOKEN is set but no chat ids were found")
    if not token and parsed_chat_ids:
        return DoctorCheck(name="telegram_config", status="warn", detail="Chat ids exist but TELEGRAM_TOKEN is missing")
    return DoctorCheck(name="telegram_config", status="info", detail="Telegram env/chat ids are not configured")


def _check_output_dir(path: Path, *, label: str) -> DoctorCheck:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        return DoctorCheck(name=label, status="fail", detail=f"Cannot create directory: {exc}")
    if not path.is_dir():
        return DoctorCheck(name=label, status="fail", detail=f"Path is not a directory: {path}")
    return DoctorCheck(name=label, status="ok", detail=str(path))


def _check_cache_dir(path: Path, *, label: str) -> DoctorCheck:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".doctor_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except Exception as exc:
        return DoctorCheck(name=label, status="warn", detail=f"Cache dir not writable: {exc}")
    return DoctorCheck(name=label, status="ok", detail=str(path))


def _check_examples(paths: dict[str, Path]) -> DoctorCheck:
    missing = [name for name, path in paths.items() if not path.exists()]
    if missing:
        return DoctorCheck(name="example_files", status="warn", detail=f"Missing example files: {', '.join(missing)}")
    return DoctorCheck(name="example_files", status="ok", detail="portfolio / chat / telegram example files present")


def _check_verification_health(snapshot_csv: Path, outcomes_csv: Path) -> DoctorCheck:
    if not snapshot_csv.exists() or not outcomes_csv.exists():
        missing = [str(path) for path in [snapshot_csv, outcomes_csv] if not path.exists()]
        return DoctorCheck(name="verification_health", status="info", detail="Missing verification files: " + ", ".join(missing))

    snapshots = _load_csv_safely(snapshot_csv)
    outcomes = _load_csv_safely(outcomes_csv)
    gate = build_data_quality_gate(outcomes, snapshots)
    metrics = gate.get("metrics", {}) if isinstance(gate, dict) else {}
    gate_status = str(gate.get("status", "review")) if isinstance(gate, dict) else "review"
    has_blocking_signal = any(
        int(metrics.get(key, 0) or 0) > 0
        for key in ["snapshot_dup_keys", "outcome_dup_keys", "signal_date_missing_rows", "no_price_series_rows"]
    )
    if gate_status == "ok":
        doctor_status = "ok"
    elif has_blocking_signal:
        doctor_status = "warn"
    else:
        doctor_status = "info"

    detail = (
        f"gate={gate_status}; snapshots={metrics.get('snapshot_rows', 0)}; outcomes={metrics.get('outcome_rows', 0)}; "
        f"pending={metrics.get('pending_rows', 0)}; snapshot_dup={metrics.get('snapshot_dup_keys', 0)}; "
        f"outcome_dup={metrics.get('outcome_dup_keys', 0)}; signal_date_missing={metrics.get('signal_date_missing_rows', 0)}; "
        f"no_price_series={metrics.get('no_price_series_rows', 0)}; latest={metrics.get('latest_outcome_signal_date', '')}"
    )
    return DoctorCheck(name="verification_health", status=doctor_status, detail=detail)


def _check_watchlist_artifact_freshness(theme_outdir: Path) -> DoctorCheck:
    freshness = _watchlist_artifact_freshness(theme_outdir)
    status_map = {
        "current": "ok",
        "report_current_runtime_stale": "ok",
        "stale_report": "warn",
        "missing": "warn",
    }
    return DoctorCheck(
        name="watchlist_artifact_freshness",
        status=status_map.get(freshness["status"], "warn"),
        detail=f"{freshness['status']}: {freshness['detail']}",
    )


def _check_yahoo_dns() -> DoctorCheck:
    try:
        socket.getaddrinfo("query1.finance.yahoo.com", 443, type=socket.SOCK_STREAM)
    except Exception as exc:
        return DoctorCheck(name="yahoo_dns", status="warn", detail=f"DNS lookup failed: {exc}")
    return DoctorCheck(name="yahoo_dns", status="ok", detail="query1.finance.yahoo.com resolves")


def run_doctor_checks(args: argparse.Namespace) -> list[DoctorCheck]:
    checks = [
        _check_python_runtime(),
        _check_required_file(REPO_ROOT / "requirements.txt", label="requirements_txt"),
        _check_config_json(REPO_ROOT / "config.json"),
        _check_watchlist_csv(REPO_ROOT / "watchlist.csv"),
        _check_portfolio_csv(REPO_ROOT / "portfolio.csv"),
        _check_telegram_config(REPO_ROOT / "chat_ids"),
        _check_output_dir(THEME_OUTDIR, label="theme_outdir"),
        _check_output_dir(VERIFICATION_OUTDIR, label="verification_outdir"),
        _check_cache_dir(THEME_OUTDIR / ".yfinance_cache", label="theme_cache_dir"),
        _check_cache_dir(THEME_OUTDIR / "history_cache", label="history_cache_dir"),
        _check_cache_dir(VERIFICATION_OUTDIR / "yfinance_cache", label="verification_cache_dir"),
        _check_examples(
            {
                "portfolio.csv.example": REPO_ROOT / "portfolio.csv.example",
                "chat_id_map.csv.example": REPO_ROOT / "chat_id_map.csv.example",
                "telegram_getupdates_url.example": REPO_ROOT / "telegram_getupdates_url.example",
            }
        ),
        _check_watchlist_artifact_freshness(THEME_OUTDIR),
        _check_verification_health(
            VERIFICATION_OUTDIR / "reco_snapshots.csv",
            VERIFICATION_OUTDIR / "reco_outcomes.csv",
        ),
    ]
    if not args.skip_network:
        checks.append(_check_yahoo_dns())
    return checks


def collect_doctor_metrics() -> dict[str, object]:
    history_cache_dir = THEME_OUTDIR / "history_cache"
    artifact_freshness = _watchlist_artifact_freshness(THEME_OUTDIR)
    watchlist_runtime = _load_runtime_metrics(THEME_OUTDIR / "runtime_metrics.json")
    portfolio_runtime = _load_runtime_metrics(THEME_OUTDIR / "portfolio_runtime_metrics.json")
    report_sync_runtime = _load_runtime_metrics(THEME_OUTDIR / "report_sync_metrics.json")
    verification_runtime = _load_runtime_metrics(VERIFICATION_OUTDIR / "runtime_metrics.json")
    spec_risk_metrics = _collect_spec_risk_metrics(THEME_OUTDIR / "daily_rank.csv")
    verification_gate = build_data_quality_gate(
        _load_csv_safely(VERIFICATION_OUTDIR / "reco_outcomes.csv"),
        _load_csv_safely(VERIFICATION_OUTDIR / "reco_snapshots.csv"),
    )
    verification_metrics = verification_gate.get("metrics", {}) if isinstance(verification_gate, dict) else {}
    return {
        "daily_rank_rows": _safe_count_csv_rows(THEME_OUTDIR / "daily_rank.csv"),
        "alert_tracking_rows": _safe_count_csv_rows(THEME_OUTDIR / "alert_tracking.csv"),
        "snapshot_rows": _safe_count_csv_rows(VERIFICATION_OUTDIR / "reco_snapshots.csv"),
        "outcome_rows": _safe_count_csv_rows(VERIFICATION_OUTDIR / "reco_outcomes.csv"),
        "watchlist_artifact_freshness_status": artifact_freshness["status"],
        "watchlist_artifact_freshness_detail": artifact_freshness["detail"],
        "verification_gate_status": str(verification_gate.get("status", "unknown")) if isinstance(verification_gate, dict) else "unknown",
        "latest_snapshot_signal_date": str(verification_metrics.get("latest_snapshot_signal_date", "")),
        "latest_outcome_signal_date": str(verification_metrics.get("latest_outcome_signal_date", "")),
        "snapshot_dup_keys": int(verification_metrics.get("snapshot_dup_keys", 0) or 0),
        "outcome_dup_keys": int(verification_metrics.get("outcome_dup_keys", 0) or 0),
        "outcome_ok_rows": int(verification_metrics.get("ok_rows", 0) or 0),
        "outcome_pending_rows": int(verification_metrics.get("pending_rows", 0) or 0),
        "signal_date_missing_rows": int(verification_metrics.get("signal_date_missing_rows", 0) or 0),
        "no_price_series_rows": int(verification_metrics.get("no_price_series_rows", 0) or 0),
        "history_cache_files": _safe_dir_file_count(history_cache_dir, "*.csv"),
        "history_cache_bytes": _safe_dir_total_bytes(history_cache_dir, "*.csv"),
        "watchlist_runtime_seconds": round(float(watchlist_runtime.get("wall_seconds", 0.0) or 0.0), 3),
        "portfolio_runtime_seconds": round(float(portfolio_runtime.get("wall_seconds", 0.0) or 0.0), 3),
        "report_sync_runtime_seconds": round(float(report_sync_runtime.get("wall_seconds", 0.0) or 0.0), 3),
        "report_sync_runtime_status": str(report_sync_runtime.get("status", "") or ""),
        "report_sync_generated_at": str(report_sync_runtime.get("generated_at", "") or ""),
        "verification_runtime_seconds": round(float(verification_runtime.get("wall_seconds", 0.0) or 0.0), 3),
        "spec_risk_high_rows": int(spec_risk_metrics["spec_risk_high_rows"]),
        "spec_risk_watch_rows": int(spec_risk_metrics["spec_risk_watch_rows"]),
        "spec_risk_top_tickers": list(spec_risk_metrics["spec_risk_top_tickers"]),
    }


def overall_status(checks: list[DoctorCheck]) -> str:
    if any(check.status == "fail" for check in checks):
        return "fail"
    if any(check.status == "warn" for check in checks):
        return "warn"
    return "ok"


def should_exit_nonzero(*, overall: str, fail_on: str) -> bool:
    severity = {"ok": 0, "warn": 1, "fail": 2}
    return severity.get(overall, 0) >= severity.get(fail_on, 2)


def build_doctor_summary(checks: list[DoctorCheck]) -> dict[str, object]:
    failing = [check.name for check in checks if check.status == "fail"]
    warnings = [check.name for check in checks if check.status == "warn"]
    advisories = [check.name for check in checks if check.status == "info"]
    return {
        "fail_count": len(failing),
        "warn_count": len(warnings),
        "info_count": len(advisories),
        "failing_checks": failing,
        "warning_checks": warnings,
        "advisory_checks": advisories,
    }


def build_compact_summary(*, overall: str, checks: list[DoctorCheck], metrics: dict[str, object]) -> str:
    summary = build_doctor_summary(checks)
    highlights: list[str] = []
    if summary["warning_checks"]:
        highlights.append("warnings=" + ",".join(summary["warning_checks"]))
    if summary["advisory_checks"]:
        highlights.append("info=" + ",".join(summary["advisory_checks"]))
    highlights.append(f"notification={next((check.status for check in checks if check.name == 'telegram_config'), 'unknown')}")
    highlights.append(f"verification={metrics.get('verification_gate_status', 'unknown')}")
    highlights.append(f"report={metrics.get('watchlist_artifact_freshness_status', 'unknown')}")
    return f"overall={overall} | " + " | ".join(highlights)


def render_doctor_markdown(*, generated_at: str, checks: list[DoctorCheck], metrics: dict[str, object], overall: str) -> str:
    summary = build_doctor_summary(checks)
    lines = [
        "# Local Doctor",
        f"- Generated: {generated_at}",
        f"- Overall: `{overall}`",
        f"- Action required: `{summary['warn_count']}` warning(s), `{summary['fail_count']}` failure(s)",
        f"- Advisory only: `{summary['info_count']}` info item(s)",
        "",
        "## Summary",
        "",
        f"- Failing checks: `{', '.join(summary['failing_checks']) or 'none'}`",
        f"- Warning checks: `{', '.join(summary['warning_checks']) or 'none'}`",
        f"- Advisory checks: `{', '.join(summary['advisory_checks']) or 'none'}`",
        "",
        "## Checks",
        "",
        "| Check | Status | Detail |",
        "| --- | --- | --- |",
    ]
    for check in checks:
        lines.append(f"| {check.name} | {check.status} | {check.detail} |")

    lines.extend(
        [
            "",
            "## Metrics",
            "",
            f"- Daily rank rows: `{metrics.get('daily_rank_rows', 0)}`",
            f"- Watchlist artifact freshness: `{metrics.get('watchlist_artifact_freshness_status', 'unknown')}` ({metrics.get('watchlist_artifact_freshness_detail', 'n/a')})",
            f"- Alert tracking rows: `{metrics.get('alert_tracking_rows', 0)}`",
            f"- Verification snapshot rows: `{metrics.get('snapshot_rows', 0)}`",
            f"- Verification outcome rows: `{metrics.get('outcome_rows', 0)}`",
            f"- Verification gate status: `{metrics.get('verification_gate_status', 'unknown')}`",
            f"- Latest snapshot signal date: `{metrics.get('latest_snapshot_signal_date', '')}`",
            f"- Latest outcome signal date: `{metrics.get('latest_outcome_signal_date', '')}`",
            f"- Verification ok rows: `{metrics.get('outcome_ok_rows', 0)}`",
            f"- Verification pending rows: `{metrics.get('outcome_pending_rows', 0)}`",
            f"- Verification duplicate keys: `snapshots={metrics.get('snapshot_dup_keys', 0)}, outcomes={metrics.get('outcome_dup_keys', 0)}`",
            f"- Verification missing price rows: `signal_date_missing={metrics.get('signal_date_missing_rows', 0)}, no_price_series={metrics.get('no_price_series_rows', 0)}`",
            f"- History cache files: `{metrics.get('history_cache_files', 0)}`",
            f"- History cache bytes: `{metrics.get('history_cache_bytes', 0)}`",
            f"- Spec risk high rows: `{metrics.get('spec_risk_high_rows', 0)}`",
            f"- Spec risk watch rows: `{metrics.get('spec_risk_watch_rows', 0)}`",
            f"- Spec risk top tickers: `{', '.join(metrics.get('spec_risk_top_tickers', [])) or 'n/a'}`",
            f"- Watchlist runtime seconds: `{metrics.get('watchlist_runtime_seconds', 0.0)}`",
            f"- Portfolio runtime seconds: `{metrics.get('portfolio_runtime_seconds', 0.0)}`",
            f"- Report sync runtime seconds: `{metrics.get('report_sync_runtime_seconds', 0.0)}`"
            + (
                f" ({metrics.get('report_sync_runtime_status', 'n/a')}, {metrics.get('report_sync_generated_at')})"
                if metrics.get("report_sync_generated_at")
                else ""
            ),
            f"- Verification runtime seconds: `{metrics.get('verification_runtime_seconds', 0.0)}`",
        ]
    )
    return "\n".join(lines)


def write_doctor_outputs(
    *,
    checks: list[DoctorCheck],
    overall: str,
    metrics: dict[str, object],
    output_md: Path | None = None,
    output_json: Path | None = None,
    output_summary_txt: Path | None = None,
) -> None:
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    output_md = output_md or DOCTOR_MD
    output_json = output_json or DOCTOR_JSON
    output_summary_txt = output_summary_txt or DOCTOR_SUMMARY_TXT
    summary = build_doctor_summary(checks)
    compact_summary = build_compact_summary(overall=overall, checks=checks, metrics=metrics)
    payload = {
        "generated_at": generated_at,
        "overall": overall,
        "summary": summary,
        "summary_line": compact_summary,
        "checks": [check.__dict__ for check in checks],
        "metrics": metrics,
    }
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_summary_txt.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text(
        render_doctor_markdown(generated_at=generated_at, checks=checks, metrics=metrics, overall=overall),
        encoding="utf-8",
    )
    output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    output_summary_txt.write_text(compact_summary + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    checks = run_doctor_checks(args)
    metrics = collect_doctor_metrics()
    overall = overall_status(checks)
    write_doctor_outputs(checks=checks, overall=overall, metrics=metrics)
    return 1 if should_exit_nonzero(overall=overall, fail_on=args.fail_on) else 0


if __name__ == "__main__":
    raise SystemExit(main())
