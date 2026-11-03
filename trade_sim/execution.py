"""Pure cost and fill calculations (no state, easy to test)."""

from __future__ import annotations

import math
from dataclasses import dataclass

BUY = "buy"
SELL = "sell"
_EPS = 1e-9


@dataclass(frozen=True)
class Costs:
    half_spread: float = 0.0005      # fractions, not percent
    slippage: float = 0.0005
    fee_per_order: float = 0.99
    fx_spread: float = 0.0015
    fx_mode: str = "deposit"         # deposit | per_buy | none
    withholding_tax: float = 0.30
    fractional: bool = False

    @classmethod
    def from_config(cls, cfg: dict) -> "Costs":
        c = cfg["costs"]
        return cls(
            half_spread=c["half_spread_pct"] / 100.0,
            slippage=c["slippage_pct"] / 100.0,
            fee_per_order=c["fee_per_order_usd"],
            fx_spread=c["fx_spread_pct"] / 100.0,
            fx_mode=c["fx_mode"],
            withholding_tax=cfg["dividends"]["withholding_tax_pct"] / 100.0,
            fractional=cfg["fractional_shares"],
        )

    @property
    def per_buy_fx(self) -> float:
        return self.fx_spread if self.fx_mode == "per_buy" else 0.0


def fill_price(open_price: float, side: str, costs: Costs) -> float:
    """Open price moved against the trader by half the spread plus slippage."""
    adverse = costs.half_spread + costs.slippage
    if side == BUY:
        return open_price * (1.0 + adverse)
    if side == SELL:
        return open_price * (1.0 - adverse)
    raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")


def round_shares(quantity: float, fractional: bool) -> float:
    """Round *down*: whole shares by default, 6 decimal places in fractional mode."""
    if quantity <= 0:
        return 0.0
    if fractional:
        return math.floor(quantity * 1e6 + _EPS) / 1e6
    return float(math.floor(quantity + _EPS))


def fx_cost(gross_usd: float, costs: Costs) -> float:
    return gross_usd * costs.per_buy_fx


def deposit_fx_cost(amount_usd: float, costs: Costs) -> float:
    return amount_usd * costs.fx_spread if costs.fx_mode == "deposit" else 0.0


def buy_quantity(budget_usd: float, price: float, costs: Costs) -> float:
    """Largest share count whose total cost (shares, FX, flat fee) fits in ``budget_usd``."""
    available = budget_usd - costs.fee_per_order
    if available <= 0 or price <= 0:
        return 0.0
    return round_shares(available / (price * (1.0 + costs.per_buy_fx)), costs.fractional)


def sell_quantity(amount_usd: float, price: float, held: float, costs: Costs) -> float:
    """Shares to sell to raise roughly ``amount_usd`` gross, capped at the holding."""
    if price <= 0 or amount_usd <= 0:
        return 0.0
    wanted = round_shares(amount_usd / price, costs.fractional)
    return min(wanted, held)


def spread_slippage_cost(shares: float, open_price: float, fill: float) -> float:
    """How much worse than the open the fill was, in USD (always >= 0)."""
    return abs(fill - open_price) * shares


def buy_total(shares: float, price: float, costs: Costs) -> tuple[float, float, float, float]:
    """(gross, fee, fx, total cash out) for a buy."""
    gross = shares * price
    fee = costs.fee_per_order
    fx = fx_cost(gross, costs)
    return gross, fee, fx, gross + fee + fx


def sell_total(shares: float, price: float, costs: Costs) -> tuple[float, float, float]:
    """(gross, fee, net cash in) for a sell."""
    gross = shares * price
    fee = costs.fee_per_order
    return gross, fee, gross - fee


def net_dividend(per_share: float, shares: float, costs: Costs) -> tuple[float, float, float]:
    """(gross, withholding tax, net) for a cash dividend."""
    gross = per_share * shares
    tax = gross * costs.withholding_tax
    return gross, tax, gross - tax


def loss_from_cost(price: float, avg_cost: float) -> float:
    """Fractional loss vs average cost (0.15 means 15% below cost; negative = a gain)."""
    if avg_cost <= 0:
        return 0.0
    return 1.0 - price / avg_cost


def loss_limit_breached(price: float, avg_cost: float, limit: float) -> bool:
    return loss_from_cost(price, avg_cost) >= limit - _EPS
