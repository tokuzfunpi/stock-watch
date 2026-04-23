from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from io import StringIO
from pathlib import Path

import pandas as pd
import yfinance as yf

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from daily_theme_watchlist import CONFIG, LOCAL_TZ, add_indicators, build_market_scenario
from verification.verify_recommendations import (
    build_verification_report_markdown,
    select_forced_recommendations,
    upsert_csv_with_existing_header,
    _maybe_date_from_rank,  # type: ignore[attr-defined]
)


@dataclass(frozen=True)
class BackfillItem:
    signal_date: str
    commit_sha: str


def run_git(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(REPO_ROOT),
        text=True,
        capture_output=True,
        check=False,
    )


def parse_git_log_dates(text: str) -> list[BackfillItem]:
    """
    Expects lines like:
      <sha> <YYYY-MM-DD>
    Returns one item per line (not yet de-duped).
    """
    items: list[BackfillItem] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        sha, date_str = parts[0], parts[1]
        if len(sha) < 7 or len(date_str) != 10:
            continue
        items.append(BackfillItem(signal_date=date_str, commit_sha=sha))
    return items


def list_daily_rank_commits(path: str) -> list[BackfillItem]:
    proc = run_git(["log", "--date=short", "--pretty=format:%H %ad", "--", path])
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "git log failed")
    # De-dupe to latest commit per day (log is newest first).
    out: list[BackfillItem] = []
    seen_dates: set[str] = set()
    for item in parse_git_log_dates(proc.stdout):
        if item.signal_date in seen_dates:
            continue
        seen_dates.add(item.signal_date)
        out.append(item)
    return out


def read_file_at_commit(path: str, commit_sha: str) -> str:
    proc = run_git(["show", f"{commit_sha}:{path}"])
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"git show failed for {commit_sha}:{path}")
    return proc.stdout


def _download_history_until(signal_date: str, ticker: str, *, lookback_days: int = 400) -> pd.DataFrame:
    target = pd.Timestamp(signal_date).tz_localize(None)
    start = (target - pd.Timedelta(days=lookback_days)).date().isoformat()
    end = (target + pd.Timedelta(days=2)).date().isoformat()
    df = yf.download(
        ticker,
        start=start,
        end=end,
        interval="1d",
        auto_adjust=True,
        progress=False,
        threads=False,
    )
    if df.empty:
        raise ValueError(f"No history returned for {ticker} up to {signal_date}")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.title)
    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna().copy()
    df.index = pd.to_datetime(df.index).tz_localize(None)
    df = df.loc[df.index <= target].copy()
    if df.empty:
        raise ValueError(f"No rows available for {ticker} on or before {signal_date}")
    return df


def build_market_regime_from_history(df_hist: pd.DataFrame) -> dict:
    if not CONFIG.market_filter.enabled:
        return {"enabled": False, "is_bullish": True, "comment": "大盤濾網關掉"}

    df = add_indicators(df_hist, CONFIG.market_filter.ma_period)
    x = df.iloc[-1]
    close_ = float(x["Close"])
    ma = float(x[f"MA{CONFIG.market_filter.ma_period}"])
    ret20 = float(x["Ret20D"]) if pd.notna(x["Ret20D"]) else 0.0
    vol_ratio = float(x["VolumeRatio20"]) if pd.notna(x["VolumeRatio20"]) else 1.0

    is_bullish = (
        close_ >= ma
        and ret20 >= CONFIG.market_filter.min_ret20
        and vol_ratio >= CONFIG.market_filter.volume_ratio_min
    )
    return {
        "enabled": True,
        "ticker": CONFIG.market_filter.ticker,
        "name": CONFIG.market_filter.name,
        "close": round(close_, 2),
        "ma": round(ma, 2),
        "ret20_pct": round(ret20 * 100, 2),
        "volume_ratio20": round(vol_ratio, 2),
        "is_bullish": bool(is_bullish),
        "comment": (
            f"{CONFIG.market_filter.name}目前"
            f"{'偏多' if is_bullish else '偏保守'}，"
            f"收在 {round(close_,2)}，"
            f"20日漲幅 {round(ret20*100,2)}%，"
            f"量比 {round(vol_ratio,2)}。"
        ),
    }


