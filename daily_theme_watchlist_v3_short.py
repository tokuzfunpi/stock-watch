from __future__ import annotations

import csv
import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import requests
import yfinance as yf
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = Path(os.getenv("CONFIG_PATH", BASE_DIR / "config_v3_short.json"))
WATCHLIST_CSV = Path(os.getenv("WATCHLIST_CSV", BASE_DIR / "watchlist_v3_short.csv"))

OUTDIR = Path(os.getenv("OUTDIR", BASE_DIR / "theme_watchlist_daily"))
OUTDIR.mkdir(parents=True, exist_ok=True)

RANK_CSV = OUTDIR / "daily_rank.csv"
STATE_FILE = OUTDIR / "last_rank_state.txt"
PREV_RANK_CSV = OUTDIR / "prev_daily_rank.csv"
REPORT_MD = OUTDIR / "daily_report.md"
REPORT_HTML = OUTDIR / "daily_report.html"
LOG_DIR = OUTDIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_IDS = [
    int(x.strip()) for x in os.getenv("TELEGRAM_CHAT_IDS", "").split(",") if x.strip()
]
HTTP_TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "20"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()


@dataclass
class MarketFilter:
    enabled: bool
    ticker: str
    name: str
    ma_period: int
    min_ret20: float
    volume_ratio_min: float
    allow_when_not_bullish_if_strong_signal: bool


@dataclass
class NotificationRule:
    top_n: int
    min_setup_score: int
    max_risk_score: int
    min_ret5_pct: float
    min_volume_ratio: float
    require_any_signals: List[str]
    push_rank_improvement_at_least: int
    push_setup_change_at_least: int
    priority_groups: List[str]


@dataclass
class BacktestConfig:
    enabled: bool
    period: str
    lookahead_days: List[int]
    min_setup_score: int
    max_risk_score: int
    min_ret5_pct: float
    min_volume_ratio: float
    accepted_signals: List[str]


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
    mf = raw["market_filter"]
    nf = raw["notify"]
    bf = raw["backtest"]
    gw = raw["group_weights"]
    return AppConfig(
        yf_period=raw.get("yf_period", "2y"),
        state_enabled=bool(raw.get("state_enabled", True)),
        always_notify=bool(raw.get("always_notify", False)),
        max_message_length=int(raw.get("max_message_length", 3500)),
        watchlist_default_group=raw.get("watchlist_default_group", "theme"),
        market_filter=MarketFilter(
            enabled=bool(mf.get("enabled", True)),
            ticker=mf.get("ticker", "^TWII"),
            name=mf.get("name", "加權指數"),
            ma_period=int(mf.get("ma_period", 20)),
            min_ret20=float(mf.get("min_ret20", -0.05)),
            volume_ratio_min=float(mf.get("volume_ratio_min", 0.8)),
            allow_when_not_bullish_if_strong_signal=bool(
                mf.get("allow_when_not_bullish_if_strong_signal", True)
            ),
        ),
        notify=NotificationRule(
            top_n=int(nf.get("top_n", 3)),
            min_setup_score=int(nf.get("min_setup_score", 5)),
            max_risk_score=int(nf.get("max_risk_score", 5)),
            min_ret5_pct=float(nf.get("min_ret5_pct", 5.0)),
            min_volume_ratio=float(nf.get("min_volume_ratio", 1.5)),
            require_any_signals=list(nf.get("require_any_signals", ["REBREAK", "SURGE", "EARLY"])),
            push_rank_improvement_at_least=int(nf.get("push_rank_improvement_at_least", 1)),
            push_setup_change_at_least=int(nf.get("push_setup_change_at_least", 1)),
            priority_groups=list(nf.get("priority_groups", ["theme"])),
        ),
        backtest=BacktestConfig(
            enabled=bool(bf.get("enabled", True)),
            period=bf.get("period", "4y"),
            lookahead_days=list(bf.get("lookahead_days", [1, 3, 5])),
            min_setup_score=int(bf.get("min_setup_score", 5)),
            max_risk_score=int(bf.get("max_risk_score", 5)),
            min_ret5_pct=float(bf.get("min_ret5_pct", 5.0)),
            min_volume_ratio=float(bf.get("min_volume_ratio", 1.5)),
            accepted_signals=list(bf.get("accepted_signals", ["REBREAK", "SURGE", "EARLY"])),
        ),
        group_weights=GroupWeights(
            theme_bonus=int(gw.get("theme_bonus", 2)),
            core_penalty=int(gw.get("core_penalty", 1)),
            etf_penalty=int(gw.get("etf_penalty", 3)),
        ),
    )


