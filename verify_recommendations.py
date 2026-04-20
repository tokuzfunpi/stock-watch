from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

from daily_theme_watchlist import (
    LOCAL_TZ,
    CONFIG,
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
) -> str:
    now_local = now_local or datetime.now(LOCAL_TZ)
    heuristics = heuristics or VerificationHeuristics()
    asof_date = _maybe_date_from_rank(df_rank)

    short_pool = rank_short_term_pool(df_rank) if not df_rank.empty else df_rank.head(0).copy()
    midlong_pool = rank_midlong_pool(df_rank) if not df_rank.empty else df_rank.head(0).copy()
    short_candidates = select_short_term_candidates(df_rank) if not df_rank.empty else df_rank.head(0).copy()
    midlong_candidates = select_midlong_candidates(df_rank) if not df_rank.empty else df_rank.head(0).copy()

    short_backups = select_short_term_backup_candidates(
        df_rank,
        exclude_tickers=set(short_candidates["ticker"].astype(str)) if not short_candidates.empty else None,
    ) if not df_rank.empty else df_rank.head(0).copy()
    midlong_backups = select_midlong_backup_candidates(
        df_rank,
        exclude_tickers=set(midlong_candidates["ticker"].astype(str)) if not midlong_candidates.empty else None,
    ) if not df_rank.empty else df_rank.head(0).copy()

    if not short_candidates.empty:
        short_candidates = short_candidates.copy()
        short_candidates["action"] = short_candidates.apply(short_term_action_label, axis=1)
    if not midlong_candidates.empty:
        midlong_candidates = midlong_candidates.copy()
        midlong_candidates["action"] = midlong_candidates.apply(midlong_action_label, axis=1)
    if not short_backups.empty:
        short_backups = short_backups.copy()
        short_backups["action"] = short_backups.apply(short_term_action_label, axis=1)
    if not midlong_backups.empty:
        midlong_backups = midlong_backups.copy()
        midlong_backups["action"] = midlong_backups.apply(midlong_action_label, axis=1)

    overlap = sorted(
        set(short_candidates.get("ticker", pd.Series(dtype=str)).astype(str))
        & set(midlong_candidates.get("ticker", pd.Series(dtype=str)).astype(str))
    )

    short_action_counts = _action_counts(short_candidates, "action")
    midlong_action_counts = _action_counts(midlong_candidates, "action")

    warnings: list[str] = []
    if short_candidates.empty:
        warnings.append("短線推薦為空：可能條件過嚴或資料不足。")
    if midlong_candidates.empty:
        warnings.append("中線推薦為空：可能條件過嚴或資料不足。")
    if overlap:
        warnings.append(f"短線/中線推薦重疊 {len(overlap)} 檔：{', '.join(overlap)}")

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

    _scan_overheated(short_candidates, "短線推薦")
    _scan_overheated(midlong_candidates, "中線推薦")
    _scan_liquidity(short_candidates, "短線推薦")
    _scan_liquidity(midlong_candidates, "中線推薦")

    lines: list[str] = [
        "# Recommendation Verification (pre-09:00 best-effort)",
        f"- Generated: {now_local.strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"- As-of market date: {asof_date}",
        f"- Source: {source}",
        f"- Notify config: short={CONFIG.notify.top_n_short} midlong={CONFIG.notify.top_n_midlong}",
        "",
        "## Summary",
        f"- Short pool size: {len(short_pool)} | candidates: {len(short_candidates)} | backups: {len(short_backups)}",
        f"- Midlong pool size: {len(midlong_pool)} | candidates: {len(midlong_candidates)} | backups: {len(midlong_backups)}",
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
                short_candidates,
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
                ],
            ).rstrip(),
            "",
            "## Mid-Long Candidates",
            _format_table(
                midlong_candidates,
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
    out_dir = Path("watchlist_daily")
    parser.add_argument("--out", default=str(out_dir / "verification_report.md"))
    parser.add_argument("--snapshot-csv", default=str(out_dir / "reco_snapshots.csv"))
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
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    print(report)

    if not args.no_snapshot:
        asof_date = _maybe_date_from_rank(df_rank)
        short_candidates = select_short_term_candidates(df_rank).copy()
        midlong_candidates = select_midlong_candidates(df_rank).copy()
        if not short_candidates.empty:
            short_candidates["watch_type"] = "short"
            short_candidates["action"] = short_candidates.apply(short_term_action_label, axis=1)
        if not midlong_candidates.empty:
            midlong_candidates["watch_type"] = "midlong"
            midlong_candidates["action"] = midlong_candidates.apply(midlong_action_label, axis=1)

        combined = pd.concat([short_candidates, midlong_candidates], ignore_index=True)
        if not combined.empty:
            combined = combined.copy()
            combined["generated_at"] = now_local.strftime("%Y-%m-%d %H:%M:%S %Z")
            combined["signal_date"] = asof_date
            combined["source"] = str(rank_csv)
            keep = [
                "generated_at",
                "signal_date",
                "source",
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
