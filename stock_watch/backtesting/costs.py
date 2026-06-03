"""Execution-cost model for Taiwan equities.

Forward returns elsewhere in the system are close-to-close and therefore
ignore real trading frictions. For any figure that is meant to resemble a
realized P&L, costs must be subtracted. This module centralizes the Taiwan
cost assumptions so they are explicit, configurable, and testable.

Default Taiwan assumptions (round trip = buy + sell):
  - Brokerage fee: 0.1425% per side (often discounted by brokers; configurable)
  - Securities transaction tax: 0.3% on the SELL side only (0.15% for day-trade
    of listed shares, but we model the standard swing case)
  - Slippage: a configurable per-side allowance for spread/impact

All rates are expressed as decimals (0.001425 == 0.1425%).
"""

from __future__ import annotations

from dataclasses import dataclass

# Taiwan standard rates.
DEFAULT_FEE_RATE = 0.001425  # brokerage, per side
DEFAULT_SELL_TAX_RATE = 0.003  # transaction tax, sell side only
DEFAULT_SLIPPAGE_RATE = 0.0  # per side; opt-in


@dataclass(frozen=True)
class CostModel:
    fee_rate: float = DEFAULT_FEE_RATE
    sell_tax_rate: float = DEFAULT_SELL_TAX_RATE
    slippage_rate: float = DEFAULT_SLIPPAGE_RATE

    def round_trip_cost_pct(self) -> float:
        """Total round-trip cost as a percentage of notional.

        buy side : fee + slippage
        sell side: fee + tax + slippage
        """
        buy = self.fee_rate + self.slippage_rate
        sell = self.fee_rate + self.sell_tax_rate + self.slippage_rate
        return round((buy + sell) * 100, 4)

    def net_return_pct(self, gross_return_pct: float) -> float:
        """Convert a gross close-to-close percentage return into a net return
        after a single round-trip's costs.

        A multiplicative model is used so costs scale with the traded notional:
            net = (1 + gross) * (1 - buy_cost) * (1 - sell_cost) - 1
        """
        gross = gross_return_pct / 100.0
        buy_cost = self.fee_rate + self.slippage_rate
        sell_cost = self.fee_rate + self.sell_tax_rate + self.slippage_rate
        net = (1.0 + gross) * (1.0 - buy_cost) * (1.0 - sell_cost) - 1.0
        return round(net * 100, 2)


DEFAULT_COST_MODEL = CostModel()


def apply_costs_to_returns(returns, model: CostModel = DEFAULT_COST_MODEL):
    """Apply round-trip costs to an iterable / pandas Series of percentage
    returns, returning the same container type where practical."""
    try:
        import pandas as pd

        if isinstance(returns, pd.Series):
            return returns.map(model.net_return_pct)
    except ImportError:  # pragma: no cover - pandas always present in this repo
        pass
    return [model.net_return_pct(r) for r in returns]
