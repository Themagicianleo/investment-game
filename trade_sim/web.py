"""Local web app: a JSON API over ``Session`` plus a single-page front end.

Run with ``trade-sim web`` and open http://127.0.0.1:8000. It binds to
localhost by default and keeps sessions in memory (one process, one user).

Every endpoint reads market data through the session's ``MarketView``, so the
browser never receives prices or events after the session's current day.
"""

from __future__ import annotations

import copy
import threading
from pathlib import Path
from typing import Callable

import numpy as np
from flask import Flask, jsonify, request, send_from_directory

from . import DISCLAIMER, __version__
from .data import DataError, NetworkError
from .execution import BUY, SELL
from .market import MarketData
from .report import compute_report
from .session import DayReport, Session, SessionError, fmt_date
from .walkforward import required_warmup

STATIC_DIR = Path(__file__).parent / "web_static"


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _num(x):
    if x is None:
        return None
    x = float(x)
    return x if np.isfinite(x) else None


# ----------------------------------------------------------------------------
# serialisation
# ----------------------------------------------------------------------------

def day_events(s: Session, rep: DayReport) -> list[dict]:
    st = rep.step
    d = fmt_date(st.date)
    ev = []
    for sp in st.splits:
        ev.append({"date": d, "type": "split",
                   "text": f"{sp.ticker} {sp.ratio:g}-for-1 split: {sp.shares_before:g} → {sp.shares_after:g} shares"})
    for dv in st.dividends:
        ev.append({"date": d, "type": "dividend",
                   "text": f"{dv.ticker} dividend ${dv.per_share:.4f}/sh × {dv.shares:g}: gross ${dv.gross:,.2f}, "
                           f"withholding tax −${dv.tax:,.2f}, credited ${dv.net:,.2f}"})
    for f in st.fills:
        fx = f", FX ${f.fx:,.2f}" if f.fx else ""
        ev.append({"date": d, "type": "fill",
                   "text": f"{f.order.side.upper()} {f.shares:g} {f.order.ticker} @ ${f.price:,.4f} "
                           f"(open ${f.open_price:,.4f}), fee ${f.fee:.2f}{fx}, cash {f.cash_change:+,.2f}"})
    for r in st.rejections:
        ev.append({"date": d, "type": "rejected", "text": f"{r.order.side} {r.order.ticker} not filled: {r.reason}"})
    for ep in rep.new_alerts:
        ev.append({"date": d, "type": "alert",
                   "text": f"Loss-limit alert: {ep.ticker} is {ep.loss:.1%} below your average cost "
                           f"(limit {s.loss_limit:.0%}). Nothing is sold automatically. Check your "
                           "'what would prove me wrong' note."})
    if rep.journal_error:
        ev.append({"date": d, "type": "warning", "text": rep.journal_error})
    return ev


def session_state(s: Session, log: list[dict]) -> dict:
    v, a = s.view, s.account
    positions = []
    for t, p in a.positions.items():
        if p.shares > 0:
            close = v.close(t)
            positions.append({
                "ticker": t, "shares": p.shares, "avg_cost": p.avg_cost, "close": close,
                "value": p.shares * close, "pl": close / p.avg_cost - 1 if p.avg_cost else 0.0,
                "alert": s.alert_loss(t) is not None,
            })
    market = []
    for t in s.tickers:
        today = v.today(t)
        market.append({
            "ticker": t, "open": today["Open"], "high": today["High"], "low": today["Low"],
            "close": today["Close"], "change_1d": _num(v.trailing_return(t, 1)),
            "change_20d": _num(v.trailing_return(t, 20)),
        })
    c = s.costs
    return {
        "id": s.session_id,
        "date": fmt_date(s.date),
        "start_date": fmt_date(s.equity_curve[0][0]),
        "day": v.days_elapsed(),
        "has_next": v.has_next(),
        "ended": s.ended,
        "starting_cash": a.starting_cash,
        "cash": a.cash,
        "available_cash": s.available_cash(),
        "holdings": a.market_value(),
        "equity": a.equity(),
        "return": a.total_return(),
        "positions": positions,
        "pending": [{"side": o.side, "ticker": o.ticker, "amount": o.amount, "reason": o.reason,
                     "placed": fmt_date(o.placed_date)} for o in a.pending],
        "market": market,
        "costs": {"fees": a.fees_paid, "fx": a.fx_paid, "spread_slippage": a.spread_slippage_paid,
                  "dividend_tax": a.dividend_tax_paid, "dividends_net": a.dividends_net},
        "equity_curve": [[fmt_date(d), e] for d, e in s.equity_curve],
        "journal": s.entries,
        "log": log[-200:],
        "rules": {
            "tickers": s.tickers, "fee": c.fee_per_order, "half_spread": c.half_spread,
            "slippage": c.slippage, "fx_mode": c.fx_mode, "fx_spread": c.fx_spread,
            "withholding": c.withholding_tax, "loss_limit": s.loss_limit, "fractional": c.fractional,
        },
    }


