from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from stock_watch.paths import REPO_ROOT
from stock_watch.paths import THEME_OUTDIR
from stock_watch.paths import VERIFICATION_OUTDIR
from stock_watch.runtime import ALERT_TRACK_CSV, LOCAL_TZ
from stock_watch.strategy.pullback import classify_short_pullback_quality
from stock_watch.strategy.pullback import confirmed_pullback_action_for_quality
from stock_watch.strategy.pullback import confirmed_pullback_guidance_for_quality
from stock_watch.strategy.pullback import confirmed_pullback_position_for_quality
from stock_watch.strategy.pullback import next_session_confirmation_bucket
from stock_watch.strategy.pullback import pullback_action_for_quality
from stock_watch.strategy.pullback import pullback_guidance_for_quality
from stock_watch.strategy.pullback import pullback_position_for_quality
from verification.reports.summarize_outcomes import summarize_atr_band_checkpoints
from verification.reports.summarize_outcomes import summarize_outcomes

VERIFICATION_OUTCOMES_CSV = VERIFICATION_OUTDIR / "reco_outcomes.csv"
VERIFICATION_SNAPSHOTS_CSV = VERIFICATION_OUTDIR / "reco_snapshots.csv"
FEEDBACK_SENSITIVITY_CSV = VERIFICATION_OUTDIR / "feedback_weight_sensitivity.csv"
DAILY_RANK_CSV = THEME_OUTDIR / "daily_rank.csv"
WEEKLY_REVIEW_MD = THEME_OUTDIR / "weekly_review.md"
WEEKLY_REVIEW_JSON = THEME_OUTDIR / "weekly_review.json"
WATCHLIST_CSV = REPO_ROOT / "watchlist.csv"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a weekly decision note from local verification outputs.")
    parser.add_argument("--outcomes-csv", default=str(VERIFICATION_OUTCOMES_CSV))
    parser.add_argument("--snapshots-csv", default=str(VERIFICATION_SNAPSHOTS_CSV))
    parser.add_argument("--feedback-csv", default=str(FEEDBACK_SENSITIVITY_CSV))
    parser.add_argument("--alert-csv", default=str(ALERT_TRACK_CSV))
    parser.add_argument("--rank-csv", default=str(DAILY_RANK_CSV))
    parser.add_argument("--watchlist-csv", default=str(WATCHLIST_CSV))
    parser.add_argument("--out", default=str(WEEKLY_REVIEW_MD))
    parser.add_argument("--json-out", default=str(WEEKLY_REVIEW_JSON))
    parser.add_argument("--max-signal-dates", type=int, default=5, help="Number of latest signal_date values to include.")
    return parser.parse_args(argv)


def _table_markdown(df: pd.DataFrame) -> str:
    if df.empty:
        return "_None_\n"
    headers = [str(c) for c in df.columns.tolist()]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for _, row in df.iterrows():
        values: list[str] = []
        for col in headers:
            val = row.get(col)
            text = "" if pd.isna(val) else str(val)
            values.append(text.replace("|", "\\|").replace("\n", " "))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines) + "\n"


def filter_recent_signal_dates(outcomes: pd.DataFrame, max_signal_dates: int) -> tuple[pd.DataFrame, list[str]]:
    if outcomes.empty or "signal_date" not in outcomes.columns:
        return outcomes.head(0).copy(), []
    dates = outcomes["signal_date"].dropna().astype(str).str.strip()
    dates = sorted([d for d in dates.unique().tolist() if d])
    if max_signal_dates > 0:
        dates = dates[-max_signal_dates:]
    recent = outcomes[outcomes["signal_date"].astype(str).isin(dates)].copy()
    return recent, dates


def _spec_risk_bucket(df: pd.DataFrame) -> pd.Series:
    if df.empty:
        return pd.Series(dtype=str)
    score = pd.to_numeric(df.get("spec_risk_score"), errors="coerce")
    label = df.get("spec_risk_label", pd.Series(index=df.index, dtype=object)).fillna("").astype(str).str.strip()
    bucket = pd.Series("normal", index=df.index, dtype=object)
    bucket[(score >= 3) | label.isin(["投機偏高", "偏熱", "留意"])] = "watch"
    bucket[(score >= 6) | (label == "疑似炒作風險高")] = "high"
    return bucket.astype(str)


def _signal_set(value: object) -> set[str]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return set()
    text = str(value).strip()
    if not text or text == "NONE":
        return set()
    return {part.strip() for part in text.split(",") if part.strip()}


def _derive_candidate_source(row: pd.Series) -> str:
    group = str(row.get("group", "") or "").strip()
    layer = str(row.get("layer", "") or "").strip()
    signals = _signal_set(row.get("signals"))
    volume_ratio20 = float(pd.to_numeric(row.get("volume_ratio20"), errors="coerce") or 0.0)
    ret5_pct = float(pd.to_numeric(row.get("ret5_pct"), errors="coerce") or 0.0)
    ret20_pct = float(pd.to_numeric(row.get("ret20_pct"), errors="coerce") or 0.0)
    volatility_tag = str(row.get("volatility_tag", "") or "").strip()

    if group == "etf" or layer == "defensive_watch":
        return "ETF / Defensive carry"
    if layer == "short_attack":
        if "SURGE" in signals or volume_ratio20 >= 2.5 or ret5_pct >= 20:
            return "Theme momentum burst"
        if "ACCEL" in signals or ret20_pct >= 20 or volatility_tag == "活潑":
            return "Theme trend acceleration"
        return "Theme rotation candidates"
    if group == "satellite":
        if ret20_pct >= 30 or volatility_tag == "活潑":
            return "Satellite high-beta leaders"
        return "Satellite quality breakouts"
    if group == "core":
        if "TREND" in signals or "REBREAK" in signals:
            return "Core trend compounders"
        return "Core steady follow-through"
    if layer == "midlong_core":
        if "TREND" in signals or "REBREAK" in signals:
            return "Midlong quality trend"
        return "Midlong rerating candidates"
    return "General watchlist candidates"


def build_rank_spec_risk_coverage(rank_csv: Path) -> dict[str, list[dict[str, object]]]:
    empty = {"by_group": [], "by_layer": [], "top_candidates": []}
    if not rank_csv.exists():
        return empty
    try:
        rank = pd.read_csv(rank_csv)
    except Exception:
        return empty
    if rank.empty:
        return empty

    work = rank.copy()
    work["spec_risk_bucket"] = _spec_risk_bucket(work)

    def _coverage_table(col: str) -> pd.DataFrame:
        if col not in work.columns:
            return pd.DataFrame()
        grouped = (
            work.groupby(col, dropna=False)
            .agg(
                total_rows=("ticker", "count"),
                high_rows=("spec_risk_bucket", lambda s: int((s.astype(str) == "high").sum())),
                watch_rows=("spec_risk_bucket", lambda s: int((s.astype(str) == "watch").sum())),
            )
            .reset_index()
        )
        if grouped.empty:
            return grouped
        grouped["non_normal_rows"] = grouped["high_rows"] + grouped["watch_rows"]
        grouped["non_normal_rate_pct"] = ((grouped["non_normal_rows"] / grouped["total_rows"]) * 100).round(1)
        return grouped.sort_values(
            by=["non_normal_rows", "high_rows", "watch_rows", "total_rows", col],
            ascending=[False, False, False, False, True],
        )

    candidates = work[work["spec_risk_bucket"].isin(["high", "watch"])].copy()
    if not candidates.empty:
        if "rank" not in candidates.columns:
            candidates["rank"] = range(1, len(candidates) + 1)
        candidates["_spec_risk_order"] = candidates["spec_risk_bucket"].map({"high": 0, "watch": 1}).fillna(2)
        candidates["_spec_risk_score_num"] = pd.to_numeric(candidates.get("spec_risk_score"), errors="coerce").fillna(0)
        candidates = candidates.sort_values(
            by=["_spec_risk_order", "_spec_risk_score_num", "rank"],
            ascending=[True, False, True],
        )

    by_group = _coverage_table("group")
    by_layer = _coverage_table("layer")

    return {
        "by_group": by_group.to_dict(orient="records"),
        "by_layer": by_layer.to_dict(orient="records"),
        "top_candidates": candidates[
            [
                c
                for c in [
                    "rank",
                    "ticker",
                    "name",
                    "group",
                    "layer",
                    "spec_risk_score",
                    "spec_risk_label",
                    "spec_risk_subtype",
                    "ret5_pct",
                    "ret20_pct",
                ]
                if c in candidates.columns
            ]
        ]
        .head(10)
        .to_dict(orient="records"),
    }


def build_rank_candidate_source_summary(rank_csv: Path) -> dict[str, list[dict[str, object]]]:
    empty = {"by_source": [], "top_sources": []}
    if not rank_csv.exists():
        return empty
    try:
        rank = pd.read_csv(rank_csv)
    except Exception:
        return empty
    if rank.empty:
        return empty

    work = rank.copy()
    work["spec_risk_bucket"] = _spec_risk_bucket(work)
    work["candidate_source"] = work.apply(_derive_candidate_source, axis=1)

    grouped = (
        work.groupby("candidate_source", dropna=False)
        .agg(
            total_rows=("ticker", "count"),
            high_rows=("spec_risk_bucket", lambda s: int((s.astype(str) == "high").sum())),
            watch_rows=("spec_risk_bucket", lambda s: int((s.astype(str) == "watch").sum())),
        )
        .reset_index()
    )
    if not grouped.empty:
        grouped["non_normal_rows"] = grouped["high_rows"] + grouped["watch_rows"]
        grouped["non_normal_rate_pct"] = ((grouped["non_normal_rows"] / grouped["total_rows"]) * 100).round(1)
        grouped = grouped.sort_values(
            by=["non_normal_rows", "high_rows", "non_normal_rate_pct", "total_rows", "candidate_source"],
            ascending=[False, False, False, False, True],
        )

    top_sources = grouped[grouped["non_normal_rows"] > 0].head(3) if not grouped.empty else grouped
    return {
        "by_source": grouped.to_dict(orient="records"),
        "top_sources": top_sources.to_dict(orient="records") if top_sources is not None else [],
    }


