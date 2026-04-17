from __future__ import annotations

import csv
import hashlib
import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import yfinance as yf
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = Path(os.getenv("CONFIG_PATH", BASE_DIR / "config.json"))
WATCHLIST_CSV = Path(os.getenv("WATCHLIST_CSV", BASE_DIR / "watchlist.csv"))
OUTDIR = Path(os.getenv("OUTDIR", BASE_DIR / "theme_watchlist_daily"))
OUTDIR.mkdir(parents=True, exist_ok=True)

RANK_CSV = OUTDIR / "daily_rank.csv"
STATE_FILE = OUTDIR / "last_rank_state.txt"
PREV_RANK_CSV = OUTDIR / "prev_daily_rank.csv"
REPORT_MD = OUTDIR / "daily_report.md"
REPORT_HTML = OUTDIR / "daily_report.html"
ALERT_TRACK_CSV = OUTDIR / "alert_tracking.csv"
FEEDBACK_SUMMARY_CSV = OUTDIR / "feedback_summary.csv"
SUCCESS_FILE = OUTDIR / "last_success_date.txt"
LOG_DIR = OUTDIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_IDS = [int(x.strip()) for x in os.getenv("TELEGRAM_CHAT_IDS", "").split(",") if x.strip()]
HTTP_TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "20"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
FORCE_RUN = os.getenv("FORCE_RUN", "").strip().lower() in {"1", "true", "yes", "y"}
LOCAL_TZ = ZoneInfo(os.getenv("LOCAL_TZ", "Asia/Taipei"))


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


def load_config(path: Path) -> AppConfig:
    raw = json.loads(path.read_text(encoding="utf-8"))
    notify_raw = raw["notify"]
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


WATCHLIST = load_watchlist(WATCHLIST_CSV)
SPECIAL_ETF_TICKERS = [
    "00772B.TWO",
    "00773B.TWO",
    "0050.TW",
    "00878.TW",
]
SCHEDULE_TARGET_TIMES = ["08:37", "08:52"]


def yf_download_one(ticker: str, period: str) -> pd.DataFrame:
    df = yf.download(
        ticker, period=period, interval="1d",
        auto_adjust=True, progress=False, threads=False,
    )
    if df.empty:
        raise ValueError(f"No data returned for {ticker}")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.title)
    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna().copy()
    if len(df) < 250:
        raise ValueError(f"Insufficient history for {ticker}: {len(df)} rows")
    return df


def add_indicators(df: pd.DataFrame, ma_period: int = 20) -> pd.DataFrame:
    out = df.copy()
    for n in [5, 10, 20, 60, 120, 250]:
        out[f"MA{n}"] = out["Close"].rolling(n).mean()

    out["AvgVol20"] = out["Volume"].rolling(20).mean()
    out["Ret1D"] = out["Close"].pct_change(1)
    out["Ret5D"] = out["Close"].pct_change(5)
    out["Ret10D"] = out["Close"].pct_change(10)
    out["Ret20D"] = out["Close"].pct_change(20)

    out["High120D"] = out["Close"].rolling(120).max()
    out["High250D"] = out["Close"].rolling(250).max()
    out["Low250D"] = out["Close"].rolling(250).min()

    out["Drawdown120D"] = out["Close"] / out["High120D"] - 1.0
    out["Range20"] = (
        out["High"].rolling(20).max() - out["Low"].rolling(20).min()
    ) / out["Close"]
    out["DistToLow250"] = out["Close"] / out["Low250D"] - 1.0
    out["VolumeRatio20"] = out["Volume"] / out["AvgVol20"]

    if ma_period not in [5, 10, 20, 60, 120, 250]:
        out[f"MA{ma_period}"] = out["Close"].rolling(ma_period).mean()
    return out


def apply_group_weight(base_score: int, group: str) -> int:
    score = base_score
    if group == "theme":
        score += CONFIG.group_weights.theme_bonus
    elif group == "core":
        score -= CONFIG.group_weights.core_penalty
    elif group == "etf":
        score -= CONFIG.group_weights.etf_penalty
    return max(score, 0)


def score_band(setup_score: int, risk_score: int) -> str:
    if risk_score >= 6:
        return "高風險追價區"
    if setup_score >= 8:
        return "進攻優勢區"
    if setup_score >= 6:
        return "偏強可追蹤"
    if setup_score >= 4:
        return "開始轉強"
    return "一般觀察"


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
    score = 0
    if ret5_pct >= 15:
        score += 2
    if ret5_pct >= 25:
        score += 1
    if ret20_pct >= 30:
        score += 2
    if volume_ratio20 >= 1.8:
        score += 1
    if volume_ratio20 >= 2.5:
        score += 1
    if bias20_pct >= 12:
        score += 2
    if risk_score >= 5:
        score += 1
    if "TREND" not in signals and "REBREAK" not in signals and ret5_pct >= 15:
        score += 1

    if "TREND" in signals:
        score -= 1
    if "REBREAK" in signals:
        score -= 1
    if group in {"core", "etf"}:
        score -= 1

    return max(score, 0)


def speculative_risk_label(score: int) -> str:
    if score >= 6:
        return "疑似炒作風險高"
    if score >= 3:
        return "投機偏高"
    return "正常"


