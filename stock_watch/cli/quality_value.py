from __future__ import annotations

import argparse
import json
import math
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

from stock_watch.data.fundamentals import FinMindFundamentalProvider
from stock_watch.data.fundamentals import FINMIND_API_URL
from stock_watch.data.fundamentals import OfficialValuationProvider
from stock_watch.data.fundamentals import _local_env_value
from stock_watch.paths import THEME_OUTDIR

NUMERIC_COLUMNS = [
    "rank",
    "close",
    "ret5_pct",
    "ret10_pct",
    "ret20_pct",
    "volume_ratio20",
    "setup_score",
    "risk_score",
    "spec_risk_score",
    "atr_pct",
    "bias20_pct",
    "drawdown120_pct",
]

ENTRY_PLAN_COLUMNS = [
    "ticker",
    "name",
    "bucket",
    "decision_priority",
    "entry_bias",
    "buy_zone_low",
    "buy_zone_high",
    "stop_loss",
    "add_rule",
    "trim_rule",
    "decision_reason",
]

SCOUT_WATCHLIST_DRAFT_COLUMNS = [
    "ticker",
    "name",
    "group",
    "layer",
    "enabled",
    "radar_priority",
    "similar_score",
    "watchlist_reason",
]

SIMILAR_SCOUT_INDUSTRIES = {
    "電機機械",
    "電器電纜",
    "半導體業",
    "電子零組件業",
    "電腦及週邊設備業",
    "光電業",
    "通信網路業",
    "其他電子業",
    "資訊服務業",
    "電子通路業",
    "汽車工業",
    "航運業",
    "貿易百貨",
}
TWSE_COMPANY_INFO_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
TPEX_COMPANY_INFO_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"
INDUSTRY_CODE_LABELS = {
    "05": "電機機械",
    "24": "半導體業",
    "25": "電腦及週邊設備業",
    "26": "光電業",
    "27": "通信網路業",
    "28": "電子零組件業",
    "29": "電子通路業",
    "30": "資訊服務業",
    "31": "其他電子業",
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a quality/value research report from the latest daily_rank.csv."
    )
    parser.add_argument("--rank-csv", default=str(THEME_OUTDIR / "daily_rank.csv"))
    parser.add_argument("--outdir", default=str(THEME_OUTDIR))
    parser.add_argument("--low-price-max", type=float, default=120.0)
    parser.add_argument("--max-volume-ratio", type=float, default=1.2)
    parser.add_argument("--min-ret20", type=float, default=5.0)
    parser.add_argument("--min-setup", type=float, default=8.0)
    parser.add_argument(
        "--fundamentals",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Fetch FinMind or official TWSE/TPEx fundamental overlay for selected rows.",
    )
    parser.add_argument(
        "--fundamental-limit",
        type=int,
        default=40,
        help="Maximum selected tickers to enrich with fundamentals.",
    )
    parser.add_argument(
        "--similar-scout",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Scan adjacent TWSE/TPEx industries for more quality/value names using public fundamentals.",
    )
    parser.add_argument("--scout-limit", type=int, default=30)
    parser.add_argument("--scout-candidate-limit", type=int, default=300)
    parser.add_argument("--scout-draft-limit", type=int, default=15)
    return parser.parse_args(argv)


def _safe_number(value: object) -> float:
    try:
        number = float(value)
    except Exception:
        return 0.0
    if math.isnan(number) or math.isinf(number):
        return 0.0
    return number


def _normalize_base(ticker: object) -> str:
    text = str(ticker or "").strip().upper()
    if "." in text:
        return text.split(".", 1)[0]
    return text


def _has_signal(row: pd.Series, signal: str) -> bool:
    tokens = {token.strip().upper() for token in str(row.get("signals", "") or "").split(",")}
    return signal.upper() in tokens


def _prepare_rank(rank_csv: Path) -> pd.DataFrame:
    df = pd.read_csv(rank_csv)
    for column in NUMERIC_COLUMNS:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")
        else:
            df[column] = pd.NA
    for column in ["ticker", "name", "group", "layer", "signals", "score_band", "regime", "spec_risk_label", "volatility_tag", "grade"]:
        if column not in df.columns:
            df[column] = ""
        df[column] = df[column].fillna("").astype(str)
    df["_base"] = df["ticker"].map(_normalize_base)
    df["_risk_bucket"] = df.apply(_risk_bucket, axis=1)
    df["_research_score"] = df.apply(_research_score, axis=1)
    df["_action"] = df.apply(_action_label, axis=1)
    df["_reason"] = df.apply(_reason_text, axis=1)
    df = df.sort_values(by=["_base", "_research_score", "rank"], ascending=[True, False, True])
    return df.drop_duplicates(subset=["_base"], keep="first").reset_index(drop=True)


def _risk_bucket(row: pd.Series) -> str:
    label = str(row.get("spec_risk_label", "") or "").strip()
    score = _safe_number(row.get("spec_risk_score"))
    risk = _safe_number(row.get("risk_score"))
    if label == "疑似炒作風險高" or score >= 6 or risk >= 6:
        return "high"
    if label in {"投機偏高", "偏熱", "留意"} or score >= 3 or risk >= 4:
        return "watch"
    return "normal"


def _research_score(row: pd.Series) -> float:
    score = _safe_number(row.get("setup_score")) * 2
    score -= _safe_number(row.get("risk_score")) * 1.5
    score -= _safe_number(row.get("spec_risk_score"))
    score += min(max(_safe_number(row.get("ret20_pct")), -20.0), 40.0) / 5.0
    score -= max(_safe_number(row.get("atr_pct")) - 5.0, 0.0) * 0.7
    volume_ratio = _safe_number(row.get("volume_ratio20"))
    if 0 < volume_ratio <= 1.2:
        score += 1.5
    if _has_signal(row, "SURGE"):
        score -= 5.0
    if _has_signal(row, "PULLBACK"):
        score += 1.0
    return round(score, 2)


