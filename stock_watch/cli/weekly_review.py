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


def build_manual_trial_guardrail(
    full_parts: dict[str, pd.DataFrame],
    recent_parts: dict[str, pd.DataFrame],
    *,
    target_action: str = "只觀察不追",
) -> dict[str, object]:
    full_watch = full_parts.get("short_gate_promotion_watch", pd.DataFrame())
    recent_watch = recent_parts.get("short_gate_promotion_watch", pd.DataFrame())

    def _action_rows(df: pd.DataFrame) -> list[dict[str, object]]:
        if df is None or df.empty:
            return []
        work = df.copy()
        work = work[
            (work.get("watch_type", "").astype(str) == "short")
            & (work.get("action", "").astype(str) == target_action)
        ].copy()
        if work.empty:
            return []
        for col in [
            "horizon_days",
            "below_n",
            "ok_n",
            "delta_avg_ret_below_minus_ok",
            "delta_win_rate_below_minus_ok",
        ]:
            if col in work.columns:
                work[col] = pd.to_numeric(work[col], errors="coerce")
        work = work.sort_values(by=["horizon_days"], ascending=[True])
        rows: list[dict[str, object]] = []
        for _, row in work.iterrows():
            rows.append(
                {
                    "horizon_days": int(row.get("horizon_days", 0) or 0),
                    "below_n": int(row.get("below_n", 0) or 0),
                    "ok_n": int(row.get("ok_n", 0) or 0),
                    "confidence": str(row.get("confidence", "low")),
                    "delta_avg_ret_below_minus_ok": round(float(row.get("delta_avg_ret_below_minus_ok", 0.0) or 0.0), 2),
                    "promotion_ready": bool(row.get("promotion_ready", False)),
                    "verdict": str(row.get("verdict", "")),
                }
            )
        return rows

    full_rows = _action_rows(full_watch)
    recent_rows = _action_rows(recent_watch)
    has_positive_edge = any(float(row.get("delta_avg_ret_below_minus_ok", 0.0) or 0.0) > 0 for row in full_rows)
    has_review_signal = any(bool(row.get("promotion_ready", False)) for row in full_rows) or has_positive_edge

    return {
        "target_action": target_action,
        "status": "manual_only" if has_review_signal else "hold",
        "trial_cap": "<= 1/3 test position",
        "why_now": (
            f"`{target_action}` 的短線候補已有正向 shadow evidence，但樣本與 spec-risk 結構仍不足以自動升格。"
            if has_review_signal
            else f"`{target_action}` 尚未累積足夠 shadow evidence。"
        ),
        "proposal": "保留 shadow-only；只有人工點名時才允許 1/3 以下試單，不進正式 Telegram 自動推薦。",
        "guardrails": [
            "不改正式 short gate。",
            "不自動推播成可買標的。",
            "單筆最多 1/3 試單，且需明確停損。",
            "若 spec_risk 不是 normal，視為高風險人工觀察，不得加碼。",
        ],
        "historical": full_rows,
        "recent": recent_rows,
    }


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


def _trade_sim_position_fraction(position_size: object) -> float:
    text = str(position_size or "").strip()
    if "0.25" in text:
        return 0.25
    if "0.5" in text:
        return 0.5
    if "1" in text and "0" not in text:
        return 1.0
    return 0.0


def _shadow_trade_decision(pullback_quality: object, confirmation: object) -> tuple[str, str, str]:
    quality_text = str(pullback_quality or "")
    confirmation_text = str(confirmation or "")
    if confirmation_text != "隔日轉強":
        return "不進場", "0 倉", "隔日沒有轉強，shadow mode 不把它算成可進場。"
    if quality_text == "高風險拉回":
        return "可小試", "0.25 倉", "隔日轉強確認後才小倉，快停損、不攤平。"
    if quality_text == "健康拉回":
        return "可試單", "0.5 倉", "隔日轉強且拉回健康，可用正常試單倉位。"
    if quality_text == "需確認拉回":
        return "只觀察", "0 倉", "即使隔日轉強也先不升級，等待更多資料排除 tail risk。"
    return "暫不買", "0 倉", "承接或結構不乾淨，等量價恢復後再重新分類。"


def build_short_pullback_trade_simulation_shadow(outcomes: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "rule",
        "confirmation",
        "entry_assumption",
        "mode",
        "entry_decision",
        "position_size",
        "position_fraction",
        "n",
        "win_rate_5d_after_entry",
        "avg_trade_ret_5d",
        "avg_position_ret_5d",
        "tail25_trade_ret_5d",
        "worst_trade_ret_5d",
        "best_trade_ret_5d",
        "profit_factor",
        "status",
        "guidance",
        "examples",
    ]
    if outcomes.empty:
        return pd.DataFrame(columns=columns)
    required = {"signal_date", "ticker", "watch_type", "action", "horizon_days", "realized_ret_pct", "status"}
    if not required.issubset(set(outcomes.columns)):
        return pd.DataFrame(columns=columns)

    work = outcomes.copy()
    work = work[
        (work["status"].astype(str) == "ok")
        & (work["watch_type"].astype(str) == "short")
        & (work["action"].astype(str) == "等拉回")
    ].copy()
    if work.empty:
        return pd.DataFrame(columns=columns)

    work["_horizon"] = pd.to_numeric(work["horizon_days"], errors="coerce")
    work["_ret"] = pd.to_numeric(work["realized_ret_pct"], errors="coerce")
    work = work[work["_horizon"].isin([1, 5])].dropna(subset=["_ret"]).copy()
    if work.empty:
        return pd.DataFrame(columns=columns)

    keys = ["signal_date", "ticker"]
    base = work.sort_values(by=keys + ["_horizon"]).drop_duplicates(subset=keys, keep="first").copy()
    base["rule"] = base.apply(classify_short_pullback_quality, axis=1)
    paired_columns = keys + ["rule"]
    if "name" in base.columns:
        paired_columns.insert(2, "name")

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
    paired = base[paired_columns].merge(ret1, on=keys, how="inner").merge(ret5, on=keys, how="inner")
    if paired.empty:
        return pd.DataFrame(columns=columns)

    paired["confirmation"] = paired["ret1_pct"].map(next_session_confirmation_bucket)
    decisions = paired.apply(lambda row: _shadow_trade_decision(row["rule"], row["confirmation"]), axis=1)
    paired["entry_decision"] = decisions.map(lambda item: item[0])
    paired["position_size"] = decisions.map(lambda item: item[1])
    paired["guidance"] = decisions.map(lambda item: item[2])
    paired["position_fraction"] = paired["position_size"].map(_trade_sim_position_fraction)
    valid_entry_base = 1 + (paired["ret1_pct"] / 100)
    paired = paired[valid_entry_base > 0].copy()
    if paired.empty:
        return pd.DataFrame(columns=columns)

    paired["_trade_ret"] = (((1 + (paired["ret5_realized_pct"] / 100)) / (1 + (paired["ret1_pct"] / 100))) - 1) * 100
    paired["_position_ret"] = paired["_trade_ret"] * paired["position_fraction"]
    paired["_win"] = paired["_trade_ret"] > 0
    name_col = "name" if "name" in paired.columns else "ticker"

    def _profit_factor(series: pd.Series) -> float:
        gains = float(series[series > 0].sum())
        losses = float(series[series < 0].sum())
        if abs(losses) == 0:
            return 999.0 if gains > 0 else 0.0
        return round(gains / abs(losses), 2)

    grouped = (
        paired.groupby(
            ["rule", "confirmation", "entry_decision", "position_size", "position_fraction", "guidance"],
            dropna=False,
        )
        .agg(
            n=("_trade_ret", "size"),
            win_rate_5d_after_entry=("_win", lambda series: round(float(series.mean()) * 100, 1) if len(series) else 0.0),
            avg_trade_ret_5d=("_trade_ret", lambda series: round(float(series.mean()), 2)),
            avg_position_ret_5d=("_position_ret", lambda series: round(float(series.mean()), 2)),
            tail25_trade_ret_5d=("_trade_ret", lambda series: round(float(series.quantile(0.25)), 2)),
            worst_trade_ret_5d=("_trade_ret", lambda series: round(float(series.min()), 2)),
            best_trade_ret_5d=("_trade_ret", lambda series: round(float(series.max()), 2)),
            profit_factor=("_trade_ret", _profit_factor),
            examples=(name_col, lambda series: "、".join(series.dropna().astype(str).head(3).tolist())),
        )
        .reset_index()
    )
    grouped.insert(2, "entry_assumption", "隔日確認收盤進")
    grouped.insert(3, "mode", "shadow")

    def _status(row: pd.Series) -> str:
        if float(row.get("position_fraction", 0.0) or 0.0) <= 0:
            return "blocked_no_entry"
        if int(row.get("n", 0) or 0) < 5:
            return "shadow_low_sample"
        if float(row.get("worst_trade_ret_5d", 0.0) or 0.0) <= -8 or float(row.get("tail25_trade_ret_5d", 0.0) or 0.0) <= -4:
            return "shadow_tail_risk"
        if (
            float(row.get("avg_trade_ret_5d", 0.0) or 0.0) > 0
            and float(row.get("profit_factor", 0.0) or 0.0) >= 1.2
            and float(row.get("win_rate_5d_after_entry", 0.0) or 0.0) >= 50
        ):
            return "shadow_candidate"
        return "shadow_watch"

    grouped["status"] = grouped.apply(_status, axis=1)
    return grouped[columns].sort_values(
        by=["position_fraction", "worst_trade_ret_5d", "n", "rule"],
        ascending=[False, True, False, True],
    )


