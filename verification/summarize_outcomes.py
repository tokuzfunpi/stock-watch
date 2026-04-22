from __future__ import annotations

import argparse
from datetime import datetime
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from daily_theme_watchlist import LOCAL_TZ


def _pct(v: float | None) -> str:
    if v is None or pd.isna(v):
        return ""
    return f"{float(v):.2f}"


def _table_markdown(df: pd.DataFrame) -> str:
    if df.empty:
        return "_None_\n"
    headers = [str(c) for c in df.columns.tolist()]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for _, r in df.iterrows():
        row: list[str] = []
        for c in headers:
            val = r.get(c)
            if pd.isna(val):
                text = ""
            else:
                text = str(val)
            text = text.replace("|", "\\|").replace("\n", " ")
            row.append(text)
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines) + "\n"


def _confidence_label(min_n: int) -> str:
    if min_n >= 10:
        return "high"
    if min_n >= 5:
        return "medium"
    return "low"


def summarize_outcomes(outcomes: pd.DataFrame) -> dict[str, pd.DataFrame]:
    if outcomes.empty:
        empty = pd.DataFrame()
        return {
            "by_action": empty,
            "by_signal": empty,
            "overall_by_action": empty,
            "overall_by_signal": empty,
            "overall_by_signal_status": empty,
            "overall_by_action_status": empty,
            "overall_by_market_heat": empty,
            "overall_by_scenario": empty,
            "overall_by_scenario_action": empty,
            "overall_by_scenario_heat": empty,
            "delta_ok_minus_below": empty,
            "delta_ok_minus_below_by_date": empty,
            "heat_bias_check": empty,
            "heat_bias_by_scenario": empty,
            "heat_bias_by_date": empty,
        }

    df = outcomes.copy()
    df["status"] = df.get("status", "").astype(str)
    df = df[df["status"] == "ok"].copy()
    if df.empty:
        empty = pd.DataFrame()
        return {
            "by_action": empty,
            "by_signal": empty,
            "overall_by_action": empty,
            "overall_by_signal": empty,
            "overall_by_signal_status": empty,
            "overall_by_action_status": empty,
            "overall_by_market_heat": empty,
            "overall_by_scenario": empty,
            "overall_by_scenario_action": empty,
            "overall_by_scenario_heat": empty,
            "delta_ok_minus_below": empty,
            "delta_ok_minus_below_by_date": empty,
            "heat_bias_check": empty,
            "heat_bias_by_scenario": empty,
            "heat_bias_by_date": empty,
        }

    if "watch_type" in df.columns:
        df["watch_type"] = df["watch_type"].astype(str).str.strip().str.lower()
        df = df[df["watch_type"].isin(["short", "midlong"])].copy()
        if df.empty:
            empty = pd.DataFrame()
            return {
            "by_action": empty,
            "by_signal": empty,
            "overall_by_action": empty,
            "overall_by_signal": empty,
            "overall_by_signal_status": empty,
            "overall_by_action_status": empty,
            "overall_by_market_heat": empty,
            "overall_by_scenario": empty,
            "overall_by_scenario_action": empty,
            "overall_by_scenario_heat": empty,
            "delta_ok_minus_below": empty,
            "delta_ok_minus_below_by_date": empty,
            "heat_bias_check": empty,
            "heat_bias_by_scenario": empty,
            "heat_bias_by_date": empty,
        }

    # Split analysis: ok vs below_threshold (forced-fill).
    if "reco_status" in df.columns:
        df["reco_status"] = df["reco_status"].astype(str).str.strip()
        df.loc[df["reco_status"] == "", "reco_status"] = "unknown"
    else:
        df["reco_status"] = "unknown"

    if "market_heat" in df.columns:
        df["market_heat"] = df["market_heat"].astype(str).str.strip().str.lower()
        df.loc[~df["market_heat"].isin(["normal", "warm", "hot"]), "market_heat"] = "unknown"
    else:
        df["market_heat"] = "unknown"

    if "scenario_label" in df.columns:
        df["scenario_label"] = df["scenario_label"].astype(str).str.strip()
        df.loc[
            (df["scenario_label"] == "")
            | (df["scenario_label"] == "b''")
            | (df["scenario_label"] == "nan"),
            "scenario_label",
        ] = "unknown"
    else:
        df["scenario_label"] = "unknown"

    df["realized_ret_pct"] = pd.to_numeric(df["realized_ret_pct"], errors="coerce")
    df["horizon_days"] = pd.to_numeric(df["horizon_days"], errors="coerce").astype("Int64")
    df["win"] = df["realized_ret_pct"] > 0

    group_cols = ["signal_date", "horizon_days", "watch_type", "action"]
    by_action = (
        df.groupby(group_cols, dropna=False)
        .agg(
            n=("realized_ret_pct", "count"),
            win_rate=("win", "mean"),
            avg_ret=("realized_ret_pct", "mean"),
            med_ret=("realized_ret_pct", "median"),
            min_ret=("realized_ret_pct", "min"),
            max_ret=("realized_ret_pct", "max"),
        )
        .reset_index()
        .sort_values(by=["signal_date", "horizon_days", "watch_type", "avg_ret"], ascending=[False, True, True, False])
    )
    by_action["win_rate"] = (by_action["win_rate"] * 100).round(1)
    for c in ["avg_ret", "med_ret", "min_ret", "max_ret"]:
        by_action[c] = by_action[c].round(2)

    by_signal = (
        df.groupby(["signal_date", "horizon_days", "watch_type"], dropna=False)
        .agg(
            n=("realized_ret_pct", "count"),
            win_rate=("win", "mean"),
            avg_ret=("realized_ret_pct", "mean"),
            med_ret=("realized_ret_pct", "median"),
        )
        .reset_index()
        .sort_values(by=["signal_date", "horizon_days", "watch_type"], ascending=[False, True, True])
    )
    by_signal["win_rate"] = (by_signal["win_rate"] * 100).round(1)
    for c in ["avg_ret", "med_ret"]:
        by_signal[c] = by_signal[c].round(2)

    overall_by_action = (
        df.groupby(["horizon_days", "watch_type", "action"], dropna=False)
        .agg(
            n=("realized_ret_pct", "count"),
            win_rate=("win", "mean"),
            avg_ret=("realized_ret_pct", "mean"),
            med_ret=("realized_ret_pct", "median"),
            min_ret=("realized_ret_pct", "min"),
            max_ret=("realized_ret_pct", "max"),
        )
        .reset_index()
        .sort_values(by=["horizon_days", "watch_type", "n", "avg_ret"], ascending=[True, True, False, False])
    )
    overall_by_action["win_rate"] = (overall_by_action["win_rate"] * 100).round(1)
    for c in ["avg_ret", "med_ret", "min_ret", "max_ret"]:
        overall_by_action[c] = overall_by_action[c].round(2)

    overall_by_signal = (
        df.groupby(["horizon_days", "watch_type"], dropna=False)
        .agg(
            n=("realized_ret_pct", "count"),
            win_rate=("win", "mean"),
            avg_ret=("realized_ret_pct", "mean"),
            med_ret=("realized_ret_pct", "median"),
        )
        .reset_index()
        .sort_values(by=["horizon_days", "watch_type"], ascending=[True, True])
    )
    overall_by_signal["win_rate"] = (overall_by_signal["win_rate"] * 100).round(1)
    for c in ["avg_ret", "med_ret"]:
        overall_by_signal[c] = overall_by_signal[c].round(2)

    overall_by_market_heat = (
        df.groupby(["horizon_days", "watch_type", "market_heat"], dropna=False)
        .agg(
            n=("realized_ret_pct", "count"),
            win_rate=("win", "mean"),
            avg_ret=("realized_ret_pct", "mean"),
            med_ret=("realized_ret_pct", "median"),
        )
        .reset_index()
        .sort_values(by=["horizon_days", "watch_type", "market_heat"], ascending=[True, True, True])
    )
    overall_by_market_heat["win_rate"] = (overall_by_market_heat["win_rate"] * 100).round(1)
    for c in ["avg_ret", "med_ret"]:
        overall_by_market_heat[c] = overall_by_market_heat[c].round(2)

    overall_by_scenario = (
        df.groupby(["horizon_days", "watch_type", "scenario_label"], dropna=False)
        .agg(
            n=("realized_ret_pct", "count"),
            win_rate=("win", "mean"),
            avg_ret=("realized_ret_pct", "mean"),
            med_ret=("realized_ret_pct", "median"),
        )
        .reset_index()
        .sort_values(by=["horizon_days", "watch_type", "scenario_label"], ascending=[True, True, True])
    )
    overall_by_scenario["win_rate"] = (overall_by_scenario["win_rate"] * 100).round(1)
    for c in ["avg_ret", "med_ret"]:
        overall_by_scenario[c] = overall_by_scenario[c].round(2)

    overall_by_scenario_heat = (
        df.groupby(["horizon_days", "watch_type", "scenario_label", "market_heat"], dropna=False)
        .agg(
            n=("realized_ret_pct", "count"),
            win_rate=("win", "mean"),
            avg_ret=("realized_ret_pct", "mean"),
            med_ret=("realized_ret_pct", "median"),
        )
        .reset_index()
        .sort_values(by=["horizon_days", "watch_type", "scenario_label", "market_heat"], ascending=[True, True, True, True])
    )
    overall_by_scenario_heat["win_rate"] = (overall_by_scenario_heat["win_rate"] * 100).round(1)
    for c in ["avg_ret", "med_ret"]:
        overall_by_scenario_heat[c] = overall_by_scenario_heat[c].round(2)

    overall_by_scenario_action = (
        df.groupby(["horizon_days", "watch_type", "scenario_label", "action"], dropna=False)
        .agg(
            n=("realized_ret_pct", "count"),
            win_rate=("win", "mean"),
            avg_ret=("realized_ret_pct", "mean"),
            med_ret=("realized_ret_pct", "median"),
            min_ret=("realized_ret_pct", "min"),
            max_ret=("realized_ret_pct", "max"),
        )
        .reset_index()
        .sort_values(by=["horizon_days", "watch_type", "scenario_label", "n", "avg_ret"], ascending=[True, True, True, False, False])
    )
    overall_by_scenario_action["win_rate"] = (overall_by_scenario_action["win_rate"] * 100).round(1)
    for c in ["avg_ret", "med_ret", "min_ret", "max_ret"]:
        overall_by_scenario_action[c] = overall_by_scenario_action[c].round(2)

    overall_by_signal_status = (
        df.groupby(["horizon_days", "watch_type", "reco_status"], dropna=False)
        .agg(
            n=("realized_ret_pct", "count"),
            win_rate=("win", "mean"),
            avg_ret=("realized_ret_pct", "mean"),
            med_ret=("realized_ret_pct", "median"),
        )
        .reset_index()
        .sort_values(by=["horizon_days", "watch_type", "reco_status"], ascending=[True, True, True])
    )
    overall_by_signal_status["win_rate"] = (overall_by_signal_status["win_rate"] * 100).round(1)
    for c in ["avg_ret", "med_ret"]:
        overall_by_signal_status[c] = overall_by_signal_status[c].round(2)

    overall_by_action_status = (
        df.groupby(["horizon_days", "watch_type", "reco_status", "action"], dropna=False)
        .agg(
            n=("realized_ret_pct", "count"),
            win_rate=("win", "mean"),
            avg_ret=("realized_ret_pct", "mean"),
            med_ret=("realized_ret_pct", "median"),
            min_ret=("realized_ret_pct", "min"),
            max_ret=("realized_ret_pct", "max"),
        )
        .reset_index()
        .sort_values(by=["horizon_days", "watch_type", "reco_status", "n", "avg_ret"], ascending=[True, True, True, False, False])
    )
    overall_by_action_status["win_rate"] = (overall_by_action_status["win_rate"] * 100).round(1)
    for c in ["avg_ret", "med_ret", "min_ret", "max_ret"]:
        overall_by_action_status[c] = overall_by_action_status[c].round(2)

    delta_ok_minus_below = pd.DataFrame()
    delta_ok_minus_below_by_date = pd.DataFrame()
    heat_bias_check = pd.DataFrame()
    heat_bias_by_scenario = pd.DataFrame()
    heat_bias_by_date = pd.DataFrame()
    try:
        delta_base = overall_by_signal_status.copy()
        delta_base = delta_base[delta_base["reco_status"].isin(["ok", "below_threshold"])].copy()
        if not delta_base.empty:
            ok = delta_base[delta_base["reco_status"] == "ok"].copy()
            below = delta_base[delta_base["reco_status"] == "below_threshold"].copy()
            merge_cols = ["horizon_days", "watch_type"]
            merged = ok.merge(
                below,
                on=merge_cols,
                how="inner",
                suffixes=("_ok", "_below"),
            )
            if not merged.empty:
                min_n = pd.concat([pd.to_numeric(merged["n_ok"], errors="coerce"), pd.to_numeric(merged["n_below"], errors="coerce")], axis=1).min(axis=1)
                delta_ok_minus_below = pd.DataFrame(
                    {
                        "horizon_days": merged["horizon_days"],
                        "watch_type": merged["watch_type"],
                        "ok_n": merged["n_ok"],
                        "below_n": merged["n_below"],
                        "min_n": min_n.astype("Int64"),
                        "confidence": [ _confidence_label(int(x)) if pd.notna(x) else "low" for x in min_n.tolist() ],
                        "delta_win_rate": (pd.to_numeric(merged["win_rate_ok"], errors="coerce") - pd.to_numeric(merged["win_rate_below"], errors="coerce")).round(1),
                        "delta_avg_ret": (pd.to_numeric(merged["avg_ret_ok"], errors="coerce") - pd.to_numeric(merged["avg_ret_below"], errors="coerce")).round(2),
                        "delta_med_ret": (pd.to_numeric(merged["med_ret_ok"], errors="coerce") - pd.to_numeric(merged["med_ret_below"], errors="coerce")).round(2),
                    }
                ).sort_values(by=["horizon_days", "watch_type"])

        # By date: find which days forced-fill is helping/hurting.
        date_base = by_signal.copy()
        if not date_base.empty and "reco_status" in df.columns:
            # rebuild by-signal with reco_status using raw df (not the aggregated by_signal which lacks reco_status)
            tmp = df.groupby(["signal_date", "horizon_days", "watch_type", "reco_status"], dropna=False).agg(
                n=("realized_ret_pct", "count"),
                win_rate=("win", "mean"),
                avg_ret=("realized_ret_pct", "mean"),
                med_ret=("realized_ret_pct", "median"),
            ).reset_index()
            tmp["win_rate"] = (tmp["win_rate"] * 100).round(1)
            for c in ["avg_ret", "med_ret"]:
                tmp[c] = tmp[c].round(2)

            tmp = tmp[tmp["reco_status"].isin(["ok", "below_threshold"])].copy()
            okd = tmp[tmp["reco_status"] == "ok"].copy()
            bd = tmp[tmp["reco_status"] == "below_threshold"].copy()
            mcols = ["signal_date", "horizon_days", "watch_type"]
            m = okd.merge(bd, on=mcols, how="inner", suffixes=("_ok", "_below"))
            if not m.empty:
                min_n2 = pd.concat([pd.to_numeric(m["n_ok"], errors="coerce"), pd.to_numeric(m["n_below"], errors="coerce")], axis=1).min(axis=1)
                delta_ok_minus_below_by_date = pd.DataFrame(
                    {
                        "signal_date": m["signal_date"],
                        "horizon_days": m["horizon_days"],
                        "watch_type": m["watch_type"],
                        "ok_n": m["n_ok"],
                        "below_n": m["n_below"],
                        "min_n": min_n2.astype("Int64"),
                        "confidence": [ _confidence_label(int(x)) if pd.notna(x) else "low" for x in min_n2.tolist() ],
                        "delta_win_rate": (pd.to_numeric(m["win_rate_ok"], errors="coerce") - pd.to_numeric(m["win_rate_below"], errors="coerce")).round(1),
                        "delta_avg_ret": (pd.to_numeric(m["avg_ret_ok"], errors="coerce") - pd.to_numeric(m["avg_ret_below"], errors="coerce")).round(2),
                        "delta_med_ret": (pd.to_numeric(m["med_ret_ok"], errors="coerce") - pd.to_numeric(m["med_ret_below"], errors="coerce")).round(2),
                    }
                ).sort_values(by=["signal_date", "horizon_days", "watch_type"], ascending=[False, True, True])
    except Exception:
        delta_ok_minus_below = pd.DataFrame()
        delta_ok_minus_below_by_date = pd.DataFrame()

    try:
        heat_base = overall_by_market_heat.copy()
        heat_base = heat_base[heat_base["market_heat"].isin(["normal", "hot"])].copy()
        if not heat_base.empty:
            normal = heat_base[heat_base["market_heat"] == "normal"].copy()
            hot = heat_base[heat_base["market_heat"] == "hot"].copy()
            merged_heat = hot.merge(
                normal,
                on=["horizon_days", "watch_type"],
                how="inner",
                suffixes=("_hot", "_normal"),
            )
            if not merged_heat.empty:
                min_n_heat = pd.concat(
                    [
                        pd.to_numeric(merged_heat["n_hot"], errors="coerce"),
                        pd.to_numeric(merged_heat["n_normal"], errors="coerce"),
                    ],
                    axis=1,
                ).min(axis=1)
                heat_bias_check = pd.DataFrame(
                    {
                        "horizon_days": merged_heat["horizon_days"],
                        "watch_type": merged_heat["watch_type"],
                        "hot_n": merged_heat["n_hot"],
                        "normal_n": merged_heat["n_normal"],
                        "min_n": min_n_heat.astype("Int64"),
                        "confidence": [_confidence_label(int(x)) if pd.notna(x) else "low" for x in min_n_heat.tolist()],
                        "delta_win_rate_hot_minus_normal": (
                            pd.to_numeric(merged_heat["win_rate_hot"], errors="coerce")
                            - pd.to_numeric(merged_heat["win_rate_normal"], errors="coerce")
                        ).round(1),
                        "delta_avg_ret_hot_minus_normal": (
                            pd.to_numeric(merged_heat["avg_ret_hot"], errors="coerce")
                            - pd.to_numeric(merged_heat["avg_ret_normal"], errors="coerce")
                        ).round(2),
                        "delta_med_ret_hot_minus_normal": (
                            pd.to_numeric(merged_heat["med_ret_hot"], errors="coerce")
                            - pd.to_numeric(merged_heat["med_ret_normal"], errors="coerce")
                        ).round(2),
                    }
                ).sort_values(by=["horizon_days", "watch_type"])

        heat_by_scenario_base = overall_by_scenario_heat.copy()
        heat_by_scenario_base = heat_by_scenario_base[heat_by_scenario_base["market_heat"].isin(["normal", "hot"])].copy()
        if not heat_by_scenario_base.empty:
            normal_s = heat_by_scenario_base[heat_by_scenario_base["market_heat"] == "normal"].copy()
            hot_s = heat_by_scenario_base[heat_by_scenario_base["market_heat"] == "hot"].copy()
            merged_s = hot_s.merge(
                normal_s,
                on=["horizon_days", "watch_type", "scenario_label"],
                how="inner",
                suffixes=("_hot", "_normal"),
            )
            if not merged_s.empty:
                min_n_s = pd.concat(
                    [
                        pd.to_numeric(merged_s["n_hot"], errors="coerce"),
                        pd.to_numeric(merged_s["n_normal"], errors="coerce"),
                    ],
                    axis=1,
                ).min(axis=1)
                heat_bias_by_scenario = pd.DataFrame(
                    {
                        "horizon_days": merged_s["horizon_days"],
                        "watch_type": merged_s["watch_type"],
                        "scenario_label": merged_s["scenario_label"],
                        "hot_n": merged_s["n_hot"],
                        "normal_n": merged_s["n_normal"],
                        "min_n": min_n_s.astype("Int64"),
                        "confidence": [_confidence_label(int(x)) if pd.notna(x) else "low" for x in min_n_s.tolist()],
                        "delta_win_rate_hot_minus_normal": (
                            pd.to_numeric(merged_s["win_rate_hot"], errors="coerce")
                            - pd.to_numeric(merged_s["win_rate_normal"], errors="coerce")
                        ).round(1),
                        "delta_avg_ret_hot_minus_normal": (
                            pd.to_numeric(merged_s["avg_ret_hot"], errors="coerce")
                            - pd.to_numeric(merged_s["avg_ret_normal"], errors="coerce")
                        ).round(2),
                    }
                ).sort_values(by=["horizon_days", "watch_type", "scenario_label"])

        heat_by_date_base = (
            df.groupby(["signal_date", "market_heat"], dropna=False)
            .agg(
                n=("realized_ret_pct", "count"),
                avg_ret=("realized_ret_pct", "mean"),
            )
            .reset_index()
        )
        heat_by_date_base = heat_by_date_base[heat_by_date_base["market_heat"].isin(["normal", "hot"])].copy()
        if not heat_by_date_base.empty:
            normal_d = heat_by_date_base[heat_by_date_base["market_heat"] == "normal"].copy()
            hot_d = heat_by_date_base[heat_by_date_base["market_heat"] == "hot"].copy()
            merged_d = hot_d.merge(normal_d, on=["signal_date"], how="inner", suffixes=("_hot", "_normal"))
            if not merged_d.empty:
                heat_bias_by_date = pd.DataFrame(
                    {
                        "signal_date": merged_d["signal_date"],
                        "hot_n": merged_d["n_hot"],
                        "normal_n": merged_d["n_normal"],
                        "delta_avg_ret_hot_minus_normal": (
                            pd.to_numeric(merged_d["avg_ret_hot"], errors="coerce")
                            - pd.to_numeric(merged_d["avg_ret_normal"], errors="coerce")
                        ).round(2),
                    }
                ).sort_values(by=["signal_date"], ascending=False)
    except Exception:
        heat_bias_check = pd.DataFrame()
        heat_bias_by_scenario = pd.DataFrame()
        heat_bias_by_date = pd.DataFrame()

    return {
        "by_action": by_action,
        "by_signal": by_signal,
        "overall_by_action": overall_by_action,
        "overall_by_signal": overall_by_signal,
        "overall_by_signal_status": overall_by_signal_status,
        "overall_by_action_status": overall_by_action_status,
        "overall_by_market_heat": overall_by_market_heat,
        "overall_by_scenario": overall_by_scenario,
        "overall_by_scenario_action": overall_by_scenario_action,
        "overall_by_scenario_heat": overall_by_scenario_heat,
        "delta_ok_minus_below": delta_ok_minus_below,
        "delta_ok_minus_below_by_date": delta_ok_minus_below_by_date,
        "heat_bias_check": heat_bias_check,
        "heat_bias_by_scenario": heat_bias_by_scenario,
        "heat_bias_by_date": heat_bias_by_date,
    }


