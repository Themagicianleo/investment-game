"""One play-through: the player's account, orders, loss-limit alerts and journal."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import pandas as pd

from . import execution as ex
from . import journal
from .account import Account, Fill, Order, Rejection, StepResult
from .execution import BUY, SELL, Costs
from .market import MarketData


class SessionError(Exception):
    """A command that cannot be carried out (bad ticker, not enough cash...)."""


@dataclass
class AlertEpisode:
    """A stretch of time during which a position was at or past the loss limit."""
    ticker: str
    start_idx: int
    start_date: pd.Timestamp
    loss: float
    sold_idx: int | None = None        # first sell of this ticker after the alert fired
    recovered_idx: int | None = None   # first day back above the limit
    reviewed_idx: int | None = None    # first written position review after the alert fired
    end_idx: int | None = None


@dataclass
class DayReport:
    step: StepResult
    new_alerts: list[AlertEpisode] = field(default_factory=list)
    ongoing_alerts: list[str] = field(default_factory=list)
    journal_error: str | None = None


def fmt_date(d: pd.Timestamp) -> str:
    return d.strftime("%Y-%m-%d")


class Session:
    def __init__(self, market: MarketData, cfg: dict, start_idx: int, journal_path: str | None = None,
                 session_id: str | None = None, end_idx: int | None = None):
        self._market = market  # only used by the end report, bounded to today's index
        self.cfg = cfg
        self.costs = Costs.from_config(cfg)
        self.tickers = [t for t in cfg["tickers"] if t in market.tickers]
        self.view = market.view(start_idx, end_idx)
        self.start_idx = start_idx
        self.account = Account(self.view, self.costs, cfg["starting_cash_usd"])
        self.journal_path = journal_path if journal_path is not None else cfg["data"]["journal_path"]
        self.session_id = session_id or uuid.uuid4().hex[:8]
        self.loss_limit = cfg["alerts"]["loss_limit_pct"] / 100.0
        self.entries: list[dict] = []
        self.episodes: list[AlertEpisode] = []
        self._active: dict[str, AlertEpisode] = {}
        self.equity_curve: list[tuple[pd.Timestamp, float]] = [(self.date, self.account.equity())]
        self.ended = False
        # process tracking used by the coach, missions and the process score
        self.max_weights: list[float] = []          # largest single-position weight at each close
        self.peak_equity = self.account.equity()
        self.max_drawdown = 0.0                     # worst peak-to-trough fall of total value (fraction)
        self.open_since: dict[str, int] = {}        # ticker -> index of the day the position was opened
        self.longest_hold_days = 0
        self.dividends_received = 0
        self.splits_seen = 0
        # broad index funds (the benchmark, e.g. VT/SPY) are already diversified, so they are
        # left out of the single-position concentration measure
        self.broad_funds = {cfg.get("benchmark_ticker", "")}

    # --- helpers ----------------------------------------------------------
    @property
    def date(self) -> pd.Timestamp:
        return self.view.date

    def _check_ticker(self, ticker: str) -> str:
        t = ticker.upper()
        if t not in self.tickers:
            raise SessionError(f"Unknown ticker '{ticker}'. Tradable tickers: {', '.join(self.tickers)}")
        return t

    def available_cash(self) -> float:
        return self.account.cash - self.account.reserved_cash()

    def _context(self, ticker: str) -> dict:
        lookback = self.cfg["review"]["large_move_lookback_days"]
        move = self.view.trailing_return(ticker, lookback)
        return {"prior_move": move, "loss_alert_active": ticker in self._active}

    # --- orders -------------------------------------------------------------
    def validate_buy(self, ticker: str, amount: float) -> str:
        t = self._check_ticker(ticker)
        if amount <= 0:
            raise SessionError("Amount must be a positive number of US dollars, e.g. buy AAPL 1000")
        avail = self.available_cash()
        if amount > avail + 1e-9:
            raise SessionError(f"Not enough cash: ${avail:,.2f} available after pending orders.")
        if amount <= self.costs.fee_per_order:
            raise SessionError(f"Amount must be more than the ${self.costs.fee_per_order:.2f} order fee.")
        if not self.view.has_next():
            raise SessionError("This is the last day of available data; no further fills are possible.")
        return t

    def validate_sell(self, ticker: str, amount: float | str) -> str:
        t = self._check_ticker(ticker)
        if self.account.shares(t) <= 0:
            raise SessionError(f"You hold no {t} shares.")
        if amount != "all" and float(amount) <= 0:
            raise SessionError('Amount must be a positive US dollar amount or "all", e.g. sell AAPL all')
        if not self.view.has_next():
            raise SessionError("This is the last day of available data; no further fills are possible.")
        return t

    def place(self, side: str, ticker: str, amount: float | str, reason: str, prove_wrong: str) -> Order:
        t = self.validate_buy(ticker, float(amount)) if side == BUY else self.validate_sell(ticker, amount)
        if not reason.strip():
            raise SessionError("A reason is required for every trade.")
        order = Order(t, side, amount if amount == "all" else float(amount), self.date,
                      reason.strip(), prove_wrong.strip(), self._context(t))
        self.account.submit(order)
        return order

    def cancel_all(self) -> list[Order]:
        return self.account.cancel_all()

    def weights(self) -> dict[str, float]:
        """Each holding as a fraction of total value at today's close."""
        eq = self.account.equity()
        if eq <= 0:
            return {}
        return {t: p.shares * self.view.close(t) / eq for t, p in self.account.positions.items() if p.shares > 0}

    def drawdown(self) -> float:
        """Current fall of total value from its highest close this session (fraction, >= 0)."""
        return max(0.0, 1.0 - self.account.equity() / self.peak_equity) if self.peak_equity > 0 else 0.0

    def review_position(self, ticker: str, note: str) -> dict:
        """Write a 'position review' journal note without trading (e.g. after a loss-limit alert)."""
        t = self._check_ticker(ticker)
        pos = self.account.positions.get(t)
        if pos is None or pos.shares <= 0:
            raise SessionError(f"You hold no {t} shares to review.")
        if not note.strip():
            raise SessionError("Write a short note: does your original reason still hold? Has your 'prove me wrong' condition happened?")
        ep = self._active.get(t)
        if ep is not None and ep.reviewed_idx is None:
            ep.reviewed_idx = self.view.idx
        row = {
            "session_id": self.session_id, "status": "review", "placed_date": fmt_date(self.date),
            "fill_date": fmt_date(self.date), "action": "review", "ticker": t, "shares": round(pos.shares, 6),
            "loss_alert_active": ep is not None, "reason": note.strip(), "prove_wrong": "",
            "note": f"close ${self.view.close(t):,.2f}, avg cost ${pos.avg_cost:,.2f}",
        }
        self.entries.append(row)
        try:
            journal.append(self.journal_path, row)
        except journal.JournalError as exc:
            row["note"] += f" (not saved: {exc})"
        return row

    # --- time ---------------------------------------------------------------
    def advance(self) -> DayReport:
        step = self.account.step()
        report = DayReport(step=step)
        for fill in step.fills:
            report.journal_error = self._record(fill) or report.journal_error
            if fill.order.side == SELL and fill.order.ticker in self._active:
                ep = self._active[fill.order.ticker]
                if ep.sold_idx is None:
                    ep.sold_idx = self.view.idx
        for rej in step.rejections:
            report.journal_error = self._record(rej) or report.journal_error
        self._update_alerts(report)
        self._track(report)
        return report

    def _track(self, report: DayReport) -> None:
        idx = self.view.idx
        eq = self.account.equity()
        self.equity_curve.append((self.date, eq))
        self.peak_equity = max(self.peak_equity, eq)
        self.max_drawdown = max(self.max_drawdown, self.drawdown())
        w = self.weights()
        single = [v for t, v in w.items() if t not in self.broad_funds]
        self.max_weights.append(max(single) if single else (0.0 if not w else 1e-9))
        for t in self.tickers:
            held = self.account.shares(t) > 0
            if held and t not in self.open_since:
                self.open_since[t] = idx
            elif not held and t in self.open_since:
                self.longest_hold_days = max(self.longest_hold_days, idx - self.open_since.pop(t))
        for t, start in self.open_since.items():
            self.longest_hold_days = max(self.longest_hold_days, idx - start)
        self.dividends_received += len(report.step.dividends)
        self.splits_seen += len(report.step.splits)

    def hold(self, days: int = 1) -> tuple[list[DayReport], str | None]:
        """Advance up to ``days`` trading days. Stops early on a new loss-limit alert or at the end of data.

        Returns the day reports and, if it stopped early, the reason.
        """
        if days < 1:
            raise SessionError("days must be at least 1")
        reports: list[DayReport] = []
        for i in range(days):
            if not self.view.has_next():
                return reports, "No more data: this is the last available trading day."
            rep = self.advance()
            reports.append(rep)
            if rep.new_alerts and i < days - 1:
                return reports, f"Stopped after {i + 1} day(s) because of a loss-limit alert."
        return reports, None

    def _update_alerts(self, report: DayReport) -> None:
        idx = self.view.idx
        for t in self.tickers:
            pos = self.account.positions.get(t)
            held = pos is not None and pos.shares > 0
            breached = held and ex.loss_limit_breached(self.view.close(t), pos.avg_cost, self.loss_limit)
            ep = self._active.get(t)
            if breached and ep is None:
                ep = AlertEpisode(t, idx, self.date, ex.loss_from_cost(self.view.close(t), pos.avg_cost))
                self._active[t] = ep
                self.episodes.append(ep)
                report.new_alerts.append(ep)
            elif breached:
                report.ongoing_alerts.append(t)
            elif ep is not None:
                if held:
                    ep.recovered_idx = idx
                ep.end_idx = idx
                del self._active[t]

    def alert_loss(self, ticker: str) -> float | None:
        pos = self.account.positions.get(ticker)
        if ticker not in self._active or pos is None or pos.shares <= 0:
            return None
        return ex.loss_from_cost(self.view.close(ticker), pos.avg_cost)

    # --- journal ------------------------------------------------------------
    def _record(self, item: Fill | Rejection) -> str | None:
        o = item.order
        move = o.context.get("prior_move")
        row = {
            "session_id": self.session_id,
            "placed_date": fmt_date(o.placed_date),
            "fill_date": fmt_date(item.date),
            "action": o.side,
            "ticker": o.ticker,
            "prior_move_pct": "" if move is None else round(move * 100, 2),
            "loss_alert_active": o.context.get("loss_alert_active", False),
            "reason": o.reason,
            "prove_wrong": o.prove_wrong,
        }
        if isinstance(item, Fill):
            row.update(
                status="filled", shares=round(item.shares, 6), open_price=round(item.open_price, 4),
                fill_price=round(item.price, 4), fee_usd=round(item.fee, 2), fx_cost_usd=round(item.fx, 2),
                spread_slippage_usd=round(item.spread_slippage, 2), cash_change_usd=round(item.cash_change, 2),
            )
        else:
            row.update(status="rejected", note=item.reason)
        self.entries.append(row)
        try:
            journal.append(self.journal_path, row)
        except journal.JournalError as exc:
            return str(exc)
        return None

    # --- end ----------------------------------------------------------------
    def bounded_market(self) -> tuple[MarketData, int, int]:
        """Market plus the session's [start, today] index range, for end-of-session benchmarks."""
        return self._market, self.start_idx, self.view.idx
