"""Comparison benchmarks: a moving-average trend bot and buy-and-hold.

BENCHMARKS ONLY. The trend bot mechanically follows *past* price trends; it is
a point of comparison for your own decisions, not a recommendation, and its
past results do not predict future ones.

Both benchmarks trade through the same ``Account`` as the player, so they pay
the same fees, spread, slippage, FX and dividend tax, and they only ever see
data up to the current day through a ``MarketView``.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .account import Account, Order
from .execution import BUY, SELL, Costs
from .market import MarketData


@dataclass(frozen=True)
class TrendRule:
    short_ma: int = 50
    long_ma: int = 200

    def __str__(self) -> str:
        return f"MA{self.short_ma}/MA{self.long_ma}"


@dataclass
class BenchmarkResult:
    name: str
    start: pd.Timestamp
    end: pd.Timestamp
    starting_cash: float
    final_value: float
    trades: int
    fees: float
    fx: float
    spread_slippage: float
    dividend_tax: float

    @property
    def total_return(self) -> float:
        return self.final_value / self.starting_cash - 1.0


def _result(name: str, acct: Account, start: pd.Timestamp) -> BenchmarkResult:
    return BenchmarkResult(
        name=name, start=start, end=acct.view.date, starting_cash=acct.starting_cash,
        final_value=acct.equity(), trades=acct.order_count, fees=acct.fees_paid, fx=acct.fx_paid,
        spread_slippage=acct.spread_slippage_paid, dividend_tax=acct.dividend_tax_paid,
    )


def trend_signal(view, ticker: str, rule: TrendRule) -> bool:
    """True when the short MA is above the long MA using data up to today only."""
    short = view.moving_average(ticker, rule.short_ma)
    long = view.moving_average(ticker, rule.long_ma)
    return short is not None and long is not None and short > long


def run_trend_bot(market: MarketData, start_idx: int, end_idx: int, rule: TrendRule, costs: Costs,
                  starting_cash: float, tickers: list[str] | None = None) -> BenchmarkResult:
    """Equal slot per ticker: hold it while MA_short > MA_long, otherwise keep that slot in cash.

    Signals are computed at each day's close and executed at the next open,
    exactly like the player's market orders.
    """
    tickers = list(tickers or market.tickers)
    view = market.view(start_idx, end_idx)
    acct = Account(view, costs, starting_cash)
    while view.has_next():
        slot = acct.equity() / len(tickers)
        for t in tickers:
            on = trend_signal(view, t, rule)
            held = acct.shares(t) > 0
            if held and not on:
                acct.submit(Order(t, SELL, "all", view.date))
            elif on and not held:
                acct.submit(Order(t, BUY, slot, view.date))
        acct.step()
    return _result(f"Trend bot {rule}", acct, market.dates[start_idx])


def run_buy_and_hold(market: MarketData, ticker: str, start_idx: int, end_idx: int, costs: Costs,
                     starting_cash: float) -> BenchmarkResult:
    """Put all cash into ``ticker`` at the first possible fill (next open) and hold to ``end_idx``."""
    view = market.view(start_idx, end_idx)
    acct = Account(view, costs, starting_cash)
    if view.has_next():
        acct.submit(Order(ticker, BUY, acct.cash, view.date))
        while view.has_next():
            acct.step()
    return _result(f"Buy & hold {ticker}", acct, market.dates[start_idx])
