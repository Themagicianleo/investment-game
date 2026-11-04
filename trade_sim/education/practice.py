"""Practice market: FICTIONAL tickers with randomly generated prices.

Works offline and differs every session. It includes bull runs, crashes, choppy
stretches, a speculative bubble, dividends and stock splits so every feature of
the game can be practised. The prices are made up and say nothing about any
real security.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..market import MarketData

# ticker: (description, beta to market factor, idiosyncratic daily vol, extra daily drift, annual dividend yield)
PRACTICE_TICKERS: dict[str, tuple[str, float, float, float, float]] = {
    "GLOBX": ("Fictional total-world index fund", 1.0, 0.002, 0.0, 0.02),
    "STDY": ("Fictional steady consumer-goods company", 0.6, 0.009, 0.00005, 0.03),
    "ROKT": ("Fictional high-growth software company", 1.4, 0.022, 0.0003, 0.0),
    "CYCL": ("Fictional cyclical industrial company", 1.25, 0.015, -0.00005, 0.02),
    "HYPE": ("Fictional speculative tech start-up", 1.6, 0.03, 0.0, 0.0),
}
PRACTICE_BENCHMARK = "GLOBX"

_REGIMES = {  # name: (daily drift, daily vol, min len, max len)
    "bull": (0.0007, 0.008, 120, 400),
    "chop": (0.0, 0.011, 60, 200),
    "bear": (-0.0011, 0.016, 60, 200),
    "crash": (-0.006, 0.032, 10, 30),
    "rebound": (0.003, 0.02, 20, 60),
}
_NEXT = {"bull": ["bull", "chop", "bear", "crash"], "chop": ["bull", "bear", "chop"],
         "bear": ["crash", "chop", "rebound"], "crash": ["rebound", "bear"], "rebound": ["bull", "chop"]}


def _market_factor(rng: np.random.Generator, n: int) -> np.ndarray:
    out, regime = [], "bull"
    while len(out) < n:
        mu, sd, lo, hi = _REGIMES[regime]
        length = int(rng.integers(lo, hi + 1))
        out.extend(rng.normal(mu, sd, length))
        regime = str(rng.choice(_NEXT[regime]))
    return np.array(out[:n])


def make_practice_market(seed: int | None = None, n_days: int = 2600, start: str = "2001-01-02") -> MarketData:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start, periods=n_days)
    mkt = _market_factor(rng, n_days)

    # HYPE: a bubble (strong extra drift) followed by a bust, at a random point in the second half
    bubble_start = int(rng.integers(n_days // 2, n_days - 400))
    hype_extra = np.zeros(n_days)
    hype_extra[bubble_start:bubble_start + 180] = 0.006
    hype_extra[bubble_start + 180:bubble_start + 330] = -0.009

    frames = {}
    for ticker, (_, beta, ivol, drift, div_yield) in PRACTICE_TICKERS.items():
        r = beta * mkt + rng.normal(drift, ivol, n_days)
        if ticker == "HYPE":
            r = r + hype_extra
        price = float(rng.uniform(20, 120))
        rows, split_done = [], False
        for i in range(n_days):
            prev = price
            gap = rng.normal(0, 0.35 * (abs(r[i]) + 0.004))
            open_ = prev * np.exp(gap)
            close = prev * np.exp(r[i])
            div = 0.0
            split = 0.0
            if div_yield and i > 0 and i % 63 == 0:  # quarterly ex-dividend date
                div = round(prev * div_yield / 4, 4)
                open_ -= div
                close -= div
            if not split_done and ticker in ("ROKT", "HYPE") and prev > 300:
                split, split_done = 4.0, True
                open_ /= 4
                close /= 4
            spread = abs(rng.normal(0, 0.006))
            high = max(open_, close) * (1 + spread)
            low = min(open_, close) * (1 - spread)
            close = max(close, 0.5)
            open_ = max(open_, 0.5)
            rows.append((open_, high, max(low, 0.4), close, float(rng.integers(200_000, 2_000_000)), div, split))
            price = close
        frames[ticker] = pd.DataFrame(rows, index=pd.DatetimeIndex(dates, name="Date"),
                                      columns=["Open", "High", "Low", "Close", "Volume", "Dividends", "Stock Splits"])
    return MarketData(frames)
