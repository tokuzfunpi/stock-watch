from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from daily_theme_watchlist import (
    ALERT_TRACK_CSV,
    LOCAL_TZ,
    compute_feedback_score_components,
    feedback_label_from_score,
    feedback_window_size,
    history_target_return,
)


@dataclass(frozen=True)
class WeightConfig:
    name: str
    base_weight: float
    recent_weight: float


def parse_weight_configs(raw: str) -> list[WeightConfig]:
    configs: list[WeightConfig] = []
    for item in [part.strip() for part in raw.split(",") if part.strip()]:
        if ":" not in item:
            raise ValueError(f"Invalid weight config: {item}")
        base_raw, recent_raw = item.split(":", 1)
        base_weight = float(base_raw) / 100.0
        recent_weight = float(recent_raw) / 100.0
        total = round(base_weight + recent_weight, 6)
        if total <= 0:
            raise ValueError(f"Invalid zero-sum weight config: {item}")
        base_weight = base_weight / total
        recent_weight = recent_weight / total
        configs.append(
            WeightConfig(
                name=f"{int(round(base_weight * 100))}/{int(round(recent_weight * 100))}",
                base_weight=base_weight,
                recent_weight=recent_weight,
            )
        )
    if not configs:
        raise ValueError("No weight configs provided")
    return configs


def build_feedback_summary_for_weights(hist: pd.DataFrame, config: WeightConfig) -> pd.DataFrame:
    if hist.empty or "watch_type" not in hist.columns:
        return pd.DataFrame()

    rows: list[dict[str, object]] = []
    working = hist.copy()
    for watch_type in ["short", "midlong"]:
        subset = working[working["watch_type"].astype(str) == watch_type].copy()
        if subset.empty:
            continue
        if "action_label" not in subset.columns:
            subset["action_label"] = ""
        subset["action_label"] = subset["action_label"].astype(str).str.strip()
        subset.loc[
            (subset["action_label"] == "")
            | (subset["action_label"] == "nan")
            | (subset["action_label"] == "None"),
            "action_label",
        ] = "unknown"
        subset["alert_date"] = pd.to_datetime(subset.get("alert_date"), errors="coerce")
        subset["target_return"] = subset.apply(lambda row: history_target_return(row)[0], axis=1)
        subset = subset[subset["target_return"].notna()].copy()
        if subset.empty:
            continue
        subset = subset.sort_values("alert_date", ascending=False, kind="mergesort").reset_index(drop=True)

        for action_label in ["__all__"] + sorted(set(subset["action_label"].astype(str))):
            action_df = subset if action_label == "__all__" else subset[subset["action_label"].astype(str) == action_label].copy()
            if action_df.empty:
                continue
            samples = int(action_df.shape[0])
            base_metrics = compute_feedback_score_components(
                action_df["target_return"],
                sample_scale=samples,
                use_weights=False,
            )
            recent_df = action_df.head(feedback_window_size(watch_type)).copy()
            recent_samples = int(recent_df.shape[0])
            recent_metrics = compute_feedback_score_components(
                recent_df["target_return"],
                sample_scale=recent_samples,
                use_weights=True,
            )
            feedback_score = round(
                (base_metrics["feedback_score"] * config.base_weight)
                + (recent_metrics["feedback_score"] * config.recent_weight),
                2,
            )
            rows.append(
                {
                    "config_name": config.name,
                    "base_weight_pct": round(config.base_weight * 100, 1),
                    "recent_weight_pct": round(config.recent_weight * 100, 1),
                    "watch_type": watch_type,
                    "action_label": action_label,
                    "samples": samples,
                    "recent_samples": recent_samples,
                    "base_feedback_score": base_metrics["feedback_score"],
                    "recent_feedback_score": recent_metrics["feedback_score"],
                    "feedback_score": feedback_score,
                    "feedback_label": feedback_label_from_score(feedback_score, samples),
                    "pl_ratio": base_metrics["pl_ratio"],
                }
            )

    summary = pd.DataFrame(rows)
    if summary.empty:
        return summary

    rank_base = summary[summary["action_label"] != "__all__"].copy()
    if rank_base.empty:
        summary["rank"] = pd.NA
        return summary

    rank_base["rank"] = (
        rank_base.groupby(["config_name", "watch_type"], dropna=False)["feedback_score"]
        .rank(method="dense", ascending=False)
        .astype("Int64")
    )
    summary = summary.merge(
        rank_base[["config_name", "watch_type", "action_label", "rank"]],
        on=["config_name", "watch_type", "action_label"],
        how="left",
    )
    return summary.sort_values(by=["config_name", "watch_type", "rank", "action_label"], na_position="last").reset_index(drop=True)