def build_trade_simulation_shadow_decision(trade_simulation: pd.DataFrame) -> dict[str, object]:
    if trade_simulation.empty:
        return {
            "status": "shadow_only",
            "detail": "`trade simulation` 已設定為事後分析；目前缺少可配對的 `1D/5D` 拉回樣本，不進 Telegram。",
        }

    work = trade_simulation.copy()
    work["position_fraction"] = pd.to_numeric(work.get("position_fraction"), errors="coerce").fillna(0.0)
    actionable = work[work["position_fraction"] > 0].copy()
    if actionable.empty:
        return {
            "status": "shadow_only",
            "detail": "`trade simulation` 目前沒有通過進場閘門的樣本；維持事後分析，不進 Telegram。",
        }

    actionable = actionable.sort_values(
        by=["position_fraction", "worst_trade_ret_5d", "n"],
        ascending=[False, True, False],
    )
    top_row = actionable.iloc[0]
    return {
        "status": "shadow_only",
        "detail": (
            "`trade simulation` 採 `隔日確認收盤進`，目前只進 weekly/research shadow mode，不進 Telegram。"
            f" 代表規則 `{top_row.get('rule', '')} + {top_row.get('confirmation', '')}`："
            f"`n={int(top_row.get('n', 0))}`、"
            f"`avg_trade={float(top_row.get('avg_trade_ret_5d', 0.0)):.2f}%`、"
            f"`avg_position={float(top_row.get('avg_position_ret_5d', 0.0)):.2f}%`、"
            f"`worst={float(top_row.get('worst_trade_ret_5d', 0.0)):.2f}%`、"
            f"`status={top_row.get('status', '')}`。"
        ),
    }


def _weekly_segment_label(series: pd.Series) -> pd.Series:
    values = series.fillna("").astype(str).str.strip()
    return values.mask(values.isin(["", "b''", "nan", "None"]), "unknown")


