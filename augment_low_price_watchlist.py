from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from daily_theme_watchlist import HTTP, HTTP_TIMEOUT, normalize_ticker_symbol


@dataclass(frozen=True)
class Quote:
    ticker: str
    name: str
    price: float
    volume: int


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Augment watchlist.csv with low-price TW stocks (best effort).")
    p.add_argument("--watchlist-csv", default="watchlist.csv")
    p.add_argument("--out", default="", help="Write updated CSV to this path (default: in-place).")
    p.add_argument("--max-new", type=int, default=20)
    p.add_argument("--price-max", type=float, default=200.0)
    p.add_argument("--min-volume", type=int, default=1000, help="Minimum volume filter (best effort).")
    p.add_argument("--group", default="satellite")
    p.add_argument("--layer", default="short_attack")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args(argv)


def _safe_float(v: str) -> float | None:
    s = str(v or "").strip().replace(",", "")
    if not s or s in {"-", "—"}:
        return None
    try:
        return float(s)
    except Exception:
        return None


def _safe_int(v: str) -> int:
    s = str(v or "").strip().replace(",", "")
    if not s or s in {"-", "—"}:
        return 0
    try:
        return int(float(s))
    except Exception:
        return 0


def fetch_isin_codes(mode: int) -> list[str]:
    url = "https://isin.twse.com.tw/isin/C_public.jsp"
    try:
        resp = HTTP.get(url, params={"strMode": str(mode)}, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
    except Exception:
        return []

    try:
        tables = pd.read_html(resp.text)
    except Exception:
        return []

    if not tables:
        return []

    df = tables[0].copy()
    if df.empty:
        return []

    first_col = df.columns[0]
    values = df[first_col].dropna().astype(str).tolist()

    out: list[str] = []
    for raw in values:
        token = raw.strip().split()[0] if raw.strip() else ""
        token = token.strip()
        if len(token) == 4 and token.isdigit():
            out.append(token)
    return out


def fetch_tw_universe_codes() -> tuple[list[str], list[str]]:
    # Best-effort: TWSE listed + TPEx OTC via ISIN "C_public.jsp".
    listed = fetch_isin_codes(2)
    otc = fetch_isin_codes(4)
    return listed, otc


def fetch_quotes_from_mis(codes: list[str], *, prefix: str) -> dict[str, Quote]:
    # prefix: "tse" or "otc"
    if not codes:
        return {}
    out: dict[str, Quote] = {}

    def chunked(items: list[str], n: int) -> list[list[str]]:
        return [items[i : i + n] for i in range(0, len(items), n)]

    for chunk in chunked(codes, 50):
        channels = [f"{prefix}_{c}.tw" for c in chunk]
        try:
            resp = HTTP.get(
                "https://mis.twse.com.tw/stock/api/getStockInfo.jsp",
                params={"ex_ch": "|".join(channels), "json": "1", "delay": "0"},
                timeout=HTTP_TIMEOUT,
            )
            resp.raise_for_status()
            payload = resp.json()
        except Exception:
            continue

        for item in payload.get("msgArray", []) or []:
            code = str(item.get("c", "")).strip()
            if not code or code not in chunk:
                continue
            name = str(item.get("n", "")).strip() or code
            price = _safe_float(str(item.get("z", "")))
            volume = _safe_int(str(item.get("v", "")))
            if price is None:
                continue
            suffix = "TW" if prefix == "tse" else "TWO"
            ticker = normalize_ticker_symbol(f"{code}.{suffix}")
            out[code] = Quote(ticker=ticker, name=name, price=float(price), volume=int(volume))
    return out


def load_watchlist_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return [dict(r) for r in reader]


def write_watchlist_rows(path: Path, rows: list[dict[str, str]]) -> None:
    header = ["ticker", "name", "group", "layer", "enabled"]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header)
        w.writeheader()
        for r in rows:
            w.writerow({k: (r.get(k) or "") for k in header})


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    watchlist_path = Path(args.watchlist_csv)
    out_path = Path(args.out) if str(args.out).strip() else watchlist_path

    if not watchlist_path.exists():
        print(f"Missing watchlist csv: {watchlist_path}")
        return 1

    rows = load_watchlist_rows(watchlist_path)
    existing = {normalize_ticker_symbol(r.get("ticker", "")) for r in rows if r.get("ticker")}

    listed, otc = fetch_tw_universe_codes()
    if not listed and not otc:
        print("Failed to fetch TW universe list (best effort). No changes made.")
        return 0

    q_listed = fetch_quotes_from_mis(listed, prefix="tse")
    q_otc = fetch_quotes_from_mis(otc, prefix="otc")
    merged = list(q_listed.values()) + list(q_otc.values())

    candidates = [
        q
        for q in merged
        if q.ticker not in existing
        and 0 < q.price <= float(args.price_max)
        and q.volume >= int(args.min_volume)
        and not q.ticker.startswith("00")  # exclude ETFs
    ]
    candidates.sort(key=lambda q: (q.volume, -q.price), reverse=True)
    chosen = candidates[: max(int(args.max_new), 0)]

    if not chosen:
        print("No new low-price candidates matched filters. No changes made.")
        return 0

    new_rows: list[dict[str, str]] = []
    for q in chosen:
        new_rows.append(
            {
                "ticker": q.ticker,
                "name": q.name,
                "group": str(args.group),
                "layer": str(args.layer),
                "enabled": "true",
            }
        )

    print(f"Selected {len(new_rows)} new tickers (price<= {args.price_max}, vol>= {args.min_volume}).")
    for r in new_rows[:20]:
        print(f"- {r['ticker']} {r['name']}")

    if args.dry_run:
        print("Dry run; not writing watchlist.")
        return 0

    rows.extend(new_rows)
    write_watchlist_rows(out_path, rows)
    print(f"Updated: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

