import pytest

from trade_sim import execution as ex
from trade_sim.account import Account, Order
from trade_sim.execution import BUY, Costs
from trade_sim.market import MarketData

from conftest import flat_frame


def _costs(tax=0.30):
    return Costs(half_spread=0, slippage=0, fee_per_order=0, fx_mode="none", withholding_tax=tax)


def test_net_dividend_deducts_30pct_withholding():
    gross, tax, net = ex.net_dividend(0.50, 100, _costs())
    assert gross == pytest.approx(50.0)
    assert tax == pytest.approx(15.0)
    assert net == pytest.approx(35.0)


def test_withholding_rate_is_configurable():
    _, tax, net = ex.net_dividend(1.0, 10, _costs(tax=0.15))
    assert tax == pytest.approx(1.5) and net == pytest.approx(8.5)


def test_dividend_credited_on_ex_date_after_tax():
    f = flat_frame(n=10, price=100.0)
    f.iloc[4, f.columns.get_loc("Dividends")] = 1.0  # ex-date is row 4
    m = MarketData({"AAA": f})
    acct = Account(m.view(0), _costs(), 10_000)
    acct.submit(Order("AAA", BUY, 1_000.0, m.dates[0]))
    acct.step()  # day 1: buy 10 shares
    assert acct.shares("AAA") == 10
    cash_before = acct.cash
    acct.step(); acct.step()  # days 2, 3: no dividend yet
    assert acct.cash == cash_before
    result = acct.step()  # day 4: ex-date
    assert result.date == m.dates[4]
    assert len(result.dividends) == 1
    assert acct.cash == pytest.approx(cash_before + 10 * 1.0 * 0.70)
    assert acct.dividend_tax_paid == pytest.approx(3.0)


def test_buying_at_ex_date_open_does_not_receive_dividend():
    f = flat_frame(n=6, price=100.0)
    f.iloc[2, f.columns.get_loc("Dividends")] = 1.0
    m = MarketData({"AAA": f})
    acct = Account(m.view(1), _costs(), 10_000)
    acct.submit(Order("AAA", BUY, 1_000.0, m.dates[1]))
    result = acct.step()  # fills at the open of the ex-date
    assert result.fills and not result.dividends
    assert acct.dividend_tax_paid == 0


def test_split_updates_shares_and_average_cost():
    f = flat_frame(n=6, price=100.0)
    f.iloc[3:, :4] = 25.0
    f.iloc[3, f.columns.get_loc("Stock Splits")] = 4.0
    m = MarketData({"AAA": f})
    acct = Account(m.view(0), _costs(), 10_000)
    acct.submit(Order("AAA", BUY, 1_000.0, m.dates[0]))
    acct.step()
    value_before = acct.equity()
    acct.step(); res = acct.step()
    assert res.splits and acct.shares("AAA") == 40
    assert acct.positions["AAA"].avg_cost == pytest.approx(25.0)
    assert acct.equity() == pytest.approx(value_before)