def build_rank_coverage_guidance(rank_spec_coverage: dict[str, list[dict[str, object]]]) -> dict[str, object]:
    by_group = pd.DataFrame(rank_spec_coverage.get("by_group", []))
    by_layer = pd.DataFrame(rank_spec_coverage.get("by_layer", []))

    def _to_numeric(df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        work = df.copy()
        for col in ["total_rows", "high_rows", "watch_rows", "non_normal_rows", "non_normal_rate_pct"]:
            if col in work.columns:
                work[col] = pd.to_numeric(work[col], errors="coerce").fillna(0)
        return work

    by_group = _to_numeric(by_group)
    by_layer = _to_numeric(by_layer)

    def _focus_items(df: pd.DataFrame, key: str) -> list[dict[str, object]]:
        if df.empty or key not in df.columns:
            return []
        focus = df[df["non_normal_rows"] > 0].copy()
        if focus.empty:
            return []
        focus = focus.sort_values(
            by=["non_normal_rows", "high_rows", "non_normal_rate_pct", "total_rows", key],
            ascending=[False, False, False, False, True],
        )
        return focus[[c for c in [key, "non_normal_rows", "high_rows", "non_normal_rate_pct"] if c in focus.columns]].head(2).to_dict(orient="records")

    def _deprioritize_items(df: pd.DataFrame, key: str) -> list[dict[str, object]]:
        if df.empty or key not in df.columns:
            return []
        cold = df[df["non_normal_rows"] <= 0].copy()
        if cold.empty:
            return []
        cold = cold.sort_values(by=["total_rows", key], ascending=[False, True])
        return cold[[c for c in [key, "total_rows"] if c in cold.columns]].head(2).to_dict(orient="records")

    focus_groups = _focus_items(by_group, "group")
    focus_layers = _focus_items(by_layer, "layer")
    deprioritize_groups = _deprioritize_items(by_group, "group")
    deprioritize_layers = _deprioritize_items(by_layer, "layer")

    notes: list[str] = []
    if focus_groups:
        focus_group_names = ", ".join(
            f"{row['group']} ({int(row['non_normal_rows'])} rows, {float(row['non_normal_rate_pct']):.1f}%)"
            for row in focus_groups
        )
        notes.append(f"If we expand the candidate pool for more spec-risk evidence, prioritize groups like {focus_group_names}.")
    if focus_layers:
        focus_layer_names = ", ".join(
            f"{row['layer']} ({int(row['non_normal_rows'])} rows, {float(row['non_normal_rate_pct']):.1f}%)"
            for row in focus_layers
        )
        notes.append(f"Within the current ranking stack, layers like {focus_layer_names} are producing the most non-normal spec-risk rows.")
    if deprioritize_groups or deprioritize_layers:
        cold_parts: list[str] = []
        if deprioritize_groups:
            cold_parts.append("groups " + ", ".join(str(row["group"]) for row in deprioritize_groups))
        if deprioritize_layers:
            cold_parts.append("layers " + ", ".join(str(row["layer"]) for row in deprioritize_layers))
        notes.append(
            "Do not broaden low-yield areas just to raise counts; "
            + " and ".join(cold_parts)
            + " are currently contributing little or no non-normal spec-risk coverage."
        )
    if not notes:
        notes.append("Current rank coverage is still too thin to recommend a candidate-pool expansion yet.")

    return {
        "focus_groups": focus_groups,
        "focus_layers": focus_layers,
        "deprioritize_groups": deprioritize_groups,
        "deprioritize_layers": deprioritize_layers,
        "notes": notes,
    }


def build_candidate_expansion_plan(rank_spec_coverage: dict[str, list[dict[str, object]]]) -> dict[str, list[dict[str, object]]]:
    def _prepare(df: pd.DataFrame, key: str) -> pd.DataFrame:
        if df.empty or key not in df.columns:
            return pd.DataFrame()
        work = df.copy()
        for col in ["total_rows", "high_rows", "watch_rows", "non_normal_rows", "non_normal_rate_pct"]:
            if col in work.columns:
                work[col] = pd.to_numeric(work[col], errors="coerce").fillna(0)
        work = work[work["non_normal_rows"] > 0].copy()
        if work.empty:
            return work

        target_additions = pd.Series(0, index=work.index, dtype=float)
        target_additions += (work["non_normal_rows"] >= 3).astype(int)
        target_additions += (work["non_normal_rows"] >= 5).astype(int)
        target_additions += (work["non_normal_rate_pct"] >= 30).astype(int)
        target_additions += (work["non_normal_rate_pct"] >= 50).astype(int)
        work["suggested_additions"] = target_additions.clip(lower=1, upper=3).astype(int)
        work["why"] = (
            "non_normal="
            + work["non_normal_rows"].astype(int).astype(str)
            + ", rate="
            + work["non_normal_rate_pct"].round(1).astype(str)
            + "%"
        )
        work = work.sort_values(
            by=["suggested_additions", "non_normal_rate_pct", "non_normal_rows", "high_rows", key],
            ascending=[False, False, False, False, True],
        )
        return work[[c for c in [key, "suggested_additions", "non_normal_rows", "high_rows", "non_normal_rate_pct", "why"] if c in work.columns]]

    groups = _prepare(pd.DataFrame(rank_spec_coverage.get("by_group", [])), "group")
    layers = _prepare(pd.DataFrame(rank_spec_coverage.get("by_layer", [])), "layer")
    return {
        "groups": groups.head(3).to_dict(orient="records"),
        "layers": layers.head(3).to_dict(orient="records"),
    }


def build_candidate_source_plan(source_summary: dict[str, list[dict[str, object]]]) -> dict[str, object]:
    by_source = pd.DataFrame(source_summary.get("by_source", []))
    if by_source.empty:
        return {"sources": [], "notes": ["Current rank data does not yet support a source-side expansion recommendation."]}

    work = by_source.copy()
    for col in ["total_rows", "high_rows", "watch_rows", "non_normal_rows", "non_normal_rate_pct"]:
        if col in work.columns:
            work[col] = pd.to_numeric(work[col], errors="coerce").fillna(0)
    work = work[work["non_normal_rows"] > 0].copy()
    if work.empty:
        return {"sources": [], "notes": ["Current rank data has no non-normal spec-risk rows by source yet."]}

    target_additions = pd.Series(0, index=work.index, dtype=float)
    target_additions += (work["non_normal_rows"] >= 2).astype(int)
    target_additions += (work["non_normal_rows"] >= 4).astype(int)
    target_additions += (work["non_normal_rate_pct"] >= 35).astype(int)
    work["suggested_additions"] = target_additions.clip(lower=1, upper=3).astype(int)
    work["why"] = (
        "non_normal="
        + work["non_normal_rows"].astype(int).astype(str)
        + ", rate="
        + work["non_normal_rate_pct"].round(1).astype(str)
        + "%"
    )
    work = work.sort_values(
        by=["suggested_additions", "non_normal_rate_pct", "non_normal_rows", "high_rows", "candidate_source"],
        ascending=[False, False, False, False, True],
    )
    top = work.head(3)
    notes = [
        "Use these source archetypes as the first place to look when filling the current group/layer expansion slots."
    ]
    if not top.empty:
        top_names = ", ".join(
            f"{row['candidate_source']} (+{int(row['suggested_additions'])})"
            for _, row in top.iterrows()
        )
        notes.append(f"Current best source-side expansion targets are {top_names}.")
    return {"sources": top.to_dict(orient="records"), "notes": notes}


def _source_search_hint(candidate_source: str) -> tuple[str, str, str]:
    mapping = {
        "Satellite high-beta leaders": (
            "satellite",
            "midlong_core",
            "找高 beta、20D 已明顯轉強、波動偏活潑但還沒進極端追價區的衛星股",
        ),
        "Theme trend acceleration": (
            "theme",
            "short_attack",
            "找帶 ACCEL/TREND、量能放大但未爆量、5D 已轉強且 20D 延續中的主題股",
        ),
        "Theme momentum burst": (
            "theme",
            "short_attack",
            "找短線量價急拉但仍可拆成觀察/備選的爆量題材股，先偏向只放觀察池",
        ),
        "Theme rotation candidates": (
            "theme",
            "short_attack",
            "找剛從整理轉強、量能回補、還沒有走到失控飆漲的輪動題材股",
        ),
        "Core trend compounders": (
            "core",
            "midlong_core",
            "找 TREND/REBREAK 明確、波動較穩、適合補核心中長線池的權值或龍頭",
        ),
    }
    return mapping.get(
        candidate_source,
        ("watchlist", "mixed", "優先找與目前高 spec-risk archetype 最相近、但風險還在可控區間的候選。"),
    )


def build_candidate_fill_directions(rank_csv: Path, candidate_source_plan: dict[str, object]) -> dict[str, list[dict[str, object]]]:
    empty = {"directions": []}
    sources = candidate_source_plan.get("sources", []) if isinstance(candidate_source_plan, dict) else []
    if not sources:
        return empty
    if not rank_csv.exists():
        return empty
    try:
        rank = pd.read_csv(rank_csv)
    except Exception:
        return empty
    if rank.empty:
        return empty

    work = rank.copy()
    work["candidate_source"] = work.apply(_derive_candidate_source, axis=1)

    directions: list[dict[str, object]] = []
    for source_row in sources[:3]:
        source_name = str(source_row.get("candidate_source", "")).strip()
        if not source_name:
            continue
        preferred_group, preferred_layer, search_hint = _source_search_hint(source_name)
        subset = work[work["candidate_source"].astype(str) == source_name].copy()
        examples = []
        if not subset.empty:
            sort_cols = [c for c in ["spec_risk_score", "rank"] if c in subset.columns]
            ascending = [False, True][: len(sort_cols)]
            subset = subset.sort_values(by=sort_cols, ascending=ascending) if sort_cols else subset
            examples = [
                f"{str(row.get('ticker', ''))} {str(row.get('name', '')).strip()}".strip()
                for _, row in subset.head(3).iterrows()
            ]
        directions.append(
            {
                "candidate_source": source_name,
                "suggested_additions": int(pd.to_numeric(source_row.get("suggested_additions"), errors="coerce") or 0),
                "preferred_group": preferred_group,
                "preferred_layer": preferred_layer,
                "search_hint": search_hint,
                "current_examples": ", ".join(examples),
            }
        )
    return {"directions": directions}


def build_watchlist_gap_snapshot(
    watchlist_csv: Path,
    candidate_expansion_plan: dict[str, object],
    candidate_source_plan: dict[str, object],
) -> dict[str, list[dict[str, object]]]:
    empty = {"by_group": [], "by_source": []}
    if not watchlist_csv.exists():
        return empty
    try:
        watchlist = pd.read_csv(watchlist_csv)
    except Exception:
        return empty
    if watchlist.empty:
        return empty

    by_group = []
    if "group" in watchlist.columns:
        group_counts = watchlist["group"].fillna("").astype(str).value_counts().to_dict()
        for row in candidate_expansion_plan.get("groups", []) if isinstance(candidate_expansion_plan, dict) else []:
            group = str(row.get("group", "")).strip()
            if not group:
                continue
            current_count = int(group_counts.get(group, 0))
            suggested = int(pd.to_numeric(row.get("suggested_additions"), errors="coerce") or 0)
            by_group.append(
                {
                    "group": group,
                    "current_watchlist_count": current_count,
                    "suggested_additions": suggested,
                    "next_target_count": current_count + suggested,
                }
            )

    by_source = []
    for row in candidate_source_plan.get("sources", []) if isinstance(candidate_source_plan, dict) else []:
        source = str(row.get("candidate_source", "")).strip()
        if not source:
            continue
        preferred_group, preferred_layer, _ = _source_search_hint(source)
        suggested = int(pd.to_numeric(row.get("suggested_additions"), errors="coerce") or 0)
        current_count = 0
        if preferred_group in {"theme", "satellite", "core", "etf"} and "group" in watchlist.columns:
            current_count = int((watchlist["group"].fillna("").astype(str) == preferred_group).sum())
        by_source.append(
            {
                "candidate_source": source,
                "preferred_group": preferred_group,
                "preferred_layer": preferred_layer,
                "current_group_count": current_count,
                "suggested_additions": suggested,
            }
        )

    return {"by_group": by_group, "by_source": by_source}


def _find_single_row(df: pd.DataFrame, *, horizon_days: int, watch_type: str) -> pd.Series | None:
    if df.empty:
        return None
    work = df.copy()
    if "horizon_days" in work.columns:
        work = work[pd.to_numeric(work["horizon_days"], errors="coerce") == horizon_days]
    if "watch_type" in work.columns:
        work = work[work["watch_type"].astype(str) == watch_type]
    if work.empty:
        return None
    return work.iloc[0]


def summarize_feedback_decision(feedback_csv: Path) -> tuple[str, str, dict[str, float | int | str]]:
    if not feedback_csv.exists():
        return "hold", "feedback sensitivity CSV not found; keep current weights for now.", {}
    try:
        feedback = pd.read_csv(feedback_csv)
    except Exception as exc:
        return "hold", f"feedback sensitivity CSV unreadable ({exc}); keep `70/30`.", {}

    non_baseline = feedback[feedback["config_name"].astype(str) != "70/30"].copy() if not feedback.empty else feedback
    if non_baseline.empty:
        return "hold", "no non-baseline feedback configs were available; keep `70/30`.", {}

    non_baseline["rank_delta"] = pd.to_numeric(non_baseline.get("rank_delta"), errors="coerce").fillna(0)
    non_baseline["score_delta"] = pd.to_numeric(non_baseline.get("score_delta"), errors="coerce").fillna(0)

    max_rank_shift = int(non_baseline["rank_delta"].abs().max()) if not non_baseline.empty else 0
    max_score_shift = round(float(non_baseline["score_delta"].abs().max()), 2) if not non_baseline.empty else 0.0
    if max_rank_shift == 0:
        return (
            "hold",
            f"feedback 權重改動目前只會小幅移動分數（最大 `score_delta={max_score_shift}`），不會改變 action 排名；先維持 `70/30`。",
            {"max_rank_shift": max_rank_shift, "max_score_shift": max_score_shift},
        )
    return (
        "review",
        f"feedback 權重已開始改變 action 排名（最大 `rank_delta={max_rank_shift}`）；可以考慮做更深入的離線比較。",
        {"max_rank_shift": max_rank_shift, "max_score_shift": max_score_shift},
    )


def build_spec_risk_overview(parts: dict[str, pd.DataFrame]) -> dict[str, object]:
    overall_by_spec_risk = parts.get("overall_by_spec_risk", pd.DataFrame())
    overall_by_spec_subtype = parts.get("overall_by_spec_subtype", pd.DataFrame())

    summary: dict[str, object] = {
        "non_normal_rows": 0,
        "top_subtype": {},
        "weakest_subtype": {},
        "same_subtype_extremes": False,
    }

    if not overall_by_spec_risk.empty:
        spec_rows = overall_by_spec_risk[overall_by_spec_risk["spec_risk_bucket"].astype(str) != "normal"].copy()
        if not spec_rows.empty:
            summary["non_normal_rows"] = int(pd.to_numeric(spec_rows["n"], errors="coerce").fillna(0).sum())

    if not overall_by_spec_subtype.empty:
        subtype = overall_by_spec_subtype.copy()
        subtype["n"] = pd.to_numeric(subtype["n"], errors="coerce").fillna(0)
        subtype["avg_ret"] = pd.to_numeric(subtype["avg_ret"], errors="coerce")
        subtype["win_rate"] = pd.to_numeric(subtype["win_rate"], errors="coerce")

        top = subtype.sort_values(by=["n", "avg_ret"], ascending=[False, False]).iloc[0]
        summary["top_subtype"] = {
            "horizon_days": int(pd.to_numeric(top.get("horizon_days"), errors="coerce") or 0),
            "watch_type": str(top.get("watch_type", "")),
            "spec_risk_subtype": str(top.get("spec_risk_subtype", "")),
            "n": int(pd.to_numeric(top.get("n"), errors="coerce") or 0),
            "avg_ret": round(float(pd.to_numeric(top.get("avg_ret"), errors="coerce") or 0.0), 2),
            "win_rate": round(float(pd.to_numeric(top.get("win_rate"), errors="coerce") or 0.0), 1),
        }

        weakest = subtype.sort_values(by=["avg_ret", "n"], ascending=[True, False]).iloc[0]
        summary["weakest_subtype"] = {
            "horizon_days": int(pd.to_numeric(weakest.get("horizon_days"), errors="coerce") or 0),
            "watch_type": str(weakest.get("watch_type", "")),
            "spec_risk_subtype": str(weakest.get("spec_risk_subtype", "")),
            "n": int(pd.to_numeric(weakest.get("n"), errors="coerce") or 0),
            "avg_ret": round(float(pd.to_numeric(weakest.get("avg_ret"), errors="coerce") or 0.0), 2),
            "win_rate": round(float(pd.to_numeric(weakest.get("win_rate"), errors="coerce") or 0.0), 1),
        }
        summary["same_subtype_extremes"] = (
            summary["top_subtype"].get("spec_risk_subtype", "") == summary["weakest_subtype"].get("spec_risk_subtype", "")
            and summary["top_subtype"].get("watch_type", "") == summary["weakest_subtype"].get("watch_type", "")
            and summary["top_subtype"].get("horizon_days", 0) == summary["weakest_subtype"].get("horizon_days", 0)
        )

    return summary


def build_short_gate_tuning_draft(
    full_parts: dict[str, pd.DataFrame],
    recent_parts: dict[str, pd.DataFrame],
    *,
    target_action: str = "開高不追",
) -> dict[str, object]:
    summary: dict[str, object] = {
        "target_action": target_action,
        "status": "hold",
        "why_now": "",
        "proposal": "",
        "guardrails": [],
        "historical": {},
        "recent": {},
        "contexts": [],
        "simulation": {},
    }

    full_watch = full_parts.get("short_gate_promotion_watch", pd.DataFrame())
    recent_watch = recent_parts.get("short_gate_promotion_watch", pd.DataFrame())
    full_context = full_parts.get("short_gate_action_context", pd.DataFrame())
    full_sim = full_parts.get("short_gate_simulation", pd.DataFrame())

    def _pick_action_row(df: pd.DataFrame) -> pd.Series | None:
        if df is None or df.empty:
            return None
        work = df.copy()
        work = work[
            (pd.to_numeric(work.get("horizon_days"), errors="coerce") == 1)
            & (work.get("watch_type", "").astype(str) == "short")
            & (work.get("action", "").astype(str) == target_action)
        ].copy()
        if work.empty:
            return None
        return work.iloc[0]

    full_row = _pick_action_row(full_watch)
    recent_row = _pick_action_row(recent_watch)

    if full_row is not None:
        summary["historical"] = {
            "below_n": int(pd.to_numeric(full_row.get("below_n"), errors="coerce") or 0),
            "ok_n": int(pd.to_numeric(full_row.get("ok_n"), errors="coerce") or 0),
            "confidence": str(full_row.get("confidence", "low")),
            "delta_avg_ret_below_minus_ok": round(float(pd.to_numeric(full_row.get("delta_avg_ret_below_minus_ok"), errors="coerce") or 0.0), 2),
            "promotion_ready": bool(full_row.get("promotion_ready", False)),
            "verdict": str(full_row.get("verdict", "")),
        }

    if recent_row is not None:
        summary["recent"] = {
            "below_n": int(pd.to_numeric(recent_row.get("below_n"), errors="coerce") or 0),
            "ok_n": int(pd.to_numeric(recent_row.get("ok_n"), errors="coerce") or 0),
            "confidence": str(recent_row.get("confidence", "low")),
            "delta_avg_ret_below_minus_ok": round(float(pd.to_numeric(recent_row.get("delta_avg_ret_below_minus_ok"), errors="coerce") or 0.0), 2),
            "promotion_ready": bool(recent_row.get("promotion_ready", False)),
            "verdict": str(recent_row.get("verdict", "")),
        }

    if not full_context.empty:
        context = full_context.copy()
        context = context[
            (pd.to_numeric(context.get("horizon_days"), errors="coerce") == 1)
            & (context.get("reco_status", "").astype(str) == "below_threshold")
            & (context.get("action", "").astype(str) == target_action)
        ].copy()
        if not context.empty:
            context["n"] = pd.to_numeric(context.get("n"), errors="coerce").fillna(0)
            context["avg_ret"] = pd.to_numeric(context.get("avg_ret"), errors="coerce").fillna(0.0)
            context = context.sort_values(
                by=["n", "avg_ret"],
                ascending=[False, False],
            )
            summary["contexts"] = context[
                [
                    c
                    for c in [
                        "scenario_label",
                        "market_heat",
                        "spec_risk_bucket",
                        "n",
                        "signal_dates",
                        "win_rate",
                        "avg_ret",
                        "med_ret",
                    ]
                    if c in context.columns
                ]
            ].head(5).to_dict(orient="records")

    if not full_sim.empty:
        sim = full_sim.copy()
        sim = sim[
            (pd.to_numeric(sim.get("horizon_days"), errors="coerce") == 1)
            & (sim.get("watch_type", "").astype(str) == "short")
            & (sim.get("promoted_actions", "").astype(str).str.contains(target_action, regex=False))
        ].copy()
        if not sim.empty:
            top_sim = sim.iloc[0]
            summary["simulation"] = {
                "promoted_n": int(pd.to_numeric(top_sim.get("promoted_n"), errors="coerce") or 0),
                "delta_avg_ret_simulated_minus_current": round(float(pd.to_numeric(top_sim.get("delta_avg_ret_simulated_minus_current"), errors="coerce") or 0.0), 2),
                "delta_win_rate_simulated_minus_current": round(float(pd.to_numeric(top_sim.get("delta_win_rate_simulated_minus_current"), errors="coerce") or 0.0), 1),
            }

    historical_ready = bool(summary.get("historical", {}).get("promotion_ready", False))
    recent_ready = bool(summary.get("recent", {}).get("promotion_ready", False))
    hist_delta = float(summary.get("historical", {}).get("delta_avg_ret_below_minus_ok", 0.0) or 0.0)
    sim_delta = float(summary.get("simulation", {}).get("delta_avg_ret_simulated_minus_current", 0.0) or 0.0)

    guardrails: list[str] = [
        "只研究 `開高不追`，不動整體 short gate。",
        "僅限 `1D short`，不外推到 `5D short` 或 `midlong`。",
        "先只把它當 shadow upgrade / paper experiment，不直接變正式 candidate 規則。",
        "若 `spec_risk_bucket` 不是 `normal`，或最近樣本仍偏單日集中，就不建議正式升格。",
    ]
    summary["guardrails"] = guardrails

    if historical_ready and sim_delta > 0:
        summary["status"] = "draft_ready"
        summary["why_now"] = (
            f"全歷史 `1D short / {target_action}` 目前 `below-ok={hist_delta:.2f}%`，"
            f"而且最小模擬再增加 `ok avg_ret {sim_delta:.2f}%`。"
        )
        context_hints = []
        for row in summary["contexts"][:2]:
            scenario = str(row.get("scenario_label", ""))
            heat = str(row.get("market_heat", ""))
            if scenario:
                context_hints.append(f"`{scenario}` / `{heat}`")
        context_text = "、".join(context_hints) if context_hints else "近期偏強情境"
        summary["proposal"] = (
            f"草案建議：保留 `開高不追` 原標籤，但在 {context_text} 下，"
            "把它加入 shadow promotion 觀察名單，先追蹤是否持續優於 short `ok` baseline。"
        )
    elif hist_delta > 0:
        summary["status"] = "watch"
        summary["why_now"] = (
            f"全歷史 `1D short / {target_action}` 雖然偏強（`below-ok={hist_delta:.2f}%`），"
            "但近週樣本還不夠穩。"
        )
        summary["proposal"] = (
            "先維持現行規則，只把 `開高不追` 放進每週的 short-gate tuning watchlist，"
            "等 recent-only 也轉成 `promotion_ready` 再討論是否進一步升格。"
        )
    else:
        summary["status"] = "hold"
        summary["why_now"] = "目前還沒有足夠證據支持針對 `開高不追` 做 tuning 草案。"
        summary["proposal"] = "先繼續累積樣本，保持現行 short gate。"

    return summary


def build_research_diagnostics(
    parts: dict[str, pd.DataFrame],
    full_parts: dict[str, pd.DataFrame],
) -> dict[str, object]:
    def _best_factor(source: dict[str, pd.DataFrame]) -> dict[str, object]:
        table = source.get("factor_high_low_spread", pd.DataFrame())
        if table.empty:
            return {}
        work = table.copy()
        work["min_n"] = pd.to_numeric(work.get("min_n"), errors="coerce").fillna(0)
        work["delta_avg_ret_high_minus_low"] = pd.to_numeric(work.get("delta_avg_ret_high_minus_low"), errors="coerce").fillna(0.0)
        work = work[work["min_n"] >= 5].copy()
        if work.empty:
            return {}
        work["_abs_delta"] = work["delta_avg_ret_high_minus_low"].abs()
        row = work.sort_values(by=["_abs_delta", "min_n"], ascending=[False, False]).iloc[0]
        return {
            "horizon_days": int(pd.to_numeric(row.get("horizon_days"), errors="coerce") or 0),
            "watch_type": str(row.get("watch_type", "")),
            "factor_name": str(row.get("factor_name", "")),
            "min_n": int(pd.to_numeric(row.get("min_n"), errors="coerce") or 0),
            "confidence": str(row.get("confidence", "low")),
            "delta_avg_ret_high_minus_low": round(float(pd.to_numeric(row.get("delta_avg_ret_high_minus_low"), errors="coerce") or 0.0), 2),
        }

    def _best_sensitivity(source: dict[str, pd.DataFrame]) -> dict[str, object]:
        table = source.get("sensitivity_matrix", pd.DataFrame())
        if table.empty:
            return {}
        work = table.copy()
        work = work[work.get("config_name", "").astype(str) != "baseline_all"].copy()
        if work.empty:
            return {}
        work["n"] = pd.to_numeric(work.get("n"), errors="coerce").fillna(0)
        work["delta_avg_ret_vs_baseline"] = pd.to_numeric(work.get("delta_avg_ret_vs_baseline"), errors="coerce").fillna(0.0)
        work = work[work["n"] >= 5].copy()
        if work.empty:
            return {}
        row = work.sort_values(by=["delta_avg_ret_vs_baseline", "n"], ascending=[False, False]).iloc[0]
        return {
            "horizon_days": int(pd.to_numeric(row.get("horizon_days"), errors="coerce") or 0),
            "watch_type": str(row.get("watch_type", "")),
            "config_name": str(row.get("config_name", "")),
            "n": int(pd.to_numeric(row.get("n"), errors="coerce") or 0),
            "avg_ret": round(float(pd.to_numeric(row.get("avg_ret"), errors="coerce") or 0.0), 2),
            "delta_avg_ret_vs_baseline": round(float(pd.to_numeric(row.get("delta_avg_ret_vs_baseline"), errors="coerce") or 0.0), 2),
        }

    def _worst_tail(source: dict[str, pd.DataFrame]) -> dict[str, object]:
        table = source.get("tail_risk_by_action", pd.DataFrame())
        if table.empty:
            return {}
        work = table.copy()
        work["n"] = pd.to_numeric(work.get("n"), errors="coerce").fillna(0)
        work["worst_ret"] = pd.to_numeric(work.get("worst_ret"), errors="coerce").fillna(0.0)
        work = work[(work["n"] >= 3) & (work.get("risk_label", "").astype(str) != "ok")].copy()
        if work.empty:
            return {}
        row = work.sort_values(by=["worst_ret", "n"], ascending=[True, False]).iloc[0]
        return {
            "horizon_days": int(pd.to_numeric(row.get("horizon_days"), errors="coerce") or 0),
            "watch_type": str(row.get("watch_type", "")),
            "reco_status": str(row.get("reco_status", "")),
            "action": str(row.get("action", "")),
            "n": int(pd.to_numeric(row.get("n"), errors="coerce") or 0),
            "tail25_ret": round(float(pd.to_numeric(row.get("tail25_ret"), errors="coerce") or 0.0), 2),
            "worst_ret": round(float(pd.to_numeric(row.get("worst_ret"), errors="coerce") or 0.0), 2),
            "risk_label": str(row.get("risk_label", "")),
        }

    recent_factor = _best_factor(parts)
    full_factor = _best_factor(full_parts)
    recent_sensitivity = _best_sensitivity(parts)
    full_sensitivity = _best_sensitivity(full_parts)
    recent_tail = _worst_tail(parts)
    full_tail = _worst_tail(full_parts)

    notes: list[str] = []
    if full_factor:
        notes.append(
            f"Full-history strongest factor spread: `{full_factor['factor_name']}` in "
            f"`{full_factor['horizon_days']}D {full_factor['watch_type']}` "
            f"(`high-low={full_factor['delta_avg_ret_high_minus_low']}%`, `min_n={full_factor['min_n']}`)."
        )
    if full_sensitivity:
        notes.append(
            f"Full-history best sensitivity: `{full_sensitivity['config_name']}` in "
            f"`{full_sensitivity['horizon_days']}D {full_sensitivity['watch_type']}` "
            f"(`delta={full_sensitivity['delta_avg_ret_vs_baseline']}%`, `n={full_sensitivity['n']}`)."
        )
    if full_tail:
        notes.append(
            f"Full-history tail risk watch: `{full_tail['horizon_days']}D {full_tail['watch_type']} / {full_tail['action']}` "
            f"(`worst={full_tail['worst_ret']}%`, `tail25={full_tail['tail25_ret']}%`, `n={full_tail['n']}`)."
        )
    if not notes:
        notes.append("Research diagnostics are present, but samples are still too thin for a weekly read.")

    return {
        "recent_factor": recent_factor,
        "full_factor": full_factor,
        "recent_sensitivity": recent_sensitivity,
        "full_sensitivity": full_sensitivity,
        "recent_tail": recent_tail,
        "full_tail": full_tail,
        "notes": notes,
    }


def build_pullback_quality_diagnostics(outcomes: pd.DataFrame) -> pd.DataFrame:
    if outcomes.empty:
        return pd.DataFrame()
    required = {"watch_type", "action", "horizon_days", "realized_ret_pct", "status"}
    if not required.issubset(set(outcomes.columns)):
        return pd.DataFrame()

    work = outcomes.copy()
    work = work[
        (work["status"].astype(str) == "ok")
        & (work["watch_type"].astype(str) == "short")
        & (work["action"].astype(str) == "等拉回")
    ].copy()
    if work.empty:
        return pd.DataFrame()

    work["pullback_quality"] = work.apply(classify_short_pullback_quality, axis=1)
    work["_ret"] = pd.to_numeric(work["realized_ret_pct"], errors="coerce")
    work = work.dropna(subset=["_ret"])
    if work.empty:
        return pd.DataFrame()
    work["_win"] = work["_ret"] > 0
    grouped = (
        work.groupby(["horizon_days", "pullback_quality"], dropna=False)
        .agg(
            n=("_ret", "size"),
            win_rate=("_win", lambda s: round(float(s.mean()) * 100, 1) if len(s) else 0.0),
            avg_ret=("_ret", lambda s: round(float(s.mean()), 2)),
            med_ret=("_ret", lambda s: round(float(s.median()), 2)),
            tail25_ret=("_ret", lambda s: round(float(s.quantile(0.25)), 2)),
            worst_ret=("_ret", lambda s: round(float(s.min()), 2)),
            best_ret=("_ret", lambda s: round(float(s.max()), 2)),
        )
        .reset_index()
        .sort_values(by=["horizon_days", "worst_ret", "n"], ascending=[True, True, False])
    )
    grouped.insert(2, "action_guide", grouped["pullback_quality"].map(pullback_action_for_quality))
    grouped.insert(3, "guidance", grouped["pullback_quality"].map(pullback_guidance_for_quality))
    grouped.insert(4, "position_size", grouped["pullback_quality"].map(pullback_position_for_quality))
    return grouped


def build_pullback_confirmation_diagnostics(outcomes: pd.DataFrame) -> pd.DataFrame:
    if outcomes.empty:
        return pd.DataFrame()
    required = {"signal_date", "ticker", "watch_type", "action", "horizon_days", "realized_ret_pct", "status"}
    if not required.issubset(set(outcomes.columns)):
        return pd.DataFrame()

    work = outcomes.copy()
    work = work[
        (work["status"].astype(str) == "ok")
        & (work["watch_type"].astype(str) == "short")
        & (work["action"].astype(str) == "等拉回")
    ].copy()
    if work.empty:
        return pd.DataFrame()

    work["_horizon"] = pd.to_numeric(work["horizon_days"], errors="coerce")
    work["_ret"] = pd.to_numeric(work["realized_ret_pct"], errors="coerce")
    work = work[work["_horizon"].isin([1, 5])].dropna(subset=["_ret"]).copy()
    if work.empty:
        return pd.DataFrame()

    keys = ["signal_date", "ticker"]
    base = work.sort_values(by=keys + ["_horizon"]).drop_duplicates(subset=keys, keep="first").copy()
    base["pullback_quality"] = base.apply(classify_short_pullback_quality, axis=1)
    base["action_guide"] = base["pullback_quality"].map(pullback_action_for_quality)
    base["position_size"] = base["pullback_quality"].map(pullback_position_for_quality)

    ret1 = (
        work[work["_horizon"] == 1]
        .groupby(keys, dropna=False)["_ret"]
        .first()
        .rename("ret1_pct")
        .reset_index()
    )
    ret5 = (
        work[work["_horizon"] == 5]
        .groupby(keys, dropna=False)["_ret"]
        .first()
        .rename("ret5_realized_pct")
        .reset_index()
    )
    paired_columns = keys + ["pullback_quality", "action_guide", "position_size"]
    if "name" in base.columns:
        paired_columns.insert(2, "name")
    paired = base[paired_columns]
    paired = paired.merge(ret1, on=keys, how="inner").merge(ret5, on=keys, how="inner")
    if paired.empty:
        return pd.DataFrame()

    paired["confirmation"] = paired["ret1_pct"].map(next_session_confirmation_bucket)
    paired["action_guide"] = paired.apply(
        lambda row: confirmed_pullback_action_for_quality(row["pullback_quality"], row["confirmation"]),
        axis=1,
    )
    paired["guidance"] = paired.apply(
        lambda row: confirmed_pullback_guidance_for_quality(row["pullback_quality"], row["confirmation"]),
        axis=1,
    )
    paired["position_size"] = paired.apply(
        lambda row: confirmed_pullback_position_for_quality(row["pullback_quality"], row["confirmation"]),
        axis=1,
    )
    paired["_win5"] = paired["ret5_realized_pct"] > 0
    name_col = "name" if "name" in paired.columns else "ticker"
    grouped = (
        paired.groupby(["pullback_quality", "confirmation", "action_guide", "guidance", "position_size"], dropna=False)
        .agg(
            n=("ret5_realized_pct", "size"),
            win_rate_5d=("_win5", lambda series: round(float(series.mean()) * 100, 1) if len(series) else 0.0),
            avg_5d=("ret5_realized_pct", lambda series: round(float(series.mean()), 2)),
            tail25_5d=("ret5_realized_pct", lambda series: round(float(series.quantile(0.25)), 2)),
            worst_5d=("ret5_realized_pct", lambda series: round(float(series.min()), 2)),
            best_5d=("ret5_realized_pct", lambda series: round(float(series.max()), 2)),
            examples=(name_col, lambda series: "、".join(series.dropna().astype(str).head(3).tolist())),
        )
        .reset_index()
        .sort_values(by=["worst_5d", "n"], ascending=[True, False])
    )
    return grouped


def build_pullback_rule_recommendations(confirmation: pd.DataFrame) -> pd.DataFrame:
    columns = ["rule", "condition", "status", "action_guide", "position_size", "evidence", "note"]
    if confirmation.empty:
        return pd.DataFrame(columns=columns)

    work = confirmation.copy()
    for col in ["n", "win_rate_5d", "avg_5d", "worst_5d"]:
        if col in work.columns:
            work[col] = pd.to_numeric(work[col], errors="coerce")

    def _match(quality: str, confirmation_label: str | None = None) -> pd.DataFrame:
        matched = work[work["pullback_quality"].astype(str) == quality].copy()
        if confirmation_label is not None:
            matched = matched[matched["confirmation"].astype(str) == confirmation_label].copy()
        return matched

    def _evidence(row: pd.Series | None) -> str:
        if row is None:
            return "n=0"
        return (
            f"n={int(row.get('n', 0))}, "
            f"win5={row.get('win_rate_5d', 0.0)}%, "
            f"avg5={row.get('avg_5d', 0.0)}%, "
            f"worst5={row.get('worst_5d', 0.0)}%"
        )

    high_confirmed = _match("高風險拉回", "隔日轉強")
    high_row = high_confirmed.iloc[0] if not high_confirmed.empty else None
    high_n = int(high_row.get("n", 0)) if high_row is not None else 0
    high_worst = float(high_row.get("worst_5d", 0.0)) if high_row is not None and pd.notna(high_row.get("worst_5d")) else -999.0
    high_status = "active_low_sample" if high_n < 5 else "active"
    if high_worst < 0:
        high_status = "research_only"

    confirm_pullback = _match("需確認拉回", "隔日轉強")
    confirm_row = confirm_pullback.iloc[0] if not confirm_pullback.empty else None
    confirm_worst = float(confirm_row.get("worst_5d", 0.0)) if confirm_row is not None and pd.notna(confirm_row.get("worst_5d")) else 0.0
    confirm_status = "block_upgrade" if confirm_worst <= -8 else "review_required"

    rows = [
        {
            "rule": "高風險拉回",
            "condition": "隔日轉強",
            "status": high_status,
            "action_guide": "可小試",
            "position_size": "0.25 倉",
            "evidence": _evidence(high_row),
            "note": "只允許確認後小倉，不提前試單；樣本不足時維持 low-sample 標記。",
        },
        {
            "rule": "高風險拉回",
            "condition": "未達隔日轉強",
            "status": "blocked",
            "action_guide": "只觀察",
            "position_size": "0 倉",
            "evidence": "rule-based",
            "note": "未確認前不試單，避免追高波動與隔日失守。",
        },
        {
            "rule": "需確認拉回",
            "condition": "即使隔日轉強",
            "status": confirm_status,
            "action_guide": "只觀察",
            "position_size": "0 倉",
            "evidence": _evidence(confirm_row),
            "note": "隔日轉強仍可能有大尾端風險，不能升級成買進訊號。",
        },
        {
            "rule": "弱承接/疑似破位",
            "condition": "任何隔日確認",
            "status": "blocked",
            "action_guide": "暫不買",
            "position_size": "0 倉",
            "evidence": _evidence(_match("弱承接/疑似破位").sort_values(by=["worst_5d"]).iloc[0] if not _match("弱承接/疑似破位").empty else None),
            "note": "先等量價恢復，不因單日反彈直接升級。",
        },
    ]
    return pd.DataFrame(rows, columns=columns)


def build_pullback_exit_guard_recommendations(confirmation: pd.DataFrame) -> pd.DataFrame:
    columns = ["setup", "entry_gate", "initial_size", "close_exit_guard", "time_stop", "profit_guard", "evidence", "status"]
    if confirmation.empty:
        return pd.DataFrame(columns=columns)

    work = confirmation.copy()
    for col in ["n", "win_rate_5d", "avg_5d", "worst_5d"]:
        if col in work.columns:
            work[col] = pd.to_numeric(work[col], errors="coerce")

    def _best_row(quality: str, confirmation_label: str | None = None) -> pd.Series | None:
        matched = work[work["pullback_quality"].astype(str) == quality].copy()
        if confirmation_label is not None:
            matched = matched[matched["confirmation"].astype(str) == confirmation_label].copy()
        if matched.empty:
            return None
        return matched.sort_values(by=["worst_5d", "n"], ascending=[False, False]).iloc[0]

    def _worst_row(quality: str) -> pd.Series | None:
        matched = work[work["pullback_quality"].astype(str) == quality].copy()
        if matched.empty:
            return None
        return matched.sort_values(by=["worst_5d", "n"], ascending=[True, False]).iloc[0]

    def _evidence(row: pd.Series | None) -> str:
        if row is None:
            return "n=0"
        return (
            f"n={int(row.get('n', 0))}, "
            f"win5={row.get('win_rate_5d', 0.0)}%, "
            f"avg5={row.get('avg_5d', 0.0)}%, "
            f"worst5={row.get('worst_5d', 0.0)}%"
        )

    high_confirmed = _best_row("高風險拉回", "隔日轉強")
    healthy_worst = _worst_row("健康拉回")
    confirm_worst = _worst_row("需確認拉回")

    rows = [
        {
            "setup": "高風險拉回 / 可小試",
            "entry_gate": "只在隔日轉強後進場",
            "initial_size": "0.25 倉",
            "close_exit_guard": "進場後收盤跌回確認日低點或單日 -2%：退出",
            "time_stop": "2 個交易日不續強：降回觀察",
            "profit_guard": "5D 內急拉優先分批落袋，不加碼攤平",
            "evidence": _evidence(high_confirmed),
            "status": "active_low_sample",
        },
        {
            "setup": "健康拉回 / 可等買點",
            "entry_gate": "等支撐確認，不追第一根",
            "initial_size": "0.5 倉",
            "close_exit_guard": "收盤跌破支撐或單日 -2%：減碼/退出",
            "time_stop": "5D 未轉強且跌破買點：退出",
            "profit_guard": "若 5D 急拉，先把試單轉保本",
            "evidence": _evidence(healthy_worst),
            "status": "active",
        },
        {
            "setup": "需確認拉回 / 只觀察",
            "entry_gate": "不因隔日轉強升級",
            "initial_size": "0 倉",
            "close_exit_guard": "無部位；若已誤進，跌破前低立即退出",
            "time_stop": "等待新訊號重新分類",
            "profit_guard": "不追反彈",
            "evidence": _evidence(confirm_worst),
            "status": "blocked_tail_risk",
        },
    ]
    return pd.DataFrame(rows, columns=columns)


def build_data_quality_gate(outcomes: pd.DataFrame, snapshots: pd.DataFrame) -> dict[str, object]:
    summary: dict[str, object] = {
        "status": "ok",
        "notes": [],
        "metrics": {},
        "coverage_by_horizon": [],
        "coverage_by_signal_date": [],
    }

    out = outcomes.copy() if outcomes is not None else pd.DataFrame()
    snap = snapshots.copy() if snapshots is not None else pd.DataFrame()
    notes: list[str] = []

    def _latest_date(df: pd.DataFrame) -> str:
        if df.empty or "signal_date" not in df.columns:
            return ""
        dates = sorted([d for d in df["signal_date"].dropna().astype(str).str.strip().unique().tolist() if d])
        return dates[-1] if dates else ""

    snapshot_dup_keys = 0
    if not snap.empty and all(c in snap.columns for c in ["signal_date", "watch_type", "ticker"]):
        snapshot_dup_keys = int(snap.duplicated(["signal_date", "watch_type", "ticker"]).sum())

    outcome_dup_keys = 0
    if not out.empty and all(c in out.columns for c in ["signal_date", "horizon_days", "watch_type", "ticker"]):
        outcome_dup_keys = int(out.duplicated(["signal_date", "horizon_days", "watch_type", "ticker"]).sum())

    status_counts: dict[str, int] = {}
    if not out.empty and "status" in out.columns:
        status_counts = {str(k): int(v) for k, v in out["status"].fillna("").astype(str).value_counts().to_dict().items()}

    snapshot_dates = set(snap["signal_date"].dropna().astype(str).str.strip()) if "signal_date" in snap.columns else set()
    outcome_dates = set(out["signal_date"].dropna().astype(str).str.strip()) if "signal_date" in out.columns else set()
    missing_outcome_dates = sorted(d for d in snapshot_dates - outcome_dates if d)
    missing_snapshot_dates = sorted(d for d in outcome_dates - snapshot_dates if d)

    coverage_by_horizon = pd.DataFrame()
    if not out.empty and all(c in out.columns for c in ["horizon_days", "status"]):
        work = out.copy()
        work["horizon_days"] = pd.to_numeric(work["horizon_days"], errors="coerce").astype("Int64")
        work["status"] = work["status"].fillna("").astype(str)
        coverage_by_horizon = (
            work.groupby("horizon_days", dropna=False)
            .agg(
                total=("status", "count"),
                ok=("status", lambda s: int((s.astype(str) == "ok").sum())),
                pending=("status", lambda s: int((s.astype(str) == "insufficient_forward_data").sum())),
                missing=("status", lambda s: int((s.astype(str).isin(["signal_date_missing", "no_price_series"])).sum())),
            )
            .reset_index()
            .sort_values(by=["horizon_days"])
        )
        coverage_by_horizon["ok_rate_pct"] = ((coverage_by_horizon["ok"] / coverage_by_horizon["total"]) * 100).round(1)

    coverage_by_signal_date = pd.DataFrame()
    if not out.empty and all(c in out.columns for c in ["signal_date", "status"]):
        work = out.copy()
        work["status"] = work["status"].fillna("").astype(str)
        coverage_by_signal_date = (
            work.groupby("signal_date", dropna=False)
            .agg(
                total=("status", "count"),
                ok=("status", lambda s: int((s.astype(str) == "ok").sum())),
                pending=("status", lambda s: int((s.astype(str) == "insufficient_forward_data").sum())),
                missing=("status", lambda s: int((s.astype(str).isin(["signal_date_missing", "no_price_series"])).sum())),
            )
            .reset_index()
            .sort_values(by=["signal_date"], ascending=False)
        )

    metrics = {
        "snapshot_rows": int(len(snap)),
        "outcome_rows": int(len(out)),
        "latest_snapshot_signal_date": _latest_date(snap),
        "latest_outcome_signal_date": _latest_date(out),
        "snapshot_dup_keys": snapshot_dup_keys,
        "outcome_dup_keys": outcome_dup_keys,
        "ok_rows": int(status_counts.get("ok", 0)),
        "pending_rows": int(status_counts.get("insufficient_forward_data", 0)),
        "signal_date_missing_rows": int(status_counts.get("signal_date_missing", 0)),
        "no_price_series_rows": int(status_counts.get("no_price_series", 0)),
        "missing_outcome_dates": missing_outcome_dates,
        "missing_snapshot_dates": missing_snapshot_dates,
    }

    blocking = []
    if snapshot_dup_keys:
        blocking.append(f"snapshot duplicate keys = {snapshot_dup_keys}")
    if outcome_dup_keys:
        blocking.append(f"outcome duplicate keys = {outcome_dup_keys}")
    if metrics["signal_date_missing_rows"]:
        blocking.append(f"signal_date_missing rows = {metrics['signal_date_missing_rows']}")
    if metrics["no_price_series_rows"]:
        blocking.append(f"no_price_series rows = {metrics['no_price_series_rows']}")
    if missing_outcome_dates:
        blocking.append("snapshot dates missing outcomes: " + ", ".join(missing_outcome_dates[:5]))
    if missing_snapshot_dates:
        blocking.append("outcome dates missing snapshots: " + ", ".join(missing_snapshot_dates[:5]))

    if blocking:
        summary["status"] = "review"
        notes.append("Data quality gate needs review: " + "; ".join(blocking) + ".")
    else:
        notes.append("Data quality gate is clean: no duplicate keys, no missing-price statuses, and snapshot/outcome dates align.")

    if metrics["pending_rows"]:
        notes.append(
            f"`{metrics['pending_rows']}` outcome rows are still pending forward data; this is expected for fresh 1D/5D/20D horizons."
        )

    latest_snapshot = str(metrics["latest_snapshot_signal_date"])
    latest_outcome = str(metrics["latest_outcome_signal_date"])
    if latest_snapshot and latest_outcome and latest_snapshot == latest_outcome:
        notes.append(f"Latest snapshot/outcome signal date is aligned at `{latest_snapshot}`.")
    elif latest_snapshot or latest_outcome:
        notes.append(f"Latest date mismatch: snapshot=`{latest_snapshot}`, outcome=`{latest_outcome}`.")
        if summary["status"] == "ok":
            summary["status"] = "watch"

    summary["notes"] = notes
    summary["metrics"] = metrics
    summary["coverage_by_horizon"] = coverage_by_horizon.to_dict(orient="records")
    summary["coverage_by_signal_date"] = coverage_by_signal_date.head(15).to_dict(orient="records")
    return summary


def build_decisions(
    parts: dict[str, pd.DataFrame],
    band_parts: dict[str, pd.DataFrame],
    feedback_csv: Path,
) -> dict[str, dict[str, object]]:
    gate = parts.get("midlong_threshold_gate", pd.DataFrame())
    threshold_row = _find_single_row(parts.get("delta_ok_minus_below", pd.DataFrame()), horizon_days=1, watch_type="midlong")
    heat_row = _find_single_row(parts.get("heat_bias_check", pd.DataFrame()), horizon_days=1, watch_type="midlong")
    spec_risk_row = _find_single_row(parts.get("spec_risk_check", pd.DataFrame()), horizon_days=1, watch_type="short")
    short_gate_watch = parts.get("short_gate_promotion_watch", pd.DataFrame())
    short_gate_simulation = parts.get("short_gate_simulation", pd.DataFrame())

    if not gate.empty and "decision" in gate.columns:
        blocked = gate[gate["decision"].astype(str) == "block_loosening"].copy()
        if not blocked.empty:
            worst = blocked.sort_values(by=["heat_share_gap_pct"], ascending=[False]).iloc[0]
            threshold_decision = {
                "status": "block",
                "detail": (
                    f"`midlong threshold gate` 目前是 `{worst.get('decision')}`："
                    f"`{int(worst.get('horizon_days'))}D` below_threshold 的 hot share "
                    f"`{float(worst.get('below_hot_share_pct')):.1f}%`，"
                    f"normal below_threshold 樣本 `{int(worst.get('normal_below_n'))}`；"
                    "先禁止放寬門檻，只持續觀察。"
                ),
            }
        else:
            reviewable = gate[gate["decision"].astype(str) == "eligible_for_review"].copy()
            if not reviewable.empty:
                best = reviewable.iloc[0]
                threshold_decision = {
                    "status": "review",
                    "detail": (
                        f"`midlong threshold gate` 已達 `{best.get('decision')}`："
                        f"`normal_below_n={int(best.get('normal_below_n'))}`；"
                        "可以進一步看回撤與 tail risk，再決定是否調參。"
                    ),
                }
            else:
                threshold_decision = {
                    "status": "hold",
                    "detail": "`midlong threshold gate` 仍是 observe-only；先累積 normal 盤樣本。",
                }
    elif threshold_row is None:
        threshold_decision = {
            "status": "hold",
            "detail": "最近樣本還不足以判斷 `midlong threshold`；先持續累積。",
        }
    else:
        min_n = int(pd.to_numeric(threshold_row.get("min_n"), errors="coerce") or 0)
        delta_avg = float(pd.to_numeric(threshold_row.get("delta_avg_ret"), errors="coerce") or 0.0)
        confidence = str(threshold_row.get("confidence", "low"))
        if min_n >= 5 and delta_avg <= -0.5:
            heat_hint = ""
            if heat_row is not None:
                heat_hint = (
                    f" 同時 `1D midlong` 的 `hot-normal` 仍有 `{float(pd.to_numeric(heat_row.get('delta_avg_ret_hot_minus_normal'), errors='coerce') or 0.0):.2f}%`，"
                    "要先排除 heat bias 再動門檻。"
                )
            threshold_decision = {
                "status": "review",
                "detail": (
                    f"`ok - below_threshold = {delta_avg:.2f}%`、`min_n={min_n}`、`confidence={confidence}`；"
                    "這代表 forced-fill 沒有明顯更差，值得優先研究 `midlong threshold`。"
                    + heat_hint
                ),
            }
        else:
            threshold_decision = {
                "status": "hold",
                "detail": (
                    f"`ok - below_threshold = {delta_avg:.2f}%`、`min_n={min_n}`、`confidence={confidence}`；"
                    "目前還不夠支持直接調整 `midlong threshold`。"
                ),
            }

    coverage = band_parts.get("band_coverage", pd.DataFrame())
    coverage = coverage.copy() if not coverage.empty else coverage
    if not coverage.empty:
        coverage["horizon_days"] = pd.to_numeric(coverage["horizon_days"], errors="coerce")
        matured_5_20 = coverage[coverage["horizon_days"].isin([5, 20])]["matured_rows"].sum()
    else:
        matured_5_20 = 0
    if int(matured_5_20) == 0:
        atr_decision = {
            "status": "hold",
            "detail": "ATR band 在 `5D / 20D` 還沒有成熟樣本；先把它當 coverage / checkpoint 報表，不要急著改 exit。",
        }
    else:
        atr_decision = {
            "status": "review",
            "detail": f"ATR band 的 `5D / 20D` 已有 `{int(matured_5_20)}` 筆成熟樣本，可以開始做更深的 exit 驗證。",
        }

    feedback_status, feedback_detail, feedback_meta = summarize_feedback_decision(feedback_csv)
    feedback_decision = {"status": feedback_status, "detail": feedback_detail, **feedback_meta}

    if spec_risk_row is None:
        spec_risk_decision = {
            "status": "hold",
            "detail": "最近樣本還不足以判斷 `spec_risk high vs normal`；先持續累積。",
        }
    else:
        min_n = int(pd.to_numeric(spec_risk_row.get("min_n"), errors="coerce") or 0)
        delta_avg = float(pd.to_numeric(spec_risk_row.get("delta_avg_ret_high_minus_normal"), errors="coerce") or 0.0)
        confidence = str(spec_risk_row.get("confidence", "low"))
        if min_n >= 5 and delta_avg <= -0.5:
            spec_risk_decision = {
                "status": "review",
                "detail": (
                    f"`high - normal = {delta_avg:.2f}%`、`min_n={min_n}`、`confidence={confidence}`；"
                    "高疑似炒作樣本已開始明顯跑輸正常樣本，值得優先研究是否要再收緊短線推播/補滿邏輯。"
                ),
            }
        else:
            spec_risk_decision = {
                "status": "hold",
                "detail": (
                    f"`high - normal = {delta_avg:.2f}%`、`min_n={min_n}`、`confidence={confidence}`；"
                    "目前還不夠支持直接把 `spec_risk` 變成硬性排除條件。"
                ),
            }

    if short_gate_watch is None or short_gate_watch.empty:
        short_gate_decision = {
            "status": "hold",
            "detail": "最近樣本還不足以判斷哪一種短線候補值得升格；先持續累積。",
        }
    else:
        candidates = short_gate_watch.copy()
        candidates = candidates[
            (pd.to_numeric(candidates.get("horizon_days"), errors="coerce") == 1)
            & (candidates.get("watch_type", "").astype(str) == "short")
        ].copy()
        candidates = candidates[candidates["verdict"].astype(str) == "watch_upgrade"].copy()
        if candidates.empty:
            short_gate_decision = {
                "status": "hold",
                "detail": "目前還沒有明確的短線候補升格對象；先維持原本 short gate。",
            }
        else:
            candidates["below_n"] = pd.to_numeric(candidates.get("below_n"), errors="coerce").fillna(0)
            candidates["delta_avg_ret_below_minus_ok"] = pd.to_numeric(candidates.get("delta_avg_ret_below_minus_ok"), errors="coerce").fillna(0.0)
            candidates = candidates.sort_values(
                by=["delta_avg_ret_below_minus_ok", "below_n"],
                ascending=[False, False],
            )
            top_candidate = candidates.iloc[0]
            min_n = int(pd.to_numeric(top_candidate.get("min_n"), errors="coerce") or 0)
            delta_avg = float(pd.to_numeric(top_candidate.get("delta_avg_ret_below_minus_ok"), errors="coerce") or 0.0)
            confidence = str(top_candidate.get("confidence", "low"))
            action = str(top_candidate.get("action", ""))
            if min_n >= 5 and delta_avg >= 1.0:
                short_gate_decision = {
                    "status": "review",
                    "detail": (
                        f"`{action}` 目前是最值得研究的短線候補升格對象，"
                        f"`below-ok = {delta_avg:.2f}%`、`min_n={min_n}`、`confidence={confidence}`；"
                        "可以優先做 action-level tuning，但先不要一次放寬整體 short gate。"
                    ),
                }
            else:
                short_gate_decision = {
                    "status": "hold",
                    "detail": (
                        f"`{action}` 雖然看起來偏強，但目前只有 `below-ok = {delta_avg:.2f}%`、"
                        f"`min_n={min_n}`、`confidence={confidence}`；先當成觀察名單，不急著動 short gate。"
                    ),
                }

    if not isinstance(short_gate_simulation, pd.DataFrame) or short_gate_simulation.empty:
        pass
    else:
        sim = short_gate_simulation.copy()
        sim = sim[pd.to_numeric(sim.get("horizon_days"), errors="coerce") == 1].copy()
        if not sim.empty:
            sim["delta_avg_ret_simulated_minus_current"] = pd.to_numeric(
                sim.get("delta_avg_ret_simulated_minus_current"), errors="coerce"
            ).fillna(0.0)
            sim["promoted_n"] = pd.to_numeric(sim.get("promoted_n"), errors="coerce").fillna(0)
            sim = sim.sort_values(
                by=["delta_avg_ret_simulated_minus_current", "promoted_n"],
                ascending=[False, False],
            )
            top_sim = sim.iloc[0]
            sim_delta = float(pd.to_numeric(top_sim.get("delta_avg_ret_simulated_minus_current"), errors="coerce") or 0.0)
            sim_promoted_n = int(pd.to_numeric(top_sim.get("promoted_n"), errors="coerce") or 0)
            sim_actions = str(top_sim.get("promoted_actions", ""))
            if short_gate_decision["status"] == "review":
                short_gate_decision["detail"] += (
                    f" 最小模擬顯示只升格 `{sim_actions}` 時，`1D short ok` 平均報酬可再增加 `{sim_delta:.2f}%` "
                    f"（`promoted_n={sim_promoted_n}`）。"
                )
            elif sim_promoted_n >= 3 and sim_delta >= 0.5:
                short_gate_decision = {
                    "status": "review",
                    "detail": (
                        f"最小模擬顯示只升格 `{sim_actions}` 時，`1D short ok` 平均報酬可增加 `{sim_delta:.2f}%` "
                        f"（`promoted_n={sim_promoted_n}`）；值得先做 action-level tuning，不要直接改整體 short gate。"
                    ),
                }

    return {
        "threshold": threshold_decision,
        "short_gate": short_gate_decision,
        "atr": atr_decision,
        "feedback": feedback_decision,
        "spec_risk": spec_risk_decision,
    }


def build_weekly_review_payload(
    *,
    outcomes_csv: Path,
    snapshots_csv: Path,
    feedback_csv: Path,
    alert_csv: Path,
    rank_csv: Path,
    watchlist_csv: Path,
    max_signal_dates: int,
) -> dict[str, object]:
    if not outcomes_csv.exists():
        raise FileNotFoundError(f"Missing outcomes CSV: {outcomes_csv}")
    outcomes = pd.read_csv(outcomes_csv)
    snapshots = pd.read_csv(snapshots_csv) if snapshots_csv.exists() else pd.DataFrame()
    recent_outcomes, recent_dates = filter_recent_signal_dates(outcomes, max_signal_dates=max_signal_dates)
    parts = summarize_outcomes(recent_outcomes)
    full_parts = summarize_outcomes(outcomes)

    if alert_csv.exists():
        try:
            alert_df = pd.read_csv(alert_csv)
        except Exception:
            alert_df = pd.DataFrame()
    else:
        alert_df = pd.DataFrame()
    band_parts = summarize_atr_band_checkpoints(alert_df)
    decisions = build_decisions(parts, band_parts, feedback_csv)
    spec_risk_overview = build_spec_risk_overview(parts)
    rank_spec_coverage = build_rank_spec_risk_coverage(rank_csv)
    candidate_source_summary = build_rank_candidate_source_summary(rank_csv)
    rank_coverage_guidance = build_rank_coverage_guidance(rank_spec_coverage)
    candidate_expansion_plan = build_candidate_expansion_plan(rank_spec_coverage)
    candidate_source_plan = build_candidate_source_plan(candidate_source_summary)
    candidate_fill_directions = build_candidate_fill_directions(rank_csv, candidate_source_plan)
    watchlist_gap_snapshot = build_watchlist_gap_snapshot(watchlist_csv, candidate_expansion_plan, candidate_source_plan)
    short_gate_tuning_draft = build_short_gate_tuning_draft(full_parts, parts)
    research_diagnostics = build_research_diagnostics(parts, full_parts)
    data_quality_gate = build_data_quality_gate(outcomes, snapshots)
    recent_pullback_quality = build_pullback_quality_diagnostics(recent_outcomes)
    full_pullback_quality = build_pullback_quality_diagnostics(outcomes)
    recent_pullback_confirmation = build_pullback_confirmation_diagnostics(recent_outcomes)
    full_pullback_confirmation = build_pullback_confirmation_diagnostics(outcomes)
    pullback_rule_recommendations = build_pullback_rule_recommendations(full_pullback_confirmation)
    pullback_exit_guard_recommendations = build_pullback_exit_guard_recommendations(full_pullback_confirmation)

    overall_by_signal = parts.get("overall_by_signal", pd.DataFrame())
    weekly_checkpoint = parts.get("delta_ok_minus_below", pd.DataFrame())
    heat_bias_check = parts.get("heat_bias_check", pd.DataFrame())
    midlong_threshold_gate = parts.get("midlong_threshold_gate", pd.DataFrame())
    overall_by_spec_risk = parts.get("overall_by_spec_risk", pd.DataFrame())
    overall_by_spec_subtype = parts.get("overall_by_spec_subtype", pd.DataFrame())
    spec_risk_check = parts.get("spec_risk_check", pd.DataFrame())
    short_gate_promotion_watch = parts.get("short_gate_promotion_watch", pd.DataFrame())
    short_gate_simulation = parts.get("short_gate_simulation", pd.DataFrame())

    summary = {
        "signal_dates": recent_dates,
        "row_count": int(len(recent_outcomes)),
        "ok_rows": int((recent_outcomes.get("status", pd.Series(dtype=str)).astype(str) == "ok").sum()) if not recent_outcomes.empty else 0,
        "spec_risk_overview": spec_risk_overview,
        "rank_coverage_guidance": rank_coverage_guidance,
        "candidate_expansion_plan": candidate_expansion_plan,
        "candidate_source_plan": candidate_source_plan,
        "candidate_fill_directions": candidate_fill_directions,
        "watchlist_gap_snapshot": watchlist_gap_snapshot,
        "short_gate_tuning_draft": short_gate_tuning_draft,
        "research_diagnostics": research_diagnostics,
        "data_quality_gate": data_quality_gate,
    }

    return {
        "generated_at": datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S %Z"),
        "source": str(outcomes_csv),
        "summary": summary,
        "decisions": decisions,
        "tables": {
            "overall_by_signal": overall_by_signal.to_dict(orient="records"),
            "weekly_threshold_delta": weekly_checkpoint.to_dict(orient="records"),
            "heat_bias_check": heat_bias_check.to_dict(orient="records"),
            "midlong_threshold_gate": midlong_threshold_gate.to_dict(orient="records"),
            "overall_by_spec_risk": overall_by_spec_risk.to_dict(orient="records"),
            "overall_by_spec_subtype": overall_by_spec_subtype.to_dict(orient="records"),
            "spec_risk_check": spec_risk_check.to_dict(orient="records"),
            "short_gate_promotion_watch": short_gate_promotion_watch.to_dict(orient="records"),
            "short_gate_simulation": short_gate_simulation.to_dict(orient="records"),
            "full_short_gate_promotion_watch": full_parts.get("short_gate_promotion_watch", pd.DataFrame()).to_dict(orient="records"),
            "full_short_gate_action_context": full_parts.get("short_gate_action_context", pd.DataFrame()).to_dict(orient="records"),
            "full_short_gate_simulation": full_parts.get("short_gate_simulation", pd.DataFrame()).to_dict(orient="records"),
            "recent_factor_high_low_spread": parts.get("factor_high_low_spread", pd.DataFrame()).to_dict(orient="records"),
            "full_factor_high_low_spread": full_parts.get("factor_high_low_spread", pd.DataFrame()).to_dict(orient="records"),
            "recent_sensitivity_matrix": parts.get("sensitivity_matrix", pd.DataFrame()).to_dict(orient="records"),
            "full_sensitivity_matrix": full_parts.get("sensitivity_matrix", pd.DataFrame()).to_dict(orient="records"),
            "recent_tail_risk_by_action": parts.get("tail_risk_by_action", pd.DataFrame()).to_dict(orient="records"),
            "full_tail_risk_by_action": full_parts.get("tail_risk_by_action", pd.DataFrame()).to_dict(orient="records"),
            "recent_short_pullback_quality": recent_pullback_quality.to_dict(orient="records"),
            "full_short_pullback_quality": full_pullback_quality.to_dict(orient="records"),
            "recent_short_pullback_confirmation": recent_pullback_confirmation.to_dict(orient="records"),
            "full_short_pullback_confirmation": full_pullback_confirmation.to_dict(orient="records"),
            "short_pullback_rule_recommendations": pullback_rule_recommendations.to_dict(orient="records"),
            "short_pullback_exit_guard_recommendations": pullback_exit_guard_recommendations.to_dict(orient="records"),
            "current_rank_spec_risk_by_group": rank_spec_coverage["by_group"],
            "current_rank_spec_risk_by_layer": rank_spec_coverage["by_layer"],
            "current_rank_spec_risk_by_source": candidate_source_summary["by_source"],
            "current_rank_spec_risk_top_candidates": rank_spec_coverage["top_candidates"],
            "atr_band_coverage": band_parts.get("band_coverage", pd.DataFrame()).to_dict(orient="records"),
            "atr_band_checkpoints": band_parts.get("band_checkpoints", pd.DataFrame()).to_dict(orient="records"),
        },
    }


def render_weekly_review_markdown(payload: dict[str, object]) -> str:
    summary = payload.get("summary", {})
    decisions = payload.get("decisions", {})
    tables = payload.get("tables", {})
    signal_dates = summary.get("signal_dates", [])
    signal_range = f"{signal_dates[0]} → {signal_dates[-1]}" if signal_dates else "n/a"
    spec_risk_overview = summary.get("spec_risk_overview", {}) if isinstance(summary, dict) else {}
    rank_coverage_guidance = summary.get("rank_coverage_guidance", {}) if isinstance(summary, dict) else {}
    candidate_expansion_plan = summary.get("candidate_expansion_plan", {}) if isinstance(summary, dict) else {}
    candidate_source_plan = summary.get("candidate_source_plan", {}) if isinstance(summary, dict) else {}
    candidate_fill_directions = summary.get("candidate_fill_directions", {}) if isinstance(summary, dict) else {}
    watchlist_gap_snapshot = summary.get("watchlist_gap_snapshot", {}) if isinstance(summary, dict) else {}
    short_gate_tuning_draft = summary.get("short_gate_tuning_draft", {}) if isinstance(summary, dict) else {}
    research_diagnostics = summary.get("research_diagnostics", {}) if isinstance(summary, dict) else {}
    data_quality_gate = summary.get("data_quality_gate", {}) if isinstance(summary, dict) else {}
    top_subtype = spec_risk_overview.get("top_subtype", {}) if isinstance(spec_risk_overview, dict) else {}
    weakest_subtype = spec_risk_overview.get("weakest_subtype", {}) if isinstance(spec_risk_overview, dict) else {}
    same_subtype_extremes = bool(spec_risk_overview.get("same_subtype_extremes", False)) if isinstance(spec_risk_overview, dict) else False

    lines = [
        "# Weekly Review",
        f"- Generated: {payload.get('generated_at', '')}",
        f"- Source: {payload.get('source', '')}",
        f"- Signal dates: `{signal_range}`",
        f"- Included signal_date count: `{len(signal_dates)}`",
        f"- Outcome rows: `{summary.get('row_count', 0)}`",
        f"- OK rows: `{summary.get('ok_rows', 0)}`",
        f"- Non-normal spec-risk rows: `{spec_risk_overview.get('non_normal_rows', 0) if isinstance(spec_risk_overview, dict) else 0}`",
        "",
        "## Decisions",
        "",
    ]
    for key in ["threshold", "short_gate", "atr", "feedback", "spec_risk"]:
        item = decisions.get(key, {})
        lines.append(f"- `{key}`: `{item.get('status', 'hold')}` — {item.get('detail', '')}")

    lines.extend(["", "## Data Quality Gate", ""])
    if isinstance(data_quality_gate, dict):
        lines.append(f"- Status: `{data_quality_gate.get('status', 'unknown')}`")
        for note in data_quality_gate.get("notes", []):
            lines.append(f"- {note}")
        metrics = data_quality_gate.get("metrics", {})
        if isinstance(metrics, dict):
            lines.append(
                f"- Rows: `snapshots={metrics.get('snapshot_rows', 0)}` / `outcomes={metrics.get('outcome_rows', 0)}` / "
                f"`ok={metrics.get('ok_rows', 0)}` / `pending={metrics.get('pending_rows', 0)}`."
            )
            lines.append(
                f"- Keys: `snapshot_dup={metrics.get('snapshot_dup_keys', 0)}` / "
                f"`outcome_dup={metrics.get('outcome_dup_keys', 0)}` / "
                f"`signal_date_missing={metrics.get('signal_date_missing_rows', 0)}` / "
                f"`no_price_series={metrics.get('no_price_series_rows', 0)}`."
            )
    else:
        lines.append("- `_No data quality gate yet._`")

    lines.extend(["", "## Research Diagnostics", ""])
    if isinstance(research_diagnostics, dict):
        for note in research_diagnostics.get("notes", []):
            lines.append(f"- {note}")
        recent_sensitivity = research_diagnostics.get("recent_sensitivity", {})
        if recent_sensitivity:
            lines.append(
                f"- Recent sensitivity watch: `{recent_sensitivity.get('config_name', '')}` in "
                f"`{recent_sensitivity.get('horizon_days', 0)}D {recent_sensitivity.get('watch_type', '')}` "
                f"(`delta={recent_sensitivity.get('delta_avg_ret_vs_baseline', 0.0)}%`, `n={recent_sensitivity.get('n', 0)}`)."
            )
    else:
        lines.append("- `_No research diagnostics yet._`")

    lines.extend(["", "## Spec Risk Highlights", ""])
    if top_subtype:
        lines.append(
            f"- Most frequent subtype: `{top_subtype.get('horizon_days', 0)}D {top_subtype.get('watch_type', '')}` / "
            f"`{top_subtype.get('spec_risk_subtype', '')}` with `n={top_subtype.get('n', 0)}`, "
            f"`avg_ret={top_subtype.get('avg_ret', 0.0)}%`, `win_rate={top_subtype.get('win_rate', 0.0)}%`."
        )
    if weakest_subtype:
        lines.append(
            f"- Weakest subtype so far: `{weakest_subtype.get('horizon_days', 0)}D {weakest_subtype.get('watch_type', '')}` / "
            f"`{weakest_subtype.get('spec_risk_subtype', '')}` with `n={weakest_subtype.get('n', 0)}`, "
            f"`avg_ret={weakest_subtype.get('avg_ret', 0.0)}%`, `win_rate={weakest_subtype.get('win_rate', 0.0)}%`."
        )
    if int(spec_risk_overview.get("non_normal_rows", 0) or 0) < 6:
        lines.append("- Confidence note: non-normal spec-risk rows are still thin, so treat subtype conclusions as directional only.")
    if same_subtype_extremes and top_subtype:
        lines.append("- Interpretation note: the same subtype is currently both the most frequent and the weakest, which usually means sample size is still too small to separate leaders from laggards.")
    if not top_subtype and not weakest_subtype:
        lines.append("- `_No non-normal spec-risk subtype rows yet._`")

    lines.extend(["", "## Candidate Mix Guidance", ""])
    if isinstance(rank_coverage_guidance, dict):
        for note in rank_coverage_guidance.get("notes", []):
            lines.append(f"- {note}")
    if not isinstance(rank_coverage_guidance, dict) or not rank_coverage_guidance.get("notes"):
        lines.append("- `_No candidate-mix guidance yet._`")

    expansion_groups = pd.DataFrame(candidate_expansion_plan.get("groups", [])) if isinstance(candidate_expansion_plan, dict) else pd.DataFrame()
    expansion_layers = pd.DataFrame(candidate_expansion_plan.get("layers", [])) if isinstance(candidate_expansion_plan, dict) else pd.DataFrame()
    lines.extend(["", "## Candidate Expansion Targets", ""])
    lines.extend(["### By Group", _table_markdown(expansion_groups).rstrip(), ""])
    lines.extend(["### By Layer", _table_markdown(expansion_layers).rstrip(), ""])
    if isinstance(candidate_source_plan, dict):
        for note in candidate_source_plan.get("notes", []):
            lines.append(f"- {note}")
    lines.extend(["### By Source Archetype", _table_markdown(pd.DataFrame(candidate_source_plan.get("sources", []) if isinstance(candidate_source_plan, dict) else [])).rstrip(), ""])
    lines.extend(["### Practical Fill Directions", _table_markdown(pd.DataFrame(candidate_fill_directions.get("directions", []) if isinstance(candidate_fill_directions, dict) else [])).rstrip(), ""])
    lines.extend(["### Watchlist Gap Snapshot By Group", _table_markdown(pd.DataFrame(watchlist_gap_snapshot.get("by_group", []) if isinstance(watchlist_gap_snapshot, dict) else [])).rstrip(), ""])
    lines.extend(["### Watchlist Gap Snapshot By Source", _table_markdown(pd.DataFrame(watchlist_gap_snapshot.get("by_source", []) if isinstance(watchlist_gap_snapshot, dict) else [])).rstrip(), ""])

    lines.extend(["", "## 開高不追 Tuning Draft", ""])
    if isinstance(short_gate_tuning_draft, dict) and short_gate_tuning_draft:
        lines.append(f"- Status: `{short_gate_tuning_draft.get('status', 'hold')}`")
        if short_gate_tuning_draft.get("why_now"):
            lines.append(f"- Why now: {short_gate_tuning_draft.get('why_now', '')}")
        if short_gate_tuning_draft.get("proposal"):
            lines.append(f"- Proposal: {short_gate_tuning_draft.get('proposal', '')}")
        for guardrail in short_gate_tuning_draft.get("guardrails", []):
            lines.append(f"- Guardrail: {guardrail}")
        historical = short_gate_tuning_draft.get("historical", {})
        recent = short_gate_tuning_draft.get("recent", {})
        simulation = short_gate_tuning_draft.get("simulation", {})
        if historical:
            lines.append(
                f"- Historical: `below_n={historical.get('below_n', 0)}` / `ok_n={historical.get('ok_n', 0)}` / "
                f"`below-ok={historical.get('delta_avg_ret_below_minus_ok', 0.0)}%` / "
                f"`promotion_ready={historical.get('promotion_ready', False)}`"
            )
        if recent:
            lines.append(
                f"- Recent: `below_n={recent.get('below_n', 0)}` / `ok_n={recent.get('ok_n', 0)}` / "
                f"`below-ok={recent.get('delta_avg_ret_below_minus_ok', 0.0)}%` / "
                f"`promotion_ready={recent.get('promotion_ready', False)}`"
            )
        if simulation:
            lines.append(
                f"- Simulation: `promoted_n={simulation.get('promoted_n', 0)}` / "
                f"`delta_avg_ret={simulation.get('delta_avg_ret_simulated_minus_current', 0.0)}%` / "
                f"`delta_win_rate={simulation.get('delta_win_rate_simulated_minus_current', 0.0)}%`"
            )
    else:
        lines.append("- `_No tuning draft yet._`")

    lines.extend(["", "## Overall By Signal", _table_markdown(pd.DataFrame(tables.get("overall_by_signal", []))).rstrip(), ""])
    lines.extend(["## Threshold Delta", _table_markdown(pd.DataFrame(tables.get("weekly_threshold_delta", []))).rstrip(), ""])
    lines.extend(["## Midlong Threshold Gate", _table_markdown(pd.DataFrame(tables.get("midlong_threshold_gate", []))).rstrip(), ""])
    lines.extend(["## Short Gate Promotion Watch", _table_markdown(pd.DataFrame(tables.get("short_gate_promotion_watch", []))).rstrip(), ""])
    lines.extend(["## Short Gate Simulation", _table_markdown(pd.DataFrame(tables.get("short_gate_simulation", []))).rstrip(), ""])
    lines.extend(["## Full Short Gate Promotion Watch", _table_markdown(pd.DataFrame(tables.get("full_short_gate_promotion_watch", []))).rstrip(), ""])
    lines.extend(["## Full Short Gate Action Context", _table_markdown(pd.DataFrame(tables.get("full_short_gate_action_context", [])).head(20)).rstrip(), ""])
    lines.extend(["## Full Short Gate Simulation", _table_markdown(pd.DataFrame(tables.get("full_short_gate_simulation", []))).rstrip(), ""])
    lines.extend(["## Recent Factor High-Low Spread", _table_markdown(pd.DataFrame(tables.get("recent_factor_high_low_spread", []))).rstrip(), ""])
    lines.extend(["## Full Factor High-Low Spread", _table_markdown(pd.DataFrame(tables.get("full_factor_high_low_spread", []))).rstrip(), ""])
    lines.extend(["## Recent Sensitivity Matrix", _table_markdown(pd.DataFrame(tables.get("recent_sensitivity_matrix", []))).rstrip(), ""])
    lines.extend(["## Full Sensitivity Matrix", _table_markdown(pd.DataFrame(tables.get("full_sensitivity_matrix", []))).rstrip(), ""])
    lines.extend(["## Recent Tail Risk By Action", _table_markdown(pd.DataFrame(tables.get("recent_tail_risk_by_action", []))).rstrip(), ""])
    lines.extend(["## Full Tail Risk By Action", _table_markdown(pd.DataFrame(tables.get("full_tail_risk_by_action", [])).head(80)).rstrip(), ""])
    lines.extend(["## Recent Short Pullback Quality", _table_markdown(pd.DataFrame(tables.get("recent_short_pullback_quality", []))).rstrip(), ""])
    lines.extend(["## Full Short Pullback Quality", _table_markdown(pd.DataFrame(tables.get("full_short_pullback_quality", []))).rstrip(), ""])
    lines.extend(["## Recent Short Pullback Confirmation", _table_markdown(pd.DataFrame(tables.get("recent_short_pullback_confirmation", []))).rstrip(), ""])
    lines.extend(["## Full Short Pullback Confirmation", _table_markdown(pd.DataFrame(tables.get("full_short_pullback_confirmation", []))).rstrip(), ""])
    lines.extend(["## Short Pullback Rule Recommendations", _table_markdown(pd.DataFrame(tables.get("short_pullback_rule_recommendations", []))).rstrip(), ""])
    lines.extend(["## Short Pullback Exit Guard Recommendations", _table_markdown(pd.DataFrame(tables.get("short_pullback_exit_guard_recommendations", []))).rstrip(), ""])
    if isinstance(data_quality_gate, dict):
        lines.extend(["## Data Quality Coverage By Horizon", _table_markdown(pd.DataFrame(data_quality_gate.get("coverage_by_horizon", []))).rstrip(), ""])
        lines.extend(["## Data Quality Coverage By Signal Date", _table_markdown(pd.DataFrame(data_quality_gate.get("coverage_by_signal_date", []))).rstrip(), ""])
    lines.extend(["## Heat Bias Check", _table_markdown(pd.DataFrame(tables.get("heat_bias_check", []))).rstrip(), ""])
    lines.extend(["## Overall By Spec Risk", _table_markdown(pd.DataFrame(tables.get("overall_by_spec_risk", []))).rstrip(), ""])
    lines.extend(["## Overall By Spec Subtype", _table_markdown(pd.DataFrame(tables.get("overall_by_spec_subtype", []))).rstrip(), ""])
    lines.extend(["## Spec Risk Check", _table_markdown(pd.DataFrame(tables.get("spec_risk_check", []))).rstrip(), ""])
    lines.extend(["## Current Rank Spec Risk By Group", _table_markdown(pd.DataFrame(tables.get("current_rank_spec_risk_by_group", []))).rstrip(), ""])
    lines.extend(["## Current Rank Spec Risk By Layer", _table_markdown(pd.DataFrame(tables.get("current_rank_spec_risk_by_layer", []))).rstrip(), ""])
    lines.extend(["## Current Rank Spec Risk By Source", _table_markdown(pd.DataFrame(tables.get("current_rank_spec_risk_by_source", []))).rstrip(), ""])
    lines.extend(["## Current Suspicious Candidates", _table_markdown(pd.DataFrame(tables.get("current_rank_spec_risk_top_candidates", []))).rstrip(), ""])
    lines.extend(["## ATR Band Coverage", _table_markdown(pd.DataFrame(tables.get("atr_band_coverage", []))).rstrip(), ""])
    lines.extend(["## ATR Band Checkpoints", _table_markdown(pd.DataFrame(tables.get("atr_band_checkpoints", []))).rstrip(), ""])
    return "\n".join(lines)


def write_outputs(payload: dict[str, object], *, out: Path, json_out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    json_out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_weekly_review_markdown(payload), encoding="utf-8")
    json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    payload = build_weekly_review_payload(
        outcomes_csv=Path(args.outcomes_csv),
        snapshots_csv=Path(args.snapshots_csv),
        feedback_csv=Path(args.feedback_csv),
        alert_csv=Path(args.alert_csv),
        rank_csv=Path(args.rank_csv),
        watchlist_csv=Path(args.watchlist_csv),
        max_signal_dates=int(args.max_signal_dates),
    )
    write_outputs(payload, out=Path(args.out), json_out=Path(args.json_out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
