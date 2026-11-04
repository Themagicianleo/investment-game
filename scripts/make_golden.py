"""Generate golden fixtures that the TypeScript engine (web/) must reproduce exactly.

Writes web/tests/golden/market/*.csv (input data, split-adjusted like the Yahoo cache)
and web/tests/golden/expected.json (what the Python engine does with scripted sessions).

    python scripts/make_golden.py

tests/test_golden.py fails if the Python engine's output no longer matches expected.json,
so any engine change has to be mirrored in web/src/engine and the fixtures regenerated.
"""

from __future__ import annotations

import copy
import json
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from trade_sim.config import DEFAULTS, validate  # noqa: E402
from trade_sim.data import load_market_frames  # noqa: E402
from trade_sim.education import coach, missions  # noqa: E402
from trade_sim.education.scoring import process_score  # noqa: E402
from trade_sim.market import MarketData  # noqa: E402
from trade_sim.report import compute_report  # noqa: E402
from trade_sim.session import Session, fmt_date  # noqa: E402

GOLDEN = ROOT / "web" / "tests" / "golden"
TICKERS = ["AAA", "BBB", "CCC", "VT"]


def make_frames() -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(20240501)
    frames = {}
    n = 900
    dates = pd.bdate_range("2012-01-03", periods=n)
    for i, t in enumerate(TICKERS):
        drift = [0.0004, -0.0002, 0.0008, 0.0003][i]
        vol = [0.018, 0.022, 0.03, 0.01][i]
        closes = 50 * (i + 1) * np.exp(np.cumsum(rng.normal(drift, vol, n)))
        opens = closes * (1 + rng.normal(0, 0.004, n))
        df = pd.DataFrame({
            "Open": opens, "High": np.maximum(opens, closes) * 1.01, "Low": np.minimum(opens, closes) * 0.99,
            "Close": closes, "Volume": rng.integers(100_000, 5_000_000, n).astype(float),
            "Dividends": 0.0, "Stock Splits": 0.0,
        }, index=pd.DatetimeIndex(dates, name="Date"))
        if t in ("AAA", "VT"):
            for k in range(40, n, 63):
                df.iloc[k, df.columns.get_loc("Dividends")] = round(float(closes[k]) * 0.004, 4)
        if t == "CCC":
            df.iloc[520, df.columns.get_loc("Stock Splits")] = 4.0  # split-adjusted input, like Yahoo
        frames[t] = df
    # a missing day for one ticker exercises the common-date alignment
    frames["BBB"] = frames["BBB"].drop(frames["BBB"].index[100])
    return frames


def summary(s: Session) -> dict:
    a = s.account
    return {
        "date": fmt_date(s.date), "idx": s.view.idx, "cash": a.cash, "equity": a.equity(),
        "fees": a.fees_paid, "fx": a.fx_paid, "spread": a.spread_slippage_paid,
        "div_tax": a.dividend_tax_paid, "div_net": a.dividends_net, "orders": a.order_count,
        "positions": {t: [p.shares, p.avg_cost] for t, p in sorted(a.positions.items()) if p.shares > 0},
        "max_drawdown": s.max_drawdown, "episodes": len(s.episodes), "longest_hold": s.longest_hold_days,
        "process": process_score(s)["score"],
    }