def compare_weight_configs(summary: pd.DataFrame, baseline_name: str) -> pd.DataFrame:
    if summary.empty:
        return pd.DataFrame()

    actions = summary[summary["action_label"] != "__all__"].copy()
    baseline = actions[actions["config_name"] == baseline_name].copy()
    if baseline.empty:
        return pd.DataFrame()
    baseline = baseline.rename(
        columns={
            "feedback_score": "baseline_score",
            "rank": "baseline_rank",
            "feedback_label": "baseline_label",
        }
    )
    merged = actions.merge(
        baseline[["watch_type", "action_label", "baseline_score", "baseline_rank", "baseline_label"]],
        on=["watch_type", "action_label"],
        how="left",
    )
    merged["score_delta"] = (pd.to_numeric(merged["feedback_score"], errors="coerce") - pd.to_numeric(merged["baseline_score"], errors="coerce")).round(2)
    merged["rank_delta"] = pd.to_numeric(merged["baseline_rank"], errors="coerce") - pd.to_numeric(merged["rank"], errors="coerce")
    return merged.sort_values(by=["watch_type", "config_name", "rank"], na_position="last").reset_index(drop=True)


def _table_markdown(df: pd.DataFrame) -> str:
    if df.empty:
        return "_None_\n"
    headers = [str(col) for col in df.columns.tolist()]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for _, row in df.iterrows():
        items: list[str] = []
        for col in headers:
            value = row.get(col)
            items.append("" if pd.isna(value) else str(value).replace("|", "\\|").replace("\n", " "))
        lines.append("| " + " | ".join(items) + " |")
    return "\n".join(lines) + "\n"


def build_findings(compare_df: pd.DataFrame, baseline_name: str) -> list[str]:
    if compare_df.empty:
        return ["目前沒有可比較的 action 樣本。"]

    findings: list[str] = []
    non_baseline = compare_df[compare_df["config_name"] != baseline_name].copy()
    if non_baseline.empty:
        return findings

    movers = non_baseline[non_baseline["rank_delta"].notna()].copy()
    movers = movers[movers["rank_delta"] != 0].copy()
    if not movers.empty:
        movers["_abs_rank_delta"] = pd.to_numeric(movers["rank_delta"], errors="coerce").abs()
        top_move = movers.sort_values(by=["_abs_rank_delta", "score_delta"], ascending=[False, False]).iloc[0]
        findings.append(
            f"`{top_move['watch_type']}` 的 `{top_move['action_label']}` 在 `{top_move['config_name']}` 相較基準 `{baseline_name}` 排名變動最大，"
            f"`rank_delta={int(top_move['rank_delta'])}`、`score_delta={top_move['score_delta']}`。"
        )

    score_shift = non_baseline.copy()
    score_shift["_abs_score_delta"] = pd.to_numeric(score_shift["score_delta"], errors="coerce").abs()
    score_shift = score_shift.sort_values(by=["_abs_score_delta"], ascending=[False])
    if not score_shift.empty:
        top_score = score_shift.iloc[0]
        findings.append(
            f"`{top_score['config_name']}` 對 `{top_score['watch_type']} / {top_score['action_label']}` 的分數影響最大，"
            f"`score_delta={top_score['score_delta']}`、基準分數 `{top_score['baseline_score']}`。"
        )

    stable = (
        non_baseline.groupby(["watch_type", "action_label"], dropna=False)["rank_delta"]
        .apply(lambda s: int(pd.to_numeric(s, errors="coerce").fillna(0).abs().sum()))
        .reset_index(name="total_rank_shift")
    )
    stable = stable.sort_values(by=["total_rank_shift", "watch_type", "action_label"], ascending=[True, True, True])
    if not stable.empty:
        most_stable = stable.iloc[0]
        findings.append(
            f"`{most_stable['watch_type']} / {most_stable['action_label']}` 對不同權重最穩定，總排名位移 `{int(most_stable['total_rank_shift'])}`。"
        )
    return findings


