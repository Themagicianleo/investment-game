"""Local web app: a JSON API over ``Session`` plus the single-page front end.

Run with ``trade-sim web`` and open http://127.0.0.1:8000. Sessions are kept in
memory; learner progress is stored in ``progress.json`` (git-ignored).

Every endpoint reads market data through the session's ``MarketView``, so the
browser never receives prices or events after the session's current day.
"""

from __future__ import annotations

import copy
import threading
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd
from flask import Flask, jsonify, request, send_from_directory

from . import DISCLAIMER, __version__
from .data import DataError, NetworkError, load_market_frames
from .education import coach, missions
from .education.glossary import GLOSSARY
from .education.lessons import LESSON_IDS, grade_quiz, public_lessons
from .education.practice import PRACTICE_BENCHMARK, PRACTICE_TICKERS, make_practice_market
from .education.progress import ProgressStore, level_info
from .education.scenarios import SCENARIO_BY_ID, public_scenarios
from .education.scoring import process_score
from .execution import BUY, SELL
from .market import MarketData
from .report import compute_report
from .session import DayReport, Session, SessionError, fmt_date
from .walkforward import required_warmup

STATIC_DIR = Path(__file__).parent / "web_static"

FINDING_LESSONS = {
    "Trading right after a large move": "biases",
    "Skipping the loss limit": "exit-plans",
    "Adding to a position that was past the loss limit": "exit-plans",
    "Short or missing journal reasoning": "process",
    "Frequent trading": "costs",
}


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
# market data for the different game modes
# ----------------------------------------------------------------------------

class MarketProvider:
    """Loads (and caches) real market data for a set of tickers."""

    def __init__(self, cfg: dict, market: MarketData | None = None,
                 loader: Callable[[list[str]], MarketData] | None = None):
        self.cfg = cfg
        self._fixed = market
        self._loader = loader or self._default_loader
        self._cache: dict[tuple, MarketData] = {}
        self._lock = threading.Lock()

    def _default_loader(self, tickers: list[str]) -> MarketData:
        return MarketData(load_market_frames(tickers, self.cfg["data"]["cache_dir"]))

    def get(self, tickers: list[str]) -> MarketData:
        tickers = list(dict.fromkeys(t.upper() for t in tickers))
        if self._fixed is not None:
            missing = [t for t in tickers if t not in self._fixed.tickers]
            if missing:
                raise DataError(f"No data loaded for: {', '.join(missing)}")
            return self._fixed
        key = tuple(sorted(tickers))
        with self._lock:
            if key not in self._cache:
                self._cache[key] = self._loader(tickers)
            return self._cache[key]


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
                           f"(limit {s.loss_limit:.0%}). Nothing is sold automatically."})
    if rep.journal_error:
        ev.append({"date": d, "type": "warning", "text": rep.journal_error})
    return ev


class _Game:
    def __init__(self, session: Session, mode: str, scenario: dict | None = None):
        self.session = session
        self.mode = mode
        self.scenario = scenario
        self.log: list[dict] = []
        self.coach: list[dict] = []
        self.seen: set[str] = set()
        self.report: dict | None = None
        self.lock = threading.Lock()


