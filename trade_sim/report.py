"""End-of-session report."""

from __future__ import annotations

from . import DISCLAIMER
from .bots import BenchmarkResult, TrendRule, run_buy_and_hold, run_trend_bot
from .review import review
from .session import Session, fmt_date
from .walkforward import WalkForwardResult, run_walk_forward


def _pct(x: float) -> str:
    return f"{x:+.2%}"


def _bench_line(r: BenchmarkResult) -> str:
    return (f"  {r.name:<26} {_pct(r.total_return):>9}   final ${r.final_value:>11,.2f}   "
            f"trades {r.trades:>3}   costs ${r.fees + r.fx + r.spread_slippage:,.2f}")


def format_walk_forward(wf: WalkForwardResult, dates) -> list[str]:
    s = wf.split
    lines = [
        "Walk-forward check of the trend bot (benchmark only)",
        f"  In-sample   {fmt_date(dates[s.train_start])} to {fmt_date(dates[s.train_end])} ({s.train_len} days): "
        f"MA lengths chosen here",
        f"  Out-sample  {fmt_date(dates[s.test_start])} to {fmt_date(dates[s.test_end])} ({s.test_len} days): "
        f"chosen lengths run unchanged",
        f"  Chosen rule {wf.chosen}:   in-sample {_pct(wf.in_sample.total_return)}   "
        f"out-of-sample {_pct(wf.out_of_sample.total_return)}",
        f"  Default {wf.default_rule}:  in-sample {_pct(wf.default_in_sample.total_return)}   "
        f"out-of-sample {_pct(wf.default_out_of_sample.total_return)}",
        "  The in-sample figure was selected *because* it was the best on that data, so it is optimistic",
        "  by construction. Only the out-of-sample figure is a (single, small) test, and it is not proof.",
    ]
    return lines


def build_report(session: Session, include_bot: bool = True) -> str:
    cfg = session.cfg
    acct = session.account
    market, start, end = session.bounded_market()
    dates = market.dates
    cash0 = acct.starting_cash
    days = end - start
    L: list[str] = []
    L.append("=" * 78)
    L.append("END OF SESSION REPORT")
    L.append(f"Period: {fmt_date(dates[start])} to {fmt_date(dates[end])} ({days} trading days)")
    L.append("=" * 78)

    total_costs = acct.fees_paid + acct.fx_paid + acct.spread_slippage_paid
    L.append("You")
    L.append(f"  Final value:        ${acct.equity():,.2f}  (started with ${cash0:,.2f})")
    L.append(f"  Return:             {_pct(acct.total_return())}")
    L.append(f"  Orders filled:      {acct.order_count}")
    L.append(f"  Flat fees:          ${acct.fees_paid:,.2f}")
    L.append(f"  FX spread:          ${acct.fx_paid:,.2f}")
    L.append(f"  Spread + slippage:  ${acct.spread_slippage_paid:,.2f}")
    L.append(f"  Dividend tax:       ${acct.dividend_tax_paid:,.2f}  (net dividends received ${acct.dividends_net:,.2f})")
    L.append(f"  Total fees+taxes:   ${total_costs + acct.dividend_tax_paid:,.2f}")
    if acct.pending:
        L.append(f"  ({len(acct.pending)} pending order(s) cancelled at session end)")
    L.append("")

    L.append("Benchmarks over the same dates, same costs (comparisons, NOT recommendations)")
    bench = cfg["benchmark_ticker"]
    if include_bot and cfg["trend_bot"]["enabled"]:
        rule = TrendRule(cfg["trend_bot"]["short_ma"], cfg["trend_bot"]["long_ma"])
        L.append(_bench_line(run_trend_bot(market, start, end, rule, session.costs, cash0, session.tickers)))
        L.append("    ^ trend bot: follows PAST trends (holds a ticker while its "
                 f"{rule.short_ma}-day MA > {rule.long_ma}-day MA)")
    if bench in market.tickers:
        L.append(_bench_line(run_buy_and_hold(market, bench, start, end, session.costs, cash0)))
    L.append("")
    L.append("Each ticker bought at the first open and held through the period")
    for t in session.tickers:
        L.append(_bench_line(run_buy_and_hold(market, t, start, end, session.costs, cash0)))
    L.append("")

    wf_cfg = cfg["trend_bot"]["walk_forward"]
    if include_bot and cfg["trend_bot"]["enabled"] and wf_cfg["enabled"]:
        try:
            wf = run_walk_forward(market, start, end, cfg, session.costs, cash0, session.tickers)
            L.extend(format_walk_forward(wf, dates))
        except ValueError as exc:
            L.append(f"Walk-forward check skipped: {exc}")
        L.append("")

    L.append("Journal review (patterns to reflect on, not advice)")
    findings = review(session.entries, session.episodes, cfg, end, days, total_costs, cash0)
    if not findings:
        L.append("  No common patterns were flagged in this session's journal.")
    for f in findings[:3]:
        L.append(f"  - {f.title} (x{f.count}): {f.detail}")
    L.append("")
    L.append("Important")
    L.append("  * Educational only. This simulator does not give buy or sell advice.")
    L.append("  * Past performance does not predict future results.")
    L.append("  * The trend bot is a comparison, not a recommendation.")
    L.append("=" * 78)
    return "\n".join(L)


__all__ = ["build_report", "DISCLAIMER"]
