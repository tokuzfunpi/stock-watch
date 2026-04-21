from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import pandas as pd
import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from daily_theme_watchlist import (
    LOCAL_TZ,
    CONFIG,
    is_midlong_buyable,
    is_short_term_buyable,
    rank_midlong_pool,
    rank_short_term_pool,
    select_midlong_backup_candidates,
    select_midlong_candidates,
    select_short_term_backup_candidates,
    select_short_term_candidates,
    midlong_action_label,
    short_term_action_label,
)


@dataclass(frozen=True)
class VerificationHeuristics:
    warn_overheated_ret5_pct: float = 18.0
    warn_high_risk_score: int = 5
    warn_low_volume_ratio: float = 0.9


DEFAULT_IMPROVEMENT_NOTES = [
    "- 若短線多為「開高不追 / 只觀察不追」，可考慮降低 `top_n_short` 或收斂追價條件。",
    "- 若中線多為「分批落袋」，代表偏後段；可考慮提升趨勢訊號權重或降低過熱門檻。",
    "- 若短/中線重疊過多，可考慮讓短線池排除 `midlong_core` 或在推播層做去重。",
]

OPENAI_FALLBACK_MODELS = [
    "gpt-5.1",
    "gpt-5-mini",
    "gpt-4.1-mini",
]

LOCAL_API_KEY_FILE = REPO_ROOT / "local_api_key"
AI_RECO_CACHE_PATH = Path("verification") / "watchlist_daily" / "ai_reco_cache.json"


def load_local_api_key(path: Path = LOCAL_API_KEY_FILE) -> str:
    if not path.exists():
        return ""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if "=" in s:
            s = s.split("=", 1)[1].strip()
        if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
            s = s[1:-1].strip()
        return s
    return ""


def select_forced_recommendations(
    df_rank: pd.DataFrame,
    *,
    watch_type: str,
    top_n: int = 5,
) -> pd.DataFrame:
    if df_rank is None or df_rank.empty:
        return pd.DataFrame()

    watch_type = str(watch_type or "").strip().lower()
    if watch_type == "short":
        pool = rank_short_term_pool(df_rank).copy()
        if pool.empty:
            return pool
        pool["action"] = pool.apply(short_term_action_label, axis=1)
        pool["_ok"] = pool.apply(is_short_term_buyable, axis=1)
    elif watch_type == "midlong":
        pool = rank_midlong_pool(df_rank).copy()
        if pool.empty:
            return pool
        pool["action"] = pool.apply(midlong_action_label, axis=1)
        pool["_ok"] = pool.apply(is_midlong_buyable, axis=1)
    else:
        raise ValueError(f"Unknown watch_type: {watch_type}")

    pool["_rank"] = pd.to_numeric(pool.get("rank"), errors="coerce")
    pool = pool.sort_values(by=["_ok", "_rank"], ascending=[False, True]).copy()
    if int(top_n) > 0:
        pool = pool.head(int(top_n)).copy()
    pool["reco_status"] = pool["_ok"].map(lambda v: "ok" if bool(v) else "below_threshold")
    pool = pool.drop(columns=["_ok", "_rank"], errors="ignore")
    return pool


def append_csv_with_existing_header(path: Path, rows: pd.DataFrame) -> None:
    if rows is None or rows.empty:
        return
    path.parent.mkdir(parents=True, exist_ok=True)

    if not path.exists():
        rows.to_csv(path, index=False, encoding="utf-8")
        return

    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        header = next(reader, [])

    if not header:
        rows.to_csv(path, index=False, encoding="utf-8")
        return

    aligned = rows.copy()
    for col in header:
        if col not in aligned.columns:
            aligned[col] = ""
    aligned = aligned[[c for c in header if c in aligned.columns]].copy()
    with path.open("a", encoding="utf-8", newline="") as f:
        aligned.to_csv(f, index=False, header=False)


