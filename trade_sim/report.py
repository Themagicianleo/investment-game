"""End-of-session report: ``compute_report`` returns plain data, ``build_report`` formats it as text."""

from __future__ import annotations

from dataclasses import asdict

from .bots import BenchmarkResult, TrendRule, run_buy_and_hold, run_trend_bot
from .education.scoring import process_score
from .review import review
from .session import Session, fmt_date
from .walkforward import WalkForwardResult, run_walk_forward

NOTICES = [
    "Educational only. This simulator does not give buy or sell advice.",
    "Past performance does not predict future results.",
    "The trend bot is a comparison, not a recommendation.",
]

WALK_FORWARD_CAVEAT = (
    "The in-sample figure was selected *because* it was the best on that data, so it is optimistic "
    "by construction. Only the out-of-sample figure is a (single, small) test, and it is not proof."
)


def _bench(r: BenchmarkResult) -> dict:
    return {
        "name": r.name, "start": fmt_date(r.start), "end": fmt_date(r.end),
        "final_value": r.final_value, "return": r.total_return, "trades": r.trades,
        "costs": r.fees + r.fx + r.spread_slippage, "dividend_tax": r.dividend_tax,
        "max_drawdown": r.max_drawdown,
    }


def walk_forward_dict(wf: WalkForwardResult, dates) -> dict:
    s = wf.split
    return {
        "train": {"start": fmt_date(dates[s.train_start]), "end": fmt_date(dates[s.train_end]), "days": s.train_len},
        "test": {"start": fmt_date(dates[s.test_start]), "end": fmt_date(dates[s.test_end]), "days": s.test_len},
        "chosen_rule": str(wf.chosen),
        "chosen_in_sample": wf.in_sample.total_return,
        "chosen_out_of_sample": wf.out_of_sample.total_return,
        "default_rule": str(wf.default_rule),
        "default_in_sample": wf.default_in_sample.total_return,
        "default_out_of_sample": wf.default_out_of_sample.total_return,
        "candidates": [{"rule": str(r), "in_sample": ret} for r, ret in sorted(wf.table, key=lambda x: -x[1])],
        "caveat": WALK_FORWARD_CAVEAT,
    }


def compute_report(session: Session, include_bot: bool = True) -> dict:
    cfg = session.cfg
    acct = session.account
    market, start, end = session.bounded_market()
    dates = market.dates
    cash0 = acct.starting_cash
    days = end - start
    trading_costs = acct.fees_paid + acct.fx_paid + acct.spread_slippage_paid

    rep: dict = {
        "start": fmt_date(dates[start]), "end": fmt_date(dates[end]), "trading_days": days,
        "user": {
            "starting_cash": cash0, "final_value": acct.equity(), "return": acct.total_return(),
            "orders": acct.order_count, "fees": acct.fees_paid, "fx": acct.fx_paid,
            "spread_slippage": acct.spread_slippage_paid, "dividend_tax": acct.dividend_tax_paid,
            "dividends_net": acct.dividends_net, "total_fees_and_taxes": trading_costs + acct.dividend_tax_paid,
            "cancelled_orders": len(acct.pending),
            "max_drawdown": session.max_drawdown,
        },
        "trend_bot": None, "trend_bot_rule": None, "benchmark": None, "tickers": [],
        "walk_forward": None, "walk_forward_skipped": None,
        "notices": NOTICES,
    }

    bot_on = include_bot and cfg["trend_bot"]["enabled"]
    if bot_on:
        rule = TrendRule(cfg["trend_bot"]["short_ma"], cfg["trend_bot"]["long_ma"])
        rep["trend_bot"] = _bench(run_trend_bot(market, start, end, rule, session.costs, cash0, session.tickers))
        rep["trend_bot_rule"] = (f"Follows PAST trends: holds a ticker while its {rule.short_ma}-day moving "
                                 f"average is above its {rule.long_ma}-day moving average, otherwise cash.")
    bench = cfg["benchmark_ticker"]
    if bench in market.tickers:
        rep["benchmark"] = _bench(run_buy_and_hold(market, bench, start, end, session.costs, cash0))
    rep["tickers"] = [_bench(run_buy_and_hold(market, t, start, end, session.costs, cash0)) for t in session.tickers]

    if bot_on and cfg["trend_bot"]["walk_forward"]["enabled"]:
        try:
            wf = run_walk_forward(market, start, end, cfg, session.costs, cash0, session.tickers)
            rep["walk_forward"] = walk_forward_dict(wf, dates)
        except ValueError as exc:
            rep["walk_forward_skipped"] = str(exc)

    findings = review(session.entries, session.episodes, cfg, end, days, trading_costs, cash0)
    rep["review"] = [asdict(f) for f in findings[:3]]
    rep["process"] = process_score(session)
    return rep