def _action_label(row: pd.Series) -> str:
    setup = _safe_number(row.get("setup_score"))
    risk_bucket = str(row.get("_risk_bucket", "normal"))
    if risk_bucket == "high" or _has_signal(row, "SURGE"):
        return "過熱先等"
    if setup >= 10 and risk_bucket == "normal":
        return "優先研究"
    if setup >= 8 and risk_bucket in {"normal", "watch"}:
        return "等拉回確認"
    if setup >= 4:
        return "觀察轉強"
    return "暫不急"


def _reason_text(row: pd.Series) -> str:
    parts: list[str] = []
    if _has_signal(row, "TREND"):
        parts.append("趨勢成立")
    if _has_signal(row, "PULLBACK"):
        parts.append("回檔中")
    if _safe_number(row.get("volume_ratio20")) <= 1.2:
        parts.append("量能未過熱")
    if str(row.get("_risk_bucket", "")) != "normal":
        parts.append(f"風險={row.get('spec_risk_label') or row.get('_risk_bucket')}")
    if not parts:
        parts.append(str(row.get("score_band", "") or "一般觀察"))
    return "、".join(parts)


def _select_low_price_pool(df: pd.DataFrame, args: argparse.Namespace) -> pd.DataFrame:
    mask = (
        (df["close"] <= float(args.low_price_max))
        & (df["setup_score"] >= float(args.min_setup))
        & (df["ret20_pct"] >= float(args.min_ret20))
        & (df["volume_ratio20"] <= float(args.max_volume_ratio))
        & (df["_risk_bucket"] != "high")
        & (~df.apply(lambda row: _has_signal(row, "SURGE"), axis=1))
    )
    return df[mask].sort_values(by=["_research_score", "close"], ascending=[False, True]).reset_index(drop=True)


def _select_quality_value_pool(df: pd.DataFrame) -> pd.DataFrame:
    mask = df["layer"].astype(str).eq("quality_value")
    return df[mask].sort_values(by=["_action", "_research_score"], ascending=[True, False]).reset_index(drop=True)


def _merge_fundamentals(export: pd.DataFrame, fundamentals: pd.DataFrame) -> pd.DataFrame:
    if export.empty or fundamentals.empty or "ticker" not in fundamentals.columns:
        return export
    work = export.copy()
    work["_fundamental_base"] = work["ticker"].map(_normalize_base)
    fundamentals = fundamentals.copy()
    fundamentals["_fundamental_base"] = fundamentals["ticker"].map(_normalize_base)
    fundamentals = fundamentals.drop_duplicates(subset=["_fundamental_base"], keep="first")
    merged = work.merge(
        fundamentals.drop(columns=["ticker"]),
        on="_fundamental_base",
        how="left",
    )
    return merged.drop(columns=["_fundamental_base"])


def _fetch_fundamentals(export: pd.DataFrame, *, enabled: bool, limit: int) -> pd.DataFrame:
    if not enabled or export.empty or limit <= 0:
        return pd.DataFrame()
    tickers = export["ticker"].astype(str).dropna().drop_duplicates().head(limit).tolist()
    if not tickers:
        return pd.DataFrame()
    return FinMindFundamentalProvider().fetch_many(tickers)


def _prefer_cached_full_fundamentals(current: pd.DataFrame, cache_path: Path) -> pd.DataFrame:
    if not cache_path.exists():
        return current
    try:
        cached = pd.read_csv(cache_path)
    except Exception:
        return current
    if cached.empty or "ticker" not in cached.columns:
        return current
    if "fundamental_data_status" not in cached.columns:
        return current
    cached = cached[cached["fundamental_data_status"].astype(str) == "ok"].copy()
    if cached.empty:
        return current
    if current.empty or "ticker" not in current.columns:
        return cached

    current = current.copy()
    cached = cached.drop_duplicates(subset=["ticker"], keep="first").set_index("ticker")
    for index, row in current.iterrows():
        ticker = str(row.get("ticker", ""))
        status = str(row.get("fundamental_data_status", ""))
        if status == "ok" or ticker not in cached.index:
            continue
        for column, value in cached.loc[ticker].items():
            if column in current.columns:
                current.at[index, column] = value
    return current


def _watchlist_tickers(path: Path = Path("watchlist.csv")) -> set[str]:
    if not path.exists():
        return set()
    try:
        df = pd.read_csv(path)
    except Exception:
        return set()
    if "ticker" not in df.columns:
        return set()
    return set(df["ticker"].dropna().astype(str).str.strip())


def _fetch_stock_info_universe(session: requests.Session | None = None) -> pd.DataFrame:
    session = session or requests.Session()
    token = _local_env_value("FINMIND_TOKEN")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    response = session.get(FINMIND_API_URL, params={"dataset": "TaiwanStockInfo"}, headers=headers, timeout=20)
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data") or []
    frame = pd.DataFrame(data)
    if frame.empty:
        return pd.DataFrame(columns=["ticker", "stock_id", "name", "industry_category", "type"])
    frame = frame[frame["type"].astype(str).isin(["twse", "tpex"])].copy()
    frame["ticker"] = frame.apply(
        lambda row: f"{str(row.get('stock_id', '')).strip()}.{('TW' if str(row.get('type', '')).strip() == 'twse' else 'TWO')}",
        axis=1,
    )
    frame["name"] = frame.get("stock_name", pd.Series(index=frame.index, dtype=object)).fillna("").astype(str)
    frame["industry_category"] = frame.get("industry_category", pd.Series(index=frame.index, dtype=object)).fillna("").astype(str)
    frame["_specificity"] = frame["industry_category"].map(lambda value: 0 if value in {"電子工業", "其他"} else 1)
    frame = frame.sort_values(by=["ticker", "date", "_specificity"], ascending=[True, False, False])
    return frame.drop_duplicates(subset=["ticker"], keep="first")[
        ["ticker", "stock_id", "name", "industry_category", "type"]
    ].reset_index(drop=True)