def detect_row(df: pd.DataFrame, ticker: str, name: str, group: str, layer: str) -> dict:
    x = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else x

    close_ = float(x["Close"])
    volume = float(x["Volume"])
    avg_vol20 = float(x["AvgVol20"]) if pd.notna(x["AvgVol20"]) else 0.0
    vol_ratio20 = float(x["VolumeRatio20"]) if pd.notna(x["VolumeRatio20"]) else 0.0

    ma20 = float(x["MA20"]) if pd.notna(x["MA20"]) else None
    ma60 = float(x["MA60"]) if pd.notna(x["MA60"]) else None
    ma120 = float(x["MA120"]) if pd.notna(x["MA120"]) else None
    low250 = float(x["Low250D"]) if pd.notna(x["Low250D"]) else None

    ret1 = float(x["Ret1D"]) if pd.notna(x["Ret1D"]) else 0.0
    ret5 = float(x["Ret5D"]) if pd.notna(x["Ret5D"]) else 0.0
    ret10 = float(x["Ret10D"]) if pd.notna(x["Ret10D"]) else 0.0
    ret20 = float(x["Ret20D"]) if pd.notna(x["Ret20D"]) else 0.0

    drawdown120 = float(x["Drawdown120D"]) if pd.notna(x["Drawdown120D"]) else 0.0
    range20 = float(x["Range20"]) if pd.notna(x["Range20"]) else 999.0
    dist_low250 = float(x["DistToLow250"]) if pd.notna(x["DistToLow250"]) else 999.0

    base_signal = bool(
        low250 is not None
        and close_ <= low250 * 1.20
        and avg_vol20 > 0
        and volume < avg_vol20
        and range20 < 0.15
    )
    rebreak_signal = bool(
        ma20 is not None and ma60 is not None and avg_vol20 > 0
        and close_ > ma20 and close_ > ma60
        and vol_ratio20 > 1.35
        and pd.notna(prev.get("MA20"))
        and float(prev["Close"]) <= float(prev["MA20"])
    )
    surge_signal = bool(ret20 > 0.22 and vol_ratio20 > 1.55)
    trend_signal = bool(
        ma20 is not None and ma60 is not None
        and close_ > ma20 and ma20 > ma60 and ret20 > 0.08
    )
    accel_signal = bool(
        (ret5 > 0.08 and vol_ratio20 > 1.3 and ret20 > 0)
        or (ret10 > 0.12 and vol_ratio20 > 1.2 and ret20 > 0)
    )
    pullback_signal = bool(drawdown120 <= -0.20)

    setup_score = 0
    if low250 is not None and close_ <= low250 * 1.20:
        setup_score += 2
    elif low250 is not None and close_ <= low250 * 1.35:
        setup_score += 1

    if avg_vol20 > 0 and volume < avg_vol20:
        setup_score += 1
    if range20 < 0.15:
        setup_score += 1
    if range20 < 0.10:
        setup_score += 1
    if ma20 is not None and close_ > ma20:
        setup_score += 1
    if ma60 is not None and close_ > ma60:
        setup_score += 2

    if vol_ratio20 > 1.5:
        setup_score += 2
    elif vol_ratio20 > 1.2:
        setup_score += 1

    if dist_low250 < 0.25 and ret20 > 0.10:
        setup_score += 1
    if ret20 > 0.12:
        setup_score += 1
    if rebreak_signal:
        setup_score += 1
    if surge_signal:
        setup_score += 1
    if trend_signal:
        setup_score += 1

    # v2.2: 進攻優化
    if ret5 > 0.08:
        setup_score += 2
    elif ret5 > 0.04:
        setup_score += 1

    if vol_ratio20 > 1.5:
        setup_score += 1

    if group == "theme" and ret5 > 0.06:
        setup_score += 2
    elif group == "satellite" and ret5 > 0.06:
        setup_score += 1

    risk_score = 0
    if ret5 > 0.18:
        risk_score += 2
    if ret20 > 0.30:
        risk_score += 2
    if ret20 > 0.50:
        risk_score += 2
    if vol_ratio20 > 2.5:
        risk_score += 2
    elif vol_ratio20 > 1.8:
        risk_score += 1
    if drawdown120 > -0.05:
        risk_score += 1

    if ma20 is not None and ma20 > 0:
        bias20 = close_ / ma20 - 1.0
        if bias20 > 0.15:
            risk_score += 2
        elif bias20 > 0.08:
            risk_score += 1
    else:
        bias20 = 0.0

    setup_score = apply_group_weight(setup_score, group)

    signals = []
    if base_signal:
        signals.append("BASE")
    if rebreak_signal:
        signals.append("REBREAK")
    if surge_signal:
        signals.append("SURGE")
    if trend_signal:
        signals.append("TREND")
    if accel_signal:
        signals.append("ACCEL")
    if pullback_signal:
        signals.append("PULLBACK")

    if risk_score >= 6:
        regime = "有點過熱，別硬追"
    elif surge_signal:
        regime = "題材正在發酵"
    elif rebreak_signal:
        regime = "重新站上來了"
    elif accel_signal:
        regime = "轉強速度有出來"
    elif trend_signal:
        regime = "中段延續中"
    elif base_signal:
        regime = "低檔慢慢墊高"
    elif pullback_signal:
        regime = "高檔拉回整理"
    else:
        regime = "還在觀察"

    spec_score = speculative_risk_score(
        ret5_pct=ret5 * 100,
        ret20_pct=ret20 * 100,
        volume_ratio20=vol_ratio20,
        bias20_pct=bias20 * 100,
        risk_score=risk_score,
        signals=",".join(signals) if signals else "NONE",
        group=group,
    )

    return {
        "date": df.index[-1].strftime("%Y-%m-%d"),
        "ticker": ticker,
        "name": name,
        "group": group,
        "layer": layer,
        "close": round(close_, 2),
        "ret1_pct": round(ret1 * 100, 2),
        "ret5_pct": round(ret5 * 100, 2),
        "ret10_pct": round(ret10 * 100, 2),
        "ret20_pct": round(ret20 * 100, 2),
        "volume": int(volume),
        "avg_vol20": int(avg_vol20) if avg_vol20 else 0,
        "volume_ratio20": round(vol_ratio20, 2),
        "ma20": round(ma20, 2) if ma20 is not None else None,
        "ma60": round(ma60, 2) if ma60 is not None else None,
        "ma120": round(ma120, 2) if ma120 is not None else None,
        "drawdown120_pct": round(drawdown120 * 100, 2),
        "bias20_pct": round(bias20 * 100, 2),
        "setup_score": int(setup_score),
        "risk_score": int(risk_score),
        "signals": ",".join(signals) if signals else "NONE",
        "score_band": score_band(setup_score, risk_score),
        "regime": regime,
        "spec_risk_score": int(spec_score),
        "spec_risk_label": speculative_risk_label(spec_score),
    }


