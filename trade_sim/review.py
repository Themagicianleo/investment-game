"""Look for common decision-making patterns in the session journal.

These are observations to reflect on, not instructions: the review never says
whether a trade was "right", only which habits showed up how often.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Finding:
    title: str
    count: int
    detail: str


def _is_vague(text: str, min_len: int) -> bool:
    return len((text or "").strip()) < min_len


def review(entries: list[dict], episodes: list, cfg: dict, end_idx: int, session_days: int,
           fees_and_costs: float, starting_cash: float) -> list[Finding]:
    filled = [e for e in entries if e.get("status") == "filled"]
    large = cfg["review"]["large_move_pct"]
    lookback = cfg["review"]["large_move_lookback_days"]
    grace = cfg["alerts"]["loss_limit_grace_days"]
    findings: list[Finding] = []

    def move(e):
        v = e.get("prior_move_pct")
        return None if v in ("", None) else float(v)

    chased = [e for e in filled if e["action"] == "buy" and (move(e) or 0) >= large]
    dumped = [e for e in filled if e["action"] == "sell" and (move(e) or 0) <= -large]
    other_big = [e for e in filled if e not in chased and e not in dumped and abs(move(e) or 0) >= large]
    n_big = len(chased) + len(dumped) + len(other_big)
    if n_big:
        findings.append(Finding(
            "Trading right after a large move", n_big,
            f"{len(chased)} buy(s) after a rise of {large:g}%+ and {len(dumped)} sell(s) after a fall of "
            f"{large:g}%+ over the previous {lookback} trading days ({len(other_big)} other). Big recent moves "
            "can pull attention and urgency; check whether your written reason still holds without them.",
        ))

    skipped = []
    for ep in episodes:
        deadline = ep.start_idx + grace
        acted = ep.sold_idx is not None and ep.sold_idx <= deadline
        recovered = ep.recovered_idx is not None and ep.recovered_idx <= deadline
        if not acted and not recovered and end_idx >= deadline:
            skipped.append(ep)
    if skipped:
        names = ", ".join(sorted({ep.ticker for ep in skipped}))
        findings.append(Finding(
            "Skipping the loss limit", len(skipped),
            f"A loss-limit alert fired and the position was neither reduced nor recovered within {grace} "
            f"trading days ({names}). Holding can be a deliberate choice; the question is whether your "
            "'what would prove me wrong' note had already come true.",
        ))

    averaging_down = [e for e in filled if e["action"] == "buy" and str(e.get("loss_alert_active")) == "True"]
    if averaging_down:
        findings.append(Finding(
            "Adding to a position that was past the loss limit", len(averaging_down),
            "You bought more of a holding while its loss-limit alert was active. Compare the new reason with "
            "the original one for that position.",
        ))

    vague = [e for e in filled if _is_vague(e.get("reason", ""), 15) or _is_vague(e.get("prove_wrong", ""), 10)]
    if vague:
        findings.append(Finding(
            "Short or missing journal reasoning", len(vague),
            "Some entries had a very short reason or no clear 'what would prove me wrong' condition, which "
            "makes it hard to review the decision later.",
        ))

    months = max(session_days / 21.0, 1.0)
    per_month = len(filled) / months
    cost_share = fees_and_costs / starting_cash
    if len(filled) >= 6 and (per_month > 4 or cost_share > 0.01):
        findings.append(Finding(
            "Frequent trading", len(filled),
            f"{len(filled)} trades in about {months:.1f} month(s) ({per_month:.1f}/month); trading costs were "
            f"{cost_share:.2%} of starting cash.",
        ))

    findings.sort(key=lambda f: f.count, reverse=True)
    return findings