def _fetch_official_stock_info_universe(session: requests.Session | None = None) -> pd.DataFrame:
    session = session or requests.Session()
    rows: list[dict[str, object]] = []
    twse_response = session.get(TWSE_COMPANY_INFO_URL, headers={"User-Agent": "stock-watch/1.0"}, timeout=20)
    twse_response.raise_for_status()
    twse_payload = twse_response.json()
    if isinstance(twse_payload, list):
        for item in twse_payload:
            if not isinstance(item, dict):
                continue
            stock_id = str(item.get("公司代號", "")).strip()
            industry_code = str(item.get("產業別", "")).strip()
            rows.append(
                {
                    "ticker": f"{stock_id}.TW",
                    "stock_id": stock_id,
                    "name": str(item.get("公司簡稱", "")).strip(),
                    "industry_category": INDUSTRY_CODE_LABELS.get(industry_code, industry_code),
                    "type": "twse",
                }
            )
    tpex_response = session.get(TPEX_COMPANY_INFO_URL, headers={"User-Agent": "stock-watch/1.0"}, timeout=20)
    tpex_response.raise_for_status()
    tpex_payload = tpex_response.json()
    if isinstance(tpex_payload, list):
        for item in tpex_payload:
            if not isinstance(item, dict):
                continue
            stock_id = str(item.get("SecuritiesCompanyCode", "")).strip()
            industry_code = str(item.get("SecuritiesIndustryCode", "")).strip()
            rows.append(
                {
                    "ticker": f"{stock_id}.TWO",
                    "stock_id": stock_id,
                    "name": str(item.get("CompanyAbbreviation", "")).strip(),
                    "industry_category": INDUSTRY_CODE_LABELS.get(industry_code, industry_code),
                    "type": "tpex",
                }
            )
    if not rows:
        return pd.DataFrame(columns=["ticker", "stock_id", "name", "industry_category", "type"])
    return pd.DataFrame(rows).drop_duplicates(subset=["ticker"], keep="first").reset_index(drop=True)


def build_similar_scout(
    *,
    existing_tickers: set[str] | None = None,
    candidate_limit: int = 120,
    output_limit: int = 20,
    session: requests.Session | None = None,
) -> pd.DataFrame:
    existing = {str(ticker).strip() for ticker in (existing_tickers or set()) if str(ticker).strip()}
    provider = OfficialValuationProvider(timeout=30)
    try:
        valuations = provider.fetch_all()
    except Exception:
        return pd.DataFrame()
    if valuations.empty:
        return pd.DataFrame()

    try:
        universe = _fetch_stock_info_universe(session=session)
    except Exception:
        try:
            universe = _fetch_official_stock_info_universe(session=session)
        except Exception:
            universe = pd.DataFrame()
    if not universe.empty:
        universe = universe[universe["industry_category"].isin(SIMILAR_SCOUT_INDUSTRIES)].copy()
    if universe.empty:
        universe = valuations[["ticker", "stock_id", "name"]].copy()
        universe["industry_category"] = "public_fundamental_seed"
        universe["type"] = universe["ticker"].map(lambda ticker: "tpex" if str(ticker).endswith(".TWO") else "twse")

    if existing:
        universe = universe[~universe["ticker"].astype(str).isin(existing)].copy()
    if universe.empty:
        return pd.DataFrame()

    valuation_cols = ["ticker", "pe", "pbr", "dividend_yield", "name"]
    valuation_work = valuations[[col for col in valuation_cols if col in valuations.columns]].copy()
    if "name" in valuation_work.columns:
        valuation_work = valuation_work.rename(columns={"name": "valuation_name"})
    pool = universe.merge(valuation_work, on="ticker", how="left")
    if "valuation_name" in pool.columns:
        pool["name"] = pool["name"].fillna("").astype(str)
        pool.loc[pool["name"].str.strip() == "", "name"] = pool.loc[pool["name"].str.strip() == "", "valuation_name"]
    for column in ["pe", "pbr", "dividend_yield"]:
        pool[column] = pd.to_numeric(pool.get(column), errors="coerce")
    pool = pool[
        pool["pe"].between(0.1, 28.0, inclusive="both")
        & pool["pbr"].between(0.1, 4.5, inclusive="both")
        & (pool["dividend_yield"].fillna(0) >= 1.5)
    ].copy()
    if pool.empty:
        return pd.DataFrame()
    pool["_valuation_seed_score"] = (
        (28.0 - pool["pe"]).clip(lower=0) * 0.35
        + (4.5 - pool["pbr"]).clip(lower=0) * 1.2
        + pool["dividend_yield"].fillna(0) * 0.8
    )
    pool = pool.sort_values(by=["_valuation_seed_score", "dividend_yield"], ascending=[False, False]).head(candidate_limit)

    fundamentals = provider.fetch_many(pool["ticker"].astype(str).tolist())
    if fundamentals.empty:
        return pd.DataFrame()
    scout = pool[["ticker", "name", "industry_category", "type"]].merge(fundamentals, on="ticker", how="left")
    for column in ["quality_score", "value_score", "pe", "pbr", "dividend_yield", "revenue_yoy_pct", "roe_pct", "debt_to_equity_pct"]:
        scout[column] = pd.to_numeric(scout.get(column), errors="coerce")
    scout = scout[(scout["quality_score"] >= 3) & (scout["value_score"] >= 2)].copy()
    if scout.empty:
        return pd.DataFrame()
    scout["similar_score"] = (
        scout["quality_score"].fillna(0) * 2.0
        + scout["value_score"].fillna(0) * 1.5
        + scout["dividend_yield"].fillna(0) * 0.5
        + (scout["revenue_yoy_pct"].fillna(0).clip(lower=0, upper=50) / 25)
        + (scout["roe_pct"].fillna(0).clip(lower=0, upper=30) / 15)
        - (scout["pe"].fillna(99).clip(lower=0, upper=99) / 25)
    ).round(2)
    scout["scout_reason"] = scout.apply(
        lambda row: (
            f"{row.get('industry_category', '')}；"
            f"Q/V={_format_number(row.get('quality_score'), 0)}/{_format_number(row.get('value_score'), 0)}；"
            f"PE={_format_number(row.get('pe'))}；殖利率={_format_with_suffix(row.get('dividend_yield'), '%')}"
        ),
        axis=1,
    )
    scout = _add_scout_priority(scout)
    return scout.sort_values(
        by=["radar_sort", "similar_score", "quality_score", "value_score"],
        ascending=[False, False, False, False],
    ).head(output_limit).reset_index(drop=True)


