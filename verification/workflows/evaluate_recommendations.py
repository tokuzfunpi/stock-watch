from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime
import re
import sys
import time
from pathlib import Path

import pandas as pd
import yfinance as yf

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from stock_watch.paths import VERIFICATION_OUTDIR
from stock_watch.runtime import ALERT_TRACK_CSV, LOCAL_TZ, logger
from stock_watch.signals import build_speculative_risk_profile


@dataclass(frozen=True)
class EvalConfig:
    period: str = "180d"
    batch_size: int = 25
    retries: int = 3
    backoff_seconds: float = 1.0
    cache_dir: Path | None = None


def _spec_profile_from_snapshot_row(row) -> tuple[object, str, str]:
    score = pd.to_numeric(getattr(row, "spec_risk_score", None), errors="coerce")
    label = str(getattr(row, "spec_risk_label", "")).strip()
    subtype = str(getattr(row, "spec_risk_subtype", "")).strip()
    note = str(getattr(row, "spec_risk_note", "")).strip()
    if pd.notna(score) or label or subtype or note:
        return (None if pd.isna(score) else int(score), label, subtype, note)

    def _num(name: str, default: float = 0.0) -> float:
        value = pd.to_numeric(getattr(row, name, None), errors="coerce")
        return float(default if pd.isna(value) else value)

    def _int_num(name: str, default: int = 0) -> int:
        value = pd.to_numeric(getattr(row, name, None), errors="coerce")
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
            signals=str(getattr(row, "signals", "")),
            group=str(getattr(row, "group", "")),
        )
        return profile.score, profile.label, profile.subtype, profile.note
    except Exception:
        return None, "", "", ""


def classify_market_heat(
    *,
    ret5_pct: object,
    ret20_pct: object,
    risk_score: object,
    volume_ratio20: object,
) -> tuple[str, str]:
    def _to_float(value: object) -> float:
        try:
            if value is None or pd.isna(value):
                return 0.0
            return float(value)
        except Exception:
            return 0.0

    ret5 = _to_float(ret5_pct)
    ret20 = _to_float(ret20_pct)
    risk = _to_float(risk_score)
    vol = _to_float(volume_ratio20)

    reasons: list[str] = []
    if risk >= 5:
        reasons.append("high_risk")
    if ret5 >= 15:
        reasons.append("ret5_hot")
    if ret20 >= 25:
        reasons.append("ret20_hot")
    if vol >= 2.5:
        reasons.append("volume_hot")
    if reasons:
        return "hot", ",".join(reasons)

    reasons = []
    if risk >= 3:
        reasons.append("risk_up")
    if ret5 >= 8:
        reasons.append("ret5_warm")
    if ret20 >= 12:
        reasons.append("ret20_warm")
    if vol >= 1.5:
        reasons.append("volume_warm")
    if reasons:
        return "warm", ",".join(reasons)
    return "normal", ""


