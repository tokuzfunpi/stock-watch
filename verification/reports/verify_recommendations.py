from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime
import json
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from stock_watch.paths import THEME_OUTDIR
from stock_watch.paths import VERIFICATION_OUTDIR
from daily_theme_watchlist import (
    build_market_scenario,
    LOCAL_TZ,
    CONFIG,
    get_market_regime,
    get_us_market_reference,
    is_midlong_buyable,
    is_short_term_buyable,
    rank_midlong_pool,
    rank_short_term_pool,
    select_midlong_backup_candidates,
    select_midlong_candidates,
    select_short_term_backup_candidates,
    select_short_term_candidates,
    midlong_action_label,
    short_term_action_label,
)
from stock_watch.signals import apply_signal_template_labels
from stock_watch.signals import summarize_signal_templates
from verification.reports.summarize_outcomes import summarize_outcomes


@dataclass(frozen=True)
class VerificationHeuristics:
    warn_overheated_ret5_pct: float = 18.0
    warn_high_risk_score: int = 5
    warn_low_volume_ratio: float = 0.9


DEFAULT_IMPROVEMENT_NOTES = [
    "- 若短線多為「開高不追 / 只觀察不追」，可考慮降低 `top_n_short` 或收斂追價條件。",
    "- 若中線多為「分批落袋」，代表偏後段；可考慮提升趨勢訊號權重或降低過熱門檻。",
    "- 若短/中線重疊過多，可考慮讓短線池排除 `midlong_core` 或在推播層做去重。",
]


def select_forced_recommendations(
    df_rank: pd.DataFrame,
    *,
    watch_type: str,
    top_n: int = 5,
) -> pd.DataFrame:
    if df_rank is None or df_rank.empty:
        return pd.DataFrame()

    watch_type = str(watch_type or "").strip().lower()
    if watch_type == "short":
        pool = rank_short_term_pool(df_rank).copy()
        if pool.empty:
            return pool
        pool["action"] = pool.apply(short_term_action_label, axis=1)
        pool["_ok"] = pool.apply(is_short_term_buyable, axis=1)
    elif watch_type == "midlong":
        pool = rank_midlong_pool(df_rank).copy()
        if pool.empty:
            return pool
        pool["action"] = pool.apply(midlong_action_label, axis=1)
        pool["_ok"] = pool.apply(is_midlong_buyable, axis=1)
    else:
        raise ValueError(f"Unknown watch_type: {watch_type}")

    pool["_rank"] = pd.to_numeric(pool.get("rank"), errors="coerce")
    pool = pool.sort_values(by=["_ok", "_rank"], ascending=[False, True]).copy()
    if int(top_n) > 0:
        pool = pool.head(int(top_n)).copy()
    pool["reco_status"] = pool["_ok"].map(lambda v: "ok" if bool(v) else "below_threshold")
    pool = pool.drop(columns=["_ok", "_rank"], errors="ignore")
    return pool


def append_csv_with_existing_header(path: Path, rows: pd.DataFrame) -> None:
    if rows is None or rows.empty:
        return
    path.parent.mkdir(parents=True, exist_ok=True)

    if not path.exists():
        rows.to_csv(path, index=False, encoding="utf-8")
        return

    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        header = next(reader, [])

    if not header:
        rows.to_csv(path, index=False, encoding="utf-8")
        return

    aligned = rows.copy()
    for col in header:
        if col not in aligned.columns:
            aligned[col] = ""
    aligned = aligned[[c for c in header if c in aligned.columns]].copy()
    with path.open("a", encoding="utf-8", newline="") as f:
        aligned.to_csv(f, index=False, header=False)


