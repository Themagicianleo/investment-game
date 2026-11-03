"""The replay must never expose prices or events after the current day."""

import numpy as np
import pandas as pd
import pytest

from trade_sim.account import Account, Order
from trade_sim.execution import BUY, Costs, fill_price
from trade_sim.market import EndOfData, MarketData

from conftest import make_frame


def _perturbed(frame: pd.DataFrame, after: int) -> pd.DataFrame:
    """Copy of ``frame`` with every row after position ``after`` replaced by garbage."""
    f = frame.copy()
    f.iloc[after + 1:, :4] = f.iloc[after + 1:, :4] * 7.3 + 11
    f.iloc[after + 1:, f.columns.get_loc("Dividends")] = 5.0
    f.iloc[after + 1:, f.columns.get_loc("Stock Splits")] = 4.0
    return f


def test_view_only_returns_rows_up_to_today(market):
    view = market.view(300)
    for t in market.tickers:
        hist = view.history(t)
        assert hist.index[-1] == market.dates[300]
        assert len(hist) == 301
        assert (hist.index <= view.date).all()
        assert len(view.adjusted_closes(t)) == 301


def test_future_data_changes_are_invisible_to_the_view():
    """Change every row after day t: nothing the view shows on day t may change."""
    t = 400
    base = make_frame(seed=7)
    real = MarketData({"AAA": base})
    fake = MarketData({"AAA": _perturbed(base, t)})
    v1, v2 = real.view(t), fake.view(t)
    pd.testing.assert_frame_equal(v1.history("AAA"), v2.history("AAA"))
    assert v1.today("AAA") == v2.today("AAA")
    np.testing.assert_array_equal(v1.adjusted_closes("AAA"), v2.adjusted_closes("AAA"))
    assert v1.moving_average("AAA", 200) == v2.moving_average("AAA", 200)
    assert v1.trailing_return("AAA", 5) == v2.trailing_return("AAA", 5)


def test_next_open_is_not_visible_until_advance(market):
    view = market.view(100)
    tomorrow_open = market.view(101).open("AAA")
    seen = view.history("AAA").to_numpy().ravel()
    assert tomorrow_open not in seen
    assert view.today("AAA")["Open"] != tomorrow_open
    view.advance()
    assert view.open("AAA") == tomorrow_open


def test_market_order_fills_at_next_day_open_not_today(market):
    costs = Costs()
    view = market.view(100)
    acct = Account(view, costs, 10_000)
    today_open = view.open("AAA")
    acct.submit(Order("AAA", BUY, 1_000.0, view.date))
    assert acct.shares("AAA") == 0  # nothing fills on the day the order is placed
    result = acct.step()
    next_open = market.view(101).open("AAA")
    fill = result.fills[0]
    assert fill.date == market.dates[101]
    assert fill.open_price == next_open != today_open
    assert fill.price == pytest.approx(fill_price(next_open, BUY, costs))
    assert fill.price > next_open


def test_fill_does_not_depend_on_data_after_fill_day():
    t = 300
    base = make_frame(seed=9)
    fills = []
    for frame in (base, _perturbed(base, t + 1)):
        m = MarketData({"AAA": frame})
        acct = Account(m.view(t), Costs(), 10_000)
        acct.submit(Order("AAA", BUY, 2_000.0, m.dates[t]))
        fills.append(acct.step().fills[0])
    assert fills[0].price == fills[1].price
    assert fills[0].shares == fills[1].shares


def test_cannot_advance_past_session_end(market):
    view = market.view(10, end_idx=12)
    view.advance()
    view.advance()
    assert not view.has_next()
    with pytest.raises(EndOfData):
        view.advance()


def test_view_does_not_expose_full_dataset_publicly(market):
    view = market.view(50)
    public = [name for name in dir(view) if not name.startswith("_")]
    assert "market" not in public and "_market" not in dir(view)


def test_trend_bot_signals_ignore_future_data():
    from trade_sim.bots import TrendRule, run_trend_bot

    t_end = 500
    base = make_frame(seed=11, n=900)
    costs = Costs()
    results = []
    for frame in (base, _perturbed(base, t_end)):
        m = MarketData({"AAA": frame})
        results.append(run_trend_bot(m, 260, t_end, TrendRule(50, 200), costs, 10_000))
    assert results[0].final_value == pytest.approx(results[1].final_value)
    assert results[0].trades == results[1].trades