def enrich_market_heat_columns(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    if "market_heat" not in out.columns:
        out["market_heat"] = ""
    if "market_heat_reason" not in out.columns:
        out["market_heat_reason"] = ""

    missing_mask = out["market_heat"].isna() | (out["market_heat"].astype(str).str.strip() == "")
    if not missing_mask.any():
        return out

    for idx in out.index[missing_mask]:
        heat, reason = classify_market_heat(
            ret5_pct=out.at[idx, "ret5_pct"] if "ret5_pct" in out.columns else None,
            ret20_pct=out.at[idx, "ret20_pct"] if "ret20_pct" in out.columns else None,
            risk_score=out.at[idx, "risk_score"] if "risk_score" in out.columns else None,
            volume_ratio20=out.at[idx, "volume_ratio20"] if "volume_ratio20" in out.columns else None,
        )
        out.at[idx, "market_heat"] = heat
        if str(out.at[idx, "market_heat_reason"]).strip() == "":
            out.at[idx, "market_heat_reason"] = reason
    return out


def enrich_scenario_label_columns(
    df: pd.DataFrame,
    snapshots: pd.DataFrame | None = None,
    alert_tracking_csv: Path | None = None,
) -> pd.DataFrame:
    if df.empty:
        return df

    out = df.copy()
    if "scenario_label" not in out.columns:
        out["scenario_label"] = ""
    out["scenario_label"] = out["scenario_label"].astype(str).str.strip()
    out.loc[
        (out["scenario_label"] == "")
        | (out["scenario_label"] == "b''")
        | (out["scenario_label"] == "nan")
        | (out["scenario_label"] == "unknown"),
        "scenario_label",
    ] = ""

    merge_keys = ["signal_date", "watch_type", "ticker"]
    missing_mask = out["scenario_label"] == ""

    if snapshots is not None and not snapshots.empty and all(c in snapshots.columns for c in merge_keys + ["scenario_label"]):
        snap = snapshots[merge_keys + ["scenario_label"]].copy()
        for c in merge_keys:
            snap[c] = snap[c].astype(str).str.strip()
        snap["scenario_label"] = snap["scenario_label"].astype(str).str.strip()
        snap = snap[(snap["scenario_label"] != "") & (snap["scenario_label"] != "b''") & (snap["scenario_label"] != "nan")]
        if not snap.empty and missing_mask.any():
            snap = snap.drop_duplicates(subset=merge_keys, keep="last")
            merged = out.loc[missing_mask, merge_keys].astype(str).merge(snap, on=merge_keys, how="left")
            out.loc[missing_mask, "scenario_label"] = merged["scenario_label"].fillna("").tolist()
            missing_mask = out["scenario_label"] == ""

    tracking_path = alert_tracking_csv or ALERT_TRACK_CSV
    if missing_mask.any() and tracking_path.exists():
        try:
            hist = pd.read_csv(tracking_path, dtype={"alert_date": "string", "watch_type": "string", "ticker": "string", "scenario_label": "string"})
        except Exception:
            hist = pd.DataFrame()
        if not hist.empty and all(c in hist.columns for c in ["alert_date", "watch_type", "ticker", "scenario_label"]):
            hist = hist[["alert_date", "watch_type", "ticker", "scenario_label"]].copy()
            hist = hist.rename(columns={"alert_date": "signal_date"})
            for c in merge_keys:
                hist[c] = hist[c].astype(str).str.strip()
            hist["scenario_label"] = hist["scenario_label"].astype(str).str.strip()
            hist = hist[(hist["scenario_label"] != "") & (hist["scenario_label"] != "b''") & (hist["scenario_label"] != "nan")]
            if not hist.empty:
                hist = hist.drop_duplicates(subset=merge_keys, keep="last")
                merged = out.loc[missing_mask, merge_keys].astype(str).merge(hist, on=merge_keys, how="left")
                out.loc[missing_mask, "scenario_label"] = merged["scenario_label"].fillna("").tolist()

    out.loc[out["scenario_label"] == "", "scenario_label"] = "unknown"
    return out


def compute_forward_return_pct(
    close_series: pd.Series,
    signal_date: str,
    horizon_days: int,
) -> tuple[float | None, float | None, str, str]:
    if close_series is None or close_series.empty:
        return None, None, "empty_series", ""
    if horizon_days < 1:
        return None, None, "invalid_horizon", ""

    series = close_series.dropna().copy()
    series.index = pd.to_datetime(series.index).tz_localize(None)

    target_date = pd.to_datetime(signal_date).tz_localize(None)
    status_detail = ""
    if target_date not in series.index:
        # Best effort: if snapshot date is a non-trading day (holiday/weekend) or Yahoo shifts
        # the index, use the next available trading day within a small window.
        try:
            idx_pos = int(series.index.searchsorted(target_date))
        except Exception:
            idx_pos = -1

        if 0 <= idx_pos < len(series.index):
            shifted = series.index[idx_pos]
            shift_days = int((shifted - target_date).days)
            if shift_days >= 0 and shift_days <= 3:
                target_date = shifted
                status_detail = f"signal_date_shifted:+{shift_days}d"
            else:
                return None, None, "signal_date_missing", ""
        else:
            return None, None, "signal_date_missing", ""

    idx = series.index.get_loc(target_date)
    if isinstance(idx, slice):
        idx = idx.start
    if not isinstance(idx, int):
        return None, None, "signal_date_ambiguous", status_detail

    entry = float(series.iloc[idx])
    out_idx = idx + horizon_days
    if out_idx >= len(series):
        return None, None, "insufficient_forward_data", status_detail
    out_close = float(series.iloc[out_idx])
    ret_pct = (out_close / entry - 1.0) * 100.0
    return ret_pct, out_close, "ok", status_detail


def _chunked(items: list[str], size: int) -> list[list[str]]:
    if size <= 0:
        return [items]
    return [items[i : i + size] for i in range(0, len(items), size)]


def _cache_covers_required_date(series: pd.Series | None, required_end_date: str) -> bool:
    if series is None or getattr(series, "empty", True):
        return False
    required_text = str(required_end_date or "").strip()
    if not required_text:
        return True
    try:
        required_dt = pd.to_datetime(required_text).tz_localize(None)
    except Exception:
        return True
    try:
        index = pd.to_datetime(series.index, errors="coerce").tz_localize(None)
    except Exception:
        return False
    index = index.dropna()
    if len(index) == 0:
        return False
    return bool(index.max() >= required_dt)


def _download_prices(
    tickers: list[str],
    cfg: EvalConfig,
    *,
    group_by_ticker: bool,
) -> tuple[pd.DataFrame | None, str]:
    last_err = ""
    tickers = [t for t in tickers if t]
    if not tickers:
        return None, "empty_ticker_list"

    for attempt in range(max(int(cfg.retries), 1)):
        try:
            df = yf.download(
                " ".join(tickers),
                period=cfg.period,
                interval="1d",
                auto_adjust=True,
                progress=False,
                threads=False,
                group_by="ticker" if group_by_ticker else None,
            )
            if df is None or getattr(df, "empty", True):
                raise RuntimeError("empty_dataframe")
            return df, ""
        except Exception as exc:
            last_err = str(exc) or exc.__class__.__name__
            if attempt < max(int(cfg.retries), 1) - 1:
                time.sleep(float(cfg.backoff_seconds) * (2**attempt))
            continue
    return None, last_err or "download_failed"


def fetch_close_series(
    tickers: list[str],
    cfg: EvalConfig,
    *,
    required_end_date: str = "",
) -> tuple[dict[str, pd.Series], dict[str, str]]:
    uniq = [str(t).strip() for t in tickers if str(t).strip()]
    seen: set[str] = set()
    uniq = [t for t in uniq if not (t in seen or seen.add(t))]
    if not uniq:
        return {}, {}

    out: dict[str, pd.Series] = {}
    errors: dict[str, str] = {}

    cache_dir = cfg.cache_dir
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)

    def cache_path(ticker: str) -> Path | None:
        if cache_dir is None:
            return None
        safe = re.sub(r"[^0-9A-Za-z]+", "_", str(ticker).strip())
        return cache_dir / f"{safe}.csv"

    def load_cached(ticker: str) -> pd.Series | None:
        p = cache_path(ticker)
        if p is None or not p.exists():
            return None
        try:
            df = pd.read_csv(p)
            if "Date" not in df.columns or "Close" not in df.columns:
                return None
            s = pd.Series(df["Close"].values, index=pd.to_datetime(df["Date"], errors="coerce"))
            s = s.dropna()
            return s if not s.empty else None
        except Exception:
            return None

    def save_cached(ticker: str, series: pd.Series) -> None:
        p = cache_path(ticker)
        if p is None:
            return
        try:
            s = series.dropna().copy()
            s.index = pd.to_datetime(s.index).tz_localize(None)
            df = pd.DataFrame({"Date": s.index.strftime("%Y-%m-%d"), "Close": s.values})
            df.to_csv(p, index=False)
        except Exception:
            return

    # 0) Load cached data first (helps when network is flaky/unavailable).
    for ticker in uniq:
        s = load_cached(ticker)
        if s is not None:
            if _cache_covers_required_date(s, required_end_date):
                out[ticker] = s
            else:
                try:
                    last_cached = pd.to_datetime(s.index, errors="coerce").max()
                    last_cached_text = "" if pd.isna(last_cached) else str(pd.Timestamp(last_cached).date())
                except Exception:
                    last_cached_text = "unknown"
                logger.info(
                    "Cached series stale for %s: last=%s, required_end_date=%s. Refreshing.",
                    ticker,
                    last_cached_text,
                    required_end_date,
                )

    # 1) Bulk (chunked) download for speed; may still miss some tickers.
    need_download = [t for t in uniq if t not in out]
    for chunk in _chunked(need_download, int(cfg.batch_size)):
        df, err = _download_prices(chunk, cfg, group_by_ticker=True)
        if df is None:
            for t in chunk:
                errors.setdefault(t, err)
            continue

        if isinstance(df.columns, pd.MultiIndex):
            for ticker in chunk:
                try:
                    if ticker not in df.columns.get_level_values(0):
                        continue
                    sub = df[ticker]
                    if "Close" not in sub.columns:
                        continue
                    series = sub["Close"].copy()
                    if getattr(series, "empty", True):
                        continue
                    out[ticker] = series
                    save_cached(ticker, series)
                except Exception as exc:
                    errors.setdefault(ticker, str(exc) or exc.__class__.__name__)
                    continue
        else:
            # Single ticker may return a normal dataframe.
            if len(chunk) == 1 and "Close" in df.columns:
                series = df["Close"].copy()
                if not getattr(series, "empty", True):
                    out[chunk[0]] = series
                    save_cached(chunk[0], series)

    missing = [t for t in uniq if t not in out]
    if missing:
        logger.info(
            "Bulk download missing %s/%s tickers. Falling back to per-ticker downloads.",
            len(missing),
            len(uniq),
        )

    # 2) Fallback: per-ticker download for whatever bulk missed.
    for ticker in missing:
        df, err = _download_prices([ticker], cfg, group_by_ticker=False)
        if df is None:
            errors.setdefault(ticker, err)
            continue
        try:
            if "Close" not in df.columns:
                errors.setdefault(ticker, "missing_close_column")
                continue
            series = df["Close"].copy()
            if getattr(series, "empty", True):
                errors.setdefault(ticker, "empty_series")
                continue
            out[ticker] = series
            save_cached(ticker, series)
        except Exception as exc:
            errors.setdefault(ticker, str(exc) or exc.__class__.__name__)
            continue

    # If a ticker succeeded, clear its error.
    for t in out.keys():
        errors.pop(t, None)
    return out, errors


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate recommendation snapshots vs future closes (best effort).")
    out_dir = VERIFICATION_OUTDIR
    parser.add_argument("--snapshot-csv", default=str(out_dir / "reco_snapshots.csv"))
    parser.add_argument("--outcomes-csv", default=str(out_dir / "reco_outcomes.csv"))
    parser.add_argument("--signal-date", default="", help="Evaluate snapshots for this signal date (YYYY-MM-DD).")
    parser.add_argument("--all-dates", action="store_true", help="Evaluate all signal_date values in snapshots.")
    parser.add_argument("--since", default="", help="YYYY-MM-DD (inclusive); only used with --all-dates.")
    parser.add_argument("--until", default="", help="YYYY-MM-DD (inclusive); only used with --all-dates.")
    parser.add_argument("--max-days", type=int, default=0, help="Limit number of signal_date days processed (0=unlimited).")
    parser.add_argument("--horizons", default="1,5,20", help="Comma-separated horizons in trading days.")
    parser.add_argument("--period", default="180d", help="yfinance period to fetch (e.g. 90d, 6mo).")
    parser.add_argument("--batch-size", type=int, default=25, help="Batch size for bulk yfinance download.")
    parser.add_argument("--retries", type=int, default=3, help="Retries per download attempt.")
    parser.add_argument("--backoff-seconds", type=float, default=1.0, help="Base backoff seconds between retries.")
    parser.add_argument("--cache-dir", default=str(out_dir / "yfinance_cache"), help="Local cache dir for Close series.")
    return parser.parse_args(argv)


