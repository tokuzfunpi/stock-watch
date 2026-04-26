from __future__ import annotations

import argparse
from datetime import datetime
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from daily_theme_watchlist import ALERT_TRACK_CSV, LOCAL_TZ
from stock_watch.signals import apply_signal_template_labels
from stock_watch.signals import build_speculative_risk_profile


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


def _pick_best_row(df: pd.DataFrame, min_samples: int, delta_col: str) -> pd.Series | None:
    if df.empty or delta_col not in df.columns:
        return None
    work = df.copy()
    if "min_n" in work.columns:
        work = work[pd.to_numeric(work["min_n"], errors="coerce") >= min_samples].copy()
    if work.empty:
        return None
    work["_abs_delta"] = pd.to_numeric(work[delta_col], errors="coerce").abs()
    work = work.sort_values(by=["_abs_delta"], ascending=[False])
    if work.empty:
        return None
    return work.iloc[0]


def _spec_risk_bucket_from_row(row: pd.Series) -> str:
    score, label, _ = _spec_risk_profile_from_row(row)
    if pd.notna(score):
        if float(score) >= 6:
            return "high"
        if float(score) >= 3:
            return "watch"
        return "normal"
    if "疑似炒作風險高" in label:
        return "high"
    if label in {"偏熱", "留意"}:
        return "watch"
    return "normal"


def _spec_risk_profile_from_row(row: pd.Series) -> tuple[object, str, str]:
    score = pd.to_numeric(row.get("spec_risk_score"), errors="coerce")
    label = str(row.get("spec_risk_label", "")).strip()
    subtype = str(row.get("spec_risk_subtype", "")).strip()
    if pd.notna(score) or label:
        return score, label, subtype

    def _num(name: str, default: float = 0.0) -> float:
        value = pd.to_numeric(row.get(name), errors="coerce")
        return float(default if pd.isna(value) else value)

    def _int_num(name: str, default: int = 0) -> int:
        value = pd.to_numeric(row.get(name), errors="coerce")
        return int(default if pd.isna(value) else value)

    try:
        profile = build_speculative_risk_profile(
            ret1_pct=_num("ret1_pct"),
            ret5_pct=_num("ret5_pct"),
            ret20_pct=_num("ret20_pct"),
            volume_ratio20=_num("volume_ratio20"),
            bias20_pct=_num("bias20_pct"),
            atr_pct=_num("atr_pct"),
            range20_pct=_num("range20_pct"),
            drawdown120_pct=_num("drawdown120_pct", -100.0),
            risk_score=_int_num("risk_score"),
            setup_score=_int_num("setup_score"),
            signals=str(row.get("signals", "")),
            group=str(row.get("group", "")),
        )
        return profile.score, profile.label, profile.subtype
    except Exception:
        return pd.NA, "", ""


