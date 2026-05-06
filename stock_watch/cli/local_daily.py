from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

from stock_watch.paths import THEME_OUTDIR
from stock_watch.paths import VERIFICATION_OUTDIR
from stock_watch.cli.weekly_review import build_data_quality_gate
from stock_watch.cli.weekly_review import build_short_gate_tuning_draft
from stock_watch.cli.weekly_review import filter_recent_signal_dates
from stock_watch.cli import quality_value
from stock_watch.cli import report_sync
from stock_watch.workflows.daily_watchlist import run_daily_watchlist
from stock_watch.workflows.portfolio import run_default_portfolio_check
from verification.reports.summarize_outcomes import summarize_outcomes
from verification.workflows import run_daily_verification

LOCAL_STATUS_MD = THEME_OUTDIR / "local_run_status.md"
LOCAL_STATUS_JSON = THEME_OUTDIR / "local_run_status.json"
RUNTIME_METRICS_JSON = THEME_OUTDIR / "runtime_metrics.json"
PORTFOLIO_RUNTIME_METRICS_JSON = THEME_OUTDIR / "portfolio_runtime_metrics.json"
VERIFICATION_RUNTIME_METRICS_JSON = VERIFICATION_OUTDIR / "runtime_metrics.json"
PORTFOLIO_RUNTIME_METRICS_MD = THEME_OUTDIR / "portfolio_runtime_metrics.md"
REPORT_SYNC_METRICS_JSON = THEME_OUTDIR / "report_sync_metrics.json"
SHADOW_OPEN_NOT_CHASE_TRACKING_MD = THEME_OUTDIR / "shadow_open_not_chase_tracking.md"
SHADOW_OPEN_NOT_CHASE_TRACKING_CSV = THEME_OUTDIR / "shadow_open_not_chase_tracking.csv"
QUALITY_VALUE_ENTRY_PLAN_CSV = THEME_OUTDIR / "quality_value_entry_plan.csv"

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

