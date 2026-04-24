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

REPO_ROOT = Path(__file__).resolve().parent
THEME_OUTDIR = REPO_ROOT / "theme_watchlist_daily"
VERIFICATION_OUTDIR = REPO_ROOT / "verification" / "watchlist_daily"
DOCTOR_MD = THEME_OUTDIR / "local_doctor.md"
DOCTOR_JSON = THEME_OUTDIR / "local_doctor.json"


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    status: str
    detail: str


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check local stock-watch readiness before daily runs.")
    parser.add_argument("--skip-network", action="store_true", help="Skip the best-effort Yahoo DNS check.")
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
        return DoctorCheck(name="portfolio_csv", status="warn", detail=f"Missing optional local file: {path}")
    try:
        df = pd.read_csv(path, dtype={"ticker": "string"})
    except Exception as exc:
        return DoctorCheck(name="portfolio_csv", status="warn", detail=f"Unreadable CSV: {exc}")
    if df.empty:
        return DoctorCheck(name="portfolio_csv", status="warn", detail="portfolio.csv exists but has no rows")
    if "ticker" not in df.columns:
        return DoctorCheck(name="portfolio_csv", status="warn", detail="portfolio.csv missing `ticker` column")
    return DoctorCheck(name="portfolio_csv", status="ok", detail=f"{len(df)} holdings rows")


def _check_telegram_config(chat_ids_path: Path) -> DoctorCheck:
    token = os.getenv("TELEGRAM_TOKEN", "").strip()
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
        return DoctorCheck(name="telegram_config", status="ok", detail=f"Token present, {len(parsed_chat_ids)} chat id(s) from {source}")
    if token and not parsed_chat_ids:
        return DoctorCheck(name="telegram_config", status="warn", detail="TELEGRAM_TOKEN is set but no chat ids were found")
    if not token and parsed_chat_ids:
        return DoctorCheck(name="telegram_config", status="warn", detail="Chat ids exist but TELEGRAM_TOKEN is missing")
    return DoctorCheck(name="telegram_config", status="warn", detail="Telegram env/chat ids are not configured")


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
    ]
    if not args.skip_network:
        checks.append(_check_yahoo_dns())
    return checks


def collect_doctor_metrics() -> dict[str, int]:
    history_cache_dir = THEME_OUTDIR / "history_cache"
    return {
        "daily_rank_rows": _safe_count_csv_rows(THEME_OUTDIR / "daily_rank.csv"),
        "alert_tracking_rows": _safe_count_csv_rows(THEME_OUTDIR / "alert_tracking.csv"),
        "snapshot_rows": _safe_count_csv_rows(VERIFICATION_OUTDIR / "reco_snapshots.csv"),
        "outcome_rows": _safe_count_csv_rows(VERIFICATION_OUTDIR / "reco_outcomes.csv"),
        "history_cache_files": _safe_dir_file_count(history_cache_dir, "*.csv"),
        "history_cache_bytes": _safe_dir_total_bytes(history_cache_dir, "*.csv"),
    }


def overall_status(checks: list[DoctorCheck]) -> str:
    if any(check.status == "fail" for check in checks):
        return "fail"
    if any(check.status == "warn" for check in checks):
        return "warn"
    return "ok"


def render_doctor_markdown(*, generated_at: str, checks: list[DoctorCheck], metrics: dict[str, int], overall: str) -> str:
    lines = [
        "# Local Doctor",
        f"- Generated: {generated_at}",
        f"- Overall: `{overall}`",
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
            f"- Alert tracking rows: `{metrics.get('alert_tracking_rows', 0)}`",
            f"- Verification snapshot rows: `{metrics.get('snapshot_rows', 0)}`",
            f"- Verification outcome rows: `{metrics.get('outcome_rows', 0)}`",
            f"- History cache files: `{metrics.get('history_cache_files', 0)}`",
            f"- History cache bytes: `{metrics.get('history_cache_bytes', 0)}`",
        ]
    )
    return "\n".join(lines)


def write_doctor_outputs(
    *,
    checks: list[DoctorCheck],
    overall: str,
    metrics: dict[str, int],
    output_md: Path | None = None,
    output_json: Path | None = None,
) -> None:
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    output_md = output_md or DOCTOR_MD
    output_json = output_json or DOCTOR_JSON
    payload = {
        "generated_at": generated_at,
        "overall": overall,
        "checks": [check.__dict__ for check in checks],
        "metrics": metrics,
    }
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text(
        render_doctor_markdown(generated_at=generated_at, checks=checks, metrics=metrics, overall=overall),
        encoding="utf-8",
    )
    output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    checks = run_doctor_checks(args)
    metrics = collect_doctor_metrics()
    overall = overall_status(checks)
    write_doctor_outputs(checks=checks, overall=overall, metrics=metrics)
    return 1 if overall == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