def build_key_findings(parts: dict[str, pd.DataFrame]) -> list[str]:
    findings: list[str] = []

    heat_row = _pick_best_row(parts.get("heat_bias_check", pd.DataFrame()), min_samples=5, delta_col="delta_avg_ret_hot_minus_normal")
    if heat_row is not None:
        direction = "較強" if float(heat_row["delta_avg_ret_hot_minus_normal"]) >= 0 else "較弱"
        findings.append(
            f"`{int(heat_row['horizon_days'])}D {heat_row['watch_type']}` 在 `hot` 盤相較 `normal` {direction}，"
            f"平均報酬差 `{_pct(heat_row['delta_avg_ret_hot_minus_normal'])}%`，"
            f"`min_n={int(heat_row['min_n'])}`、`confidence={heat_row['confidence']}`。"
        )

    scenario_row = _pick_best_row(parts.get("heat_bias_by_scenario", pd.DataFrame()), min_samples=3, delta_col="delta_avg_ret_hot_minus_normal")
    if scenario_row is not None:
        direction = "較強" if float(scenario_row["delta_avg_ret_hot_minus_normal"]) >= 0 else "較弱"
        findings.append(
            f"放到同一個 scenario 看，`{scenario_row['scenario_label']}` 下的 "
            f"`{int(scenario_row['horizon_days'])}D {scenario_row['watch_type']}` 在 `hot` 盤仍然{direction}，"
            f"平均報酬差 `{_pct(scenario_row['delta_avg_ret_hot_minus_normal'])}%`。"
        )

    date_row = _pick_best_row(parts.get("heat_bias_by_date", pd.DataFrame()), min_samples=2, delta_col="delta_avg_ret_hot_minus_normal")
    if date_row is not None:
        findings.append(
            f"按日期看，`{date_row['signal_date']}` 的熱度差最明顯："
            f"`hot-normal = {_pct(date_row['delta_avg_ret_hot_minus_normal'])}%` "
            f"（`hot_n={int(date_row['hot_n'])}`、`normal_n={int(date_row['normal_n'])}`）。"
        )

    template_row = _pick_best_row(parts.get("overall_by_signal_template", pd.DataFrame()), min_samples=3, delta_col="avg_ret")
    if template_row is not None:
        findings.append(
            f"`{int(template_row['horizon_days'])}D {template_row['watch_type']}` 裡，"
            f"`{template_row['signal_template']}` 目前平均報酬 `{_pct(template_row['avg_ret'])}%`、"
            f"勝率 `{_pct(template_row['win_rate'])}%`，`n={int(template_row['n'])}`。"
        )

    spec_row = _pick_best_row(parts.get("spec_risk_check", pd.DataFrame()), min_samples=3, delta_col="delta_avg_ret_high_minus_normal")
    if spec_row is not None:
        direction = "較強" if float(spec_row["delta_avg_ret_high_minus_normal"]) >= 0 else "較弱"
        findings.append(
            f"`{int(spec_row['horizon_days'])}D {spec_row['watch_type']}` 裡，`high` 疑似炒作樣本相較 `normal` {direction}，"
            f"平均報酬差 `{_pct(spec_row['delta_avg_ret_high_minus_normal'])}%`，"
            f"`min_n={int(spec_row['min_n'])}`、`confidence={spec_row['confidence']}`。"
        )

    spec_subtype_row = _pick_best_row(parts.get("overall_by_spec_subtype", pd.DataFrame()), min_samples=3, delta_col="avg_ret")
    if spec_subtype_row is not None:
        findings.append(
            f"`{int(spec_subtype_row['horizon_days'])}D {spec_subtype_row['watch_type']}` 裡，"
            f"`{spec_subtype_row['spec_risk_subtype']}` 平均報酬 `{_pct(spec_subtype_row['avg_ret'])}%`、"
            f"勝率 `{_pct(spec_subtype_row['win_rate'])}%`，`n={int(spec_subtype_row['n'])}`。"
        )

    threshold_rows = parts.get("threshold_guard_check", pd.DataFrame())
    if not threshold_rows.empty:
        short_rows = threshold_rows[threshold_rows["watch_type"].astype(str) == "short"].copy()
        threshold_row = _pick_best_row(short_rows, min_samples=5, delta_col="delta_avg_ret_ok_minus_below")
        if threshold_row is not None and float(threshold_row["delta_avg_ret_ok_minus_below"]) < 0:
            findings.append(
                f"`{int(threshold_row['horizon_days'])}D short` 目前是 `below_threshold` 樣本比 `ok` 樣本更強，"
                f"`ok-below = {_pct(threshold_row['delta_avg_ret_ok_minus_below'])}%`，"
                f"`min_n={int(threshold_row['min_n'])}`、`confidence={threshold_row['confidence']}`；"
                "短線 `ok` 門檻可能偏保守。"
            )

    short_diag = parts.get("short_threshold_diagnostics", pd.DataFrame())
    if not short_diag.empty:
        below_short = short_diag[short_diag["reco_status"].astype(str) == "below_threshold"].copy()
        below_short = below_short[pd.to_numeric(below_short["n"], errors="coerce") >= 2].copy()
        if not below_short.empty:
            below_short = below_short.sort_values(by=["avg_ret", "n"], ascending=[False, False])
            top_below = below_short.iloc[0]
            findings.append(
                f"短線 `below_threshold` 裡目前最強的是 `{top_below['action']}`，"
                f"平均報酬 `{_pct(top_below['avg_ret'])}%`、勝率 `{_pct(top_below['win_rate'])}%`，"
                f"`n={int(top_below['n'])}`；這表示補滿用名單偏強，可能是近期強盤把保守動作也往上抬。"
            )

    short_promotion = parts.get("short_gate_promotion_watch", pd.DataFrame())
    if not short_promotion.empty:
        promote = short_promotion[short_promotion["verdict"].astype(str) == "watch_upgrade"].copy()
        if not promote.empty:
            promote = promote.sort_values(
                by=["delta_avg_ret_below_minus_ok", "below_n"],
                ascending=[False, False],
            )
            top_promote = promote.iloc[0]
            findings.append(
                f"短線候補裡 `{top_promote['action']}` 最值得列入升格觀察，"
                f"`below-ok = {_pct(top_promote['delta_avg_ret_below_minus_ok'])}%`、"
                f"`below_n={int(top_promote['below_n'])}`、`confidence={top_promote['confidence']}`；"
                "先把它當成 tuning 候選，而不是直接放寬整體門檻。"
            )

    if not findings and not parts.get("overall_by_scenario", pd.DataFrame()).empty:
        findings.append("目前 scenario 資料已開始累積，但 `hot vs normal` 的可比較樣本還不夠，先以表格追蹤，不急著下規則結論。")

    return findings


