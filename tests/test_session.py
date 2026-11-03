import copy
import csv

import pandas as pd
import pytest

from trade_sim import execution as ex
from trade_sim.cli import handle, run_repl
from trade_sim.config import DEFAULTS, validate
from trade_sim.market import MarketData
from trade_sim.session import Session, SessionError

from conftest import flat_frame, make_frame


def _cfg(**overrides):
    cfg = validate(copy.deepcopy(DEFAULTS))
    cfg["tickers"] = ["AAA", "VT"]
    cfg["costs"].update(half_spread_pct=0, slippage_pct=0, fx_mode="none")
    for k, v in overrides.items():
        cfg[k] = v
    return cfg


def _falling_market(n=30, start=100.0, step=-0.01):
    f = flat_frame(n=n, price=start)
    prices = [start * (1 + step) ** i for i in range(n)]
    for col in ("Open", "High", "Low", "Close"):
        f[col] = prices
    return MarketData({"AAA": f, "VT": flat_frame(n=n)})


# --- loss-limit alert ---------------------------------------------------------

def test_loss_limit_threshold():
    assert not ex.loss_limit_breached(85.1, 100.0, 0.15)
    assert ex.loss_limit_breached(85.0, 100.0, 0.15)
    assert ex.loss_limit_breached(70.0, 100.0, 0.15)


def test_loss_limit_alert_fires_once_and_never_auto_sells(tmp_path):
    m = _falling_market()
    s = Session(m, _cfg(), 0, journal_path=tmp_path / "j.csv")
    s.place("buy", "AAA", 1_000, "test reason here", "price falls a lot")
    s.advance()
    shares = s.account.shares("AAA")
    avg = s.account.positions["AAA"].avg_cost
    fired = []
    while s.view.has_next():
        rep = s.advance()
        fired.extend((s.view.idx, ep.ticker) for ep in rep.new_alerts)
    assert len(fired) == 1
    alert_idx = fired[0][0]
    # first day the close is >= 15% below average cost
    assert m.view(alert_idx).close("AAA") <= avg * 0.85
    assert m.view(alert_idx - 1).close("AAA") > avg * 0.85
    assert s.account.shares("AAA") == shares  # nothing sold automatically


def test_loss_limit_is_configurable(tmp_path):
    cfg = _cfg()
    cfg["alerts"]["loss_limit_pct"] = 5.0
    s = Session(_falling_market(), cfg, 0, journal_path=tmp_path / "j.csv")
    s.place("buy", "AAA", 1_000, "test reason here", "price falls a lot")
    days = 0
    while not s.episodes:
        s.advance()
        days += 1
    assert days <= 7  # ~1% fall per day hits a 5% limit within a week


# --- orders & journal ---------------------------------------------------------

def test_journal_records_date_fill_price_fees_and_reason(tmp_path):
    m = MarketData({"AAA": make_frame(n=50, seed=1), "VT": make_frame(n=50, seed=2)})
    path = tmp_path / "journal.csv"
    s = Session(m, _cfg(), 5, journal_path=path)
    s.place("buy", "AAA", 2_000, "testing the journal", "if it drops 10%")
    s.advance()
    s.place("sell", "AAA", "all", "done testing now", "n/a for test")
    s.advance()
    rows = list(csv.DictReader(path.open()))
    assert [r["action"] for r in rows] == ["buy", "sell"]
    assert rows[0]["fill_date"] == m.dates[6].strftime("%Y-%m-%d")
    assert float(rows[0]["fill_price"]) == pytest.approx(m.view(6).open("AAA"), rel=1e-6)
    assert float(rows[0]["fee_usd"]) == 0.99
    assert rows[0]["reason"] == "testing the journal"
    assert rows[0]["prove_wrong"] == "if it drops 10%"


def test_buy_validation_errors(tmp_path):
    m = MarketData({"AAA": make_frame(n=50), "VT": make_frame(n=50, seed=2)})
    s = Session(m, _cfg(), 5, journal_path=tmp_path / "j.csv")
    with pytest.raises(SessionError, match="Unknown ticker"):
        s.validate_buy("ZZZ", 100)
    with pytest.raises(SessionError, match="Not enough cash"):
        s.validate_buy("AAA", 20_000)
    s.place("buy", "AAA", 9_000, "reserve most cash", "x")
    with pytest.raises(SessionError, match="Not enough cash"):
        s.validate_buy("AAA", 2_000)  # pending orders reserve cash
    with pytest.raises(SessionError, match="hold no"):
        s.validate_sell("VT", "all")


def test_repl_asks_for_reason_and_prove_wrong(tmp_path):
    m = MarketData({"AAA": make_frame(n=50), "VT": make_frame(n=50, seed=2)})
    s = Session(m, _cfg(), 5, journal_path=tmp_path / "j.csv")
    answers = iter(["", "my reason for buying", "earnings miss"])
    prompts = []

    def ask(prompt):
        prompts.append(prompt)
        return next(answers)

    handle(s, "buy aaa $1,500", ask, lambda *_: None)
    assert any("Reason" in p for p in prompts) and any("prove me wrong" in p for p in prompts)
    order = s.account.pending[0]
    assert (order.ticker, order.amount, order.reason, order.prove_wrong) == ("AAA", 1500.0, "my reason for buying", "earnings miss")


def test_full_repl_session_produces_report(tmp_path):
    frames = {t: make_frame(n=900, seed=i) for i, t in enumerate(["AAA", "VT"])}
    m = MarketData(frames)
    cfg = _cfg()
    s = Session(m, cfg, 300, journal_path=tmp_path / "j.csv")
    script = iter([
        "status", "buy AAA 3000", "chasing it", "", "x", "hold", "hold 5", "journal",
        "history AAA 3", "sell AAA all", "taking profit for test", "if it keeps rising", "hold 100", "end",
    ])
    out = []
    report = run_repl(s, ask=lambda _p: next(script), out=out.append)
    text = "\n".join(map(str, out))
    assert "FILLED BUY" in text and "FILLED SELL" in text
    assert "Educational only" in report
    assert "Past performance does not predict future results" in report
    assert "trend bot is a comparison, not a recommendation" in report
    assert "Buy & hold VT" in report and "Trend bot" in report
    assert "Walk-forward" in report and "out-of-sample" in report
    # the report never covers dates after the last replayed day
    assert m.dates[s.view.idx].strftime("%Y-%m-%d") in report
    later = m.dates[s.view.idx + 1].strftime("%Y-%m-%d")
    assert later not in report
