from __future__ import annotations

import argparse
import csv
import contextlib
import hashlib
import io
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import yfinance as yf
from requests.adapters import HTTPAdapter
from stock_watch.data.providers.finmind import FinMindPriceProvider
from stock_watch.data.providers.yahoo import (
    YahooFinancePriceProvider,
    alternate_taiwan_ticker as alternate_taiwan_ticker_impl,
)
from stock_watch.backtesting.core import (
    run_backtest_dual as run_backtest_dual_impl,
    summarize_events as summarize_events_impl,
)
from stock_watch.ranking.scoring import build_rank_table, enrich_rank_changes as enrich_rank_changes_impl
from stock_watch.reports.common import dataframe_to_html as dataframe_to_html_impl
from stock_watch.reports.daily import (
    build_daily_report_html as build_daily_report_html_impl,
    build_daily_report_markdown as build_daily_report_markdown_impl,
    save_reports as save_reports_impl,
)
from stock_watch.reports.portfolio import (
    build_portfolio_report_html as build_portfolio_report_html_impl,
    build_portfolio_report_markdown as build_portfolio_report_markdown_impl,
    save_portfolio_reports as save_portfolio_reports_impl,
)
from stock_watch.state.alert_tracking import upsert_alert_tracking as upsert_alert_tracking_impl
from stock_watch.signals.detect import (
    add_indicators as add_indicators_impl,
    apply_group_weight as apply_group_weight_impl,
    build_speculative_risk_profile as build_speculative_risk_profile_impl,
    detect_row as detect_row_impl,
    grade_signal as grade_signal_impl,
    score_band as score_band_impl,
    speculative_risk_label as speculative_risk_label_impl,
    speculative_risk_score as speculative_risk_score_impl,
    volatility_label as volatility_label_impl,
)
from urllib3.util.retry import Retry

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = Path(os.getenv("CONFIG_PATH", BASE_DIR / "config.json"))
WATCHLIST_CSV = Path(os.getenv("WATCHLIST_CSV", BASE_DIR / "watchlist.csv"))
PORTFOLIO_CSV = Path(os.getenv("PORTFOLIO_CSV", BASE_DIR / "portfolio.csv"))
CHAT_IDS_PATH = Path(os.getenv("CHAT_IDS_PATH", BASE_DIR / "chat_ids"))
OUTDIR = Path(os.getenv("OUTDIR", BASE_DIR / "theme_watchlist_daily"))
OUTDIR.mkdir(parents=True, exist_ok=True)

YF_CACHE_DIR = OUTDIR / ".yfinance_cache"
YF_CACHE_DIR.mkdir(parents=True, exist_ok=True)
try:
    yf.cache.set_cache_location(str(YF_CACHE_DIR))
    yf.set_tz_cache_location(str(YF_CACHE_DIR))
except Exception:
    pass

RANK_CSV = OUTDIR / "daily_rank.csv"
STATE_FILE = OUTDIR / "last_rank_state.txt"
PREV_RANK_CSV = OUTDIR / "prev_daily_rank.csv"
REPORT_MD = OUTDIR / "daily_report.md"
REPORT_HTML = OUTDIR / "daily_report.html"
PORTFOLIO_REPORT_MD = OUTDIR / "portfolio_report.md"
PORTFOLIO_REPORT_HTML = OUTDIR / "portfolio_report.html"
RUNTIME_METRICS_MD = OUTDIR / "runtime_metrics.md"
RUNTIME_METRICS_JSON = OUTDIR / "runtime_metrics.json"
ALERT_TRACK_CSV = OUTDIR / "alert_tracking.csv"
FEEDBACK_SUMMARY_CSV = OUTDIR / "feedback_summary.csv"
SUCCESS_FILE = OUTDIR / "last_success_date.txt"
VERIFICATION_OUTCOMES_CSV = BASE_DIR / "verification" / "watchlist_daily" / "reco_outcomes.csv"
LOG_DIR = OUTDIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
FINMIND_TOKEN = os.getenv("FINMIND_TOKEN", "").strip()
HTTP_TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "20"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
FORCE_RUN = os.getenv("FORCE_RUN", "").strip().lower() in {"1", "true", "yes", "y"}
LOCAL_TZ = ZoneInfo(os.getenv("LOCAL_TZ", "Asia/Taipei"))
STOCK_DATA_PROVIDER = os.getenv("STOCK_DATA_PROVIDER", "yahoo").strip().lower()
STOCK_DATA_FALLBACKS = [
    item.strip().lower()
    for item in os.getenv("STOCK_DATA_FALLBACKS", "finmind").split(",")
    if item.strip()
]
TWSE_NAME_CACHE: dict[str, str] = {}
REALTIME_QUOTE_INTERVAL = os.getenv("REALTIME_QUOTE_INTERVAL", "1m").strip()
REALTIME_QUOTE_PERIOD = os.getenv("REALTIME_QUOTE_PERIOD", "1d").strip()
ENABLE_HISTORY_CACHE = os.getenv("ENABLE_HISTORY_CACHE", "1").strip().lower() in {"1", "true", "yes", "y"}
ENABLE_DISK_HISTORY_CACHE = os.getenv("ENABLE_DISK_HISTORY_CACHE", "1").strip().lower() in {"1", "true", "yes", "y"}
HISTORY_CACHE_DIR = OUTDIR / "history_cache"
HISTORY_CACHE_DIR.mkdir(parents=True, exist_ok=True)
US_MARKET_TZ = ZoneInfo("America/New_York")


def realtime_quotes_enabled() -> bool:
    return os.getenv("REALTIME_QUOTES", "1").strip().lower() in {"1", "true", "yes", "y"}


_REALTIME_QUOTE_CACHE: dict[tuple[str, str, tuple[str, ...]], tuple[float, dict[str, float]]] = {}
_DAILY_OHLCV_CACHE: dict[tuple[str, str], pd.DataFrame] = {}
_INDICATOR_FRAME_CACHE: dict[tuple[str, str, int], pd.DataFrame] = {}
_CACHE_STATS = {
    "history_hit": 0,
    "history_disk_hit": 0,
    "history_superset_hit": 0,
    "history_miss": 0,
    "indicator_hit": 0,
    "indicator_superset_hit": 0,
    "indicator_miss": 0,
}


def parse_chat_ids(raw: str) -> list[int]:
    tokens = re.split(r"[\s,]+", str(raw or "").strip())
    chat_ids: list[int] = []
    for token in tokens:
        if not token:
            continue
        chat_ids.append(int(token))
    return chat_ids


def load_telegram_chat_ids(chat_ids_path: Path) -> list[int]:
    env_value = os.getenv("TELEGRAM_CHAT_IDS", "").strip()
    if env_value:
        return parse_chat_ids(env_value)
    if not chat_ids_path.exists():
        return []
    return parse_chat_ids(chat_ids_path.read_text(encoding="utf-8-sig"))


TELEGRAM_CHAT_IDS = load_telegram_chat_ids(CHAT_IDS_PATH)


@dataclass
class MarketFilter:
    enabled: bool
    ticker: str
    name: str
    ma_period: int
    min_ret20: float
    volume_ratio_min: float
    allow_a_grade_even_if_weak: bool


@dataclass
class NotificationRule:
    top_n_short: int
    top_n_midlong: int
    min_setup_score: int
    max_risk_score: int
    min_ret20_pct: float
    min_ret5_pct: float
    min_volume_ratio: float
    priority_groups: List[str]


@dataclass
class BacktestConfig:
    enabled: bool
    period: str
    lookahead_days: List[int]


@dataclass
class GroupWeights:
    theme_bonus: int
    core_penalty: int
    etf_penalty: int


@dataclass
class StrategyConfig:
    base_low250_mult: float
    base_range20_max: float
    rebreak_vol_ratio: float
    surge_ret20: float
    surge_vol_ratio: float
    trend_ret20: float
    accel_ret5: float
    accel_ret10: float
    accel_vol_ratio_fast: float
    accel_vol_ratio_slow: float


@dataclass
class ScenarioPolicy:
    correction_short_top_n: int
    heat_bias_short_top_n: int
    correction_midlong_top_n: int
    min_correction_ok_samples: int
    new_watch_spotlight_limit: int


@dataclass
class AppConfig:
    yf_period: str
    state_enabled: bool
    always_notify: bool
    max_message_length: int
    watchlist_default_group: str
    market_filter: MarketFilter
    notify: NotificationRule
    backtest: BacktestConfig
    group_weights: GroupWeights
    strategy: StrategyConfig
    scenario_policy: ScenarioPolicy


def load_config(path: Path) -> AppConfig:
    raw = json.loads(path.read_text(encoding="utf-8"))
    notify_raw = raw["notify"]
    strat_raw = raw.get("strategy", {})
    scenario_policy_raw = raw.get("scenario_policy", {})
    return AppConfig(
        yf_period=raw.get("yf_period", "3y"),
        state_enabled=bool(raw.get("state_enabled", True)),
        always_notify=bool(raw.get("always_notify", False)),
        max_message_length=int(raw.get("max_message_length", 3500)),
        watchlist_default_group=raw.get("watchlist_default_group", "theme"),
        market_filter=MarketFilter(**raw["market_filter"]),
        notify=NotificationRule(
            top_n_short=int(notify_raw.get("top_n_short", notify_raw.get("top_n", 5))),
            top_n_midlong=int(notify_raw.get("top_n_midlong", notify_raw.get("top_n", 5))),
            min_setup_score=int(notify_raw.get("min_setup_score", 4)),
            max_risk_score=int(notify_raw.get("max_risk_score", 4)),
            min_ret20_pct=float(notify_raw.get("min_ret20_pct", 3.0)),
            min_ret5_pct=float(notify_raw.get("min_ret5_pct", 5.0)),
            min_volume_ratio=float(notify_raw.get("min_volume_ratio", 1.3)),
            priority_groups=list(notify_raw.get("priority_groups", [])),
        ),
        backtest=BacktestConfig(**raw["backtest"]),
        group_weights=GroupWeights(**raw["group_weights"]),
        strategy=StrategyConfig(
            base_low250_mult=float(strat_raw.get("base_low250_mult", 1.20)),
            base_range20_max=float(strat_raw.get("base_range20_max", 0.15)),
            rebreak_vol_ratio=float(strat_raw.get("rebreak_vol_ratio", 1.35)),
            surge_ret20=float(strat_raw.get("surge_ret20", 0.22)),
            surge_vol_ratio=float(strat_raw.get("surge_vol_ratio", 1.55)),
            trend_ret20=float(strat_raw.get("trend_ret20", 0.08)),
            accel_ret5=float(strat_raw.get("accel_ret5", 0.08)),
            accel_ret10=float(strat_raw.get("accel_ret10", 0.12)),
            accel_vol_ratio_fast=float(strat_raw.get("accel_vol_ratio_fast", 1.3)),
            accel_vol_ratio_slow=float(strat_raw.get("accel_vol_ratio_slow", 1.2)),
        ),
        scenario_policy=ScenarioPolicy(
            correction_short_top_n=int(scenario_policy_raw.get("correction_short_top_n", 1)),
            heat_bias_short_top_n=int(scenario_policy_raw.get("heat_bias_short_top_n", 2)),
            correction_midlong_top_n=int(scenario_policy_raw.get("correction_midlong_top_n", 3)),
            min_correction_ok_samples=int(scenario_policy_raw.get("min_correction_ok_samples", 10)),
            new_watch_spotlight_limit=int(scenario_policy_raw.get("new_watch_spotlight_limit", 3)),
        ),
    )


CONFIG = load_config(CONFIG_PATH)

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("theme_watchlist")


