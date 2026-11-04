"""Missions: small goals that reward good process, earning XP."""

from __future__ import annotations

from .scoring import process_score, trading_costs

MISSIONS: list[dict] = [
    {"id": "plan-first", "title": "Plan your exit", "xp": 20, "lesson": "exit-plans",
     "desc": "Make a trade with a specific 'what would prove me wrong' condition (15+ characters)."},
    {"id": "diversify", "title": "Spread your risk", "xp": 30, "lesson": "sizing",
     "desc": "End a day holding 3 or more tickers with no single stock above 40% of your portfolio."},
    {"id": "dividend", "title": "Paid to wait", "xp": 20, "lesson": "dividends",
     "desc": "Receive a dividend, and notice the withholding tax."},
    {"id": "review", "title": "Think before acting", "xp": 15, "lesson": "exit-plans",
     "desc": "Write a position review note instead of trading."},
    {"id": "alert-response", "title": "Face the alert", "xp": 40, "lesson": "exit-plans",
     "desc": "Respond to a loss-limit alert within 5 trading days by selling or writing a review."},
    {"id": "patience", "title": "Patience", "xp": 30, "lesson": "costs",
     "desc": "Hold one position for 60 trading days or more."},
    {"id": "full-year", "title": "A full year", "xp": 40, "lesson": "drawdowns",
     "desc": "Play through 252 trading days (about a year)."},
    {"id": "cost-control", "title": "Cost-conscious", "xp": 30, "lesson": "costs",
     "desc": "Finish a session with 3+ trades and trading costs under 0.5% of starting cash."},
    {"id": "disciplined", "title": "Disciplined", "xp": 50, "lesson": "process",
     "desc": "Finish a session with 3+ trades and a process score of 80 or more."},
]

MISSION_BY_ID = {m["id"]: m for m in MISSIONS}


def _achieved(s, at_end: bool) -> set[str]:
    done = set()
    filled = [e for e in s.entries if e.get("status") == "filled"]
    if any(len(e.get("prove_wrong", "").strip()) >= 15 for e in filled):
        done.add("plan-first")
    w = s.weights()
    single_stocks = [v for t, v in w.items() if t not in s.broad_funds]
    if len(w) >= 3 and all(v <= 0.40 for v in single_stocks):
        done.add("diversify")
    if s.dividends_received > 0:
        done.add("dividend")
    if any(e.get("status") == "review" for e in s.entries):
        done.add("review")
    grace = s.cfg["alerts"]["loss_limit_grace_days"]
    if any(x is not None and x <= ep.start_idx + grace for ep in s.episodes for x in (ep.sold_idx, ep.reviewed_idx)):
        done.add("alert-response")
    if s.longest_hold_days >= 60:
        done.add("patience")
    if s.view.days_elapsed() >= 252:
        done.add("full-year")
    if at_end and len(filled) >= 3:
        if trading_costs(s) / s.account.starting_cash < 0.005:
            done.add("cost-control")
        score = process_score(s)["score"]
        if score is not None and score >= 80:
            done.add("disciplined")
    return done


def update(s, at_end: bool = False) -> list[dict]:
    """Record newly completed missions on the session; return them."""
    if not hasattr(s, "missions_done"):
        s.missions_done = {}
    new = []
    for mid in sorted(_achieved(s, at_end) - set(s.missions_done)):
        s.missions_done[mid] = s.date.strftime("%Y-%m-%d")
        new.append(MISSION_BY_ID[mid])
    return new


def status(s) -> list[dict]:
    done = getattr(s, "missions_done", {})
    return [{**m, "done": m["id"] in done, "date": done.get(m["id"])} for m in MISSIONS]