def summarize_atr_band_checkpoints(alert_tracking: pd.DataFrame) -> dict[str, pd.DataFrame]:
    empty = pd.DataFrame()
    if alert_tracking.empty:
        return {"band_coverage": empty, "band_checkpoints": empty}

    required = {"alert_close", "add_price", "trim_price", "stop_price", "watch_type"}
    if not required.issubset(set(alert_tracking.columns)):
        return {"band_coverage": empty, "band_checkpoints": empty}

    df = alert_tracking.copy()
    df["watch_type"] = df["watch_type"].astype(str).str.strip().str.lower()
    df = df[df["watch_type"].isin(["short", "midlong"])].copy()
    if df.empty:
        return {"band_coverage": empty, "band_checkpoints": empty}

    for col in ["alert_close", "add_price", "trim_price", "stop_price"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    band_ready = df.dropna(subset=["alert_close", "add_price", "trim_price", "stop_price"]).copy()
    if band_ready.empty:
        return {"band_coverage": empty, "band_checkpoints": empty}

    coverage_rows: list[dict[str, object]] = []
    checkpoint_rows: list[dict[str, object]] = []

    for horizon in [1, 5, 20]:
        ret_col = f"ret{horizon}_future_pct"
        if ret_col not in band_ready.columns:
            continue
        band_ready[ret_col] = pd.to_numeric(band_ready[ret_col], errors="coerce")
        for watch_type, group in band_ready.groupby("watch_type", dropna=False):
            matured = group.dropna(subset=[ret_col]).copy()
            coverage_rows.append(
                {
                    "horizon_days": horizon,
                    "watch_type": watch_type,
                    "band_rows": int(len(group)),
                    "matured_rows": int(len(matured)),
                    "maturity_rate_pct": round((len(matured) / len(group)) * 100, 1) if len(group) else 0.0,
                }
            )
            if matured.empty:
                continue
            future_close = matured["alert_close"] * (1 + matured[ret_col] / 100.0)
            checkpoint_rows.append(
                {
                    "horizon_days": horizon,
                    "watch_type": watch_type,
                    "n": int(len(matured)),
                    "closed_below_add": int((future_close <= matured["add_price"]).sum()),
                    "closed_above_trim": int((future_close >= matured["trim_price"]).sum()),
                    "closed_below_stop": int((future_close <= matured["stop_price"]).sum()),
                    "avg_ret_pct": round(float(matured[ret_col].mean()), 2),
                }
            )

    band_coverage = pd.DataFrame(coverage_rows)
    band_checkpoints = pd.DataFrame(checkpoint_rows)
    if not band_coverage.empty:
        band_coverage = band_coverage.sort_values(by=["horizon_days", "watch_type"]).reset_index(drop=True)
    if not band_checkpoints.empty:
        band_checkpoints = band_checkpoints.sort_values(by=["horizon_days", "watch_type"]).reset_index(drop=True)
        for col in ["closed_below_add", "closed_above_trim", "closed_below_stop"]:
            band_checkpoints[f"{col}_rate_pct"] = (
                (band_checkpoints[col] / band_checkpoints["n"]) * 100
            ).round(1)
    return {"band_coverage": band_coverage, "band_checkpoints": band_checkpoints}


def build_atr_band_findings(band_parts: dict[str, pd.DataFrame]) -> list[str]:
    findings: list[str] = []
    coverage = band_parts.get("band_coverage", pd.DataFrame())
    checkpoints = band_parts.get("band_checkpoints", pd.DataFrame())

    if coverage.empty:
        return findings

    for horizon in [5, 20]:
        matured = coverage[pd.to_numeric(coverage["horizon_days"], errors="coerce") == horizon]
        if matured.empty:
            continue
        matured_rows = int(pd.to_numeric(matured["matured_rows"], errors="coerce").sum())
        band_rows = int(pd.to_numeric(matured["band_rows"], errors="coerce").sum())
        if matured_rows == 0 and band_rows > 0:
            findings.append(f"`ATR band` 目前已有 `{band_rows}` 筆 band 樣本，但 `{horizon}D` 還沒有成熟資料，先累積樣本。")

    row = _pick_best_row(checkpoints, min_samples=3, delta_col="closed_above_trim_rate_pct")
    if row is not None:
        findings.append(
            f"`{int(row['horizon_days'])}D {row['watch_type']}` 的 band checkpoint 目前有 `{int(row['n'])}` 筆成熟樣本，"
            f"收盤站上 `trim` 比例 `{_pct(row['closed_above_trim_rate_pct'])}%`，"
            f"跌破 `stop` 比例 `{_pct(row['closed_below_stop_rate_pct'])}%`。"
        )

    return findings


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
            "overall_by_signal_template": empty,
            "overall_by_scenario_template": empty,
            "overall_by_spec_risk": empty,
            "overall_by_spec_subtype": empty,
            "delta_ok_minus_below": empty,
            "delta_ok_minus_below_by_date": empty,
            "threshold_guard_check": empty,
            "short_threshold_diagnostics": empty,
            "short_gate_promotion_watch": empty,
            "heat_bias_check": empty,
            "heat_bias_by_scenario": empty,
            "heat_bias_by_date": empty,
            "spec_risk_check": empty,
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
            "overall_by_signal_template": empty,
            "overall_by_scenario_template": empty,
            "overall_by_spec_risk": empty,
            "overall_by_spec_subtype": empty,
            "delta_ok_minus_below": empty,
            "delta_ok_minus_below_by_date": empty,
            "threshold_guard_check": empty,
            "short_threshold_diagnostics": empty,
            "short_gate_promotion_watch": empty,
            "heat_bias_check": empty,
            "heat_bias_by_scenario": empty,
            "heat_bias_by_date": empty,
            "spec_risk_check": empty,
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
            "overall_by_signal_template": empty,
            "overall_by_scenario_template": empty,
            "overall_by_spec_risk": empty,
            "overall_by_spec_subtype": empty,
            "delta_ok_minus_below": empty,
            "delta_ok_minus_below_by_date": empty,
            "threshold_guard_check": empty,
            "short_threshold_diagnostics": empty,
            "short_gate_promotion_watch": empty,
            "heat_bias_check": empty,
            "heat_bias_by_scenario": empty,
            "heat_bias_by_date": empty,
            "spec_risk_check": empty,
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

    df = apply_signal_template_labels(df, signal_col="signals", output_col="signal_template")
    df["signal_template"] = df["signal_template"].fillna("General").astype(str).str.strip()
    df.loc[df["signal_template"] == "", "signal_template"] = "General"
    spec_profiles = df.apply(_spec_risk_profile_from_row, axis=1, result_type="expand")
    spec_profiles.columns = ["_spec_risk_score", "_spec_risk_label", "_spec_risk_subtype"]
    df["spec_risk_subtype"] = spec_profiles["_spec_risk_subtype"].fillna("").astype(str).str.strip()
    df.loc[df["spec_risk_subtype"] == "", "spec_risk_subtype"] = "正常"
    df["spec_risk_bucket"] = df.apply(_spec_risk_bucket_from_row, axis=1)

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

    overall_by_signal_template = (
        df.groupby(["horizon_days", "watch_type", "signal_template"], dropna=False)
        .agg(
            n=("realized_ret_pct", "count"),
            win_rate=("win", "mean"),
            avg_ret=("realized_ret_pct", "mean"),
            med_ret=("realized_ret_pct", "median"),
        )
        .reset_index()
        .sort_values(by=["horizon_days", "watch_type", "n", "avg_ret"], ascending=[True, True, False, False])
    )
    overall_by_signal_template["win_rate"] = (overall_by_signal_template["win_rate"] * 100).round(1)
    for c in ["avg_ret", "med_ret"]:
        overall_by_signal_template[c] = overall_by_signal_template[c].round(2)

    overall_by_scenario_template = (
        df.groupby(["horizon_days", "watch_type", "scenario_label", "signal_template"], dropna=False)
        .agg(
            n=("realized_ret_pct", "count"),
            win_rate=("win", "mean"),
            avg_ret=("realized_ret_pct", "mean"),
            med_ret=("realized_ret_pct", "median"),
        )
        .reset_index()
        .sort_values(by=["horizon_days", "watch_type", "scenario_label", "n", "avg_ret"], ascending=[True, True, True, False, False])
    )
    overall_by_scenario_template["win_rate"] = (overall_by_scenario_template["win_rate"] * 100).round(1)
    for c in ["avg_ret", "med_ret"]:
        overall_by_scenario_template[c] = overall_by_scenario_template[c].round(2)

    overall_by_spec_risk = (
        df.groupby(["horizon_days", "watch_type", "spec_risk_bucket"], dropna=False)
        .agg(
            n=("realized_ret_pct", "count"),
            win_rate=("win", "mean"),
            avg_ret=("realized_ret_pct", "mean"),
            med_ret=("realized_ret_pct", "median"),
        )
        .reset_index()
        .sort_values(by=["horizon_days", "watch_type", "spec_risk_bucket"], ascending=[True, True, True])
    )
    overall_by_spec_risk["win_rate"] = (overall_by_spec_risk["win_rate"] * 100).round(1)
    for c in ["avg_ret", "med_ret"]:
        overall_by_spec_risk[c] = overall_by_spec_risk[c].round(2)

    overall_by_spec_subtype = (
        df[df["spec_risk_bucket"] != "normal"]
        .groupby(["horizon_days", "watch_type", "spec_risk_subtype"], dropna=False)
        .agg(
            n=("realized_ret_pct", "count"),
            win_rate=("win", "mean"),
            avg_ret=("realized_ret_pct", "mean"),
            med_ret=("realized_ret_pct", "median"),
        )
        .reset_index()
        .sort_values(by=["horizon_days", "watch_type", "n", "avg_ret"], ascending=[True, True, False, False])
    )
    if not overall_by_spec_subtype.empty:
        overall_by_spec_subtype["win_rate"] = (overall_by_spec_subtype["win_rate"] * 100).round(1)
        for c in ["avg_ret", "med_ret"]:
            overall_by_spec_subtype[c] = overall_by_spec_subtype[c].round(2)

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
    spec_risk_check = pd.DataFrame()
    threshold_guard_check = pd.DataFrame()
    short_threshold_diagnostics = pd.DataFrame()
    short_gate_promotion_watch = pd.DataFrame()
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
                threshold_guard_check = delta_ok_minus_below.rename(
                    columns={
                        "delta_win_rate": "delta_win_rate_ok_minus_below",
                        "delta_avg_ret": "delta_avg_ret_ok_minus_below",
                        "delta_med_ret": "delta_med_ret_ok_minus_below",
                    }
                ).copy()

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
        threshold_guard_check = pd.DataFrame()

    try:
        short_threshold_diagnostics = overall_by_action_status.copy()
        short_threshold_diagnostics = short_threshold_diagnostics[
            (short_threshold_diagnostics["watch_type"].astype(str) == "short")
            & (short_threshold_diagnostics["reco_status"].isin(["ok", "below_threshold"]))
        ].copy()
        if not short_threshold_diagnostics.empty:
            short_threshold_diagnostics = short_threshold_diagnostics.sort_values(
                by=["horizon_days", "reco_status", "avg_ret", "n"],
                ascending=[True, True, False, False],
            ).reset_index(drop=True)
    except Exception:
        short_threshold_diagnostics = pd.DataFrame()

    try:
        if not short_threshold_diagnostics.empty:
            ok_baseline = short_threshold_diagnostics[
                short_threshold_diagnostics["reco_status"].astype(str) == "ok"
            ].copy()
            ok_baseline = (
                ok_baseline.groupby(["horizon_days", "watch_type"], dropna=False)
                .agg(
                    ok_n=("n", "sum"),
                    ok_win_rate=("win_rate", "mean"),
                    ok_avg_ret=("avg_ret", "mean"),
                    ok_med_ret=("med_ret", "mean"),
                )
                .reset_index()
            )
            below_actions = short_threshold_diagnostics[
                short_threshold_diagnostics["reco_status"].astype(str) == "below_threshold"
            ].copy()
            if not below_actions.empty and not ok_baseline.empty:
                short_gate_promotion_watch = below_actions.merge(
                    ok_baseline,
                    on=["horizon_days", "watch_type"],
                    how="left",
                )
                short_gate_promotion_watch["below_n"] = pd.to_numeric(short_gate_promotion_watch["n"], errors="coerce").astype("Int64")
                short_gate_promotion_watch["ok_n"] = pd.to_numeric(short_gate_promotion_watch["ok_n"], errors="coerce").astype("Int64")
                min_n_promo = pd.concat(
                    [
                        pd.to_numeric(short_gate_promotion_watch["below_n"], errors="coerce"),
                        pd.to_numeric(short_gate_promotion_watch["ok_n"], errors="coerce"),
                    ],
                    axis=1,
                ).min(axis=1)
                short_gate_promotion_watch["min_n"] = min_n_promo.astype("Int64")
                short_gate_promotion_watch["confidence"] = [
                    _confidence_label(int(x)) if pd.notna(x) else "low" for x in min_n_promo.tolist()
                ]
                short_gate_promotion_watch["delta_win_rate_below_minus_ok"] = (
                    pd.to_numeric(short_gate_promotion_watch["win_rate"], errors="coerce")
                    - pd.to_numeric(short_gate_promotion_watch["ok_win_rate"], errors="coerce")
                ).round(1)
                short_gate_promotion_watch["delta_avg_ret_below_minus_ok"] = (
                    pd.to_numeric(short_gate_promotion_watch["avg_ret"], errors="coerce")
                    - pd.to_numeric(short_gate_promotion_watch["ok_avg_ret"], errors="coerce")
                ).round(2)
                short_gate_promotion_watch["delta_med_ret_below_minus_ok"] = (
                    pd.to_numeric(short_gate_promotion_watch["med_ret"], errors="coerce")
                    - pd.to_numeric(short_gate_promotion_watch["ok_med_ret"], errors="coerce")
                ).round(2)

                def _promotion_verdict(row: pd.Series) -> str:
                    below_n = pd.to_numeric(row.get("below_n"), errors="coerce")
                    delta_avg = pd.to_numeric(row.get("delta_avg_ret_below_minus_ok"), errors="coerce")
                    if pd.notna(below_n) and below_n >= 3 and pd.notna(delta_avg) and delta_avg >= 0:
                        return "watch_upgrade"
                    if pd.notna(below_n) and below_n >= 2 and pd.notna(delta_avg) and delta_avg >= -1.0:
                        return "mixed"
                    return "keep_guardrail"

                short_gate_promotion_watch["verdict"] = short_gate_promotion_watch.apply(_promotion_verdict, axis=1)
                short_gate_promotion_watch = short_gate_promotion_watch[
                    [
                        "horizon_days",
                        "watch_type",
                        "action",
                        "below_n",
                        "ok_n",
                        "min_n",
                        "confidence",
                        "win_rate",
                        "ok_win_rate",
                        "delta_win_rate_below_minus_ok",
                        "avg_ret",
                        "ok_avg_ret",
                        "delta_avg_ret_below_minus_ok",
                        "med_ret",
                        "ok_med_ret",
                        "delta_med_ret_below_minus_ok",
                        "verdict",
                    ]
                ].rename(
                    columns={
                        "win_rate": "below_win_rate",
                        "avg_ret": "below_avg_ret",
                        "med_ret": "below_med_ret",
                    }
                )
                verdict_order = {"watch_upgrade": 0, "mixed": 1, "keep_guardrail": 2}
                short_gate_promotion_watch["_verdict_order"] = short_gate_promotion_watch["verdict"].map(verdict_order).fillna(9)
                short_gate_promotion_watch = short_gate_promotion_watch.sort_values(
                    by=["horizon_days", "_verdict_order", "delta_avg_ret_below_minus_ok", "below_n"],
                    ascending=[True, True, False, False],
                ).drop(columns="_verdict_order")
    except Exception:
        short_gate_promotion_watch = pd.DataFrame()

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

    try:
        spec_base = overall_by_spec_risk.copy()
        spec_base = spec_base[spec_base["spec_risk_bucket"].isin(["normal", "high"])].copy()
        if not spec_base.empty:
            normal = spec_base[spec_base["spec_risk_bucket"] == "normal"].copy()
            high = spec_base[spec_base["spec_risk_bucket"] == "high"].copy()
            merged_spec = high.merge(
                normal,
                on=["horizon_days", "watch_type"],
                how="inner",
                suffixes=("_high", "_normal"),
            )
            if not merged_spec.empty:
                min_n_spec = pd.concat(
                    [
                        pd.to_numeric(merged_spec["n_high"], errors="coerce"),
                        pd.to_numeric(merged_spec["n_normal"], errors="coerce"),
                    ],
                    axis=1,
                ).min(axis=1)
                spec_risk_check = pd.DataFrame(
                    {
                        "horizon_days": merged_spec["horizon_days"],
                        "watch_type": merged_spec["watch_type"],
                        "high_n": merged_spec["n_high"],
                        "normal_n": merged_spec["n_normal"],
                        "min_n": min_n_spec.astype("Int64"),
                        "confidence": [_confidence_label(int(x)) if pd.notna(x) else "low" for x in min_n_spec.tolist()],
                        "delta_win_rate_high_minus_normal": (
                            pd.to_numeric(merged_spec["win_rate_high"], errors="coerce")
                            - pd.to_numeric(merged_spec["win_rate_normal"], errors="coerce")
                        ).round(1),
                        "delta_avg_ret_high_minus_normal": (
                            pd.to_numeric(merged_spec["avg_ret_high"], errors="coerce")
                            - pd.to_numeric(merged_spec["avg_ret_normal"], errors="coerce")
                        ).round(2),
                        "delta_med_ret_high_minus_normal": (
                            pd.to_numeric(merged_spec["med_ret_high"], errors="coerce")
                            - pd.to_numeric(merged_spec["med_ret_normal"], errors="coerce")
                        ).round(2),
                    }
                ).sort_values(by=["horizon_days", "watch_type"])
    except Exception:
        spec_risk_check = pd.DataFrame()

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
        "overall_by_signal_template": overall_by_signal_template,
        "overall_by_scenario_template": overall_by_scenario_template,
        "overall_by_spec_risk": overall_by_spec_risk,
        "overall_by_spec_subtype": overall_by_spec_subtype,
        "delta_ok_minus_below": delta_ok_minus_below,
        "delta_ok_minus_below_by_date": delta_ok_minus_below_by_date,
        "threshold_guard_check": threshold_guard_check,
        "short_threshold_diagnostics": short_threshold_diagnostics,
        "short_gate_promotion_watch": short_gate_promotion_watch,
        "heat_bias_check": heat_bias_check,
        "heat_bias_by_scenario": heat_bias_by_scenario,
        "heat_bias_by_date": heat_bias_by_date,
        "spec_risk_check": spec_risk_check,
    }