CONFIG = load_config(CONFIG_PATH)

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("theme_watchlist_v3_short")


def build_session() -> requests.Session:
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=1.0,
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
    if not csv_path.exists():
        raise FileNotFoundError(f"watchlist csv not found: {csv_path}")

    rows: List[dict] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        required = {"ticker", "name"}
        if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
            raise ValueError("watchlist csv must contain ticker,name and optional group,enabled")

        for row in reader:
            ticker = (row.get("ticker") or "").strip()
            name = (row.get("name") or "").strip()
            group = (row.get("group") or CONFIG.watchlist_default_group).strip()
            enabled = (row.get("enabled") or "true").strip().lower()

            if not ticker or not name:
                continue
            if enabled in {"false", "0", "no", "n"}:
                continue

            rows.append({"ticker": ticker, "name": name, "group": group})

    if not rows:
        raise ValueError("No enabled symbols found in watchlist csv")
    return rows


WATCHLIST = load_watchlist(WATCHLIST_CSV)


def yf_download_one(ticker: str, period: str) -> pd.DataFrame:
    df = yf.download(
        ticker,
        period=period,
        interval="1d",
        auto_adjust=True,
        progress=False,
        threads=False,
    )
    if df.empty:
        raise ValueError(f"No data returned for {ticker}")

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = df.rename(columns=str.title)
    required_cols = ["Open", "High", "Low", "Close", "Volume"]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise ValueError(f"Missing columns for {ticker}: {missing}")

    df = df[required_cols].dropna().copy()
    if len(df) < 120:
        raise ValueError(f"Insufficient history for {ticker}: {len(df)} rows")
    return df


def add_indicators(df: pd.DataFrame, ma_period: int = 20) -> pd.DataFrame:
    out = df.copy()
    for n in [5, 10, 20, 60, 120]:
        out[f"MA{n}"] = out["Close"].rolling(n).mean()

    out["AvgVol20"] = out["Volume"].rolling(20).mean()
    out["Ret1D"] = out["Close"].pct_change(1)
    out["Ret3D"] = out["Close"].pct_change(3)
    out["Ret5D"] = out["Close"].pct_change(5)
    out["Ret10D"] = out["Close"].pct_change(10)
    out["Ret20D"] = out["Close"].pct_change(20)

    out["High20D"] = out["Close"].rolling(20).max()
    out["High60D"] = out["Close"].rolling(60).max()
    out["Low60D"] = out["Close"].rolling(60).min()

    out["Drawdown20D"] = out["Close"] / out["High20D"] - 1.0
    out["Drawdown60D"] = out["Close"] / out["High60D"] - 1.0
    out["Range10"] = (
        out["High"].rolling(10).max() - out["Low"].rolling(10).min()
    ) / out["Close"]
    out["VolumeRatio20"] = out["Volume"] / out["AvgVol20"]

    if ma_period not in [5, 10, 20, 60, 120]:
        out[f"MA{ma_period}"] = out["Close"].rolling(ma_period).mean()

    return out


def score_band(setup_score: int, risk_score: int) -> str:
    if risk_score >= 7:
        return "高風險追價區"
    if setup_score >= 8:
        return "短線強勢區"
    if setup_score >= 6:
        return "短線啟動區"
    if setup_score >= 4:
        return "開始轉強"
    return "一般觀察"