def build_hold_continuation_diagnostics(outcomes: pd.DataFrame, *, min_n: int = 5) -> pd.DataFrame:
    columns = [
        "segment_type",
        "segment_value",
        "watch_type",
        "n",
        "signal_dates",
        "win_rate_5d",
        "continuation_win_rate",
        "avg_1d",
        "avg_5d",
        "avg_continuation_1d_to_5d",
        "hold_edge_5d_vs_1d",
        "tail25_5d",
        "worst_5d",
        "best_5d",
        "status",
        "hold_read",
        "examples",
    ]
    if outcomes.empty:
        return pd.DataFrame(columns=columns)
    required = {"signal_date", "ticker", "watch_type", "horizon_days", "realized_ret_pct", "status"}
    if not required.issubset(set(outcomes.columns)):
        return pd.DataFrame(columns=columns)

    work = outcomes.copy()
    work = work[
        (work["status"].astype(str) == "ok")
        & (work["watch_type"].astype(str).isin(["short", "midlong"]))
    ].copy()
    if work.empty:
        return pd.DataFrame(columns=columns)

    work["_horizon"] = pd.to_numeric(work["horizon_days"], errors="coerce")
    work["_ret"] = pd.to_numeric(work["realized_ret_pct"], errors="coerce")
    work = work[work["_horizon"].isin([1, 5])].dropna(subset=["_ret"]).copy()
    if work.empty:
        return pd.DataFrame(columns=columns)

    for segment_col in ["action", "scenario_label", "market_heat", "reco_status"]:
        if segment_col not in work.columns:
            work[segment_col] = "unknown"
        work[segment_col] = _weekly_segment_label(work[segment_col])

    keys = ["signal_date", "ticker", "watch_type"]
    base = work.sort_values(by=keys + ["_horizon"]).drop_duplicates(subset=keys, keep="first").copy()
    paired_columns = keys + ["action", "scenario_label", "market_heat", "reco_status"]
    if "name" in base.columns:
        paired_columns.insert(2, "name")

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
        .rename("ret5_pct")
        .reset_index()
    )
    paired = base[paired_columns].merge(ret1, on=keys, how="inner").merge(ret5, on=keys, how="inner")
    if paired.empty:
        return pd.DataFrame(columns=columns)

    valid_base = 1 + (paired["ret1_pct"] / 100)
    paired = paired[valid_base > 0].copy()
    if paired.empty:
        return pd.DataFrame(columns=columns)
    paired["continuation_ret_1d_to_5d"] = (((1 + (paired["ret5_pct"] / 100)) / (1 + (paired["ret1_pct"] / 100))) - 1) * 100
    paired["hold_edge_5d_vs_1d"] = paired["ret5_pct"] - paired["ret1_pct"]
    paired["_win5"] = paired["ret5_pct"] > 0
    paired["_continuation_win"] = paired["continuation_ret_1d_to_5d"] > 0
    paired["_example"] = paired["name"] if "name" in paired.columns else paired["ticker"]

    frames = [paired.assign(segment_type="overall", segment_value="all")]
    for segment_col in ["action", "scenario_label", "market_heat", "reco_status"]:
        frames.append(paired.assign(segment_type=segment_col, segment_value=paired[segment_col]))
    frames.append(paired.assign(segment_type="action+scenario", segment_value=paired["action"] + " / " + paired["scenario_label"]))
    long = pd.concat(frames, ignore_index=True)

    grouped = (
        long.groupby(["segment_type", "segment_value", "watch_type"], dropna=False)
        .agg(
            n=("ret5_pct", "size"),
            signal_dates=("signal_date", lambda series: int(series.astype(str).nunique())),
            win_rate_5d=("_win5", lambda series: round(float(series.mean()) * 100, 1) if len(series) else 0.0),
            continuation_win_rate=("_continuation_win", lambda series: round(float(series.mean()) * 100, 1) if len(series) else 0.0),
            avg_1d=("ret1_pct", lambda series: round(float(series.mean()), 2)),
            avg_5d=("ret5_pct", lambda series: round(float(series.mean()), 2)),
            avg_continuation_1d_to_5d=("continuation_ret_1d_to_5d", lambda series: round(float(series.mean()), 2)),
            hold_edge_5d_vs_1d=("hold_edge_5d_vs_1d", lambda series: round(float(series.mean()), 2)),
            tail25_5d=("ret5_pct", lambda series: round(float(series.quantile(0.25)), 2)),
            worst_5d=("ret5_pct", lambda series: round(float(series.min()), 2)),
            best_5d=("ret5_pct", lambda series: round(float(series.max()), 2)),
            examples=("_example", lambda series: "、".join(series.dropna().astype(str).head(3).tolist())),
        )
        .reset_index()
    )

    def _status(row: pd.Series) -> str:
        n = int(row.get("n", 0) or 0)
        avg_continuation = float(row.get("avg_continuation_1d_to_5d", 0.0) or 0.0)
        win_rate = float(row.get("win_rate_5d", 0.0) or 0.0)
        worst = float(row.get("worst_5d", 0.0) or 0.0)
        if n < min_n:
            return "need_more_samples"
        if avg_continuation >= 0.5 and win_rate >= 55.0 and worst > -8.0:
            return "hold_candidate"
        if avg_continuation >= 0.0 and worst <= -8.0:
            return "hold_with_tail_risk"
        if avg_continuation <= -0.5:
            return "fade_after_1d"
        return "keep_shadow"

    def _read(row: pd.Series) -> str:
        status = str(row.get("status", ""))
        if status == "hold_candidate":
            return "1D 後續抱到 5D 仍有正延伸，先列研究候選；不是自動加倉。"
        if status == "hold_with_tail_risk":
            return "續抱有機會，但尾端虧損仍大；若操作只能小倉且用硬風控。"
        if status == "fade_after_1d":
            return "第 1 天後延伸轉弱，偏向有利就收或等重新站穩。"
        if status == "need_more_samples":
            return "樣本太少，先累積，不進規則。"
        return "訊號不夠乾淨，維持 shadow 觀察。"

    grouped["status"] = grouped.apply(_status, axis=1)
    grouped["hold_read"] = grouped.apply(_read, axis=1)
    status_order = {
        "hold_candidate": 0,
        "hold_with_tail_risk": 1,
        "fade_after_1d": 2,
        "keep_shadow": 3,
        "need_more_samples": 4,
    }
    grouped["_status_order"] = grouped["status"].map(status_order).fillna(9)
    grouped = grouped.sort_values(
        by=["_status_order", "watch_type", "n", "avg_continuation_1d_to_5d"],
        ascending=[True, True, False, False],
    ).drop(columns=["_status_order"])
    return grouped[columns]


def build_atr_exit_verification(band_checkpoints: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "horizon_days",
        "watch_type",
        "n",
        "path_n",
        "sequence_n",
        "close_stop_rate_pct",
        "touch_stop_rate_pct",
        "intraday_stop_only_rate_pct",
        "stop_recovered_rate_pct",
        "trim_first_rate_pct",
        "stop_first_rate_pct",
        "same_day_stop_trim_rate_pct",
        "trim_failed_rate_pct",
        "avg_ret_pct",
        "avg_mfe_pct",
        "avg_mae_pct",
        "worst_mae_pct",
        "exit_read",
        "status",
        "next_action",
    ]
    if band_checkpoints.empty:
        return pd.DataFrame(columns=columns)

    work = band_checkpoints.copy()
    required = {"horizon_days", "watch_type", "n"}
    if not required.issubset(set(work.columns)):
        return pd.DataFrame(columns=columns)
    numeric_cols = [
        "horizon_days",
        "n",
        "path_n",
        "sequence_n",
        "closed_below_stop_rate_pct",
        "touched_below_stop_rate_pct",
        "stop_touch_recovered_rate_pct",
        "trim_before_stop_rate_pct",
        "stop_before_trim_rate_pct",
        "same_day_stop_trim_rate_pct",
        "trim_touch_failed_rate_pct",
        "avg_ret_pct",
        "avg_mfe_pct",
        "avg_mae_pct",
        "worst_mae_pct",
    ]
    for col in numeric_cols:
        if col in work.columns:
            work[col] = pd.to_numeric(work[col], errors="coerce")

    rows: list[dict[str, object]] = []
    for _, row in work.iterrows():
        horizon = int(row.get("horizon_days", 0) or 0)
        if horizon not in {5, 20}:
            continue
        n = int(row.get("n", 0) or 0)
        path_n = int(row.get("path_n", 0) or 0)
        sequence_n = int(row.get("sequence_n", 0) or 0)
        close_stop_rate = float(row.get("closed_below_stop_rate_pct", 0.0) or 0.0)
        touch_stop_rate = float(row.get("touched_below_stop_rate_pct", 0.0) or 0.0)
        intraday_stop_only = max(0.0, round(touch_stop_rate - close_stop_rate, 1))
        stop_recovered_rate = float(row.get("stop_touch_recovered_rate_pct", 0.0) or 0.0)
        trim_first_rate = float(row.get("trim_before_stop_rate_pct", 0.0) or 0.0)
        stop_first_rate = float(row.get("stop_before_trim_rate_pct", 0.0) or 0.0)
        same_day_rate = float(row.get("same_day_stop_trim_rate_pct", 0.0) or 0.0)
        trim_failed_rate = float(row.get("trim_touch_failed_rate_pct", 0.0) or 0.0)
        worst_mae = float(row.get("worst_mae_pct", 0.0) or 0.0)

        if n < 10 or path_n < 10:
            status = "need_more_samples"
            exit_read = "樣本仍薄，先不要改 exit。"
            next_action = "繼續累積成熟樣本。"
        elif touch_stop_rate > close_stop_rate and stop_recovered_rate >= 50 and stop_first_rate <= 10:
            status = "review_close_stop_bias"
            exit_read = "盤中碰 stop 明顯多於收盤跌破，且不少可收回；硬用 touched-stop 可能太吵。"
            next_action = "優先比較收盤停損 vs 盤中提醒，不直接自動停損。"
        elif stop_first_rate > 10 or worst_mae <= -12:
            status = "review_intraday_tail"
            exit_read = "盤中尾端風險存在，不能只看收盤結果。"
            next_action = "驗證 touched-stop 提醒是否能降低 worst MAE。"
        elif trim_first_rate >= 30 and trim_failed_rate >= 50:
            status = "review_trim_guard"
            exit_read = "常先碰停利但收盤未必守住，停利線更像分批提醒。"
            next_action = "驗證碰 trim 後分批落袋是否優於等收盤。"
        else:
            status = "keep_shadow"
            exit_read = "目前沒有足夠證據改 exit。"
            next_action = "維持 shadow 檢查。"

        rows.append(
            {
                "horizon_days": horizon,
                "watch_type": str(row.get("watch_type", "")),
                "n": n,
                "path_n": path_n,
                "sequence_n": sequence_n,
                "close_stop_rate_pct": round(close_stop_rate, 1),
                "touch_stop_rate_pct": round(touch_stop_rate, 1),
                "intraday_stop_only_rate_pct": intraday_stop_only,
                "stop_recovered_rate_pct": round(stop_recovered_rate, 1),
                "trim_first_rate_pct": round(trim_first_rate, 1),
                "stop_first_rate_pct": round(stop_first_rate, 1),
                "same_day_stop_trim_rate_pct": round(same_day_rate, 1),
                "trim_failed_rate_pct": round(trim_failed_rate, 1),
                "avg_ret_pct": row.get("avg_ret_pct"),
                "avg_mfe_pct": row.get("avg_mfe_pct"),
                "avg_mae_pct": row.get("avg_mae_pct"),
                "worst_mae_pct": row.get("worst_mae_pct"),
                "exit_read": exit_read,
                "status": status,
                "next_action": next_action,
            }
        )

    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows, columns=columns).sort_values(
        by=["status", "horizon_days", "watch_type"],
        ascending=[True, True, True],
    )