def _add_scout_priority(scout: pd.DataFrame) -> pd.DataFrame:
    work = scout.copy()
    for column in ["quality_score", "value_score", "pe", "pbr", "dividend_yield", "revenue_yoy_pct", "roe_pct", "debt_to_equity_pct"]:
        work[column] = pd.to_numeric(work.get(column), errors="coerce")

    def _priority(row: pd.Series) -> tuple[str, int, str]:
        quality = _safe_number(row.get("quality_score"))
        value = _safe_number(row.get("value_score"))
        pe = _safe_number(row.get("pe"))
        revenue_yoy = _safe_number(row.get("revenue_yoy_pct"))
        roe = _safe_number(row.get("roe_pct"))
        debt_to_equity = row.get("debt_to_equity_pct")
        debt_ok = pd.isna(debt_to_equity) or _safe_number(debt_to_equity) <= 120
        reasons: list[str] = []
        if quality >= 4:
            reasons.append("品質分數高")
        if value >= 4:
            reasons.append("估值分數高")
        elif value >= 3:
            reasons.append("估值合理")
        if revenue_yoy > 0:
            reasons.append("營收YoY正")
        if roe >= 12:
            reasons.append("ROE>=12%")
        if pe and pe <= 18:
            reasons.append("PE<=18")
        if debt_ok:
            reasons.append("負債可控")
        else:
            reasons.append("負債偏高需確認")

        if quality >= 4 and value >= 3 and revenue_yoy >= 0 and debt_ok and (roe >= 10 or pe <= 16):
            return "A加入觀察", 3, "、".join(reasons)
        if (quality >= 4 and value >= 2) or (quality >= 3 and value >= 3):
            return "B研究追蹤", 2, "、".join(reasons)
        return "C等待確認", 1, "、".join(reasons) if reasons else "基本面種子，需補技術確認"

    priority_rows = work.apply(_priority, axis=1)
    work["radar_priority"] = [item[0] for item in priority_rows]
    work["radar_sort"] = [item[1] for item in priority_rows]
    work["radar_reason"] = [item[2] for item in priority_rows]
    return work


def build_scout_watchlist_draft(scout: pd.DataFrame, *, limit: int = 12) -> pd.DataFrame:
    if scout.empty:
        return pd.DataFrame(columns=SCOUT_WATCHLIST_DRAFT_COLUMNS)
    work = scout.copy()
    if "radar_sort" not in work.columns:
        work = _add_scout_priority(work)
    work["radar_sort"] = pd.to_numeric(work.get("radar_sort"), errors="coerce").fillna(0)
    work["similar_score"] = pd.to_numeric(work.get("similar_score"), errors="coerce").fillna(0)
    work = work[work["radar_priority"].astype(str).isin(["A加入觀察", "B研究追蹤"])].copy()
    if work.empty:
        return pd.DataFrame(columns=SCOUT_WATCHLIST_DRAFT_COLUMNS)
    work = work.sort_values(by=["radar_sort", "similar_score", "ticker"], ascending=[False, False, True]).head(limit)
    draft = pd.DataFrame(
        {
            "ticker": work["ticker"].astype(str),
            "name": work["name"].astype(str),
            "group": "satellite",
            "layer": "quality_value",
            "enabled": True,
            "radar_priority": work["radar_priority"].astype(str),
            "similar_score": work["similar_score"].round(2),
            "watchlist_reason": work.get("radar_reason", pd.Series(index=work.index, dtype=object)).fillna("").astype(str),
        }
    )
    return draft.reset_index(drop=True)


def _format_number(value: object, digits: int = 2) -> str:
    number = _safe_number(value)
    if not number:
        if pd.isna(value) or str(value).strip() in {"", "nan", "<NA>"}:
            return ""
    return f"{number:.{digits}f}"


def _format_with_suffix(value: object, suffix: str, digits: int = 2) -> str:
    text = _format_number(value, digits)
    return f"{text}{suffix}" if text else ""


def _atr_amount(row: pd.Series) -> float:
    close = _safe_number(row.get("close"))
    atr_pct = _safe_number(row.get("atr_pct"))
    return close * atr_pct / 100.0 if close and atr_pct else 0.0