def apply_group_weight(base_score: int, group: str) -> int:
    score = base_score
    if group == "theme":
        score += CONFIG.group_weights.theme_bonus
    elif group == "core":
        score -= CONFIG.group_weights.core_penalty
    elif group == "etf":
        score -= CONFIG.group_weights.etf_penalty
    return score


def grade_signal(row: dict) -> str:
    setup = row["setup_score"]
    risk = row["risk_score"]
    ret5 = row["ret5_pct"]
    vol_ratio = row["volume_ratio20"]
    signals = row["signals"]

    if setup >= 7 and risk <= 4 and (("REBREAK" in signals) or ("SURGE" in signals) or ("EARLY" in signals)):
        return "A"
    if setup >= 5 and risk <= 5 and (ret5 >= 5 or vol_ratio >= 1.5):
        return "B"
    if risk >= 7:
        return "C"
    return "X"


def detect_row(df: pd.DataFrame, ticker: str, name: str, group: str) -> dict:
    x = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else x

    close_ = float(x["Close"])
    volume = float(x["Volume"])
    avg_vol20 = float(x["AvgVol20"]) if pd.notna(x["AvgVol20"]) else 0.0
    vol_ratio20 = float(x["VolumeRatio20"]) if pd.notna(x["VolumeRatio20"]) else 0.0
    ma5 = float(x["MA5"]) if pd.notna(x["MA5"]) else None
    ma10 = float(x["MA10"]) if pd.notna(x["MA10"]) else None
    ma20 = float(x["MA20"]) if pd.notna(x["MA20"]) else None
    ma60 = float(x["MA60"]) if pd.notna(x["MA60"]) else None

    ret1 = float(x["Ret1D"]) if pd.notna(x["Ret1D"]) else 0.0
    ret3 = float(x["Ret3D"]) if pd.notna(x["Ret3D"]) else 0.0
    ret5 = float(x["Ret5D"]) if pd.notna(x["Ret5D"]) else 0.0
    ret10 = float(x["Ret10D"]) if pd.notna(x["Ret10D"]) else 0.0
    ret20 = float(x["Ret20D"]) if pd.notna(x["Ret20D"]) else 0.0

    drawdown20 = float(x["Drawdown20D"]) if pd.notna(x["Drawdown20D"]) else 0.0
    drawdown60 = float(x["Drawdown60D"]) if pd.notna(x["Drawdown60D"]) else 0.0
    range10 = float(x["Range10"]) if pd.notna(x["Range10"]) else 999.0
    high20 = float(x["High20D"]) if pd.notna(x["High20D"]) else close_
    low60 = float(x["Low60D"]) if pd.notna(x["Low60D"]) else close_

    early_signal = bool(
        ma5 is not None
        and ma10 is not None
        and avg_vol20 > 0
        and close_ > ma5
        and close_ > ma10
        and ret3 > 0.03
        and vol_ratio20 > 1.2
    )

    rebreak_signal = bool(
        ma10 is not None
        and ma20 is not None
        and avg_vol20 > 0
        and close_ > ma10
        and close_ > ma20
        and vol_ratio20 > 1.5
        and pd.notna(prev.get("MA10"))
        and float(prev["Close"]) <= float(prev["MA10"])
    )

    surge_signal = bool(ret5 > 0.08 and vol_ratio20 > 1.8)
    breakout20_signal = bool(high20 > 0 and close_ >= high20 * 0.985 and vol_ratio20 > 1.3)
    tight_signal = bool(range10 < 0.10 and drawdown20 > -0.08)

    setup_score = 0
    if early_signal:
        setup_score += 2
    if rebreak_signal:
        setup_score += 3
    if surge_signal:
        setup_score += 3
    if breakout20_signal:
        setup_score += 2
    if tight_signal:
        setup_score += 1

    if ma5 is not None and close_ > ma5:
        setup_score += 1
    if ma10 is not None and close_ > ma10:
        setup_score += 1
    if ma20 is not None and close_ > ma20:
        setup_score += 1
    if ret3 > 0.05:
        setup_score += 2
    elif ret3 > 0.02:
        setup_score += 1
    if ret5 > 0.08:
        setup_score += 2
    elif ret5 > 0.04:
        setup_score += 1
    if vol_ratio20 > 2.0:
        setup_score += 2
    elif vol_ratio20 > 1.4:
        setup_score += 1
    if close_ <= low60 * 1.35 and ret5 > 0:
        setup_score += 1

    risk_score = 0
    if ret1 > 0.095:
        risk_score += 3
    elif ret1 > 0.06:
        risk_score += 2

    if ret5 > 0.18:
        risk_score += 3
    elif ret5 > 0.12:
        risk_score += 2

    if ret10 > 0.25:
        risk_score += 2
    if vol_ratio20 > 3.0:
        risk_score += 2
    elif vol_ratio20 > 2.2:
        risk_score += 1

    if ma5 is not None and ma5 > 0:
        bias5 = close_ / ma5 - 1.0
        if bias5 > 0.12:
            risk_score += 2
        elif bias5 > 0.06:
            risk_score += 1
    else:
        bias5 = 0.0

    if drawdown20 > -0.02:
        risk_score += 1

    setup_score = apply_group_weight(setup_score, group)

    signals: List[str] = []
    if early_signal:
        signals.append("EARLY")
    if rebreak_signal:
        signals.append("REBREAK")
    if surge_signal:
        signals.append("SURGE")
    if breakout20_signal:
        signals.append("BREAK20")
    if tight_signal:
        signals.append("TIGHT")

    if risk_score >= 7:
        regime = "短線過熱"
    elif surge_signal:
        regime = "短線爆量推進"
    elif rebreak_signal:
        regime = "短線轉強"
    elif early_signal:
        regime = "短線預備轉強"
    elif tight_signal:
        regime = "收斂待發"
    elif drawdown60 <= -0.18:
        regime = "反彈修復中"
    else:
        regime = "一般觀察"

    row = {
        "date": df.index[-1].strftime("%Y-%m-%d"),
        "ticker": ticker,
        "name": name,
        "group": group,
        "close": round(close_, 2),
        "ret1_pct": round(ret1 * 100, 2),
        "ret3_pct": round(ret3 * 100, 2),
        "ret5_pct": round(ret5 * 100, 2),
        "ret10_pct": round(ret10 * 100, 2),
        "ret20_pct": round(ret20 * 100, 2),
        "volume": int(volume),
        "avg_vol20": int(avg_vol20) if avg_vol20 else 0,
        "volume_ratio20": round(vol_ratio20, 2),
        "ma5": round(ma5, 2) if ma5 is not None else None,
        "ma10": round(ma10, 2) if ma10 is not None else None,
        "ma20": round(ma20, 2) if ma20 is not None else None,
        "ma60": round(ma60, 2) if ma60 is not None else None,
        "drawdown20_pct": round(drawdown20 * 100, 2),
        "drawdown60_pct": round(drawdown60 * 100, 2),
        "bias5_pct": round(bias5 * 100, 2),
        "setup_score": int(setup_score),
        "risk_score": int(risk_score),
        "signals": ",".join(signals) if signals else "NONE",
        "score_band": score_band(setup_score, risk_score),
        "regime": regime,
    }
    row["grade"] = grade_signal(row)
    return row


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
    df = df.sort_values(
        by=["setup_score", "risk_score", "ret3_pct", "ret5_pct"],
        ascending=[False, True, False, False],
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
    if not CONFIG.state_enabled:
        return
    STATE_FILE.write_text(state, encoding="utf-8")


def get_market_regime() -> dict:
    if not CONFIG.market_filter.enabled:
        return {"enabled": False, "is_bullish": True, "comment": "大盤濾網關閉"}

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
            f"{CONFIG.market_filter.name} "
            f"{'偏多' if is_bullish else '偏保守'} | "
            f"Close {round(close_,2)} / MA{CONFIG.market_filter.ma_period} {round(ma,2)} / "
            f"20D {round(ret20*100,2)}% / VolRatio {round(vol_ratio,2)}"
        ),
    }