def build_atr_exit_decision(
    atr_exit_verification: pd.DataFrame,
    atr_exit_policy_simulation: pd.DataFrame | None = None,
) -> dict[str, object]:
    if atr_exit_verification.empty:
        return {
            "status": "hold",
            "detail": "`ATR exit verification` 尚無足夠成熟樣本；維持 shadow 分析，不改 exit。",
        }

    if isinstance(atr_exit_policy_simulation, pd.DataFrame) and not atr_exit_policy_simulation.empty:
        sim = atr_exit_policy_simulation.copy()
        sim = sim[sim["policy"].astype(str) != "baseline_close"].copy()
        candidates = sim[sim["status"].astype(str) == "research_candidate"].copy()
        if candidates.empty:
            return {
                "status": "hold",
                "detail": "`ATR exit policy simulation` 沒有找到優於 baseline 的 exit policy；維持 shadow，不改 live exit。",
            }
        candidates["delta_worst_vs_baseline"] = pd.to_numeric(candidates.get("delta_worst_vs_baseline"), errors="coerce").fillna(0.0)
        candidates["delta_avg_vs_baseline"] = pd.to_numeric(candidates.get("delta_avg_vs_baseline"), errors="coerce").fillna(0.0)
        candidates = candidates.sort_values(
            by=["delta_worst_vs_baseline", "delta_avg_vs_baseline"],
            ascending=[False, False],
        )
        top_candidate = candidates.iloc[0]
        return {
            "status": "review",
            "detail": (
                "`ATR exit policy simulation` 找到可 review 的 shadow policy："
                f"`{int(top_candidate.get('horizon_days', 0))}D {top_candidate.get('watch_type', '')} "
                f"{top_candidate.get('policy', '')}`，"
                f"`delta_avg={float(top_candidate.get('delta_avg_vs_baseline', 0.0)):.2f}%`、"
                f"`delta_worst={float(top_candidate.get('delta_worst_vs_baseline', 0.0)):.2f}%`。"
            ),
        }

    work = atr_exit_verification.copy()
    review = work[work["status"].astype(str).str.startswith("review_")].copy()
    if review.empty:
        return {
            "status": "hold",
            "detail": "`ATR exit verification` 目前沒有需要升級討論的 exit pattern；維持 shadow 分析。",
        }

    priority = {
        "review_intraday_tail": 0,
        "review_close_stop_bias": 1,
        "review_trim_guard": 2,
    }
    review["_priority"] = review["status"].map(priority).fillna(9)
    review["_worst_mae"] = pd.to_numeric(review.get("worst_mae_pct"), errors="coerce").fillna(0.0)
    review = review.sort_values(by=["_priority", "_worst_mae", "horizon_days", "watch_type"]).copy()
    top = review.iloc[0]
    return {
        "status": "review",
        "detail": (
            "`ATR exit verification` 已完成 shadow 分析；先不改 live exit。"
            f" 目前最需要 review：`{int(top.get('horizon_days', 0))}D {top.get('watch_type', '')}` "
            f"`{top.get('status', '')}`，{top.get('next_action', '')}"
        ),
    }