def session_state(game: _Game) -> dict:
    s = game.session
    v, a = s.view, s.account
    weights = s.weights()
    positions = []
    for t, p in a.positions.items():
        if p.shares > 0:
            close = v.close(t)
            positions.append({
                "ticker": t, "shares": p.shares, "avg_cost": p.avg_cost, "close": close,
                "value": p.shares * close, "pl": close / p.avg_cost - 1 if p.avg_cost else 0.0,
                "weight": weights.get(t, 0.0), "alert": s.alert_loss(t) is not None,
                "held_days": v.idx - s.open_since[t] if t in s.open_since else 0,
            })
    market = []
    for t in s.tickers:
        today = v.today(t)
        market.append({
            "ticker": t, "open": today["Open"], "high": today["High"], "low": today["Low"],
            "close": today["Close"], "change_1d": _num(v.trailing_return(t, 1)),
            "change_20d": _num(v.trailing_return(t, 20)),
            "description": PRACTICE_TICKERS[t][0] if game.mode == "practice" and t in PRACTICE_TICKERS else None,
        })
    c = s.costs
    scenario = None
    if game.scenario:
        sc = game.scenario
        scenario = {"id": sc["id"], "title": sc["title"], "period": sc["period"], "briefing": sc["briefing"],
                    "lessons": sc["lessons"]}
    return {
        "id": s.session_id,
        "mode": game.mode,
        "scenario": scenario,
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
        "drawdown": s.drawdown(),
        "max_drawdown": s.max_drawdown,
        "positions": positions,
        "pending": [{"side": o.side, "ticker": o.ticker, "amount": o.amount, "reason": o.reason,
                     "placed": fmt_date(o.placed_date)} for o in a.pending],
        "market": market,
        "costs": {"fees": a.fees_paid, "fx": a.fx_paid, "spread_slippage": a.spread_slippage_paid,
                  "dividend_tax": a.dividend_tax_paid, "dividends_net": a.dividends_net},
        "equity_curve": [[fmt_date(d), e] for d, e in s.equity_curve],
        "journal": s.entries,
        "log": game.log[-200:],
        "coach": game.coach[-12:],
        "missions": missions.status(s),
        "process": process_score(s),
        "rules": {
            "tickers": s.tickers, "fee": c.fee_per_order, "half_spread": c.half_spread,
            "slippage": c.slippage, "fx_mode": c.fx_mode, "fx_spread": c.fx_spread,
            "withholding": c.withholding_tax, "loss_limit": s.loss_limit, "fractional": c.fractional,
            "benchmark": s.cfg["benchmark_ticker"],
        },
    }


def history_payload(s: Session, ticker: str, days: int | None) -> dict:
    t = s._check_ticker(ticker)
    full_adj = s.view.adjusted_closes(t)  # everything up to today, split-adjusted as of today
    h = s.view.history(t, days)
    n = len(h)
    series = pd.Series(full_adj)
    ma = {}
    for length in (50, 200):
        vals = series.rolling(length).mean().to_numpy()[-n:]  # trailing window: uses past rows only
        ma[f"ma{length}"] = [None if not np.isfinite(x) else round(float(x), 4) for x in vals]
    dates = [fmt_date(d) for d in h.index]
    date_set = set(dates)
    markers = [{"date": e["fill_date"], "side": e["action"], "price": e["fill_price"], "shares": e["shares"]}
               for e in s.entries if e.get("status") == "filled" and e["ticker"] == t and e["fill_date"] in date_set]
    return {
        "ticker": t,
        "as_of": fmt_date(s.date),
        "dates": dates,
        "open": h["Open"].round(4).tolist(),
        "high": h["High"].round(4).tolist(),
        "low": h["Low"].round(4).tolist(),
        "close": h["Close"].round(4).tolist(),
        "adj_close": np.round(full_adj[-n:], 4).tolist(),
        "volume": h["Volume"].round(0).tolist(),
        "dividends": h["Dividends"].round(6).tolist(),
        "splits": h["Stock Splits"].tolist(),
        **ma,
        "markers": markers,
    }


def recommended_lessons(report: dict, game: _Game) -> list[str]:
    recs = [FINDING_LESSONS[f["title"]] for f in report.get("review", []) if f["title"] in FINDING_LESSONS]
    comps = [c for c in report["process"]["components"] if c["score"] is not None and c["score"] < c["max"] * 0.6]
    recs += [c["lesson"] for c in sorted(comps, key=lambda c: c["score"])]
    bench = report.get("benchmark")
    if bench and report["user"]["return"] < bench["return"]:
        recs.append("benchmarks")
    if report.get("walk_forward"):
        recs.append("backtesting")
    if game.scenario:
        recs += game.scenario["lessons"]
    out = []
    for lid in recs:
        if lid in LESSON_IDS and lid not in out:
            out.append(lid)
    return out[:3]


