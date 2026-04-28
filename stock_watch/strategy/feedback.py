from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pandas as pd

from stock_watch.strategy import candidates


def history_target_return(row: pd.Series) -> tuple[float | None, str]:
    watch_type = str(row.get("watch_type", ""))
    if watch_type == "short":
        for col, label in [("ret5_future_pct", "5D"), ("ret1_future_pct", "1D")]:
            value = row.get(col)
            if pd.notna(value):
                return float(value), label
    if watch_type == "midlong":
        for col, label in [("ret20_future_pct", "20D"), ("ret5_future_pct", "5D"), ("ret1_future_pct", "1D")]:
            value = row.get(col)
            if pd.notna(value):
                return float(value), label
    return None, ""


def feedback_action_label(row: pd.Series, watch_type: str) -> str:
    if watch_type == "short":
        return candidates.short_term_action_label(row)
    return candidates.midlong_action_label(row)


def feedback_label_from_score(score: float, samples: int) -> str:
    if samples < 3:
        return "樣本不足"
    if score >= 1.2:
        return "近期有效"
    if score <= -1.2:
        return "近期偏弱"
    return "中性"


def feedback_window_size(watch_type: str) -> int:
    return 12 if watch_type == "short" else 8


def compute_feedback_score_components(
    returns: pd.Series,
    sample_scale: int,
    use_weights: bool = False,
) -> dict[str, float]:
    if returns.empty:
        return {
            "win_rate_pct": 0.0,
            "avg_return_pct": 0.0,
            "avg_win_return_pct": 0.0,
            "avg_loss_return_pct": 0.0,
            "pl_ratio": 0.0,
            "feedback_score": 0.0,
        }

    working = returns.astype(float).reset_index(drop=True)
    weights = pd.Series([1.0] * len(working))
    if use_weights and len(working) > 1:
        floor = 0.65
        step = (1.0 - floor) / max(len(working) - 1, 1)
        weights = pd.Series([1.0 - (step * i) for i in range(len(working))])

    positive_mask = working > 0
    negative_mask = ~positive_mask
    positive = working[positive_mask]
    negative = working[negative_mask]
    positive_weights = weights[positive_mask]
    negative_weights = weights[negative_mask]

    total_weight = float(weights.sum()) or 1.0
    win_rate_pct = round(float(weights[positive_mask].sum() / total_weight) * 100, 2)
    avg_return_pct = round(float((working * weights).sum() / total_weight), 2)
    avg_win_return_pct = round(float((positive * positive_weights).sum() / positive_weights.sum()), 2) if not positive.empty else 0.0
    avg_loss_return_pct = round(float((negative * negative_weights).sum() / negative_weights.sum()), 2) if not negative.empty else 0.0
    gross_win = float((positive * positive_weights).sum()) if not positive.empty else 0.0
    gross_loss = abs(float((negative * negative_weights).sum())) if not negative.empty else 0.0
    pl_ratio = round(gross_win / gross_loss, 2) if gross_loss > 0 else (round(gross_win, 2) if gross_win > 0 else 0.0)
    shrink = min(sample_scale / 8.0, 1.0)
    pl_ratio_capped = min(max(pl_ratio, 0.0), 4.0)
    pl_ratio_component = (pl_ratio_capped - 1.0) / 4.0
    feedback_score = round(
        (
            ((win_rate_pct - 50.0) / 10.0)
            + (avg_return_pct / 5.0)
            + pl_ratio_component
        )
        * shrink,
        2,
    )
    return {
        "win_rate_pct": win_rate_pct,
        "avg_return_pct": avg_return_pct,
        "avg_win_return_pct": avg_win_return_pct,
        "avg_loss_return_pct": avg_loss_return_pct,
        "pl_ratio": pl_ratio,
        "feedback_score": feedback_score,
    }


