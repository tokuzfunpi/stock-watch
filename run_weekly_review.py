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
WEEKLY_REVIEW_MD = REPO_ROOT / "theme_watchlist_daily" / "weekly_review.md"
WEEKLY_REVIEW_JSON = REPO_ROOT / "theme_watchlist_daily" / "weekly_review.json"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a weekly decision note from local verification outputs.")
    parser.add_argument("--outcomes-csv", default=str(VERIFICATION_OUTCOMES_CSV))
    parser.add_argument("--feedback-csv", default=str(FEEDBACK_SENSITIVITY_CSV))
    parser.add_argument("--alert-csv", default=str(ALERT_TRACK_CSV))
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


def build_decisions(
    parts: dict[str, pd.DataFrame],
    band_parts: dict[str, pd.DataFrame],
    feedback_csv: Path,
) -> dict[str, dict[str, object]]:
    threshold_row = _find_single_row(parts.get("delta_ok_minus_below", pd.DataFrame()), horizon_days=1, watch_type="midlong")
    heat_row = _find_single_row(parts.get("heat_bias_check", pd.DataFrame()), horizon_days=1, watch_type="midlong")

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

    return {
        "threshold": threshold_decision,
        "atr": atr_decision,
        "feedback": feedback_decision,
    }


def build_weekly_review_payload(
    *,
    outcomes_csv: Path,
    feedback_csv: Path,
    alert_csv: Path,
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

    overall_by_signal = parts.get("overall_by_signal", pd.DataFrame())
    weekly_checkpoint = parts.get("delta_ok_minus_below", pd.DataFrame())
    heat_bias_check = parts.get("heat_bias_check", pd.DataFrame())

    summary = {
        "signal_dates": recent_dates,
        "row_count": int(len(recent_outcomes)),
        "ok_rows": int((recent_outcomes.get("status", pd.Series(dtype=str)).astype(str) == "ok").sum()) if not recent_outcomes.empty else 0,
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
            "atr_band_coverage": band_parts.get("band_coverage", pd.DataFrame()).to_dict(orient="records"),
        },
    }


def render_weekly_review_markdown(payload: dict[str, object]) -> str:
    summary = payload.get("summary", {})
    decisions = payload.get("decisions", {})
    tables = payload.get("tables", {})
    signal_dates = summary.get("signal_dates", [])
    signal_range = f"{signal_dates[0]} → {signal_dates[-1]}" if signal_dates else "n/a"

    lines = [
        "# Weekly Review",
        f"- Generated: {payload.get('generated_at', '')}",
        f"- Source: {payload.get('source', '')}",
        f"- Signal dates: `{signal_range}`",
        f"- Included signal_date count: `{len(signal_dates)}`",
        f"- Outcome rows: `{summary.get('row_count', 0)}`",
        f"- OK rows: `{summary.get('ok_rows', 0)}`",
        "",
        "## Decisions",
        "",
    ]
    for key in ["threshold", "atr", "feedback"]:
        item = decisions.get(key, {})
        lines.append(f"- `{key}`: `{item.get('status', 'hold')}` — {item.get('detail', '')}")

    lines.extend(["", "## Overall By Signal", _table_markdown(pd.DataFrame(tables.get("overall_by_signal", []))).rstrip(), ""])
    lines.extend(["## Threshold Delta", _table_markdown(pd.DataFrame(tables.get("weekly_threshold_delta", []))).rstrip(), ""])
    lines.extend(["## Heat Bias Check", _table_markdown(pd.DataFrame(tables.get("heat_bias_check", []))).rstrip(), ""])
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
        max_signal_dates=int(args.max_signal_dates),
    )
    write_outputs(payload, out=Path(args.out), json_out=Path(args.json_out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