def _render_notes_section(title: str, notes: list[str]) -> list[str]:
    lines = [title]
    if not notes:
        return lines + ["- None"]
    return lines + notes


def _load_outcomes_aggregate(outcomes_csv: Path) -> dict:
    if not outcomes_csv.exists():
        return {}
    try:
        df = pd.read_csv(outcomes_csv)
    except Exception:
        return {}
    if df.empty:
        return {}
    if "status" in df.columns:
        df = df[df["status"].astype(str) == "ok"].copy()
    if df.empty:
        return {}
    for col in ["realized_ret_pct", "horizon_days"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "watch_type" in df.columns:
        df["watch_type"] = df["watch_type"].astype(str).str.strip().str.lower()
    df["win"] = pd.to_numeric(df.get("realized_ret_pct"), errors="coerce") > 0
    try:
        overall = (
            df.groupby(["horizon_days", "watch_type"], dropna=False)
            .agg(
                n=("realized_ret_pct", "count"),
                win_rate=("win", "mean"),
                avg_ret=("realized_ret_pct", "mean"),
                med_ret=("realized_ret_pct", "median"),
            )
            .reset_index()
            .sort_values(by=["horizon_days", "watch_type"])
        )
        overall["win_rate"] = (overall["win_rate"] * 100).round(1)
        for c in ["avg_ret", "med_ret"]:
            overall[c] = overall[c].round(2)
    except Exception:
        return {}
    return {"overall_by_signal": overall.to_dict(orient="records")}


def _extract_json_object(text: str) -> dict:
    s = str(text or "").strip()
    if not s:
        return {}
    try:
        return json.loads(s)
    except Exception:
        pass
    start = s.find("{")
    end = s.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(s[start : end + 1])
        except Exception:
            return {}
    return {}

def _read_json_file(path: Path) -> dict:
    try:
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}


