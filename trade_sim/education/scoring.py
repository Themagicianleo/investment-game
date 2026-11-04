"""Process score: how disciplined the decisions were, independent of how they turned out."""

from __future__ import annotations

from .. import execution as ex

POINTS = 20


def trading_costs(s) -> float:
    """Fees + spread/slippage + FX on trades (excluding the one-off deposit conversion)."""
    a = s.account
    deposit = ex.deposit_fx_cost(a.starting_cash, s.costs)
    return a.fees_paid + a.spread_slippage_paid + max(0.0, a.fx_paid - deposit)


def process_score(s) -> dict:
    filled = [e for e in s.entries if e.get("status") == "filled"]
    components = []
    grace = s.cfg["alerts"]["loss_limit_grace_days"]
    large = s.cfg["review"]["large_move_pct"]

    def add(key, name, frac, detail, lesson):
        components.append({
            "key": key, "name": name, "max": POINTS, "lesson": lesson, "detail": detail,
            "score": None if frac is None else round(POINTS * max(0.0, min(1.0, frac)), 1),
        })

    if filled:
        good = [e for e in filled if len(e.get("reason", "").strip()) >= 15 and len(e.get("prove_wrong", "").strip()) >= 10]
        add("journal", "Clear written reasoning", len(good) / len(filled),
            f"{len(good)} of {len(filled)} trades had a clear reason and a 'prove me wrong' condition.", "process")

        calm = [e for e in filled if e.get("prior_move_pct") in ("", None) or abs(float(e["prior_move_pct"])) < large]
        add("calm", "Not reacting to big moves", len(calm) / len(filled),
            f"{len(filled) - len(calm)} of {len(filled)} trades came right after a move of {large:g}% or more.", "biases")
    else:
        add("journal", "Clear written reasoning", None, "No trades yet.", "process")
        add("calm", "Not reacting to big moves", None, "No trades yet.", "biases")

    due = [ep for ep in s.episodes if s.view.idx >= ep.start_idx + grace or ep.sold_idx or ep.reviewed_idx]
    if due:
        ok = [ep for ep in due if any(x is not None and x <= ep.start_idx + grace
                                      for x in (ep.sold_idx, ep.reviewed_idx, ep.recovered_idx))]
        add("exits", "Responding to loss limits", len(ok) / len(due),
            f"{len(ok)} of {len(due)} loss-limit alerts got a response (sale, review note or recovery) "
            f"within {grace} days.", "exit-plans")
    else:
        add("exits", "Responding to loss limits", None, "No loss-limit alerts to respond to.", "exit-plans")

    invested = [w for w in s.max_weights if w > 0]
    if invested:
        ok_days = sum(1 for w in invested if w <= 0.40)
        add("sizing", "Position sizing", ok_days / len(invested),
            f"On {ok_days} of {len(invested)} invested days no single stock was over 40% of your portfolio "
            f"(a broad index fund such as {s.cfg['benchmark_ticker']} is already diversified, so it is not counted).", "sizing")
    else:
        add("sizing", "Position sizing", None, "You have not held a position at a close yet.", "sizing")

    if filled:
        ratio = trading_costs(s) / s.account.starting_cash
        frac = 1.0 if ratio <= 0.005 else max(0.0, 1 - (ratio - 0.005) / 0.015)
        add("costs", "Keeping costs down", frac,
            f"Trading costs were {ratio:.2%} of starting cash (full marks at 0.5% or less, none at 2% or more).", "costs")
    else:
        add("costs", "Keeping costs down", None, "No trades yet.", "costs")

    scored = [c for c in components if c["score"] is not None]
    total = round(100 * sum(c["score"] for c in scored) / (POINTS * len(scored))) if scored and filled else None
    return {"score": total, "components": components,
            "note": "Measures decision habits, not profit. A good process can lose money and a bad one can win."}
