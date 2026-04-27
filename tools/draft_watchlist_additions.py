from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import pandas as pd
import yfinance as yf

from daily_theme_watchlist import CONFIG
from daily_theme_watchlist import LOCAL_TZ
from stock_watch.cli.weekly_review import _derive_candidate_source
from stock_watch.paths import REPO_ROOT
from stock_watch.paths import THEME_OUTDIR
from stock_watch.signals.detect import add_indicators
from stock_watch.signals.detect import detect_row
from tools.augment_low_price_watchlist import fetch_quotes_from_mis
from tools.augment_low_price_watchlist import fetch_tw_universe_codes

WATCHLIST_CSV = REPO_ROOT / "watchlist.csv"
OUT_MD = THEME_OUTDIR / "watchlist_addition_draft.md"
OUT_JSON = THEME_OUTDIR / "watchlist_addition_draft.json"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Draft watchlist additions from a best-effort TW universe scan.")
    parser.add_argument("--watchlist-csv", default=str(WATCHLIST_CSV))
    parser.add_argument("--out", default=str(OUT_MD))
    parser.add_argument("--json-out", default=str(OUT_JSON))
    parser.add_argument("--top-quote-count", type=int, default=60)
    parser.add_argument("--min-quote-volume", type=int, default=1200)
    parser.add_argument("--min-price", type=float, default=20.0)
    parser.add_argument("--history-period", default="9mo")
    return parser.parse_args(argv)


def _markdown_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "_None_\n"
    headers = [str(col) for col in df.columns.tolist()]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for _, row in df.iterrows():
        values: list[str] = []
        for col in headers:
            value = row.get(col)
            text = "" if pd.isna(value) else str(value)
            values.append(text.replace("|", "\\|").replace("\n", " "))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines) + "\n"


def load_existing_tickers(watchlist_csv: Path) -> set[str]:
    if not watchlist_csv.exists():
        return set()
    try:
        watchlist = pd.read_csv(watchlist_csv)
    except Exception:
        return set()
    if "ticker" not in watchlist.columns:
        return set()
    return set(watchlist["ticker"].dropna().astype(str))


def fetch_candidate_quotes(existing_tickers: set[str], *, min_quote_volume: int, min_price: float, top_quote_count: int) -> pd.DataFrame:
    listed, otc = fetch_tw_universe_codes()
    quotes = list(fetch_quotes_from_mis(listed, prefix="tse").values()) + list(fetch_quotes_from_mis(otc, prefix="otc").values())
    rows: list[dict[str, object]] = []
    for quote in quotes:
        ticker = str(quote.ticker)
        base = ticker.split(".")[0]
        if ticker in existing_tickers:
            continue
        if base.startswith("00"):
            continue
        if quote.volume < min_quote_volume:
            continue
        if quote.price <= min_price:
            continue
        rows.append(
            {
                "ticker": ticker,
                "name": quote.name,
                "quote_price": float(quote.price),
                "quote_volume": int(quote.volume),
            }
        )
    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(rows)
    return out.sort_values(by=["quote_volume", "quote_price"], ascending=[False, False]).head(top_quote_count).reset_index(drop=True)


def score_candidate_universe(candidate_quotes: pd.DataFrame, *, history_period: str) -> pd.DataFrame:
    if candidate_quotes.empty:
        return pd.DataFrame()

    scored_rows: list[dict[str, object]] = []
    group_pairs = [
        ("theme", "short_attack"),
        ("satellite", "midlong_core"),
        ("core", "midlong_core"),
    ]
    for _, quote_row in candidate_quotes.iterrows():
        ticker = str(quote_row["ticker"])
        try:
            history = yf.download(
                ticker,
                period=history_period,
                interval="1d",
                auto_adjust=False,
                progress=False,
                threads=False,
            )
        except Exception:
            continue
        if history is None or len(history) < 120:
            continue
        if isinstance(history.columns, pd.MultiIndex):
            history.columns = history.columns.get_level_values(0)
        required = ["Open", "High", "Low", "Close", "Volume"]
        if any(col not in history.columns for col in required):
            continue
        history = history[required].dropna().copy()
        if len(history) < 120:
            continue
        enriched = add_indicators(history)
        for group, layer in group_pairs:
            try:
                row = detect_row(
                    enriched,
                    ticker=ticker,
                    name=str(quote_row["name"]),
                    group=group,
                    layer=layer,
                    strat=CONFIG.strategy,
                    group_weights=CONFIG.group_weights,
                )
            except Exception:
                continue
            row["candidate_source"] = _derive_candidate_source(pd.Series(row))
            row["scan_group"] = group
            row["scan_layer"] = layer
            row["quote_price"] = float(quote_row["quote_price"])
            row["quote_volume"] = int(quote_row["quote_volume"])
            scored_rows.append(row)
    return pd.DataFrame(scored_rows)


