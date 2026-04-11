from __future__ import annotations

import csv
import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import pandas as pd
import requests
import yfinance as yf
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = Path(os.getenv("CONFIG_PATH", BASE_DIR / "config_20d_v22.json"))
WATCHLIST_CSV = Path(
    os.getenv("WATCHLIST_CSV", BASE_DIR / "watchlist_20d_v22.csv")
)
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
    int(x.strip())
    for x in os.getenv("TELEGRAM_CHAT_IDS", "").split(",")
    if x.strip()
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
    allow_a_grade_even_if_weak: bool


@dataclass
class NotificationRule:
    top_n: int
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
    return AppConfig(
        yf_period=raw.get("yf_period", "3y"),
        state_enabled=bool(raw.get("state_enabled", True)),
        always_notify=bool(raw.get("always_notify", False)),
        max_message_length=int(raw.get("max_message_length", 3500)),
        watchlist_default_group=raw.get("watchlist_default_group", "theme"),
        market_filter=MarketFilter(**raw["market_filter"]),
        notify=NotificationRule(**raw["notify"]),
        backtest=BacktestConfig(**raw["backtest"]),
        group_weights=GroupWeights(**raw["group_weights"]),
    )


CONFIG = load_config(CONFIG_PATH)

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("theme_watchlist_20d_v22")


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
    rows: List[dict] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
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


