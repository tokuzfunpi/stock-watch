from __future__ import annotations

import argparse
import json
import os
import re
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
DAILY_RANK_CSV = THEME_OUTDIR / "daily_rank.csv"
QUALITY_VALUE_ENTRY_PLAN_CSV = THEME_OUTDIR / "quality_value_entry_plan.csv"
QUALITY_VALUE_SIMILAR_SCOUT_CSV = THEME_OUTDIR / "quality_value_similar_scout.csv"
QUALITY_VALUE_WATCHLIST_DRAFT_CSV = THEME_OUTDIR / "quality_value_watchlist_draft.csv"
QUALITY_VALUE_TRACKING_CSV = THEME_OUTDIR / "quality_value_tracking.csv"
QUALITY_VALUE_PRUNING_MD = THEME_OUTDIR / "quality_value_pruning_report.md"
QUALITY_VALUE_CANDIDATE_REVIEW_CSV = THEME_OUTDIR / "quality_value_candidate_review.csv"
QUALITY_VALUE_CANDIDATE_REVIEW_MD = THEME_OUTDIR / "quality_value_candidate_review.md"
QUALITY_VALUE_NEW_ADDITIONS_TRACKING_CSV = THEME_OUTDIR / "quality_value_new_additions_tracking.csv"
QUALITY_VALUE_NEW_ADDITIONS_TRACKING_MD = THEME_OUTDIR / "quality_value_new_additions_tracking.md"
QUALITY_VALUE_NEW_ADDITION_TICKERS = ("3213.TWO", "3158.TWO", "6996.TWO", "3556.TWO", "6292.TWO")
QUALITY_VALUE_TRIAL_LEDGER_CSV = THEME_OUTDIR / "quality_value_trial_ledger.csv"
QUALITY_VALUE_TRIAL_LEDGER_MD = THEME_OUTDIR / "quality_value_trial_ledger.md"
QUALITY_VALUE_TRIAL_TICKERS = ("3213.TWO",)
DEFAULT_LOCAL_TELEGRAM_CHAT_IDS = "7758949915"

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
    parser.add_argument(
        "--local-telegram-chat-ids",
        default=default_local_telegram_chat_ids(),
        help=(
            "Restrict Telegram recipients for this local workflow. Defaults to "
            "STOCK_WATCH_LOCAL_TELEGRAM_CHAT_IDS, then TELEGRAM_CHAT_IDS, then 7758949915; "
            "use commas/newlines for multiple ids or an empty string to disable local sends."
        ),
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


def default_local_telegram_chat_ids() -> str:
    local_value = os.getenv("STOCK_WATCH_LOCAL_TELEGRAM_CHAT_IDS")
    if local_value is not None:
        return local_value.strip()
    telegram_value = os.getenv("TELEGRAM_CHAT_IDS")
    if telegram_value is not None and telegram_value.strip():
        return telegram_value.strip()
    return DEFAULT_LOCAL_TELEGRAM_CHAT_IDS


def parse_local_telegram_chat_ids(raw: str | None) -> list[int]:
    tokens = re.split(r"[\s,]+", str(raw or "").strip())
    chat_ids: list[int] = []
    seen: set[int] = set()
    for token in tokens:
        if not token:
            continue
        chat_id = int(token)
        if chat_id in seen:
            continue
        seen.add(chat_id)
        chat_ids.append(chat_id)
    return chat_ids


def configure_local_telegram_chat_ids(raw: str | None, daily_module: object | None = None) -> list[int]:
    chat_ids = parse_local_telegram_chat_ids(raw)
    if daily_module is None:
        import daily_theme_watchlist as daily_module
    setattr(daily_module, "TELEGRAM_CHAT_IDS", chat_ids)
    return chat_ids


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


def send_quality_value_notification(
    entry_plan_csv: Path = QUALITY_VALUE_ENTRY_PLAN_CSV,
    portfolio_report_md: Path = THEME_OUTDIR / "portfolio_report.md",
    new_additions_tracking_csv: Path = QUALITY_VALUE_NEW_ADDITIONS_TRACKING_CSV,
    trial_ledger_csv: Path = QUALITY_VALUE_TRIAL_LEDGER_CSV,
) -> None:
    if not entry_plan_csv.exists() and not portfolio_report_md.exists() and not new_additions_tracking_csv.exists() and not trial_ledger_csv.exists():
        return
    metrics = {
        **_collect_quality_value_action_summary(entry_plan_csv),
        **_collect_new_additions_action_summary(new_additions_tracking_csv),
        **_collect_trial_ledger_action_summary(trial_ledger_csv),
    }
    if not any(metrics.values()):
        return
    try:
        import daily_theme_watchlist

        full_chat_ids = list(getattr(daily_theme_watchlist, "TELEGRAM_CHAT_IDS", []) or [])
        simple_chat_ids = list(getattr(daily_theme_watchlist, "TELEGRAM_SIMPLE_CHAT_IDS", []) or [])
        if full_chat_ids:
            message = build_action_summary_notification(metrics)
            daily_theme_watchlist.send_telegram_message(message, chat_ids=full_chat_ids)
        if simple_chat_ids:
            message = build_simple_action_summary_notification(metrics)
            daily_theme_watchlist.send_telegram_message(message, chat_ids=simple_chat_ids)
    except Exception:
        return


def build_simple_action_summary_notification(metrics: dict[str, object]) -> str:
    def _section(label: str, key: str, *, price_label: str = "買") -> list[str]:
        values = metrics.get(key, [])
        if not isinstance(values, list):
            values = []
        visible_values = [_format_action_summary_item(str(value), price_label=price_label) for value in values[:3] if str(value).strip()]
        if not visible_values:
            return []
        return [label, *[f"• {value}" for value in visible_values]]

    sections = [
        _section("🟢 今天可小買：(小買試水溫，不重壓)", "action_trial_tickers", price_label="買"),
        _section("🟡 等便宜再買：(等回到買的位置，不追高)", "action_pullback_tickers", price_label="等買"),
        _section("⚪ 量縮先等：(已從可行動名單排除)", "action_low_liquidity_tickers", price_label="等量再說"),
    ]
    visible_sections: list[str] = []
    for section in sections:
        if not section:
            continue
        if visible_sections:
            visible_sections.append("")
        visible_sections.extend(section)
    if not visible_sections:
        visible_sections = ["今天沒有新的可小買 / 等便宜買動作。"]
    return "\n".join(["📌 今日可行動名單", "", *visible_sections])


def build_action_summary_notification(metrics: dict[str, object]) -> str:
    def _section(label: str, key: str, *, price_label: str = "買") -> list[str]:
        values = metrics.get(key, [])
        if not isinstance(values, list):
            values = []
        visible_values = [_format_action_summary_item(str(value), price_label=price_label) for value in values[:5] if str(value).strip()]
        if not visible_values:
            return []
        return [label, *[f"• {value}" for value in visible_values]]

    sections = [
        _section("🟢 今天可小買：(小買試水溫，不重壓)", "action_trial_tickers", price_label="買"),
        _section("🟡 等便宜再買：(等回到買的位置，不追高)", "action_pullback_tickers", price_label="等買"),
        _section("⚪ 量縮先等：(交易量太低，先不動)", "action_low_liquidity_tickers", price_label="等量再說"),
        _section("🔵 等變強再買：(訊號還沒完整，等量價確認)", "action_wait_strength_tickers", price_label="等強再買"),
        _section("🔴 太熱別追：(漲幅或風險偏高，先等降溫)", "action_cooldown_tickers", price_label="別追，等"),
        _section("🆕 新加入觀察：(剛進名單，先看能不能買)", "new_addition_action_tickers"),
        _section("🧪 買後檢查：(已列試單，檢查變強或逃)", "trial_ledger_action_tickers"),
    ]
    visible_sections: list[str] = []
    for section in sections:
        if not section:
            continue
        if visible_sections:
            visible_sections.append("")
        visible_sections.extend(section)
    if not visible_sections:
        visible_sections = ["今天沒有新的可小買 / 等便宜買動作。"]
    return "\n".join(["📌 今日動作摘要", "", *visible_sections])


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


def _format_ticker_names(df: pd.DataFrame, *, price_label: str = "買", limit: int = 5) -> list[str]:
    if df.empty:
        return []
    names: list[str] = []
    for _, row in df.head(limit).iterrows():
        ticker = str(row.get("ticker", "") or "").strip()
        name = str(row.get("name", "") or "").strip()
        if not ticker and not name:
            continue
        names.append(_format_stock_price_display(row, ticker=ticker, name=name, price_label=price_label))
    return names


def _format_stock_display(ticker: str, name: str) -> str:
    ticker = str(ticker or "").strip()
    name = str(name or "").strip()
    if ticker and name:
        return f"{name} ({ticker})"
    return name or ticker


def _format_price(value: object) -> str:
    try:
        price = float(value)
    except Exception:
        return ""
    if pd.isna(price) or price <= 0:
        return ""
    if price >= 1000:
        return f"{price:.0f}"
    if price == int(price):
        return str(int(price))
    return f"{price:.2f}".rstrip("0").rstrip(".")


def _price_range_text(row: pd.Series, *, low_key: str = "buy_zone_low", high_key: str = "buy_zone_high") -> str:
    low = _format_price(row.get(low_key))
    high = _format_price(row.get(high_key))
    if low and high:
        return f"{low}–{high}"
    return high or low


def _format_stock_price_display(row: pd.Series, *, ticker: str, name: str, price_label: str = "買") -> str:
    parts = [_format_stock_display(ticker, name)]
    current = _format_price(row.get("close"))
    zone = _price_range_text(row)
    stop = _format_price(row.get("stop_loss"))
    if current:
        parts.append(f"現價 {current}")
    if zone:
        parts.append(f"{price_label} {zone}")
    if stop:
        parts.append(f"逃 {stop}")
    return "｜".join(parts)


def _plain_action_summary_terms(text: str) -> str:
    replacements = [
        ("過熱先等", "太熱別追"),
        ("分批落袋", "分批賣"),
        ("可試單", "可小買"),
        ("等拉回", "等便宜買"),
        ("等轉強", "等變強再買"),
        ("等待降溫", "太熱別追"),
        ("移除試單", "逃"),
        ("移除審核", "先拿掉"),
        ("active_trial", "試買中"),
        ("risk_watch", "風險偏高，買更小"),
        ("holding_trial", "持續觀察"),
        ("profit_watch", "快到收成區"),
        ("add_watch", "第二筆可小買"),
        ("risk_pause", "先暫停"),
        ("invalidated", "逃"),
        ("paused", "先暫停"),
        ("waiting", "等條件"),
        ("watch_wait", "等條件"),
        ("第一筆 1/3 可研究", "第一筆可小買"),
        ("第二筆 1/3", "第二筆可小買"),
        ("停損", "逃"),
        ("賣出≥", "賣≥"),
        ("逃跑", "逃"),
        ("失效", "逃"),
    ]
    plain = text
    plain = re.sub(r"(?:可)?買區", "買", plain)
    for old, new in replacements:
        plain = plain.replace(old, new)
    plain = plain.replace("/", "、")
    return plain


def _format_action_summary_item(value: str, *, price_label: str = "買") -> str:
    text = str(value or "").strip()
    match = re.match(r"^([0-9A-Z]{2,8}\.(?:TW|TWO))\s+([^｜\s]+)(.*)$", text)
    if not match:
        return _apply_action_price_label(_plain_action_summary_terms(text), price_label)
    ticker, name, rest = match.groups()
    return _apply_action_price_label(_plain_action_summary_terms(f"{_format_stock_display(ticker, name)}{rest}".strip()), price_label)


def _apply_action_price_label(text: str, price_label: str) -> str:
    if price_label == "買":
        return text
    return re.sub(r"(?<=｜)買 (?=[0-9])", f"{price_label} ", text)


def _collect_quality_value_action_summary(entry_plan_csv: Path) -> dict[str, list[str]]:
    volume_ratio_threshold = 0.9
    turnover_threshold_m = 30.0
    if not entry_plan_csv.exists():
        return {
            "action_trial_tickers": [],
            "action_pullback_tickers": [],
            "action_wait_strength_tickers": [],
            "action_cooldown_tickers": [],
            "action_low_liquidity_tickers": [],
        }
    entry_plan = _load_csv_safely(entry_plan_csv)
    if entry_plan.empty or "entry_bias" not in entry_plan.columns:
        return {
            "action_trial_tickers": [],
            "action_pullback_tickers": [],
            "action_wait_strength_tickers": [],
            "action_cooldown_tickers": [],
            "action_low_liquidity_tickers": [],
        }
    work = entry_plan.copy()
    if "decision_priority" in work.columns:
        work["_decision_priority"] = pd.to_numeric(work["decision_priority"], errors="coerce").fillna(0)
        work = work.sort_values(by=["_decision_priority"], ascending=[False])
    bias = work["entry_bias"].fillna("").astype(str).str.strip()

    candidates_csv = entry_plan_csv.parent / "quality_value_candidates.csv"
    volume_ratio_by_ticker: dict[str, float] = {}
    if candidates_csv.exists():
        candidates = _load_csv_safely(candidates_csv)
        if not candidates.empty and "ticker" in candidates.columns and "volume_ratio20" in candidates.columns:
            work_candidates = candidates.copy()
            work_candidates["ticker"] = work_candidates["ticker"].astype(str).str.strip()
            work_candidates["_volume_ratio20"] = pd.to_numeric(work_candidates["volume_ratio20"], errors="coerce")
            work_candidates = work_candidates.dropna(subset=["_volume_ratio20"])
            if not work_candidates.empty:
                volume_ratio_by_ticker = work_candidates.set_index("ticker")["_volume_ratio20"].astype(float).to_dict()

    daily_rank_csv = entry_plan_csv.parent / "daily_rank.csv"
    turnover_by_ticker_m: dict[str, float] = {}
    if daily_rank_csv.exists():
        daily_rank = _load_csv_safely(daily_rank_csv)
        if not daily_rank.empty and "ticker" in daily_rank.columns and "avg_vol20" in daily_rank.columns and "close" in daily_rank.columns:
            work_rank = daily_rank.copy()
            work_rank["ticker"] = work_rank["ticker"].astype(str).str.strip()
            work_rank["_avg_vol20"] = pd.to_numeric(work_rank["avg_vol20"], errors="coerce")
            work_rank["_close"] = pd.to_numeric(work_rank["close"], errors="coerce")
            work_rank["_avg_turnover20_m"] = (work_rank["_avg_vol20"] * work_rank["_close"]) / 1e6
            work_rank = work_rank.dropna(subset=["_avg_turnover20_m"])
            if not work_rank.empty:
                turnover_by_ticker_m = work_rank.set_index("ticker")["_avg_turnover20_m"].astype(float).to_dict()

    def _is_low_liquidity(df: pd.DataFrame) -> pd.Series:
        if df.empty or not volume_ratio_by_ticker:
            return pd.Series([False] * len(df), index=df.index)
        tickers = df.get("ticker", pd.Series([""] * len(df), index=df.index)).astype(str).str.strip()
        ratios = tickers.map(volume_ratio_by_ticker)
        ratios = pd.to_numeric(ratios, errors="coerce")
        return ratios.notna() & (ratios < volume_ratio_threshold)

    def _is_low_turnover(df: pd.DataFrame) -> pd.Series:
        if df.empty or not turnover_by_ticker_m:
            return pd.Series([False] * len(df), index=df.index)
        tickers = df.get("ticker", pd.Series([""] * len(df), index=df.index)).astype(str).str.strip()
        turnovers = tickers.map(turnover_by_ticker_m)
        turnovers = pd.to_numeric(turnovers, errors="coerce")
        return turnovers.notna() & (turnovers < turnover_threshold_m)

    def _low_liquidity_note(row: pd.Series) -> str:
        ticker = str(row.get("ticker", "") or "").strip()
        notes: list[str] = []
        turnover = turnover_by_ticker_m.get(ticker)
        if turnover is not None and turnover < turnover_threshold_m:
            notes.append(f"流動性低 to20={turnover:.1f}M".rstrip("0").rstrip("."))
        ratio = volume_ratio_by_ticker.get(ticker)
        if ratio is not None and ratio < volume_ratio_threshold:
            notes.append(f"量縮 vr20={ratio:.2f}".rstrip("0").rstrip("."))
        return "、".join(notes) or "流動性偏低"

    def _format_low_liquidity_items(df: pd.DataFrame, *, price_label: str) -> list[str]:
        if df.empty:
            return []
        items: list[str] = []
        for _, row in df.head(5).iterrows():
            ticker = str(row.get("ticker", "") or "").strip()
            name = str(row.get("name", "") or "").strip()
            if not ticker and not name:
                continue
            base = _format_stock_price_display(row, ticker=ticker, name=name, price_label=price_label)
            items.append(f"{base}｜{_low_liquidity_note(row)}")
        return items

    trial_df = work[bias.isin(["分批試單", "研究試單"])].copy()
    pullback_df = work[bias == "等拉回"].copy()
    wait_strength_df = work[bias == "等轉強"].copy()
    cooldown_df = work[bias == "等待降溫"].copy()

    low_liquidity_frames: list[pd.DataFrame] = []
    for label_df in [trial_df, pullback_df, wait_strength_df, cooldown_df]:
        mask = _is_low_liquidity(label_df) | _is_low_turnover(label_df)
        if mask.any():
            low_liquidity_frames.append(label_df[mask].copy())
            label_df.drop(index=label_df[mask].index, inplace=True)

    low_liquidity_items: list[str] = []
    if low_liquidity_frames:
        combined = pd.concat(low_liquidity_frames, ignore_index=True)
        if "decision_priority" in combined.columns:
            combined["_decision_priority"] = pd.to_numeric(combined["decision_priority"], errors="coerce").fillna(0)
            combined = combined.sort_values(by=["_decision_priority"], ascending=[False])
        low_liquidity_items = _format_low_liquidity_items(combined, price_label="等量再說")

    return {
        "action_trial_tickers": _format_ticker_names(trial_df, price_label="買"),
        "action_pullback_tickers": _format_ticker_names(pullback_df, price_label="等買"),
        "action_wait_strength_tickers": _format_ticker_names(wait_strength_df, price_label="等強再買"),
        "action_cooldown_tickers": _format_ticker_names(cooldown_df, price_label="別追，等"),
        "action_low_liquidity_tickers": low_liquidity_items,
    }


def _collect_portfolio_action_summary(portfolio_report_md: Path) -> dict[str, list[str]]:
    if not portfolio_report_md.exists():
        return {"portfolio_trim_tickers": []}
    try:
        lines = portfolio_report_md.read_text(encoding="utf-8").splitlines()
    except Exception:
        return {"portfolio_trim_tickers": []}
    trim_tickers: list[str] = []
    for line in lines:
        if not line.startswith("- ") or "建議 分批落袋" not in line:
            continue
        parts = [part.strip() for part in line.removeprefix("- ").split("|")]
        left = parts[0] if parts else ""
        if left:
            current = next((part for part in parts if part.startswith("現價 ")), "")
            sell = ""
            price_band = next((part for part in parts if part.startswith("價格帶 ")), "")
            sell_match = re.search(r"賣出≥([0-9.]+)", price_band)
            if not sell_match:
                sell_match = re.search(r"賣≥([0-9.]+)", price_band)
            if sell_match:
                sell = f"賣≥{_format_price(sell_match.group(1))}"
            price_parts = [left]
            if current:
                price_parts.append(current)
            if sell:
                price_parts.append(sell)
            trim_tickers.append("｜".join(price_parts))
    return {"portfolio_trim_tickers": trim_tickers[:5]}


def _collect_new_additions_action_summary(new_additions_tracking_csv: Path) -> dict[str, list[str]]:
    if not new_additions_tracking_csv.exists():
        return {"new_addition_action_tickers": []}
    tracking = _load_csv_safely(new_additions_tracking_csv)
    if tracking.empty or "next_action" not in tracking.columns:
        return {"new_addition_action_tickers": []}
    work = tracking.copy()
    if "rank" in work.columns:
        work["_rank"] = pd.to_numeric(work["rank"], errors="coerce").fillna(9999)
        work = work.sort_values(by=["_rank"], ascending=[True])
    items: list[str] = []
    for _, row in work.head(5).iterrows():
        ticker = str(row.get("ticker", "") or "").strip()
        name = str(row.get("name", "") or "").strip()
        action = str(row.get("next_action", "") or "").strip()
        if ticker:
            plain_action = _plain_action_summary_terms(action)
            price_label = "買"
            if "等便宜買" in plain_action:
                price_label = "等買"
            elif "等變強再買" in plain_action:
                price_label = "等強再買"
            elif "太熱別追" in plain_action:
                price_label = "別追，等"
            parts = [_format_stock_price_display(row, ticker=ticker, name=name, price_label=price_label)]
            if action:
                parts.append(plain_action)
            items.append("｜".join(parts))
    return {"new_addition_action_tickers": items}


def _collect_trial_ledger_action_summary(trial_ledger_csv: Path) -> dict[str, list[str]]:
    if not trial_ledger_csv.exists():
        return {"trial_ledger_action_tickers": []}
    ledger = _load_csv_safely(trial_ledger_csv)
    if ledger.empty or "next_action" not in ledger.columns:
        return {"trial_ledger_action_tickers": []}
    items: list[str] = []
    for _, row in ledger.head(5).iterrows():
        ticker = str(row.get("ticker", "") or "").strip()
        name = str(row.get("name", "") or "").strip()
        status = str(row.get("trial_status", "") or "").strip()
        decision_state = str(row.get("decision_state", "") or "").strip()
        action = str(row.get("next_action", "") or "").strip()
        if ticker:
            status_label = f"{status}/{decision_state}" if decision_state else status
            parts = [_format_stock_price_display(
                row.rename({"entry_zone_low": "buy_zone_low", "entry_zone_high": "buy_zone_high"}),
                ticker=ticker,
                name=name,
                price_label="買",
            )]
            details = " ".join(part for part in [status_label, action] if part)
            if details:
                parts.append(details)
            items.append("｜".join(parts))
    return {"trial_ledger_action_tickers": items}


def _quality_value_current_date(daily_rank: pd.DataFrame) -> str:
    if daily_rank.empty or "date" not in daily_rank.columns:
        return datetime.now().strftime("%Y-%m-%d")
    dates = daily_rank["date"].dropna().astype(str).str.strip()
    dates = dates[dates != ""]
    if dates.empty:
        return datetime.now().strftime("%Y-%m-%d")
    return str(sorted(dates.tolist())[-1])


def _days_watched(first_seen_date: object, current_date: str) -> int:
    try:
        first = pd.to_datetime(str(first_seen_date)).date()
        current = pd.to_datetime(str(current_date)).date()
    except Exception:
        return 1
    return max(int((current - first).days) + 1, 1)


def _quality_value_lifecycle_action(row: pd.Series) -> tuple[str, str]:
    entry_bias = str(row.get("entry_bias", "") or "").strip()
    spec_risk_label = str(row.get("spec_risk_label", "") or "").strip()
    risk_score = float(pd.to_numeric(pd.Series([row.get("risk_score")]), errors="coerce").fillna(0).iloc[0])
    setup_score = float(pd.to_numeric(pd.Series([row.get("setup_score")]), errors="coerce").fillna(0).iloc[0])
    ret5_pct = float(pd.to_numeric(pd.Series([row.get("ret5_pct")]), errors="coerce").fillna(0).iloc[0])
    days_watched = int(pd.to_numeric(pd.Series([row.get("days_watched")]), errors="coerce").fillna(1).iloc[0])

    if entry_bias == "等待降溫" or spec_risk_label == "疑似炒作風險高" or risk_score >= 6:
        return "cooldown", "風險分數或投機標籤過高，先降溫觀察"
    if entry_bias in {"分批試單", "研究試單"}:
        return "promote", "技術與品質條件同時達標，可進入試單研究"
    if days_watched >= 5 and (entry_bias == "暫不急" or (setup_score <= 5 and ret5_pct <= 0)):
        return "drop_review", "追蹤滿 5 天但動能不足，列入移除審核"
    return "hold", "條件尚未完整，維持觀察"


def _render_quality_value_pruning_report(tracking: pd.DataFrame, *, generated_at: str) -> str:
    lines = [
        "# Quality Value Pruning Report",
        f"- Generated: {generated_at}",
        "- Scope: quality-value lifecycle actions for promote / cooldown / drop-review decisions",
        "",
    ]
    sections = [
        ("Drop Review", "drop_review"),
        ("Cooldown", "cooldown"),
        ("Promote/Trial", "promote"),
    ]
    for title, action in sections:
        rows = tracking[tracking["lifecycle_action"] == action].copy() if not tracking.empty else pd.DataFrame()
        lines.extend([f"## {title}", ""])
        if rows.empty:
            lines.extend(["- None", ""])
            continue
        rows = rows.sort_values(by=["decision_priority", "rank"], ascending=[False, True]).head(15)
        lines.extend(
            [
                "| Ticker | Name | Days | Entry Bias | Rank | Setup | Risk | Reason |",
                "| --- | --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
        for _, row in rows.iterrows():
            lines.append(
                f"| {row.get('ticker', '')} | {row.get('name', '')} | {row.get('days_watched', '')} | "
                f"{row.get('entry_bias', '')} | {row.get('rank', '')} | {row.get('setup_score', '')} | "
                f"{row.get('risk_score', '')} | {row.get('lifecycle_reason', '')} |"
            )
        lines.append("")
    return "\n".join(lines)


def write_quality_value_candidate_review(
    *,
    draft_csv: Path = QUALITY_VALUE_WATCHLIST_DRAFT_CSV,
    review_csv: Path = QUALITY_VALUE_CANDIDATE_REVIEW_CSV,
    review_md: Path = QUALITY_VALUE_CANDIDATE_REVIEW_MD,
) -> pd.DataFrame:
    draft = _load_csv_safely(draft_csv)
    columns = ["ticker", "name", "radar_priority", "similar_score", "review_action", "review_reason", "watchlist_row"]
    if draft.empty:
        review = pd.DataFrame(columns=columns)
    else:
        review = draft.copy()
        priority = review.get("radar_priority", pd.Series(index=review.index, dtype=object)).fillna("").astype(str)
        review["review_action"] = "wait"
        review.loc[priority == "A加入觀察", "review_action"] = "needs_decision_add_watchlist"
        review.loc[priority == "B研究追蹤", "review_action"] = "hold_for_technical_confirmation"
        review["review_reason"] = "等待更多相似標的或技術確認"
        review.loc[priority == "A加入觀察", "review_reason"] = "A 級品質價值候選；待你決策是否加入 watchlist"
        review.loc[priority == "B研究追蹤", "review_reason"] = "B 級研究追蹤；先等技術面或基本面再確認"
        review["watchlist_row"] = review.apply(
            lambda row: f"{row.get('ticker', '')},{row.get('name', '')},satellite,quality_value,TRUE",
            axis=1,
        )
        review["_review_sort"] = review["review_action"].map(
            {"needs_decision_add_watchlist": 0, "hold_for_technical_confirmation": 1, "wait": 2}
        ).fillna(3)
        for col in columns:
            if col not in review.columns:
                review[col] = ""
        review = review.sort_values(by=["_review_sort", "similar_score"], ascending=[True, False])[columns].reset_index(drop=True)

    review_csv.parent.mkdir(parents=True, exist_ok=True)
    review_md.parent.mkdir(parents=True, exist_ok=True)
    review.to_csv(review_csv, index=False, encoding="utf-8-sig")

    lines = [
        "# Quality Value Candidate Review",
        f"- Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "- Decision point: choose which A-grade candidates to add to the quality-value watchlist.",
        "",
    ]
    for title, action in [
        ("Needs Decision", "needs_decision_add_watchlist"),
        ("Hold For Confirmation", "hold_for_technical_confirmation"),
    ]:
        rows = review[review["review_action"] == action].copy() if not review.empty else pd.DataFrame()
        lines.extend([f"## {title}", ""])
        if rows.empty:
            lines.extend(["- None", ""])
            continue
        lines.extend(["| Ticker | Name | Priority | Score | Reason |", "| --- | --- | --- | --- | --- |"])
        for _, row in rows.iterrows():
            lines.append(
                f"| {row.get('ticker', '')} | {row.get('name', '')} | {row.get('radar_priority', '')} | "
                f"{row.get('similar_score', '')} | {row.get('review_reason', '')} |"
            )
        lines.append("")
    review_md.write_text("\n".join(lines), encoding="utf-8")
    return review


def _quality_value_zone_status(row: pd.Series) -> str:
    close = float(pd.to_numeric(pd.Series([row.get("close")]), errors="coerce").fillna(0).iloc[0])
    low = float(pd.to_numeric(pd.Series([row.get("buy_zone_low")]), errors="coerce").fillna(0).iloc[0])
    high = float(pd.to_numeric(pd.Series([row.get("buy_zone_high")]), errors="coerce").fillna(0).iloc[0])
    stop = float(pd.to_numeric(pd.Series([row.get("stop_loss")]), errors="coerce").fillna(0).iloc[0])
    if stop > 0 and close < stop:
        return "跌破停損"
    if low > 0 and high > 0 and low <= close <= high:
        return "買區內"
    if high > 0 and close > high:
        return "買區上方"
    if low > 0 and close < low:
        return "買區下方"
    return "無買區"


def _quality_value_heat_status(row: pd.Series) -> str:
    label = str(row.get("spec_risk_label", "") or "").strip()
    risk_score = float(pd.to_numeric(pd.Series([row.get("risk_score")]), errors="coerce").fillna(0).iloc[0])
    volume_ratio = float(pd.to_numeric(pd.Series([row.get("volume_ratio20")]), errors="coerce").fillna(0).iloc[0])
    if label == "疑似炒作風險高" or risk_score >= 6:
        return "過熱"
    if label == "投機偏高" or risk_score >= 3 or volume_ratio >= 2.5:
        return "偏熱"
    return "正常"


def _quality_value_new_addition_action(row: pd.Series) -> tuple[str, str]:
    entry_bias = str(row.get("entry_bias", "") or "").strip()
    zone_status = str(row.get("zone_status", "") or "").strip()
    heat_status = str(row.get("heat_status", "") or "").strip()
    if zone_status == "跌破停損":
        return "移除審核", "已跌破停損線，先退出新加入觀察"
    if heat_status == "過熱":
        return "先不追", "投機風險過高，等降溫再看"
    if entry_bias == "分批試單" and zone_status == "買區內" and heat_status != "過熱":
        return "可試單", "位於買區且尚未過熱，可做小部位研究單"
    if entry_bias == "等拉回":
        return "等拉回", "價格仍高於或尚未穩定落入理想買區"
    if entry_bias == "等轉強":
        return "等轉強", "技術條件未完整，等站回關鍵均線與量能確認"
    if heat_status == "偏熱":
        return "小心觀察", "量能或風險分數偏熱，不用追"
    return "續觀察", "條件未惡化，持續追蹤"


def _render_new_additions_tracking_markdown(tracking: pd.DataFrame, *, generated_at: str) -> str:
    lines = [
        "# Quality Value New Additions Tracking",
        f"- Generated: {generated_at}",
        "- Scope: A-grade quality-value names newly added to `watchlist.csv`; track 5/10/20D momentum, buy-zone status, heat risk, and next action.",
        "",
        "## Summary",
        "",
    ]
    if tracking.empty:
        lines.extend(["- No active new additions.", ""])
        return "\n".join(lines)
    action_counts = tracking["next_action"].fillna("").astype(str).value_counts().to_dict()
    lines.append("- Actions: " + ", ".join(f"`{key}`={value}" for key, value in action_counts.items()))
    lines.append("")
    lines.extend(
        [
            "## Daily Rows",
            "",
            "| Ticker | Name | Days | Rank | Close | Since Add | 5D | 10D | 20D | Zone | Heat | Action | Reason |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for _, row in tracking.iterrows():
        lines.append(
            f"| {row.get('ticker', '')} | {row.get('name', '')} | {row.get('days_tracked', '')} | {row.get('rank', '')} | "
            f"{row.get('close', '')} | {row.get('ret_since_add_pct', '')}% | {row.get('ret5_pct', '')}% | "
            f"{row.get('ret10_pct', '')}% | {row.get('ret20_pct', '')}% | {row.get('zone_status', '')} | "
            f"{row.get('heat_status', '')} | {row.get('next_action', '')} | {row.get('action_reason', '')} |"
        )
    lines.append("")
    return "\n".join(lines)


def write_quality_value_new_additions_tracking(
    tracking: pd.DataFrame,
    *,
    tracking_csv: Path = QUALITY_VALUE_NEW_ADDITIONS_TRACKING_CSV,
    tracking_md: Path = QUALITY_VALUE_NEW_ADDITIONS_TRACKING_MD,
    new_addition_tickers: tuple[str, ...] = QUALITY_VALUE_NEW_ADDITION_TICKERS,
) -> pd.DataFrame:
    columns = [
        "ticker",
        "name",
        "added_date",
        "days_tracked",
        "added_close",
        "close",
        "ret_since_add_pct",
        "ret5_pct",
        "ret10_pct",
        "ret20_pct",
        "rank",
        "entry_bias",
        "buy_zone_low",
        "buy_zone_high",
        "stop_loss",
        "zone_status",
        "heat_status",
        "next_action",
        "action_reason",
    ]
    if tracking.empty:
        result = pd.DataFrame(columns=columns)
    else:
        work = tracking[tracking["ticker"].astype(str).isin(new_addition_tickers)].copy()
        if work.empty:
            result = pd.DataFrame(columns=columns)
        else:
            previous = _load_csv_safely(tracking_csv)
            last_seen_values = work.get("last_seen_date", pd.Series(dtype=object)).dropna().astype(str).str.strip()
            last_seen_values = last_seen_values[last_seen_values != ""]
            current_date = str(last_seen_values.max()) if not last_seen_values.empty else datetime.now().strftime("%Y-%m-%d")
            if not previous.empty and {"ticker", "added_date", "added_close"}.issubset(set(previous.columns)):
                previous = previous.drop_duplicates(subset=["ticker"], keep="last")
                work = work.merge(previous[["ticker", "added_date", "added_close"]], on="ticker", how="left")
            else:
                work["added_date"] = ""
                work["added_close"] = ""
            work["added_date"] = work["added_date"].fillna("").astype(str)
            work.loc[work["added_date"].str.strip() == "", "added_date"] = current_date
            work["added_close"] = pd.to_numeric(work["added_close"], errors="coerce")
            work["close"] = pd.to_numeric(work["close"], errors="coerce")
            work["added_close"] = work["added_close"].fillna(work["close"])
            work["days_tracked"] = work["added_date"].map(lambda value: _days_watched(value, current_date))
            work["ret_since_add_pct"] = ((work["close"] / work["added_close"] - 1) * 100).round(2)
            for col in ["ret5_pct", "ret10_pct", "ret20_pct"]:
                if col not in work.columns:
                    work[col] = ""
            work["zone_status"] = work.apply(_quality_value_zone_status, axis=1)
            work["heat_status"] = work.apply(_quality_value_heat_status, axis=1)
            actions = work.apply(_quality_value_new_addition_action, axis=1)
            work["next_action"] = [action for action, _ in actions]
            work["action_reason"] = [reason for _, reason in actions]
            for col in columns:
                if col not in work.columns:
                    work[col] = ""
            work["_ticker_order"] = work["ticker"].map({ticker: index for index, ticker in enumerate(new_addition_tickers)}).fillna(999)
            result = work.sort_values(by=["_ticker_order"])[columns].reset_index(drop=True)

    tracking_csv.parent.mkdir(parents=True, exist_ok=True)
    tracking_md.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(tracking_csv, index=False, encoding="utf-8-sig")
    tracking_md.write_text(
        _render_new_additions_tracking_markdown(result, generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        encoding="utf-8",
    )
    return result


def _quality_value_trial_action(row: pd.Series) -> tuple[str, str, str]:
    zone_status = str(row.get("zone_status", "") or "").strip()
    heat_status = str(row.get("heat_status", "") or "").strip()
    entry_bias = str(row.get("entry_bias", "") or "").strip()
    if zone_status == "跌破停損":
        return "invalidated", "移除試單", "跌破停損線，試單假設失效"
    if heat_status == "過熱":
        return "paused", "暫停試單", "投機風險過高，先不建立新部位"
    if entry_bias == "分批試單" and zone_status == "買區內":
        return "active_trial", "第一筆 1/3 可研究", "位於買區且尚未過熱；只作追蹤，不代表已下單"
    return "watch_wait", "等待條件", "尚未同時滿足買區與試單條件"


def _quality_value_trial_decision(row: pd.Series) -> tuple[str, str]:
    trial_status = str(row.get("trial_status", "") or "").strip()
    zone_status = str(row.get("zone_status", "") or "").strip()
    heat_status = str(row.get("heat_status", "") or "").strip()
    close = float(pd.to_numeric(pd.Series([row.get("close")]), errors="coerce").fillna(0).iloc[0])
    entry_zone_high = float(pd.to_numeric(pd.Series([row.get("entry_zone_high")]), errors="coerce").fillna(0).iloc[0])
    add_trigger_price = float(pd.to_numeric(pd.Series([row.get("add_trigger_price")]), errors="coerce").fillna(0).iloc[0])
    trim_watch_price = float(pd.to_numeric(pd.Series([row.get("trim_watch_price")]), errors="coerce").fillna(0).iloc[0])
    risk_to_stop_pct = float(pd.to_numeric(pd.Series([row.get("risk_to_stop_pct")]), errors="coerce").fillna(0).iloc[0])
    days_to_review = int(float(pd.to_numeric(pd.Series([row.get("days_to_review")]), errors="coerce").fillna(0).iloc[0]))
    if trial_status == "invalidated" or zone_status == "跌破停損":
        return "invalidated", "跌破停損，移出試單並回到觀察池"
    if trial_status == "paused" or heat_status == "過熱":
        return "risk_pause", "熱度過高，不新增部位，等風險降溫"
    if trial_status != "active_trial":
        return "waiting", "條件未齊，等買區與轉強訊號重新同步"
    if trim_watch_price > 0 and close >= trim_watch_price:
        return "profit_watch", "接近 +8% 試單檢查；若不續強先鎖定成果"
    if add_trigger_price > 0 and close >= add_trigger_price and heat_status != "過熱":
        return "add_watch", "突破確認區；若量能健康可研究第二筆 1/3"
    if entry_zone_high > 0 and close <= entry_zone_high and abs(risk_to_stop_pct) >= 7:
        return "risk_watch", "仍在買區但停損距離偏大；試單要小，嚴守停損"
    return "holding_trial", f"持續追蹤；{days_to_review} 個交易日內重新檢查是否轉強或失效"


def _render_quality_value_trial_ledger_markdown(ledger: pd.DataFrame, *, generated_at: str) -> str:
    lines = [
        "# Quality Value Trial Ledger",
        f"- Generated: {generated_at}",
        "- Scope: simulated/research-only trial tracking. This file does not represent an executed order.",
        "",
        "## Summary",
        "",
    ]
    if ledger.empty:
        lines.extend(["- No active trial names.", ""])
        return "\n".join(lines)
    status_counts = ledger["trial_status"].fillna("").astype(str).value_counts().to_dict()
    lines.append("- Status: " + ", ".join(f"`{key}`={value}" for key, value in status_counts.items()))
    if "decision_state" in ledger.columns:
        decision_counts = ledger["decision_state"].fillna("").astype(str).value_counts().to_dict()
        lines.append("- Decisions: " + ", ".join(f"`{key}`={value}" for key, value in decision_counts.items()))
    lines.append("")
    lines.extend(
        [
            "## Decision Cards",
            "",
            "| Ticker | Name | State | Next Check | Add Trigger | Trim Watch | Hard Stop | Risk To Stop | Days To Review |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for _, row in ledger.iterrows():
        lines.append(
            f"| {row.get('ticker', '')} | {row.get('name', '')} | {row.get('decision_state', '')} | "
            f"{row.get('next_check', '')} | {row.get('add_trigger_price', '')} | {row.get('trim_watch_price', '')} | "
            f"{row.get('hard_stop_price', '')} | {row.get('risk_to_stop_pct', '')}% | {row.get('days_to_review', '')} |"
        )
    lines.append("")
    lines.extend(
        [
            "## Trial Rows",
            "",
            "| Ticker | Name | Status | Days | Close | Sim Entry | Sim Ret | Zone | Heat | Stop | Stop Gap | Next Action | Rule |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for _, row in ledger.iterrows():
        lines.append(
            f"| {row.get('ticker', '')} | {row.get('name', '')} | {row.get('trial_status', '')} | {row.get('days_tracked', '')} | "
            f"{row.get('close', '')} | {row.get('simulated_entry_price', '')} | {row.get('simulated_ret_pct', '')}% | "
            f"{row.get('zone_status', '')} | {row.get('heat_status', '')} | {row.get('stop_loss', '')} | "
            f"{row.get('stop_distance_pct', '')}% | {row.get('next_action', '')} | {row.get('rule_note', '')} |"
        )
    lines.append("")
    return "\n".join(lines)


def write_quality_value_trial_ledger(
    new_additions_tracking: pd.DataFrame,
    *,
    ledger_csv: Path = QUALITY_VALUE_TRIAL_LEDGER_CSV,
    ledger_md: Path = QUALITY_VALUE_TRIAL_LEDGER_MD,
    trial_tickers: tuple[str, ...] = QUALITY_VALUE_TRIAL_TICKERS,
) -> pd.DataFrame:
    columns = [
        "ticker",
        "name",
        "trial_start_date",
        "days_tracked",
        "trial_status",
        "close",
        "simulated_entry_price",
        "simulated_ret_pct",
        "planned_unit",
        "entry_zone_low",
        "entry_zone_high",
        "stop_loss",
        "stop_distance_pct",
        "hard_stop_price",
        "risk_to_stop_pct",
        "add_trigger_price",
        "trim_watch_price",
        "days_to_review",
        "decision_state",
        "next_check",
        "zone_status",
        "heat_status",
        "next_action",
        "rule_note",
    ]
    if new_additions_tracking.empty:
        ledger = pd.DataFrame(columns=columns)
    else:
        work = new_additions_tracking[new_additions_tracking["ticker"].astype(str).isin(trial_tickers)].copy()
        if work.empty:
            ledger = pd.DataFrame(columns=columns)
        else:
            previous = _load_csv_safely(ledger_csv)
            current_date_values = work.get("added_date", pd.Series(dtype=object)).dropna().astype(str).str.strip()
            current_date_values = current_date_values[current_date_values != ""]
            current_date = str(current_date_values.max()) if not current_date_values.empty else datetime.now().strftime("%Y-%m-%d")
            if not previous.empty and {"ticker", "trial_start_date", "simulated_entry_price"}.issubset(set(previous.columns)):
                previous = previous.drop_duplicates(subset=["ticker"], keep="last")
                work = work.merge(previous[["ticker", "trial_start_date", "simulated_entry_price"]], on="ticker", how="left")
            else:
                work["trial_start_date"] = ""
                work["simulated_entry_price"] = ""
            work["trial_start_date"] = work["trial_start_date"].fillna("").astype(str)
            work.loc[work["trial_start_date"].str.strip() == "", "trial_start_date"] = current_date
            work["close"] = pd.to_numeric(work["close"], errors="coerce")
            work["stop_loss"] = pd.to_numeric(work["stop_loss"], errors="coerce")
            work["simulated_entry_price"] = pd.to_numeric(work["simulated_entry_price"], errors="coerce")
            actions = work.apply(_quality_value_trial_action, axis=1)
            work["trial_status"] = [status for status, _, _ in actions]
            work["next_action"] = [action for _, action, _ in actions]
            work["rule_note"] = [rule for _, _, rule in actions]
            active_mask = work["trial_status"] == "active_trial"
            work.loc[active_mask, "simulated_entry_price"] = work.loc[active_mask, "simulated_entry_price"].fillna(work.loc[active_mask, "close"])
            simulated_ret_pct = ((work["close"] / work["simulated_entry_price"] - 1) * 100).round(2)
            work["simulated_ret_pct"] = simulated_ret_pct.where(work["simulated_entry_price"].notna(), "")
            work["days_tracked"] = work["trial_start_date"].map(lambda value: _days_watched(value, current_date))
            work["planned_unit"] = "1/3"
            work["entry_zone_low"] = work.get("buy_zone_low", "")
            work["entry_zone_high"] = work.get("buy_zone_high", "")
            stop_distance_pct = ((work["close"] / work["stop_loss"] - 1) * 100).round(2)
            work["stop_distance_pct"] = stop_distance_pct.where(work["stop_loss"].notna() & (work["stop_loss"] != 0), "")
            work["hard_stop_price"] = work["stop_loss"]
            risk_to_stop_pct = ((work["stop_loss"] / work["simulated_entry_price"] - 1) * 100).round(2)
            work["risk_to_stop_pct"] = risk_to_stop_pct.where(work["simulated_entry_price"].notna() & work["stop_loss"].notna() & (work["stop_loss"] != 0), "")
            work["add_trigger_price"] = (pd.to_numeric(work["entry_zone_high"], errors="coerce") * 1.03).round(2)
            work["trim_watch_price"] = (work["simulated_entry_price"] * 1.08).round(2)
            days_tracked_num = pd.to_numeric(work["days_tracked"], errors="coerce").fillna(0).astype(int)
            work["days_to_review"] = days_tracked_num.map(lambda value: max(0, 10 - int(value)))
            decisions = work.apply(_quality_value_trial_decision, axis=1)
            work["decision_state"] = [state for state, _ in decisions]
            work["next_check"] = [next_check for _, next_check in decisions]
            for col in columns:
                if col not in work.columns:
                    work[col] = ""
            work["_ticker_order"] = work["ticker"].map({ticker: index for index, ticker in enumerate(trial_tickers)}).fillna(999)
            ledger = work.sort_values(by=["_ticker_order"])[columns].reset_index(drop=True)

    ledger_csv.parent.mkdir(parents=True, exist_ok=True)
    ledger_md.parent.mkdir(parents=True, exist_ok=True)
    ledger.to_csv(ledger_csv, index=False, encoding="utf-8-sig")
    ledger_md.write_text(
        _render_quality_value_trial_ledger_markdown(ledger, generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        encoding="utf-8",
    )
    return ledger


def update_quality_value_tracking(
    *,
    daily_rank_csv: Path = DAILY_RANK_CSV,
    entry_plan_csv: Path = QUALITY_VALUE_ENTRY_PLAN_CSV,
    draft_csv: Path = QUALITY_VALUE_WATCHLIST_DRAFT_CSV,
    tracking_csv: Path = QUALITY_VALUE_TRACKING_CSV,
    pruning_md: Path = QUALITY_VALUE_PRUNING_MD,
    candidate_review_csv: Path = QUALITY_VALUE_CANDIDATE_REVIEW_CSV,
    candidate_review_md: Path = QUALITY_VALUE_CANDIDATE_REVIEW_MD,
    new_additions_tracking_csv: Path | None = None,
    new_additions_tracking_md: Path | None = None,
    trial_ledger_csv: Path | None = None,
    trial_ledger_md: Path | None = None,
) -> pd.DataFrame:
    if new_additions_tracking_csv is None:
        new_additions_tracking_csv = tracking_csv.parent / "quality_value_new_additions_tracking.csv"
    if new_additions_tracking_md is None:
        new_additions_tracking_md = tracking_csv.parent / "quality_value_new_additions_tracking.md"
    if trial_ledger_csv is None:
        trial_ledger_csv = tracking_csv.parent / "quality_value_trial_ledger.csv"
    if trial_ledger_md is None:
        trial_ledger_md = tracking_csv.parent / "quality_value_trial_ledger.md"
    daily_rank = _load_csv_safely(daily_rank_csv)
    if daily_rank.empty:
        tracking = pd.DataFrame(
            columns=[
                "ticker",
                "name",
                "first_seen_date",
                "last_seen_date",
                "days_watched",
                "radar_grade",
                "rank",
                "close",
                "ret5_pct",
                "ret10_pct",
                "ret20_pct",
                "volume_ratio20",
                "setup_score",
                "risk_score",
                "spec_risk_label",
                "entry_bias",
                "decision_priority",
                "lifecycle_action",
                "lifecycle_reason",
                "buy_zone_low",
                "buy_zone_high",
                "stop_loss",
            ]
        )
    else:
        work = daily_rank.copy()
        if "layer" in work.columns:
            work = work[work["layer"].fillna("").astype(str) == "quality_value"].copy()
        current_date = _quality_value_current_date(work)
        entry_plan = _load_csv_safely(entry_plan_csv)
        draft = _load_csv_safely(draft_csv)
        previous = _load_csv_safely(tracking_csv)

        keep_cols = [
            "ticker",
            "name",
            "rank",
            "close",
            "ret5_pct",
            "ret10_pct",
            "ret20_pct",
            "volume_ratio20",
            "setup_score",
            "risk_score",
            "spec_risk_label",
        ]
        for col in keep_cols:
            if col not in work.columns:
                work[col] = ""
        tracking = work[keep_cols].copy()
        tracking["ticker"] = tracking["ticker"].astype(str).str.strip()

        if not entry_plan.empty:
            entry_cols = ["ticker", "entry_bias", "decision_priority", "buy_zone_low", "buy_zone_high", "stop_loss"]
            for col in entry_cols:
                if col not in entry_plan.columns:
                    entry_plan[col] = ""
            tracking = tracking.merge(entry_plan[entry_cols], on="ticker", how="left")
        else:
            for col in ["entry_bias", "decision_priority", "buy_zone_low", "buy_zone_high", "stop_loss"]:
                tracking[col] = ""

        if not previous.empty and {"ticker", "first_seen_date"}.issubset(set(previous.columns)):
            previous = previous.drop_duplicates(subset=["ticker"], keep="last")
            tracking = tracking.merge(previous[["ticker", "first_seen_date"]], on="ticker", how="left")
        else:
            tracking["first_seen_date"] = ""
        tracking["first_seen_date"] = tracking["first_seen_date"].fillna("").astype(str)
        tracking.loc[tracking["first_seen_date"].str.strip() == "", "first_seen_date"] = current_date
        tracking["last_seen_date"] = current_date
        tracking["days_watched"] = tracking["first_seen_date"].map(lambda value: _days_watched(value, current_date))

        if not draft.empty and {"ticker", "radar_priority"}.issubset(set(draft.columns)):
            draft = draft.drop_duplicates(subset=["ticker"], keep="first").copy()
            draft["radar_grade"] = draft["radar_priority"].fillna("").astype(str).str[:1]
            tracking = tracking.merge(draft[["ticker", "radar_grade"]], on="ticker", how="left")
        else:
            tracking["radar_grade"] = ""
        tracking["radar_grade"] = tracking["radar_grade"].fillna("")

        actions = tracking.apply(_quality_value_lifecycle_action, axis=1)
        tracking["lifecycle_action"] = [action for action, _ in actions]
        tracking["lifecycle_reason"] = [reason for _, reason in actions]
        ordered_cols = [
            "ticker",
            "name",
            "first_seen_date",
            "last_seen_date",
            "days_watched",
            "radar_grade",
            "rank",
            "close",
            "ret5_pct",
            "ret10_pct",
            "ret20_pct",
            "volume_ratio20",
            "setup_score",
            "risk_score",
            "spec_risk_label",
            "entry_bias",
            "decision_priority",
            "lifecycle_action",
            "lifecycle_reason",
            "buy_zone_low",
            "buy_zone_high",
            "stop_loss",
        ]
        tracking = tracking[ordered_cols].reset_index(drop=True)

    tracking_csv.parent.mkdir(parents=True, exist_ok=True)
    pruning_md.parent.mkdir(parents=True, exist_ok=True)
    tracking.to_csv(tracking_csv, index=False, encoding="utf-8-sig")
    pruning_md.write_text(
        _render_quality_value_pruning_report(tracking, generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        encoding="utf-8",
    )
    write_quality_value_candidate_review(draft_csv=draft_csv, review_csv=candidate_review_csv, review_md=candidate_review_md)
    new_additions_tracking = write_quality_value_new_additions_tracking(
        tracking,
        tracking_csv=new_additions_tracking_csv,
        tracking_md=new_additions_tracking_md,
    )
    write_quality_value_trial_ledger(
        new_additions_tracking,
        ledger_csv=trial_ledger_csv,
        ledger_md=trial_ledger_md,
    )
    return tracking


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
    action_summary = _collect_quality_value_action_summary(theme_outdir / "quality_value_entry_plan.csv")
    portfolio_summary = _collect_portfolio_action_summary(theme_outdir / "portfolio_report.md")
    new_additions_summary = _collect_new_additions_action_summary(theme_outdir / "quality_value_new_additions_tracking.csv")
    trial_ledger_summary = _collect_trial_ledger_action_summary(theme_outdir / "quality_value_trial_ledger.csv")
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
        "quality_value_scout_draft_rows": int(quality_value_runtime.get("scout_draft_rows", 0) or 0),
        "quality_value_tracking_rows": _count_csv_rows(theme_outdir / "quality_value_tracking.csv"),
        "quality_value_new_additions_tracking_rows": _count_csv_rows(theme_outdir / "quality_value_new_additions_tracking.csv"),
        "quality_value_trial_ledger_rows": _count_csv_rows(theme_outdir / "quality_value_trial_ledger.csv"),
        "quality_value_candidate_review_rows": _count_csv_rows(theme_outdir / "quality_value_candidate_review.csv"),
        "quality_value_pruning_status": "ready" if (theme_outdir / "quality_value_pruning_report.md").exists() else "missing",
        "verification_runtime_seconds": float(verification_runtime.get("wall_seconds", 0.0) or 0.0),
        "verification_runtime_status": str(verification_runtime.get("status", "") or ""),
        "spec_risk_high_rows": int(spec_risk_metrics["spec_risk_high_rows"]),
        "spec_risk_watch_rows": int(spec_risk_metrics["spec_risk_watch_rows"]),
        "spec_risk_top_tickers": list(spec_risk_metrics["spec_risk_top_tickers"]),
        **action_summary,
        **portfolio_summary,
        **new_additions_summary,
        **trial_ledger_summary,
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
            f"- Quality value similar scout rows: `{metrics.get('quality_value_scout_rows', 0)}`, draft=`{metrics.get('quality_value_scout_draft_rows', 0)}`",
            f"- Quality value lifecycle rows: tracking=`{metrics.get('quality_value_tracking_rows', 0)}`, candidate_review=`{metrics.get('quality_value_candidate_review_rows', 0)}`, pruning=`{metrics.get('quality_value_pruning_status') or 'missing'}`",
            f"- Quality value new-addition rows: `{metrics.get('quality_value_new_additions_tracking_rows', 0)}`",
            f"- Quality value trial ledger rows: `{metrics.get('quality_value_trial_ledger_rows', 0)}`",
            f"- Quality value runtime: `{metrics.get('quality_value_runtime_seconds', 0.0):.3f}s` ({metrics.get('quality_value_runtime_status') or 'n/a'})"
            + (f", generated `{metrics.get('quality_value_generated_at')}`" if metrics.get("quality_value_generated_at") else ""),
            f"- Verification runtime: `{metrics.get('verification_runtime_seconds', 0.0):.3f}s` ({metrics.get('verification_runtime_status') or 'n/a'})",
            "",
            "## Action Summary",
            "",
            f"- 可試單: `{', '.join(metrics.get('action_trial_tickers', [])) or 'n/a'}`",
            f"- 等拉回: `{', '.join(metrics.get('action_pullback_tickers', [])) or 'n/a'}`",
            f"- 量縮先等: `{', '.join(metrics.get('action_low_liquidity_tickers', [])) or 'n/a'}`",
            f"- 等轉強: `{', '.join(metrics.get('action_wait_strength_tickers', [])) or 'n/a'}`",
            f"- 過熱先等: `{', '.join(metrics.get('action_cooldown_tickers', [])) or 'n/a'}`",
            f"- 新A追蹤: `{', '.join(metrics.get('new_addition_action_tickers', [])) or 'n/a'}`",
            f"- 試單追蹤: `{', '.join(metrics.get('trial_ledger_action_tickers', [])) or 'n/a'}`",
            f"- 持股分批落袋: `{', '.join(metrics.get('portfolio_trim_tickers', [])) or 'n/a'}`",
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
            f"- Quality value watchlist draft: `{theme_outdir_str('quality_value_watchlist_draft.csv')}`",
            f"- Quality value tracking: `{theme_outdir_str('quality_value_tracking.csv')}`",
            f"- Quality value new additions tracking: `{theme_outdir_str('quality_value_new_additions_tracking.md')}`",
            f"- Quality value trial ledger: `{theme_outdir_str('quality_value_trial_ledger.md')}`",
            f"- Quality value pruning: `{theme_outdir_str('quality_value_pruning_report.md')}`",
            f"- Quality value candidate review: `{theme_outdir_str('quality_value_candidate_review.md')}`",
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
            "quality_value_watchlist_draft": str(theme_outdir / "quality_value_watchlist_draft.csv"),
            "quality_value_tracking": str(theme_outdir / "quality_value_tracking.csv"),
            "quality_value_new_additions_tracking": str(theme_outdir / "quality_value_new_additions_tracking.md"),
            "quality_value_new_additions_tracking_csv": str(theme_outdir / "quality_value_new_additions_tracking.csv"),
            "quality_value_trial_ledger": str(theme_outdir / "quality_value_trial_ledger.md"),
            "quality_value_trial_ledger_csv": str(theme_outdir / "quality_value_trial_ledger.csv"),
            "quality_value_pruning": str(theme_outdir / "quality_value_pruning_report.md"),
            "quality_value_candidate_review": str(theme_outdir / "quality_value_candidate_review.md"),
            "quality_value_candidate_review_csv": str(theme_outdir / "quality_value_candidate_review.csv"),
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
    configure_local_telegram_chat_ids(args.local_telegram_chat_ids)
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
        update_quality_value_tracking()
        if args.quality_value_notification:
            send_quality_value_notification()
        steps.append({"name": "quality_value", "label": STEP_LABELS["quality_value"], "status": "completed", "detail": "Updated research report"})

    write_local_status_dashboard(args=args, steps=steps, overall_status=overall_status)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