def _zone_text(low: object, high: object) -> str:
    low_text = _format_number(low)
    high_text = _format_number(high)
    return f"{low_text}–{high_text}" if low_text and high_text else ""


def _entry_bias(row: pd.Series) -> str:
    action = str(row.get("_action", ""))
    fundamental_action = str(row.get("fundamental_action", ""))
    if action == "過熱先等":
        return "等待降溫"
    if action == "優先研究" and fundamental_action == "品質價值優先":
        return "分批試單"
    if action == "優先研究":
        return "研究試單"
    if action == "等拉回確認":
        return "等拉回"
    if action == "觀察轉強":
        return "等轉強"
    return "暫不急"


def _entry_plan_for_row(row: pd.Series) -> dict[str, object]:
    close = _safe_number(row.get("close"))
    ma20 = _safe_number(row.get("ma20"))
    ma60 = _safe_number(row.get("ma60"))
    atr = _atr_amount(row)
    atr_pct = _safe_number(row.get("atr_pct"))
    quality_score = _safe_number(row.get("quality_score"))
    value_score = _safe_number(row.get("value_score"))
    setup_score = _safe_number(row.get("setup_score"))
    risk_score = _safe_number(row.get("risk_score"))
    spec_risk_score = _safe_number(row.get("spec_risk_score"))
    action = str(row.get("_action", ""))
    bias = _entry_bias(row)

    if action == "過熱先等":
        zone_low = ma20 if ma20 else close * 0.92
        zone_high = (ma20 + atr) if ma20 and atr else close * 0.96
    elif action == "觀察轉強":
        zone_low = ma20 if ma20 else close
        zone_high = (ma20 + atr * 0.5) if ma20 and atr else close * 1.02
    elif action == "等拉回確認":
        zone_low = max(ma20, close - atr * 1.5) if ma20 and atr else close * 0.96
        zone_high = max(ma20, close - atr * 0.5) if ma20 and atr else close * 0.99
    elif action == "優先研究":
        zone_low = max(ma20, close - atr) if ma20 and atr else close * 0.97
        zone_high = close
    else:
        zone_low = ma20 if ma20 else close * 0.95
        zone_high = close

    zone_mid = (zone_low + zone_high) / 2 if zone_low and zone_high else close
    stop_pct = max(atr_pct * 2, 6.0)
    stop_by_pct = zone_mid * (1 - stop_pct / 100.0) if zone_mid else 0.0
    stop_by_ma = ma20 - atr * 1.5 if ma20 and atr else 0.0
    stop_loss = max(stop_by_pct, stop_by_ma) if stop_by_pct and stop_by_ma else stop_by_pct or stop_by_ma
    if ma60 and stop_loss and stop_loss > ma60 and action in {"觀察轉強", "等拉回確認"}:
        stop_loss = ma60

    priority = quality_score * 2.0 + value_score * 1.5 + setup_score - risk_score * 2.0 - spec_risk_score
    if action == "優先研究":
        priority += 3.0
    if action == "過熱先等":
        priority -= 5.0
    if str(row.get("fundamental_action", "")) == "品質價值優先":
        priority += 3.0
    if _has_signal(row, "SURGE"):
        priority -= 4.0

    if action == "過熱先等":
        add_rule = "不追高；等量縮回 MA20 附近後，再看站回 5 日高點"
        trim_rule = "若已持有，急拉或跌破前一日低點先降風險"
    elif action == "觀察轉強":
        add_rule = "收盤站回 MA20 且量能回到 0.8–1.4 倍，再開第一筆"
        trim_rule = "未站回 MA20 前不加碼；跌破停損直接退出觀察單"
    elif action == "等拉回確認":
        add_rule = "回到買區後若守住 MA20，第二天轉強再加"
        trim_rule = "跌破買區下緣或放量轉弱先撤"
    elif action == "優先研究":
        add_rule = "第一筆 1/3；突破前高且未爆量再加 1/3"
        trim_rule = "跌破停損或出現 SURGE+爆量失衡先減"
    else:
        add_rule = "只追蹤，不主動建立部位"
        trim_rule = "訊號改善前不處理"

    reasons = [
        f"technical={action}",
        f"fundamental={row.get('fundamental_action', '') or 'n/a'}",
        f"Q/V={_format_number(quality_score, 0)}/{_format_number(value_score, 0)}",
    ]
    if _has_signal(row, "SURGE"):
        reasons.append("SURGE")
    if _has_signal(row, "PULLBACK"):
        reasons.append("PULLBACK")

    return {
        "ticker": row.get("ticker", ""),
        "name": row.get("name", ""),
        "bucket": row.get("bucket", ""),
        "decision_priority": round(priority, 2),
        "entry_bias": bias,
        "buy_zone_low": round(zone_low, 2) if zone_low else "",
        "buy_zone_high": round(zone_high, 2) if zone_high else "",
        "stop_loss": round(stop_loss, 2) if stop_loss else "",
        "add_rule": add_rule,
        "trim_rule": trim_rule,
        "decision_reason": "、".join(reasons),
    }


def build_entry_plan(export: pd.DataFrame) -> pd.DataFrame:
    if export.empty:
        return pd.DataFrame(columns=ENTRY_PLAN_COLUMNS)
    work = export.copy()
    for column in ["quality_score", "value_score"]:
        if column not in work.columns:
            work[column] = 0
    rows = [_entry_plan_for_row(row) for _, row in work.iterrows()]
    plan = pd.DataFrame(rows, columns=ENTRY_PLAN_COLUMNS)
    if plan.empty:
        return plan
    plan["decision_priority"] = pd.to_numeric(plan["decision_priority"], errors="coerce").fillna(0)
    return plan.sort_values(by=["decision_priority", "ticker"], ascending=[False, True]).reset_index(drop=True)


