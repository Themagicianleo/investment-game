"""Load and validate config.yaml (falling back to config.example.yaml / built-in defaults)."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

DEFAULTS: dict[str, Any] = {
    "tickers": ["VT", "AAPL", "NVDA", "INTC", "AMD"],
    "benchmark_ticker": "VT",
    "starting_cash_usd": 10000.0,
    "fractional_shares": False,
    "costs": {
        "half_spread_pct": 0.05,
        "slippage_pct": 0.05,
        "fee_per_order_usd": 0.99,
        "fx_spread_pct": 0.15,
        "fx_mode": "deposit",
    },
    "dividends": {"withholding_tax_pct": 30.0},
    "alerts": {"loss_limit_pct": 15.0, "loss_limit_grace_days": 5},
    "session": {"min_years_after_start": 2, "seed": None},
    "data": {"cache_dir": "data_cache", "journal_path": "journal.csv", "progress_path": "progress.json"},
    "trend_bot": {
        "enabled": True,
        "short_ma": 50,
        "long_ma": 200,
        "walk_forward": {
            "enabled": True,
            "train_fraction": 0.7,
            "short_grid": [20, 50, 100],
            "long_grid": [100, 150, 200, 250],
        },
    },
    "review": {"large_move_pct": 10.0, "large_move_lookback_days": 5},
}

FX_MODES = ("deposit", "per_buy", "none")


class ConfigError(Exception):
    """Raised when the configuration file is missing values or has invalid ones."""


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _pct(cfg: dict, section: str, key: str, upper: float = 100.0) -> float:
    try:
        value = float(cfg[section][key])
    except (TypeError, ValueError):
        raise ConfigError(f"{section}.{key} must be a number (got {cfg[section][key]!r})")
    if not 0 <= value < upper:
        raise ConfigError(f"{section}.{key} must be between 0 and {upper} (got {value})")
    return value


def validate(cfg: dict) -> dict:
    tickers = cfg.get("tickers")
    if not isinstance(tickers, list) or not tickers or not all(isinstance(t, str) and t.strip() for t in tickers):
        raise ConfigError("tickers must be a non-empty list of ticker symbols, e.g. [VT, AAPL]")
    cfg["tickers"] = [t.strip().upper() for t in tickers]
    cfg["benchmark_ticker"] = str(cfg.get("benchmark_ticker") or "VT").strip().upper()

    try:
        cash = float(cfg["starting_cash_usd"])
    except (TypeError, ValueError):
        raise ConfigError("starting_cash_usd must be a number")
    if cash <= 0:
        raise ConfigError("starting_cash_usd must be greater than 0")
    cfg["starting_cash_usd"] = cash

    if not isinstance(cfg.get("fractional_shares"), bool):
        raise ConfigError("fractional_shares must be true or false")

    for key in ("half_spread_pct", "slippage_pct", "fx_spread_pct"):
        cfg["costs"][key] = _pct(cfg, "costs", key, upper=10.0)
    try:
        cfg["costs"]["fee_per_order_usd"] = float(cfg["costs"]["fee_per_order_usd"])
    except (TypeError, ValueError):
        raise ConfigError("costs.fee_per_order_usd must be a number")
    if cfg["costs"]["fee_per_order_usd"] < 0:
        raise ConfigError("costs.fee_per_order_usd cannot be negative")
    if cfg["costs"]["fx_mode"] not in FX_MODES:
        raise ConfigError(f"costs.fx_mode must be one of {', '.join(FX_MODES)} (got {cfg['costs']['fx_mode']!r})")

    cfg["dividends"]["withholding_tax_pct"] = _pct(cfg, "dividends", "withholding_tax_pct", upper=100.0001)
    cfg["alerts"]["loss_limit_pct"] = _pct(cfg, "alerts", "loss_limit_pct")
    if cfg["alerts"]["loss_limit_pct"] <= 0:
        raise ConfigError("alerts.loss_limit_pct must be greater than 0")
    cfg["alerts"]["loss_limit_grace_days"] = int(cfg["alerts"]["loss_limit_grace_days"])

    years = cfg["session"]["min_years_after_start"]
    if not isinstance(years, (int, float)) or years <= 0:
        raise ConfigError("session.min_years_after_start must be a positive number")
    seed = cfg["session"].get("seed")
    if seed is not None and not isinstance(seed, int):
        raise ConfigError("session.seed must be an integer or null")

    tb = cfg["trend_bot"]
    for key in ("short_ma", "long_ma"):
        if not isinstance(tb[key], int) or tb[key] < 2:
            raise ConfigError(f"trend_bot.{key} must be an integer >= 2")
    if tb["short_ma"] >= tb["long_ma"]:
        raise ConfigError("trend_bot.short_ma must be smaller than trend_bot.long_ma")
    wf = tb["walk_forward"]
    frac = float(wf["train_fraction"])
    if not 0.1 <= frac <= 0.9:
        raise ConfigError("trend_bot.walk_forward.train_fraction must be between 0.1 and 0.9")
    wf["train_fraction"] = frac
    for key in ("short_grid", "long_grid"):
        grid = wf[key]
        if not isinstance(grid, list) or not grid or not all(isinstance(x, int) and x >= 2 for x in grid):
            raise ConfigError(f"trend_bot.walk_forward.{key} must be a list of integers >= 2")
    if not any(s < l for s in wf["short_grid"] for l in wf["long_grid"]):
        raise ConfigError("walk_forward grids need at least one pair with short < long")

    cfg["review"]["large_move_pct"] = float(cfg["review"]["large_move_pct"])
    cfg["review"]["large_move_lookback_days"] = int(cfg["review"]["large_move_lookback_days"])
    return cfg


def load_config(path: str | Path | None = None) -> tuple[dict, str]:
    """Return (config, description of where it came from).

    Lookup order when ``path`` is None: ./config.yaml, then ./config.example.yaml,
    then built-in defaults.
    """
    if path is not None:
        candidates = [Path(path)]
        if not candidates[0].exists():
            raise ConfigError(f"Config file not found: {path}")
    else:
        candidates = [Path("config.yaml"), Path("config.example.yaml")]

    for candidate in candidates:
        if candidate.exists():
            try:
                raw = yaml.safe_load(candidate.read_text()) or {}
            except yaml.YAMLError as exc:
                raise ConfigError(f"Could not parse {candidate}: {exc}")
            if not isinstance(raw, dict):
                raise ConfigError(f"{candidate} must contain a YAML mapping of settings")
            return validate(_merge(DEFAULTS, raw)), str(candidate)
    return validate(copy.deepcopy(DEFAULTS)), "built-in defaults"