def grade_signal(row: dict) -> str:
    setup = row["setup_score"]
    risk = row["risk_score"]
    signals = row["signals"]
    ret5 = row["ret5_pct"]
    vol_ratio20 = row["volume_ratio20"]
    ret20 = row["ret20_pct"]

    if setup >= 7 and risk <= 4 and (("ACCEL" in signals) or ("REBREAK" in signals) or ("SURGE" in signals)) and ret20 > 0:
        return "A"
    if setup >= 5 and risk <= 4 and (ret5 >= 5 or vol_ratio20 >= 1.3):
        return "B"
    if risk >= 6:
        return "C"
    return "X"


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
    df = df_rank.copy()
    df["rank_change"] = 0
    df["setup_change"] = 0
    df["risk_change"] = 0
    df["status_change"] = "NEW"

    if prev_rank is None or prev_rank.empty:
        return df

    prev = prev_rank.copy()
    prev["ticker"] = prev["ticker"].astype(str)
    prev = prev.set_index("ticker")

    for i, row in df.iterrows():
        ticker = str(row["ticker"])
        if ticker in prev.index:
            old = prev.loc[ticker]
            old_rank = int(old["rank"]) if pd.notna(old["rank"]) else 0
            old_setup = int(old["setup_score"]) if pd.notna(old["setup_score"]) else 0
            old_risk = int(old["risk_score"]) if pd.notna(old["risk_score"]) else 0
            df.at[i, "rank_change"] = old_rank - int(row["rank"])
            df.at[i, "setup_change"] = int(row["setup_score"]) - old_setup
            df.at[i, "risk_change"] = int(row["risk_score"]) - old_risk
            if df.at[i, "setup_change"] > 0 or df.at[i, "rank_change"] > 0:
                df.at[i, "status_change"] = "UP"
            elif df.at[i, "setup_change"] < 0 or df.at[i, "rank_change"] < 0:
                df.at[i, "status_change"] = "DOWN"
            else:
                df.at[i, "status_change"] = "FLAT"
    return df