def build_feedback_summary(alert_track_csv: Path, feedback_summary_csv: Path) -> pd.DataFrame:
    if not alert_track_csv.exists():
        return pd.DataFrame()
    try:
        hist = pd.read_csv(alert_track_csv)
    except Exception:
        return pd.DataFrame()
    if hist.empty or "watch_type" not in hist.columns:
        return pd.DataFrame()

    rows = []
    working = hist.copy()
    for watch_type in ["short", "midlong"]:
        subset = working[working["watch_type"].astype(str) == watch_type].copy()
        if subset.empty:
            continue
        if "action_label" not in subset.columns:
            subset["action_label"] = ""
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
            recent_window = feedback_window_size(watch_type)
            recent_df = action_df.head(recent_window).copy()
            recent_samples = int(recent_df.shape[0])
            recent_metrics = compute_feedback_score_components(
                recent_df["target_return"],
                sample_scale=recent_samples,
                use_weights=True,
            )
            feedback_score = round(
                (base_metrics["feedback_score"] * 0.7) + (recent_metrics["feedback_score"] * 0.3),
                2,
            )
            rows.append(
                {
                    "watch_type": watch_type,
                    "action_label": action_label,
                    "samples": samples,
                    "recent_samples": recent_samples,
                    "win_rate_pct": base_metrics["win_rate_pct"],
                    "avg_return_pct": base_metrics["avg_return_pct"],
                    "avg_win_return_pct": base_metrics["avg_win_return_pct"],
                    "avg_loss_return_pct": base_metrics["avg_loss_return_pct"],
                    "pl_ratio": base_metrics["pl_ratio"],
                    "recent_win_rate_pct": recent_metrics["win_rate_pct"],
                    "recent_avg_return_pct": recent_metrics["avg_return_pct"],
                    "recent_pl_ratio": recent_metrics["pl_ratio"],
                    "base_feedback_score": base_metrics["feedback_score"],
                    "recent_feedback_score": recent_metrics["feedback_score"],
                    "feedback_score": feedback_score,
                    "feedback_label": feedback_label_from_score(feedback_score, samples),
                }
            )
    summary = pd.DataFrame(rows)
    if not summary.empty:
        summary.to_csv(feedback_summary_csv, index=False, encoding="utf-8-sig")
    return summary


def feedback_score_lookup(summary: pd.DataFrame, watch_type: str, action_label: str) -> tuple[float, str, float]:
    if summary is None or summary.empty:
        return 0.0, "樣本不足", 0.0
    exact = summary[
        (summary["watch_type"].astype(str) == watch_type)
        & (summary["action_label"].astype(str) == action_label)
    ]
    if not exact.empty:
        row = exact.iloc[0]
        return float(row["feedback_score"]), str(row["feedback_label"]), float(row.get("pl_ratio", 0.0) or 0.0)
    fallback = summary[
        (summary["watch_type"].astype(str) == watch_type)
        & (summary["action_label"].astype(str) == "__all__")
    ]
    if not fallback.empty:
        row = fallback.iloc[0]
        return float(row["feedback_score"]), str(row["feedback_label"]), float(row.get("pl_ratio", 0.0) or 0.0)
    return 0.0, "樣本不足", 0.0


def apply_feedback_adjustment(
    df: pd.DataFrame,
    watch_type: str,
    *,
    summary: pd.DataFrame | None = None,
    action_label_func: Callable[[pd.Series, str], str] = feedback_action_label,
) -> pd.DataFrame:
    if df.empty:
        return df
    if summary is None:
        summary = pd.DataFrame()
    out = df.copy().reset_index(drop=True)
    out["_base_order"] = range(len(out))
    out["action_label"] = out.apply(lambda row: action_label_func(row, watch_type), axis=1)
    lookups = out["action_label"].apply(lambda action: feedback_score_lookup(summary, watch_type, action))
    out["feedback_score"] = [score for score, _, _ in lookups]
    out["feedback_label"] = [label for _, label, _ in lookups]
    out["feedback_pl_ratio"] = [pl_ratio for _, _, pl_ratio in lookups]
    out = out.sort_values(
        by=["feedback_score", "feedback_pl_ratio", "_base_order"],
        ascending=[False, False, True],
        kind="mergesort",
    ).reset_index(drop=True)
    return out.drop(columns=["_base_order"])