def build_atr_exit_policy_simulation(alert_tracking: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "horizon_days",
        "watch_type",
        "policy",
        "n",
        "stop_exit_count",
        "trim_exit_count",
        "win_rate",
        "avg_ret",
        "tail25_ret",
        "worst_ret",
        "best_ret",
        "delta_avg_vs_baseline",
        "delta_worst_vs_baseline",
        "status",
        "read",
    ]
    if alert_tracking.empty:
        return pd.DataFrame(columns=columns)
    required = {"watch_type", "alert_close", "trim_price", "stop_price"}
    if not required.issubset(set(alert_tracking.columns)):
        return pd.DataFrame(columns=columns)

    work = alert_tracking.copy()
    work["watch_type"] = work["watch_type"].astype(str).str.strip().str.lower()
    work = work[work["watch_type"].isin(["short", "midlong"])].copy()
    for col in ["alert_close", "trim_price", "stop_price"]:
        work[col] = pd.to_numeric(work[col], errors="coerce")
    work = work.dropna(subset=["alert_close", "trim_price", "stop_price"]).copy()
    if work.empty:
        return pd.DataFrame(columns=columns)

    rows: list[dict[str, object]] = []

    def _policy_row(
        horizon: int,
        watch_type: str,
        policy: str,
        returns: pd.Series,
        baseline: pd.Series,
        stop_exits: pd.Series,
        trim_exits: pd.Series,
    ) -> dict[str, object]:
        returns = pd.to_numeric(returns, errors="coerce").dropna()
        baseline = pd.to_numeric(baseline, errors="coerce").reindex(returns.index).dropna()
        returns = returns.reindex(baseline.index).dropna()
        stop_exits = stop_exits.reindex(returns.index).fillna(False).astype(bool)
        trim_exits = trim_exits.reindex(returns.index).fillna(False).astype(bool)
        if returns.empty:
            return {}
        baseline_avg = float(baseline.mean()) if len(baseline) else 0.0
        baseline_worst = float(baseline.min()) if len(baseline) else 0.0
        avg_ret = float(returns.mean())
        worst_ret = float(returns.min())
        delta_avg = avg_ret - baseline_avg
        delta_worst = worst_ret - baseline_worst
        if len(returns) < 10:
            status = "need_more_samples"
            read = "樣本仍薄，先只保留 shadow 比較。"
        elif policy == "baseline_close":
            status = "baseline"
            read = "基準：持有到 horizon close，不套用 ATR exit。"
        elif delta_avg >= -0.5 and delta_worst >= 0.5:
            status = "research_candidate"
            read = "尾端改善且平均報酬未明顯惡化，值得進一步拆樣本。"
        elif delta_worst >= 0.5 and delta_avg < -0.5:
            status = "tail_hedge_costly"
            read = "尾端改善但犧牲平均報酬，適合作為提醒，不宜直接硬規則。"
        elif delta_avg <= -0.5 and delta_worst <= 0:
            status = "worse_than_baseline"
            read = "平均與尾端都沒有優於基準，暫不升級。"
        else:
            status = "keep_shadow"
            read = "效果不夠明確，繼續累積資料。"
        return {
            "horizon_days": horizon,
            "watch_type": watch_type,
            "policy": policy,
            "n": int(len(returns)),
            "stop_exit_count": int(stop_exits.sum()),
            "trim_exit_count": int(trim_exits.sum()),
            "win_rate": round(float((returns > 0).mean()) * 100, 1),
            "avg_ret": round(avg_ret, 2),
            "tail25_ret": round(float(returns.quantile(0.25)), 2),
            "worst_ret": round(worst_ret, 2),
            "best_ret": round(float(returns.max()), 2),
            "delta_avg_vs_baseline": round(delta_avg, 2),
            "delta_worst_vs_baseline": round(delta_worst, 2),
            "status": status,
            "read": read,
        }

    for horizon in [5, 20]:
        ret_col = f"ret{horizon}_future_pct"
        trim_day_col = f"trim{horizon}_touch_day"
        stop_day_col = f"stop{horizon}_touch_day"
        trim_before_col = f"trim{horizon}_before_stop"
        stop_before_col = f"stop{horizon}_before_trim"
        if ret_col not in work.columns:
            continue
        horizon_work = work.copy()
        horizon_work[ret_col] = pd.to_numeric(horizon_work[ret_col], errors="coerce")
        for col in [trim_day_col, stop_day_col, trim_before_col, stop_before_col]:
            if col in horizon_work.columns:
                horizon_work[col] = pd.to_numeric(horizon_work[col], errors="coerce").fillna(0)
            else:
                horizon_work[col] = 0
        horizon_work = horizon_work.dropna(subset=[ret_col]).copy()
        if horizon_work.empty:
            continue
        horizon_work["_stop_ret"] = ((horizon_work["stop_price"] / horizon_work["alert_close"]) - 1) * 100
        horizon_work["_trim_ret"] = ((horizon_work["trim_price"] / horizon_work["alert_close"]) - 1) * 100
        horizon_work["_final_close"] = horizon_work["alert_close"] * (1 + horizon_work[ret_col] / 100)
        horizon_work["_stop_touched"] = horizon_work[stop_day_col] > 0
        horizon_work["_trim_touched"] = horizon_work[trim_day_col] > 0
        horizon_work["_stop_before_trim"] = horizon_work[stop_before_col] > 0
        horizon_work["_trim_before_stop"] = horizon_work[trim_before_col] > 0
        horizon_work["_close_stop_exit"] = horizon_work["_final_close"] <= horizon_work["stop_price"]

        for watch_type, group in horizon_work.groupby("watch_type", dropna=False):
            baseline = group[ret_col]
            policy_returns = {
                "baseline_close": (
                    baseline,
                    pd.Series(False, index=group.index),
                    pd.Series(False, index=group.index),
                ),
                "close_stop_exit": (
                    group["_stop_ret"].where(group["_close_stop_exit"], baseline),
                    group["_close_stop_exit"],
                    pd.Series(False, index=group.index),
                ),
                "touched_stop_exit": (
                    group["_stop_ret"].where(group["_stop_touched"], baseline),
                    group["_stop_touched"],
                    pd.Series(False, index=group.index),
                ),
                "trim_touch_half": (
                    ((group["_trim_ret"] * 0.5) + (baseline * 0.5)).where(group["_trim_touched"], baseline),
                    pd.Series(False, index=group.index),
                    group["_trim_touched"],
                ),
                "sequence_stop_or_trim_half": (
                    group["_stop_ret"].where(
                        group["_stop_before_trim"],
                        ((group["_trim_ret"] * 0.5) + (baseline * 0.5)).where(group["_trim_touched"], baseline),
                    ),
                    group["_stop_before_trim"],
                    group["_trim_touched"] & ~group["_stop_before_trim"],
                ),
            }
            for policy, (returns, stop_exits, trim_exits) in policy_returns.items():
                row = _policy_row(horizon, str(watch_type), policy, returns, baseline, stop_exits, trim_exits)
                if row:
                    rows.append(row)

    if not rows:
        return pd.DataFrame(columns=columns)
    policy_order = {
        "baseline_close": 0,
        "close_stop_exit": 1,
        "touched_stop_exit": 2,
        "trim_touch_half": 3,
        "sequence_stop_or_trim_half": 4,
    }
    result = pd.DataFrame(rows, columns=columns)
    result["_policy_order"] = result["policy"].map(policy_order).fillna(99)
    result = result.sort_values(by=["horizon_days", "watch_type", "_policy_order"]).drop(columns=["_policy_order"])
    return result


def build_atr_exit_policy_segment_simulation(
    alert_tracking: pd.DataFrame,
    *,
    min_segment_n: int = 10,
) -> pd.DataFrame:
    columns = [
        "segment_type",
        "segment_value",
        "horizon_days",
        "watch_type",
        "policy",
        "n",
        "stop_exit_count",
        "trim_exit_count",
        "win_rate",
        "avg_ret",
        "tail25_ret",
        "worst_ret",
        "best_ret",
        "delta_avg_vs_baseline",
        "delta_worst_vs_baseline",
        "status",
        "read",
    ]
    if alert_tracking.empty:
        return pd.DataFrame(columns=columns)

    segment_cols = [col for col in ["action_label", "scenario_label"] if col in alert_tracking.columns]
    if not segment_cols:
        return pd.DataFrame(columns=columns)

    rows: list[pd.DataFrame] = []
    for segment_col in segment_cols:
        work = alert_tracking.copy()
        work["_segment_value"] = work[segment_col].fillna("").astype(str).str.strip()
        work.loc[work["_segment_value"].isin(["", "b''", "nan", "None"]), "_segment_value"] = "unknown"
        for segment_value, segment_df in work.groupby("_segment_value", dropna=False):
            simulated = build_atr_exit_policy_simulation(segment_df.drop(columns=["_segment_value"]))
            if simulated.empty:
                continue
            simulated = simulated[pd.to_numeric(simulated["n"], errors="coerce") >= min_segment_n].copy()
            if simulated.empty:
                continue
            simulated.insert(0, "segment_value", str(segment_value))
            simulated.insert(0, "segment_type", segment_col)
            rows.append(simulated)

    if not rows:
        return pd.DataFrame(columns=columns)
    result = pd.concat(rows, ignore_index=True)
    result = result[columns].copy()
    status_order = {
        "research_candidate": 0,
        "tail_hedge_costly": 1,
        "worse_than_baseline": 2,
        "keep_shadow": 3,
        "baseline": 4,
        "need_more_samples": 5,
    }
    result["_status_order"] = result["status"].map(status_order).fillna(99)
    result["delta_worst_vs_baseline"] = pd.to_numeric(result["delta_worst_vs_baseline"], errors="coerce")
    result["delta_avg_vs_baseline"] = pd.to_numeric(result["delta_avg_vs_baseline"], errors="coerce")
    result = result.sort_values(
        by=["_status_order", "delta_worst_vs_baseline", "delta_avg_vs_baseline", "segment_type", "segment_value", "horizon_days", "watch_type", "policy"],
        ascending=[True, False, False, True, True, True, True, True],
    ).drop(columns=["_status_order"])
    return result


