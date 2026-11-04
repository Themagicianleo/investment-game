import copy
import re

import pandas as pd
import pytest

from trade_sim.config import DEFAULTS, validate
from trade_sim.education import coach, missions
from trade_sim.education.glossary import GLOSSARY
from trade_sim.education.lessons import LESSONS, grade_quiz, public_lessons
from trade_sim.education.practice import PRACTICE_TICKERS, make_practice_market
from trade_sim.education.progress import ProgressStore, level_info
from trade_sim.education.scenarios import SCENARIOS, public_scenarios
from trade_sim.education.scoring import process_score
from trade_sim.market import MarketData
from trade_sim.session import Session

from conftest import flat_frame, make_frame


def _cfg(tickers=("AAA", "BBB", "CCC", "VT")):
    cfg = validate(copy.deepcopy(DEFAULTS))
    cfg["tickers"] = list(tickers)
    return cfg


def _session(tmp_path, frames=None, start=5, **cfg_kw):
    frames = frames or {t: make_frame(n=400, seed=i) for i, t in enumerate(["AAA", "BBB", "CCC", "VT"])}
    cfg = _cfg(tuple(frames))
    return Session(MarketData(frames), cfg, start, journal_path=tmp_path / "j.csv")


# --- content ------------------------------------------------------------------

def _all_text():
    for lesson in LESSONS:
        for block in lesson["body"]:
            for v in block.values():
                yield from (v if isinstance(v, list) else [v])
        for q in lesson["quiz"]:
            yield q["q"]
            yield q["explain"]
            yield from q["options"]
    for sc in SCENARIOS:
        yield sc["briefing"]
        yield sc["debrief"]
    yield from GLOSSARY.values()


def test_every_glossary_link_in_lessons_exists():
    for text in _all_text():
        for term in re.findall(r"\[\[(.+?)\]\]", text):
            assert term.lower() in GLOSSARY, f"missing glossary term: {term}"


def test_educational_text_never_gives_buy_or_sell_advice():
    advice = re.compile(r"\byou should (buy|sell)\b|\b(buy|sell) now\b|\bstrong buy\b|\bguaranteed (profit|return)", re.I)
    for text in _all_text():
        assert not advice.search(text), text


def test_quiz_answers_are_not_sent_to_the_browser():
    for lesson in public_lessons():
        for q in lesson["quiz"]:
            assert set(q) == {"q", "options"}


def test_every_quiz_question_is_well_formed():
    for lesson in LESSONS:
        assert len(lesson["quiz"]) >= 3
        for q in lesson["quiz"]:
            assert 0 <= q["answer"] < len(q["options"])


def test_grade_quiz_pass_threshold():
    lesson = LESSONS[0]
    right = [q["answer"] for q in lesson["quiz"]]
    assert grade_quiz(lesson["id"], right)["passed"]
    one_wrong = right[:-1] + [(right[-1] + 1) % len(lesson["quiz"][-1]["options"])]
    assert grade_quiz(lesson["id"], one_wrong)["passed"]  # n-1 of n passes
    assert not grade_quiz(lesson["id"], [-1] * len(right))["passed"]
    with pytest.raises(ValueError):
        grade_quiz(lesson["id"], [0])


def test_scenario_debrief_hidden_from_public_list():
    assert all("debrief" not in s for s in public_scenarios())


# --- coach --------------------------------------------------------------------

def test_preview_flags_fee_drag_on_small_order(tmp_path):
    s = _session(tmp_path)
    p = coach.trade_preview(s, "buy", "AAA", 150)
    assert p["fee"] == 0.99 and p["shares"] >= 1
    assert p["break_even_move"] > 0.004
    assert "fee-drag" in {t["id"] for t in p["tips"]}


def test_preview_flags_concentration(tmp_path):
    s = _session(tmp_path)
    p = coach.trade_preview(s, "buy", "AAA", 8000)
    assert p["weight_after"] > 0.4
    assert "concentration" in {t["id"] for t in p["tips"]}
    assert p["loss_if_down_20"] == pytest.approx(p["value_after"] * 0.2)


