"""Command-line interface: ``trade-sim play | fetch | walkforward``."""

from __future__ import annotations

import argparse
import shlex
import sys
from typing import Callable

import numpy as np

from . import DISCLAIMER, __version__
from .config import ConfigError, load_config
from .data import DataError, NetworkError, load_market_frames
from .execution import BUY, SELL
from .market import EndOfData, MarketData
from .report import build_report, walk_forward_dict, walk_forward_lines
from .session import DayReport, Session, SessionError, fmt_date
from .walkforward import required_warmup, run_walk_forward

HELP = """Commands
  buy <ticker> <USD amount>     market order, fills at the NEXT trading day's open
  sell <ticker> <USD amount|all> market order, fills at the NEXT trading day's open
  hold [days]                   advance one trading day (or several; stops early on an alert)
  status                        cash, positions, pending orders, today's closes
  history <ticker> [days]       past daily prices up to today (default 10 days)
  journal                       this session's journal entries
  cancel                        cancel all pending orders
  end                           finish and show the end-of-session report
  help                          show this help"""


class _Quit(Exception):
    pass


# ----------------------------------------------------------------------------
# formatting
# ----------------------------------------------------------------------------

def status_text(s: Session) -> str:
    v, a = s.view, s.account
    L = [f"Date {fmt_date(s.date)}  (trading day {v.days_elapsed()} of this session)",
         f"Cash ${a.cash:,.2f}   Holdings ${a.market_value():,.2f}   Total ${a.equity():,.2f}   "
         f"Return {a.total_return():+.2%}"]
    held = [(t, p) for t, p in a.positions.items() if p.shares > 0]
    if held:
        L.append("Positions")
        for t, p in held:
            close = v.close(t)
            pl = close / p.avg_cost - 1 if p.avg_cost else 0.0
            flag = "   [LOSS LIMIT]" if s.alert_loss(t) is not None else ""
            L.append(f"  {t:<5} {p.shares:>10g} sh  avg cost ${p.avg_cost:>9,.2f}  close ${close:>9,.2f}  "
                     f"value ${p.shares * close:>11,.2f}  {pl:+.2%}{flag}")
    else:
        L.append("Positions: none")
    if a.pending:
        L.append("Pending orders (fill at next open)")
        for o in a.pending:
            amt = "all" if o.amount == "all" else f"${o.amount:,.2f}"
            L.append(f"  {o.side} {o.ticker} {amt}")
    L.append("Market at today's close        1-day     20-day")
    for t in s.tickers:
        r1, r20 = v.trailing_return(t, 1), v.trailing_return(t, 20)
        f = lambda r: "    n/a" if r is None else f"{r:+7.2%}"
        L.append(f"  {t:<5} ${v.close(t):>10,.2f}        {f(r1)}   {f(r20)}")
    L.append(f"Costs so far: fees ${a.fees_paid:,.2f}  FX ${a.fx_paid:,.2f}  spread+slippage "
             f"${a.spread_slippage_paid:,.2f}  dividend tax ${a.dividend_tax_paid:,.2f}")
    return "\n".join(L)


