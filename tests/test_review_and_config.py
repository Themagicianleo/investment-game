import copy

import pytest

from trade_sim.config import DEFAULTS, ConfigError, load_config, validate
from trade_sim.review import review
from trade_sim.session import AlertEpisode


def _cfg():
    return validate(copy.deepcopy(DEFAULTS))


def _entry(action="buy", move=0.0, alert=False, reason="a considered reason", wrong="clear exit condition"):
    return {"status": "filled", "action": action, "prior_move_pct": move, "loss_alert_active": alert,
            "reason": reason, "prove_wrong": wrong}


def test_review_flags_trading_after_large_move():
    entries = [_entry(move=12.0), _entry(action="sell", move=-15.0), _entry(move=2.0)]
    findings = review(entries, [], _cfg(), end_idx=100, session_days=100, fees_and_costs=3, starting_cash=10_000)
    big = [f for f in findings if f.title.startswith("Trading right after")]
    assert big and big[0].count == 2


def test_review_flags_skipped_loss_limit():
    ep = AlertEpisode("AAA", start_idx=10, start_date=None, loss=0.16)
    findings = review([], [ep], _cfg(), end_idx=50, session_days=50, fees_and_costs=0, starting_cash=10_000)
    assert any(f.title == "Skipping the loss limit" for f in findings)


def test_review_does_not_flag_acted_on_loss_limit():
    ep = AlertEpisode("AAA", start_idx=10, start_date=None, loss=0.16, sold_idx=12)
    findings = review([], [ep], _cfg(), end_idx=50, session_days=50, fees_and_costs=0, starting_cash=10_000)
    assert not any(f.title == "Skipping the loss limit" for f in findings)


def test_review_sorted_most_common_first():
    entries = [_entry(reason="x")] * 3 + [_entry(move=20.0)]
    findings = review(entries, [], _cfg(), end_idx=500, session_days=500, fees_and_costs=0, starting_cash=10_000)
    assert findings[0].count >= findings[-1].count


def test_config_defaults_and_validation(tmp_path):
    cfg = _cfg()
    assert cfg["starting_cash_usd"] == 10_000 and cfg["costs"]["fee_per_order_usd"] == 0.99
    assert cfg["fractional_shares"] is False
    bad = tmp_path / "bad.yaml"
    bad.write_text("costs:\n  fx_mode: sometimes\n")
    with pytest.raises(ConfigError, match="fx_mode"):
        load_config(bad)
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "missing.yaml")


def test_example_config_matches_defaults():
    cfg, source = load_config("config.example.yaml")
    assert source == "config.example.yaml"
    assert cfg == _cfg()