STEP_LABELS = {
    "watchlist": "Watchlist",
    "portfolio": "Portfolio",
    "verification": "Verification",
    "report_sync": "Report Sync",
    "quality_value": "Quality Value",
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
    parser.add_argument("--force-watchlist", action="store_true", help="Ignore same-day watchlist duplicate guard.")
    parser.add_argument(
        "--sync-watchlist-report",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Auto-run `report-sync` when a portfolio step leaves daily_rank.csv newer than daily_report.md. Defaults to on for modes that include portfolio.",
    )
    parser.add_argument(
        "--quality-value-notification",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Send a concise Telegram quality/value entry-plan summary after the quality-value step.",
    )

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


def run_portfolio_step() -> int:
    return run_default_portfolio_check(
        runtime_metrics_md=PORTFOLIO_RUNTIME_METRICS_MD,
        runtime_metrics_json=PORTFOLIO_RUNTIME_METRICS_JSON,
        print_fn=print,
        stderr=sys.stderr,
    )


def send_quality_value_notification(entry_plan_csv: Path = QUALITY_VALUE_ENTRY_PLAN_CSV) -> None:
    if not entry_plan_csv.exists():
        return
    try:
        entry_plan = pd.read_csv(entry_plan_csv)
    except Exception:
        return
    if entry_plan.empty:
        return
    try:
        import daily_theme_watchlist

        message = quality_value.build_entry_plan_notification(entry_plan)
        daily_theme_watchlist.send_telegram_message(message)
    except Exception:
        return


def _count_csv_rows(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        df = pd.read_csv(path)
    except Exception:
        return 0
    return int(len(df))


def _load_csv_safely(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception:
        return pd.DataFrame()


def _latest_signal_date(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        df = pd.read_csv(path, dtype={"signal_date": "string"})
    except Exception:
        return ""
    if "signal_date" not in df.columns or df.empty:
        return ""
    non_empty = df["signal_date"].dropna().astype(str).str.strip()
    non_empty = non_empty[non_empty != ""]
    if non_empty.empty:
        return ""
    return str(sorted(non_empty.tolist())[-1])


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


def _empty_shadow_open_not_chase_tracking_df() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "signal_date",
            "ticker",
            "name",
            "rank",
            "scenario_label",
            "market_heat",
            "spec_risk_bucket",
            "shadow_status",
            "shadow_eligible",
            "action_label",
            "outcome_status_1d",
            "realized_ret_pct_1d",
            "matured_1d",
            "win_1d",
        ]
    )


def build_shadow_open_not_chase_tracking_df(
    shadow_snapshots_df: pd.DataFrame,
    outcomes_df: pd.DataFrame,
) -> pd.DataFrame:
    if shadow_snapshots_df is None or shadow_snapshots_df.empty:
        return _empty_shadow_open_not_chase_tracking_df()

    required_snapshot_cols = {"signal_date", "ticker"}
    if not required_snapshot_cols.issubset(set(shadow_snapshots_df.columns)):
        return _empty_shadow_open_not_chase_tracking_df()

    shadow = shadow_snapshots_df.copy()
    shadow["signal_date"] = shadow["signal_date"].astype(str).str.strip()
    shadow["ticker"] = shadow["ticker"].astype(str).str.strip()
    if "rank" in shadow.columns:
        shadow["rank"] = pd.to_numeric(shadow["rank"], errors="coerce")
    if "shadow_eligible" in shadow.columns:
        shadow["shadow_eligible"] = shadow["shadow_eligible"].astype(str).str.strip().str.lower().isin(["true", "1", "yes"])
    else:
        shadow["shadow_eligible"] = False
    shadow["shadow_status"] = shadow.get("shadow_status", pd.Series(index=shadow.index, dtype=object)).fillna("").astype(str)
    shadow["action_label"] = shadow.get("action_label", pd.Series(index=shadow.index, dtype=object)).fillna("").astype(str)

    merged = shadow.copy()
    if outcomes_df is not None and not outcomes_df.empty and {"signal_date", "ticker", "horizon_days"}.issubset(set(outcomes_df.columns)):
        outcomes = outcomes_df.copy()
        outcomes["signal_date"] = outcomes["signal_date"].astype(str).str.strip()
        outcomes["ticker"] = outcomes["ticker"].astype(str).str.strip()
        outcomes["horizon_days"] = pd.to_numeric(outcomes["horizon_days"], errors="coerce")
        outcomes = outcomes[
            (outcomes["horizon_days"] == 1)
            & (outcomes.get("watch_type", pd.Series(index=outcomes.index, dtype=object)).fillna("").astype(str) == "short")
        ].copy()
        if not outcomes.empty:
            outcomes["outcome_status_1d"] = outcomes.get("status", pd.Series(index=outcomes.index, dtype=object)).fillna("").astype(str)
            outcomes["realized_ret_pct_1d"] = pd.to_numeric(outcomes.get("realized_ret_pct"), errors="coerce")
            outcomes["matured_1d"] = outcomes["outcome_status_1d"].eq("ok")
            outcomes["win_1d"] = outcomes["realized_ret_pct_1d"] > 0
            outcomes = outcomes.drop_duplicates(subset=["signal_date", "ticker"], keep="last")
            merged = merged.merge(
                outcomes[["signal_date", "ticker", "outcome_status_1d", "realized_ret_pct_1d", "matured_1d", "win_1d"]],
                on=["signal_date", "ticker"],
                how="left",
            )

    if "outcome_status_1d" not in merged.columns:
        merged["outcome_status_1d"] = ""
    merged["outcome_status_1d"] = merged["outcome_status_1d"].fillna("").astype(str)
    if "realized_ret_pct_1d" not in merged.columns:
        merged["realized_ret_pct_1d"] = pd.Series(index=merged.index, dtype=float)
    merged["realized_ret_pct_1d"] = pd.to_numeric(merged["realized_ret_pct_1d"], errors="coerce")
    if "matured_1d" not in merged.columns:
        merged["matured_1d"] = False
    merged["matured_1d"] = merged["matured_1d"].map(lambda value: bool(value) if pd.notna(value) else False)
    if "win_1d" not in merged.columns:
        merged["win_1d"] = False
    merged["win_1d"] = merged["win_1d"].map(lambda value: bool(value) if pd.notna(value) else False)

    keep_cols = _empty_shadow_open_not_chase_tracking_df().columns.tolist()
    for col in keep_cols:
        if col not in merged.columns:
            merged[col] = ""
    sort_cols: list[str] = []
    ascending: list[bool] = []
    if "signal_date" in merged.columns:
        sort_cols.append("signal_date")
        ascending.append(False)
    if "shadow_eligible" in merged.columns:
        sort_cols.append("shadow_eligible")
        ascending.append(False)
    if "rank" in merged.columns:
        sort_cols.append("rank")
        ascending.append(True)
    if sort_cols:
        merged = merged.sort_values(by=sort_cols, ascending=ascending)
    return merged[keep_cols].reset_index(drop=True)


def render_shadow_open_not_chase_tracking_markdown(
    tracking_df: pd.DataFrame,
    *,
    tuning_draft: dict[str, object],
    recent_dates: list[str],
    generated_at: str,
) -> str:
    lines = [
        "# 開高不追 Daily Tracking",
        f"- Generated: {generated_at}",
        "- Scope: `開高不追` / `1D short` / shadow-only daily tracking",
    ]
    if recent_dates:
        lines.append(f"- Recent signal window: `{recent_dates[0]} -> {recent_dates[-1]}` (`{len(recent_dates)}` dates)")
    else:
        lines.append("- Recent signal window: `n/a`")
    lines.append("")

    historical = tuning_draft.get("historical", {}) if isinstance(tuning_draft, dict) else {}
    recent = tuning_draft.get("recent", {}) if isinstance(tuning_draft, dict) else {}
    simulation = tuning_draft.get("simulation", {}) if isinstance(tuning_draft, dict) else {}

    total_rows = int(len(tracking_df)) if tracking_df is not None else 0
    eligible_rows = int(tracking_df.get("shadow_eligible", pd.Series(dtype=bool)).astype(bool).sum()) if tracking_df is not None and not tracking_df.empty else 0
    matured_rows = int(tracking_df.get("matured_1d", pd.Series(dtype=bool)).astype(bool).sum()) if tracking_df is not None and not tracking_df.empty else 0
    matured_eligible = int(
        (
            tracking_df.get("shadow_eligible", pd.Series(dtype=bool)).astype(bool)
            & tracking_df.get("matured_1d", pd.Series(dtype=bool)).astype(bool)
        ).sum()
    ) if tracking_df is not None and not tracking_df.empty else 0

    lines.extend(
        [
            "## Summary",
            "",
            f"- Observed rows: `{total_rows}`",
            f"- Eligible rows: `{eligible_rows}`",
            f"- Matured 1D rows: `{matured_rows}`",
            f"- Matured eligible rows: `{matured_eligible}`",
            f"- Current draft status: `{tuning_draft.get('status', 'hold') if isinstance(tuning_draft, dict) else 'hold'}`",
        ]
    )
    if isinstance(tuning_draft, dict) and tuning_draft.get("why_now"):
        lines.append(f"- Why now: {tuning_draft.get('why_now')}")
    if isinstance(tuning_draft, dict) and tuning_draft.get("proposal"):
        lines.append(f"- Proposal: {tuning_draft.get('proposal')}")
    if historical:
        lines.append(
            f"- Historical gate progress: `below_n={historical.get('below_n', 0)}` / `ok_n={historical.get('ok_n', 0)}` / "
            f"`below-ok={historical.get('delta_avg_ret_below_minus_ok', 0.0)}%` / `promotion_ready={historical.get('promotion_ready', False)}`"
        )
    if recent:
        lines.append(
            f"- Recent gate progress: `below_n={recent.get('below_n', 0)}` / `ok_n={recent.get('ok_n', 0)}` / "
            f"`below-ok={recent.get('delta_avg_ret_below_minus_ok', 0.0)}%` / `promotion_ready={recent.get('promotion_ready', False)}`"
        )
    if simulation:
        lines.append(
            f"- Simulation: `promoted_n={simulation.get('promoted_n', 0)}` / "
            f"`delta_avg_ret={simulation.get('delta_avg_ret_simulated_minus_current', 0.0)}%` / "
            f"`delta_win_rate={simulation.get('delta_win_rate_simulated_minus_current', 0.0)}%`"
        )

    lines.extend(
        [
            "",
            "## Promotion Criteria",
            "",
            "- `below_n >= 3`",
            "- `action_signal_dates >= 2`",
            "- `dominant_positive_share_pct <= 70`",
            "- recent `below-ok > 0`",
            "- edge should not come only from `hot` + non-normal `spec_risk`",
            "",
        ]
    )

    if tracking_df is None or tracking_df.empty:
        lines.extend(["## Daily Rows", "", "- None", ""])
        return "\n".join(lines)

    lines.extend(
        [
            "## Daily Rows",
            "",
            "| Signal Date | Ticker | Name | Rank | Scenario | Heat | Spec | Eligible | Status | 1D Outcome | 1D Ret |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for _, row in tracking_df.iterrows():
        ret_value = row.get("realized_ret_pct_1d")
        ret_text = "" if pd.isna(ret_value) else f"{float(ret_value):.2f}%"
        lines.append(
            f"| {row.get('signal_date', '')} | {row.get('ticker', '')} | {row.get('name', '')} | "
            f"{'' if pd.isna(row.get('rank')) else int(float(row.get('rank')))} | {row.get('scenario_label', '')} | "
            f"{row.get('market_heat', '')} | {row.get('spec_risk_bucket', '')} | "
            f"{bool(row.get('shadow_eligible', False))} | {row.get('shadow_status', '')} | "
            f"{row.get('outcome_status_1d', '') or 'pending'} | {ret_text} |"
        )
    lines.append("")
    return "\n".join(lines)


def write_shadow_open_not_chase_tracking_outputs(
    *,
    theme_outdir: Path = THEME_OUTDIR,
    verification_outdir: Path = VERIFICATION_OUTDIR,
    tracking_md: Path = SHADOW_OPEN_NOT_CHASE_TRACKING_MD,
    tracking_csv: Path = SHADOW_OPEN_NOT_CHASE_TRACKING_CSV,
) -> None:
    shadow_snapshots = _load_csv_safely(verification_outdir / "shadow_open_not_chase_snapshots.csv")
    outcomes = _load_csv_safely(verification_outdir / "reco_outcomes.csv")
    tracking_df = build_shadow_open_not_chase_tracking_df(shadow_snapshots, outcomes)

    if outcomes.empty:
        recent_dates: list[str] = []
        tuning_draft: dict[str, object] = {
            "status": "hold",
            "why_now": "No verification outcomes yet.",
            "proposal": "Wait for mature 1D outcomes before evaluating promotion.",
        }
    else:
        try:
            recent_outcomes, recent_dates = filter_recent_signal_dates(outcomes, max_signal_dates=3)
            recent_parts = summarize_outcomes(recent_outcomes)
            full_parts = summarize_outcomes(outcomes)
            tuning_draft = build_short_gate_tuning_draft(full_parts, recent_parts)
        except Exception:
            recent_dates = []
            tuning_draft = {
                "status": "hold",
                "why_now": "Shadow tracking summary is waiting for richer verification fields.",
                "proposal": "Keep collecting outcomes; do not promote the action yet.",
            }

    tracking_csv.parent.mkdir(parents=True, exist_ok=True)
    tracking_md.parent.mkdir(parents=True, exist_ok=True)
    tracking_df.to_csv(tracking_csv, index=False, encoding="utf-8-sig")
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    tracking_md.write_text(
        render_shadow_open_not_chase_tracking_markdown(
            tracking_df,
            tuning_draft=tuning_draft,
            recent_dates=recent_dates,
            generated_at=generated_at,
        ),
        encoding="utf-8",
    )


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


def _collect_spec_risk_metrics(daily_rank_csv: Path) -> dict[str, object]:
    if not daily_rank_csv.exists():
        return {
            "spec_risk_high_rows": 0,
            "spec_risk_watch_rows": 0,
            "spec_risk_top_tickers": [],
        }
    try:
        df = pd.read_csv(daily_rank_csv)
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


def collect_status_metrics(theme_outdir: Path = THEME_OUTDIR, verification_outdir: Path = VERIFICATION_OUTDIR) -> dict[str, object]:
    snapshots_csv = verification_outdir / "reco_snapshots.csv"
    outcomes_csv = verification_outdir / "reco_outcomes.csv"
    daily_rank_csv = theme_outdir / "daily_rank.csv"
    artifact_freshness = _watchlist_artifact_freshness(theme_outdir)
    watchlist_runtime = _load_runtime_metrics(theme_outdir / "runtime_metrics.json")
    portfolio_runtime = _load_runtime_metrics(theme_outdir / "portfolio_runtime_metrics.json")
    report_sync_runtime = _load_runtime_metrics(theme_outdir / "report_sync_metrics.json")
    quality_value_runtime = _load_runtime_metrics(theme_outdir / "quality_value_metrics.json")
    verification_runtime = _load_runtime_metrics(verification_outdir / "runtime_metrics.json")
    spec_risk_metrics = _collect_spec_risk_metrics(daily_rank_csv)
    snapshots_df = _load_csv_safely(snapshots_csv)
    outcomes_df = _load_csv_safely(outcomes_csv)
    verification_gate = build_data_quality_gate(outcomes_df, snapshots_df)
    verification_gate_metrics = verification_gate.get("metrics", {})
    if not isinstance(verification_gate_metrics, dict):
        verification_gate_metrics = {}

    outcomes_total = 0
    outcomes_ok = 0
    outcomes_pending = 0
    midlong_gate_status = ""
    midlong_gate_horizon = ""
    midlong_gate_detail = ""
    if not outcomes_df.empty:
        outcomes_total = int(len(outcomes_df))
        if "status" in outcomes_df.columns:
            status = outcomes_df["status"].astype(str).str.strip()
            outcomes_ok = int((status == "ok").sum())
            outcomes_pending = int((status == "insufficient_forward_data").sum())
        try:
            parts = summarize_outcomes(outcomes_df)
            gate = parts.get("midlong_threshold_gate", pd.DataFrame())
        except Exception:
            gate = pd.DataFrame()
        if not gate.empty:
            blocked = gate[gate.get("decision", pd.Series(dtype=str)).astype(str) == "block_loosening"].copy()
            selected = blocked.iloc[0] if not blocked.empty else gate.iloc[0]
            midlong_gate_status = str(selected.get("decision", ""))
            horizon = selected.get("horizon_days", "")
            midlong_gate_horizon = "" if pd.isna(horizon) else str(int(horizon))
            midlong_gate_detail = (
                f"normal_below_n={int(selected.get('normal_below_n', 0))}, "
                f"below_hot_share={float(selected.get('below_hot_share_pct', 0.0)):.1f}%, "
                f"heat_gap={float(selected.get('heat_share_gap_pct', 0.0)):.1f}pp"
            )

    return {
        "latest_snapshot_signal_date": _latest_signal_date(snapshots_csv),
        "latest_outcome_signal_date": _latest_signal_date(outcomes_csv),
        "daily_rank_rows": _count_csv_rows(daily_rank_csv),
        "snapshot_rows": _count_csv_rows(snapshots_csv),
        "outcome_rows": outcomes_total,
        "outcome_ok_rows": outcomes_ok,
        "outcome_pending_rows": outcomes_pending,
        "midlong_threshold_gate_status": midlong_gate_status,
        "midlong_threshold_gate_horizon": midlong_gate_horizon,
        "midlong_threshold_gate_detail": midlong_gate_detail,
        "verification_gate_status": str(verification_gate.get("status", "unknown") or "unknown"),
        "watchlist_artifact_freshness_status": artifact_freshness["status"],
        "watchlist_artifact_freshness_detail": artifact_freshness["detail"],
        "snapshot_dup_keys": int(verification_gate_metrics.get("snapshot_dup_keys", 0) or 0),
        "outcome_dup_keys": int(verification_gate_metrics.get("outcome_dup_keys", 0) or 0),
        "signal_date_missing_rows": int(verification_gate_metrics.get("signal_date_missing_rows", 0) or 0),
        "no_price_series_rows": int(verification_gate_metrics.get("no_price_series_rows", 0) or 0),
        "watchlist_runtime_seconds": float(watchlist_runtime.get("wall_seconds", 0.0) or 0.0),
        "watchlist_runtime_status": str(watchlist_runtime.get("status", "") or ""),
        "portfolio_runtime_seconds": float(portfolio_runtime.get("wall_seconds", 0.0) or 0.0),
        "portfolio_runtime_status": str(portfolio_runtime.get("status", "") or ""),
        "report_sync_runtime_seconds": float(report_sync_runtime.get("wall_seconds", 0.0) or 0.0),
        "report_sync_runtime_status": str(report_sync_runtime.get("status", "") or ""),
        "report_sync_generated_at": str(report_sync_runtime.get("generated_at", "") or ""),
        "quality_value_runtime_seconds": float(quality_value_runtime.get("wall_seconds", 0.0) or 0.0),
        "quality_value_runtime_status": str(quality_value_runtime.get("status", "") or ""),
        "quality_value_generated_at": str(quality_value_runtime.get("generated_at", "") or ""),
        "quality_value_low_price_rows": int(quality_value_runtime.get("low_price_rows", 0) or 0),
        "quality_value_research_rows": int(quality_value_runtime.get("quality_value_rows", 0) or 0),
        "quality_value_fundamental_rows": int(quality_value_runtime.get("fundamental_rows", 0) or 0),
        "quality_value_scout_rows": int(quality_value_runtime.get("scout_rows", 0) or 0),
        "verification_runtime_seconds": float(verification_runtime.get("wall_seconds", 0.0) or 0.0),
        "verification_runtime_status": str(verification_runtime.get("status", "") or ""),
        "spec_risk_high_rows": int(spec_risk_metrics["spec_risk_high_rows"]),
        "spec_risk_watch_rows": int(spec_risk_metrics["spec_risk_watch_rows"]),
        "spec_risk_top_tickers": list(spec_risk_metrics["spec_risk_top_tickers"]),
    }


def render_local_status_markdown(
    *,
    generated_at: str,
    mode: str,
    overall_status: str,
    steps: list[dict[str, str]],
    metrics: dict[str, object],
) -> str:
    lines = [
        "# Local Run Status",
        f"- Generated: {generated_at}",
        f"- Mode: `{mode}`",
        f"- Overall: `{overall_status}`",
        "",
        "## Steps",
        "",
        "| Step | Status | Detail |",
        "| --- | --- | --- |",
    ]
    for step in steps:
        lines.append(f"| {step['label']} | {step['status']} | {step['detail']} |")

    lines.extend(
        [
            "",
            "## Metrics",
            "",
            f"- Latest snapshot signal date: `{metrics.get('latest_snapshot_signal_date') or 'n/a'}`",
            f"- Latest outcome signal date: `{metrics.get('latest_outcome_signal_date') or 'n/a'}`",
            f"- Daily rank rows: `{metrics.get('daily_rank_rows', 0)}`",
            f"- Watchlist artifact freshness: `{metrics.get('watchlist_artifact_freshness_status') or 'unknown'}` ({metrics.get('watchlist_artifact_freshness_detail') or 'n/a'})",
            f"- Snapshot rows: `{metrics.get('snapshot_rows', 0)}`",
            f"- Outcome rows: `{metrics.get('outcome_rows', 0)}`",
            f"- Outcome OK rows: `{metrics.get('outcome_ok_rows', 0)}`",
            f"- Outcome pending rows: `{metrics.get('outcome_pending_rows', 0)}`",
            f"- Midlong threshold gate: `{metrics.get('midlong_threshold_gate_status') or 'n/a'}`"
            + (
                f" (`{metrics.get('midlong_threshold_gate_horizon')}D`, {metrics.get('midlong_threshold_gate_detail')})"
                if metrics.get("midlong_threshold_gate_status")
                else ""
            ),
            f"- Verification gate status: `{metrics.get('verification_gate_status') or 'unknown'}`",
            f"- Verification duplicate keys: snapshots=`{metrics.get('snapshot_dup_keys', 0)}`, outcomes=`{metrics.get('outcome_dup_keys', 0)}`",
            f"- Verification missing price rows: signal_date_missing=`{metrics.get('signal_date_missing_rows', 0)}`, no_price_series=`{metrics.get('no_price_series_rows', 0)}`",
            f"- Spec risk high rows: `{metrics.get('spec_risk_high_rows', 0)}`",
            f"- Spec risk watch rows: `{metrics.get('spec_risk_watch_rows', 0)}`",
            f"- Spec risk top tickers: `{', '.join(metrics.get('spec_risk_top_tickers', [])) or 'n/a'}`",
            f"- Watchlist runtime: `{metrics.get('watchlist_runtime_seconds', 0.0):.3f}s` ({metrics.get('watchlist_runtime_status') or 'n/a'})",
            f"- Portfolio runtime: `{metrics.get('portfolio_runtime_seconds', 0.0):.3f}s` ({metrics.get('portfolio_runtime_status') or 'n/a'})",
            f"- Report sync runtime: `{metrics.get('report_sync_runtime_seconds', 0.0):.3f}s` ({metrics.get('report_sync_runtime_status') or 'n/a'})"
            + (f", generated `{metrics.get('report_sync_generated_at')}`" if metrics.get("report_sync_generated_at") else ""),
            f"- Quality value rows: low-price=`{metrics.get('quality_value_low_price_rows', 0)}`, research=`{metrics.get('quality_value_research_rows', 0)}`, fundamentals=`{metrics.get('quality_value_fundamental_rows', 0)}`",
            f"- Quality value similar scout rows: `{metrics.get('quality_value_scout_rows', 0)}`",
            f"- Quality value runtime: `{metrics.get('quality_value_runtime_seconds', 0.0):.3f}s` ({metrics.get('quality_value_runtime_status') or 'n/a'})"
            + (f", generated `{metrics.get('quality_value_generated_at')}`" if metrics.get("quality_value_generated_at") else ""),
            f"- Verification runtime: `{metrics.get('verification_runtime_seconds', 0.0):.3f}s` ({metrics.get('verification_runtime_status') or 'n/a'})",
            "",
            "## Key Outputs",
            "",
            f"- Watchlist report: `{theme_outdir_str('daily_report.md')}`",
            f"- Watchlist runtime: `{theme_outdir_str('runtime_metrics.md')}`",
            f"- Portfolio report: `{theme_outdir_str('portfolio_report.md')}`",
            f"- Portfolio runtime: `{theme_outdir_str('portfolio_runtime_metrics.md')}`",
            f"- Report sync runtime: `{theme_outdir_str('report_sync_metrics.md')}`",
            f"- Quality value report: `{theme_outdir_str('quality_value_report.md')}`",
            f"- Quality value CSV: `{theme_outdir_str('quality_value_candidates.csv')}`",
            f"- Quality value fundamentals: `{theme_outdir_str('quality_value_fundamentals.csv')}`",
            f"- Quality value entry plan: `{theme_outdir_str('quality_value_entry_plan.csv')}`",
            f"- Quality value similar scout: `{theme_outdir_str('quality_value_similar_scout.csv')}`",
            f"- Verification report: `{verification_outdir_str('verification_report.md')}`",
            f"- Verification runtime: `{verification_outdir_str('runtime_metrics.md')}`",
            f"- Outcomes summary: `{verification_outdir_str('outcomes_summary.md')}`",
            f"- Feedback sensitivity: `{verification_outdir_str('feedback_weight_sensitivity.md')}`",
            f"- Shadow tracking: `{theme_outdir_str('shadow_open_not_chase_tracking.md')}`",
        ]
    )
    return "\n".join(lines)


def theme_outdir_str(name: str) -> str:
    return str(THEME_OUTDIR / name)


def verification_outdir_str(name: str) -> str:
    return str(VERIFICATION_OUTDIR / name)


def write_local_status_dashboard(
    *,
    args: argparse.Namespace,
    steps: list[dict[str, str]],
    overall_status: str,
    theme_outdir: Path = THEME_OUTDIR,
    verification_outdir: Path = VERIFICATION_OUTDIR,
    status_md: Path = LOCAL_STATUS_MD,
    status_json: Path = LOCAL_STATUS_JSON,
) -> None:
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    write_shadow_open_not_chase_tracking_outputs(
        theme_outdir=theme_outdir,
        verification_outdir=verification_outdir,
        tracking_md=theme_outdir / "shadow_open_not_chase_tracking.md",
        tracking_csv=theme_outdir / "shadow_open_not_chase_tracking.csv",
    )
    metrics = collect_status_metrics(theme_outdir, verification_outdir)
    payload = {
        "generated_at": generated_at,
        "mode": args.mode,
        "overall_status": overall_status,
        "steps": steps,
        "metrics": metrics,
        "outputs": {
            "watchlist_report": str(theme_outdir / "daily_report.md"),
            "watchlist_runtime": str(theme_outdir / "runtime_metrics.md"),
            "portfolio_report": str(theme_outdir / "portfolio_report.md"),
            "portfolio_runtime": str(theme_outdir / "portfolio_runtime_metrics.md"),
            "report_sync_runtime": str(theme_outdir / "report_sync_metrics.md"),
            "quality_value_report": str(theme_outdir / "quality_value_report.md"),
            "quality_value_candidates": str(theme_outdir / "quality_value_candidates.csv"),
            "quality_value_fundamentals": str(theme_outdir / "quality_value_fundamentals.csv"),
            "quality_value_entry_plan": str(theme_outdir / "quality_value_entry_plan.csv"),
            "quality_value_similar_scout": str(theme_outdir / "quality_value_similar_scout.csv"),
            "verification_report": str(verification_outdir / "verification_report.md"),
            "verification_runtime": str(verification_outdir / "runtime_metrics.md"),
            "outcomes_summary": str(verification_outdir / "outcomes_summary.md"),
            "feedback_sensitivity": str(verification_outdir / "feedback_weight_sensitivity.md"),
            "shadow_tracking": str(theme_outdir / "shadow_open_not_chase_tracking.md"),
            "shadow_tracking_csv": str(theme_outdir / "shadow_open_not_chase_tracking.csv"),
        },
    }
    status_md.parent.mkdir(parents=True, exist_ok=True)
    status_json.parent.mkdir(parents=True, exist_ok=True)
    status_md.write_text(
        render_local_status_markdown(
            generated_at=generated_at,
            mode=args.mode,
            overall_status=overall_status,
            steps=steps,
            metrics=metrics,
        ),
        encoding="utf-8",
    )
    status_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    steps: list[dict[str, str]] = []
    overall_status = "ok"
    force_watchlist = args.force_watchlist or args.mode == "postclose"
    watchlist_success_scope = args.mode if args.mode in {"preopen", "postclose", "full"} else None
    sync_watchlist_report = args.sync_watchlist_report if args.sync_watchlist_report is not None else args.mode in {"portfolio", "postclose", "full"}

    step_runners = {
        "watchlist": lambda: run_daily_watchlist(force_run=force_watchlist, success_scope=watchlist_success_scope),
        "portfolio": run_portfolio_step,
        "verification": lambda: run_daily_verification.main(build_verification_argv(args)),
    }
    execution_order = ("watchlist", "portfolio", "verification")

    for index, step_name in enumerate(execution_order):
        if not should_run_step(args, step_name):
            steps.append({"name": step_name, "label": STEP_LABELS[step_name], "status": "skipped", "detail": "Not selected for this mode"})
            continue

        code = step_runners[step_name]()
        if code:
            overall_status = "failed"
            steps.append({"name": step_name, "label": STEP_LABELS[step_name], "status": "failed", "detail": f"Exit code {code}"})
            for blocked_step in execution_order[index + 1 :]:
                if should_run_step(args, blocked_step):
                    steps.append({"name": blocked_step, "label": STEP_LABELS[blocked_step], "status": "blocked", "detail": f"Blocked by {step_name} failure"})
                else:
                    steps.append({"name": blocked_step, "label": STEP_LABELS[blocked_step], "status": "skipped", "detail": "Not selected for this mode"})
            write_local_status_dashboard(args=args, steps=steps, overall_status=overall_status)
            return code

        steps.append({"name": step_name, "label": STEP_LABELS[step_name], "status": "completed", "detail": "OK"})

    if sync_watchlist_report and should_run_step(args, "portfolio"):
        artifact_freshness = _watchlist_artifact_freshness(THEME_OUTDIR)
        if artifact_freshness["status"] == "stale_report":
            sync_code = report_sync.main([])
            if sync_code:
                overall_status = "failed"
                steps.append({"name": "report_sync", "label": STEP_LABELS["report_sync"], "status": "failed", "detail": f"Exit code {sync_code}"})
                write_local_status_dashboard(args=args, steps=steps, overall_status=overall_status)
                return sync_code
            steps.append({"name": "report_sync", "label": STEP_LABELS["report_sync"], "status": "completed", "detail": "Synced watchlist report"})
        else:
            steps.append({"name": "report_sync", "label": STEP_LABELS["report_sync"], "status": "skipped", "detail": "Watchlist report already synced"})

    if should_run_step(args, "watchlist") or should_run_step(args, "portfolio"):
        quality_code = quality_value.main([])
        if quality_code:
            overall_status = "failed"
            steps.append({"name": "quality_value", "label": STEP_LABELS["quality_value"], "status": "failed", "detail": f"Exit code {quality_code}"})
            write_local_status_dashboard(args=args, steps=steps, overall_status=overall_status)
            return quality_code
        if args.quality_value_notification:
            send_quality_value_notification()
        steps.append({"name": "quality_value", "label": STEP_LABELS["quality_value"], "status": "completed", "detail": "Updated research report"})

    write_local_status_dashboard(args=args, steps=steps, overall_status=overall_status)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