# ---------------------------------------------------------------------------
# text formatting (CLI)
# ---------------------------------------------------------------------------

def _pct(x: float) -> str:
    return f"{x:+.2%}"


def _bench_line(b: dict) -> str:
    return (f"  {b['name']:<26} {_pct(b['return']):>9}   final ${b['final_value']:>11,.2f}   "
            f"trades {b['trades']:>3}   costs ${b['costs']:,.2f}   max DD {-b.get('max_drawdown', 0):.1%}")


def walk_forward_lines(wf: dict) -> list[str]:
    tr, te = wf["train"], wf["test"]
    return [
        "Walk-forward check of the trend bot (benchmark only)",
        f"  In-sample   {tr['start']} to {tr['end']} ({tr['days']} days): MA lengths chosen here",
        f"  Out-sample  {te['start']} to {te['end']} ({te['days']} days): chosen lengths run unchanged",
        f"  Chosen rule {wf['chosen_rule']}:   in-sample {_pct(wf['chosen_in_sample'])}   "
        f"out-of-sample {_pct(wf['chosen_out_of_sample'])}",
        f"  Default {wf['default_rule']}:  in-sample {_pct(wf['default_in_sample'])}   "
        f"out-of-sample {_pct(wf['default_out_of_sample'])}",
        "  The in-sample figure was selected *because* it was the best on that data, so it is optimistic",
        "  by construction. Only the out-of-sample figure is a (single, small) test, and it is not proof.",
    ]


def format_report(rep: dict) -> str:
    u = rep["user"]
    L = ["=" * 78, "END OF SESSION REPORT",
         f"Period: {rep['start']} to {rep['end']} ({rep['trading_days']} trading days)", "=" * 78,
         "You",
         f"  Final value:        ${u['final_value']:,.2f}  (started with ${u['starting_cash']:,.2f})",
         f"  Return:             {_pct(u['return'])}",
         f"  Orders filled:      {u['orders']}",
         f"  Flat fees:          ${u['fees']:,.2f}",
         f"  FX spread:          ${u['fx']:,.2f}",
         f"  Spread + slippage:  ${u['spread_slippage']:,.2f}",
         f"  Dividend tax:       ${u['dividend_tax']:,.2f}  (net dividends received ${u['dividends_net']:,.2f})",
         f"  Total fees+taxes:   ${u['total_fees_and_taxes']:,.2f}",
         f"  Max drawdown:       {-u['max_drawdown']:.2%}"]
    if u["cancelled_orders"]:
        L.append(f"  ({u['cancelled_orders']} pending order(s) cancelled at session end)")
    L += ["", "Benchmarks over the same dates, same costs (comparisons, NOT recommendations)"]
    if rep["trend_bot"]:
        L.append(_bench_line(rep["trend_bot"]))
        L.append(f"    ^ trend bot: {rep['trend_bot_rule']}")
    if rep["benchmark"]:
        L.append(_bench_line(rep["benchmark"]))
    L += ["", "Each ticker bought at the first open and held through the period"]
    L += [_bench_line(b) for b in rep["tickers"]]
    L.append("")
    if rep["walk_forward"]:
        L += walk_forward_lines(rep["walk_forward"]) + [""]
    elif rep["walk_forward_skipped"]:
        L += [f"Walk-forward check skipped: {rep['walk_forward_skipped']}", ""]
    proc = rep.get("process")
    if proc:
        score = "n/a (no trades)" if proc["score"] is None else f"{proc['score']}/100"
        L.append(f"Process score: {score}  ({proc['note']})")
        for c in proc["components"]:
            pts = "  n/a" if c["score"] is None else f"{c['score']:>5g}/{c['max']}"
            L.append(f"  {c['name']:<28} {pts}  {c['detail']}")
        L.append("")
    L.append("Journal review (patterns to reflect on, not advice)")
    if not rep["review"]:
        L.append("  No common patterns were flagged in this session's journal.")
    for f in rep["review"]:
        L.append(f"  - {f['title']} (x{f['count']}): {f['detail']}")
    L += ["", "Important"] + [f"  * {n}" for n in rep["notices"]] + ["=" * 78]
    return "\n".join(L)


def build_report(session: Session, include_bot: bool = True) -> str:
    return format_report(compute_report(session, include_bot))
