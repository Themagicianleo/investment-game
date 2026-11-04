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
    cfg["data"]["progress_path"] = str(tmp_path / "progress.json")
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

    def loader(_tickers):
        raise NetworkError("Could not download VT: connection refused")

    client = create_app(cfg, market_loader=loader, progress_path=None).test_client()
    r = client.post("/api/sessions", json={})
    assert r.status_code == 503
    assert "Network error" in r.get_json()["error"] and "VT" in r.get_json()["error"]


# --- education features ---------------------------------------------------------

def test_education_endpoint_hides_answers_and_debriefs(setup):
    client, _ = setup
    data = client.get("/api/education").get_json()
    assert data["lessons"] and data["glossary"] and data["missions"]
    assert all("answer" not in q for l in data["lessons"] for q in l["quiz"])
    assert all("debrief" not in s for s in data["scenarios"])


def test_quiz_awards_xp_once(setup):
    client, _ = setup
    from trade_sim.education.lessons import LESSONS
    lesson = LESSONS[1]
    answers = [q["answer"] for q in lesson["quiz"]]
    r1 = client.post(f"/api/lessons/{lesson['id']}/quiz", json={"answers": answers}).get_json()
    r2 = client.post(f"/api/lessons/{lesson['id']}/quiz", json={"answers": answers}).get_json()
    assert r1["passed"] and r1["xp_gained"] == 50 and r2["xp_gained"] == 0
    assert all("explain" in r for r in r1["results"])
    assert client.post("/api/lessons/nope/quiz", json={"answers": []}).status_code == 404


def test_practice_mode_works_without_real_data():
    cfg = validate(copy.deepcopy(DEFAULTS))

    def offline(_tickers):
        raise NetworkError("offline")

    client = create_app(cfg, market_loader=offline, progress_path=None).test_client()
    assert client.post("/api/sessions", json={"mode": "random"}).status_code == 503
    r = client.post("/api/sessions", json={"mode": "practice", "seed": 2})
    assert r.status_code == 201
    st = r.get_json()
    assert st["mode"] == "practice" and "GLOBX" in st["rules"]["tickers"]
    assert st["market"][0]["description"].startswith("Fictional")


def test_scenario_mode_starts_on_scenario_date_and_caps_length(tmp_path):
    from trade_sim.education.scenarios import SCENARIO_BY_ID
    sc = SCENARIO_BY_ID["dotcom"]
    cfg = validate(copy.deepcopy(DEFAULTS))
    cfg["data"]["journal_path"] = str(tmp_path / "j.csv")
    tickers = sc["tickers"]
    market = MarketData({t: make_frame(n=1500, seed=i, start="1998-01-02") for i, t in enumerate(tickers)})
    client = create_app(cfg, market=market, progress_path=None).test_client()
    st = client.post("/api/sessions", json={"mode": "scenario", "scenario": "dotcom"}).get_json()
    assert st["date"] == sc["start"] and st["scenario"]["briefing"]
    assert "debrief" not in st["scenario"]
    st = client.post(f"/api/sessions/{st['id']}/hold", json={"days": 1000}).get_json()
    assert st["day"] == sc["days"] and not st["has_next"]
    rep = client.post(f"/api/sessions/{st['id']}/end").get_json()["report"]
    assert rep["scenario"]["debrief"] and rep["benchmark"]["name"] == "Buy & hold SPY"


def test_preview_and_review_endpoints(setup):
    client, _ = setup
    sid = _start(client)["id"]
    p = client.post(f"/api/sessions/{sid}/preview", json={"side": "buy", "ticker": "AAA", "amount": 9000}).get_json()
    assert p["shares"] > 0 and any(t["id"] == "concentration" for t in p["tips"])
    assert client.get(f"/api/sessions/{sid}").get_json()["pending"] == []  # preview places nothing
    _order(client, sid, amount=3000, prove_wrong="drops below my entry by 15%")
    st = client.post(f"/api/sessions/{sid}/hold", json={"days": 1}).get_json()
    assert any(m["id"] == "plan-first" for m in st["new_missions"])
    r = client.post(f"/api/sessions/{sid}/review", json={"ticker": "AAA", "note": "still fine, no change in thesis"})
    assert r.status_code == 201
    st = r.get_json()
    assert st["journal"][-1]["status"] == "review"
    assert next(m for m in st["missions"] if m["id"] == "review")["done"]
    assert client.post(f"/api/sessions/{sid}/review", json={"ticker": "BBB", "note": "x"}).status_code == 400


def test_history_moving_averages_use_only_past_data(setup):
    client, market = setup
    st = _start(client)
    sid = st["id"]
    client.post(f"/api/sessions/{sid}/hold", json={"days": 30})
    h = client.get(f"/api/sessions/{sid}/history/AAA?days=60").get_json()
    idx = market.dates.get_loc(pd.Timestamp(h["as_of"]))
    closes = market.view(idx).adjusted_closes("AAA")
    assert h["ma50"][-1] == pytest.approx(closes[-50:].mean(), rel=1e-6)
    assert h["ma200"][-1] == pytest.approx(closes[-200:].mean(), rel=1e-6)
    assert len(h["ma50"]) == len(h["dates"]) == 60


def test_end_report_has_process_xp_and_recommendations(setup):
    client, _ = setup
    sid = _start(client)["id"]
    _order(client, sid, reason="x", prove_wrong="y", amount=9000)
    client.post(f"/api/sessions/{sid}/hold", json={"days": 60})
    rep = client.post(f"/api/sessions/{sid}/end").get_json()["report"]
    assert rep["process"]["score"] is not None
    assert rep["recommended_lessons"]
    assert rep["xp"]["gained_now"] >= 25
    assert "max_drawdown" in rep["user"] and "max_drawdown" in rep["benchmark"]
    prog = client.get("/api/progress").get_json()
    assert prog["sessions"][-1]["process_score"] == rep["process"]["score"]


def test_network_failure_is_remembered_briefly():
    cfg = validate(copy.deepcopy(DEFAULTS))
    calls = []

    def offline(tickers):
        calls.append(tickers)
        raise NetworkError("offline")

    client = create_app(cfg, market_loader=offline, progress_path=None).test_client()
    assert client.post("/api/sessions", json={"mode": "random"}).status_code == 503
    assert client.post("/api/sessions", json={"mode": "random"}).status_code == 503
    assert len(calls) == 1  # second attempt fails fast without re-downloading
