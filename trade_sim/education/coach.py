"""Coach: cost/risk previews before a trade and explanations after events.

The coach explains mechanics and points out moments when well-known biases
tend to appear. It never says whether to buy, sell or hold.
"""

from __future__ import annotations

from .. import execution as ex
from ..execution import BUY, SELL


def tip(tid: str, title: str, text: str, lesson: str | None = None, kind: str = "info", date: str | None = None) -> dict:
    return {"id": tid, "title": title, "text": text, "lesson": lesson, "kind": kind, "date": date}


def trade_preview(s, side: str, ticker: str, amount) -> dict:
    """Estimate a trade's cost and risk using today's close as a stand-in for tomorrow's unknown open."""
    from ..session import SessionError

    t = s._check_ticker(ticker)
    c = s.costs
    close = s.view.close(t)
    equity = s.account.equity()
    pos = s.account.positions.get(t)
    held = pos.shares if pos else 0.0
    est_price = ex.fill_price(close, side, c)
    adverse = c.half_spread + c.slippage

    if side == BUY:
        try:
            budget = float(amount)
        except (TypeError, ValueError):
            raise SessionError("Enter a US dollar amount to preview the order.")
        budget = min(budget, max(s.available_cash(), 0.0))
        shares = ex.buy_quantity(budget, est_price, c)
        gross, fee, fx, total = ex.buy_total(shares, est_price, c)
        spread_cost = shares * close * adverse
        shares_after = held + shares
        cash_after = s.available_cash() - total
        # cost of a later sale at an unchanged price
        exit_cost = c.fee_per_order + shares * close * adverse
        round_trip = fee + fx + spread_cost + exit_cost
        break_even = round_trip / gross if gross > 0 else None
    elif side == SELL:
        if held <= 0:
            raise SessionError(f"You hold no {t} shares.")
        if str(amount).lower() == "all":
            shares = held
        else:
            try:
                shares = ex.sell_quantity(float(amount), est_price, held, c)
            except (TypeError, ValueError):
                raise SessionError('Enter a US dollar amount or "all" to preview the order.')
        gross, fee, net = ex.sell_total(shares, est_price, c)
        fx, total = 0.0, -net
        spread_cost = shares * close * adverse
        shares_after = held - shares
        cash_after = s.available_cash() + net
        round_trip, break_even = None, None
    else:
        raise SessionError("side must be buy or sell")

    value_after = shares_after * close
    weight_after = value_after / equity if equity > 0 else 0.0
    move = s.view.trailing_return(t, s.cfg["review"]["large_move_lookback_days"])
    preview = {
        "ticker": t, "side": side, "close": close, "est_fill": est_price, "shares": shares,
        "gross": gross, "fee": fee, "fx": fx, "spread_slippage": spread_cost,
        "costs": fee + fx + spread_cost,
        "cost_pct": (fee + fx + spread_cost) / gross if gross > 0 else None,
        "break_even_move": break_even, "weight_after": weight_after, "value_after": value_after,
        "loss_if_down_20": value_after * 0.20, "loss_if_down_20_pct_portfolio": value_after * 0.20 / equity if equity else 0.0,
        "cash_after": cash_after, "prior_move": move,
        "note": "Estimate only. It uses today's close; your order fills at tomorrow's open, which nobody can see yet.",
    }
    preview["tips"] = trade_tips(s, side, t, preview, pos)
    return preview