def _has_any_signal(sig_text: str, targets: List[str]) -> bool:
    if not targets:
        return True
    return any(sig in sig_text.split(",") for sig in targets)


def select_push_candidates(df_rank: pd.DataFrame) -> pd.DataFrame:
    rule = CONFIG.notify
    df = df_rank.copy()

    if rule.priority_groups:
        pri = df[df["group"].isin(rule.priority_groups)].copy()
        non = df[~df["group"].isin(rule.priority_groups)].copy()
        df = pd.concat([pri, non], ignore_index=True)

    df = df[
        (df["setup_score"] >= rule.min_setup_score)
        & (df["risk_score"] <= rule.max_risk_score)
        & ((df["ret5_pct"] >= rule.min_ret5_pct) | (df["volume_ratio20"] >= rule.min_volume_ratio))
        & (df["signals"].apply(lambda s: _has_any_signal(s, rule.require_any_signals)))
    ].copy()

    df = df[
        (df["rank_change"] >= rule.push_rank_improvement_at_least)
        | (df["setup_change"] >= rule.push_setup_change_at_least)
        | (df["grade"].isin(["A", "B"]))
    ].copy()

    return df.head(rule.top_n)


def build_state(df_rank: pd.DataFrame, market_regime: dict) -> str:
    base = "|".join(
        f"{r.ticker}:{r.setup_score}:{r.risk_score}:{r.signals}:{r.rank}:{r.grade}"
        for r in df_rank.itertuples(index=False)
    )
    return f"market={market_regime.get('is_bullish', True)}||{base}"