def build_summary_markdown(outcomes: pd.DataFrame, source: str, now_local: datetime | None = None) -> str:
    now_local = now_local or datetime.now(LOCAL_TZ)
    parts = summarize_outcomes(outcomes)

    lines: list[str] = [
        "# Recommendation Outcomes Summary",
        f"- Generated: {now_local.strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"- Source: {source}",
        "",
    ]

    if outcomes.empty:
        lines.extend(["_No outcomes file rows._", ""])
        return "\n".join(lines)

    ok = outcomes[outcomes.get("status", "").astype(str) == "ok"]
    lines.extend(
        [
            "## Coverage",
            f"- Total rows: {len(outcomes)}",
            f"- OK rows: {len(ok)}",
            "",
        ]
    )

    try:
        ok_cov = ok.copy()
        if "scenario_label" in ok_cov.columns:
            ok_cov["scenario_label"] = ok_cov["scenario_label"].astype(str).str.strip()
            ok_cov.loc[
                (ok_cov["scenario_label"] == "")
                | (ok_cov["scenario_label"] == "b''")
                | (ok_cov["scenario_label"] == "nan"),
                "scenario_label",
            ] = "unknown"
        else:
            ok_cov["scenario_label"] = "unknown"
        known_mask = ok_cov["scenario_label"] != "unknown"
        scenario_cov = pd.DataFrame(
            [
                {
                    "ok_rows": int(len(ok_cov)),
                    "known_scenario_rows": int(known_mask.sum()),
                    "unknown_scenario_rows": int((~known_mask).sum()),
                    "known_scenario_rate_pct": round((float(known_mask.mean()) * 100.0), 1) if len(ok_cov) else 0.0,
                }
            ]
        )
        lines.extend(["## Scenario Coverage", _table_markdown(scenario_cov).rstrip(), ""])
    except Exception:
        pass

    lines.extend(
        [
            "## Notes",
            "- `pending`（insufficient_forward_data）代表還沒走滿 horizon 的交易日數，之後重跑 evaluate 會自動轉成 ok。",
            "- `below_threshold` 是為了固定補滿 5 檔而納入的樣本；請優先看 `min_n/confidence`，避免小樣本誤判。",
            "- `market_heat` 是樣本熱度標籤；若 `hot/warm` 樣本偏多，代表近期結果可能被強勢盤墊高。",
            "",
        ]
    )

    # Extra coverage diagnostics (helps understand why 20D isn't showing up yet).
    try:
        cov = outcomes.copy()
        cov["horizon_days"] = pd.to_numeric(cov.get("horizon_days"), errors="coerce").astype("Int64")
        cov["status"] = cov.get("status", "").astype(str)
        cov["is_ok"] = cov["status"] == "ok"
        by_h = (
            cov.groupby(["horizon_days"], dropna=False)
            .agg(
                total=("status", "count"),
                ok=("is_ok", "sum"),
                pending=("status", lambda s: int((s.astype(str) == "insufficient_forward_data").sum())),
                no_price=("status", lambda s: int((s.astype(str) == "no_price_series").sum())),
            )
            .reset_index()
            .sort_values(by=["horizon_days"])
        )
        by_h["ok_rate_pct"] = ((by_h["ok"] / by_h["total"]) * 100).round(1)
        lines.extend(["## Coverage By Horizon", _table_markdown(by_h).rstrip(), ""])
    except Exception:
        pass

    lines.extend(["## Overall By Signal (all dates)", _table_markdown(parts["overall_by_signal"]).rstrip(), ""])
    if not parts["overall_by_market_heat"].empty:
        lines.extend(["## Overall By Market Heat (all dates)", _table_markdown(parts["overall_by_market_heat"]).rstrip(), ""])
    if not parts["overall_by_scenario"].empty:
        lines.extend(["## Overall By Scenario (all dates)", _table_markdown(parts["overall_by_scenario"]).rstrip(), ""])
    if not parts["heat_bias_check"].empty:
        lines.extend(["## Heat Bias Check (hot - normal)", _table_markdown(parts["heat_bias_check"]).rstrip(), ""])
    if not parts["heat_bias_by_scenario"].empty:
        lines.extend(["## Heat Bias By Scenario (hot - normal)", _table_markdown(parts["heat_bias_by_scenario"]).rstrip(), ""])
    if not parts["heat_bias_by_date"].empty:
        lines.extend(["## Heat Bias By Date (hot - normal, top 20)", _table_markdown(parts["heat_bias_by_date"].head(20)).rstrip(), ""])
    if not parts["overall_by_signal_status"].empty:
        lines.extend(["## Overall By Signal + reco_status (all dates)", _table_markdown(parts["overall_by_signal_status"]).rstrip(), ""])
    if not parts["delta_ok_minus_below"].empty:
        lines.extend(["## Delta (ok - below_threshold) By Signal (all dates)", _table_markdown(parts["delta_ok_minus_below"]).rstrip(), ""])
    if not parts["delta_ok_minus_below_by_date"].empty:
        lines.extend(["## Delta (ok - below_threshold) By Signal Date (top 30)", _table_markdown(parts["delta_ok_minus_below_by_date"].head(30)).rstrip(), ""])

    # Weekly checkpoint: only show deltas with enough samples to be actionable.
    try:
        delta_strong = parts["delta_ok_minus_below"].copy()
        if not delta_strong.empty and "min_n" in delta_strong.columns:
            delta_strong = delta_strong[pd.to_numeric(delta_strong["min_n"], errors="coerce") >= 5].copy()
        if not delta_strong.empty:
            lines.extend(["## Weekly Checkpoint (min_n>=5)", _table_markdown(delta_strong).rstrip(), ""])
        else:
            lines.extend(["## Weekly Checkpoint (min_n>=5)", "_None_", ""])
    except Exception:
        lines.extend(["## Weekly Checkpoint (min_n>=5)", "_None_", ""])

    lines.extend(["## Overall By Action (all dates, top 80)", _table_markdown(parts["overall_by_action"].head(80)).rstrip(), ""])
    if not parts["overall_by_action_status"].empty:
        lines.extend(["## Overall By Action + reco_status (all dates, top 80)", _table_markdown(parts["overall_by_action_status"].head(80)).rstrip(), ""])
    if not parts["overall_by_scenario_action"].empty:
        lines.extend(["## Overall By Scenario + Action (all dates, top 80)", _table_markdown(parts["overall_by_scenario_action"].head(80)).rstrip(), ""])
    lines.extend(["## By Signal (watch_type)", _table_markdown(parts["by_signal"].head(30)).rstrip(), ""])
    lines.extend(["## By Action (top 50)", _table_markdown(parts["by_action"].head(50)).rstrip(), ""])
    lines.append("")
    return "\n".join(lines).strip() + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize reco outcomes (win rate / average returns).")
    out_dir = Path("verification") / "watchlist_daily"
    parser.add_argument("--outcomes-csv", default=str(out_dir / "reco_outcomes.csv"))
    parser.add_argument("--out", default=str(out_dir / "outcomes_summary.md"))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    outcomes_csv = Path(args.outcomes_csv)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not outcomes_csv.exists():
        report = build_summary_markdown(pd.DataFrame(), source=str(outcomes_csv))
        out_path.write_text(report, encoding="utf-8")
        print(report)
        return 0

    outcomes = pd.read_csv(outcomes_csv)
    report = build_summary_markdown(outcomes, source=str(outcomes_csv))
    out_path.write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