def day_text(s: Session, rep: DayReport, verbose: bool = True) -> str:
    st = rep.step
    L = [f"--- {fmt_date(st.date)} ---"]
    for sp in st.splits:
        L.append(f"  SPLIT {sp.ticker} {sp.ratio:g}-for-1: {sp.shares_before:g} -> {sp.shares_after:g} shares")
    for d in st.dividends:
        L.append(f"  DIVIDEND {d.ticker} ${d.per_share:.4f}/sh x {d.shares:g}: gross ${d.gross:,.2f}, "
                 f"withholding tax -${d.tax:,.2f}, credited ${d.net:,.2f}")
    for f in st.fills:
        L.append(f"  FILLED {f.order.side.upper()} {f.shares:g} {f.order.ticker} @ ${f.price:,.4f} "
                 f"(open ${f.open_price:,.4f}) fee ${f.fee:.2f}"
                 + (f" FX ${f.fx:,.2f}" if f.fx else "") + f"  cash {f.cash_change:+,.2f}")
    for r in st.rejections:
        L.append(f"  NOT FILLED {r.order.side} {r.order.ticker}: {r.reason}")
    for ep in rep.new_alerts:
        L.append(f"  !! LOSS-LIMIT ALERT: {ep.ticker} is {ep.loss:.1%} below your average cost "
                 f"(limit {s.loss_limit:.0%}). Nothing is sold automatically; check your journal note "
                 "on what would prove you wrong.")
    if rep.ongoing_alerts:
        L.append(f"  (still past the loss limit: {', '.join(rep.ongoing_alerts)})")
    if rep.journal_error:
        L.append(f"  WARNING: {rep.journal_error}")
    if verbose:
        closes = "  ".join(f"{t} {s.view.close(t):,.2f}" for t in s.tickers)
        L.append(f"  Close: {closes}")
        L.append(f"  Total value ${s.account.equity():,.2f} ({s.account.total_return():+.2%})")
    return "\n".join(L)


def journal_text(s: Session) -> str:
    if not s.entries and not s.account.pending:
        return "No journal entries yet this session."
    L = []
    for e in s.entries:
        if e["status"] == "filled":
            L.append(f"{e['fill_date']} {e['action'].upper():<4} {e['shares']:g} {e['ticker']} @ ${e['fill_price']:,.4f}"
                     f"  fee ${e['fee_usd']:.2f}")
        else:
            L.append(f"{e['fill_date']} REJECTED {e['action']} {e['ticker']}: {e['note']}")
        L.append(f"    reason: {e['reason']}")
        L.append(f"    would prove me wrong: {e['prove_wrong'] or '(blank)'}")
    for o in s.account.pending:
        L.append(f"(pending) {o.side} {o.ticker}  reason: {o.reason}")
    L.append(f"Saved to {s.journal_path}")
    return "\n".join(L)


def history_text(s: Session, ticker: str, n: int) -> str:
    t = s._check_ticker(ticker)
    h = s.view.history(t, n)
    L = [f"{t} — last {len(h)} trading days up to {fmt_date(s.date)} (as traded)",
         "Date            Open       High        Low      Close       Volume   Div  Split"]
    for d, r in h.iterrows():
        L.append(f"{fmt_date(d)} {r.Open:>10,.2f} {r.High:>10,.2f} {r.Low:>10,.2f} {r.Close:>10,.2f} "
                 f"{r.Volume:>12,.0f} {r.Dividends or 0:>5g} {r['Stock Splits'] or 0:>5g}")
    return "\n".join(L)


# ----------------------------------------------------------------------------
# REPL
# ----------------------------------------------------------------------------

def parse_amount(text: str, allow_all: bool) -> float | str:
    t = text.strip().lower().replace(",", "").lstrip("$")
    if allow_all and t == "all":
        return "all"
    try:
        value = float(t)
    except ValueError:
        raise SessionError(f"'{text}' is not a valid USD amount" + (' (or "all")' if allow_all else ""))
    if not np.isfinite(value) or value <= 0:
        raise SessionError("Amount must be a positive number of US dollars")
    return value


def ask_required(ask: Callable[[str], str], out, prompt: str) -> str:
    while True:
        answer = ask(prompt).strip()
        if answer.lower() == "cancel":
            raise SessionError("Order cancelled.")
        if answer:
            return answer
        out("  Please write something (or type 'cancel' to drop the order).")


