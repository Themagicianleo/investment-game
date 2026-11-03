"""Account simulation shared by the human player and the benchmark bots.

Each ``step()`` moves the market view forward one trading day and then, in
this order:

1. applies stock splits that take effect today (shares x ratio, cost / ratio),
2. credits dividends whose ex-date is today to shares held *before* today's
   open (a buy that fills at today's open does not get the dividend),
3. fills orders placed yesterday at today's open, sells first, adjusted by
   half-spread + slippage.

Orders are only filled by a later ``step()``, so every fill uses the open of
the trading day *after* the order was placed, a price that was not visible
when the order was entered.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from . import execution as ex
from .execution import BUY, SELL, Costs
from .market import MarketView


@dataclass
class Position:
    shares: float = 0.0
    avg_cost: float = 0.0   # average fill price per share (after spread/slippage, before flat fee)


@dataclass
class Order:
    ticker: str
    side: str
    amount: float | str            # USD amount, or "all" for sells
    placed_date: pd.Timestamp
    reason: str = ""
    prove_wrong: str = ""
    context: dict = field(default_factory=dict)


@dataclass
class Fill:
    order: Order
    date: pd.Timestamp
    shares: float
    open_price: float
    price: float
    gross: float
    fee: float
    fx: float
    spread_slippage: float
    cash_change: float


@dataclass
class Rejection:
    order: Order
    date: pd.Timestamp
    reason: str


@dataclass
class DividendEvent:
    ticker: str
    date: pd.Timestamp
    per_share: float
    shares: float
    gross: float
    tax: float
    net: float


@dataclass
class SplitEvent:
    ticker: str
    date: pd.Timestamp
    ratio: float
    shares_before: float
    shares_after: float


@dataclass
class StepResult:
    date: pd.Timestamp
    splits: list[SplitEvent] = field(default_factory=list)
    dividends: list[DividendEvent] = field(default_factory=list)
    fills: list[Fill] = field(default_factory=list)
    rejections: list[Rejection] = field(default_factory=list)


class Account:
    def __init__(self, view: MarketView, costs: Costs, starting_cash: float):
        self.view = view
        self.costs = costs
        self.starting_cash = starting_cash
        self.positions: dict[str, Position] = {}
        self.pending: list[Order] = []
        self.fees_paid = 0.0
        self.fx_paid = 0.0
        self.spread_slippage_paid = 0.0
        self.dividend_tax_paid = 0.0
        self.dividends_net = 0.0
        self.order_count = 0
        deposit_fx = ex.deposit_fx_cost(starting_cash, costs)
        self.fx_paid += deposit_fx
        self.cash = starting_cash - deposit_fx

    # --- queries ------------------------------------------------------------
    def shares(self, ticker: str) -> float:
        pos = self.positions.get(ticker)
        return pos.shares if pos else 0.0

    def market_value(self) -> float:
        return sum(p.shares * self.view.close(t) for t, p in self.positions.items() if p.shares > 0)

    def equity(self) -> float:
        return self.cash + self.market_value()

    def total_return(self) -> float:
        return self.equity() / self.starting_cash - 1.0

    def reserved_cash(self) -> float:
        """Cash earmarked by pending buy orders."""
        return sum(o.amount for o in self.pending if o.side == BUY and isinstance(o.amount, (int, float)))

    # --- orders -------------------------------------------------------------
    def submit(self, order: Order) -> None:
        self.pending.append(order)

    def cancel_all(self) -> list[Order]:
        cancelled, self.pending = self.pending, []
        return cancelled

    def step(self) -> StepResult:
        date = self.view.advance()
        result = StepResult(date=date)
        self._apply_splits(result)
        self._credit_dividends(result)
        orders, self.pending = self.pending, []
        for order in sorted(orders, key=lambda o: 0 if o.side == SELL else 1):
            self._fill(order, result)
        return result

    def _apply_splits(self, result: StepResult) -> None:
        for ticker, pos in self.positions.items():
            ratio = self.view.today(ticker)["Stock Splits"]
            if ratio > 0 and ratio != 1.0 and pos.shares > 0:
                before = pos.shares
                pos.shares = pos.shares * ratio
                if not self.costs.fractional:
                    # Brokers pay cash in lieu of fractional shares; ignored here (tiny amounts),
                    # but keep the share count whole.
                    pos.shares = ex.round_shares(pos.shares, False)
                pos.avg_cost = pos.avg_cost / ratio
                result.splits.append(SplitEvent(ticker, result.date, ratio, before, pos.shares))

    def _credit_dividends(self, result: StepResult) -> None:
        for ticker, pos in self.positions.items():
            per_share = self.view.today(ticker)["Dividends"]
            if per_share > 0 and pos.shares > 0:
                gross, tax, net = ex.net_dividend(per_share, pos.shares, self.costs)
                self.cash += net
                self.dividend_tax_paid += tax
                self.dividends_net += net
                result.dividends.append(DividendEvent(ticker, result.date, per_share, pos.shares, gross, tax, net))

    def _fill(self, order: Order, result: StepResult) -> None:
        open_price = self.view.open(order.ticker)
        price = ex.fill_price(open_price, order.side, self.costs)
        pos = self.positions.setdefault(order.ticker, Position())

        if order.side == BUY:
            budget = min(float(order.amount), self.cash)
            shares = ex.buy_quantity(budget, price, self.costs)
            if shares <= 0:
                why = "not enough cash" if self.cash < float(order.amount) else "amount too small for one share after fees"
                result.rejections.append(Rejection(order, result.date, f"{why} at fill price ${price:,.2f}"))
                return
            gross, fee, fx, total = ex.buy_total(shares, price, self.costs)
            pos.avg_cost = (pos.avg_cost * pos.shares + gross) / (pos.shares + shares)
            pos.shares += shares
            self.cash -= total
            cash_change = -total
        else:
            held = pos.shares
            if held <= 0:
                result.rejections.append(Rejection(order, result.date, f"no {order.ticker} shares held"))
                return
            if order.amount == "all":
                shares = held
            else:
                shares = ex.sell_quantity(float(order.amount), price, held, self.costs)
            if shares <= 0:
                result.rejections.append(Rejection(order, result.date, f"amount is less than one share at ${price:,.2f}"))
                return
            gross, fee, net = ex.sell_total(shares, price, self.costs)
            fx = 0.0
            pos.shares = held - shares
            if pos.shares <= 1e-9:
                pos.shares, pos.avg_cost = 0.0, 0.0
            self.cash += net
            cash_change = net

        slip = ex.spread_slippage_cost(shares, open_price, price)
        self.fees_paid += fee
        self.fx_paid += fx
        self.spread_slippage_paid += slip
        self.order_count += 1
        result.fills.append(Fill(order, result.date, shares, open_price, price, gross, fee, fx, slip, cash_change))