def _pick_candidates(scored: pd.DataFrame, *, group: str, source: str, limit: int, observation_only: bool = False) -> pd.DataFrame:
    if scored.empty:
        return pd.DataFrame()
    work = scored[(scored["scan_group"] == group) & (scored["candidate_source"] == source)].copy()
    if work.empty:
        return work

    if source == "Satellite high-beta leaders":
        work = work[(work["setup_score"] >= 10) & (work["ret20_pct"] >= 20)].copy()
        work = work.sort_values(
            by=["spec_risk_score", "risk_score", "ret20_pct", "volume_ratio20", "quote_volume"],
            ascending=[True, True, False, False, False],
        )
    elif source == "Theme trend acceleration":
        work = work[(work["setup_score"] >= 8) & (work["ret20_pct"] >= 10)].copy()
        work = work.sort_values(
            by=["setup_score", "spec_risk_score", "ret20_pct", "volume_ratio20", "quote_volume"],
            ascending=[False, True, False, False, False],
        )
    elif source == "Core trend compounders":
        work = work[(work["setup_score"] >= 8) & (work["risk_score"] <= 4) & (work["spec_risk_score"] <= 5)].copy()
        work = work.sort_values(
            by=["spec_risk_score", "risk_score", "setup_score", "ret20_pct", "quote_volume"],
            ascending=[True, True, False, False, False],
        )
    elif source == "Theme momentum burst":
        work = work[(work["setup_score"] >= 10)].copy()
        work = work.sort_values(
            by=["spec_risk_score", "ret5_pct", "volume_ratio20", "quote_volume"],
            ascending=[True, False, False, False],
        )
    else:
        work = work.sort_values(
            by=["setup_score", "ret20_pct", "quote_volume"],
            ascending=[False, False, False],
        )

    if work.empty:
        return work
    if observation_only:
        work["proposal_status"] = "reserve"
    else:
        work["proposal_status"] = "proposed"
    return work.head(limit).copy()


def build_addition_draft(scored: pd.DataFrame) -> dict[str, list[dict[str, object]]]:
    sections = {
        "satellite_add": _pick_candidates(scored, group="satellite", source="Satellite high-beta leaders", limit=3),
        "theme_add": _pick_candidates(scored, group="theme", source="Theme trend acceleration", limit=3),
        "core_add": _pick_candidates(scored, group="core", source="Core trend compounders", limit=1),
        "theme_reserve": _pick_candidates(scored, group="theme", source="Theme momentum burst", limit=3, observation_only=True),
    }
    output: dict[str, list[dict[str, object]]] = {}
    selected_tickers: set[str] = set()
    display_cols = [
        "ticker",
        "name",
        "candidate_source",
        "proposal_status",
        "ret5_pct",
        "ret20_pct",
        "volume_ratio20",
        "setup_score",
        "risk_score",
        "spec_risk_score",
        "spec_risk_label",
        "signals",
        "regime",
        "quote_price",
        "quote_volume",
    ]
    for key, df in sections.items():
        if df.empty:
            output[key] = []
            continue
        df = df[~df["ticker"].astype(str).isin(selected_tickers)].copy()
        selected_tickers.update(df["ticker"].astype(str))
        output[key] = df[[col for col in display_cols if col in df.columns]].to_dict(orient="records")
    return output


def render_markdown(payload: dict[str, object]) -> str:
    summary = payload.get("summary", {})
    sections = payload.get("sections", {})
    lines = [
        "# Watchlist Addition Draft",
        f"- Generated: {payload.get('generated_at', '')}",
        f"- Existing watchlist count: `{summary.get('existing_watchlist_count', 0)}`",
        f"- Candidate quote pool: `{summary.get('candidate_quote_count', 0)}`",
        f"- Scored candidate rows: `{summary.get('scored_rows', 0)}`",
        "",
        "## Notes",
        "",
        "- This is a best-effort draft built from current TW quote activity plus the repo's existing signal/spec-risk logic.",
        "- `satellite/theme/core` sections are proposed adds; `theme reserve` is observation-only and should not be auto-added blindly.",
        "",
        "## Proposed Satellite Adds",
        _markdown_table(pd.DataFrame(sections.get("satellite_add", []))).rstrip(),
        "",
        "## Proposed Theme Adds",
        _markdown_table(pd.DataFrame(sections.get("theme_add", []))).rstrip(),
        "",
        "## Proposed Core Add",
        _markdown_table(pd.DataFrame(sections.get("core_add", []))).rstrip(),
        "",
        "## Theme Reserve Only",
        _markdown_table(pd.DataFrame(sections.get("theme_reserve", []))).rstrip(),
        "",
    ]
    return "\n".join(lines) + "\n"


def write_outputs(payload: dict[str, object], *, out: Path, json_out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    json_out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_markdown(payload), encoding="utf-8")
    json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    existing_tickers = load_existing_tickers(Path(args.watchlist_csv))
    candidate_quotes = fetch_candidate_quotes(
        existing_tickers,
        min_quote_volume=int(args.min_quote_volume),
        min_price=float(args.min_price),
        top_quote_count=int(args.top_quote_count),
    )
    scored = score_candidate_universe(candidate_quotes, history_period=str(args.history_period))
    payload = {
        "generated_at": datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S %Z"),
        "summary": {
            "existing_watchlist_count": len(existing_tickers),
            "candidate_quote_count": int(len(candidate_quotes)),
            "scored_rows": int(len(scored)),
        },
        "sections": build_addition_draft(scored),
    }
    write_outputs(payload, out=Path(args.out), json_out=Path(args.json_out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