def trade_tips(s, side: str, t: str, p: dict, pos) -> list[dict]:
    tips = []
    c = s.costs
    large = s.cfg["review"]["large_move_pct"] / 100.0
    lookback = s.cfg["review"]["large_move_lookback_days"]
    move = p["prior_move"]

    if p["shares"] <= 0:
        tips.append(tip("no-shares", "This order would buy nothing",
                        f"At about ${p['est_fill']:,.2f} a share, the amount is too small for one whole share after the "
                        f"${c.fee_per_order:.2f} fee.", "orders", "warn"))
        return tips
    if side == BUY and p["gross"] > 0 and c.fee_per_order / p["gross"] >= 0.005:
        tips.append(tip("fee-drag", "The flat fee is a big slice of this order",
                        f"The ${c.fee_per_order:.2f} fee is {c.fee_per_order / p['gross']:.1%} of this order. Including "
                        f"selling later, the price must rise about {p['break_even_move']:.1%} just to break even.",
                        "costs", "warn"))
    if side == BUY and p["weight_after"] > 0.40:
        tips.append(tip("concentration", "Concentration check",
                        f"After this trade {t} would be about {p['weight_after']:.0%} of your portfolio. A 20% fall in "
                        f"{t} alone would cost about {p['loss_if_down_20_pct_portfolio']:.1%} of everything you have. "
                        "There is no right answer; just make sure the size matches your conviction and your exit plan.",
                        "sizing", "warn"))
    if move is not None and side == BUY and move >= large:
        tips.append(tip("chasing", "Buying after a sharp rise",
                        f"{t} is up {move:.1%} over the last {lookback} trading days. Recent moves feel predictive "
                        "(recency bias / FOMO). Would your reason for this trade still exist without that move?",
                        "biases", "warn"))
    if move is not None and side == SELL and move <= -large:
        tips.append(tip("panic", "Selling after a sharp drop",
                        f"{t} is down {abs(move):.1%} over the last {lookback} trading days. Has your 'what would prove me "
                        "wrong' condition actually happened, or is the drop itself the reason? Either can be valid. "
                        "Write down which.",
                        "biases", "warn"))
    if side == BUY and s.alert_loss(t) is not None:
        tips.append(tip("averaging-down", "Adding to a position past your loss limit",
                        f"{t} is past your {s.loss_limit:.0%} loss limit. Adding more is sometimes deliberate, but it is also "
                        "a classic way small mistakes grow. Compare this reason with your original one.",
                        "exit-plans", "warn"))
    if side == SELL and pos is not None and pos.avg_cost > 0:
        pl = s.view.close(t) / pos.avg_cost - 1
        opened = s.open_since.get(t)
        if opened is not None and s.view.idx - opened < 5:
            tips.append(tip("quick-flip", "A very short holding period",
                            f"You opened {t} {s.view.idx - opened} trading day(s) ago. Quick round trips pay the full "
                            "costs twice for little time in the market.", "costs"))
        if pl >= 0.20:
            tips.append(tip("disposition", "Selling a winner",
                            f"{t} is {pl:.0%} above your cost. Is this sale about the investment's prospects, or about "
                            "locking in the good feeling of a gain? (the disposition effect)", "biases"))
    if side == BUY and p["cash_after"] < 0.02 * s.account.equity():
        tips.append(tip("all-in", "Nearly fully invested",
                        "After this order you would have almost no cash left. That's a valid choice, but it leaves no room "
                        "to act on new information or to absorb costs.", "sizing"))
    return tips


def event_tips(s, reports: list, seen: set) -> list[dict]:
    """Explain notable events from the days just replayed. ``seen`` stops one-off tips repeating."""
    tips = []

    def once(tid: str) -> bool:
        if tid in seen:
            return False
        seen.add(tid)
        return True

    for rep in reports:
        st = rep.step
        d = st.date.strftime("%Y-%m-%d")
        if st.fills and once("first-fill"):
            f = st.fills[0]
            tips.append(tip("first-fill", "Your first fill",
                            f"Your {f.order.side} of {f.order.ticker} filled at ${f.price:,.2f}, while the open was "
                            f"${f.open_price:,.2f}. The difference is half the spread plus slippage, a cost you pay on "
                            "every market order.", "orders", date=d))
        for dv in st.dividends:
            if once("dividend"):
                tips.append(tip("dividend", "You received a dividend",
                                f"{dv.ticker} went ex-dividend today, so its price drops by about ${dv.per_share:.2f}, the cash "
                                f"that left the company. You received ${dv.gross:,.2f} gross, minus "
                                f"{s.costs.withholding_tax:.0%} US withholding tax = ${dv.net:,.2f}. Dividends are part of "
                                "total return, not extra money on top of it.", "dividends", date=d))
        for sp in st.splits:
            if once(f"split-{sp.ticker}"):
                tips.append(tip("split", f"{sp.ticker} split {sp.ratio:g}-for-1",
                                f"You now hold {sp.shares_after:g} shares instead of {sp.shares_before:g}, and the price "
                                "is divided by the same ratio. Your position's value did not change.", "dividends", date=d))
        for r in st.rejections:
            tips.append(tip("rejected", "An order could not fill",
                            f"Your {r.order.side} of {r.order.ticker} was not filled: {r.reason}. Prices can open "
                            "differently from the previous close, so an amount that looked enough may not be.",
                            "orders", "warn", date=d))
        for ep in rep.new_alerts:
            tips.append(tip("loss-alert", f"{ep.ticker} hit your loss limit",
                            f"{ep.ticker} is {ep.loss:.1%} below your average cost. This is the moment your earlier self "
                            "planned for. Re-read what you said would prove you wrong. Then act on it, or use "
                            "'Review' to write down why your thesis still holds. Looking away is the one response "
                            "that is never part of a plan.", "exit-plans", "alert", date=d))
    dd = s.drawdown()
    for level in (0.10, 0.20, 0.30):
        if dd >= level and once(f"drawdown-{int(level * 100)}"):
            need = 1 / (1 - dd) - 1
            tips.append(tip("drawdown", f"Your portfolio is {dd:.0%} below its peak",
                            f"Drawdowns are asymmetric: from here you need +{need:.0%} just to get back to your high. "
                            "How does this feel compared with what you expected when you started? That gap is "
                            "worth noting in your journal.", "drawdowns", "warn",
                            date=s.date.strftime("%Y-%m-%d")))
    return tips