def build_summary_markdown(
    outcomes: pd.DataFrame,
    source: str,
    now_local: datetime | None = None,
    alert_tracking: pd.DataFrame | None = None,
) -> str:
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

    key_findings = build_key_findings(parts)
    if key_findings:
        lines.append("## Key Findings")
        lines.extend([f"- {item}" for item in key_findings])
        lines.append("")

    band_parts = summarize_atr_band_checkpoints(alert_tracking if alert_tracking is not None else pd.DataFrame())
    band_findings = build_atr_band_findings(band_parts)
    if band_findings:
        lines.append("## ATR Band Findings")
        lines.extend([f"- {item}" for item in band_findings])
        lines.append("")

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
    if not parts["overall_by_signal_template"].empty:
        lines.extend(["## Overall By Signal Template (all dates)", _table_markdown(parts["overall_by_signal_template"]).rstrip(), ""])
    if not parts["overall_by_spec_risk"].empty:
        lines.extend(["## Overall By Spec Risk (all dates)", _table_markdown(parts["overall_by_spec_risk"]).rstrip(), ""])
    if not parts["overall_by_spec_subtype"].empty:
        lines.extend(["## Overall By Spec Subtype (all dates)", _table_markdown(parts["overall_by_spec_subtype"]).rstrip(), ""])
    if not parts["overall_by_scenario"].empty:
        lines.extend(["## Overall By Scenario (all dates)", _table_markdown(parts["overall_by_scenario"]).rstrip(), ""])
    if not parts["overall_by_scenario_template"].empty:
        lines.extend(["## Overall By Scenario + Signal Template (all dates, top 80)", _table_markdown(parts["overall_by_scenario_template"].head(80)).rstrip(), ""])
    if not parts["heat_bias_check"].empty:
        lines.extend(["## Heat Bias Check (hot - normal)", _table_markdown(parts["heat_bias_check"]).rstrip(), ""])
    if not parts["heat_bias_by_scenario"].empty:
        lines.extend(["## Heat Bias By Scenario (hot - normal)", _table_markdown(parts["heat_bias_by_scenario"]).rstrip(), ""])
    if not parts["heat_bias_by_date"].empty:
        lines.extend(["## Heat Bias By Date (hot - normal, top 20)", _table_markdown(parts["heat_bias_by_date"].head(20)).rstrip(), ""])
    if not parts["spec_risk_check"].empty:
        lines.extend(["## Spec Risk Check (high - normal)", _table_markdown(parts["spec_risk_check"]).rstrip(), ""])
    if not band_parts["band_coverage"].empty:
        lines.extend(["## ATR Band Coverage", _table_markdown(band_parts["band_coverage"]).rstrip(), ""])
    if not band_parts["band_checkpoints"].empty:
        lines.extend(["## ATR Band Checkpoints", _table_markdown(band_parts["band_checkpoints"]).rstrip(), ""])
    if not parts["overall_by_signal_status"].empty:
        lines.extend(["## Overall By Signal + reco_status (all dates)", _table_markdown(parts["overall_by_signal_status"]).rstrip(), ""])
    if not parts["delta_ok_minus_below"].empty:
        lines.extend(["## Delta (ok - below_threshold) By Signal (all dates)", _table_markdown(parts["delta_ok_minus_below"]).rstrip(), ""])
    if not parts["delta_ok_minus_below_by_date"].empty:
        lines.extend(["## Delta (ok - below_threshold) By Signal Date (top 30)", _table_markdown(parts["delta_ok_minus_below_by_date"].head(30)).rstrip(), ""])
    if not parts["threshold_guard_check"].empty:
        lines.extend(["## Threshold Guard Check (ok - below_threshold)", _table_markdown(parts["threshold_guard_check"]).rstrip(), ""])
    else:
        lines.extend(["## Threshold Guard Check (ok - below_threshold)", "_None_", ""])

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
    if not parts["short_threshold_diagnostics"].empty:
        lines.extend(["## Short Threshold Diagnostics", _table_markdown(parts["short_threshold_diagnostics"].head(20)).rstrip(), ""])
    else:
        lines.extend(["## Short Threshold Diagnostics", "_None_", ""])
    if not parts["short_gate_promotion_watch"].empty:
        lines.extend(["## Short Gate Promotion Watch", _table_markdown(parts["short_gate_promotion_watch"].head(20)).rstrip(), ""])
    else:
        lines.extend(["## Short Gate Promotion Watch", "_None_", ""])
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
    alert_tracking = pd.DataFrame()
    if ALERT_TRACK_CSV.exists():
        try:
            alert_tracking = pd.read_csv(ALERT_TRACK_CSV)
        except Exception:
            alert_tracking = pd.DataFrame()
    report = build_summary_markdown(outcomes, source=str(outcomes_csv), alert_tracking=alert_tracking)
    out_path.write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
