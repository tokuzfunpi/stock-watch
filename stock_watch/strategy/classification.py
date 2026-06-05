"""Single source of truth for steady / attack trade-event classification.

Historically the "steady" and "attack" definitions were hardcoded inside
``stock_watch.backtesting.core.run_backtest_dual`` and also described informally
in the README and ``config.notify``. That meant the rules used to *backtest* a
setup could silently drift away from the rules used *live*, invalidating the
backtest conclusions.

This module centralizes those definitions so both the backtest and any live
consumer share identical thresholds, sourced from ``config.json`` (with defaults
that preserve the previous hardcoded behavior).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class ClassificationThresholds:
    """Thresholds that decide whether a scored row is a steady / attack event.

    Defaults intentionally match the previously hardcoded logic:

    - steady: ``setup_score >= 5 and risk_score <= 4``
    - attack: ``(ret5_pct > 8 and volume_ratio20 > 1.3 and ret20_pct > 0)``
      ``or "ACCEL" in signals``
    """

    steady_min_setup_score: float = 5.0
    steady_max_risk_score: float = 4.0
    attack_min_ret5_pct: float = 8.0
    attack_min_volume_ratio: float = 1.3
    attack_min_ret20_pct: float = 0.0
    attack_breakout_signals: tuple[str, ...] = field(default=("ACCEL",))

    @classmethod
    def from_mapping(
        cls, raw: Mapping[str, Any] | None
    ) -> "ClassificationThresholds":
        raw = raw or {}
        signals_raw = raw.get("attack_breakout_signals")
        if isinstance(signals_raw, str):
            breakout_signals: tuple[str, ...] = tuple(
                part.strip() for part in signals_raw.split(",") if part.strip()
            )
        elif isinstance(signals_raw, (list, tuple)):
            breakout_signals = tuple(str(part).strip() for part in signals_raw if str(part).strip())
        else:
            breakout_signals = cls.attack_breakout_signals

        return cls(
            steady_min_setup_score=_as_float(raw.get("steady_min_setup_score"), cls.steady_min_setup_score),
            steady_max_risk_score=_as_float(raw.get("steady_max_risk_score"), cls.steady_max_risk_score),
            attack_min_ret5_pct=_as_float(raw.get("attack_min_ret5_pct"), cls.attack_min_ret5_pct),
            attack_min_volume_ratio=_as_float(raw.get("attack_min_volume_ratio"), cls.attack_min_volume_ratio),
            attack_min_ret20_pct=_as_float(raw.get("attack_min_ret20_pct"), cls.attack_min_ret20_pct),
            attack_breakout_signals=breakout_signals,
        )


DEFAULT_THRESHOLDS = ClassificationThresholds()


def is_steady_event(
    row: Mapping[str, Any],
    thresholds: ClassificationThresholds = DEFAULT_THRESHOLDS,
) -> bool:
    """A lower-risk, well-formed setup worth tracking as a "steady" event."""
    setup_score = _as_float(row.get("setup_score"))
    risk_score = _as_float(row.get("risk_score"))
    return (
        setup_score >= thresholds.steady_min_setup_score
        and risk_score <= thresholds.steady_max_risk_score
    )


def is_attack_event(
    row: Mapping[str, Any],
    thresholds: ClassificationThresholds = DEFAULT_THRESHOLDS,
) -> bool:
    """A momentum / breakout setup worth tracking as an "attack" event."""
    ret5 = _as_float(row.get("ret5_pct"))
    vol_ratio20 = _as_float(row.get("volume_ratio20"))
    ret20 = _as_float(row.get("ret20_pct"))
    signals = str(row.get("signals", "") or "")

    momentum = (
        ret5 > thresholds.attack_min_ret5_pct
        and vol_ratio20 > thresholds.attack_min_volume_ratio
        and ret20 > thresholds.attack_min_ret20_pct
    )
    breakout = any(sig and sig in signals for sig in thresholds.attack_breakout_signals)
    return bool(momentum or breakout)
