"""Web API tests, including that the browser never receives future data."""

import copy
import json

import pandas as pd
import pytest

from trade_sim.config import DEFAULTS, validate
from trade_sim.data import NetworkError
from trade_sim.market import MarketData
from trade_sim.web import create_app

from conftest import make_frame


@pytest.fixture
def setup(tmp_path):
    cfg = validate(copy.deepcopy(DEFAULTS))
    cfg["tickers"] = ["AAA", "BBB", "VT"]
    cfg["data"]["journal_path"] = str(tmp_path / "journal.csv")
    market = MarketData({t: make_frame(n=1000, seed=i) for i, t in enumerate(cfg["tickers"])})
    # a 1000-day market has ~4 years; require only 2 years after the start
    app = create_app(cfg, market=market)
    return app.test_client(), market


def _start(client, seed=1):
    r = client.post("/api/sessions", json={"seed": seed})
    assert r.status_code == 201, r.get_json()
    return r.get_json()


def _order(client, sid, **kw):
    body = {"side": "buy", "ticker": "AAA", "amount": 1000, "reason": "practice trade", "prove_wrong": "falls 10%"}
    body.update(kw)
    return client.post(f"/api/sessions/{sid}/orders", json=body)


def test_index_and_static_served(setup):
    client, _ = setup
    assert b"trade-sim" in client.get("/").data
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/style.css").status_code == 200


def test_new_session_same_seed_same_start(setup):
    client, _ = setup
    assert _start(client, 5)["date"] == _start(client, 5)["date"]


def test_api_never_returns_dates_after_today(setup):
    client, market = setup
    st = _start(client)
    sid, today = st["id"], pd.Timestamp(st["date"])
    for _ in range(3):
        st = client.post(f"/api/sessions/{sid}/hold", json={"days": 7}).get_json()
        today = pd.Timestamp(st["date"])
        hist = client.get(f"/api/sessions/{sid}/history/AAA").get_json()
        assert pd.Timestamp(hist["dates"][-1]) == today
        assert all(pd.Timestamp(d) <= today for d in hist["dates"])
        # nothing in the full state payload mentions a later trading date
        blob = json.dumps(st)
        later = market.dates[market.dates > today][:30]
        assert not any(d.strftime("%Y-%m-%d") in blob for d in later)


def test_order_requires_reason_and_prove_wrong(setup):
    client, _ = setup
    sid = _start(client)["id"]
    r = _order(client, sid, reason="  ")
    assert r.status_code == 400 and "reason" in r.get_json()["error"]
    r = _order(client, sid, prove_wrong="")
    assert r.status_code == 400 and "prove you wrong" in r.get_json()["error"]


def test_order_validation_messages(setup):
    client, _ = setup
    sid = _start(client)["id"]
    assert "Unknown ticker" in _order(client, sid, ticker="ZZZ").get_json()["error"]
    assert "Not enough cash" in _order(client, sid, amount=50_000).get_json()["error"]
    assert "positive" in _order(client, sid, amount=-5).get_json()["error"]
    assert "hold no" in _order(client, sid, side="sell", amount="all").get_json()["error"]


def test_buy_fills_next_open_and_is_journaled(setup):
    client, market = setup
    st = _start(client)
    sid = st["id"]
    idx = market.dates.get_loc(pd.Timestamp(st["date"]))
    st = _order(client, sid, amount="1,500").get_json()
    assert st["pending"][0]["amount"] == 1500
    st = client.post(f"/api/sessions/{sid}/hold", json={"days": 1}).get_json()
    assert st["advanced"] == 1 and not st["pending"]
    fill = [e for e in st["new_events"] if e["type"] == "fill"]
    assert fill
    entry = st["journal"][0]
    assert entry["fill_date"] == market.dates[idx + 1].strftime("%Y-%m-%d")
    assert entry["fill_price"] > market.view(idx + 1).open("AAA")
    assert entry["reason"] == "practice trade"
    pos = st["positions"][0]
    assert pos["ticker"] == "AAA" and pos["shares"] == int(pos["shares"])


def test_end_returns_report_and_locks_session(setup):
    client, _ = setup
    sid = _start(client)["id"]
    _order(client, sid)
    client.post(f"/api/sessions/{sid}/hold", json={"days": 120})
    r = client.post(f"/api/sessions/{sid}/end").get_json()
    rep = r["report"]
    assert rep["trend_bot"]["name"].startswith("Trend bot")
    assert rep["benchmark"]["name"] == "Buy & hold VT"
    assert len(rep["tickers"]) == 3
    assert rep["walk_forward"]["train"]["days"] > rep["walk_forward"]["test"]["days"]
    assert any("Past performance" in n for n in rep["notices"])
    assert any("not a recommendation" in n for n in rep["notices"])
    assert rep["end"] == r["state"]["date"]
    assert _order(client, sid).status_code == 409
    assert client.post(f"/api/sessions/{sid}/hold", json={}).status_code == 409
    assert client.get(f"/api/sessions/{sid}/report").status_code == 200


def test_unknown_session_and_bad_input(setup):
    client, _ = setup
    assert client.get("/api/sessions/nope").status_code == 404
    sid = _start(client)["id"]
    assert client.post(f"/api/sessions/{sid}/hold", json={"days": 0}).status_code == 400
    assert client.post("/api/sessions", json={"seed": "abc"}).status_code == 400


def test_network_failure_reported_to_browser():
    cfg = validate(copy.deepcopy(DEFAULTS))

    def loader(_cfg):
        raise NetworkError("Could not download VT: connection refused")

    client = create_app(cfg, market_loader=loader).test_client()
    r = client.post("/api/sessions", json={})
    assert r.status_code == 503
    assert "Network error" in r.get_json()["error"] and "VT" in r.get_json()["error"]
