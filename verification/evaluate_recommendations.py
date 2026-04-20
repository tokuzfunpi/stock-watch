from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime
import sys
from pathlib import Path

import pandas as pd
import yfinance as yf

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from daily_theme_watchlist import LOCAL_TZ, logger


@dataclass(frozen=True)
class EvalConfig:
    period: str = "60d"


def compute_forward_return_pct(
    close_series: pd.Series,
    signal_date: str,
    horizon_days: int,
) -> tuple[float | None, float | None, str]:
    if close_series is None or close_series.empty:
        return None, None, "empty_series"
    if horizon_days < 1:
        return None, None, "invalid_horizon"

    series = close_series.dropna().copy()
    series.index = pd.to_datetime(series.index).tz_localize(None)

    target_date = pd.to_datetime(signal_date).tz_localize(None)
    if target_date not in series.index:
        return None, None, "signal_date_missing"

    idx = series.index.get_loc(target_date)
    if isinstance(idx, slice):
        idx = idx.start
    if not isinstance(idx, int):
        return None, None, "signal_date_ambiguous"

    entry = float(series.iloc[idx])
    out_idx = idx + horizon_days
    if out_idx >= len(series):
        return None, None, "insufficient_forward_data"
    out_close = float(series.iloc[out_idx])
    ret_pct = (out_close / entry - 1.0) * 100.0
    return ret_pct, out_close, "ok"


def fetch_close_series(tickers: list[str], cfg: EvalConfig) -> dict[str, pd.Series]:
    uniq = [str(t).strip() for t in tickers if str(t).strip()]
    seen: set[str] = set()
    uniq = [t for t in uniq if not (t in seen or seen.add(t))]
    if not uniq:
        return {}

    try:
        df = yf.download(
            " ".join(uniq),
            period=cfg.period,
            interval="1d",
            auto_adjust=True,
            progress=False,
            threads=False,
            group_by="ticker",
        )
    except Exception as exc:
        logger.info("Outcome price fetch failed (best effort): %s", exc)
        return {}

    if df is None or getattr(df, "empty", True):
        return {}

    out: dict[str, pd.Series] = {}
    if isinstance(df.columns, pd.MultiIndex):
        for ticker in uniq:
            try:
                if ticker not in df.columns.get_level_values(0):
                    continue
                sub = df[ticker]
                if "Close" not in sub.columns:
                    continue
                out[ticker] = sub["Close"].copy()
            except Exception:
                continue
    else:
        if "Close" in df.columns and len(uniq) == 1:
            out[uniq[0]] = df["Close"].copy()
    return out


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate recommendation snapshots vs future closes (best effort).")
    out_dir = Path("verification") / "watchlist_daily"
    parser.add_argument("--snapshot-csv", default=str(out_dir / "reco_snapshots.csv"))
    parser.add_argument("--outcomes-csv", default=str(out_dir / "reco_outcomes.csv"))
    parser.add_argument("--signal-date", default="", help="Evaluate snapshots for this signal date (YYYY-MM-DD).")
    parser.add_argument("--horizons", default="1,5,20", help="Comma-separated horizons in trading days.")
    parser.add_argument("--period", default="90d", help="yfinance period to fetch (e.g. 60d, 6mo).")
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
        return pd.read_csv(path)
    except pd.errors.ParserError:
        _normalize_snapshot_csv_inplace(path)
        return pd.read_csv(path)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    snapshot_csv = Path(args.snapshot_csv)
    outcomes_csv = Path(args.outcomes_csv)
    cfg = EvalConfig(period=str(args.period))

    if not snapshot_csv.exists():
        print(f"No snapshot file: {snapshot_csv}")
        return 0

    snapshots = load_snapshots_csv(snapshot_csv)
    if snapshots.empty:
        print("Snapshot file is empty.")
        return 0

    horizons = [int(x.strip()) for x in str(args.horizons).split(",") if x.strip()]
    horizons = sorted({h for h in horizons if h >= 1})
    if not horizons:
        print("No valid horizons.")
        return 0

    signal_date = str(args.signal_date).strip()
    if not signal_date:
        non_empty = snapshots["signal_date"].dropna().astype(str)
        signal_date = non_empty.iloc[-1] if not non_empty.empty else ""
    if signal_date:
        snapshots = snapshots[snapshots["signal_date"].astype(str) == signal_date].copy()

    if snapshots.empty:
        print("No snapshots matched signal date.")
        return 0

    tickers = snapshots["ticker"].dropna().astype(str).tolist()
    series_map = fetch_close_series(tickers, cfg)

    now_local = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
    rows: list[dict] = []
    for r in snapshots.itertuples(index=False):
        ticker = str(getattr(r, "ticker"))
        watch_type = str(getattr(r, "watch_type", ""))
        name = str(getattr(r, "name", ""))
        for h in horizons:
            close_series = series_map.get(ticker)
            ret_pct, out_close, reason = compute_forward_return_pct(close_series, str(getattr(r, "signal_date")), h) if close_series is not None else (None, None, "no_price_series")
            rows.append(
                {
                    "evaluated_at": now_local,
                    "signal_date": str(getattr(r, "signal_date")),
                    "horizon_days": h,
                    "watch_type": watch_type,
                    "ticker": ticker,
                    "name": name,
                    "action": str(getattr(r, "action", "")),
                    "grade": str(getattr(r, "grade", "")),
                    "setup_score": getattr(r, "setup_score", None),
                    "risk_score": getattr(r, "risk_score", None),
                    "ret5_pct": getattr(r, "ret5_pct", None),
                    "ret20_pct": getattr(r, "ret20_pct", None),
                    "volume_ratio20": getattr(r, "volume_ratio20", None),
                    "signals": str(getattr(r, "signals", "")),
                    "out_close": out_close,
                    "realized_ret_pct": ret_pct,
                    "status": reason,
                }
            )

    out_df = pd.DataFrame(rows)
    if out_df.empty:
        print("No evaluation rows produced.")
        return 0

    # De-dupe when appending.
    if outcomes_csv.exists():
        try:
            existing = pd.read_csv(outcomes_csv)
        except Exception:
            existing = pd.DataFrame()
        if not existing.empty:
            key_cols = ["signal_date", "horizon_days", "watch_type", "ticker"]
            existing_keys = set(tuple(x) for x in existing[key_cols].astype(str).itertuples(index=False, name=None))
            out_df_keys = out_df[key_cols].astype(str)
            keep_mask = [
                tuple(row) not in existing_keys
                for row in out_df_keys.itertuples(index=False, name=None)
            ]
            out_df = out_df[keep_mask].copy()

    if out_df.empty:
        print("No new outcome rows (already evaluated).")
        return 0

    outcomes_csv.parent.mkdir(parents=True, exist_ok=True)
    write_header = not outcomes_csv.exists()
    with outcomes_csv.open("a", encoding="utf-8", newline="") as f:
        out_df.to_csv(f, index=False, header=write_header)

    ok_rows = out_df[out_df["status"] == "ok"]
    print(f"Appended {len(out_df)} rows to {outcomes_csv} (ok={len(ok_rows)}).")
    if not ok_rows.empty:
        by_type = ok_rows.groupby("watch_type")["realized_ret_pct"].mean().to_dict()
        print(f"Avg realized_ret_pct by watch_type: {by_type}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