def history_payload(s: Session, ticker: str, days: int | None) -> dict:
    t = s._check_ticker(ticker)
    h = s.view.history(t, days)
    adj = s.view.adjusted_closes(t, len(h))
    return {
        "ticker": t,
        "as_of": fmt_date(s.date),
        "dates": [fmt_date(d) for d in h.index],
        "open": h["Open"].round(4).tolist(),
        "high": h["High"].round(4).tolist(),
        "low": h["Low"].round(4).tolist(),
        "close": h["Close"].round(4).tolist(),
        "adj_close": np.round(adj, 4).tolist(),
        "volume": h["Volume"].round(0).tolist(),
        "dividends": h["Dividends"].round(6).tolist(),
        "splits": h["Stock Splits"].tolist(),
    }


# ----------------------------------------------------------------------------
# app
# ----------------------------------------------------------------------------

class _Game:
    def __init__(self, session: Session):
        self.session = session
        self.log: list[dict] = []
        self.report: dict | None = None
        self.lock = threading.Lock()


def create_app(cfg: dict, market: MarketData | None = None,
               market_loader: Callable[[dict], MarketData] | None = None, include_bot: bool = True) -> Flask:
    app = Flask(__name__, static_folder=None)
    games: dict[str, _Game] = {}
    state = {"market": market}
    market_lock = threading.Lock()

    def get_market() -> MarketData:
        with market_lock:
            if state["market"] is None:
                if market_loader is None:
                    raise ApiError("No market data loader configured", 500)
                state["market"] = market_loader(cfg)
            return state["market"]

    def get_game(sid: str) -> _Game:
        game = games.get(sid)
        if game is None:
            raise ApiError("Session not found. It may have been lost when the server restarted; start a new one.", 404)
        return game

    def body() -> dict:
        data = request.get_json(silent=True)
        return data if isinstance(data, dict) else {}

    def active(game: _Game) -> Session:
        if game.session.ended:
            raise ApiError("This session has ended. Start a new session to keep practising.", 409)
        return game.session

    @app.errorhandler(ApiError)
    def _api_error(exc: ApiError):
        return jsonify({"error": str(exc)}), exc.status

    @app.errorhandler(SessionError)
    def _session_error(exc: SessionError):
        return jsonify({"error": str(exc)}), 400

    @app.errorhandler(NetworkError)
    def _network_error(exc: NetworkError):
        return jsonify({"error": f"Network error: {exc}"}), 503

    @app.errorhandler(DataError)
    def _data_error(exc: DataError):
        return jsonify({"error": f"Data error: {exc}"}), 503

    # --- static -----------------------------------------------------------
    @app.get("/")
    def index():
        return send_from_directory(STATIC_DIR, "index.html")

    @app.get("/static/<path:name>")
    def static_file(name: str):
        return send_from_directory(STATIC_DIR, name)

    # --- API ----------------------------------------------------------------
    @app.get("/api/info")
    def info():
        return jsonify({"version": __version__, "disclaimer": DISCLAIMER, "tickers": cfg["tickers"],
                        "starting_cash": cfg["starting_cash_usd"]})

    @app.post("/api/sessions")
    def new_session():
        data = body()
        seed = data.get("seed")
        if seed in ("", None):
            seed = cfg["session"]["seed"]
        else:
            try:
                seed = int(seed)
            except (TypeError, ValueError):
                raise ApiError("Seed must be a whole number (or leave it blank for a random start).")
        m = get_market()
        rng = np.random.default_rng(seed)
        start = m.random_start_index(rng, required_warmup(cfg), cfg["session"]["min_years_after_start"])
        session = Session(m, copy.deepcopy(cfg), start)
        game = _Game(session)
        games[session.session_id] = game
        if session.account.fx_paid:
            game.log.append({"date": fmt_date(session.date), "type": "info",
                             "text": f"SGD→USD conversion of your deposit cost ${session.account.fx_paid:,.2f} (FX spread)."})
        game.log.append({"date": fmt_date(session.date), "type": "info",
                         "text": f"Session started on {fmt_date(session.date)} with ${session.account.starting_cash:,.2f}."})
        return jsonify(session_state(session, game.log)), 201

    @app.get("/api/sessions/<sid>")
    def get_state(sid: str):
        game = get_game(sid)
        return jsonify(session_state(game.session, game.log))

    @app.get("/api/sessions/<sid>/history/<ticker>")
    def history(sid: str, ticker: str):
        game = get_game(sid)
        days = request.args.get("days", type=int)
        if days is not None and days < 1:
            raise ApiError("days must be at least 1")
        return jsonify(history_payload(game.session, ticker, days))

    @app.post("/api/sessions/<sid>/orders")
    def place_order(sid: str):
        game = get_game(sid)
        data = body()
        with game.lock:
            s = active(game)
            side = str(data.get("side", "")).lower()
            if side not in (BUY, SELL):
                raise ApiError("side must be 'buy' or 'sell'")
            ticker = str(data.get("ticker", ""))
            raw = data.get("amount")
            if side == SELL and str(raw).lower() == "all":
                amount = "all"
            else:
                try:
                    amount = float(str(raw).replace(",", "").lstrip("$"))
                except (TypeError, ValueError):
                    raise ApiError("Amount must be a US dollar amount" + (' or "all"' if side == SELL else ""))
                if not np.isfinite(amount) or amount <= 0:
                    raise ApiError("Amount must be a positive number of US dollars")
            reason = str(data.get("reason", "")).strip()
            prove_wrong = str(data.get("prove_wrong", "")).strip()
            if not reason:
                raise ApiError("Please write a reason for this trade.")
            if not prove_wrong:
                raise ApiError("Please write what would prove you wrong.")
            order = s.place(side, ticker, amount, reason, prove_wrong)
            amt = "all shares" if order.amount == "all" else f"${order.amount:,.2f}"
            game.log.append({"date": fmt_date(s.date), "type": "order",
                             "text": f"Order queued: {side} {order.ticker} {amt}. Fills at the next trading day's open."})
            return jsonify(session_state(s, game.log)), 201

    @app.post("/api/sessions/<sid>/cancel")
    def cancel(sid: str):
        game = get_game(sid)
        with game.lock:
            s = active(game)
            n = len(s.cancel_all())
            game.log.append({"date": fmt_date(s.date), "type": "info", "text": f"Cancelled {n} pending order(s)."})
            return jsonify(session_state(s, game.log))

    @app.post("/api/sessions/<sid>/hold")
    def hold(sid: str):
        game = get_game(sid)
        data = body()
        try:
            days = int(data.get("days", 1))
        except (TypeError, ValueError):
            raise ApiError("days must be a whole number")
        if not 1 <= days <= 1000:
            raise ApiError("days must be between 1 and 1000")
        with game.lock:
            s = active(game)
            reports, stopped = s.hold(days)
            events = [e for rep in reports for e in day_events(s, rep)]
            game.log.extend(events)
            if stopped:
                game.log.append({"date": fmt_date(s.date), "type": "info", "text": stopped})
            payload = session_state(s, game.log)
            payload["advanced"] = len(reports)
            payload["stopped"] = stopped
            payload["new_events"] = events
            return jsonify(payload)

    @app.post("/api/sessions/<sid>/end")
    def end(sid: str):
        game = get_game(sid)
        with game.lock:
            if game.report is None:
                game.report = compute_report(game.session, include_bot=include_bot)
                game.session.ended = True
            return jsonify({"state": session_state(game.session, game.log), "report": game.report})

    @app.get("/api/sessions/<sid>/report")
    def report(sid: str):
        game = get_game(sid)
        if game.report is None:
            raise ApiError("The report is available once the session has ended.", 409)
        return jsonify(game.report)

    return app