SCRIPTS = {
    "default": ({}, [
        ("preview", "buy", "AAA", 3000), ("buy", "AAA", 3000, "core position for the test run", "falls 15% below cost"),
        ("buy", "BBB", 2500, "second holding to diversify", "closes 15% under my cost"),
        ("buy", "VT", 2000, "broad fund", "x"),
        ("hold", 1), ("hold", 30), ("review", "BBB", "thesis still fine after a month"),
        ("sell", "BBB", 1000, "trim", "it recovers fully"), ("hold", 5),
        ("preview", "sell", "AAA", "all"), ("sell", "AAA", "all", "taking it off", "n/a here"),
        ("hold", 120), ("preview", "buy", "CCC", 150), ("buy", "CCC", 4000, "y", "z"), ("hold", 200),
        ("buy", "AAA", 1500, "adding again after a while", "drops 15% from here"), ("hold", 150),
    ]),
    "fractional_per_buy": ({"fractional_shares": True, "costs": {"fx_mode": "per_buy"}, "alerts": {"loss_limit_pct": 5.0}}, [
        ("buy", "CCC", 6000, "concentrated test position", "loses 5% from cost"),
        ("buy", "AAA", 1234.56, "fractional buy", "falls 5% below cost"), ("hold", 60),
        ("sell", "CCC", 1500.5, "partial sale", "n/a"), ("hold", 300),
        ("buy", "VT", 500, "small", "x"), ("hold", 100), ("sell", "AAA", "all", "exit", "n/a"), ("hold", 10),
    ]),
}


def run_script(market: MarketData, overrides: dict, steps: list, journal: Path) -> dict:
    cfg = validate(copy.deepcopy(DEFAULTS))
    cfg["tickers"] = ["AAA", "BBB", "CCC", "VT"]
    for k, v in overrides.items():
        if isinstance(v, dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    s = Session(market, cfg, 300, journal_path=journal, session_id="golden")
    seen: set[str] = set()
    out = {"overrides": overrides, "start_idx": 300, "start": summary(s), "steps": []}
    for step in steps:
        kind = step[0]
        rec: dict = {"step": list(step)}
        if kind == "preview":
            p = coach.trade_preview(s, step[1], step[2], step[3])
            rec["preview"] = {k: p[k] for k in ("shares", "est_fill", "gross", "fee", "fx", "spread_slippage", "costs",
                                                "cost_pct", "break_even_move", "weight_after", "cash_after", "prior_move")}
            rec["tips"] = [t["id"] for t in p["tips"]]
        elif kind in ("buy", "sell"):
            s.place(kind, step[1], step[2], step[3], step[4])
        elif kind == "review":
            s.review_position(step[1], step[2])
            rec["missions"] = [m["id"] for m in missions.update(s)]
        elif kind == "hold":
            # keep holding through loss-limit stops until the full number of days has passed
            reports, stops = [], []
            while len(reports) < step[1] and s.view.has_next():
                more, stopped = s.hold(step[1] - len(reports))
                reports += more
                if stopped:
                    stops.append(stopped)
            rec["advanced"] = len(reports)
            rec["stopped"] = stops
            rec["tips"] = [t["id"] for t in coach.event_tips(s, reports, seen)]
            rec["missions"] = [m["id"] for m in missions.update(s)]
            rec["fills"] = [[f.order.ticker, f.order.side, f.shares, f.price] for r in reports for f in r.step.fills]
            rec["dividends"] = [[d.ticker, d.net] for r in reports for d in r.step.dividends]
            rec["splits"] = [[x.ticker, x.ratio, x.shares_after] for r in reports for x in r.step.splits]
            rec["alerts"] = [[e.ticker, fmt_date(e.start_date)] for r in reports for e in r.new_alerts]
            rec["summary"] = summary(s)
        out["steps"].append(rec)
    out["end_missions"] = [m["id"] for m in missions.update(s, at_end=True)]
    out["entries"] = s.entries
    out["report"] = compute_report(s)
    return out


def build() -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        market = MarketData(load_market_frames(TICKERS, GOLDEN / "market"))
        return {name: run_script(market, ov, steps, Path(tmp) / f"{name}.csv") for name, (ov, steps) in SCRIPTS.items()}


def write_market() -> None:
    (GOLDEN / "market").mkdir(parents=True, exist_ok=True)
    for t, df in make_frames().items():
        df.to_csv(GOLDEN / "market" / f"{t}.csv", index_label="Date")


def main() -> None:
    write_market()
    expected = build()
    (GOLDEN / "expected.json").write_text(json.dumps(expected, indent=1, default=str) + "\n")
    print(f"wrote {GOLDEN / 'expected.json'}")


if __name__ == "__main__":
    main()