def upsert_csv_with_existing_header(path: Path, rows: pd.DataFrame, *, key_cols: list[str]) -> None:
    if rows is None or rows.empty:
        return
    path.parent.mkdir(parents=True, exist_ok=True)

    incoming = rows.copy()
    for col in key_cols:
        if col not in incoming.columns:
            raise ValueError(f"Missing key column for upsert: {col}")
        incoming[col] = incoming[col].astype(str).str.strip()

    if not path.exists():
        incoming.to_csv(path, index=False, encoding="utf-8")
        return

    existing = pd.read_csv(path)
    if existing.empty:
        incoming.to_csv(path, index=False, encoding="utf-8")
        return

    for col in incoming.columns:
        if col not in existing.columns:
            existing[col] = ""
    for col in existing.columns:
        if col not in incoming.columns:
            incoming[col] = ""

    existing = existing[incoming.columns.tolist()].copy()
    for col in key_cols:
        if col not in existing.columns:
            existing[col] = ""
        existing[col] = existing[col].astype(str).str.strip()

    merged = pd.concat([existing, incoming], ignore_index=True)
    merged = merged.drop_duplicates(subset=key_cols, keep="last")
    merged.to_csv(path, index=False, encoding="utf-8")


def _render_notes_section(title: str, notes: list[str]) -> list[str]:
    lines = [title]
    if not notes:
        return lines + ["- None"]
    return lines + notes


