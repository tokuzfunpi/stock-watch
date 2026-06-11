"""Market breadth metrics.

The existing market filter is a single binary check on the index (TWII close vs
its MA20). Breadth looks *inside* the market: what fraction of names are above
their own moving average, and how do advancers compare to decliners. This gives
an early read on whether a rally is broad or narrow.

Pure functions operating on already-computed per-ticker rows, so this composes
with the existing scan output without any extra data fetching.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Optional

import pandas as pd


@dataclass(frozen=True)
class BreadthSnapshot:
    universe: int
    pct_above_ma: Optional[float]
    advancers: int
    decliners: int
    advance_decline_ratio: Optional[float]
    label: str

    def as_dict(self) -> dict:
        return {
            "universe": self.universe,
            "pct_above_ma": self.pct_above_ma,
            "advancers": self.advancers,
            "decliners": self.decliners,
            "advance_decline_ratio": self.advance_decline_ratio,
            "breadth_label": self.label,
        }


def _breadth_label(pct_above_ma: Optional[float]) -> str:
    if pct_above_ma is None:
        return "未知"
    if pct_above_ma >= 70:
        return "強勢普漲"
    if pct_above_ma >= 55:
        return "偏多"
    if pct_above_ma >= 45:
        return "分歧"
    if pct_above_ma >= 30:
        return "偏弱"
    return "普遍走弱"


def compute_breadth(
    rows: Iterable[Mapping[str, object]],
    *,
    close_key: str = "close",
    ma_key: str = "ma20",
    ret_key: str = "ret1_pct",
) -> BreadthSnapshot:
    """Compute breadth from per-ticker scan rows.

    - ``pct_above_ma``: share of names whose close is above ``ma_key``.
    - advancers / decliners: by ``ret_key`` (defaults to 1-day return).
    """
    rows = list(rows)
    universe = len(rows)
    if universe == 0:
        return BreadthSnapshot(0, None, 0, 0, None, _breadth_label(None))

    above = 0
    ma_counted = 0
    advancers = 0
    decliners = 0

    for row in rows:
        close = row.get(close_key)
        ma = row.get(ma_key)
        if close is not None and ma is not None and not pd.isna(close) and not pd.isna(ma):
            ma_counted += 1
            if float(close) > float(ma):
                above += 1

        ret = row.get(ret_key)
        if ret is not None and not pd.isna(ret):
            ret_f = float(ret)
            if ret_f > 0:
                advancers += 1
            elif ret_f < 0:
                decliners += 1

    pct_above_ma = round(above / ma_counted * 100, 2) if ma_counted else None
    ad_ratio = round(advancers / decliners, 2) if decliners else (float("inf") if advancers else None)

    return BreadthSnapshot(
        universe=universe,
        pct_above_ma=pct_above_ma,
        advancers=advancers,
        decliners=decliners,
        advance_decline_ratio=ad_ratio,
        label=_breadth_label(pct_above_ma),
    )
