import pytest

from trade_sim.bots import TrendRule, run_buy_and_hold, run_trend_bot
from trade_sim.config import DEFAULTS
from trade_sim.execution import Costs
from trade_sim.market import MarketData
from trade_sim.walkforward import candidate_rules, run_walk_forward, select_rule, walk_forward_split

from conftest import make_frame


def test_split_is_70_30_consecutive_and_non_overlapping():
    s = walk_forward_split(100, 199, 0.7)
    assert (s.train_start, s.train_end, s.test_start, s.test_end) == (100, 169, 170, 199)
    assert s.train_len == 70 and s.test_len == 30
    assert s.train_end < s.test_start  # all training data comes before all test data


def test_split_covers_every_day_exactly_once():
    s = walk_forward_split(0, 532, 0.7)
    days = list(range(s.train_start, s.train_end + 1)) + list(range(s.test_start, s.test_end + 1))
    assert days == list(range(0, 533))


def test_split_rejects_too_short_periods():
    with pytest.raises(ValueError, match="too short"):
        walk_forward_split(0, 30, 0.7)
    with pytest.raises(ValueError):
        walk_forward_split(0, 500, 1.0)


def test_candidate_rules_only_short_below_long():
    rules = candidate_rules([20, 50, 100], [50, 100, 200])
    assert all(r.short_ma < r.long_ma for r in rules)
    assert TrendRule(50, 50) not in rules and TrendRule(20, 50) in rules


def test_rule_selection_uses_only_training_data():
    """Corrupting the test block must not change which rule is chosen."""
    base = make_frame(n=1200, seed=4)
    split = walk_forward_split(260, 1199, 0.7)
    corrupted = base.copy()
    corrupted.iloc[split.test_start:, :4] *= 3.0
    rules = candidate_rules([20, 50], [100, 200])
    picks = [select_rule(MarketData({"AAA": f}), split, rules, Costs(), 10_000)[0] for f in (base, corrupted)]
    assert picks[0] == picks[1]


def test_walk_forward_reports_both_periods():
    m = MarketData({"AAA": make_frame(n=1200, seed=5), "BBB": make_frame(n=1200, seed=6)})
    res = run_walk_forward(m, 260, 1199, DEFAULTS, Costs(), 10_000)
    assert res.in_sample.end == m.dates[res.split.train_end]
    assert res.out_of_sample.start == m.dates[res.split.test_start]
    assert res.in_sample.total_return == pytest.approx(max(r for _, r in res.table))


def test_trend_bot_pays_same_costs():
    m = MarketData({"AAA": make_frame(n=900, seed=8)})
    free = Costs(half_spread=0, slippage=0, fee_per_order=0, fx_mode="none", withholding_tax=0)
    with_costs = run_trend_bot(m, 260, 899, TrendRule(20, 100), Costs(), 10_000)
    without = run_trend_bot(m, 260, 899, TrendRule(20, 100), free, 10_000)
    assert with_costs.trades > 0
    assert with_costs.fees == pytest.approx(0.99 * with_costs.trades)
    assert with_costs.final_value < without.final_value


def test_buy_and_hold_fills_next_open():
    m = MarketData({"VT": make_frame(n=300, seed=3)})
    res = run_buy_and_hold(m, "VT", 10, 299, Costs(fractional=True, fx_mode="none"), 10_000)
    assert res.trades == 1 and res.end == m.dates[299]
    first_open = m.view(11).open("VT")
    last_close = m.view(299).close("VT")
    expected = (10_000 - 0.99) / (first_open * 1.001) * last_close
    assert res.final_value == pytest.approx(expected, rel=1e-5)