def handle(s: Session, line: str, ask: Callable[[str], str], out) -> None:
    try:
        parts = shlex.split(line)
    except ValueError as exc:
        raise SessionError(f"Could not parse command: {exc}")
    if not parts:
        return
    cmd, args = parts[0].lower(), parts[1:]

    if cmd in ("buy", "sell"):
        if len(args) != 2:
            raise SessionError(f"Usage: {cmd} <ticker> <USD amount{'|all' if cmd == 'sell' else ''}>")
        side = BUY if cmd == "buy" else SELL
        amount = parse_amount(args[1], allow_all=side == SELL)
        ticker = s.validate_buy(args[0], amount) if side == BUY else s.validate_sell(args[0], amount)
        loss = s.alert_loss(ticker)
        if loss is not None:
            out(f"  Note: {ticker} is currently {loss:.1%} below your average cost (loss-limit alert active).")
        reason = ask_required(ask, out, "  Reason for this trade: ")
        prove_wrong = ask_required(ask, out, "  What would prove me wrong? ")
        o = s.place(side, ticker, amount, reason, prove_wrong)
        amt = "all shares" if o.amount == "all" else f"${o.amount:,.2f}"
        out(f"  Order queued: {side} {ticker} {amt}. It fills at the next trading day's open "
            f"(+/- {(s.costs.half_spread + s.costs.slippage):.2%} spread & slippage, ${s.costs.fee_per_order:.2f} fee). "
            "Type 'hold' to advance.")
    elif cmd == "hold":
        days = 1
        if args:
            try:
                days = int(args[0])
            except ValueError:
                raise SessionError("Usage: hold [days]")
        reports, stopped = s.hold(days)
        for i, rep in enumerate(reports):
            st = rep.step
            eventful = st.fills or st.rejections or st.dividends or st.splits or rep.new_alerts
            if eventful or i == len(reports) - 1:
                out(day_text(s, rep, verbose=True))
        if stopped:
            out(stopped)
            if not s.view.has_next():
                raise _Quit()
    elif cmd == "status":
        out(status_text(s))
    elif cmd == "journal":
        out(journal_text(s))
    elif cmd == "history":
        if not args:
            raise SessionError("Usage: history <ticker> [days]")
        n = 10
        if len(args) > 1:
            try:
                n = max(1, int(args[1]))
            except ValueError:
                raise SessionError("days must be a whole number")
        out(history_text(s, args[0], n))
    elif cmd == "cancel":
        cancelled = s.cancel_all()
        out(f"Cancelled {len(cancelled)} pending order(s).")
    elif cmd in ("end", "quit", "exit"):
        raise _Quit()
    elif cmd in ("help", "?"):
        out(HELP)
    else:
        raise SessionError(f"Unknown command '{cmd}'. Type 'help' for the list of commands.")


def run_repl(s: Session, ask: Callable[[str], str] = input, out=print, include_bot: bool = True) -> str:
    out(DISCLAIMER)
    out("")
    out(f"Session {s.session_id}: starting on {fmt_date(s.date)} with ${s.account.starting_cash:,.2f}.")
    if s.account.fx_paid:
        out(f"SGD->USD conversion of your deposit cost ${s.account.fx_paid:,.2f} (FX spread).")
    out("You only see data up to the current day. Type 'help' for commands.\n")
    out(status_text(s))
    while True:
        try:
            line = ask(f"\n[{fmt_date(s.date)}] > ")
        except (EOFError, KeyboardInterrupt):
            out("")
            break
        try:
            handle(s, line, ask, out)
        except _Quit:
            break
        except (EOFError, KeyboardInterrupt):
            out("\nOrder cancelled.")
        except (SessionError, EndOfData) as exc:
            out(f"  {exc}")
    report = build_report(s, include_bot=include_bot)
    out(report)
    return report


# ----------------------------------------------------------------------------
# entry points
# ----------------------------------------------------------------------------

def _load_market(cfg: dict, refresh: bool) -> MarketData:
    tickers = list(dict.fromkeys(cfg["tickers"] + [cfg["benchmark_ticker"]]))
    return MarketData(load_market_frames(tickers, cfg["data"]["cache_dir"], refresh=refresh))


def cmd_play(args, cfg) -> int:
    if args.no_bot:
        cfg["trend_bot"]["enabled"] = False
    market = _load_market(cfg, args.refresh)
    seed = args.seed if args.seed is not None else cfg["session"]["seed"]
    rng = np.random.default_rng(seed)
    warmup = required_warmup(cfg)
    start = market.random_start_index(rng, warmup, cfg["session"]["min_years_after_start"])
    session = Session(market, cfg, start, journal_path=args.journal)
    run_repl(session, include_bot=not args.no_bot)
    return 0