def build_markdown(
    summary: pd.DataFrame,
    compare_df: pd.DataFrame,
    baseline_name: str,
    source: str,
    now_local: datetime | None = None,
) -> str:
    now_local = now_local or datetime.now(LOCAL_TZ)
    lines = [
        "# Feedback Weight Sensitivity",
        f"- Generated: {now_local.strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"- Source: {source}",
        f"- Baseline: `{baseline_name}`",
        "",
    ]

    if summary.empty:
        lines.extend(["_No usable feedback history rows._", ""])
        return "\n".join(lines)

    lines.extend(["## Findings"])
    lines.extend([f"- {item}" for item in build_findings(compare_df, baseline_name)])
    lines.append("")

    overview = (
        summary.groupby(["config_name", "watch_type"], dropna=False)
        .agg(actions=("action_label", lambda s: int((s.astype(str) != "__all__").sum())))
        .reset_index()
    )
    lines.extend(["## Coverage", _table_markdown(overview).rstrip(), ""])

    score_table = summary[summary["action_label"] != "__all__"][
        ["config_name", "watch_type", "action_label", "rank", "feedback_score", "feedback_label", "samples", "recent_samples", "pl_ratio"]
    ].copy()
    lines.extend(["## Action Scores", _table_markdown(score_table).rstrip(), ""])

    if not compare_df.empty:
        compare_view = compare_df[
            [
                "config_name",
                "watch_type",
                "action_label",
                "rank",
                "baseline_rank",
                "rank_delta",
                "feedback_score",
                "baseline_score",
                "score_delta",
            ]
        ].copy()
        lines.extend(["## Rank Deltas vs Baseline", _table_markdown(compare_view).rstrip(), ""])
    return "\n".join(lines).strip() + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    out_dir = Path("verification") / "watchlist_daily"
    parser = argparse.ArgumentParser(description="Compare feedback_score sensitivity across base/recent weight splits.")
    parser.add_argument("--alert-csv", default=str(ALERT_TRACK_CSV))
    parser.add_argument("--weights", default="70:30,80:20,60:40")
    parser.add_argument("--out", default=str(out_dir / "feedback_weight_sensitivity.md"))
    parser.add_argument("--csv-out", default=str(out_dir / "feedback_weight_sensitivity.csv"))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    alert_csv = Path(args.alert_csv)
    out_path = Path(args.out)
    csv_out_path = Path(args.csv_out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    csv_out_path.parent.mkdir(parents=True, exist_ok=True)

    if not alert_csv.exists():
        report = build_markdown(pd.DataFrame(), pd.DataFrame(), baseline_name="70/30", source=str(alert_csv))
        out_path.write_text(report, encoding="utf-8")
        csv_out_path.write_text("", encoding="utf-8")
        print(report)
        return 0

    hist = pd.read_csv(alert_csv)
    configs = parse_weight_configs(args.weights)
    frames = [build_feedback_summary_for_weights(hist, config) for config in configs]
    summary = pd.concat([frame for frame in frames if not frame.empty], ignore_index=True) if frames else pd.DataFrame()
    baseline_name = configs[0].name
    compare_df = compare_weight_configs(summary, baseline_name=baseline_name)
    report = build_markdown(summary, compare_df, baseline_name=baseline_name, source=str(alert_csv))
    out_path.write_text(report, encoding="utf-8")
    if not compare_df.empty:
        compare_df.to_csv(csv_out_path, index=False, encoding="utf-8-sig")
    else:
        csv_out_path.write_text("", encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