def _write_json_file(path: Path, obj: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    except Exception:
        return


def cache_key_for_context(ctx: dict) -> str:
    raw = json.dumps(ctx, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def load_ai_reco_cache(key: str, *, path: Path = AI_RECO_CACHE_PATH) -> dict | None:
    data = _read_json_file(path)
    if not data:
        return None
    if str(data.get("key", "")) != str(key):
        return None
    reco = data.get("reco")
    return reco if isinstance(reco, dict) else None


def save_ai_reco_cache(key: str, reco: dict, *, path: Path = AI_RECO_CACHE_PATH) -> None:
    _write_json_file(
        path,
        {
            "key": key,
            "saved_at": datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S %Z"),
            "reco": reco,
        },
    )


def generate_ai_recommendations(
    context: dict,
    *,
    provider: str,
    model: str,
    base_url: str,
    api_key: str,
    timeout_seconds: int = 25,
    retries: int = 2,
    backoff_seconds: float = 2.0,
) -> dict:
    provider = (provider or "").strip().lower()
    model = (model or "").strip()
    base_url = (base_url or "").strip().rstrip("/")

    prompt = (
        "你是台股 watchlist 的研究助手（不是投資顧問）。請只根據提供的 JSON（daily_rank pool 摘要），"
        "給出短線(short)與中線(midlong)各 5 檔『值得進一步研究/追蹤』的標的。\n"
        "要求：\n"
        "- 以 `close<=price_max` 優先（若不足才用較高價格補齊，並在 reason 說明）。\n"
        "- short 偏向 5D 動能/量價；midlong 偏向 20D 趨勢/可分批。\n"
        "- 請避免選到 signals 完全為 NONE 且 setup_score 低的。\n"
        "- 輸出必須是 JSON，格式：\n"
        "{\n"
        '  "short": [{"ticker": "...", "reason": "..."}],\n'
        '  "midlong": [{"ticker": "...", "reason": "..."}]\n'
        "}\n"
        "不要輸出任何多餘文字。"
    )

    if provider == "ollama":
        if not model:
            raise ValueError("ollama requires --ai-model")
        if not base_url:
            base_url = os.getenv("OLLAMA_HOST", "").strip().rstrip("/") or "http://localhost:11434"
        url = f"{base_url}/api/generate"
        payload = {
            "model": model,
            "prompt": prompt + "\n\nJSON:\n" + json.dumps(context, ensure_ascii=False),
            "stream": False,
            "options": {"temperature": 0.2},
        }
        r = requests.post(url, json=payload, timeout=timeout_seconds)
        r.raise_for_status()
        text = str(r.json().get("response", "")).strip()
        return _extract_json_object(text)

    if provider == "openai":
        if not api_key:
            api_key = load_local_api_key()
        if not api_key:
            raise ValueError("openai requires OPENAI_API_KEY, --ai-api-key, or ./local_api_key")
        if not model:
            raise ValueError("openai requires --ai-model")
        if not base_url:
            base_url = os.getenv("OPENAI_BASE_URL", "").strip().rstrip("/") or "https://api.openai.com/v1"

        url = f"{base_url}/responses"
        headers = {"Authorization": f"Bearer {api_key}"}
        tried: list[str] = []
        last_rate_limit: str | None = None
        for attempt_model in [model, *[m for m in OPENAI_FALLBACK_MODELS if m != model]]:
            tried.append(attempt_model)
            for attempt in range(max(int(retries), 0) + 1):
                payload = {
                    "model": attempt_model,
                    "input": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "input_text", "text": prompt},
                                {"type": "input_text", "text": json.dumps(context, ensure_ascii=False)},
                            ],
                        }
                    ],
                    "temperature": 0.2,
                }
                r = requests.post(url, headers=headers, json=payload, timeout=timeout_seconds)

                if r.status_code == 429:
                    try:
                        err = r.json().get("error", {})
                        msg = str(err.get("message", "")).strip()
                    except Exception:
                        msg = r.text.strip()[:200]
                    last_rate_limit = msg or "rate_limited"
                    retry_after = r.headers.get("retry-after")
                    sleep_s = float(retry_after) if retry_after and str(retry_after).strip().isdigit() else float(backoff_seconds) * (2**attempt)
                    time.sleep(min(sleep_s, 30.0))
                    continue

                if r.status_code >= 400:
                    # If the model name is invalid/unavailable, try fallbacks. Otherwise, raise immediately.
                    try:
                        err = r.json().get("error", {})
                        code = str(err.get("code", "")).lower()
                        msg = str(err.get("message", "")).lower()
                    except Exception:
                        code, msg = "", ""
                    if r.status_code in {400, 404} and ("model" in msg or "model_not_found" in code or "invalid_model" in code):
                        break
                    r.raise_for_status()

                data = r.json()
                text = ""
                for item in data.get("output", []) or []:
                    for c in item.get("content", []) or []:
                        if c.get("type") == "output_text":
                            text += str(c.get("text", ""))
                text = text.strip() or str(data.get("text", "")).strip()
                obj = _extract_json_object(text)
                if obj:
                    obj["_meta"] = {"provider": "openai", "model": attempt_model, "tried": tried}
                return obj

        if last_rate_limit:
            raise RuntimeError(f"Rate limited (429): {last_rate_limit}")

        raise RuntimeError(f"OpenAI request failed for models: {', '.join(tried)}")

    raise ValueError("Unknown provider; use --ai-provider openai|ollama")


