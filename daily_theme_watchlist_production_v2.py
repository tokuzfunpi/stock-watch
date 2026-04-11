from __future__ import annotations

import csv
import logging
import os
import sys
from pathlib import Path
from typing import Dict, List

import pandas as pd
import requests
import yfinance as yf
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_DIR = Path(__file__).resolve().parent
WATCHLIST_CSV = Path(os.getenv("WATCHLIST_CSV", BASE_DIR / "watchlist.csv"))
OUTDIR = Path(os.getenv("OUTDIR", BASE_DIR / "theme_watchlist_daily"))
OUTDIR.mkdir(parents=True, exist_ok=True)

RANK_CSV = OUTDIR / "daily_rank.csv"
STATE_FILE = OUTDIR / "last_rank_state.txt"
LOG_DIR = OUTDIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_IDS = [
    int(x.strip())
    for x in os.getenv("TELEGRAM_CHAT_IDS", "").split(",")
    if x.strip()
]

YF_PERIOD = os.getenv("YF_PERIOD", "3y")
ENABLE_STATE = os.getenv("ENABLE_STATE", "true").lower() == "true"
ALWAYS_NOTIFY = os.getenv("ALWAYS_NOTIFY", "false").lower() == "true"

HTTP_TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "20"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("theme_watchlist")


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


def load_watchlist(csv_path: Path) -> Dict[str, str]:
    if not csv_path.exists():
        raise FileNotFoundError(f"watchlist.csv not found: {csv_path}")

    watchlist: Dict[str, str] = {}
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        required = {"ticker", "name"}
        if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
            raise ValueError("watchlist.csv must contain headers: ticker,name")

        for row in reader:
            ticker = (row.get("ticker") or "").strip()
            name = (row.get("name") or "").strip()
            enabled = (row.get("enabled") or "true").strip().lower()

            if not ticker or not name:
                continue
            if enabled in {"false", "0", "no", "n"}:
                continue

            watchlist[ticker] = name

    if not watchlist:
        raise ValueError("No enabled symbols found in watchlist.csv")

    return watchlist


WATCHLIST = load_watchlist(WATCHLIST_CSV)


def yf_download_one(ticker: str, period: str = YF_PERIOD) -> pd.DataFrame:
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
    if len(df) < 250:
        raise ValueError(f"Insufficient history for {ticker}: {len(df)} rows")

    return df


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()

    for n in [5, 10, 20, 60, 120, 250]:
        out[f"MA{n}"] = out["Close"].rolling(n).mean()

    out["AvgVol20"] = out["Volume"].rolling(20).mean()
    out["Ret5D"] = out["Close"].pct_change(5)
    out["Ret20D"] = out["Close"].pct_change(20)

    out["High120D"] = out["Close"].rolling(120).max()
    out["High250D"] = out["Close"].rolling(250).max()
    out["Low250D"] = out["Close"].rolling(250).min()

    out["Drawdown120D"] = out["Close"] / out["High120D"] - 1.0
    out["Range20"] = (
        out["High"].rolling(20).max() - out["Low"].rolling(20).min()
    ) / out["Close"]

    out["DistToLow250"] = out["Close"] / out["Low250D"] - 1.0

    return out


def score_band(setup_score: int, risk_score: int) -> str:
    if risk_score >= 6:
        return "高風險追價區"
    if setup_score >= 7:
        return "高關注啟動區"
    if setup_score >= 5:
        return "可能啟動前"
    if setup_score >= 3:
        return "開始留意"
    return "一般觀察"