def build_session() -> requests.Session:
    retry = Retry(
        total=3, connect=3, read=3, backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "POST"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


HTTP = build_session()


def _make_daily_price_provider(name: str):
    if name == "yahoo":
        return YahooFinancePriceProvider(yf_module=yf, logger=logger)
    if name == "finmind":
        return FinMindPriceProvider(session=HTTP, token=FINMIND_TOKEN)
    raise ValueError(f"Unsupported stock data provider: {name}")


PRIMARY_DAILY_PROVIDER = _make_daily_price_provider(STOCK_DATA_PROVIDER)
FALLBACK_DAILY_PROVIDERS = [
    _make_daily_price_provider(name)
    for name in STOCK_DATA_FALLBACKS
    if name and name != STOCK_DATA_PROVIDER
]
DAILY_PRICE_PROVIDERS = [PRIMARY_DAILY_PROVIDER, *FALLBACK_DAILY_PROVIDERS]


def load_watchlist(csv_path: Path) -> List[dict]:
    rows: List[dict] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ticker = (row.get("ticker") or "").strip()
            name = (row.get("name") or "").strip()
            group = (row.get("group") or CONFIG.watchlist_default_group).strip()
            layer = (row.get("layer") or "").strip()
            enabled = (row.get("enabled") or "true").strip().lower()
            if not ticker or not name:
                continue
            if enabled in {"false", "0", "no", "n"}:
                continue
            if not layer:
                if group == "theme":
                    layer = "short_attack"
                elif group in {"core", "etf"}:
                    layer = "midlong_core"
                else:
                    layer = "midlong_core"
            rows.append({"ticker": ticker, "name": name, "group": group, "layer": layer})
    if not rows:
        raise ValueError("No enabled symbols found in watchlist csv")
    return rows


def normalize_ticker_symbol(raw_ticker: str) -> str:
    ticker = str(raw_ticker).strip().upper()
    if not ticker:
        return ""
    if ticker.endswith(".0"):
        ticker = ticker[:-2]
    if ticker.isdigit():
        if len(ticker) <= 2:
            ticker = ticker.zfill(4)
        elif len(ticker) == 3:
            ticker = ticker.zfill(5)
    if "." in ticker:
        return ticker
    if ticker.endswith("B"):
        return f"{ticker}.TWO"
    return f"{ticker}.TW"


def is_placeholder_name(name: str, ticker: str) -> bool:
    base = ticker.split(".")[0]
    cleaned = str(name or "").strip()
    return not cleaned or cleaned == base


def should_refresh_watchlist_name(name: str, ticker: str) -> bool:
    cleaned = str(name or "").strip()
    if is_placeholder_name(cleaned, ticker):
        return True
    return bool(re.fullmatch(r"[A-Z0-9 .&'()/-]+", cleaned))


def lookup_twse_display_name(ticker: str) -> str:
    cached = TWSE_NAME_CACHE.get(ticker)
    if cached is not None:
        return cached

    base, _, suffix = ticker.partition(".")
    if not base or not any(ch.isdigit() for ch in base):
        TWSE_NAME_CACHE[ticker] = ""
        return ""

    channels = []
    if suffix == "TW":
        channels.extend([f"tse_{base}.tw", f"otc_{base}.tw"])
    elif suffix == "TWO":
        channels.extend([f"otc_{base}.tw", f"tse_{base}.tw"])
    else:
        channels.extend([f"tse_{base}.tw", f"otc_{base}.tw"])

    try:
        resp = HTTP.get(
            "https://mis.twse.com.tw/stock/api/getStockInfo.jsp",
            params={"ex_ch": "|".join(channels), "json": "1", "delay": "0"},
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception:
        TWSE_NAME_CACHE[ticker] = ""
        return ""

    for item in payload.get("msgArray", []) or []:
        name = str(item.get("n", "")).strip()
        if name:
            TWSE_NAME_CACHE[ticker] = name
            return name

    TWSE_NAME_CACHE[ticker] = ""
    return ""


def lookup_yahoo_tw_name(ticker: str) -> str:
    base = ticker.split(".")[0]
    try:
        resp = HTTP.get(f"https://tw.stock.yahoo.com/quote/{base}", timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
    except Exception:
        return ""

    match = re.search(r"<h1[^>]*>([^<]+)</h1>", resp.text)
    if not match:
        return ""
    return match.group(1).strip()


def resolve_security_name(ticker: str) -> str:
    base = ticker.split(".")[0]
    twse_name = lookup_twse_display_name(ticker)
    if twse_name:
        return twse_name
    yahoo_tw_name = lookup_yahoo_tw_name(ticker)
    if yahoo_tw_name:
        return yahoo_tw_name
    try:
        info = yf.Ticker(ticker).get_info()
    except Exception:
        return base

    for key in ["shortName", "longName", "displayName"]:
        value = str(info.get(key, "")).strip()
        if value:
            return value
    return base


def infer_watchlist_row(ticker: str, name: Optional[str] = None) -> dict:
    base = ticker.split(".")[0]
    resolved_name = (name or "").strip() or resolve_security_name(ticker)
    if ticker.endswith(".TWO") and base.endswith("B"):
        return {"ticker": ticker, "name": resolved_name, "group": "etf", "layer": "defensive_watch", "enabled": "true"}
    if base.startswith("00"):
        return {"ticker": ticker, "name": resolved_name, "group": "etf", "layer": "midlong_core", "enabled": "true"}
    return {"ticker": ticker, "name": resolved_name, "group": "core", "layer": "midlong_core", "enabled": "true"}


def sync_watchlist_with_portfolio(watchlist_csv: Path, portfolio_csv: Path) -> list[str]:
    if not portfolio_csv.exists():
        return []

    with watchlist_csv.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
        fieldnames = list(rows[0].keys()) if rows else ["ticker", "name", "group", "layer", "enabled"]

    row_by_ticker = {str(row.get("ticker", "")).strip().upper(): row for row in rows}
    known = set(row_by_ticker)
    additions: list[dict] = []
    added_tickers: list[str] = []
    rows_changed = False

    with portfolio_csv.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            ticker = normalize_ticker_symbol(row.get("ticker", ""))
            if not ticker:
                continue
            if ticker in known:
                existing_row = row_by_ticker[ticker]
                if should_refresh_watchlist_name(existing_row.get("name", ""), ticker):
                    resolved_name = resolve_security_name(ticker)
                    if resolved_name and resolved_name != existing_row.get("name", ""):
                        existing_row["name"] = resolved_name
                        rows_changed = True
                continue
            additions.append(infer_watchlist_row(ticker))
            added_tickers.append(ticker)
            known.add(ticker)

    if additions or rows_changed:
        rows.extend(additions)
        with watchlist_csv.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    return added_tickers


def load_portfolio(csv_path: Path) -> pd.DataFrame:
    if not csv_path.exists():
        return pd.DataFrame(columns=["ticker", "shares", "avg_cost", "target_profit_pct"])
    df = pd.read_csv(csv_path, dtype={"ticker": str})
    if df.empty:
        return df
    df = df.copy()
    df["ticker"] = df["ticker"].astype(str).map(normalize_ticker_symbol)
    for col in ["shares", "avg_cost", "target_profit_pct"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["ticker", "shares", "avg_cost", "target_profit_pct"]).reset_index(drop=True)
    return df


AUTO_ADDED_TICKERS = sync_watchlist_with_portfolio(WATCHLIST_CSV, PORTFOLIO_CSV)
WATCHLIST = load_watchlist(WATCHLIST_CSV)
PORTFOLIO = load_portfolio(PORTFOLIO_CSV)
SPECIAL_ETF_TICKERS = [
    "00772B.TWO",
    "00773B.TWO",
    "0050.TW",
    "00878.TW",
]
SCHEDULE_TARGET_TIMES = ["08:45", "14:00"]


def alternate_taiwan_ticker(ticker: str) -> str:
    return alternate_taiwan_ticker_impl(ticker)


def _download_daily_ohlcv(ticker: str, period: str) -> pd.DataFrame:
    return PRIMARY_DAILY_PROVIDER.download_daily_ohlcv(ticker, period)


def _period_to_days(period: str) -> Optional[int]:
    period = str(period or "").strip().lower()
    if not period:
        return None
    if period == "max":
        return 10**9
    if period == "ytd":
        return 366
    if period.endswith("d") and period[:-1].isdigit():
        return int(period[:-1])
    if period.endswith("mo") and period[:-2].isdigit():
        return int(period[:-2]) * 30
    if period.endswith("y") and period[:-1].isdigit():
        return int(period[:-1]) * 365
    return None


def _slice_frame_to_period(df: pd.DataFrame, period: str) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    requested_days = _period_to_days(period)
    if requested_days is None:
        return df.copy()
    last_index = df.index.max()
    if not isinstance(last_index, pd.Timestamp):
        return df.copy()
    cutoff = last_index - pd.Timedelta(days=requested_days + 10)
    sliced = df.loc[df.index >= cutoff].copy()
    return sliced if not sliced.empty else df.copy()


def _lookup_superset_cache(
    cache: dict[tuple, pd.DataFrame],
    ticker: str,
    period: str,
    *,
    suffix: tuple = (),
) -> Optional[pd.DataFrame]:
    requested_days = _period_to_days(period)
    if requested_days is None:
        return None
    normalized_ticker = str(ticker).strip().upper()
    best: Optional[tuple[int, pd.DataFrame]] = None
    for key, cached_df in cache.items():
        if not key or key[0] != normalized_ticker:
            continue
        if suffix and tuple(key[-len(suffix):]) != suffix:
            continue
        cached_days = _period_to_days(str(key[1]))
        if cached_days is None or cached_days < requested_days:
            continue
        if best is None or cached_days < best[0]:
            best = (cached_days, cached_df)
    return None if best is None else best[1]


def _preferred_shared_period() -> Optional[str]:
    periods = [CONFIG.yf_period]
    if CONFIG.backtest.enabled:
        periods.append(CONFIG.backtest.period)
    normalized = [(period, _period_to_days(period)) for period in periods]
    normalized = [(period, days) for period, days in normalized if days is not None]
    if not normalized:
        return None
    return max(normalized, key=lambda item: item[1])[0]


def _history_cache_path(ticker: str, period: str) -> Path:
    safe_ticker = re.sub(r"[^A-Z0-9]+", "_", str(ticker).strip().upper()).strip("_") or "UNKNOWN"
    safe_period = re.sub(r"[^a-zA-Z0-9]+", "_", str(period).strip()).strip("_") or "period"
    return HISTORY_CACHE_DIR / f"{safe_ticker}__{safe_period}.csv"


def _read_history_cache(path: Path) -> Optional[pd.DataFrame]:
    if not path.exists():
        return None
    try:
        df = pd.read_csv(path, index_col=0, parse_dates=True)
    except Exception:
        return None
    if df.empty:
        return None
    try:
        df.index = pd.to_datetime(df.index)
    except Exception:
        return None
    required_columns = {"Open", "High", "Low", "Close", "Volume"}
    if not required_columns.issubset(df.columns):
        return None
    return df.sort_index()


def _write_history_cache(path: Path, df: pd.DataFrame) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        frame = df.copy()
        frame.index.name = "Date"
        frame.to_csv(path, encoding="utf-8")
    except Exception as exc:
        logger.warning("History cache write skipped for %s: %s", path.name, exc)


def _history_market(ticker: str) -> str:
    normalized = str(ticker or "").strip().upper()
    if normalized.endswith(".TW") or normalized.endswith(".TWO") or normalized in {"^TWII", "TWII"}:
        return "tw"
    return "us"


def _business_day_on_or_before(day: pd.Timestamp) -> pd.Timestamp:
    current = pd.Timestamp(day).normalize()
    while current.weekday() >= 5:
        current -= pd.Timedelta(days=1)
    return current


def _previous_business_day(day: pd.Timestamp) -> pd.Timestamp:
    current = pd.Timestamp(day).normalize() - pd.Timedelta(days=1)
    while current.weekday() >= 5:
        current -= pd.Timedelta(days=1)
    return current


def _required_history_end_date(ticker: str, now_local: Optional[datetime] = None) -> pd.Timestamp:
    current = now_local or datetime.now(LOCAL_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=LOCAL_TZ)

    market = _history_market(ticker)
    market_tz = LOCAL_TZ if market == "tw" else US_MARKET_TZ
    close_minutes = 13 * 60 + 35 if market == "tw" else 16 * 60 + 5

    market_now = current.astimezone(market_tz)
    market_day = pd.Timestamp(market_now.date()).normalize()
    if market_now.weekday() >= 5:
        return _business_day_on_or_before(market_day)

    if market_now.hour * 60 + market_now.minute >= close_minutes:
        return market_day

    return _previous_business_day(market_day)


def _load_history_from_disk_cache(ticker: str, period: str) -> Optional[pd.DataFrame]:
    if not ENABLE_DISK_HISTORY_CACHE:
        return None
    path = _history_cache_path(ticker, period)
    df = _read_history_cache(path)
    if df is None:
        return None
    latest_index = df.index.max()
    if not isinstance(latest_index, pd.Timestamp):
        return None
    if latest_index.normalize() < _required_history_end_date(ticker):
        return None
    _CACHE_STATS["history_disk_hit"] += 1
    return df.copy()


def yf_download_one(ticker: str, period: str) -> pd.DataFrame:
    cache_key = (str(ticker).strip().upper(), str(period).strip())
    if ENABLE_HISTORY_CACHE and cache_key in _DAILY_OHLCV_CACHE:
        _CACHE_STATS["history_hit"] += 1
        return _DAILY_OHLCV_CACHE[cache_key].copy()
    if ENABLE_HISTORY_CACHE:
        superset = _lookup_superset_cache(_DAILY_OHLCV_CACHE, ticker, period)
        if superset is not None:
            _CACHE_STATS["history_superset_hit"] += 1
            sliced = _slice_frame_to_period(superset, period)
            _DAILY_OHLCV_CACHE[cache_key] = sliced.copy()
            return sliced
    disk_cached = _load_history_from_disk_cache(cache_key[0], cache_key[1])
    if disk_cached is not None:
        if ENABLE_HISTORY_CACHE:
            _DAILY_OHLCV_CACHE[cache_key] = disk_cached.copy()
        return disk_cached
    _CACHE_STATS["history_miss"] += 1

    errors: list[str] = []
    for provider in DAILY_PRICE_PROVIDERS:
        try:
            df = provider.download_daily_ohlcv(ticker, period)
            if df.empty:
                raise ValueError(f"No data returned for {ticker}")
            if len(df) < 250:
                raise ValueError(f"Insufficient history for {ticker}: {len(df)} rows")
            if provider.name != PRIMARY_DAILY_PROVIDER.name:
                logger.warning("Data provider fallback: %s via %s", ticker, provider.name)
            if ENABLE_HISTORY_CACHE:
                _DAILY_OHLCV_CACHE[cache_key] = df.copy()
            if ENABLE_DISK_HISTORY_CACHE:
                _write_history_cache(_history_cache_path(cache_key[0], cache_key[1]), df)
            return df.copy()
        except Exception as exc:
            errors.append(f"{provider.name}: {exc}")
    raise ValueError("; ".join(errors) if errors else f"No data returned for {ticker}")


@contextlib.contextmanager
def _suppress_yfinance_noise() -> None:
    # yfinance sometimes emits noisy per-ticker errors (e.g. "possibly delisted") even when we
    # treat quotes as best-effort. Suppress internal chatter but keep our own logs.
    targets = ["yfinance", "yfinance.utils", "yfinance.base", "yfinance.multi"]
    prev: list[tuple[logging.Logger, int, bool]] = []
    for name in targets:
        lg = logging.getLogger(name)
        prev.append((lg, lg.level, lg.propagate))
        lg.setLevel(logging.CRITICAL)
        lg.propagate = False
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            yield
    finally:
        for lg, level, propagate in prev:
            lg.setLevel(level)
            lg.propagate = propagate


def _yf_download_last_close_multi(
    tickers: list[str],
    *,
    period: str,
    interval: str,
) -> dict[str, float]:
    if not tickers:
        return {}
    with _suppress_yfinance_noise():
        df = yf.download(
            " ".join(tickers),
            period=period,
            interval=interval,
            auto_adjust=True,
            progress=False,
            threads=False,
            group_by="ticker",
        )

    if df is None or getattr(df, "empty", True):
        return {}

    out: dict[str, float] = {}
    try:
        if isinstance(df.columns, pd.MultiIndex):
            for ticker in tickers:
                try:
                    if ticker not in df.columns.get_level_values(0):
                        continue
                    sub = df[ticker]
                    if "Close" not in sub.columns:
                        continue
                    series = sub["Close"].dropna()
                    if series.empty:
                        continue
                    out[ticker] = float(series.iloc[-1])
                except Exception:
                    continue
        else:
            if "Close" in df.columns and len(tickers) == 1:
                series = df["Close"].dropna()
                if not series.empty:
                    out[tickers[0]] = float(series.iloc[-1])
    except Exception:
        return {}
    return out


def fetch_realtime_last_close(tickers: list[str]) -> dict[str, float]:
    if not realtime_quotes_enabled():
        return {}
    uniq = [str(t).strip() for t in tickers if str(t).strip()]
    seen: set[str] = set()
    uniq = [t for t in uniq if not (t in seen or seen.add(t))]
    if not uniq:
        return {}

    cache_key = (REALTIME_QUOTE_PERIOD, REALTIME_QUOTE_INTERVAL, tuple(sorted(uniq)))
    cached = _REALTIME_QUOTE_CACHE.get(cache_key)
    if cached:
        ts, data = cached
        ttl = 300 if not data else 30
        if time.time() - ts <= ttl:
            return dict(data)

    out = _yf_download_last_close_multi(
        uniq,
        period=REALTIME_QUOTE_PERIOD,
        interval=REALTIME_QUOTE_INTERVAL,
    )

    # Intraday quotes can be flaky for TW symbols; fallback to last daily close.
    if not out:
        out = _yf_download_last_close_multi(
            uniq,
            period="5d",
            interval="1d",
        )

    _REALTIME_QUOTE_CACHE[cache_key] = (time.time(), dict(out))
    return dict(out)


def add_indicators(df: pd.DataFrame, ma_period: int = 20) -> pd.DataFrame:
    return add_indicators_impl(df, ma_period=ma_period)


def get_indicator_frame(ticker: str, period: str, ma_period: int = 20) -> pd.DataFrame:
    cache_key = (str(ticker).strip().upper(), str(period).strip(), int(ma_period))
    if ENABLE_HISTORY_CACHE and cache_key in _INDICATOR_FRAME_CACHE:
        _CACHE_STATS["indicator_hit"] += 1
        return _INDICATOR_FRAME_CACHE[cache_key].copy()
    if ENABLE_HISTORY_CACHE:
        superset = _lookup_superset_cache(_INDICATOR_FRAME_CACHE, ticker, period, suffix=(int(ma_period),))
        if superset is not None:
            _CACHE_STATS["indicator_superset_hit"] += 1
            sliced = _slice_frame_to_period(superset, period)
            _INDICATOR_FRAME_CACHE[cache_key] = sliced.copy()
            return sliced
    _CACHE_STATS["indicator_miss"] += 1

    frame = add_indicators(yf_download_one(ticker, period), ma_period=ma_period)
    if ENABLE_HISTORY_CACHE:
        _INDICATOR_FRAME_CACHE[cache_key] = frame.copy()
    return frame.copy()


def prewarm_watchlist_indicator_cache() -> None:
    if not ENABLE_HISTORY_CACHE or not WATCHLIST:
        return
    shared_period = _preferred_shared_period()
    if not shared_period:
        return
    for item in WATCHLIST:
        ticker = str(item.get("ticker", "") or "").strip()
        if not ticker:
            continue
        try:
            get_indicator_frame(ticker, shared_period)
        except Exception as exc:
            logger.warning("Cache warmup skipped for %s: %s", ticker, exc)


def apply_group_weight(base_score: int, group: str) -> int:
    return apply_group_weight_impl(base_score, group, CONFIG.group_weights)


def score_band(setup_score: int, risk_score: int) -> str:
    return score_band_impl(setup_score, risk_score)


def layer_label(layer: str) -> str:
    labels = {
        "short_attack": "短線主攻",
        "midlong_core": "中長線核心",
        "defensive_watch": "防守觀察",
    }
    return labels.get(layer, layer)


def speculative_risk_score(
    ret5_pct: float,
    ret20_pct: float,
    volume_ratio20: float,
    bias20_pct: float,
    risk_score: int,
    signals: str,
    group: str,
) -> int:
    return speculative_risk_score_impl(
        ret5_pct=ret5_pct,
        ret20_pct=ret20_pct,
        volume_ratio20=volume_ratio20,
        bias20_pct=bias20_pct,
        risk_score=risk_score,
        signals=signals,
        group=group,
    )


def speculative_risk_label(score: int) -> str:
    return speculative_risk_label_impl(score)


def build_speculative_risk_profile(**kwargs):
    return build_speculative_risk_profile_impl(**kwargs)


def volatility_label(atr_pct: float) -> str:
    return volatility_label_impl(atr_pct)


def volatility_emoji(tag: str) -> str:
    return {
        "穩健": "🧊",
        "標準": "⚖️",
        "活潑": "🔥",
        "劇烈": "⚡",
    }.get(tag, "❔")


def volatility_badge_text(row: pd.Series) -> str:
    tag = str(row.get("volatility_tag", "") or "")
    atr_pct = row.get("atr_pct")
    if not tag:
        try:
            atr_value = float(atr_pct)
            if atr_value > 0:
                tag = volatility_label(atr_value)
        except Exception:
            tag = ""
    if not tag:
        return ""
    emoji = volatility_emoji(tag)
    try:
        atr_value = float(atr_pct)
        if atr_value > 0:
            return f"{emoji}{tag}({atr_value:.2f}%)"
    except Exception:
        pass
    return f"{emoji}{tag}"


def heat_bias_message(df_rank: Optional[pd.DataFrame], scenario: dict) -> str:
    if df_rank is None or df_rank.empty:
        return ""
    working = df_rank.head(10).copy()
    if working.empty:
        return ""
    for col in ["risk_score", "ret5_pct"]:
        if col in working.columns:
            working[col] = pd.to_numeric(working[col], errors="coerce")
    hot_mask = (
        working.get("risk_score", pd.Series(dtype=float)).fillna(0).ge(5)
        | working.get("ret5_pct", pd.Series(dtype=float)).fillna(0).ge(12)
        | working.get("volatility_tag", pd.Series(dtype=str)).astype(str).isin(["活潑", "劇烈"])
        | working.get("spec_risk_label", pd.Series(dtype=str)).astype(str).eq("疑似炒作風險高")
    )
    hot_ratio = float(hot_mask.mean()) if len(working) else 0.0
    label = str(scenario.get("label", "") or "")
    if hot_ratio >= 0.5:
        return "⚠️ Heat Bias 偏強：前排標的偏熱，最近績效可能有行情抬轎，追價風險高。"
    if hot_ratio >= 0.3 and label in {"高檔震盪盤", "強勢延伸盤"}:
        return "⚠️ Heat Bias 提醒：前排標的已有明顯熱度，請把拉回買點與分批落袋看得比平常更重。"
    return ""


def correction_sample_warning_message(scenario: dict) -> str:
    label = str(scenario.get("label", "") or "")
    if label != "明顯修正盤":
        return ""
    if not VERIFICATION_OUTCOMES_CSV.exists():
        return "修正盤驗證提醒：目前還沒有足夠的歷史驗證檔，先把防守放前面。"
    try:
        outcomes = pd.read_csv(
            VERIFICATION_OUTCOMES_CSV,
            dtype={"scenario_label": "string", "status": "string"},
            usecols=["scenario_label", "status"],
        )
    except Exception:
        return "修正盤驗證提醒：驗證資料暫時讀不到，今天先按保守模式處理。"

    if outcomes.empty:
        return "修正盤驗證提醒：目前修正盤 OK 樣本仍不足，先不要把反彈當成行情回來。"

    scenario_label = outcomes.get("scenario_label", pd.Series(dtype="string")).astype(str).str.strip()
    status = outcomes.get("status", pd.Series(dtype="string")).astype(str).str.strip()
    ok_count = int(((scenario_label == "明顯修正盤") & (status == "ok")).sum())
    min_ok = int(CONFIG.scenario_policy.min_correction_ok_samples)
    if ok_count < min_ok:
        return f"修正盤驗證提醒：目前明顯修正盤 OK 樣本只有 {ok_count} 筆，還沒到 {min_ok} 筆，先把風險控管放第一。"
    return ""


def effective_short_top_n(
    df_rank: pd.DataFrame,
    market_regime: Optional[dict] = None,
    us_market: Optional[dict] = None,
) -> int:
    top_n = int(CONFIG.notify.top_n_short)
    if market_regime is None or us_market is None or df_rank.empty:
        return top_n

    scenario = build_market_scenario(market_regime, us_market, df_rank)
    if str(scenario.get("label", "") or "") in {"明顯修正盤", "盤中保守觀察"}:
        return min(top_n, int(CONFIG.scenario_policy.correction_short_top_n))

    heat_bias = heat_bias_message(df_rank, scenario)
    if "Heat Bias 偏強" in heat_bias:
        return min(top_n, int(CONFIG.scenario_policy.heat_bias_short_top_n))
    return top_n


def effective_midlong_top_n(
    df_rank: pd.DataFrame,
    market_regime: Optional[dict] = None,
    us_market: Optional[dict] = None,
) -> int:
    top_n = int(CONFIG.notify.top_n_midlong)
    if market_regime is None or us_market is None or df_rank.empty:
        return top_n

    scenario = build_market_scenario(market_regime, us_market, df_rank)
    if str(scenario.get("label", "") or "") in {"明顯修正盤", "盤中保守觀察"}:
        return min(top_n, int(CONFIG.scenario_policy.correction_midlong_top_n))
    return top_n


def detect_row(
    df: pd.DataFrame,
    ticker: str,
    name: str,
    group: str,
    layer: str,
    strat: Optional[StrategyConfig] = None,
) -> dict:
    if strat is None:
        strat = CONFIG.strategy
    return detect_row_impl(
        df=df,
        ticker=ticker,
        name=name,
        group=group,
        layer=layer,
        strat=strat,
        group_weights=CONFIG.group_weights,
    )


def grade_signal(row: dict) -> str:
    return grade_signal_impl(row)


def append_stock_log(row: dict) -> None:
    log_csv = LOG_DIR / f"{row['ticker'].replace('.', '_')}.csv"
    df_new = pd.DataFrame([row])
    if log_csv.exists():
        df_old = pd.read_csv(log_csv)
        if not df_old.empty and row["date"] in set(df_old["date"].astype(str)):
            df_old = df_old[df_old["date"].astype(str) != row["date"]]
        df_all = pd.concat([df_old, df_new], ignore_index=True)
    else:
        df_all = df_new
    df_all.to_csv(log_csv, index=False, encoding="utf-8-sig")


def load_previous_rank() -> Optional[pd.DataFrame]:
    if not RANK_CSV.exists():
        return None
    try:
        return pd.read_csv(RANK_CSV)
    except Exception:
        return None


def enrich_rank_changes(df_rank: pd.DataFrame, prev_rank: Optional[pd.DataFrame]) -> pd.DataFrame:
    return enrich_rank_changes_impl(df_rank, prev_rank)


def save_daily_rank(rows: List[dict], prev_rank: Optional[pd.DataFrame]) -> pd.DataFrame:
    df = build_rank_table(rows, prev_rank)
    if RANK_CSV.exists():
        RANK_CSV.replace(PREV_RANK_CSV)
    df.to_csv(RANK_CSV, index=False, encoding="utf-8-sig")
    return df


def load_last_state() -> str:
    if not CONFIG.state_enabled or not STATE_FILE.exists():
        return ""
    return STATE_FILE.read_text(encoding="utf-8").strip()


def save_last_state(state: str) -> None:
    if CONFIG.state_enabled:
        STATE_FILE.write_text(state, encoding="utf-8")


def today_local_str() -> str:
    return datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")


def runtime_trigger_label() -> str:
    event_name = os.getenv("GITHUB_EVENT_NAME", "").strip().lower()
    if event_name == "schedule":
        return "Scheduled"
    if event_name == "workflow_dispatch":
        return "Manual"
    if event_name:
        return event_name
    return "Local"


def nearest_schedule_delay_minutes(now_local: datetime) -> Optional[int]:
    candidates: list[int] = []
    for time_str in SCHEDULE_TARGET_TIMES:
        hour_str, minute_str = time_str.split(":")
        target = now_local.replace(
            hour=int(hour_str),
            minute=int(minute_str),
            second=0,
            microsecond=0,
        )
        delta_minutes = int((now_local - target).total_seconds() // 60)
        if delta_minutes >= 0:
            candidates.append(delta_minutes)
    return min(candidates) if candidates else None


def runtime_context_lines() -> list[str]:
    now_local = datetime.now(LOCAL_TZ)
    trigger = runtime_trigger_label()
    lines = [
        f"台灣時間：{now_local.strftime('%Y-%m-%d %H:%M:%S')}",
    ]

    if trigger == "Scheduled":
        delay_minutes = nearest_schedule_delay_minutes(now_local)
        if delay_minutes is None:
            lines.append("排程延遲：尚未到預定時段")
        elif delay_minutes <= 15:
            lines.append(f"排程延遲：{delay_minutes} 分鐘內，屬正常波動")
        else:
            lines.append(f"排程延遲：已延後約 {delay_minutes} 分鐘")

    return lines


def load_last_success_date() -> str:
    if not SUCCESS_FILE.exists():
        return ""
    raw = SUCCESS_FILE.read_text(encoding="utf-8").strip()
    if not raw:
        return ""
    try:
        data = json.loads(raw)
        return str(data.get("date", ""))
    except json.JSONDecodeError:
        return raw


def current_run_signature() -> str:
    hasher = hashlib.sha256()
    for path in [Path(__file__), CONFIG_PATH, WATCHLIST_CSV]:
        hasher.update(str(path).encode("utf-8"))
        hasher.update(path.read_bytes())
    return hasher.hexdigest()[:16]


def load_last_success_signature() -> str:
    if not SUCCESS_FILE.exists():
        return ""
    raw = SUCCESS_FILE.read_text(encoding="utf-8").strip()
    if not raw:
        return ""
    try:
        data = json.loads(raw)
        return str(data.get("signature", ""))
    except json.JSONDecodeError:
        return ""


def save_last_success_date(success_date: str) -> None:
    SUCCESS_FILE.write_text(
        json.dumps({"date": success_date, "signature": current_run_signature()}, ensure_ascii=False),
        encoding="utf-8",
    )


def _timed_call(step_timings: dict[str, float], name: str, func, *args, **kwargs):
    started = time.perf_counter()
    try:
        return func(*args, **kwargs)
    finally:
        step_timings[name] = time.perf_counter() - started


def build_runtime_metrics_markdown(
    *,
    generated_at: str,
    status: str,
    step_timings: dict[str, float],
    warnings: list[str],
    cache_stats: dict[str, int],
    backtest_meta: dict[str, object],
    wall_seconds: float | None = None,
) -> str:
    lines = [
        "# Runtime Metrics",
        f"- Generated: {generated_at}",
        f"- Status: `{status}`",
        "",
        "## Steps",
        "",
        "| Step | Seconds |",
        "| --- | --- |",
    ]
    for name, seconds in step_timings.items():
        lines.append(f"| {name} | {seconds:.4f} |")
    total = sum(step_timings.values())
    lines.extend(["", f"- Total tracked seconds: `{total:.3f}`"])
    if wall_seconds is not None:
        lines.append(f"- Wall-clock seconds: `{wall_seconds:.3f}`")
    lines.extend(
        [
            "",
            "## Cache",
            "",
            (
                f"- History cache: `{cache_stats.get('history_hit', 0)}` exact hit / "
                f"`{cache_stats.get('history_disk_hit', 0)}` disk hit / "
                f"`{cache_stats.get('history_superset_hit', 0)}` superset hit / "
                f"`{cache_stats.get('history_miss', 0)}` miss"
            ),
            (
                f"- Indicator cache: `{cache_stats.get('indicator_hit', 0)}` exact hit / "
                f"`{cache_stats.get('indicator_superset_hit', 0)}` superset hit / "
                f"`{cache_stats.get('indicator_miss', 0)}` miss"
            ),
        ]
    )
    if backtest_meta:
        lines.extend(
            [
                "",
                "## Backtest",
                "",
                f"- Mode: `{backtest_meta.get('last_run_mode', 'unknown')}`",
                f"- Scanned cutoffs: `{backtest_meta.get('last_run_scanned_cutoffs', 0)}`",
            ]
        )
    if warnings:
        lines.extend(["", "## Warnings", ""])
        for warning in warnings:
            lines.append(f"- {warning}")
    return "\n".join(lines)


def write_runtime_metrics(
    *,
    status: str,
    step_timings: dict[str, float],
    warnings: list[str],
    wall_seconds: float | None = None,
) -> None:
    generated_at = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
    backtest_meta: dict[str, object] = {}
    backtest_state_path = OUTDIR / "backtest_state.json"
    if backtest_state_path.exists():
        try:
            backtest_meta = json.loads(backtest_state_path.read_text(encoding="utf-8"))
        except Exception:
            backtest_meta = {}
    payload = {
        "generated_at": generated_at,
        "status": status,
        "step_timings": step_timings,
        "warnings": warnings,
        "total_seconds": round(sum(step_timings.values()), 3),
        "wall_seconds": round(wall_seconds, 3) if wall_seconds is not None else None,
        "cache_stats": dict(_CACHE_STATS),
        "backtest_meta": backtest_meta,
    }
    RUNTIME_METRICS_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    RUNTIME_METRICS_MD.write_text(
        build_runtime_metrics_markdown(
            generated_at=generated_at,
            status=status,
            step_timings=step_timings,
            warnings=warnings,
            cache_stats=dict(_CACHE_STATS),
            backtest_meta=backtest_meta,
            wall_seconds=wall_seconds,
        ),
        encoding="utf-8",
    )


def market_session_phase(now_local: Optional[datetime] = None) -> str:
    current = now_local or datetime.now(LOCAL_TZ)
    minutes = current.hour * 60 + current.minute
    if minutes < 9 * 60:
        return "preopen"
    if minutes < (13 * 60 + 35):
        return "intraday"
    return "postclose"


def get_market_regime() -> dict:
    if not CONFIG.market_filter.enabled:
        return {"enabled": False, "is_bullish": True, "comment": "大盤濾網關掉"}

    df = get_indicator_frame(CONFIG.market_filter.ticker, CONFIG.yf_period, CONFIG.market_filter.ma_period)
    x = df.iloc[-1]
    close_ = float(x["Close"])
    ma = float(x[f"MA{CONFIG.market_filter.ma_period}"])
    ret20 = float(x["Ret20D"]) if pd.notna(x["Ret20D"]) else 0.0
    raw_vol_ratio = float(x["VolumeRatio20"]) if pd.notna(x["VolumeRatio20"]) else float("nan")
    vol_ratio_valid = pd.notna(raw_vol_ratio) and raw_vol_ratio > 0.05
    vol_ratio = raw_vol_ratio if vol_ratio_valid else 1.0
    session_phase = market_session_phase()

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
        "volume_ratio20_raw": round(raw_vol_ratio, 2) if pd.notna(raw_vol_ratio) else None,
        "volume_ratio20_valid": bool(vol_ratio_valid),
        "session_phase": session_phase,
        "is_bullish": bool(is_bullish),
        "comment": (
            f"{CONFIG.market_filter.name}目前"
            f"{'偏多' if is_bullish else '偏保守'}，"
            f"收在 {round(close_,2)}，"
            f"20日漲幅 {round(ret20*100,2)}%，"
            f"量比 {round(vol_ratio,2)}。"
            + (" 量比資料異常，這輪先按中性處理。" if not vol_ratio_valid else "")
        ),
    }


def get_us_market_reference() -> dict:
    refs = [
        ("^GSPC", "S&P500"),
        ("^IXIC", "NASDAQ"),
        ("SOXX", "SOXX"),
        ("NVDA", "NVDA"),
    ]
    rows = []
    for ticker, name in refs:
        try:
            df = get_indicator_frame(ticker, CONFIG.yf_period)
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
        except Exception as exc:
            logger.warning("US market reference failed for %s: %s", ticker, exc)

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


def build_market_scenario(market_regime: dict, us_market: dict, df_rank: Optional[pd.DataFrame] = None) -> dict:
    ret20_pct = float(market_regime.get("ret20_pct", 0.0) or 0.0)
    volume_ratio20 = float(market_regime.get("volume_ratio20", 1.0) or 1.0)
    is_bullish = bool(market_regime.get("is_bullish", False))
    session_phase = str(market_regime.get("session_phase", "postclose") or "postclose")
    us_summary = str(us_market.get("summary", ""))
    us_weak = ("偏弱" in us_summary) or ("續殺" in us_summary)

    hot_count = 0
    strong_count = 0
    candidate_count = 0
    if df_rank is not None and not df_rank.empty:
        working = df_rank.copy()
        for col in ["risk_score", "ret5_pct", "ret20_pct", "volume_ratio20", "setup_score"]:
            if col in working.columns:
                working[col] = pd.to_numeric(working[col], errors="coerce")
        candidate_count = int(min(len(working), 20))
        focus = working.head(candidate_count).copy()
        if not focus.empty:
            hot_mask = (
                (focus.get("risk_score", pd.Series(dtype=float)).fillna(0) >= 5)
                | (focus.get("ret5_pct", pd.Series(dtype=float)).fillna(0) >= 15)
            )
            strong_mask = (
                (focus.get("setup_score", pd.Series(dtype=float)).fillna(0) >= 6)
                & (focus.get("risk_score", pd.Series(dtype=float)).fillna(9) <= 3)
                & (focus.get("ret20_pct", pd.Series(dtype=float)).fillna(-99) >= 0)
            )
            hot_count = int(hot_mask.sum())
            strong_count = int(strong_mask.sum())

    hot_ratio = (hot_count / candidate_count) if candidate_count > 0 else 0.0
    strong_ratio = (strong_count / candidate_count) if candidate_count > 0 else 0.0

    correction_condition = (not is_bullish) or (ret20_pct <= 3 and us_weak)
    if correction_condition and session_phase != "postclose":
        return {
            "label": "盤中保守觀察",
            "stance": "先保守，等收盤定案",
            "focus": "盤中先縮手，避免被即時波動或大盤欄位異常誤導；等收盤後再確認是否真轉修正盤。",
            "exit_note": "盤中若持股轉弱先減碼，但不要只因盤中噪音就全面翻空。",
        }

    if correction_condition:
        return {
            "label": "明顯修正盤",
            "stance": "先保守",
            "focus": "短線先縮手，中線也以守部位、等重新站回為主。",
            "exit_note": "若持股跌破短線支撐或反彈無量，優先減碼，不用硬等。",
        }

    if is_bullish and ret20_pct >= 12 and strong_ratio < 0.35:
        return {
            "label": "權值撐盤、個股轉弱",
            "stance": "選股更重要",
            "focus": "指數可能還不差，但不是每一檔都好做，先看個股延續性。",
            "exit_note": "若持股不跟漲、開高走低或量縮轉弱，要比大盤更早處理。",
        }

    if is_bullish and (ret20_pct >= 10 or volume_ratio20 >= 1.2) and (hot_ratio >= 0.3 or us_weak):
        return {
            "label": "高檔震盪盤",
            "stance": "邊做邊收",
            "focus": "行情還熱，但追價風險明顯變高，進場要更挑買點。",
            "exit_note": "若隔日不續強、出現長上影或爆量不漲，就先分批落袋。",
        }

    return {
        "label": "強勢延伸盤",
        "stance": "順勢但不追價",
        "focus": "主流趨勢仍在，但仍以等拉回取代追高。",
        "exit_note": "有獲利的部位可採分批落袋，避免只看進場不管出場。",
    }


def subscriber_scenario_lines(scenario: dict) -> list[str]:
    label = str(scenario.get("label", "") or "")

    if label == "盤中保守觀察":
        return [
            "今日盤勢：盤中保守觀察",
            "今日策略：先保守，等收盤再定案。",
            "白話說：盤中先縮手、少追高，但不要只因即時噪音就把整天當成修正盤。",
        ]

    if label == "明顯修正盤":
        return [
            "今日盤勢：明顯修正盤",
            "今日策略：先防守，短線名單縮小。",
            "白話說：今天先保留資金、少做少追高，等盤勢穩定再提高出手頻率。",
        ]

    if label == "高檔震盪盤":
        return [
            "今日盤勢：高檔震盪盤",
            "今日策略：可以挑股，但節奏放慢。",
            "白話說：盤面還不差，但追價容錯率下降，優先等拉回、強中選強。",
        ]

    if label == "權值撐盤、個股轉弱":
        return [
            "今日盤勢：權值撐盤、個股轉弱",
            "今日策略：指數可看，選股更要保守。",
            "白話說：不是整個市場都好做，先看真正有量有延續性的個股，不要被指數撐住騙進去。",
        ]

    return [
        "今日盤勢：強勢延伸盤",
        "今日策略：正常推送，可做但不追高。",
        "白話說：市場資金偏正向，名單可正常參考，但仍以拉回分批進場為主。",
    ]


def subscriber_watchlist_lines(scenario: dict, watch_type: str, candidate_limit: int) -> list[str]:
    label = str(scenario.get("label", "") or "")
    title = "短線" if watch_type == "short" else "中長線"

    if watch_type == "short":
        if label == "盤中保守觀察":
            return [
                f"今天{title}策略：先保守，暫時只看 {candidate_limit} 檔，等收盤再決定要不要放大。",
                "白話提醒：盤中先縮手，不急著把每次拉回都當買點。",
            ]
        if label == "明顯修正盤":
            return [
                f"今天{title}策略：先縮手，最多看 {candidate_limit} 檔最清楚的標的。",
                "白話提醒：少做比做錯更重要，先等盤勢穩定。",
            ]
        if label == "高檔震盪盤":
            return [
                f"今天{title}策略：可以做，但只挑前排 {candidate_limit} 檔，優先等拉回。",
                "白話提醒：盤還熱，但追價風險明顯上來了。",
            ]
        if label == "權值撐盤、個股轉弱":
            return [
                f"今天{title}策略：只做最強的 {candidate_limit} 檔，不被指數撐盤帶著追。",
                "白話提醒：指數不差，不代表個股都值得追。",
            ]
        return [
            f"今天{title}策略：正常推送，最多看 {candidate_limit} 檔，拉回再切入。",
            "白話提醒：有行情可以做，但仍以分批進場取代追高。",
        ]

    if label == "盤中保守觀察":
        return [
            f"今天{title}策略：先守結構，只留 {candidate_limit} 檔核心觀察，收盤後再定案。",
            "白話提醒：盤中先保守，不急著把部位一次放大。",
        ]
    if label == "明顯修正盤":
        return [
            f"今天{title}策略：偏保守，只留 {candidate_limit} 檔核心觀察。",
            "白話提醒：先守部位、看結構，不急著擴大進場。",
        ]
    if label == "高檔震盪盤":
        return [
            f"今天{title}策略：可以布局，但只挑 {candidate_limit} 檔結構最穩的標的。",
            "白話提醒：這種盤要挑買點，不要因為看好就直接追。",
        ]
    if label == "權值撐盤、個股轉弱":
        return [
            f"今天{title}策略：偏精挑細選，只留 {candidate_limit} 檔延續性最好的標的。",
            "白話提醒：先看個股有沒有真延續，不要只看指數撐住。",
        ]
    return [
        f"今天{title}策略：正常布局，最多看 {candidate_limit} 檔。",
        "白話提醒：趨勢還在，但仍以等拉回與分批進場為主。",
    ]


def adjust_strategy_by_scenario(base_strat: StrategyConfig, scenario: dict) -> StrategyConfig:
    strat = replace(base_strat)
    label = str(scenario.get("label", "") or "")

    if label in {"明顯修正盤", "盤中保守觀察"}:
        strat.rebreak_vol_ratio += 0.10
        strat.trend_ret20 += 0.01
        strat.accel_ret5 += 0.01
        strat.accel_ret10 += 0.02
        strat.accel_vol_ratio_fast += 0.15
        strat.accel_vol_ratio_slow += 0.10
    elif label == "權值撐盤、個股轉弱":
        strat.rebreak_vol_ratio += 0.05
        strat.accel_ret5 += 0.005
        strat.accel_vol_ratio_fast += 0.10
        strat.accel_vol_ratio_slow += 0.05
    elif label == "高檔震盪盤":
        strat.rebreak_vol_ratio += 0.05
        strat.accel_ret5 += 0.01
        strat.accel_vol_ratio_fast += 0.05
        strat.accel_vol_ratio_slow += 0.05
    elif label == "強勢延伸盤":
        strat.rebreak_vol_ratio = max(strat.rebreak_vol_ratio - 0.05, 1.0)
        strat.accel_ret5 = max(strat.accel_ret5 - 0.005, 0.0)
        strat.accel_ret10 = max(strat.accel_ret10 - 0.01, strat.accel_ret5)
        strat.accel_vol_ratio_fast = max(strat.accel_vol_ratio_fast - 0.05, 1.0)
        strat.accel_vol_ratio_slow = max(strat.accel_vol_ratio_slow - 0.05, 1.0)
    return strat


def strategy_preview_lines(base_strat: StrategyConfig, scenario: dict) -> list[str]:
    adjusted = adjust_strategy_by_scenario(base_strat, scenario)
    field_labels = {
        "rebreak_vol_ratio": "rebreak 量比",
        "trend_ret20": "trend 20D",
        "accel_ret5": "accel 5D",
        "accel_ret10": "accel 10D",
        "accel_vol_ratio_fast": "accel 快速量比",
        "accel_vol_ratio_slow": "accel 緩速量比",
    }
    changed: list[str] = []
    for field, label in field_labels.items():
        before = getattr(base_strat, field)
        after = getattr(adjusted, field)
        if round(before, 4) == round(after, 4):
            continue
        changed.append(f"{label}: {before:.2f} → {after:.2f}")
    if not changed:
        return ["- 今日情境下，adaptive preview 不調整門檻。"]
    return [f"- {line}" for line in changed]


def reorder_priority_groups(df_rank: pd.DataFrame) -> pd.DataFrame:
    rule = CONFIG.notify
    df = df_rank.copy()
    if rule.priority_groups:
        pri = df[df["group"].isin(rule.priority_groups)].copy()
        non = df[~df["group"].isin(rule.priority_groups)].copy()
        df = pd.concat([pri, non], ignore_index=True)
    return df


def _apply_grade_rank(df: pd.DataFrame) -> pd.Series:
    rank_map = {"A": 3, "B": 2, "X": 1, "C": 0}
    return df["grade"].map(rank_map).fillna(0)


def _signal_strength(df: pd.DataFrame, patterns: str) -> pd.Series:
    return df["signals"].fillna("").str.contains(patterns).astype(int)


def rank_short_term_pool(df_rank: pd.DataFrame) -> pd.DataFrame:
    df = reorder_priority_groups(df_rank)
    if "layer" in df.columns:
        df = df[df["layer"].isin(["short_attack", "midlong_core"])].copy()

    df = df[
        (df["setup_score"] >= 4)
        & (df["risk_score"] <= 6)
        & (df["ret20_pct"] >= -5)
    ].copy()
    if df.empty:
        return df

    df["_grade_rank"] = _apply_grade_rank(df)
    df["_signal_rank"] = _signal_strength(df, "ACCEL|TREND|REBREAK")
    df = df.sort_values(
        by=[
            "_grade_rank",
            "_signal_rank",
            "setup_score",
            "ret5_pct",
            "volume_ratio20",
            "setup_change",
            "rank_change",
            "risk_score",
            "rank",
        ],
        ascending=[False, False, False, False, False, False, False, True, True],
    ).reset_index(drop=True)
    return df.drop(columns=["_grade_rank", "_signal_rank"])


def rank_midlong_pool(df_rank: pd.DataFrame) -> pd.DataFrame:
    df = reorder_priority_groups(df_rank)
    if "layer" in df.columns:
        df = df[df["layer"].isin(["midlong_core", "defensive_watch"])].copy()

    df = df[
        (df["setup_score"] >= 4)
        & (df["risk_score"] <= 6)
        & (df["ret20_pct"] >= -5)
    ].copy()
    if df.empty:
        return df

    df["_grade_rank"] = _apply_grade_rank(df)
    df["_signal_rank"] = _signal_strength(df, "TREND|REBREAK|BASE")
    df = df.sort_values(
        by=[
            "_grade_rank",
            "_signal_rank",
            "setup_score",
            "ret20_pct",
            "rank_change",
            "setup_change",
            "risk_score",
            "rank",
        ],
        ascending=[False, False, False, False, False, False, True, True],
    ).reset_index(drop=True)
    return df.drop(columns=["_grade_rank", "_signal_rank"])


def select_short_term_candidates(
    df_rank: pd.DataFrame,
    market_regime: Optional[dict] = None,
    us_market: Optional[dict] = None,
) -> pd.DataFrame:
    df = rank_short_term_pool(df_rank)
    if df.empty:
        return df
    buyable_mask = df.apply(is_short_term_buyable, axis=1)
    top_n_short = effective_short_top_n(df_rank, market_regime, us_market)
    return apply_feedback_adjustment(df[buyable_mask].copy(), "short").head(top_n_short).copy()


def select_short_term_backup_candidates(df_rank: pd.DataFrame, exclude_tickers: Optional[set[str]] = None) -> pd.DataFrame:
    df = rank_short_term_pool(df_rank)
    if not df.empty:
        buyable_mask = df.apply(is_short_term_buyable, axis=1)
        df = df[~buyable_mask].copy()
    if exclude_tickers:
        df = df[~df["ticker"].astype(str).isin(exclude_tickers)].copy()
    return apply_feedback_adjustment(df.copy(), "short").head(5).copy()

def select_midlong_candidates(
    df_rank: pd.DataFrame,
    market_regime: Optional[dict] = None,
    us_market: Optional[dict] = None,
    exclude_tickers: Optional[set[str]] = None,
) -> pd.DataFrame:
    rule = CONFIG.notify
    df = rank_midlong_pool(df_rank)
    if not df.empty:
        buyable_mask = df.apply(is_midlong_buyable, axis=1)
        df = df[buyable_mask].copy()
    if exclude_tickers:
        df = df[~df["ticker"].astype(str).isin(exclude_tickers)].copy()
    top_n_midlong = effective_midlong_top_n(df_rank, market_regime, us_market)
    return apply_feedback_adjustment(df.copy(), "midlong").head(min(rule.top_n_midlong, top_n_midlong)).copy()


def select_midlong_backup_candidates(df_rank: pd.DataFrame, exclude_tickers: Optional[set[str]] = None) -> pd.DataFrame:
    df = rank_midlong_pool(df_rank)
    if not df.empty:
        buyable_mask = df.apply(is_midlong_buyable, axis=1)
        df = df[~buyable_mask].copy()
    if exclude_tickers:
        df = df[~df["ticker"].astype(str).isin(exclude_tickers)].copy()
    return apply_feedback_adjustment(df.copy(), "midlong").head(5).copy()


def select_push_candidates(
    df_rank: pd.DataFrame,
    market_regime: Optional[dict] = None,
    us_market: Optional[dict] = None,
) -> pd.DataFrame:
    short_candidates = select_short_term_candidates(df_rank, market_regime, us_market)
    midlong_candidates = select_midlong_candidates(df_rank, market_regime, us_market)
    return pd.concat([short_candidates, midlong_candidates], ignore_index=True)


def build_candidate_sets(
    df_rank: pd.DataFrame,
    market_regime: Optional[dict] = None,
    us_market: Optional[dict] = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    short_candidates = select_short_term_candidates(df_rank, market_regime, us_market)
    short_backups = select_short_term_backup_candidates(
        df_rank,
        exclude_tickers=set(short_candidates["ticker"].astype(str)),
    )
    midlong_candidates = select_midlong_candidates(df_rank, market_regime, us_market)
    midlong_backups = select_midlong_backup_candidates(
        df_rank,
        exclude_tickers=set(midlong_candidates["ticker"].astype(str)),
    )
    return short_candidates, short_backups, midlong_candidates, midlong_backups


def build_state(df_rank: pd.DataFrame, market_regime: dict) -> str:
    base_state = "|".join(
        f"{r.ticker}:{r.setup_score}:{r.risk_score}:{r.signals}:{r.rank}:{r.grade}"
        for r in df_rank.itertuples(index=False)
    )
    return f"market={market_regime.get('is_bullish', True)}||{base_state}"


def short_term_action_label(row: pd.Series) -> str:
    risk = int(row["risk_score"])
    ret5 = float(row["ret5_pct"])
    vol_ratio = float(row["volume_ratio20"])
    signals = str(row["signals"])
    spec_label = str(row.get("spec_risk_label", "正常"))

    if spec_label == "疑似炒作風險高":
        return "只觀察不追"
    if risk >= 5 or ret5 >= 25:
        return "分批落袋"
    if ret5 >= 15 or (risk >= 4 and ret5 >= 10):
        return "開高不追"
    # 非嚴格可追的加速型，預設以「等拉回」處理（收斂追價、偏 5D/20D 延續）
    if ("ACCEL" in signals and "TREND" in signals) and risk <= 3 and vol_ratio >= 1.0 and float(row.get("ret20_pct", 0.0) or 0.0) >= 0 and ret5 >= 4:
        return "等拉回"
    if ret5 >= 8:
        return "等拉回"
    if row["setup_change"] > 0 or row["rank_change"] > 0:
        return "續抱觀察"
    return "續追蹤"


def is_strict_short_chase(row: pd.Series) -> bool:
    try:
        risk = int(row["risk_score"])
        ret5 = float(row["ret5_pct"])
        vol_ratio = float(row["volume_ratio20"])
        signals = str(row["signals"])
        setup_score = float(row.get("setup_score", 0.0)) if pd.notna(row.get("setup_score")) else 0.0
        ret20 = float(row.get("ret20_pct", 0.0)) if pd.notna(row.get("ret20_pct")) else 0.0
    except Exception:
        return False

    if "ACCEL" not in signals:
        return False
    if "TREND" not in signals:
        return False
    if "SURGE" in signals:
        return False
    if risk > 2:
        return False
    # "可追" 盡量偏向 5D/20D 的延續，而不是 1D 的追價衝動：
    # - 需要更強的量能確認
    # - ret20 要夠大（代表中期趨勢有料）
    # - ret5 不要過熱（避免隔日回檔造成 1D 表現差）
    if vol_ratio < 1.6:
        return False
    if ret5 < 0.5:
        return False
    if ret5 > 8:
        return False
    if ret20 < 10:
        return False
    if setup_score < 9:
        return False
    return True


def is_short_term_buyable(row: pd.Series) -> bool:
    action = short_term_action_label(row)
    return action == "等拉回"


def midlong_action_label(row: pd.Series) -> str:
    risk = int(row["risk_score"])
    ret20 = float(row["ret20_pct"])
    signals = str(row["signals"])
    spec_label = str(row.get("spec_risk_label", "正常"))

    if spec_label == "疑似炒作風險高":
        return "減碼觀察"
    if risk >= 5 or ret20 >= 25:
        return "分批落袋"
    if "TREND" in signals or "REBREAK" in signals:
        return "續抱"
    if row["setup_change"] > 0 or row["rank_change"] > 0:
        return "可分批"
    return "觀察"


def is_midlong_buyable(row: pd.Series) -> bool:
    return midlong_action_label(row) in {"續抱", "可分批"}


def special_etf_action_label(row: pd.Series) -> str:
    ticker = str(row["ticker"])
    ret5 = float(row["ret5_pct"])
    ret20 = float(row["ret20_pct"])
    risk = int(row["risk_score"])
    signals = str(row["signals"])

    if ticker.endswith("B.TWO"):
        if ret20 >= 3 and risk <= 3:
            return "防守續抱"
        if ret20 >= 0:
            return "穩定追蹤"
        return "保守觀察"

    if "TREND" in signals or "REBREAK" in signals:
        return "續抱觀察"
    if ret5 >= 6 and risk <= 4:
        return "可分批"
    return "觀察"


def select_special_etf_candidates(df_rank: pd.DataFrame) -> pd.DataFrame:
    if df_rank.empty:
        return df_rank.head(0).copy()
    df = df_rank[df_rank["ticker"].astype(str).isin(SPECIAL_ETF_TICKERS)].copy()
    if df.empty:
        return df
    df["_ticker_order"] = pd.Categorical(df["ticker"], categories=SPECIAL_ETF_TICKERS, ordered=True)
    df = df.sort_values(by=["_ticker_order", "rank"], ascending=[True, True]).reset_index(drop=True)
    return df.drop(columns=["_ticker_order"])


def select_early_gem_candidates(df_rank: pd.DataFrame) -> pd.DataFrame:
    if df_rank.empty:
        return df_rank.head(0).copy()

    df = reorder_priority_groups(df_rank)
    df = df[
        (df["setup_score"] >= 4)
        & (df["risk_score"] <= 4)
        & (df["ret20_pct"] >= 2)
        & (df["ret20_pct"] <= 18)
        & (df["ret5_pct"] >= -2)
        & (df["ret5_pct"] <= 8)
        & (df["volume_ratio20"] >= 0.8)
        & (df["volume_ratio20"] <= 1.8)
        & (df["spec_risk_label"] == "正常")
    ].copy()
    if df.empty:
        return df

    signal_mask = df["signals"].fillna("").str.contains("TREND|REBREAK|BASE|ACCEL")
    change_mask = (df["setup_change"] > 0) | (df["rank_change"] > 0)
    df = df[signal_mask | change_mask].copy()
    if df.empty:
        return df

    df["_grade_rank"] = _apply_grade_rank(df)
    df["_signal_rank"] = _signal_strength(df, "TREND|REBREAK|BASE|ACCEL")
    df = df.sort_values(
        by=[
            "_grade_rank",
            "_signal_rank",
            "setup_change",
            "rank_change",
            "setup_score",
            "ret20_pct",
            "volume_ratio20",
            "rank",
        ],
        ascending=[False, False, False, False, False, False, False, True],
    ).reset_index(drop=True)
    return df.drop(columns=["_grade_rank", "_signal_rank"]).head(5).copy()


def early_gem_reason(row: pd.Series) -> str:
    reasons: list[str] = []
    signals = str(row["signals"])
    if "REBREAK" in signals:
        reasons.append("重新站回結構")
    elif "TREND" in signals:
        reasons.append("趨勢剛延續")
    elif "BASE" in signals:
        reasons.append("底部墊高")
    elif "ACCEL" in signals:
        reasons.append("剛開始加速")

    if int(row["setup_change"]) > 0:
        reasons.append("setup 轉強")
    if int(row["rank_change"]) > 0:
        reasons.append("排名上升")
    if float(row["ret20_pct"]) <= 10:
        reasons.append("20日漲幅還不算熱")
    if float(row["volume_ratio20"]) <= 1.3:
        reasons.append("量能溫和")

    return " + ".join(reasons[:3]) if reasons else "早期轉強觀察"


def watch_price_plan(row: pd.Series, watch_type: str) -> dict[str, float | str]:
    close_ = float(row.get("close", 0.0) or 0.0)
    ma20 = float(row.get("ma20", close_) or close_)
    ma60 = float(row.get("ma60", ma20) or ma20)
    ret5 = float(row.get("ret5_pct", 0.0) or 0.0)
    ret20 = float(row.get("ret20_pct", 0.0) or 0.0)
    atr_pct = float(row.get("atr_pct", 0.0) or 0.0)
    risk = int(row.get("risk_score", 0) or 0)
    signals = str(row.get("signals", "") or "")
    holding_style = str(row.get("holding_style", "") or holding_style_label(row))

    if close_ <= 0:
        return {"add_price": 0.0, "trim_price": 0.0, "stop_price": 0.0, "note": ""}

    if atr_pct >= 6.0:
        atr_scale = 1.35
    elif atr_pct >= 4.0:
        atr_scale = 1.2
    elif 0 < atr_pct <= 2.0:
        atr_scale = 0.9
    else:
        atr_scale = 1.0

    if watch_type == "short":
        if holding_style == "進攻持股":
            pullback_pct = 0.05 if ("ACCEL" in signals or ret5 >= 8) else 0.04
            trim_pct = 0.07 if ret20 < 12 else 0.08
            stop_pct = 0.045 if risk <= 2 else 0.055
            note = "進攻股用快進快出思維，先等更明確的回檔。"
        elif holding_style == "防守持股":
            pullback_pct = 0.025
            trim_pct = 0.06 if ret20 < 8 else 0.07
            stop_pct = 0.035 if risk <= 2 else 0.045
            note = "防守股不用追價，偏向小回檔再看。"
        else:
            pullback_pct = 0.03
            if "ACCEL" in signals:
                pullback_pct = 0.04
            if ret5 >= 8:
                pullback_pct = max(pullback_pct, 0.05)
            trim_pct = 0.08
            if ret20 >= 12 or ("TREND" in signals and risk <= 2):
                trim_pct = 0.1
            stop_pct = 0.05 if risk <= 2 else 0.06
            note = "短線先等回檔，不追現價。"

        pullback_pct *= atr_scale
        stop_pct *= atr_scale
        add_price = max(ma20, close_ * (1 - pullback_pct))
        trim_price = close_ * (1 + trim_pct)
        support_buffer = 0.02 * atr_scale
        stop_price = min(ma20 * (1 - support_buffer), close_ * (1 - stop_pct))
    else:
        if holding_style == "進攻持股":
            pullback_pct = 0.06 if ret20 >= 10 else 0.05
            trim_pct = 0.12 if ret20 < 15 else 0.14
            stop_pct = 0.07 if risk <= 2 else 0.09
            note = "進攻型中線股用分批策略，但失效也要看得更緊。"
        elif holding_style == "防守持股":
            pullback_pct = 0.035 if ret20 >= 5 else 0.025
            trim_pct = 0.08 if ret20 < 10 else 0.1
            stop_pct = 0.05 if risk <= 2 else 0.06
            note = "防守型標的用配置角度看，不用太激進加減碼。"
        else:
            pullback_pct = 0.05
            if risk <= 2 and ret20 >= 10:
                pullback_pct = 0.06
            trim_pct = 0.12
            if ret20 >= 15:
                trim_pct = 0.15
            stop_pct = 0.08 if risk <= 2 else 0.1
            note = "中線用分批看，不用急著一次買滿。"

        pullback_pct *= atr_scale
        stop_pct *= atr_scale
        add_price = max(ma20, ma60, close_ * (1 - pullback_pct))
        trim_price = close_ * (1 + trim_pct)
        support_buffer = 0.03 * atr_scale
        stop_price = min(ma20 * (1 - support_buffer), ma60 * (1 - support_buffer), close_ * (1 - stop_pct))

    return {
        "add_price": round(add_price, 2),
        "trim_price": round(trim_price, 2),
        "stop_price": round(max(stop_price, 0.01), 2),
        "note": note,
    }


def watch_price_plan_text(row: pd.Series, watch_type: str) -> str:
    plan = watch_price_plan(row, watch_type)
    if not plan["add_price"]:
        return ""
    return (
        f"加碼參考 {plan['add_price']} / "
        f"減碼參考 {plan['trim_price']} / "
        f"失效 {plan['stop_price']}"
    )


def build_special_etf_summary(etf_candidates: pd.DataFrame) -> list[str]:
    if etf_candidates.empty:
        return ["今天指定 ETF / 債券標的沒有抓到完整資料，先看盤中報表。"]

    equity = etf_candidates[etf_candidates["ticker"].isin(["0050.TW", "00878.TW"])].copy()
    bonds = etf_candidates[etf_candidates["ticker"].isin(["00772B.TWO", "00773B.TWO"])].copy()
    summary: list[str] = []

    if not equity.empty:
        eq_ret20 = float(equity["ret20_pct"].mean())
        if eq_ret20 >= 5:
            summary.append("股票 ETF 偏多，台股大盤與高股息風格仍有撐。")
        elif eq_ret20 >= 0:
            summary.append("股票 ETF 偏穩，較像整理後續看量價。")
        else:
            summary.append("股票 ETF 偏弱，今天台股大型權值先別太急。")

    if not bonds.empty:
        bond_ret20 = float(bonds["ret20_pct"].mean())
        if bond_ret20 >= 3:
            summary.append("債券 ETF 偏穩，防守資金和利率壓力都還算可控。")
        elif bond_ret20 >= 0:
            summary.append("債券 ETF 中性，先當防守觀察，不急著加碼。")
        else:
            summary.append("債券 ETF 偏弱，代表利率面壓力仍在。")

    return summary or ["ETF / 債券整體訊號還不夠明確，先觀察。"]


def build_early_gem_message(df_rank: pd.DataFrame, market_regime: dict, us_market: dict) -> str:
    gem_candidates = select_early_gem_candidates(df_rank)
    lines = [
        "📣 早期轉強觀察",
    ]
    if gem_candidates.empty:
        lines.append("今天沒有特別像『還沒完全被市場定價，但已開始轉強』的標的。")
        return "\n".join(lines).strip()

    lines.append("解讀：這一區不是追最熱，而是找剛轉強、還沒太擁擠的候選。")
    lines.append("")
    for _, r in gem_candidates.iterrows():
        vol_text = volatility_badge_text(r)
        lines.append(
            f"{int(r['rank'])}. {r['name']} ({r['ticker']}) | "
            f"{layer_label(r['layer'])} | 5日 {r['ret5_pct']}% / 20日 {r['ret20_pct']}% | "
            f"{vol_text} | "
            f"{early_gem_reason(r)} | {watch_price_plan_text(r, 'short')}"
        )
    return "\n".join(lines).strip()


def build_special_etf_message(df_rank: pd.DataFrame, market_regime: dict, us_market: dict) -> str:
    etf_candidates = select_special_etf_candidates(df_rank)
    lines = [
        "📣 ETF / 債券觀察",
    ]
    lines.extend(build_special_etf_summary(etf_candidates))
    if etf_candidates.empty:
        return "\n".join(lines).strip()

    lines.append("")
    lines.append("解讀：0050、00878偏向台股風向球；00772B、00773B偏向利率與防守溫度計。")
    lines.append("")
    for _, r in etf_candidates.iterrows():
        action = special_etf_action_label(r)
        lines.append(
            f"{r['name']} ({r['ticker']}) {action} | "
            f"5日 {r['ret5_pct']}% / 20日 {r['ret20_pct']}% | {layer_label(r['layer'])}"
        )
    return "\n".join(lines).strip()


def should_alert(
    df_rank: pd.DataFrame,
    current_state: str,
    last_state: str,
    market_regime: dict,
    us_market: Optional[dict] = None,
) -> bool:
    if CONFIG.always_notify:
        return True
    if current_state == last_state:
        return False
    short_candidates, short_backups, midlong_candidates, midlong_backups = build_candidate_sets(
        df_rank,
        market_regime,
        us_market,
    )
    candidates = pd.concat([short_candidates, short_backups, midlong_candidates, midlong_backups], ignore_index=True)
    if candidates.empty:
        return False
    if market_regime.get("is_bullish", True):
        return True
    if CONFIG.market_filter.allow_a_grade_even_if_weak and (candidates["grade"] == "A").any():
        return True
    return False


def build_short_term_message(df_rank: pd.DataFrame, market_regime: dict, us_market: dict) -> str:
    short_candidates, short_backups, _, _ = build_candidate_sets(df_rank, market_regime, us_market)
    scenario = build_market_scenario(market_regime, us_market, df_rank)
    short_top_n = effective_short_top_n(df_rank, market_regime, us_market)
    total_a = int((df_rank["grade"] == "A").sum()) if not df_rank.empty else 0
    total_b = int((df_rank["grade"] == "B").sum()) if not df_rank.empty else 0
    total_up = int((df_rank["status_change"] == "UP").sum()) if "status_change" in df_rank.columns else 0

    lines = [
        "📣 短線可買",
    ]
    summary_parts = [f"A級 {total_a} 檔", f"B級 {total_b} 檔", f"轉強 {total_up} 檔"]
    lines.append(" / ".join(summary_parts))
    lines.extend(subscriber_watchlist_lines(scenario, "short", short_top_n))
    if short_candidates.empty:
        lines.append("今天短線沒有夠清楚的可買標的，先等。")
        return "\n".join(lines)

    lines.append("")
    lines.append("解讀：這一區只放今天相對可考慮出手的短線標的；太熱或只適合續看的，會放到短線觀察。")
    lines.append("短線主看 5 個交易日；1D 只當輔助參考。")
    lines.append("")
    for _, r in short_candidates.iterrows():
        action = short_term_action_label(r)
        vol_text = volatility_badge_text(r)
        lines.append(
            f"{int(r['rank'])}. {r['name']} ({r['ticker']}) {action} | "
            f"5日 {r['ret5_pct']}% / 量比 {r['volume_ratio20']} | "
            f"{vol_text} | {r['regime']} | {watch_price_plan_text(r, 'short')}"
        )
    if not short_backups.empty:
        lines.append("")
        lines.append("短線觀察 (最多5檔)")
        for _, r in short_backups.iterrows():
            action = short_term_action_label(r)
            vol_text = volatility_badge_text(r)
            lines.append(
                f"{int(r['rank'])}. {r['name']} ({r['ticker']}) {action} | "
                f"5日 {r['ret5_pct']}% / 量比 {r['volume_ratio20']} | "
                f"{vol_text} | {r['regime']} | {watch_price_plan_text(r, 'short')}"
            )
    return "\n".join(lines).strip()


def build_midlong_message(df_rank: pd.DataFrame, market_regime: dict, us_market: dict) -> str:
    _, _, midlong_candidates, midlong_backups = build_candidate_sets(df_rank, market_regime, us_market)
    scenario = build_market_scenario(market_regime, us_market, df_rank)
    midlong_top_n = effective_midlong_top_n(df_rank, market_regime, us_market)
    total_b = int((df_rank["grade"] == "B").sum()) if not df_rank.empty else 0
    lines = [
        "📣 中長線可布局",
        f"B級結構股 {total_b} 檔",
    ]
    lines.extend(subscriber_watchlist_lines(scenario, "midlong", midlong_top_n))
    if midlong_candidates.empty:
        lines.append("今天中長線沒有夠穩、夠適合布局的標的，先觀察。")
        return "\n".join(lines)

    lines.append("")
    lines.append("解讀：這一區偏向可布局的趨勢股；強但不一定適合現在進場的，會放到中長線觀察。")
    lines.append("中線主看 20 個交易日；1D / 5D 只當輔助觀察。")
    lines.append("")
    for _, r in midlong_candidates.iterrows():
        action = midlong_action_label(r)
        vol_text = volatility_badge_text(r)
        lines.append(
            f"{int(r['rank'])}. {r['name']} ({r['ticker']}) {action} | "
            f"20日 {r['ret20_pct']}% / 量比 {r['volume_ratio20']} | "
            f"{vol_text} | {r['regime']} | {watch_price_plan_text(r, 'midlong')}"
        )
    if not midlong_backups.empty:
        lines.append("")
        lines.append("中長線觀察 (最多5檔)")
        for _, r in midlong_backups.iterrows():
            action = midlong_action_label(r)
            vol_text = volatility_badge_text(r)
            lines.append(
                f"{int(r['rank'])}. {r['name']} ({r['ticker']}) {action} | "
                f"20日 {r['ret20_pct']}% / 量比 {r['volume_ratio20']} | "
                f"{vol_text} | {r['regime']} | {watch_price_plan_text(r, 'midlong')}"
            )
    return "\n".join(lines).strip()


def new_watchlist_spotlight_lines(df_rank: Optional[pd.DataFrame]) -> list[str]:
    limit = int(CONFIG.scenario_policy.new_watch_spotlight_limit)
    if limit <= 0 or df_rank is None or df_rank.empty or "status_change" not in df_rank.columns:
        return []
    if not PREV_RANK_CSV.exists():
        return []

    fresh = df_rank[df_rank["status_change"].astype(str).eq("NEW")].copy().head(limit)
    if fresh.empty:
        return []

    lines = ["新加入追蹤觀察："]
    for _, row in fresh.iterrows():
        watch_type = "short" if str(row.get("layer", "")) == "short_attack" else "midlong"
        action = short_term_action_label(row) if watch_type == "short" else midlong_action_label(row)
        lines.append(
            f"- {row['name']} ({row['ticker']}) | {layer_label(str(row.get('layer', '')))} | "
            f"{volatility_badge_text(row)} | 初步看法：{action} | {row['regime']}"
        )
    return lines


def build_macro_message(market_regime: dict, us_market: dict, df_rank: Optional[pd.DataFrame] = None) -> str:
    scenario = build_market_scenario(market_regime, us_market, df_rank)
    lines = [
        "📣 大盤 / 美股摘要",
        *subscriber_scenario_lines(scenario),
        market_regime["comment"],
        us_market["summary"],
        f"盤勢情境：{scenario['label']} | 目前節奏：{scenario['stance']}",
        f"操作重點：{scenario['focus']}",
        f"出場提醒：{scenario['exit_note']}",
    ]
    heat_bias = heat_bias_message(df_rank, scenario)
    if heat_bias:
        lines.append(heat_bias)
    correction_note = correction_sample_warning_message(scenario)
    if correction_note:
        lines.append(correction_note)
    lines.extend(runtime_context_lines())
    if us_market.get("tech_bias"):
        lines.append(us_market["tech_bias"])
    if AUTO_ADDED_TICKERS:
        lines.append(f"持股同步加入觀察清單：{', '.join(AUTO_ADDED_TICKERS)}")
    lines.extend(new_watchlist_spotlight_lines(df_rank))
    return "\n".join(lines).strip()


def holding_style_label(row: pd.Series) -> str:
    ticker = str(row.get("ticker", "") or "").upper()
    group = str(row.get("group", "") or "").lower()
    layer = str(row.get("layer", "") or "").lower()
    signals = str(row.get("signals", "") or "")
    risk_score = int(row.get("risk_score", 0)) if pd.notna(row.get("risk_score")) else 0
    ret20_pct = float(row.get("ret20_pct", 0.0)) if pd.notna(row.get("ret20_pct")) else 0.0

    if any(tag in ticker for tag in [".TWO", ".TW"]):
        code = ticker.split(".")[0]
    else:
        code = ticker

    if group == "etf" or code.startswith("00"):
        return "防守持股"
    if code in {"2882", "2884", "2886", "2890", "2891", "2892"}:
        return "防守持股"
    if layer in {"midlong_core", "defensive_watch"} or group == "core":
        return "核心持股"
    if "ACCEL" in signals or risk_score >= 4 or ret20_pct >= 15:
        return "進攻持股"
    return "核心持股"


def portfolio_advice_label(row: pd.Series, market_scenario: Optional[dict] = None) -> str:
    current_close = row.get("current_close")
    if pd.isna(current_close):
        return "已補進觀察清單"

    profit_pct = float(row.get("unrealized_pnl_pct", 0.0))
    target_pct = float(row.get("target_profit_pct", 0.0))
    risk_score = int(row.get("risk_score", 0)) if pd.notna(row.get("risk_score")) else 0
    signals = str(row.get("signals", ""))
    ret20_pct = float(row.get("ret20_pct", 0.0)) if pd.notna(row.get("ret20_pct")) else 0.0
    volume_ratio20 = float(row.get("volume_ratio20", 0.0)) if pd.notna(row.get("volume_ratio20")) else 0.0
    holding_style = str(row.get("holding_style", "") or holding_style_label(row))

    if profit_pct >= target_pct and risk_score >= 4:
        base = "達標可落袋"
    elif profit_pct >= target_pct:
        base = "達標續抱"
    elif profit_pct <= -8 or risk_score >= 5:
        base = "轉弱留意"
    elif ("TREND" in signals or "REBREAK" in signals) and risk_score <= 3:
        base = "續抱"
    elif "ACCEL" in signals and risk_score <= 2 and profit_pct > 0 and ret20_pct >= 0 and volume_ratio20 >= 1.0:
        base = "強勢續抱"
    elif profit_pct > 0 and risk_score <= 3 and ret20_pct >= 0:
        base = "續抱觀察"
    else:
        base = "中性觀察"

    if not market_scenario:
        return base

    scenario_label = str(market_scenario.get("label", ""))

    if scenario_label == "高檔震盪盤":
        if holding_style == "進攻持股" and (profit_pct >= max(target_pct * 0.35, 4) or ("ACCEL" in signals and profit_pct >= 4)):
            return "分批落袋"
        if holding_style == "核心持股" and profit_pct >= max(target_pct * 0.5, 6):
            return "續抱但設停利"
        if holding_style == "防守持股" and profit_pct >= max(target_pct * 0.6, 6):
            return "續抱觀察"
        if profit_pct >= max(target_pct * 0.4, 5) or risk_score >= 4 or ("ACCEL" in signals and profit_pct >= 5):
            return "分批落袋"
        if base == "強勢續抱":
            return "續抱但設停利"
        if base in {"續抱", "續抱觀察", "達標續抱"}:
            return "續抱但盯盤"
        return base

    if scenario_label == "權值撐盤、個股轉弱":
        if holding_style == "防守持股":
            return "續抱觀察" if profit_pct > -5 else "保守觀察"
        if holding_style == "核心持股" and profit_pct > 0:
            return "續抱但看強弱"
        if profit_pct > 0 and risk_score >= 3:
            return "有賺先收一點"
        if base in {"強勢續抱", "續抱"}:
            return "續抱但看強弱"
        if profit_pct <= 0:
            return "轉弱先顧"
        return base

    if scenario_label == "明顯修正盤":
        if holding_style == "防守持股":
            return "防守續看" if profit_pct > -3 else "保守觀察"
        if holding_style == "進攻持股" and profit_pct > 0:
            return "先降部位"
        if profit_pct > 0:
            return "先降部位"
        if risk_score >= 4 or ret20_pct < 0:
            return "保守觀察"
        return "減碼觀察"

    if scenario_label == "強勢延伸盤":
        if holding_style == "防守持股":
            return "續抱觀察"
        if holding_style == "核心持股" and base in {"續抱", "強勢續抱"}:
            return "核心續抱"
        if base == "達標續抱":
            return "達標分批抱"
        if base == "強勢續抱" and profit_pct >= max(target_pct * 0.5, 8):
            return "強勢續抱但分批收"

    return base


def build_portfolio_review_df(
    df_rank: pd.DataFrame,
    market_regime: Optional[dict] = None,
    us_market: Optional[dict] = None,
) -> pd.DataFrame:
    if PORTFOLIO.empty:
        return pd.DataFrame()

    market_scenario = None
    if market_regime is not None and us_market is not None:
        market_scenario = build_market_scenario(market_regime, us_market, df_rank)

    market_cols = [
        "ticker", "name", "close", "signals", "regime", "risk_score",
        "ret5_pct", "ret20_pct", "volume_ratio20", "atr_pct", "volatility_tag"
    ]
    market_df = df_rank.reindex(columns=market_cols).copy() if not df_rank.empty else pd.DataFrame(columns=market_cols)
    review = PORTFOLIO.merge(market_df, on="ticker", how="left")
    review["name"] = review["name"].fillna(review["ticker"].str.split(".").str[0])

    review["current_close"] = pd.to_numeric(review["close"], errors="coerce")
    review["quote_source"] = "close"
    realtime = fetch_realtime_last_close(review["ticker"].tolist())
    if realtime:
        review["realtime_close"] = pd.to_numeric(review["ticker"].map(realtime), errors="coerce")
        has_realtime = review["realtime_close"].notna()
        review.loc[has_realtime, "current_close"] = review.loc[has_realtime, "realtime_close"]
        review.loc[has_realtime, "quote_source"] = "realtime"
    else:
        review["realtime_close"] = pd.NA

    review["position_cost"] = (review["shares"] * review["avg_cost"]).round(2)
    review["position_value"] = (review["shares"] * review["current_close"]).round(2)
    review["unrealized_pnl"] = (review["position_value"] - review["position_cost"]).round(2)
    review["unrealized_pnl_pct"] = ((review["current_close"] / review["avg_cost"] - 1.0) * 100).round(2)
    review["target_gap_pct"] = (review["target_profit_pct"] - review["unrealized_pnl_pct"]).round(2)
    review["holding_style"] = review.apply(holding_style_label, axis=1)
    review["advice"] = review.apply(lambda row: portfolio_advice_label(row, market_scenario), axis=1)
    review["market_scenario"] = market_scenario.get("label", "") if market_scenario else ""
    review["market_stance"] = market_scenario.get("stance", "") if market_scenario else ""
    return review.sort_values(by=["unrealized_pnl_pct", "target_gap_pct"], ascending=[False, True]).reset_index(drop=True)


def build_portfolio_message(
    df_rank: pd.DataFrame,
    market_regime: Optional[dict] = None,
    us_market: Optional[dict] = None,
) -> str:
    review = build_portfolio_review_df(df_rank, market_regime, us_market)
    lines = ["📣 持股檢查"]
    if market_regime is not None and us_market is not None:
        scenario = build_market_scenario(market_regime, us_market, df_rank)
        lines.append(f"持股節奏：{scenario['label']} | {scenario['stance']}")
        lines.append(f"今天重點：{scenario['exit_note']}")
        heat_bias = heat_bias_message(df_rank, scenario)
        if heat_bias:
            lines.append(heat_bias)
    if review.empty:
        lines.append("portfolio.csv 目前沒有可分析的持股。")
        return "\n".join(lines)

    for _, r in review.iterrows():
        current_close = r.get("current_close")
        if pd.isna(current_close):
            lines.append(f"{r['ticker'].split('.')[0]} {r['advice']} | 尚未抓到行情，已同步加入觀察清單")
            continue
        vol_text = volatility_badge_text(r)
        lines.append(
            f"{r['name']} ({r['ticker'].split('.')[0]}) [{r['holding_style']}] {r['advice']} | "
            f"{vol_text} | 現價 {round(float(current_close), 2)} / 成本 {round(float(r['avg_cost']), 2)} | "
            f"報酬 {r['unrealized_pnl_pct']}% / 目標 {r['target_profit_pct']}%"
        )
    return "\n".join(lines).strip()


def history_target_return(row: pd.Series) -> tuple[Optional[float], str]:
    watch_type = str(row.get("watch_type", ""))
    if watch_type == "short":
        for col, label in [("ret5_future_pct", "5D"), ("ret1_future_pct", "1D")]:
            value = row.get(col)
            if pd.notna(value):
                return float(value), label
    if watch_type == "midlong":
        for col, label in [("ret20_future_pct", "20D"), ("ret5_future_pct", "5D"), ("ret1_future_pct", "1D")]:
            value = row.get(col)
            if pd.notna(value):
                return float(value), label
    return None, ""


def feedback_action_label(row: pd.Series, watch_type: str) -> str:
    if watch_type == "short":
        return short_term_action_label(row)
    return midlong_action_label(row)


def feedback_label_from_score(score: float, samples: int) -> str:
    if samples < 3:
        return "樣本不足"
    if score >= 1.2:
        return "近期有效"
    if score <= -1.2:
        return "近期偏弱"
    return "中性"


def feedback_window_size(watch_type: str) -> int:
    return 12 if watch_type == "short" else 8


def compute_feedback_score_components(
    returns: pd.Series,
    sample_scale: int,
    use_weights: bool = False,
) -> dict[str, float]:
    if returns.empty:
        return {
            "win_rate_pct": 0.0,
            "avg_return_pct": 0.0,
            "avg_win_return_pct": 0.0,
            "avg_loss_return_pct": 0.0,
            "pl_ratio": 0.0,
            "feedback_score": 0.0,
        }

    working = returns.astype(float).reset_index(drop=True)
    weights = pd.Series([1.0] * len(working))
    if use_weights and len(working) > 1:
        floor = 0.65
        step = (1.0 - floor) / max(len(working) - 1, 1)
        weights = pd.Series([1.0 - (step * i) for i in range(len(working))])

    positive_mask = working > 0
    negative_mask = ~positive_mask
    positive = working[positive_mask]
    negative = working[negative_mask]
    positive_weights = weights[positive_mask]
    negative_weights = weights[negative_mask]

    total_weight = float(weights.sum()) or 1.0
    win_rate_pct = round(float(weights[positive_mask].sum() / total_weight) * 100, 2)
    avg_return_pct = round(float((working * weights).sum() / total_weight), 2)
    avg_win_return_pct = round(float((positive * positive_weights).sum() / positive_weights.sum()), 2) if not positive.empty else 0.0
    avg_loss_return_pct = round(float((negative * negative_weights).sum() / negative_weights.sum()), 2) if not negative.empty else 0.0
    gross_win = float((positive * positive_weights).sum()) if not positive.empty else 0.0
    gross_loss = abs(float((negative * negative_weights).sum())) if not negative.empty else 0.0
    pl_ratio = round(gross_win / gross_loss, 2) if gross_loss > 0 else (round(gross_win, 2) if gross_win > 0 else 0.0)
    shrink = min(sample_scale / 8.0, 1.0)
    pl_ratio_capped = min(max(pl_ratio, 0.0), 4.0)
    pl_ratio_component = (pl_ratio_capped - 1.0) / 4.0
    feedback_score = round(
        (
            ((win_rate_pct - 50.0) / 10.0)
            + (avg_return_pct / 5.0)
            + pl_ratio_component
        )
        * shrink,
        2,
    )
    return {
        "win_rate_pct": win_rate_pct,
        "avg_return_pct": avg_return_pct,
        "avg_win_return_pct": avg_win_return_pct,
        "avg_loss_return_pct": avg_loss_return_pct,
        "pl_ratio": pl_ratio,
        "feedback_score": feedback_score,
    }


def build_feedback_summary() -> pd.DataFrame:
    if not ALERT_TRACK_CSV.exists():
        return pd.DataFrame()
    try:
        hist = pd.read_csv(ALERT_TRACK_CSV)
    except Exception:
        return pd.DataFrame()
    if hist.empty or "watch_type" not in hist.columns:
        return pd.DataFrame()

    rows = []
    working = hist.copy()
    for watch_type in ["short", "midlong"]:
        subset = working[working["watch_type"].astype(str) == watch_type].copy()
        if subset.empty:
            continue
        if "action_label" not in subset.columns:
            subset["action_label"] = ""
        subset["alert_date"] = pd.to_datetime(subset.get("alert_date"), errors="coerce")
        subset["target_return"] = subset.apply(lambda r: history_target_return(r)[0], axis=1)
        subset = subset[subset["target_return"].notna()].copy()
        if subset.empty:
            continue
        subset = subset.sort_values("alert_date", ascending=False, kind="mergesort").reset_index(drop=True)

        for action_label in ["__all__"] + sorted(set(subset["action_label"].astype(str))):
            action_df = subset if action_label == "__all__" else subset[subset["action_label"].astype(str) == action_label].copy()
            if action_df.empty:
                continue
            samples = int(action_df.shape[0])
            base_metrics = compute_feedback_score_components(
                action_df["target_return"],
                sample_scale=samples,
                use_weights=False,
            )
            recent_window = feedback_window_size(watch_type)
            recent_df = action_df.head(recent_window).copy()
            recent_samples = int(recent_df.shape[0])
            recent_metrics = compute_feedback_score_components(
                recent_df["target_return"],
                sample_scale=recent_samples,
                use_weights=True,
            )
            feedback_score = round(
                (base_metrics["feedback_score"] * 0.7) + (recent_metrics["feedback_score"] * 0.3),
                2,
            )
            rows.append(
                {
                    "watch_type": watch_type,
                    "action_label": action_label,
                    "samples": samples,
                    "recent_samples": recent_samples,
                    "win_rate_pct": base_metrics["win_rate_pct"],
                    "avg_return_pct": base_metrics["avg_return_pct"],
                    "avg_win_return_pct": base_metrics["avg_win_return_pct"],
                    "avg_loss_return_pct": base_metrics["avg_loss_return_pct"],
                    "pl_ratio": base_metrics["pl_ratio"],
                    "recent_win_rate_pct": recent_metrics["win_rate_pct"],
                    "recent_avg_return_pct": recent_metrics["avg_return_pct"],
                    "recent_pl_ratio": recent_metrics["pl_ratio"],
                    "base_feedback_score": base_metrics["feedback_score"],
                    "recent_feedback_score": recent_metrics["feedback_score"],
                    "feedback_score": feedback_score,
                    "feedback_label": feedback_label_from_score(feedback_score, samples),
                }
            )
    summary = pd.DataFrame(rows)
    if not summary.empty:
        summary.to_csv(FEEDBACK_SUMMARY_CSV, index=False, encoding="utf-8-sig")
    return summary


def feedback_score_lookup(summary: pd.DataFrame, watch_type: str, action_label: str) -> tuple[float, str, float]:
    if summary is None or summary.empty:
        return 0.0, "樣本不足", 0.0
    exact = summary[
        (summary["watch_type"].astype(str) == watch_type)
        & (summary["action_label"].astype(str) == action_label)
    ]
    if not exact.empty:
        row = exact.iloc[0]
        return float(row["feedback_score"]), str(row["feedback_label"]), float(row.get("pl_ratio", 0.0) or 0.0)
    fallback = summary[
        (summary["watch_type"].astype(str) == watch_type)
        & (summary["action_label"].astype(str) == "__all__")
    ]
    if not fallback.empty:
        row = fallback.iloc[0]
        return float(row["feedback_score"]), str(row["feedback_label"]), float(row.get("pl_ratio", 0.0) or 0.0)
    return 0.0, "樣本不足", 0.0


def apply_feedback_adjustment(df: pd.DataFrame, watch_type: str) -> pd.DataFrame:
    if df.empty:
        return df
    summary = build_feedback_summary()
    out = df.copy().reset_index(drop=True)
    out["_base_order"] = range(len(out))
    out["action_label"] = out.apply(lambda row: feedback_action_label(row, watch_type), axis=1)
    lookups = out["action_label"].apply(lambda action: feedback_score_lookup(summary, watch_type, action))
    out["feedback_score"] = [score for score, _, _ in lookups]
    out["feedback_label"] = [label for _, label, _ in lookups]
    out["feedback_pl_ratio"] = [pl_ratio for _, _, pl_ratio in lookups]
    out = out.sort_values(
        by=["feedback_score", "feedback_pl_ratio", "_base_order"],
        ascending=[False, False, True],
        kind="mergesort",
    ).reset_index(drop=True)
    return out.drop(columns=["_base_order"])


def dataframe_to_html(df: pd.DataFrame) -> str:
    return dataframe_to_html_impl(df)


def summarize_events(events_df: pd.DataFrame, horizons: List[int]) -> pd.DataFrame:
    return summarize_events_impl(events_df, horizons)



def upsert_alert_tracking(
    short_candidates: pd.DataFrame,
    midlong_candidates: pd.DataFrame,
    market_scenario: Optional[dict] = None,
) -> None:
    upsert_alert_tracking_impl(
        short_candidates,
        midlong_candidates,
        alert_track_csv=ALERT_TRACK_CSV,
        market_scenario=market_scenario,
        yf_period=CONFIG.yf_period,
        feedback_action_label=feedback_action_label,
        watch_price_plan=watch_price_plan,
        yf_download_one=yf_download_one,
    )


def run_watchlist(strat: Optional[StrategyConfig] = None) -> pd.DataFrame:
    rows: List[dict] = []
    prev_rank = load_previous_rank()
    for item in WATCHLIST:
        ticker, name, group = item["ticker"], item["name"], item["group"]
        try:
            df = get_indicator_frame(ticker, CONFIG.yf_period)
            row = detect_row(df, ticker, name, group, item["layer"], strat=strat)
            rows.append(row)
            append_stock_log(row)
            logger.debug("OK: %s %s", ticker, name)
        except Exception as exc:
            logger.exception("FAILED: %s %s -> %s", ticker, name, exc)
    if not rows:
        for fallback in [RANK_CSV, PREV_RANK_CSV]:
            if not fallback.exists():
                continue
            try:
                df = pd.read_csv(fallback)
                if not df.empty:
                    logger.warning("No fresh stock data; fallback to cached rank CSV: %s", fallback)
                    return df
            except Exception:
                continue
        raise RuntimeError("No stock data available from watchlist (and no cached daily_rank.csv to fallback).")
    return save_daily_rank(rows, prev_rank)


def run_backtest_dual() -> tuple[Optional[pd.DataFrame], Optional[pd.DataFrame]]:
    return run_backtest_dual_impl(
        backtest_enabled=CONFIG.backtest.enabled,
        signature=current_run_signature(),
        watchlist=WATCHLIST,
        backtest_period=CONFIG.backtest.period,
        lookahead_days=CONFIG.backtest.lookahead_days,
        outdir=OUTDIR,
        get_indicator_frame=get_indicator_frame,
        detect_row=detect_row,
        logger=logger,
    )


def build_daily_report_markdown(
    df_rank: pd.DataFrame,
    market_regime: dict,
    bt_steady: Optional[pd.DataFrame],
    bt_attack: Optional[pd.DataFrame],
    us_market: Optional[dict] = None,
) -> str:
    return build_daily_report_markdown_impl(
        df_rank,
        market_regime,
        bt_steady,
        bt_attack,
        us_market=us_market,
        build_market_scenario=build_market_scenario,
        layer_label=layer_label,
        build_candidate_sets=build_candidate_sets,
        build_feedback_summary=build_feedback_summary,
        watch_price_plan_text=watch_price_plan_text,
        select_special_etf_candidates=select_special_etf_candidates,
        build_special_etf_summary=build_special_etf_summary,
        special_etf_action_label=special_etf_action_label,
        select_early_gem_candidates=select_early_gem_candidates,
        early_gem_reason=early_gem_reason,
        strategy_preview_lines=strategy_preview_lines,
        config_strategy=CONFIG.strategy,
        alert_track_csv=ALERT_TRACK_CSV,
    )


def build_daily_report_html(
    df_rank: pd.DataFrame,
    market_regime: dict,
    bt_steady: Optional[pd.DataFrame],
    bt_attack: Optional[pd.DataFrame],
    us_market: Optional[dict] = None,
) -> str:
    return build_daily_report_html_impl(
        df_rank,
        market_regime,
        bt_steady,
        bt_attack,
        us_market=us_market,
        build_market_scenario=build_market_scenario,
        build_candidate_sets=build_candidate_sets,
        select_special_etf_candidates=select_special_etf_candidates,
        select_early_gem_candidates=select_early_gem_candidates,
        build_feedback_summary=build_feedback_summary,
        strategy_preview_lines=strategy_preview_lines,
        config_strategy=CONFIG.strategy,
    )


def save_reports(
    df_rank: pd.DataFrame,
    market_regime: dict,
    bt_steady: Optional[pd.DataFrame],
    bt_attack: Optional[pd.DataFrame],
    us_market: Optional[dict] = None,
) -> None:
    save_reports_impl(
        df_rank,
        market_regime,
        bt_steady,
        bt_attack,
        markdown_path=REPORT_MD,
        html_path=REPORT_HTML,
        us_market=us_market,
        build_market_scenario=build_market_scenario,
        layer_label=layer_label,
        build_candidate_sets=build_candidate_sets,
        build_feedback_summary=build_feedback_summary,
        watch_price_plan_text=watch_price_plan_text,
        select_special_etf_candidates=select_special_etf_candidates,
        build_special_etf_summary=build_special_etf_summary,
        special_etf_action_label=special_etf_action_label,
        select_early_gem_candidates=select_early_gem_candidates,
        early_gem_reason=early_gem_reason,
        strategy_preview_lines=strategy_preview_lines,
        config_strategy=CONFIG.strategy,
        alert_track_csv=ALERT_TRACK_CSV,
    )


def build_portfolio_report_markdown(df_rank: pd.DataFrame, market_regime: dict, us_market: dict) -> str:
    return build_portfolio_report_markdown_impl(
        df_rank,
        market_regime,
        us_market,
        build_portfolio_review_df=build_portfolio_review_df,
        build_market_scenario=build_market_scenario,
        realtime_quote_interval=REALTIME_QUOTE_INTERVAL,
        realtime_quotes_enabled=realtime_quotes_enabled(),
        auto_added_tickers=AUTO_ADDED_TICKERS,
        volatility_badge_text=volatility_badge_text,
    )


def build_portfolio_report_html(df_rank: pd.DataFrame, market_regime: dict, us_market: dict) -> str:
    return build_portfolio_report_html_impl(
        df_rank,
        market_regime,
        us_market,
        build_portfolio_review_df=build_portfolio_review_df,
        build_market_scenario=build_market_scenario,
        auto_added_tickers=AUTO_ADDED_TICKERS,
    )


def save_portfolio_reports(df_rank: pd.DataFrame, market_regime: dict, us_market: dict) -> None:
    save_portfolio_reports_impl(
        df_rank,
        market_regime,
        us_market,
        markdown_path=PORTFOLIO_REPORT_MD,
        html_path=PORTFOLIO_REPORT_HTML,
        build_portfolio_review_df=build_portfolio_review_df,
        build_market_scenario=build_market_scenario,
        realtime_quote_interval=REALTIME_QUOTE_INTERVAL,
        realtime_quotes_enabled=realtime_quotes_enabled(),
        auto_added_tickers=AUTO_ADDED_TICKERS,
        volatility_badge_text=volatility_badge_text,
    )


def split_message(text: str, limit: int) -> List[str]:
    if len(text) <= limit:
        return [text]
    chunks, current = [], []
    for line in text.splitlines():
        candidate = "\n".join(current + [line]).strip()
        if len(candidate) > limit and current:
            chunks.append("\n".join(current).strip())
            current = [line]
        else:
            current.append(line)
    if current:
        chunks.append("\n".join(current).strip())
    return chunks


def send_telegram_message(message: str) -> None:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_IDS:
        logger.warning("Telegram not configured. Skip notification.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for part in split_message(message, CONFIG.max_message_length):
        for chat_id in TELEGRAM_CHAT_IDS:
            try:
                resp = HTTP.post(url, json={"chat_id": chat_id, "text": part}, timeout=HTTP_TIMEOUT)
                if not resp.ok:
                    logger.error("Telegram send failed. chat_id=%s status=%s body=%s", chat_id, resp.status_code, resp.text[:500])
            except Exception as exc:
                    logger.exception("Telegram send exception for chat_id=%s: %s", chat_id, exc)


def parse_cli_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the daily stock watch workflow.")
    parser.add_argument("--force", action="store_true", help="Ignore same-day duplicate guard and force a rerun.")
    return parser.parse_args(argv)


def main(*, force_run: bool | None = None) -> int:
    main_started = time.perf_counter()
    effective_force_run = FORCE_RUN if force_run is None else bool(force_run)
    for key in _CACHE_STATS:
        _CACHE_STATS[key] = 0
    step_timings: dict[str, float] = {}
    warnings: list[str] = []
    try:
        if (
            not effective_force_run
            and load_last_success_date() == today_local_str()
            and load_last_success_signature() == current_run_signature()
        ):
            logger.info("Already completed successfully for %s with same code/config. Skip duplicate run.", today_local_str())
            return 0

        try:
            market_regime = _timed_call(step_timings, "market_regime", get_market_regime)
        except Exception as exc:
            warnings.append(f"market_regime: {exc}")
            logger.exception("Market regime fetch failed (best effort): %s", exc)
            market_regime = {"comment": "加權指數資料抓不到（best effort）", "is_bullish": True}

        try:
            us_market = _timed_call(step_timings, "us_market", get_us_market_reference)
        except Exception as exc:
            warnings.append(f"us_market: {exc}")
            logger.exception("US market reference failed (best effort): %s", exc)
            us_market = {"summary": "美股參考暫時抓不到（best effort）。", "rows": []}

        initial_scenario = build_market_scenario(market_regime, us_market)
        adjusted_strat = adjust_strategy_by_scenario(CONFIG.strategy, initial_scenario)
        _timed_call(step_timings, "cache_warmup", prewarm_watchlist_indicator_cache)
        df_rank = _timed_call(step_timings, "watchlist", run_watchlist, strat=adjusted_strat)
        bt_steady, bt_attack = _timed_call(step_timings, "backtest", run_backtest_dual)

        logger.info("=== 今日排行榜 ===\n%s", df_rank.to_string(index=False))
        logger.info("Market regime: %s", market_regime["comment"])

        short_candidates, short_backups, midlong_candidates, _ = _timed_call(
            step_timings,
            "candidate_sets",
            build_candidate_sets,
            df_rank,
            market_regime,
            us_market,
        )
        market_scenario = build_market_scenario(market_regime, us_market, df_rank)
        _timed_call(step_timings, "reports", save_reports, df_rank, market_regime, bt_steady, bt_attack, us_market)
        try:
            _timed_call(
                step_timings,
                "alert_tracking",
                upsert_alert_tracking,
                short_candidates,
                midlong_candidates,
                market_scenario,
            )
        except Exception as exc:
            warnings.append(f"alert_tracking: {exc}")
            logger.exception("Alert tracking update failed (best effort): %s", exc)
        logger.info(
            "Adaptive strategy applied (%s): %s",
            initial_scenario["label"],
            " | ".join(line.removeprefix("- ") for line in strategy_preview_lines(CONFIG.strategy, initial_scenario)),
        )

        current_state = build_state(df_rank, market_regime)
        last_state = load_last_state()

        should_send = _timed_call(
            step_timings,
            "should_alert",
            should_alert,
            df_rank,
            current_state,
            last_state,
            market_regime,
            us_market,
        )
        if should_send:
            def _send_notifications() -> None:
                send_telegram_message(build_macro_message(market_regime, us_market, df_rank))
                send_telegram_message(build_short_term_message(df_rank, market_regime, us_market))
                send_telegram_message(build_early_gem_message(df_rank, market_regime, us_market))
                send_telegram_message(build_midlong_message(df_rank, market_regime, us_market))

            _timed_call(step_timings, "notifications", _send_notifications)
            logger.info("Notification sent.")
        else:
            logger.info("No notification sent.")

        _timed_call(step_timings, "persist_state", save_last_state, current_state)
        _timed_call(step_timings, "persist_success", save_last_success_date, today_local_str())
        write_runtime_metrics(
            status="ok",
            step_timings=step_timings,
            warnings=warnings,
            wall_seconds=time.perf_counter() - main_started,
        )
        logger.info("Runtime timings: %s", ", ".join(f"{name}={seconds:.3f}s" for name, seconds in step_timings.items()))

        if warnings:
            logger.warning("Best effort warnings: %s", " | ".join(warnings))
        return 0
    except Exception as exc:
        err_msg = f"Watchlist job failed: {exc}"
        logger.exception(err_msg)
        warnings.append(err_msg)
        write_runtime_metrics(
            status="failed",
            step_timings=step_timings,
            warnings=warnings,
            wall_seconds=time.perf_counter() - main_started,
        )
        send_telegram_message(err_msg)
        return 1


if __name__ == "__main__":
    cli_args = parse_cli_args()
    sys.exit(main(force_run=cli_args.force if cli_args.force else None))