def generate_ai_improvement_notes(
    context: dict,
    *,
    provider: str,
    model: str,
    base_url: str,
    api_key: str,
    timeout_seconds: int = 25,
) -> list[str]:
    provider = (provider or "").strip().lower()
    model = (model or "").strip()
    base_url = (base_url or "").strip().rstrip("/")

    prompt = (
        "你是台股短線/中線推薦系統的『校正顧問』。請根據提供的 JSON 摘要，給出 3–6 條具體可執行的建議，"
        "重點是：\n"
        "1) 如果 short/midlong 被 forced 補滿，如何調整門檻讓 ok 佔比提高但不犧牲 5D/20D follow-through。\n"
        "2) 若可追/等拉回的 outcome 表現不佳，建議要改哪個條件（ret5/ret20/量比/risk/setup/signals）。\n"
        "3) 若高股價造成操作難度，建議如何在追蹤池/排序/訊號上偏向低價或可分批標的。\n"
        "輸出請用條列（每條一行，以 '- ' 開頭），不要重述 JSON。"
    )

    if provider == "ollama":
        if not model:
            raise ValueError("ollama requires --ai-model (e.g. qwen2.5:7b-instruct)")
        if not base_url:
            base_url = os.getenv("OLLAMA_HOST", "").strip().rstrip("/") or "http://localhost:11434"
        url = f"{base_url}/api/generate"
        payload = {
            "model": model,
            "prompt": prompt + "\n\nJSON:\n" + json.dumps(context, ensure_ascii=False),
            "stream": False,
            "options": {"temperature": 0.2},
        }
        r = requests.post(url, json=payload, timeout=timeout_seconds)
        r.raise_for_status()
        text = str(r.json().get("response", "")).strip()
    elif provider == "openai":
        if not api_key:
            api_key = load_local_api_key()
        if not api_key:
            raise ValueError("openai requires OPENAI_API_KEY, --ai-api-key, or ./local_api_key")
        if not model:
            raise ValueError("openai requires --ai-model")
        if not base_url:
            base_url = os.getenv("OPENAI_BASE_URL", "").strip().rstrip("/") or "https://api.openai.com/v1"

        # Use the Responses API when available; best-effort only.
        url = f"{base_url}/responses"
        headers = {"Authorization": f"Bearer {api_key}"}
        tried: list[str] = []
        text = ""
        for attempt_model in [model, *[m for m in OPENAI_FALLBACK_MODELS if m != model]]:
            tried.append(attempt_model)
            payload = {
                "model": attempt_model,
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": prompt},
                            {"type": "input_text", "text": json.dumps(context, ensure_ascii=False)},
                        ],
                    }
                ],
                "temperature": 0.2,
            }
            r = requests.post(url, headers=headers, json=payload, timeout=timeout_seconds)
            if r.status_code >= 400:
                try:
                    err = r.json().get("error", {})
                    code = str(err.get("code", "")).lower()
                    msg = str(err.get("message", "")).lower()
                except Exception:
                    code, msg = "", ""
                if r.status_code in {400, 404} and ("model" in msg or "model_not_found" in code or "invalid_model" in code):
                    continue
                r.raise_for_status()

            data = r.json()
            for item in data.get("output", []) or []:
                for c in item.get("content", []) or []:
                    if c.get("type") == "output_text":
                        text += str(c.get("text", ""))
            text = text.strip()
            if not text:
                text = str(data.get("text", "")).strip()
            if text:
                break
        if not text:
            raise RuntimeError(f"OpenAI request produced empty output (models tried: {', '.join(tried)})")
    else:
        raise ValueError("Unknown provider; use --ai-provider openai|ollama")

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    notes = [ln for ln in lines if ln.startswith("- ")]
    if not notes:
        # Best-effort: coerce into bullets
        notes = [f"- {ln.lstrip('-').strip()}" for ln in lines[:6] if ln]
    return notes[:8]


def _format_table(df: pd.DataFrame, cols: list[str]) -> str:
    if df.empty:
        return "_None_\n"
    view = df[cols].copy()
    headers = [str(c) for c in view.columns.tolist()]
    rows: list[list[str]] = []
    for _, r in view.iterrows():
        row: list[str] = []
        for c in headers:
            val = r.get(c)
            if pd.isna(val):
                text = ""
            elif isinstance(val, float):
                text = f"{val:.2f}".rstrip("0").rstrip(".")
            else:
                text = str(val)
            text = text.replace("|", "\\|").replace("\n", " ")
            row.append(text)
        rows.append(row)

    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines) + "\n"