def _load_outcomes_aggregate(outcomes_csv: Path) -> dict:
    if not outcomes_csv.exists():
        return {}
    try:
        df = pd.read_csv(outcomes_csv)
    except Exception:
        return {}
    if df.empty:
        return {}
    if "status" in df.columns:
        df = df[df["status"].astype(str) == "ok"].copy()
    if df.empty:
        return {}
    for col in ["realized_ret_pct", "horizon_days"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "watch_type" in df.columns:
        df["watch_type"] = df["watch_type"].astype(str).str.strip().str.lower()
    df["win"] = pd.to_numeric(df.get("realized_ret_pct"), errors="coerce") > 0
    try:
        parts = summarize_outcomes(df)
        overall = (
            df.groupby(["horizon_days", "watch_type"], dropna=False)
            .agg(
                n=("realized_ret_pct", "count"),
                win_rate=("win", "mean"),
                avg_ret=("realized_ret_pct", "mean"),
                med_ret=("realized_ret_pct", "median"),
            )
            .reset_index()
            .sort_values(by=["horizon_days", "watch_type"])
        )
        overall["win_rate"] = (overall["win_rate"] * 100).round(1)
        for c in ["avg_ret", "med_ret"]:
            overall[c] = overall[c].round(2)
    except Exception:
        return {}
    return {
        "overall_by_signal": overall.to_dict(orient="records"),
        "midlong_threshold_gate": parts.get("midlong_threshold_gate", pd.DataFrame()).to_dict(orient="records"),
    }


CODEX_CONTEXT_DIR = VERIFICATION_OUTDIR / "contexts"
CODEX_CONTEXT_LATEST = VERIFICATION_OUTDIR / "codex_context.json"


def _write_json_file(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _df_records(df: pd.DataFrame, cols: list[str], limit: int) -> list[dict]:
    if df is None or df.empty:
        return []
    view = df[[c for c in cols if c in df.columns]].copy()
    if int(limit) > 0:
        view = view.head(int(limit)).copy()
    out: list[dict] = []
    for _, r in view.iterrows():
        row: dict = {}
        for c in view.columns.tolist():
            val = r.get(c)
            if pd.isna(val):
                row[c] = None
            elif isinstance(val, (int, float, str, bool)):
                row[c] = val
            else:
                row[c] = str(val)
        out.append(row)
    return out


def build_codex_context(
    *,
    df_rank: pd.DataFrame,
    source: str,
    now_local: datetime,
    top_n_short: int,
    top_n_midlong: int,
    warnings: list[str],
    overlap: list[str],
    short_forced: pd.DataFrame,
    midlong_forced: pd.DataFrame,
    short_backups: pd.DataFrame,
    midlong_backups: pd.DataFrame,
) -> dict:
    asof_date = _maybe_date_from_rank(df_rank)
    short_pool = rank_short_term_pool(df_rank) if not df_rank.empty else df_rank.head(0).copy()
    midlong_pool = rank_midlong_pool(df_rank) if not df_rank.empty else df_rank.head(0).copy()
    outcomes_csv = VERIFICATION_OUTDIR / "reco_outcomes.csv"

    cols_pool = [
        "rank",
        "ticker",
        "name",
        "grade",
        "setup_score",
        "risk_score",
        "spec_risk_score",
        "spec_risk_label",
        "spec_risk_subtype",
        "ret5_pct",
        "ret20_pct",
        "volume_ratio20",
        "signals",
        "close",
        "group",
        "layer",
        "rank_change",
        "setup_change",
        "regime",
    ]
    cols_reco = cols_pool + ["action", "reco_status"]

    return {
        "generated_at": now_local.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "asof_date": asof_date,
        "source": source,
        "forced_top_n": {"short": int(top_n_short), "midlong": int(top_n_midlong)},
        "notify_config": {"top_n_short": int(CONFIG.notify.top_n_short), "top_n_midlong": int(CONFIG.notify.top_n_midlong)},
        "summary": {
            "short_pool_size": int(len(short_pool)),
            "midlong_pool_size": int(len(midlong_pool)),
            "short_forced_size": int(len(short_forced)),
            "midlong_forced_size": int(len(midlong_forced)),
            "overlap_count": int(len(overlap)),
        },
        "warnings": list(warnings),
        "overlap": list(overlap),
        "action_counts": {
            "short": _action_counts(short_forced, "action"),
            "midlong": _action_counts(midlong_forced, "action"),
        },
        "spec_risk_counts": {
            "short": _spec_risk_counts(short_forced),
            "midlong": _spec_risk_counts(midlong_forced),
        },
        "forced": {
            "short": _df_records(short_forced, cols_reco, 20),
            "midlong": _df_records(midlong_forced, cols_reco, 20),
        },
        "backups": {
            "short": _df_records(short_backups, cols_reco, 20),
            "midlong": _df_records(midlong_backups, cols_reco, 20),
        },
        "pool_top": {
            "short": _df_records(short_pool, cols_pool, 40),
            "midlong": _df_records(midlong_pool, cols_pool, 40),
        },
        "outcomes_aggregate": _load_outcomes_aggregate(outcomes_csv),
    }


def write_codex_context_files(ctx: dict) -> tuple[Path, Path]:
    CODEX_CONTEXT_DIR.mkdir(parents=True, exist_ok=True)
    dated = CODEX_CONTEXT_DIR / f"codex_context_{ctx.get('asof_date','')}_{ctx.get('generated_at','')}.json"
    # make filename safe-ish
    dated = Path(str(dated).replace(":", "").replace(" ", "_"))
    _write_json_file(CODEX_CONTEXT_LATEST, ctx)
    _write_json_file(dated, ctx)
    return CODEX_CONTEXT_LATEST, dated


def _format_table(df: pd.DataFrame, cols: list[str]) -> str:
    if df.empty:
        return "_None_\n"
    view = df.copy()
    for col in cols:
        if col not in view.columns:
            view[col] = pd.NA
    view = view[cols].copy()
    headers = [str(c) for c in view.columns.tolist()]
    rows: list[list[str]] = []
    for _, r in view.iterrows():
        row: list[str] = []
        for c in headers:
            val = r.get(c)
            if pd.isna(val):
                text = ""
            elif isinstance(val, float):
                text = f"{val:.2f}".rstrip("0").rstrip(".")
            else:
                text = str(val)
            text = text.replace("|", "\\|").replace("\n", " ")
            row.append(text)
        rows.append(row)

    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines) + "\n"


def _action_counts(df: pd.DataFrame, col: str) -> dict[str, int]:
    if df.empty or col not in df.columns:
        return {}
    return df[col].fillna("").astype(str).value_counts().to_dict()


def _spec_risk_bucket(df: pd.DataFrame) -> pd.Series:
    if df.empty:
        return pd.Series(dtype=str)
    score = pd.to_numeric(df.get("spec_risk_score"), errors="coerce")
    label = df.get("spec_risk_label", pd.Series(index=df.index, dtype=object)).fillna("").astype(str).str.strip()
    bucket = pd.Series("normal", index=df.index, dtype=object)
    bucket[(score >= 3) | label.isin(["投機偏高", "偏熱", "留意"])] = "watch"
    bucket[(score >= 6) | (label == "疑似炒作風險高")] = "high"
    return bucket.astype(str)


def _spec_risk_counts(df: pd.DataFrame) -> dict[str, int]:
    if df.empty:
        return {}
    return _spec_risk_bucket(df).value_counts().to_dict()


def _spec_risk_watch_rows(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.head(0).copy()
    work = df.copy()
    work["spec_risk_bucket"] = _spec_risk_bucket(work)
    watch = work[work["spec_risk_bucket"].isin(["high", "watch"])].copy()
    if watch.empty:
        return watch
    watch["_spec_risk_order"] = watch["spec_risk_bucket"].map({"high": 0, "watch": 1}).fillna(2)
    watch["_spec_risk_score_num"] = pd.to_numeric(watch.get("spec_risk_score"), errors="coerce").fillna(0)
    return watch.sort_values(by=["_spec_risk_order", "_spec_risk_score_num", "rank"], ascending=[True, False, True])


def _maybe_date_from_rank(df_rank: pd.DataFrame) -> str:
    if "date" not in df_rank.columns or df_rank.empty:
        return datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")
    values = df_rank["date"].dropna().astype(str).tolist()
    return values[-1] if values else datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")


def compute_reco_warnings(
    short_forced: pd.DataFrame,
    midlong_forced: pd.DataFrame,
    *,
    overlap: list[str],
    heuristics: VerificationHeuristics,
) -> list[str]:
    warnings: list[str] = []
    if overlap:
        warnings.append(f"短線/中線推薦重疊 {len(overlap)} 檔：{', '.join(overlap)}")

    def _scan_below_threshold(df: pd.DataFrame, label: str) -> None:
        if df.empty or "reco_status" not in df.columns:
            return
        below = df[df["reco_status"].astype(str) != "ok"].copy()
        if below.empty:
            return
        names = ", ".join(below["ticker"].astype(str).head(5).tolist())
        warnings.append(f"{label} 補滿用（低於原本可買門檻）{len(below)} 檔：{names}")

    def _scan_overheated(df: pd.DataFrame, label: str) -> None:
        if df.empty:
            return
        overheated = df[
            (pd.to_numeric(df.get("ret5_pct"), errors="coerce") >= heuristics.warn_overheated_ret5_pct)
            | (pd.to_numeric(df.get("risk_score"), errors="coerce") >= heuristics.warn_high_risk_score)
        ].copy()
        if not overheated.empty:
            names = ", ".join(overheated["ticker"].astype(str).head(5).tolist())
            warnings.append(f"{label} 含偏過熱/高風險標的（ret5或risk偏高）：{names}")

    def _scan_liquidity(df: pd.DataFrame, label: str) -> None:
        if df.empty:
            return
        low_vol = df[pd.to_numeric(df.get("volume_ratio20"), errors="coerce") < heuristics.warn_low_volume_ratio]
        if not low_vol.empty:
            names = ", ".join(low_vol["ticker"].astype(str).head(5).tolist())
            warnings.append(f"{label} 含量比偏低標的（< {heuristics.warn_low_volume_ratio}）：{names}")

    def _scan_speculative(df: pd.DataFrame, label: str) -> None:
        if df.empty:
            return
        buckets = _spec_risk_bucket(df)
        high = df[buckets == "high"].copy()
        if not high.empty:
            names = ", ".join(high["ticker"].astype(str).head(5).tolist())
            warnings.append(f"{label} 含高疑似炒作樣本（spec_risk=high）：{names}")

    _scan_below_threshold(short_forced, "短線推薦")
    _scan_below_threshold(midlong_forced, "中線推薦")
    _scan_overheated(short_forced, "短線推薦")
    _scan_overheated(midlong_forced, "中線推薦")
    _scan_liquidity(short_forced, "短線推薦")
    _scan_liquidity(midlong_forced, "中線推薦")
    _scan_speculative(short_forced, "短線推薦")
    _scan_speculative(midlong_forced, "中線推薦")
    return warnings


def build_verification_report_markdown(
    df_rank: pd.DataFrame,
    *,
    source: str,
    now_local: datetime | None = None,
    heuristics: VerificationHeuristics | None = None,
    top_n_short: int = 5,
    top_n_midlong: int = 5,
    improvement_notes: list[str] | None = None,
    codex_context: dict | None = None,
) -> str:
    now_local = now_local or datetime.now(LOCAL_TZ)
    heuristics = heuristics or VerificationHeuristics()
    asof_date = _maybe_date_from_rank(df_rank)

    short_pool = rank_short_term_pool(df_rank) if not df_rank.empty else df_rank.head(0).copy()
    midlong_pool = rank_midlong_pool(df_rank) if not df_rank.empty else df_rank.head(0).copy()
    short_forced = select_forced_recommendations(df_rank, watch_type="short", top_n=top_n_short) if not df_rank.empty else df_rank.head(0).copy()
    midlong_forced = select_forced_recommendations(df_rank, watch_type="midlong", top_n=top_n_midlong) if not df_rank.empty else df_rank.head(0).copy()

    short_backups = select_short_term_backup_candidates(
        df_rank,
        exclude_tickers=set(short_forced["ticker"].astype(str)) if not short_forced.empty else None,
    ) if not df_rank.empty else df_rank.head(0).copy()
    midlong_backups = select_midlong_backup_candidates(
        df_rank,
        exclude_tickers=set(midlong_forced["ticker"].astype(str)) if not midlong_forced.empty else None,
    ) if not df_rank.empty else df_rank.head(0).copy()

    if not short_backups.empty:
        short_backups = short_backups.copy()
        short_backups["action"] = short_backups.apply(short_term_action_label, axis=1)
    if not midlong_backups.empty:
        midlong_backups = midlong_backups.copy()
        midlong_backups["action"] = midlong_backups.apply(midlong_action_label, axis=1)

    overlap = sorted(
        set(short_forced.get("ticker", pd.Series(dtype=str)).astype(str))
        & set(midlong_forced.get("ticker", pd.Series(dtype=str)).astype(str))
    )

    short_action_counts = _action_counts(short_forced, "action")
    midlong_action_counts = _action_counts(midlong_forced, "action")
    short_templates = summarize_signal_templates(short_forced)
    midlong_templates = summarize_signal_templates(midlong_forced)
    short_spec_counts = _spec_risk_counts(short_forced)
    midlong_spec_counts = _spec_risk_counts(midlong_forced)

    warnings = compute_reco_warnings(
        short_forced,
        midlong_forced,
        overlap=overlap,
        heuristics=heuristics,
    )
    if short_forced.empty:
        warnings.insert(0, "短線推薦為空：可能條件過嚴或資料不足。")
    if midlong_forced.empty:
        warnings.insert(0, "中線推薦為空：可能條件過嚴或資料不足。")

    improvement_notes = improvement_notes if improvement_notes is not None else list(DEFAULT_IMPROVEMENT_NOTES)
    short_display = apply_signal_template_labels(short_forced)
    midlong_display = apply_signal_template_labels(midlong_forced)
    short_spec_watch = _spec_risk_watch_rows(short_display)
    midlong_spec_watch = _spec_risk_watch_rows(midlong_display)

    lines: list[str] = [
        "# Recommendation Verification (pre-09:00 best-effort)",
        f"- Generated: {now_local.strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"- As-of market date: {asof_date}",
        f"- Source: {source}",
        f"- Forced top N: short={top_n_short} midlong={top_n_midlong}",
        f"- Notify config: short={CONFIG.notify.top_n_short} midlong={CONFIG.notify.top_n_midlong}",
        "",
        "## Summary",
        f"- Short pool size: {len(short_pool)} | forced: {len(short_forced)} | ok: {int((short_forced.get('reco_status','')=='ok').sum()) if not short_forced.empty else 0} | backups: {len(short_backups)}",
        f"- Midlong pool size: {len(midlong_pool)} | forced: {len(midlong_forced)} | ok: {int((midlong_forced.get('reco_status','')=='ok').sum()) if not midlong_forced.empty else 0} | backups: {len(midlong_backups)}",
        f"- Overlap candidates: {len(overlap)}",
        "",
        "## Warnings",
    ]
    if warnings:
        lines.extend([f"- {w}" for w in warnings])
    else:
        lines.append("- None")

    lines.extend(
        [
            "",
            "## Short-Term Candidates",
            _format_table(
                short_display,
                [
                    "rank",
                    "ticker",
                    "name",
                    "grade",
                    "setup_score",
                    "risk_score",
                    "spec_risk_score",
                    "spec_risk_label",
                    "spec_risk_subtype",
                    "ret5_pct",
                    "ret20_pct",
                    "volume_ratio20",
                    "signals",
                    "signal_template",
                    "action",
                    "reco_status",
                ],
            ).rstrip(),
            "",
            "## Mid-Long Candidates",
            _format_table(
                midlong_display,
                [
                    "rank",
                    "ticker",
                    "name",
                    "grade",
                    "setup_score",
                    "risk_score",
                    "spec_risk_score",
                    "spec_risk_label",
                    "spec_risk_subtype",
                    "ret5_pct",
                    "ret20_pct",
                    "volume_ratio20",
                    "signals",
                    "signal_template",
                    "action",
                    "reco_status",
                ],
            ).rstrip(),
            "",
            "## Diagnostics",
            f"- Short action counts: {short_action_counts or '{}'}",
            f"- Midlong action counts: {midlong_action_counts or '{}'}",
            f"- Short signal templates: {short_templates or '{}'}",
            f"- Midlong signal templates: {midlong_templates or '{}'}",
            f"- Short spec risk counts: {short_spec_counts or '{}'}",
            f"- Midlong spec risk counts: {midlong_spec_counts or '{}'}",
            "",
        ]
    )

    if not short_spec_watch.empty or not midlong_spec_watch.empty:
        lines.extend(
            [
                "## Spec Risk Watchlist",
                "",
            ]
        )
        if not short_spec_watch.empty:
            lines.extend(
                [
                    "### Short",
                    _format_table(
                        short_spec_watch.head(10),
                        [
                            "rank",
                            "ticker",
                            "name",
                            "spec_risk_score",
                            "spec_risk_label",
                            "spec_risk_note",
                            "ret5_pct",
                            "ret20_pct",
                            "signals",
                            "signal_template",
                            "action",
                        ],
                    ).rstrip(),
                    "",
                ]
            )
        if not midlong_spec_watch.empty:
            lines.extend(
                [
                    "### Midlong",
                    _format_table(
                        midlong_spec_watch.head(10),
                        [
                            "rank",
                            "ticker",
                            "name",
                            "spec_risk_score",
                            "spec_risk_label",
                            "spec_risk_note",
                            "ret5_pct",
                            "ret20_pct",
                            "signals",
                            "signal_template",
                            "action",
                        ],
                    ).rstrip(),
                    "",
                ]
            )

    lines.extend(_render_notes_section("## Improvement Notes (heuristic)", improvement_notes))
    lines.append("")
    if codex_context is not None:
        lines.extend(
            [
                "## Codex Context (JSON)",
                "```json",
                json.dumps(codex_context, ensure_ascii=False, indent=2),
                "```",
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Best-effort verification for daily recommendations.")
    parser.add_argument("--rank-csv", default=str(THEME_OUTDIR / "daily_rank.csv"))
    out_dir = VERIFICATION_OUTDIR
    parser.add_argument("--out", default=str(out_dir / "verification_report.md"))
    parser.add_argument("--snapshot-csv", default=str(out_dir / "reco_snapshots.csv"))
    parser.add_argument("--top-n-short", type=int, default=5, help="Force this many short recommendations into snapshot/report.")
    parser.add_argument("--top-n-midlong", type=int, default=5, help="Force this many midlong recommendations into snapshot/report.")
    parser.add_argument("--no-snapshot", action="store_true", help="Do not append recommendation snapshots to CSV.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    rank_csv = Path(args.rank_csv)
    out_path = Path(args.out)
    snapshot_csv = Path(args.snapshot_csv)

    if not rank_csv.exists():
        report = build_verification_report_markdown(
            pd.DataFrame(),
            source=str(rank_csv),
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(report, encoding="utf-8")
        print(report)
        return 0

    df_rank = pd.read_csv(rank_csv)
    now_local = datetime.now(LOCAL_TZ)
    try:
        market_regime = get_market_regime()
        us_market = get_us_market_reference()
        scenario_label = str(build_market_scenario(market_regime, us_market, df_rank).get("label", "unknown"))
    except Exception:
        scenario_label = "unknown"

    short_forced = select_forced_recommendations(df_rank, watch_type="short", top_n=int(args.top_n_short)) if not df_rank.empty else df_rank.head(0).copy()
    midlong_forced = select_forced_recommendations(df_rank, watch_type="midlong", top_n=int(args.top_n_midlong)) if not df_rank.empty else df_rank.head(0).copy()
    short_backups = select_short_term_backup_candidates(
        df_rank,
        exclude_tickers=set(short_forced["ticker"].astype(str)) if not short_forced.empty else None,
    ) if not df_rank.empty else df_rank.head(0).copy()
    midlong_backups = select_midlong_backup_candidates(
        df_rank,
        exclude_tickers=set(midlong_forced["ticker"].astype(str)) if not midlong_forced.empty else None,
    ) if not df_rank.empty else df_rank.head(0).copy()

    if not short_backups.empty:
        short_backups = short_backups.copy()
        short_backups["action"] = short_backups.apply(short_term_action_label, axis=1)
        short_backups["reco_status"] = ""
    if not midlong_backups.empty:
        midlong_backups = midlong_backups.copy()
        midlong_backups["action"] = midlong_backups.apply(midlong_action_label, axis=1)
        midlong_backups["reco_status"] = ""

    overlap = sorted(
        set(short_forced.get("ticker", pd.Series(dtype=str)).astype(str))
        & set(midlong_forced.get("ticker", pd.Series(dtype=str)).astype(str))
    )

    heuristics = VerificationHeuristics()
    warnings = compute_reco_warnings(
        short_forced,
        midlong_forced,
        overlap=overlap,
        heuristics=heuristics,
    )

    codex_context = build_codex_context(
        df_rank=df_rank,
        source=str(rank_csv),
        now_local=now_local,
        top_n_short=int(args.top_n_short),
        top_n_midlong=int(args.top_n_midlong),
        warnings=warnings,
        overlap=overlap,
        short_forced=short_forced,
        midlong_forced=midlong_forced,
        short_backups=short_backups,
        midlong_backups=midlong_backups,
    )
    latest_ctx_path, dated_ctx_path = write_codex_context_files(codex_context)

    report = build_verification_report_markdown(
        df_rank,
        source=str(rank_csv),
        now_local=now_local,
        top_n_short=int(args.top_n_short),
        top_n_midlong=int(args.top_n_midlong),
        improvement_notes=list(DEFAULT_IMPROVEMENT_NOTES),
        codex_context=codex_context,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    print(report)
    print(f"\nCodex context written: {latest_ctx_path} (latest), {dated_ctx_path} (dated)")

    if not args.no_snapshot:
        asof_date = _maybe_date_from_rank(df_rank)
        short_forced = short_forced.copy()
        midlong_forced = midlong_forced.copy()
        if not short_forced.empty:
            short_forced["watch_type"] = "short"
        if not midlong_forced.empty:
            midlong_forced["watch_type"] = "midlong"

        combined = pd.concat([short_forced, midlong_forced], ignore_index=True)
        if not combined.empty:
            combined = combined.copy()
            combined["generated_at"] = now_local.strftime("%Y-%m-%d %H:%M:%S %Z")
            combined["signal_date"] = asof_date
            combined["source"] = str(rank_csv)
            combined["source_sha"] = ""
            combined["scenario_label"] = scenario_label
            keep = [
                "generated_at",
                "signal_date",
                "source",
                "source_sha",
                "scenario_label",
                "watch_type",
                "rank",
                "ticker",
                "name",
                "grade",
                "setup_score",
                "risk_score",
                "spec_risk_score",
                "spec_risk_label",
                "spec_risk_subtype",
                "spec_risk_note",
                "ret5_pct",
                "ret20_pct",
                "volume_ratio20",
                "signals",
                "action",
                "reco_status",
            ]
            combined = combined[[c for c in keep if c in combined.columns]].copy()
            upsert_csv_with_existing_header(
                snapshot_csv,
                combined,
                key_cols=["signal_date", "watch_type", "ticker"],
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
