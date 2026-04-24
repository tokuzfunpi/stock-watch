from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Iterable, Optional

import pandas as pd


def summarize_events(events_df: pd.DataFrame, horizons: list[int]) -> pd.DataFrame:
    rows = []
    for horizon in horizons:
        col = f"ret_{horizon}d"
        series = events_df[col].dropna()
        if series.empty:
            continue
        rows.append(
            {
                "horizon": horizon,
                "trades": int(series.shape[0]),
                "win_rate_pct": round((series.gt(0).mean()) * 100, 2),
                "avg_return_pct": round(series.mean(), 2),
                "median_return_pct": round(series.median(), 2),
            }
        )
    return pd.DataFrame(rows)


def _load_backtest_state(
    state_path: Path,
    *,
    signature: str,
    backtest_period: str,
    lookahead_days: list[int],
) -> dict[str, str]:
    if not state_path.exists():
        return {}
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if str(payload.get("signature", "")) != str(signature):
        return {}
    if str(payload.get("backtest_period", "")) != str(backtest_period):
        return {}
    if list(payload.get("lookahead_days", [])) != list(lookahead_days):
        return {}
    last_scanned = payload.get("last_scanned_dates", {})
    if not isinstance(last_scanned, dict):
        return {}
    return {str(k): str(v) for k, v in last_scanned.items() if str(v).strip()}


def _save_backtest_state(
    state_path: Path,
    *,
    signature: str,
    backtest_period: str,
    lookahead_days: list[int],
    last_scanned_dates: dict[str, str],
    last_run_mode: str,
    last_run_scanned_cutoffs: int,
) -> None:
    payload = {
        "signature": signature,
        "backtest_period": backtest_period,
        "lookahead_days": list(lookahead_days),
        "last_scanned_dates": last_scanned_dates,
        "last_run_mode": last_run_mode,
        "last_run_scanned_cutoffs": int(last_run_scanned_cutoffs),
    }
    state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run_backtest_dual(
    *,
    backtest_enabled: bool,
    signature: str,
    watchlist: Iterable[dict],
    backtest_period: str,
    lookahead_days: list[int],
    outdir: Path,
    get_indicator_frame: Callable[[str, str], pd.DataFrame],
    detect_row: Callable[[pd.DataFrame, str, str, str, str], dict],
    logger,
) -> tuple[Optional[pd.DataFrame], Optional[pd.DataFrame]]:
    if not backtest_enabled:
        return None, None

    state_path = outdir / "backtest_state.json"
    steady_events_path = outdir / "backtest_events_steady.csv"
    attack_events_path = outdir / "backtest_events_attack.csv"
    steady_summary_path = outdir / "backtest_summary_steady.csv"
    attack_summary_path = outdir / "backtest_summary_attack.csv"

    last_scanned_dates = _load_backtest_state(
        state_path,
        signature=signature,
        backtest_period=backtest_period,
        lookahead_days=lookahead_days,
    )
    can_incremental = bool(last_scanned_dates) and steady_events_path.exists() and attack_events_path.exists()
    if can_incremental:
        try:
            steady_existing = pd.read_csv(steady_events_path)
        except Exception:
            steady_existing = pd.DataFrame()
        try:
            attack_existing = pd.read_csv(attack_events_path)
        except Exception:
            attack_existing = pd.DataFrame()
    else:
        steady_existing = pd.DataFrame()
        attack_existing = pd.DataFrame()
        last_scanned_dates = {}

    scanned_cutoffs = 0
    steady_events: list[dict] = []
    attack_events: list[dict] = []
    max_horizon = max(lookahead_days)
    updated_scanned_dates = dict(last_scanned_dates)

    for item in watchlist:
        ticker, name, group, layer = item["ticker"], item["name"], item["group"], item["layer"]
        try:
            df = get_indicator_frame(ticker, backtest_period)
            start_idx = 250
            last_scanned_date = last_scanned_dates.get(str(ticker), "")
            if last_scanned_date:
                idx_matches = [i for i, dt in enumerate(df.index.strftime("%Y-%m-%d")) if dt == last_scanned_date]
                if idx_matches:
                    start_idx = max(start_idx, idx_matches[-1] + 1)

            end_idx = len(df) - max_horizon
            if start_idx >= end_idx:
                if end_idx > 250:
                    updated_scanned_dates[str(ticker)] = df.index[end_idx - 1].strftime("%Y-%m-%d")
                continue

            for i in range(start_idx, end_idx):
                scanned_cutoffs += 1
                cut = df.iloc[: i + 1].copy()
                row = detect_row(cut, ticker, name, group, layer)
                entry = float(df.iloc[i]["Close"])

                event = {
                    "ticker": ticker,
                    "date": cut.index[-1].strftime("%Y-%m-%d"),
                    "group": group,
                    "setup_score": row["setup_score"],
                    "risk_score": row["risk_score"],
                    "signals": row["signals"],
                    "ret5_pct": row["ret5_pct"],
                    "ret20_pct": row["ret20_pct"],
                    "volume_ratio20": row["volume_ratio20"],
                }
                for horizon in lookahead_days:
                    future = float(df.iloc[i + horizon]["Close"])
                    event[f"ret_{horizon}d"] = round((future / entry - 1.0) * 100, 2)

                if row["setup_score"] >= 5 and row["risk_score"] <= 4:
                    steady_events.append(event.copy())

                if (
                    row["ret5_pct"] > 8
                    and row["volume_ratio20"] > 1.3
                    and row["ret20_pct"] > 0
                ) or ("ACCEL" in row["signals"]):
                    attack_events.append(event.copy())

            updated_scanned_dates[str(ticker)] = df.index[end_idx - 1].strftime("%Y-%m-%d")

        except Exception as exc:
            logger.exception("BACKTEST FAILED: %s %s -> %s", ticker, name, exc)

    steady_new = pd.DataFrame(steady_events)
    attack_new = pd.DataFrame(attack_events)
    steady_df = pd.concat([steady_existing, steady_new], ignore_index=True) if not steady_existing.empty or not steady_new.empty else None
    attack_df = pd.concat([attack_existing, attack_new], ignore_index=True) if not attack_existing.empty or not attack_new.empty else None

    steady_summary = summarize_events(steady_df, lookahead_days) if steady_df is not None else None
    attack_summary = summarize_events(attack_df, lookahead_days) if attack_df is not None else None

    if steady_df is not None:
        steady_df.to_csv(steady_events_path, index=False, encoding="utf-8-sig")
    if attack_df is not None:
        attack_df.to_csv(attack_events_path, index=False, encoding="utf-8-sig")
    if steady_summary is not None:
        steady_summary.to_csv(steady_summary_path, index=False, encoding="utf-8-sig")
    if attack_summary is not None:
        attack_summary.to_csv(attack_summary_path, index=False, encoding="utf-8-sig")

    if not can_incremental:
        last_run_mode = "full_rebuild"
    elif scanned_cutoffs == 0:
        last_run_mode = "incremental_noop"
    else:
        last_run_mode = "incremental_update"

    _save_backtest_state(
        state_path,
        signature=signature,
        backtest_period=backtest_period,
        lookahead_days=lookahead_days,
        last_scanned_dates=updated_scanned_dates,
        last_run_mode=last_run_mode,
        last_run_scanned_cutoffs=scanned_cutoffs,
    )

    return steady_summary, attack_summary