def cmd_fetch(args, cfg) -> int:
    tickers = [t.upper() for t in args.tickers] or list(dict.fromkeys(cfg["tickers"] + [cfg["benchmark_ticker"]]))
    frames = load_market_frames(tickers, cfg["data"]["cache_dir"], refresh=args.refresh)
    for t, df in frames.items():
        print(f"{t}: {len(df)} rows, {fmt_date(df.index[0])} to {fmt_date(df.index[-1])}")
    print(f"Cached in {cfg['data']['cache_dir']}/")
    return 0


def cmd_walkforward(args, cfg) -> int:
    from .execution import Costs
    import pandas as pd

    market = _load_market(cfg, args.refresh)
    warmup = required_warmup(cfg)
    start = warmup
    end = len(market) - 1
    if args.start:
        start = max(start, int(market.dates.searchsorted(pd.Timestamp(args.start))))
    if args.end:
        end = min(end, int(market.dates.searchsorted(pd.Timestamp(args.end), side="right")) - 1)
    if start >= end:
        raise DataError("The chosen date range has no usable data after the moving-average warm-up period.")
    tickers = [t for t in cfg["tickers"] if t in market.tickers]
    wf = run_walk_forward(market, start, end, cfg, Costs.from_config(cfg), cfg["starting_cash_usd"], tickers)
    print(DISCLAIMER + "\n")
    print("\n".join(walk_forward_lines(walk_forward_dict(wf, market.dates))))
    print("\nIn-sample return of every candidate (the best one was chosen):")
    for rule, ret in sorted(wf.table, key=lambda r: -r[1]):
        print(f"  {str(rule):<12} {ret:+.2%}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="trade-sim",
        description="Educational trading simulator: replay US stock/ETF history one day at a time. "
                    "Not investment advice.",
    )
    p.add_argument("--version", action="version", version=f"trade-sim {__version__}")
    p.add_argument("-c", "--config", help="path to config file (default: config.yaml, else config.example.yaml)")
    sub = p.add_subparsers(dest="command")

    play = sub.add_parser("play", help="start an interactive session (default)")
    play.add_argument("--seed", type=int, help="random seed for the start date (reproducible sessions)")
    play.add_argument("--journal", help="journal CSV path (default from config)")
    play.add_argument("--no-bot", action="store_true", help="skip the trend-bot benchmark in the report")
    play.add_argument("--refresh", action="store_true", help="re-download data instead of using the cache")

    fetch = sub.add_parser("fetch", help="download and cache data without playing")
    fetch.add_argument("tickers", nargs="*", help="tickers (default: from config)")
    fetch.add_argument("--refresh", action="store_true", help="re-download even if cached")

    wf = sub.add_parser("walkforward", help="walk-forward test of the trend bot's MA lengths over full history")
    wf.add_argument("--start", help="first date (YYYY-MM-DD)")
    wf.add_argument("--end", help="last date (YYYY-MM-DD)")
    wf.add_argument("--refresh", action="store_true", help="re-download data instead of using the cache")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    parser.set_defaults(seed=None, journal=None, no_bot=False, refresh=False)
    args = parser.parse_args(argv)
    if args.command is None:
        args.command = "play"
    try:
        cfg, source = load_config(args.config)
        if source != "config.yaml" and args.config is None:
            print(f"(Using {source}; copy config.example.yaml to config.yaml to customise.)")
        handlers = {"play": cmd_play, "fetch": cmd_fetch, "walkforward": cmd_walkforward}
        return handlers[args.command](args, cfg)
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
    except NetworkError as exc:
        print(f"Network error: {exc}", file=sys.stderr)
    except DataError as exc:
        print(f"Data error: {exc}", file=sys.stderr)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