def should_alert(df_rank: pd.DataFrame, current_state: str, last_state: str, market_regime: dict) -> bool:
    if CONFIG.always_notify:
        return True
    if current_state == last_state:
        return False

    candidates = select_push_candidates(df_rank)
    if candidates.empty:
        return False

    if market_regime.get("is_bullish", True):
        return True

    if CONFIG.market_filter.allow_when_not_bullish_if_strong_signal and (candidates["grade"] == "A").any():
        return True

    return False


def build_push_message(df_rank: pd.DataFrame, market_regime: dict) -> str:
    candidates = select_push_candidates(df_rank)
    lines = ["⚡ 短線強訊號提醒", market_regime["comment"], ""]
    if candidates.empty:
        lines.append("今天沒有符合條件的短線強訊號。")
        return "\n".join(lines)

    for _, r in candidates.iterrows():
        lines.extend([
            f"{r['grade']}級 | #{int(r['rank'])} {r['name']} {r['ticker']} [{r['group']}]",
            f"setup {r['setup_score']} / risk {r['risk_score']} / {r['signals']}",
            f"1D {r['ret1_pct']}% | 3D {r['ret3_pct']}% | 5D {r['ret5_pct']}% | VolRatio {r['volume_ratio20']}",
            f"排名變化 {int(r['rank_change']):+d} | setup變化 {int(r['setup_change']):+d}",
            f"regime: {r['regime']}",
            "",
        ])
    return "\n".join(lines).strip()