# ----------------------------------------------------------------------------
# app
# ----------------------------------------------------------------------------

def create_app(cfg: dict, market: MarketData | None = None,
               market_loader: Callable[[list[str]], MarketData] | None = None,
               include_bot: bool = True, progress_path: str | None = "__config__") -> Flask:
    app = Flask(__name__, static_folder=None)
    games: dict[str, _Game] = {}
    provider = MarketProvider(cfg, market, market_loader)
    if progress_path == "__config__":
        progress_path = cfg["data"].get("progress_path", "progress.json")
    progress = ProgressStore(progress_path)

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

    def award_missions(game: _Game, at_end: bool = False) -> list[dict]:
        new = missions.update(game.session, at_end)
        if new:
            gained = progress.record_missions([m["id"] for m in new])
            for m in new:
                game.coach.append(coach.tip(f"mission-{m['id']}", f"Mission complete: {m['title']}",
                                            f"{m['desc']} (+XP)", m["lesson"], "success", fmt_date(game.session.date)))
            game.log.append({"date": fmt_date(game.session.date), "type": "mission",
                             "text": "Mission complete: " + ", ".join(m["title"] for m in new) + f" (+{gained} XP)"})
        return new

    @app.errorhandler(ApiError)
    def _api_error(exc: ApiError):
        return jsonify({"error": str(exc)}), exc.status

    @app.errorhandler(SessionError)
    def _session_error(exc: SessionError):
        return jsonify({"error": str(exc)}), 400

    @app.errorhandler(NetworkError)
    def _network_error(exc: NetworkError):
        return jsonify({"error": f"Network error: {exc}", "offline": True}), 503

    @app.errorhandler(DataError)
    def _data_error(exc: DataError):
        return jsonify({"error": f"Data error: {exc}"}), 503

    # --- static -------------------------------------------------------------
    @app.get("/")
    def index():
        return send_from_directory(STATIC_DIR, "index.html")

    @app.get("/static/<path:name>")
    def static_file(name: str):
        return send_from_directory(STATIC_DIR, name)

    # --- education -----------------------------------------------------------
    @app.get("/api/info")
    def info():
        return jsonify({"version": __version__, "disclaimer": DISCLAIMER, "tickers": cfg["tickers"],
                        "starting_cash": cfg["starting_cash_usd"]})

    @app.get("/api/education")
    def education():
        return jsonify({
            "lessons": public_lessons(),
            "glossary": GLOSSARY,
            "scenarios": public_scenarios(),
            "missions": missions.MISSIONS,
            "practice_tickers": {t: v[0] for t, v in PRACTICE_TICKERS.items()},
            "progress": progress.snapshot(),
        })

    @app.get("/api/progress")
    def get_progress():
        return jsonify(progress.snapshot())

    @app.post("/api/lessons/<lid>/quiz")
    def quiz(lid: str):
        answers = body().get("answers")
        try:
            result = grade_quiz(lid, answers)
        except KeyError:
            raise ApiError("Unknown lesson", 404)
        except ValueError as exc:
            raise ApiError(str(exc))
        result["xp_gained"] = progress.record_quiz(lid, result)
        result["progress"] = progress.snapshot()
        return jsonify(result)

    # --- sessions --------------------------------------------------------------
    @app.post("/api/sessions")
    def new_session():
        data = body()
        mode = str(data.get("mode") or "random")
        seed = data.get("seed")
        if seed in ("", None):
            seed = cfg["session"]["seed"]
        else:
            try:
                seed = int(seed)
            except (TypeError, ValueError):
                raise ApiError("Seed must be a whole number (or leave it blank for a random start).")
        rng = np.random.default_rng(seed)
        scfg = copy.deepcopy(cfg)
        scenario, end_idx = None, None

        if mode == "practice":
            m = make_practice_market(int(rng.integers(0, 2**31)))
            scfg["tickers"] = list(PRACTICE_TICKERS)
            scfg["benchmark_ticker"] = PRACTICE_BENCHMARK
            start = m.random_start_index(rng, required_warmup(scfg), scfg["session"]["min_years_after_start"])
        elif mode == "scenario":
            scenario = SCENARIO_BY_ID.get(str(data.get("scenario")))
            if scenario is None:
                raise ApiError("Unknown scenario")
            scfg["tickers"] = list(scenario["tickers"])
            scfg["benchmark_ticker"] = scenario["benchmark"]
            m = provider.get(scenario["tickers"] + [scenario["benchmark"]])
            start = int(m.dates.searchsorted(pd.Timestamp(scenario["start"])))
            if start < required_warmup(scfg) or start >= len(m) - 1:
                raise ApiError("Not enough data around this scenario's dates for these tickers.", 503)
            end_idx = min(len(m) - 1, start + scenario["days"])
        elif mode == "random":
            m = provider.get(scfg["tickers"] + [scfg["benchmark_ticker"]])
            start = m.random_start_index(rng, required_warmup(scfg), scfg["session"]["min_years_after_start"])
        else:
            raise ApiError("mode must be random, scenario or practice")

        session = Session(m, scfg, start, end_idx=end_idx)
        game = _Game(session, mode, scenario)
        games[session.session_id] = game
        d = fmt_date(session.date)
        if mode == "practice":
            game.log.append({"date": d, "type": "info",
                             "text": "Practice market: fictional companies with randomly generated prices."})
        if session.account.fx_paid:
            game.log.append({"date": d, "type": "info",
                             "text": f"SGD→USD conversion of your deposit cost ${session.account.fx_paid:,.2f} (FX spread)."})
        game.log.append({"date": d, "type": "info",
                         "text": f"Session started on {d} with ${session.account.starting_cash:,.2f}."})
        game.coach.append(coach.tip(
            "welcome", "How to approach this session",
            "Before each trade, decide how much of your portfolio it deserves and write down what would prove you "
            "wrong. The order ticket previews costs and risk; the coach explains events as they happen. Your "
            "process score rewards discipline, not luck.", "process", "info", d))
        return jsonify(session_state(game)), 201

    @app.get("/api/sessions/<sid>")
    def get_state(sid: str):
        return jsonify(session_state(get_game(sid)))

    @app.get("/api/sessions/<sid>/history/<ticker>")
    def history(sid: str, ticker: str):
        game = get_game(sid)
        days = request.args.get("days", type=int)
        if days is not None and days < 1:
            raise ApiError("days must be at least 1")
        return jsonify(history_payload(game.session, ticker, days))

    def _parse_amount(side: str, raw):
        if side == SELL and str(raw).lower() == "all":
            return "all"
        try:
            amount = float(str(raw).replace(",", "").lstrip("$"))
        except (TypeError, ValueError):
            raise ApiError("Amount must be a US dollar amount" + (' or "all"' if side == SELL else ""))
        if not np.isfinite(amount) or amount <= 0:
            raise ApiError("Amount must be a positive number of US dollars")
        return amount

    @app.post("/api/sessions/<sid>/preview")
    def preview(sid: str):
        game = get_game(sid)
        data = body()
        side = str(data.get("side", "")).lower()
        if side not in (BUY, SELL):
            raise ApiError("side must be 'buy' or 'sell'")
        with game.lock:
            s = active(game)
            return jsonify(coach.trade_preview(s, side, str(data.get("ticker", "")), _parse_amount(side, data.get("amount"))))

    @app.post("/api/sessions/<sid>/orders")
    def place_order(sid: str):
        game = get_game(sid)
        data = body()
        with game.lock:
            s = active(game)
            side = str(data.get("side", "")).lower()
            if side not in (BUY, SELL):
                raise ApiError("side must be 'buy' or 'sell'")
            amount = _parse_amount(side, data.get("amount"))
            reason = str(data.get("reason", "")).strip()
            prove_wrong = str(data.get("prove_wrong", "")).strip()
            if not reason:
                raise ApiError("Please write a reason for this trade.")
            if not prove_wrong:
                raise ApiError("Please write what would prove you wrong.")
            ticker = str(data.get("ticker", ""))
            tips = coach.trade_preview(s, side, ticker, amount)["tips"]
            order = s.place(side, ticker, amount, reason, prove_wrong)
            amt = "all shares" if order.amount == "all" else f"${order.amount:,.2f}"
            game.log.append({"date": fmt_date(s.date), "type": "order",
                             "text": f"Order queued: {side} {order.ticker} {amt}. Fills at the next trading day's open."})
            d = fmt_date(s.date)
            game.coach.extend({**t, "date": d} for t in tips if t["kind"] == "warn")
            return jsonify(session_state(game)), 201

    @app.post("/api/sessions/<sid>/review")
    def review_position(sid: str):
        game = get_game(sid)
        data = body()
        with game.lock:
            s = active(game)
            row = s.review_position(str(data.get("ticker", "")), str(data.get("note", "")))
            game.log.append({"date": fmt_date(s.date), "type": "review",
                             "text": f"Position review written for {row['ticker']}."})
            award_missions(game)
            return jsonify(session_state(game)), 201

    @app.post("/api/sessions/<sid>/cancel")
    def cancel(sid: str):
        game = get_game(sid)
        with game.lock:
            s = active(game)
            n = len(s.cancel_all())
            game.log.append({"date": fmt_date(s.date), "type": "info", "text": f"Cancelled {n} pending order(s)."})
            return jsonify(session_state(game))

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
            new_tips = coach.event_tips(s, reports, game.seen)
            game.coach.extend(new_tips)
            new_missions = award_missions(game)
            if stopped:
                game.log.append({"date": fmt_date(s.date), "type": "info", "text": stopped})
            payload = session_state(game)
            payload.update(advanced=len(reports), stopped=stopped, new_events=events, new_tips=new_tips,
                           new_missions=new_missions)
            return jsonify(payload)

    @app.post("/api/sessions/<sid>/end")
    def end(sid: str):
        game = get_game(sid)
        with game.lock:
            if game.report is None:
                s = game.session
                new_missions = award_missions(game, at_end=True)
                rep = compute_report(s, include_bot=include_bot)
                rep["mode"] = game.mode
                rep["scenario"] = None
                if game.scenario:
                    rep["scenario"] = {k: game.scenario[k] for k in ("id", "title", "period", "debrief")}
                rep["missions_completed"] = [{**missions.MISSION_BY_ID[m], "date": d}
                                             for m, d in getattr(s, "missions_done", {}).items()]
                rep["new_missions"] = [m["id"] for m in new_missions]
                rep["recommended_lessons"] = recommended_lessons(rep, game)
                before = progress.snapshot()["xp"]
                progress.record_session({
                    "mode": game.mode, "scenario": game.scenario["title"] if game.scenario else None,
                    "start": rep["start"], "end": rep["end"], "days": rep["trading_days"],
                    "return": rep["user"]["return"],
                    "benchmark_return": rep["benchmark"]["return"] if rep["benchmark"] else None,
                    "max_drawdown": rep["user"]["max_drawdown"], "process_score": rep["process"]["score"],
                    "trades": rep["user"]["orders"],
                })
                snap = progress.snapshot()
                session_xp = sum(m["xp"] for m in rep["missions_completed"])
                rep["xp"] = {"session_total_missions": session_xp, "gained_now": snap["xp"] - before,
                             "level": snap["level"]}
                game.report = rep
                s.ended = True
            return jsonify({"state": session_state(game), "report": game.report})

    @app.get("/api/sessions/<sid>/report")
    def report(sid: str):
        game = get_game(sid)
        if game.report is None:
            raise ApiError("The report is available once the session has ended.", 409)
        return jsonify(game.report)

    app.config["PROGRESS"] = progress
    return app


__all__ = ["create_app", "level_info"]