def build_us_market_reference_from_histories(histories: dict[str, pd.DataFrame]) -> dict:
    refs = [
        ("^GSPC", "S&P500"),
        ("^IXIC", "NASDAQ"),
        ("SOXX", "SOXX"),
        ("NVDA", "NVDA"),
    ]
    rows = []
    for ticker, name in refs:
        df_hist = histories.get(ticker)
        if df_hist is None or df_hist.empty:
            continue
        df = add_indicators(df_hist)
        x = df.iloc[-1]
        rows.append(
            {
                "ticker": ticker,
                "name": name,
                "ret1_pct": round(float(x["Ret1D"]) * 100, 2) if pd.notna(x["Ret1D"]) else 0.0,
                "ret5_pct": round(float(x["Ret5D"]) * 100, 2) if pd.notna(x["Ret5D"]) else 0.0,
                "close": round(float(x["Close"]), 2),
            }
        )

    if not rows:
        return {"summary": "美股參考暫時抓不到。", "rows": []}

    df_ref = pd.DataFrame(rows)
    avg_1d = round(float(df_ref["ret1_pct"].mean()), 2)
    avg_5d = round(float(df_ref["ret5_pct"].mean()), 2)
    if avg_1d >= 1:
        tone = "美股昨晚偏強，台股開盤情緒通常較正面。"
    elif avg_1d <= -1:
        tone = "美股昨晚偏弱，台股早盤要提防開高走低或續殺。"
    else:
        tone = "美股昨晚中性，台股仍以個股表現為主。"

    tech_bias = ""
    soxx_1d = float(df_ref.loc[df_ref["name"] == "SOXX", "ret1_pct"].iloc[0])
    nasdaq_1d = float(df_ref.loc[df_ref["name"] == "NASDAQ", "ret1_pct"].iloc[0])
    if soxx_1d <= -1.5 or nasdaq_1d <= -1.2:
        tech_bias = "美股科技偏弱，今天台股電子股先保守，不追開高。"
    elif soxx_1d >= 1.5 and nasdaq_1d >= 1.0:
        tech_bias = "美股科技偏強，台股電子股若量價配合可積極一點。"

    summary = (
        f"{tone} "
        f"S&P500 {df_ref.loc[df_ref['name']=='S&P500', 'ret1_pct'].iloc[0]:+.2f}% / "
        f"NASDAQ {df_ref.loc[df_ref['name']=='NASDAQ', 'ret1_pct'].iloc[0]:+.2f}% / "
        f"SOXX {df_ref.loc[df_ref['name']=='SOXX', 'ret1_pct'].iloc[0]:+.2f}% / "
        f"NVDA {df_ref.loc[df_ref['name']=='NVDA', 'ret1_pct'].iloc[0]:+.2f}% "
        f"(5日均值 {avg_5d:+.2f}%)"
    )
    return {"summary": summary, "tech_bias": tech_bias, "rows": rows}


def reconstruct_scenario_label(signal_date: str, df_rank: pd.DataFrame) -> str:
    tw_hist = _download_history_until(signal_date, CONFIG.market_filter.ticker)
    us_histories = {
        ticker: _download_history_until(signal_date, ticker)
        for ticker in ["^GSPC", "^IXIC", "SOXX", "NVDA"]
    }
    market_regime = build_market_regime_from_history(tw_hist)
    us_market = build_us_market_reference_from_histories(us_histories)
    return str(build_market_scenario(market_regime, us_market, df_rank).get("label", "") or "")


