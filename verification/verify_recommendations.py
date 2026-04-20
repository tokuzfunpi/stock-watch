from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from daily_theme_watchlist import (
    LOCAL_TZ,
    CONFIG,
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


@dataclass(frozen=True)
class VerificationHeuristics:
    warn_overheated_ret5_pct: float = 18.0
    warn_high_risk_score: int = 5
    warn_low_volume_ratio: float = 0.9


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


def _format_table(df: pd.DataFrame, cols: list[str]) -> str:
    if df.empty:
        return "_None_\n"
    view = df[cols].copy()
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


def _maybe_date_from_rank(df_rank: pd.DataFrame) -> str:
    if "date" not in df_rank.columns or df_rank.empty:
        return datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")
    values = df_rank["date"].dropna().astype(str).tolist()
    return values[-1] if values else datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")


def build_verification_report_markdown(
    df_rank: pd.DataFrame,
    *,
    source: str,
    now_local: datetime | None = None,
    heuristics: VerificationHeuristics | None = None,
    top_n_short: int = 5,
    top_n_midlong: int = 5,
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

    warnings: list[str] = []
    if short_forced.empty:
        warnings.append("短線推薦為空：可能條件過嚴或資料不足。")
    if midlong_forced.empty:
        warnings.append("中線推薦為空：可能條件過嚴或資料不足。")
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

    _scan_below_threshold(short_forced, "短線推薦")
    _scan_below_threshold(midlong_forced, "中線推薦")
    _scan_overheated(short_forced, "短線推薦")
    _scan_overheated(midlong_forced, "中線推薦")
    _scan_liquidity(short_forced, "短線推薦")
    _scan_liquidity(midlong_forced, "中線推薦")

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
                short_forced,
                [
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
                    "reco_status",
                ],
            ).rstrip(),
            "",
            "## Mid-Long Candidates",
            _format_table(
                midlong_forced,
                [
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
                    "reco_status",
                ],
            ).rstrip(),
            "",
            "## Diagnostics",
            f"- Short action counts: {short_action_counts or '{}'}",
            f"- Midlong action counts: {midlong_action_counts or '{}'}",
            "",
            "## Improvement Notes (heuristic)",
            "- 若短線多為「開高不追 / 只觀察不追」，可考慮降低 `top_n_short` 或收斂追價條件。",
            "- 若中線多為「分批落袋」，代表偏後段；可考慮提升趨勢訊號權重或降低過熱門檻。",
            "- 若短/中線重疊過多，可考慮讓短線池排除 `midlong_core` 或在推播層做去重。",
            "",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Best-effort verification for daily recommendations.")
    parser.add_argument("--rank-csv", default=str(Path("theme_watchlist_daily") / "daily_rank.csv"))
    out_dir = Path("verification") / "watchlist_daily"
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
    report = build_verification_report_markdown(
        df_rank,
        source=str(rank_csv),
        now_local=now_local,
        top_n_short=int(args.top_n_short),
        top_n_midlong=int(args.top_n_midlong),
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    print(report)

    if not args.no_snapshot:
        asof_date = _maybe_date_from_rank(df_rank)
        short_forced = select_forced_recommendations(df_rank, watch_type="short", top_n=int(args.top_n_short)).copy()
        midlong_forced = select_forced_recommendations(df_rank, watch_type="midlong", top_n=int(args.top_n_midlong)).copy()
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
                "reco_status",
            ]
            combined = combined[[c for c in keep if c in combined.columns]].copy()
            append_csv_with_existing_header(snapshot_csv, combined)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