def _normalize_snapshot_csv_inplace(path: Path) -> None:
    if not path.exists():
        return
    raw = path.read_text(encoding="utf-8", errors="replace")
    reader = csv.reader(raw.splitlines())
    rows = list(reader)
    if not rows:
        return

    header = list(rows[0])
    body = rows[1:]
    if not header:
        return

    max_len = max((len(r) for r in body), default=len(header))
    if max_len == len(header) + 1 and "source_sha" not in header:
        header.append("source_sha")

    normalized: list[list[str]] = [header]
    for r in body:
        if not r:
            continue
        if len(r) < len(header):
            r = r + [""] * (len(header) - len(r))
        elif len(r) > len(header):
            r = r[: len(header)]
        normalized.append(r)

    bak = path.with_suffix(path.suffix + ".bak")
    if not bak.exists():
        bak.write_text(raw, encoding="utf-8")

    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerows(normalized)


def load_snapshots_csv(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(
            path,
            dtype={
                "ticker": "string",
                "signal_date": "string",
                "watch_type": "string",
                "name": "string",
                "action": "string",
                "signals": "string",
                "grade": "string",
                "source": "string",
                "source_sha": "string",
            },
        )
    except pd.errors.ParserError:
        _normalize_snapshot_csv_inplace(path)
        return pd.read_csv(
            path,
            dtype={
                "ticker": "string",
                "signal_date": "string",
                "watch_type": "string",
                "name": "string",
                "action": "string",
                "signals": "string",
                "grade": "string",
                "source": "string",
                "source_sha": "string",
            },
        )


_VALID_TICKER_RE = re.compile(r"^[0-9A-Z^][0-9A-Z^.\-]*$", re.IGNORECASE)
_VALID_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_VALID_WATCH_TYPES = {"short", "midlong"}


def dedupe_snapshots_by_key(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    key_cols = ["signal_date", "watch_type", "ticker"]
    if any(col not in df.columns for col in key_cols):
        return df

    out = df.copy()
    for col in key_cols:
        out[col] = out[col].astype(str).str.strip()

    sort_cols = [col for col in ["generated_at", "source_sha", "source"] if col in out.columns]
    if sort_cols:
        out = out.sort_values(by=sort_cols, kind="stable")
    return out.drop_duplicates(subset=key_cols, keep="last").copy()


def dedupe_outcomes_by_key(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    key_cols = ["signal_date", "horizon_days", "watch_type", "ticker"]
    if any(col not in df.columns for col in key_cols):
        return df

    out = df.copy()
    for col in key_cols:
        out[col] = out[col].astype(str).str.strip()

    if "status" in out.columns:
        status_priority = {
            "ok": 3,
            "insufficient_forward_data": 2,
            "no_price_series": 1,
            "empty_series": 1,
            "invalid_horizon": 1,
        }
        out["_status_priority"] = out["status"].astype(str).map(status_priority).fillna(0)
    else:
        out["_status_priority"] = 0

    sort_cols = ["_status_priority"]
    for col in ["evaluated_at", "scenario_label", "source_sha", "source"]:
        if col in out.columns:
            sort_cols.append(col)
    out = out.sort_values(by=sort_cols, kind="stable")
    out = out.drop_duplicates(subset=key_cols, keep="last").copy()
    return out.drop(columns=["_status_priority"], errors="ignore")


def is_valid_snapshot_ticker(raw: str) -> bool:
    t = str(raw or "").strip().upper()
    if not t or not _VALID_TICKER_RE.match(t):
        return False
    if "." in t or t.startswith("^"):
        return True
    if t.isalpha() and len(t) >= 2:
        return True
    return False


def is_valid_signal_date(raw: str) -> bool:
    return bool(_VALID_DATE_RE.match(str(raw or "").strip()))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    snapshot_csv = Path(args.snapshot_csv)
    outcomes_csv = Path(args.outcomes_csv)
    cfg = EvalConfig(
        period=str(args.period),
        batch_size=int(args.batch_size),
        retries=int(args.retries),
        backoff_seconds=float(args.backoff_seconds),
        cache_dir=Path(str(args.cache_dir)) if str(args.cache_dir) else None,
    )

    if not snapshot_csv.exists():
        print(f"No snapshot file: {snapshot_csv}")
        return 0

    snapshots = load_snapshots_csv(snapshot_csv)
    if snapshots.empty:
        print("Snapshot file is empty.")
        return 0

    if "ticker" not in snapshots.columns:
        print("Snapshot file missing 'ticker' column. Re-run verify/backfill to rebuild snapshots.")
        return 0

    snapshots["ticker"] = snapshots["ticker"].astype(str).str.strip()
    invalid_mask = ~snapshots["ticker"].map(is_valid_snapshot_ticker)
    if invalid_mask.any():
        invalid = snapshots.loc[invalid_mask, "ticker"].astype(str).head(10).tolist()
        print(
            f"WARNING: Dropping {int(invalid_mask.sum())} snapshot rows with invalid tickers "
            f"(example: {', '.join(invalid)})."
        )
        snapshots = snapshots.loc[~invalid_mask].copy()
        if snapshots.empty:
            print(
                "No valid tickers left after filtering. "
                "Your reco_snapshots.csv is likely corrupted; consider regenerating via "
                "`python3.11 -m stock_watch verification backfill` then re-run evaluate."
            )
            return 0

    if "signal_date" in snapshots.columns:
        snapshots["signal_date"] = snapshots["signal_date"].astype(str).str.strip()
        invalid_date_mask = ~snapshots["signal_date"].map(is_valid_signal_date)
        if invalid_date_mask.any():
            invalid_dates = snapshots.loc[invalid_date_mask, "signal_date"].astype(str).head(10).tolist()
            print(
                f"WARNING: Dropping {int(invalid_date_mask.sum())} snapshot rows with invalid signal_date "
                f"(example: {', '.join(invalid_dates)})."
            )
            snapshots = snapshots.loc[~invalid_date_mask].copy()
            if snapshots.empty:
                print("No valid snapshots left after signal_date filtering. Re-run verify/backfill.")
                return 0

    if "watch_type" in snapshots.columns:
        snapshots["watch_type"] = snapshots["watch_type"].astype(str).str.strip().str.lower()
        invalid_watch_mask = ~snapshots["watch_type"].isin(_VALID_WATCH_TYPES)
        if invalid_watch_mask.any():
            invalid_watch = snapshots.loc[invalid_watch_mask, "watch_type"].astype(str).head(10).tolist()
            print(
                f"WARNING: Dropping {int(invalid_watch_mask.sum())} snapshot rows with invalid watch_type "
                f"(example: {', '.join(invalid_watch)})."
            )
            snapshots = snapshots.loc[~invalid_watch_mask].copy()
            if snapshots.empty:
                print("No valid snapshots left after watch_type filtering. Re-run verify/backfill.")
                return 0

    snapshots = dedupe_snapshots_by_key(snapshots)

    horizons = [int(x.strip()) for x in str(args.horizons).split(",") if x.strip()]
    horizons = sorted({h for h in horizons if h >= 1})
    if not horizons:
        print("No valid horizons.")
        return 0

    signal_date = str(args.signal_date).strip()
    if args.all_dates:
        if "signal_date" not in snapshots.columns:
            print("Snapshot file missing 'signal_date' column. Re-run verify/backfill to rebuild snapshots.")
            return 0
        dates = snapshots["signal_date"].dropna().astype(str).unique().tolist()
        dates = [d for d in dates if is_valid_signal_date(d)]
        dates.sort()
        if args.since:
            dates = [d for d in dates if d >= args.since]
        if args.until:
            dates = [d for d in dates if d <= args.until]
        if int(args.max_days) > 0:
            dates = dates[-int(args.max_days) :]
        if not dates:
            print("No valid signal_date values matched filters.")
            return 0
        snapshots = snapshots[snapshots["signal_date"].astype(str).isin(dates)].copy()
        print(f"Evaluating signal_date days: {len(dates)} (from {dates[0]} to {dates[-1]})")
    else:
        if not signal_date:
            non_empty = snapshots["signal_date"].dropna().astype(str)
            signal_date = non_empty.iloc[-1] if not non_empty.empty else ""
        if signal_date:
            snapshots = snapshots[snapshots["signal_date"].astype(str) == signal_date].copy()

    if snapshots.empty:
        print("No snapshots matched signal date.")
        return 0

    tickers = snapshots["ticker"].dropna().astype(str).tolist()
    required_end_date = ""
    if "signal_date" in snapshots.columns and not snapshots.empty:
        non_empty_dates = snapshots["signal_date"].dropna().astype(str).str.strip()
        non_empty_dates = non_empty_dates[non_empty_dates != ""]
        if not non_empty_dates.empty:
            required_end_date = str(sorted(non_empty_dates.tolist())[-1])

    series_map, series_errors = fetch_close_series(tickers, cfg, required_end_date=required_end_date)

    now_local = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
    rows: list[dict] = []
    for r in snapshots.itertuples(index=False):
        ticker = str(getattr(r, "ticker"))
        watch_type = str(getattr(r, "watch_type", ""))
        name = str(getattr(r, "name", ""))
        spec_risk_score, spec_risk_label, spec_risk_subtype, spec_risk_note = _spec_profile_from_snapshot_row(r)
        for h in horizons:
            close_series = series_map.get(ticker)
            status_detail = ""
            if close_series is None:
                ret_pct, out_close, reason, detail = None, None, "no_price_series", ""
                status_detail = series_errors.get(ticker, "")
            else:
                ret_pct, out_close, reason, detail = compute_forward_return_pct(close_series, str(getattr(r, "signal_date")), h)
                status_detail = detail
            market_heat, market_heat_reason = classify_market_heat(
                ret5_pct=getattr(r, "ret5_pct", None),
                ret20_pct=getattr(r, "ret20_pct", None),
                risk_score=getattr(r, "risk_score", None),
                volume_ratio20=getattr(r, "volume_ratio20", None),
            )
            rows.append(
                {
                    "evaluated_at": now_local,
                    "signal_date": str(getattr(r, "signal_date")),
                    "horizon_days": h,
                    "watch_type": watch_type,
                    "ticker": ticker,
                    "name": name,
                    "reco_status": str(getattr(r, "reco_status", "")),
                    "action": str(getattr(r, "action", "")),
                    "grade": str(getattr(r, "grade", "")),
                    "setup_score": getattr(r, "setup_score", None),
                    "risk_score": getattr(r, "risk_score", None),
                    "spec_risk_score": spec_risk_score,
                    "spec_risk_label": spec_risk_label,
                    "spec_risk_subtype": spec_risk_subtype,
                    "spec_risk_note": spec_risk_note,
                    "ret5_pct": getattr(r, "ret5_pct", None),
                    "ret20_pct": getattr(r, "ret20_pct", None),
                    "volume_ratio20": getattr(r, "volume_ratio20", None),
                    "signals": str(getattr(r, "signals", "")),
                    "scenario_label": str(getattr(r, "scenario_label", "")),
                    "market_heat": market_heat,
                    "market_heat_reason": market_heat_reason,
                    "out_close": out_close,
                    "realized_ret_pct": ret_pct,
                    "status": reason,
                    "status_detail": status_detail,
                }
            )

    out_df = pd.DataFrame(rows)
    if out_df.empty:
        print("No evaluation rows produced.")
        return 0

    # Upsert: keep existing OK rows; refresh non-ok rows when re-run (so 20D can become OK later).
    key_cols = ["signal_date", "horizon_days", "watch_type", "ticker"]
    existing = pd.DataFrame()
    if outcomes_csv.exists():
        try:
            existing = pd.read_csv(outcomes_csv)
        except Exception:
            existing = pd.DataFrame()
    existing_original = existing.copy()
    existing_deduped = False
    if not existing.empty:
        deduped_existing = dedupe_outcomes_by_key(existing)
        existing_deduped = len(deduped_existing) != len(existing)
        existing = deduped_existing

    if not existing.empty:
        for c in key_cols + ["status"]:
            if c not in existing.columns:
                existing[c] = ""
        existing_key_df = existing[key_cols].astype(str)
        existing_status = existing.get("status", pd.Series(dtype=str)).astype(str)
        existing_status_by_key = {
            tuple(k): str(s)
            for k, s in zip(existing_key_df.itertuples(index=False, name=None), existing_status.tolist(), strict=False)
        }

        out_keys = out_df[key_cols].astype(str)
        keep_rows: list[bool] = []
        for k in out_keys.itertuples(index=False, name=None):
            prev = existing_status_by_key.get(tuple(k))
            keep_rows.append(prev is None or prev != "ok")
        out_df = out_df[keep_rows].copy()

    if out_df.empty:
        refreshed = existing.copy()
        refreshed = enrich_market_heat_columns(refreshed)
        refreshed = enrich_scenario_label_columns(refreshed, snapshots=snapshots)
        refreshed = dedupe_outcomes_by_key(refreshed)
        if existing_deduped or not refreshed.equals(existing_original):
            refreshed.to_csv(outcomes_csv, index=False, encoding="utf-8")
            if existing_deduped:
                print(f"De-duplicated existing outcomes in {outcomes_csv} during re-run (no new outcome rows).")
            else:
                print(f"Refreshed metadata columns in {outcomes_csv} (no new outcome rows).")
            return 0
        print("No new outcome rows (already evaluated or already OK).")
        return 0

    outcomes_csv.parent.mkdir(parents=True, exist_ok=True)
    if existing.empty:
        final_df = out_df.copy()
        replaced = 0
    else:
        replace_keys = set(tuple(x) for x in out_df[key_cols].astype(str).itertuples(index=False, name=None))
        existing_keys = set(tuple(x) for x in existing[key_cols].astype(str).itertuples(index=False, name=None))
        replaced = len(replace_keys & existing_keys)
        keep_existing_mask = [
            tuple(k) not in replace_keys
            for k in existing[key_cols].astype(str).itertuples(index=False, name=None)
        ]
        keep_existing = existing[keep_existing_mask].copy()
        # Align columns before concat without creating all-NA warning-prone frames.
        shared_cols = list(dict.fromkeys(keep_existing.columns.tolist() + out_df.columns.tolist()))
        keep_existing = keep_existing.reindex(columns=shared_cols)
        out_df = out_df.reindex(columns=shared_cols)
        merged_records = keep_existing.to_dict(orient="records") + out_df.to_dict(orient="records")
        final_df = pd.DataFrame.from_records(merged_records, columns=shared_cols)

    final_df = enrich_market_heat_columns(final_df)
    final_df = enrich_scenario_label_columns(final_df, snapshots=snapshots)
    final_df = dedupe_outcomes_by_key(final_df)
    final_df.to_csv(outcomes_csv, index=False, encoding="utf-8")

    ok_rows = out_df[out_df["status"] == "ok"]
    action = "Upserted"
    print(f"{action} {len(out_df)} rows to {outcomes_csv} (ok={len(ok_rows)}, replaced={replaced}).")
    if not ok_rows.empty:
        by_type = ok_rows.groupby("watch_type")["realized_ret_pct"].mean().to_dict()
        print(f"Avg realized_ret_pct by watch_type: {by_type}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