def build_daily_report_markdown(df_rank: pd.DataFrame, market_regime: dict, backtest_summary: Optional[pd.DataFrame]) -> str:
    today = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# Daily Short-Term Watchlist Report",
        f"- Generated: {today}",
        f"- Market Regime: {market_regime['comment']}",
        "",
        "## Top Ranking",
        "",
        "| Rank | Grade | Name | Ticker | Group | Setup | Risk | Signals | RankΔ | SetupΔ | 1D% | 3D% | 5D% | VolRatio | Regime |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]

    for _, r in df_rank.iterrows():
        lines.append(
            f"| {int(r['rank'])} | {r['grade']} | {r['name']} | {r['ticker']} | {r['group']} | "
            f"{int(r['setup_score'])} | {int(r['risk_score'])} | {r['signals']} | "
            f"{int(r['rank_change']):+d} | {int(r['setup_change']):+d} | "
            f"{r['ret1_pct']} | {r['ret3_pct']} | {r['ret5_pct']} | {r['volume_ratio20']} | {r['regime']} |"
        )

    lines.extend(["", "## Notification Candidates", ""])
    candidates = select_push_candidates(df_rank)
    if candidates.empty:
        lines.append("- None")
    else:
        for _, r in candidates.iterrows():
            lines.append(
                f"- {r['grade']} | #{int(r['rank'])} {r['name']} {r['ticker']} [{r['group']}] | "
                f"setup {r['setup_score']} risk {r['risk_score']} | "
                f"1D {r['ret1_pct']}% 3D {r['ret3_pct']}% 5D {r['ret5_pct']}% | "
                f"{r['signals']} | rankΔ {int(r['rank_change']):+d} setupΔ {int(r['setup_change']):+d}"
            )

    if backtest_summary is not None and not backtest_summary.empty:
        lines.extend(["", "## Backtest Snapshot", ""])
        lines.append("| Horizon | Trades | Win Rate | Avg Return | Median Return |")
        lines.append("| --- | --- | --- | --- | --- |")
        for _, r in backtest_summary.iterrows():
            lines.append(
                f"| {int(r['horizon'])}D | {int(r['trades'])} | {r['win_rate_pct']}% | "
                f"{r['avg_return_pct']}% | {r['median_return_pct']}% |"
            )
    return "\n".join(lines)


def dataframe_to_html(df: pd.DataFrame) -> str:
    return df.to_html(index=False, border=0, justify="center")