def save_daily_rank(rows: List[dict], prev_rank: Optional[pd.DataFrame]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df["grade"] = df.apply(lambda r: grade_signal(r.to_dict()), axis=1)
    # v2.2 排名更偏動能
    df = df.sort_values(
        by=["setup_score", "ret5_pct", "volume_ratio20", "ret20_pct", "risk_score"],
        ascending=[False, False, False, False, True],
    ).reset_index(drop=True)
    df.insert(0, "rank", range(1, len(df) + 1))
    df = enrich_rank_changes(df, prev_rank)
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
        f"觸發來源：{trigger}",
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


def get_market_regime() -> dict:
    if not CONFIG.market_filter.enabled:
        return {"enabled": False, "is_bullish": True, "comment": "大盤濾網關掉"}

    df = yf_download_one(CONFIG.market_filter.ticker, CONFIG.yf_period)
    df = add_indicators(df, CONFIG.market_filter.ma_period)
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
            df = yf_download_one(ticker, CONFIG.yf_period)
            df = add_indicators(df)
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


def select_short_term_candidates(df_rank: pd.DataFrame) -> pd.DataFrame:
    rule = CONFIG.notify
    df = rank_short_term_pool(df_rank)
    if df.empty:
        return df
    buyable_mask = df.apply(is_short_term_buyable, axis=1)
    return apply_feedback_adjustment(df[buyable_mask].copy(), "short").head(rule.top_n_short).copy()


def select_short_term_backup_candidates(df_rank: pd.DataFrame, exclude_tickers: Optional[set[str]] = None) -> pd.DataFrame:
    df = rank_short_term_pool(df_rank)
    if not df.empty:
        buyable_mask = df.apply(is_short_term_buyable, axis=1)
        df = df[~buyable_mask].copy()
    if exclude_tickers:
        df = df[~df["ticker"].astype(str).isin(exclude_tickers)].copy()
    return apply_feedback_adjustment(df.copy(), "short").head(5).copy()


def select_midlong_candidates(df_rank: pd.DataFrame, exclude_tickers: Optional[set[str]] = None) -> pd.DataFrame:
    rule = CONFIG.notify
    df = rank_midlong_pool(df_rank)
    if not df.empty:
        buyable_mask = df.apply(is_midlong_buyable, axis=1)
        df = df[buyable_mask].copy()
    if exclude_tickers:
        df = df[~df["ticker"].astype(str).isin(exclude_tickers)].copy()
    return apply_feedback_adjustment(df.copy(), "midlong").head(rule.top_n_midlong).copy()


def select_midlong_backup_candidates(df_rank: pd.DataFrame, exclude_tickers: Optional[set[str]] = None) -> pd.DataFrame:
    df = rank_midlong_pool(df_rank)
    if not df.empty:
        buyable_mask = df.apply(is_midlong_buyable, axis=1)
        df = df[~buyable_mask].copy()
    if exclude_tickers:
        df = df[~df["ticker"].astype(str).isin(exclude_tickers)].copy()
    return apply_feedback_adjustment(df.copy(), "midlong").head(5).copy()


def select_push_candidates(df_rank: pd.DataFrame) -> pd.DataFrame:
    short_candidates = select_short_term_candidates(df_rank)
    midlong_candidates = select_midlong_candidates(df_rank)
    return pd.concat([short_candidates, midlong_candidates], ignore_index=True)


def build_candidate_sets(df_rank: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    short_candidates = select_short_term_candidates(df_rank)
    short_backups = select_short_term_backup_candidates(
        df_rank,
        exclude_tickers=set(short_candidates["ticker"].astype(str)),
    )
    midlong_candidates = select_midlong_candidates(df_rank)
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
    if "ACCEL" in signals and vol_ratio >= 1.3 and ret5 <= 12:
        return "可追"
    if ret5 >= 10:
        return "等拉回"
    if row["setup_change"] > 0 or row["rank_change"] > 0:
        return "續抱觀察"
    return "續追蹤"


def is_short_term_buyable(row: pd.Series) -> bool:
    return short_term_action_label(row) in {"可追", "等拉回"}


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
        lines.append(
            f"{int(r['rank'])}. {r['name']} ({r['ticker']}) | "
            f"{layer_label(r['layer'])} | 5日 {r['ret5_pct']}% / 20日 {r['ret20_pct']}% | "
            f"{early_gem_reason(r)}"
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


def should_alert(df_rank: pd.DataFrame, current_state: str, last_state: str, market_regime: dict) -> bool:
    if CONFIG.always_notify:
        return True
    if current_state == last_state:
        return False
    short_candidates, short_backups, midlong_candidates, midlong_backups = build_candidate_sets(df_rank)
    feedback_summary = build_feedback_summary()
    feedback_summary = build_feedback_summary()
    candidates = pd.concat([short_candidates, short_backups, midlong_candidates, midlong_backups], ignore_index=True)
    if candidates.empty:
        return False
    if market_regime.get("is_bullish", True):
        return True
    if CONFIG.market_filter.allow_a_grade_even_if_weak and (candidates["grade"] == "A").any():
        return True
    return False


def build_short_term_message(df_rank: pd.DataFrame, market_regime: dict, us_market: dict) -> str:
    short_candidates, short_backups, _, _ = build_candidate_sets(df_rank)
    total_a = int((df_rank["grade"] == "A").sum()) if not df_rank.empty else 0
    total_b = int((df_rank["grade"] == "B").sum()) if not df_rank.empty else 0
    total_up = int((df_rank["status_change"] == "UP").sum()) if "status_change" in df_rank.columns else 0

    lines = [
        "📣 短線可買",
    ]
    summary_parts = [f"A級 {total_a} 檔", f"B級 {total_b} 檔", f"轉強 {total_up} 檔"]
    lines.append(" / ".join(summary_parts))
    if short_candidates.empty:
        lines.append("今天短線沒有夠清楚的可買標的，先等。")
        return "\n".join(lines)

    lines.append("")
    lines.append("解讀：這一區只放今天相對可考慮出手的短線標的；太熱或只適合續看的，會放到短線觀察。")
    lines.append("")
    for _, r in short_candidates.iterrows():
        action = short_term_action_label(r)
        lines.append(
            f"{int(r['rank'])}. {r['name']} ({r['ticker']}) {action} | "
            f"5日 {r['ret5_pct']}% / 量比 {r['volume_ratio20']} | "
            f"{r['regime']}"
        )
    if not short_backups.empty:
        lines.append("")
        lines.append("短線觀察 (最多5檔)")
        for _, r in short_backups.iterrows():
            action = short_term_action_label(r)
            lines.append(
                f"{int(r['rank'])}. {r['name']} ({r['ticker']}) {action} | "
                f"5日 {r['ret5_pct']}% / 量比 {r['volume_ratio20']} | "
                f"{r['regime']}"
            )
    return "\n".join(lines).strip()


def build_midlong_message(df_rank: pd.DataFrame, market_regime: dict, us_market: dict) -> str:
    _, _, midlong_candidates, midlong_backups = build_candidate_sets(df_rank)
    total_b = int((df_rank["grade"] == "B").sum()) if not df_rank.empty else 0
    lines = [
        "📣 中長線可布局",
        f"B級結構股 {total_b} 檔",
    ]
    if midlong_candidates.empty:
        lines.append("今天中長線沒有夠穩、夠適合布局的標的，先觀察。")
        return "\n".join(lines)

    lines.append("")
    lines.append("解讀：這一區偏向可布局的趨勢股；強但不一定適合現在進場的，會放到中長線觀察。")
    lines.append("")
    for _, r in midlong_candidates.iterrows():
        action = midlong_action_label(r)
        lines.append(
            f"{int(r['rank'])}. {r['name']} ({r['ticker']}) {action} | "
            f"20日 {r['ret20_pct']}% / 量比 {r['volume_ratio20']} | "
            f"{r['regime']}"
        )
    if not midlong_backups.empty:
        lines.append("")
        lines.append("中長線觀察 (最多5檔)")
        for _, r in midlong_backups.iterrows():
            action = midlong_action_label(r)
            lines.append(
                f"{int(r['rank'])}. {r['name']} ({r['ticker']}) {action} | "
                f"20日 {r['ret20_pct']}% / 量比 {r['volume_ratio20']} | "
                f"{r['regime']}"
            )
    return "\n".join(lines).strip()


def build_macro_message(market_regime: dict, us_market: dict) -> str:
    lines = [
        "📣 大盤 / 美股摘要",
        market_regime["comment"],
        us_market["summary"],
    ]
    lines.extend(runtime_context_lines())
    if us_market.get("tech_bias"):
        lines.append(us_market["tech_bias"])
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
        subset["target_return"] = subset.apply(lambda r: history_target_return(r)[0], axis=1)
        subset = subset[subset["target_return"].notna()].copy()
        if subset.empty:
            continue

        for action_label in ["__all__"] + sorted(set(subset["action_label"].astype(str))):
            action_df = subset if action_label == "__all__" else subset[subset["action_label"].astype(str) == action_label].copy()
            if action_df.empty:
                continue
            samples = int(action_df.shape[0])
            win_rate_pct = round(float(action_df["target_return"].gt(0).mean()) * 100, 2)
            avg_return_pct = round(float(action_df["target_return"].mean()), 2)
            shrink = min(samples / 8.0, 1.0)
            feedback_score = round((((win_rate_pct - 50.0) / 10.0) + (avg_return_pct / 5.0)) * shrink, 2)
            rows.append(
                {
                    "watch_type": watch_type,
                    "action_label": action_label,
                    "samples": samples,
                    "win_rate_pct": win_rate_pct,
                    "avg_return_pct": avg_return_pct,
                    "feedback_score": feedback_score,
                    "feedback_label": feedback_label_from_score(feedback_score, samples),
                }
            )
    summary = pd.DataFrame(rows)
    if not summary.empty:
        summary.to_csv(FEEDBACK_SUMMARY_CSV, index=False, encoding="utf-8-sig")
    return summary


def feedback_score_lookup(summary: pd.DataFrame, watch_type: str, action_label: str) -> tuple[float, str]:
    if summary is None or summary.empty:
        return 0.0, "樣本不足"
    exact = summary[
        (summary["watch_type"].astype(str) == watch_type)
        & (summary["action_label"].astype(str) == action_label)
    ]
    if not exact.empty:
        row = exact.iloc[0]
        return float(row["feedback_score"]), str(row["feedback_label"])
    fallback = summary[
        (summary["watch_type"].astype(str) == watch_type)
        & (summary["action_label"].astype(str) == "__all__")
    ]
    if not fallback.empty:
        row = fallback.iloc[0]
        return float(row["feedback_score"]), str(row["feedback_label"])
    return 0.0, "樣本不足"


def apply_feedback_adjustment(df: pd.DataFrame, watch_type: str) -> pd.DataFrame:
    if df.empty:
        return df
    summary = build_feedback_summary()
    out = df.copy().reset_index(drop=True)
    out["_base_order"] = range(len(out))
    out["action_label"] = out.apply(lambda row: feedback_action_label(row, watch_type), axis=1)
    lookups = out["action_label"].apply(lambda action: feedback_score_lookup(summary, watch_type, action))
    out["feedback_score"] = [score for score, _ in lookups]
    out["feedback_label"] = [label for _, label in lookups]
    out = out.sort_values(
        by=["feedback_score", "_base_order"],
        ascending=[False, True],
        kind="mergesort",
    ).reset_index(drop=True)
    return out.drop(columns=["_base_order"])


def dataframe_to_html(df: pd.DataFrame) -> str:
    return df.to_html(index=False, border=0, justify="center")


def summarize_events(events_df: pd.DataFrame, horizons: List[int]) -> pd.DataFrame:
    rows = []
    for horizon in horizons:
        col = f"ret_{horizon}d"
        s = events_df[col].dropna()
        if s.empty:
            continue
        rows.append({
            "horizon": horizon,
            "trades": int(s.shape[0]),
            "win_rate_pct": round((s.gt(0).mean()) * 100, 2),
            "avg_return_pct": round(s.mean(), 2),
            "median_return_pct": round(s.median(), 2),
        })
    return pd.DataFrame(rows)



def upsert_alert_tracking(short_candidates: pd.DataFrame, midlong_candidates: pd.DataFrame) -> None:
    cols = [
        "alert_date", "watch_type", "ticker", "name", "group", "grade", "rank", "setup_score", "risk_score",
        "layer", "signals", "regime", "action_label", "feedback_score", "feedback_label",
        "alert_close", "ret1_future_pct", "ret5_future_pct", "ret20_future_pct", "status"
    ]

    if ALERT_TRACK_CSV.exists():
        try:
            hist = pd.read_csv(ALERT_TRACK_CSV)
        except Exception:
            hist = pd.DataFrame(columns=cols)
    else:
        hist = pd.DataFrame(columns=cols)

    candidate_groups = [
        ("short", short_candidates),
        ("midlong", midlong_candidates),
    ]

    for watch_type, candidates in candidate_groups:
        if candidates is None or candidates.empty:
            continue
        for _, r in candidates.iterrows():
            alert_date = str(r["date"])
            mask = (
                (hist.get("alert_date", pd.Series(dtype=str)).astype(str) == alert_date)
                & (hist.get("watch_type", pd.Series(dtype=str)).astype(str) == watch_type)
                & (hist.get("ticker", pd.Series(dtype=str)).astype(str) == str(r["ticker"]))
            )
            row = {
                "alert_date": alert_date,
                "watch_type": watch_type,
                "ticker": r["ticker"],
                "name": r["name"],
                "group": r["group"],
                "layer": r.get("layer", ""),
                "grade": r["grade"],
                "rank": int(r["rank"]),
                "setup_score": int(r["setup_score"]),
                "risk_score": int(r["risk_score"]),
                "signals": r["signals"],
                "regime": r["regime"],
                "action_label": feedback_action_label(r, watch_type),
                "feedback_score": float(r.get("feedback_score", 0.0)),
                "feedback_label": str(r.get("feedback_label", "樣本不足")),
                "alert_close": float(r["close"]),
                "ret1_future_pct": None,
                "ret5_future_pct": None,
                "ret20_future_pct": None,
                "status": "OPEN",
            }
            if mask.any():
                hist.loc[mask, list(row.keys())] = list(row.values())
            else:
                hist = pd.concat([hist, pd.DataFrame([row])], ignore_index=True)

    if not hist.empty:
        for i, row in hist.iterrows():
            if row.get("status") == "CLOSED":
                continue
            try:
                df = yf_download_one(str(row["ticker"]), CONFIG.yf_period)
            except Exception:
                continue
            if df.empty:
                continue
            closes = df["Close"].reset_index(drop=True)
            # find alert date row by matching date index string
            idx_matches = [j for j, dt in enumerate(df.index.strftime("%Y-%m-%d")) if dt == str(row["alert_date"])]
            if not idx_matches:
                continue
            idx = idx_matches[-1]
            entry = float(closes.iloc[idx])

            if pd.isna(row.get("ret1_future_pct")) and idx + 1 < len(closes):
                hist.at[i, "ret1_future_pct"] = round((float(closes.iloc[idx + 1]) / entry - 1.0) * 100, 2)
            if pd.isna(row.get("ret5_future_pct")) and idx + 5 < len(closes):
                hist.at[i, "ret5_future_pct"] = round((float(closes.iloc[idx + 5]) / entry - 1.0) * 100, 2)
            if pd.isna(row.get("ret20_future_pct")) and idx + 20 < len(closes):
                hist.at[i, "ret20_future_pct"] = round((float(closes.iloc[idx + 20]) / entry - 1.0) * 100, 2)
                hist.at[i, "status"] = "CLOSED"

    hist.to_csv(ALERT_TRACK_CSV, index=False, encoding="utf-8-sig")


def run_watchlist() -> pd.DataFrame:
    rows: List[dict] = []
    prev_rank = load_previous_rank()
    for item in WATCHLIST:
        ticker, name, group = item["ticker"], item["name"], item["group"]
        try:
            df = yf_download_one(ticker, CONFIG.yf_period)
            df = add_indicators(df)
            row = detect_row(df, ticker, name, group, item["layer"])
            rows.append(row)
            append_stock_log(row)
            logger.info("OK: %s %s", ticker, name)
        except Exception as exc:
            logger.exception("FAILED: %s %s -> %s", ticker, name, exc)
    if not rows:
        raise RuntimeError("No stock data available from watchlist.")
    return save_daily_rank(rows, prev_rank)


def run_backtest_dual() -> tuple[Optional[pd.DataFrame], Optional[pd.DataFrame]]:
    if not CONFIG.backtest.enabled:
        return None, None

    steady_events = []
    attack_events = []
    max_horizon = max(CONFIG.backtest.lookahead_days)

    for item in WATCHLIST:
        ticker, name, group, layer = item["ticker"], item["name"], item["group"], item["layer"]
        try:
            df = yf_download_one(ticker, CONFIG.backtest.period)
            df = add_indicators(df)
            for i in range(250, len(df) - max_horizon):
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
                for horizon in CONFIG.backtest.lookahead_days:
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

        except Exception as exc:
            logger.exception("BACKTEST FAILED: %s %s -> %s", ticker, name, exc)

    steady_df = pd.DataFrame(steady_events) if steady_events else None
    attack_df = pd.DataFrame(attack_events) if attack_events else None

    steady_summary = summarize_events(steady_df, CONFIG.backtest.lookahead_days) if steady_df is not None else None
    attack_summary = summarize_events(attack_df, CONFIG.backtest.lookahead_days) if attack_df is not None else None

    if steady_df is not None:
        steady_df.to_csv(OUTDIR / "backtest_events_steady.csv", index=False, encoding="utf-8-sig")
    if attack_df is not None:
        attack_df.to_csv(OUTDIR / "backtest_events_attack.csv", index=False, encoding="utf-8-sig")
    if steady_summary is not None:
        steady_summary.to_csv(OUTDIR / "backtest_summary_steady.csv", index=False, encoding="utf-8-sig")
    if attack_summary is not None:
        attack_summary.to_csv(OUTDIR / "backtest_summary_attack.csv", index=False, encoding="utf-8-sig")

    return steady_summary, attack_summary


def build_daily_report_markdown(df_rank: pd.DataFrame, market_regime: dict, bt_steady: Optional[pd.DataFrame], bt_attack: Optional[pd.DataFrame]) -> str:
    today = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# Daily 20D v2.2 Attack Report",
        f"- Generated: {today}",
        f"- Market Regime: {market_regime['comment']}",
        "",
        "## Top Ranking",
        "",
        "| 排名 | 等級 | 股票 | 分類 | 近況 | 5日 | 20日 | 投機風險 | 重點 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for _, r in df_rank.iterrows():
        lines.append(
            f"| {int(r['rank'])} | {r['grade']} | {r['name']} ({r['ticker']}) | "
            f"{layer_label(r['layer'])} | {r['regime']} | "
            f"{r['ret5_pct']}% | {r['ret20_pct']}% | {r['spec_risk_label']} | "
            f"{r['signals']} / 量比 {r['volume_ratio20']} |"
        )

    short_candidates, short_backups, midlong_candidates, midlong_backups = build_candidate_sets(df_rank)

    lines.extend(["", "## Short-Term Candidates", ""])
    if short_candidates.empty:
        lines.append("- None")
    else:
        for _, r in short_candidates.iterrows():
            lines.append(
                f"- #{int(r['rank'])} {r['name']} {r['ticker']} [{r['group']}/{layer_label(r['layer'])}] | "
                f"setup {r['setup_score']} risk {r['risk_score']} | "
                f"5D {r['ret5_pct']}% 10D {r['ret10_pct']}% 20D {r['ret20_pct']}% | 投機 {r['spec_risk_label']} | "
                f"{r['signals']} | rankΔ {int(r['rank_change']):+d} setupΔ {int(r['setup_change']):+d}"
            )

    lines.extend(["", "## Short-Term Backups", ""])
    if short_backups.empty:
        lines.append("- None")
    else:
        for _, r in short_backups.iterrows():
            lines.append(
                f"- #{int(r['rank'])} {r['name']} {r['ticker']} [{r['group']}/{layer_label(r['layer'])}] | "
                f"setup {r['setup_score']} risk {r['risk_score']} | "
                f"5D {r['ret5_pct']}% 10D {r['ret10_pct']}% 20D {r['ret20_pct']}% | 投機 {r['spec_risk_label']} | "
                f"{r['signals']} | rankΔ {int(r['rank_change']):+d} setupΔ {int(r['setup_change']):+d}"
            )

    lines.extend(["", "## Mid-Long Candidates", ""])
    if midlong_candidates.empty:
        lines.append("- None")
    else:
        for _, r in midlong_candidates.iterrows():
            lines.append(
                f"- #{int(r['rank'])} {r['name']} {r['ticker']} [{r['group']}/{layer_label(r['layer'])}] | "
                f"setup {r['setup_score']} risk {r['risk_score']} | "
                f"10D {r['ret10_pct']}% 20D {r['ret20_pct']}% | 投機 {r['spec_risk_label']} | "
                f"{r['signals']} | rankΔ {int(r['rank_change']):+d} setupΔ {int(r['setup_change']):+d}"
            )

    lines.extend(["", "## Mid-Long Backups", ""])
    if midlong_backups.empty:
        lines.append("- None")
    else:
        for _, r in midlong_backups.iterrows():
            lines.append(
                f"- #{int(r['rank'])} {r['name']} {r['ticker']} [{r['group']}/{layer_label(r['layer'])}] | "
                f"setup {r['setup_score']} risk {r['risk_score']} | "
                f"10D {r['ret10_pct']}% 20D {r['ret20_pct']}% | 投機 {r['spec_risk_label']} | "
                f"{r['signals']} | rankΔ {int(r['rank_change']):+d} setupΔ {int(r['setup_change']):+d}"
            )

    special_etf_candidates = select_special_etf_candidates(df_rank)
    gem_candidates = select_early_gem_candidates(df_rank)
    lines.extend(["", "## ETF / 債券觀察", ""])
    if special_etf_candidates.empty:
        lines.append("- None")
    else:
        for summary in build_special_etf_summary(special_etf_candidates):
            lines.append(f"- {summary}")
        lines.append("")
        for _, r in special_etf_candidates.iterrows():
            lines.append(
                f"- #{int(r['rank'])} {r['name']} {r['ticker']} [{r['group']}/{layer_label(r['layer'])}] | "
                f"setup {r['setup_score']} risk {r['risk_score']} | "
                f"5D {r['ret5_pct']}% 20D {r['ret20_pct']}% | 投機 {r['spec_risk_label']} | "
                f"{r['signals']} | 操作 {special_etf_action_label(r)}"
            )

    lines.extend(["", "## Early Gem Watch", ""])
    if gem_candidates.empty:
        lines.append("- None")
    else:
        for _, r in gem_candidates.iterrows():
            lines.append(
                f"- #{int(r['rank'])} {r['name']} {r['ticker']} [{r['group']}/{layer_label(r['layer'])}] | "
                f"setup {r['setup_score']} risk {r['risk_score']} | "
                f"5D {r['ret5_pct']}% 20D {r['ret20_pct']}% | 投機 {r['spec_risk_label']} | "
                f"{r['signals']} | 理由 {early_gem_reason(r)}"
            )

    lines.extend(["", "## Prediction Feedback", ""])
    if feedback_summary.empty:
        lines.append("- None")
    else:
        lines.append("| 類型 | 操作 | 樣本 | 勝率 | 平均報酬 | 回饋分數 | 判讀 |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- |")
        for _, r in feedback_summary.iterrows():
            action_label = "整體" if str(r["action_label"]) == "__all__" else str(r["action_label"])
            watch_type = "短線" if str(r["watch_type"]) == "short" else "中長線"
            lines.append(
                f"| {watch_type} | {action_label} | {int(r['samples'])} | {r['win_rate_pct']}% | "
                f"{r['avg_return_pct']}% | {r['feedback_score']} | {r['feedback_label']} |"
            )

    lines.extend([
        "",
        "## Grade 對照表",
        "",
        "- `A`：這檔現在最值得看，通常代表結構、量能、動能都有對上。",
        "- `B`：有在轉強，但還沒有強到非看不可，適合放進追蹤名單。",
        "- `C`：不是不能漲，而是現在風險偏高，容易追在不舒服的位置。",
        "- `X`：目前沒有足夠清楚的優勢，先觀察就好。",
        "",
        "## Signals 對照表",
        "",
        "- `BASE`：低檔整理後，股價還沒真正噴出，但看起來有在慢慢打底。",
        "- `REBREAK`：前面壓著的均線重新站上去，而且量也開始放大，常見在第二波重新轉強。",
        "- `SURGE`：這段時間漲幅已經很明顯，量也大，代表市場資金真的有在追。",
        "- `TREND`：不是突然暴衝，而是沿著趨勢穩穩往上走，比較像中段延續。",
        "- `ACCEL`：最近 5 天或 10 天速度變快，通常是剛開始被市場注意到的加速段。",
        "- `PULLBACK`：前面漲過一段後，現在在拉回整理，不一定壞，但短線不是最舒服的位置。",
        "",
        "## Regime 解釋",
        "",
        "- `有點過熱，別硬追`：漲太快、乖離太大，容易追在短線高點。",
        "- `題材正在發酵`：市場開始聚焦這檔，量價有一起上來，屬於比較有熱度的階段。",
        "- `重新站上來了`：整理過後再次轉強，這種型態常常是比較漂亮的重新發動。",
        "- `轉強速度有出來`：還不一定是最強主升段，但動能有在加速，值得盯。",
        "- `中段延續中`：這檔不是剛起漲，而是已經走在趨勢裡，偏中波段續強。",
        "- `低檔慢慢墊高`：還在打底或剛離開底部，適合先放觀察名單，不一定要急著追。",
        "- `高檔拉回整理`：先前強過，但現在進入整理區，重點是看能不能整理完再上。",
        "- `還在觀察`：目前沒有特別明確的訊號，先不用太急。",
    ])

    for title, bt in [("Steady Backtest", bt_steady), ("Attack Backtest", bt_attack)]:
        lines.extend(["", f"## {title}", ""])
        if bt is None or bt.empty:
            lines.append("- None")
        else:
            lines.append("| Horizon | Trades | Win Rate | Avg Return | Median Return |")
            lines.append("| --- | --- | --- | --- | --- |")
            for _, r in bt.iterrows():
                lines.append(
                    f"| {int(r['horizon'])}D | {int(r['trades'])} | {r['win_rate_pct']}% | "
                    f"{r['avg_return_pct']}% | {r['median_return_pct']}% |"
                )

    if ALERT_TRACK_CSV.exists():
        try:
            alert_df = pd.read_csv(ALERT_TRACK_CSV)
            if not alert_df.empty:
                recent = alert_df.tail(10)
                lines.extend(["", "## Recent Alert Tracking", ""])
                lines.append("| Alert Date | Type | Ticker | Name | Grade | 1D% | 5D% | 20D% | Status |")
                lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
                for _, r in recent.iterrows():
                    lines.append(
                        f"| {r.get('alert_date','')} | {r.get('watch_type','')} | {r.get('ticker','')} | {r.get('name','')} | {r.get('grade','')} | "
                        f"{r.get('ret1_future_pct','')} | {r.get('ret5_future_pct','')} | {r.get('ret20_future_pct','')} | {r.get('status','')} |"
                    )
        except Exception:
            pass

    return "\n".join(lines)


def build_daily_report_html(df_rank: pd.DataFrame, market_regime: dict, bt_steady: Optional[pd.DataFrame], bt_attack: Optional[pd.DataFrame]) -> str:
    steady_html = "<p>None</p>" if bt_steady is None or bt_steady.empty else dataframe_to_html(bt_steady)
    attack_html = "<p>None</p>" if bt_attack is None or bt_attack.empty else dataframe_to_html(bt_attack)
    short_candidates, short_backups, midlong_candidates, midlong_backups = build_candidate_sets(df_rank)
    short_html = "<p>None</p>" if short_candidates.empty else dataframe_to_html(short_candidates)
    short_backup_html = "<p>None</p>" if short_backups.empty else dataframe_to_html(short_backups)
    midlong_html = "<p>None</p>" if midlong_candidates.empty else dataframe_to_html(midlong_candidates)
    midlong_backup_html = "<p>None</p>" if midlong_backups.empty else dataframe_to_html(midlong_backups)
    special_etf_candidates = select_special_etf_candidates(df_rank)
    special_etf_html = "<p>None</p>" if special_etf_candidates.empty else dataframe_to_html(special_etf_candidates)
    gem_candidates = select_early_gem_candidates(df_rank)
    gem_html = "<p>None</p>" if gem_candidates.empty else dataframe_to_html(gem_candidates)
    feedback_summary = build_feedback_summary()
    feedback_html = "<p>None</p>" if feedback_summary.empty else dataframe_to_html(feedback_summary)
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Daily 20D v2.2 Attack Report</title>
<style>
body {{ font-family: Arial, sans-serif; margin: 24px; }}
table {{ border-collapse: collapse; width: 100%; margin-bottom: 24px; }}
th, td {{ border: 1px solid #ddd; padding: 8px; font-size: 14px; }}
th {{ background: #f4f4f4; }}
</style></head><body>
<h1>Daily 20D v2.2 Attack Report</h1>
<p><strong>Market:</strong> {market_regime['comment']}</p>
<h2>Top Ranking</h2>{dataframe_to_html(df_rank)}
<h2>Short-Term Candidates</h2>{short_html}
<h2>Short-Term Backups</h2>{short_backup_html}
<h2>Mid-Long Candidates</h2>{midlong_html}
<h2>Mid-Long Backups</h2>{midlong_backup_html}
<h2>ETF / 債券觀察</h2>{special_etf_html}
<h2>Early Gem Watch</h2>{gem_html}
<h2>Prediction Feedback</h2>{feedback_html}
<h2>Steady Backtest</h2>{steady_html}
<h2>Attack Backtest</h2>{attack_html}
</body></html>"""


def save_reports(df_rank: pd.DataFrame, market_regime: dict, bt_steady: Optional[pd.DataFrame], bt_attack: Optional[pd.DataFrame]) -> None:
    REPORT_MD.write_text(build_daily_report_markdown(df_rank, market_regime, bt_steady, bt_attack), encoding="utf-8")
    REPORT_HTML.write_text(build_daily_report_html(df_rank, market_regime, bt_steady, bt_attack), encoding="utf-8")


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


def main() -> int:
    try:
        if (
            not FORCE_RUN
            and load_last_success_date() == today_local_str()
            and load_last_success_signature() == current_run_signature()
        ):
            logger.info("Already completed successfully for %s with same code/config. Skip duplicate run.", today_local_str())
            return 0

        market_regime = get_market_regime()
        us_market = get_us_market_reference()
        df_rank = run_watchlist()
        bt_steady, bt_attack = run_backtest_dual()

        logger.info("=== 今日排行榜 ===\n%s", df_rank.to_string(index=False))
        logger.info("Market regime: %s", market_regime["comment"])

        short_candidates, short_backups, midlong_candidates, _ = build_candidate_sets(df_rank)
        upsert_alert_tracking(short_candidates, midlong_candidates)
        save_reports(df_rank, market_regime, bt_steady, bt_attack)

        current_state = build_state(df_rank, market_regime)
        last_state = load_last_state()

        if should_alert(df_rank, current_state, last_state, market_regime):
            send_telegram_message(build_macro_message(market_regime, us_market))
            send_telegram_message(build_short_term_message(df_rank, market_regime, us_market))
            send_telegram_message(build_early_gem_message(df_rank, market_regime, us_market))
            send_telegram_message(build_midlong_message(df_rank, market_regime, us_market))
            send_telegram_message(build_special_etf_message(df_rank, market_regime, us_market))
            logger.info("Notification sent.")
        else:
            logger.info("No notification sent.")

        save_last_state(current_state)
        save_last_success_date(today_local_str())
        return 0
    except Exception as exc:
        err_msg = f"Watchlist job failed: {exc}"
        logger.exception(err_msg)
        send_telegram_message(err_msg)
        return 1


if __name__ == "__main__":
    sys.exit(main())