def build_weekly_decision_panel(
    decisions: dict[str, dict[str, object]],
    trade_simulation: pd.DataFrame,
    pullback_rules: pd.DataFrame,
    atr_exit_verification: pd.DataFrame | None = None,
    atr_exit_policy_simulation: pd.DataFrame | None = None,
    atr_exit_policy_segment_simulation: pd.DataFrame | None = None,
) -> pd.DataFrame:
    columns = ["bucket", "rule", "source", "status", "evidence", "next_action"]
    rows: list[dict[str, object]] = []

    status_to_bucket = {
        "review": "Need Human Decision",
        "block": "Blocked / Guardrail",
        "hold": "Keep Shadow",
        "shadow_only": "Keep Shadow",
    }
    for rule, decision in decisions.items():
        status = str(decision.get("status", "hold"))
        rows.append(
            {
                "bucket": status_to_bucket.get(status, "Keep Shadow"),
                "rule": rule,
                "source": "weekly_decisions",
                "status": status,
                "evidence": str(decision.get("detail", "")),
                "next_action": "人工確認是否要調規則" if status == "review" else "維持現行規則並累積樣本",
            }
        )

    if isinstance(trade_simulation, pd.DataFrame) and not trade_simulation.empty:
        work = trade_simulation.copy()
        for numeric_col in ["n", "avg_trade_ret_5d", "tail25_trade_ret_5d", "worst_trade_ret_5d", "position_fraction"]:
            if numeric_col in work.columns:
                work[numeric_col] = pd.to_numeric(work[numeric_col], errors="coerce")
        for _, row in work.iterrows():
            status = str(row.get("status", ""))
            rule_name = f"{row.get('rule', '')} + {row.get('confirmation', '')}".strip()
            tail25 = float(row.get("tail25_trade_ret_5d", 0.0) or 0.0)
            worst = float(row.get("worst_trade_ret_5d", 0.0) or 0.0)
            position_fraction = float(row.get("position_fraction", 0.0) or 0.0)
            if status == "shadow_low_sample":
                bucket = "Need More Samples"
                next_action = "繼續 shadow，不進 Telegram；等樣本數提升再討論升級"
            elif position_fraction <= 0 and (tail25 <= -4 or worst <= -8):
                bucket = "Blocked by Tail Risk"
                next_action = "維持不進場；除非後續 tail 明顯改善才重開討論"
            elif status == "shadow_candidate":
                bucket = "Ready to Review"
                next_action = "進入人工 review，不直接升級成 live rule"
            else:
                bucket = "Keep Shadow"
                next_action = "保留觀察，不改 live 規則"
            rows.append(
                {
                    "bucket": bucket,
                    "rule": rule_name,
                    "source": "trade_simulation_shadow",
                    "status": status,
                    "evidence": (
                        f"n={int(row.get('n', 0) or 0)}, "
                        f"avg={float(row.get('avg_trade_ret_5d', 0.0) or 0.0):.2f}%, "
                        f"tail25={tail25:.2f}%, worst={worst:.2f}%"
                    ),
                    "next_action": next_action,
                }
            )

    policy_has_candidate = False
    if isinstance(atr_exit_policy_simulation, pd.DataFrame) and not atr_exit_policy_simulation.empty:
        policy_has_candidate = bool((atr_exit_policy_simulation["status"].astype(str) == "research_candidate").any())

    if isinstance(atr_exit_verification, pd.DataFrame) and not atr_exit_verification.empty:
        atr_work = atr_exit_verification.copy()
        for _, row in atr_work.iterrows():
            status = str(row.get("status", ""))
            if status == "need_more_samples":
                bucket = "Need More Samples"
                next_action = str(row.get("next_action", "繼續累積成熟樣本。"))
            elif status.startswith("review_"):
                bucket = "Ready to Review" if policy_has_candidate else "Keep Shadow"
                next_action = (
                    str(row.get("next_action", "進入 exit policy shadow review。"))
                    if policy_has_candidate
                    else "policy simulation 未支持升級，維持 shadow 觀察。"
                )
            else:
                bucket = "Keep Shadow"
                next_action = str(row.get("next_action", "維持 shadow 檢查。"))
            rows.append(
                {
                    "bucket": bucket,
                    "rule": f"{int(row.get('horizon_days', 0) or 0)}D {row.get('watch_type', '')} ATR exit",
                    "source": "atr_exit_verification",
                    "status": status,
                    "evidence": (
                        f"n={int(row.get('n', 0) or 0)}, "
                        f"touch_stop={float(row.get('touch_stop_rate_pct', 0.0) or 0.0):.1f}%, "
                        f"close_stop={float(row.get('close_stop_rate_pct', 0.0) or 0.0):.1f}%, "
                        f"trim_first={float(row.get('trim_first_rate_pct', 0.0) or 0.0):.1f}%, "
                        f"worst_mae={float(row.get('worst_mae_pct', 0.0) or 0.0):.2f}%"
                    ),
                    "next_action": next_action,
                }
            )

    if isinstance(atr_exit_policy_simulation, pd.DataFrame) and not atr_exit_policy_simulation.empty:
        sim_work = atr_exit_policy_simulation.copy()
        sim_work = sim_work[sim_work["policy"].astype(str) != "baseline_close"].copy()
        if not sim_work.empty:
            sim_work["delta_worst_vs_baseline"] = pd.to_numeric(sim_work.get("delta_worst_vs_baseline"), errors="coerce").fillna(0.0)
            sim_work["delta_avg_vs_baseline"] = pd.to_numeric(sim_work.get("delta_avg_vs_baseline"), errors="coerce").fillna(0.0)
            candidates = sim_work[sim_work["status"].astype(str).isin(["research_candidate", "tail_hedge_costly"])].copy()
            candidates = candidates.sort_values(
                by=["delta_worst_vs_baseline", "delta_avg_vs_baseline"],
                ascending=[False, False],
            ).head(4)
            for _, row in candidates.iterrows():
                status = str(row.get("status", ""))
                bucket = "Ready to Review" if status == "research_candidate" else "Keep Shadow"
                rows.append(
                    {
                        "bucket": bucket,
                        "rule": f"{int(row.get('horizon_days', 0) or 0)}D {row.get('watch_type', '')} {row.get('policy', '')}",
                        "source": "atr_exit_policy_simulation",
                        "status": status,
                        "evidence": (
                            f"n={int(row.get('n', 0) or 0)}, "
                            f"avg={float(row.get('avg_ret', 0.0) or 0.0):.2f}%, "
                            f"worst={float(row.get('worst_ret', 0.0) or 0.0):.2f}%, "
                            f"delta_avg={float(row.get('delta_avg_vs_baseline', 0.0) or 0.0):.2f}%, "
                            f"delta_worst={float(row.get('delta_worst_vs_baseline', 0.0) or 0.0):.2f}%"
                        ),
                        "next_action": str(row.get("read", "保留 shadow policy simulation。")),
                    }
                )

    if isinstance(atr_exit_policy_segment_simulation, pd.DataFrame) and not atr_exit_policy_segment_simulation.empty:
        segment_work = atr_exit_policy_segment_simulation.copy()
        segment_work = segment_work[segment_work["policy"].astype(str) != "baseline_close"].copy()
        segment_work = segment_work[segment_work["status"].astype(str).isin(["research_candidate", "tail_hedge_costly"])].copy()
        if not segment_work.empty:
            segment_work["delta_worst_vs_baseline"] = pd.to_numeric(segment_work.get("delta_worst_vs_baseline"), errors="coerce").fillna(0.0)
            segment_work["delta_avg_vs_baseline"] = pd.to_numeric(segment_work.get("delta_avg_vs_baseline"), errors="coerce").fillna(0.0)
            segment_work = segment_work.sort_values(
                by=["delta_worst_vs_baseline", "delta_avg_vs_baseline"],
                ascending=[False, False],
            ).head(4)
            for _, row in segment_work.iterrows():
                status = str(row.get("status", ""))
                rows.append(
                    {
                        "bucket": "Ready to Review" if status == "research_candidate" else "Keep Shadow",
                        "rule": (
                            f"{row.get('segment_type', '')}={row.get('segment_value', '')} / "
                            f"{int(row.get('horizon_days', 0) or 0)}D {row.get('watch_type', '')} {row.get('policy', '')}"
                        ),
                        "source": "atr_exit_policy_segment_simulation",
                        "status": status,
                        "evidence": (
                            f"n={int(row.get('n', 0) or 0)}, "
                            f"avg={float(row.get('avg_ret', 0.0) or 0.0):.2f}%, "
                            f"worst={float(row.get('worst_ret', 0.0) or 0.0):.2f}%, "
                            f"delta_avg={float(row.get('delta_avg_vs_baseline', 0.0) or 0.0):.2f}%, "
                            f"delta_worst={float(row.get('delta_worst_vs_baseline', 0.0) or 0.0):.2f}%"
                        ),
                        "next_action": str(row.get("read", "保留分層 shadow policy simulation。")),
                    }
                )

    if isinstance(pullback_rules, pd.DataFrame) and not pullback_rules.empty:
        for _, row in pullback_rules.iterrows():
            status = str(row.get("status", ""))
            if status in {"block_upgrade", "blocked"}:
                bucket = "Blocked / Guardrail"
            elif status in {"active_low_sample", "research_only"}:
                bucket = "Need More Samples"
            else:
                bucket = "Keep Shadow"
            rows.append(
                {
                    "bucket": bucket,
                    "rule": f"{row.get('rule', '')} / {row.get('condition', '')}",
                    "source": "pullback_rule_recommendations",
                    "status": status,
                    "evidence": str(row.get("evidence", "")),
                    "next_action": str(row.get("note", "")),
                }
            )

    if not rows:
        return pd.DataFrame(columns=columns)
    panel = pd.DataFrame(rows, columns=columns)
    bucket_order = {
        "Need Human Decision": 0,
        "Ready to Review": 1,
        "Blocked by Tail Risk": 2,
        "Blocked / Guardrail": 3,
        "Need More Samples": 4,
        "Keep Shadow": 5,
    }
    panel["_bucket_order"] = panel["bucket"].map(bucket_order).fillna(99)
    panel = panel.sort_values(by=["_bucket_order", "source", "rule"]).drop(columns=["_bucket_order"])
    return panel


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
    atr_exit_verification = build_atr_exit_verification(band_parts.get("band_checkpoints", pd.DataFrame()))
    atr_exit_policy_simulation = build_atr_exit_policy_simulation(alert_df)
    atr_exit_policy_segment_simulation = build_atr_exit_policy_segment_simulation(alert_df)
    decisions["atr"] = build_atr_exit_decision(atr_exit_verification, atr_exit_policy_simulation)
    spec_risk_overview = build_spec_risk_overview(parts)
    rank_spec_coverage = build_rank_spec_risk_coverage(rank_csv)
    candidate_source_summary = build_rank_candidate_source_summary(rank_csv)
    rank_coverage_guidance = build_rank_coverage_guidance(rank_spec_coverage)
    candidate_expansion_plan = build_candidate_expansion_plan(rank_spec_coverage)
    candidate_source_plan = build_candidate_source_plan(candidate_source_summary)
    candidate_fill_directions = build_candidate_fill_directions(rank_csv, candidate_source_plan)
    watchlist_gap_snapshot = build_watchlist_gap_snapshot(watchlist_csv, candidate_expansion_plan, candidate_source_plan)
    short_gate_tuning_draft = build_short_gate_tuning_draft(full_parts, parts)
    manual_trial_guardrail = build_manual_trial_guardrail(full_parts, parts)
    research_diagnostics = build_research_diagnostics(parts, full_parts)
    data_quality_gate = build_data_quality_gate(outcomes, snapshots)
    recent_pullback_quality = build_pullback_quality_diagnostics(recent_outcomes)
    full_pullback_quality = build_pullback_quality_diagnostics(outcomes)
    recent_pullback_confirmation = build_pullback_confirmation_diagnostics(recent_outcomes)
    full_pullback_confirmation = build_pullback_confirmation_diagnostics(outcomes)
    recent_trade_simulation_shadow = build_short_pullback_trade_simulation_shadow(recent_outcomes)
    full_trade_simulation_shadow = build_short_pullback_trade_simulation_shadow(outcomes)
    recent_hold_continuation = build_hold_continuation_diagnostics(recent_outcomes)
    full_hold_continuation = build_hold_continuation_diagnostics(outcomes)
    decisions["trade_simulation"] = build_trade_simulation_shadow_decision(full_trade_simulation_shadow)
    pullback_rule_recommendations = build_pullback_rule_recommendations(full_pullback_confirmation)
    pullback_exit_guard_recommendations = build_pullback_exit_guard_recommendations(full_pullback_confirmation)
    weekly_decision_panel = build_weekly_decision_panel(
        decisions,
        full_trade_simulation_shadow,
        pullback_rule_recommendations,
        atr_exit_verification,
        atr_exit_policy_simulation,
        atr_exit_policy_segment_simulation,
    )

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
        "manual_trial_guardrail": manual_trial_guardrail,
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
            "weekly_decision_panel": weekly_decision_panel.to_dict(orient="records"),
            "atr_exit_verification": atr_exit_verification.to_dict(orient="records"),
            "atr_exit_policy_simulation": atr_exit_policy_simulation.to_dict(orient="records"),
            "atr_exit_policy_segment_simulation": atr_exit_policy_segment_simulation.to_dict(orient="records"),
            "full_short_gate_promotion_watch": full_parts.get("short_gate_promotion_watch", pd.DataFrame()).to_dict(orient="records"),
            "full_short_gate_action_context": full_parts.get("short_gate_action_context", pd.DataFrame()).to_dict(orient="records"),
            "full_short_gate_simulation": full_parts.get("short_gate_simulation", pd.DataFrame()).to_dict(orient="records"),
            "recent_factor_high_low_spread": parts.get("factor_high_low_spread", pd.DataFrame()).to_dict(orient="records"),
            "full_factor_high_low_spread": full_parts.get("factor_high_low_spread", pd.DataFrame()).to_dict(orient="records"),
            "recent_factor_tear_sheet": parts.get("factor_tear_sheet", pd.DataFrame()).to_dict(orient="records"),
            "full_factor_tear_sheet": full_parts.get("factor_tear_sheet", pd.DataFrame()).to_dict(orient="records"),
            "recent_sensitivity_matrix": parts.get("sensitivity_matrix", pd.DataFrame()).to_dict(orient="records"),
            "full_sensitivity_matrix": full_parts.get("sensitivity_matrix", pd.DataFrame()).to_dict(orient="records"),
            "recent_tail_risk_by_action": parts.get("tail_risk_by_action", pd.DataFrame()).to_dict(orient="records"),
            "full_tail_risk_by_action": full_parts.get("tail_risk_by_action", pd.DataFrame()).to_dict(orient="records"),
            "recent_short_pullback_quality": recent_pullback_quality.to_dict(orient="records"),
            "full_short_pullback_quality": full_pullback_quality.to_dict(orient="records"),
            "recent_short_pullback_confirmation": recent_pullback_confirmation.to_dict(orient="records"),
            "full_short_pullback_confirmation": full_pullback_confirmation.to_dict(orient="records"),
            "recent_short_pullback_trade_simulation_shadow": recent_trade_simulation_shadow.to_dict(orient="records"),
            "full_short_pullback_trade_simulation_shadow": full_trade_simulation_shadow.to_dict(orient="records"),
            "recent_hold_continuation_diagnostics": recent_hold_continuation.to_dict(orient="records"),
            "full_hold_continuation_diagnostics": full_hold_continuation.to_dict(orient="records"),
            "short_pullback_rule_recommendations": pullback_rule_recommendations.to_dict(orient="records"),
            "short_pullback_exit_guard_recommendations": pullback_exit_guard_recommendations.to_dict(orient="records"),
            "current_rank_spec_risk_by_group": rank_spec_coverage["by_group"],
            "current_rank_spec_risk_by_layer": rank_spec_coverage["by_layer"],
            "current_rank_spec_risk_by_source": candidate_source_summary["by_source"],
            "current_rank_spec_risk_top_candidates": rank_spec_coverage["top_candidates"],
            "atr_band_coverage": band_parts.get("band_coverage", pd.DataFrame()).to_dict(orient="records"),
            "atr_band_checkpoints": band_parts.get("band_checkpoints", pd.DataFrame()).to_dict(orient="records"),
            "path_risk_sequencing": band_parts.get("path_risk_sequencing", pd.DataFrame()).to_dict(orient="records"),
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
    manual_trial_guardrail = summary.get("manual_trial_guardrail", {}) if isinstance(summary, dict) else {}
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
    for key in ["threshold", "short_gate", "atr", "feedback", "spec_risk", "trade_simulation"]:
        item = decisions.get(key, {})
        lines.append(f"- `{key}`: `{item.get('status', 'hold')}` — {item.get('detail', '')}")

    lines.extend(["", "## Weekly Decision Panel", _table_markdown(pd.DataFrame(tables.get("weekly_decision_panel", []))).rstrip(), ""])

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

    lines.extend(["", "## Manual Trial Guardrail", ""])
    if isinstance(manual_trial_guardrail, dict) and manual_trial_guardrail:
        lines.append(f"- Target: `{manual_trial_guardrail.get('target_action', '')}`")
        lines.append(f"- Status: `{manual_trial_guardrail.get('status', 'hold')}`")
        lines.append(f"- Trial Cap: `{manual_trial_guardrail.get('trial_cap', '')}`")
        if manual_trial_guardrail.get("why_now"):
            lines.append(f"- Why now: {manual_trial_guardrail.get('why_now', '')}")
        if manual_trial_guardrail.get("proposal"):
            lines.append(f"- Proposal: {manual_trial_guardrail.get('proposal', '')}")
        for guardrail in manual_trial_guardrail.get("guardrails", []):
            lines.append(f"- Guardrail: {guardrail}")
        historical_rows = manual_trial_guardrail.get("historical", [])
        recent_rows = manual_trial_guardrail.get("recent", [])
        if historical_rows:
            lines.append("- Historical evidence:")
            for row in historical_rows:
                lines.append(
                    f"  - `{row.get('horizon_days', 0)}D`: `below_n={row.get('below_n', 0)}` / "
                    f"`below-ok={row.get('delta_avg_ret_below_minus_ok', 0.0)}%` / "
                    f"`promotion_ready={row.get('promotion_ready', False)}`"
                )
        if recent_rows:
            lines.append("- Recent evidence:")
            for row in recent_rows:
                lines.append(
                    f"  - `{row.get('horizon_days', 0)}D`: `below_n={row.get('below_n', 0)}` / "
                    f"`below-ok={row.get('delta_avg_ret_below_minus_ok', 0.0)}%` / "
                    f"`promotion_ready={row.get('promotion_ready', False)}`"
                )
    else:
        lines.append("- `_No manual trial guardrail summary yet._`")

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
    lines.extend(["## Recent Factor Tear Sheet", _table_markdown(pd.DataFrame(tables.get("recent_factor_tear_sheet", [])).head(80)).rstrip(), ""])
    lines.extend(["## Full Factor Tear Sheet", _table_markdown(pd.DataFrame(tables.get("full_factor_tear_sheet", [])).head(80)).rstrip(), ""])
    lines.extend(["## Recent Sensitivity Matrix", _table_markdown(pd.DataFrame(tables.get("recent_sensitivity_matrix", []))).rstrip(), ""])
    lines.extend(["## Full Sensitivity Matrix", _table_markdown(pd.DataFrame(tables.get("full_sensitivity_matrix", []))).rstrip(), ""])
    lines.extend(["## Recent Tail Risk By Action", _table_markdown(pd.DataFrame(tables.get("recent_tail_risk_by_action", []))).rstrip(), ""])
    lines.extend(["## Full Tail Risk By Action", _table_markdown(pd.DataFrame(tables.get("full_tail_risk_by_action", [])).head(80)).rstrip(), ""])
    lines.extend(["## Recent Short Pullback Quality", _table_markdown(pd.DataFrame(tables.get("recent_short_pullback_quality", []))).rstrip(), ""])
    lines.extend(["## Full Short Pullback Quality", _table_markdown(pd.DataFrame(tables.get("full_short_pullback_quality", []))).rstrip(), ""])
    lines.extend(["## Recent Short Pullback Confirmation", _table_markdown(pd.DataFrame(tables.get("recent_short_pullback_confirmation", []))).rstrip(), ""])
    lines.extend(["## Full Short Pullback Confirmation", _table_markdown(pd.DataFrame(tables.get("full_short_pullback_confirmation", []))).rstrip(), ""])
    lines.extend(["## Recent Short Pullback Trade Simulation Shadow", _table_markdown(pd.DataFrame(tables.get("recent_short_pullback_trade_simulation_shadow", []))).rstrip(), ""])
    lines.extend(["## Full Short Pullback Trade Simulation Shadow", _table_markdown(pd.DataFrame(tables.get("full_short_pullback_trade_simulation_shadow", []))).rstrip(), ""])
    lines.extend(["## Recent Hold Continuation Diagnostics", _table_markdown(pd.DataFrame(tables.get("recent_hold_continuation_diagnostics", [])).head(80)).rstrip(), ""])
    lines.extend(["## Full Hold Continuation Diagnostics", _table_markdown(pd.DataFrame(tables.get("full_hold_continuation_diagnostics", [])).head(80)).rstrip(), ""])
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
    lines.extend(["## ATR Exit Verification", _table_markdown(pd.DataFrame(tables.get("atr_exit_verification", []))).rstrip(), ""])
    lines.extend(["## ATR Exit Policy Simulation", _table_markdown(pd.DataFrame(tables.get("atr_exit_policy_simulation", []))).rstrip(), ""])
    lines.extend(["## ATR Exit Policy Segment Simulation", _table_markdown(pd.DataFrame(tables.get("atr_exit_policy_segment_simulation", [])).head(80)).rstrip(), ""])
    lines.extend(["## Path Risk Sequencing", _table_markdown(pd.DataFrame(tables.get("path_risk_sequencing", []))).rstrip(), ""])
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