def detect_row(df: pd.DataFrame, ticker: str, name: str, group: str) -> dict:
    x = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else x

    close_ = float(x["Close"])
    volume = float(x["Volume"])
    avg_vol20 = float(x["AvgVol20"]) if pd.notna(x["AvgVol20"]) else 0.0
    vol_ratio20 = (
        float(x["VolumeRatio20"]) if pd.notna(x["VolumeRatio20"]) else 0.0
    )

    ma20 = float(x["MA20"]) if pd.notna(x["MA20"]) else None
    ma60 = float(x["MA60"]) if pd.notna(x["MA60"]) else None
    ma120 = float(x["MA120"]) if pd.notna(x["MA120"]) else None
    low250 = float(x["Low250D"]) if pd.notna(x["Low250D"]) else None

    ret1 = float(x["Ret1D"]) if pd.notna(x["Ret1D"]) else 0.0
    ret5 = float(x["Ret5D"]) if pd.notna(x["Ret5D"]) else 0.0
    ret10 = float(x["Ret10D"]) if pd.notna(x["Ret10D"]) else 0.0
    ret20 = float(x["Ret20D"]) if pd.notna(x["Ret20D"]) else 0.0

    drawdown120 = (
        float(x["Drawdown120D"]) if pd.notna(x["Drawdown120D"]) else 0.0
    )
    range20 = float(x["Range20"]) if pd.notna(x["Range20"]) else 999.0
    dist_low250 = (
        float(x["DistToLow250"]) if pd.notna(x["DistToLow250"]) else 999.0
    )

    base_signal = bool(
        low250 is not None
        and close_ <= low250 * 1.20
        and avg_vol20 > 0
        and volume < avg_vol20
        and range20 < 0.15
    )
    rebreak_signal = bool(
        ma20 is not None
        and ma60 is not None
        and avg_vol20 > 0
        and close_ > ma20
        and close_ > ma60
        and vol_ratio20 > 1.35
        and pd.notna(prev.get("MA20"))
        and float(prev["Close"]) <= float(prev["MA20"])
    )
    surge_signal = bool(ret20 > 0.22 and vol_ratio20 > 1.55)
    trend_signal = bool(
        ma20 is not None
        and ma60 is not None
        and close_ > ma20
        and ma20 > ma60
        and ret20 > 0.08
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

    return {
        "date": df.index[-1].strftime("%Y-%m-%d"),
        "ticker": ticker,
        "name": name,
        "group": group,
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
    }


def grade_signal(row: dict) -> str:
    setup = row["setup_score"]
    risk = row["risk_score"]
    signals = row["signals"]
    ret5 = row["ret5_pct"]
    vol_ratio20 = row["volume_ratio20"]
    ret20 = row["ret20_pct"]

    if (
        setup >= 7
        and risk <= 4
        and (
            ("ACCEL" in signals)
            or ("REBREAK" in signals)
            or ("SURGE" in signals)
        )
        and ret20 > 0
    ):
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


def enrich_rank_changes(
    df_rank: pd.DataFrame, prev_rank: Optional[pd.DataFrame]
) -> pd.DataFrame:
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
            old_setup = (
                int(old["setup_score"]) if pd.notna(old["setup_score"]) else 0
            )
            old_risk = (
                int(old["risk_score"]) if pd.notna(old["risk_score"]) else 0
            )
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


def save_daily_rank(
    rows: List[dict], prev_rank: Optional[pd.DataFrame]
) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df["grade"] = df.apply(lambda r: grade_signal(r.to_dict()), axis=1)
    # v2.2 排名更偏動能
    df = df.sort_values(
        by=[
            "setup_score",
            "ret5_pct",
            "volume_ratio20",
            "ret20_pct",
            "risk_score",
        ],
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


def get_market_regime() -> dict:
    if not CONFIG.market_filter.enabled:
        return {"enabled": False, "is_bullish": True, "comment": "大盤濾網關掉"}

    df = yf_download_one(CONFIG.market_filter.ticker, CONFIG.yf_period)
    df = add_indicators(df, CONFIG.market_filter.ma_period)
    x = df.iloc[-1]
    close_ = float(x["Close"])
    ma = float(x[f"MA{CONFIG.market_filter.ma_period}"])
    ret20 = float(x["Ret20D"]) if pd.notna(x["Ret20D"]) else 0.0
    vol_ratio = (
        float(x["VolumeRatio20"]) if pd.notna(x["VolumeRatio20"]) else 1.0
    )

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


def select_push_candidates(df_rank: pd.DataFrame) -> pd.DataFrame:
    rule = CONFIG.notify
    df = df_rank.copy()

    if rule.priority_groups:
        pri = df[df["group"].isin(rule.priority_groups)].copy()
        non = df[~df["group"].isin(rule.priority_groups)].copy()
        df = pd.concat([pri, non], ignore_index=True)

    base_mask = (
        (df["setup_score"] >= rule.min_setup_score)
        & (df["risk_score"] <= rule.max_risk_score)
        & (
            (df["ret20_pct"] >= rule.min_ret20_pct)
            & (
                (df["ret5_pct"] >= rule.min_ret5_pct)
                | (df["volume_ratio20"] >= rule.min_volume_ratio)
            )
        )
    )

    # 進攻版過濾：避免假突破
    attack_filter = (
        (df["ret5_pct"] > 8)
        & (df["volume_ratio20"] > 1.3)
        & (df["ret20_pct"] > 0)
    )

    cond_a = df["grade"] == "A"
    cond_b = (df["setup_score"] >= 5) & (df["rank"] <= 3)
    cond_c = attack_filter
    cond_d = (df["setup_change"] > 0) | (df["rank_change"] > 0)

    return (
        df[base_mask & (cond_a | cond_b | cond_c | cond_d)]
        .head(rule.top_n)
        .copy()
    )


def build_state(df_rank: pd.DataFrame, market_regime: dict) -> str:
    base_state = "|".join(
        f"{r.ticker}:{r.setup_score}:{r.risk_score}:{r.signals}:{r.rank}:{r.grade}"
        for r in df_rank.itertuples(index=False)
    )
    return f"market={market_regime.get('is_bullish', True)}||{base_state}"


def should_alert(
    df_rank: pd.DataFrame,
    current_state: str,
    last_state: str,
    market_regime: dict,
) -> bool:
    if CONFIG.always_notify:
        return True
    if current_state == last_state:
        return False
    candidates = select_push_candidates(df_rank)
    if candidates.empty:
        return False
    if market_regime.get("is_bullish", True):
        return True
    if (
        CONFIG.market_filter.allow_a_grade_even_if_weak
        and (candidates["grade"] == "A").any()
    ):
        return True
    return False


def build_push_message(df_rank: pd.DataFrame, market_regime: dict) -> str:
    candidates = select_push_candidates(df_rank)
    lines = ["📣 今天幫你挑到幾檔比較像樣的", market_regime["comment"], ""]
    if candidates.empty:
        lines.append("今天先不用急，名單裡還沒有我覺得夠漂亮的進攻點。")
        return "\n".join(lines)

    for _, r in candidates.iterrows():
        tone = "這檔可以多看一眼" if r["grade"] == "A" else "這檔有在動了"
        lines.extend(
            [
                f"{tone}：{r['name']} {r['ticker']} [{r['group']}]",
                f"現在排名第 {int(r['rank'])}，setup {r['setup_score']}、risk {r['risk_score']}。",
                f"最近 5 天 {r['ret5_pct']}%，10 天 {r['ret10_pct']}%，20 天 {r['ret20_pct']}%，量比 {r['volume_ratio20']}。",
                f"目前看起來是「{r['regime']}」，訊號有 {r['signals']}。",
                f"跟上次比，排名 {int(r['rank_change']):+d}、setup {int(r['setup_change']):+d}。",
                "",
            ]
        )
    lines.append(
        "整體來看，這幾檔比較像是有題材、有量，值得追蹤，但還是別一次全上。"
    )
    return "\n".join(lines).strip()


def dataframe_to_html(df: pd.DataFrame) -> str:
    return df.to_html(index=False, border=0, justify="center")


def summarize_events(
    events_df: pd.DataFrame, horizons: List[int]
) -> pd.DataFrame:
    rows = []
    for horizon in horizons:
        col = f"ret_{horizon}d"
        s = events_df[col].dropna()
        if s.empty:
            continue
        rows.append(
            {
                "horizon": horizon,
                "trades": int(s.shape[0]),
                "win_rate_pct": round((s.gt(0).mean()) * 100, 2),
                "avg_return_pct": round(s.mean(), 2),
                "median_return_pct": round(s.median(), 2),
            }
        )
    return pd.DataFrame(rows)


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


def run_backtest_dual() -> (
    tuple[Optional[pd.DataFrame], Optional[pd.DataFrame]]
):
    if not CONFIG.backtest.enabled:
        return None, None

    steady_events = []
    attack_events = []
    max_horizon = max(CONFIG.backtest.lookahead_days)

    for item in WATCHLIST:
        ticker, name, group = item["ticker"], item["name"], item["group"]
        try:
            df = yf_download_one(ticker, CONFIG.backtest.period)
            df = add_indicators(df)
            for i in range(250, len(df) - max_horizon):
                cut = df.iloc[: i + 1].copy()
                row = detect_row(cut, ticker, name, group)
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
                    event[f"ret_{horizon}d"] = round(
                        (future / entry - 1.0) * 100, 2
                    )

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

    steady_summary = (
        summarize_events(steady_df, CONFIG.backtest.lookahead_days)
        if steady_df is not None
        else None
    )
    attack_summary = (
        summarize_events(attack_df, CONFIG.backtest.lookahead_days)
        if attack_df is not None
        else None
    )

    if steady_df is not None:
        steady_df.to_csv(
            OUTDIR / "backtest_events_steady.csv",
            index=False,
            encoding="utf-8-sig",
        )
    if attack_df is not None:
        attack_df.to_csv(
            OUTDIR / "backtest_events_attack.csv",
            index=False,
            encoding="utf-8-sig",
        )
    if steady_summary is not None:
        steady_summary.to_csv(
            OUTDIR / "backtest_summary_steady.csv",
            index=False,
            encoding="utf-8-sig",
        )
    if attack_summary is not None:
        attack_summary.to_csv(
            OUTDIR / "backtest_summary_attack.csv",
            index=False,
            encoding="utf-8-sig",
        )

    return steady_summary, attack_summary


def build_daily_report_markdown(
    df_rank: pd.DataFrame,
    market_regime: dict,
    bt_steady: Optional[pd.DataFrame],
    bt_attack: Optional[pd.DataFrame],
) -> str:
    today = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# Daily 20D v2.2 Attack Report",
        f"- Generated: {today}",
        f"- Market Regime: {market_regime['comment']}",
        "",
        "## Top Ranking",
        "",
        "| Rank | Grade | Name | Ticker | Group | Setup | Risk | Signals | RankΔ | SetupΔ | 5D% | 10D% | 20D% | VolRatio | Regime |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for _, r in df_rank.iterrows():
        lines.append(
            f"| {int(r['rank'])} | {r['grade']} | {r['name']} | {r['ticker']} | {r['group']} | "
            f"{int(r['setup_score'])} | {int(r['risk_score'])} | {r['signals']} | "
            f"{int(r['rank_change']):+d} | {int(r['setup_change']):+d} | "
            f"{r['ret5_pct']} | {r['ret10_pct']} | {r['ret20_pct']} | {r['volume_ratio20']} | {r['regime']} |"
        )

    lines.extend(["", "## Notification Candidates", ""])
    candidates = select_push_candidates(df_rank)
    if candidates.empty:
        lines.append("- None")
    else:
        for _, r in candidates.iterrows():
            lines.append(
                f"- #{int(r['rank'])} {r['name']} {r['ticker']} [{r['group']}] | "
                f"setup {r['setup_score']} risk {r['risk_score']} | "
                f"5D {r['ret5_pct']}% 10D {r['ret10_pct']}% 20D {r['ret20_pct']}% | "
                f"{r['signals']} | rankΔ {int(r['rank_change']):+d} setupΔ {int(r['setup_change']):+d}"
            )

    lines.extend(
        [
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
        ]
    )

    for title, bt in [
        ("Steady Backtest", bt_steady),
        ("Attack Backtest", bt_attack),
    ]:
        lines.extend(["", f"## {title}", ""])
        if bt is None or bt.empty:
            lines.append("- None")
        else:
            lines.append(
                "| Horizon | Trades | Win Rate | Avg Return | Median Return |"
            )
            lines.append("| --- | --- | --- | --- | --- |")
            for _, r in bt.iterrows():
                lines.append(
                    f"| {int(r['horizon'])}D | {int(r['trades'])} | {r['win_rate_pct']}% | "
                    f"{r['avg_return_pct']}% | {r['median_return_pct']}% |"
                )

    return "\n".join(lines)


def build_daily_report_html(
    df_rank: pd.DataFrame,
    market_regime: dict,
    bt_steady: Optional[pd.DataFrame],
    bt_attack: Optional[pd.DataFrame],
) -> str:
    steady_html = (
        "<p>None</p>"
        if bt_steady is None or bt_steady.empty
        else dataframe_to_html(bt_steady)
    )
    attack_html = (
        "<p>None</p>"
        if bt_attack is None or bt_attack.empty
        else dataframe_to_html(bt_attack)
    )
    candidates = select_push_candidates(df_rank)
    candidate_html = (
        "<p>None</p>" if candidates.empty else dataframe_to_html(candidates)
    )
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
<h2>Notification Candidates</h2>{candidate_html}
<h2>Steady Backtest</h2>{steady_html}
<h2>Attack Backtest</h2>{attack_html}
</body></html>"""


def save_reports(
    df_rank: pd.DataFrame,
    market_regime: dict,
    bt_steady: Optional[pd.DataFrame],
    bt_attack: Optional[pd.DataFrame],
) -> None:
    REPORT_MD.write_text(
        build_daily_report_markdown(
            df_rank, market_regime, bt_steady, bt_attack
        ),
        encoding="utf-8",
    )
    REPORT_HTML.write_text(
        build_daily_report_html(df_rank, market_regime, bt_steady, bt_attack),
        encoding="utf-8",
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
                resp = HTTP.post(
                    url,
                    json={"chat_id": chat_id, "text": part},
                    timeout=HTTP_TIMEOUT,
                )
                if not resp.ok:
                    logger.error(
                        "Telegram send failed. chat_id=%s status=%s body=%s",
                        chat_id,
                        resp.status_code,
                        resp.text[:500],
                    )
            except Exception as exc:
                logger.exception(
                    "Telegram send exception for chat_id=%s: %s", chat_id, exc
                )


def main() -> int:
    try:
        market_regime = get_market_regime()
        df_rank = run_watchlist()
        bt_steady, bt_attack = run_backtest_dual()

        logger.info("=== 今日排行榜 ===\n%s", df_rank.to_string(index=False))
        logger.info("Market regime: %s", market_regime["comment"])

        save_reports(df_rank, market_regime, bt_steady, bt_attack)

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