def build_daily_report_html(df_rank: pd.DataFrame, market_regime: dict, backtest_summary: Optional[pd.DataFrame]) -> str:
    candidates = select_push_candidates(df_rank)
    candidate_html = "<p>None</p>" if candidates.empty else dataframe_to_html(candidates)
    backtest_html = "<p>Unavailable</p>" if backtest_summary is None or backtest_summary.empty else dataframe_to_html(backtest_summary)
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Daily Short-Term Watchlist Report</title>
<style>
body {{ font-family: Arial, sans-serif; margin: 24px; }}
table {{ border-collapse: collapse; width: 100%; margin-bottom: 24px; }}
th, td {{ border: 1px solid #ddd; padding: 8px; font-size: 14px; }}
th {{ background: #f4f4f4; }}
</style>
</head>
<body>
<h1>Daily Short-Term Watchlist Report</h1>
<p><strong>Market:</strong> {market_regime['comment']}</p>
<h2>Top Ranking</h2>
{dataframe_to_html(df_rank)}
<h2>Notification Candidates</h2>
{candidate_html}
<h2>Backtest Snapshot</h2>
{backtest_html}
</body>
</html>"""


def save_reports(df_rank: pd.DataFrame, market_regime: dict, backtest_summary: Optional[pd.DataFrame]) -> None:
    REPORT_MD.write_text(
        build_daily_report_markdown(df_rank, market_regime, backtest_summary),
        encoding="utf-8",
    )
    REPORT_HTML.write_text(
        build_daily_report_html(df_rank, market_regime, backtest_summary),
        encoding="utf-8",
    )


def split_message(text: str, limit: int) -> List[str]:
    if len(text) <= limit:
        return [text]
    chunks: List[str] = []
    current: List[str] = []
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
            payload = {"chat_id": chat_id, "text": part}
            try:
                resp = HTTP.post(url, json=payload, timeout=HTTP_TIMEOUT)
                if not resp.ok:
                    logger.error("Telegram send failed. chat_id=%s status=%s body=%s", chat_id, resp.status_code, resp.text[:500])
            except Exception as exc:
                logger.exception("Telegram send exception for chat_id=%s: %s", chat_id, exc)


def run_watchlist() -> pd.DataFrame:
    rows: List[dict] = []
    prev_rank = load_previous_rank()

    for item in WATCHLIST:
        ticker, name, group = item["ticker"], item["name"], item["group"]
        try:
            df = yf_download_one(ticker, CONFIG.yf_period)
            df = add_indicators(df)
            row = detect_row(df, ticker, name, group)
            rows.append(row)
            append_stock_log(row)
            logger.info("OK: %s %s", ticker, name)
        except Exception as exc:
            logger.exception("FAILED: %s %s -> %s", ticker, name, exc)

    if not rows:
        raise RuntimeError("No stock data available from watchlist.")
    return save_daily_rank(rows, prev_rank)


def run_backtest_snapshot() -> Optional[pd.DataFrame]:
    if not CONFIG.backtest.enabled:
        return None

    all_events: List[dict] = []
    max_horizon = max(CONFIG.backtest.lookahead_days)

    for item in WATCHLIST:
        ticker, name, group = item["ticker"], item["name"], item["group"]
        try:
            df = yf_download_one(ticker, CONFIG.backtest.period)
            df = add_indicators(df)

            for i in range(120, len(df) - max_horizon):
                cut = df.iloc[: i + 1].copy()
                row = detect_row(cut, ticker, name, group)
                if row["setup_score"] < CONFIG.backtest.min_setup_score:
                    continue
                if row["risk_score"] > CONFIG.backtest.max_risk_score:
                    continue
                if row["ret5_pct"] < CONFIG.backtest.min_ret5_pct and row["volume_ratio20"] < CONFIG.backtest.min_volume_ratio:
                    continue
                if not _has_any_signal(row["signals"], CONFIG.backtest.accepted_signals):
                    continue

                entry = float(df.iloc[i]["Close"])
                event = {
                    "ticker": ticker,
                    "date": cut.index[-1].strftime("%Y-%m-%d"),
                    "group": group,
                    "setup_score": row["setup_score"],
                    "risk_score": row["risk_score"],
                    "signals": row["signals"],
                }
                for horizon in CONFIG.backtest.lookahead_days:
                    future = float(df.iloc[i + horizon]["Close"])
                    event[f"ret_{horizon}d"] = round((future / entry - 1.0) * 100, 2)
                all_events.append(event)
        except Exception as exc:
            logger.exception("BACKTEST FAILED: %s %s -> %s", ticker, name, exc)

    if not all_events:
        return None

    events_df = pd.DataFrame(all_events)
    summary_rows: List[dict] = []
    for horizon in CONFIG.backtest.lookahead_days:
        col = f"ret_{horizon}d"
        s = events_df[col].dropna()
        if s.empty:
            continue
        summary_rows.append(
            {
                "horizon": horizon,
                "trades": int(s.shape[0]),
                "win_rate_pct": round((s.gt(0).mean()) * 100, 2),
                "avg_return_pct": round(s.mean(), 2),
                "median_return_pct": round(s.median(), 2),
            }
        )

    bt_df = pd.DataFrame(summary_rows)
    bt_df.to_csv(OUTDIR / "backtest_summary.csv", index=False, encoding="utf-8-sig")
    events_df.to_csv(OUTDIR / "backtest_events.csv", index=False, encoding="utf-8-sig")
    return bt_df


def main() -> int:
    try:
        market_regime = get_market_regime()
        df_rank = run_watchlist()
        bt_summary = run_backtest_snapshot()

        logger.info("=== 今日排行榜 ===\n%s", df_rank.to_string(index=False))
        logger.info("Market regime: %s", market_regime["comment"])

        save_reports(df_rank, market_regime, bt_summary)

        current_state = build_state(df_rank, market_regime)
        last_state = load_last_state()

        if should_alert(df_rank, current_state, last_state, market_regime):
            send_telegram_message(build_push_message(df_rank, market_regime))
            logger.info("Notification sent.")
        else:
            logger.info("No notification sent.")

        save_last_state(current_state)
        return 0

    except Exception as exc:
        err_msg = f"Watchlist job failed: {exc}"
        logger.exception(err_msg)
        send_telegram_message(err_msg)
        return 1


if __name__ == "__main__":
    sys.exit(main())