def test_preview_flags_buying_after_large_rise(tmp_path):
    f = flat_frame(n=30, price=100.0)
    f.iloc[5:, :4] = 120.0  # +20% jump
    frames = {"AAA": f, "VT": flat_frame(n=30)}
    s = Session(MarketData(frames), _cfg(("AAA", "VT")), 7, journal_path=tmp_path / "j.csv")
    tips = {t["id"] for t in coach.trade_preview(s, "buy", "AAA", 1000)["tips"]}
    assert "chasing" in tips


def test_preview_does_not_change_state(tmp_path):
    s = _session(tmp_path)
    before = (s.account.cash, list(s.account.pending), s.view.idx)
    coach.trade_preview(s, "buy", "AAA", 1000)
    assert (s.account.cash, list(s.account.pending), s.view.idx) == before


def test_event_tips_dividend_once_and_drawdown(tmp_path):
    f = flat_frame(n=40, price=100.0)
    f.iloc[3, f.columns.get_loc("Dividends")] = 1.0
    f.iloc[6, f.columns.get_loc("Dividends")] = 1.0
    f.iloc[10:, :4] = 70.0
    s = Session(MarketData({"AAA": f, "VT": flat_frame(n=40)}), _cfg(("AAA", "VT")), 0, journal_path=tmp_path / "j.csv")
    s.place("buy", "AAA", 9000, "test reason text", "if it falls a lot")
    seen = set()
    reports, _ = s.hold(12)
    tips = coach.event_tips(s, reports, seen)
    ids = [t["id"] for t in tips]
    assert ids.count("dividend") == 1 and "first-fill" in ids
    assert "drawdown" in ids and "loss-alert" in ids


# --- process score and missions -------------------------------------------------

def test_process_score_needs_trades(tmp_path):
    s = _session(tmp_path)
    assert process_score(s)["score"] is None


def test_process_score_rewards_good_habits(tmp_path):
    s = _session(tmp_path)
    for t in ("AAA", "BBB", "CCC"):
        s.place("buy", t, 3000, "a long and specific reason", "a clear exit condition")
    s.hold(3)
    good = process_score(s)
    assert good["score"] is not None and good["score"] >= 80
    s2 = _session(tmp_path)
    s2.place("buy", "AAA", 9900, "x", "y")
    s2.hold(3)
    assert process_score(s2)["score"] < good["score"]


def test_missions_diversify_plan_review(tmp_path):
    s = _session(tmp_path)
    for t in ("AAA", "BBB", "CCC"):
        s.place("buy", t, 3000, "a long and specific reason", "falls below my entry by 15%")
    s.hold(1)
    done = {m["id"] for m in missions.update(s)}
    assert {"plan-first", "diversify"} <= done
    s.review_position("AAA", "thesis still holds because earnings were fine")
    assert {m["id"] for m in missions.update(s)} == {"review"}
    assert missions.update(s) == []  # never awarded twice in a session


# --- practice market -------------------------------------------------------------

def test_practice_market_is_fictional_and_has_events():
    m = make_practice_market(seed=1)
    assert set(m.tickers) == set(PRACTICE_TICKERS)
    view = m.view(len(m) - 1)
    divs = sum((view.history(t)["Dividends"] > 0).sum() for t in m.tickers)
    assert divs > 10
    assert len(m.eligible_start_indices(250, 2)) > 100


def test_practice_market_seeded():
    a, b = make_practice_market(seed=3), make_practice_market(seed=3)
    pd.testing.assert_frame_equal(a.view(500).history("ROKT"), b.view(500).history("ROKT"))


# --- progress --------------------------------------------------------------------

def test_progress_xp_and_levels(tmp_path):
    store = ProgressStore(tmp_path / "p.json")
    passed = {"passed": True, "score": 3, "total": 3}
    assert store.record_quiz("orders", passed) == 50
    assert store.record_quiz("orders", passed) == 0  # no XP for re-passing
    assert store.record_missions(["dividend"]) == 20
    assert store.record_missions(["dividend"]) == 5   # repeat missions earn 25%
    reloaded = ProgressStore(tmp_path / "p.json").snapshot()
    assert reloaded["xp"] == 75 and reloaded["lessons_passed"] == 1
    assert level_info(0)["name"] == "Novice" and level_info(150)["level"] == 2


def test_progress_tolerates_corrupt_file(tmp_path):
    path = tmp_path / "p.json"
    path.write_text("{not json")
    assert ProgressStore(path).snapshot()["xp"] == 0
