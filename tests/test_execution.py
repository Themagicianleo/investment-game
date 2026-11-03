import pytest

from trade_sim import execution as ex
from trade_sim.account import Account, Order
from trade_sim.execution import BUY, SELL, Costs
from trade_sim.market import MarketData

from conftest import flat_frame


# --- spread and slippage ---------------------------------------------------

def test_buy_fill_is_worse_than_open_by_half_spread_plus_slippage(costs):
    assert ex.fill_price(100.0, BUY, costs) == pytest.approx(100.0 * (1 + 0.0005 + 0.0005))
    assert ex.fill_price(100.0, BUY, costs) > 100.0


def test_sell_fill_is_worse_than_open_by_half_spread_plus_slippage(costs):
    assert ex.fill_price(100.0, SELL, costs) == pytest.approx(100.0 * (1 - 0.0005 - 0.0005))
    assert ex.fill_price(100.0, SELL, costs) < 100.0


def test_spread_and_slippage_are_configurable():
    c = Costs(half_spread=0.001, slippage=0.002)
    assert ex.fill_price(50.0, BUY, c) == pytest.approx(50.0 * 1.003)
    assert ex.fill_price(50.0, SELL, c) == pytest.approx(50.0 * 0.997)


def test_spread_slippage_cost_in_usd(costs):
    fill = ex.fill_price(200.0, BUY, costs)
    assert ex.spread_slippage_cost(10, 200.0, fill) == pytest.approx(10 * 200.0 * 0.001)


def test_invalid_side():
    with pytest.raises(ValueError):
        ex.fill_price(1.0, "short", Costs())


# --- fees, FX and rounding --------------------------------------------------

def test_flat_fee_per_order(costs):
    gross, fee, fx, total = ex.buy_total(10, 100.0, costs)
    assert fee == 0.99 and fx == 0.0  # default fx_mode is "deposit"
    assert total == pytest.approx(1000.99)
    gross, fee, net = ex.sell_total(10, 100.0, costs)
    assert net == pytest.approx(999.01)


def test_fx_per_buy_mode_charges_spread_on_each_buy():
    c = Costs(fx_mode="per_buy", fx_spread=0.0015)
    gross, fee, fx, total = ex.buy_total(10, 100.0, c)
    assert fx == pytest.approx(1.5)
    assert total == pytest.approx(1000 + 0.99 + 1.5)


def test_fx_deposit_mode_charged_once_from_starting_cash(costs):
    m = MarketData({"AAA": flat_frame()})
    acct = Account(m.view(0), costs, 10_000)
    assert acct.fx_paid == pytest.approx(15.0)
    assert acct.cash == pytest.approx(9_985.0)


def test_buy_quantity_rounds_down_to_whole_shares(costs):
    # $1000 budget, $0.99 fee -> 999.01 / 300 = 3.33 -> 3 shares
    assert ex.buy_quantity(1000, 300.0, costs) == 3.0


def test_buy_quantity_fractional_mode():
    c = Costs(fractional=True)
    q = ex.buy_quantity(1000, 300.0, c)
    assert q == pytest.approx((1000 - 0.99) / 300.0, abs=1e-6)
    assert q <= (1000 - 0.99) / 300.0


def test_buy_quantity_never_exceeds_budget():
    c = Costs(fx_mode="per_buy", fractional=True)
    q = ex.buy_quantity(500, 123.45, c)
    assert ex.buy_total(q, 123.45, c)[3] <= 500 + 1e-9


def test_budget_smaller_than_fee_buys_nothing(costs):
    assert ex.buy_quantity(0.5, 10.0, costs) == 0.0


def test_fees_are_deducted_from_cash(no_cost):
    c = Costs(half_spread=0, slippage=0, fee_per_order=0.99, fx_mode="none")
    m = MarketData({"AAA": flat_frame(price=100.0)})
    acct = Account(m.view(0), c, 10_000)
    acct.submit(Order("AAA", BUY, 1000.0, m.dates[0]))
    acct.step()
    assert acct.shares("AAA") == 9  # (1000 - 0.99) / 100 -> 9 whole shares
    assert acct.cash == pytest.approx(10_000 - 900 - 0.99)
    acct.submit(Order("AAA", SELL, "all", m.dates[1]))
    acct.step()
    assert acct.cash == pytest.approx(10_000 - 2 * 0.99)
    assert acct.fees_paid == pytest.approx(1.98)


def test_sell_amount_in_usd_rounds_down_and_caps_at_holding(no_cost):
    assert ex.sell_quantity(250, 100.0, held=10, costs=no_cost) == 2.0
    assert ex.sell_quantity(5000, 100.0, held=10, costs=no_cost) == 10.0
