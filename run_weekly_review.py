from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from daily_theme_watchlist import ALERT_TRACK_CSV, LOCAL_TZ
from verification.summarize_outcomes import summarize_atr_band_checkpoints
from verification.summarize_outcomes import summarize_outcomes

REPO_ROOT = Path(__file__).resolve().parent
VERIFICATION_OUTCOMES_CSV = REPO_ROOT / "verification" / "watchlist_daily" / "reco_outcomes.csv"
FEEDBACK_SENSITIVITY_CSV = REPO_ROOT / "verification" / "watchlist_daily" / "feedback_weight_sensitivity.csv"
DAILY_RANK_CSV = REPO_ROOT / "theme_watchlist_daily" / "daily_rank.csv"
WEEKLY_REVIEW_MD = REPO_ROOT / "theme_watchlist_daily" / "weekly_review.md"
WEEKLY_REVIEW_JSON = REPO_ROOT / "theme_watchlist_daily" / "weekly_review.json"
WATCHLIST_CSV = REPO_ROOT / "watchlist.csv"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a weekly decision note from local verification outputs.")
    parser.add_argument("--outcomes-csv", default=str(VERIFICATION_OUTCOMES_CSV))
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


def build_decisions(
    parts: dict[str, pd.DataFrame],
    band_parts: dict[str, pd.DataFrame],
    feedback_csv: Path,
) -> dict[str, dict[str, object]]:
    threshold_row = _find_single_row(parts.get("delta_ok_minus_below", pd.DataFrame()), horizon_days=1, watch_type="midlong")
    heat_row = _find_single_row(parts.get("heat_bias_check", pd.DataFrame()), horizon_days=1, watch_type="midlong")
    spec_risk_row = _find_single_row(parts.get("spec_risk_check", pd.DataFrame()), horizon_days=1, watch_type="short")

    if threshold_row is None:
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

    return {
        "threshold": threshold_decision,
        "atr": atr_decision,
        "feedback": feedback_decision,
        "spec_risk": spec_risk_decision,
    }


def build_weekly_review_payload(
    *,
    outcomes_csv: Path,
    feedback_csv: Path,
    alert_csv: Path,
    rank_csv: Path,
    watchlist_csv: Path,
    max_signal_dates: int,
) -> dict[str, object]:
    if not outcomes_csv.exists():
        raise FileNotFoundError(f"Missing outcomes CSV: {outcomes_csv}")
    outcomes = pd.read_csv(outcomes_csv)
    recent_outcomes, recent_dates = filter_recent_signal_dates(outcomes, max_signal_dates=max_signal_dates)
    parts = summarize_outcomes(recent_outcomes)

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

    overall_by_signal = parts.get("overall_by_signal", pd.DataFrame())
    weekly_checkpoint = parts.get("delta_ok_minus_below", pd.DataFrame())
    heat_bias_check = parts.get("heat_bias_check", pd.DataFrame())
    overall_by_spec_risk = parts.get("overall_by_spec_risk", pd.DataFrame())
    overall_by_spec_subtype = parts.get("overall_by_spec_subtype", pd.DataFrame())
    spec_risk_check = parts.get("spec_risk_check", pd.DataFrame())

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
            "overall_by_spec_risk": overall_by_spec_risk.to_dict(orient="records"),
            "overall_by_spec_subtype": overall_by_spec_subtype.to_dict(orient="records"),
            "spec_risk_check": spec_risk_check.to_dict(orient="records"),
            "current_rank_spec_risk_by_group": rank_spec_coverage["by_group"],
            "current_rank_spec_risk_by_layer": rank_spec_coverage["by_layer"],
            "current_rank_spec_risk_by_source": candidate_source_summary["by_source"],
            "current_rank_spec_risk_top_candidates": rank_spec_coverage["top_candidates"],
            "atr_band_coverage": band_parts.get("band_coverage", pd.DataFrame()).to_dict(orient="records"),
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
    for key in ["threshold", "atr", "feedback", "spec_risk"]:
        item = decisions.get(key, {})
        lines.append(f"- `{key}`: `{item.get('status', 'hold')}` — {item.get('detail', '')}")

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

    lines.extend(["", "## Overall By Signal", _table_markdown(pd.DataFrame(tables.get("overall_by_signal", []))).rstrip(), ""])
    lines.extend(["## Threshold Delta", _table_markdown(pd.DataFrame(tables.get("weekly_threshold_delta", []))).rstrip(), ""])
    lines.extend(["## Heat Bias Check", _table_markdown(pd.DataFrame(tables.get("heat_bias_check", []))).rstrip(), ""])
    lines.extend(["## Overall By Spec Risk", _table_markdown(pd.DataFrame(tables.get("overall_by_spec_risk", []))).rstrip(), ""])
    lines.extend(["## Overall By Spec Subtype", _table_markdown(pd.DataFrame(tables.get("overall_by_spec_subtype", []))).rstrip(), ""])
    lines.extend(["## Spec Risk Check", _table_markdown(pd.DataFrame(tables.get("spec_risk_check", []))).rstrip(), ""])
    lines.extend(["## Current Rank Spec Risk By Group", _table_markdown(pd.DataFrame(tables.get("current_rank_spec_risk_by_group", []))).rstrip(), ""])
    lines.extend(["## Current Rank Spec Risk By Layer", _table_markdown(pd.DataFrame(tables.get("current_rank_spec_risk_by_layer", []))).rstrip(), ""])
    lines.extend(["## Current Rank Spec Risk By Source", _table_markdown(pd.DataFrame(tables.get("current_rank_spec_risk_by_source", []))).rstrip(), ""])
    lines.extend(["## Current Suspicious Candidates", _table_markdown(pd.DataFrame(tables.get("current_rank_spec_risk_top_candidates", []))).rstrip(), ""])
    lines.extend(["## ATR Band Coverage", _table_markdown(pd.DataFrame(tables.get("atr_band_coverage", []))).rstrip(), ""])
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
