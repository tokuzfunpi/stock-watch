from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class SignalTemplate:
    key: str
    label: str
    required: frozenset[str]
    optional: frozenset[str] = frozenset()
    excluded: frozenset[str] = frozenset()
    summary: str = ""


SIGNAL_TEMPLATES: tuple[SignalTemplate, ...] = (
    SignalTemplate(
        key="momentum_leader",
        label="Momentum Leader",
        required=frozenset({"ACCEL", "TREND"}),
        optional=frozenset({"SURGE"}),
        summary="趨勢延續中，且短期加速度開始放大。",
    ),
    SignalTemplate(
        key="reclaim_breakout",
        label="Reclaim Breakout",
        required=frozenset({"REBREAK"}),
        optional=frozenset({"ACCEL", "TREND"}),
        summary="均線重新站回後，準備銜接下一段攻擊。",
    ),
    SignalTemplate(
        key="theme_heat",
        label="Theme Heat",
        required=frozenset({"SURGE"}),
        optional=frozenset({"ACCEL"}),
        excluded=frozenset({"PULLBACK"}),
        summary="題材資金快速湧入，熱度明顯升溫。",
    ),
    SignalTemplate(
        key="reset_pullback",
        label="Reset Pullback",
        required=frozenset({"PULLBACK"}),
        optional=frozenset({"TREND", "REBREAK"}),
        summary="高檔回檔整理，重點是等結構重新轉強。",
    ),
    SignalTemplate(
        key="early_base",
        label="Early Base",
        required=frozenset({"BASE"}),
        optional=frozenset({"REBREAK"}),
        summary="低檔整理期，偏早期觀察而不是立即追價。",
    ),
)


def parse_signal_tokens(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    tokens: list[str] = []
    seen: set[str] = set()
    for raw in str(value).split(","):
        token = raw.strip().upper()
        if not token or token in seen:
            continue
        seen.add(token)
        tokens.append(token)
    return tuple(tokens)


def match_signal_templates(value: object) -> tuple[SignalTemplate, ...]:
    active = set(parse_signal_tokens(value))
    if not active:
        return ()

    matches: list[SignalTemplate] = []
    for template in SIGNAL_TEMPLATES:
        if not template.required.issubset(active):
            continue
        if template.excluded.intersection(active):
            continue
        matches.append(template)
    return tuple(matches)


def template_labels(value: object) -> str:
    matches = match_signal_templates(value)
    if not matches:
        return "General"
    return " + ".join(template.label for template in matches)


def apply_signal_template_labels(
    df: pd.DataFrame,
    *,
    signal_col: str = "signals",
    output_col: str = "signal_template",
) -> pd.DataFrame:
    if df.empty:
        out = df.copy()
        if output_col not in out.columns:
            out[output_col] = pd.Series(dtype="string")
        return out

    out = df.copy()
    if signal_col not in out.columns:
        out[output_col] = "General"
        return out
    out[output_col] = out[signal_col].map(template_labels)
    return out


def summarize_signal_templates(
    df: pd.DataFrame,
    *,
    signal_col: str = "signals",
) -> dict[str, int]:
    if df.empty or signal_col not in df.columns:
        return {}
    labelled = apply_signal_template_labels(df, signal_col=signal_col)
    counts = labelled["signal_template"].fillna("General").astype(str).value_counts()
    return {str(key): int(value) for key, value in counts.items()}