def _action_counts(df: pd.DataFrame, col: str) -> dict[str, int]:
    if df.empty or col not in df.columns:
        return {}
    return df[col].fillna("").astype(str).value_counts().to_dict()


def _maybe_date_from_rank(df_rank: pd.DataFrame) -> str:
    if "date" not in df_rank.columns or df_rank.empty:
        return datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")
    values = df_rank["date"].dropna().astype(str).tolist()
    return values[-1] if values else datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")


def build_verification_report_markdown(
    df_rank: pd.DataFrame,
    *,
    source: str,
    now_local: datetime | None = None,
    heuristics: VerificationHeuristics | None = None,
    top_n_short: int = 5,
    top_n_midlong: int = 5,
    improvement_notes: list[str] | None = None,
    ai_reco: dict | None = None,
) -> str:
    now_local = now_local or datetime.now(LOCAL_TZ)
    heuristics = heuristics or VerificationHeuristics()
    asof_date = _maybe_date_from_rank(df_rank)

    short_pool = rank_short_term_pool(df_rank) if not df_rank.empty else df_rank.head(0).copy()
    midlong_pool = rank_midlong_pool(df_rank) if not df_rank.empty else df_rank.head(0).copy()
    short_forced = select_forced_recommendations(df_rank, watch_type="short", top_n=top_n_short) if not df_rank.empty else df_rank.head(0).copy()
    midlong_forced = select_forced_recommendations(df_rank, watch_type="midlong", top_n=top_n_midlong) if not df_rank.empty else df_rank.head(0).copy()

    short_backups = select_short_term_backup_candidates(
        df_rank,
        exclude_tickers=set(short_forced["ticker"].astype(str)) if not short_forced.empty else None,
    ) if not df_rank.empty else df_rank.head(0).copy()
    midlong_backups = select_midlong_backup_candidates(
        df_rank,
        exclude_tickers=set(midlong_forced["ticker"].astype(str)) if not midlong_forced.empty else None,
    ) if not df_rank.empty else df_rank.head(0).copy()

    if not short_backups.empty:
        short_backups = short_backups.copy()
        short_backups["action"] = short_backups.apply(short_term_action_label, axis=1)
    if not midlong_backups.empty:
        midlong_backups = midlong_backups.copy()
        midlong_backups["action"] = midlong_backups.apply(midlong_action_label, axis=1)

    overlap = sorted(
        set(short_forced.get("ticker", pd.Series(dtype=str)).astype(str))
        & set(midlong_forced.get("ticker", pd.Series(dtype=str)).astype(str))
    )

    short_action_counts = _action_counts(short_forced, "action")
    midlong_action_counts = _action_counts(midlong_forced, "action")

    warnings: list[str] = []
    if short_forced.empty:
        warnings.append("短線推薦為空：可能條件過嚴或資料不足。")
    if midlong_forced.empty:
        warnings.append("中線推薦為空：可能條件過嚴或資料不足。")
    if overlap:
        warnings.append(f"短線/中線推薦重疊 {len(overlap)} 檔：{', '.join(overlap)}")

    def _scan_below_threshold(df: pd.DataFrame, label: str) -> None:
        if df.empty or "reco_status" not in df.columns:
            return
        below = df[df["reco_status"].astype(str) != "ok"].copy()
        if below.empty:
            return
        names = ", ".join(below["ticker"].astype(str).head(5).tolist())
        warnings.append(f"{label} 補滿用（低於原本可買門檻）{len(below)} 檔：{names}")

    def _scan_overheated(df: pd.DataFrame, label: str) -> None:
        if df.empty:
            return
        overheated = df[
            (pd.to_numeric(df.get("ret5_pct"), errors="coerce") >= heuristics.warn_overheated_ret5_pct)
            | (pd.to_numeric(df.get("risk_score"), errors="coerce") >= heuristics.warn_high_risk_score)
        ].copy()
        if not overheated.empty:
            names = ", ".join(overheated["ticker"].astype(str).head(5).tolist())
            warnings.append(f"{label} 含偏過熱/高風險標的（ret5或risk偏高）：{names}")

    def _scan_liquidity(df: pd.DataFrame, label: str) -> None:
        if df.empty:
            return
        low_vol = df[pd.to_numeric(df.get("volume_ratio20"), errors="coerce") < heuristics.warn_low_volume_ratio]
        if not low_vol.empty:
            names = ", ".join(low_vol["ticker"].astype(str).head(5).tolist())
            warnings.append(f"{label} 含量比偏低標的（< {heuristics.warn_low_volume_ratio}）：{names}")

    _scan_below_threshold(short_forced, "短線推薦")
    _scan_below_threshold(midlong_forced, "中線推薦")
    _scan_overheated(short_forced, "短線推薦")
    _scan_overheated(midlong_forced, "中線推薦")
    _scan_liquidity(short_forced, "短線推薦")
    _scan_liquidity(midlong_forced, "中線推薦")

    improvement_notes = improvement_notes if improvement_notes is not None else list(DEFAULT_IMPROVEMENT_NOTES)

    lines: list[str] = [
        "# Recommendation Verification (pre-09:00 best-effort)",
        f"- Generated: {now_local.strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"- As-of market date: {asof_date}",
        f"- Source: {source}",
        f"- Forced top N: short={top_n_short} midlong={top_n_midlong}",
        f"- Notify config: short={CONFIG.notify.top_n_short} midlong={CONFIG.notify.top_n_midlong}",
        "",
        "## Summary",
        f"- Short pool size: {len(short_pool)} | forced: {len(short_forced)} | ok: {int((short_forced.get('reco_status','')=='ok').sum()) if not short_forced.empty else 0} | backups: {len(short_backups)}",
        f"- Midlong pool size: {len(midlong_pool)} | forced: {len(midlong_forced)} | ok: {int((midlong_forced.get('reco_status','')=='ok').sum()) if not midlong_forced.empty else 0} | backups: {len(midlong_backups)}",
        f"- Overlap candidates: {len(overlap)}",
        "",
        "## Warnings",
    ]
    if warnings:
        lines.extend([f"- {w}" for w in warnings])
    else:
        lines.append("- None")

    lines.extend(
        [
            "",
            "## Short-Term Candidates",
            _format_table(
                short_forced,
                [
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
                ],
            ).rstrip(),
            "",
            "## Mid-Long Candidates",
            _format_table(
                midlong_forced,
                [
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
                ],
            ).rstrip(),
            "",
            "## Diagnostics",
            f"- Short action counts: {short_action_counts or '{}'}",
            f"- Midlong action counts: {midlong_action_counts or '{}'}",
            "",
        ]
    )

    if ai_reco:
        meta = {}
        if isinstance(ai_reco, dict) and isinstance(ai_reco.get("_meta"), dict):
            meta = dict(ai_reco.get("_meta"))
        # Don't show meta in the tables.
        if isinstance(ai_reco, dict) and "_meta" in ai_reco:
            ai_reco = {k: v for k, v in ai_reco.items() if k != "_meta"}

        def _coerce_reco_df(items: list[dict]) -> pd.DataFrame:
            if not items:
                return pd.DataFrame()
            df = pd.DataFrame(items)
            if "ticker" in df.columns:
                df["ticker"] = df["ticker"].astype(str)
            return df

        short_ai = _coerce_reco_df(list(ai_reco.get("short") or []))
        midlong_ai = _coerce_reco_df(list(ai_reco.get("midlong") or []))
        lines.extend(
            [
                "## AI Picks (research only)",
                "- 這一段是 AI 根據 daily_rank 摘要給的『追蹤/研究』名單，不是買賣建議。",
                f"- AI provider/model: {meta.get('provider','')} / {meta.get('model','')}".strip(" /"),
                "",
                "### AI Short (5)",
                _format_table(short_ai, ["ticker", "reason"]).rstrip(),
                "",
                "### AI Midlong (5)",
                _format_table(midlong_ai, ["ticker", "reason"]).rstrip(),
                "",
            ]
        )

    lines.extend(_render_notes_section("## Improvement Notes (heuristic)", improvement_notes))
    lines.append("")
    return "\n".join(lines).strip() + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Best-effort verification for daily recommendations.")
    parser.add_argument("--rank-csv", default=str(Path("theme_watchlist_daily") / "daily_rank.csv"))
    out_dir = Path("verification") / "watchlist_daily"
    parser.add_argument("--out", default=str(out_dir / "verification_report.md"))
    parser.add_argument("--snapshot-csv", default=str(out_dir / "reco_snapshots.csv"))
    parser.add_argument("--top-n-short", type=int, default=5, help="Force this many short recommendations into snapshot/report.")
    parser.add_argument("--top-n-midlong", type=int, default=5, help="Force this many midlong recommendations into snapshot/report.")
    parser.add_argument("--ai-advice", action="store_true", help="Add AI-generated improvement notes (best effort).")
    parser.add_argument("--ai-recommend", action="store_true", default=True, help="Add AI-generated short/midlong picks (research only).")
    parser.add_argument("--no-ai-recommend", action="store_true", help="Disable AI picks section.")
    parser.add_argument("--ai-price-max", type=float, default=200.0, help="Preference constraint for AI picks (close<=this).")
    parser.add_argument("--ai-provider", default="openai", help="openai|ollama")
    parser.add_argument("--ai-model", default="gpt-5.4", help="Model name for provider.")
    parser.add_argument("--ai-base-url", default="", help="Override base URL (e.g. https://api.openai.com/v1 or http://localhost:11434).")
    parser.add_argument("--ai-api-key", default="", help="OpenAI API key (defaults to OPENAI_API_KEY env var).")
    parser.add_argument("--ai-refresh", action="store_true", help="Ignore local AI cache and call provider again.")
    parser.add_argument("--ai-retries", type=int, default=2, help="Retries on rate limit (429).")
    parser.add_argument("--ai-backoff-seconds", type=float, default=2.0, help="Base backoff seconds on 429.")
    parser.add_argument("--no-snapshot", action="store_true", help="Do not append recommendation snapshots to CSV.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    rank_csv = Path(args.rank_csv)
    out_path = Path(args.out)
    snapshot_csv = Path(args.snapshot_csv)

    if not rank_csv.exists():
        report = build_verification_report_markdown(
            pd.DataFrame(),
            source=str(rank_csv),
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(report, encoding="utf-8")
        print(report)
        return 0

    df_rank = pd.read_csv(rank_csv)
    now_local = datetime.now(LOCAL_TZ)

    improvement_notes: list[str] | None = None
    ai_reco: dict | None = None
    if args.ai_advice:
        outcomes_csv = (Path("verification") / "watchlist_daily" / "reco_outcomes.csv")
        ctx = {
            "asof_date": _maybe_date_from_rank(df_rank),
            "forced_top_n": {"short": int(args.top_n_short), "midlong": int(args.top_n_midlong)},
            "short_action_counts": _action_counts(select_forced_recommendations(df_rank, watch_type="short", top_n=int(args.top_n_short)), "action"),
            "midlong_action_counts": _action_counts(select_forced_recommendations(df_rank, watch_type="midlong", top_n=int(args.top_n_midlong)), "action"),
            "outcomes": _load_outcomes_aggregate(outcomes_csv),
        }
        try:
            improvement_notes = generate_ai_improvement_notes(
                ctx,
                provider=str(args.ai_provider),
                model=str(args.ai_model),
                base_url=str(args.ai_base_url),
                api_key=str(args.ai_api_key).strip() or os.getenv("OPENAI_API_KEY", "").strip(),
            )
        except Exception as exc:
            improvement_notes = list(DEFAULT_IMPROVEMENT_NOTES)
            improvement_notes.insert(0, f"- （AI 建議暫不可用：{exc}）")

    if bool(args.ai_recommend) and not bool(args.no_ai_recommend):
        short_pool = rank_short_term_pool(df_rank)
        midlong_pool = rank_midlong_pool(df_rank)
        keep_cols = [
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
            "close",
            "layer",
            "group",
        ]
        ctx = {
            "asof_date": _maybe_date_from_rank(df_rank),
            "price_max": float(args.ai_price_max),
            "short_pool_top": short_pool[[c for c in keep_cols if c in short_pool.columns]].head(40).to_dict(orient="records") if not short_pool.empty else [],
            "midlong_pool_top": midlong_pool[[c for c in keep_cols if c in midlong_pool.columns]].head(40).to_dict(orient="records") if not midlong_pool.empty else [],
        }
        try:
            key = cache_key_for_context(ctx)
            if not args.ai_refresh:
                cached = load_ai_reco_cache(key)
                if isinstance(cached, dict) and cached:
                    ai_reco = cached
                else:
                    cached = None
            else:
                cached = None

            # Avoid polluting the report with an "AI unavailable" section when the user hasn't configured a key.
            if str(args.ai_provider).strip().lower() == "openai":
                api_key = (
                    str(args.ai_api_key).strip()
                    or os.getenv("OPENAI_API_KEY", "").strip()
                    or load_local_api_key()
                )
                if not api_key:
                    api_key = ""
            else:
                api_key = str(args.ai_api_key).strip() or os.getenv("OPENAI_API_KEY", "").strip()

            if str(args.ai_provider).strip().lower() == "openai" and not api_key:
                ai_reco = None
            elif ai_reco is None:
                ai_reco = generate_ai_recommendations(
                    ctx,
                    provider=str(args.ai_provider),
                    model=str(args.ai_model),
                    base_url=str(args.ai_base_url),
                    api_key=api_key,
                    retries=int(args.ai_retries),
                    backoff_seconds=float(args.ai_backoff_seconds),
                )
                if isinstance(ai_reco, dict) and ai_reco:
                    save_ai_reco_cache(key, ai_reco)
        except Exception as exc:
            ai_reco = {
                "_meta": {"provider": str(args.ai_provider), "model": str(args.ai_model)},
                "short": [{"ticker": "", "reason": f"AI picks unavailable: {exc}"}],
                "midlong": [],
            }

    report = build_verification_report_markdown(
        df_rank,
        source=str(rank_csv),
        now_local=now_local,
        top_n_short=int(args.top_n_short),
        top_n_midlong=int(args.top_n_midlong),
        improvement_notes=improvement_notes,
        ai_reco=ai_reco,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    print(report)

    if not args.no_snapshot:
        asof_date = _maybe_date_from_rank(df_rank)
        short_forced = select_forced_recommendations(df_rank, watch_type="short", top_n=int(args.top_n_short)).copy()
        midlong_forced = select_forced_recommendations(df_rank, watch_type="midlong", top_n=int(args.top_n_midlong)).copy()
        if not short_forced.empty:
            short_forced["watch_type"] = "short"
        if not midlong_forced.empty:
            midlong_forced["watch_type"] = "midlong"

        combined = pd.concat([short_forced, midlong_forced], ignore_index=True)
        if not combined.empty:
            combined = combined.copy()
            combined["generated_at"] = now_local.strftime("%Y-%m-%d %H:%M:%S %Z")
            combined["signal_date"] = asof_date
            combined["source"] = str(rank_csv)
            combined["source_sha"] = ""
            keep = [
                "generated_at",
                "signal_date",
                "source",
                "source_sha",
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
            combined = combined[[c for c in keep if c in combined.columns]].copy()
            append_csv_with_existing_header(snapshot_csv, combined)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
