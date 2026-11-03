"""Synthetic market data for tests (no network needed)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from trade_sim.execution import Costs
from trade_sim.market import MarketData


def make_frame(n: int = 800, start: str = "2010-01-04", seed: int = 0, drift: float = 0.0003,
               first_price: float = 100.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start, periods=n)
    closes = first_price * np.exp(np.cumsum(rng.normal(drift, 0.015, n)))
    opens = closes * (1 + rng.normal(0, 0.003, n))
    return pd.DataFrame(
        {
            "Open": opens,
            "High": np.maximum(opens, closes) * 1.01,
            "Low": np.minimum(opens, closes) * 0.99,
            "Close": closes,
            "Volume": 1_000_000.0,
            "Dividends": 0.0,
            "Stock Splits": 0.0,
        },
        index=pd.DatetimeIndex(dates, name="Date"),
    )


def flat_frame(n: int = 10, price: float = 100.0, start: str = "2020-01-01") -> pd.DataFrame:
    dates = pd.bdate_range(start, periods=n)
    return pd.DataFrame(
        {"Open": price, "High": price, "Low": price, "Close": price, "Volume": 1.0,
         "Dividends": 0.0, "Stock Splits": 0.0},
        index=pd.DatetimeIndex(dates, name="Date"),
    )


@pytest.fixture
def market() -> MarketData:
    return MarketData({"AAA": make_frame(seed=1), "BBB": make_frame(seed=2), "VT": make_frame(seed=3)})


@pytest.fixture
def costs() -> Costs:
    return Costs()  # spec defaults: 0.05% half spread, 0.05% slippage, $0.99 fee, 0.15% FX, 30% tax


@pytest.fixture
def no_cost() -> Costs:
    return Costs(half_spread=0, slippage=0, fee_per_order=0, fx_spread=0, fx_mode="none",
                 withholding_tax=0, fractional=False)
