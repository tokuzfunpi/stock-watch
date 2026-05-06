from __future__ import annotations

import argparse
import json
import math
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

from stock_watch.data.fundamentals import FinMindFundamentalProvider
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
        default=20,
        help="Maximum selected tickers to enrich with fundamentals.",
    )
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


def _format_number(value: object, digits: int = 2) -> str:
    number = _safe_number(value)
    if not number:
        if pd.isna(value) or str(value).strip() in {"", "nan", "<NA>"}:
            return ""
    return f"{number:.{digits}f}"


def _format_with_suffix(value: object, suffix: str, digits: int = 2) -> str:
    text = _format_number(value, digits)
    return f"{text}{suffix}" if text else ""


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
) -> tuple[str, pd.DataFrame, pd.DataFrame]:
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
    export = _merge_fundamentals(export, fundamentals)

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
        "## 使用方式",
        "",
        "- `優先研究`：價量結構乾淨，值得深入看基本面與買點。",
        "- `等拉回確認`：題材/趨勢可看，但不要用追價心態處理。",
        "- `觀察轉強`：先放研究池，等 setup_score 或量價轉強。",
        "- `過熱先等`：先看不碰，等熱度退或重新整理。",
        "",
    ]
    return "\n".join(lines), export, fundamentals


def _write_metrics(
    *,
    outdir: Path,
    generated_at: str,
    rows: int,
    low_price_rows: int,
    quality_value_rows: int,
    fundamental_rows: int,
    wall_seconds: float,
) -> None:
    payload = {
        "generated_at": generated_at,
        "status": "ok",
        "rows": int(rows),
        "low_price_rows": int(low_price_rows),
        "quality_value_rows": int(quality_value_rows),
        "fundamental_rows": int(fundamental_rows),
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
    markdown, export, fundamentals = build_quality_value_report(df_rank, args=args, generated_at=generated_at)

    outdir.mkdir(parents=True, exist_ok=True)
    report_md = outdir / "quality_value_report.md"
    report_csv = outdir / "quality_value_candidates.csv"
    fundamentals_csv = outdir / "quality_value_fundamentals.csv"
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
        wall_seconds=wall_seconds,
    )

    print(f"Wrote quality/value report from {rank_csv}")
    print(f"- markdown: {report_md}")
    print(f"- csv: {report_csv}")
    if not fundamentals.empty:
        print(f"- fundamentals: {fundamentals_csv}")
    print(f"- rows: {len(export)}")
    print(f"- wall_seconds: {wall_seconds:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