def detect_row(df: pd.DataFrame, ticker: str, name: str) -> dict:
    x = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else x

    close_ = float(x["Close"])
    volume = float(x["Volume"])
    avg_vol20 = float(x["AvgVol20"]) if pd.notna(x["AvgVol20"]) else 0.0
    ma20 = float(x["MA20"]) if pd.notna(x["MA20"]) else None
    ma60 = float(x["MA60"]) if pd.notna(x["MA60"]) else None
    ma120 = float(x["MA120"]) if pd.notna(x["MA120"]) else None
    low250 = float(x["Low250D"]) if pd.notna(x["Low250D"]) else None
    ret5 = float(x["Ret5D"]) if pd.notna(x["Ret5D"]) else 0.0
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
        ma20 is not None
        and ma60 is not None
        and avg_vol20 > 0
        and close_ > ma20
        and close_ > ma60
        and volume > avg_vol20 * 1.8
        and pd.notna(prev.get("MA20"))
        and float(prev["Close"]) <= float(prev["MA20"])
    )

    surge_signal = bool(avg_vol20 > 0 and ret20 > 0.30 and volume > avg_vol20 * 2.5)
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

    if avg_vol20 > 0 and volume > avg_vol20 * 1.8:
        setup_score += 2

    if dist_low250 < 0.25 and ret20 > 0.10:
        setup_score += 1

    risk_score = 0

    if ret5 > 0.18:
        risk_score += 2
    if ret20 > 0.30:
        risk_score += 2
    if ret20 > 0.50:
        risk_score += 2

    if avg_vol20 > 0 and volume > avg_vol20 * 2.5:
        risk_score += 2
    elif avg_vol20 > 0 and volume > avg_vol20 * 1.8:
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

    signals: List[str] = []
    if base_signal:
        signals.append("BASE")
    if rebreak_signal:
        signals.append("REBREAK")
    if surge_signal:
        signals.append("SURGE")
    if pullback_signal:
        signals.append("PULLBACK")

    if risk_score >= 6:
        regime = "高風險追價區"
    elif surge_signal:
        regime = "題材暴衝段"
    elif rebreak_signal:
        regime = "重新啟動"
    elif base_signal:
        regime = "低檔盤整"
    elif pullback_signal:
        regime = "高檔回落整理"
    else:
        regime = "一般觀察"

    return {
        "date": df.index[-1].strftime("%Y-%m-%d"),
        "ticker": ticker,
        "name": name,
        "close": round(close_, 2),
        "ret5_pct": round(ret5 * 100, 2),
        "ret20_pct": round(ret20 * 100, 2),
        "volume": int(volume),
        "avg_vol20": int(avg_vol20) if avg_vol20 else 0,
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


def save_daily_rank(rows: List[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df = df.sort_values(
        by=["setup_score", "risk_score", "ret20_pct"],
        ascending=[False, True, True],
    ).reset_index(drop=True)
    df.insert(0, "rank", range(1, len(df) + 1))
    df.to_csv(RANK_CSV, index=False, encoding="utf-8-sig")
    return df


def load_last_state() -> str:
    if not ENABLE_STATE or not STATE_FILE.exists():
        return ""
    return STATE_FILE.read_text(encoding="utf-8").strip()


def save_last_state(state: str) -> None:
    if not ENABLE_STATE:
        return
    STATE_FILE.write_text(state, encoding="utf-8")


def interpret_scores(row: dict) -> str:
    setup = row["setup_score"]
    risk = row["risk_score"]

    if setup <= 2:
        setup_desc = "尚未形成結構"
    elif setup <= 4:
        setup_desc = "開始築底 / 可觀察"
    elif setup <= 6:
        setup_desc = "接近啟動（關鍵區）"
    else:
        setup_desc = "高機率啟動區"

    if risk <= 2:
        risk_desc = "風險低（安全區）"
    elif risk <= 5:
        risk_desc = "中等風險（注意）"
    else:
        risk_desc = "高風險（可能出貨）"

    if setup >= 5 and risk <= 3:
        action = "👉 可考慮布局 / 等突破加碼"
    elif setup >= 3 and risk <= 3:
        action = "👉 放入觀察名單，等待 REBREAK"
    elif risk >= 6:
        action = "👉 不要追高，偏出貨區"
    elif "PULLBACK" in row["signals"]:
        action = "👉 回檔整理中，等待重新築底"
    else:
        action = "👉 暫時觀望"

    edge = setup - risk
    if edge >= 3:
        edge_desc = "強勢機會"
    elif edge >= 1:
        edge_desc = "偏多觀察"
    elif edge == 0:
        edge_desc = "中性"
    else:
        edge_desc = "偏空 / 不建議"

    return (
        f"解析：{setup_desc} / {risk_desc}\n"
        f"Edge: {edge}（{edge_desc}）\n"
        f"{action}"
    )


def overall_market_comment(df_rank: pd.DataFrame) -> str:
    if (df_rank["setup_score"] >= 5).any():
        return "🔥 市場可能開始出現啟動股"
    elif (df_rank["signals"] == "SURGE").any():
        return "🚀 市場已有題材主升段"
    elif (df_rank["signals"] == "REBREAK").any():
        return "⚡ 有個股轉強"
    elif (df_rank["signals"] == "PULLBACK").all():
        return "❄️ 題材股全面冷卻中"
    else:
        return "🟡 盤面觀察期"


def build_rank_message(df_rank: pd.DataFrame) -> str:
    top = df_rank.head(5)
    lines = [overall_market_comment(df_rank), "", "今日題材股觀察排行"]

    for _, r in top.iterrows():
        interpret = interpret_scores(r.to_dict())
        lines.append(
            f"{int(r['rank'])}. {r['name']} {r['ticker']}\n"
            f"收盤 {r['close']} | setup {r['setup_score']} | risk {r['risk_score']}\n"
            f"{r['signals']} | {r['score_band']}\n"
            f"{interpret}\n"
        )

    high_attention = df_rank[
        (df_rank["setup_score"] >= 5)
        | (df_rank["risk_score"] >= 6)
        | (df_rank["signals"] != "NONE")
    ]

    if not high_attention.empty:
        lines.append("")
        lines.append("重點觀察：")
        for _, r in high_attention.iterrows():
            lines.append(
                f"- {r['name']} {r['ticker']}：{r['regime']}，"
                f"20D {r['ret20_pct']}%，距120D高點 {r['drawdown120_pct']}%"
            )

    return "\n".join(lines)


def split_message(text: str, limit: int = 3500) -> List[str]:
    if len(text) <= limit:
        return [text]

    chunks: List[str] = []
    current = []

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

    for part in split_message(message):
        payload_base = {"text": part}
        for chat_id in TELEGRAM_CHAT_IDS:
            payload = {**payload_base, "chat_id": chat_id}
            try:
                resp = HTTP.post(url, json=payload, timeout=HTTP_TIMEOUT)
                if not resp.ok:
                    logger.error(
                        "Telegram send failed. chat_id=%s status=%s body=%s",
                        chat_id,
                        resp.status_code,
                        resp.text[:500],
                    )
            except Exception as exc:
                logger.exception("Telegram send exception for chat_id=%s: %s", chat_id, exc)


def run_watchlist() -> pd.DataFrame:
    rows: List[dict] = []

    for ticker, name in WATCHLIST.items():
        try:
            df = yf_download_one(ticker)
            df = add_indicators(df)
            row = detect_row(df, ticker, name)
            rows.append(row)
            append_stock_log(row)
            logger.info("OK: %s %s", ticker, name)
        except Exception as exc:
            logger.exception("FAILED: %s %s -> %s", ticker, name, exc)

    if not rows:
        raise RuntimeError("No stock data available from watchlist.")

    return save_daily_rank(rows)


def build_state(df_rank: pd.DataFrame) -> str:
    return "|".join(
        f"{r.ticker}:{r.setup_score}:{r.risk_score}:{r.signals}"
        for r in df_rank.itertuples(index=False)
    )


def should_alert(df_rank: pd.DataFrame, current_state: str, last_state: str) -> bool:
    if ALWAYS_NOTIFY:
        return True

    has_signal = (
        (df_rank["setup_score"] >= 5).any()
        or (df_rank["risk_score"] >= 6).any()
        or (df_rank["signals"] != "NONE").any()
    )
    return current_state != last_state and has_signal


def main() -> int:
    try:
        df_rank = run_watchlist()

        logger.info("=== 今日排行榜 ===\n%s", df_rank.to_string(index=False))

        msg = build_rank_message(df_rank)
        logger.info("\n%s", msg)
        logger.info("Saved rank CSV: %s", RANK_CSV)

        current_state = build_state(df_rank)
        last_state = load_last_state()

        if should_alert(df_rank, current_state, last_state):
            send_telegram_message(msg)
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