def append_snapshot_rows(
    df_rank: pd.DataFrame,
    *,
    generated_at: datetime,
    signal_date: str,
    source: str,
    source_sha: str,
    snapshot_csv: Path,
    scenario_label: str = "",
) -> int:
    short_forced = select_forced_recommendations(df_rank, watch_type="short", top_n=5).copy()
    midlong_forced = select_forced_recommendations(df_rank, watch_type="midlong", top_n=5).copy()
    if not short_forced.empty:
        short_forced["watch_type"] = "short"
    if not midlong_forced.empty:
        midlong_forced["watch_type"] = "midlong"

    combined = pd.concat([short_forced, midlong_forced], ignore_index=True)
    if combined.empty:
        return 0

    combined = combined.copy()
    combined["generated_at"] = generated_at.strftime("%Y-%m-%d %H:%M:%S %Z")
    combined["signal_date"] = signal_date
    combined["source"] = source
    combined["source_sha"] = source_sha
    if scenario_label:
        combined["scenario_label"] = scenario_label

    keep = [
        "generated_at",
        "signal_date",
        "source",
        "source_sha",
        "scenario_label",
        "watch_type",
        "rank",
        "ticker",
        "name",
        "grade",
        "setup_score",
        "risk_score",
        "ret5_pct",
        "ret20_pct",
        "volume_ratio20",
        "signals",
        "action",
        "reco_status",
    ]
    for col in keep:
        if col not in combined.columns:
            combined[col] = ""
    combined = combined[[c for c in keep if c in combined.columns]].copy()
    upsert_csv_with_existing_header(
        snapshot_csv,
        combined,
        key_cols=["signal_date", "watch_type", "ticker"],
    )
    return int(len(combined))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill verification snapshots from git history.")
    parser.add_argument("--path", default="theme_watchlist_daily/daily_rank.csv")
    parser.add_argument("--since", default="", help="YYYY-MM-DD (inclusive)")
    parser.add_argument("--until", default="", help="YYYY-MM-DD (inclusive)")
    parser.add_argument("--limit", type=int, default=30, help="Max number of days to backfill (0=unlimited)")
    parser.add_argument("--out-dir", default=str(Path("verification") / "watchlist_daily" / "backfill_reports"))
    parser.add_argument("--snapshot-csv", default=str(Path("verification") / "watchlist_daily" / "reco_snapshots.csv"))
    parser.add_argument("--no-snapshot", action="store_true", help="Do not append to reco_snapshots.csv")
    parser.add_argument(
        "--rebuild-snapshot",
        action="store_true",
        help="Overwrite reco_snapshots.csv from scratch (makes a .bak copy if file exists).",
    )
    parser.add_argument(
        "--reconstruct-scenario-label",
        action="store_true",
        help="Rebuild historical scenario_label from market data and current scenario rules.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    out_dir = Path(args.out_dir)
    snapshot_csv = Path(args.snapshot_csv)

    items = list_daily_rank_commits(str(args.path))
    if args.since:
        items = [x for x in items if x.signal_date >= args.since]
    if args.until:
        items = [x for x in items if x.signal_date <= args.until]
    limit = int(args.limit)
    if limit > 0:
        items = items[:limit]

    if not items:
        print("No backfill items.")
        return 0

    now_local = datetime.now(LOCAL_TZ)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not args.no_snapshot and args.rebuild_snapshot:
        if snapshot_csv.exists():
            bak = snapshot_csv.with_suffix(snapshot_csv.suffix + f".bak.{now_local.strftime('%Y%m%d_%H%M%S')}")
            snapshot_csv.replace(bak)
            print(f"Backed up snapshot CSV to: {bak}")
        snapshot_csv.parent.mkdir(parents=True, exist_ok=True)

    total_reports = 0
    total_snapshots = 0
    for item in items:
        try:
            content = read_file_at_commit(str(args.path), item.commit_sha)
            df_rank = pd.read_csv(StringIO(content))
            signal_date = _maybe_date_from_rank(df_rank) or item.signal_date
            source = f"git:{item.commit_sha}:{args.path}"
            scenario_label = ""
            if args.reconstruct_scenario_label:
                try:
                    scenario_label = reconstruct_scenario_label(signal_date, df_rank)
                except Exception as exc:
                    print(f"WARN {signal_date} scenario reconstruct failed: {exc}")
            report = build_verification_report_markdown(df_rank, source=source, now_local=now_local)
            report_path = out_dir / f"verification_report_{signal_date}.md"
            report_path.write_text(report, encoding="utf-8")
            total_reports += 1

            if not args.no_snapshot:
                total_snapshots += append_snapshot_rows(
                    df_rank,
                    generated_at=now_local,
                    signal_date=signal_date,
                    source=source,
                    source_sha=item.commit_sha,
                    snapshot_csv=snapshot_csv,
                    scenario_label=scenario_label,
                )
        except Exception as exc:
            print(f"SKIP {item.signal_date} {item.commit_sha[:8]}: {exc}")
            continue

    print(f"Backfill done: reports={total_reports} snapshot_rows_appended={total_snapshots}")
    print(f"Reports dir: {out_dir}")
    if not args.no_snapshot:
        print(f"Snapshot CSV: {snapshot_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