def _entry_plan_table(plan: pd.DataFrame, *, limit: int = 12) -> list[str]:
    if plan.empty:
        return ["- None"]
    lines = [
        "| Ticker | Name | Priority | Bias | Buy Zone | Stop | Add Rule | Trim Rule |",
        "| --- | --- | ---: | --- | ---: | ---: | --- | --- |",
    ]
    for _, row in plan.head(limit).iterrows():
        lines.append(
            f"| {row.get('ticker', '')} | {row.get('name', '')} | {_format_number(row.get('decision_priority'))} | "
            f"{row.get('entry_bias', '')} | {_zone_text(row.get('buy_zone_low'), row.get('buy_zone_high'))} | "
            f"{_format_number(row.get('stop_loss'))} | {row.get('add_rule', '')} | {row.get('trim_rule', '')} |"
        )
    return lines


def _similar_scout_table(scout: pd.DataFrame, *, limit: int = 15) -> list[str]:
    if scout.empty:
        return ["- None"]
    lines = [
        "| Ticker | Name | Industry | Priority | Score | Fundamental | Q/V | PE | PBR | Yield | Rev YoY | ROE | Reason |",
        "| --- | --- | --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for _, row in scout.head(limit).iterrows():
        lines.append(
            f"| {row.get('ticker', '')} | {row.get('name', '')} | {row.get('industry_category', '')} | "
            f"{row.get('radar_priority', '')} | {_format_number(row.get('similar_score'))} | {row.get('fundamental_action', '')} | "
            f"{_format_number(row.get('quality_score'), 0)}/{_format_number(row.get('value_score'), 0)} | "
            f"{_format_number(row.get('pe'))} | {_format_number(row.get('pbr'))} | {_format_with_suffix(row.get('dividend_yield'), '%')} | "
            f"{_format_with_suffix(row.get('revenue_yoy_pct'), '%')} | {_format_with_suffix(row.get('roe_pct'), '%')} | "
            f"{row.get('radar_reason', '') or row.get('scout_reason', '')} |"
        )
    return lines


def _scout_watchlist_draft_table(draft: pd.DataFrame, *, limit: int = 12) -> list[str]:
    if draft.empty:
        return ["- None"]
    lines = [
        "| Ticker | Name | Priority | Score | Watchlist Row | Reason |",
        "| --- | --- | --- | ---: | --- | --- |",
    ]
    for _, row in draft.head(limit).iterrows():
        lines.append(
            f"| {row.get('ticker', '')} | {row.get('name', '')} | {row.get('radar_priority', '')} | "
            f"{_format_number(row.get('similar_score'))} | "
            f"`{row.get('ticker', '')},{row.get('name', '')},satellite,quality_value,true` | "
            f"{row.get('watchlist_reason', '')} |"
        )
    return lines


def build_scout_notification(scout: pd.DataFrame, *, limit: int = 5) -> str:
    if scout.empty:
        return "🔎 類似標的雷達\n今天沒有新的 A/B 級研究種子。"
    work = scout.copy()
    if "radar_sort" not in work.columns:
        work = _add_scout_priority(work)
    work["radar_sort"] = pd.to_numeric(work.get("radar_sort"), errors="coerce").fillna(0)
    work["similar_score"] = pd.to_numeric(work.get("similar_score"), errors="coerce").fillna(0)
    work = work[work["radar_priority"].astype(str).isin(["A加入觀察", "B研究追蹤"])].copy()
    if work.empty:
        return "🔎 類似標的雷達\n今天沒有新的 A/B 級研究種子。"
    work = work.sort_values(by=["radar_sort", "similar_score", "ticker"], ascending=[False, False, True]).head(limit)
    lines = ["🔎 類似標的雷達", "規則：先加觀察，不自動買；等日線價量確認。"]
    for _, row in work.iterrows():
        emoji = "🟢" if str(row.get("radar_priority")) == "A加入觀察" else "🟡"
        lines.append(
            f"{emoji} {row.get('name', '')}({row.get('ticker', '')})｜{row.get('radar_priority', '')}｜"
            f"Score {_format_number(row.get('similar_score'))}｜PE {_format_number(row.get('pe'))}｜"
            f"殖利率 {_format_with_suffix(row.get('dividend_yield'), '%')}"
        )
    return "\n".join(lines)


def build_quality_value_notification(entry_plan: pd.DataFrame, scout: pd.DataFrame | None = None) -> str:
    parts = [build_entry_plan_notification(entry_plan)]
    if scout is not None:
        scout_message = build_scout_notification(scout)
        if scout_message.strip():
            parts.append(scout_message)
    return "\n\n".join(parts)


def build_entry_plan_notification(entry_plan: pd.DataFrame, *, limit: int = 6) -> str:
    if entry_plan.empty:
        return "🧭 品質價值買點雷達\n今天沒有符合條件的新買點。"
    work = entry_plan.copy()
    work["decision_priority"] = pd.to_numeric(work.get("decision_priority"), errors="coerce").fillna(0)
    work = work.sort_values(by=["decision_priority", "ticker"], ascending=[False, True]).head(limit)
    lines = ["🧭 品質價值買點雷達", "規則：不追過熱；先看買區、停損、加碼條件。"]
    for _, row in work.iterrows():
        bias = str(row.get("entry_bias", ""))
        emoji = "🟢" if bias in {"分批試單", "研究試單"} else "🟡" if bias in {"等轉強", "等拉回"} else "🔴"
        lines.append(
            f"{emoji} {row.get('name', '')}({row.get('ticker', '')})｜{bias}｜"
            f"買區 {_zone_text(row.get('buy_zone_low'), row.get('buy_zone_high'))}｜停損 {_format_number(row.get('stop_loss'))}"
        )
    return "\n".join(lines)


def _markdown_table(df: pd.DataFrame, *, limit: int = 15) -> list[str]:
    if df.empty:
        return ["- None"]
    lines = [
        "| Rank | Ticker | Name | Close | 20D | Vol/20 | Setup | Risk | Action | Reason |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    for _, row in df.head(limit).iterrows():
        rank = "" if pd.isna(row.get("rank")) else str(int(float(row.get("rank"))))
        lines.append(
            f"| {rank} | {row.get('ticker', '')} | {row.get('name', '')} | "
            f"{_format_number(row.get('close'))} | {_format_with_suffix(row.get('ret20_pct'), '%')} | "
            f"{_format_number(row.get('volume_ratio20'))} | {_format_number(row.get('setup_score'), 0)} | "
            f"{_format_number(row.get('risk_score'), 0)} | {row.get('_action', '')} | {row.get('_reason', '')} |"
        )
    return lines


def _fundamental_table(df: pd.DataFrame, *, limit: int = 20) -> list[str]:
    if df.empty or "fundamental_action" not in df.columns:
        return ["- None"]
    work = df.copy()
    if "quality_score" not in work.columns:
        return ["- None"]
    work["quality_score"] = pd.to_numeric(work["quality_score"], errors="coerce").fillna(0)
    work["value_score"] = pd.to_numeric(work.get("value_score"), errors="coerce").fillna(0)
    work = work.sort_values(
        by=["quality_score", "value_score", "_research_score"],
        ascending=[False, False, False],
    )
    lines = [
        "| Ticker | Name | Fundamental | Q/V | PE | PBR | Yield | Rev YoY | ROE | D/E | FCF TTM | Reason |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for _, row in work.head(limit).iterrows():
        lines.append(
            f"| {row.get('ticker', '')} | {row.get('name', '')} | {row.get('fundamental_action', '')} | "
            f"{_format_number(row.get('quality_score'), 0)}/{_format_number(row.get('value_score'), 0)} | "
            f"{_format_number(row.get('pe'))} | {_format_number(row.get('pbr'))} | {_format_with_suffix(row.get('dividend_yield'), '%')} | "
            f"{_format_with_suffix(row.get('revenue_yoy_pct'), '%')} | {_format_with_suffix(row.get('roe_pct'), '%')} | "
            f"{_format_with_suffix(row.get('debt_to_equity_pct'), '%')} | {_format_number(row.get('free_cashflow_ttm'), 0)} | "
            f"{row.get('fundamental_reason', '')} |"
        )
    return lines


def build_quality_value_report(
    df_rank: pd.DataFrame,
    *,
    args: argparse.Namespace,
    generated_at: str,
) -> tuple[str, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    low_price = _select_low_price_pool(df_rank, args)
    quality_value = _select_quality_value_pool(df_rank)
    export = pd.concat(
        [
            low_price.assign(bucket="low_price_health"),
            quality_value.assign(bucket="quality_value_research"),
        ],
        ignore_index=True,
    )
    export = export.drop_duplicates(subset=["bucket", "_base"], keep="first")
    fundamentals = _fetch_fundamentals(
        export,
        enabled=bool(args.fundamentals),
        limit=int(args.fundamental_limit),
    )
    fundamentals = _prefer_cached_full_fundamentals(fundamentals, Path(args.outdir) / "quality_value_fundamentals.csv")
    export = _merge_fundamentals(export, fundamentals)
    entry_plan = build_entry_plan(export)
    scout = (
        build_similar_scout(
            existing_tickers=_watchlist_tickers() | set(export["ticker"].astype(str)),
            candidate_limit=int(args.scout_candidate_limit),
            output_limit=int(args.scout_limit),
        )
        if bool(args.similar_scout)
        else pd.DataFrame()
    )
    scout_draft = build_scout_watchlist_draft(scout, limit=int(args.scout_draft_limit))

    lines = [
        "# 冷門高品質 / 低價健康 Research",
        f"- Generated: {generated_at}",
        f"- Source: `{args.rank_csv}`",
        f"- Low-price rules: `close <= {args.low_price_max:g}`, `setup >= {args.min_setup:g}`, `ret20 >= {args.min_ret20:g}%`, `volume_ratio20 <= {args.max_volume_ratio:g}`, exclude `SURGE` and high spec risk.",
        "",
        "## 低價健康候選",
        "",
        *_markdown_table(low_price),
        "",
        "## 冷門高品質研究池",
        "",
        *_markdown_table(quality_value),
        "",
        "## 基本面 Overlay",
        "",
        "- Data priority: `FINMIND_TOKEN` full fundamentals → official TWSE/TPEx public filings → official valuation-only fallback.",
        "- Official public filings provide latest reported quarter + latest monthly revenue; FCF and true TTM fields require FinMind.",
        "",
        *_fundamental_table(export),
        "",
        "## 買點 / 停損 / 加碼紀律",
        "",
        "- `Buy Zone` 是研究用價格區間，不是市價追單；先用小部位驗證，再依規則加碼。",
        "- `Stop` 優先視為收盤跌破或放量跌破的風控線；若隔日跳空失守，先處理風險。",
        "",
        *_entry_plan_table(entry_plan),
        "",
        "## 更多類似標的雷達",
        "",
        "- Scope: 類似 `3034/3005/3044` 的電子/半導體/零組件/電腦週邊族群；排除既有 watchlist，先用公開基本面找研究種子。",
        "- 注意：這是 `seed list`，還沒通過日線技術與流動性確認；下一步要加入 watchlist 觀察價量。",
        "",
        *_similar_scout_table(scout),
        "",
        "## Watchlist 加入草稿",
        "",
        "- 這裡只產生草稿，不會自動改 `watchlist.csv`；A/B 級標的等你決定後再正式加入。",
        "",
        *_scout_watchlist_draft_table(scout_draft),
        "",
        "## 使用方式",
        "",
        "- `優先研究`：價量結構乾淨，值得深入看基本面與買點。",
        "- `等拉回確認`：題材/趨勢可看，但不要用追價心態處理。",
        "- `觀察轉強`：先放研究池，等 setup_score 或量價轉強。",
        "- `過熱先等`：先看不碰，等熱度退或重新整理。",
        "",
    ]
    return "\n".join(lines), export, fundamentals, entry_plan, scout, scout_draft


def _write_metrics(
    *,
    outdir: Path,
    generated_at: str,
    rows: int,
    low_price_rows: int,
    quality_value_rows: int,
    fundamental_rows: int,
    scout_rows: int,
    scout_draft_rows: int,
    wall_seconds: float,
) -> None:
    payload = {
        "generated_at": generated_at,
        "status": "ok",
        "rows": int(rows),
        "low_price_rows": int(low_price_rows),
        "quality_value_rows": int(quality_value_rows),
        "fundamental_rows": int(fundamental_rows),
        "scout_rows": int(scout_rows),
        "scout_draft_rows": int(scout_draft_rows),
        "wall_seconds": round(wall_seconds, 3),
    }
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "quality_value_metrics.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (outdir / "quality_value_metrics.md").write_text(
        "\n".join(
            [
                "# Quality Value Metrics",
                f"- Generated: {generated_at}",
                "- Status: `ok`",
                f"- Rows: `{rows}`",
                f"- Low-price rows: `{low_price_rows}`",
                f"- Quality-value rows: `{quality_value_rows}`",
                f"- Fundamental rows: `{fundamental_rows}`",
                f"- Similar scout rows: `{scout_rows}`",
                f"- Similar scout draft rows: `{scout_draft_rows}`",
                f"- Wall-clock seconds: `{wall_seconds:.3f}`",
            ]
        ),
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    started = time.perf_counter()
    args = parse_args(argv)
    rank_csv = Path(args.rank_csv)
    outdir = Path(args.outdir)
    if not rank_csv.exists():
        print(f"daily_rank.csv not found: {rank_csv}")
        return 1

    df_rank = _prepare_rank(rank_csv)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    markdown, export, fundamentals, entry_plan, scout, scout_draft = build_quality_value_report(df_rank, args=args, generated_at=generated_at)

    outdir.mkdir(parents=True, exist_ok=True)
    report_md = outdir / "quality_value_report.md"
    report_csv = outdir / "quality_value_candidates.csv"
    fundamentals_csv = outdir / "quality_value_fundamentals.csv"
    entry_plan_csv = outdir / "quality_value_entry_plan.csv"
    scout_csv = outdir / "quality_value_similar_scout.csv"
    scout_draft_csv = outdir / "quality_value_watchlist_draft.csv"
    report_md.write_text(markdown, encoding="utf-8")

    export_cols = [
        "bucket",
        "rank",
        "ticker",
        "name",
        "group",
        "layer",
        "close",
        "ret5_pct",
        "ret20_pct",
        "volume_ratio20",
        "setup_score",
        "risk_score",
        "spec_risk_label",
        "signals",
        "score_band",
        "atr_pct",
        "_research_score",
        "_action",
        "_reason",
        "pe",
        "pbr",
        "dividend_yield",
        "revenue_yoy_pct",
        "eps_ttm",
        "eps_yoy_pct",
        "roe_pct",
        "gross_margin_pct",
        "operating_margin_pct",
        "debt_to_equity_pct",
        "current_ratio",
        "free_cashflow_ttm",
        "quality_score",
        "value_score",
        "fundamental_action",
        "fundamental_reason",
        "fundamental_data_status",
    ]
    export[[col for col in export_cols if col in export.columns]].to_csv(report_csv, index=False, encoding="utf-8-sig")
    if not fundamentals.empty:
        fundamentals.to_csv(fundamentals_csv, index=False, encoding="utf-8-sig")
    entry_plan.to_csv(entry_plan_csv, index=False, encoding="utf-8-sig")
    if not scout.empty:
        scout.to_csv(scout_csv, index=False, encoding="utf-8-sig")
    scout_draft.to_csv(scout_draft_csv, index=False, encoding="utf-8-sig")

    low_price_rows = int((export.get("bucket", pd.Series(dtype=str)) == "low_price_health").sum())
    quality_value_rows = int((export.get("bucket", pd.Series(dtype=str)) == "quality_value_research").sum())
    wall_seconds = time.perf_counter() - started
    _write_metrics(
        outdir=outdir,
        generated_at=generated_at,
        rows=len(export),
        low_price_rows=low_price_rows,
        quality_value_rows=quality_value_rows,
        fundamental_rows=len(fundamentals),
        scout_rows=len(scout),
        scout_draft_rows=len(scout_draft),
        wall_seconds=wall_seconds,
    )

    print(f"Wrote quality/value report from {rank_csv}")
    print(f"- markdown: {report_md}")
    print(f"- csv: {report_csv}")
    if not fundamentals.empty:
        print(f"- fundamentals: {fundamentals_csv}")
    print(f"- entry_plan: {entry_plan_csv}")
    if not scout.empty:
        print(f"- similar_scout: {scout_csv}")
    print(f"- watchlist_draft: {scout_draft_csv}")
    print(f"- rows: {len(export)}")
    print(f"- wall_seconds: {wall_seconds:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
